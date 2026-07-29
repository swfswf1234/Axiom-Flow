# 架构决策记录

状态：Current
最后更新：2026-07-29

ADR 记录会改变系统边界、公开 API、持久化语义、事实来源或解析路由的决策。Accepted ADR 不可
静默改写；新决定通过新增 ADR 或明确的 Superseded 关系演进。

| 范围 | ADR | 状态 |
| --- | --- | --- |
| 历史存储与路由 | [`0001`](0001-local-first-and-storage.md)、[`0002`](0002-parser-routing-and-provider-boundary.md) | Superseded |
| 发布与 API | [`0003`](0003-excel-publish-source-of-truth.md)、[`0004`](0004-v02-http-api-boundary.md)、[`0006`](0006-persistent-jobs-and-api-v1.md) | 0003、0006 Accepted；0004 Superseded |
| MySQL 与版本记录 | [`0005`](0005-mysql-runtime-storage.md)、[`0007`](0007-versioned-domain-records.md) | Accepted |
| 解析产物与当前运行 | [`0008`](0008-immutable-parse-artifact-bundles.md)、[`0011`](0011-current-parse-run-and-prunable-artifacts.md) | Accepted |
| Rudin 路由实验 | [`0009`](0009-reject-current-rudin-parser-route.md)、[`0010`](0010-qwen-ocr-only-rudin-trial.md) | Accepted；0010 限定恢复工程试跑 |
| Backend 边界 | [`0012`](0012-backend-package-boundaries.md) | Accepted |

新 ADR 使用 [`../templates/adr.md`](../templates/adr.md)，并同步更新本索引和关联设计。
