# 知识模型与图关系

设计状态：Accepted
实现状态：Verified
最后更新：2026-07-27
关联代码：由 `docs/architecture/code-map.md` 中的解析、仓储与工作簿服务组合实现  
关联测试：`tests/test_document_workflow.py`、`tests/test_code_document_mapping.py`
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0003-excel-publish-source-of-truth.md`

知识抽取只消费质量已接受的规范化内容，每次创建独立 `ExtractionRun` 并输出待审阅
`KnowledgeCandidate`。新抽取不得删除旧候选。候选和正式
`KnowledgeNode` 至少包含稳定 ID、类型、标题、正文、来源证据、置信度、状态和版本。
首期类型为章节、概念/定义、命题/定理、证明、例题、练习、算法与代码。

关系 `KnowledgeEdge` 至少支持 `CONTAINS`、`PREREQUISITE_OF`、`DEFINES`、
`PROVES`、`USES`、`ILLUSTRATES` 和 `RELATED_TO`。每条关系同样要保存证据与审阅状态，
不能由模型输出直接发布。每次审阅追加 `ReviewEvent`，实体上的当前状态只用于高效查询。

首期图谱由 MySQL 中的节点表和边表承载，Web 使用轻量原生 DOM 显示。图数据库投影、
向量检索和聊天功能均是后续消费者，不改变已发布知识的版本语义。
