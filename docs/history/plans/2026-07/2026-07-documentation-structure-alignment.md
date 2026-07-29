# 文档结构与开发流程对齐

设计状态：Accepted
实现状态：Completed
最后更新：2026-07-29

## 目标与范围

建立根 README、文档入口和 Agent 协议的单一职责，按 architecture、design、ADR、standards、
guides、plans、trackers、templates 与 history 划分文档边界。本计划不修改生产行为、数据库、
解析产物或模型路由。

## 工作项

- [x] 删除活跃 `docs/agents_read.md` 兼容入口并重写根、docs 两级 README。
- [x] 建立 standards、templates 和各一级目录 README。
- [x] 将当前架构与指南改为稳定名称，归档旧版本指南。
- [x] 将 Completed 与 Superseded 计划移入 history。
- [x] 同步代码映射、DesignRef、计划和追踪器。
- [x] 增加文档结构门禁并通过适用的本地回归。

## 验证

运行文档结构、Markdown 链接、代码文档映射、Ruff、全量 Pytest、JavaScript 语法和
`git diff --check`。本轮不调用外部模型。

## 回滚与完成条件

回滚锚点为 `a6ec4e0`。所有移动通过 Git 历史保留；若入口或链接门禁失败，整体回滚本轮文档
提交。结构、索引、元数据和链接已经通过；本计划按文档规范移入历史计划目录。
