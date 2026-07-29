"""
模块职责：${message}
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
关联测试：tests/test_mysql_migrations.py
"""

${imports if imports else ""}

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    """应用本次 schema 变更。"""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """回滚本次 schema 变更。"""
    ${downgrades if downgrades else "pass"}
