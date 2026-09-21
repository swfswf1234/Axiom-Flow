"""解析产物按版本划分：生效指针 + 页状态与块编辑的 job 作用域

Revision ID: 20260920_0002
Revises: 20260917_0001
Create Date: 2026-09-21

契约事实源：docs/history/plans/2026-09/2026-09-20-req080-parse-version-layout.md 条文 V2/V7（承接根仓库 REQ-080）。
- `af_books.active_job_id` 是「哪一版生效」的唯一事实源（NULL = 根视图 legacy）；`af_parse_jobs`
  不存布尔生效列，避免同一事实两处漂移。
- `af_pages` 主键与 `af_block_edits` 唯一键纳入 `job_id`：同一页在不同解析版本可有独立状态，
  人工编辑绑定其所属版本（块下标 `index` 随映射表版本变化，跨版本共用键即污染编辑）。
- 存量不自动迁移语义：一书只有一个 job 时回填该 job，否则回填哨兵 `legacy`；哨兵不是「丢弃」而是
  「归属根视图」，端点在 `active_job_id IS NULL` 时继续读到这批行（旧产物不因升级而失明）。

实现约束：`af_pages`/`af_block_edits` 的主键与唯一键变更用「建新表 + 拷贝 + 改名」而非
`batch_alter_table`，因为同一份 DDL 必须同时在 SQLite（无 ALTER PRIMARY KEY）与 MySQL（保留
InnoDB/utf8mb4 与注释）上成立；两表均无外键被引用，改名安全。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_0002"
down_revision: str | None = "20260917_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}

LEGACY_JOB_ID = "legacy"


def _job_scope(alias: str) -> str:
    """存量行的 `job_id` 作用域判据：一书只有一个 job 时回填该 job，否则回填哨兵 `legacy`。

    写成 ANSI 相关子查询，是为了让同一份 SQL 在 SQLite（门禁）与 MySQL（真机窗口）上都成立。
    """
    return (
        "CASE WHEN (SELECT COUNT(*) FROM af_parse_jobs j WHERE j.book_id = "
        f"{alias}.book_id) = 1"
        f" THEN (SELECT MIN(j.job_id) FROM af_parse_jobs j WHERE j.book_id = {alias}.book_id)"
        f" ELSE '{LEGACY_JOB_ID}' END"
    )


def _page_columns() -> list[sa.Column]:
    return [
        sa.Column("book_id", sa.String(100), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("source", sa.String(24), nullable=False, server_default=sa.text("''"), comment="实际引擎标识"),
        sa.Column("quality", sa.JSON(), nullable=True, comment="质量信号"),
        sa.Column("error", sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
    ]


def _edit_columns() -> list[sa.Column]:
    return [
        sa.Column("edit_id", sa.String(100), nullable=False, comment="ed_<md5(book_id:job_id:page_no:block_index)>"),
        sa.Column("book_id", sa.String(100), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("block_index", sa.Integer(), nullable=False, comment="blocks 数组下标（0 基）"),
        sa.Column("block_type", sa.String(24), nullable=False, server_default=sa.text("''")),
        sa.Column("verdict", sa.String(8), nullable=False, server_default=sa.text("''"), comment="''=未判定 / ok / bad"),
        sa.Column("note", sa.String(1000), nullable=False, server_default=sa.text("''")),
        sa.Column("corrected_text", sa.Text(), nullable=True, comment="文字修正（公式为 LaTeX 源码）"),
        sa.Column("corrected_bbox", sa.JSON(), nullable=True, comment="[x0,y0,x1,y1] 修正范围"),
        sa.Column("edited_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "af_books",
        sa.Column(
            "active_job_id",
            sa.String(64),
            nullable=True,
            comment="当前生效解析版本（NULL = 根视图 legacy）；生效指针唯一事实源",
        ),
    )
    op.add_column(
        "af_parse_jobs",
        sa.Column("source_sha256", sa.String(64), nullable=False, server_default=sa.text("''"), comment="版本与源 PDF 对应自证"),
    )
    op.add_column(
        "af_parse_jobs",
        sa.Column("page_count", sa.Integer(), nullable=True, comment="建任务时的全书页数快照"),
    )
    # PLAN-003「W7 发现 2」销账：W4 起写入路径恒显式传解析后的引擎名，DDL 的 `'mineru'` 成了
    # 死兜底（静默回落，ADR 0004 禁止）。batch 模式让同一份变更在 SQLite（重建表）与
    # MySQL（直接 DROP DEFAULT）上都成立。
    with op.batch_alter_table("af_parse_jobs") as batch:
        batch.alter_column("engine", server_default=None)

    # af_pages：主键纳入 job_id（建新表 → 拷贝并定作用域 → 换名）
    op.create_table(
        "af_pages__new",
        *_page_columns(),
        sa.Column(
            "job_id",
            sa.String(64),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_JOB_ID}'"),
            comment="所属解析版本；'legacy' = 根视图存量",
        ),
        sa.PrimaryKeyConstraint("book_id", "job_id", "page_no"),
        comment="页状态表：逐页解析状态与质量信号，按解析版本作用域（Axiom-Flow 私有）",
        **_MYSQL,
    )
    op.execute(
        f"""
        INSERT INTO af_pages__new
            (book_id, job_id, page_no, status, source, quality, error, parsed_at)
        SELECT book_id, {_job_scope('af_pages')}, page_no,
               status, source, quality, error, parsed_at
        FROM af_pages
        """
    )
    op.drop_table("af_pages")
    op.rename_table("af_pages__new", "af_pages")

    # af_block_edits：唯一键纳入 job_id；edit_id 摘要同样纳入 job_id（同一块在不同版本各自成立）
    op.create_table(
        "af_block_edits__new",
        *_edit_columns(),
        sa.Column(
            "job_id",
            sa.String(64),
            nullable=False,
            server_default=sa.text(f"'{LEGACY_JOB_ID}'"),
            comment="编辑绑定的解析版本；'legacy' = 根视图存量",
        ),
        sa.PrimaryKeyConstraint("edit_id"),
        sa.UniqueConstraint("book_id", "job_id", "page_no", "block_index", name="uq_af_block_edits_pos"),
        comment="块编辑表：人工判定/备注/文字与范围修正，绑定其所属解析版本（Axiom-Flow 私有）",
        **_MYSQL,
    )
    # 存量 edit_id 按旧摘要（不含 job_id）生成，原样保留：仓储读取以唯一键为准而非摘要，
    # 故旧行仍可达；新写入才用含 job_id 的新摘要（重算历史主键属无收益的破坏性动作）。
    op.execute(
        f"""
        INSERT INTO af_block_edits__new
            (edit_id, job_id, book_id, page_no, block_index, block_type, verdict, note,
             corrected_text, corrected_bbox, edited_at, updated_at)
        SELECT edit_id, {_job_scope('af_block_edits')}, book_id, page_no,
               block_index, block_type, verdict, note, corrected_text, corrected_bbox,
               edited_at, updated_at
        FROM af_block_edits
        """
    )
    op.drop_table("af_block_edits")
    op.rename_table("af_block_edits__new", "af_block_edits")


def downgrade() -> None:
    """降级为**有损**操作（D 类）：旧结构无法表达多版本，按 (书, 页) 保留 `job_id` 最小的一行。

    真机执行前必须先备份 `af_*` 四表；产物文件不随本迁移删除（DB 与文件解耦）。
    """
    op.create_table(
        "af_block_edits__old",
        *_edit_columns(),
        sa.PrimaryKeyConstraint("edit_id"),
        sa.UniqueConstraint("book_id", "page_no", "block_index", name="uq_af_block_edits_pos"),
        comment="块编辑表：对照视图中人工判定/备注/文字与范围修正（Axiom-Flow 私有）",
        **_MYSQL,
    )
    op.execute(
        """
        INSERT INTO af_block_edits__old
            (edit_id, book_id, page_no, block_index, block_type, verdict, note,
             corrected_text, corrected_bbox, edited_at, updated_at)
        SELECT edit_id, book_id, page_no, block_index, block_type, verdict, note,
               corrected_text, corrected_bbox, edited_at, updated_at
        FROM af_block_edits p
        WHERE NOT EXISTS (
            SELECT 1 FROM af_block_edits q
            WHERE q.book_id = p.book_id AND q.page_no = p.page_no
              AND q.block_index = p.block_index AND q.job_id < p.job_id
        )
        """
    )
    op.drop_table("af_block_edits")
    op.rename_table("af_block_edits__old", "af_block_edits")

    op.create_table(
        "af_pages__old",
        *_page_columns(),
        sa.PrimaryKeyConstraint("book_id", "page_no"),
        comment="页状态表：逐页解析状态与质量信号（Axiom-Flow 私有）",
        **_MYSQL,
    )
    op.execute(
        """
        INSERT INTO af_pages__old (book_id, page_no, status, source, quality, error, parsed_at)
        SELECT book_id, page_no, status, source, quality, error, parsed_at
        FROM af_pages p
        WHERE NOT EXISTS (
            SELECT 1 FROM af_pages q
            WHERE q.book_id = p.book_id AND q.page_no = p.page_no AND q.job_id < p.job_id
        )
        """
    )
    op.drop_table("af_pages")
    op.rename_table("af_pages__old", "af_pages")

    op.drop_column("af_parse_jobs", "page_count")
    op.drop_column("af_parse_jobs", "source_sha256")
    op.drop_column("af_books", "active_job_id")
