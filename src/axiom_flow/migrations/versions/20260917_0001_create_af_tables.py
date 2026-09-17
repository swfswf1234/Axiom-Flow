"""af_* 四表一次建全（qed 库 af_* 命名空间）

Revision ID: 20260917_0001
Revises:
Create Date: 2026-09-17

结构事实源：docs/architecture/database-design.md（ARCH-020 四表契约，2026-09-17 用户裁决
一次迁移建全，PLAN-002 Step 2-B）。本仓库只建自己的 af_* 表，不触碰共享 qed_* 表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260917_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "af_books",
        sa.Column("book_id", sa.String(100), nullable=False, comment="同源 qt_books.book_id"),
        sa.Column("domain_id", sa.String(32), nullable=False, server_default=sa.text("''")),
        sa.Column("course_id", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("course_name", sa.String(200), nullable=False, server_default=sa.text("''")),
        sa.Column("knowledge_id", sa.String(100), nullable=False, server_default=sa.text("''")),
        sa.Column("title", sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column("part", sa.String(32), nullable=False, server_default=sa.text("''")),
        sa.Column("display_title", sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column("authors", sa.JSON(), nullable=False, comment="list[{name, role}]"),
        sa.Column("file_path", sa.String(512), nullable=False, server_default=sa.text("''"), comment="数据根相对路径"),
        sa.Column("page_count", sa.Integer(), nullable=True, comment="ingest 时计算"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=sa.text("''"), comment="ingest 时计算"),
        sa.Column("ingest_status", sa.String(16), nullable=False, server_default=sa.text("'none'")),
        sa.Column("parse_status", sa.String(24), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("book_id"),
        comment="书目同步表：QED-Tracker 已验证书目的只读快照 + ingest/解析进度（Axiom-Flow 私有）",
        **_MYSQL,
    )
    op.create_index("ix_af_books_course", "af_books", ["course_id"])
    op.create_index("ix_af_books_status", "af_books", ["parse_status"])

    op.create_table(
        "af_parse_jobs",
        sa.Column("job_id", sa.String(64), nullable=False),
        sa.Column("book_id", sa.String(100), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=True, comment="NULL = 全书；否则页码数组"),
        sa.Column("engine", sa.String(24), nullable=False, server_default=sa.text("'mineru'")),
        sa.Column("status", sa.String(16), nullable=False, comment="queued/running/completed/failed"),
        sa.Column("progress", sa.JSON(), nullable=False, comment='{"parsed":N,"total":M,"error":""}'),
        sa.Column("error", sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("job_id"),
        comment="解析任务表：逐页/全本解析任务状态与进度（Axiom-Flow 私有）",
        **_MYSQL,
    )
    op.create_index("ix_af_parse_jobs_book", "af_parse_jobs", ["book_id"])

    op.create_table(
        "af_pages",
        sa.Column("book_id", sa.String(100), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("source", sa.String(24), nullable=False, server_default=sa.text("''"), comment="实际引擎标识"),
        sa.Column("quality", sa.JSON(), nullable=True, comment="质量信号"),
        sa.Column("error", sa.String(500), nullable=False, server_default=sa.text("''")),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("book_id", "page_no"),
        comment="页状态表：逐页解析状态与质量信号（Axiom-Flow 私有）",
        **_MYSQL,
    )

    op.create_table(
        "af_block_edits",
        sa.Column("edit_id", sa.String(100), nullable=False, comment="ed_<md5(book_id:page_no:block_index)>"),
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
        sa.PrimaryKeyConstraint("edit_id"),
        sa.UniqueConstraint("book_id", "page_no", "block_index", name="uq_af_block_edits_pos"),
        comment="块编辑表：对照视图中人工判定/备注/文字与范围修正（Axiom-Flow 私有）",
        **_MYSQL,
    )


def downgrade() -> None:
    op.drop_table("af_block_edits")
    op.drop_table("af_pages")
    op.drop_index("ix_af_parse_jobs_book", table_name="af_parse_jobs")
    op.drop_table("af_parse_jobs")
    op.drop_index("ix_af_books_status", table_name="af_books")
    op.drop_index("ix_af_books_course", table_name="af_books")
    op.drop_table("af_books")
