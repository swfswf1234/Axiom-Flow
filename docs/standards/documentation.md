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

- `docs/index.md` 是文档总入口；各文档目录只使用小写 `index.md` 导航，不在索引中维护架构、
  流程、命令、测试结果或其他正文事实。
- `docs/**/README.md` 不允许存在。仓库根 `README.md` 是面向用户和新开发者的项目入口，属于
  唯一例外。
- 内部链接显式指向目标文件或 `index.md`，不依赖托管平台将目录自动解析为 README。
- 当前目录只保存仍然有效的事实。Rejected/Superseded ADR、Completed/Superseded 计划和旧版
  指南分别进入 `history/adr/`、`history/plans/<year-month>/` 和 `history/guides/`。
- 历史正文保留当时事实，可以补充 Historical 声明和修复链接，但不得改写旧结论。

文档移动或删除前必须先搜索全部引用。仍有唯一事实、决策依据或审计价值的文件必须移入对应
history 目录；只有精确重复、空草稿或不含唯一事实和审计价值的文件才可删除。完成后必须运行
文档结构、Markdown 链接和代码映射测试。
