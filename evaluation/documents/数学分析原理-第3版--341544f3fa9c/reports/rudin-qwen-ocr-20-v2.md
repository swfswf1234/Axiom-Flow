# Rudin qwen-vl-ocr 20 页工程评估报告 v2

日期：2026-07-30
实验清单：[`rudin-qwen-ocr-20-v2.json`](../manifests/rudin-qwen-ocr-20-v2.json)
源文件：`sha256:341544f3fa9ce6ac8bf3860b4d9f9e4e86b1d2778e2a8644c1cafb31267ed968`
决策范围：`engineering_chain_only`

## 运行结果

- 单页 smoke：1 页、1 次调用，标准 ParseRun 与 snapshot 完整；人工质量为 `failed`。
- 20 页 Job：`62a07515-61f9-464c-819e-d18d19fa3530`
- ParseRun：`049b9938-4d80-47a3-9abf-9668312a7c44`
- 中性 snapshot：`rudin-qwen-ocr-20-v2-79d0d97edd8e-ef273595`
- assessment：`assessment-rudin-qwen-ocr-20-v2-19eb4cb69096`
- 页范围：PDF 物理页 20–39，共 20 页
- 模型与契约：`qwen-vl-ocr` / `qwen-ocr-markdown-v2`
- 调用量：20 / 60；任务首次尝试成功
- ParseRun manifest SHA-256：`44dbb4789cf4e4896746c05c2a796c09c22e4a7cfa8e47dbfe4ed3fa244305ac`
- assessment JSON SHA-256：`36a3fb21be4ea62b940a8d2f782fde6634ebbf3eafc09373d6bc4b7464d31253`

20 页的 page JSON、页图、Markdown、blocks、evidence 与 bbox 自动完整性检查全部通过，快照页集合为
20–39，模型调用未发生重试。自动检查只证明产物齐备，不证明 OCR 语义完整或公式正确。

## Web 人工抽检

| PDF 页 | 结果 | 脱敏结论 |
| --- | --- | --- |
| 20 | Failed | 定理 1.28 的命题被排到“证”之后；后部 aligned 公式换行转义无效。 |
| 24 | Failed | 来源公式为 `v=nw+p`，OCR 产生 `v=mw+p`，主要公式变量错误。 |
| 29 | Passed | 章标题、映射与可数性定义、阅读顺序和主要公式完整可读。 |
| 34 | Passed | 度量空间定义、公理、阅读顺序和主要公式完整可读。 |
| 39 | Failed | OCR 在开集证明后停止，遗漏下半页紧集 2.31–2.33 与公式 (22)。 |

五页均通过 Web 单次质量模式对照来源页图与规范化结果后保存结论，待审页为零。严格门槛要求 5/5，
本次为 2/5，因此 `execution_status=complete`、`quality_status=failed`。该结果只证明工程链路可运行，
不能作为模型采纳或 317 页整书解析依据。

## 后续边界

第 39 页的大段遗漏和第 24 页的变量错误均未被当前自动完整性规则发现，归入 `DES-001`。这些问题
属于外部模型质量波动；新的解析或校验路线必须另建冻结实验和采纳门槛，不修改本报告或原始快照。
教材正文、页图、完整供应商响应、绝对路径和凭证仅保存在本地隔离数据目录，未进入本报告。
