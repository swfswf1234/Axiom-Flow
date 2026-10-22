"""
模块职责：守护 `af_*` 表族的**单一 base 迁移链**——档 B 整族重建后链上只有一条
`20261022_0001`（`down_revision = None`），一次建齐四表；升降级可重复且形状与
`database-design.md` 目标列清单逐列一致。
设计关联（DesignRef）：docs/architecture/database-design.md（目标形状四表 + 整族重建的档位与迁移链）
实现状态：Current
关联测试：tests/unit/test_db_table_family_governance.py（表集合派生面）、
tests/integration/test_parse_persistence.py（新 base 在真 MySQL 上从 base 建到 head）
守护面：迁移链张数、四表列集合、主键/唯一键/索引名、DDL 默认值
失效后果：旧链残留 ⇒ 测试库夹具每次先建旧五表再删；列集合漂移 ⇒ 权威文档与 DDL 各说一套
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]
BASE_REVISION = "20261022_0001"

#: 四表列集合（正本＝database-design.md 目标列清单；`created_at`/`updated_at` 各算一列）。
EXPECTED_COLUMNS: dict[str, set[str]] = {
    "af_books": {
        "book_id",
        "domain_id",
        "course_id",
        "course_name",
        "knowledge_id",
        "title",
        "part",
        "display_title",
        "authors",
        "file_path",
        "page_count",
        "sha256",
        "ingest_status",
        "ingested_at",
        "parse_status",
        "last_parse_result",
        "pages_done",
        "active_job_id",
        "notes",
        "meta",
        "synced_at",
        "updated_at",
    },
    "af_operations": {
        "op_id",
        "book_id",
        "job_id",
        "op_kind",
        "lifecycle",
        "actor",
        "page_no",
        "block_index",
        "target_id",
        "intent_key",
        "engine",
        "pages",
        "page_count",
        "source_sha256",
        "parser_signature",
        "embedding_signature",
        "render_profile",
        "status",
        "progress",
        "failed_pages",
        "error",
        "payload",
        "result",
        "note",
        "meta",
        "created_at",
        "updated_at",
    },
    "af_pages": {
        "book_id",
        "job_id",
        "page_no",
        "status",
        "source",
        "quality",
        "error",
        "artifacts",
        "confirm_state",
        "section_kind",
        "meta",
        "parsed_at",
    },
    "af_kp": {
        "node_id",
        "book_id",
        "job_id",
        "node_kind",
        "parent_id",
        "depth",
        "order_hint",
        "name",
        "norm_key",
        "role",
        "type",
        "summary",
        "links",
        "anchors",
        "narrative_ref",
        "gen_status",
        "confirmed",
        "gen_batch",
        "model_ref",
        "rev",
        "meta",
        "created_at",
        "updated_at",
    },
}

EXPECTED_INDEXES: dict[str, dict[str, list[str]]] = {
    "af_books": {
        "ix_af_books_course": ["course_id"],
        "ix_af_books_status": ["parse_status"],
    },
    "af_operations": {
        "ix_af_operations_book": ["book_id", "op_kind", "created_at"],
        "ix_af_operations_version": ["job_id", "page_no"],
        "ix_af_operations_sig": ["book_id", "parser_signature", "status"],
    },
    "af_pages": {},
    "af_kp": {
        "ix_af_kp_book": ["book_id", "node_kind", "gen_status"],
        "ix_af_kp_parent": ["parent_id"],
        "ix_af_kp_batch": ["gen_batch"],
        "ix_af_kp_norm": ["book_id", "node_kind", "norm_key"],
    },
}

#: 被吸收的三张旧表：档 B 之后任何路径都不得再建出它们。
RETIRED_TABLES = ("af_parse_jobs", "af_block_edits", "af_page_regions")


@pytest.fixture()
def migrated_url(tmp_path: Path) -> str:
    config, url = _config(tmp_path)
    command.upgrade(config, "head")
    return url


def _config(tmp_path: Path) -> tuple[Config, str]:
    db_path = (tmp_path / "af_migration.db").as_posix()
    url = f"sqlite+pysqlite:///{db_path}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))


def _columns(url: str, table: str) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(create_engine(url)).get_columns(table)}


def _indexes(url: str, table: str) -> dict[str, list[str]]:
    return {i["name"]: list(i["column_names"]) for i in inspect(create_engine(url)).get_indexes(table)}


# ---------- 迁移链张数（档 B 重置的唯一形状） ----------


def test_chain_is_a_single_base_revision():
    """链上只有一条迁移且它就是 base：旧链每条都在为不存在的形状跑 DDL，留着即漂移源。"""
    script = _script()
    revisions = list(script.walk_revisions())

    assert [rev.revision for rev in revisions] == [BASE_REVISION]
    assert revisions[0].down_revision is None, "重置后的 base 不得再指向任何旧序号"


def test_version_files_are_exactly_the_base():
    """目录里不得残留旧链文件（`__pycache__` 之外只有 base 一个 .py）。"""
    versions = ROOT / "src/axiom_flow/migrations/versions"
    files = sorted(p.name for p in versions.glob("*.py"))

    assert files == [f"{BASE_REVISION}_af_family_rebuild.py"], files


def test_upgrade_creates_exactly_the_four_tables(migrated_url: str):
    tables = {t for t in inspect(create_engine(migrated_url)).get_table_names() if t.startswith("af_")}

    assert tables == set(EXPECTED_COLUMNS)
    assert "alembic_version" in inspect(create_engine(migrated_url)).get_table_names()


def test_retired_tables_are_never_recreated(migrated_url: str):
    """被吸收的三张表不得再由任何迁移建出（吸收结论的可执行半边）。"""
    tables = set(inspect(create_engine(migrated_url)).get_table_names())

    assert not (tables & set(RETIRED_TABLES)), sorted(tables & set(RETIRED_TABLES))


def test_downgrade_drops_every_af_table(migrated_url: str, tmp_path: Path):
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    assert not (set(EXPECTED_COLUMNS) & set(inspect(create_engine(url)).get_table_names()))


def test_upgrade_is_repeatable_after_downgrade(tmp_path: Path):
    """升→降→升可重复：真机回滚演练的最低门槛，也是集成夹具每轮从 base 重建的前提。"""
    config, url = _config(tmp_path)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert set(EXPECTED_COLUMNS) <= set(inspect(create_engine(url)).get_table_names())


# ---------- 逐表列集合（与权威文档对账） ----------


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_columns_match_the_authoritative_list(migrated_url: str, table: str):
    actual = set(_columns(migrated_url, table))
    expected = EXPECTED_COLUMNS[table]

    assert actual == expected, f"{table} 列集合与 database-design.md 漂移：多={sorted(actual - expected)}，缺={sorted(expected - actual)}"


@pytest.mark.parametrize("table", sorted(EXPECTED_COLUMNS))
def test_table_indexes_match_the_authoritative_names(migrated_url: str, table: str):
    """索引名与列序逐条 pin：显式命名是「避免 autogenerate 漂移」的全部依据。"""
    actual = _indexes(migrated_url, table)

    for name, columns in EXPECTED_INDEXES[table].items():
        assert actual.get(name) == columns, f"{table}.{name} 期望 {columns}，实际 {actual}"


def test_primary_keys(migrated_url: str):
    inspector = inspect(create_engine(migrated_url))

    assert inspector.get_pk_constraint("af_books")["constrained_columns"] == ["book_id"]
    assert inspector.get_pk_constraint("af_operations")["constrained_columns"] == ["op_id"]
    assert inspector.get_pk_constraint("af_pages")["constrained_columns"] == ["book_id", "job_id", "page_no"]
    assert inspector.get_pk_constraint("af_kp")["constrained_columns"] == ["node_id"]


# ---------- DDL 默认值（判据面的空值语义） ----------


def test_intent_key_is_the_only_unique_key_on_af_operations(migrated_url: str):
    """意图幂等靠 `intent_key` 唯一键；坐标列本身不设唯一键（多行 NULL 即部分唯一索引）。"""
    uniques = inspect(create_engine(migrated_url)).get_unique_constraints("af_operations")

    assert [(u["name"], set(u["column_names"])) for u in uniques] == [
        ("uq_af_operations_intent", {"intent_key"})
    ], uniques


def test_intent_key_column_is_nullable(migrated_url: str):
    """`event` 行恒 NULL——MySQL 唯一键允许多行 NULL，这正是「只对意图行生效」的实现方式。"""
    assert _columns(migrated_url, "af_operations")["intent_key"]["nullable"] is True


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("lifecycle", "'event'"),
        ("actor", "'human'"),
        ("op_kind", None),
        ("job_id", "''"),
        ("status", "'queued'"),
        ("engine", "''"),
        ("parser_signature", "''"),
        ("embedding_signature", "''"),
        ("render_profile", "''"),
        ("target_id", "''"),
        ("error", "''"),
        ("note", "''"),
    ],
)
def test_af_operations_defaults(migrated_url: str, column: str, expected: str | None):
    """默认值逐列 pin：`engine` 的默认是空串而非某个引擎名（缺引擎名要报错，不静默回落）。"""
    assert _columns(migrated_url, "af_operations")[column]["default"] == expected


@pytest.mark.parametrize(
    ("column", "expected"),
    [("confirm_state", "'unconfirmed'"), ("section_kind", "''"), ("status", "'pending'")],
)
def test_af_pages_defaults(migrated_url: str, column: str, expected: str):
    assert _columns(migrated_url, "af_pages")[column]["default"] == expected


@pytest.mark.parametrize(
    ("column", "expected"),
    [
        ("node_kind", "'point'"),
        ("job_id", "''"),
        ("role", "''"),
        ("type", "''"),
        ("summary", "''"),
        ("narrative_ref", "''"),
        ("gen_status", "'candidate'"),
        ("confirmed", "0"),
        ("gen_batch", "''"),
        ("model_ref", "''"),
        ("rev", "0"),
        ("norm_key", None),
        ("name", None),
    ],
)
def test_af_kp_defaults(migrated_url: str, column: str, expected: str | None):
    assert _columns(migrated_url, "af_kp")[column]["default"] == expected


def test_extension_slots_are_all_nullable_json(migrated_url: str):
    """四表各有一个 `meta` 扩展槽：可空 JSON，不进任何唯一键（三条红线的可执行半边）。"""
    for table in EXPECTED_COLUMNS:
        column = _columns(migrated_url, table)["meta"]
        assert column["nullable"] is True, table
        assert "meta" not in {
            name for u in inspect(create_engine(migrated_url)).get_unique_constraints(table) for name in u["column_names"]
        }, table
