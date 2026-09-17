# 设计索引

状态：Current
最后更新：2026-09-17

本目录保存当前流程、接口、数据模型与验收契约（v2 纪元）。v1 设计已归档
（`docs/history/v1-20260814/design/`）；v0.1 文档体系对齐（V2-015）中使命完成的 Superseded
文档已移入 `docs/history/design/`。元数据规则见 [文档治理规范](../standards/doc-governance.md)。

本目录是**设计职责登记处**：每篇登记「管/不管」边界；后续 plans/ 文档归档晋升或新增设计
文档，先在此确定唯一归属，禁止同类内容多文并存。

## 当前设计

| 文档 | 设计状态 | 实现状态 | 管 | 不管（指向事实源） |
| --- | --- | --- | --- | --- |
| [PDF 解析与渲染管线](parsing-pipeline.md) | Accepted | In Progress | 解析管线内部结构：ingest、引擎适配、归一化、编排落盘、质量信号、统一 blocks 格式 | 对外 API 契约（→ [architecture/api](../architecture/api.md)）；数据库表（→ [architecture/database-design](../architecture/database-design.md)）；引擎配置与对接（→ [model-integration](model-integration.md)） |
| [解析管理闭环](parsing-management.md) | Accepted | Not Started | 书目同步/CRUD、ingest 语义、解析任务/页状态、对照供给与块编辑（af_* 闭环） | 表结构 DDL（→ [architecture/database-design](../architecture/database-design.md)）；端点总表（→ [architecture/api](../architecture/api.md)）；管线内部（→ [parsing-pipeline](parsing-pipeline.md)） |
| [模型与引擎对接](model-integration.md) | Accepted | In Progress | 引擎配置（AXIOM_OCR_ENGINE）、适配器接口、MinerU/PaddleOCR-VL/qwen-vl 接入、与 8900 边界、调用记录 | 模型生命周期实现（→ 根仓库 8900）；管线编排（→ [parsing-pipeline](parsing-pipeline.md)） |

## 三态核对（2026-09-14）

- **Superseded**：`af-books-sync.md`、`model-mode-config.md` 随 ARCH-020 重构归档
  `history/design/`（af_* 两表草案被四表设计取代；双模式视觉调用被引擎适配器取代）。
- **Draft**：无残留（原 `af-books-sync.md` 的 Draft 豁免随归档结束）。
- **仍具契约价值**：`parsing-pipeline.md`（重写）、`parsing-management.md`（新增）、
  `model-integration.md`（新增）为当前解析管理闭环的设计事实源。
