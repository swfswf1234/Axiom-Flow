# 规范索引

状态：Current
确认状态：已确认
最后更新：2026-09-11

本目录是 Axiom-Flow 工程治理规则的唯一事实源。根 [AGENTS.md](../../AGENTS.md) 负责快速路由，
[文档治理规范](doc-governance.md) 负责文档事实边界与生命周期，[ADR 治理](adr-governance.md)
负责决策登记。v1 时代规范（governance-contract、cross-project-collaboration、
code-document-traceability）已归档至 `docs/history/v1-20260814/standards/`。

| 标准 | 确认状态 | 治理对象 | 关联测试 |
| --- | --- | --- | --- |
| [文档治理规范](doc-governance.md) | 已确认 | 文档分类边界、确认状态、生命周期、代码与文档追溯、版本末期整理、归档与删除 | `tests/contract/test_document_structure.py` 等 |
| [任务生命周期](task-lifecycle.md) | 已确认 | A/B/C/D 分类、计划生命周期、探索节奏 | `tests/contract/test_plan_governance.py`、`test_tracker_governance.py` |
| [测试架构与门禁](testing.md) | 已确认 | 测试分层、门禁组成、测试编写约定 | `tests/contract/test_test_suite_governance.py` |
| [ADR 治理](adr-governance.md) | 已确认 | ADR 编号、结构、状态流转、归档 | `tests/contract/test_adr_governance.py` |
| [跨项目协作流程](cross-project-collaboration.md) | 已确认 | 工作区边界、接收/发起跨项目请求 | `tests/contract/test_standard_governance.py` |
| [本地开发环境](local-dev.md) | 已确认 | 机器标识、Python 环境、端口速查 | 无 |

## 规则

- 标准声明状态、确认状态、最后更新、治理对象、依据、关联测试，并使用统一公共章节
  （目的与边界 / 强制规则 / 执行与门禁 / 变更与取代）。
- 改变规范语义必须先新增 ADR（见 [ADR 索引](../adr/index.md)）。
- 治理模式以 QED-Tracker 与 QED-Engine 根仓库标准为上游参照；本目录只保存适配本仓库的
  副本，语义变化先经 ADR 评审。
