# 解析评测治理

状态：Current（目标设计）  
最后更新：2026-07-27
关联代码：`evaluation/scorecard.py`、`evaluation/preflight.py`  
关联测试：`tests/test_evaluation_scorecard.py`、`tests/test_evaluation_preflight.py`、`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0002-parser-routing-and-provider-boundary.md`

## 目的与边界

评测验证候选解析路由、模型或提示词是否值得进入正式设计。它不修改生产数据、不调用
主链写入逻辑，也不替代已采纳实现的自动化测试。

## 连通性预检

正式实验前对一页本地 PDF 运行 `python -m evaluation.preflight`。预检先调用主视觉模型，
失败后最多调用一次回退模型，最多消耗 2 次调用。预检报告只包含模型、调用次数、耗时、
JSON 结构摘要和脱敏错误；完整结构化响应只存放在被忽略的本地 `data/`。

只有主模型成功并返回合法 JSON 才允许启动正式 12 页实验。仅回退成功或两次均失败均记录
为外部依赖/主链路问题，不能解释为解析质量通过，也不能修改生产路由。

## 固定实验协议

- 每轮使用 12 页 manifest，包含文字型数学、扫描数学、公式密集、计算机论文表格/图示
  四类页面各 3 页。
- 每页声明适用评分维度：`text`、`structure`、`formula`、`table_figure`、`source_evidence`。
- 人工按每个适用维度打 `0`、`1`、`2` 分：不可用、可用但需明显修订、可直接用于下一步。
- 默认最多 36 次外部模型调用；记录模型、参数、耗时、错误与费用估算。超出预算即停止。
- 候选方案只有在平均有效项得分不低于 `1.5`、公式页与所有页级证据均无 `0` 分、且无
  严重不可定位错误时才可进入 ADR 采纳。

## 结果与回归

实验报告保存 manifest 标识、代码版本、模型配置、逐页理由、评分、预算使用和结论。
可稳定复现的失败样本必须在修复后转入 `tests/regression/`；外部服务波动只记录为外部
失败，不作为质量通过或失败的依据。
