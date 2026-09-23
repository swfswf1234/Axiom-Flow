# 项目状态快照

设计状态：Accepted
实现状态：In Progress
确认状态：暂定
最后更新：2026-09-22
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
| 解析管线（ingest/engines/normalize/orchestrator） | Windows 编排层 | 部分验证 | 预处理（150 DPI 页图 + book.json/manifest）→ MinerU 适配（**全本默认按 56 页窗口串行分块提交**、逐页重解析）→ 统一 blocks + 质量信号 → 分块 → 落盘 `parsed/` + af_* 回写。**2026-09-20 真机已打通（小样止）**：单页 PNG 提交（真书第 3 页）、5 页合成小样整本提交、失败注入与 kill 续跑四类路径全部落库并产出 blocks/md/chunks/manifest，L1~L8 判据全绿（证据 `data/probes/live-bringup-20260920/`，结论见 PLAN-003「收尾执行登记」）；**2026-09-21 真机链路固化为冒烟用例** `tests/smoke/test_live_mineru_chain.py`（默认门禁 skip，`QED_SMOKE_LIVE=1` 真机绿），DEFECT-002 随之关闭。**遗留缺陷**：~~bbox 坐标空间误判~~ **2026-09-21 已修复关闭**（DEFECT-004：适配器以 `PageResult.bbox_space` 声明 0–1000 归一化空间，`scale_bbox` 按空间换算，真机锚点复验 5/5 块 IoU 0.9314 / 偏移 ≤7px；修复前落盘的 `parsed/` 产物坐标仍是错的，需重解析）；剩余缺陷：~~块类型字面值缺口（真机 `header`/`footer` 不在映射表 → 页眉页脚块静默丢弃）~~ **2026-09-21 已修复关闭**（DEFECT-005：新旧字面值并存入 `_TYPE_MAP`，未覆盖字面值以 `QualityMetrics.dropped_block_count`/`dropped_types` **可见丢弃**计数，`index` 只在存活块连续编号；11 例单元两轮红→绿（映射与计数 6、整页回退收紧 1）+ 冒烟零丢弃对账断言，真机复跑待 MinerU 起服务）；两项修复都不回溯历史产物。**2026-09-22 代表书抽查窗口（ARCH-009 口径）类型分布取证**：两本各 20 页抽样内实际出现 8 种 `BlockType`（`paragraph / formula / header / footer / page_number / image / table`，`caption` 仅 b11 1 块），`heading`/`list` 两键从未命中（取证事实、非缺陷），b05 另有 26 块按可见丢弃计数；同轮发现并同日修复第三处归一缺陷（ARCH-018，已关闭）：引擎 `equation` 条目的 `latex` 自带 `$$…$$`，归一层原样存储致下游二次包裹成 `$$$…$$$`（KaTeX 不可渲染）、`display` 恒 False；现由 `split_math_delimiters()` 剥定界符并据其决定 `display`（裸 LaTeX 契约成文于 parsing-pipeline「公式定界符契约」），真机同批 20 页复解析后公式段可解析率 87.79%/91.95% → **100%/100%**。同轮暴露 `ref_text`（参考文献条目）未入 `_TYPE_MAP` ⇒ b05 抽样页 26 块**可见丢弃**，登记为 ARCH-019（映射目标待裁决） |
| 模型与引擎对接 | 引擎适配器 | 已实现 | **解析直连本地模型服务**（`QED_OCR_MODEL_URL` 5002，不经 8900 网关）；MinerU 落地，PaddleOCR-VL/qwen-vl 预留，槽位由 `engines/factory.py` 显式分派（未实现即 400，不自动兜底）；模型生命周期与调参归 QED-Engine（[model-integration](../design/model-integration.md)，[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)）。 |
| 模型生命周期 | 根仓库 8900 托管 | 已就绪 | 模型文件、启停、探针、资源互斥与调参归 QED-Engine（`scripts/image-model/`、`/models`）；本仓库只消费模型服务端点。 |
| orchestrator 任务编排 | af_parse_jobs + af_pages | 已实现（全本真机已证） | 全本（pages 缺省）/逐页；单页失败不中断。**2026-09-20 PLAN-003 W2 代码侧修复**：每页成功/失败即 `commit`（产物写先于页行提交）、`progress` 按 ≥1s 或每 10 页节流、终态收尾必写。**2026-09-21 真机已证**：运行中 `progress.parsed` 单调递增（L4）、失败页不中断任务（L5）、kill 服务后按「无行 ∪ failed」续跑且前序页不重跑（L6，真 MySQL `qed`）。**MySQL 级提交边界回归网已就位（DEFECT-001 关闭）**：`tests/support/mysql.py` + `tests/integration/test_parse_persistence.py`（[ADR 0005](../adr/0005-mysql-integration-test-isolation.md) Accepted，`QED_TEST_MYSQL=1` 才连库、默认整层 skip）以双 Session 证明「flush 未提交对读侧不可见」与逐页提交序，并以 `persist_per_page=False` 反向判据自证有效性。**2026-09-21 状态语义收口（[ADR 0007](../adr/0007-parse-status-semantics.md) / PLAN-006；DEFECT-006/007 关闭）**：`JobStatus` 六态 `queued/running/completed/partial/failed/interrupted`——`completed` 与版本激活共用 `_version_is_complete()` 单判据（「`completed` 但未激活」结构上不可能）、有产出未齐备为 `partial`（旧契约在此只报 `completed`）、失败页结构化落 `af_parse_jobs.failed_pages`（整型数组、**不截断**，`error` 退回人类摘要）；书级 `parse_status` 收缩为纯进度（`pending`/`parsing`）、结局改由新列 `last_parse_result` 表达；`interrupted` **只由启动期归一产出**（`reconcile_on_startup()` 接在 lifespan 受理首个请求之前，单实例前提见 [operations.md](../guides/operations.md)）。**真机侧三条欠账已于 2026-09-22 R2 窗口清零（ARCH-003 关闭）**：业务库 `qed` 升 `20260921_0001`、长期孤儿行 `5f43593a12cc` 由 `reconcile_on_startup()` 归一为 `interrupted`、`QED_SMOKE_LIVE=1 pytest tests/smoke` **2 passed**（取证与「冒烟层默认 skip 致契约漂移」教训见 PLAN-003 R2 窗口 W-A 与 [completed.md](completed.md) ARCH-003 行）。**单一占用闸门（ARCH-017，2026-09-21 已关闭）**：`run_parse_job` 在 `health()` 与 `mark_running()` **之前**调 `_await_model_idle()`（`engine.model_load()` 读 MinerU `/health` 的 `queued_tasks/processing_tasks`，5s 复探 / 900s 截止）——本机 GPU 不敷并行解析，服务侧并发许可不替代调用方自我排他；等待期保持 `queued`（`started_at` 为空、零页行、不激活），超时文案 `模型服务被其他解析任务占用` 与 `模型服务不可用` **分列**，`model_load()` 返回 `None` 按可用放行（不臆造阻塞）。**已知容量限制（2026-09-22 承接登记为入站请求 REQ-086）**：全本（`pages:null`）走**整本单任务**，提交/轮询/取结果共用单一 `ocr_timeout=600s`（`config.py:54`），而 MinerU 侧按 64 页窗口分批推理 ⇒ 约 >60 页的书全本必 `failed` 且**进度恒为 0**；逐页/页区间路径每页各自计时、页级失败不杀 job，不受影响（根侧止血即走此路）。根治＝按窗口分块提交 + deadline 按块计 ⇒ **PLAN-007（2026-09-22 W0~W5 已落地，代码门禁全绿）**：
`_page_windows()` 按 `AXIOM_PARSE_WINDOW_SIZE`（默认 56，真机 W0 实测 56 页 245s 冻结）把全本切成
**串行窗口**，每块一次 `parse_document(start_page, end_page)` 并**独立计 deadline**；块级 `EngineError`
只把该块目标页记 `failed`（原因 `窗口 {a}-{b}：…` 进 `progress.error`/终态 `error`）后继续下一块，
**不做块级重试**（恢复靠同 job 续跑），块间 `health()` 失败即停止提交剩余块。空白页一并合法化：
适配器以 `middle_json.pdf_info` 枚举页 ⇒ 空白页产 `blocks=[]` 的合法页并计 `parsed`，新增
`QualityMetrics.blank_page`（与「解析失败」「映射缺口」三分，映射缺口页必为 false）。契约两条（S1 失败域、
S2 按块进度）+ 单测 18 例常驻，并对「撤掉块级隔离」「取消分块」做过变异转红复验。
**W6 真机达成（2026-09-22 尝试 4，job `652a3e3be33c`）**：b05《数学分析原理》317 页全本一次跑通——
6 个 56 页窗口串行、718 秒 `completed` 并**激活**，317/317 `parsed`、`failed_pages=[]`、进度采样 73 次
单调非零、空白页 `[48, 98, 136, 162, 282]` 计 `parsed` 且带 `blank_page`、`dropped_block_count` 合计 0、
954 文件版本目录完整；业务库 `qed` 与真实 `dataset/parsed/` 前后指纹一致（只落 `qed_test` + 探针数据根）。
前三次尝试的根因经直证更正为环境侧两条（WSL 全局 OOM 与 **WSL 空闲回收发行版**），判据与处置写入
[operations.md](../guides/operations.md)。本行状态据此转「已实现（全本真机已证）」。 |
| af_* 书目同步与块编辑 | MySQL `qed` 库 af_* 私有四表 | 已实现（真机已迁移版本布局并首次激活） | 四表一次迁移（`src/axiom_flow/migrations/`）+ 仓储层；sync 幂等、edit upsert 覆盖；MySQL 隔离测试网见 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 内只 DROP 自有 `af_*` + `alembic_version`，`qed` 只读未触达）。**2026-09-21 PLAN-004 实施**：迁移 `20260920_0002`（`af_books.active_job_id` 生效指针 + `af_pages` 主键与 `af_block_edits` 唯一键纳入 `job_id` + `af_parse_jobs.source_sha256/page_count` + `engine` 死默认值 DROP）与仓储/编排/API 读取面全部落地，S1~S6 在 SQLite + `tmp_path` 上取证。**真机 B→C→D 单窗口已于 2026-09-21 执行完毕**：`alembic_version = 20260920_0002`、D-1 re-ingest 486 页并作废 39 行 legacy（现 0 行）、D-2 单次重解析 20/20 后 `active_job_id = 86ab56ae2b6c`（**真机首次激活**）。**迁移 `20260921_0001`（PLAN-006 / ADR 0007：`af_books.last_parse_result` + `af_parse_jobs.failed_pages`）已落地并做 SQLite 5 例 + 真 MySQL `qed_test` 升降级往返；**2026-09-22 R2 窗口在真机 `qed` 上 apply 完成**（备份先行、`alembic_version` 现为 `20260921_0001`，回滚按 `data/backups/window-20260922/af_backup_20260922.sql` 还原而非有损 downgrade）**。**REQ-080 双侧闭环（2026-09-22）**：根侧四条确认全部接受（不做切换/回滚写入端点、旧版本清理属 D 类另立、人工编辑不随版本继承），版本布局契约自此冻结，PLAN-004 关闭 |
| 分块（blocks → chunks） | `chunks.json` + `GET /chunks` | 已实现（真机抽样取证完成） | 派生文件、不入库、可重建；参数（max_chars/overlap_blocks）可经端点重算。**REQ-080 归属约定成文并经真机验证**：`chunks.json` 每个解析版本一份、落 `versions/<job_id>/`（真机 `versions/86ab56ae2b6c/chunks.json` 存在），`GET /chunks` 读生效版本，**根视图不含 `chunks.json`**（实测 `root_view_has_no_chunks=True`）。**2026-09-21 抽样完整性取证（PLAN-002 标准 7）**：`scripts/tmp/live_check_chunk_formula_integrity.py` 对生效版本 20 页 / 159 原始块 / 17 chunk 跑四项判据全 PASS——18 个 formula 块按 `$$…$$`/`$…$` 规则整块出现在 chunk 文本中、逐 chunk `$` 定界符平衡、`block_refs` 零悬空、非噪声块 100% 覆盖；**反向判据**在临时副本上人为腰斩/悬空 ref/整删 chunk 全部变红；留档 `data/probes/chunk-integrity-20260921/C1_chunks.json`。规则本身由 `tests/unit/test_chunking.py::test_formula_and_table_never_split` 在默认门禁常驻守护。**2026-09-22 R2 窗口复跑**：ARCH-018 修复后两本代表书各 20 页的生效版本 chunk 完整性检查 **PASS**（公式整块出现、溯源无悬空、非噪声块全覆盖）；同轮实证一条**判据边界**：修复前的 b11 版本 `chunks.json` 含 126 处 `$$$` 而四项判据仍 PASS（留档 `data/probes/window-20260922/C8_chunk_prefix_b11.json`）——腰斩判据看块边界与 `$` 奇偶，不看定界符包裹层级，故公式渲染前的定界符契约由 `normalize/` 单测 + 代表书取证把住 |
| 套1 代表书验收 | 教程 Rudin 中译 + 习题集吉米多维奇 | 抽查口径 + 探针根全本已证（ARCH-009/019 已关闭；业务库全量待 ARCH-011） | ARCH-009；**2026-09-22 R2 窗口按用户裁决的抽查口径判定**（每本 20 页＝首 3 + 中 7 + 尾 5 + 公式密集 5；两本 PDF 均无文本层，密集页按**墨度**排序选页）：b05《数学分析原理》317 页 / b11《吉米多维奇》406 页，逐条判定 ①~⑤ PASS、公式段可解析率修复后 **100% / 100%**（判据为程序化结构可解析 + 人工留档，不引入 KaTeX 渲染依赖）、chunk 完整性两版本 PASS、`$$$` 归零；⑥类型分布取证未达成 ⇒ **ARCH-019**（`ref_text` 26 块可见丢弃）。**未做全量**：b05 生效版本是混合版本（20 页新 / 292 页继承修复前基线，跨旧页 chunk 仍有 `$$$` 485/525），其 5 个缺页与 `last_parse_result=failed` 未处理——全量重生已随根侧入站并入 **PLAN-007**（W6 全本复测，D 类逐步批准；原 ARCH-011 批量批次口径不变）。**容量限制已在本轮消除（待真机复测）**：全本单任务的 600s 单墙由 PLAN-007 的按页窗口分块提交根治
（见 orchestrator 行 REQ-086），W0~W7 全部落地并**已随 PLAN-007 关闭（2026-09-22 收口轮）**：**尝试 4 在探针根上把 b05 全本 317 页一次跑通并激活**
（缺页清零、丢弃 0、单一生效版本、空白五页计 `parsed`），故「全量重生按页区间路径执行」的临时口径已不再必要，
ARCH-009（验收）与 ARCH-019（`ref_text` 映射）**同批关闭**；**但业务库 `qed` 与真实 `dataset/parsed/` 未做**
（本轮判据按用户裁决只落 `qed_test` + 探针根），其 b05 生效版本仍是混合版本，业务侧全本重生属另一次
D 类批准（批量为 ARCH-011）。 |

