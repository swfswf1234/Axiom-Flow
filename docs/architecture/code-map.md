# 代码映射

状态：Current
确认状态：已确认
最后更新：2026-09-26
覆盖范围：`src/axiom_flow/` 全部受管包与模块、`scripts/` 与仓库级数据/评估目录；映射目标为
`docs/architecture/` 契约正文与 `docs/design/` 三篇的守则条文

## 目的与边界

本表登记**受管模块 ↔ 设计事实源 ↔ 关联测试**的对应关系，是「改动落在哪个模块、契约写在哪儿、
由哪条测试守护」的唯一映射入口。它只回答三件事：

- 这个模块**是什么层级、担什么职责**（一句话，不含行为判据）；
- 它的**契约与判据事实源**在哪（固定文档小节，或 `design/` 的条文编号 `PP-n` / `PM-n` / `MI-n`）；
- 它的**回归面**是哪几条测试。

v1 时代的模块已归档于 `docs/history/v1-20260814/`，不在本表映射范围（`src/axiom_flow/` 下
偶见的 `application/`、`domain/`、`infrastructure/`、`tools/`、`worker/` 目录只含 v1 遗留的
`__pycache__` 字节码，无源码，不是受管模块）。

**本表不复述行为判据**。治理规范把「备注长成第二份实现正文」列为缺陷（ADR 0008 背景）：同一事实
两处存必漂移。因此每条判据只在 `design/` 条文表或 `architecture/` 契约小节出现一次，本表以编号
反向指过去。模块职责或契约变化时，同一变更内同步本表与对应设计文档。

### 本篇不管

| 不在本表范围 | 事实源位置 |
| --- | --- |
| 端点契约、请求/响应字段、错误码 | [api.md](api.md) |
| 表结构、列语义、迁移序列 | [database-design.md](database-design.md) |
| 解析管线条文（预处理/引擎/归一化/落盘/分块） | `docs/design/parsing-pipeline.md`（PP-1~PP-20） |
| 书目与版本管理条文 | `docs/design/parsing-management.md`（PM-1~PM-17） |
| 模型服务对接与配置条文 | `docs/design/model-integration.md`（MI-1~MI-15） |
| 测试分层与门禁命令 | [testing.md](../standards/testing.md)、[development.md](../guides/development.md) |
| 交付过程、退役时间线 | `docs/trackers/completed.md` 与承载计划 |

## 当前契约

### 受管模块

「层级」列说明调用链位置；「事实源」列给出该模块行为判据的**权威出处**（条文编号或固定文档小节）；
「关联测试」是该模块的回归面。路径列逐字与仓库目录/文件一致（守护按模块路径存在性对账）。

