# 评测报告

提交可阅读的结果摘要和 `scorecard.py` 输出。不得提交完整模型响应、原始 PDF、页图、
API 密钥或包含本地绝对路径的运行日志。

结果文件必须包含 experiment ID、运行产物标识、无凭证的模型配置摘要、外部调用次数、
耗时、费用估算、逐页评分、逐页理由和严重错误列表。

`parser-v1-preflight.json` 是正式评测前的单页连通性摘要。它只允许包含状态、调用次数、
模型、耗时、结构统计和脱敏错误，不能包含模型正文、页图、原始 PDF 或绝对路径。

`rudin-scan-v1.md`、`rudin-scan-v1-bailian-results.json` 和
`rudin-scan-v1-scorecard.json` 记录首轮扫描教材评测；完整页面响应仍只在本地 `data/`。

`rudin-qwen-ocr-20-v1.md` 记录 ADR 0010 的连续 20 页工程链路和五页严格人工抽检；链路成功与
OCR 质量门禁分别给出结论。
