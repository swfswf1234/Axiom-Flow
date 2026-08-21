# 代码映射（v2）

状态：Current
最后更新：2026-08-21

本表登记受管模块与其设计文档（DesignRef）、测试的对应关系。v1 模块已归档
（`docs/history/v1-20260814/`），不在此列。新包结构在实施计划
（V2-002 起）中落地，本表随实现同步更新。

## 受管模块

| 路径 | 职责 | 状态 | 设计 | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 对外 `/api/v1` 路由，只做协议适配 | Pending | `docs/design/pdf-parsing-and-rendering.md` | 契约测试 | 待 V2-007 实现 |
| `src/axiom_flow/orchestrator/` | 解析任务编排、质量校验、兜底触发 | Pending | `docs/design/pdf-parsing-and-rendering.md` | 集成测试 | 待 V2-004 实现 |
| `src/axiom_flow/ingest/` | PDF 导入、页图渲染、book.json | **Current** | `docs/design/pdf-parsing-and-rendering.md` | `tests/unit/test_ingest.py` | V2-003 完成 |
| `src/axiom_flow/fallback/` | qwen-vl-plus 客户端（页图 → Markdown → blocks 归一化） | **Current** | `docs/design/pdf-parsing-and-rendering.md` | `tests/unit/test_fallback.py` | V2-010 实验落地（V2-006 继承） |
| `src/axiom_flow/schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job） | **Current** | `docs/design/pdf-parsing-and-rendering.md` | `tests/unit/test_schemas.py` | V2-002 完成 |
| `src/axiom_flow/llm_client.py` | VisionClient（双模式视觉调用，local 直连 / qed-engine 网关） | **Current** | `docs/design/model-mode-config.md` | `tests/unit/test_llm_client.py` | V2-014 实现完成 |

## 非受管配置

| 路径 | 职责 |
| --- | --- |
| `scripts/axiom_flow_service.py` | 8902 API 服务唯一启停脚本（start/stop/restart/status，DesignRef：`docs/design/service-lifecycle.md`） |
| `scripts/tmp/` | 临时/试验脚本（V2-010 试验运行器、质量统计、旧冒烟脚本等） |
| `data/` | 运行时工作目录（ignore、可再生；`data/books/<book_id>/` 为解析产物），源书在 `dataset/` 只读 |
| `evaluation/` | 评估专区（样本与报告，与主链路隔离；v1 评测代码已移除，V2 评估体系待 V2-008 重建） |
| `web/` | 已移交 QED-Engine，本仓库不再维护 |
