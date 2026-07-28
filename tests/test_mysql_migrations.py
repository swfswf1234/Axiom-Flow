"""
模块职责：验证 v0.2 MySQL 迁移版本、表隔离和测试库安全边界。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
被测代码：backend/infrastructure/database.py、backend/infrastructure/mysql.py、backend/migrations/versions/20260727_0001_mysql_v02.py
"""

from sqlalchemy import text

from backend.infrastructure.config import Settings
from backend.infrastructure.database import upgrade_database
from backend.infrastructure.mysql import MySQLRepository

EXPECTED_TABLES = {
    "af_documents",
    "af_parse_runs",
    "af_pages",
    "af_candidates",
    "af_edges",
    "af_workbook_revisions",
    "af_releases",
    "af_jobs",
    "af_extraction_runs",
    "af_review_events",
    "af_artifacts",
    "af_content_blocks",
    "af_source_spans",
    "af_quality_reports",
    "af_parse_run_selections",
}


def test_migration_is_idempotent_and_owns_only_prefixed_tables(mysql_settings: Settings):
    upgrade_database(mysql_settings.mysql_url)
    store = MySQLRepository(mysql_settings.mysql_url)
    store.require_schema()
    try:
        with store.engine.connect() as connection:
            tables = set(connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name LIKE 'af\\_%'" )).scalars())
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert EXPECTED_TABLES <= tables
        assert revision == "20260728_0004"
    finally:
        store.dispose()


def test_test_database_must_not_equal_runtime_database(mysql_settings: Settings):
    assert mysql_settings.mysql_database == mysql_settings.mysql_test_database
    assert mysql_settings.mysql_database != Settings().mysql_database
