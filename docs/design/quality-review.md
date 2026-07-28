# 页级质量与人工审阅

设计状态：Accepted
实现状态：Verified
最后更新：2026-07-27
关联代码：由 `docs/architecture/code-map.md` 中的解析与仓储服务组合实现  
关联测试：`tests/test_document_workflow.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0002-parser-routing-and-provider-boundary.md`

自动初筛检测空页、低文字覆盖、乱码、页数不一致、公式或表格缺失、解析器冲突和模型
低置信度。规则只产生风险信号，不修改正文和知识语义。

质量状态为 `pending`、`accepted`、`needs_review`、`rejected`、`reparse_requested`。
v0.2 人工审阅记录结论和原因，页面通过其 `ParseRun` 关联；审阅时间是下一轮审计字段。
只有 `accepted` 页面可被自动送入知识候选抽取；被拒绝或待重解析页面不能静默降级为可用内容。

当前工作台并排显示 PDF 页图、规范 Markdown 和风险原因，支持接受、拒绝和提出重解析请求；
解析历史可通过 API v1 查询。内容块坐标覆盖层和 Web 历史版本切换属于后续界面增强，不能把
它们写成当前已交付能力。
