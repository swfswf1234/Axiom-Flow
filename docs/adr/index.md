# ADR 索引

状态：Current
最后更新：2026-09-23

本目录登记影响长期约束的决定。编号从 0001 重新开始（v2 新纪元，v1 决策已归档至
`docs/history/v1-20260814/adr/`）。规则见 [ADR 治理](../standards/adr-governance.md)。

**当前版本：v0.1（跑通完整服务）**——本索引登记当前版本的架构决策（ADR 0001 v2 探索方向，
文档体系对齐根仓库 [ADR 0010](https://github.com/QED-Engine/qed-engine/blob/main/docs/adr/0010-documentation-versioning.md) 范本）。
版本末期（用户确认升版本）时，本版本决策合并进 `architecture/` 或其他固定文档，`history/`
记录前版本。

## 当前 ADR

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| [0001](0001-v2-exploration-direction.md) | v2 探索方向：推倒重来与后端解析定位 | Accepted |
| [0002](0002-pending-design-location.md) | 待评审设计的目录流转（Draft 设计先入 plans/，确定后落 design/；范本：根仓库 ADR 0011） | Accepted |
| [0003](0003-standards-governance-alignment.md) | 文档治理体系对齐 QED-Tracker 成熟范本（doc-governance 改名扩写、local-dev、project-status、确认状态） | Accepted |
| [0004](0004-model-boundary-and-pipeline-ownership.md) | 解析管线归位与模型直连（对齐根仓库 ADR 0014：模型生命周期归 8900，解析管线归 Axiom-Flow） | Accepted |
| [0005](0005-mysql-integration-test-isolation.md) | MySQL 集成测试的隔离边界（`QED_TEST_MYSQL=1` 显式 opt-in，只用 `qed_test` 库内本方自有的 `af_*` 四表 + `alembic_version`，绝不 `DROP DATABASE`，缺开关时 skip） | Accepted |
| [0006](0006-ledger-plan-carrier-contract.md) | 任务台账与计划承载契约（一任务一承载、进度单源、关闭条件成文、In Progress ≤3、DEFECT 前缀退役） | Accepted |
| [0007](0007-parse-status-semantics.md) | 解析状态语义三分（任务结局三态 + `failed_pages`、书级进度/结局分列、启动期归一（单实例前提）） | Accepted |
| [0008](0008-fixed-doc-form-and-metadata-genres.md) | 固定文档的体裁化元数据、统一四段骨架、正文去日期化与可对账契约表 | Accepted |

下一个可用编号：0009
