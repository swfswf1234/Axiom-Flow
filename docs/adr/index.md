# ADR 索引

状态：Current
最后更新：2026-10-22

本目录登记影响长期约束的决定。编号从 0001 重新开始（v2 新纪元，v1 决策已归档至
`docs/history/v1-20260814/adr/`）。规则见 [ADR 治理](../standards/adr-governance.md)。

**当前版本：v1.0（解析与知识库优化轮，PLAN-009 线）**——v0.1 已收口（标签 `v0.1.0` 合入 `main`，
2026-09-24；用户 2026-10-01 确认切换版本）。本索引登记当前版本的架构决策（ADR 0001 v2 探索方向，
文档体系对齐根仓库 ADR 0010 范本＝`QED-Engine/docs/history/adr/v0.1/0010-documentation-versioning.md`；
跨仓引用一律写纯路径、不猜 URL（根侧纪元整理会把旧 ADR 整批迁入 `history/adr/v0.1/`，路径会变而
同机可 `rg` 复算）。
版本末期（用户确认升版本）时，本版本决策合并进 `architecture/` 或其他固定文档，`history/`
记录前版本。

## 当前 ADR

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| [0001](0001-v2-exploration-direction.md) | v2 探索方向：推倒重来与后端解析定位 | Accepted |
| [0002](0002-pending-design-location.md) | 待评审设计的目录流转（Draft 设计先入 plans/，确定后落 design/；范本：根仓库 ADR 0011） | Accepted |
| [0003](0003-standards-governance-alignment.md) | 文档治理体系对齐 QED-Tracker 成熟范本（doc-governance 改名扩写、local-dev、project-status、确认状态） | Accepted |
| [0004](0004-model-boundary-and-pipeline-ownership.md) | 解析管线归位与模型直连（对齐根仓库 ADR 0014：模型生命周期归 8900，解析管线归 Axiom-Flow） | Accepted |
| [0005](0005-mysql-integration-test-isolation.md) | MySQL 集成测试的隔离边界（`QED_TEST_MYSQL=1` 显式 opt-in，只用 `qed_test` 库内本方自有的 `af_*` 表族（以 `AF_TABLES` 为准）+ `alembic_version`，绝不 `DROP DATABASE`，缺开关时 skip） | Accepted |
| [0006](0006-ledger-plan-carrier-contract.md) | 任务台账与计划承载契约（一任务一承载、进度单源、关闭条件成文、In Progress ≤3、DEFECT 前缀退役） | Accepted |
| [0007](0007-parse-status-semantics.md) | 解析状态语义三分（任务结局三态 + `failed_pages`、书级进度/结局分列、启动期归一（单实例前提）） | Accepted |
| [0008](0008-fixed-doc-form-and-metadata-genres.md) | 固定文档的体裁化元数据、统一四段骨架、正文去日期化与可对账契约表 | Accepted |
| [0009](0009-data-root-must-be-explicit.md) | 数据根必须显式配置（去仓内静默回退与留档区双身份、顶层白名单哨兵区、写入面锁 `parsed/`） | Accepted |
| [0010](0010-page-regions-are-parse-inputs.md) | 页框图是页级解析输入，不是版本内覆盖层（人工意图落页级、`af_block_edits` 语义不变、区域视图服务端投影合并归 8902、每框一次单页提交带退化路径与框数上限、legacy 无生效版本拒绝一切结构操作；承载已由 0016 折叠为 `af_operations` 的 `structure`／`edit` 意图行，不变式原样保留） | Accepted |
| [0011](0011-ledger-one-row-per-task.md) | 台账一任务一行（专属承接计划只保留 `PLAN-*` 镜像行、请求号降为来源标注；共享计划承接仍登记 `REQ-*` 支线行） | Accepted |
| [0012](0012-workspace-directory-hygiene.md) | 工作区目录卫生（仓库根 `tmp/` 唯一临时区且不入库、仓内留档区退役并取代 ADR 0009 决定 2、`scripts/` 只放长期受管脚本、`logs/` 只写本项目日志且 ≤3 文件、本仓库不含前端） | Accepted |
| [0013](0013-ledger-row-three-elements.md) | 台账行的三要素（`任务`＝任务描述含来源标记、`成功标准`＝完成时的具体终态且禁验收流程措辞、`承载`＝唯一承载文档指针（恰好 1 个链接，`design/`/`architecture/` 引用退回承载文档的关联设计）；日期、现状与细节只活在 `plans/`，取代 ADR 0006 的台账列名与列语义表述） | Accepted |
| [0014](0014-develop-branch-rename.md) | 开发分支改名 `develop`、`release` 名称退役（分支职责与合并纪律不变；`AGENTS.md`＋testing＋doc-governance 三处正文同步；历史与当时记录的 `release` 措辞不回改） | Accepted |
| [0015](0015-db-single-source-for-readiness.md) | 就绪与激活判据以 DB 为唯一事实源（`af_pages` 页行＋`af_books.active_job_id`＋版本终态（现为 `af_operations` 的 parse 行）之外无判据源；fs 只作完整性披露与清点输入；抗崩溃由提交序承担，不引入 manifest-last；取代 ADR 0007 决定 1 的「产物在位」子句） | Accepted |
| [0016](0016-af-family-rebuild-into-four-tables.md) | `af_*` 表族整族重建为四表（`af_books`／`af_operations`／`af_pages`／`af_kp`；`lifecycle` 分事件与意图两态、当前态只有四个来源、幂等由 `intent_key` 部分唯一索引承担、档 B 迁移链重置为一条 base、检索索引版本以「一值一列」并入且本轮无写者、`af_kp.links` 越 JSON 反向闸的例外与转表判据） | Accepted |
| [0017](0017-database-design-eight-chapter-skeleton.md) | 数据库设计篇改用根侧八章编号体（骨架体裁分治：四段体为默认、`database-design.md` 用八章；追溯段随体裁移动、可对账锚点与章号解耦；部分取代 ADR 0008 决定 2 与决定 3 的适用范围） | Accepted |

下一个可用编号：0018
