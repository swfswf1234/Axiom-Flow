# 项目状态快照

设计状态：Accepted
实现状态：In Progress
确认状态：暂定
最后更新：2026-09-21
关联代码：无（状态快照，不映射具体模块）
关联测试：无
关联 ADR：[ADR 0001](../adr/0001-v2-exploration-direction.md)、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)、
[ADR 0007](../adr/0007-parse-status-semantics.md)

## 用途

本文件是 Axiom-Flow 开发状态的单一事实源入口：进场先读本表，30 秒掌握「项目现在到哪了」。
具体任务状态以[待做任务](todo.md)为准，未来方向以[能力路线图](roadmap.md)为准；本表只保存
「当前实现状态」快照。

**当前版本 v0.1 目标**：解析管理闭环——配置简化（服务/数据与库/解析引擎三段式）→ 基本解析
（引擎适配，默认全本、指定页逐页）→ 分块（blocks → chunks）→ 对照渲染 → 块级人工校验修改，
对齐根仓库第三轮主线 ARCH-020。

## 能力状态

| 能力 | 形态 | 状态 | 说明 |
| --- | --- | --- | --- |
| 8902 HTTP 服务（`/api/v1`） | 8902 | 已服务化 | FastAPI；sync/CRUD/ingest/异步 parse-jobs/pages/manifest/edit/chunks/**versions** 全端点，af_* 持久化；生命周期脚本 `scripts/axiom_flow_service.py`。 |
| 解析管线（ingest/engines/normalize/orchestrator） | Windows 编排层 | 部分验证 | 预处理（150 DPI 页图 + book.json/manifest）→ MinerU 适配（全本默认/逐页重解析）→ 统一 blocks + 质量信号 → 分块 → 落盘 `parsed/` + af_* 回写。**2026-09-20 真机已打通（小样止）**：单页 PNG 提交（真书第 3 页）、5 页合成小样整本提交、失败注入与 kill 续跑四类路径全部落库并产出 blocks/md/chunks/manifest，L1~L8 判据全绿（证据 `data/probes/live-bringup-20260920/`，结论见 PLAN-003「收尾执行登记」）；**2026-09-21 真机链路固化为冒烟用例** `tests/smoke/test_live_mineru_chain.py`（默认门禁 skip，`QED_SMOKE_LIVE=1` 真机绿），DEFECT-002 随之关闭。**遗留缺陷**：~~bbox 坐标空间误判~~ **2026-09-21 已修复关闭**（DEFECT-004：适配器以 `PageResult.bbox_space` 声明 0–1000 归一化空间，`scale_bbox` 按空间换算，真机锚点复验 5/5 块 IoU 0.9314 / 偏移 ≤7px；修复前落盘的 `parsed/` 产物坐标仍是错的，需重解析）；剩余缺陷：~~块类型字面值缺口（真机 `header`/`footer` 不在映射表 → 页眉页脚块静默丢弃）~~ **2026-09-21 已修复关闭**（DEFECT-005：新旧字面值并存入 `_TYPE_MAP`，未覆盖字面值以 `QualityMetrics.dropped_block_count`/`dropped_types` **可见丢弃**计数，`index` 只在存活块连续编号；11 例单元两轮红→绿（映射与计数 6、整页回退收紧 1）+ 冒烟零丢弃对账断言，真机复跑待 MinerU 起服务）；两项修复都不回溯历史产物，`title`/`equation`/`table` 的真实类型分布仍待代表书取证（ARCH-009） |
| 模型与引擎对接 | 引擎适配器 | 已实现 | **解析直连本地模型服务**（`QED_OCR_MODEL_URL` 5002，不经 8900 网关）；MinerU 落地，PaddleOCR-VL/qwen-vl 预留，槽位由 `engines/factory.py` 显式分派（未实现即 400，不自动兜底）；模型生命周期与调参归 QED-Engine（[model-integration](../design/model-integration.md)，[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)）。 |
| 模型生命周期 | 根仓库 8900 托管 | 已就绪 | 模型文件、启停、探针、资源互斥与调参归 QED-Engine（`scripts/image-model/`、`/models`）；本仓库只消费模型服务端点。 |
| orchestrator 任务编排 | af_parse_jobs + af_pages | 部分实现 | 全本（pages 缺省）/逐页；单页失败不中断。**2026-09-20 PLAN-003 W2 代码侧修复**：每页成功/失败即 `commit`（产物写先于页行提交）、`progress` 按 ≥1s 或每 10 页节流、终态收尾必写。**2026-09-21 真机已证**：运行中 `progress.parsed` 单调递增（L4）、失败页不中断任务（L5）、kill 服务后按「无行 ∪ failed」续跑且前序页不重跑（L6，真 MySQL `qed`）。**MySQL 级提交边界回归网已就位（DEFECT-001 关闭）**：`tests/support/mysql.py` + `tests/integration/test_parse_persistence.py`（[ADR 0005](../adr/0005-mysql-integration-test-isolation.md) Accepted，`QED_TEST_MYSQL=1` 才连库、默认整层 skip）以双 Session 证明「flush 未提交对读侧不可见」与逐页提交序，并以 `persist_per_page=False` 反向判据自证有效性。**2026-09-21 状态语义收口（[ADR 0007](../adr/0007-parse-status-semantics.md) / PLAN-006；DEFECT-006/007 关闭）**：`JobStatus` 六态 `queued/running/completed/partial/failed/interrupted`——`completed` 与版本激活共用 `_version_is_complete()` 单判据（「`completed` 但未激活」结构上不可能）、有产出未齐备为 `partial`（旧契约在此只报 `completed`）、失败页结构化落 `af_parse_jobs.failed_pages`（整型数组、**不截断**，`error` 退回人类摘要）；书级 `parse_status` 收缩为纯进度（`pending`/`parsing`）、结局改由新列 `last_parse_result` 表达；`interrupted` **只由启动期归一产出**（`reconcile_on_startup()` 接在 lifespan 受理首个请求之前，单实例前提见 [operations.md](../guides/operations.md)）。**仍开放（真机侧，随下次获批窗口）**：业务库 `qed` 未 apply `20260921_0001`、孤儿行 `5f43593a12cc` 未归一、语义未经冒烟复跑（ARCH-003）。**单一占用闸门（ARCH-017，2026-09-21 已关闭）**：`run_parse_job` 在 `health()` 与 `mark_running()` **之前**调 `_await_model_idle()`（`engine.model_load()` 读 MinerU `/health` 的 `queued_tasks/processing_tasks`，5s 复探 / 900s 截止）——本机 GPU 不敷并行解析，服务侧并发许可不替代调用方自我排他；等待期保持 `queued`（`started_at` 为空、零页行、不激活），超时文案 `模型服务被其他解析任务占用` 与 `模型服务不可用` **分列**，`model_load()` 返回 `None` 按可用放行（不臆造阻塞） |
| af_* 书目同步与块编辑 | MySQL `qed` 库 af_* 私有四表 | 已实现（真机已迁移版本布局并首次激活） | 四表一次迁移（`src/axiom_flow/migrations/`）+ 仓储层；sync 幂等、edit upsert 覆盖；MySQL 隔离测试网见 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 内只 DROP 自有 `af_*` + `alembic_version`，`qed` 只读未触达）。**2026-09-21 PLAN-004 实施**：迁移 `20260920_0002`（`af_books.active_job_id` 生效指针 + `af_pages` 主键与 `af_block_edits` 唯一键纳入 `job_id` + `af_parse_jobs.source_sha256/page_count` + `engine` 死默认值 DROP）与仓储/编排/API 读取面全部落地，S1~S6 在 SQLite + `tmp_path` 上取证。**真机 B→C→D 单窗口已于 2026-09-21 执行完毕**：`alembic_version = 20260920_0002`、D-1 re-ingest 486 页并作废 39 行 legacy（现 0 行）、D-2 单次重解析 20/20 后 `active_job_id = 86ab56ae2b6c`（**真机首次激活**）。**迁移 `20260921_0001`（PLAN-006 / ADR 0007：`af_books.last_parse_result` + `af_parse_jobs.failed_pages`）已落地并做 SQLite 5 例 + 真 MySQL `qed_test` 升降级往返；真机 `qed` 尚未 apply（D 类，等下次获批窗口）** |
| 分块（blocks → chunks） | `chunks.json` + `GET /chunks` | 已实现（真机抽样取证完成） | 派生文件、不入库、可重建；参数（max_chars/overlap_blocks）可经端点重算。**REQ-080 归属约定成文并经真机验证**：`chunks.json` 每个解析版本一份、落 `versions/<job_id>/`（真机 `versions/86ab56ae2b6c/chunks.json` 存在），`GET /chunks` 读生效版本，**根视图不含 `chunks.json`**（实测 `root_view_has_no_chunks=True`）。**2026-09-21 抽样完整性取证（PLAN-002 标准 7）**：`scripts/tmp/live_check_chunk_formula_integrity.py` 对生效版本 20 页 / 159 原始块 / 17 chunk 跑四项判据全 PASS——18 个 formula 块按 `$$…$$`/`$…$` 规则整块出现在 chunk 文本中、逐 chunk `$` 定界符平衡、`block_refs` 零悬空、非噪声块 100% 覆盖；**反向判据**在临时副本上人为腰斩/悬空 ref/整删 chunk 全部变红；留档 `data/probes/chunk-integrity-20260921/C1_chunks.json`。规则本身由 `tests/unit/test_chunking.py::test_formula_and_table_never_split` 在默认门禁常驻守护 |
| 套1 代表书验收 | 教程 Rudin 中译 + 习题集吉米多维奇 | 待验证 | ARCH-009；分阶段验证（前 20 页冒烟 → 全量），需 MinerU 5002 在线。 |

## 当前主线

- **本轮主攻（全库唯一，2026-09-21 更新）**：**待指派**——PLAN-006 解析状态语义收口已关闭
  （[ADR 0007](../adr/0007-parse-status-semantics.md) 决定 1~7 全部实施，DEFECT-006/007 随之关闭，
  ARCH-003 的裁决依赖解除），本方**无可自行推进的本地主线**：真机侧欠账为 PLAN-003 的 D3 删除演练
  （需用户批准）与 ARCH-003 冒烟复跑（需 MinerU 在线窗口，同窗口顺带 apply `20260921_0001`）；
  前端侧为 ARCH-005/006/009（需 8903 排期与代表书重解析）；REQ-086 的数据来源四条缺口本轮已具备，
  剩 jobs 列表与页级失败清单两个端点须另行立项；跨项目侧全部转为**等根侧读取/确认**
  （回执批次 `95e1d37` + 欠账回收批次）。
- **已关闭（2026-09-21）**：**PLAN-006 解析状态语义收口**（任务六态 + `failed_pages` + 进度/结局分列
  + 启动期归一 + 迁移 `20260921_0001`；壳按 `Delete` 删除，回溯锚点 `9034a19`，S1~S7 逐条判据入
  completed.md）。
- **已关闭（2026-09-21）**：**PLAN-005 台账与计划承载契约治理**（[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)
  转 Accepted，`standards/`、todo 新契约、三个技能、治理契约测试与存量行压缩全部落地；计划壳按
  `Delete` 判定删除，回溯锚点 `b42540b`，证据入 completed.md）。
- **v2 探索纪元**：Axiom-Flow 为 QED-Engine 后端解析组件（[ADR 0001](../adr/0001-v2-exploration-direction.md)），
  推倒 v1 重建，聚焦解析与渲染数据供给。
- **ARCH-020 重构（2026-09-14）**：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)
  确立模型生命周期归 8900、解析管线归本仓库并直连模型服务；af_* 重定义为四表；本仓库文档
  体系（AGENTS/architecture/design/trackers/guides）按新形态重构。
- **已交付**：WSL 容器基建（V2-001）、新包结构与 schemas（V2-002）、ingest（V2-003）、
  qwen-vl 最小闭环（V2-010）、生命周期脚本（V2-011/012）、模型模式与 MinerU 移交（V2-014）、
  文档体系范本与治理对齐（V2-015/017）。
- **进行中**：**PLAN-003 解析真机打通 + 存储收尾**（承接 PLAN-002 Step 3 的联调与缺陷修复）。W0 冻结探测完成
  （M1~M4 全 PASS，MinerU 3.4.4 端点契约与留档证据见计划正文），W1 引擎适配器三段化、W2 页级
  持久化、W4 引擎显式分派、W5 提交期 503 探活、W6 数据根边界与 ingest/purge 幂等**代码侧完成**
  （285 → 283 passed + ruff 全绿，W7 删除重复事实源用例 2 例）、W7 文档自洽与条文晋升已完成
  （`model-integration`/`parsing-pipeline`/`parsing-management` + `api`/`database-design` 五处成文，
  ADR 0004 正文端口修正）。**2026-09-20 收尾裁决**：执行面收窄为 C1 真机打通（**小样止**：单页 +
  5 页合成全本 + 真书单页回归）、C2 数据库存储收口（W3 走路 B，先评审 ADR 0005）、C3 数据根收口
  （`tmp/axiom-flow/` 改判为设计表述错误 → 条文 12「不写即合规」，零代码）；判据清单与工作项重排见
  计划「收尾执行设计」一节。**2026-09-20 收尾轮真机取证完成 C1**：L1~L8 判据全绿（小样止），
  整本与逐页两条路径、进度递增、失败注入、kill 续跑、只读端点、提交期 503 全部在 5002 + 真
  MySQL + 真实数据根上成立；同时暴露 bbox 坐标空间与块类型映射两处实质缺陷（DEFECT-004/005）与
  三项未成文语义（DEFECT-006/007 + 幽灵行清理待批）。**2026-09-21 收尾二轮**：W8 全部完成（8.1 冒烟用例真机绿、
8.4/W8-g 跨项目回执稿成文）→ **DEFECT-002、REQ-075 关闭**；C3 数据根收口面**全部达成并关闭**（D4 凭据零命中 +
仅 `af_*` 四表可触达；G5 三段走查证明生效层恒为本仓库 `.env`），并新登 DEFECT-008（`.env` 行内注释不剥离）。
PLAN-003 剩余全部是外部或裁决依赖：C2 的 D3（真机删除演练需批准）与 D5/W3（ADR 0005 评审）、REQ-001 等根仓库
回执、DEFECT-004~008 修复。外部依赖仅剩 ADR 0005 评审。**2026-09-21 三轮开工**：**DEFECT-004 修复并关闭**
（`PageResult.bbox_space` 坐标空间契约 + `scale_bbox` 按空间换算；9 例单测先红后绿、真机锚点复验 5/5 块平均
IoU 0.9314 / 最大偏移 7px、冒烟加「块与 PDF 原生文字 IoU ≥ 0.5」判据且反转坐标空间即红）→ 根仓库 **REQ-081**
（前端叠框失真，同一缺陷）回执成文，ARCH-005/006 的坐标前置解除、仅剩 DEFECT-005；门禁 283 → 292 passed。
受害页真机重解析（写真实 `dataset/parsed/` + 共享 `qed`）属 D 类，待用户批准。**2026-09-21 四轮开工**：
**DEFECT-005 修复并关闭**（`header`/`footer` 与旧版 `page_header`/`page_footer` 并存入 `_TYPE_MAP`；
`normalize_page` 逐条对账，`QualityMetrics` 新增 `dropped_block_count`/`dropped_types` 使丢弃可见，
`index` 只在存活块连续编号；新增 11 例单元（两轮红→绿：映射与计数 `6 failed`、整页回退条件收紧 `1 failed`）
+ 冒烟加「原始项 = 存活块 + 丢弃」对账断言）→
归一化两处正确性缺陷全清，ARCH-005/006/009 与 PLAN-004 的归一化前置解除；门禁 292 → 303 passed +
契约 82 + ruff 全绿。真机零丢弃复跑待 MinerU 5002 起服务（当前 `/health` 连接拒绝）。
- **进行中（接上一条）**：**PLAN-004 解析产物按版本划分（承接根仓库 REQ-080）已由挂起转实施**——2026-09-21
  审核判定其原 `Blocked` 属**过度阻塞**：计划自身的裁决 4 已声明不依赖真机模型服务，而 Alembic 升降级用例
  跑在 SQLite 临时库、编排/契约测试跑在 fake engine + 临时数据根，且应用**不自动迁移**共享 `qed` 库
  （迁移是运维命令），故 P2~P6 的开发与门禁取证本就不需要等 PLAN-003 的 C2。真机面则合并为**单一批准窗口**
  B→C→D：本地全绿 → 一个窗口内 `af_*` 备份 + `alembic upgrade 0002` + 发布（手工清 `af_pages` 幽灵行
  已取消，由 D 步 re-ingest 作废 legacy 行） →
  在版本布局上**单次**重解析 b01 前 20 页（同时承载 REQ-081 的根侧 §13 复测前提与 PLAN-003 W8 取证）；
  不在旧扁平布局重复真机解析，否则迁移后那批页行会被回填成 `legacy` 哨兵而立即作废。REQ-080 优先级同时对齐
  根仓库（高）。**2026-09-21 五轮（实施完成，本地闭环）**：P2~P7 全部落地——迁移 0002 + 仓储版本作用域、
  `versions/<job_id>/` 写时复制播种（`seed_version_from`，页图硬链接）、终态激活门槛、`api/` 读取面单一入口
  `_read_scope`（URL 与 `image_url` 字面量零变化）、`GET /books/{id}/versions`、ingest 只交换根视图并保历史版本、
  `engine` 死默认值随迁移 DROP（销 PLAN-003「W7 发现 2」）。门禁 **303 → 352 passed, 2 skipped**（契约 82 → 94）
  + ruff 全绿；S1~S6 取证矩阵与实现口径见计划「实施登记」，REQ-080 回执稿与待根确认清单见同计划「实施回执稿」节。
  **真机面为 C/D 单窗口**（迁移 + re-ingest + 单次重解析），2026-09-21 已获原则批准，执行前逐步确认写操作。
- **2026-09-21 六轮（R0 真机窗口前置批次，本地闭环）**：不需真机即可收掉的入站项与窗口前置项一次性做完
  （承载 [PLAN-003](../plans/2026-09-20-parse-live-bringup.md)「承载 REQ-083 / DEFECT-008 / DEFECT-006」节）：
  **REQ-083**（`file_path` 段规则白名单→字符黑名单，全角标点 `（）``：` 不再整批 422，遍历/绝对/盘符仍拦）、
  **DEFECT-008**（`.env` 值尾行内 `#` 注释剥离，引号内 `#` 不误伤）、**DEFECT-006 的 `pages` 越界 422**
  （受理前门槛、判在探活前、不创建任务行）、**`scripts/tmp/live_reparse_b01_pages.py` 重写**为版本布局
  HTTP 驱动（部署自检 + `--check-only` 只读预检 + 越界 422 复验 + 轮询 `/health` 至模型就绪）、
  **operations.md C/D runbook 修订**（C-5 手工删幽灵行取消、新增 C-7 预检、「模型就绪不以 8900 一次提示为准」）、
  **REQ-001 改判**（根侧承载行 `PLAN-044`/`ARCH-020-D`，避开与根仓库同号不同事的 `REQ-001`）与新登出站请求
  **REQ-084**（8900 启动「假失败」误报：前端 15s 轮询超时 vs vLLM 分钟级加载）。门禁 **352 → 366 passed, 2 skipped**
  + ruff 全绿；**本节不含任何真机结果**，REQ-083/080/081 的正式回执随 C/D 窗口发出。
- **2026-09-21 七轮（ADR 0005 接受 + W3 落库回归网，本地闭环）**：[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)
  由 Draft 转 **Accepted**，评审阶段发现草案的「`qed_test` 整库自建自删」会毁掉根仓库把 `qed_test` 当作 `qed`
  13 表镜像的既有用法，按用户裁决改判为**只 DROP 本仓库自有的 `af_*` 四表 + `alembic_version`、绝不
  `DROP DATABASE`**（夹具逐名断言、异常文本不回显 DSN）。执行面落地为 `tests/support/mysql.py`（`QED_TEST_MYSQL=1`
  显式 opt-in、连接后硬校验 `SELECT DATABASE()`、误连业务库即 `pytest.exit`、数据根仍 `tmp_path`）与
  `tests/integration/test_parse_persistence.py` 8 例（双 `sessionmaker` 提交边界、逐页提交序 `[0,1,2]`、
  进度单调、失败页累积行已落库、迁移建表恰为自有集合；反向判据 `persist_per_page=False` → 快照 `[0,0]`）；
  `standards/testing.md` 隔离铁律补「唯一例外」条，`development.md` 加执行时机口径（改 `orchestrator/`/`db/`
  提交语义必须跑该层）。门禁 **366 passed, 2 → 10 skipped**（默认零外部依赖不变）+ ruff 全绿；
  **DEFECT-001 随之关闭**（用户 2026-09-20 裁决的关闭条件——ADR 0005 通过 + 双 Session 回归网——已达成）。
  边界验证：真机 `qed` 表结构与 `20260917_0001` 未变，`qed_test` 的 `qt_*` 镜像表零损。
- **2026-09-21 八轮（R1 真机窗口：C 迁移 + D-1 re-ingest + D-2 排他重跑，ARCH-017 落地）**：
  ① **ARCH-017 单一占用闸门**（用户指令「保证只有一个 mineru 服务进行解析，不要并行因为资源不够，先保证
  这个然后再重跑」）——`ParseEngine` 协议新增 `model_load() -> ModelLoad | None`、`pipeline._await_model_idle()`
  接在探活与 `mark_running` 之前、占用与不可用两种文案分列；红灯先行 6 + 4 例，门禁 **366 → 376 passed,
  10 skipped** + `ruff check src tests scripts` 全绿 + `QED_TEST_MYSQL=1` 层 `8 passed`。
  ② **C 段真机迁移**执行成功：`af_*` 四表备份（17/10/39/1 行）→ `alembic upgrade 20260920_0002` → 六项校验
  全绿（上一轮「`qed` 结构与 `20260917_0001` 未变」的边界到此**已过时**，现 `alembic_version = 20260920_0002`）。
  ③ **D-1 re-ingest**：真实数据根 `parsed/linear_algebra/linearalgebra-b01/` 补齐 486 张页图，39 行 legacy
  （含第 999 页幽灵行）整批作废为 **0 行**——证明 PLAN-004 取消手工 DELETE（C-5）的判据成立；`pages` 越界
  探针在 ingest 后转 `[491]/[0]/[1,487]` 全 **422**、任务行增量 0（DEFECT-006 受理前门槛真机自证）。
  ④ **D-2 单次重解析**（首次尝试因根侧并发提交 + vision 容器被重启而失败，且被误记成「模型服务不可用」→
  正是闸门的实证）：排他自证后 **job `86ab56ae2b6c` 20/20 页、52 秒完成**，`active_job_id` 置位为**真机首次
  激活**，`versions/86ab56ae2b6c/` = `book.json`+`chunks.json`+`manifest.json`+526 个页级文件（486 页图硬链接
  `st_ino` 一致、根视图无 `chunks.json`）、**丢弃 0**、`completed_tasks` 1→21 自证逐页真打模型。
  ⑤ 台账：`ARCH-017`（闸门 + D-2 达成）与 `ARCH-004`（版本目录 chunks）**关闭**，出站请求
  `REQ-085`（8900 不得在 8902 有在飞任务时重启 vision 槽位）新登；`DEFECT-007` 取证更新（孤儿
  `running` 行 `5f43593a12cc` 空闲 106683 秒 ⇒ 窗口排他改用模型侧计数判据）。
  ⑥ 窗口脚本两处缺陷同轮修（页端点信封误用 → 新增 `--evidence-only` 免「为补证据再解析一次」；
  系统代理劫持回环令 5002/8902 全变 502 → 统一 `httpx.Client(trust_env=False)`），并把「本地流量恒直连」
  写进 `operations.md` 的 C-0 前置。证据 `data/probes/live-window-20260921/`（`S1c_exclusive.json` 为排他自证）。
  **未包含**：任何回执发出（REQ-080/081/083 当轮未发 → 见下条）、对照/块编辑前端联调（ARCH-005/006）、代表书验收（ARCH-009）。
- **九轮（2026-09-21：跨项目回执批次发出，R1 窗口的对外收口）**：把 R1 取证转成对根仓库的正式材料，
  承载 PLAN-003「跨项目回执发出（2026-09-21，R1 窗口后）」节。锚点 commit `95e1d37`（门禁
  `376 passed, 10 skipped` + ruff 全绿 + 契约 96 + `QED_TEST_MYSQL=1 tests/integration` 8 passed）。
  ① **REQ-080 实施回执**：版本布局 + 生效指针 + 迁移 `20260920_0002` + 只读 `/versions`，8900/前端零改造；
  随件请根侧确认**四条**（根目录字面措辞差异、是否要版本切换/回滚端点、编辑不随版本继承、旧版本清理另立项）。
  ② **REQ-081 二次回执**：承诺的三步（版本布局 → 迁移 → 单次重解析）已全部达成，**根侧 §13 复测前提成立**，
  可复测产物 = 生效版本 `86ab56ae2b6c` 的第 1~20 页（含第 7 页），坐标/类型/`index` 可信、丢弃 0、
  仍有约 7px 系统性左偏；旧代际产物与 39 行 legacy 已作废。③ **REQ-083 回执**：`file_path` 段规则改黑名单式，
  全角标点放行、遍历/盘符仍拦，根侧可撤销 09-20 的临时改名（改名范围决定是否需重新 ingest）。
  ④ **出站转达稿两条**：REQ-084（8900 以固定 15s 超时判启动失败 = 假失败，建议改轮询 `:5002/health` 至 200）、
  REQ-085（8900 不得在 8902 有在飞任务时重启/换起 vision 槽位，R1 实证并发重启令两任务同时假死）。
  ⑤ REQ-001 的延后项②（版本布局二次回执）随之解除，本方侧无待办、等根侧 PLAN-044/ARCH-020-D 登记。
- **十轮（2026-09-21：跨项目欠账回收，A 类零代码）**：审计根侧台账后发现在**等我方动作而我方从未承接**
  的四条，全部按 PLAN-003「承载 REQ-036/057/074」节 X1~X4 核对成文并回执。① **REQ-036 正式承接**：
  V2-003 误建代码早已于 2026-08-20 审阅提交，V2-004/005/007（orchestrator、MinerU 三段端点、
  15 条 `/api/v1/*` 路由 + 契约测试）**全部交付**——**根侧 REQ-034 的「等 8902 API 建立」前置据此解除**；
  ② **REQ-057** 本仓库 [ADR 0002](../adr/0002-pending-design-location.md) 实为 **Accepted 2026-08-23** 且持续执行
  （PLAN-003 条文草案、PLAN-004 V1~V10 即其产物），根侧「未审阅」一行已过时；③ **REQ-074** 按用户裁决以
  「**等价覆盖，不新增同义文件**」回执（本仓库走 ADR 0003 同源不同名路线），三条差异同步声明，其中
  唯一实质不等价处——`src/` 26 模块未普遍采用根侧三段文件头——**未获裁决不实施**，作为 (A) 补齐＋硬化契约
  / (B) 接受现状并写入 `doc-governance.md` 两项处置挂入 ARCH-010 长期行；④ **REQ-075 点名**其回执正文位置
  ⇒ 根侧 **ARCH-020-E 的「前置阻塞 REQ-075」可解除**。另新登 **REQ-086**（根侧 ARCH-020-E 的页级失败清单与
  jobs 列表数据源需求）：本轮**只登记不实施**——`api.md` 是 8900 透传的契约事实源，须先裁 DEFECT-006/007
  的语义（`parse_status` 一字段两用、孤儿 `running`）再开端点，否则把错误语义冻结给前端。
  REQ-036/057/074 三条关闭行入 completed.md（结果 Achieved/Achieved/**Partial**）。
- **十一轮（2026-09-21：v0.1 主线审计与就地收尾）**：主线 15 行逐条对表「关闭条件 vs 已有证据」，
  结论是**只有两行可收**，其余全卡在根侧、用户裁决或真机窗口。① **PLAN-002 关闭（Achieved，Retain
  归档）**：8 条标准逐条判定，标准 6 改判达成（其两项成因 DEFECT-001/002 已关闭 + R1 首次激活），
  标准 7 **就地补做真机取证**（生效版本 20 页 / 159 块 / 17 chunk 四项判据全 PASS，18 公式块零腰斩，
  反向判据四色齐红，脚本 `scripts/tmp/live_check_chunk_formula_integrity.py`），标准 8 判「本方侧发出」；
  **与计划旧口径的偏差已改判**——不再绑「随 PLAN-003 关闭」，因 PLAN-003 受 D3/裁决长期开放；
  壳移至 `docs/history/plans/2026-09/`（新建该月索引）。② **ARCH-016 关闭（Achieved，去重）**：
  零丢弃复跑达成（20/20 页 `dropped_block_count=0`），其「代表书类型分布取证」与 ARCH-009 关闭条件
  逐字重复 ⇒ 移交 ARCH-009，并把**已知缺口**写进 ARCH-009 判据（真机样本仅出现
  `paragraph/page_number/formula/footer/caption`，**`title`/`table` 未经真机验证**）。③ **DEFECT-006 行
  收窄**：`pages` 越界 422 子项（已实施 + 真机复验 + 已成文）自本行剔除，行只剩「`completed` 掩盖部分
  失败」与「书级 `parse_status` 拆分」两个待裁决语义。门禁 `376 passed, 10 skipped` + ruff 全绿。
- **十二轮（2026-09-21：PLAN-006 解析状态语义收口，本地闭环）**：把用户 2026-09-21 的三项裁决
  （A1 三态 + `failed_pages`、B1 进度/结局分列、C1 启动期归一）落成代码 + 迁移 + 成文契约，承载
  [ADR 0007](../adr/0007-parse-status-semantics.md)。① `schemas.JobStatus` 六态、`ParseStatus` 收缩为
  `pending`/`parsing`、新枚举 `LastParseResult`；② `pipeline` 的 `completed` 与版本激活**共用同一判据**
  （`_version_is_complete()`），故「`completed` 但未激活」不可构造，有产出未齐备落 `partial`；
  ③ `af_parse_jobs.failed_pages`（JSON 数组、不截断）与 `af_books.last_parse_result` 两列由迁移
  `20260921_0001` 加入，回填只做**平移**且**不改写历史任务 `status`**（反向判据逐行断言）；
  MySQL 8 拒绝 JSON 字面 DEFAULT、SQLite 拒绝无默认加 NOT NULL 列 ⇒ 用「可空加列 → `UPDATE` 回填 →
  `batch_alter_table` 收紧」顺序解决，不做方言分支；④ `JobRunner.reconcile_on_startup()` 接在 lifespan
  `yield` **之前**（先于受理首个请求），`interrupted` 的唯一写入者，单实例前提与租约缺口写入
  `operations.md`；⑤ 契约面：`api.md` 新增六态状态机表与两条错误语义、`BookOut.last_parse_result`、
  `ParseJob.failed_pages`。TDD 全程先红后绿（`TestStatusSemanticsAdr0007`、`TestParseStatusSemantics`、
  `TestStartupReconciliation` 等），门禁 **376 → 405 passed, 10 skipped** + ruff 全绿 +
  `QED_TEST_MYSQL=1` 层 `8 passed`（含 `qed_test` 上 `20260921_0001` 升降级往返）。**关闭判据不含真机**：
  真机 `qed` 未 apply 新迁移、孤儿行未归一、冒烟未复跑 ⇒ 三条留 ARCH-003（转 Blocked）与下次窗口。
- **已关闭（2026-09-20）**：ARCH-013 af_* 四表建表与仓储层、ARCH-014 书目同步与 CRUD、
  ARCH-015 ingest 与 `QED_DATA_ROOT/parsed/` 布局（真机迁移 + 486 页 E2E 证据见
  [completed.md](completed.md)）。
- **待启动**：ARCH-009 套1 代表书验收（前置 DEFECT-002/004/005 与串行占用 ARCH-017 已全部关闭，版本布局
  真机可用；剩余为 MinerU 在线 + 在**排他窗口**内重解析代表书 + 块类型真实分布取证）；
  ARCH-005/006 对照与块编辑的 8903 前端联调；REQ-001 请求 8900 适配新契约
  （`/review`→`/edit`、ingest/chunks 透传，根仓库侧 2026-09-20 已自修四处断裂，本方复核材料 2026-09-21
  已出；根侧承载行 2026-09-21 改判为 `PLAN-044`/`ARCH-020-D`，等其登记后关闭）；
  **回执批次已发出（`95e1d37`）**：REQ-080 等根侧确认四条、REQ-081 等根侧 §13 复测、REQ-083 等根侧撤销改名，
  REQ-084/085 待经用户转达根侧登记。
- **长期**：ARCH-010 文档体系对齐维护；ARCH-011 批量解析；ARCH-012 探索路线图。

## 维护规则

- 服务实现状态、当前主线或能力归属变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 [todo.md](todo.md) / [roadmap.md](roadmap.md)）。
