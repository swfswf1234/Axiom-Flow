# ADR 0017：统一任务台账与无状态能力路线图

状态：Accepted
日期：2026-07-29
领域：工程治理
决策阶段：v0.3
取代：—
被取代：—

## 背景

`trackers/` 同时维护 current、backlog、regressions 和 roadmap。current 重复 Plans 状态；backlog
与 regressions 都是未关闭工作但字段不同；roadmap 又混入任务进度和缺乏证据的 Completed/Pending
判断。开发者需要同时打开多个文件才能回答“还要做什么”和“已经完成什么”，状态容易漂移。

## 决定

1. tracker 只保留 `index.md`、`todo.md`、`completed.md` 和 `roadmap.md`。todo 统一导航全部未关闭
   工作，completed 保存简短关闭台账，roadmap 只描述长期能力依赖和退出条件，index 只导航。
2. 所有任务使用全局稳定的 `PREFIX-NNN` ID，进入 completed 后不得复用。todo 中 Plan 状态必须与
   计划正文一致；计划正文仍是范围、验证、回滚和状态的事实源。
3. 任务关闭时必须在同一变更从 todo 移除并写入 completed，记录日期、终态、关闭结果和证据。
   completed 只做导航，不取代 ADR、报告、History 或 Git。
4. roadmap 不保存 Completed、Pending、Active 等状态。未来能力先登记能力里程碑；形成可执行问题
   后进入 todo，需要长期决定或契约时再进入 ADR、Design 和 Plan。
5. 删除 current、backlog 和 regressions；重写 roadmap。旧文件没有独立审计价值，不进入
   History，需要时从 Git 锚点 `205b9c9` 恢复。

## 后果

开放任务、缺陷和实现偏差可以从一张表查询，关闭结果也有紧凑入口。路线图继续保存长期方向，但
不再成为第三份任务状态源。todo 会镜像活跃计划状态，因此必须由测试保证一致；新增和关闭任务也
必须维护稳定 ID 与证据。

## 关联

关联 [ADR 0013](0013-selective-history-retention.md)、[ADR 0014](0014-risk-based-plan-lifecycle.md)、
[ADR 0015](0015-standards-as-governance-source.md)、[任务生命周期](../standards/task-lifecycle.md)和
[文档规范](../standards/documentation.md)。
