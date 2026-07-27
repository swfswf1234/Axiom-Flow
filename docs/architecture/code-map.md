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
| `backend/app/pipeline.py` | PDF 解析与知识候选服务 | Current | `docs/design/document-pipeline.md` | `tests/test_v02_pipeline.py` | 只消费规范化页面事实。 |
| `backend/app/workbook.py` | Excel 草稿与显式发布服务 | Current | `docs/design/excel-release-workflow.md` | `tests/test_v02_pipeline.py` | 导入必须完成模板和证据校验。 |
| `backend/app/main.py` | v0.2 HTTP API 与静态入口 | Current | `docs/design/web-workbench.md` | `tests/test_v02_pipeline.py` | Web 层不承载领域规则。 |
| `web/index.html` | v0.2 工作台页面结构 | Current | `docs/design/web-workbench.md` | `tests/test_v02_pipeline.py` | 原生静态前端。 |
| `web/style.css` | v0.2 工作台样式 | Current | `docs/design/web-workbench.md` | `tests/test_v02_pipeline.py` | 响应式桌面与窄屏布局。 |
| `web/app.js` | v0.2 工作台交互 | Current | `docs/design/web-workbench.md` | `tests/test_v02_pipeline.py` | 只调用后端 API。 |
| `evaluation/scorecard.py` | 解析实验评分与门禁 | Current | `docs/design/evaluation-governance.md` | `tests/test_evaluation_scorecard.py` | 不访问模型或生产数据。 |
| `evaluation/preflight.py` | 百炼单页连通性预检 | Current | `docs/design/evaluation-governance.md` | `tests/test_evaluation_preflight.py` | 最多调用主模型与一次回退模型。 |
| `tests/test_evaluation_scorecard.py` | 评分门禁测试 | Current | `docs/design/evaluation-governance.md` | — | 被测代码为 scorecard。 |
| `tests/test_evaluation_preflight.py` | 百炼预检测试 | Current | `docs/design/evaluation-governance.md` | — | 使用假视觉供应商，不访问外部服务。 |
| `tests/test_providers.py` | 百炼响应归一化测试 | Current | `docs/design/document-pipeline.md` | — | 覆盖尾随说明与无效 JSON。 |
| `tests/test_code_document_mapping.py` | 映射一致性测试 | Current | `docs/guides/code-document-traceability.md` | — | 守护本表和文件头。 |
| `tests/conftest.py` | MySQL 测试库准备与清理 | Current | `docs/architecture/data-lifecycle.md` | — | 仅自动创建 axiom_flow_test。 |
| `tests/test_v02_pipeline.py` | v0.2 主链闭环测试 | Current | `docs/architecture/data-lifecycle.md` | — | 使用确定性假模型，不访问百炼。 |
| `tests/test_mysql_migrations.py` | MySQL 迁移与隔离测试 | Current | `docs/architecture/data-lifecycle.md` | — | 验证迁移幂等和 af_ 表边界。 |
