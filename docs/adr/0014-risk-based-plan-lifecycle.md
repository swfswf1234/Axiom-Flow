# ADR 0014：风险分级计划准入与单一生命周期

状态：Accepted
日期：2026-07-29
领域：工程治理
决策阶段：v0.3
取代：—
被取代：—

## 背景

当前计划使用“设计状态/实现状态”双字段，但计划不是设计；`plans/index.md`、计划正文和
`current.md` 已出现状态不一致。Rudin 计划还把 C 类实验、B 类实现、D 类真实执行和结果记录
放在同一文件中，导致工作项完成后仍长期 Blocked。所有轻微修改都创建计划又会制造目录噪声。

## 决定

1. B/C/D 类任务必须建立独立计划；跨模块、跨会话或需要回滚的非平凡 A 类也必须建立。简单 A
   类变更由差异、验证和提交记录承接，不进入 Plans 或 current。
2. 每份计划只使用一个任务类型。C 实验、B 确定性实现和 D 发布/真实数据操作必须分别关闭，
   由实验报告、ADR 和明确前置条件连接，不能使用一个端到端计划跨越三类门禁。
3. 计划使用单一 `状态`：`Accepted`、`In Progress`、`Blocked`、`Completed`、`Cancelled`、
   `Superseded`。活跃目录只允许前三种；后三种按 ADR 0013 选择性保留或删除。
4. `Completed` 只表示执行结束；终态另写 `关闭结果`：`Achieved`、`Rejected`、`Partial` 或
   `Not Applicable`。Blocked 只适用于原范围可恢复的计划，并必须声明证据、恢复条件、责任位置
   和复核触发点；需要新候选、重新设计或新 ADR 时关闭旧计划并返回 backlog。
5. 计划正文是状态事实源；`plans/index.md` 汇总全部活跃计划，`current.md` 只镜像所有
   `In Progress` 计划，backlog 只保存尚未具备计划条件的工作，regressions 保存缺陷证据。

## 后果

计划成为短期执行合同而不是设计、队列或工作日志。跨阶段工作需要多个计划，但每个计划的门禁、
回滚和关闭语义更清楚。状态与索引必须由自动化测试同步；实验结果、通用命令和重复事实继续留在
各自报告、指南、设计或 tracker 中。

## 关联

关联 [ADR 0013](0013-selective-history-retention.md)、[任务生命周期](../standards/task-lifecycle.md)、
[文档规范](../standards/documentation.md)。原计划模板已按 [ADR 0016](0016-remove-document-templates.md)
删除，计划正文直接遵守任务生命周期。
