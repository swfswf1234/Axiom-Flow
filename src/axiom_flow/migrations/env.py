"""Alembic 迁移环境：URL 解析顺序 `-x db_url` > `AXIOM_DB_URL` > alembic.ini > `QED_DB_*`。

设计事实源：`docs/architecture/database-design.md`、`docs/design/parsing-management.md`。
本仓库只建自己的 af_* 表（四表一次迁移），不触碰共享 `qed_*` 表。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import mysql_url  # noqa: E402
from axiom_flow.db.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return x_args["db_url"]
    env_url = os.environ.get("AXIOM_DB_URL")
    if env_url:
        return env_url
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    return mysql_url(load_settings())


def run_migrations_offline() -> None:
    """离线模式：只渲染 SQL（不连接数据库）。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
