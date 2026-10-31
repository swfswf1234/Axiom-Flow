"""引擎显式分派：引擎名 → 适配器实例（守则 MI-2，`docs/design/model-integration.md`；来源 PLAN-003 W4）。

槽位清单与 `docs/design/model-integration.md` 同源；**任何情况下都不回落到其他引擎**
（AGENTS.md「不设自动兜底」与 ADR 0004）：槽位存在但没有适配器、或名字非法，一律抛
`EngineSelectionError`，由 API 层映射为 400。

文本类槽位（梳理，`KA-22`）另立一册：解析请求体可以点名 `engine`，梳理请求体**没有** `engine`
字段（成本归因要有唯一落点），所以它的名字只可能来自 `AXIOM_KP_ENGINE` 一处；缺配、闭集外、
未接入三形一律抛 `TextSlotError`，由 API 层映射为 503（与解析侧的 400 分开：那是请求体的错，
这是服务侧的槽位还没接线）。
"""

from __future__ import annotations

from axiom_flow.config import Settings
from axiom_flow.engines.base import KnowledgeEngine, ParseEngine
from axiom_flow.engines.kp_gateway import KP_SLOT_NAME, KpGatewayEngine
from axiom_flow.engines.mineru import MinerUEngine

# 设计登记的引擎槽位（model-integration.md）；声明 ≠ 已实现。
ENGINE_NAMES: tuple[str, ...] = ("mineru", "paddleocr-vl", "qwen-vl")
IMPLEMENTED_ENGINES: tuple[str, ...] = ("mineru",)
#: 文本类槽位登记面（`KA-22`）：`qed-gateway`＝经 8900 统一中转的梳理网关（R8 甲，PLAN-026 W6）。
#: 启用路径与 `MI-13` 同形：8900 注册单元 → 本仓库新增 `engines/<name>.py` → 名字同时登记进这两个
#: 元组（缺一个就是「已登记未接入」或「闭集外」，API 面读成 503）。
TEXT_ENGINE_NAMES: tuple[str, ...] = (KP_SLOT_NAME,)
IMPLEMENTED_TEXT_ENGINES: tuple[str, ...] = (KP_SLOT_NAME,)


class EngineSelectionError(ValueError):
    """引擎名非法或槽位未实现——API 层映射为 400（不得静默换引擎）。"""


class TextSlotError(ValueError):
    """梳理槽位缺配、闭集外或未接入——API 层映射为 503（不回落成任何具体引擎，也不建批）。"""


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


def resolve_text_engine_name(configured: str) -> str:
    """梳理槽位名的唯一解析点（`KA-22`）：三形各给一条文案，任何一形都不返回别的引擎名。

    与 `resolve_engine_name` 分开是刻意的：那一条有「请求体优先」的回落，这一条只可能读配置一处，
    共用一个函数就等于给梳理开了一条「调用方点名引擎」的通道。
    """
    name = (configured or "").strip()
    if not name:
        raise TextSlotError(
            "梳理引擎槽位未配置：槽位名为空（默认值被显式清空，不回落、不缺省成任何引擎）"
        )
    if name not in TEXT_ENGINE_NAMES:
        registered = ", ".join(TEXT_ENGINE_NAMES) or "（文本类槽位当前一个都没登记）"
        raise TextSlotError(f"未知梳理引擎槽位：{name}（已登记槽位：{registered}）")
    if name not in IMPLEMENTED_TEXT_ENGINES:
        raise TextSlotError(f"梳理引擎槽位尚未接入：{name}（当前可用：无）")
    return name


def build_text_engine(name: str, settings: Settings) -> KnowledgeEngine:
    """按已解析的文本槽位名构造适配器——先过 `resolve_text_engine_name`，闭集外一条都构造不出来。

    登记面与实现面都在构造前面把关：只加适配器不登记（或反之）当场报错，而不是静默返回一个没人
    认得的引擎。出站地址与等待预算只从 `settings` 取（`config.py` 一处默认），适配器不自己拼默认值。
    """
    resolve_text_engine_name(name)
    if name == KP_SLOT_NAME:
        return KpGatewayEngine(settings.engine_gateway_url, timeout=float(settings.kp_timeout))
    raise TextSlotError(f"梳理引擎槽位尚未接入：{name}（当前可用：{', '.join(IMPLEMENTED_TEXT_ENGINES)}）")