## 当前主线

- **本轮主攻（全库唯一，2026-09-22 PLAN-008 进场轮）**：**实施 PLAN-008**（页级失败清单 + jobs 列表
  两条只读端点，承载 REQ-088 / ARCH-005 / ARCH-006）。进场三项裁决：① 原三条 `In Progress` 支线行
  （ARCH-019、REQ-086、REQ-087）剩余判据**同源等 PLAN-007 W6** ⇒ 一并改判 **`Blocked`**（恢复条件与
  责任位置＝根仓库 8900 / 本机 WSL 网络），使并发上限（支线 `In Progress` ≤3）在 REQ-088 开工后仍成立
  （**同日 W6 落地后该三行 Blocked 已解除**，见下条）；
  ② 主攻位由 PLAN-007 改指本计划；③ jobs 列表**行内补时间戳**（`ParseJob` 字段原样 + `created_at`/
  `started_at`/`finished_at`/`active`）。**进度：W1 契约已成文**进 `architecture/api.md` ③ 类（两节 +
  两张字段表 + 错误语义两行 + 「作用域唯一入口」强制规则改写），剩 W2~W5 实施与契约测试、W6 联调。
- **已关闭（2026-09-22 收口轮）**：**PLAN-007**（全本窗口分块 + 空白页合法化，承载 REQ-086/087、
  ARCH-009/019）S1~S5 逐条判定后关闭，壳按 **Retain** 迁 `docs/history/plans/2026-09/`，五行关闭证据入
  [completed.md](completed.md)。**判据达成**：四项协议假设真机冻结 ⇒ 默认 56 页/块；引擎窗口参数与按块
  deadline、pipeline 串行窗口循环与块级失败隔离、`blank_page`、`ref_text` 映射、契约两条（含两处变异
  转红复验）；W6 尝试 4（job `652a3e3be33c`）b05 317 页 / 6 窗口 / 718 秒 completed 且**激活**、
  `failed_pages=[]`、空白页 `[48, 98, 136, 162, 282]` 与预测逐页一致、丢弃 0、954 文件版本目录、业务侧
  零写入自证（留档 `data/probes/wholebook-req086-20260922/attempt4-green/`）。**交接两项（不再占本仓库
  台账位，用户裁决 09-22 收口轮）**：① REQ-086/087 回执正文成文于壳两承载节，由根仓库自读登记；
  ② **环境侧**（WSL 内存容量、发行版空闲回收、`mineru-api` 容器）归 QED-Engine 图像模型维护线，
  两条根因的鉴别判据与处置命令见 [operations.md](../guides/operations.md)。
