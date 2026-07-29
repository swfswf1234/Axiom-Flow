# 解析评测治理

设计状态：Accepted
实现状态：Implemented
最后更新：2026-07-27
关联代码：`evaluation/scorecard.py`、`evaluation/preflight.py`、`evaluation/scanned_textbook.py`
关联测试：`tests/test_evaluation_scorecard.py`、`tests/test_evaluation_preflight.py`、`tests/test_scanned_textbook_evaluation.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0009-reject-current-rudin-parser-route.md`、`docs/adr/0010-qwen-ocr-only-rudin-trial.md`

## 目的与边界

评测验证候选解析路由、模型或提示词是否值得进入正式设计。它不修改生产数据、不调用
主链写入逻辑，也不替代已采纳实现的自动化测试。

## 连通性预检

正式实验前对一页本地 PDF 运行 `python -m evaluation.preflight`。预检只调用 `qwen-vl-ocr`，
按生产页级策略最多重试三次。预检报告只包含模型、调用次数、耗时、
JSON 结构摘要和脱敏错误；完整结构化响应只存放在被忽略的本地 `data/`。

只有模型以完整结束状态返回合法顶层页面 JSON 才允许启动实验。失败记录为外部依赖或主链路
问题，不能解释为解析质量通过。

## 固定实验协议

- 每轮 manifest 自身声明一个或多个受控类别及其样本数。当前 Rudin 首轮只使用
  `scanned_math_textbook` 类别的 12 页，后续收到新类型样本时再增加类别，不能把未验证类别
  写成当前能力。
- 每页声明适用评分维度：`text`、`structure`、`formula`、`table_figure`、`source_evidence`。
- 人工按每个适用维度打 `0`、`1`、`2` 分：不可用、可用但需明显修订、可直接用于下一步。
- 默认最多 36 次外部模型调用；Rudin 的百炼候选最多使用 24 次。记录模型、参数、耗时、错误与
  费用估算，超出预算即停止。
- 候选方案只有在平均有效项得分不低于 `1.5`、公式页与所有页级证据均无 `0` 分、且无
  严重不可定位错误时才可进入 ADR 采纳。
- ADR 0010 的连续 20 页运行是工程链路试跑，独立冻结 60 次预算和五页严格人工抽检；其结果
  不覆盖上述 12 页整书路线采纳门槛。

## 结果与回归

实验报告保存 manifest 标识、代码版本、模型配置、逐页理由、评分、预算使用和结论。
可稳定复现的失败样本必须在修复后转入 `tests/regression/`；外部服务波动只记录为外部
失败，不作为质量通过或失败的依据。
