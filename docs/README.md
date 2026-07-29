# Axiom-Flow 文档中心

状态：Current
最后更新：2026-07-29

本页只负责帮助开发者定位文档。项目定位、能力边界和快速启动见[根 README](../README.md)；
强制执行协议见 [AGENTS.md](../AGENTS.md)。

## 按任务阅读

| 读者或任务 | 阅读顺序 |
| --- | --- |
| 本地操作者 | [根 README](../README.md) → [本地开发](guides/local-development.md) → [运行与清理](guides/operations.md) |
| 新开发者 | [AGENTS.md](../AGENTS.md) → [架构总览](architecture/README.md) → [代码映射](architecture/code-map.md) → [当前工作](trackers/current.md) |
| 架构评审 | [当前架构](architecture/runtime-architecture.md) → [ADR](adr/README.md) → [设计](design/README.md) |
| 模型实验 | [评测治理](design/evaluation-governance.md) → [evaluation](../evaluation/README.md) → 采纳或拒绝 ADR |
| 文档维护 | [文档规范](standards/documentation.md) → [模板](templates/README.md) → [追溯规范](standards/code-document-traceability.md) |

## 文档地图

| 目录 | 职责 | 入口 |
| --- | --- | --- |
| `architecture/` | 已接受的系统结构、领域边界、数据生命周期和实现映射 | [架构文档](architecture/README.md) |
| `design/` | 接口、状态机、数据模型和验收约束 | [设计文档](design/README.md) |
| `adr/` | 高成本决策、替代关系和后果 | [架构决策](adr/README.md) |
| `standards/` | 任务生命周期、文档格式和代码追溯规则 | [工程规范](standards/README.md) |
| `guides/` | 当前可重复执行的开发、测试和运维步骤 | [操作指南](guides/README.md) |
| `plans/` | 尚未关闭的实施计划 | [活跃计划](plans/README.md) |
| `trackers/` | 当前工作、回归、待办和路线图 | [工作追踪](trackers/README.md) |
| `templates/` | ADR、设计、计划和实验的最小合规模板 | [文档模板](templates/README.md) |
| `history/` | 已关闭计划、旧版本指南和原始历史资料 | [历史资料](history/README.md) |

## 去哪里确认事实

| 问题 | 唯一维护位置 |
| --- | --- |
| 当前代码实现了什么 | 运行代码、测试和 [code-map](architecture/code-map.md) |
| 为什么采用当前边界 | [Accepted ADR](adr/README.md) |
| 接口与行为应当是什么 | [architecture](architecture/README.md) 与 [design](design/README.md) |
| 现在正在做什么 | [current tracker](trackers/current.md) 与其关联计划 |
| 哪些失败尚未关闭 | [regressions](trackers/regressions.md) |
| 历史版本当时如何运行 | [history](history/README.md) |

状态、归档和写作规则不在本页重复维护，统一见[文档规范](standards/documentation.md)；开发任务的
分类和关闭门禁见[任务生命周期](standards/task-lifecycle.md)。
