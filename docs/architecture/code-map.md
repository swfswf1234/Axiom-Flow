# 代码映射（v2）

状态：Current
确认状态：暂定
最后更新：2026-09-17

本表登记受管模块与其设计文档（DesignRef）、测试的对应关系。v1 模块已归档
（`docs/history/v1-20260814/`），不在此列。**ARCH-020 重构（2026-09-14）**后目标包结构：
api / ingest / engines / normalize / orchestrator / db / schemas / config；**PLAN-002
（2026-09-17）**完成基本解析 + 分块落地与 v1 模块退役。

## 受管模块

| 路径 | 职责 | 状态 | 设计 | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 对外 `/api/v1` 路由，只做协议适配 | **Current** | `docs/architecture/api.md` | `tests/contract/test_api_v1_contract.py` | sync/CRUD/ingest/parse-jobs（异步）/pages/manifest/edit/chunks 已实现 |
| `src/axiom_flow/ingest/` | 预处理：PDF 导入、页图渲染、book.json/manifest（原子落盘） | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_ingest.py` | 产物落 `QED_DATA_ROOT/parsed/`；`artifacts.py` 提供原子写入与 PNG 尺寸 |
| `src/axiom_flow/orchestrator/` | 解析编排：全本/逐页、失败语义、分块重建、af_* 回写 | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_orchestrator.py` | `jobs.py` 后台单 worker；`pipeline.py` 主流程 |
| `src/axiom_flow/engines/` | 引擎适配器：MinerU 落地；paddleocr-vl / qwen-vl 预留 | **Current** | `docs/design/model-integration.md` | `tests/unit/test_engines.py` | 直连 `QED_OCR_MODEL_URL`；不暴露调参 |
| `src/axiom_flow/normalize/` | 后处理：统一 blocks + Markdown + 质量信号 + 分块 | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_normalize.py`、`test_chunking.py` | `blocks.py` / `chunking.py` |
| `src/axiom_flow/db/` | af_* 四表 ORM/仓储 | **Current** | `docs/design/parsing-management.md` | `tests/unit/test_db.py` | engine/models/repositories |
| `src/axiom_flow/migrations/` | Alembic 迁移（af_* 四表一次建全） | **Current** | `docs/architecture/database-design.md` | `tests/unit/test_db_migrations.py` | 升级/降级/重复执行；`alembic.ini` 为入口 |
| `src/axiom_flow/schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job/edit/chunk） | **Current** | `docs/design/parsing-pipeline.md` | `tests/unit/test_schemas.py` | 内外契约单一事实源 |
| `src/axiom_flow/config.py` | 统一配置（三段式：服务/数据与库/解析引擎） | **Current** | `docs/design/model-integration.md` | `tests/unit/test_config.py` | `QED_OCR_MODEL_URL`（5002）/ `AXIOM_OCR_TIMEOUT`；v1 键退役 |

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
