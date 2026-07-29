# 规范索引

状态：Current
最后更新：2026-07-29

本目录是工程治理规则的唯一事实源。根 [AGENTS.md](../../AGENTS.md) 负责快速路由，指南负责操作
步骤，ADR 负责决定及理由；这些位置不得复制标准正文。

| 标准 | 治理对象 | 权威产物 | 自动门禁 |
| --- | --- | --- | --- |
| [任务生命周期](task-lifecycle.md) | 任务分类、计划准入、tracker 状态、实施门禁与关闭交付 | 计划正文及 tracker | `tests/test_plan_governance.py`、`tests/test_tracker_governance.py` |
| [文档规范](documentation.md) | 文档分类、写作、元数据、索引、命名、归档与删除 | 当前文档树及 History | `tests/test_document_structure.py`、`tests/test_markdown_links.py`、`tests/test_standard_governance.py` |
| [ADR 治理规范](adr-governance.md) | ADR 准入、全局编号、元数据、状态、取代关系与归档路径 | ADR 正文及 ADR index | `tests/test_adr_structure.py` |
| [文档与代码双向追溯规范](code-document-traceability.md) | code-map、模块 DesignRef、架构/设计关联和语义同步门禁 | `docs/architecture/code-map.md` | `tests/test_code_document_mapping.py`、`tests/test_architecture_documents.py`、`tests/test_design_documents.py` |
