"""Alembic 迁移单测：表族建表 + 解析版本布局与状态语义迁移可升级/降级（SQLite 临时库，不依赖 MySQL）。

守护面：af_* 表族 DDL 与 database-design.md 一致、迁移可重复执行、降级干净；
`20260920_0002`（PLAN-004）额外守护版本作用域：`af_pages` 主键与 `af_block_edits` 唯一键纳入
`job_id`、生效指针单列、存量 backfill（一书一 job 回填该 job，否则 `legacy` 哨兵）；
`20260921_0001`（PLAN-006 / ADR 0007）守护进度与结局分列：书目新增 `last_parse_result`、
任务新增 `failed_pages`，存量只平移不改写历史 job 状态；
`20260926_0001`（PLAN-017 / REQ-097）守护 `af_books.ingested_at` 纯加列：存量不回填（NULL =
「本轮规则之前 ingest」），降级干净删列；
`20260926_0002`（PLAN-018 W2 / ADR 0010）守护 `af_page_regions`：页级三列主键、**无
`job_id`**、显式索引名，降级只带走该表且不动其余 `af_*` 表。
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from axiom_flow.db.models import AF_TABLES as _AF_TABLE_REGISTRY

ROOT = Path(__file__).resolve().parents[2]
#: 表名集合从 ORM 派生，不再本地硬编码（PLAN-020 S2：加表只改 `AF_TABLES`，升降级往返自动覆盖新表）。
AF_TABLES = {table.name for table in _AF_TABLE_REGISTRY}
REVISION_0001 = "20260917_0001"
REVISION_0002 = "20260920_0002"
LEGACY_JOB_ID = "legacy"


def _config(tmp_path: Path) -> tuple[Config, str]:
    db_path = (tmp_path / "af_migration.db").as_posix()
    url = f"sqlite+pysqlite:///{db_path}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def test_upgrade_creates_four_af_tables(tmp_path):
    config, url = _config(tmp_path)

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(url)).get_table_names())
    assert AF_TABLES <= tables
    assert "alembic_version" in tables


def test_downgrade_drops_all_af_tables(tmp_path):
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    tables = set(inspect(create_engine(url)).get_table_names())
    assert not (AF_TABLES & tables)


def test_upgrade_is_repeatable_after_downgrade(tmp_path):
    config, url = _config(tmp_path)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert AF_TABLES <= set(inspect(create_engine(url)).get_table_names())


# ---------- 20260920_0002：解析产物按版本划分（PLAN-004 / REQ-080） ----------


def _seed_pre_version_rows(url: str, *, jobs: list[tuple[str, str]]) -> None:
    """在 0001 形态的库上写存量行：一本书 + `jobs` 个任务 + 全书 2 页 + 1 条块编辑。

    直连 Core 写（不经 ORM 模型）：迁移的 backfill 判据必须对「旧结构里的真实存量」成立，
    而不是对新模型写入的结果成立。
    """
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO af_books (book_id, domain_id, course_id, title, authors, file_path,"
                " sha256, ingest_status, parse_status, pages_done, synced_at, updated_at)"
                " VALUES ('b01','','','线性代数','[]','raw/b01.pdf','','ingested','completed',2,"
                " '2026-09-20 00:00:00','2026-09-20 00:00:00')"
            )
        )
        for job_id, status in jobs:
            conn.execute(
                text(
                    "INSERT INTO af_parse_jobs (job_id, book_id, pages, engine, status, progress, error,"
                    " created_at) VALUES (:job_id,'b01',NULL,'mineru',:status,'{}','','2026-09-20 00:00:00')"
                ),
                {"job_id": job_id, "status": status},
            )
        for page_no in (1, 2):
            conn.execute(
                text(
                    "INSERT INTO af_pages (book_id, page_no, status, source, quality, error, parsed_at)"
                    " VALUES ('b01',:page_no,'parsed','mineru',NULL,'','2026-09-20 00:00:00')"
                ),
                {"page_no": page_no},
            )
        conn.execute(
            text(
                "INSERT INTO af_block_edits (edit_id, book_id, page_no, block_index, block_type, verdict,"
                " note, corrected_text, corrected_bbox, edited_at, updated_at)"
                " VALUES ('ed_old001','b01',1,0,'paragraph','ok','改一处','正文',NULL,"
                " '2026-09-20 00:00:00','2026-09-20 00:00:00')"
            )
        )
    engine.dispose()


def _job_ids_for_pages(url: str) -> list[str | None]:
    engine = create_engine(url)
    with engine.begin() as conn:
        rows = [row[0] for row in conn.execute(text("SELECT job_id FROM af_pages ORDER BY page_no"))]
    engine.dispose()
    return rows


def test_upgrade_0002_adds_version_pointer_and_scope_columns(tmp_path):
    """版本布局：`af_books` 单列生效指针 + 页/编辑表按 `job_id` 作用域。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0001)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    book_columns = {column["name"] for column in inspector.get_columns("af_books")}
    job_columns = {column["name"] for column in inspector.get_columns("af_parse_jobs")}
    assert "active_job_id" in book_columns, "生效指针必须落在 af_books 单列（V2）"
    assert {"source_sha256", "page_count"} <= job_columns, "版本与源快照自证对应（V7）"
    assert set(inspector.get_pk_constraint("af_pages")["constrained_columns"]) == {
        "book_id",
        "job_id",
        "page_no",
    }, "同一页在不同版本可有独立状态：主键必须纳入 job_id"
    unique_keys = [set(item["column_names"]) for item in inspector.get_unique_constraints("af_block_edits")]
    assert {"book_id", "job_id", "page_no", "block_index"} in unique_keys, (
        f"编辑键必须绑定其所属版本，实际唯一键：{unique_keys}"
    )


