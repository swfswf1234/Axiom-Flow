# 代码映射（v2）

状态：Current
最后更新：2026-08-14

本表登记受管模块与其设计文档（DesignRef）、测试的对应关系。v1 模块已归档
（`docs/history/v1-20260814/`），不在此列。新包结构在实施计划
（V2-002 起）中落地，本表随实现同步更新。

## 受管模块

| 路径 | 职责 | 状态 | 设计 | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 对外 `/api/v1` 路由，只做协议适配 | Pending | `docs/design/pdf-parsing-and-rendering.md` | 契约测试 | 待 V2-007 实现 |
| `src/axiom_flow/orchestrator/` | 解析任务编排、质量校验、兜底触发 | Pending | `docs/design/pdf-parsing-and-rendering.md` | 集成测试 | 待 V2-004 实现 |
| `src/axiom_flow/ingest/` | PDF 导入、页图渲染、book.json | Pending | `docs/design/pdf-parsing-and-rendering.md` | 单元测试 | 待 V2-003 实现 |
| `src/axiom_flow/fallback/` | qwen-vl-plus 兜底客户端 | Pending | `docs/design/pdf-parsing-and-rendering.md` | 单元测试 | 待 V2-006 实现 |
| `src/axiom_flow/schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job） | **Current** | `docs/design/pdf-parsing-and-rendering.md` | `tests/unit/test_schemas.py` | V2-002 完成 |

## 非受管配置

| 路径 | 职责 |
| --- | --- |
| `scripts/compose.yaml` 与 `scripts/infra-*.ps1` | WSL 容器编排与启停（V2-001） |
| `data/books/` | 解析产物（页图/markdown/blocks/sqlite），不入库 |
| `web/` | 已移交 QED-Engine，本仓库不再维护 |
