# Axiom-Flow 评测模块

本目录保存不修改生产数据的可复现实验资产。解析实验使用固定 12 页 manifest，调用结果
由人工复核后交给 `scorecard.py` 判定是否达到采纳门槛。

```powershell
python -m evaluation.scorecard \
  --manifest evaluation/manifests/rudin-scan-v1.json \
  --results evaluation/reports/rudin-scan-v1-bailian-results.json \
  --output evaluation/reports/rudin-scan-v1-scorecard.json
```

真实样本路径、完整模型响应和密钥不得提交。提交的报告只保存评分、原因、模型配置摘要和
可追溯产物标识。协议见 `docs/design/evaluation-governance.md`。

正式评测前先运行单页连通性预检。它不写 MySQL 或生产数据，只调用 `qwen-vl-ocr`：

```powershell
python -m evaluation.preflight --pdf <本地-PDF> --page-no 1
```

预检通过仅表示主模型链路可用，不表示正式评测或解析质量已通过。

扫描教材候选使用 `python -m evaluation.scanned_textbook` 运行。完整响应只能写入被忽略的
`data/evaluation/`，生成的评分模板必须人工填写，禁止用默认分数形成结论。
