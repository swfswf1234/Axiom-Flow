"""
模块职责：以清单和完整确认词保护开发数据库的显式重建操作。
设计关联（DesignRef）：docs/adr/0007-versioned-domain-records.md
实现状态：Current
关联测试：tests/test_reset_safety.py
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text

from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.database import alembic_config

SYSTEM_DATABASES = {"mysql", "information_schema", "performance_schema", "sys"}


def build_manifest(database_url: str, database: str) -> dict:
    """只记录结构和行数，不把业务正文或凭证写入清单。"""
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            tables = list(connection.execute(text("""SELECT table_name FROM information_schema.tables
                WHERE table_schema=:database AND table_name LIKE 'af\\_%' ORDER BY table_name"""),
                {"database": database}).scalars())
            counts = {table: connection.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar_one() for table in tables}
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none() if tables else None
        return {"database": database, "revision": revision, "tables": counts,
                "created_at": datetime.now(UTC).isoformat()}
    finally:
        engine.dispose()


def reset(database: str, allow_runtime: bool, confirmation: str, settings: Settings | None = None) -> Path:
    resolved = settings or Settings()
    if not database or database.lower() in SYSTEM_DATABASES:
        raise ValueError("拒绝重建空名称或 MySQL 系统数据库")
    if database != resolved.mysql_test_database and not allow_runtime:
        raise ValueError("默认只允许重建测试库；运行库必须显式传入 --allow-runtime")
    expected = f"RESET {database}"
    if confirmation != expected:
        raise ValueError(f"确认词不匹配，必须完整输入：{expected}")
    url = resolved.mysql_url_for(database)
    manifest = build_manifest(url, database)
    destination = resolved.data_dir / "backups" / f"reset-{database}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="显式重建 Axiom-Flow 开发数据库")
    parser.add_argument("--database", required=True)
    parser.add_argument("--allow-runtime", action="store_true")
    args = parser.parse_args()
    confirmation = input(f"输入 RESET {args.database} 以确认重建：")
    manifest = reset(args.database, args.allow_runtime, confirmation)
    print(f"重建完成，操作前清单：{manifest}")


if __name__ == "__main__":
    main()
