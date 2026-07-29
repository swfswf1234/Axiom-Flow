# 文档规范

状态：Current
最后更新：2026-07-29

## 写作原则

- 中文说明使用短句和明确主语；标识符、API 字段和外部协议名称保留英文。
- 当前事实、目标设计、决策理由、实施步骤和历史记录分别进入对应目录。
- 一个事实只设一个维护位置；其他文档使用链接，不复制整段状态或命令。
- 文件名使用小写英文和连字符；活跃文档使用稳定名称，不在文件名中绑定当前版本。

## 元数据

活跃架构和设计必须声明 `设计状态`、`实现状态`、`最后更新`、`关联代码`、`关联测试` 和
`关联 ADR`。计划声明设计与实现状态、范围、验证、回滚和完成条件。指南、标准、索引和追踪器
至少声明状态与最后更新。

- 设计状态：`Draft`、`Proposed`、`Accepted`、`Rejected`、`Superseded`、`Historical`。
- 实现状态：`Not Started`、`In Progress`、`Implemented`、`Verified`、`Blocked`、`Completed`。

`Implemented` 表示实现和本地定向门禁已完成；`Verified` 还要求适用的全量与远端门禁通过。
`Blocked` 必须写明阻塞证据、恢复条件和责任位置。

## 索引与归档

每个一级文档目录使用 `README.md` 作为入口。`plans/` 只保留未关闭计划；Completed 与
Superseded 计划移入 `history/plans/<year-month>/`。旧版本操作指南进入 `history/guides/`。
历史正文保留当时事实，可以补充 Historical 声明和修复链接，但不得改写旧结论。

删除或移动文档前必须先搜索所有引用；完成后运行 Markdown 链接和代码映射测试。