| 路径 | 层级 | 一句话职责 | 事实源（条文 / 固定文档小节） | 关联测试 |
| --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 接口层 | 对外 `/api/v1` 路由，只做协议适配与读取作用域解析 | [api.md](api.md) 全文；`_read_scope` 唯一入口见 api.md「兼容与不可变规则」；PM-6、PM-10、PM-16 | `tests/contract/test_api_v1_contract.py` |
| `src/axiom_flow/api/main.py` | 接口层 | 公共契约端点 + lifespan 启动期归一与瞬态残留告警 | [api.md](api.md)「① 生命周期与健康」「② 数据查询」「③ 解析结果与 PDF 对照」；PM-12；PP-4 | `tests/contract/test_api_v1_contract.py`、`tests/unit/test_orchestrator.py` |
| `src/axiom_flow/api/admin.py` | 运维接口层 | 非公开管理面：`af_*` 四表行 + `parsed/` 产物一键清除 | [api.md](api.md)「非公开管理面」节（挂载/token/双确认/守卫次序/六条不变式）；PM-17；PP-4 | `tests/contract/test_api_v1_contract.py`（`TestAdminPurgeEndpoint`） |
| `src/axiom_flow/ingest/` | 预处理 | PDF 导入、页图渲染、`book.json`/manifest 原子落盘与版本目录播种 | PP-2、PP-3、PP-4、PP-5、PP-15、PP-16；[api.md](api.md) ingest 端点节 | `tests/unit/test_ingest.py`、`tests/contract/test_storage_conventions_governance.py` |
| `src/axiom_flow/orchestrator/` | 编排层 | 全本按窗口串行提交、逐页解析、失败语义、分块重建、af_* 回写、版本激活 | PP-11、PP-13、PP-14、PP-16、PP-17、PP-18；PM-8、PM-11、PM-12、PM-15；MI-12 | `tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py`、`tests/smoke/test_live_mineru_chain.py` |
| `src/axiom_flow/orchestrator/jobs.py` | 编排层 | 后台单 worker 队列与任务生命周期转发 | PM-8、PM-13；[api.md](api.md)「单占用与排队」 | `tests/unit/test_orchestrator.py` |
| `src/axiom_flow/orchestrator/pipeline.py` | 编排层 | 一次解析版本的执行主体（窗口循环、页级持久化、终态判定） | PP-11、PP-13、PP-14、PP-18；PM-11、PM-15；MI-9 | `tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py` |
| `src/axiom_flow/engines/` | 适配层 | 引擎适配器（MinerU 落地，paddleocr-vl / qwen-vl 预留）与显式分派工厂 | MI-2、MI-3、MI-4、MI-5、MI-6、MI-7、MI-8、MI-10、MI-11、MI-12、MI-13、PP-12 | `tests/unit/test_engines.py`、`tests/unit/test_engine_factory.py`、`tests/smoke/test_live_mineru_chain.py` |
| `src/axiom_flow/normalize/` | 后处理 | 统一 blocks 归一化、坐标换算、可见丢弃、公式定界符剥离、知识块切分与质量信号 | PP-7、PP-8、PP-9、PP-10、PP-19、PP-20；MI-8 | `tests/unit/test_normalize.py`、`tests/unit/test_chunking.py` |
| `src/axiom_flow/db/` | 持久层 | `af_*` 四表 ORM 与仓储（版本作用域读写、进度/结局分列、清除原语） | [database-design.md](database-design.md) 列清单与条文语义节；PM-1、PM-2、PM-3、PM-4、PM-5、PM-6、PM-9、PM-10、PM-17 | `tests/unit/test_db.py` |
| `src/axiom_flow/db/engine.py` | 持久层 | 双 Session 工厂（业务库 / 测试库）与连接参数装配 | [database-design.md](database-design.md)「边界」；ADR 0005 | `tests/integration/test_parse_persistence.py` |
| `src/axiom_flow/migrations/` | 持久层 | Alembic 迁移（建表、解析版本布局、状态语义分列） | [database-design.md](database-design.md)「迁移序列」表 + 「边界」的无自动迁移约定 | `tests/unit/test_db_migrations.py`、`tests/integration/test_parse_persistence.py` |
| `src/axiom_flow/schemas.py` | 契约模型 | 统一格式与请求/响应的 Pydantic 单一事实源 | [api.md](api.md)「兼容承诺」；PP-7、PP-20；PM-8、PM-10 | `tests/unit/test_schemas.py` |
| `src/axiom_flow/config.py` | 配置 | 三段式配置读取、数据根 fail-closed 解析与 `.env` 走查（含管理面 fail-closed 校验） | MI-1；[model-integration.md](../design/model-integration.md)「配置键」表；PM-11 的超时/窗口键；[api.md](api.md)「非公开管理面」；ADR 0009；[operations.md](../guides/operations.md)「数据目录」 | `tests/unit/test_config.py`、`tests/contract/test_storage_conventions_governance.py` |

### 非受管配置

| 路径 | 一句话职责 | 事实源 |
| --- | --- | --- |
| `scripts/axiom_flow_service.py` | 8902 服务唯一启停脚本 | [api.md](api.md)「服务生命周期脚本」 |
| `scripts/`（其余） | 数据与运维辅助脚本（重解析、备份、探针驱动） | [operations.md](../guides/operations.md) |
| `scripts/tmp/` | 真机窗口驱动与只读取证脚本（**已入库**，被运维手册按路径引用）
 | [operations.md](../guides/operations.md) runbook |
