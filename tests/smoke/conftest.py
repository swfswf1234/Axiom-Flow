"""
模块职责：为进程冒烟创建并清理独立的 MySQL smoke 数据库。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
关联测试：tests/smoke/test_process_startup.py
"""

import os
import re

import pymysql
import pytest

from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.database import upgrade_database
from axiom_flow.infrastructure.mysql import MySQLRepository


@pytest.fixture(scope="session")
def smoke_settings() -> Settings:
    runtime = Settings()
    database = os.getenv("AXIOM_MYSQL_SMOKE_DATABASE", "axiom_flow_smoke")
    if not re.fullmatch(r"[A-Za-z0-9_]+_smoke", database):
        raise ValueError("AXIOM_MYSQL_SMOKE_DATABASE 必须是以 _smoke 结尾的安全库名")
    if database in {runtime.mysql_database, runtime.mysql_test_database}:
        raise ValueError("smoke 数据库不能与运行库或普通测试库相同")

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
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()

    settings = Settings(mysql_database=database)
    upgrade_database(settings.mysql_url)
    repository = MySQLRepository(settings.mysql_url)
    repository.truncate_all()
    repository.dispose()
    yield settings
    repository = MySQLRepository(settings.mysql_url)
    repository.truncate_all()
    repository.dispose()
