# 代码与设计映射表

状态：Current  
维护位置：`docs/architecture/code-map.md`  
关联代码：受管模块清单  
关联测试：`tests/test_code_document_mapping.py`  
关联 ADR：`docs/adr/0005-mysql-runtime-storage.md`

本表是代码与文档关系的唯一事实源。`Current` 表示符合当前已接受设计；`Legacy` 表示仅供
历史运行或迁移追溯，不能作为目标架构实现。`__init__.py` 及无业务语义的极短文件豁免。

| 代码路径 | 层级/职责 | 状态 | 设计关联 | 关联测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `app/main.py` | v0.1 应用入口 | Legacy | `docs/history/2026-07-mineru-baseline/architecture.md` | — | 不属于当前目标架构。 |
| `app/api/ingest.py` | v0.1 导入接口 | Legacy | `docs/history/2026-07-mineru-baseline/design/api_spec.md` | — | 不属于当前目标架构。 |
| `app/api/layout.py` | v0.1 布局接口 | Legacy | `docs/history/2026-07-mineru-baseline/design/api_spec.md` | — | 不属于当前目标架构。 |
| `app/api/status.py` | v0.1 状态接口 | Legacy | `docs/history/2026-07-mineru-baseline/design/api_spec.md` | — | 不属于当前目标架构。 |
| `app/core/config.py` | v0.1 配置 | Legacy | `docs/history/2026-07-mineru-baseline/environment.md` | — | 不属于当前目标架构。 |
| `app/core/database.py` | v0.1 数据库连接 | Legacy | `docs/history/2026-07-mineru-baseline/design/data_schema.md` | — | 不属于当前目标架构。 |
| `app/models/document.py` | v0.1 文档模型 | Legacy | `docs/history/2026-07-mineru-baseline/design/data_schema.md` | — | 不属于当前目标架构。 |
| `app/models/layout_block.py` | v0.1 布局块模型 | Legacy | `docs/history/2026-07-mineru-baseline/design/data_schema.md` | — | 不属于当前目标架构。 |
| `app/repository/base.py` | v0.1 仓储基类 | Legacy | `docs/history/2026-07-mineru-baseline/architecture.md` | — | 不属于当前目标架构。 |
| `app/repository/document_repo.py` | v0.1 文档仓储 | Legacy | `docs/history/2026-07-mineru-baseline/architecture.md` | — | 不属于当前目标架构。 |
| `app/repository/layout_repo.py` | v0.1 布局仓储 | Legacy | `docs/history/2026-07-mineru-baseline/architecture.md` | — | 不属于当前目标架构。 |
| `app/services/mineru_service.py` | v0.1 MinerU 解析服务 | Legacy | `docs/history/2026-07-mineru-baseline/design/mineru_integration.md` | — | 不属于当前目标架构。 |
| `scripts/init_db.py` | v0.1 初始化脚本 | Legacy | `docs/history/2026-07-mineru-baseline/environment.md` | — | 不属于当前目标架构。 |
| `backend/app/config.py` | v0.2 本地配置 | Current | `docs/design/document-pipeline.md` | `tests/test_v02_pipeline.py` | 仅读取本地 `.env` 与运行目录。 |
| `backend/app/models.py` | v0.2 API 数据模型 | Current | `docs/design/normalized-content.md` | `tests/test_v02_pipeline.py` | API 审阅命令与响应形状。 |
| `backend/app/providers.py` | 百炼模型适配 | Current | `docs/design/document-pipeline.md` | `tests/test_v02_pipeline.py` | 隔离 DashScope OpenAI 兼容协议。 |
| `backend/app/store.py` | MySQL 事实与版本仓储 | Current | `docs/architecture/data-lifecycle.md` | `tests/test_v02_pipeline.py` | 仅访问 af_ 前缀表并校验迁移版本。 |
| `backend/migrations/env.py` | Alembic 迁移运行环境 | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 只由显式迁移命令调用。 |
| `backend/migrations/versions/20260727_0001_mysql_v02.py` | v0.2 MySQL 初始 schema | Current | `docs/architecture/data-lifecycle.md` | `tests/test_mysql_migrations.py` | 创建 af_ 表、外键和索引。 |
| `backend/migrations/versions/20260727_0002_v03_jobs_and_history.py` | v0.3 任务与历史 schema | Current | `docs/architecture/v03-target.md` | `tests/test_v03_jobs.py` | 增量增加任务和版本表。 |
| `backend/app/pipeline.py` | PDF 解析与知识候选服务 | Current | `docs/design/document-pipeline.md` | `tests/test_v02_pipeline.py` | 只消费规范化页面事实。 |
| `backend/app/workbook.py` | Excel 草稿与显式发布服务 | Current | `docs/design/excel-release-workflow.md` | `tests/test_v02_pipeline.py` | 导入必须完成模板和证据校验。 |
| `backend/app/main.py` | 稳定 Uvicorn 转发入口 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 转发到 API v1 组装模块。 |
| `backend/domain/models.py` | 状态、任务资源和领域错误 | Current | `docs/architecture/v03-target.md` | `tests/test_v03_jobs.py` | 不依赖框架和基础设施。 |
| `backend/infrastructure/mysql.py` | v0.3 MySQL 仓储与任务租约 | Current | `docs/architecture/v03-target.md` | `tests/test_v03_jobs.py` | 追加式运行和审阅历史。 |
| `backend/application/jobs.py` | 任务提交与执行用例 | Current | `docs/design/background-jobs.md` | `tests/test_v03_jobs.py` | HTTP 和 Worker 共用。 |
| `backend/api/schemas.py` | API v1 请求与响应模型 | Current | `docs/architecture/v03-target.md` | `tests/test_v03_api.py` | 统一错误和任务资源。 |
| `backend/api/main.py` | API v1 与静态入口 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 长命令只入队。 |
| `backend/worker/runner.py` | 独立任务 Worker | Current | `docs/design/background-jobs.md` | `tests/test_v03_jobs.py` | 租约、重试和取消。 |
| `backend/worker/__main__.py` | Worker 命令入口 | Current | `docs/design/background-jobs.md` | `tests/test_v03_jobs.py` | 本地独立进程。 |
| `backend/tools/reset_dev_database.py` | 受保护开发库重建 | Current | `docs/adr/0007-versioned-domain-records.md` | `tests/test_reset_safety.py` | 默认只允许测试库。 |
| `web/index.html` | v0.3 工作台页面结构 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 显示任务进度和审阅视图。 |
| `web/style.css` | v0.3 工作台样式 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 响应式桌面与窄屏布局。 |
| `web/app.js` | v0.3 工作台交互 | Current | `docs/design/web-workbench.md` | `tests/test_v03_api.py` | 只调用 API v1。 |
| `evaluation/scorecard.py` | 解析实验评分与门禁 | Current | `docs/design/evaluation-governance.md` | `tests/test_evaluation_scorecard.py` | 不访问模型或生产数据。 |
| `evaluation/preflight.py` | 百炼单页连通性预检 | Current | `docs/design/evaluation-governance.md` | `tests/test_evaluation_preflight.py` | 最多调用主模型与一次回退模型。 |
| `tests/test_evaluation_scorecard.py` | 评分门禁测试 | Current | `docs/design/evaluation-governance.md` | — | 被测代码为 scorecard。 |
| `tests/test_evaluation_preflight.py` | 百炼预检测试 | Current | `docs/design/evaluation-governance.md` | — | 使用假视觉供应商，不访问外部服务。 |
| `tests/test_providers.py` | 百炼响应归一化测试 | Current | `docs/design/document-pipeline.md` | — | 覆盖尾随说明与无效 JSON。 |
| `tests/test_code_document_mapping.py` | 映射一致性测试 | Current | `docs/guides/code-document-traceability.md` | — | 守护本表和文件头。 |
| `tests/conftest.py` | MySQL 测试库准备与清理 | Current | `docs/architecture/data-lifecycle.md` | — | 仅自动创建 axiom_flow_test。 |
| `tests/test_v02_pipeline.py` | v0.2 主链闭环测试 | Current | `docs/architecture/data-lifecycle.md` | — | 使用确定性假模型，不访问百炼。 |
| `tests/test_mysql_migrations.py` | MySQL 迁移与隔离测试 | Current | `docs/architecture/data-lifecycle.md` | — | 验证迁移幂等和 af_ 表边界。 |
| `tests/test_v03_jobs.py` | v0.3 任务与历史测试 | Current | `docs/design/background-jobs.md` | — | 覆盖幂等、租约、取消和版本。 |
| `tests/test_v03_api.py` | API v1 集成测试 | Current | `docs/design/web-workbench.md` | — | API 进程不执行模型任务。 |
| `tests/test_reset_safety.py` | 数据重建保护测试 | Current | `docs/adr/0007-versioned-domain-records.md` | — | 拒绝系统库和错误确认词。 |
