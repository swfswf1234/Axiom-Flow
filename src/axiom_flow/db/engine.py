"""数据库连接与时间工具：`qed` 库 af_* 表接入点。

`QED_DB_*` 经 `config.Settings` 注入；DB 不可达时由上层降级，不在此层抛错。
测试可传 `url`（如 sqlite://）绕过 MySQL，保持默认测试隔离。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import URL, Engine, create_engine

from axiom_flow.config import Settings


def mysql_url(settings: Settings) -> str:
    """拼装 pymysql DSN：charset=utf8mb4，密码明文呈交 SQLAlchemy。"""
    return URL.create(
        "mysql+pymysql",
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        username=settings.db_user,
        password=settings.db_password,
        query={"charset": "utf8mb4"},
    ).render_as_string(hide_password=False)


def create_engine_for(settings: Settings, url: str | None = None) -> Engine:
    """创建 SQLAlchemy engine：pool_pre_ping 防断连，future 行为；url 覆盖用于测试。"""
    return create_engine(url or mysql_url(settings), pool_pre_ping=True, future=True)


def utc_now() -> datetime:
    """MySQL DATETIME 无时区：统一返回 naive UTC。"""
    return datetime.now(UTC).replace(tzinfo=None)
