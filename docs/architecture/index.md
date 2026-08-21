# 架构索引

状态：Current
最后更新：2026-08-21

本目录描述 v2 系统结构、运行拓扑、对外契约与代码映射（v0.1 固定文档集，根仓库 ADR 0010
范本）。设计契约见 [design](../design/index.md)，实现与测试映射以 [code-map](code-map.md) 为准。

| 文档 | 内容 |
| --- | --- |
| [系统概览](overview.md) | v2 拓扑：Windows 编排层、WSL 容器推理、QED-Engine 前端 |
| [8902 API 接口文档](api.md) | 对外 `/api/v1` 契约总表（生命周期+健康 / 数据查询 / 解析结果与 PDF 对照 / RAG 预留，REQ-046 四类） |
| [数据库设计](database-design.md) | qed 库 af_* 命名空间私有表设计（af_books / af_block_reviews，V2-013 规划） |
| [代码映射](code-map.md) | 新包结构与 DesignRef 登记 |