- **已关闭（2026-09-22 立项轮）**：**PLAN-003 解析真机打通**（四面判定：契约条文全部晋升
  `design/`+`architecture/`、W0~W8 实现收口、R0/R1/R2 三个真机窗口取证、REQ-080/081/083/084/085 回执
  双侧闭环）。**新入站承接**：根侧 `REQ-087`「空白页合法化」以同号登记（其 00:35 早于本方 00:57），
  本方原端点行作为后占者**再让号为 `REQ-088`**（判例同 `REQ-001`/`REQ-086`）。**ARCH-019 转 `Blocked`**
  （映射目标 (A)/(B)/(C) 待裁决，恢复条件与责任已写入证据列）。
- **已关闭（2026-09-22 R2 窗口）**：**ARCH-003 解析编排语义真机复跑**（业务库升 `20260921_0001`、
  孤儿行归一、冒烟 2 passed）、**ARCH-018 公式定界符缺陷**（`split_math_delimiters`，两本 100.00%）；
  D3 真机删除演练已执行并登记。**新立 ARCH-019**（`ref_text` 映射缺口）。
- **下一轮候选（均需你先定级/批准，本表不自行指派）**：① PLAN-008 的 **W6（8903 前端联调）**排期
  （根侧动作，消费本计划的两个新端点）；② **PLAN-007 已关闭**，其全本复测只落 `qed_test` + 探针根 ⇒
  是否把同一复测在**业务库 `qed` + 真实数据根**上再做一次（代表书 b05 生效版本仍是混合版本，业务侧
  全本重生属 D 类，需另行批准；批量其余书籍为 ARCH-011）；③ PLAN-008 收口后
  把两条新端点补进 `tests/contract/test_architecture_documents.py` 的 ③ 类端点守护清单（W5 已含
  code-map 行，该清单是白名单式包含检查，补入即冻结）。
