# 设计索引

状态：Current
最后更新：2026-07-29

本索引只负责从设计定位当前实现与测试；反向定位以
[code-map](../architecture/code-map.md) 为准。

| 设计 | 当前关联代码 | 当前关联测试 |
| --- | --- | --- |
| [解析、规范内容与页级审阅](document-pipeline.md) | PDF pipeline、百炼和产物适配器 | 文档工作流、供应商和产物测试 |
| [知识审阅、工作簿与发布](excel-release-workflow.md) | `backend/application/workbooks.py` | 主链发布测试 |
| [持久化后台任务](background-jobs.md) | 任务应用服务与 Worker | 任务、租约和恢复测试 |
| [Web 与 API v1 工作台](web-workbench.md) | API 入口与 `web/` | API 与工作台集成测试 |
| [解析评测治理](evaluation-governance.md) | `evaluation/` 执行与评分模块 | 评测专项测试 |
