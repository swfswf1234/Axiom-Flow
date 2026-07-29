# Architecture 文档对齐与语义同步门禁

设计状态：Accepted
实现状态：Completed
最后更新：2026-07-29

## 目标与范围

将 `docs/architecture/` 收敛为系统边界、运行架构、数据生命周期和代码映射四份正文，使用
内嵌 Mermaid 表达当前架构，并建立图、领域状态、DesignRef 和已知偏差的同步门禁。本计划只
修改文档与测试，不改变 Backend、API、数据库或运行数据。

## 工作项

- [x] 重写系统总览、运行架构和数据生命周期，合并并删除独立领域边界文档。
- [x] 同时表达 Accepted 架构约束、当前实现符合度和 `ARCH-001` 偏差。
- [x] 增加架构图、领域枚举、索引和偏差状态的语义测试。
- [x] 同步 Agent 协议、追溯规范、测试指南、code-map 和 tracker。
- [x] 通过专项与全量本地门禁。

## 验证

架构文档、依赖方向、代码映射、文档结构和 Markdown 链接专项测试共 22 项通过；全量 Pytest
共 74 项通过；Ruff、JavaScript 语法和 `git diff --check` 通过。活跃架构目录不再包含
`domain-boundaries.md`。

## 回滚与完成条件

回滚锚点为 `ae5fec5`。架构目录只保留约定入口与四份正文，Mermaid 视图覆盖系统上下文、运行
拓扑、依赖方向、数据流和真实状态，`ARCH-001` 与代码事实同步，全部门禁通过后标记 Completed
并归档。
