# ADR 0022：中性评测快照与单运行质量评估

状态：Accepted
日期：2026-07-30
领域：解析与评测
决策阶段：v0.3
取代：—
被取代：—

## 背景

ADR 0020 建立了文档中心工作区和冻结主链对比，但把 `baseline`、`candidate` 角色写入快照，导致
第一次真实运行在尚无干净 main 基线时不能形成正式评估。现有离线 scorecard 与工作区、API 和 Web
分离，也形成第二套人工质量事实。开发 CLI 还在自身进程调用 `Worker.run_once()`，与生产独立 Worker
边界不一致。

生产运行数据与冻结评估数据目前还共享 `AXIOM_DATA_DIR` 推导路径。真实案例需要避开存在失效文件
引用的 `xqfm11`，同时不能把隔离 ParseRun 目录与长期评估快照绑定为同一生命周期。

## 决定

ParseRun 快照改为中性不可变事实，不保存 baseline/candidate 角色。comparison 在创建时指定两份
快照的角色，并只允许修订信息表明来自干净 `main` 的快照作为 baseline。脏工作树快照仍必须保存
完整 diff hash，但可以先进入单运行 assessment。

assessment 成为与 comparison 并列的一等资源。assessment 针对一份快照保存自动完整性检查、逐页
人工质量结论和可重复生成的报告；comparison 只保存两份快照的逐页差异和相对人工结论。工程链路
清单和正式 scorecard 采用不同 profile，报告必须声明决策范围，工程链路结论不得解释为模型采纳。

评估 CLI 只导入来源、提交持久 Job、轮询和捕获结果，不在 CLI 进程执行 Worker；模型任务继续由
独立 `axiom_flow.worker` 进程领取。直接调用 Provider 的评估预检入口删除，真实单页连通性同样经过
生产 Job/Worker 与标准 ParseRun。

新增独立 `AXIOM_EVALUATION_DATA_DIR`。生产 ParseRun 使用 `AXIOM_DATA_DIR`，冻结 case、snapshot、
assessment 和 comparison 使用评估数据目录；评估状态仍以 JSON、JSONL、Markdown 和冻结文件为
唯一事实源，不写 MySQL。版本化定义和版权边界继续遵守 ADR 0019。

本决定修订 ADR 0020 中“角色属于快照”和“CLI 可用同进程 Worker 完成运行”的部分；ADR 0020 的
文档聚合、不可变快照、完整哈希、无绝对路径和 Web 不调用 Provider 等其余决定继续有效。

## 后果

首次真实运行可以作为中性快照完成绝对质量审阅，待实现进入干净 main 后再作为新运行建立正式
基线。一个快照可被多个 assessment 或 comparison 引用，角色不会污染运行事实。Web 增加单次质量
和版本对比两种审阅模式，但仍不承担模型执行、Git 读取或快照捕获。

现有 v0.3 评估 API 和本地工作区尚未发布且当前无运行快照，因此直接升级契约，不维护 role-based
兼容层或数据迁移。历史脱敏报告保持原结论。真实数据库创建、模型调用和本地数据清理仍分别受 D、
C 类计划约束。

## 关联

- 前置决定：[ADR 0019](0019-public-fixture-and-private-benchmark-boundary.md)、[ADR 0020](0020-document-centric-evaluation-workspace.md)
- 设计：[解析评测治理](../design/evaluation-governance.md)、[Web 工作台](../design/web-workbench.md)
- 实施记录：[EVAL-002](../history/plans/2026-07/2026-07-evaluation-assessment-workspace.md)
- 运行记录：[DATA-003](../history/plans/2026-07/2026-07-isolated-evaluation-runtime.md)、[EXP-002](../history/plans/2026-07/2026-07-rudin-engineering-chain-trial.md)
