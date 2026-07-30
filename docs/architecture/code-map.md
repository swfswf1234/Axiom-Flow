# 代码与设计映射表

设计状态：Accepted
实现状态：Implemented
维护位置：`docs/architecture/code-map.md`
关联代码：受管模块清单  
关联测试：`tests/contract/test_code_document_mapping.py`
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0008-immutable-parse-artifact-bundles.md`、`docs/adr/0010-qwen-ocr-only-rudin-trial.md`、`docs/adr/0011-current-parse-run-and-prunable-artifacts.md`、`docs/adr/0013-selective-history-retention.md`、`docs/adr/0015-standards-as-governance-source.md`、`docs/adr/0018-src-package-and-application-owned-workflows.md`、`docs/adr/0019-public-fixture-and-private-benchmark-boundary.md`、`docs/adr/0020-document-centric-evaluation-workspace.md`、`docs/adr/0021-layered-deterministic-test-architecture.md`、`docs/adr/0022-neutral-evaluation-snapshots-and-assessments.md`

本表是代码与文档关系的唯一事实源。v0.1 运行代码已删除，其范围和 Git 恢复锚点见
`docs/history/baselines/v01-mineru.md`。`__init__.py` 及无业务语义的极短文件豁免。

| 代码路径 | 层级/职责 | 状态 | 设计关联 | 关联测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `alembic.ini` | Alembic CLI 配置 | Current | `docs/architecture/data-lifecycle.md` | `tests/integration/test_mysql_migrations.py` | 定位迁移环境且不保存数据库凭证。 |
| `src/axiom_flow/infrastructure/config.py` | v0.3 本地配置 | Current | `docs/design/document-pipeline.md` | `tests/system/test_document_release_flow.py` | 仅读取本地 `.env` 与运行目录。 |
| `src/axiom_flow/infrastructure/bailian.py` | 百炼模型适配 | Current | `docs/design/document-pipeline.md` | `tests/unit/test_providers.py` | 隔离 DashScope OpenAI 兼容协议。 |
| `src/axiom_flow/infrastructure/artifacts.py` | 不可变解析产物包 | Current | `docs/design/document-pipeline.md` | `tests/integration/test_parse_artifacts.py` | 共享页图、v1/v2 哈希清单和完整性校验。 |
| `src/axiom_flow/infrastructure/pdf_pipeline.py` | PDF 解析基础设施 pipeline | Current | `docs/design/document-pipeline.md` | `tests/integration/test_jobs.py` | 逐页提交规范化页面事实。 |
| `src/axiom_flow/infrastructure/database.py` | MySQL 连接与迁移辅助 | Current | `docs/architecture/data-lifecycle.md` | `tests/integration/test_mysql_migrations.py` | 校验 schema 且不隐式迁移。 |
| `src/axiom_flow/infrastructure/evaluation_workspace.py` | 评测文件工作区适配器 | Current | `docs/design/evaluation-governance.md` | `tests/integration/test_evaluation_workspace.py` | 保存 case、中性快照、assessment、comparison 和人工结论。 |
| `src/axiom_flow/infrastructure/files.py` | 受控本地文件定位适配器 | Current | `docs/architecture/data-lifecycle.md` | `tests/integration/test_api.py` | 拒绝数据目录逃逸并返回文件资源。 |
| `src/axiom_flow/application/workbooks.py` | Excel 草稿与显式发布用例 | Current | `docs/design/excel-release-workflow.md` | `tests/system/test_document_release_flow.py` | 导入必须完成模板和证据校验。 |
| `src/axiom_flow/infrastructure/workbooks.py` | OpenPyXL 工作簿格式适配器 | Current | `docs/design/excel-release-workflow.md` | `tests/system/test_document_release_flow.py` | 只负责文件格式读写和结构校验。 |
| `src/axiom_flow/main.py` | 唯一 ASGI 入口 | Current | `docs/design/web-workbench.md` | `tests/integration/test_api.py` | 仅创建 API 应用。 |
| `src/axiom_flow/domain/models.py` | 状态、任务资源和领域错误 | Current | `docs/architecture/runtime-architecture.md` | `tests/integration/test_jobs.py` | 不依赖框架和供应商 SDK。 |
| `src/axiom_flow/bootstrap.py` | 应用与基础设施装配根 | Current | `docs/architecture/runtime-architecture.md` | `tests/contract/test_architecture_dependencies.py` | API 与 Worker 共用。 |
| `src/axiom_flow/application/ports.py` | 供应商与 pipeline 端口 | Current | `docs/architecture/runtime-architecture.md` | `tests/contract/test_architecture_dependencies.py` | 应用层不依赖适配器。 |
| `src/axiom_flow/application/documents.py` | 文档、运行、页面与产物用例 | Current | `docs/design/document-pipeline.md` | `tests/integration/test_api.py` | API 不直接访问仓储或本地路径。 |
| `src/axiom_flow/application/evaluations.py` | 文档中心评测用例 | Current | `docs/design/evaluation-governance.md` | `tests/unit/test_evaluation_comparison.py` | 编排中性快照、绝对评估、比较、评审和报告。 |
| `src/axiom_flow/application/evaluation_analysis.py` | 评估纯分析规则 | Current | `docs/design/evaluation-governance.md` | `tests/unit/test_evaluation_assessment.py` | 自动完整性、逐维差异和 profile 质量结论。 |
| `src/axiom_flow/application/reviews.py` | 页面与知识人工审阅用例 | Current | `docs/design/web-workbench.md` | `tests/system/test_document_release_flow.py` | 统一页面、节点和关系审阅入口。 |
| `src/axiom_flow/infrastructure/mysql.py` | 唯一 MySQL 仓储适配器 | Current | `docs/architecture/runtime-architecture.md` | `tests/integration/test_jobs.py` | 追加式运行、任务租约和审阅历史。 |
| `src/axiom_flow/application/jobs.py` | 任务提交与执行用例 | Current | `docs/design/background-jobs.md` | `tests/integration/test_jobs.py` | HTTP 和 Worker 共用。 |
| `src/axiom_flow/api/schemas.py` | API v1 请求与响应模型 | Current | `docs/architecture/runtime-architecture.md` | `tests/integration/test_api.py` | 统一错误、任务和产物资源。 |
| `src/axiom_flow/api/main.py` | API v1 与静态入口 | Current | `docs/design/web-workbench.md` | `tests/integration/test_api.py` | 长命令只入队。 |
| `src/axiom_flow/worker/runner.py` | 独立任务 Worker | Current | `docs/design/background-jobs.md` | `tests/integration/test_jobs.py` | 租约、重试和取消。 |
| `src/axiom_flow/worker/__main__.py` | Worker 命令入口 | Current | `docs/design/background-jobs.md` | `tests/integration/test_jobs.py` | 本地独立进程。 |
| `src/axiom_flow/tools/reset_dev_database.py` | 受保护开发库重建 | Current | `docs/adr/0007-versioned-domain-records.md` | `tests/unit/test_reset_safety.py` | 默认只允许测试库。 |
| `src/axiom_flow/tools/prune_parse_runs.py` | 受保护解析运行清理 | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | `tests/integration/test_prune_parse_runs.py` | dry-run、暂存、回滚和显式 purge。 |
| `src/axiom_flow/migrations/env.py` | Alembic 迁移运行环境 | Current | `docs/architecture/data-lifecycle.md` | `tests/integration/test_mysql_migrations.py` | 只由显式迁移命令调用。 |
| `src/axiom_flow/migrations/script.py.mako` | Alembic revision 生成模板 | Current | `docs/architecture/data-lifecycle.md` | `tests/integration/test_mysql_migrations.py` | 生成符合中文追溯规范的空迁移骨架。 |
| `src/axiom_flow/migrations/versions/20260727_0001_mysql_v02.py` | MySQL 初始 schema | Current | `docs/architecture/data-lifecycle.md` | `tests/integration/test_mysql_migrations.py` | 创建基础 `af_` 表。 |
| `src/axiom_flow/migrations/versions/20260727_0002_v03_jobs_and_history.py` | 任务与历史 schema | Current | `docs/architecture/runtime-architecture.md` | `tests/integration/test_jobs.py` | 增加任务和版本表。 |
| `src/axiom_flow/migrations/versions/20260728_0003_parse_artifacts.py` | 解析产物元数据 schema | Current | `docs/adr/0008-immutable-parse-artifact-bundles.md` | `tests/integration/test_mysql_migrations.py` | 增加 MIME、大小和定位元数据。 |
| `src/axiom_flow/migrations/versions/20260728_0004_current_parse_run.py` | 当前运行与清理状态 schema | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | `tests/integration/test_mysql_migrations.py` | 指针、选择历史、墓碑和回填。 |
| `web/index.html` | v0.3 工作台页面结构 | Current | `docs/design/web-workbench.md` | `tests/integration/test_evaluation_api.py` | 包含单次质量双栏和版本对比三栏。 |
| `web/style.css` | v0.3 工作台样式 | Current | `docs/design/web-workbench.md` | `tests/integration/test_evaluation_api.py` | 桌面多栏、390px 分段单栏和横向页导航。 |
| `web/app.js` | v0.3 工作台交互 | Current | `docs/design/web-workbench.md` | `tests/integration/test_evaluation_api.py` | 通过 API v1 管理 assessment/comparison 审阅，不执行模型或 Git。 |
| `evaluation/__main__.py` | 文档中心评测模块入口 | Current | `docs/design/evaluation-governance.md` | `tests/unit/test_evaluation_cli.py` | 转发到统一 CLI。 |
| `evaluation/cli.py` | 文档中心评测开发 CLI | Current | `docs/design/evaluation-governance.md` | `tests/unit/test_evaluation_cli.py` | 只提交并轮询 Job，由独立 Worker 执行模型任务。 |
| `evaluation/tools/fixture_builder.py` | 公开数学 fixture 重建 | Current | `docs/design/evaluation-governance.md` | `tests/system/test_public_fixture_regression.py` | 生成自有 PDF、replay 和金标包。 |
| `evaluation/tools/replay.py` | 确定性解析产物回放 | Current | `docs/design/evaluation-governance.md` | `tests/system/test_public_fixture_regression.py` | 复用生产 ParseArtifactWriter 且不调用模型。 |
| `evaluation/tools/regression.py` | 完整页面事实自动比较 | Current | `docs/design/evaluation-governance.md` | `tests/system/test_public_fixture_regression.py` | 校验文本、结构、公式、表格、图片、bbox 和 manifest。 |
| `tests/conftest.py` | 测试层级、网络与 fixture 入口 | Current | `docs/standards/testing.md` | `tests/contract/test_test_suite_governance.py` | 自动附加 marker、拒绝外网并延迟加载 MySQL 支持。 |
| `tests/contract/test_adr_governance.py` | ADR 治理测试 | Current | `docs/standards/adr-governance.md` | — | 守护全局编号、登记表、元数据和完整取代关系。 |
| `tests/contract/test_architecture_dependencies.py` | Python 包依赖方向测试 | Current | `docs/architecture/runtime-architecture.md` | — | 禁止领域和应用层反向依赖。 |
| `tests/contract/test_architecture_documents.py` | 架构文档语义同步测试 | Current | `docs/standards/code-document-traceability.md` | — | 守护 Mermaid 视图、领域状态和已知架构偏差。 |
| `tests/contract/test_code_document_mapping.py` | 映射一致性测试 | Current | `docs/standards/code-document-traceability.md` | — | 守护本表和文件头。 |
| `tests/contract/test_design_documents.py` | 设计契约语义同步测试 | Current | `docs/standards/code-document-traceability.md` | — | 守护流程图、接口字段和关键常量。 |
| `tests/contract/test_document_structure.py` | 文档结构测试 | Current | `docs/standards/documentation.md` | — | 守护入口、目录边界、计划归档和 Agent 协议。 |
| `tests/contract/test_markdown_links.py` | Markdown 链接测试 | Current | `docs/standards/documentation.md` | — | 守护仓库本地相对链接。 |
| `tests/contract/test_plan_governance.py` | 计划治理测试 | Current | `docs/standards/task-lifecycle.md` | — | 守护活跃计划元数据、状态和索引边界。 |
| `tests/contract/test_standard_governance.py` | Standards 治理测试 | Current | `docs/standards/documentation.md` | — | 守护标准目录边界、统一契约、索引和测试反向关联。 |
| `tests/contract/test_test_suite_governance.py` | 测试套件治理测试 | Current | `docs/standards/testing.md` | — | 守护五层目录、marker 和根 fixture 边界。 |
| `tests/contract/test_tracker_governance.py` | Tracker 治理测试 | Current | `docs/standards/task-lifecycle.md` | — | 守护任务 ID、Plan 镜像、关闭证据和无状态路线图。 |
| `tests/integration/test_api.py` | API v1 集成测试 | Current | `docs/design/web-workbench.md` | — | API 进程不执行模型任务。 |
| `tests/integration/test_current_parse_runs.py` | 当前解析运行测试 | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | — | 覆盖显式选择、历史和非法候选。 |
| `tests/integration/test_evaluation_api.py` | 评测 API 集成测试 | Current | `docs/design/web-workbench.md` | — | 拒绝绝对路径泄漏并返回对照资源。 |
| `tests/integration/test_evaluation_workspace.py` | 评测工作区集成测试 | Current | `docs/design/evaluation-governance.md` | — | 覆盖命名、快照完整性和损坏输入。 |
| `tests/integration/test_jobs.py` | 任务与历史测试 | Current | `docs/design/background-jobs.md` | — | 覆盖幂等、租约、取消、续跑和版本。 |
| `tests/integration/test_mysql_migrations.py` | MySQL 迁移与隔离测试 | Current | `docs/architecture/data-lifecycle.md` | — | 验证迁移幂等和表边界。 |
| `tests/integration/test_parse_artifacts.py` | 解析产物包测试 | Current | `docs/design/document-pipeline.md` | — | 覆盖固定路径、清单和哈希。 |
| `tests/integration/test_prune_parse_runs.py` | 解析运行清理测试 | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | — | 覆盖预演、保护、回滚和 purge。 |
| `tests/smoke/conftest.py` | smoke 数据库隔离 | Current | `docs/standards/testing.md` | — | 创建并清理独立 `_smoke` 数据库。 |
| `tests/smoke/test_process_startup.py` | API/Worker 进程冒烟 | Current | `docs/standards/testing.md` | — | 使用真实命令入口且不提交模型任务。 |
| `tests/support/mysql.py` | MySQL 测试库 fixture | Current | `docs/standards/testing.md` | — | 只创建隔离测试库并清理 `af_` 表。 |
| `tests/support/pdf.py` | 确定性 PDF builder | Current | `docs/standards/testing.md` | — | 生成带文字层的最小 PDF。 |
| `tests/support/providers.py` | 确定性供应商替身 | Current | `docs/standards/testing.md` | — | 不访问外部模型。 |
| `tests/system/test_document_release_flow.py` | 主链闭环测试 | Current | `docs/architecture/data-lifecycle.md` | — | 使用确定性假模型。 |
| `tests/system/test_public_fixture_regression.py` | 公开 fixture 完整事实回归 | Current | `docs/design/evaluation-governance.md` | — | 覆盖 replay、文本、公式、表格、图片、bbox 和哈希篡改。 |
| `tests/unit/test_evaluation_comparison.py` | 冻结快照比较测试 | Current | `docs/design/evaluation-governance.md` | — | 覆盖基线规则、页集合和人工结论。 |
| `tests/unit/test_evaluation_cli.py` | 评测 CLI 单元测试 | Current | `docs/design/evaluation-governance.md` | — | 覆盖修订指纹、独立 Worker 轮询、超时恢复和退出码。 |
| `tests/unit/test_evaluation_assessment.py` | 单运行质量评估测试 | Current | `docs/design/evaluation-governance.md` | — | 覆盖自动检查、两类 profile、人工结论和报告范围。 |
| `tests/unit/test_providers.py` | 百炼响应归一化测试 | Current | `docs/design/document-pipeline.md` | — | 覆盖截断、顶层结构、重试和元数据。 |
| `tests/unit/test_reset_safety.py` | 数据重建保护测试 | Current | `docs/adr/0007-versioned-domain-records.md` | — | 拒绝系统库和错误确认词。 |
