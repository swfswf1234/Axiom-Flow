# Axiom-Flow 配置中心对齐与分阶段重构（axiom-flow-refactor）

状态：In Progress
任务类型：B
最后更新：2026-09-17
关联 ADR：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、解析管线归本仓库；本计划不新增 ADR，只对齐既有决策）
关联设计：[model-integration.md](../design/model-integration.md)、[parsing-pipeline.md](../design/parsing-pipeline.md)、[parsing-management.md](../design/parsing-management.md)
关联 Tracker：docs/trackers/todo.md（ARCH-001、PLAN-002、REQ-075）
归档判定：Retain（配置对齐与重构事实并入 design/architecture 后，计划壳归档 history/plans/2026-09/）

## 背景与用户裁决（2026-09-17）

QED-Engine 根仓库已重建配置中心（`.env` 四段式、模型注册表与 runtime 同化，PLAN-046）：
`QED_MINERU_URL` 删除改为 `QED_OCR_MODEL_URL`，MinerU 端口 8002 → 5002，模型启停/探针/资源
互斥全部归 8900，Axiom-Flow 只需**连接**模型服务。根仓库已登记
[REQ-075](https://github.com/QED-Engine/qed-engine/blob/main/docs/trackers/todo.md)（请求：Axiom-Flow
——MinerU 直连配置同步），要求本仓库引擎适配器读新键与新端口、同步 operations/development/
model-integration 文档与 smoke 脚本，**不保留旧键别名**。

本仓库现状：`.env` 仍为 v1 残留（`AXIOM_MYSQL_*` / `AXIOM_API_KEY` / `AXIOM_DATA_DIR` /
`AXIOM_WEB_DIR` / `AXIOM_VISION_*`）；`config.py` 无引擎与 OCR 地址键、不读 `QED_DATA_ROOT`；
设计文档仍含 8002/`QED_MINERU_URL`；源码仍为 v1 形态（`data/books/`、qwen-vl fallback、内存态
job），`engines/` / `normalize/` / `db/` / `orchestrator/` 未落地。

用户裁决（2026-09-17）：

1. **库名**：af_* 落 `qed` 库（`qed_test` 为测试库）；本地启动读本仓库自身 `.env`（含
   `QED_DB_NAME=qed`），由 QED-Engine 启动时跟随根 `.env`；两边配置允许不一致。
2. **节奏**：分阶段——先骨架 + 配置，再功能。
3. **`.env` 策略**：保留必要配置，支持本服务独立部署（不强制精简为仅私有键）。
4. **ADR**：不新增 ADR，更新现有 design/architecture。
5. **提交**：先完成文档改造并提交文档 commit；代码更新后经用户确认再提交代码 commit。

## 目标与成功标准

**目标**：① Axiom-Flow 配置中心与重建后的 QED-Engine 对齐（新键、新端口、数据根、库名）；
② 设计文档收口为重构唯一事实源；③ 按阶段完成骨架与功能重构，实现 v0.1 解析管理闭环。

**成功标准**：

1. `.env` / `.env.example` 四段式对齐根仓库；废弃键清除；本服务可独立部署（自身 `.env`
   含 `QED_DB_NAME=qed` 与引擎配置）。
2. `config.py` 读取 `AXIOM_OCR_ENGINE`、`QED_OCR_MODEL_URL`、`QED_DATA_ROOT`（经
   `AXIOM_FLOW_DATA_DIR` 覆盖优先）、`QED_LLM_TIMEOUT`；密钥不进 repr；坏值不阻断启动。
3. 全仓库无 `8002` / `QED_MINERU_URL` 活跃引用（历史归档除外）；MinerU 表述统一为
   「8900 托管生命周期，本仓库只连接 `QED_OCR_MODEL_URL`（5002）」。
4. 目标包结构（api / ingest / engines / normalize / orchestrator / db / schemas / config）与
   code-map 一致；v1 模块（`llm_client.py`、`fallback/`、`database.py`、仅剩 `.pyc` 的旧目录）
   按计划退役。
5. af_* 四表 Alembic 可升级/降级；sync 幂等、edit upsert 覆盖、ingest 幂等。
6. 端到端冒烟：Rudin 教程同步 → ingest → 解析（MinerU 5002）→ 对照 → 编辑 → 回显；质量
   验收公式可渲染 ≥ 90%（用户确认）。
7. 门禁全绿：`pytest tests -q` + `ruff check src tests` + `tests/contract/`。

## 范围与非目标

**范围内**：本仓库 `.env(.example)`、`config.py`、`schemas.py`、`ingest/`、新增
`engines/`、`normalize/`、`orchestrator/`、`db/`（Alembic + 仓储）、`api/`；v1 模块退役；
`docs/design/`、`docs/architecture/`、`docs/guides/`、`docs/standards/local-dev.md` 的配置与
引擎契约同步；`scripts/axiom_flow_service.py` 与 smoke 脚本适配。

**非目标**：不修改 QED-Engine 根仓库与 QED-Tracker（只读）；不实现 PaddleOCR-VL / 云端
qwen-vl 引擎（仅预留适配器接口）；不实现 RAG / 知识图谱 / 数学解析器；不做 `axiom` / `xqfm`
遗留库删除与 dataset 物理清理（D 类，另立计划）；不改 af_* 四表 DDL（以现有设计为准）。

## 前置条件

1. QED-Engine 根仓库配置中心已重建（`.env` 四段式、`QED_OCR_MODEL_URL`、MinerU 5002）。
2. MinerU 容器可由 8900 自动启停，`http://127.0.0.1:5002/health` 可达（本仓库只连接）。
3. `qed` 库可用，本仓库 Alembic 可独立初始化 af_* 表。
4. 工作树 ARCH-020 文档重构改动先提交为基线（2026-09-17 已由用户确认顺序）。

## 工作项

### Phase 1 配置中心对齐（文档先行）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 1.1 | `.env` / `.env.example` 四段式重写 | ① 全局 ② API ③ 本地模型/引擎 ④ 元数据库；删 `AXIOM_API_KEY`/`AXIOM_MYSQL_*`/`AXIOM_DATA_DIR`/`AXIOM_WEB_DIR`/`AXIOM_EVALUATION_DATA_DIR`/`AXIOM_VISION_*`/`AXIOM_MODEL_*`；本服务独立部署（`QED_DB_NAME=qed`） |
| 1.2 | `config.py` 键对齐 | 新增 `ocr_engine` / `ocr_model_url`（`QED_OCR_MODEL_URL`）/ `llm_timeout`；`data_dir` 优先级 `AXIOM_FLOW_DATA_DIR` > `QED_DATA_ROOT` > 仓库 `data/`；移除 `AXIOM_DATA_DIR` 回退 |
| 1.3 | `tests/unit/test_config.py` 同步 | env 优先级、新键默认、数据根回退、密钥隔离 |
| 1.4 | 文档同步 | `model-integration.md` / `parsing-pipeline.md` / `local-dev.md` / `operations.md` / `development.md` / `code-map.md`：8002 → 5002、`QED_MINERU_URL` → `QED_OCR_MODEL_URL`、MinerU 生命周期归属 |

### Phase 2 设计收口

| # | 工作项 | 产出 |
| --- | --- | --- |
| 2.1 | `model-integration.md` 刷新 | 引擎配置表（`AXIOM_OCR_ENGINE` / `QED_OCR_MODEL_URL` / `AXIOM_PADDLEOCR_URL`）、直连契约、`llm_client`/`fallback` 退役确认、`qed_llm_calls` 写入 |
| 2.2 | `parsing-pipeline.md` 刷新 | 数据根 `QED_DATA_ROOT/parsed/`、MinerU 5002、source 枚举（mineru/paddleocr-vl/qwen-vl）、退役清单 |
| 2.3 | `parsing-management.md` 刷新 | `qed` 库、`file_path` 数据根相对语义、独立部署与根 `.env` 两种语境 |
| 2.4 | `api.md` / `database-design.md` / `overview.md` 核对 | 端口、库名、拓扑一致性 |
| 2.5 | `code-map.md` 更新 | 模块状态（config Current、engines/normalize/db/orchestrator Pending）、退役模块标注 |

### Phase 3 重构·骨架 + 配置（代码第一步）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 3.1 | 目标包骨架 | `engines/`（`base.py` + `mineru.py` 占位）、`normalize/`、`db/`（含 Alembic env）、`orchestrator/` 骨架 |
| 3.2 | 模块退役 | `database.py` 并入 `db/`；删除 `llm_client.py`、`fallback/`；清理仅剩 `.pyc` 的 v1 目录（`app/`、`application/`、`domain/`、`infrastructure/`、`worker/`、`tools/`、旧 `migrations/`） |
| 3.3 | `schemas.py` 升级 | `image_size` / `index` / `order` / `Source` 扩展 / `EditRecord` / `QualityMetrics` / job 字段对齐设计 |
| 3.4 | `api/main.py` 骨架 | 按 `api.md` 四类端点路由骨架与错误语义 |
| 3.5 | 骨架门禁 | `pytest tests -q` + `ruff check src tests` + `tests/contract/` |

### Phase 4 重构·功能（代码第二步）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 4.1 | db：af_* 四表 | Alembic 迁移（升级/降级）+ 仓储层（af_books / af_parse_jobs / af_pages / af_block_edits） |
| 4.2 | engines + normalize | MinerU 适配器（`QED_OCR_MODEL_URL` 5002，`/file_parse`）+ 统一 blocks 归一化（mock 单测） |
| 4.3 | orchestrator | 任务/页状态机、逐页/全本、重试、质量信号、断点续跑 |
| 4.4 | ingest 落点迁移 | `QED_DATA_ROOT/parsed/<domain_id>/<course_id>/<book_id>/` + `book.json` / `manifest.json` |
| 4.5 | api 功能端点 | books sync/CRUD/ingest、pages/blocks/edit、parse-jobs（af_* 持久化） |
| 4.6 | 端到端冒烟 | Rudin 教程（mathanalysis-b05 + b11）全链路 + 质量验收 |

## 验证与验收

- **定向测试**：`tests/unit/test_config.py`、`tests/unit/test_schemas.py`、`tests/unit/test_ingest.py`、
  `tests/unit/`（引擎归一化、仓储）、`tests/contract/test_api_v1_contract.py`、
  `tests/integration/`（MySQL 隔离）。
- **回归门禁**：`pytest tests -q` + `ruff check src tests` + `tests/contract/` 全绿。
- **文档门禁**：架构变更跑 `test_architecture_documents.py`，设计变更跑 `test_design_documents.py`，
  code-map 变更跑 `test_code_document_mapping.py`，计划/台账变更跑 `test_plan_governance.py`、
  `test_tracker_governance.py`。
- **冒烟（需 MinerU 5002 在线）**：真实解析单页/整本，产物 `source=mineru`；`qed_llm_calls` 记录。
- **不能自动化的人工验收**：Rudin 解析效果与公式渲染 ≥ 90% 由用户确认，责任位置：用户。
- **跨项目回执**：完成 Phase 1/2 后回执根仓库 REQ-075（提交号 + 门禁输出）。

## 回滚

- 文档与配置：纯文档/代码变更，`git revert` 即可；无数据库迁移、无数据操作。
- 新键全部有缺省值，回滚后旧键语义不恢复（根仓库已删除旧键，属声明风险）。
- Phase 4 的 af_* 表 Alembic 可降级；`parsed/` 产物为可再生数据，可清理。

## 关闭与归档

- 关闭条件：成功标准 7 条全达成 + Rudin 端到端用户确认 + REQ-075 回执。
- 收尾：配置与引擎事实并入 `design/model-integration.md`、`architecture/api.md`、
  `architecture/code-map.md`；todo 任务原子迁移 `completed.md`；计划壳按 Retain 归档
  `docs/history/plans/2026-09/`。
