"""解析任务编排：全本/逐页调引擎 → 归一化 → 分块 → 落盘 → 回写 af_*。

设计事实源：`docs/design/parsing-pipeline.md`；任务与页状态表见
`docs/architecture/database-design.md`。
"""

from axiom_flow.orchestrator.jobs import JobRunner
from axiom_flow.orchestrator.pipeline import (
    MODEL_UNAVAILABLE,
    ParseOutcome,
    rebuild_chunks,
    run_parse_job,
)

__all__ = ["MODEL_UNAVAILABLE", "JobRunner", "ParseOutcome", "rebuild_chunks", "run_parse_job"]
