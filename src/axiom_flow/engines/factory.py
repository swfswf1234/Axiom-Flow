"""引擎显式分派：引擎名 → 适配器实例（守则 MI-2，`docs/design/model-integration.md`；来源 PLAN-003 W4）。

槽位清单与 `docs/design/model-integration.md` 同源；**任何情况下都不回落到其他引擎**
（AGENTS.md「不设自动兜底」与 ADR 0004）：槽位存在但没有适配器、或名字非法，一律抛
`EngineSelectionError`，由 API 层映射为 400。
"""

from __future__ import annotations

from axiom_flow.config import Settings
from axiom_flow.engines.base import ParseEngine
from axiom_flow.engines.mineru import MinerUEngine

# 设计登记的引擎槽位（model-integration.md）；声明 ≠ 已实现。
ENGINE_NAMES: tuple[str, ...] = ("mineru", "paddleocr-vl", "qwen-vl")
IMPLEMENTED_ENGINES: tuple[str, ...] = ("mineru",)


class EngineSelectionError(ValueError):
    """引擎名非法或槽位未实现——API 层映射为 400（不得静默换引擎）。"""


def resolve_engine_name(requested: str | None, configured: str) -> str:
    """请求体 `engine` 优先，缺省回落 `AXIOM_OCR_ENGINE`；两者都要落在已实现槽位内。"""
    name = (requested or configured or "").strip()
    if not name:
        raise EngineSelectionError("未指定解析引擎：请设置 AXIOM_OCR_ENGINE 或在请求体传 engine")
    if name not in ENGINE_NAMES:
        raise EngineSelectionError(f"未知解析引擎：{name}（已登记槽位：{', '.join(ENGINE_NAMES)}）")
    if name not in IMPLEMENTED_ENGINES:
        raise EngineSelectionError(f"解析引擎尚未实现：{name}（当前可用：{', '.join(IMPLEMENTED_ENGINES)}）")
    return name


def build_engine(name: str, settings: Settings) -> ParseEngine:
    """按已解析的引擎名构造适配器实例（直连配置取自 settings）。"""
    if name == "mineru":
        return MinerUEngine(settings.ocr_model_url, timeout=float(settings.ocr_timeout))
    raise EngineSelectionError(f"解析引擎尚未实现：{name}（当前可用：{', '.join(IMPLEMENTED_ENGINES)}）")
