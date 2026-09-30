# 项目状态快照

设计状态：Accepted
实现状态：In Progress
确认状态：暂定
最后更新：2026-09-30
关联代码：无（状态快照，不映射具体模块）
关联测试：无
关联 ADR：[ADR 0001](../adr/0001-v2-exploration-direction.md)、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)、
[ADR 0007](../adr/0007-parse-status-semantics.md)

## 用途

本文件是 Axiom-Flow 开发状态的单一事实源入口：进场先读本表，30 秒掌握「项目现在到哪了」。
具体任务状态以[待做任务](todo.md)为准，未来方向以[能力路线图](roadmap.md)为准；本表只保存
「当前实现状态」快照。

**版本口径（2026-09-24 收口）**：**v0.1 开发完成**。v0.1 目标＝解析管理闭环——配置简化（服务/数据与
库/解析引擎三段式）→ 基本解析（引擎适配，默认全本、指定页逐页）→ 分块（blocks → chunks）→ 对照渲染 →
块级人工校验修改（对齐根仓库第三轮主线 ARCH-020），其任务行全部进入终态（逐行判据见
[completed.md](completed.md)，收口批＝PLAN-013，四行终态＝ARCH-005 Achieved／ARCH-006 Partial／PLAN-008／
ARCH-001 聚合行）。**未交付**＝v1.0 线（PLAN-009 只立项
未排期）、重建后代际的块编辑真机写入证据（ARCH-006 判 Partial，移交根仓库下一联调窗口）；
**发布口径（2026-09-24 清理轮）**＝v0.1 已打标签 `v0.1.0` 并把 `release` 合入 `main`（你当日裁决；
仍**未推送**，且包版本双源 `pyproject 0.4.0` vs `__version__ 0.5.0` 未动 ⇒ 标签是**产品版本**坐标，
与包版本不等价，见 `overview.md`「版本与纪元」）。

## 能力状态

