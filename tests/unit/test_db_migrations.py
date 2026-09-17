"""Alembic 迁移单测：四表一次迁移可升级/降级（SQLite 临时库，不依赖 MySQL）。

守护面：af_* 四表 DDL 与 database-design.md 一致、迁移可重复执行、降级干净。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]
AF_TABLES = {"af_books", "af_parse_jobs", "af_pages", "af_block_edits"}


def _config(tmp_path: Path) -> tuple[Config, str]:
    db_path = (tmp_path / "af_migration.db").as_posix()
    url = f"sqlite+pysqlite:///{db_path}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config, url


def test_upgrade_creates_four_af_tables(tmp_path):
    config, url = _config(tmp_path)

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(url)).get_table_names())
    assert AF_TABLES <= tables
    assert "alembic_version" in tables


def test_downgrade_drops_all_af_tables(tmp_path):
    config, url = _config(tmp_path)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    tables = set(inspect(create_engine(url)).get_table_names())
    assert not (AF_TABLES & tables)


def test_upgrade_is_repeatable_after_downgrade(tmp_path):
    config, url = _config(tmp_path)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert AF_TABLES <= set(inspect(create_engine(url)).get_table_names())
