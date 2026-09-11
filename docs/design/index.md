# 设计索引

状态：Current
最后更新：2026-09-11

本目录保存当前流程、接口、数据模型与验收契约（v2 纪元）。v1 设计已归档
（`docs/history/v1-20260814/design/`）；v0.1 文档体系对齐（V2-015）中使命完成的 Superseded
文档已移入 `docs/history/design/`。元数据规则见 [文档治理规范](../standards/doc-governance.md)。

本目录是**设计职责登记处**：每篇登记「管/不管」边界；后续 plans/ 文档归档晋升或新增设计
文档，先在此确定唯一归属，禁止同类内容多文并存。

## 当前设计

| 文档 | 设计状态 | 实现状态 | 管 | 不管（指向事实源） |
| --- | --- | --- | --- | --- |
| [PDF 解析与渲染管线](parsing-pipeline.md) | Accepted | In Progress | 解析编排、统一格式 blocks/page/book/job、质量信号与兜底通道、产物模型 | 对外 API 契约（→ [architecture/api](../architecture/api.md)）；数据库表（→ [architecture/database-design](../architecture/database-design.md)） |
| [模型模式与 MinerU 移交](model-mode-config.md) | Accepted | Implemented | 模型调用模式（local/qed-engine）、`llm_client` 兼容层、`--mode`、MinerU 编排移交、`qed_llm_calls` 记录 | 8900 网关实现（→ 根仓库）；端口/环境变量（→ [local-dev](../standards/local-dev.md)） |
| [af_* 书目同步与块判定](af-books-sync.md) | Draft | Not Started | af_books / af_block_reviews 契约草案、`POST /books/sync` 幂等 upsert、`/books` 读表回退、块判定端点、parse-jobs 回写 | 表结构 DDL（→ [architecture/database-design](../architecture/database-design.md)）；8902 端点总表（→ [architecture/api](../architecture/api.md)） |

## 三态核对（2026-09-11）

- **Superseded**：无残留；使命完成文档已归档 `history/design/`（V2-015）。
- **Draft**：`af-books-sync.md` 为 [ADR 0002](../adr/0002-pending-design-location.md) 存量豁免，
  就地保留；待用户评审冻结后转 `Accepted`，或随其所属计划迁移。
- **仍具契约价值**：`parsing-pipeline.md`、`model-mode-config.md` 保持原状并更新实现状态。
