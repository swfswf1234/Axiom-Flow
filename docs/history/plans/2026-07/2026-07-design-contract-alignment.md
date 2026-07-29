# Design 文档收敛与契约同步门禁

设计状态：Accepted
实现状态：Completed
最后更新：2026-07-29

## 目标与范围

将 `docs/design/` 收敛为解析与审阅、知识与发布、后台任务、Web 工作台和解析评测五份真实流程
契约，使用内嵌 Mermaid 表达交互，并建立接口字段、状态、工作表、关系、视图和评测常量的同步
门禁。本计划只修改文档与测试，不改变生产代码、HTTP API、数据库或运行数据。

## 工作项

- [x] 将规范内容和质量审阅合入解析设计，将知识模型合入工作簿发布设计。
- [x] 按实际代码重写五份契约并登记 `DES-001`、`DES-002`。
- [x] 增加 Design 目录、Mermaid 与关键代码契约的语义测试。
- [x] 同步 Agent 协议、追溯规范、测试指南、code-map 和 tracker。
- [x] 通过专项与全量本地门禁。

## 验证

设计语义、架构语义、依赖方向、代码映射、文档结构和 Markdown 链接专项测试共 30 项通过；
全量 Pytest 共 82 项通过；Ruff、JavaScript 语法和 `git diff --check` 通过。活跃代码与文档不再
引用 `normalized-content.md`、`quality-review.md`、`knowledge-model.md`。

## 回滚与完成条件

回滚锚点为 `7995e9e`。Design 目录只保留索引和五份流程契约，正文与当前代码事实一致，未实现
目标有稳定 DES 编号与关闭条件，全部门禁通过后标记 Completed 并归档。