- **已关闭（2026-09-22）**：**PLAN-004 解析产物按版本划分**（迁移 `20260920_0002` +
  `versions/<job_id>/` + 生效指针 + 只读 `/versions`；根侧四条确认全部接受 ⇒ 同批关 **REQ-080**；
  壳按 **Retain** 移至 `docs/history/plans/2026-09/`）与**六个跨项目行**（**REQ-001/080/081/083/084/085**
  ——根侧逐行回执在案：版本布局四条全接受、§13 双面复测零夹取通过、改名已物理撤销且 sync 零 422、
  BUGFIX-010 采纳启动两段式、以确认约定关闭并发重启请求并写入其设计约束 9/10、PLAN-044 已登记读取
  复核材料）。同轮把本方原 `REQ-086` **改号为 `REQ-087`**（根侧把 086 用给了新入站请求，同号不同事，
  判例同 `REQ-001`）并**承接登记根侧 `REQ-086`**。
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
- **PLAN-004 实施轮（2026-09-21；该计划已于 2026-09-22 关闭、壳按 Retain 归档至 [history/plans/2026-09](../history/plans/2026-09/2026-09-20-req080-parse-version-layout.md)，下文「见计划某节」均指该归档件）**：**解析产物按版本划分（承接根仓库 REQ-080）由挂起转实施**——2026-09-21
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
  （承载 [PLAN-003](../history/plans/2026-09/2026-09-20-parse-live-bringup.md)「承载 REQ-083 / DEFECT-008 / DEFECT-006」节）：
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
  真机 `qed` 未 apply 新迁移、孤儿行未归一、冒烟未复跑 ⇒ 三条留 ARCH-003（转 Blocked）与下次窗口
  （该真机后续**不另立轮次**，随下次获批窗口一并完成）。**2026-09-22 R2 窗口三条全部达成、ARCH-003 据此
  关闭**，其中「冒烟层默认 skip 使 PLAN-006 收口时未跑到」的教训写入 `development.md` 的冒烟层执行时机。
