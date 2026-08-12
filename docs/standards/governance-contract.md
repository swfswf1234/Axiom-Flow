# 工程治理契约规范

状态：Current
最后更新：2026-08-10
治理对象：治理契约的定义、守护面清单、契约头、编写约定与新增流程
依据 ADR：`docs/adr/0015-standards-as-governance-source.md`
关联测试：`tests/contract/test_test_suite_governance.py`

## 目的与边界

治理契约测试（`tests/contract/`）是治理规则的自动化守护：把标准正文中的强制规则翻译为可执行
断言，防止规则漂移。本规范定义治理契约是什么、守护哪些治理面、如何编写、如何新增，以及如何
作为 QED-Engine 三项目范本在本仓库落地（承接根仓库 REQ-022 / 本仓库 ALN-008）。

边界：本规范不重复各标准正文的规则内容（一个事实只设一个维护位置）；测试工程的分层、隔离与
门禁由[测试架构与门禁](testing.md)规定；标准/ADR/计划等文档的元数据规则在其对应标准正文。

## 强制规则

### 守护面清单

| 治理面 | 守护内容 | 契约测试 |
| --- | --- | --- |
| 文档结构与导航 | 文档目录边界、index 入口、命名稳定、AGENTS 协议单一、Markdown 链接有效 | `test_document_structure.py`、`test_markdown_links.py` |
| 标准治理 | standards/ 目录边界、标准统一元数据与章节、索引镜像、AGENTS 路由 | `test_standard_governance.py` |
| ADR 治理 | ADR 编号单调、元数据完整、索引登记、取代关系双向、章节顺序 | `test_adr_governance.py` |
| 计划与任务治理 | 计划命名/元数据/生命周期、任务 ID 稳定唯一、todo 镜像活跃计划、roadmap 无状态 | `test_plan_governance.py`、`test_tracker_governance.py` |
| 架构与设计追溯 | 架构/设计元数据、Mermaid 视图、代码-文档-测试双向映射（DesignRef）、分层依赖 | `test_architecture_documents.py`、`test_design_documents.py`、`test_code_document_mapping.py`、`test_architecture_dependencies.py` |
| 测试工程与门禁 | 测试目录边界与分层、pytest 配置、契约测试自身规范 | `test_test_suite_governance.py` |
| 跨项目协作 | 根仓库请求接收、ALN 承接登记、回执证据与设计文档模板字段 | `test_cross_project_collaboration.py` |

新增治理面或既有治理面新增守护对象时，更新本清单与对应契约测试。

### 契约头

每个契约测试文件以 docstring 声明完整契约头（六字段）：

```
"""
模块职责：<验证什么保持一致>
设计关联（DesignRef）：docs/standards/<对应标准>.md
实现状态：Current
被测代码：<守护对象路径，多个用顿号分隔>
守护面：<守护面清单中的治理面>
失效后果：<测试失败说明哪条治理规则被违反>
"""
```

- DesignRef 指向守护的核心标准；同一文件被多个标准关联时，可声明多行 DesignRef（第一行为
  核心标准）。
- 「实现状态」必须为 Current；历史守护场景进入 `history/` 后不再守护。

### 编写约定（自包含与零依赖）

- 只使用 Python 标准库（`pathlib`、`re`、`datetime`、`collections`），不引入额外依赖。
- 文件自包含：不依赖 `tests/contract/` 内共享 helper 或 support 目录；辅助函数（如 `_field`、
  `_records`）允许在各文件内重复编写。
- 仓库相对路径一律通过 `ROOT = Path(__file__).resolve().parents[2]` 推导，不依赖工作目录。
- 只解析仓库内文档与源码，不发网络请求、不加载生产应用（与 testing.md 隔离规则一致）。
- 断言失败消息指明违规对象与路径，便于定位。

## 执行与门禁

### 新增/修改治理契约的流程

1. 治理规则变化：按[ADR 治理规范](adr-governance.md)判断是否需新增 ADR，修改对应标准正文。
2. 编写契约测试（契约头 + 断言新规则），先于正文生效（先红后绿）。
3. 运行 `pytest tests/contract/` 全量，确认新旧守护全绿。
4. 同步登记：标准索引（index.md）的「自动门禁」列、AGENTS.md 文档入口（如需）、相关标准的
   「关联测试」字段。
5. 涉及新治理面时更新守护面清单。

### 门禁

- 文档治理类变更提交前必须运行 `tests/contract/` 全量（见 testing.md）。
- 契约测试属于 `tests/` 门禁范围：`pytest tests -q` 与 `ruff check src tests` 必须全绿。

## 三项目复用（范本）

本规范按根仓库范本（QED-Engine `docs/standards/governance-contract.md`）适配：

- 调整 `ROOT` 推导深度与目录名；
- DesignRef 指向本仓库自己的标准；
- ADR 编号按本仓库独立编号；
- 守护面清单按本仓库实际文档目录与契约测试清单裁剪。

根仓库范本变更以跨项目请求（根仓库 REQ-xxx / 本仓库 ALN-xxx）同步推进，本仓库实现并遵守
其门禁（见[跨项目协作流程](cross-project-collaboration.md)）。

## 变更与取代

改变守护面清单结构、契约头字段、编写约定禁令或新增流程的实质规则时必须先新增 ADR；措辞勘误、
链接修复可直接修改。