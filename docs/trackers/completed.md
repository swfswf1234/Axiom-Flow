# 已关闭任务

状态：Current
最后更新：2026-07-30

本表只保存关闭导航。详细决定、执行记录和历史事实以链接的 ADR、报告、History 或 Git 为准；
任务 ID 进入本表后不得复用。

| 日期 | ID | 任务 | 终态 | 关闭结果 | 证据 |
| --- | --- | --- | --- | --- | --- |
| 2026-07-30 | EXP-002 | Rudin 20 页评估与 Web 工程链路验收 | Completed | Rejected | [执行记录](../history/plans/2026-07/2026-07-rudin-engineering-chain-trial.md)与[脱敏报告](../../evaluation/documents/数学分析原理-第3版--341544f3fa9c/reports/rudin-qwen-ocr-20-v2.md)；链路完整，20 次调用，Web 抽检 2/5。 |
| 2026-07-30 | DATA-003 | 隔离真实评估运行环境 | Completed | Achieved | [执行记录](../history/plans/2026-07/2026-07-isolated-evaluation-runtime.md)；独立 revision 0004、API/Worker 和目录验证通过，xqfm11 计数未变化。 |
| 2026-07-30 | EVAL-003 | 评测 CLI 二进制差异指纹修复 | Completed | Achieved | 修订指纹禁用 Git textconv；17 项定向测试通过，真实 smoke ParseRun capture 成功并保存 64 位 diff hash。 |
| 2026-07-30 | EVAL-002 | 中性快照与单运行评估工作区 | Completed | Achieved | [执行记录](../history/plans/2026-07/2026-07-evaluation-assessment-workspace.md)与 [ADR 0022](../adr/0022-neutral-evaluation-snapshots-and-assessments.md)；全量 119 tests、双模式桌面/390px 视觉验收和静态门禁通过。 |
| 2026-07-30 | TEST-002 | 架构一致性与测试加固 | Completed | Achieved | REL-001 阻塞契约与 tracker 已同步；评测 CLI 新增 9 tests，五层全量 128 tests、Ruff、Web 语法和差异检查通过。 |
| 2026-07-30 | TEST-001 | 分层确定性测试架构 | Completed | Achieved | [ADR 0021](../adr/0021-layered-deterministic-test-architecture.md)与[测试标准](../standards/testing.md)；五层 119 tests、真实 API/Worker smoke 和 78% 覆盖率基线通过。 |
| 2026-07-30 | EVAL-001 | 文档中心解析评测基线 | Completed | Achieved | [ADR 0020](../adr/0020-document-centric-evaluation-workspace.md)与[执行记录](../history/plans/2026-07/2026-07-document-centric-evaluation.md)；评测专项 29 tests、隔离数据库全量 119 tests 和桌面/窄屏视觉验收通过。 |
| 2026-07-29 | DATA-001 | 本地运行数据与构建产物重置 | Completed | Achieved | [执行记录](../history/plans/2026-07/2026-07-local-data-reset.md)；精确清理 27,210,526 字节，全量 104 tests 通过，运行库后续问题登记 DATA-002。 |
| 2026-07-29 | QA-001 | 公开解析回归基线 | Completed | Achieved | [ADR 0019](../adr/0019-public-fixture-and-private-benchmark-boundary.md)；CC0 四页 fixture、完整事实比较器与全量 104 tests 通过。 |
| 2026-07-29 | REG-001 | GitHub Actions Pytest 失败 | Completed | Achieved | 提交 `907e196` 增加跨平台路径脱敏回归；Actions run `30438394283` success，98 tests。 |
| 2026-07-29 | ENG-001 | 主流程应用边界与包根迁移 | Completed | Achieved | [ADR 0018](../adr/0018-src-package-and-application-owned-workflows.md)；wheel 构建成功，API + Worker 完整发布闭环与全量 97 tests 通过。 |
| 2026-07-29 | ARCH-001 | API 绕过应用服务 | Completed | Achieved | [ADR 0018](../adr/0018-src-package-and-application-owned-workflows.md)；API/Worker 只调用应用服务，完整主链与架构门禁通过。 |
| 2026-07-29 | DOC-002 | Trackers 任务台账收口 | Completed | Achieved | [ADR 0017](../adr/0017-consolidated-task-ledgers-and-roadmap.md)；专项与全量门禁通过，旧 tracker 由 Git `205b9c9` 恢复。 |
| 2026-07-29 | DOC-001 | 文档治理阶段 | Completed | Achieved | Git `f76078a..205b9c9`；建立文档边界、架构/设计/指南/History/Plans/Standards 治理并删除派生模板。 |
| 2026-07-28 | EXP-001 | Rudin 20 页 OCR 试跑 | Completed | Rejected | [执行记录](../history/plans/2026-07/2026-07-v03-rudin-scan-ingestion.md)；链路完成，人工抽检 3/5。 |
| 2026-07-28 | BASE-004 | v0.3 结果工作台与存储 | Completed | Achieved | [执行记录](../history/plans/2026-07/2026-07-v03-result-workbench-storage.md)，提交 `a6ec4e0`。 |
| 2026-07-28 | BASE-003 | v0.3 架构重建 | Completed | Achieved | [执行记录](../history/plans/2026-07/2026-07-v03-architecture-rebuild.md)，提交 `be7ec34`。 |
| 2026-07-27 | BASE-002 | v0.2 首个本地闭环 | Completed | Achieved | [执行记录](../history/plans/2026-07/2026-07-v02-first-loop.md)，提交 `4961cfa`。 |
| 2026-05-15 | BASE-001 | v0.1 MinerU 基线 | Superseded | Achieved | [历史基线](../history/baselines/v01-mineru.md)，提交 `6cc4129`。 |
