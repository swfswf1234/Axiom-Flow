# 代码映射

状态：Current
确认状态：已确认
最后更新：2026-10-22
覆盖范围：`src/axiom_flow/` 全部受管包与模块、`scripts/` 与仓库级临时/日志/评估目录；映射目标为
`docs/architecture/` 契约正文与 `docs/design/` 四篇的守则条文

## 目的与边界

本表登记**受管模块 ↔ 设计事实源 ↔ 关联测试**的对应关系，是「改动落在哪个模块、契约写在哪儿、
由哪条测试守护」的唯一映射入口。它只回答三件事：

- 这个模块**是什么层级、担什么职责**（一句话，不含行为判据）；
- 它的**契约与判据事实源**在哪（固定文档小节，或 `design/` 的条文编号 `PP-n` / `PM-n` / `MI-n` / `KA-n`）；
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
| 知识点梳理与提升条文（发起、批次三态、写者分域、确认与提升通路） | `docs/design/knowledge-analysis.md`（KA-1~KA-45） |
| 测试分层与门禁命令 | [testing.md](../standards/testing.md)、[development.md](../guides/development.md) |
| 交付过程、退役时间线 | `docs/trackers/completed.md` 与承载计划 |

## 当前契约

### 受管模块

「层级」列说明调用链位置；「事实源」列给出该模块行为判据的**权威出处**（条文编号或固定文档小节）；
「关联测试」是该模块的回归面。路径列逐字与仓库目录/文件一致（守护按模块路径存在性对账）。

