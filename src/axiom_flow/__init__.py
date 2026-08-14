"""Axiom-Flow v2：QED-Engine 后端解析组件。

包结构：
- api/          对外 /api/v1 路由（V2-007）
- orchestrator/ 解析任务编排（V2-004）
- ingest/       PDF 导入与页图渲染（V2-003）
- fallback/     qwen-vl-plus 兜底通道（V2-006）
- schemas.py    统一格式 Pydantic 模型（内外契约单一事实源）
"""

__version__ = "0.4.0"
