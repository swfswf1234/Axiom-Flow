# 文档解析流水线

设计状态：Accepted
实现状态：Verified
最后更新：2026-07-27
关联代码：`backend/infrastructure/config.py`、`backend/infrastructure/bailian.py`、`backend/infrastructure/artifacts.py`、`backend/infrastructure/pdf_pipeline.py`
关联测试：`tests/test_document_workflow.py`、`tests/test_v03_jobs.py`、`tests/test_providers.py`、`tests/test_parse_artifacts.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0008-immutable-parse-artifact-bundles.md`、`docs/adr/0010-qwen-ocr-only-rudin-trial.md`

## 输入与输出

输入是一个不可变的 PDF 文档版本。输出是独立的 `ParseRun`，包含规范 Markdown、
`NormalizedPage`、`ContentBlock`、`SourceSpan`、页级质量报告和解析产物索引。

新解析运行的私有文件产物位于内容哈希下的 `parse-runs/<run-id>/`，200 DPI 原始页图位于
`page-assets/render-200dpi-v1/` 并由同一文档的运行共享。产物以逐页检查点写入；只有最终
清单中的文件、哈希和数据库页面事实全部一致时，运行才可进入 `parsed`。扫描页必须产生非空
正文或显式 `blank` 质量结论，模型错误不能伪装成成功的文字层降级。

新运行使用 manifest v2，分别列出运行私有文件与数据根下的共享资产；校验器兼容既有 v1 清单。
成功运行不会自动成为文档当前结果，必须在 Web 或 API 中显式选择且再次校验 manifest。

## 路由

1. 用 PyMuPDF 读取页数、文字层和原生块，作为最低限度的可定位事实。
2. 扫描页只调用百炼 `qwen-vl-ocr`。每页最多重试三次；截断、非法顶层结构、空正文、限流、
   超时和 5xx 不得写成成功页面，也不得静默切换模型。
3. 解析器结果全部归一化，再运行质量初筛；禁止由下游读取原始供应商响应。
4. 后续实验可依据文字密度、公式/表格/图示风险把低风险页改为文字层直通，但该路由
   优化必须通过评测后再修改本设计和 ADR。

当前 `ParseRun` 绑定一个持久任务，记录本次任务独立的模型配置与调用量；同一任务重试复用
运行并跳过已校验页面。任务可绑定包含端点的 PDF 物理页范围，局部运行的 payload、供应商摘要
和 manifest 必须保存相同范围。页记录页图、原生块、
规范 Markdown、证据和质量风险。页级进度写回任务，取消请求在页面边界生效。路由原因和逐页
耗时列为下一轮可观测性增量，尚未作为发布依据。

## 统一接口

`ParserAdapter.parse_page(page_image, native_evidence, page_no) -> NormalizedPage`
`QualityAssessor.assess(parse_run) -> QualityReport`

解析错误分为可重试的服务/限流失败与不可重试的文件损坏/不支持格式；两类均保留错误
证据，但不得产生可发布知识。模型响应必须记录 `finish_reason` 和 usage，只有完整顶层页面 JSON
可以归一化。`finish_reason=stop` 的非标准 JSON 可由固定版本结构化修复器处理，修复标记必须进入
产物；失败响应也写入本地诊断产物，但不得登记为成功页。单次模型请求受超时、token 上限和任务
预算约束，进程中断时下次启动会
把遗留 `parsing` 运行标记为 `interrupted`、文档标记为 `failed`，避免悬挂任务。

`qwen-vl-ocr` 的原生成功契约允许顶层对象只包含非空 `markdown`。适配器以
`qwen-ocr-markdown-v2` 规则先保护 LaTeX 反斜杠，再按空行拆分块；短编号行归一化为 heading，
独立数学段归一化为 formula，其余归一化为 paragraph。原始响应保存在供应商产物，派生块不
伪造 bbox。
