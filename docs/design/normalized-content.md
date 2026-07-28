# 规范化内容与证据

设计状态：Accepted
实现状态：Verified
最后更新：2026-07-27
关联代码：`backend/app/models.py`  
关联测试：`tests/test_v02_pipeline.py`、`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`

## 核心类型

| 类型 | 最小字段 |
| --- | --- |
| `DocumentVersion` | `id`, `document_id`, `content_hash`, `source_path`, `page_count` |
| `ParseRun` | `id`, `document_version_id`, `status`, `router_version`, `started_at` |
| `NormalizedPage` | `id`, `parse_run_id`, `page_no`, `width`, `height`, `markdown`, `quality_state` |
| `ContentBlock` | `id`, `page_id`, `kind`, `content`, `latex`, `order_no`, `confidence` |
| `SourceSpan` | `id`, `block_id`, `page_no`, `bbox`, `quoted_text` |
| `QualityReport` | `id`, `page_id`, `status`, `issues`, `metrics`, `review_state` |

`kind` 至少支持 `heading`、`paragraph`、`formula`、`table`、`figure`、`code` 和 `list`。
`bbox` 缺失时必须显式标注能力限制，不能伪造坐标。v0.2 的块坐标来自 PyMuPDF；视觉
模型块无法匹配原生文字块时 `bbox` 为 `null`。

## 不变量

- `SourceSpan` 必须指向同一 `DocumentVersion` 的真实页。
- 公式无法可靠识别时保留原证据并标记待检查，不得补全推断结果。
- 内容块顺序由 `order_no` 固定，不能依赖模型返回顺序。
