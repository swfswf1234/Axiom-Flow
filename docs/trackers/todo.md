# 待做任务

状态：Current
最后更新：2026-07-29

本表是全部未关闭工作的统一导航。Plan 行镜像计划正文状态，计划正文仍是范围、验证和回滚的事实
源；其他行在具备范围、前置条件、验证与成功标准后建立独立计划，并保留原 ID。

| ID | 类型 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| REL-001 | Plan | P0 | Blocked | [v0.3 工程基线发布](../plans/2026-07-v03-engineering-baseline.md) | REG-001 关闭且预定发布提交的远端门禁通过。 |
| REG-001 | Plan | P0 | In Progress | [CI 跨平台路径脱敏修复](../plans/2026-07-ci-path-redaction.md) | run `30437375294` 复现 POSIX 绝对路径未脱敏；补跨平台回归并使同一工作流通过。 |
| EXP-002 | Candidate | P0 | Candidate | Rudin 扫描教材新解析候选 | 当前单模型工程链路人工抽检 3/5；新候选必须使用冻结样本重新评测。 |
| EXP-003 | Candidate | P0 | Candidate | 通用新增类别解析评测 | 新类别样本到达后新建 manifest，不回写 Rudin 单类别实验。 |
| DES-001 | Gap | P1 | Open | 自动质量信号不足 | [解析设计](../design/document-pipeline.md)尚缺乱码、覆盖、页数、公式和表格风险的稳定规则或评测门槛。 |
| DES-002 | Gap | P1 | Open | 工作簿占位表无语义 | [发布设计](../design/excel-release-workflow.md)中的 `sections`、`review_notes` 必须决定删除或实现。 |
| OPS-001 | Gap | P1 | Open | 生产运维基线未实现 | [运维指南](../guides/operations.md)只支持本地手工运行；先以 ADR 选择部署边界，再完成备份恢复和发布演练。 |
| PRD-001 | Candidate | P1 | Candidate | 学习交互闭环 | 已发布知识可稳定检索后，设计对话、图片问答、练习和学习进度。 |
| JOB-001 | Candidate | P1 | Candidate | 批量导入与任务恢复策略 | 单篇实际样本验证后，明确队列、恢复策略和资源上限。 |
| QA-001 | Candidate | P2 | Candidate | 扫描件质量规则与人工标注回归集 | 已有 Rudin 12 页评分；待达标候选后转为自动回归。 |
| RET-001 | Candidate | P2 | Candidate | 向量检索与图数据库投影 | 学习交互的检索需求、索引版本和同步语义经设计验证后立项。 |