- **十三轮（2026-09-22：跨项目清零 + PLAN-004 关闭 + 编号冲突处置，A 类零代码）**：只做台账与文档
  收口，无实现改动。① **六个跨项目行全关**（REQ-001/080/081/083/084/085）——逐行核对根仓库
  `docs/trackers/`（其 `completed.md` 六行 Achieved + 其 `todo.md` PLAN-044 行登记读取复核材料），
  判据与根侧原文入 PLAN-003「根侧回执已到：六个跨项目行关闭」表，行本体迁 completed.md；
  ② **PLAN-004 关闭并按 Retain 归档**至 `docs/history/plans/2026-09/`（根侧四条确认全部接受 ⇒
  其唯一外部门槛达成；壳内相对链接随移动改指，`20260920_0002` 迁移文件头的事实源路径同批改指，
  `plans/index.md` 登记去处）；③ **编号冲突处置**：根侧 2026-09-21 把 `REQ-086` 用给新入站请求
  「全本解析按窗口分块提交 + 超时按块计」，与本方 09-21 新登的「页级失败清单与 jobs 列表数据源」
  同号不同事（判例同 `REQ-001`）⇒ 本方那行**改号为 `REQ-087`**（PLAN-003 承载节、ADR 0007 两处、
  `operations.md`、todo/project-status 同步），根侧 `REQ-086` 以**同号入站承接登记**，本方核对
  其归因与我方代码事实一致（`config.py:54` `ocr_timeout=600` 单一墙、整本提交/轮询/取结果共用、
  页区间路径不受影响 ⇒ 根侧止血 job `7af72c68180e` 走逐页能跑通），**本轮只登记不实施**（B 类需
  另立计划 + TDD）；④ **跨仓库相对链接清扫**（ARCH-010 遗留项之一）：ADR 0002 两处
  `../../../docs/history/adr/v0.1/0011-*.md`（靠目录嵌套侥幸可解析、逃逸工作区）改仓库 URL，并以
  脚本证明**全库非 history 文档零逃逸链接**。门禁见本轮提交（todo 行有界/承载/镜像契约全绿）。