def test_downgrade_0002_restores_pre_version_shape(tmp_path):
    """降级干净：删新增列、主键与唯一键回到 0001 形态（不残留 job_id）。"""
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, REVISION_0001)

    inspector = inspect(create_engine(url))
    assert "active_job_id" not in {column["name"] for column in inspector.get_columns("af_books")}
    assert "job_id" not in {column["name"] for column in inspector.get_columns("af_pages")}
    assert set(inspector.get_pk_constraint("af_pages")["constrained_columns"]) == {"book_id", "page_no"}
    unique_keys = [set(item["column_names"]) for item in inspector.get_unique_constraints("af_block_edits")]
    assert {"book_id", "page_no", "block_index"} in unique_keys


def test_upgrade_0002_backfills_single_job_book_to_that_job(tmp_path):
    """一书只有一个 job：存量页行/编辑行回填该 job（该书只有一个版本，作用域无歧义）。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0001)
    _seed_pre_version_rows(url, jobs=[("j_single", "completed")])

    command.upgrade(config, "head")

    assert _job_ids_for_pages(url) == ["j_single", "j_single"]
    engine = create_engine(url)
    with engine.begin() as conn:
        edit_job = conn.execute(text("SELECT job_id FROM af_block_edits")).scalar_one()
    engine.dispose()
    assert edit_job == "j_single"


def test_upgrade_0002_backfills_multi_job_book_to_legacy_sentinel(tmp_path):
    """一书有多个历史 job：无法判定归属 → 回填 `legacy` 哨兵（根视图语义），不猜。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0001)
    _seed_pre_version_rows(url, jobs=[("j_first", "completed"), ("j_second", "failed")])

    command.upgrade(config, "head")

    assert _job_ids_for_pages(url) == [LEGACY_JOB_ID, LEGACY_JOB_ID]


