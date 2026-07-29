# 架构文档

设计状态：Accepted
实现状态：Implemented
最后更新：2026-07-29
关联代码：受管实现见 `docs/architecture/code-map.md`
关联测试：`tests/test_architecture_dependencies.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0012-backend-package-boundaries.md`

本目录只描述已经接受的系统边界和当前实现结构。

| 文档 | 职责 |
| --- | --- |
| [`overview.md`](overview.md) | 产品边界和主数据流 |
| [`runtime-architecture.md`](runtime-architecture.md) | Backend 分层、任务和运行组件 |
| [`domain-boundaries.md`](domain-boundaries.md) | 领域职责与跨域约束 |
| [`data-lifecycle.md`](data-lifecycle.md) | 数据事实来源、版本和清理边界 |
| [`code-map.md`](code-map.md) | 代码、设计和测试的唯一映射事实源 |

架构文档的 `Verified` 只表示其关联门禁已经通过；当前远端 CI 未通过的文档使用
`Implemented`，避免把本地测试结果扩大为发布结论。
