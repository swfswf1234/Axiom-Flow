# 设计索引

状态：Current
最后更新：2026-08-21

本目录保存当前流程、接口、数据模型与验收契约（v2 纪元）。v1 设计已归档
（`docs/history/v1-20260814/design/`）；v0.1 文档体系对齐（V2-015）中使命完成的 Superseded
文档已移入 `docs/history/design/`。元数据规则见 [文档规范](../standards/documentation.md)。

## 当前设计

| 文档 | 设计状态 | 实现状态 | 内容 |
| --- | --- | --- | --- |
| [PDF 解析与渲染管线](parsing-pipeline.md) | Accepted | Partial | 解析管线契约（MinerU 编排、统一格式、兜底通道、产物模型，ingest/schemas/fallback 已实现）；对外契约见 [architecture/api](../architecture/api.md) |
| [模型模式与 MinerU 移交](model-mode-config.md) | Accepted | Implemented | 模型调用模式（local 直连 / qed-engine 经 8900 网关）、`llm_client.py` 兼容层、`--mode`、MinerU 编排移交、`qed_llm_calls` 记录（REQ-044） |
| [af_* 书目同步与块判定](af-books-sync.md) | Draft | Not Started | af_books / af_block_reviews 建表、`POST /books/sync` 幂等 upsert、`/books` 改读 af_books、块判定端点、parse-jobs 回写（REQ-041，待评审）；表结构登记于 [architecture/database-design](../architecture/database-design.md) |