- **十四轮（2026-09-22：R2 真机窗口三件一次做完 + ARCH-018 修复，PLAN-003 R2 承载节）**：窗口前备份
  `data/backups/window-20260922/af_backup_20260922.sql`（184371 字节 / 5 表 / `20260920_0002`），全程
  C-0 排他自证 + 串行提交 + 工具统一 `trust_env=False`。① **W-A（ARCH-003 关闭）**：`qed` 升
  `20260921_0001` → 重启 8902 → 孤儿行 `5f43593a12cc` 归一为 `interrupted`（`failed_pages=[]`、
  `parse_status=pending`）→ `QED_SMOKE_LIVE=1 pytest tests/smoke` **2 passed**；首跑 `1 failed` 的根因是
  两处断言停在 PLAN-006 之前的旧契约（按 api.md:244 / ADR 0007 对齐测试而非改实现），教训写入
  `development.md`。② **W-B（PLAN-003 D3 演练）**：合成 5 页探针书走完 同步→ingest→解析（job
  `006e244b1966`）→写 1 条编辑→`DELETE /books/{id}`，六项判据全中（页行 5→0、编辑 1→0、书目录消失、
  `GET` 200→404、任务行**保留 1 行供审计**且 `GET /parse-jobs/{id}` 仍 200、`af_books` 18→17、
  `failures: []`），真实书目零改动；登记一条口径备注：删除不裁剪空的父级目录。③ **W-C（ARCH-009 抽查
  口径）**：b05《数学分析原理》+ b11《吉米多维奇》各 20 页（首 3 + 中 7 + 尾 5 + 墨度选公式密集 5，
  两本 PDF 均无文本层故 `live_pick_sample_pages.py` 加墨度模式）→ 取证首跑双红（87.79% / 91.95%，失败
  形态全为页 markdown 的 `$$$…$$$`）⇒ 定位并修复 **ARCH-018**（`split_math_delimiters` 剥引擎自带定界符、
  `display` 改由定界符判定，6 例单元先红后绿）→ 重启后**同一批页**串行重解析（`bbe0d401ce83` /
  `185a37d55985`，均激活）→ **100% / 100%**、`$$$` 126→0、chunk 完整性 PASS；⑥类型分布取证未达成
  ⇒ 新立 **ARCH-019**（`ref_text` 未入 `_TYPE_MAP`，b05 p313/314 丢弃 26 块）。④ 判据边界留档：chunk
  四项判据对修复前含 126 处 `$$$` 的版本仍判 PASS（`C8_chunk_prefix_b11.json`）⇒ 定界符契约不靠该脚本把守。
  门禁 **405 → 411 passed, 10 skipped** + `ruff check src tests scripts` 全绿；**收口轮**把两个显式层
  重跑在 ARCH-018 修复后的代码上（`QED_TEST_MYSQL=1` 集成层 **8 passed**、`QED_SMOKE_LIVE=1` 冒烟层
  **2 passed**，日志 `G1_mysql_layer.log` / `G2_smoke_closeout.log`）——W-A 那次冒烟跑的是修复前实现。**未包含**：b05 整本重解析与 5 个缺页（属 ARCH-011）、任何回执发出（本窗口无对外请求）。
