# 运行与产物指南

状态：Current
最后更新：2026-07-29

运行期必须区分原件、解析产物、草稿工作簿和已发布快照。原件与已发布版本不得被后台
任务覆盖或清理。模型原始响应可用于追溯，但必须避免写入密钥、请求授权信息或其他敏感
配置。

当前启动与迁移命令见 [`local-development.md`](local-development.md)。运行库为既有 MySQL
实例中的 `af_` 前缀表，表结构
只允许通过 `python -m alembic upgrade head` 变更；应用启动会校验 schema，不能自动建表。

本地 `data/` 保存原 PDF、页图、工作簿和可重建的诊断产物，不纳入版本控制。清理前必须确认
目标不含原件或已发布快照；数据库备份、批处理保留周期和生产部署仍是后续运维任务。在此之前
不得将历史 MinerU 输出目录约定当作当前规范。显式重建规则见 ADR 0007；普通运维不得直接
执行 `DROP`、`TRUNCATE` 或 Alembic `downgrade base`。

新解析运行的私有文件固定写入 `data/documents/<内容哈希>/parse-runs/<运行 ID>/`，共享页图写入
`page-assets/render-200dpi-v1/`。正式整书运行前必须记录
源文件哈希、数据库表和行数、目标目录状态、可用空间、模型预算和回滚位置；最终
`manifest.json` 通过后才可关闭任务。

旧运行只能通过 `backend.tools.prune_parse_runs` 清理。命令默认 dry-run，`stage --apply` 仍只把
目录移动到 `data/trash/<operation-id>/` 并保留墓碑；复验当前 manifest 和 API 后再运行
`purge --operation-id ... --apply`。每次 apply 都必须同时给出完整文档 SHA-256 和当前
`--keep-run-id`。purge 前可用 `rollback --operation-id ... --apply` 恢复。禁止直接删除
`parse-runs/`、`page-assets/` 或数据库行。