| 路径 | 层级 | 一句话职责 | 事实源（条文 / 固定文档小节） | 关联测试 |
| --- | --- | --- | --- | --- |
| `src/axiom_flow/api/` | 接口层 | 对外 `/api/v1` 路由，只做协议适配与读取作用域解析 | [api.md](api.md) 全文；`_read_scope` 唯一入口见 api.md「兼容与不可变规则」；PM-6、PM-10、PM-16 | `tests/contract/test_api_v1_contract.py` |
| `src/axiom_flow/api/main.py` | 接口层 | 公共契约端点（含 ④ 梳理与复核十条，长文两条腿成对开在节点路径上、总结重生成一条；进度面的在飞两条腿＝`draft` 行 ∧ 在途登记，404 判据＝行／流水／登记三者全无）+ lifespan 启动期归一与瞬态残留告警 + 梳理槽位与调用身份的装配点（接线不全当场 503、零写入） | [api.md](api.md)「① 生命周期与健康」「② 数据查询」「③ 解析结果与 PDF 对照」「④ 梳理与复核通路」；PM-12；PP-4；KA-22、KA-24、KA-30、KA-40、KA-42、KA-44、KA-45 | `tests/contract/test_api_v1_contract.py`、`tests/unit/test_orchestrator.py` |
| `src/axiom_flow/api/admin.py` | 运维接口层 | 非公开管理面：`af_*` 表族行 + `parsed/` 产物一键清除 | [api.md](api.md)「非公开管理面」节（挂载/token/双确认/守卫次序/六条不变式）；PM-17；PP-4 | `tests/contract/test_api_v1_contract.py`（`TestAdminPurgeEndpoint`） |
| `src/axiom_flow/ingest/` | 预处理 | PDF 导入、页图渲染（根视图唯一产物 `pages/`）、原子落盘与版本目录播种 | PP-2、PP-3、PP-4、PP-5、PP-15、PP-16；[api.md](api.md) ingest 端点节 | `tests/unit/test_ingest.py`、`tests/contract/test_storage_conventions_governance.py` |
| `src/axiom_flow/orchestrator/` | 编排层 | 解析侧＝全本按窗口串行提交、逐页解析、区域驱动逐框提交、失败语义、分块重建、af_* 回写、版本激活；梳理侧＝受理门、两阶段执行体、收尾与启动期归一、长文与删除通路 | PP-11、PP-13、PP-14、PP-16、PP-17、PP-18、PP-21；PM-8、PM-11、PM-12、PM-15；MI-12 | `tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py`、`tests/smoke/test_live_mineru_chain.py` |
| `src/axiom_flow/orchestrator/jobs.py` | 编排层 | 后台单 worker 队列与任务生命周期转发；梳理批次的唯一引擎入口 （在此过一次记账包装器）与接线不全的受理期拒跑、受理到首枚目录行落库之间的在途登记（唯一读者是进度面，不参与受理门、不落盘） | PM-8、PM-13；[api.md](api.md)「单占用与排队」；KA-22、KA-23、KA-27、KA-40 | `tests/unit/test_orchestrator.py`、`tests/unit/test_knowledge_pipeline.py` |
| `src/axiom_flow/orchestrator/pipeline.py` | 编排层 | 一次解析版本的执行主体（窗口循环、区域驱动逐框裁剪与一框至多一块、页级持久化、终态判定、激活轮换删旧版本、版本 book.json 生成） | PP-11、PP-13、PP-14、PP-18、PP-21；PM-11、PM-15；MI-9 | `tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py` |
| `src/axiom_flow/orchestrator/signature.py` | 编排层 | 解析代际签名的 canonical 计算与重跑判定纯函数（不探模型服务，版本读数由受理面注入） | [database-design.md](database-design.md)「`af_operations` 列清单」的 `parser_signature` 行；[ADR 0015](../adr/0015-db-single-source-for-readiness.md) | `tests/unit/test_parser_signature.py` |
| `src/axiom_flow/orchestrator/knowledge.py` | 编排层 | 梳理批次的两阶段执行体（受理门与取证集、目录先于知识点、`stage` 决定点腿发不发、目录阶段的两条腿＝模型判页与逐页整理为正腿／单页故障该页回落条目腿、印刷页码偏移直查定锚与窗口外坏行攒批一次核对、正文前后附件页各成一枚顶级行并带非知识点身份位、取材面的逐页家具剔除与目录页条目腿、失败域与形状剔除、失败节的账面点名、目录行按页落库与定稿同批改写、收尾与启动期归一、长文两条腿与删除通路、记账包装器） | [knowledge-analysis.md](../design/knowledge-analysis.md) `KA-1~KA-3、KA-5~KA-23、KA-25~KA-27、KA-29~KA-45`；[database-design.md](database-design.md)「`af_kp` 列清单」 | `tests/unit/test_knowledge_pipeline.py`、`tests/contract/test_api_v1_contract.py` |
| `src/axiom_flow/engines/` | 适配层 | 引擎适配器（MinerU 落地，paddleocr-vl / qwen-vl 预留）与显式分派工厂；文本梳理协议 `KnowledgeEngine` 与文本槽位登记面（登记一枚 `qed-gateway`＝经 8900 网关的适配器，prompt 在本仓组装、回复的非法 JSON 转义在此补计；目录三条腿 `toc_pages`／`toc_entries`／`toc_verify` 的提示词同样在本仓组装（判页回页号集与附件页集、整理走三段竖线行而不是 JSON、核对每条一形），域外页号在适配器里整条弃用；空槽位名与闭集外名字一律 503、不回落） | MI-2、MI-3、MI-4、MI-5、MI-6、MI-7、MI-8、MI-10、MI-11、MI-12、MI-13、PP-12、KA-22、KA-31、KA-32、KA-35、KA-37、KA-38、KA-43、KA-45 | `tests/unit/test_engines.py`、`tests/unit/test_engine_factory.py`、`tests/unit/test_engine_kp_gateway.py`、`tests/smoke/test_live_mineru_chain.py` |
| `src/axiom_flow/normalize/` | 后处理 | 统一 blocks 归一化、坐标换算、可见丢弃、公式定界符剥离、知识块切分与质量信号；`intents.py`＝人工类型改判的读时回放（只覆盖判别维，零写回）；`furniture.py`＝跨页重复线的页域判据（读时派生、零写库）与梳理取材的剔除面（逐页家具文本 ∪ 整行删副本，产物不动）；`toc.py`＝书自身目录页的条目腿（行末引导点与印刷页码剔离、折行拼回、偏移众数标定与窗口内回定位、行内第二条目再拆与页级命中率复核；判页样本的取材封顶＝`page_sample_text` 按预算逐块截字，回落腿的非知识点身份位集合＝`reference_role_for_name`） | PP-7、PP-8、PP-9、PP-10、PP-19、PP-20；MI-8；PM-5、PM-19；KA-29、KA-33、KA-35、KA-36、KA-37、KA-38、KA-39、KA-41、KA-44 | `tests/unit/test_normalize.py`、`tests/unit/test_chunking.py`、`tests/contract/test_api_v1_contract.py`（回放面）、`tests/unit/test_knowledge_pipeline.py`（取证面、剔除面与条目腿并进） |
| `src/axiom_flow/db/` | 持久层 | `af_*` 四表 ORM 与仓储（`BookRepository`/`OperationRepository`/`PageRepository`/`KpRepository`：事件与意图两态、`intent_key` 幂等 upsert、结构意图全量声明、版本作用域读写、进度/结局分列、清除原语） | [database-design.md](database-design.md) 列清单与条文语义节；[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)；[api.md](api.md)「PUT …/structure」小节；PM-1、PM-2、PM-3、PM-4、PM-5、PM-6、PM-9、PM-10、PM-17、PM-18；[knowledge-analysis.md](../design/knowledge-analysis.md)（KA-11 写者分域与长文指针一条、KA-27 批次流水的正向断言与 KA-28 域外值守护、KA-30 的 `set_narrative_ref`、KA-40 的同批定稿写手 `repoint_draft_outline`） | `tests/unit/test_db.py`、`tests/unit/test_db_knowledge.py`、`tests/unit/test_db_table_family_governance.py`、`tests/integration/test_concurrency_criteria.py` |
| `src/axiom_flow/db/llm_ledger.py` | 持久层 | 共享表 `qed_llm_calls` 的追加写通路（只有一条 `INSERT`、不建 ORM、不进 `Base.metadata`；身份三列由装配点注入，形状闸在发出前） | [knowledge-analysis.md](../design/knowledge-analysis.md) `KA-23`；[database-design.md](database-design.md)「共享表登记面」；MI-13 | `tests/unit/test_db_llm_ledger.py`、`tests/integration/test_llm_call_ledger.py` |
| `src/axiom_flow/db/engine.py` | 持久层 | 双 Session 工厂（业务库 / 测试库）与连接参数装配 | [database-design.md](database-design.md)「边界」；ADR 0005 | `tests/integration/test_parse_persistence.py` |
| `src/axiom_flow/migrations/` | 持久层 | Alembic 迁移（档 B 链重置后只有唯一 base：一条迁移建 `af_*` 四表，`downgrade` 可重复） | [database-design.md](database-design.md)「迁移序列」表 + 「边界」的无自动迁移约定；[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md) | `tests/unit/test_db_migrations.py`、`tests/integration/test_parse_persistence.py` |
| `src/axiom_flow/schemas.py` | 契约模型 | 统一格式与请求/响应的 Pydantic 单一事实源 | [api.md](api.md)「兼容承诺」；PP-7、PP-20；PM-8、PM-10；KA-31、KA-34、KA-42、KA-43、KA-44、KA-45 | `tests/unit/test_schemas.py` |
| `src/axiom_flow/config.py` | 配置 | 三段式配置读取、数据根 fail-closed 解析与 `.env` 走查（含管理面 fail-closed 校验）；梳理出站两键（网关地址与超时）内置默认、`.env` 零改动 | MI-1；[model-integration.md](../design/model-integration.md)「配置键」表；PM-11 的超时/窗口键；KA-22 的槽位默认值；[api.md](api.md)「非公开管理面」；ADR 0009；[operations.md](../guides/operations.md)「数据目录」 | `tests/unit/test_config.py`、`tests/contract/test_storage_conventions_governance.py` |

