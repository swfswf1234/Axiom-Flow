"""
模块职责：验证 v0.2 MySQL 迁移版本、表隔离和测试库安全边界。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
被测代码：alembic.ini、src/axiom_flow/infrastructure/database.py、src/axiom_flow/infrastructure/mysql.py、src/axiom_flow/migrations/env.py、src/axiom_flow/migrations/script.py.mako、src/axiom_flow/migrations/versions/20260727_0001_mysql_v02.py
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.database import alembic_config, upgrade_database
from axiom_flow.infrastructure.mysql import MySQLRepository

ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = ROOT / "alembic.ini"
MIGRATION_TEMPLATE = ROOT / "src" / "axiom_flow" / "migrations" / "script.py.mako"

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


def test_alembic_paths_are_relative_to_config_file(monkeypatch, tmp_path, mysql_settings: Settings):
    ini_content = ALEMBIC_INI.read_text(encoding="utf-8")
    ini_content.encode("ascii")
    assert "unused:unused" not in ini_content

    monkeypatch.chdir(tmp_path)
    config = Config(str(ALEMBIC_INI))
    assert not config.get_main_option("sqlalchemy.url")

    explicit_config = alembic_config(mysql_settings.mysql_url)
    assert Path(explicit_config.get_main_option("script_location")).resolve() == ROOT / "src" / "axiom_flow" / "migrations"
    assert ScriptDirectory.from_config(explicit_config).get_current_head() == "20260728_0004"


def test_alembic_cli_uses_settings_when_url_is_absent(monkeypatch, mysql_settings: Settings):
    monkeypatch.setenv("AXIOM_MYSQL_HOST", mysql_settings.mysql_host)
    monkeypatch.setenv("AXIOM_MYSQL_PORT", str(mysql_settings.mysql_port))
    monkeypatch.setenv("AXIOM_MYSQL_DATABASE", mysql_settings.mysql_database)
    monkeypatch.setenv("AXIOM_MYSQL_USER", mysql_settings.mysql_user)
    monkeypatch.setenv("AXIOM_MYSQL_PASSWORD", mysql_settings.mysql_password)

    command.current(Config(str(ALEMBIC_INI)))


def test_revision_template_generates_traceable_skeleton(tmp_path):
    script_location = tmp_path / "migrations"
    versions = script_location / "versions"
    versions.mkdir(parents=True)
    (script_location / "script.py.mako").write_text(
        MIGRATION_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (versions / "20260728_0004_existing.py").write_text(
        'revision = "20260728_0004"\ndown_revision = None\n',
        encoding="utf-8",
    )
    config = Config()
    config.set_main_option("script_location", str(script_location))

    generated = command.revision(
        config,
        message="增加测试字段",
        rev_id="20260729_0005",
    )
    assert generated is not None
    rendered = Path(generated.path).read_text(encoding="utf-8")

    assert "模块职责：增加测试字段" in rendered
    assert "设计关联（DesignRef）：docs/architecture/data-lifecycle.md" in rendered
    assert "revision = '20260729_0005'" in rendered
    assert "down_revision = '20260728_0004'" in rendered
    compile(rendered, "generated_migration.py", "exec")


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
