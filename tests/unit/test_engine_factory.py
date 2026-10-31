"""引擎显式分派单测（PLAN-003 W4，守则 MI-2）：配置生效且不自动兜底。

文本类槽位面守 `KA-22`（梳理的引擎名只可能来自 `AXIOM_KP_ENGINE`，缺配／闭集外／未接入都不回落）。

设计关联（DesignRef）：docs/design/model-integration.md（`MI-2`/`MI-13`）、
docs/design/knowledge-analysis.md（`KA-22`）
关联测试：本文件（`tests/unit/test_engine_factory.py`）

隔离：只构造适配器对象，不发网络请求（`build_engine` 仅建实例）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from axiom_flow.config import Settings
from axiom_flow.engines.factory import (
    ENGINE_NAMES,
    IMPLEMENTED_ENGINES,
    IMPLEMENTED_TEXT_ENGINES,
    TEXT_ENGINE_NAMES,
    EngineSelectionError,
    TextSlotError,
    build_engine,
    build_text_engine,
    resolve_engine_name,
    resolve_text_engine_name,
)
from axiom_flow.engines.kp_gateway import KP_SLOT_NAME, KpGatewayEngine
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


class TestTextSlot:
    """文本类槽位（`KA-22`）：梳理的名字只可能来自配置一处，三形都不回落。"""

    def test_the_gateway_slot_is_registered_on_both_faces(self):
        """登记面与实现面同批加名字：只登不接（或只接不登）当场落进「未接入／闭集外」那两形。"""
        assert TEXT_ENGINE_NAMES == (KP_SLOT_NAME,)
        assert IMPLEMENTED_TEXT_ENGINES == (KP_SLOT_NAME,)
        assert set(IMPLEMENTED_TEXT_ENGINES) <= set(TEXT_ENGINE_NAMES)

    def test_the_registered_slot_name_resolves_as_is(self):
        assert resolve_text_engine_name(KP_SLOT_NAME) == KP_SLOT_NAME

    def test_an_empty_slot_name_raises_instead_of_defaulting(self, monkeypatch):
        monkeypatch.setattr("axiom_flow.engines.factory.TEXT_ENGINE_NAMES", ("qwen-plus",))
        monkeypatch.setattr("axiom_flow.engines.factory.IMPLEMENTED_TEXT_ENGINES", ("qwen-plus",))
        with pytest.raises(TextSlotError, match="未配置"):
            resolve_text_engine_name("")
        with pytest.raises(TextSlotError, match="未配置"):
            resolve_text_engine_name("   ")

    def test_a_name_outside_the_closed_set_is_rejected(self):
        """闭集外拒绝：拼错的名字不得被当成某个已接入引擎悄悄跑起来。"""
        with pytest.raises(TextSlotError, match="未知梳理引擎"):
            resolve_text_engine_name("gpt-4o")

    def test_a_registered_but_unwired_slot_is_reported_as_not_connected(self, monkeypatch):
        """与 `MI-13` 同形：登记了名字却没有适配器 ⇒ 报「尚未接入」，不返回任何解析引擎。"""
        monkeypatch.setattr("axiom_flow.engines.factory.TEXT_ENGINE_NAMES", ("qwen-plus",))
        monkeypatch.setattr("axiom_flow.engines.factory.IMPLEMENTED_TEXT_ENGINES", ())
        with pytest.raises(TextSlotError, match="尚未接入"):
            resolve_text_engine_name("qwen-plus")

    def test_text_resolution_never_returns_a_parse_engine_name(self, monkeypatch):
        """两册槽位表不共用：解析侧的合法名在梳理侧就是闭集外，回落通道一条都不留。"""
        monkeypatch.setattr("axiom_flow.engines.factory.TEXT_ENGINE_NAMES", ("qwen-plus",))
        monkeypatch.setattr("axiom_flow.engines.factory.IMPLEMENTED_TEXT_ENGINES", ("qwen-plus",))
        with pytest.raises(TextSlotError, match="未知梳理引擎"):
            resolve_text_engine_name("mineru")

    def test_build_text_engine_constructs_the_gateway_adapter_from_settings(self):
        """构造点只读配置两处：出站地址与等待预算——适配器不自己拼默认值，免得两处事实源。"""
        settings = Settings(data_dir=Path(), engine_gateway_url="http://127.0.0.1:9100", kp_timeout=120)

        engine = build_text_engine(KP_SLOT_NAME, settings)

        assert isinstance(engine, KpGatewayEngine)
        assert (engine.name, engine.base_url, engine.timeout) == (KP_SLOT_NAME, "http://127.0.0.1:9100", 120.0)

    def test_build_text_engine_refuses_every_name_outside_the_registry(self):
        settings = Settings(data_dir=Path(), kp_engine="qwen-plus")
        with pytest.raises(TextSlotError):
            build_text_engine("qwen-plus", settings)
        with pytest.raises(TextSlotError):
            build_text_engine("mineru", settings)
