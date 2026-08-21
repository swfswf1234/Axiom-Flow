# Axiom-Flow Agent 执行协议

## 项目目标

Axiom-Flow 是 QED-Engine 的后端解析组件（v2 探索纪元，ADR 0001）：把 PDF 教材完整解析为统一
格式（Markdown/LaTeX/结构化块），支撑 QED-Engine 前端高还原对照展示。前端由 QED-Engine 负责，
本仓库只保证 API 契约与产物格式稳定。当前重点是解析与渲染数据供给，检索/知识图谱为后续探索项。

## 快速开始

接到任务后按顺序执行：

1. 检查工作树和当前分支，保留用户已有变更。
2. 先读 `docs/architecture/overview.md` 了解拓扑，再从 `docs/index.md` 进入对应文档域，阅读
   `docs/trackers/todo.md`（v2 任务清单）和关联计划。
3. 从 `docs/standards/index.md` 选择本任务适用的强制规则；开发命令查
   `docs/guides/development.md`，运行与数据操作查 `docs/guides/operations.md`。
4. 在 `docs/architecture/code-map.md` 定位受影响模块、DesignRef、实现状态和测试。
5. 阅读对应 architecture、design 和 Accepted ADR；v1 时代资料只查
   `docs/history/`（解释背景，不覆盖当前设计）。

常用定位命令：

```powershell
rg -n "<关键词>" src tests docs
rg -n "<接口或状态名>" docs/adr docs/design docs/history
```

## 分支协作

- `main` 保存已经确认的稳定基线，`release` 承接后续开发；开始实现前先确认当前位于 `release`。
- 普通开发提交和推送进入 `release`。只有用户明确要求并完成适用门禁后，才把 `release` 合并回
  `main`；不得隐式创建标签或 GitHub Release。
- 本仓库无远端 CI，本地门禁是唯一门禁（`pytest tests -q` + `ruff check src tests`）；直接推送
  `release` 或合并回 `main` 前必须完成本地门禁。

## 任务路由

| 任务 | 首查实现 | 设计/协议 | 定向测试 |
| --- | --- | --- | --- |
| WSL 容器、启停、推理服务 | `scripts/`（compose.yaml、infra-*.ps1） | `guides/operations.md`、ADR 0001 | 冒烟链路 |
| 解析编排、质量校验、兜底 | `src/axiom_flow/orchestrator/`、`fallback/` | `docs/design/parsing-pipeline.md` | `tests/unit/`、`tests/integration/` |
| PDF 导入与页图 | `src/axiom_flow/ingest/` | `docs/design/parsing-pipeline.md` | `tests/unit/` |
| 统一格式与 schemas | `src/axiom_flow/schemas.py` | `docs/design/parsing-pipeline.md` | 契约测试 |
| API 端点 | `src/axiom_flow/api/` | `docs/architecture/api.md` | 契约测试 |
| 文档体系、计划、ADR | `docs/` | `docs/standards/documentation.md`、`adr-governance.md` | `tests/contract/` |
| 测试分层与门禁 | `tests/`、`pyproject.toml` | `docs/standards/testing.md` | `tests/contract/test_test_suite_governance.py` |

表格只提供入口；准确文件映射始终以 `docs/architecture/code-map.md` 为准。

## 问题定位

| 现象 | 首查位置 | 首个验证 |
| --- | --- | --- |
| API 503、容器未起 | `scripts/infra-status.ps1`、`guides/operations.md` | 运行 `infra-up.ps1` 后重试 |
| 解析失败/空输出/超时 | `orchestrator/`、MinerU 任务日志 | 冒烟链路测试 |
| 质量不达标未触发兜底 | `orchestrator/` 质量信号、`fallback/` | 低质量页构造验证 |
| 产物缺失或哈希不符 | `orchestrator/` 落盘逻辑、`manifest.json` | 产物完整性测试 |
| WSL 不可达 | `wsl -l -v`、`infra-status.ps1` | 重启 WSL 后重新 up |
| 文档链接、DesignRef 或映射失败 | `docs/architecture/code-map.md`、模块文件头 | 契约测试 |

## 事实来源与决策

事实冲突时依次采用：运行代码和测试结果、Accepted ADR、架构文档、设计文档、计划、追踪器、
历史资料。历史资料只解释背景，不覆盖当前设计。

- `docs/standards/` 是工程治理规则的唯一事实源，具体采用[任务生命周期](docs/standards/task-lifecycle.md)、
  [文档规范](docs/standards/documentation.md)、[ADR 治理](docs/standards/adr-governance.md)、
  [测试架构与门禁](docs/standards/testing.md)和[跨项目协作流程](docs/standards/cross-project-collaboration.md)。
- 任务先按任务生命周期分类并建立适用计划；需要长期决策时按 ADR 治理新增决定；关闭时按文档
  规范选择归档或删除。日常小规模数据与发布操作不建立独立计划，由 git 提交与标签留痕；正式
  发布、受保护环境和大规模数据操作仍要求 D 类计划。
- 外部模型/工具实验前必须冻结假设、样本、内容哈希、预算和采纳门槛；结果经 ADR 接受后才成为
  设计。

## 文档与代码追溯

具体格式、豁免和同步触发项以[文档规范](docs/standards/documentation.md)为准。执行时先查
`docs/architecture/code-map.md`；职责或契约变化必须同步文件头、关联架构/设计和测试。架构变更
运行 `tests/contract/test_architecture_documents.py`，设计变更运行
`tests/contract/test_design_documents.py`，code-map 变化运行
`tests/contract/test_code_document_mapping.py`。

## 中文与注释

设计、计划、指南、模块/类/公共函数 docstring 默认使用中文。标识符、类型、API 字段和外部协议
名称保持英文。函数内部注释只说明业务约束、异常原因、不变条件、性能或安全风险。

## 工作区边界与跨项目协作

本仓库（Axiom-Flow）是独立 git 仓库，也是 agent 的**唯一合法工作区**；QED-Engine 根仓库与
QED-Tracker 一律**只读**（理解契约与联调上下文），禁止任何文件或 git 写入。用户口头指令
（如「改造 QED-Engine 某服务」）不豁免此边界；对根仓库的代码改动必须由根仓库自身执行。
跨项目请求的接收（评审 → 执行 → 回执）与发起流程见
[跨项目协作流程](docs/standards/cross-project-collaboration.md)。误写入根仓库时立即登记
todo 并请求用户协助回滚。

## 完成检查

结束任务前逐项确认：

1. 实现与当前 Accepted 设计/ADR 一致，未恢复 Superseded 或 Historical 契约。
2. 受影响模块、DesignRef、code-map、设计和测试已经同步。
3. 架构与设计触发项已同步正文与 Mermaid，语义、映射、链接和适用回归/冒烟测试通过。
4. 可复现失败已进入 todo；外部依赖失败有证据、恢复条件和责任位置。
5. D 类操作已经完成备份、回滚和完整差异复核，没有隐式执行。
6. 计划正文与 todo 状态一致，关闭任务已原子迁移到 completed，计划已按文档规范选择性保留或删除。
7. 未越界：对 QED-Engine 根仓库 / QED-Tracker 只读，无任何写入；误写入已登记并请求回滚。
