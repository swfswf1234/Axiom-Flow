"""
模块职责：增加显式当前解析运行、选择历史与解析产物清理状态。
设计关联（DesignRef）：docs/adr/0011-current-parse-run-and-prunable-artifacts.md
实现状态：Current
关联测试：tests/test_mysql_migrations.py、tests/test_current_parse_runs.py
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260728_0004"
down_revision = "20260728_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    created_at = mysql.DATETIME(fsp=6)
    op.add_column("af_documents", sa.Column("current_parse_run_id", sa.String(36), nullable=True))
    op.add_column(
        "af_parse_runs",
        sa.Column("artifact_state", sa.String(16), nullable=False, server_default="available"),
    )
    op.add_column("af_parse_runs", sa.Column("prune_summary_json", mysql.JSON(), nullable=True))
    op.add_column("af_parse_runs", sa.Column("pruned_at", created_at, nullable=True))

    # 迁移只为既有数据建立一次确定性基线；运行时成功解析不会自动切换当前结果。
    op.execute(sa.text("""UPDATE af_documents d
        LEFT JOIN af_parse_runs r ON r.id = (
            SELECT r2.id FROM af_parse_runs r2
            WHERE r2.document_id = d.id AND r2.status = 'parsed'
            ORDER BY r2.created_at DESC, r2.id DESC LIMIT 1
        )
        SET d.current_parse_run_id = r.id"""))
    op.create_index("ix_af_documents_current_parse_run", "af_documents", ["current_parse_run_id"])
    op.create_foreign_key(
        "fk_af_documents_current_parse_run", "af_documents", "af_parse_runs",
        ["current_parse_run_id"], ["id"], ondelete="SET NULL",
    )

    op.create_table(
        "af_parse_run_selections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("previous_run_id", sa.String(36), nullable=True),
        sa.Column("selected_run_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", created_at, nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["af_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["previous_run_id"], ["af_parse_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["selected_run_id"], ["af_parse_runs.id"], ondelete="RESTRICT"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_af_parse_run_selections_document_created",
        "af_parse_run_selections", ["document_id", "created_at"],
    )
    op.execute(sa.text("""INSERT INTO af_parse_run_selections
        (id, document_id, previous_run_id, selected_run_id, reason, created_at)
        SELECT UUID(), id, NULL, current_parse_run_id, 'migration_backfill', CURRENT_TIMESTAMP(6)
        FROM af_documents WHERE current_parse_run_id IS NOT NULL"""))


def downgrade() -> None:
    op.drop_table("af_parse_run_selections")
    op.drop_constraint("fk_af_documents_current_parse_run", "af_documents", type_="foreignkey")
    op.drop_index("ix_af_documents_current_parse_run", table_name="af_documents")
    op.drop_column("af_documents", "current_parse_run_id")
    op.drop_column("af_parse_runs", "pruned_at")
    op.drop_column("af_parse_runs", "prune_summary_json")
    op.drop_column("af_parse_runs", "artifact_state")
