# ADR 0019：公开回归样本与私有模型评测分离

状态：Accepted
日期：2026-07-29
领域：解析与评测
决策阶段：v0.3
取代：—
被取代：—

## 背景

现有评测只包含 Rudin 私有教材 manifest、外部模型响应和人工 scorecard。它可以形成候选质量结论，
但不能向 CI 提供可再分发、无外部调用且能够检查完整解析产物的稳定输入。完整响应、页图和运行
文件又与生产 `data/` 混放，导致公开证据、私有实验和临时数据的边界不清晰。

## 决定

解析评测采用两层独立门禁。仓库提交项目自制并明确许可的数学 PDF、人工金标页面事实和确定性
回放输入；CI 使用生产产物契约生成结果并自动比较 Markdown、结构、公式、表格、图片、来源位置
和 manifest。公开 fixture 只证明工程契约和回归稳定性，不证明外部模型质量。

真实教材只作为本地私有 benchmark。仓库允许提交不含正文、页图、绝对路径和凭证的冻结 manifest、
人工评分与脱敏结论；原 PDF、完整模型响应和运行产物写入被忽略的 `data/evaluation/runs/`。Rudin
内容不得进入公开 fixture。

生产解析路线只有同时通过公开确定性回归和冻结真实样本质量门禁，才可由后续 Accepted ADR 接受。
20 页连续试跑只验证工程链路，正式质量结论仍使用固定 12 页 scorecard。

`data/` 保持本地运行根，由应用按需创建；`build/` 只作为可删除的打包副产物；仓库不设置空的
`scripts/` 第二命令入口，评测命令统一使用 `python -m evaluation.<module>`。

## 后果

CI 可以在无模型密钥和无版权教材的环境中验证完整解析事实包。模型或提示词变化仍必须走 C 类
实验，不能用 replay 通过冒充质量采纳。清理本地运行数据属于 D 类操作，不修改 MySQL，也不能
绕过现有生产 ParseRun 清理规则。

## 关联

- 设计：[`evaluation-governance.md`](../design/evaluation-governance.md)
- 执行记录：[`QA-001`](../history/plans/2026-07/2026-07-evaluation-regression-baseline.md)、
  [`DATA-001`](../history/plans/2026-07/2026-07-local-data-reset.md)
- 既有实验决定：[ADR 0009](0009-reject-current-rudin-parser-route.md)、
  [ADR 0010](0010-qwen-ocr-only-rudin-trial.md)
