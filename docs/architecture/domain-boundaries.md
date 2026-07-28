# 领域边界

设计状态：Accepted
实现状态：Verified
最后更新：2026-07-27
关联代码：各领域的 v0.3 实现边界见 `docs/architecture/code-map.md`
关联测试：`tests/test_architecture_dependencies.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0002-parser-routing-and-provider-boundary.md`、`docs/adr/0012-backend-package-boundaries.md`

| 领域 | 责任 | 不负责 |
| --- | --- | --- |
| Document | 导入、版本、产物索引和处理任务。 | 知识语义判定。 |
| Parsing | PDF 分流、解析适配、内容归一化和页级证据。 | 发布知识。 |
| Quality Review | 自动风险检测、人工复核和可用性结论。 | 修改原始 PDF。 |
| Knowledge | 候选单元、关系、证据、审阅状态和已发布快照。 | 直接调用供应商 SDK。 |
| Workbook | Excel 导出、导入校验、差异与发布版本。 | 绕过知识校验直接写运行库。 |
| Web | 工作台浏览、审阅与命令提交。 | 承担领域规则。 |
| Jobs | 幂等入队、租约、进度、取消、重试和恢复。 | 解释 PDF 或知识语义。 |

领域、应用、基础设施、API 和 Worker 的依赖方向见 `v03-target.md`。`ParserAdapter` 和
`ModelProvider` 是基础设施边界。当前只实现百炼 `qwen-vl-ocr`；任何未来引擎都
在此边界后实现。领域服务只消费规范化类型，不能读取供应商私有 JSON。
