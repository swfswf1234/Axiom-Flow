# 待做任务

状态：Current
最后更新：2026-09-21

本表是全部未关闭工作的统一导航，按「轮次 → 阶段」分节（排版对齐 QED-Engine 根仓库台账）。v1
台账已归档（`docs/history/v1-20260814/trackers/todo.md`）。任务 ID 采用稳定前缀-序号；已关闭
任务见[关闭台账](completed.md)。当前版本 v0.1 目标见 [项目状态快照](project-status.md)。

## v0.1 主线：解析管理闭环（对齐根仓库 ARCH-020 第三轮）

链路：书目同步（af_books）→ ingest（页图 + 产物落 `QED_DATA_ROOT/parsed/`）→ 解析（引擎适配，
逐页/全本）→ 对照渲染 → 块级人工校验修改（判定/备注/文字/范围）。设计见
[parsing-management](../design/parsing-management.md)、[parsing-pipeline](../design/parsing-pipeline.md)、
[model-integration](../design/model-integration.md)。

**推进顺序**：配置与契约（PLAN-002 Step 1，已交付）→ 数据库与数据根存储（ARCH-013/014/015，
2026-09-20 真机验证并关闭）→ 解析真机打通 + 存储收尾（**PLAN-003**：W0 探测 → DEFECT-002 →
DEFECT-001 → DEFECT-003（2026-09-20 已关闭）→ W7 文档自洽（已完成）→ **收尾三块 C1 真机小样 /
C2 数据库收口 / C3 数据根收口**，承载于其「收尾执行设计」一节；**2026-09-20 收尾轮：C1 真机小样
L1~L8 全绿已达成**，**2026-09-21 收尾二轮：C3 全部达成并关闭、W8 全部完成（8.1 冒烟固化真机绿 +
8.4/W8-g 回执稿）→ DEFECT-002 与 REQ-075 关闭**；本收口面仅剩 C2 的 D3（删除演练需批准）与 D5/W3
（ADR 0005 评审阻塞））→ **归一化正确性修复（DEFECT-004 坐标空间 → DEFECT-005 类型映射）**（2026-09-20
真机新登，对照与块编辑的硬前置；**两项契约均已于 2026-09-20 晋升进 Accepted 设计，无待裁决前置，
可直接 TDD 开工**；**2026-09-21 DEFECT-004 已按 TDD 修复并真机复验关闭**（同时关闭根仓库 REQ-081
的请求面），仅剩 DEFECT-005）→ 产物按版本划分（**PLAN-004**，承接 REQ-080，2026-09-20 立项后 **Blocked** 挂起在
PLAN-003 收尾之后）→ 对照与块编辑联调（ARCH-005/006）→ 代表书验收（ARCH-009）→ 跨项目回执
（REQ-001/REQ-080）。

