# 计划 2026-08：文档体系范本对齐（V2-015）

> 归档说明：Completed（2026-08-21，V2-015 关闭）。正文为执行事实源，关闭后按文档规范归档
> 至 `docs/history/plans/2026-08/`；文档体系持续维护转长期任务 V2-016。

状态：Completed
任务类型：B
最后更新：2026-08-21
关联 ADR：`docs/adr/0001-v2-exploration-direction.md`（本仓库纪元）、根仓库 [ADR 0010](https://github.com/QED-Engine/qed-engine/blob/main/docs/adr/0010-documentation-versioning.md)（文档体系三层结构与版本治理，范本依据）
关联设计：`docs/design/docs-restructure-alignment.md`（本计划的设计事实源）
关联 Tracker：`docs/trackers/todo.md`（V2-015 Plan 行镜像本计划；完成后文档体系对齐转为长期任务）
归档判定：关闭后按文档规范选择性保留或归档至 `docs/history/` 对应分类

## 目标与成功标准

- 目标：Axiom-Flow 文档体系对齐根仓库 ADR 0010 范本（根仓库 REQ-048 请求，承接 REQ-046 API 接口开发 / REQ-047 数据库设计的子项目侧）：
  1. `architecture/` 固定化：overview.md（服务架构）+ **新增 api.md（8902 API 接口文档）** + **新增 database-design.md（af_* 数据库设计文档）** + code-map.md；
  2. api.md 按 REQ-046 四类组织：① 生命周期与健康；② 数据查询（af_* 数据库知识）；③ 解析结果与 PDF 对照；④ 未来 RAG / 知识图谱预留；
  3. `adr/index.md` 声明当前版本 v0.1（本仓库沿用 v2 纪元，版本号 v0.1）；
  4. `design/` 三态梳理：pdf-parsing-and-rendering / 8902-integration-contract / service-lifecycle / service-lifecycle-encoding-fix 标 Superseded 并移入 `docs/history/design/`；解析管线主体由新 `parsing-pipeline.md` 承接；af-books-sync / model-mode-config 保持原状；
  5. 契约测试同步（architecture / design / code-map / history 目录集合与守护断言）；
  6. 文档整理后续转为**长期任务**：版本末期（用户确认发布版本时）规范化重新整理一轮。
- 成功标准：
  1. 新增/归档文档全部落位，全部链接可解析（`test_markdown_links.py` 全绿）；
  2. 契约测试 `tests/contract/` 全绿（含新增 api.md / database-design.md 守护）；
  3. 全量门禁 `pytest tests -q` + `ruff check src tests` 全绿；
  4. todo：V2-015 关闭迁移 completed.md（证据：提交号 + 门禁输出），长期任务登记承接后续整理。

## 范围与非目标

- 范围：
  - 文档结构调整与新建（parsing-pipeline.md / api.md / database-design.md）；
  - Superseded 设计文档归档（git mv → `docs/history/design/` + 索引）；
  - 引用同步（code-map / AGENTS.md / guides / src 文件头 / todo / 活跃计划链接）；
  - adr/index.md 版本声明、todo 主线归属标注与长期任务登记；
  - 契约测试同步（tests/contract/ 4 个文件）。
- 非目标：
  - 不改动任何功能代码逻辑（src 仅同步文件头 DesignRef 注释）；
  - 不迁移数据、不新建表（af_* 表结构仅文档化，落地属 V2-013）；
  - 不提交 git、不推送（由本仓库执行阶段按任务生命周期自行提交）；
  - 不写根仓库 / QED-Tracker 任何文件（只读参照；回执由根仓库自行读取登记）。

## 前置条件

- 工作树在 `release` 分支，保留用户已有变更（工作树当前干净）；
- 用户已评审设计文档 docs-restructure-alignment.md 并裁决：pdf-parsing-and-rendering 整体 Superseded；Superseded 文档移入 `docs/history/`；
- 用户确认当前版本 v0.1；文档整理完成后转为长期任务（版本末期再规范化整理一轮）；
- 当前 todo.md V2-015 行为 Plan 类型但任务列无计划链接，`test_tracker_governance.py` Plan 镜像断言已红，本计划建立后随 Task 1 修复。

## 工作项

### Task 1：计划建立与 todo 镜像

**文件**：创建 `docs/plans/2026-08-docs-restructure-alignment.md`；修改 `docs/trackers/todo.md`

- [ ] **Step 1**：创建本计划文档（元数据 + 必需章节齐全）。
- [ ] **Step 2**：todo.md V2-015 行任务列改为 `[计划 2026-08：文档体系范本对齐（V2-015）](../plans/2026-08-docs-restructure-alignment.md)`，状态改为 In Progress。
- [ ] **Step 3**：验证 `pytest tests/contract/test_plan_governance.py tests/contract/test_tracker_governance.py -q` 通过。

### Task 2：新建 `docs/design/parsing-pipeline.md`（解析管线承接文档）

**文件**：创建 `docs/design/parsing-pipeline.md`

- [ ] **Step 1**：迁移 pdf-parsing-and-rendering.md 主体章节：背景与决策记录、总体架构（mermaid）、数据模型与产物格式（schemas：blocks/page/book/job）、组件职责（编排层 / MinerU 接入 / 兜底通道 / 明确不做）、错误处理与质量验收、测试策略。
- [ ] **Step 2**：API 契约章节不迁，正文标注「对外 API 契约见 `../architecture/api.md`」；元数据 6 字段齐全（Accepted / Partial / 关联代码 / 关联测试 / 关联 ADR 0001）+ 1 个 mermaid。
- [ ] **Step 3**：验证 `pytest tests/contract/test_design_documents.py -q` 通过（暂未纳入集合，先自检 mermaid/元数据格式）。

### Task 3：新建 `docs/architecture/api.md`（8902 API 接口文档）

**文件**：创建 `docs/architecture/api.md`

- [ ] **Step 1**：编写元数据（Accepted / Current / 关联代码 src/axiom_flow/api/main.py、schemas.py、scripts/axiom_flow_service.py / 关联测试 tests/contract/test_api_v1_contract.py / 关联 ADR 0001）+ 服务信息（8902、/api/v1、启动命令）。
- [ ] **Step 2**：四类组织（REQ-046）：① 生命周期与健康（`GET /api/v1/health` + `axiom_flow_service.py {start|stop|restart|status}` 契约摘要，承接 service-lifecycle.md）；② 数据查询（`GET /books`、`POST /books/sync`、块判定 PUT/GET review，标注 V2-013 规划状态）；③ 解析结果与 PDF 对照（`GET /books/{id}`、`/pages/{no}`、`/pages/{no}/image`、`/manifest`、`POST /parse-jobs`、`GET /parse-jobs/{job_id}`）；④ 未来 RAG·知识图谱预留（Milvus 占位、检索端点预留说明）。
- [ ] **Step 3**：错误语义（400/404/503 + 独立性铁律）、强制规则、验证章节 + 1 个 mermaid。

### Task 4：新建 `docs/architecture/database-design.md`（af_* 数据库设计文档）

**文件**：创建 `docs/architecture/database-design.md`

- [ ] **Step 1**：目的与边界（qed 库 af_* 私有命名空间、互不读取、Alembic 独立建表；根仓库总纲不复制）、表清单总览（af_books / af_block_reviews，V2-013 承接、当前规划）。
- [ ] **Step 2**：af_books / af_block_reviews 完整 DDL 与同步语义（幂等 upsert / qt_books 快照 / 进度自持 / 字段冗余），从 af-books-sync.md §表结构迁移；敏感字段规则；验证章节 + 1 个 mermaid。

### Task 5：Superseded 归档（git mv → `docs/history/design/`）

**文件**：移动 4 篇 + 创建 `docs/history/design/index.md`；修改 `docs/history/index.md`

- [ ] **Step 1**：`git mv docs/design/{pdf-parsing-and-rendering,8902-integration-contract,service-lifecycle,service-lifecycle-encoding-fix}.md docs/history/design/`，每篇头部追加归档说明。
- [ ] **Step 2**：创建 `docs/history/design/index.md` 归档导航；`docs/history/index.md` 表格加 design/ 行。

### Task 6：引用同步（防 test_markdown_links / test_code_document_mapping 红）

**文件**：修改 `docs/architecture/code-map.md`、`AGENTS.md`、`docs/guides/operations.md`、`docs/design/index.md`、`docs/architecture/index.md`、`docs/trackers/todo.md`、`docs/plans/2026-08-model-mode-and-mineru-gateway.md`、src 文件头 7 处、测试文件头 2 处

- [ ] **Step 1**：code-map：api/ → `api.md`；orchestrator/ingest/fallback/schemas → `parsing-pipeline.md`；scripts/axiom_flow_service.py → `api.md`。
- [ ] **Step 2**：AGENTS.md 任务路由 4 行、operations.md:9 链接、design/index.md 表格（去 4 加 1）、architecture/index.md（加 2 行）、todo V2-007 契约草案链接 → `../history/design/8902-integration-contract.md`、活跃计划 3 处链接 → history 路径。
- [ ] **Step 3**：src 文件头 DesignRef（ingest×2、fallback×3、api/main.py、scripts/axiom_flow_service.py）；`tests/contract/test_api_v1_contract.py:3`、`tests/unit/test_service_scripts.py:3` docstring 同步。
- [ ] **Step 4**：验证 `pytest tests/contract/test_markdown_links.py -q` 通过。

### Task 7：adr/index 版本声明 + todo 长期任务登记

**文件**：修改 `docs/adr/index.md`、`docs/trackers/todo.md`

- [ ] **Step 1**：adr/index.md 顶部加「**当前版本：v0.1（跑通完整服务）**」声明（仿根仓库范本，注明版本末期合并进 architecture/、history/ 记录前版本）。
- [ ] **Step 2**：todo.md V2-015 行标注主线归属；登记长期任务（文档体系对齐维护：版本末期规范化重新整理一轮，承接本任务关闭后的持续维护）。

### Task 8：契约测试同步

**文件**：修改 `tests/contract/test_architecture_documents.py`、`test_design_documents.py`、`test_code_document_mapping.py`、`test_document_structure.py`

- [ ] **Step 1**：test_architecture_documents：CURRENT_DOCUMENTS += `{"api.md","database-design.md"}`；code-map 断言改 `docs/design/parsing-pipeline.md`；新增守护（api.md 四类章节与 6 端点、database-design.md 含 af_books/af_block_reviews）。
- [ ] **Step 2**：test_design_documents：CURRENT_DOCUMENTS = `{parsing-pipeline, af-books-sync, model-mode-config, docs-restructure-alignment}`。
- [ ] **Step 3**：test_code_document_mapping：ACTIVE_DOCUMENTS = `[parsing-pipeline.md, overview.md, api.md, database-design.md]`。
- [ ] **Step 4**：test_document_structure：HISTORY_DIRECTORIES += `design`。
- [ ] **Step 5**：验证 `pytest tests/contract -q` 全绿。

### Task 9：门禁、关闭与回执

**文件**：修改 `docs/trackers/todo.md`、`docs/trackers/completed.md`；移动 `docs/design/docs-restructure-alignment.md`

- [ ] **Step 1**：全量门禁 `pytest tests -q` + `ruff check src tests`。
- [ ] **Step 2**：docs-restructure-alignment.md 标 Superseded → git mv 至 `docs/history/design/`。
- [ ] **Step 3**：todo V2-015 原子迁移 completed.md（终态 Completed / 结果 Achieved / 证据：提交号 + 门禁输出）；计划按文档规范选择性保留或归档。
- [ ] **Step 4**：回执登记（更新本仓库 todo/completed 证据；根仓库自行读取登记 REQ-048，本仓库不写根仓库文件）。

## 验证与验收

- 定向：`pytest tests/contract -q`（architecture/design/code-map/markdown-links/tracker/plan/history 全绿）。
- 全量：`pytest tests -q` + `ruff check src tests`。
- 人工：docs/architecture/index.md、design/index.md、history/index.md 导航核对；api.md 四类端点与 main.py 实际路由一致。

## 回滚

- 文档变更均可经 git 恢复（git mv 保留历史，无破坏性操作）；
- 任一契约测试失败即停止，先修测试与文档一致性，不回退功能代码。

## 关闭与归档

- 关闭条件：门禁全绿 + todo/completed 状态一致 + 长期任务已登记；
- 归档：docs-restructure-alignment.md 使命完成标 Superseded 移入 `docs/history/design/`；本计划按文档规范选择性保留（转入长期任务后可作为范本参考）。