- **十五轮（2026-09-22：立项轮 + PLAN-003 关闭，A 类零代码）**：用户对 PLAN-003 去留裁决取 (A)「先给
  REQ-086/087 另立 B 类计划、ARCH-019 挂过去，再关 PLAN-003（Retain）」，据此做完四件事。① **两份新计划
  立项**：`docs/plans/2026-09-22-req086-whole-book-window.md`（**PLAN-007**，S1~S5 + W0~W7，承载 REQ-086/087、
  ARCH-009/019）与 `docs/plans/2026-09-22-req088-page-jobs-list.md`（**PLAN-008**，S1~S5 + W1~W7，承载 REQ-088、
  ARCH-005/006，归档判定 Delete）；两份均以「已核实的 file:line 事实」为地基（`mineru.py:321` 的
  `deadline = time.monotonic() + self.timeout` **每次调用只算一次** ⇒ 全本单墙、`mineru.py:170-173` 的
  「无结构化产物即 `EngineError`」⇒ 空白页永不过激活门槛、`_resumed_pages` 只认同 `job_id` 页行 ⇒ 跨版本
  续跑不成立是设计后果），**未验证的三条**（窗口页码基准、空白页响应形状、56 页窗口耗时）冻结为 W0 探测
  而非假设。② **承载改挂**：PLAN-003 正文实际挂**六条**活行（先前记载的「三条」经全库 grep 证伪），
  ARCH-009/019 → PLAN-007（共用 b05 全本判据现场）、ARCH-005/006 → PLAN-008（其关闭判据正是消费这两个
  端点的 8903 联调）。③ **二次撞号处置**：根仓库 09-22 00:35 用 `REQ-087` 登记「空白页合法化」，早于本方
  00:57 的端点行 ⇒ 本方端点行**改号 `REQ-088`**、根侧 087 以同号入站承接并归 PLAN-007（其 §② 跨版本续跑
  结论已在承载节作答），撞号时间戳三处留证。④ **PLAN-003 关闭 + Retain 归档**（`git mv` 至
  `docs/history/plans/2026-09/2026-09-20-parse-live-bringup.md`，壳内 43 处相对链接随移动改指，`adr/0007`、
  `trackers/*`、`scripts/tmp/probe_mineru_api.py` 四处引用改指归档位置，月份索引与 `plans/index.md` 登记
  去处）。门禁 **411 passed, 10 skipped** + `ruff check src tests scripts` 全绿（本轮**零代码**，故用例数与
  十四轮收口轮持平；治理契约测试覆盖本批改动的全部台账/计划/链接不变式）。**未包含**：任何实现改动
  （两计划工作项全 `Pending`）、任何真机操作、任何推送。
- **十六轮（2026-09-22：PLAN-007 W6 b05 全本真机复测全绿，D 类探针窗口）**：驱动脚本
  `scripts/tmp/live_wholebook_req086.py` 以**真实环境变量**把库换成 `qed_test`、数据根换成
  `data/probes/wholebook-req086-20260922/root`（不改 `.env`），探针服务起在独立端口 8912（不碰
  `logs/qed-axiom.pid`，业务 8902 全程在位），全流程只经 HTTP 驱动，解析前硬闸门断言作用域到位。
  ① **归因两度更正**：尝试 1/2 的「连续 3 次 `WinError 10054` + 剩余页 `MODEL_UNAVAILABLE`」原记为
  WSL 全局 OOM（`dmesg` 直证，用户据此把 `memory` 抬到 24GB）；尝试 3 见 `RestartCount` **恒为 0**、
  `ExitCode=0`、uvicorn 存活 ≈14 秒、`:5002` 无监听 ⇒ 排除 `--restart` 拉起，进而经 `journalctl` 确诊
  **WSL 空闲回收整个发行版**（最后一个 Linux 进程退出约 15 秒后 `Stopping docker.service` → `final.target`，
  下次 `wsl.exe` 调用重新开机；`vmIdleTimeout=2147483647` 在本机不生效）。**该误判已作废**，处置＝窗口
  全程留一个常驻 WSL 进程，属机器侧配合、**不改任何业务参数**（窗口仍 56、deadline 仍 600s/块）。
  两条判据（OOM 与空闲关机）与鉴别命令一并写入 `operations.md`；该前置随后**落进驱动脚本**
  （`--wsl-holder` 默认 300s 滚动重拉、`0` 关闭留 WARN，`atexit` 收口；回归网
  `tests/unit/test_wholebook_probe_holder.py` 5 例含「常驻命令绝不含 `--terminate`」反向判据）。
  ② **尝试 4 全绿**（job `652a3e3be33c`）：317 页 / 6 窗口 / **718 秒** `completed` 并**激活**，
  页行 317/317 全 `parsed`、`failed_pages=[]`、进度采样 73 次单调非零（+50/+60/+50/+60/+60/+30/+7），
  空白页 `[48, 98, 136, 162, 282]` 与 W0 预测逐页一致且 `blocks=[]` + `blank_page=true`，
  `dropped_block_count` 合计 **0**、字面值列表空（ARCH-019 判据就此达成），版本目录 954 文件
  （blocks.json 317 + md 317 + chunks + manifest）。S1~S5 全部达成。
  ③ **业务侧零写入自证**：窗口前后比对业务库 `qed` 的 b05 行与真实 `dataset/parsed/.../mathanalysis-b05`
  目录（4449 文件 / 945,726,371 字节 / 最新 mtime）逐项一致；ingest 复用同一探针根时跳过重渲染
  （页图 317/317 不产生第二份）。④ **留档 durability 口径**：冒烟/集成层会在 `qed_test` DROP `af_*` 四表
  ⇒ 跑过门禁后 `--evidence-only` 取不到该 job，本窗口结论以 `attempt4-green/` 内 JSON 与 954 文件版本目录
  为准，探针库事后重建到 `20260921_0001`。收口轮门禁 **433 passed, 10 skipped** + ruff 全绿 +
  `QED_TEST_MYSQL=1` 集成层 8 passed + `QED_SMOKE_LIVE=1` 冒烟层 2 passed。**未包含**：业务库全本重生
  （需另一次 D 类批准）、任何推送、PLAN-007 的 W7 收口（等根侧复测确认）。
