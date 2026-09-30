"""Axiom-Flow v2：QED-Engine 后端解析组件。

包结构（模块清单以 `docs/architecture/code-map.md` 为准）：
- api/          对外 /api/v1 路由（ARCH-001）
- orchestrator/ 解析任务编排（ARCH-003）
- engines/      引擎适配器与显式分派（ARCH-016）
- normalize/    blocks/markdown 归一化与分块（ARCH-004）
- db/           af_* 表族 ORM 与仓储（ARCH-013/014）
- ingest/       PDF 导入与页图渲染（ARCH-015）
- schemas.py    统一格式 Pydantic 模型（内外契约单一事实源）

已退役：`llm_client.py`、`fallback/`（职责由 `engines/` 承接，PLAN-002）。
"""

__version__ = "0.5.0"