| 能力 | 形态 | 状态 | 说明 |
| --- | --- | --- | --- |
| 8902 HTTP 服务（`/api/v1`） | 8902 | 已服务化 | FastAPI；sync/CRUD/ingest/异步 parse-jobs/**jobs 列表**/pages/**页状态清单**/manifest/edit/chunks/**versions** 全端点（18 条公共契约，见 `api.md` 端点总表），af_* 持久化；生命周期脚本 `scripts/axiom_flow_service.py`。 |
| 解析管线（ingest/engines/normalize/orchestrator） | Windows 编排层 | 部分验证 | 预处理（150 DPI 页图 + book.json/manifest）→ MinerU 适配（**全本默认按 56 页窗口串行分块提交**、逐页重解析）→ 统一 blocks + 质量信号 → 分块 → 落盘 `parsed/` + af_* 回写。**2026-09-20 真机已打通（小样止）**：单页 PNG 提交（真书第 3 页）、5 页合成小样整本提交、失败注入与 kill 续跑四类路径全部落库并产出 blocks/md/chunks/manifest，L1~L8 判据全绿（证据 `tmp/probes/live-bringup-20260920/`，结论见 PLAN-003「收尾执行登记」）；**2026-09-21 真机链路固化为冒烟用例** `tests/smoke/test_live_mineru_chain.py`（默认门禁 skip，`QED_SMOKE_LIVE=1` 真机绿），DEFECT-002 随之关闭。**遗留缺陷**：~~bbox 坐标空间误判~~ **2026-09-21 已修复关闭**（DEFECT-004：适配器以 `PageResult.bbox_space` 声明 0–1000 归一化空间，`scale_bbox` 按空间换算，真机锚点复验 5/5 块 IoU 0.9314 / 偏移 ≤7px；修复前落盘的 `parsed/` 产物坐标仍是错的，需重解析）；剩余缺陷：~~块类型字面值缺口（真机 `header`/`footer` 不在映射表 → 页眉页脚块静默丢弃）~~ **2026-09-21 已修复关闭**（DEFECT-005：新旧字面值并存入 `_TYPE_MAP`，未覆盖字面值以 `QualityMetrics.dropped_block_count`/`dropped_types` **可见丢弃**计数，`index` 只在存活块连续编号；11 例单元两轮红→绿（映射与计数 6、整页回退收紧 1）+ 冒烟零丢弃对账断言，真机复跑待 MinerU 起服务）；两项修复都不回溯历史产物。**2026-09-22 代表书抽查窗口（ARCH-009 口径）类型分布取证**：两本各 20 页抽样内实际出现 8 种 `BlockType`（`paragraph / formula / header / footer / page_number / image / table`，`caption` 仅 b11 1 块），`heading`/`list` 两键从未命中（取证事实、非缺陷），b05 另有 26 块按可见丢弃计数；同轮发现并同日修复第三处归一缺陷（ARCH-018，已关闭）：引擎 `equation` 条目的 `latex` 自带 `$$…$$`，归一层原样存储致下游二次包裹成 `$$$…$$$`（KaTeX 不可渲染）、`display` 恒 False；现由 `split_math_delimiters()` 剥定界符并据其决定 `display`（裸 LaTeX 契约成文于 parsing-pipeline「公式定界符契约」），真机同批 20 页复解析后公式段可解析率 87.79%/91.95% → **100%/100%**。同轮暴露 `ref_text`（参考文献条目）未入 `_TYPE_MAP` ⇒ b05 抽样页 26 块**可见丢弃**，登记为 ARCH-019（映射目标待裁决） |
| 非公开管理面（一键清除） | 8902 `POST /api/v1/admin/purge` | 已实现（真机两形态演练通过） | PLAN-010 / ARCH-024（2026-09-23）：默认**不挂载**（未启用即 404、`/openapi.json` 不收录），启用需 `QED_ADMIN_ENABLED=1` + `QED_ADMIN_TOKEN`（启用无 token ⇒ 启动期拒绝起服）；`X-QED-Admin-Token` 逐字比对 （缺失与错值同 401）；`dry_run` 缺省只回清单、真删需逐字 `confirm:"PURGE"`；作用域可按书目或全项目，清 `af_*` 四表行（**含 `af_parse_jobs`**，只 DML）与书目录整目录（不裁剪空父级）；**书目录识别只看产物标记（`book.json`/`pages/`/`versions/`）、不看层数**——`domain_id`/`course_id` 为空时 pathlib 折叠空段 ⇒ 真实布局只有两层（判例 ARCH-026）；守卫次序 `confirm` → 作用域（形状＋书目落点）→ 在飞 → 删除，故脏落点判 400 而非 409；先磁盘后库、`failures` 可见、重跑幂等。8900/8903 **不得透传**。回归网 `TestAdminPurgeEndpoint` 14 例 + 真机留档 `tmp/probes/purge-20260923/`（探针两形态）与 `tmp/probes/purge-business-20260923/`（**2026-09-23 业务侧全项目真删 17/24/1015/2 + 4 目录、3.90 GB、零失败，随后 `books/sync` 回灌 17 本**）；运维口径见 [operations.md runbook](../guides/operations.md)。 |
| 模型与引擎对接 | 引擎适配器 | 已实现 | **解析直连本地模型服务**（`QED_OCR_MODEL_URL` 5002，不经 8900 网关）；MinerU 落地，PaddleOCR-VL/qwen-vl 预留，槽位由 `engines/factory.py` 显式分派（未实现即 400，不自动兜底）；模型生命周期与调参归 QED-Engine，而**预处理与后处理两段恒在本仓库**（`ingest/` 与 `normalize/`，MI-15；见 [model-integration](../design/model-integration.md)，[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)）。 |
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
| af_* 书目同步与块编辑 | MySQL `qed` 库 af_* 私有四表 | 已实现（真机已迁移版本布局并首次激活） | 四表一次迁移（`src/axiom_flow/migrations/`）+ 仓储层；sync 幂等、edit upsert 覆盖；MySQL 隔离测试网见 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 内只 DROP 自有 `af_*` + `alembic_version`，`qed` 只读未触达）。**2026-09-21 PLAN-004 实施**：迁移 `20260920_0002`（`af_books.active_job_id` 生效指针 + `af_pages` 主键与 `af_block_edits` 唯一键纳入 `job_id` + `af_parse_jobs.source_sha256/page_count` + `engine` 死默认值 DROP）与仓储/编排/API 读取面全部落地，S1~S6 在 SQLite + `tmp_path` 上取证。**真机 B→C→D 单窗口已于 2026-09-21 执行完毕**：`alembic_version = 20260920_0002`、D-1 re-ingest 486 页并作废 39 行 legacy（现 0 行）、D-2 单次重解析 20/20 后 `active_job_id = 86ab56ae2b6c`（**真机首次激活**）。**迁移 `20260921_0001`（PLAN-006 / ADR 0007：`af_books.last_parse_result` + `af_parse_jobs.failed_pages`）已落地并做 SQLite 5 例 + 真 MySQL `qed_test` 升降级往返；**2026-09-22 R2 窗口在真机 `qed` 上 apply 完成**（备份先行、`alembic_version` 现为 `20260921_0001`，回滚按 `tmp/backups/window-20260922/af_backup_20260922.sql` 还原而非有损 downgrade）**。**REQ-080 双侧闭环（2026-09-22）**：根侧四条确认全部接受（不做切换/回滚写入端点、旧版本清理属 D 类另立、人工编辑不随版本继承），版本布局契约自此冻结，PLAN-004 关闭 |
| 分块（blocks → chunks） | `chunks.json` + `GET /chunks` | 已实现（真机抽样取证完成） | 派生文件、不入库、可重建；参数（max_chars/overlap_blocks）可经端点重算。**REQ-080 归属约定成文并经真机验证**：`chunks.json` 每个解析版本一份、落 `versions/<job_id>/`（真机 `versions/86ab56ae2b6c/chunks.json` 存在），`GET /chunks` 读生效版本，**根视图不含 `chunks.json`**（实测 `root_view_has_no_chunks=True`）。**2026-09-21 抽样完整性取证（PLAN-002 标准 7）**：`tmp/live_check_chunk_formula_integrity.py` 对生效版本 20 页 / 159 原始块 / 17 chunk 跑四项判据全 PASS——18 个 formula 块按 `$$…$$`/`$…$` 规则整块出现在 chunk 文本中、逐 chunk `$` 定界符平衡、`block_refs` 零悬空、非噪声块 100% 覆盖；**反向判据**在临时副本上人为腰斩/悬空 ref/整删 chunk 全部变红；留档 `tmp/probes/chunk-integrity-20260921/C1_chunks.json`。规则本身由 `tests/unit/test_chunking.py::test_formula_and_table_never_split` 在默认门禁常驻守护。**2026-09-22 R2 窗口复跑**：ARCH-018 修复后两本代表书各 20 页的生效版本 chunk 完整性检查 **PASS**（公式整块出现、溯源无悬空、非噪声块全覆盖）；同轮实证一条**判据边界**：修复前的 b11 版本 `chunks.json` 含 126 处 `$$$` 而四项判据仍 PASS（留档 `tmp/probes/window-20260922/C8_chunk_prefix_b11.json`）——腰斩判据看块边界与 `$` 奇偶，不看定界符包裹层级，故公式渲染前的定界符契约由 `normalize/` 单测 + 代表书取证把住 |
| 套1 代表书验收 | 教程 Rudin 中译 + 习题集吉米多维奇 | 抽查口径 + 真实根全本已证（ARCH-009/019 已关闭；业务侧重生随 2026-09-23 真删后当晚重建达成，ARCH-011 行已于 2026-09-30 关闭） | ARCH-009；**2026-09-22 R2 窗口按用户裁决的抽查口径判定**（每本 20 页＝首 3 + 中 7 + 尾 5 + 公式密集 5；两本 PDF 均无文本层，密集页按**墨度**排序选页）：b05《数学分析原理》317 页 / b11《吉米多维奇》406 页，逐条判定 ①~⑤ PASS、公式段可解析率修复后 **100% / 100%**（判据为程序化结构可解析 + 人工留档，不引入 KaTeX 渲染依赖）、chunk 完整性两版本 PASS、`$$$` 归零；⑥类型分布取证未达成 ⇒ **ARCH-019**（`ref_text` 26 块可见丢弃）。**当时未做全量（2026-09-22 读数，已被后续两轮取代）**：b05 生效版本是混合版本（20 页新 / 292 页继承修复前基线，跨旧页 chunk 仍有 `$$$` 485/525），其 5 个缺页与 `last_parse_result=failed` 未处理——全量重生先并入 **PLAN-007**（W6 全本复测，探针根达成），该混合版本又随 2026-09-23 全项目真删物理清除、当晚重建窗口在真实数据根跑出 b05 `012d637df1eb` 317/317 全 `parsed` 且激活（见本文件二十/二十一轮）⇒ 混合版本与 5 个缺页由此不存在。**容量限制已在本轮消除（待真机复测）**：全本单任务的 600s 单墙由 PLAN-007 的按页窗口分块提交根治
（见 orchestrator 行 REQ-086），W0~W7 全部落地并**已随 PLAN-007 关闭（2026-09-22 收口轮）**：**尝试 4 在探针根上把 b05 全本 317 页一次跑通并激活**
（缺页清零、丢弃 0、单一生效版本、空白五页计 `parsed`），故「全量重生按页区间路径执行」的临时口径已不再必要，
ARCH-009（验收）与 ARCH-019（`ref_text` 映射）**同批关闭**；**当时业务库 `qed` 与真实 `parsed/` 未做**
（本轮判据按用户裁决只落 `qed_test` + 探针根）；该欠账已随 2026-09-23 真删＋当晚重建清零（二十/二十一轮），
原「批量为 ARCH-011」的窗口行已于 2026-09-30 关闭（判据重复，见 completed.md 该行与 PLAN-009 承载节）。 |

## 当前主线

- **主攻实施（2026-09-29）＝PLAN-018 区域驱动重解析与块结构编辑（承载根侧 REQ-099）**：用户确认 ADR 0010（Draft→Accepted，新增决定 7＝legacy 无生效版本拒绝一切结构操作；并按用户流程原话补「用户流程表述」节：画框→按框重标→确认固定版本（删旧）→查转译错误，解析与版本生命周期全归 8902）。同日 Q1~Q5 定判：合并归 8902、`AXIOM_MAX_REGIONS_PER_PAGE`=16、下标↔`region_id` 翻译归本仓、加第五表改道**新立 PLAN-020**（`af_*` 表族措辞收口 + 守护断言，评审关闭前 W2 冻结建表），2026-09-29 评审通过并当日实施关闭（S1~S6 逐条判定、守护四判据先红后绿带变异复验、措辞收口、准入守则晋升 database-design；Retain 归档 history/plans），**W2 加表前置已解除**；每页人工确认门自 PLAN-019 P3 移入 PLAN-018 W8（单侧登记）。实施次序＝PLAN-020 → W2 → W1/W3/W8 → W6 回执；真机面（迁移 upgrade、S 判据消费侧实测）仍按 D 类另批。
- **立项（2026-09-28）＝PLAN-019 解析产物可信与增量重跑实施**（承载 **ARCH-022**，自 PLAN-009
  转入）：P1 重跑判定签名 + 过时清理、P2 就绪口径、P3 分诊 ledger + 页确认门。用户两轮裁决定形
  口径——① 正本作用域＝**生效版本 + 在飞任务目录**，其余判过时（页图留物理形态为例外），DB 与
  数据根一一配对；② **就绪以 DB 元数据为唯一事实源**，推翻 PLAN-009 原「manifest-last 作就绪标记」
  判定，manifest 降为完整性校验数据；③ **分块前每页须人工确认**，确认三形态齐备（单页 / 页区间
  批量 / 按页重解析后回未确认），禁止的只是系统自动整本背书；④ ledger 落 `af_pages.quality`
  JSON 追加键；⑤ 签名记录模型版本，但 W1 须先把 `/health` 回文取证成文并**停下确认无争议**才动码。
  壳状态 `Accepted`（零代码），剩 Q2/Q4/Q6-a/Q7/Q9/Q10 待裁。**2026-09-29 更新**：每页人工确认门（其裁决③/P3）经用户裁决移入 PLAN-018 W8，单侧登记，PLAN-019 壳未动。
- **已关闭（2026-09-30 立项并同日收口）＝PLAN-022 台账行三要素改造**（用户三轮裁决：行的本职＝任务描述＋**完成时的具体终态**＋**唯一承载文档指针**，现状与细节全部退回 `plans/`）：[ADR 0013](../adr/0013-ledger-row-three-elements.md) 立稿并取代 ADR 0006 的台账列名与列语义表述；`todo.md` 七列为 `ID / 类别 / 优先级 / 状态 / 任务 / 成功标准 / 承载`——成功标准 ≤80 字且禁 `门禁`/`收口`/`全绿`/`成文` 与 `S1~S6` 形验收措辞；承载列**恰好 1 个链接**（镜像行＝自身壳且链接文本＝壳 H1 标题、支线行＝`../plans/*.md` 承载壳、长期行＝其治理文档），`design/`/`architecture/` 引用退回承载文档自身的「关联设计」元数据；「任务／成功标准」两列不得出现文档链接，来源标记 `**请求接收…**` 写在「任务」列句首，三列剥链接后禁 ISO 日期；守护 `test_tracker_governance.py` 三轮先红（第一/二轮各 4 failed＝表头不匹配 ⇒ todo 行为空；第三轮 2 failed＝指针数 2≠1 与镜像行「承载」列非 `plans/` 指针）后绿（11 例），两批各五条变异（第二批＝承载列塞现状／成功标准用禁词／成功标准超界／指针超 3／注入日期；第三批＝承载列加第二篇链接／文档链接挪回「任务」列／指针后加现状散文／镜像行改指非 `plans/` 文档／成功标准注入日期）各转红一次后复绿；「规则」节与全部 9 个活跃行按三要素重写并新增 PLAN-022 镜像行，从行内移出的现状、读数与 design 指针逐条核对已在 PLAN-019/009/018 壳、`completed.md`、`roadmap.md` 与 `project-status.md` 安家（两轮零丢失核对表在壳）。全量门禁 **560 passed / 10 skipped** + ruff clean。已关闭行与 `docs/history/` 的旧列名叙述按 ADR 0013 后果节**不回改**。**W16 去重（收口批附带）**：三列口径的**唯一正文**定为 [task-lifecycle.md](../standards/task-lifecycle.md)「任务台账与承载」，`todo.md` 节首段与「规则」节改为指针（只留 ID 前缀、类别/状态取值、排版标记等读表必需的本地事实）、`doc-governance.md` 两处复写改链接——同一规则不再在多篇文档各写一遍。**W17 收口**：todo「治理轮」节整节原子迁移至 [completed.md](completed.md) PLAN-022 行，壳按 Retain 迁 [history/plans/2026-09](../history/plans/2026-09/2026-09-30-ledger-row-three-elements.md)，ADR 0013 的活链接与 `plans/index.md` 登记随批改指。
- **口径变更（2026-09-30）＝开发分支改名 `release` → `develop`**（[ADR 0014](../adr/0014-develop-branch-rename.md)）：
  分支职责与合并纪律不变（日常提交进 `develop`，`main` 只在用户明确要求且本地门禁全绿后接收合并，
  不隐式打标签或建 GitHub Release）；`AGENTS.md`「分支与完成门禁」、`standards/testing.md`、
  `standards/doc-governance.md` 触发条件三处正文同步，本文件的日期化轮次记录与 `docs/history/**`、
  已关闭台账里的 `release` 字样属当时记录不回改。
- **已关闭（2026-09-29 立项并同日收口）＝PLAN-021 工作区目录卫生清理轮**（B 类支线，零 `src` 行为改动）：四条进场裁决落地——① 仓内 `data/` 留档区**退役**（2468 文件 / 466 MiB 真删、不备份，按用户 Q4；前后指纹与 3 份备份 sql 的 sha256 前缀只留档在壳），[ADR 0012](../adr/0012-workspace-directory-hygiene.md) 决定 2 取代 ADR 0009 决定 2 的「永久留档与备份区」定性，「仓内目录不得当数据根」守卫继续有效；② 临时区收进仓库根 `tmp/`（21 个 tracked 脚本迁出后不入库、`.gitignore` 加 `/tmp/`），历史面与台账路径按 Q2 全量改指新落点（157 处/26 文件，代价＝新路径不再解析到已删证据，注记在壳）；③ `web/` 与 `app/` 前端残骸清零；④ `logs/` 只写本项目日志、文件数 ≤3、前缀 `qed-axiom` 成文（不改服务脚本代码）。守护 S5 先红后绿 + 变异复验 M1~M5，门禁 **558 passed / 10 skipped** + ruff clean（本壳净增 3 例）。**未来窗口口径**：一次性驱动、探针、窗口证据与 D 类备份一律落仓库根 `tmp/`（可随手清理、不作事实源），长命回滚锚点落 `<QED_DATA_ROOT>/backups/`。遗留：Q3 未裁 ⇒ `logs/qed-axiom-mode` 原地保留。壳按 Retain 迁 [history/plans/2026-09](../history/plans/2026-09/2026-09-29-workspace-directory-hygiene.md)。
- **已关闭（2026-09-26 立项并同日收口）＝PLAN-017 解析产物根视图清理实施**（承载根侧 REQ-097，
  根仓库 ARCH-032 续轮请求）：S1~S5 按**本地判据**逐条判定、REQ-097 回执正文成文（锚点 `f2fd689`
  代码批 / `6cab58f` 文档批）——根视图只留 `pages/p*.png`（`book.json`/`manifest.json` 退役）、
  版本目录为唯一正本、版本 `book.json` 由任务从 af_books 生成（元数据事实源统一到库；偏差 D1
  复用既有 `sha256` 列不新增 `ingest_sha256`，补列 `ingested_at`＝迁移 `20260926_0001` 不回填）、
  激活成功后删除被替换的旧版本目录（删除严格在指针提交后；`legacy`/自替换不删；`OSError` 只告警
  保留、终态不翻转）、读取面纯 DB 驱动无文件系统回落。门禁 548 passed / 10 skipped + ruff clean +
  `QED_TEST_MYSQL=1` 集成层 8 passed。真机两项中**项①（业务库 `qed` 升 `20260926_0001`）已于 2026-09-28 执行完毕**
  （该列缺失曾致 `GET /books` 全 500、前端报「8902 服务不可达」；备份先行
  `tmp/backups/window-20260928/af_backup_20260928.sql`，升级后 `/books` 200 × 17 本复验）；
  余下**项②（真实数据根激活删旧实测）改挂 PLAN-018 真机面**（原移交的 ARCH-011 窗口行已于 2026-09-30 关闭：判据重复，取证项改挂见 completed.md 与 PLAN-009 承载节）；
  根仓库按回执正文自读登记关闭其 REQ-097 行。
- **已关闭（2026-09-24 立项，2026-09-26 收口批）＝PLAN-016 存储规范对齐实施**
  （承载 ARCH-034 / REQ-095 / PLAN-015，落根仓库 ADR 0018 下放的落盘守护自证）：S1~S7 全部达成——
  白名单成文 + ADR 0009 Accepted、存储治理守护 18 例（均先红后绿）、数据根 fail-closed 三拒（不
  自动创建，追认）、书目枚举深度无关（两形态回归）、`.tmp-`/`.old-` 三式识别与 lifespan/purge
  告警、备份与留档口径成文、仓内残骸清理经批执行（前后指纹留档、`git status` 零差异）；变异复验
  M1~M4 + 第二窗口逐条独立复验。W8 回执于 2026-09-26 口径确认后成文：PLAN-015 壳 A1~A4 判定表 +
  REQ-095 回执正文（锚点 `a97ccc7`），**同批关闭 PLAN-015/REQ-095/ARCH-034 四行**（判据与读数入
  [completed.md](completed.md)）。门禁 517 → 537 passed / 10 skipped + ruff clean（收口批复跑同读数）。
  壳按 **Retain** 迁 [history/plans/2026-09](../history/plans/2026-09/2026-09-24-storage-convention-alignment.md)，
  PLAN-015 按 **Delete** 删壳。**跨项目承接项由此只剩 PLAN-017**（承载根侧 REQ-097，见上条；
  该条已于同日收口批关闭 ⇒ 跨项目承接清零，活跃承接项现仅 PLAN-018 承载根侧 REQ-099）。
- **主攻位历史（2026-09-24 记）＝无**：v0.1 已收口并完成台账清理与发布动作，当时下一主攻待指派（同日
  晚立项 PLAN-016 顶上，见上一条）。上一轮主攻 **PLAN-013 V0.1 收口与
  文档清理（A 类）已 `Completed`**（2026-09-24，S1~S5 逐条判定，壳按立项判定 `Delete` 删除、锚点
  `268b7d2`）：进场只读取证（W0）把「前端验证已通过」拆成两条不同结论——`GET /parse-jobs` 已被真实消费、
  `GET /books/{id}/pages` 当时消费 0 次且调用方仍在逐页试错、`af_block_edits` 0 行 ⇒ 据 D1 裁决补做一次
  小窗口取证后，ARCH-005 达成、ARCH-006 按你 2026-09-23 裁决「以清除前那次 PASS 为准」改判关闭（Partial）。
  四项进场裁决 D1~D4 全部消耗，判据与读数逐行入 [completed.md](completed.md)。
- **已关闭（2026-09-23 立项并当日收口）＝V0.1 固定文档形式正规化（PLAN-014，B 类，[ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md)
  的执行轮，ARCH-010 的第二次实质执行）**：审核 `docs/architecture/` 落出六处形式缺陷（元数据三套写法、
  `Current` 不在任何枚举里、正文混入数十行台账式叙述、`code-map.md` 备注长成第二份实现正文、表清单
  「待真机迁移」与头部 `Verified` **自相矛盾**、契约端点清单靠硬编码字符串锚定且该断言**空转通过**）
  ⇒ 统一到与 `design/` 同等严肃度：三体裁元数据（契约正文类 / 映射与索引类 / 目录索引类）+ 四段骨架白名单
  + 正文零任务编号零日期 + **契约表从运行态真源双向对账**（`app.routes` ↔ 端点总表 ↔ `#### METHOD /path`
  小节；SQLAlchemy 模型 ↔ 四张列清单，含可空性、主键、VARCHAR 长度、唯一键与索引名）。`code-map.md`
  回到纯五元组映射，备注列的行为判据逐条登记「删 → 存于 `PP-n`/`PM-n`/`MI-n`/`api.md`/列清单」。
  S1~S7 逐条达成、守护变异复验 25 例、零丢失以反引号 token 覆盖比对自证 ⇒ 壳按 **Retain** 迁
  [history/plans/2026-09](../history/plans/2026-09/2026-09-23-arch010-v01-architecture-form-normalization.md)。
  **`docs/architecture/` 四篇 + `docs/design/` 三篇的 `确认状态` 已按你的裁决统一转「已确认」**（七篇
  逐篇依据记在壳「登记与结论」第 4 条）⇒ PLAN-012 行末尾「仍暂定、等你逐篇确认」的话已了结，
  PLAN-013 D4 判据里的「不做 `确认状态` 登记」由本行完成，不重复执行。门禁 517 passed / 10 skipped + ruff clean。
- **已关闭（2026-09-23 立项并当日收口）＝`docs/design/` 叙述框架重整（PLAN-012，A 类）**：
  体检落出七条缺陷（非法枚举 `实现状态`、元数据写成变更日志、design 内出现第二份端点/列名表、条文编号指向
  已归档壳、`qed_llm_calls` 与退役清单自相矛盾、裸「块」三义、index 带日期章节）⇒ 框架定稿并执行 W2~W7：
  三篇保持不拆、每篇一个唯一问题、六段骨架齐备、条位在地编号 `PP-1~PP-20` / `PM-1~PM-17` / `MI-1~MI-14`
  （实现落点与守护测试名当时在每篇篇末条文表）、边界去重（列清单与字段表退回 `architecture/`）、术语定名
  （版面块 / 解析窗口 / 知识块，裸「块」清零）、`design/index.md` 回归纯导航。S1~S6 逐条判定达成，
  三条守护各做变异转红复验（13 例）⇒ 壳按 **Retain** 迁
  [history/plans/2026-09](../history/plans/2026-09/2026-09-23-design-narrative-framework.md)（原判 `Delete`，
  因未提交而改判）。**三篇 `确认状态` 当时仍「暂定」**（等你逐篇确认）⇒ 同日 PLAN-014 轮按你的裁决统一转「已确认」，见上一条末段。
  **同日追加（呈现轮，不重开 PLAN-012）**：三篇的「不变式与条文」段先改名**「已确定守则」**（按主题 H4 分组、
  每条 = 一行加粗结论 + 分列「判据 / 为什么 / 例外 / 后果 / 边界」），同日再定形为**「确定规范」**——每个主题
  一张表，一行 = `编号 | 守则（结论 ＋ 判据）| 理由／例外／边界 | 实现落点 → 守护测试`，篇末「条文表」并入
  守则段（每篇只留一张登记表）；设计守护同批改写为校验新形状（编号连续有序、结论加粗、落点→守护成对），
  条文编号与六段骨架零变化，逐字丢失以 8-gram 覆盖比对确认；`model-integration.md` 新增 **MI-15**——预处理与
  后处理两段恒在本仓库（输入整形与结果修补只落 `ingest/` 与 `normalize/`，不靠引擎侧新增调参开关）。
  门禁同批 485 passed / 10 skipped + ruff clean，登记在 ARCH-010。
  该轮收尾时写的「本仓库侧无自主可执行动作」已被同日 V0.1 收口轮的 W0 实测读数更正：判据取证补做与
  文档清理两项**本仓库可自证**，故主攻位移交 PLAN-013；真机联调与产物重建仍只在你的排期裁决里。
- **同日已完成（2026-09-23 清理轮）**：**业务侧全项目真删 + 书目基线回灌**按用户批准
  （`scope.all=true`、不备份磁盘产物）执行完毕，执行前发现并修掉真实布局缺陷 ⇒ 当日立项当日关闭
  **ARCH-026 / PLAN-011**（书目录识别改深度无关）。当前业务库 `qed`＝`af_books` 17 行（其余三表 0 行）、
  `QED_DATA_ROOT/parsed/` **零文件**（只剩空的书目父级骨架）；重建任何产物需重新 ingest + 解析。
  判据与留档见归档壳 [history/plans/2026-09/PLAN-011](../history/plans/2026-09/2026-09-23-purge-book-dir-depth-agnostic.md)
  与 [completed.md](completed.md)；前置工具 PLAN-010 的判据见
  [history/plans/2026-09/PLAN-010](../history/plans/2026-09/2026-09-23-admin-purge-endpoint.md)。
- **PLAN-008 收口链（2026-09-23 本方侧 → 2026-09-24 关闭）**：S1~S4 达成（14 条契约测试绿 + 变异复验、
  文档四处收口）⇒ **REQ-088 于 2026-09-23 关闭**（判据统一为「端点交付、本方侧自证」，根侧确认改挂 S5 与
  ARCH-005/006）；当时**不提前关计划**的理由＝ARCH-005/006 的承载就是本壳，其 `Delete` 判定要求三行全关，
  提前删壳会使两行失去承载（违反 ADR 0006 一任务一承载）。2026-09-24 两行判据消耗完毕 ⇒ PLAN-008 与
  ARCH-005/006 同批关闭、壳按 `Delete` 删除，锚点 `8fca39b`（复验读数与改判入册）。
- **已关闭（2026-09-22 收口轮）**：**PLAN-007**（全本窗口分块 + 空白页合法化，承载 REQ-086/087、
  ARCH-009/019）S1~S5 逐条判定后关闭，壳按 **Retain** 迁 `docs/history/plans/2026-09/`，五行关闭证据入
  [completed.md](completed.md)。**判据达成**：四项协议假设真机冻结 ⇒ 默认 56 页/块；引擎窗口参数与按块
  deadline、pipeline 串行窗口循环与块级失败隔离、`blank_page`、`ref_text` 映射、契约两条（含两处变异
  转红复验）；W6 尝试 4（job `652a3e3be33c`）b05 317 页 / 6 窗口 / 718 秒 completed 且**激活**、
  `failed_pages=[]`、空白页 `[48, 98, 136, 162, 282]` 与预测逐页一致、丢弃 0、954 文件版本目录、业务侧
  零写入自证（留档 `tmp/probes/wholebook-req086-20260922/attempt4-green/`）。**交接两项（不再占本仓库
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
- **下一轮候选（2026-09-23 清理轮后；均需你先定级/批准，本表不自行指派）**：
  ① ~~业务侧清理一轮~~ **已执行完**（全项目真删 + 四表备份 + 回灌 17 本，判据见 PLAN-011 行）；
  ② ~~产物重建窗口~~ **已关闭（2026-09-30，ARCH-011 行删除）**：清理的直接后果是「任何对照/联调/验收前
  必须重新 ingest + 解析」，该动作自 2026-09-23 真删后由**用户按需解析**逐本承担（当晚 b05 已在真实数据根
  全本激活），不再作为台账窗口存在；批量重解析若将来确需，操作规程留档在 PLAN-009 承载节、排期与批准在用户，
  而**解析依赖根侧图像模型服务**（裁决 2026-09-22：WSL/`mineru-api` 归 QED-Engine 图像模型维护线）；
  ③ ~~8903 前端联调（PLAN-008 W6 ＝ ARCH-005/006 剩余判据）~~ **已于 2026-09-24 收口**：ARCH-005 经真机
  消费复验关闭，ARCH-006 按你裁决改判 Partial 关闭 ⇒ 该项不再占台账位；仅剩「重建代际块编辑写入」由
  根仓库下一窗口自证；
  ⑤ 已达成不需再做：一键清除端点（PLAN-010 全工作项）、两条列表端点补进 `test_architecture_documents.py`
  的 ③ 类守护（PLAN-008 W5 已按**节标题锚定**方式补入，比白名单子串更强）。
  ⑥ **v1.0 开工**（PLAN-009 的 ARCH-022/023：产物易消费优化 P1~P3 与知识库面 R1~R4）——v0.1 收口后
  其「实施前置＝v0.1 收口」一条已解除，只剩你的排期裁决与产物重建；
  ⑦ ~~发布口径~~ **已执行（2026-09-24 清理轮）**：打标签 `v0.1.0` + `release` → `main`（**未推送**）；
  ⑧ **ARCH-010 长尾的条文编号清扫（本轮实测新发现，未入任何批次）**：`src`/`tests`/`scripts` 注释里
  仍有裸形式编号 `（V3）`/`（V8）`/`（S3）` 数十处指向已归档壳——PLAN-013 W4-② 只清了
  `PLAN-0xx 条文 N` 写法（该字面现已 0 命中），裸 `V*`/`S*` 引用属同一类悬空引用，需要一次带门禁的清扫轮。
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
  （受理前门槛、判在探活前、不创建任务行）、**`tmp/live_reparse_b01_pages.py` 重写**为版本布局
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
  写进 `operations.md` 的 C-0 前置。证据 `tmp/probes/live-window-20260921/`（`S1c_exclusive.json` 为排他自证）。
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
  反向判据四色齐红，脚本 `tmp/live_check_chunk_formula_integrity.py`），标准 8 判「本方侧发出」；
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
  `tmp/backups/window-20260922/af_backup_20260922.sql`（184371 字节 / 5 表 / `20260920_0002`），全程
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
  `trackers/*`、`tmp/probe_mineru_api.py` 四处引用改指归档位置，月份索引与 `plans/index.md` 登记
  去处）。门禁 **411 passed, 10 skipped** + `ruff check src tests scripts` 全绿（本轮**零代码**，故用例数与
  十四轮收口轮持平；治理契约测试覆盖本批改动的全部台账/计划/链接不变式）。**未包含**：任何实现改动
  （两计划工作项全 `Pending`）、任何真机操作、任何推送。
- **十六轮（2026-09-22：PLAN-007 W6 b05 全本真机复测全绿，D 类探针窗口）**：驱动脚本
  `tmp/live_wholebook_req086.py` 以**真实环境变量**把库换成 `qed_test`、数据根换成
  `tmp/probes/wholebook-req086-20260922/root`（不改 `.env`），探针服务起在独立端口 8912（不碰
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
- **十八轮（2026-09-23：PLAN-008 本方侧收口 + 一键清除端点立项，A 类零代码）**：应「重新整理 todo，
  看接下来还有哪些事、顺序如何」+「需要一个不对外开放的接口，一键清除本项目部分数据库数据和
  `QED_DATA_ROOT` 对应解析数据（也可按书目）」两条需求。① **v0.1 逐行对表后的结论**：本仓库侧只剩
  **一键清除端点**（ARCH-024）一件可自证的事；其余全在根侧（8903 联调＝ARCH-005/006 + PLAN-008 W6）或
  等用户批准（业务库全本重生、ARCH-011 批量），v1.0（PLAN-009 / ARCH-022/023）只登记不排期。
  ② **PLAN-008 局部收口**：S1~S4 判定达成 ⇒ **REQ-088 关闭**入 completed.md，计划正文转 `Blocked`
  并新增「阻塞与恢复」四要素（阻塞证据/恢复条件/责任位置/复核触发点）；同轮统一壳内**两处互斥的
  REQ-088 判据**（原「验证与验收」写未确认前可关、「关闭与归档」写须根侧确认）⇒ 按「端点交付判据、
  本方侧自证」统一，根侧确认挂 S5 与 ARCH-005/006。回执正文成文于壳「REQ-088 回执正文」节由根仓库自读。
  **不提前关计划**：ARCH-005/006 的承载即本壳，Delete 判定须三行全关。③ **新立 ARCH-024 / PLAN-010**
  （`docs/plans/2026-09-23-admin-purge-endpoint.md`，`任务类型：B`、状态 `Accepted`；同日交付后按 Retain
  归档至 `docs/history/plans/2026-09/2026-09-23-admin-purge-endpoint.md`）：形态与条文草案、
  六条成功标准（默认 404／token fail-closed／`dry_run` 缺省真＋`confirm` 双确认／磁盘作用域守卫＋库侧
  只 DML／`queued`+`running` 在飞 409／`deleted`+`failures` 可见且重跑幂等）、真机演练只落 `qed_test`＋
  探针根（业务库全项目清除另需 D 类批准）全部成文于壳，**待评审后才动 `api.md` 与代码**。
  ④ **事实核对**：8902 只绑 `127.0.0.1`（`axiom_flow_service.py:71-72`）⇒「不对外开放」不能靠网络，
  只能＝不进 8900 消费的公共契约 + 默认不挂载 + 双确认；现成最近能力是 `api/main.py:284-301`
  `DELETE /books/{id}?purge=true`（逐本、按条文 9 保留 jobs 行），`ParseJobRepository` 至今无任何删除方法。
  **门禁**：本轮零代码，`pytest tests -q` 用例数与十七轮持平（465 passed / 10 skipped）+
  `ruff check src tests scripts` clean。**未包含**：任何实现或契约形状改动、`api.md`/`database-design.md`
  条文落地（等 PLAN-010 W1 评审）、任何真机操作、任何提交与推送。
- **十九轮（2026-09-23：PLAN-010 一键清除端点当日交付并关闭，B 类含真机 D 类演练）**：接十八轮立好的行，按评审通过的五项默认口径实施 W1~W7。① **先文档**：`api.md`「非公开管理面」节（六条不变式 + 错误语义 + 「管理面不得透传」强制规则）、`database-design.md` 条文 9 的 purge 例外、`parsing-management.md` 端点行三处同批落地。② **实现**：`db/repositories.py` 五个清除原语（四表 `delete_for_book`/`delete_all` + `count_active`，只 DML）＋新模块 `api/admin.py`（作用域解析、磁盘落点守卫、先磁盘后库、`failures` 可见、幂等重跑）＋ `mount_admin()` 装配期开关与 token fail-closed ＋ `schemas.py` 三个模型 ＋ `config.py` `QED_ADMIN_ENABLED`/`QED_ADMIN_TOKEN`（`repr=False`）。**位置偏离计划**：purge 逻辑落 `api/admin.py` 而非 `application/`，因 `test_architecture_dependencies.py` 禁止 `application/` import sqlalchemy。③ **回归网**：`TestAdminPurgeEndpoint` 13 例（先取真红灯 11 failed 再实现），并对 W6 暴露的守卫次序缺陷补 `test_unsafe_segment_beats_in_flight_conflict`。④ **真机演练（W6）**：`tmp/live_admin_purge_plan010.py` 在 `qed_test` ＋ 独立探针数据根上 **K1~K9 全 PASS**（`all_pass=True`、退出码 0；清除前 `af_*` 整表备份，业务库与真实 `dataset/parsed` 前后指纹逐字段相同＝零写入）。**首轮 K6 FAIL 的教训**：两条守卫各自的用例都成立，但「脏落点 + 在飞行」的组合只在真机出现，409 抢在 400 之前把配置脏报成「解析在飞」⇒ 守卫次序固定为 `confirm` → 作用域（形状＋落点）→ 在飞 → 删除，四处文档与 code-map 同批改写。⑤ **收口**：todo 两行（ARCH-024、PLAN-010）压缩关闭入 completed.md，壳按 **Retain** 迁 `docs/history/plans/2026-09/`。**门禁**：**478 passed / 10 skipped** + `ruff check src tests scripts` clean + `QED_TEST_MYSQL=1 tests/integration` 8 passed。**未包含**：业务库与真实数据根上的任何清除（＝「清理一轮」那一步，逐步 D 类批准）、对 QED-Tracker 侧书目的回灌、任何提交与推送。
- **二十轮（2026-09-23：清理轮执行完毕 + 同日立同日关 PLAN-011/ARCH-026）**：按用户批准在业务侧做
  **全项目真删**（`scope.all=true`，裁决「直接真删、不备份磁盘」；四表 JSON 备份照做）。① **先手只读盘点**
  （`tmp/live_purge_inventory_plan010.py`）：四表 17/24/1015/**2**、在飞 0、`parsed/` 书目录 4 个
  7379 文件 3.90 GB、`dataset/` 其余（源 PDF 等）89 文件不在作用域。② **首次 dry-run 暴露真实布局缺陷**：
  `targets` 回出 **8 条** `pages/`、`versions/` 子目录而非 4 条书目录——`af_books` 17 本 `domain_id` 全为
  空串，pathlib 折叠空段 ⇒ 书目录只有两层，而枚举按固定三层 glob。若照此真删会留下 `book.json`/
  `manifest.json` 与空目录 ⇒ PLAN-010 的「重跑幂等」在真实数据上不成立。**根因与 K6 同一类**：PLAN-010 的
  13 例契约测试与 K1~K9 演练全部用非空 `domain_id`（三层）⇒「测试数据形状替真实数据背书」。③ **当日立项
  PLAN-011 / ARCH-026（B 类）并 TDD 修复**：红灯 `test_empty_domain_book_dir_is_purged_whole` 先在默认门禁
  复现业务侧症状（`['…/pages','…/versions'] != ['parsed/math_analysis/mathanalysis-b09']`）⇒ 枚举改
  **产物标记识别 + 命中剪枝 + 深度上限 3**；无回归自证＝W6 演练脚本重跑 K1~K9 全 PASS（`业务零写入=True`）。
  ④ **条文同步三处**：`api.md` 磁盘作用域段新增识别规则与判例、`code-map.md` 模块行、`operations.md` runbook
  第 2 步新增「核对 `targets` 层数形状，出现 `pages/`、`versions/…` 即停手」。⑤ **真删与回灌**：修复后
  dry-run `targets` 恰 4 条 ⇒ 真删 200、`deleted`={17,24,1015,2,4}、`failures=[]`、四表归零、`parsed/`
  文件数 **7379 ⇒ 0**；`POST /api/v1/books/sync` 回灌 17 本（`synced:17`），与根库 `qt_books` 只读比对＝
  17 本 `verified`（有 `file_path`）逐本对齐，余 9 本 `failed`/`decided` 无文件不在同步域。留档
  `tmp/probes/purge-business-20260923/`。**连带事实**：三本生效版本产物（b01/b05/b11）与 2 条人工块编辑
  一并消失 ⇒ ARCH-005/006 联调前置回到「先重建产物」，两行证据列同批改写。⑥ **收口与判定**：S1~S6 逐条
  达成；**归档判定 `Delete` 改判 `Retain`**——壳与修复均未提交（「暂不推送」且本轮未要求 commit）⇒ Delete
  所需 Git 锚点不成立。**门禁**：**479 passed / 10 skipped**（+1＝本轮回归网）+ `ruff check src tests scripts`
  clean + `QED_TEST_MYSQL=1 tests/integration` 8 passed。**未包含**：任何提交/推送、根仓库文件与库的写入
  （`qt_books` 只读）、重新 ingest/解析、对根侧图像模型服务可用性的改动。
- **二十一轮（2026-09-23：补记当晚的产物重建窗口，应 V0.1 收口轮 D2 裁决）**：二十轮收尾后**同日稍晚**
  又跑了一次 ingest + 解析，但当时未登记任何台账，致本项目状态快照与运行态直接矛盾（本条即补记，读数取自
  PLAN-013「W0 实测读数」的只读取证：`af_*` SELECT + 磁盘计数 + 8902 访问日志 + 三条 GET，零写入）。
  ① **产物现状**：`QED_DATA_ROOT/parsed/` **5355 个文件**（更正二十轮的「零文件」，该读数当日即失效）、
  `af_parse_jobs` 6 行（`completed` 4 / `partial` 2）、`af_pages` 957 行；两本书有生效版本——
  b05《数学分析原理》`012d637df1eb` **317/317 全 `parsed` 且产物全部在位**（全本窗口路径第二次真机激活）、
  LA-b01《数学分析》`ab91e2d19bf4` **486 页只 3 页 `parsed`（483 页无页行＝`pending`）**。
  ② **两次 `partial` 归因**（按 ADR 0007：有产出未齐备即 `partial`，不美化）：一次「模型服务不可用」
  （块间探活不健康即止，剩余页记 `MODEL_UNAVAILABLE`）、一次「窗口 251-300 轮询返回 HTTP 404」
  （任务号在 MinerU 侧消失）。**责任位置＝QED-Engine 图像模型维护线**（WSL/`mineru-api`，裁决 2026-09-22），
  本仓库不据此调业务参数（窗口大小、超时、重试一律不动），只保留判据：`partial` 版本不激活、
  产物保留在 `versions/` 供排查，同 `job_id` 重放可续跑（`PM-14`）。
  ③ **`af_block_edits` 0 行**：原 2 行人工校验随二十轮清除消失，重建窗口内**没有**新的块编辑写入
  ⇒ ARCH-006 的字面判据「代表书重解析后真机写入」在重建后的数据上未发生，这正是 PLAN-013 W1 要补的取证。
  ④ **流程缺口（本仓库侧，进 W4 待复核清单）**：一次会改变运行态与联调前提的真机写入窗口没有承载登记
  ⇒ 台账与磁盘分叉。判据补一条：此后任何 ingest/解析窗口必须在**同一轮**内落 project-status 轮次条目，
  否则收口轮只读取证一旦发现分叉即以取证读数为准改写台账（本轮即按此执行）。
  ⑤ **访问日志的联调侧读数**：`GET /api/v1/parse-jobs` 200 共 33 次（含多值 `status` 过滤与按书过滤两种
  形状）⇒ jobs 列表已被真实消费；`GET /api/v1/books/{id}/pages` **0 次**，同期调用方仍在逐页试错
  （`/books/mathanalysis-b01/pages/1` 404 共 401 次）。另两条历史 500（`POST /books/sync`、
  `POST /books/mathanalysis-b01/ingest`）发生在重建之前、同形请求其后为 200 ⇒ 列待复核不在本轮结案。
- **二十二轮（2026-09-24：V0.1 收口批执行，PLAN-013 W2/W3/W4-③/W6 剩余一次做完）**：并行 PLAN-014 轮
  入库后（`6bf2ff9`，其形式正规化产物本会话未参与、按你 2026-09-24 授权代为提交），本仓库一次做完 v0.1
  收口：**ARCH-005 关闭 Achieved**（8903 真实消费页清单：`/pages` b05 200、LA-b01 200、无产物的 b01 404＝
  契约语义，同窗口对照面 `image` 36／`edits` 40／`pages/{n}` 25 次，作业面列表两形状 + 单 job 轮询 346+99
  次）；**ARCH-006 按你裁决改判关闭 Partial**（末次起服后 `PUT .../edit` 0 次、`af_block_edits` 0 行 ⇒
  重建代际无写入样本，以清除前那次 PASS 为准，当代际证据移交根仓库）；**PLAN-008 关闭 Achieved**、
  **ARCH-001 主线聚合行随之关闭**（判 Partial，唯一原因是 ARCH-006 那面）。两壳按 `Delete` 删除、
  todo v0.1 节收缩为收口存根（该存根已由二十三轮整节移出）、`plans/index.md` 登记去处与锚点、三行终态入 completed.md。
  **本轮零代码零真机写入**（判据取证只读），门禁 517 passed / 10 skipped + ruff clean。
  **仍待你裁决的两条**：① v0.1 是否打标签/合 `main`（本轮按 D3 未做）；② 下一主攻＝PLAN-009 v1.0 开工
  还是 ARCH-011 产物重建窗口（后者为 D 类，含业务库与真实数据根写入）。
- **二十三轮（2026-09-24：v0.1 台账残留清理 + 发布动作）**：按你「v0.1 主线要完全清理完」的裁决，
  收口存根**整节移出 todo**（本表不留已收口版本的节，导语补一条通用规则；v0.1 的事实源＝
  [completed.md](completed.md) 五行终态 + 本文件「版本口径」段）。三条运维事实在移出前逐条对源：
  产物代际＝本文件「下一轮候选 ②」与二十一轮 ①、环境侧交接＝本文件「v0.1 已无开放行」条与
  [operations.md](../guides/operations.md)、文档面长尾＝todo 长期行 ARCH-010 ⇒ 零丢失。
  **同批清扫两处实况不符**：① `code-map.md` 的 `tmp/` 行原写「不入库、不作为事实源」，实际
  20 个脚本已 tracked 且被 runbook 按路径引用 ⇒ 改指 operations.md runbook（架构正文禁任务编号 ⇒ 
  去留判据只落在 todo 的 ARCH-010 行，守护在本批实执过一次）；
  ② `plans/index.md` 三条「提交后下一轮按 `Delete` 重判」的悬置线程结案（三壳已分别随
  `fb7eb5a`/`23664c2`/`6bf2ff9` 入库，Retain 判定本身不变）。`overview.md`「能力矩阵（v0.1）」逐行
  核对与收口口径一致，未改。**发布**＝本批提交后给 `release` 打 `v0.1.0`（annotated）并
  `main` ← `release` 快进合并，**未推送**（你未授权推送）。**新发现长尾**见「下一轮候选 ⑧」。
- **已关闭（2026-09-20）**：ARCH-013 af_* 四表建表与仓储层、
  ARCH-014 书目同步与 CRUD、
  ARCH-015 ingest 与 `QED_DATA_ROOT/parsed/` 布局（真机迁移 + 486 页 E2E 证据见
  [completed.md](completed.md)）。
- **v0.1 已无开放行，且 todo 不再为它设节（2026-09-24 收口轮 + 清理轮）**：**PLAN-013**（A 类收口计划）与 **PLAN-008**（两条只读
  端点，承载 ARCH-005/006）**同批 `Completed` 并按 `Delete` 删壳**，主线聚合行 **ARCH-001 关闭**（判
  Partial，理由＝ARCH-006 当代际无块编辑写入样本，按用户裁决改判关闭）。**PLAN-010/011**（一键清除与其
  真机布局修复）、**PLAN-007**（全本窗口分块 + 空白页合法化）、**PLAN-012/014**（两轮文档形式正规化）
  均已关闭并按 Retain 归档在 `docs/history/plans/2026-09/`。**跨项目侧无开放行**（REQ-001/080/081/083/
  084/085/086/087/088 全部关闭，回执正文由根仓库自读登记）。**环境侧交接仍生效**：WSL 容量、发行版空闲
  回收与 `mineru-api` 容器归 QED-Engine 图像模型维护线（裁决 2026-09-22，判据见 [operations.md](../guides/operations.md)）。
  下一动作全部在你的排期裁决里（见「下一轮候选」），本表不自行指派。
  **跨项目侧无待启动项**：六个请求行已于 2026-09-22 全部关闭（含 REQ-001，其承载行改判为根侧
  `PLAN-044`/`ARCH-020-D` 后由根侧登记读取）；REQ-087（空白页）已随 PLAN-007 关闭，本方 **REQ-088 于
  2026-09-23 关闭** ⇒ 当前无开放跨项目行。
- **已关闭（2026-09-30）＝ARCH-011 产物重建批量窗口**（原 2026-09-26 自「长期」改判为支线行、移入 todo「后续待做」节；该节随本轮删除，因它是节内唯一行）：判据重复 ⇒ 终态 `Superseded`／结果 `Partial`。三条直证＝① 待重生对象（DEFECT-004/005、ARCH-018 修复前代际的业务侧产物）已随 2026-09-23 全项目真删物理清除；② 行内唯一具体判据「b05 整本与 5 个缺页」已由当晚重建窗口在真实数据根达成（`012d637df1eb` 317/317 且激活）；③ 「其余书目逐本」是随根侧 sync 与用户下载变动的开放集合，闭集关闭条件写不出，且窗口本体是零代码、人工排期的 D 类数据操作。**取代关系**：重解析清单 ⇒ PLAN-019 S2 端点，逐本重解析 ⇒ 用户按需解析。**真机取证三项改挂**：PLAN-017 真机项②⇒PLAN-018 真机面、ARCH-022 真机读数⇒PLAN-019 W9、PLAN-018「同批出效」约束失效。判据全文与操作规程见 [completed.md](completed.md) 2026-09-30 行与 [PLAN-009 承载节](../plans/2026-09-22-v1-parsing-knowledge-optimization.md)。**todo「后续待做」节由此清空并删除；当前无该形态待做行。**
- **长期**：ARCH-010 文档体系对齐维护；ARCH-012 探索路线图。

## 维护规则

- 服务实现状态、当前主线或能力归属变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 [todo.md](todo.md) / [roadmap.md](roadmap.md)）。
