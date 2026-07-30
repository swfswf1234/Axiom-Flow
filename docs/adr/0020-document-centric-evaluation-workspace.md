# ADR 0020：文档中心评测工作区与冻结主链对比

状态：Accepted
日期：2026-07-30
领域：解析与评测
决策阶段：v0.3
取代：—
被取代：—

## 背景

ADR 0019 已经分离公开工程回归与私有真实教材评测，但现有目录仍按 replay、benchmark 和
scorecard 工具组织。不同运行的源文档、页范围和产物无法在一个稳定位置逐页对照，且旧
`evaluation/benchmark.py` 直接调用供应商，绕过 API/Application、Job、Worker、`PDFPipeline` 和
标准 ParseRun 产物写入链路，因此不能回答生产主链修改是否引入回归。

评测运行还必须在原 ParseRun 被清理后继续可复核。把评测状态写入 MySQL 会形成第二套运行事实，
而让 Web 执行 Git 切换或直接调用模型会破坏现有进程边界和当前解析结果的显式选择规则。

## 决定

评测以文档为聚合边界。版本化定义保存于
`evaluation/documents/<规范化短标题>--<内容哈希前十二位>/`，本地运行状态保存于
`data/evaluation/documents/<同名目录>/`；生产 `data/documents/<完整内容哈希>/` 的内容寻址结构
保持不变。目录标题使用 Unicode NFC，移除 Windows 非法字符和路径语义，正文最长 48 个字符，
并以完整 PDF SHA-256 校验目录后缀。

候选与基线均通过生产导入、持久 Job、独立 Worker、`PDFPipeline` 和标准 ParseRun 产物链路执行。
评测服务只捕获已经完成且可用的 ParseRun，不直接实例化供应商，也不改变
`current_parse_run_id`。捕获时把运行元数据和全部必需文件冻结为不可变快照；优先使用硬链接，
不支持时复制，且保存哈希以便原 ParseRun 清理后独立校验。

基线必须来自干净且可识别的 `main` 修订；候选可以来自脏工作树，但必须记录 commit、dirty 标记
和 diff hash，脏候选不得提升为基线。无人工金标时自动结论只允许 `changed`、
`no_regression_detected` 或 `review_required`，不得宣称 `improved`。人工逐页结论只允许
`candidate_better`、`baseline_better`、`equivalent`、`both_failed`、`needs_review`。

评测状态以可读 JSON、JSONL、Markdown 和冻结文件为唯一事实源，不写 MySQL。既有 Web 工作台
增加 `evaluation` 主视图，只通过 API 读取快照、提交比较和人工结论；Web 不执行 Git 命令、不调用
Provider，也不自动切换当前 ParseRun。ADR 0019 的公开 fixture/私有教材授权边界继续有效，本决定
只补充组织方式和主链对比约束，不取代它。

## 后果

同一文档的来源、基线、候选、逐页差异和人工结论可从一个目录审计，且历史对比不依赖生产运行
继续存在。文件快照会占用额外磁盘空间；硬链接只优化本机占用，完整哈希仍是完整性依据。冻结
主线基线需要在干净提交上执行，不能由普通候选运行自动生成。

真实教材 OCR、提示词或模型质量采纳仍属于 C 类实验；评测工作区和接口实现属于 B 类确定性工作。
本决定不处理 DATA-002 的失效数据库引用，也不扩大 v0.3 发布权限。

## 关联

- 补充决定：[ADR 0019](0019-public-fixture-and-private-benchmark-boundary.md)
- 设计：[解析评测治理](../design/evaluation-governance.md)、[Web 工作台](../design/web-workbench.md)
- 执行记录：[EVAL-001](../history/plans/2026-07/2026-07-document-centric-evaluation.md)
