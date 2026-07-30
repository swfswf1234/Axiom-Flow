# 中性快照与单运行评估工作区

状态：Completed
关闭结果：Achieved
任务类型：B
最后更新：2026-07-30
关联 ADR：`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`、`docs/adr/0020-document-centric-evaluation-workspace.md`、`docs/adr/0022-neutral-evaluation-snapshots-and-assessments.md`
关联设计：`docs/design/evaluation-governance.md`、`docs/design/web-workbench.md`
关联 Tracker：`docs/trackers/todo.md`（EVAL-002）
归档判定：Retain；保留评估资源契约、Web 门禁和后续真实案例的实现依据。

## 目标与成功标准

把角色绑定快照的对比工具升级为同时支持单运行绝对质量和双运行回归的评估模块。成功标准是中性
快照、assessment、comparison、独立 Worker CLI、API 和两模式 Web 共用同一契约，公开 fixture
端到端通过且全量确定性门禁无回归。

## 范围与非目标

范围包括评估应用分析、文件工作区、配置、CLI、API、Web、公开 fixture、设计和测试。不调用真实
模型，不创建或修改正式数据库，不处理 DATA-002，不决定 OCR 模型是否可采纳。

## 前置条件

- ADR 0022 已接受，当前 role-based 工作区没有需要迁移的本地快照。
- TEST-002 已以 128 tests 关闭；重构前评估专项 34 tests 通过。
- 公开 fixture 可在无网络条件下重放并校验完整页面事实。

## 工作项

- [x] 实现中性快照、baseline 资格和独立评估数据根。
- [x] 实现 assessment profile、绝对人工结论和报告。
- [x] 保留 comparison 的结构差异与相对人工结论，迁移到中性快照输入。
- [x] 删除直接 Provider 预检和重复 scorecard 事实，改造 CLI 为独立 Worker 轮询。
- [x] 更新 API 与 Web 的单次质量/版本对比模式。
- [x] 同步设计、架构、代码映射、指南和全部分层测试。

## 验证与验收

定向覆盖快照中性语义、脏修订、baseline 资格、assessment profile、待审页、报告范围、CLI 超时
恢复、快照篡改、API 路径脱敏和公开 fixture 双模式闭环。Web 使用桌面和 390px 视口检查双栏、
三栏、页导航、无水平溢出和表单状态。关闭前运行五层全量 Pytest、Ruff、JavaScript、文档映射、
Markdown 链接和差异检查。

## 回滚

回滚新增 assessment 和中性快照接口，恢复 ADR 0020 的 role-based 未发布实现。`data/evaluation`
当前没有旧快照，无需数据迁移；若开发中生成确定性临时数据，只删除计划明确创建的隔离目录。

## 关闭与归档

全部确定性门禁和 Web 人工验收通过后以 Achieved 关闭，移入 `docs/history/plans/2026-07/`。失败时
保留 ADR 和回归证据，以 Partial 关闭并登记明确缺陷，不用直接 Provider 调用绕过架构问题。

关闭证据：评测核心、API、CLI、公开 fixture 与五层全量 `119 passed`；Ruff、JavaScript、Markdown、
代码映射和差异检查通过。公开四页 fixture 在 1440px 完成 assessment 双栏与 comparison 三栏验收，
在 390px 完成分段单栏、页导航、图片加载、表单状态与无水平溢出验收。
