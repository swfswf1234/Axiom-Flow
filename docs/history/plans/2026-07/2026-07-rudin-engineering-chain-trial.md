# Rudin 20 页评估与 Web 工程链路验收

状态：Completed
关闭结果：Rejected
任务类型：C
最后更新：2026-07-30
关联 ADR：`docs/adr/0010-qwen-ocr-only-rudin-trial.md`、`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`、`docs/adr/0022-neutral-evaluation-snapshots-and-assessments.md`
关联设计：`docs/design/document-pipeline.md`、`docs/design/evaluation-governance.md`、`docs/design/web-workbench.md`
关联 Tracker：`docs/trackers/todo.md`（EXP-002）
归档判定：Retain；保留真实外部调用、人工质量结论和工程链路验收证据。

## 目标与成功标准

在隔离环境中使用生产主链重新解析 Rudin PDF 物理页 20–39，冻结中性快照并在 Web 完成五页人工
审阅。成功标准是 20 页运行和评估产物完整、Web 可复核、五个指定页面均有结论；工程链路和质量
分别报告，不把本次结果解释为正式模型采纳。

## 范围与非目标

先运行第 20 页连通性清单，再运行 20–39 页工程清单。模型固定为 `qwen-vl-ocr`，契约固定为
`qwen-ocr-markdown-v2`。不运行正式 12 页类别均衡 scorecard，不扩展到 317 页，不抽取知识，不发布。

## 前置条件

- EVAL-002 已关闭且 DATA-003 隔离环境验证完成。
- 源 PDF SHA-256 固定为 `341544f3fa9ce6ac8bf3860b4d9f9e4e86b1d2778e2a8644c1cafb31267ed968`。
- 单页最多 3 次调用；20 页最多 60 次调用；模型参数和人工页在调用前冻结。
- 人工审阅页固定为 20、24、29、34、39，检查段落遗漏、标题/顺序、主要公式和来源证据。

## 工作项

- [x] 冻结单页 smoke 和 20 页 engineering-chain manifest。
- [x] 经 CLI 提交、独立 Worker 执行第 20 页连通性运行并校验标准 ParseRun。
- [x] 连通性通过后执行 20–39 页运行并捕获中性快照。
- [x] 创建 `engineering_chain` assessment，在 Web 完成五页人工结论。
- [x] 生成 JSON/Markdown 报告并提交脱敏结果，原始内容只留本地。

## 验证与验收

自动验证页数、页范围、调用上限、manifest、页面 JSON/Markdown、页图、原始响应、bbox、文件哈希
和快照独立性。人工验证五页可读性与 Web 操作。报告分别保存 `execution_status`、`quality_status` 和
`decision_scope=engineering_chain_only`；未完成审阅时只能是 `review_required`。

## 回滚

停止独立 Worker，保留已完成的不可变 ParseRun 和快照供审计。失败任务不伪造成功结果；本地运行
数据仅由 DATA-003 的精确回滚处理。模型波动保存脱敏错误，不重用调用额度掩盖失败。

## 关闭与归档

工程链路完整且五页全部审阅后关闭。链路完成但质量未通过时关闭结果为 Rejected；链路和 Web 均
满足目标时为 Achieved。任何模型路线采纳另建正式 12 页 C 类实验和后续 ADR。

关闭证据：单页 smoke 与 20 页 Job 均首次尝试完成；20 页运行使用 20/60 次调用，ParseRun manifest
哈希为 `44dbb4789cf4e4896746c05c2a796c09c22e4a7cfa8e47dbfe4ed3fa244305ac`。assessment 自动完整性
为 complete，Web 五页审阅为 2/5，质量为 failed，待审为零；脱敏结论见
[`rudin-qwen-ocr-20-v2.md`](../../../../evaluation/documents/数学分析原理-第3版--341544f3fa9c/reports/rudin-qwen-ocr-20-v2.md)。
