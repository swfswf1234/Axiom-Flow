"""共享表 `qed_llm_calls` 的追加写通路：梳理批次的每次模型调用留一行账（`KA-23`）。

本组件对这张表**只有一条 INSERT**——表结构、审核两列与检索面归根仓库 8900，改形状走 ADR 不走这里。
不建 ORM 模型、不进 `Base.metadata`：`af_` 表族守护扫的就是元数据，把共享表挂上去等于认领它。

列的取舍按「有没有事实依据」定：`service`/`endpoint`/`task` 是本通路的常量；`mode`/`provider`/`model`
三枚 `NOT NULL` 身份列只能由装配点注入（只有适配器知道连的是哪家）；`prompt`/`response` 只有适配器
手里才有完整文本，账层拿摘要冒充就是往共享表写假事实，所以留 NULL；归因（哪本书、哪个批次）进
`meta` 扩展槽（根侧定义：未定档字段先入本列，只读不筛）。

失败只降级为 warning：调用账是审计线索，不能反过来把模型调用打挂。发出前的形状闸除外——
身份列给空值会让整条 INSERT 被 MySQL 拒掉，账反而更少，那种错必须当场抛给调用方。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now

logger = logging.getLogger(__name__)

#: 本通路写进共享表的常量面（`service` 值与根仓库约定一致，用于 8900 侧按来源筛选）。
LLM_CALL_SERVICE = "axiom_flow"
LLM_CALL_ENDPOINT = "text"
KP_LEDGER_TASK = "kp-curation"

#: `step` 只允许梳理管线的真实调用腿名（`KA-22` 两阶段；目录阶段自 `KA-35` 起是三条腿：判页／逐页整理／
#: 坏行核对，`KA-37`／`KA-38`），域外即拒。单一 `outline` 已随确定性目录一并退役——一条名字盖不住
#: 三种调用形状，账面就答不出「这一轮目录是哪几页抄来的」。
KP_LEDGER_STEPS: tuple[str, ...] = ("toc_pages", "toc_entries", "toc_verify", "points")
#: `status` 用根侧的两形：成功一行、失败一行，失败原因进 `error`。
KP_LEDGER_STATUSES: tuple[str, ...] = ("success", "error")
#: `mode` 的取值域来自根侧 DDL 注释（api＝经 8900 网关，local＝直连厂商）。
LLM_CALL_MODES: tuple[str, ...] = ("api", "local")

#: `error` 是 VARCHAR(500)：超长会被 MySQL 整条拒收，所以由写入器截断。
MAX_ERROR_CHARS = 500

_INSERT_LEDGER_ROW = """
INSERT INTO qed_llm_calls (service, mode, provider, model, endpoint, duration_ms,
                           status, error, created_at, task, step, meta)
VALUES (:service, :mode, :provider, :model, :endpoint, :duration_ms,
        :status, :error, :created_at, :task, :step, :meta)
"""


@dataclass(frozen=True)
class LlmCallIdentity:
    """三枚 `NOT NULL` 身份列的取值，由装配点随引擎一起注入——账层不猜、不兜底。

    取值语义按根侧 DDL 注释（本层不校验、也不得反着写）：`mode`＝api（经 8900 网关）／local（直连），
    且 `local` 时 `provider` 是 **runtime 名**（lmstudio/llamacpp/mineru 一类）而不是厂商名，
    `model` 是实际模型名。接线的人给错，8900 的筛选面就读错，这不是账层能补救的。
    """

    mode: str
    provider: str
    model: str


@dataclass(frozen=True)
class KpCallRow:
    """一次梳理模型调用的账面事实。`created_at` 不在字段里：时间戳由写入器盖章，不接受调用方给值。"""

    step: str
    status: str
    identity: LlmCallIdentity
    duration_ms: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class LlmCallLedger:
    """`qed_llm_calls` 追加写入器：一条 INSERT、失败降级、永不碰结构。"""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def insert_statement() -> str:
        """本组件对这张表的全部语句面——供守护断言「只有 INSERT」。"""
        return _INSERT_LEDGER_ROW

    def record(self, row: KpCallRow) -> int | None:
        """追加一行调用账，返回自增 `id`；库不可达（含表不存在）时返回 `None` 并记 warning。"""
        params = self._checked(row)
        try:
            with self._session_factory() as session:
                result = session.execute(text(_INSERT_LEDGER_ROW), params)
                session.commit()
                # `qed_llm_calls` 是共享表：按自增 id 精确删自己写的行，不做整表清理
                return int(result.lastrowid)
        except Exception:  # noqa: BLE001 - 记账失败不得影响模型调用本身
            logger.warning(
                "qed_llm_calls 调用账写入降级为不落账（step=%s status=%s）", row.step, row.status, exc_info=True
            )
            return None

    @staticmethod
    def _checked(row: KpCallRow) -> dict[str, Any]:
        """发出前的形状闸：错在参数上就拒，别让一条脏行进共享表污染 8900 的筛选面。"""
        if row.step not in KP_LEDGER_STEPS:
            raise ValueError(f"step 只能是 {KP_LEDGER_STEPS} 之一，收到 {row.step!r}")
        if row.status not in KP_LEDGER_STATUSES:
            raise ValueError(f"status 只能是 {KP_LEDGER_STATUSES} 之一，收到 {row.status!r}")
        identity = row.identity
        values = {"mode": identity.mode, "provider": identity.provider, "model": identity.model}
        for name, value in values.items():
            if not value.strip():
                raise ValueError(f"{name} 是 NOT NULL 且无默认的身份列，不能给空值")
        if identity.mode not in LLM_CALL_MODES:
            raise ValueError(f"mode 只能是 {LLM_CALL_MODES} 之一（api＝经网关，local＝直连厂商），收到 {identity.mode!r}")
        if row.duration_ms is not None and row.duration_ms < 0:
            raise ValueError(f"duration_ms 不能为负，收到 {row.duration_ms}")
        return {
            "service": LLM_CALL_SERVICE,
            "endpoint": LLM_CALL_ENDPOINT,
            "task": KP_LEDGER_TASK,
            "mode": identity.mode,
            "provider": identity.provider,
            "model": identity.model,
            "duration_ms": row.duration_ms,
            "status": row.status,
            # 空 error 写成 NULL 而不是空串：8900 侧「有 error 值」的筛选要能等价于「这行是失败的」
            "error": row.error[:MAX_ERROR_CHARS] if row.error else None,
            # DATETIME 是秒精度：绑字符串而不是 datetime，避开 Python 3.12 已弃用的 sqlite3 默认适配器
            "created_at": utc_now().strftime("%Y-%m-%d %H:%M:%S"),
            "step": row.step,
            "meta": json.dumps(row.meta, ensure_ascii=False, default=str),
        }
