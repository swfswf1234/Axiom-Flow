# 设计索引

状态：Current
最后更新：2026-07-29

本索引只负责从设计定位当前实现与测试；反向定位以
[code-map](../architecture/code-map.md) 为准。

| 设计 | 当前关联代码 | 当前关联测试 |
| --- | --- | --- |
| [文档解析流水线](document-pipeline.md) | `backend/infrastructure/pdf_pipeline.py`、`bailian.py`、`artifacts.py` | `tests/test_document_workflow.py`、`tests/test_providers.py` |
| [规范内容](normalized-content.md) | `backend/domain/models.py`、`backend/api/schemas.py` | `tests/test_document_workflow.py` |
| [质量审阅](quality-review.md) | 由解析与仓储服务组合实现 | `tests/test_document_workflow.py` |
| [知识模型](knowledge-model.md) | 由解析、仓储与工作簿服务组合实现 | `tests/test_document_workflow.py` |
| [Excel 发布工作流](excel-release-workflow.md) | `backend/application/workbooks.py` | `tests/test_document_workflow.py` |
| [Web 工作台](web-workbench.md) | `backend/api/main.py`、`backend/main.py`、`web/` | `tests/test_v03_api.py` |
| [评测治理](evaluation-governance.md) | `evaluation/scorecard.py`、`evaluation/preflight.py` | `tests/test_evaluation_scorecard.py`、`tests/test_evaluation_preflight.py` |
| [后台任务](background-jobs.md) | `backend/application/jobs.py`、`backend/worker/` | `tests/test_v03_jobs.py` |
