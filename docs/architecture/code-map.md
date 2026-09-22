# 代码映射（v2）

状态：Current
确认状态：暂定
最后更新：2026-09-21

本表登记受管模块与其设计文档（DesignRef）、测试的对应关系。v1 模块已归档
（`docs/history/v1-20260814/`），不在此列。**ARCH-020 重构（2026-09-14）**后目标包结构：
api / ingest / engines / normalize / orchestrator / db / schemas / config；**PLAN-002
（2026-09-17）**完成基本解析 + 分块落地与 v1 模块退役；**PLAN-004 / REQ-080（2026-09-21）**把产物
按解析版本划分（`versions/<job_id>/` + `af_books.active_job_id` 生效指针），受影响模块为
`ingest/`（版本目录与根视图交换）、`orchestrator/`（写版本目录 + 终态激活）、`api/`（读取面单一
入口 `_read_scope`）、`db/`（`job_id` 作用域）。

## 受管模块

| 路径 | 职责 | 状态 | 设计 | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 对外 `/api/v1` 路由，只做协议适配 | **Current** | `docs/architecture/api.md` | `tests/contract/test_api_v1_contract.py` | sync/CRUD/ingest/parse-jobs（异步）/pages/manifest/edit/chunks/**versions** 已实现；读取面版本作用域唯一入口 `_read_scope`；lifespan 在受理首个请求**之前**执行 `runner.reconcile_on_startup()`（ADR 0007 S5）；响应体暴露 `last_parse_result` 与 `failed_pages` |
| `src/axiom_flow/ingest/` | 预处理：PDF 导入、页图渲染、book.json/manifest（原子落盘） | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_ingest.py` | 产物落 `QED_DATA_ROOT/parsed/`；`artifacts.py` 提供原子写入、PNG 尺寸与数据根边界闸（`ensure_safe_segment`/`ensure_within`）+ **版本目录**（`version_dir_for`/`link_pages_into` 硬链接回落 copy）；`importer.py` 只交换**根视图**（`book.json` + `pages/`）并保留 `versions/`；根 manifest 排除 `versions/` |
| `src/axiom_flow/orchestrator/` | 解析编排：全本按页窗口分块/逐页、失败语义、分块重建、af_* 回写 | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py`、`tests/smoke/test_live_mineru_chain.py` | `jobs.py` 后台单 worker（转发 `window_size`，来自 `AXIOM_PARSE_WINDOW_SIZE`）；`pipeline.py` 主流程（页级 commit + progress 节流；提交边界由 MySQL 双连接集成测试守护；**全本按 `_page_windows()` 串行分块，块级 `EngineError` 只失败该块目标页并保留块原因，块间 `health()` 失败即停止提交剩余块**）；写 `versions/<job_id>/`、继承页行、`(book_id, job_id)` 续跑、终态激活 `active_job_id`；`_await_model_idle()` 单一占用闸门（在探活与 `mark_running` 之前，占用与不可用文案分列）；终态 `completed`/`partial`/`failed` 与激活共用 `_version_is_complete()` 单判据（ADR 0007 S1/S4），`interrupted` 只由 `JobRunner.reconcile_on_startup()` 产出（lifespan 受理首个请求之前，S5） |
| `src/axiom_flow/engines/` | 引擎适配器：MinerU 落地 + `factory.py` 显式分派（未实现/未知即报错，不自动兜底）；paddleocr-vl / qwen-vl 预留 | **Current** | `docs/design/model-integration.md` | `tests/unit/test_engines.py`、`test_engine_factory.py`、`tests/smoke/test_live_mineru_chain.py` | 直连 `QED_OCR_MODEL_URL`；不暴露调参；`PageResult.bbox_space` 声明原始块坐标空间；`parse_document(start_page, end_page)` 以 1 基含两端映射 0 基 `start_page_id/end_page_id`，并把窗口内相对 `page_idx` 还原为全书绝对页号；以 `pdf_info` 枚举页使空白页成为合法空页；deadline 每次调用独立计；`model_load()` 上报 `queued/processing` 供编排层排他（无法判定即 `None`，不臆造阻塞） |
| `src/axiom_flow/normalize/` | 后处理：统一 blocks + Markdown + 质量信号 + 分块 | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_normalize.py`、`test_chunking.py` | `blocks.py` / `chunking.py`；`scale_bbox` 按 `bbox_space` 换算到页图像素；未映射字面值与缺失必需字段的条目可见丢弃（`dropped_block_count`/`dropped_types`）；`ref_text` → `PARAGRAPH`（ARCH-019 裁决 (A)）；`split_math_delimiters` 剥引擎自带数学定界符，使 `latex` 恒为裸式、`display` 由定界符决定（ARCH-018） |
| `src/axiom_flow/db/` | af_* 四表 ORM/仓储 | **Current** | `docs/design/parsing-management.md` | `tests/unit/test_db.py` | engine/models/repositories；页与编辑仓储按 `(book_id, job_id)` 作用域，`LEGACY_JOB_ID` 表示根视图，`BookRepository.set_active_job` 为激活唯一入口；进度与结局分列（`update_parse(last_parse_result=…)` 只在终态写结局、`mark_parse_idle()` 只回进度），`ParseJobRepository.reconcile_interrupted()` 是启动期归一的唯一改写口（ADR 0007） |
| `src/axiom_flow/migrations/` | Alembic 迁移（af_* 四表一次建全 + 解析版本布局 + 状态语义分列） | **Current** | `docs/architecture/database-design.md` | `tests/unit/test_db_migrations.py` | 升级/降级/重复执行；`20260920_0002` 加 `active_job_id`、页/编辑 `job_id` 作用域与 `legacy` backfill（主键/唯一键走「建新表 + 拷贝 + 改名」，同一份 DDL 兼容 SQLite 与 MySQL）；`20260921_0001` 加 `last_parse_result`/`failed_pages`，存量终态值只**平移**一次且**不改写历史 job 状态**（JSON 列走「加可空 → 回填 `[]` → 收紧 NOT NULL」避开 MySQL 不支持 JSON 字面量 DEFAULT）；`alembic.ini` 为入口 |
| `src/axiom_flow/schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job/edit/chunk） | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_schemas.py` | 内外契约单一事实源；`QualityMetrics.blank_page` 标「引擎确认该页存在但未回内容」（与解析失败、映射缺口三分）；状态枚举按 ADR 0007 三分：`JobStatus`（queued/running/completed/partial/failed/interrupted）、`ParseStatus`（仅进度 pending/parsing）、`LastParseResult`（结局，`""` = 从未有任务） |
| `src/axiom_flow/config.py` | 统一配置（三段式：服务/数据与库/解析引擎） | **Current** | `docs/design/model-integration.md` | `tests/unit/test_config.py` | `QED_OCR_MODEL_URL`（5002）/ `AXIOM_OCR_TIMEOUT`（**单块**一次调用）/ `AXIOM_PARSE_WINDOW_SIZE`（全本每块页数，默认 56）；v1 键退役 |
| `tests/support/` | 测试侧共享基建：`mysql.py` 兑现 `conftest.py` 的 `mysql_settings`/`mysql_store` 夹具（`QED_TEST_MYSQL=1` opt-in、`qed_test` 库名硬校验、白名单 DROP 自有表、Alembic 升级到 head） | **Current** | `docs/standards/testing.md` | 被 `tests/integration/test_parse_persistence.py` 使用 | [ADR 0005](../adr/0005-mysql-integration-test-isolation.md) 的隔离边界执行面；缺 opt-in 时整层 skip |

**已退役（2026-09-17，PLAN-002）**：`src/axiom_flow/llm_client.py`、`src/axiom_flow/fallback/`
（由 `engines/` 承接）、`src/axiom_flow/database.py`（并入 `db/engine.py`）。

## 非受管配置

| 路径 | 职责 |
| --- | --- |
| `scripts/axiom_flow_service.py` | 8902 API 服务唯一启停脚本（start/stop/restart/status，DesignRef：`docs/architecture/api.md` ①生命周期与健康） |
| `scripts/tmp/` | 临时/试验脚本（API 冒烟等） |
| `alembic.ini` | Alembic CLI 入口（`src/axiom_flow/migrations/`；URL 解析见 env.py） |
| `data/` | 仓库内兜底数据根（无 `QED_DATA_ROOT` 时；ignore、可再生） |
| `evaluation/` | 评估专区（样本与报告，与主链路隔离） |
| `web/` | 已移交 QED-Engine，本仓库不再维护 |
| `model/`（根仓库） | 模型文件槽位（由根仓库 8900 管理，本仓库只读引用） |
