# 设计文档索引

状态：Current  
维护位置：`docs/design/index.md`  
关联代码：各设计的受管实现见 `docs/architecture/code-map.md`  
关联测试：`tests/test_code_document_mapping.py`、`tests/test_evaluation_scorecard.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0002-parser-routing-and-provider-boundary.md`

本索引只负责从设计定位当前代码和测试，不承载设计细节。反向定位请使用
`docs/architecture/code-map.md`。

| 设计 | 当前关联代码 | 当前关联测试 |
| --- | --- | --- |
| `document-pipeline.md` | `backend/app/config.py`、`providers.py`、`pipeline.py` | `tests/test_v02_pipeline.py`、`tests/test_providers.py` |
| `normalized-content.md` | `backend/app/models.py` | `tests/test_v02_pipeline.py` |
| `quality-review.md` | 由解析与仓储服务组合实现 | `tests/test_v02_pipeline.py` |
| `knowledge-model.md` | 由解析、仓储与工作簿服务组合实现 | `tests/test_v02_pipeline.py` |
| `excel-release-workflow.md` | `backend/app/workbook.py` | `tests/test_v02_pipeline.py` |
| `web-workbench.md` | `backend/app/main.py`、`web/` | `tests/test_v02_pipeline.py` |
| `evaluation-governance.md` | `evaluation/scorecard.py`、`evaluation/preflight.py` | `tests/test_evaluation_scorecard.py`、`tests/test_evaluation_preflight.py` |
| `background-jobs.md` | 尚未实现 | `tests/test_code_document_mapping.py` |
