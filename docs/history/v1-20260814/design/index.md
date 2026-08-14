# 设计索引

状态：Current
最后更新：2026-08-12

本目录保存当前流程、接口、状态、失败语义与验收契约。文档分类与元数据规则见
[文档规范](../standards/documentation.md)；反向定位以 [code-map](../architecture/code-map.md) 为准。

## 当前设计

| 文档 | 设计状态 | 实现状态 | 内容 |
| --- | --- | --- | --- |
| [解析、规范内容与页级审阅](document-pipeline.md) | Accepted | In Progress | PDF 导入、OCR、内容规范化、来源证据与页级审阅契约 |
| [知识审阅、工作簿与发布](excel-release-workflow.md) | Accepted | In Progress | 知识候选、关系、工作簿与显式 `KnowledgeRelease` |
| [持久化后台任务](background-jobs.md) | Accepted | Implemented | MySQL 持久任务、租约、取消与重试 |
| [Web 与 API v1 工作台](web-workbench.md) | Accepted | Implemented | API v1 协议、文件边界与静态工作台 |
| [解析评测治理](evaluation-governance.md) | Accepted | Implemented | 文档中心评测、冻结主链对比与评分门槛 |
| [数据库结构事实源（ALN-009）](database-schema-ownership.md) | Accepted | Pending | `af_*` 表清单与结构由本仓库 Alembic 迁移确认 |
| [治理契约范本对齐（ALN-008）](governance-contract-alignment.md) | Accepted | Pending | 契约头六字段、守护面裁剪与新增流程对齐根仓库范本 |