def test_upgrade_0002_leaves_active_pointer_null_for_existing_books(tmp_path):
    """存量不自动迁移：升级后 `active_job_id` 恒 NULL → 端点继续读根视图（旧产物不失明）。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0001)
    _seed_pre_version_rows(url, jobs=[("j_single", "completed")])

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.begin() as conn:
        active = conn.execute(text("SELECT active_job_id FROM af_books")).scalar_one()
    engine.dispose()
    assert active is None


def test_upgrade_0002_drops_dead_engine_default(tmp_path):
    """PLAN-003「W7 发现 2」销账：写入路径恒显式传解析后的引擎名，DDL 的 `'mineru'` 是死兜底。

    留着它等于在表里藏一个静默回落（ADR 0004 禁无自动兜底）；随本迁移一并删除，并证明
    `af_parse_jobs` 重建后存量行与索引不丢。
    """
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0001)
    _seed_pre_version_rows(url, jobs=[("j_single", "completed")])

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    columns = {column["name"]: column for column in inspector.get_columns("af_parse_jobs")}
    assert columns["engine"]["default"] is None, "引擎列不得保留死默认值"
    assert "ix_af_parse_jobs_book" in {item["name"] for item in inspector.get_indexes("af_parse_jobs")}
    engine = create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT job_id, engine, status FROM af_parse_jobs")).all()
    engine.dispose()
    assert rows == [("j_single", "mineru", "completed")], "存量任务行必须原样保留"


def test_upgrade_0002_is_repeatable_after_full_downgrade(tmp_path):
    """升→降→升可重复（真机回滚演练的最低门槛）。"""
    config, url = _config(tmp_path)
    command.upgrade(config, "head")
    command.downgrade(config, REVISION_0001)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "job_id" in {column["name"] for column in inspector.get_columns("af_pages")}


# ---------- 20260921_0001：进度与结局分列（PLAN-006 / ADR 0007） ----------


def _seed_pre_semantics_rows(url: str) -> None:
    """在 0002 形态（当前 head 前）的库上写存量：三本书分别停在 `completed`/`failed`/`parsing`。

    直连 Core 写而不经 ORM：回填判据必须对旧枚举的真实存量成立。历史 job 状态一并写入，
    用于证明迁移**不改写**任务行（改写会让「活动态只有一个写入者」的不变式失效）。
    """
    engine = create_engine(url)
    with engine.begin() as conn:
        for book_id, parse_status in (("b_done", "completed"), ("b_fail", "failed"), ("b_run", "parsing")):
            conn.execute(
                text(
                    "INSERT INTO af_books (book_id, domain_id, course_id, title, authors, file_path,"
                    " sha256, ingest_status, parse_status, pages_done, synced_at, updated_at)"
                    " VALUES (:book_id,'','','书名','[]','raw/x.pdf','','ingested',:parse_status,3,"
                    " '2026-09-21 00:00:00','2026-09-21 00:00:00')"
                ),
                {"book_id": book_id, "parse_status": parse_status},
            )
        for job_id, book_id, status in (("j_done", "b_done", "completed"), ("j_fail", "b_fail", "failed")):
            conn.execute(
                text(
                    "INSERT INTO af_parse_jobs (job_id, book_id, pages, engine, status, progress, error,"
                    " created_at) VALUES (:job_id,:book_id,NULL,'mineru',:status,'{}','',:created_at)"
                ),
                {"job_id": job_id, "book_id": book_id, "status": status, "created_at": "2026-09-21 00:00:00"},
            )
        conn.execute(
            text(
                "INSERT INTO af_parse_jobs (job_id, book_id, pages, engine, status, progress, error, created_at)"
                " VALUES ('j_run','b_run',NULL,'mineru','running','{\"parsed\": 1, \"total\": 3}','',"
                " '2026-09-21 00:00:00')"
            )
        )
    engine.dispose()


def _book_states(url: str) -> dict[str, tuple[str, str]]:
    engine = create_engine(url)
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT book_id, parse_status, last_parse_result FROM af_books ORDER BY book_id")
        ).all()
    engine.dispose()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_upgrade_0003_adds_result_and_failed_page_columns(tmp_path):
    """ADR 0007 决定 1/2：结局与失败页各落一列，进度不再兼任结局。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0002)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "last_parse_result" in {column["name"] for column in inspector.get_columns("af_books")}
    job_columns = {column["name"]: column for column in inspector.get_columns("af_parse_jobs")}
    assert "failed_pages" in job_columns, "失败页必须有结构化列，不能只埋在 error 文本里"


def test_upgrade_0003_splits_outcome_from_progress_without_touching_job_status(tmp_path):
    """回填只做平移：旧 `parse_status` 的终态值搬进 `last_parse_result`，进度归 `pending`。

    反向判据：历史 job 状态一字不改（迁移不是第二次写活动态的机会）。
    """
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0002)
    _seed_pre_semantics_rows(url)

    command.upgrade(config, "head")

    assert _book_states(url) == {
        "b_done": ("pending", "completed"),
        "b_fail": ("pending", "failed"),
        "b_run": ("pending", ""),
    }
    engine = create_engine(url)
    with engine.begin() as conn:
        jobs = conn.execute(text("SELECT job_id, status FROM af_parse_jobs ORDER BY job_id")).all()
    engine.dispose()
    assert jobs == [("j_done", "completed"), ("j_fail", "failed"), ("j_run", "running")], (
        "迁移不得改写历史任务状态"
    )


def test_upgrade_0003_backfills_failed_pages_as_empty_array(tmp_path):
    """存量任务没有失败页记录：回填 `[]` 而非留 NULL，读取面免于两种空值语义。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0002)
    _seed_pre_semantics_rows(url)

    command.upgrade(config, "head")

    engine = create_engine(url)
    with engine.begin() as conn:
        values = conn.execute(
            text("SELECT failed_pages FROM af_parse_jobs ORDER BY job_id")
        ).scalars().all()
    engine.dispose()
    assert [json.loads(v) if isinstance(v, str) else v for v in values] == [[], [], []]


def test_downgrade_0003_drops_semantics_columns(tmp_path):
    """降级干净：两列消失，0002 形态可回到 head 再升一次。"""
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, REVISION_0002)

    inspector = inspect(create_engine(url))
    assert "last_parse_result" not in {column["name"] for column in inspector.get_columns("af_books")}
    assert "failed_pages" not in {column["name"] for column in inspector.get_columns("af_parse_jobs")}

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "last_parse_result" in {column["name"] for column in inspector.get_columns("af_books")}


def test_upgrade_0003_keeps_rows_and_indexes_on_rebuild(tmp_path):
    """SQLite 侧经重建表收敛 NOT NULL：存量行与索引不得在重建中丢失。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0002)
    _seed_pre_semantics_rows(url)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "ix_af_parse_jobs_book" in {item["name"] for item in inspector.get_indexes("af_parse_jobs")}
    columns = {column["name"]: column for column in inspector.get_columns("af_parse_jobs")}
    assert columns["failed_pages"]["nullable"] is False


