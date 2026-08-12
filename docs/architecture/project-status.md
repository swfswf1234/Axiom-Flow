# 项目状态快照

设计状态：Accepted
实现状态：Implemented
最后更新：2026-08-12
关联代码：无（状态快照，不映射具体模块）
关联测试：无
关联 ADR：无

## 用途

本文件是 Axiom-Flow 开发状态的单一事实源入口：Agent 进场先读本表，30 秒掌握「项目现在到哪了」。
具体任务状态以[任务台账](../trackers/todo.md)为准，未来方向以[能力路线图](../trackers/roadmap.md)
为准；本表只保存「当前实现状态」快照。

## 各域状态

| 域 | 状态 | 说明 |
| --- | --- | --- |
| 文档解析管道 | 已实现 | PDF 导入、OCR（阿里百炼，`AXIOM_VISION_MODEL` 配置）、内容规范化、ParseRun 逐页检查点与 SHA-256 manifest；API + Worker 于 **8902** 运行（ALN-002，2026-08-11） |
| 审阅工作台 | 已实现 | 本地 `web/` 单页工作台（8902 同源），对照原始页图、OCR、Markdown 与结构化结果 |
| 知识发布 | 已实现 | 知识候选、关系与审阅事件，经 Excel 工作簿显式发布 `KnowledgeRelease` |
| 解析评测 | 已实现 | 文档中心评测工作区、公开 fixture 回归、中性快照与单运行评估（ADR 0019/0020/0022） |
| 工程门禁 | 本地唯一 | 无远端 CI（ADR 0023，2026-08-12）；`pytest tests -q` + `ruff check src tests` 为唯一门禁 |
| 生产运维 | 未实现 | OPS-001：无认证、TLS、备份与监控，API 只绑定回环地址，不暴露公网 |

## 当前主线

- **文档对齐 QED-Engine 模板轮（DOCS-001，本计划）**：W0 根层（README 定位、requirements.txt 退役、
  8902 端口）✅、W1 standards（governance-contract / cross-project-collaboration 新增、契约头补齐）
  ✅、W2 architecture（本表新增）进行中；W3-W9（design/guides/trackers/plans/adr/history）待执行。
- **QED-Engine 对齐请求**：ALN-002 端口 8902 已完成（2026-08-11，提交 `29b6524`，根仓库 REQ-001 回执）；
  ALN-003 数据目录/`QED_*` 直读/qed 库待执行（B 类计划 ALN-007 Accepted）；ALN-004 前端迁移后置；
  ALN-006/008/009 待用户确认后执行。
- **解析质量**：真实数学教材公式 OCR 评测与人工审阅继续（EXP-003 候选，新类别样本到达后新建
  manifest）；公开 fixture 回归作为质量基线。
- 详情见[任务台账](../trackers/todo.md)。

## 维护规则

- 各域实现状态、端口或当前主线变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 todo.md / roadmap.md）；与架构文档（overview /
  runtime-architecture）的静态描述不一致时，以本表当前状态为准并回修对应架构文档。
- 端口、配置与跨项目事实以 ADR 与根仓库契约为准（8902 见 QED-Engine ADR 0002）。