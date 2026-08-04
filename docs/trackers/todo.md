# 待做任务

状态：Current
最后更新：2026-08-04

本表是全部未关闭工作的统一导航。Plan 行镜像计划正文状态，计划正文仍是范围、验证和回滚的事实
源；其他行在具备范围、前置条件、验证与成功标准后建立独立计划，并保留原 ID。

| ID | 类型 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| REL-001 | Plan | P0 | Blocked | [v0.3 工程基线发布](../plans/2026-07-v03-engineering-baseline.md) | DATA-002 尚未关闭且发布实现未形成已验证提交；满足计划内恢复条件后再冻结发布目标。 |
| DATA-002 | Gap | P0 | Open | 运行库存在失效本地文件引用 | `xqfm11` 仍有 1 个文档、5 个 ParseRun、20 页和 103 个产物记录；建立独立 D 类备份与重建计划后清理。 |
| EXP-003 | Candidate | P0 | Candidate | 通用新增类别解析评测 | 新类别样本到达后新建 manifest，不回写 Rudin 单类别实验。 |
| DES-001 | Gap | P1 | Open | 自动质量信号不足 | v2 工程评估中第 39 页大段遗漏和第 24 页公式变量错误均通过自动完整性检查；需为遗漏、覆盖、公式和表格风险建立稳定规则或冻结评测门槛。 |
| WEB-001 | Gap | P1 | Open | 解析结果高保真页面渲染 | 第一阶段正确渲染 Markdown、LaTeX、表格和内容图片；第二阶段基于 block/bbox 评估版面级还原。实施前建立 B 类计划并冻结渲染安全、依赖、视觉样本和验收门槛。 |
| DES-002 | Gap | P1 | Open | 工作簿占位表无语义 | [发布设计](../design/excel-release-workflow.md)中的 `sections`、`review_notes` 必须决定删除或实现。 |
| OPS-001 | Gap | P1 | Open | 生产运维基线未实现 | [运维指南](../guides/operations.md)只支持本地手工运行；先以 ADR 选择部署边界，再完成备份恢复和发布演练。 |
| PRD-001 | Candidate | P1 | Candidate | 学习交互闭环 | 已发布知识可稳定检索后，设计对话、图片问答、练习和学习进度。 |
| JOB-001 | Candidate | P1 | Candidate | 批量导入与任务恢复策略 | 单篇实际样本验证后，明确队列、恢复策略和资源上限。 |
| RET-001 | Candidate | P2 | Candidate | 向量检索与图数据库投影 | 学习交互的检索需求、索引版本和同步语义经设计验证后立项。 |
| ALN-001 | Plan | P1 | In Progress | [计划 2026-08：QED-Engine 对齐请求登记与轻量更新](../plans/2026-08-qed-engine-alignment.md) | 计划状态 In Progress；用户确认 QED-Engine ADR 0002 后拆 B 类计划执行迁移。 |
| ALN-002 | Request | P1 | Open | 端口 8000→8902 迁移（CORS、README、指南） | QED-Engine 发起，见 ADR 0002；用户确认后拆 B 类计划执行。 |
| ALN-003 | Request | P1 | Open | 数据目录指向根 dataset/、直读 QED_ 变量、load-env.ps1 退役 | QED-Engine 发起；用户确认后拆 B 类计划执行。 |
| ALN-004 | Request | P1 | Open | web/ 前端迁入 QED-Engine 统一前端（8903），本仓库退役 web/ | QED-Engine 发起，见 ADR 0002；用户确认后拆 A/B 类计划执行。 |
| ALN-005 | Request | P1 | Open | OCR 多后端适配：qwen-vl-plus → glm-ocr（文档解析专用接口） | QED-Engine 发起（根仓库 REQ-008）；glm-ocr 走专用文档解析接口，需适配 bailian provider 后冻结样本评测；用户确认后拆 B 类计划执行。 |
