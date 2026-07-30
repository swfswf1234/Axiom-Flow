"""
模块职责：为集成与系统层准备隔离 MySQL 测试库并清理 af_ 表数据。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
关联测试：tests/integration/test_mysql_migrations.py、tests/system/test_document_release_flow.py
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

import pymysql

from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.database import upgrade_database
from axiom_flow.infrastructure.mysql import MySQLRepository


def prepare_mysql_settings() -> Settings:
    """自动创建测试库，但拒绝与运行库同名以防止测试写入业务数据。"""
    runtime = Settings()
    test_database = runtime.mysql_test_database
    if test_database == runtime.mysql_database:
        raise ValueError("AXIOM_MYSQL_TEST_DATABASE 不能与运行数据库相同")
    if not re.fullmatch(r"[A-Za-z0-9_]+", test_database):
        raise ValueError("AXIOM_MYSQL_TEST_DATABASE 只能包含字母、数字和下划线")
    connection = pymysql.connect(
        host=runtime.mysql_host,
        port=runtime.mysql_port,
        user=runtime.mysql_user,
        password=runtime.mysql_password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{test_database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    finally:
        connection.close()
    settings = Settings(mysql_database=test_database)
    upgrade_database(settings.mysql_url)
    upgrade_database(settings.mysql_url)
    return settings


@contextmanager
def isolated_mysql_store(mysql_settings: Settings) -> Iterator[MySQLRepository]:
    """每个测试独占已清空的 af_ 表集合，保留测试库和迁移版本供下次复用。"""
    store = MySQLRepository(mysql_settings.mysql_url)
    store.require_schema()
    store.truncate_all()
    try:
        yield store
    finally:
        store.truncate_all()
        store.dispose()