### 主线聚合

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-001 | 主线 | 高 | In Progress | 解析管理闭环：af_* 四表 + 同步/ingest + 引擎适配 + 编排 + 对照编辑（对齐根仓库 ARCH-020） | 数据库与存储支线已关闭（ARCH-013/014/015，2026-09-20）；**2026-09-21 引擎对齐支线关闭**（DEFECT-002/003 + REQ-075）；剩余：归一化正确性（DEFECT-004/005）→ 产物版本划分（REQ-080/PLAN-004）→ 对照联调（ARCH-005/006）→ Rudin 端到端验收（ARCH-009）；DEFECT-001 按用户裁决保持开放（回归网缺位属已知代价） |
| PLAN-002 | 主线 | 高 | In Progress | [Axiom-Flow 配置简化与解析重构（axiom-flow-refactor）](../plans/2026-09-17-axiom-flow-refactor.md) | **2026-09-20 复核收窄**：Step 1 与 Step 2-A~E 交付、门禁 245 passed + ruff 全绿，DB 与存储支线关闭；**2026-09-20 收口**：Step 3 联调与缺陷修复由 PLAN-003 承接，本计划剩余仅「标准 6/7 随 PLAN-003 关闭即判达成 + 标准 8 回执」 |
| PLAN-003 | 主线 | 高 | In Progress | [解析真机打通：设计偏差核对与契约修正（parse-live-bringup）](../plans/2026-09-20-parse-live-bringup.md) | **2026-09-20 立项并执行 W0**：三方对表产出偏差台账 23 项（A1~A6/B/C/D/E/F）+ 契约条文 11 条（含 1b）；W0 冻结探测 M1~M4 全 PASS（留档 `data/probes/mineru-3.4.4-20260920/`），端点终版与响应信封已成文，新增偏差 A5（`return_content_list` 等默认 false）与 A6（系统代理劫持回环流量）。**W1/W2/W4/W5/W6 代码侧完成**（`mineru.py` 三段化 + 信封双兼容 + 直连；`pipeline.py` 页级 commit + progress 节流；`engines/factory.py` 显式分派 + 提交期 503 + 数据根双闸 + ingest/purge 幂等），门禁 285 → **283 passed** + ruff 全绿（W7 删除重复事实源用例 2 例，基线相应下调），DEFECT-003 随 W6 关闭，完成登记见计划正文。**W7 完成**（F1~F5 文档自洽 + 条文 1~9/11 晋升 design/architecture，含 `database-design.md` 表状态口径改「已实现」与新增「任务表语义」「页表语义」；ADR 0004 正文 8002→5002）。**2026-09-20 收尾裁决**（取消延后、恢复推进，执行面收窄为三块）：C1 解析真机打通**小样止**（单页 + 5 页合成全本 + 真书单页回归，全量与质量验收归 ARCH-009）、C2 数据库存储收口（含 W3 走**路 B**：先评审 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md) 再写真 MySQL 双 Session 测试）、C3 数据根收口（`tmp/axiom-flow/` 改判为**设计表述错误** → 条文 12「不写即合规、存在即缺陷」，零代码；`data/` 重定义为探测/合成样本留档区）。判据清单 L1~L8 / D1~D5 / G1~G6 与工作项 W3-a~c、W8-a~g 见计划「收尾执行设计」。**2026-09-20 收尾轮真机取证完成 C1**：L1~L8 **全绿（小样止）**——L2 真书第 3 页、L3 五页合成整本（5/5 + 18 产物 + chunks 2 块）、L4 进度单调、L5 失败注入、L6 kill 续跑（真 MySQL 21 行留存 → 16/16 补齐）、L7 只读端点、L8 提交期 503 不落行，结论与原始留档见计划「收尾执行登记」与 `data/probes/live-bringup-20260920/`。同时暴露 4 项新缺陷（DEFECT-004 bbox 坐标空间 / DEFECT-005 类型字面值 / DEFECT-006 校验与状态语义 / DEFECT-007 遗留 running）与 1 项待批清理（真机 `af_pages` 第 999 页幽灵行）。**剩余**：C2 的 D3/D4/D5、C3 的 G1/G4/G5 口径晋升、W8 的 8.1 冒烟用例固化与 8.4/W8-g 跨项目回执；外部依赖仅 ADR 0005 评审。DEFECT-001 按用户裁决**不随真机证据关闭**。**2026-09-21 收尾二轮**：8.1 固化完成——`tests/smoke/test_live_mineru_chain.py`（逐页 PNG 形状 + 5 页整本落库与产物齐套 + 数据根 `tmp/` 不存在；默认门禁 `2 skipped`，`QED_SMOKE_LIVE=1` 真机 `2 passed in 7.08s`，5002 `completed_tasks` 48→50）；8.4/W8-g 回执稿成文（PLAN-003「跨项目回执稿」节）→ **DEFECT-002 与 REQ-075 关闭**；C3 的 D4/G5 只读取证成立（凭据零命中 + 仅 `af_*` 四表可触达；三段走查证明生效层恒为本仓库 `.env`，与根 `.env` 值一致）→ **C3 收口面关闭**；新登 DEFECT-008（`.env` 行内注释不剥离的潜伏坏 URL）。**本计划剩余全部为外部/裁决依赖**：C2 的 D3（真机删除演练，需批准）与 D5/W3（ADR 0005 评审）、REQ-001（等根仓库回执）、DEFECT-004~008 修复。**2026-09-21 DEFECT-004 修复并关闭**（本轮开工项，详证据与 REQ-081 回执见计划「DEFECT-004 修复登记」与「跨项目回执稿（REQ-081）」两节；门禁 283 → **292 passed** + 契约 82 + ruff 全绿，真机冒烟 `2 passed`）。本计划仍不关闭：剩余均为外部/裁决依赖项。 |
| PLAN-004 | 主线 | 高 | Blocked | [解析产物按版本划分：versions/&lt;job_id&gt; 落盘与生效指针（req080-parse-version-layout）](../plans/2026-09-20-req080-parse-version-layout.md) | **2026-09-20 立项**（需求对表 + 四项裁决；**同日裁决挂起在 PLAN-003 收尾之后**，当前不实施）。需求对表已完成：根仓库侧 R1~R10（`dataset-conventions.md:64-72` 版本目录与生效视图、`storage-conventions.md:30-31/44-45/62` 原子落盘与测试隔离、`api-contracts.md:558-585` 与 8900 代码证明读取方全走 HTTP、REQ-080 关闭条件需实施回执）；本仓库侧 L1~L10（页级持久化不变式排除「攒完再搬」、ingest 现会 `rmtree` 连带删 `versions/`、`build_manifest` rglob 需排除、页图 100MB 级不宜复制、af_pages/af_block_edits 键无版本维度）。**四项裁决**：① 指针式生效（`af_books.active_job_id`，端点契约零变化）② 页图硬链接自包含（`os.link` 回落 copy）③ 页状态与块编辑随版本（一次 Alembic 迁移，收口 W6 挂账的块下标 remapping）④ 本计划自闭环验证，真机证据并入 PLAN-003 W8。条文 V1~V10 待晋升（V10 已按条文 12 改判为「不引入数据根暂存」），工作项 P0~P8。**布局改动的治理入口是本仓库大修改路径（本计划 + todo），不新增 ADR**（2026-09-20 裁决：根侧 `storage-conventions.md:62` 只约束根仓库）。恢复条件见计划「阻塞与恢复」（2026-09-21 复核后收窄）：PLAN-003 收尾关闭（现仅剩 C2 的 D3 需批准 + D5/W3 等 ADR 0005；C1/C3/W8 已达成）+ **DEFECT-005 修复**（坐标空间面已随 DEFECT-004 于 2026-09-21 关闭，版本目录内块坐标自此可信）+ 真机 `af_*` 迁移窗口批准 + 本计划转 Accepted |

