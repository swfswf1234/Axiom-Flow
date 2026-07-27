"""
模块职责：创建 v0.2 MySQL 的 af_ 运行事实与发布快照表。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
关联测试：tests/test_mysql_migrations.py
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "20260727_0001"
down_revision = None
branch_labels = None
depends_on = None


def _id_column(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.String(36), primary_key=True)


def upgrade() -> None:
    created_at = mysql.DATETIME(fsp=6)
    json_type = mysql.JSON()
    op.create_table(
        "af_documents",
        _id_column(),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_documents_status_created", "af_documents", ["status", "created_at"])
    op.create_table(
        "af_parse_runs",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider_summary", json_type, nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_parse_runs_document_status_created", "af_parse_runs", ["document_id", "status", "created_at"])
    op.create_table(
        "af_pages",
        _id_column(),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("blocks_json", json_type, nullable=False),
        sa.Column("evidence_json", json_type, nullable=False),
        sa.Column("quality_json", json_type, nullable=False),
        sa.Column("image_path", sa.String(1024), nullable=False),
        sa.Column("page_kind", sa.String(32), nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["af_parse_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "page_no", name="uq_af_pages_run_page"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_pages_document_run", "af_pages", ["document_id", "run_id"])
    op.create_table(
        "af_candidates",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("evidence_json", json_type, nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_candidates_document_status_title", "af_candidates", ["document_id", "review_status", "title"])
    op.create_table(
        "af_edges",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("evidence_json", json_type, nullable=False),
        sa.Column("review_status", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["af_candidates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["af_candidates.id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_edges_document_status", "af_edges", ["document_id", "review_status"])
    op.create_table(
        "af_workbook_revisions",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("snapshot_json", json_type, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_workbook_revisions_document_created", "af_workbook_revisions", ["document_id", "created_at"])
    op.create_table(
        "af_releases",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("revision_id", sa.String(36), nullable=False),
        sa.Column("snapshot_json", json_type, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revision_id"], ["af_workbook_revisions.id"], ondelete="RESTRICT"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_releases_document_status_created", "af_releases", ["document_id", "status", "created_at"])


def downgrade() -> None:
    op.drop_table("af_releases")
    op.drop_table("af_workbook_revisions")
    op.drop_table("af_edges")
    op.drop_table("af_candidates")
    op.drop_table("af_pages")
    op.drop_table("af_parse_runs")
    op.drop_table("af_documents")
