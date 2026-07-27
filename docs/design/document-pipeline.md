# 文档解析流水线

状态：Current（目标设计）  
最后更新：2026-07-27
关联代码：`backend/app/config.py`、`backend/app/providers.py`、`backend/app/pipeline.py`  
关联测试：`tests/test_v02_pipeline.py`、`tests/test_providers.py`、`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0002-parser-routing-and-provider-boundary.md`

## 输入与输出

输入是一个不可变的 PDF 文档版本。输出是独立的 `ParseRun`，包含规范 Markdown、
`NormalizedPage`、`ContentBlock`、`SourceSpan`、页级质量报告和解析产物索引。

## 路由

1. 用 PyMuPDF 读取页数、文字层和原生块，作为最低限度的可定位事实。
2. v0.2 以百炼视觉模型作为论文页的主解析器：`qwen-vl-ocr` 失败时降级到
   `qwen-vl-plus`；模型不可用时保留文字层和错误证据，等待人工决定。
3. 解析器结果全部归一化，再运行质量初筛；禁止由下游读取原始供应商响应。
4. 后续实验可依据文字密度、公式/表格/图示风险把低风险页改为文字层直通，但该路由
   优化必须通过评测后再修改本设计和 ADR。

当前 `ParseRun` 记录模型配置与调用量，页记录页图、原生块、规范 Markdown、证据和
质量风险；路由原因、耗时和重试次数列为下一轮可观测性增量，尚未作为发布依据。

## 统一接口

`ParserAdapter.parse(document_version, page_selection) -> ParseRun`  
`QualityAssessor.assess(parse_run) -> QualityReport`

解析错误分为可重试的服务/限流失败与不可重试的文件损坏/不支持格式；两类均保留错误
证据，但不得产生可发布知识。v0.2 将单次模型请求限制为可配置超时，进程中断时下次启动会
把遗留 `parsing` 运行标记为 `interrupted`、文档标记为 `failed`，避免悬挂任务。
