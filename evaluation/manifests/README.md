# 评测样本清单

将 `parser-v1.template.json` 复制为本地 `parser-v1.json` 后填写 12 个真实页面。真实 PDF
路径应指向被 `.gitignore` 排除的数据目录；清单中的 `artifact_id` 是可提交的稳定标识。

清单通过 `required_categories` 冻结本轮实际拥有的类别和数量。Rudin 首轮只使用
`scanned_math_textbook` 的 12 页；每页只列出实际应评分的维度，公式页必须包含 `formula`，
每页都必须包含 `source_evidence`。后续新增类别时新建 manifest，不回写已有实验定义。

`rudin-qwen-ocr-20-v1.json` 冻结 ADR 0010 的连续 20 页工程试跑、60 次调用预算和五页人工抽检
门槛；它不替代 `rudin-scan-v1.json` 的整书路线质量评测。
