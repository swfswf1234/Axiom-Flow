"""
模块职责：为 v0.3 增加持久任务、版本化抽取、审阅历史和规范化页面明细。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
关联测试：tests/test_v03_jobs.py、tests/test_mysql_migrations.py
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260727_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def _id_column(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.String(36), primary_key=True)


def upgrade() -> None:
    created_at = mysql.DATETIME(fsp=6)
    json_type = mysql.JSON()
    op.create_table(
        "af_jobs",
        _id_column(),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("input_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("payload_json", json_type, nullable=False),
        sa.Column("result_json", json_type, nullable=True),
        sa.Column("error_json", json_type, nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", created_at, nullable=True),
        sa.Column("created_at", created_at, nullable=False),
        sa.Column("updated_at", created_at, nullable=False),
        sa.Column("started_at", created_at, nullable=True),
        sa.Column("finished_at", created_at, nullable=True),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_jobs_claim", "af_jobs", ["status", "lease_expires_at", "created_at"])
    op.create_index("ix_af_jobs_aggregate", "af_jobs", ["aggregate_id", "kind", "created_at"])

    op.add_column("af_parse_runs", sa.Column("job_id", sa.String(36), nullable=True))
    op.add_column("af_parse_runs", sa.Column("finished_at", created_at, nullable=True))
    op.add_column("af_parse_runs", sa.Column("error_json", json_type, nullable=True))
    op.create_foreign_key("fk_af_parse_runs_job", "af_parse_runs", "af_jobs", ["job_id"], ["id"], ondelete="SET NULL")
    op.create_unique_constraint("uq_af_parse_runs_job", "af_parse_runs", ["job_id"])

    op.create_table(
        "af_extraction_runs",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("parse_run_id", sa.String(36), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider_summary", json_type, nullable=False),
        sa.Column("model_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_json", json_type, nullable=True),
        sa.Column("created_at", created_at, nullable=False),
        sa.Column("finished_at", created_at, nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parse_run_id"], ["af_parse_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["af_jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("job_id", name="uq_af_extraction_runs_job"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_extraction_document_status", "af_extraction_runs", ["document_id", "status", "created_at"])
    op.add_column("af_candidates", sa.Column("extraction_run_id", sa.String(36), nullable=True))
    op.add_column("af_edges", sa.Column("extraction_run_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_af_candidates_extraction", "af_candidates", "af_extraction_runs", ["extraction_run_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key("fk_af_edges_extraction", "af_edges", "af_extraction_runs", ["extraction_run_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_af_candidates_extraction", "af_candidates", ["extraction_run_id"])
    op.create_index("ix_af_edges_extraction", "af_edges", ["extraction_run_id"])

    op.create_table(
        "af_review_events",
        _id_column(),
        sa.Column("target_type", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_review_target_created", "af_review_events", ["target_type", "target_id", "created_at"])

    op.create_table(
        "af_artifacts",
        _id_column(),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("path", sa.String(1024), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_artifacts_document_run", "af_artifacts", ["document_id", "run_id", "kind"])

    op.create_table(
        "af_content_blocks",
        _id_column(),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("latex", sa.Text(), nullable=True),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["af_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("page_id", "order_no", name="uq_af_blocks_page_order"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_table(
        "af_source_spans",
        _id_column(),
        sa.Column("block_id", sa.String(36), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("bbox_json", json_type, nullable=True),
        sa.Column("quoted_text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["block_id"], ["af_content_blocks.id"], ondelete="CASCADE"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_af_source_spans_block", "af_source_spans", ["block_id"])
    op.create_table(
        "af_quality_reports",
        _id_column(),
        sa.Column("page_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("issues_json", json_type, nullable=False),
        sa.Column("metrics_json", json_type, nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["af_pages.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("page_id", name="uq_af_quality_page"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )


def downgrade() -> None:
    op.drop_table("af_quality_reports")
    op.drop_table("af_source_spans")
    op.drop_table("af_content_blocks")
    op.drop_table("af_artifacts")
    op.drop_table("af_review_events")
    op.drop_index("ix_af_edges_extraction", table_name="af_edges")
    op.drop_constraint("fk_af_edges_extraction", "af_edges", type_="foreignkey")
    op.drop_column("af_edges", "extraction_run_id")
    op.drop_index("ix_af_candidates_extraction", table_name="af_candidates")
    op.drop_constraint("fk_af_candidates_extraction", "af_candidates", type_="foreignkey")
    op.drop_column("af_candidates", "extraction_run_id")
    op.drop_table("af_extraction_runs")
    op.drop_constraint("uq_af_parse_runs_job", "af_parse_runs", type_="unique")
    op.drop_constraint("fk_af_parse_runs_job", "af_parse_runs", type_="foreignkey")
    op.drop_column("af_parse_runs", "error_json")
    op.drop_column("af_parse_runs", "finished_at")
    op.drop_column("af_parse_runs", "job_id")
    op.drop_table("af_jobs")