### 非受管配置

| 路径 | 一句话职责 | 事实源 |
| --- | --- | --- |
| `scripts/axiom_flow_service.py` | 8902 服务唯一启停脚本 | [api.md](api.md)「服务生命周期脚本」 |
| `scripts/`（其余） | 无其他受管脚本：一次性窗口驱动与探针脚本一律落仓库根 `tmp/`（ADR 0012 决定 3） | [operations.md](../guides/operations.md)「临时与留档区」 |
| `tmp/`（仓库根） | 仓内**唯一**临时与留档区：真机窗口驱动、只读取证脚本、探针留档与备份件；`.gitignore` 排除 ⇒ **不入库、不作为事实源、可随手清理**（运维手册按路径引用其历史位置） | [ADR 0012](../adr/0012-workspace-directory-hygiene.md)；[operations.md](../guides/operations.md)「数据目录」与 runbook |
| `alembic.ini` | Alembic CLI 入口（URL 解析见 `migrations/env.py`） | [database-design.md](database-design.md)「迁移序列」 |
| `logs/` | 本项目运行日志与运行时文件（`qed-axiom*`）；**文件数固定 ≤3**、不写外部组件日志，人工守则无代码轮转（ADR 0012 决定 4） | [operations.md](../guides/operations.md)「服务生命周期」；[ADR 0012](../adr/0012-workspace-directory-hygiene.md) |
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
| `web/` | 移交根仓库 QED-Engine；本仓库 `web/` 目录已删除（ADR 0012 决定 5：本仓库不含前端） |

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
