# Docs 索引与历史归档规范重构

设计状态：Accepted
实现状态：Completed
最后更新：2026-07-29

## 目标与范围

将 `docs/**/README.md` 统一迁移为纯导航 `index.md`，并把 Rejected/Superseded ADR 归档到
`docs/history/adr/`。本计划只修改文档结构、内部链接和对应门禁，不改变生产行为或数据。

## 工作项

- [x] 迁移 15 个 README 并更新全部活跃链接与索引职责。
- [x] 将 ADR 0001、0002、0004 移入 history，保持全局登记和双向关系。
- [x] 明确当前、历史和可删除文档的边界。
- [x] 更新文档、ADR、映射和链接门禁。
- [x] 通过专项与全量本地门禁。

## 验证

文档索引、ADR、Markdown 链接和代码映射专项测试共 17 项通过；全量 Pytest 共 69 项通过；
Ruff、JavaScript 语法和 `git diff --check` 通过。

## 回滚与完成条件

回滚锚点为 `533c06d`。`docs/` 下不存在 README，所有索引和历史 ADR 链接有效，当前与历史状态
位置一致，全部适用门禁通过后标记 Completed 并归档。
