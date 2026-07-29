# 代码与设计映射表

设计状态：Accepted
实现状态：Implemented
维护位置：`docs/architecture/code-map.md`  
关联代码：受管模块清单  
关联测试：`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`、`docs/adr/0008-immutable-parse-artifact-bundles.md`、`docs/adr/0010-qwen-ocr-only-rudin-trial.md`、`docs/adr/0011-current-parse-run-and-prunable-artifacts.md`、`docs/adr/0012-backend-package-boundaries.md`、`docs/adr/0013-selective-history-retention.md`、`docs/adr/0015-standards-as-governance-source.md`

本表是代码与文档关系的唯一事实源。v0.1 运行代码已删除，其范围和 Git 恢复锚点见
`docs/history/baselines/v01-mineru.md`。`__init__.py` 及无业务语义的极短文件豁免。

| 代码路径 | 层级/职责 | 状态 | 设计关联 | 关联测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `alembic.ini` | Alembic CLI 配置 | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 定位迁移环境且不保存数据库凭证。 |
| `backend/infrastructure/config.py` | v0.3 本地配置 | Current | `docs/design/document-pipeline.md` | `tests/test_document_workflow.py` | 仅读取本地 `.env` 与运行目录。 |
| `backend/infrastructure/bailian.py` | 百炼模型适配 | Current | `docs/design/document-pipeline.md` | `tests/test_providers.py` | 隔离 DashScope OpenAI 兼容协议。 |
| `backend/infrastructure/artifacts.py` | 不可变解析产物包 | Current | `docs/design/document-pipeline.md` | `tests/test_parse_artifacts.py` | 共享页图、v1/v2 哈希清单和完整性校验。 |
| `backend/infrastructure/pdf_pipeline.py` | PDF 解析基础设施 pipeline | Current | `docs/design/document-pipeline.md` | `tests/test_v03_jobs.py` | 逐页提交规范化页面事实。 |
| `backend/infrastructure/database.py` | MySQL 连接与迁移辅助 | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 校验 schema 且不隐式迁移。 |
| `backend/application/workbooks.py` | Excel 草稿与显式发布用例 | Current | `docs/design/excel-release-workflow.md` | `tests/test_document_workflow.py` | 导入必须完成模板和证据校验。 |
| `backend/main.py` | 唯一 ASGI 入口 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 仅创建 API 应用。 |
| `backend/domain/models.py` | 状态、任务资源和领域错误 | Current | `docs/architecture/runtime-architecture.md` | `tests/test_v03_jobs.py` | 不依赖框架和供应商 SDK。 |
| `backend/bootstrap.py` | 应用与基础设施装配根 | Current | `docs/architecture/runtime-architecture.md` | `tests/test_architecture_dependencies.py` | API 与 Worker 共用。 |
| `backend/application/ports.py` | 供应商与 pipeline 端口 | Current | `docs/architecture/runtime-architecture.md` | `tests/test_architecture_dependencies.py` | 应用层不依赖适配器。 |
| `backend/infrastructure/mysql.py` | 唯一 MySQL 仓储适配器 | Current | `docs/architecture/runtime-architecture.md` | `tests/test_v03_jobs.py` | 追加式运行、任务租约和审阅历史。 |
| `backend/application/jobs.py` | 任务提交与执行用例 | Current | `docs/design/background-jobs.md` | `tests/test_v03_jobs.py` | HTTP 和 Worker 共用。 |
| `backend/api/schemas.py` | API v1 请求与响应模型 | Current | `docs/architecture/runtime-architecture.md` | `tests/test_v03_api.py` | 统一错误、任务和产物资源。 |
| `backend/api/main.py` | API v1 与静态入口 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 长命令只入队。 |
| `backend/worker/runner.py` | 独立任务 Worker | Current | `docs/design/background-jobs.md` | `tests/test_v03_jobs.py` | 租约、重试和取消。 |
| `backend/worker/__main__.py` | Worker 命令入口 | Current | `docs/design/background-jobs.md` | `tests/test_v03_jobs.py` | 本地独立进程。 |
| `backend/tools/reset_dev_database.py` | 受保护开发库重建 | Current | `docs/adr/0007-versioned-domain-records.md` | `tests/test_reset_safety.py` | 默认只允许测试库。 |
| `backend/tools/prune_parse_runs.py` | 受保护解析运行清理 | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | `tests/test_prune_parse_runs.py` | dry-run、暂存、回滚和显式 purge。 |
| `backend/migrations/env.py` | Alembic 迁移运行环境 | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 只由显式迁移命令调用。 |
| `backend/migrations/script.py.mako` | Alembic revision 生成模板 | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 生成符合中文追溯规范的空迁移骨架。 |
| `backend/migrations/versions/20260727_0001_mysql_v02.py` | MySQL 初始 schema | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 创建基础 `af_` 表。 |
| `backend/migrations/versions/20260727_0002_v03_jobs_and_history.py` | 任务与历史 schema | Current | `docs/architecture/runtime-architecture.md` | `tests/test_v03_jobs.py` | 增加任务和版本表。 |
| `backend/migrations/versions/20260728_0003_parse_artifacts.py` | 解析产物元数据 schema | Current | `docs/adr/0008-immutable-parse-artifact-bundles.md` | `tests/test_mysql_migrations.py` | 增加 MIME、大小和定位元数据。 |
| `backend/migrations/versions/20260728_0004_current_parse_run.py` | 当前运行与清理状态 schema | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | `tests/test_mysql_migrations.py` | 指针、选择历史、墓碑和回填。 |
| `web/index.html` | v0.3 工作台页面结构 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 显示任务进度和审阅视图。 |
| `web/style.css` | v0.3 工作台样式 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 响应式桌面与窄屏布局。 |
| `web/app.js` | v0.3 工作台交互 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 只调用 API v1。 |
| `evaluation/scorecard.py` | 解析实验评分与门禁 | Current | `docs/design/evaluation-governance.md` | `tests/test_evaluation_scorecard.py` | 不访问模型或生产数据。 |
| `evaluation/preflight.py` | 百炼单页连通性预检 | Current | `docs/design/evaluation-governance.md` | `tests/test_evaluation_preflight.py` | 只调用 OCR 模型并沿用页级重试。 |
| `evaluation/scanned_textbook.py` | 扫描教材候选评测执行 | Current | `docs/design/evaluation-governance.md` | `tests/test_scanned_textbook_evaluation.py` | 生成响应和人工评分模板。 |
| `tests/conftest.py` | MySQL 测试库准备与清理 | Current | `docs/architecture/data-lifecycle.md` | — | 仅自动创建隔离测试库。 |
| `tests/test_architecture_dependencies.py` | Backend 依赖方向测试 | Current | `docs/architecture/runtime-architecture.md` | — | 禁止领域和应用层反向依赖。 |
| `tests/test_architecture_documents.py` | 架构文档语义同步测试 | Current | `docs/standards/code-document-traceability.md` | — | 守护 Mermaid 视图、领域状态和已知架构偏差。 |
| `tests/test_code_document_mapping.py` | 映射一致性测试 | Current | `docs/standards/code-document-traceability.md` | — | 守护本表和文件头。 |
| `tests/test_design_documents.py` | 设计契约语义同步测试 | Current | `docs/standards/code-document-traceability.md` | — | 守护流程图、接口字段和关键常量。 |
| `tests/test_markdown_links.py` | Markdown 链接测试 | Current | `docs/standards/documentation.md` | — | 守护仓库本地相对链接。 |
| `tests/test_document_structure.py` | 文档结构测试 | Current | `docs/standards/documentation.md` | — | 守护入口、目录边界、计划归档和 Agent 协议。 |
| `tests/test_adr_structure.py` | ADR 治理测试 | Current | `docs/standards/adr-governance.md` | — | 守护全局编号、登记表、元数据和完整取代关系。 |
| `tests/test_plan_governance.py` | 计划治理测试 | Current | `docs/standards/task-lifecycle.md` | — | 守护活跃计划元数据、状态和索引边界。 |
| `tests/test_tracker_governance.py` | Tracker 治理测试 | Current | `docs/standards/task-lifecycle.md` | — | 守护任务 ID、Plan 镜像、关闭证据和无状态路线图。 |
| `tests/test_standard_governance.py` | Standards 治理测试 | Current | `docs/standards/documentation.md` | — | 守护标准目录边界、统一契约、索引和测试反向关联。 |
| `tests/test_evaluation_scorecard.py` | 评分门禁测试 | Current | `docs/design/evaluation-governance.md` | — | 被测代码为 scorecard。 |
| `tests/test_evaluation_preflight.py` | 百炼预检测试 | Current | `docs/design/evaluation-governance.md` | — | 使用假视觉供应商。 |
| `tests/test_providers.py` | 百炼响应归一化测试 | Current | `docs/design/document-pipeline.md` | — | 覆盖截断、顶层结构、重试和元数据。 |
| `tests/test_parse_artifacts.py` | 解析产物包测试 | Current | `docs/design/document-pipeline.md` | — | 覆盖固定路径、清单和哈希。 |
| `tests/test_current_parse_runs.py` | 当前解析运行测试 | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | — | 覆盖显式选择、历史和非法候选。 |
| `tests/test_prune_parse_runs.py` | 解析运行清理测试 | Current | `docs/adr/0011-current-parse-run-and-prunable-artifacts.md` | — | 覆盖预演、保护、回滚和 purge。 |
| `tests/test_scanned_textbook_evaluation.py` | 扫描教材评测测试 | Current | `docs/design/evaluation-governance.md` | — | 禁止自动填造人工分数。 |
| `tests/test_document_workflow.py` | 主链闭环测试 | Current | `docs/architecture/data-lifecycle.md` | — | 使用确定性假模型。 |
| `tests/test_mysql_migrations.py` | MySQL 迁移与隔离测试 | Current | `docs/architecture/data-lifecycle.md` | — | 验证迁移幂等和表边界。 |
| `tests/test_v03_jobs.py` | 任务与历史测试 | Current | `docs/design/background-jobs.md` | — | 覆盖幂等、租约、取消、续跑和版本。 |
| `tests/test_v03_api.py` | API v1 集成测试 | Current | `docs/design/web-workbench.md` | — | API 进程不执行模型任务。 |
| `tests/test_reset_safety.py` | 数据重建保护测试 | Current | `docs/adr/0007-versioned-domain-records.md` | — | 拒绝系统库和错误确认词。 |
