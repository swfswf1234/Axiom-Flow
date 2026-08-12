# 数据库设计确认：af_* 表结构事实源（ALN-009，承接根仓库 REQ-027）

设计状态：Accepted
实现状态：Pending
最后更新：2026-08-10
关联代码：`src/axiom_flow/infrastructure/mysql.py`、`alembic` 迁移
关联测试：`tests/integration/test_mysql_migrations.py`
需求方：QED-Engine（根仓库 REQ-027；2026-08-09 用户裁决：数据库设计先在各子项目确认，
根仓库只做指引和规划）
执行方：Axiom-Flow
接口面：qed 库 `af_*` 表清单与结构（Alembic 迁移为事实源）
评审方：用户
验收标准：见下「成功标准」

## 背景

三项目共享 `qed` 库（根仓库 ADR 0003），表命名空间隔离：本仓库只使用 `af_*` 前缀表。
对齐轮（ALN-003）已规划 `af_*` 表 Alembic 初始化（qed 库），存量 `xqfm11` 库不迁移。
根仓库 `docs/design/database-design.md` 原承担表结构细节设计；2026-08-09 用户裁决：`af_*`
表清单与结构由本仓库设计确认，确认后回执根仓库补登记表清单，根仓库 database-design.md
按「指引与规划」收尾。

## 变更内容

1. **事实源声明**：`af_*` 表清单与结构的事实源为本仓库 Alembic 迁移
   （`src/axiom_flow/migrations/`）与 `infrastructure/mysql.py`；新增表先更新迁移与设计
   文档（先文档后实现）。
2. **回执**：本仓库确认 af_* 表清单后回执根仓库 REQ-027，根仓库 database-design.md 补登记
   表清单摘要并收尾为指引与规划。

## 成功标准

- 本仓库正式声明 `af_*` 表结构事实源位置（本文件与迁移链接生效）。
- 根仓库 REQ-027 收到回执，根仓库 database-design.md 收尾完成。