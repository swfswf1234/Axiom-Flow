# 项目状态快照

设计状态：Accepted
实现状态：In Progress
确认状态：暂定
最后更新：2026-09-21
关联代码：无（状态快照，不映射具体模块）
关联测试：无
关联 ADR：[ADR 0001](../adr/0001-v2-exploration-direction.md)、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)

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
| orchestrator 任务编排 | af_parse_jobs + af_pages | 部分实现 | 全本（pages 缺省）/逐页；单页失败不中断。**2026-09-20 PLAN-003 W2 代码侧修复**：每页成功/失败即 `commit`（产物写先于页行提交）、`progress` 按 ≥1s 或每 10 页节流、终态收尾必写。**2026-09-20 真机已证**：运行中 `progress.parsed` 单调递增（L4）、失败页不中断任务（L5）、kill 服务后按「无行 ∪ failed」续跑且前序页不重跑（L6，真 MySQL `qed`）。**仍开放**（用户裁决：不因真机证据关闭 [DEFECT-001](todo.md)）：MySQL 级提交边界回归网缺位（W3-b 双 Session 测试受 ADR 0005 评审阻塞）；新登记语义缺陷——被中断任务永久 `running`（DEFECT-007）、`completed` 掩盖部分失败与 `pages` 无上界校验（DEFECT-006） |
| af_* 书目同步与块编辑 | MySQL `qed` 库 af_* 私有四表 | 已实现（真机已建表；版本布局待真机迁移） | 四表一次迁移（`src/axiom_flow/migrations/`）+ 仓储层；sync 幂等、edit upsert 覆盖。**2026-09-20 真机**：`qed` 库四表存在、`alembic_version = 20260917_0001`，af_books 4 行；缺 MySQL 隔离集成测试（Step 3.3）。**2026-09-21 PLAN-004 实施**：迁移 `20260920_0002`（`af_books.active_job_id` 生效指针 + `af_pages` 主键与 `af_block_edits` 唯一键纳入 `job_id` + `af_parse_jobs.source_sha256/page_count` + `engine` 死默认值 DROP）与仓储/编排/API 读取面全部落地，S1~S6 在 SQLite + `tmp_path` 上取证；真机 `qed` 未迁移（B→C→D 单窗口待批准） |
| 分块（blocks → chunks） | `chunks.json` + `GET /chunks` | 已实现 | 派生文件、不入库、可重建；参数（max_chars/overlap_blocks）可经端点重算。2026-09-20 真机产物已生成 `chunks.json`；**REQ-080 归属约定已成文**：`chunks.json` 每个解析版本一份、落 `versions/<job_id>/`，`GET /chunks` 读生效版本 |
| 套1 代表书验收 | 教程 Rudin 中译 + 习题集吉米多维奇 | 待验证 | ARCH-009；分阶段验证（前 20 页冒烟 → 全量），需 MinerU 5002 在线。 |

## 当前主线

- **本轮主攻（全库唯一，2026-09-21）**：**PLAN-004 解析产物按版本划分（REQ-080）**——P2~P7 已实施
  并**完成本地闭环**（迁移 `20260920_0002` + 版本目录写入/读取 + ingest 保版本 + 六份文档晋升，
  门禁 `352 passed, 2 skipped` + 契约 94 + ruff），剩余仅真机 **B→C→D 单窗口**（D 类待批准）与
  REQ-080 回执发出；本计划**尚不可关闭**（判据拆分见其正文「关闭与归档」）。
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
  B→C→D：本地全绿 → 一个窗口内 `af_*` 备份 + `alembic upgrade 0002` + 清 `af_pages` 幽灵行 + 发布 →
  在版本布局上**单次**重解析 b01 前 20 页（同时承载 REQ-081 的根侧 §13 复测前提与 PLAN-003 W8 取证）；
  不在旧扁平布局重复真机解析，否则迁移后那批页行会被回填成 `legacy` 哨兵而立即作废。REQ-080 优先级同时对齐
  根仓库（高）。**2026-09-21 五轮（实施完成，本地闭环）**：P2~P7 全部落地——迁移 0002 + 仓储版本作用域、
  `versions/<job_id>/` 写时复制播种（`seed_version_from`，页图硬链接）、终态激活门槛、`api/` 读取面单一入口
  `_read_scope`（URL 与 `image_url` 字面量零变化）、`GET /books/{id}/versions`、ingest 只交换根视图并保历史版本、
  `engine` 死默认值随迁移 DROP（销 PLAN-003「W7 发现 2」）。门禁 **303 → 352 passed, 2 skipped**（契约 82 → 94）
  + ruff 全绿；S1~S6 取证矩阵与实现口径见计划「实施登记」，REQ-080 回执稿与待根确认清单见同计划「实施回执稿」节。
  **真机面仍待 C/D 窗口批准**（迁移 + 幽灵行清理 + 单次重解析），未获批前不执行任何 D 类动作。
- **已关闭（2026-09-20）**：ARCH-013 af_* 四表建表与仓储层、ARCH-014 书目同步与 CRUD、
  ARCH-015 ingest 与 `QED_DATA_ROOT/parsed/` 布局（真机迁移 + 486 页 E2E 证据见
  [completed.md](completed.md)）。
- **待启动**：ARCH-009 套1 代表书验收（前置 DEFECT-002/004/005 已于 2026-09-21 全部关闭，剩余前置为
  MinerU 5002 在线 + 在修复后代码上重解析代表书 + 块类型真实分布取证）；REQ-001 请求 8900 适配新契约
  （`/review`→`/edit`、ingest/chunks 透传，根仓库侧 2026-09-20 已自修四处断裂，本方复核材料 2026-09-21
  已出、等根仓库回执后关闭）；REQ-080 解析产物按版本划分。
- **长期**：ARCH-010 文档体系对齐维护；ARCH-011 批量解析；ARCH-012 探索路线图。

## 维护规则

- 服务实现状态、当前主线或能力归属变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 [todo.md](todo.md) / [roadmap.md](roadmap.md)）。
