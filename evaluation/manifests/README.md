# 评测样本清单

将 `parser-v1.template.json` 复制为本地 `parser-v1.json` 后填写 12 个真实页面。真实 PDF
路径应指向被 `.gitignore` 排除的数据目录；清单中的 `artifact_id` 是可提交的稳定标识。

清单必须包含 `math_text`、`math_scanned`、`math_formula`、`cs_table_figure` 四类页面各
3 页。每页只列出实际应评分的维度；公式页必须包含 `formula`，每页都必须包含
`source_evidence`。
