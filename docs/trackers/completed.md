# 已关闭任务

状态：Current
最后更新：2026-07-29

本表只保存关闭导航。详细决定、执行记录和历史事实以链接的 ADR、报告、History 或 Git 为准；
任务 ID 进入本表后不得复用。

| 日期 | ID | 任务 | 终态 | 关闭结果 | 证据 |
| --- | --- | --- | --- | --- | --- |
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
