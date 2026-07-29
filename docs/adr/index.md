# 架构决策索引

状态：Current
最后更新：2026-07-29

ADR 使用全局四位编号。Proposed/Accepted 位于本目录，Rejected/Superseded 位于
[`../history/adr/`](../history/adr/index.md)；完整规则见
[ADR 治理规范](../standards/adr-governance.md)；新文件遵守对应 standard，并参考最近一份仍有效的
同类文档组织内容。

下一个可用编号：0018

| ADR | 标题 | 领域 | 决策阶段 | 状态 | 取代关系 |
| --- | --- | --- | --- | --- | --- |
| [`0001`](../history/adr/0001-local-first-and-storage.md) | 本地优先与运行数据存储 | 数据与持久化 | v0.2 | Superseded | 被 0005 取代 |
| [`0002`](../history/adr/0002-parser-routing-and-provider-boundary.md) | 分层解析路由与供应商适配边界 | 解析与评测 | v0.2 | Superseded | 被 0010 取代 |
| [`0003`](0003-excel-publish-source-of-truth.md) | Excel 显式发布工作流 | 审阅与发布 | v0.2 | Accepted | — |
| [`0004`](../history/adr/0004-v02-http-api-boundary.md) | v0.2 HTTP API 与静态工作台边界 | API 与任务 | v0.2 | Superseded | 被 0006 取代 |
| [`0005`](0005-mysql-runtime-storage.md) | MySQL 运行事实源与版本化迁移 | 数据与持久化 | v0.2 | Accepted | 取代 0001 |
| [`0006`](0006-persistent-jobs-and-api-v1.md) | MySQL 持久任务与 API v1 | API 与任务 | v0.3 | Accepted | 取代 0004 |
| [`0007`](0007-versioned-domain-records.md) | 版本化领域记录与受保护重建 | 数据与持久化 | v0.3 | Accepted | — |
| [`0008`](0008-immutable-parse-artifact-bundles.md) | 不可变解析产物包与逐页检查点 | 解析与评测 | v0.3 | Accepted | — |
| [`0009`](0009-reject-current-rudin-parser-route.md) | 拒绝当前 Rudin 扫描教材解析路线 | 解析与评测 | v0.3 | Accepted | — |
| [`0010`](0010-qwen-ocr-only-rudin-trial.md) | Rudin 试跑采用 qwen-vl-ocr 单模型路线 | 解析与评测 | v0.3 | Accepted | 取代 0002 |
| [`0011`](0011-current-parse-run-and-prunable-artifacts.md) | 显式当前解析运行与可清理产物 | 数据与持久化 | v0.3 | Accepted | — |
| [`0012`](0012-backend-package-boundaries.md) | Backend 包边界一次性收口 | 工程治理 | v0.3 | Accepted | — |
| [`0013`](0013-selective-history-retention.md) | 选择性历史保留与 Git 锚点 | 工程治理 | v0.3 | Accepted | — |
| [`0014`](0014-risk-based-plan-lifecycle.md) | 风险分级计划准入与单一生命周期 | 工程治理 | v0.3 | Accepted | — |
| [`0015`](0015-standards-as-governance-source.md) | 标准作为治理事实源与变更门禁 | 工程治理 | v0.3 | Accepted | — |
| [`0016`](0016-remove-document-templates.md) | 删除文档模板目录 | 工程治理 | v0.3 | Accepted | — |
| [`0017`](0017-consolidated-task-ledgers-and-roadmap.md) | 统一任务台账与无状态能力路线图 | 工程治理 | v0.3 | Accepted | — |
