# CI 跨平台路径脱敏修复

状态：In Progress
任务类型：B
最后更新：2026-07-29
关联 ADR：—
关联设计：`docs/design/evaluation-governance.md`
关联 Tracker：`docs/trackers/todo.md`（REG-001）
归档判定：Delete；缺陷、回归测试、Actions 和 Git 提交承接关闭证据。

## 目标与成功标准

修复预检错误摘要只屏蔽 Windows 路径、未屏蔽 POSIX 绝对路径的问题；本地全量测试和同一 GitHub
Actions 工作流通过，报告仍保留不含敏感路径的可诊断文本。

## 范围与非目标

只修改评测预检的错误文本脱敏和跨平台回归测试。不修改模型调用、实验门槛、生产解析、数据库或
历史评测报告。

## 前置条件

- Actions run `30437375294` 已确认唯一失败为 POSIX 临时路径泄露断言。
- 本地 Windows 全量 97 tests 已通过，可用于比较跨平台差异。

## 工作项

- [ ] 统一屏蔽 Windows 盘符、UNC 和非 URL 的 POSIX 绝对路径。
- [ ] 增加与运行平台无关的固定路径回归测试。
- [ ] 通过本地门禁并推送，确认新 Actions run 通过。

## 验证与验收

运行评测预检定向测试、Ruff、全量 Pytest、差异检查，并核对新提交 Actions 的 SHA、job 和结论。

## 回滚

整体回滚修复提交；不改动评测产物或真实数据。若规则过度脱敏，恢复原规则并保留失败测试后重新
设计，不放宽“错误摘要不得包含本地绝对路径”的安全约束。

## 关闭与归档

远端同一工作流通过后将 REG-001 从 todo 原子迁移到 completed 并删除本计划。