### 真机打通后的遗留卡点：落库回归网与归一化正确性

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| DEFECT-001 | 支线 | 高 | In Progress | 解析进度与页状态不落库：`orchestrator/pipeline.py` 全程只 flush，唯一 commit 在 `orchestrator/jobs.py` 任务结束时；`ParseJobRepository.update_progress` 无调用点 → 实时进度查询与断点续跑在 MySQL 上不成立 | 来源：提交 a5d2e96 代码审查（2026-09-20）。复现缺口：`tests/integration/` 目前无测试文件，DB 测试全为内存/临时 SQLite（`test_db.py`、`test_db_migrations.py`），共享 Session 掩盖了未提交问题。**2026-09-20 W2 代码侧已落地**：页级 `commit`（产物写先于提交）+ `progress` 按 ≥1s 或每 10 页节流 + `_fail` 前置提交 + `persist_per_page` 开关；守护测试改用**文件 SQLite + 独立读连接**复现连接隔离（5 例先红后绿）。**2026-09-20 收尾轮真机证据已取得**：L4 运行中 `progress.parsed` 单调递增（0→1→2→3、9→16）、L5 单页失败不中断且已解析页保留、L6 kill 后真 MySQL 21 页行留存并按「无行 ∪ failed」续跑 16/16。**2026-09-20 用户裁决：本项不因真机证据关闭**（「DEFECT-001 不落库」= 不收口），仍开放的是 MySQL 级提交边界回归网：W3-b 双 Session 测试受 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md) Draft 评审阻塞；关闭时回改 `project-status.md` 的「断点续跑基础」措辞 |
| ARCH-003 | 支线 | 高 | In Progress | 解析编排：af_parse_jobs + af_pages + 逐页/全本 + 重试 + 质量信号（取代 state.sqlite 方案） | 代码与 mock 单测已交付（PLAN-002 Step 2-C），全本/逐页与单页失败不中断语义在测试内成立。**2026-09-20 收尾轮真机四条链路全绿**（整本 L3、逐页 L2、失败注入 L5、kill 续跑 L6 + 进度面 L4），DEFECT-002 的阻塞解除。**收口位置修订**：DEFECT-001 按用户裁决保持开放（回归网缺位属已知代价，不阻断本行的真机结论）；**2026-09-21 8.1 冒烟用例已固化并真机绿**（`tests/smoke/test_live_mineru_chain.py`），本行剩余关闭条件收窄为 **DEFECT-006/007 的语义成文与修复**（均需用户裁决 / ADR） |
| ARCH-016 | 支线 | 高 | In Progress | 引擎适配器：MinerU 接入 + 统一 blocks 归一化（PaddleOCR-VL/qwen-vl 预留）；llm_client/fallback 退役 | 直连配置已对齐（`QED_OCR_MODEL_URL` + 5002，Step 1/2-A，文档与脚本同步）；归一化 mock 单测通过；端点契约已按 3.4.4 改写并在真机两条路径（整本 PDF / 逐页 PNG）验证通过（L2/L3/L7）；引擎槽位分派已完成（`engines/factory.py`，随 DEFECT-003 关闭）。**2026-09-20 真机新发现两处归一化实质缺陷**：bbox 坐标空间误判（DEFECT-004）与 `header`/`footer` 字面值缺口（DEFECT-005），另 `title`/`equation`/`table` 类型真实分布未取证 → 本行改由「DEFECT-004/005 修复 + ARCH-009 代表书类型分布取证」关闭，不再由 W8 单点关闭。**2026-09-21 补**：端点面证据已固化为冒烟用例（8.1），本行与 DEFECT-002 解耦、继续开放。**2026-09-21 DEFECT-004 关闭**（`PageResult.bbox_space` 坐标空间契约 + `scale_bbox` 按空间换算，真机锚点复验 5/5 块 IoU 0.93 / 最大偏移 7px），本行剩余关闭条件收窄为 **DEFECT-005 类型映射修复 + ARCH-009 代表书类型分布取证** |
| DEFECT-005 | 支线 | 中 | Open | 块类型字面值映射缺口：`normalize/blocks.py:_TYPE_MAP` 只有 `page_header`/`page_footer`，真机 `content_list` 实际返回 `header`/`footer` → `block_from_raw` 返回 `None` 把块**静默丢弃**，页内块数与引擎返回数不一致且 `index` 下标漂移 | 来源：PLAN-003 收尾轮留档 `data/probes/live-bringup-20260920/L3d_coord_space.json`（同一次响应同时给出 `header` 与 `footer`）与 `L3b_raw_bbox.json`（样本第 1 页原始 8 项 → 归一化 7 块）。次生风险：`index` 是 af_block_edits 的定位键，映射表变更后同页下标含义改变，与 PLAN-004 裁决 ③（块下标重映射）直接相关。**下一条件**：~~需先裁决「未知类型如何处理」~~ **前置已解除**——2026-09-20 该规则已晋升 Accepted 设计 [parsing-pipeline.md](../design/parsing-pipeline.md)「引擎类型字面值」条（未覆盖字面值必须**可见丢弃**、计入质量信号，不得静默丢块致 `index` 漂移），故本项无待裁决项，可直接按 TDD 补映射与回归测试；`title`/`equation`/`table` 的真实分布取证仍归 ARCH-009 |
| DEFECT-006 | 支线 | 中 | Open | 任务与页范围校验/状态语义未成文：① `pages` 无上界与存在性校验（486 页书接受 `pages=[40,999]` 并写出幽灵 `af_pages` 行）② `job.status=completed` 掩盖部分失败（有失败页仍 `completed`）③ 书级 `af_books.parse_status` 把「本轮任务结果」与「全书是否覆盖完成」混为同一字段 | 来源：PLAN-003 收尾轮真机取证（L5：`L5_polls.txt` 显示 completed 且 `error=失败页：999`；`L9_book_state.txt` 显示真书 `failed 38/486`）。**待用户裁决**：是否新增「部分成功」字面值 or 明确「`completed` + `error`/页行」为唯一判据；`pages` 越界应 422 拒收。**运行库遗留**：本轮取证在真机 `qed.af_pages` 注入的 `linearalgebra-b01` 第 999 页 `failed` 幽灵行**尚未清理**（D 类运行库写操作，需用户批准，不自行删） |
| DEFECT-007 | 支线 | 中 | Open | 被中断的解析任务永久停留 `running`：编排进程被 kill 后无人回收 job 行（无 reaper/心跳/启动期归一），断点续跑实际只依赖 `af_pages`，`af_parse_jobs.status` 在此场景失真 | 来源：PLAN-003 收尾轮 L6（kill 后 `5f43593a12cc` 至今 `running (parsed 14/30)`，见 `L9_final_jobs.txt`）。当前行为不阻断续跑（条文 9 的集合判定不看 job 行），但与 `database-design.md`「任务表语义」的状态描述不一致，且 `GET /parse-jobs/{id}` 会长期显示假运行。**下一条件**：属状态机契约，需 ADR 级裁决修复选项（启动期把遗留 `running` 归 `failed` / 心跳超时列 / 明确「遗留 running 为已知语义」成文）后实施 |
| DEFECT-008 | 支线 | 中 | Open | `.env` 解析不剥离**行内 `#` 注释**：`config._env_file_values` 只跳过整行注释，值后的注释会连同空格并入配置值 → 若上层 `.env` 写成 `KEY=value # 说明` 且本仓库 `.env` 缺该键，生效值即为坏 URL/坏路径 | 来源：2026-09-21 PLAN-003 收尾二轮 G5 三段走查（根 `.env:20` `QED_OCR_MODEL_URL=http://127.0.0.1:5002   # 图像模型地址（5002）`，当前因本仓库 `.env:17` 优先生效未触发；留档 `data/probes/live-bringup-20260920/D4_G5_config_credential.txt`）。修复属 B 类一行改动 + 单测（值尾部 ` #…` 剥离，保持引号内 `#` 不误伤）；根仓库 `.env` 只读，不由本仓库改动 |

