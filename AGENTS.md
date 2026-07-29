# Axiom-Flow Agent 执行协议

## 项目目标

Axiom-Flow 是 QED 的本地优先技术 PDF 解析与质量审阅组件。当前重点是解析质量、证据追溯和
人工发布，不是已完成的检索或学习助手。

## 快速开始

接到任务后按顺序执行：

1. 检查工作树和当前分支，保留用户已有变更。
2. 从 `docs/index.md` 进入对应文档域，阅读 `docs/trackers/current.md`、关联计划和回归记录。
3. 在 `docs/architecture/code-map.md` 定位受影响模块、DesignRef、实现状态和测试。
4. 阅读对应 architecture、design 和 Accepted ADR；旧协议再查 `docs/history/index.md`。
5. 用 `rg` 搜索实际调用、测试和历史定义，不根据文件名猜行为。

常用定位命令：

```powershell
rg -n "<关键词>" backend evaluation tests docs
rg -n "设计关联（DesignRef）|被测代码" backend evaluation tests
rg -n "<接口或状态名>" docs/adr docs/design docs/history
```

## 任务路由

| 任务 | 首查实现 | 设计/协议 | 定向测试 |
| --- | --- | --- | --- |
| OCR、PDF、页面事实、manifest | `backend/infrastructure/{bailian,pdf_pipeline,artifacts}.py` | `docs/design/document-pipeline.md`、ADR 0008/0010 | `test_providers.py`、`test_parse_artifacts.py`、`test_v03_jobs.py` |
| API、持久任务、Worker | `backend/api/`、`backend/application/jobs.py`、`backend/worker/` | `background-jobs.md`、`web-workbench.md`、ADR 0006 | `test_v03_api.py`、`test_v03_jobs.py` |
| MySQL、迁移、当前 ParseRun | `alembic.ini`、`backend/infrastructure/{database,mysql}.py`、`backend/migrations/` | `data-lifecycle.md`、ADR 0005/0007/0011 | `test_mysql_migrations.py`、`test_current_parse_runs.py` |
| 产物或运行清理 | `backend/tools/prune_parse_runs.py`、`artifacts.py` | ADR 0011、`operations.md` | `test_prune_parse_runs.py`、`test_parse_artifacts.py` |
| 知识、关系、工作簿、发布 | `backend/application/workbooks.py`、`mysql.py` | `knowledge-model.md`、`excel-release-workflow.md` | `test_document_workflow.py` |
| Web 对照与交互 | `web/`、`backend/api/main.py`、`schemas.py` | `web-workbench.md` | `test_v03_api.py`、JavaScript 语法检查 |
| 模型评测与评分 | `evaluation/` | `evaluation-governance.md`、实验 ADR | `test_evaluation_*.py`、`test_scanned_textbook_evaluation.py` |
| 文档、目录与追溯 | `docs/`、模块文件头、`code-map.md` | `docs/standards/` | `test_document_structure.py`、`test_markdown_links.py`、`test_code_document_mapping.py` |

表格只提供入口；准确文件映射始终以 `code-map.md` 为准。

## 问题定位

| 现象 | 首查位置 | 首个验证 |
| --- | --- | --- |
| API 404、422 或错误格式异常 | `backend/api/main.py`、`schemas.py` | `pytest tests/test_v03_api.py -q` |
| 任务排队、租约、取消或重试异常 | `application/jobs.py`、`worker/runner.py`、`mysql.py` | `pytest tests/test_v03_jobs.py -q` |
| OCR 空内容、截断或供应商响应非法 | `infrastructure/bailian.py`、`pdf_pipeline.py` | `pytest tests/test_providers.py -q` |
| manifest、哈希或文件下载失败 | `infrastructure/artifacts.py`、`api/main.py` | `pytest tests/test_parse_artifacts.py -q` |
| 迁移、测试库或表边界异常 | `infrastructure/database.py`、`migrations/`、`tests/conftest.py` | `pytest tests/test_mysql_migrations.py -q` |
| Web 页面或交互异常 | `web/` 与对应 API 路由 | API 测试、`node --check web/app.js` |
| 文档链接、DesignRef 或映射失败 | `docs/architecture/code-map.md`、模块文件头 | 三项文档专项测试 |

## 事实来源与决策

事实冲突时依次采用：运行代码和测试结果、Accepted ADR、架构文档、设计文档、计划、追踪器、
历史资料。历史资料只解释背景，不覆盖当前设计。

- 修改领域边界、公开 API、持久化语义、事实来源或解析路由前，先新增或更新 ADR。
- ADR 使用全局顺序编号；路径随生命周期状态变化，完整登记以 `docs/adr/index.md` 为准。
- 每项实现必须关联计划，包含范围、验证、回滚和完成条件。
- 按 `docs/standards/task-lifecycle.md` 分类 A/B/C/D；不是所有任务都需要实验。
- 外部模型实验前必须冻结假设、样本、内容哈希、预算和采纳门槛；结果经 ADR 接受后才成为设计。
- 数据重建、正式发布和远端推送属于 D 类操作，必须先验证目标、备份/回滚和完整差异。

## 文档与代码追溯

- `docs/architecture/code-map.md` 是代码、设计和测试映射的唯一事实源。
- 非平凡生产模块、评测脚本和测试必须有中文模块说明、`DesignRef` 和实现状态。
- 新增、移动、删除模块或改变职责时，同步修改 code-map、文件头、设计文档和映射测试。
- 活跃架构与设计声明关联代码、测试和 ADR；Legacy 只能关联 `docs/history/`。

## 中文与注释

设计、计划、指南、模块/类/公共函数 docstring 默认使用中文。标识符、类型、API 字段和外部协议
名称保持英文。函数内部注释只说明业务约束、异常原因、不变条件、性能或安全风险。

## 完成检查

结束任务前逐项确认：

1. 实现与当前 Accepted 设计/ADR 一致，未恢复 Superseded 或 Historical 契约。
2. 受影响模块、DesignRef、code-map、设计和测试已经同步。
3. 定向测试、适用回归/端到端、文档映射、链接和 `git diff --check` 通过。
4. 可复现失败已进入 regressions；外部依赖失败有证据、恢复条件和责任位置。
5. D 类操作已经完成备份、回滚和完整差异复核，没有隐式执行。
6. 计划与 tracker 已关闭，Completed/Superseded 计划按规范归档。
