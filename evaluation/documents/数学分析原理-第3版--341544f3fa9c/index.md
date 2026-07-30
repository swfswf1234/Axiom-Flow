# Rudin 私有 benchmark

本目录只提交不含教材正文、页图、绝对路径和凭证的冻结 manifest 与脱敏结论。原 PDF 和完整模型
响应只存在于本地 `data/evaluation/documents/数学分析原理-第3版--341544f3fa9c/`，不得上传 GitHub。

| 类型 | 文件 | 用途 |
| --- | --- | --- |
| 正式质量样本 | [`manifests/rudin-scan-v1.json`](manifests/rudin-scan-v1.json) | 固定 12 页人工 scorecard。 |
| 连续工程试跑 | [`manifests/rudin-qwen-ocr-20-v1.json`](manifests/rudin-qwen-ocr-20-v1.json) | ADR 0010 的 20 页链路与 5 页抽检。 |
| 工程连通 smoke | [`manifests/rudin-qwen-ocr-smoke-v2.json`](manifests/rudin-qwen-ocr-smoke-v2.json) | 生产 Job/Worker 单页连通性与中性快照。 |
| 中性快照工程评估 | [`manifests/rudin-qwen-ocr-20-v2.json`](manifests/rudin-qwen-ocr-20-v2.json) | assessment/Web 双模式架构下的 20 页工程链路。 |
| 首轮拒绝证据 | [`reports/rudin-scan-v1.md`](reports/rudin-scan-v1.md) | ADR 0009 的报告、评分输入与 scorecard。 |
| 工程试跑证据 | [`reports/rudin-qwen-ocr-20-v1.md`](reports/rudin-qwen-ocr-20-v1.md) | EXP-001 的 3/5 抽检结论。 |
| v2 工程评估证据 | [`reports/rudin-qwen-ocr-20-v2.md`](reports/rudin-qwen-ocr-20-v2.md) | EXP-002 的完整链路与 2/5 Web 抽检结论。 |