### 对照、分块与验收

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-004 | 支线 | 中 | Open | 分块模型：blocks → chunk（paragraph/formula/table 为切分点，为 RAG 预留） | 已实现（Step 2-D：`chunks.json` + `GET /chunks` 参数可重算），规则单测通过；真机派生文件已在 linearalgebra-b01 产物目录生成。**2026-09-20 待办**：chunks 随真机产物重建才具意义（PLAN-003 W2 页级持久化 → W8 冒烟）；REQ-080 版本划分落地后 chunks.json 归属版本目录需同步约定（PLAN-004） |
| ARCH-005 | 支线 | 高 | Open | 解析对照与质量度量：页图 vs 重渲染 + 质量信号落盘 | 质量信号字段随页产物落盘（Step 2-C），真机页产物与 `quality` 已在 L2/L3/L7 取证；对照视图待 8903 前端联调（根仓库 ARCH-020-D/ARCH-020-UI）。**2026-09-20 前置新增**：叠框依赖 bbox 正确性 → [DEFECT-004](todo.md)（坐标空间）与 [DEFECT-005](todo.md)（类型丢弃致块数不一致）修复前不得开始对照联调，否则前端按错误坐标定位会形成二次返工。**2026-09-21 前置收窄**：坐标空间面已随 DEFECT-004 关闭解除（块 `bbox` 已真机复验贴住原文字，IoU 0.93），本行仍受 **DEFECT-005** 阻塞（类型丢弃会造成同页块数与 `index` 漂移，对照两侧对不齐） |
| ARCH-006 | 支线 | 高 | Open | 渲染数据供给与块编辑：pages/blocks/image/manifest + edit 端点（af_block_edits） | 端点与 af_block_edits 仓储已实现（Step 2-C/2-E），契约测试全绿；真机读取面 L7 全绿（含 `image_url` 字面量不变量）；真机 af_block_edits 仍 0 行，edit 写入与 pages 合并回显待前端联调。**2026-09-20 前置新增**：`corrected_bbox` 与块 `index` 定位受 DEFECT-004/005 影响（下标漂移会污染编辑键），修复前不写入真机编辑数据。**2026-09-21 前置收窄**：`corrected_bbox` 的坐标空间已随 DEFECT-004 关闭恢复可用；仍受 DEFECT-005 的 `index` 漂移阻塞 |
| ARCH-009 | 支线 | 中 | Open | 套1 代表书验收（教程 Rudin 中译 + 习题集吉米多维奇） | 前 20 页冒烟 → 全量（公式可渲染 ≥ 90%），需 MinerU 5002 在线；~~前置 DEFECT-002 解除~~ **2026-09-20 真机证据已取得**（L2/L3）。剩余前置：~~DEFECT-004/005 修复~~ **2026-09-21 DEFECT-004 已关闭**，剩 DEFECT-005（否则块级质量判定失真）+ 真实 `title`/`equation`/`table` 类型分布取证（合成小样 40 项只有 `text`/`header`，不足以证明映射可用，见 `L3b_raw_bbox.json`）；另需在修复后代码上重跑代表书页产物（既有 `parsed/` 落盘块是修复前的错误坐标，换算修复不回溯历史产物） |

