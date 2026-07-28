# Excel 审阅与发布工作流

设计状态：Accepted
实现状态：Verified
最后更新：2026-07-27
关联代码：`backend/application/workbooks.py`
关联测试：`tests/test_document_workflow.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0003-excel-publish-source-of-truth.md`

导出工作簿包含 `documents`、`sections`、`knowledge_nodes`、`knowledge_edges` 和
`review_notes`。所有跨表引用使用 UUID；知识节点和边均保留证据、状态和来源版本。

导入流程固定为：上传工作簿 -> 模板、`document_id` 与字段校验 -> 引用、证据和枚举校验 -> 创建草稿版本 ->
用户显式发布。校验失败不得部分写入已发布快照。结构化差异预览属于后续增强；当前导入结果只
确认新草稿版本，不得宣称已提供逐项差异。

发布流程生成不可变 `KnowledgeRelease`。后续发布创建新版本并将旧版本标为 `superseded`；
运行时检索和图谱只读取最新已发布版本。Excel 文件变动不会自动同步。
