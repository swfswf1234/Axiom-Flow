"""解析签名与重跑判定：一版产物「用哪一代解析参数跑出来的」由 `af_operations` parse 行的 `parser_signature` 单源自证。

设计事实源：承载计划 PLAN-019 契约草案 C1（输入集已冻结）与
[ADR 0015](../../../docs/adr/0015-db-single-source-for-readiness.md)（判定只看库内读数，不看磁盘）。

- **只含影响块产物的参数**：引擎名 + 模型服务 `version`/`protocol_version` + 块策略版本。超时只改
  成败、提交窗口只改批次、分块规则只改 chunks，都不改 blocks 内容——入签名就等于「改一行配置触发
  全本重跑判定」。
- **本模块不探模型服务**：版本读数由调用方（受理面）注入。默认门禁不许打外部服务，而「探不到就不
  建签名」这条 fail-closed 判据在构造 `SignatureInputs` 时即已成立，不依赖调用方自觉。
- **哨兵 `''` 只有一种含义**：该行未记录签名（存量代际）。生效侧的 `''` 判 `signature_unknown`；
  当前侧的 `''` 是调用方漏算，直接报错——两者混用会把「没算」读成「一个代际」。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

from axiom_flow.engines.mineru import _OUTPUT_FLAGS
from axiom_flow.normalize.blocks import _TYPE_MAP

Verdict = Literal["up_to_date", "resume", "need_reparse", "never_parsed"]

#: 未记录签名（迁移回填的存量代际）。只判需重跑，不判过时，也不回算补签。
UNKNOWN_SIGNATURE = ""

#: 冻结的签名输入集（键名即 JSON 键，按字典序参与 canonical 串）。
SIGNATURE_INPUT_KEYS: tuple[str, ...] = (
    "block_policy_version",
    "ocr_engine",
    "protocol_version",
    "service_version",
)


def canonical_json(payload: Mapping[str, Any]) -> str:
    """规范化串：键按字典序、无多余空白。同一事实只允许一种写法，否则同参数会算出两个签名。"""
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SignatureInputs:
    """一次建版本所需的全部签名输入（缺一项就不建签名）。

    `service_version`/`protocol_version` 是模型服务 `/health` 的**实际回文字段**（W1 已取证），
    由调用方注入；本模块不发起探活，因为受理前的探活属于 api 面。
    """

    ocr_engine: str
    service_version: str
    protocol_version: int | str
    block_policy_version: str

    def __post_init__(self) -> None:
        for name in ("ocr_engine", "service_version", "protocol_version", "block_policy_version"):
            value = getattr(self, name)
            if value is None or str(value) == "":
                raise ValueError(f"签名输入缺失：{name} 不得为空（探不到读数就不建签名，不臆造代际）")


def block_policy_payload() -> dict[str, Any]:
    """块策略输入集：类型映射表 + 产物形态开关，两处代码级常量的原样快照。

    取代码级派生值而另设配置位，是为了避免「配置写一套、代码算一套」的第二判据；分块侧常量
    （`chunking.py` 的跳过集等）属 `chunk_policy_version`，不入签名。
    """
    return {
        "output_flags": {str(k): str(v) for k, v in _OUTPUT_FLAGS.items()},
        "type_map": {str(k): str(v) for k, v in _TYPE_MAP.items()},
    }


def block_policy_version() -> str:
    """块策略表的规范化哈希：改一行映射或一个产物开关都会翻转签名。"""
    return _sha256_hex(canonical_json(block_policy_payload()))


def signature_payload(inputs: SignatureInputs) -> dict[str, Any]:
    return asdict(inputs)


def signature_canonical(inputs: SignatureInputs) -> str:
    return canonical_json(signature_payload(inputs))


def compute_signature(inputs: SignatureInputs) -> str:
    """SHA-256(键按字典序的 JSON 规范化串)，落 `af_operations` parse 行的 `parser_signature`。"""
    return _sha256_hex(signature_canonical(inputs))


@dataclass(frozen=True, slots=True)
class VersionIdentity:
    """判定所需的最小版本读数：任务号 + 源指纹 + 签名。

    由调用方从 parse 事件行投影而来；仓储返回的 ORM 行也能直接喂进来（同名列）。
    """

    job_id: str
    source_sha256: str
    parser_signature: str


@dataclass(frozen=True, slots=True)
class RerunDecision:
    """重跑判定结果。

    `job_id` 指向该交回调用方的任务：`up_to_date` 回生效版本（客户端读回已有产物即可），
    `resume` 回在飞任务（受理门据此 409，不双跑），`need_reparse`/`never_parsed` 为 `None`。
    """

    verdict: Verdict
    reason: str
    job_id: str | None = None


def decide_rerun(
    *,
    active: VersionIdentity | None,
    current_source_sha256: str,
    current_signature: str,
    in_flight: Sequence[VersionIdentity] = (),
) -> RerunDecision:
    """`(source_sha256, parser_signature)` 双全等才叫「不用重跑」。

    判据只写在这一处，受理门（POST）与只读重跑清单（reparse-plan）共用同一函数：两处各写一遍必
    漂移，而漂移的后果是该不该重跑由端点决定。
    """
    if current_signature == UNKNOWN_SIGNATURE:
        raise ValueError("current_signature 为空：当前签名必须由受理面算出，'' 不是代际而是漏算")
    if current_source_sha256 == UNKNOWN_SIGNATURE:
        raise ValueError("current_source_sha256 为空：源指纹缺失时无法判「参数没变」")

    if (
        active is not None
        and active.parser_signature == current_signature
        and active.source_sha256 == current_source_sha256
    ):
        return RerunDecision(
            verdict="up_to_date", reason="same_generation_and_source", job_id=active.job_id
        )

    for job in in_flight:
        if job.parser_signature == current_signature:
            return RerunDecision(verdict="resume", reason="in_flight_same_signature", job_id=job.job_id)

    if active is None:
        return RerunDecision(verdict="never_parsed", reason="no_active_version")
    if active.parser_signature == UNKNOWN_SIGNATURE:
        # 未知代际只判需重跑：历史参数不可考，任何「参数已变」的结论都是倒推出来的
        return RerunDecision(verdict="need_reparse", reason="signature_unknown", job_id=active.job_id)
    if active.source_sha256 != current_source_sha256:
        return RerunDecision(verdict="need_reparse", reason="source_changed", job_id=active.job_id)
    return RerunDecision(verdict="need_reparse", reason="signature_changed", job_id=active.job_id)
