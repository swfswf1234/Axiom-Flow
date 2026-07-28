"""
模块职责：提供 MySQL 连接生命周期、Alembic 命令和通用查询解码基础设施。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
关联测试：tests/test_mysql_migrations.py、tests/test_reset_safety.py
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[2]


def utc_now() -> datetime:
    """MySQL DATETIME 不保存时区，统一写入 UTC 的无时区时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


def alembic_config(database_url: str) -> Config:
    """构造显式数据库 URL 的 Alembic 配置。"""
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str) -> None:
    """由部署命令或测试显式升级，应用启动不得隐式修改 schema。"""
    command.upgrade(alembic_config(database_url), "head")


class DatabaseConnection:
    """封装连接池和无领域语义的查询辅助。"""

    def __init__(self, database_url: str, pool_size: int = 5, max_overflow: int = 10):
        self.database_url = database_url
        self.engine: Engine = create_engine(
            database_url, pool_pre_ping=True, pool_size=pool_size,
            max_overflow=max_overflow, future=True,
        )

    def require_schema(self) -> None:
        """确认库版本达到当前迁移头，避免半初始化服务启动。"""
        expected = ScriptDirectory.from_config(alembic_config(self.database_url)).get_current_head()
        try:
            with self.engine.connect() as connection:
                actual = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise RuntimeError("MySQL schema 未初始化，请先执行 alembic upgrade head") from exc
        if actual != expected:
            raise RuntimeError("MySQL schema 版本不是当前版本，请执行 alembic upgrade head")

    def dispose(self) -> None:
        self.engine.dispose()

    def _one(self, statement: str, values: dict[str, Any]) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(text(statement), values).mappings().first()
        return dict(row) if row else None

    def _many(self, statement: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(text(statement), values or {}).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _json(value: Any) -> Any:
        if value is None or isinstance(value, (dict, list)):
            return value
        return json.loads(value)
