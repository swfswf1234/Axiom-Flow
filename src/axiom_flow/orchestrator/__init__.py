"""解析任务与分析（梳理）作业的编排：解析＝全本/逐页调引擎 → 归一化 → 分块 → 落盘 → 回写 af_*；
梳理＝受理门 → 取证集 → 目录阶段 → 知识点阶段 → 收尾 → 批次流水一行（`op_kind='kp'`）。

设计事实源：`docs/design/parsing-pipeline.md` 与 `docs/design/knowledge-analysis.md`；
任务与页状态表见 `docs/architecture/database-design.md`。
"""

from axiom_flow.orchestrator.jobs import JobRunner
from axiom_flow.orchestrator.knowledge import (
    KpAcceptance,
    KpGateError,
    accept_kp_job,
    anchor_review_flags,
    finalize_batch,
    run_knowledge_job,
)
from axiom_flow.orchestrator.pipeline import (
    MODEL_UNAVAILABLE,
    ParseOutcome,
    rebuild_chunks,
    run_parse_job,
)

__all__ = [
    "MODEL_UNAVAILABLE",
    "JobRunner",
    "KpAcceptance",
    "KpGateError",
    "ParseOutcome",
    "accept_kp_job",
    "anchor_review_flags",
    "finalize_batch",
    "rebuild_chunks",
    "run_knowledge_job",
    "run_parse_job",
]
