"""引擎显式分派单测（PLAN-003 W4，守则 MI-2）：配置生效且不自动兜底。

隔离：只构造适配器对象，不发网络请求（`build_engine` 仅建实例）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_flow.config import Settings
from axiom_flow.engines.factory import (
    ENGINE_NAMES,
    IMPLEMENTED_ENGINES,
    EngineSelectionError,
    build_engine,
    resolve_engine_name,
)
from axiom_flow.engines.mineru import MinerUEngine


class TestResolveEngineName:
    def test_payload_wins_over_config(self):
        assert resolve_engine_name("mineru", "paddleocr-vl") == "mineru"

    def test_missing_payload_falls_back_to_config(self):
        assert resolve_engine_name(None, "mineru") == "mineru"

    def test_unimplemented_slot_raises_not_fallback(self):
        """MI-2：槽位已登记但未实现时报错，绝不回落 mineru。"""
        with pytest.raises(EngineSelectionError, match="尚未实现"):
            resolve_engine_name(None, "paddleocr-vl")
        with pytest.raises(EngineSelectionError, match="尚未实现"):
            resolve_engine_name("qwen-vl", "mineru")

    def test_unknown_name_raises(self):
        with pytest.raises(EngineSelectionError, match="未知解析引擎"):
            resolve_engine_name(None, "gpt-4o")

    def test_empty_selection_raises(self):
        with pytest.raises(EngineSelectionError, match="未指定解析引擎"):
            resolve_engine_name(None, "")

    def test_registered_slots_cover_design(self):
        assert ENGINE_NAMES == ("mineru", "paddleocr-vl", "qwen-vl")
        assert set(IMPLEMENTED_ENGINES) <= set(ENGINE_NAMES)


class TestBuildEngine:
    def test_mineru_uses_model_url_and_timeout(self):
        settings = Settings(data_dir=Path(), ocr_model_url="http://127.0.0.1:5002", ocr_timeout=42)
        engine = build_engine("mineru", settings)
        assert isinstance(engine, MinerUEngine)
        assert engine.base_url == "http://127.0.0.1:5002"
        assert engine.timeout == 42.0

    def test_unimplemented_name_raises(self):
        with pytest.raises(EngineSelectionError, match="尚未实现"):
            build_engine("qwen-vl", Settings(data_dir=Path()))