### 跨项目请求

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| REQ-001 | 支线 | 高 | Open | **请求发出（目标：QED-Engine）**——8900 数据域适配 af_* 新契约（books CRUD/ingest、`/review`→`/edit`、chunks 透传） | 根仓库 2026-09-20「联调最小打通轮」已自行修复四处契约断裂（review→edit 门面、sync file_path、parse-jobs engine/id/progress、page_no/image_url 补写，见根仓库 BUGFIX-005）。**2026-09-21 本方复核材料已出**（PLAN-003「跨项目回执稿（W8-g）」：L7 九端点 + `image_url` 不变量 + 契约测试对照），两项延后告知已写明——① DEFECT-004 修复前块坐标不可用于叠框，② PLAN-004 版本布局后 `versions/<job_id>` 与生效指针需再回执。**2026-09-21 延后告知 ① 已解除**：DEFECT-004 关闭 + REQ-081 回执成文 → 块 `bbox` 恢复可用于叠框（新解析产物；历史产物需重跑）。**关闭条件**：根仓库读取材料并在其 todo 登记回执 |
| REQ-081 | 支线 | 高 | In Progress | **请求接收（QED-Engine）**——MinerU 解析块 bbox 坐标失真：前端对照框与原页文字不对齐（`GET /books/linearalgebra-b01/pages/7` 全部段落块 `x1` 恒等于 1241＝图像右缘，块高 1–3px 的病态窄块） | 2026-09-21 本方接收并判定：**与自家 DEFECT-004 同一缺陷**（根仓库从前端症状报，本方从适配层定因），根仓库怀疑的两条路径均成立。**答疑**：`content_list[].bbox` 实测为**相对页框逐轴归一化 0–1000**，既非 PDF 点也非像素；`middle_json.pdf_info[].page_size` 对 PNG 输入回的是按 200 DPI 折算的标称点（1241x1755 → `[446,631]`），与归一化 bbox **确实错配**，不能作缩放基准。**症状复算吻合**：把 0–1000 当点按 `image/page_size` 过放＝x 轴 1.68 倍、y 轴 1.19 倍 → 右缘/底缘精确夹取，正是 `x1=1241` 与贴底 1px 块。**修复与回执**：DEFECT-004 已关闭（`bbox_space` 契约 + 按空间换算；单测锚点判据 + 真机复验 IoU 0.93/偏移 ≤7px + 冒烟对齐断言，反转旧空间即红），回执正文见 PLAN-003「跨项目回执稿（REQ-081）」。**待本方**：`linearalgebra-b01` 受害页需真机重解析才能产出正确坐标（写真实 `dataset/parsed/` + 共享 `qed`，D 类，待用户批准；未批准前不自行重跑）。**关闭条件**：根仓库以其 §13 验收复测通过后回执，本方随之关闭本行 |
| REQ-080 | 支线 | 中 | Open | **请求接收（QED-Engine）**——解析产物按解析版本划分：每次 job 产物原子落 `parsed/<domain>/<course>/<book_id>/versions/<job_id>/`（页图/blocks/markdown/manifest 全套），book_id 根目录保持「当前生效版本」视图（读取方与端点契约不变），af_parse_jobs 记录生效版本指向 | 2026-09-20 本方接收确认（现状：重复解析覆盖落盘、无版本留痕）；本方产物布局事实源见 [parsing-pipeline](../design/parsing-pipeline.md)，根仓库侧约定见其 dataset-conventions 正文；实施需 af_parse_jobs 增量迁移 + 落盘改造，属数据布局变更，按变更分级先立计划。**2026-09-20 裁决**：拆 PLAN-004 后置，不在 PLAN-003 内做 DDL；**2026-09-20 PLAN-004 立项**（需求对表 R1~R10 / L1~L10 + 四项裁决 + 条文 V1~V10 见计划）。当前前置：PLAN-003 收尾三块（真机打通 + 数据库 + 数据根）关闭 + 真机 `af_*` 迁移窗口批准（D 类备份）；布局改动走本仓库大修改路径，**不新增 ADR**。版本布局落地后在版本目录上复跑真机证据。**2026-09-21 前置更新**：归一化正确性前置解除一半——DEFECT-004（坐标空间）关闭后版本目录内的块坐标可信，DEFECT-005 仍开放；实施承载体是 PLAN-004（其「阻塞与恢复」为状态事实源），回执在其收口时发出 |


## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护：固定文档随实现同步；版本末期规范化整理轮 | **2026-09-20 本轮**：台账改按轮次/阶段排版并同步 DB 与存储真机验证事实；`project-status.md` 断点续跑措辞按 DEFECT-001 下修。遗留待整理：design 与 architecture 确认状态补登记、`tests/support` 等空目录残留清理 |
| ARCH-011 | 长期 | 低 | Open | 批量处理其余书籍 | ARCH-009 关闭后拆 D 类计划执行 |
| ARCH-012 | 长期 | 低 | Open | 探索路线图：Milvus 检索 / 知识图谱 / 数学解析器 / 学习交互闭环 | 见 [路线图](roadmap.md)，按需立项 |

## 规则

- **ID 前缀**：`ARCH`=主线/支线实现；`REQ`=跨项目请求；`PLAN`=计划类（必有 `plans/` 正文）；
  `DEFECT`=缺陷。
- **类别**只允许 `主线 / 支线 / 长期`：主线 = 大类目标（聚合支线）；支线 = 主线细则，可独立
  完成；长期 = 持续存在、无单一终态。
- **状态**只允许 `Open / In Progress / Blocked`；阻塞必须声明证据、恢复条件和责任位置。
  `PLAN` 行状态必须与 `plans/` 正文 `状态：` 字段逐字一致（契约测试守护，故不使用中文状态词）。
- **优先级**只允许 `高 / 中 / 低`。
- **排版**：按「轮次 → 阶段」分节，每节开头一句**推进顺序**；跨项目请求以
  `**请求接收（来源仓库）**——` / `**请求发出（目标：仓库名）**——` 粗体标记；「证据/下一条件」
  列按 `**YYYY-MM-DD 事件**` 追加日期化事实，不改写历史结论。
- 涉及子项目改造的请求在本表登记并标注目标仓库；根仓库不直接修改子项目文件
  （见[跨项目协作流程](../standards/cross-project-collaboration.md)）。
- Plan 行镜像 `docs/plans/` 活跃计划正文（标题、链接、状态、关联 Tracker）。
- 任务终态时从本表原子移除并写入 [关闭台账](completed.md)。

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
