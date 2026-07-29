"""
模块职责：为解析产物增加可下载元数据和相对路径约束所需字段。
设计关联（DesignRef）：docs/adr/0008-immutable-parse-artifact-bundles.md
实现状态：Current
关联测试：tests/test_mysql_migrations.py、tests/test_parse_artifacts.py
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "20260728_0003"
down_revision = "20260727_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("af_artifacts", sa.Column("mime_type", sa.String(128), nullable=False, server_default="application/octet-stream"))
    op.add_column("af_artifacts", sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("af_artifacts", sa.Column("metadata_json", mysql.JSON(), nullable=False, server_default=sa.text("('{}')")))


def downgrade() -> None:
    op.drop_column("af_artifacts", "metadata_json")
    op.drop_column("af_artifacts", "size_bytes")
    op.drop_column("af_artifacts", "mime_type")
