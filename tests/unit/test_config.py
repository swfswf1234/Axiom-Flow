"""config.py 单元测试：env 优先级、.env 合并、密钥隔离与默认值（V2-014）。"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from axiom_flow.config import DataRootError, llm_api_key, load_settings
from axiom_flow.db.engine import mysql_url, utc_now

REPO_ROOT = Path(__file__).resolve().parents[2]

_ENV_KEYS = (
    "API_KEY",
    "AXIOM_API_KEY",
    "QED_DATA_ROOT",
    "AXIOM_FLOW_DATA_DIR",
    "AXIOM_OCR_ENGINE",
    "QED_OCR_MODEL_URL",
    "AXIOM_OCR_TIMEOUT",
    "AXIOM_PARSE_WINDOW_SIZE",
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
    """隔离真实环境与仓库 .env：cwd 移至 tmp_path，清空相关环境变量，给出已存在的临时数据根。

    ADR 0009 后缺 `QED_DATA_ROOT` 即报错，故夹具默认注入临时根；测缺配行为的用例
    自行 `monkeypatch.delenv("QED_DATA_ROOT")`。
    """
    monkeypatch.chdir(tmp_path)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    data_root = tmp_path / "dataset"
    data_root.mkdir()
    monkeypatch.setenv("QED_DATA_ROOT", str(data_root))
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

    def test_builtin_defaults_when_only_data_root_set(self, isolated):
        settings = load_settings()

        assert settings.vision_model == "qwen-vl-plus"
        assert settings.ocr_engine == "mineru"
        assert settings.ocr_model_url == "http://127.0.0.1:5002"
        assert settings.ocr_timeout == 600
        assert settings.db_host == "127.0.0.1"
        assert settings.db_port == 3306
        assert settings.db_name == "qed"
        assert settings.db_user == "root"
        assert settings.db_password == ""
        assert settings.port == 8902
        assert settings.data_dir == isolated / "dataset"


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
        (isolated / ".env").write_bytes(b"\xef\xbb\xbfQED_DB_NAME=from-bom\n")

        assert load_settings().db_name == "from-bom"


class TestDotenvInlineComment:
    """DEFECT-008：值尾行内 `#` 注释剥离（根 `.env` 用 `值 # 说明` 写法，不剥离即成为坏 URL）。"""

    def test_inline_comment_after_value_is_stripped(self, isolated):
        (isolated / ".env").write_text(
            "QED_OCR_MODEL_URL=http://127.0.0.1:5002 # 本地 MinerU 容器\n", encoding="utf-8"
        )

        assert load_settings().ocr_model_url == "http://127.0.0.1:5002"

    def test_hash_inside_quotes_is_preserved(self, isolated):
        (isolated / ".env").write_text('AXIOM_VISION_MODEL="qwen # vl"\n', encoding="utf-8")

        assert load_settings().vision_model == "qwen # vl"

    def test_hash_without_preceding_space_stays_in_value(self, isolated):
        (isolated / ".env").write_text("QED_DB_NAME=qed#mine\n", encoding="utf-8")

        assert load_settings().db_name == "qed#mine"

    def test_password_inline_comment_stripped_and_not_leaked(self, isolated):
        (isolated / ".env").write_text("QED_DB_PASSWORD=pw123 # 口令\n", encoding="utf-8")

        settings = load_settings()

        assert settings.db_password == "pw123"
        assert "#" not in repr(settings)


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
    """data_dir（ADR 0009）：唯一事实源 `QED_DATA_ROOT`；缺配、仓内留档区、目录不存在均拒绝启动。"""

    def test_data_root_var_wins_over_default(self, isolated, monkeypatch):
        target = isolated / "dataset-root"
        target.mkdir()
        monkeypatch.setenv("QED_DATA_ROOT", str(target))

        assert load_settings().data_dir == target

    def test_missing_data_root_raises(self, isolated, monkeypatch):
        """缺 `QED_DATA_ROOT` 即报错：无仓内静默回退（Q1 裁决 = 直接报错）。"""
        monkeypatch.delenv("QED_DATA_ROOT", raising=False)

        with pytest.raises(DataRootError, match="QED_DATA_ROOT"):
            load_settings()

    def test_nonexistent_data_root_raises(self, isolated, monkeypatch):
        """目录不存在即报错：不自动创建（决定 1；自动创建会把 `.env` 手误变成真实目录）。"""
        monkeypatch.setenv("QED_DATA_ROOT", str(isolated / "does-not-exist"))

        with pytest.raises(DataRootError, match="QED_DATA_ROOT"):
            load_settings()

    def test_repo_data_dir_is_rejected(self, isolated):
        """仓内 `data/` 永久不是数据根：配置成它同样拒绝（ADR 0009 决定 2／ADR 0012 决定 2：该目录已退役）。"""
        with pytest.raises(DataRootError, match="QED_DATA_ROOT"):
            load_settings(data_dir=REPO_ROOT / "data")

    def test_retired_flow_var_is_ignored(self, isolated, monkeypatch):
        """AXIOM_FLOW_DATA_DIR 已删除（配置简化）：设置旧键不生效，数据根仍指夹具临时根。"""
        monkeypatch.setenv("AXIOM_FLOW_DATA_DIR", str(isolated / "flow"))

        assert load_settings().data_dir == isolated / "dataset"


class TestEngineConfig:
    """引擎配置：AXIOM_OCR_ENGINE / QED_OCR_MODEL_URL / AXIOM_OCR_TIMEOUT（PLAN-002）。"""

    def test_engine_defaults(self, isolated):
        settings = load_settings()

        assert settings.ocr_engine == "mineru"
        assert settings.ocr_model_url == "http://127.0.0.1:5002"
        assert settings.ocr_timeout == 600

    def test_engine_env_overrides(self, isolated, monkeypatch):
        monkeypatch.setenv("AXIOM_OCR_ENGINE", "paddleocr-vl")
        monkeypatch.setenv("QED_OCR_MODEL_URL", "http://127.0.0.1:9001/")
        monkeypatch.setenv("AXIOM_OCR_TIMEOUT", "900")

        settings = load_settings()

        assert settings.ocr_engine == "paddleocr-vl"
        assert settings.ocr_model_url == "http://127.0.0.1:9001"
        assert settings.ocr_timeout == 900

    def test_legacy_mineru_url_key_is_ignored(self, isolated, monkeypatch):
        """QED_MINERU_URL 已删除且无别名：设置旧键不改变 ocr_model_url。"""
        monkeypatch.setenv("QED_MINERU_URL", "http://127.0.0.1:8002")

        assert load_settings().ocr_model_url == "http://127.0.0.1:5002"

    def test_removed_gateway_keys_are_ignored(self, isolated, monkeypatch):
        """模式/网关键已删除：设置旧键不影响 Settings（无对应字段）。"""
        monkeypatch.setenv("QED_API_SELECT", "qed-engine")
        monkeypatch.setenv("QED_LLM_GATEWAY_URL", "http://127.0.0.1:9999")
        monkeypatch.setenv("QED_LLM_TIMEOUT", "10")

        settings = load_settings()

        assert not hasattr(settings, "api_select")
        assert not hasattr(settings, "llm_gateway_url")

    def test_invalid_ocr_timeout_falls_back_to_default(self, isolated, monkeypatch):
        monkeypatch.setenv("AXIOM_OCR_TIMEOUT", "notnum")

        assert load_settings().ocr_timeout == 600

    def test_parse_window_size_default_and_override(self, isolated, monkeypatch):
        """PLAN-007：56 页/块由真机 W0 冻结（b05 前 56 页 245s，且 ≤ 容器 `processing_window_size`）。"""
        assert load_settings().parse_window_size == 56

        monkeypatch.setenv("AXIOM_PARSE_WINDOW_SIZE", "30")
        assert load_settings().parse_window_size == 30


class TestOverrides:
    """load_settings 关键字覆盖优先于 env。"""

    def test_keyword_overrides_win(self, isolated, monkeypatch):
        monkeypatch.setenv("QED_DB_PORT", "3306")
        monkeypatch.setenv("AXIOM_VISION_MODEL", "qwen-vl-ocr")

        settings = load_settings(db_port=5432, vision_model="custom-model")

        assert settings.db_port == 5432
        assert settings.vision_model == "custom-model"


class TestDatabase:
    """db/engine.py 轻量单测：DSN 拼装与 naive UTC。"""

    def test_mysql_url_renders_pymysql_dsn(self, isolated):
        settings = load_settings(db_password="swfswf123")

        assert mysql_url(settings) == "mysql+pymysql://root:swfswf123@127.0.0.1:3306/qed?charset=utf8mb4"

    def test_utc_now_is_naive_utc(self):
        now = utc_now()

        assert now.tzinfo is None
        expected = datetime.now(UTC).replace(tzinfo=None)
        assert abs(now - expected) < timedelta(seconds=5)
