# v0.3 工程基线发布

状态：Blocked
任务类型：D
最后更新：2026-07-30
关联 ADR：`docs/adr/0013-selective-history-retention.md`、`docs/adr/0014-risk-based-plan-lifecycle.md`、`docs/adr/0018-src-package-and-application-owned-workflows.md`、`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`
关联设计：`docs/architecture/runtime-architecture.md`
关联 Tracker：`docs/trackers/todo.md`（REL-001）
归档判定：Retain；发布后保留远端 CI、标签和发布锚点作为里程碑证据。

## 目标与成功标准

在已完成的 v0.3 实现上执行发布门禁，将通过同一远端工作流验证的 `main` 提交标记为
`v0.3.0`。成功标准是 REG-001 已关闭、目标提交的 GitHub Actions 全部通过，且远端标签准确
指向该提交。

## 范围与非目标

本计划只负责核对发布目标、执行远端 CI、复核完整差异和创建标签。不修改生产代码、测试、
数据库、解析产物或真实数据；REG-001 的诊断和确定性修复必须使用独立 B 类计划。

## 前置条件

- v0.3 架构、工作台与存储变更已经合入 `main`，本地关闭门禁已经通过。
- REG-001 已由独立修复任务关闭；提交 `907e196` 的 Actions run `30438394283` 已通过。
- QA-001 与 DATA-001 必须先关闭，发布提交不得混用旧评测目录或旧本地运行数据。
- EVAL-001 必须先关闭，发布提交必须以文档中心评测和冻结主链对比作为解析回归基线。
- DATA-002 必须先关闭，运行库不得保留指向已清理本地文件的记录。
- 预定发布提交及其回滚锚点尚待在本计划内冻结。
- 发布人可核对 `origin/main`、GitHub Actions 和远端标签目标。

## 工作项

- [x] 完成本地 Ruff、全量 Pytest、文档映射、Markdown 链接和差异检查。
- [x] 关闭 REG-001；跨平台回归、本地 98 tests 和远端同一工作流已通过。
- [ ] 冻结唯一预定发布提交及其回滚锚点。
- [ ] 确认该提交已推送到 `origin/main`，同一提交的全部远端门禁通过。
- [ ] 复核提交差异、回滚锚点和现有标签后创建并推送 `v0.3.0`。
- [ ] 登记发布提交、标签和 Actions 证据并关闭计划。

## 阻塞与恢复

阻塞证据：`DATA-002` 仍为 P0 Open，运行库保留失效本地文件引用；同时 v0.3 文档中心评测与
分层测试变更尚在未提交工作树中，`HEAD` 虽与 `origin/main` 一致，但还不存在可冻结和远端验证的
唯一发布提交。

恢复条件：独立 D 类计划完成 `DATA-002` 的备份、清理、回滚验证和关闭；当前实现形成唯一提交、
合入 `main` 并通过适用本地门禁后，才恢复本计划并冻结发布提交。

责任位置：`DATA-002` 的责任位置是后续独立数据操作计划；提交冻结、远端 CI 和标签复核仍由
`REL-001` 负责。

复核触发点：`DATA-002` 进入 Completed，且 `git status`、`main`、`origin/main` 和预定发布提交具备
可逐项核对的确定状态时复核。

## 验证与验收

发布前重新执行[开发指南](../guides/development.md#测试与检查)中的完整关闭门禁，确认
`git status`、`origin/main` 和目标提交一致，并人工核对 GitHub Actions 的提交 SHA 与结论。
标签推送后复查远端 `v0.3.0` 只指向已验证提交。

## 回滚

CI 未通过时不得创建标签。标签创建前的远端推送通过新增修复提交回滚，不改写 `main` 历史；
标签目标错误时停止发布，删除错误标签属于新的 D 类操作，必须先核对本地和远端精确目标。

## 关闭与归档

完成发布后写入 `关闭结果：Achieved`，记录提交、标签和 Actions 证据，并按 ADR 0013 移入
`docs/history/plans/<year-month>/`。发布取消或由新版本取代时按实际终态与关闭结果处理。