| `alembic.ini` | Alembic CLI 入口（URL 解析见 `migrations/env.py`） | [database-design.md](database-design.md)「迁移序列」 |
| `data/` | 仓库内留档与备份区（`probes/` 窗口证据、`backups/` D 类留痕；**非数据根候选**，ignore、可再生） | [operations.md](../guides/operations.md)「数据目录」；[local-dev.md](../standards/local-dev.md) |
| `evaluation/` | 评估专区（样本与报告，与主链路隔离） | [testing.md](../standards/testing.md) |
| `model/`（根仓库） | 模型文件槽位，由根仓库 8900 管理，本仓库只读引用 | [model-integration.md](../design/model-integration.md) |

## 失败与兼容语义

- **模块新增/移动/删除**：与代码同一变更内同步本表；先用 `rg` 全库确认旧路径无残余引用，再改
  本表与对应设计文档（治理规范「代码与文档追溯」）。
- **只映射、不裁决**：本表的「实现状态」类判断一律不在本表出现——某模块是否完成取决于其事实源
  小节的 `实现状态` 与测试面。本表若与 `design/` 条文冲突，以条文为准并修正本表。
- **退役模块只留形状事实**：下表记录已被承接走的旧路径，供历史资料与链接定位用；其取代关系是
  `design/` 条文与 ADR 的事实。

### 已退役模块

| 旧路径 | 取代者 |
| --- | --- |
| `src/axiom_flow/llm_client.py` | 退役（无取代：直连模型服务由 `engines/` 承接，见 MI-3） |
| `src/axiom_flow/fallback/` | `src/axiom_flow/engines/`（无自动兜底，ADR 0004 / MI-13） |
| `src/axiom_flow/database.py` | `src/axiom_flow/db/engine.py` |
| `web/` | 移交根仓库 QED-Engine（本仓库不再维护） |

## 验证与追溯

- **映射守护**：`tests/contract/test_architecture_documents.py` 断言本表覆盖关键模块路径、并含
  `docs/design/parsing-pipeline.md`；`tests/contract/test_code_document_mapping.py` 断言
  设计文档 ↔ 模块的双向可达与链接零残余。
- **形式守护**：`tests/contract/test_fixed_document_form.py` 断言本表元数据属「映射与索引」体裁
  （无 `设计状态`/`实现状态`）、H2 等于四段骨架白名单、正文零任务编号与日期，并逐条校验
  「关联测试」列里的测试文件真实存在（映射表指向不存在的回归面即失败）。
- **来源**：模块划分自 ARCH-020 的 v2 重构（`PLAN-001`，已归档）；`versions/<job_id>/` 与
  `active_job_id` 作用域自 REQ-080（`PLAN-004`，已归档）；状态语义分列自 ADR 0007（`PLAN-006`，
  已归档）；管理面清除行自 ARCH-024 / ARCH-026（`PLAN-010` / `PLAN-011`，已归档）；数据根 fail-closed
  与存储治理守护自 ADR 0009（`PLAN-016`，进行中）；本轮形式改造
  依据 [ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md)（`PLAN-014`）。
- **删→存于登记**：原「备注」列复述的行为判据全部回指既有条文，未新增事实：`_read_scope` 唯一
  入口 → PM-6 + api.md 兼容规则；`_page_total()` 枚举域 → api.md 页清单节「枚举域」；lifespan
  启动期归一 → PM-12；管理面守卫次序与目录识别 → api.md「非公开管理面」六条不变式 + PM-17；
  原子写入/双闸/版本目录与硬链接 → PP-4、PP-5、PP-15；根 manifest 排除 `versions/` → PP-16；
  窗口串行分块与块级失败域 → PP-11；页级 commit 与产物先于页行 → PP-13；继承页行与续跑认产物 →
  PP-18；单一占用闸门 → MI-12；`completed`/激活同判据 → PM-11；`model_load()` 与窗口字段 →
  MI-6、MI-12；坐标换算与可见丢弃 → PP-8、PP-9；`ref_text` 映射与数学定界符剥离 → MI-8、PP-10；
  仓储作用域与清除原语 → PM-3、PM-4、PM-6、PM-17 + database-design.md 列清单；`last_parse_result`
  分列 → PM-10；列表读取面（`list_all`/`list_states`/`count_by_page`/`active_job_index`）→
  api.md 两条列表端点节；配置键与超时/窗口 → MI-1、PP-11 + model-integration.md 配置键表；
  迁移主键改表法与 JSON 列顺序 → database-design.md「迁移序列」表。
