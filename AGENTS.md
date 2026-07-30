# Axiom-Flow Agent 执行协议

## 项目目标

Axiom-Flow 是 QED 的本地优先技术 PDF 解析与质量审阅组件。当前重点是解析质量、证据追溯和
人工发布，不是已完成的检索或学习助手。

## 快速开始

接到任务后按顺序执行：

1. 检查工作树和当前分支，保留用户已有变更。
2. 从 `docs/index.md` 进入对应文档域，阅读 `docs/trackers/todo.md`、关联计划和关闭证据。
3. 从 `docs/standards/index.md` 选择本任务适用的强制规则；开发命令查
   `docs/guides/development.md`，运行与数据操作查 `docs/guides/operations.md`。
4. 在 `docs/architecture/code-map.md` 定位受影响模块、DesignRef、实现状态和测试。
5. 阅读对应 architecture、design 和 Accepted ADR；架构图以活跃文档内嵌 Mermaid 为准，旧协议
   再查 `docs/history/index.md`。
6. 用 `rg` 搜索实际调用、测试和历史定义，不根据文件名猜行为。

常用定位命令：

```powershell
rg -n "<关键词>" src evaluation tests docs
rg -n "设计关联（DesignRef）|被测代码" src evaluation tests
rg -n "<接口或状态名>" docs/adr docs/design docs/history
```

## 任务路由

| 任务 | 首查实现 | 设计/协议 | 定向测试 |
| --- | --- | --- | --- |
| OCR、PDF、页面事实、manifest | `src/axiom_flow/infrastructure/{bailian,pdf_pipeline,artifacts}.py` | `docs/design/document-pipeline.md`、ADR 0008/0010 | `test_providers.py`、`test_parse_artifacts.py`、`test_v03_jobs.py` |
| API、持久任务、Worker | `src/axiom_flow/api/`、`src/axiom_flow/application/jobs.py`、`src/axiom_flow/worker/` | `background-jobs.md`、`web-workbench.md`、ADR 0006 | `test_v03_api.py`、`test_v03_jobs.py` |
| MySQL、迁移、当前 ParseRun | `alembic.ini`、`src/axiom_flow/infrastructure/{database,mysql}.py`、`src/axiom_flow/migrations/` | `data-lifecycle.md`、ADR 0005/0007/0011 | `test_mysql_migrations.py`、`test_current_parse_runs.py` |
| 产物或运行清理 | `src/axiom_flow/tools/prune_parse_runs.py`、`artifacts.py` | ADR 0011、`operations.md` | `test_prune_parse_runs.py`、`test_parse_artifacts.py` |
| 知识、关系、工作簿、发布 | `src/axiom_flow/application/workbooks.py`、`mysql.py` | `excel-release-workflow.md` | `test_document_workflow.py` |
| Web 对照与交互 | `web/`、`src/axiom_flow/api/main.py`、`schemas.py` | `web-workbench.md` | `test_v03_api.py`、JavaScript 语法检查 |
| 模型评测与评分 | `evaluation/{regression,replay,benchmark,scorecard,preflight}.py` | `evaluation-governance.md`、实验 ADR | `test_evaluation_*.py` |
| 计划、ADR 与文档目录 | `docs/`、`AGENTS.md` | `task-lifecycle.md`、`documentation.md`、`adr-governance.md` | `test_plan_governance.py`、`test_standard_governance.py`、`test_adr_structure.py` |
| DesignRef 与语义同步 | 模块文件头、`code-map.md`、架构/设计 | `code-document-traceability.md` | 映射、架构和设计语义测试 |

表格只提供入口；准确文件映射始终以 `code-map.md` 为准。

## 问题定位

| 现象 | 首查位置 | 首个验证 |
| --- | --- | --- |
| API 404、422 或错误格式异常 | `src/axiom_flow/api/main.py`、`schemas.py` | `pytest tests/test_v03_api.py -q` |
| 任务排队、租约、取消或重试异常 | `application/jobs.py`、`worker/runner.py`、`mysql.py` | `pytest tests/test_v03_jobs.py -q` |
| OCR 空内容、截断或供应商响应非法 | `infrastructure/bailian.py`、`pdf_pipeline.py` | `pytest tests/test_providers.py -q` |
| manifest、哈希或文件下载失败 | `infrastructure/artifacts.py`、`api/main.py` | `pytest tests/test_parse_artifacts.py -q` |
| 迁移、测试库或表边界异常 | `infrastructure/database.py`、`migrations/`、`tests/conftest.py` | `pytest tests/test_mysql_migrations.py -q` |
| Web 页面或交互异常 | `web/` 与对应 API 路由 | API 测试、`node --check web/app.js` |
| 文档链接、DesignRef 或映射失败 | `docs/architecture/code-map.md`、模块文件头 | 三项文档专项测试 |

## 事实来源与决策

事实冲突时依次采用：运行代码和测试结果、Accepted ADR、架构文档、设计文档、计划、追踪器、
历史资料。历史资料只解释背景，不覆盖当前设计。

- `docs/standards/` 是工程治理规则的唯一事实源，具体采用
  [任务生命周期](docs/standards/task-lifecycle.md)、[文档规范](docs/standards/documentation.md)、
  [ADR 治理](docs/standards/adr-governance.md)和
  [代码与文档追溯](docs/standards/code-document-traceability.md)。
- 任务先按任务生命周期分类并建立适用计划；需要长期决策时按 ADR 治理新增决定；关闭时按文档
  规范选择归档或删除。普通源码推送属于原任务交付，正式发布、受保护环境和数据操作使用 D 类计划。
- 外部模型实验前必须冻结假设、样本、内容哈希、预算和采纳门槛；结果经 ADR 接受后才成为设计。

## 文档与代码追溯

具体格式、豁免和同步触发项以[代码与文档追溯规范](docs/standards/code-document-traceability.md)
为准。执行时先查 `docs/architecture/code-map.md`；职责或契约变化必须同步文件头、关联架构/设计和
测试。架构变更运行 `tests/test_architecture_documents.py`，设计变更运行
`tests/test_design_documents.py`，全部受管模块运行 `tests/test_code_document_mapping.py`。

## 中文与注释

设计、计划、指南、模块/类/公共函数 docstring 默认使用中文。标识符、类型、API 字段和外部协议
名称保持英文。函数内部注释只说明业务约束、异常原因、不变条件、性能或安全风险。

## 完成检查

结束任务前逐项确认：

1. 实现与当前 Accepted 设计/ADR 一致，未恢复 Superseded 或 Historical 契约。
2. 受影响模块、DesignRef、code-map、设计和测试已经同步。
3. 架构与设计触发项已同步正文与 Mermaid，语义、映射、链接和适用回归/端到端测试通过。
4. 可复现失败已进入 todo；外部依赖失败有证据、恢复条件和责任位置。
5. D 类操作已经完成备份、回滚和完整差异复核，没有隐式执行。
6. 计划正文与 todo 状态一致，关闭任务已原子迁移到 completed，计划已按文档规范选择性保留或删除。
