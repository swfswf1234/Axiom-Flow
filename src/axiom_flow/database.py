"""数据库连接工具：共享 qed 库 `qed_llm_calls` 调用日志接入点（V2-014）。

`QED_DB_*` 经 `config.Settings` 注入；DB 不可达时由上层降级记日志，不在此层抛错。
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


def create_engine_for(settings: Settings) -> Engine:
    """创建 SQLAlchemy engine：pool_pre_ping 防断连，future 行为。"""
    return create_engine(mysql_url(settings), pool_pre_ping=True, future=True)


def utc_now() -> datetime:
    """MySQL DATETIME 无时区：统一返回 naive UTC。"""
    return datetime.now(UTC).replace(tzinfo=None)
