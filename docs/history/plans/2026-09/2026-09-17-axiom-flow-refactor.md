# Axiom-Flow 配置简化与解析重构（axiom-flow-refactor）

状态：Completed
任务类型：B
最后更新：2026-09-21
关联 ADR：[ADR 0004](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、解析管线归本仓库；本计划不新增 ADR，只对齐既有决策）
关联设计：[model-integration.md](../../../design/model-integration.md)、[parsing-pipeline.md](../../../design/parsing-pipeline.md)、[parsing-management.md](../../../design/parsing-management.md)
关联 Tracker：docs/trackers/todo.md（PLAN-002、ARCH-001/003/004/005/006/009/016、REQ-001、REQ-075、REQ-080、DEFECT-001/002/003）
归档判定：Retain（配置与解析事实并入 design/architecture 后，计划壳归档 history/plans/2026-09/）

## 背景与用户裁决（2026-09-17）

QED-Engine 根仓库已重建配置中心（`.env` 四段式、模型注册表与 runtime 同化，PLAN-046）：
`QED_MINERU_URL` 删除改为 `QED_OCR_MODEL_URL`，MinerU 端口 8002 → 5002，模型启停/探针/资源
互斥全部归 8900，Axiom-Flow 只需**连接**模型服务。根仓库已登记
[REQ-075](https://github.com/QED-Engine/qed-engine/blob/main/docs/trackers/todo.md)（请求：
Axiom-Flow——MinerU 直连配置同步），要求引擎适配器读新键与新端口、同步文档，**不保留旧键
别名**。

用户裁决（2026-09-17）：

1. **定位简化**：Axiom-Flow 只做「模型 API 调用 + 预处理/后处理」；模型生命周期与**调参**
   归 QED-Engine（8900）。配置只保留必要参数（服务/数据/库）与本地参数（引擎/模型地址）。
2. **三段式 `.env`**：服务（`AXIOM_PORT`）/ 数据与数据库（`QED_DATA_ROOT`、`QED_DB_*`）/
   解析引擎（`AXIOM_OCR_ENGINE`、`QED_OCR_MODEL_URL`、`AXIOM_OCR_TIMEOUT`）；`API_KEY` 与
   `AXIOM_VISION_MODEL` 仅作 qwen-vl 预留（注释态）。删除 `QED_API_SELECT`、
   `QED_LLM_GATEWAY_URL`、`QED_LLM_TIMEOUT`、`QED_PROXY`、`QED_API_PROVIDER`、`QED_MODEL`、
   `QED_EMBEDDING_MODEL`、`AXIOM_FLOW_DATA_DIR`、`AXIOM_PADDLEOCR_URL`。
3. **三个优先目标**：① 配置中心简化；② API 与数据库设计（更新接口 + 解析结果整理，即
   `QED_DATA_ROOT` 与 `qed` 库）；③ 基本解析 + 分块先行。
4. **分块落盘**：book 级 `chunks.json` + `GET /books/{id}/chunks`（参数可覆盖重算）；分块
   不入库、可重建。
5. **建表节奏**：af_* 四表**一次迁移**建全（DDL 已冻结）；编辑端点可后置。
6. **解析粒度**：默认全本（pages 缺省，整本 PDF 一次解析）；指定 pages 时逐页页图重解析。
7. **提交**：文档先行提交；代码更新后经用户确认再提交代码 commit。

## 2026-09-20 复核与收窄

Step 1 与 Step 2-A~E 已交付并进入提交 `a5d2e96`。本轮对「数据库 + 数据根存储」做真机复核，
并对提交 `a5d2e96` 做了独立代码审查，据此收窄计划剩余范围：

| 成功标准 | 2026-09-20 判定 | 依据 |
| --- | --- | --- |
| 1/2/3 配置三段式、`config.py`、服务脚本 | 达成 | Step 1/2-A，`test_config.py` + `test_service_scripts.py` |
| 4 API 契约 + 契约测试 | 达成 | `api.md` 更新 + `test_api_v1_contract.py` 全绿 |
| 5 af_* 四表迁移 + 仓储层 | **真机达成** | 本机 `alembic upgrade head`（用户批准）后 `qed` 库四表齐、`alembic_version = 20260917_0001`、af_books 4 行；ARCH-013/014/015 关闭入 completed.md |
| 6 基本解析闭环 | **未达成** | 真机 ingest 与产物布局通过（486 页图 + book.json/manifest/chunks），但解析任务真机 failed：MinerU 3.4.4 无 `/get_task_results/{id}` 路由（DEFECT-002）；进度与页状态不落库（DEFECT-001） |
| 7 分块 | 部分达成 | 派生文件与端点可用；产物按版本划分（REQ-080）后 chunks.json 归属需同步约定 |
| 8 门禁 + REQ-001/075 回执 | 未达成 | 本地门禁 245 passed + ruff 全绿；回执因标准 6 未达成而延后 |

因此本计划剩余工作收口到 **Step 3 联调与缺陷修复**；标准 6/7/8 的达成即本计划关闭条件，Rudin
端到端验收仍按原裁决后置到 ARCH-009。

## 目标与成功标准

**目标**：① 配置中心按「服务/数据与库/解析引擎」三段式简化并与根仓库对齐；② API 契约与
af_* 四表按新形态落地，解析结果按 `QED_DATA_ROOT/parsed/` 组织；③ 交付基本解析与分块闭环
（预处理 → 引擎调用 → 归一化 → 分块 → 落盘 → 元数据回写）。

**成功标准**：

1. `.env` / `.env.example` 三段式；废弃键清除；服务可独立部署（自身 `.env` 含
   `QED_DB_NAME=qed` 与引擎配置），由 QED-Engine 启动时向上走查跟随根 `.env`。
2. `config.py` 读取 `AXIOM_PORT` / `QED_DATA_ROOT` / `QED_DB_*` / `AXIOM_OCR_ENGINE` /
   `QED_OCR_MODEL_URL` / `AXIOM_OCR_TIMEOUT`；无「模式」概念；旧键忽略；坏值不阻断启动。
3. 服务脚本无 `--mode`、不注入密钥；生命周期命令与健康判据不变。
4. `api.md` 契约更新（sync/ingest/CRUD、异步 parse-jobs、edit/edits、chunks）；契约测试全绿。
5. af_* 四表一次 Alembic 迁移可升级/降级；仓储层 sync 幂等、edit upsert 覆盖。
6. 基本解析闭环：sync → ingest → 解析（默认全本，指定页逐页）→ 归一化 → 落盘 `parsed/` +
   af_books/af_parse_jobs/af_pages 回写；产物含 `book.json` / `manifest.json` / 页三件套。
7. 分块：`chunks.json` + `GET /chunks`；抽样 20 页无公式腰斩、溯源可回跳页/块。
8. 门禁全绿：`pytest tests -q` + `ruff check src tests` + `tests/contract/`；REQ-001/REQ-075
   回执根仓库。

## 范围与非目标

**范围内**：本仓库 `.env(.example)`、`config.py`、`scripts/axiom_flow_service.py`、
`schemas.py`、`ingest/`、新增 `engines/`、`normalize/`（含 `chunking`）、`orchestrator/`、
`db/`（Alembic + 仓储）、`api/`；v1 模块退役；`docs/design/`、`docs/architecture/`、
`docs/guides/`、`docs/standards/local-dev.md` 契约同步；台账与计划。

**非目标**：不修改 QED-Engine 根仓库与 QED-Tracker（只读）；不实现 PaddleOCR-VL / 云端
qwen-vl 引擎（仅预留）；不实现 RAG / Milvus / 知识图谱 / 数学解析器；不做 `axiom` / `xqfm`
遗留库删除与 dataset 物理清理（D 类，另立计划）；不改 af_* 四表 DDL（以现有设计为准）；
不实现模型调参（归 QED-Engine）。

## 前置条件

1. QED-Engine 根仓库配置中心已重建（`QED_OCR_MODEL_URL`、MinerU 5002）。
2. MinerU 容器可由 8900 自动启停，`http://127.0.0.1:5002/health` 可达（本仓库只连接）。
3. `qed` 库可用，本仓库 Alembic 可独立初始化 af_* 表。
4. 工作树 ARCH-020 文档重构改动已提交为基线（2026-09-17 已提交）。

## 工作项

### Step 1 文档与契约（先行提交）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 1.1 | `.env` / `.env.example` 三段式 | 服务 / 数据与库 / 解析引擎；预留键注释态；删废弃键 |
| 1.2 | design 三份刷新 | `model-integration`（配置表/边界/调参归 8900）、`parsing-pipeline`（预处理-调用-后处理-分块、产物布局、全本/逐页）、`parsing-management`（四表一次迁移、chunks 不入库、端点更新） |
| 1.3 | architecture 四份刷新 | `api.md`（chunks/edit/ingest、异步 parse-jobs、`--mode` 退役）、`code-map.md`（模块状态与 chunking）、`overview.md`（分块）、`database-design.md`（入库边界） |
| 1.4 | standards/guides 同步 | `local-dev.md`、`operations.md`、`development.md` 新键与配置语境 |
| 1.5 | 计划与台账 | 本计划重写；`todo.md` 镜像 PLAN-002、更新 REQ-001/REQ-075 |
| 1.6 | 契约门禁 + 文档 commit | `pytest tests/contract -q` 全绿后提交（不含代码） |

### Step 2-A 配置简化（代码）

| # | 工作项 | 产出 |
| --- | --- | --- |
| A.1 | `config.py` 精简 | `Settings`：`port` / `data_dir`（仅 `QED_DATA_ROOT`）/ `db_*` / `ocr_engine` / `ocr_model_url` / `ocr_timeout`；删模式与网关字段；保留向上走查 |
| A.2 | `tests/unit/test_config.py` | 新键默认与覆盖、旧键忽略、坏值容错、密钥隔离 |
| A.3 | 服务脚本 | 删 `--mode` 与密钥注入；`tests/unit/test_service_scripts.py` 同步 |

### Step 2-B DB + 骨架（代码）

| # | 工作项 | 产出 |
| --- | --- | --- |
| B.1 | `db/` + `migrations/` | SQLAlchemy engine/models/repositories；Alembic 四表一次迁移（升级/降级） |
| B.2 | `engines/` 骨架 | `base.py` 适配器契约；`mineru.py` 直连 `QED_OCR_MODEL_URL`（`/health`、`/file_parse`） |
| B.3 | `normalize/` 骨架 | 归一化入口 + `chunking` 模块骨架 |
| B.4 | `orchestrator/` 骨架 | job/页状态机与流水线骨架 |
| B.5 | v1 退役 | 删 `llm_client.py`、`fallback/`；`database.py` 并入 `db/`；清理仅剩 `.pyc` 的 v1 目录 |

### Step 2-C 基本解析（代码）

| # | 工作项 | 产出 |
| --- | --- | --- |
| C.1 | `schemas.py` 升级 | `BookMeta`（authors/source_file/engine/ingested_at）、`BlocksPage`（index/order/image_size）、`Source` 扩展、`EditRecord`、`ParseJob` 对齐 |
| C.2 | ingest 落点迁移 | `QED_DATA_ROOT/parsed/<domain>/<course>/<book_id>/` + 原子落盘 + 幂等覆盖 |
| C.3 | MinerU 归一化 | mock 单测（content_list → 统一 blocks + Markdown + 质量信号） |
| C.4 | 编排闭环 | parse-jobs（默认全本/指定页逐页）→ 落盘 → af_parse_jobs/af_pages/af_books 回写；断点续跑 |
| C.5 | API 端点 | sync/CRUD/ingest/parse-jobs/pages/manifest/image；契约测试 |

### Step 2-D 分块（代码）

| # | 工作项 | 产出 |
| --- | --- | --- |
| D.1 | `normalize/chunking.py` | 书级 blocks → chunks（规则见 parsing-pipeline），落 `chunks.json` |
| D.2 | `GET /books/{id}/chunks` | 读文件；`?max_chars=&overlap_blocks=` 实时重算 |
| D.3 | 测试 | 分块规则单测（公式/表格不拆、溯源完整、边界）+ 契约测试 |

### Step 2-E 对照编辑（代码，可后置）

| # | 工作项 | 产出 |
| --- | --- | --- |
| E.1 | 编辑仓储与端点 | `af_block_edits` upsert / 列表；`pages/{no}` 合并回显 |
| E.2 | 测试 | edit upsert 覆盖 + 契约测试 |

### Step 3 联调与缺陷修复（2026-09-20 收窄新增）

> **2026-09-20 承载变更（用户裁决）**：本表的实施与验收由
> [PLAN-003 解析真机打通](2026-09-20-parse-live-bringup.md) 承接（W0 冻结探测 → W1~W6 修复 →
> W8 冒烟与回执）。**本表仅作历史索引**，不再作为工作项与验收入口；3.5（版本划分产物）进一步
> 拆入 PLAN-004。

按 DEFECT 序号串行执行，每项先补会失败的测试再改实现（TDD）。

| # | 工作项 | 产出 | 关联 |
| --- | --- | --- | --- |
| 3.1 | MinerU 新异步 API 对齐 | 提交 `/file_parse` → `/tasks/{task_id}` 状态轮询 → `/tasks/{task_id}/result` 取结果；单次瞬时 HTTP 错误不中断整本；非 JSON 响应给出可诊断错误 | DEFECT-002、REQ-075 |
| 3.2 | 进度与页状态落库 | 每页成功/失败后提交（页级持久化）+ 周期 `update_progress`；`project-status.md` 断点续跑措辞与事实对齐 | DEFECT-001 |
| 3.3 | MySQL 隔离集成测试 | `tests/integration/` 双 Session 用例：写会话提交前读会话不可见已解析页，逐页提交后可见 | DEFECT-001 |
| 3.4 | 引擎分派与入参校验 | `AXIOM_OCR_ENGINE` 显式分派（未配置槽位报错，不自动兜底）；parse-jobs 提交前探活 503；sync 入参拒绝越出数据根；重复 ingest 回置 af_pages 与 parse_status | DEFECT-003 |
| 3.5 | 产物按解析版本划分 | `parsed/<domain>/<course>/<book_id>/versions/<job_id>/` 原子落盘 + 根目录当前生效版本视图 + af_parse_jobs 生效版本指向（增量迁移） | REQ-080 |
| 3.6 | 真机冒烟与回执 | MinerU 5002 在线下单页与整本冒烟，产物 `source=mineru`、chunks 生成；REQ-075 与 REQ-001 复核后回执根仓库 | REQ-075、REQ-001 |

## 进展（滚动记录）

| 阶段 | 状态 | 证据 |
| --- | --- | --- |
| Step 1 文档与契约 | 完成 | 文档 commit `b1bd9b1`（16 files）；`tests/contract` 59 passed |
| Step 2-A 配置简化 | 完成 | `config.py` 三段式、服务脚本去 `--mode`；`test_config.py` / `test_service_scripts.py` |
| Step 2-B DB + 骨架 | 完成 | `db/`（engine/models/repositories）、`migrations/` 四表一次迁移；`test_db.py` / `test_db_migrations.py` |
| Step 2-C 基本解析 | 完成 | `schemas.py` 升级、ingest `parsed/` 布局、`engines/mineru.py`、`normalize/`、`orchestrator/`、API 全端点；`test_engines.py` / `test_normalize.py` / `test_ingest.py` / `test_orchestrator.py` / `test_api_v1_contract.py` |
| Step 2-D 分块 | 完成 | `normalize/chunking.py` + `chunks.json` + `GET /chunks`；`test_chunking.py` |
| Step 2-E 对照编辑 | 完成 | `af_block_edits` 仓储 + `PUT .../edit` / `GET .../edits`（含在契约测试） |
| 2026-09-20 真机复核 | 完成 | `qed` 库四表 + `alembic_version = 20260917_0001`（用户批准执行迁移）；`QED_DATA_ROOT=D:\coding\QED-Engine\dataset`，`parsed/linear_algebra/linearalgebra-b01/` 486 页图 + book.json/manifest/chunks；ARCH-013/014/015 关闭 |
| 2026-09-20 代码审查 | 完成 | 提交 `a5d2e96` 审查结论：1 项关键（进度不落库）+ 6 项重要，已登记为 DEFECT-001/002/003 |
| Step 3 联调与缺陷修复 | 移交 PLAN-003 | 3.1~3.4/3.6 由 [PLAN-003](2026-09-20-parse-live-bringup.md) W0~W8 承接（含其 W0 冻结探测与 ADR 0005 前置）；3.5 版本划分拆 PLAN-004；Rudin 验收（ARCH-009）另线后置 |

门禁：`pytest tests -q` 245 passed；`ruff check src tests scripts` 全绿（2026-09-20 复跑，
解释器 `D:\software\anaconda3\envs\QED_env\python.exe`）。

## 验证与验收

- **定向测试**：`tests/unit/test_config.py`、`test_schemas.py`、`test_ingest.py`、引擎归一化
  与分块单测、仓储单测、`tests/contract/test_api_v1_contract.py`。
  **2026-09-20 事实校正**：`tests/integration/` 当前无测试文件，DB 侧全部为内存/临时 SQLite
  隔离，MySQL 真机验证仅由手工迁移 + 根仓库 E2E 留痕；补齐双 Session 集成测试列入 Step 3.3。
- **回归门禁**：`pytest tests -q` + `ruff check src tests` + `tests/contract/` 全绿。
- **文档门禁**：架构变更跑 `test_architecture_documents.py`，设计变更跑
  `test_design_documents.py`，code-map 变更跑 `test_code_document_mapping.py`，计划/台账变更
  跑 `test_plan_governance.py`、`test_tracker_governance.py`。
- **冒烟（需 MinerU 5002 在线）**：真实解析单页/整本，产物 `source=mineru`，chunks 生成；
  质量验收（公式可渲染 ≥ 90%）由用户确认。
- **跨项目回执**：REQ-075（MinerU 直连配置同步）与 REQ-001（8900 适配 `/edit`/ingest/chunks）
  完成后回执根仓库。

## 回滚

- 文档与配置：纯文档/代码变更，`git revert` 即可；无数据库迁移、无数据操作。
- 新键全部有缺省值；回滚后旧键语义不恢复（根仓库已删除旧键，属声明风险）。
- af_* 表 Alembic 可降级；`parsed/` 产物与 `chunks.json` 为可再生数据，可清理。

## 关闭与归档

### 关闭判定（2026-09-21，逐条）

**与计划口径的偏差（已改判）**：下文旧口径把标准 6/7 绑到「PLAN-003 关闭即判达成」。PLAN-003 因
D3 真机删除演练（需用户批准）与 DEFECT-006/007 语义裁决而长期开放，继续绑定只会让本计划无期限
挂起；而标准 6/7 的**判据本身**已在 2026-09-21 的 R1 真机窗口逐条达成。故本轮改为**按判据自身证据
判定**，PLAN-003 的剩余依赖与本计划无关。

| 标准 | 2026-09-21 判定 | 依据（可复跑） |
| --- | --- | --- |
| 1~3 配置三段式 / `config.py` / 服务脚本 | 达成（2026-09-20 已判） | `test_config.py`（含 DEFECT-008 行内注释 4 例）+ `test_service_scripts.py`；G5 三段走查证明生效层恒为本仓库 `.env` |
| 4 API 契约 + 契约测试 | 达成（2026-09-20 已判），此后随版本布局扩到 15 条路由 | `architecture/api.md` + `tests/contract/test_api_v1_contract.py` |
| 5 af_* 四表一次迁移 + 仓储幂等/upsert | 达成（2026-09-20 真机） | `qed` 库迁移至 `20260920_0002`；ARCH-013/014/015/003 侧证据入 completed.md |
| 6 基本解析闭环（sync→ingest→解析→归一化→落盘+回写） | **达成（改判）**：原「未达成」的两项成因均已消除 | DEFECT-002（3.4.4 三段端点）与 DEFECT-001（页级提交边界）关闭；R1 窗口 job `86ab56ae2b6c` 20/20 页 52 秒完成并**首次激活** `active_job_id`，产物落 `versions/<job_id>/`（`tmp/probes/live-window-20260921/S2_after.json`）；`tests/smoke/test_live_mineru_chain.py`（`QED_SMOKE_LIVE=1`）常驻复跑 |
| 7 分块：`chunks.json` + `GET /chunks` + 抽样 20 页无公式腰斩、溯源可回跳 | **达成（本轮就地补做真机取证）** | 见下「标准 7 真机取证」 |
| 8 门禁全绿 + REQ-001/REQ-075 回执 | **达成（本方侧发出）** | 门禁 `376 passed, 10 skipped` + `ruff check src tests scripts` 全绿 + 契约 96；REQ-075 已关闭（completed 行）；REQ-001 复核材料与延后项② 二次回执随 `95e1d37` 发出——根侧登记在其 `REQ-001` todo 行独立跟踪，不再作为本计划门槛 |

**标准 7 真机取证（2026-09-21 就地补做，只读）**：脚本 `tmp/live_check_chunk_formula_integrity.py`
对**生效版本** `86ab56ae2b6c`（20 页 / 原始块 159 / chunk 17）跑四项判据，结果 **PASS**，留档
`tmp/probes/chunk-integrity-20260921/C1_chunks.json`：

1. 公式整块不腰斩：18 个 `formula` 块按 `block_text` 规则（display → `$$…$$`，行内 → `$…$`）渲染后，
   **全部**作为完整子串出现在某个 chunk 文本中（缺失 0）；
2. 分隔符平衡：逐 chunk 统计 `$$` 对与残余单 `$`，奇数者 0（腰斩必然留下奇数定界符）；
3. 溯源可回跳：`block_refs` 的 `(page, index)` 全部命中该版本 `pages/pNNNN.blocks.json` 的合法下标
   （悬空 0）；
4. 非噪声块全覆盖：排除 `header/footer/page_number/image` 后应入块的块 100% 被引用（未覆盖 0）。

**反向判据**（证明上述 PASS 不是空判据，在临时副本上人为破坏）：腰斩一个公式 →
`formula_blocks_missing_from_chunks=2` 且 `chunks_with_unbalanced_dollar_delimiters=1`；插入悬空
`block_refs` → `dangling_block_refs` 命中；整删一个 chunk → `uncovered_non_noise_blocks=3`。四种破坏
全部变红。默认门禁侧由 `tests/unit/test_chunking.py::test_formula_and_table_never_split` 常驻守护；
真机取证脚本读真实数据根，按测试隔离铁律**不进默认门禁**。

**顺带取证（移交 ARCH-009 复用）**：该版本块类型分布 = `paragraph` 121 / `page_number` 14 /
`formula` 18 / `footer` 4 / `caption` 2，与 `dropped_block_count = 0`（R1 窗口）互证映射表无缺口。

**移交项（唯一）**：上述四项判据在**代表书**（Rudin 中译 + 吉米多维奇）上的复跑属质量验收，
归 ARCH-009 关闭条件（其「类型分布取证登记」与本脚本同源，脚本可直接复用）。

**关闭结果 Achieved**，归档判定 **Retain**（本计划承载 2026-09-17 重构基线裁决与 af_* 真机迁移里程碑）。

### 旧口径（保留原貌，不覆盖事实）

- 关闭条件（2026-09-20 收口）：Step 3 工作项由 PLAN-003 承接后，本计划判定口径改为——
  **标准 1~5 已达成；标准 6（基本解析闭环）与标准 7（分块）随 PLAN-003 关闭即判达成；标准 8 的
  门禁部分已达成、REQ-001/075 回执随 PLAN-003 W8**。因此本计划在 PLAN-003 关闭且回执发出后随之
  关闭；3.5 版本划分产物（REQ-080）与 Rudin 端到端验收（ARCH-009）不属本计划关闭条件。
- 收尾：配置与解析事实并入 `design/model-integration.md`、`design/parsing-pipeline.md`、
  `architecture/api.md`、`architecture/code-map.md`；todo 任务原子迁移 `completed.md`；计划壳
  按 Retain 归档 `docs/history/plans/2026-09/`。

## 台账历史归档（2026-09-21 承载契约压缩）

按 [ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md) 决定 4 压缩 todo 行证据列，以下为
PLAN-002 行被移除的历史日志原文（保留原貌，不回改）。

> **2026-09-20 复核收窄**：Step 1 与 Step 2-A~E 交付、门禁 245 passed + ruff 全绿，DB 与存储支线关闭；**2026-09-20 收口**：Step 3 联调与缺陷修复由 PLAN-003 承接，本计划剩余仅「标准 6/7 随 PLAN-003 关闭即判达成 + 标准 8 回执」
