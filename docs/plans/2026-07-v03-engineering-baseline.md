# v0.3 工程基线发布

状态：Blocked
任务类型：D
最后更新：2026-07-29
关联 ADR：`docs/adr/0012-backend-package-boundaries.md`、`docs/adr/0013-selective-history-retention.md`、`docs/adr/0014-risk-based-plan-lifecycle.md`
关联设计：`docs/architecture/runtime-architecture.md`
关联 Tracker：`docs/trackers/regressions.md`（REG-001）
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
- REG-001 已由独立修复任务关闭，预定发布提交及其回滚锚点已经冻结。
- 发布人可核对 `origin/main`、GitHub Actions 和远端标签目标。

## 工作项

- [x] 完成本地 Ruff、全量 Pytest、文档映射、Markdown 链接和差异检查。
- [ ] 关闭 REG-001，并冻结唯一预定发布提交。
- [ ] 确认该提交已推送到 `origin/main`，同一提交的全部远端门禁通过。
- [ ] 复核提交差异、回滚锚点和现有标签后创建并推送 `v0.3.0`。
- [ ] 登记发布提交、标签和 Actions 证据并关闭计划。

## 验证与验收

发布前重新执行[开发指南](../guides/development.md#测试与检查)中的完整关闭门禁，确认
`git status`、`origin/main` 和目标提交一致，并人工核对 GitHub Actions 的提交 SHA 与结论。
标签推送后复查远端 `v0.3.0` 只指向已验证提交。

## 回滚

CI 未通过时不得创建标签。标签创建前的远端推送通过新增修复提交回滚，不改写 `main` 历史；
标签目标错误时停止发布，删除错误标签属于新的 D 类操作，必须先核对本地和远端精确目标。

## 阻塞与恢复

- 阻塞证据：[`REG-001`](../trackers/regressions.md) 记录 `main@a6ec4e0` 的 GitHub Actions
  Pytest 步骤失败，本地同批门禁通过，远端失败原因尚未形成可复现结论。
- 恢复条件：独立 B 类任务定位并关闭 REG-001，且预定发布提交的同一 Actions 工作流全部通过。
- 责任位置：`tests/`、`.github/workflows/` 与 REG-001 后续修复计划负责诊断和修复；本计划只负责
  发布复核与标签操作。
- 复核触发点：REG-001 状态更新为已关闭，或新的预定发布提交产生完整远端 Actions 结果时。

## 关闭与归档

完成发布后写入 `关闭结果：Achieved`，记录提交、标签和 Actions 证据，并按 ADR 0013 移入
`docs/history/plans/<year-month>/`。发布取消或由新版本取代时按实际终态与关闭结果处理。
