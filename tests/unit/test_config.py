"""config.py 单元测试：env 优先级、.env 合并、密钥隔离与默认值（V2-014）。"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axiom_flow.config import llm_api_key, load_settings
from axiom_flow.database import mysql_url, utc_now

REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_KEYS = (
    "API_KEY",
    "AXIOM_API_KEY",
    "AXIOM_FLOW_DATA_DIR",
    "AXIOM_DATA_DIR",
    "QED_API_SELECT",
    "QED_LLM_GATEWAY_URL",
    "AXIOM_VISION_MODEL",
    "QED_DB_HOST",
    "QED_DB_PORT",
    "QED_DB_NAME",
    "QED_DB_USER",
    "QED_DB_PASSWORD",
    "AXIOM_PORT",
)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """隔离真实环境与仓库 .env：cwd 移至 tmp_path，清空相关环境变量。"""
    monkeypatch.chdir(tmp_path)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    return tmp_path


class TestLoadSettingsPriority:
    """env 优先级：真实环境变量 > 自身 .env > 父 .env（向上走查）> 内置默认。"""

    def test_real_env_beats_dotenv(self, isolated, monkeypatch):
        (isolated / ".env").write_text("QED_DB_NAME=from_file\n", encoding="utf-8")
        monkeypatch.setenv("QED_DB_NAME", "from_env")

        assert load_settings().db_name == "from_env"

    def test_own_dotenv_beats_parent_dotenv(self, isolated, monkeypatch):
        child = isolated / "child"
        child.mkdir()
        (isolated / ".env").write_text("QED_DB_NAME=parent\n", encoding="utf-8")
        (child / ".env").write_text("QED_DB_NAME=child\n", encoding="utf-8")
        monkeypatch.chdir(child)

        assert load_settings().db_name == "child"

    def test_builtin_defaults_when_nothing_set(self, isolated):
        settings = load_settings()

        assert settings.api_select == "local"
        assert settings.llm_gateway_url == "http://127.0.0.1:8900"
        assert settings.vision_model == "qwen-vl-plus"
        assert settings.db_host == "127.0.0.1"
        assert settings.db_port == 3306
        assert settings.db_name == "qed"
        assert settings.db_user == "root"
        assert settings.db_password == ""
        assert settings.port == 8902
        assert settings.data_dir == REPO_ROOT / "data"


class TestEnvIntTolerance:
    """坏值环境变量不阻断启动：int 转换失败回退内置默认。"""

    def test_invalid_db_port_env_falls_back_to_default(self, isolated, monkeypatch):
        monkeypatch.setenv("QED_DB_PORT", "abc")

        assert load_settings().db_port == 3306

    def test_invalid_port_env_falls_back_to_default(self, isolated, monkeypatch):
        monkeypatch.setenv("AXIOM_PORT", "notnum")

        assert load_settings().port == 8902


class TestDotenvIsolation:
    """.env 只读合并，不污染 os.environ。"""

    def test_dotenv_not_exported_to_os_environ(self, isolated):
        (isolated / ".env").write_text("API_KEY=sk-secret\nQED_DB_PASSWORD=pw\n", encoding="utf-8")

        load_settings()

        assert llm_api_key() == "sk-secret"
        assert "API_KEY" not in os.environ
        assert "QED_DB_PASSWORD" not in os.environ

    def test_empty_value_skipped_falls_through_to_parent(self, isolated, monkeypatch):
        child = isolated / "child"
        child.mkdir()
        (child / ".env").write_text("API_KEY=\n", encoding="utf-8")
        (isolated / ".env").write_text("API_KEY=parent\n", encoding="utf-8")
        monkeypatch.chdir(child)

        assert llm_api_key() == "parent"

    def test_empty_value_with_no_fallback_yields_empty(self, isolated):
        (isolated / ".env").write_text("API_KEY=\n", encoding="utf-8")

        assert llm_api_key() == ""

    def test_utf8_bom_dotenv_first_key_parsed(self, isolated):
        (isolated / ".env").write_bytes(b"\xef\xbb\xbfQED_API_SELECT=local\n")

        assert load_settings().api_select == "local"


class TestLlmApiKey:
    """llm_api_key 只读唯一密钥 API_KEY；AXIOM_API_KEY 退役无回退。"""

    def test_reads_api_key(self, isolated, monkeypatch):
        monkeypatch.setenv("API_KEY", "sk-real")
        monkeypatch.delenv("AXIOM_API_KEY", raising=False)

        assert llm_api_key() == "sk-real"

    def test_axiom_api_key_alone_does_not_satisfy(self, isolated, monkeypatch):
        monkeypatch.setenv("AXIOM_API_KEY", "sk-old")
        monkeypatch.delenv("API_KEY", raising=False)

        assert llm_api_key() == ""


class TestSettingsPrivacy:
    """密钥字段不进 Settings repr。"""

    def test_repr_excludes_db_password(self, isolated):
        settings = load_settings(db_password="swfswf123")

        assert settings.db_password == "swfswf123"
        assert "swfswf123" not in repr(settings)

    def test_db_configured_reflects_password(self, isolated):
        assert load_settings().db_configured is False
        assert load_settings(db_password="pw").db_configured is True


class TestDataDir:
    """data_dir：AXIOM_FLOW_DATA_DIR > AXIOM_DATA_DIR > 仓库 data/。"""

    def test_flow_var_wins(self, isolated, monkeypatch):
        monkeypatch.setenv("AXIOM_FLOW_DATA_DIR", str(isolated / "flow-data"))
        monkeypatch.setenv("AXIOM_DATA_DIR", str(isolated / "plain-data"))

        assert load_settings().data_dir == isolated / "flow-data"

    def test_data_dir_var_wins_over_default(self, isolated, monkeypatch):
        monkeypatch.setenv("AXIOM_DATA_DIR", str(isolated / "plain-data"))

        assert load_settings().data_dir == isolated / "plain-data"

    def test_default_is_repo_data(self, isolated):
        assert load_settings().data_dir == REPO_ROOT / "data"


class TestOverrides:
    """load_settings 关键字覆盖优先于 env。"""

    def test_keyword_overrides_win(self, isolated, monkeypatch):
        monkeypatch.setenv("QED_DB_PORT", "3306")
        monkeypatch.setenv("AXIOM_VISION_MODEL", "qwen-vl-ocr")

        settings = load_settings(db_port=5432, vision_model="custom-model")

        assert settings.db_port == 5432
        assert settings.vision_model == "custom-model"


class TestDatabase:
    """database.py 轻量单测：DSN 拼装与 naive UTC。"""

    def test_mysql_url_renders_pymysql_dsn(self, isolated):
        settings = load_settings(db_password="swfswf123")

        assert mysql_url(settings) == "mysql+pymysql://root:swfswf123@127.0.0.1:3306/qed?charset=utf8mb4"

    def test_utc_now_is_naive_utc(self):
        now = utc_now()

        assert now.tzinfo is None
        expected = datetime.now(UTC).replace(tzinfo=None)
        assert abs(now - expected) < timedelta(seconds=5)