# ---------- 20260926_0001：ingest 元数据补列（PLAN-017 / REQ-097） ----------

REVISION_0003 = "20260921_0001"


def test_upgrade_0004_adds_ingested_at_without_backfill(tmp_path):
    """纯加列且**不回填**：存量行 `NULL` 如实表示「本列规则确立之前 ingest」，不猜值。"""
    config, url = _config(tmp_path)
    command.upgrade(config, REVISION_0003)
    engine = create_engine(url)
    with engine.begin() as conn:
        for book_id in ("b_a", "b_b", "b_c"):
            conn.execute(
                text(
                    "INSERT INTO af_books (book_id, domain_id, course_id, title, authors, file_path,"
                    " sha256, ingest_status, parse_status, last_parse_result, pages_done,"
                    " synced_at, updated_at)"
                    " VALUES (:book_id,'','','书名','[]','raw/x.pdf','','ingested','pending','',0,"
                    " '2026-09-26 00:00:00','2026-09-26 00:00:00')"
                ),
                {"book_id": book_id},
            )
    engine.dispose()

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    columns = {column["name"]: column for column in inspector.get_columns("af_books")}
    assert "ingested_at" in columns
    assert columns["ingested_at"]["nullable"] is True, "从未 ingest 必须能用 NULL 表达"
    engine = create_engine(url)
    with engine.begin() as conn:
        stamps = conn.execute(
            text("SELECT book_id, ingested_at FROM af_books ORDER BY book_id")
        ).all()
    engine.dispose()
    assert [row[1] for row in stamps] == [None, None, None], "迁移不得为存量行编造 ingest 时刻"


def test_downgrade_0004_drops_ingested_at(tmp_path):
    """降级干净：列消失，回到 0003 形态后可再升回。"""
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, REVISION_0003)

    inspector = inspect(create_engine(url))
    assert "ingested_at" not in {column["name"] for column in inspector.get_columns("af_books")}

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "ingested_at" in {column["name"] for column in inspector.get_columns("af_books")}


# ---------- 20260926_0002：页级区域表（PLAN-018 W2 / ADR 0010） ----------

REVISION_PAGE_REGIONS = "20260926_0002"
REVISION_HEAD_BEFORE_REGIONS = "20260926_0001"


def test_upgrade_0005_creates_page_regions_without_job_id(tmp_path):
    """`af_page_regions` 按页级建：主键三列、**无 `job_id`**，索引显式命名（PLAN-020 评审壳的列清单口径）。"""
    config, url = _config(tmp_path)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(url))
    assert "af_page_regions" in inspector.get_table_names()
    with create_engine(url).connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
            REVISION_PAGE_REGIONS
        ), "区域表迁移接在 20260926_0001 之后成为现 head"
    columns = {column["name"] for column in inspector.get_columns("af_page_regions")}
    assert columns == {
        "book_id",
        "page_no",
        "region_id",
        "op",
        "bbox",
        "anchor_bbox",
        "members",
        "kind",
        "note",
        "created_at",
        "updated_at",
    }, f"列集合与 database-design.md 列清单漂移：{sorted(columns)}"
    assert "job_id" not in columns, "区域是页级解析输入，不是版本内覆盖层（ADR 0010 决定 1）"
    assert set(inspector.get_pk_constraint("af_page_regions")["constrained_columns"]) == {
        "book_id",
        "page_no",
        "region_id",
    }
    indexed = {item["name"]: item["column_names"] for item in inspector.get_indexes("af_page_regions")}
    assert indexed.get("ix_af_page_regions_page") == ["book_id", "page_no", "op"], indexed


def test_page_regions_downgrade_is_clean_and_repeatable(tmp_path):
    """降级只带走 `af_page_regions`（其余表与列不动）；升回来后可重复。"""
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, REVISION_HEAD_BEFORE_REGIONS)

    inspector = inspect(create_engine(url))
    assert "af_page_regions" not in inspector.get_table_names()
    retained = {t.name for t in _AF_TABLE_REGISTRY} - {"af_page_regions"}
    assert retained <= set(inspector.get_table_names())
    assert "ingested_at" in {column["name"] for column in inspector.get_columns("af_books")}

    command.upgrade(config, "head")

    assert "af_page_regions" in inspect(create_engine(url)).get_table_names()
