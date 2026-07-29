# Axiom-Flow 文档入口

状态：Current
最后更新：2026-07-29

本目录维护 Axiom-Flow 的当前架构、设计决策、实施计划和可重复操作。项目介绍与快速启动见
[`../README.md`](../README.md)，强制执行规则见 [`../AGENTS.md`](../AGENTS.md)。

## 事实优先级

发生冲突时依次采用：运行代码和测试结果、Accepted ADR、架构文档、设计文档、计划、追踪器、
历史资料。目标设计不得写成已交付能力，历史资料不得覆盖当前事实。

当前工程能力已经在本地通过 54 项测试，但 GitHub Actions 的 Pytest 步骤仍失败；因此工程基线
处于 In Progress，不能表述为已发布或远端验证完成。

## 阅读路径

1. [`architecture/README.md`](architecture/README.md)：系统边界和当前运行架构。
2. [`architecture/code-map.md`](architecture/code-map.md)：代码、设计和测试的唯一映射事实源。
3. [`design/README.md`](design/README.md)：各子系统的行为与接口设计。
4. [`adr/README.md`](adr/README.md)：已接受、被取代和实验决策。
5. [`standards/README.md`](standards/README.md)：任务、文档和代码追溯规则。
6. [`guides/README.md`](guides/README.md)：当前可执行的开发、测试和运维步骤。
7. [`trackers/current.md`](trackers/current.md)：当前实施项及其计划。

## 目录边界

| 目录 | 只负责 | 不负责 |
| --- | --- | --- |
| `architecture/` | 已接受的系统结构、边界、数据流和实现映射 | 未来功能设想 |
| `design/` | 接口、状态机、数据模型和验收约束 | 记录一次性决策过程 |
| `adr/` | 高成本决策、替代关系和后果 | 操作步骤和任务进度 |
| `standards/` | 开发流程、文档格式和追溯规则 | 项目运行命令 |
| `guides/` | 当前可重复执行的操作 | 历史版本说明 |
| `plans/` | In Progress、Blocked 或待执行的实施计划 | 已关闭计划 |
| `trackers/` | 当前工作、回归、待办和路线图 | 详细设计 |
| `templates/` | 新文档的最小合规模板 | 当前事实 |
| `history/` | 已关闭计划、旧版本指南和原始历史资料 | 当前入口 |

状态、元数据和归档规则见 [`standards/documentation.md`](standards/documentation.md)。模型实验的
manifest、评分和报告位于 [`../evaluation/README.md`](../evaluation/README.md)。
