"""Axiom-Flow v2：QED-Engine 后端解析组件。

包结构：
- api/          对外 /api/v1 路由（ARCH-007）
- orchestrator/ 解析任务编排（ARCH-003）
- ingest/       PDF 导入与页图渲染（V2-003）
- fallback/     qwen-vl-plus local 直连通道（V2-006 兜底已取消）
- schemas.py    统一格式 Pydantic 模型（内外契约单一事实源）
"""

__version__ = "0.4.0"
