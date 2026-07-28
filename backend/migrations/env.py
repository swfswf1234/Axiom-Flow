"""
模块职责：为 v0.2 MySQL 运行库执行 Alembic 版本化迁移。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
关联测试：tests/test_mysql_migrations.py
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from backend.infrastructure.config import Settings

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
if config.get_main_option("sqlalchemy.url").startswith("mysql+pymysql://unused:"):
    config.set_main_option("sqlalchemy.url", Settings().mysql_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    """离线模式只生成 SQL，不连接运行数据库。"""
    context.configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式由显式 alembic upgrade 命令调用。"""
    engine = create_engine(config.get_main_option("sqlalchemy.url"), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