- **十七轮（2026-09-22：PLAN-007 W7 收口并关闭，五行入关闭台账，A 类零代码）**：用户裁决
  「**wsl 和 mineru 服务不是本项目负责的**，我们只需要做好本项目的事情」⇒ 原关闭条文里的「根侧
  b05/b11 全本复测确认」**移出本仓库判据**：根侧复测与环境侧（WSL 容量、发行版空闲回收、`mineru-api`
  容器生命周期）自本日起由 **QED-Engine 图像模型维护线**负责（ADR 0004 边界），本仓库的诊断与处置**定格
  为交接材料**（`operations.md` 两条根因判据与鉴别命令、壳的 W6 归因链、探针脚本的 `--wsl-holder`），
  后续不再投入环境侧。① **S1~S5 逐条判定后关闭 PLAN-007**，连带关闭 **REQ-086 / REQ-087 / ARCH-009 /
  ARCH-019** 五行（todo 压缩关闭 → completed.md 迁移 → 壳按 **Retain** 迁 `docs/history/plans/2026-09/`
  → `plans/index.md` 与该目录索引登记）。② **v0.1 剩余面收敛为一条**：对照与块编辑联调
  （ARCH-005/006，消费 PLAN-008 的两个只读端点）；ARCH-011 批量解析的前置 ARCH-009 已解除，仍需用户
  排期 + D 类批准。③ **链接与台账同步**：引用旧壳路径处全部改指归档位置（`docs/plans/2026-09-22-req088-page-jobs-list.md`、
  `docs/trackers/*`、PLAN-003 壳）。**门禁（收口轮）**：`pytest tests -q` = **465 passed, 10 skipped**、
  `ruff check src tests scripts` clean、`tests/contract` 114 passed。**未包含**：任何实现改动、业务库
  `qed`/真实数据根全本重生、任何推送、PLAN-008 的工作项（他线）。
- **已关闭（2026-09-20）**：ARCH-013 af_* 四表建表与仓储层、
  ARCH-014 书目同步与 CRUD、
  ARCH-015 ingest 与 `QED_DATA_ROOT/parsed/` 布局（真机迁移 + 486 页 E2E 证据见
  [completed.md](completed.md)）。
- **进行中**：**PLAN-008**（页级失败清单 + jobs 列表两个只读端点，承载 REQ-088、ARCH-005/006；语义前置
  已由 ADR 0007 解除，W1 契约成文达成，剩 W2~W5 实施与契约测试、W6 联调）。**PLAN-007**（全本窗口分块 +
  空白页合法化）W0~W7 全部落地并于 2026-09-22 **收口关闭**（S1~S5 达成、b05 全本真机全绿、壳 Retain
  归档），连带行 ARCH-009/ARCH-019 与 REQ-086/087 同批关闭；REQ-086/087 回执正文成文于壳承载节，由
  根仓库自读登记，**根侧复测与环境侧（WSL/`mineru-api`）归 QED-Engine 图像模型维护线**（用户裁决）。
  业务库口径的全本重生仍属另一次 D 类批准；批量批次仍属 ARCH-011。
  **跨项目侧无待启动项**：六个请求行已于 2026-09-22 全部关闭（含 REQ-001，其承载行改判为根侧
  `PLAN-044`/`ARCH-020-D` 后由根侧登记读取）；新入站 REQ-087（空白页）已承接进 PLAN-007，本方 REQ-088
  已改号入站台账。
- **长期**：ARCH-010 文档体系对齐维护；ARCH-011 批量解析；ARCH-012 探索路线图。

## 维护规则

- 服务实现状态、当前主线或能力归属变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 [todo.md](todo.md) / [roadmap.md](roadmap.md)）。
