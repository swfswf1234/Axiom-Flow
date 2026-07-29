# 设计文档索引

设计状态：Accepted
实现状态：Implemented
维护位置：`docs/design/README.md`
关联代码：各设计的受管实现见 `docs/architecture/code-map.md`  
关联测试：`tests/test_code_document_mapping.py`、`tests/test_evaluation_scorecard.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0010-qwen-ocr-only-rudin-trial.md`

本索引只负责从设计定位当前代码和测试，不承载设计细节。反向定位请使用
`docs/architecture/code-map.md`。

| 设计 | 当前关联代码 | 当前关联测试 |
| --- | --- | --- |
| `document-pipeline.md` | `backend/infrastructure/pdf_pipeline.py`、`bailian.py`、`artifacts.py` | `tests/test_document_workflow.py`、`tests/test_providers.py` |
| `normalized-content.md` | `backend/domain/models.py`、`backend/api/schemas.py` | `tests/test_document_workflow.py` |
| `quality-review.md` | 由解析与仓储服务组合实现 | `tests/test_document_workflow.py` |
| `knowledge-model.md` | 由解析、仓储与工作簿服务组合实现 | `tests/test_document_workflow.py` |
| `excel-release-workflow.md` | `backend/application/workbooks.py` | `tests/test_document_workflow.py` |
| `web-workbench.md` | `backend/api/main.py`、`backend/main.py`、`web/` | `tests/test_v03_api.py` |
| `evaluation-governance.md` | `evaluation/scorecard.py`、`evaluation/preflight.py` | `tests/test_evaluation_scorecard.py`、`tests/test_evaluation_preflight.py` |
| `background-jobs.md` | `backend/application/jobs.py`、`backend/worker/` | `tests/test_v03_jobs.py` |
