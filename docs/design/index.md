# 设计索引

状态：Current
最后更新：2026-08-20

本目录保存当前流程、接口、数据模型与验收契约（v2 纪元）。v1 设计已归档
（`docs/history/v1-20260814/design/`）。元数据规则见 [文档规范](../standards/documentation.md)。

## 当前设计

| 文档 | 设计状态 | 实现状态 | 内容 |
| --- | --- | --- | --- |
| [PDF 解析与渲染（v2 探索基线）](pdf-parsing-and-rendering.md) | Accepted | Partial | MinerU 解析、统一格式、兜底通道、API 契约、产物模型（ingest/schemas 已实现） |
| [8902 集成契约与联调对齐](8902-integration-contract.md) | Accepted | Pending | C 组 8900↔8902 联调时序、8902 API 契约草案、待对齐问题（V2-007 冻结） |
| [服务生命周期脚本](service-lifecycle.md) | Accepted | Implemented | 8902 服务启停脚本契约（start/stop/restart/status）与 8900 接入方式 |
| [生命周期脚本编码修复](service-lifecycle-encoding-fix.md) | Accepted | Implemented | `_pid_is_alive` 容忍中文 Windows tasklist GBK 输出（errors=replace + stdout 空值兜底） |
| [af_* 书目同步与块判定](af-books-sync.md) | Draft | Not Started | af_books / af_block_reviews 建表、`POST /books/sync` 幂等 upsert、`/books` 改读 af_books、块判定端点、parse-jobs 回写（REQ-041，待评审） |
| [模型模式与 MinerU 移交](model-mode-config.md) | Draft | Not Started | 模型调用模式（local 直连 / qed-engine 经 8900 网关）、`llm_client.py` 兼容层、`--mode`、MinerU 编排移交、`qed_llm_calls` 记录（REQ-044，待评审） |
