# Axiom-Flow Documentation

状态：Current  
最后更新：2026-07-27

本目录描述当前 Axiom-Flow。它是一个单用户、本地优先的文档质量与知识审阅工作台：PDF
先被解析为可定位的内容，再转为经人工发布的知识。

## 阅读顺序

1. `../AGENTS.md`：任何 Agent 或开发者的执行规则。
2. `architecture/overview.md`：系统边界和主数据流。
3. `architecture/code-map.md`：代码、设计与测试的双向映射。
4. `design/index.md`：各子系统的当前目标设计。
5. `adr/`：为什么采用这些不可逆或高成本决策。
6. `trackers/current.md`：当前正在推进的工作。
7. `plans/`：每项工作的实施与验收定义。

## 文档状态

| 状态 | 含义 |
| --- | --- |
| `Current` | 当前系统事实或已接受目标，按文档声明区分。 |
| `Proposed` | 已形成方案，尚未接受或实施。 |
| `Implemented` | 已实现且已验证的能力。 |
| `Completed` | 已完成且已形成阶段关闭记录；不表示后续增强已完成。 |
| `Blocked` | 因明确的外部前置条件暂停；恢复条件必须写入计划或追踪器。 |
| `Deprecated` | 不再作为活跃入口，保留迁移说明。 |
| `Historical` | 仅用于追溯，不能覆盖当前设计。 |

## 目录职责

| 目录 | 作用 |
| --- | --- |
| `architecture/` | 已接受的系统边界、领域关系与数据生命周期。 |
| `design/` | 目标接口、数据模型、状态机和验收约束。 |
| `adr/` | 关键决策及其后果。 |
| `plans/` | 可执行实施计划及完成条件。 |
| `trackers/` | 当前工作、路线图和待办状态。 |
| `guides/` | 可重复执行的开发、测试与运行操作。 |
| `guides/code-document-traceability.md` | 中文注释、文件头和双向映射维护规范。 |
| `../evaluation/` | 可复现实验、样本清单、评分工具与报告。 |
| `history/` | 历史方案和记录。 |

实际运行行为以代码与测试为准；设计文档不能把未实现的能力写成已支持。
