# 隔离真实评估运行环境

状态：Completed
关闭结果：Achieved
任务类型：D
最后更新：2026-07-30
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0007-versioned-domain-records.md`、`docs/adr/0022-neutral-evaluation-snapshots-and-assessments.md`
关联设计：`docs/architecture/data-lifecycle.md`、`docs/design/evaluation-governance.md`
关联 Tracker：`docs/trackers/todo.md`（DATA-003）
归档判定：Retain；记录真实数据库和本地产物目标、验证、回滚与清理边界。

## 目标与成功标准

为真实 Rudin 工程链路建立不接触 `xqfm11` 的独立运行环境。成功标准是精确创建并迁移
`axiom_flow_eval_runtime`，ParseRun 写入 `data/runtime/evaluation-rudin`，冻结评估写入
`data/evaluation`，API/Worker/CLI 使用同一配置且可独立回滚。

## 范围与非目标

只创建和验证上述隔离 MySQL schema 与两个本地数据根。不修复、清理或迁移 `xqfm11`，不删除历史
数据，不调用模型，不改生产 schema 设计。

## 前置条件

- EVAL-002 已通过全量确定性门禁。
- 操作前确认目标 schema 不存在或仅包含本计划创建的 `af_` 表。
- 外部 Rudin PDF 哈希与 case 定义一致，API key 只从本地环境读取。

## 工作项

- [x] 记录目标 schema 和目录的操作前状态。
- [x] 显式创建 `axiom_flow_eval_runtime` 并执行 Alembic `upgrade head`。
- [x] 使用隔离环境启动 API 和独立 Worker，验证健康接口与空运行事实。
- [x] 记录精确配置、迁移版本、目录和回滚命令，不记录凭证。

## 验证与验收

确认数据库名精确匹配、Alembic head 正确、仅存在预期 `af_` 表，运行目录位于仓库 `data/` 下且
`xqfm11` 的文档/运行统计未变化。API health、Worker 启动和评估 case 列表成功。

操作前记录：`axiom_flow_eval_runtime`、`data/runtime/evaluation-rudin` 与 `data/evaluation` 均不存在；
`xqfm11` 保持旧结构，其中 `af_documents=1`、`af_parse_runs=5`、`af_pages=20`、`af_artifacts=103`。
操作后记录：目标 schema 位于 revision `20260728_0004`，包含 15 张 `af_` 表与 `alembic_version`；
API health、评测 case 列表和独立 Worker 空轮询通过，`xqfm11` 上述计数未变化。
运行完成记录：隔离库包含 1 个文档、2 个 ParseRun、21 页、88 个产物和 2 个 Job；运行数据根约
9.75 MB，评估数据根约 10.15 MB。`xqfm11` 的 1/5/20/103 计数仍未变化，API health 保持正常。

## 回滚

停止隔离 API/Worker；仅在确认 schema 由本计划新建后删除 `axiom_flow_eval_runtime`，并删除本计划
创建的 `data/runtime/evaluation-rudin` 与 `data/evaluation` 运行内容。任何删除前重新解析绝对目标、
列出内容并保存需要保留的脱敏报告。

## 关闭与归档

EXP-002 完成真实运行且回滚路径复核后以 Achieved 关闭并归档。环境无法隔离时以 Rejected 关闭，
不得退回 `xqfm11` 继续执行。
