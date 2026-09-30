# 解析产物可信与增量重跑实施（parse-artifact-trust）

状态：Accepted
任务类型：B
最后更新：2026-09-28
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0007](../adr/0007-parse-status-semantics.md)（`completed` 与激活同一判据——P2 的 DB 单口径必须与之对齐）、[ADR 0009](../adr/0009-data-root-must-be-explicit.md)（数据根 fail-closed，W6 残骸清点只读不自动创建）、[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型版本探针归 8900 侧能力，P1 只消费 `/health` 读数）
关联设计：[parsing-pipeline.md](../design/parsing-pipeline.md)（blocks 归一与分块规则）、[parsing-management.md](../design/parsing-management.md)（af_* 表族与版本生命周期）、[database-design.md](../architecture/database-design.md)、[api.md](../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-019 镜像行；承载 ARCH-022，自 PLAN-009 转入）
归档判定：Retain（含 P 系列契约草案与逐条实施判据，关闭时按当时里程碑归档）

> **登记沿革**：本壳承接 PLAN-009「ARCH-022 · 解析面优化（P 系列）」的 P1/P2/P3 三条（P4 暂缓、
> P5 无动作不入本壳）。2026-09-28 用户裁决立项实施，并对三条原口径给出**四项改判**（见下），
> 本壳是当前口径的唯一事实源，PLAN-009 壳的 P 系列表只保留来源指向。

## 用户裁决（2026-09-28，本壳口径的上界）

| # | 裁决 | 对 P 系列原口径的改判 |
| --- | --- | --- |
| ① | P1 最终结果**只记录一份正本**：DB 与 `QED_DATA_ROOT` 各一份、一一对应；确认过时后**清理旧数据，含 `tmp` 残骸** | 原 P1 只管「要不要重跑」，不管产物份数。现新增：正本配对唯一性 + 过时清理为 P1 的组成部分（清理执行属 D 类，见 W6/Q7） |
| ② | P2 就绪口径**以 DB 元数据为准**；可以做页级记录，但文件系统不得成为第二判据 | 推翻 PLAN-009 原判定「manifest-last 作为抗崩溃就绪标记」。**改判为：DB 是唯一就绪事实源**，manifest 降为完整性校验数据；S3（崩溃不产生假就绪）改由 DB 提交序保证 |
| ③ | 分块（RAG 消费的前置）必须在**每页由管理员用户确认之后**才做 | 原 P3 只管归一分诊。现新增「人工确认门」：chunking 只聚合已确认页，未确认页逐页可见排除。P3 扩为「语义分诊 ledger + 确认门」两件 |
| ④ | P4 多引擎 bridge 暂缓、P5 双轨 IR 无动作（维持 PLAN-009 原判） | 不入本壳 |

> 同日第二轮评审对 ①③ 与各悬置项给出更细口径，见文末「评审裁决（R1~R4）」；**R1~R4 是本壳
> 的最上界**，与之冲突的本文其他措辞以 R 表为准。

## 目标与成功标准

**目标**：解析层从「能解析出来」升级为「**增量可判、就绪单一口径、取舍有账、消费过人工门、
旧数据可清**」。服务的现场 = ARCH-011 批量重建窗口（重跑清单作为其 W0 输入）与 PLAN-018
区域驱动重解析的常态化重跑。

| ID | 成功标准（可检验） |
| --- | --- |
| S1 | `af_parse_jobs` 有 `parser_signature` 列（迁移落地），签名输入集冻结成文且**只含影响块产物的参数**；`(source_sha256, parser_signature)` 双全等 ⇒ 默认不新建重跑；重跑判定单测 + 契约测试常驻 |
| S2 | 只读重跑清单端点交付：逐本给出 `need_reparse`/`resume`/`up_to_date` 判定与原因，可直接作为 ARCH-011 W0 读数 |
| S3 | 就绪口径 DB 单源成文（job 级终态 + 书级生效指针 + 页级 `af_pages` 行）：全仓读取面不存在「以 manifest 存在与否判就绪」的路径，守护用例证明撤销/伪造 manifest 不改变任何 API 就绪读数 |
| S4 | 块策略表版本化（`_TYPE_MAP` + 分块过滤规则），未知字面值**宁收不丢**并落逐块 ledger（可定位 页/字面值/原文摘要/策略版本/处置）；既有 `dropped_block_count` 语义被 ledger 覆盖且不回归黑箱 |
| S5 | 页级确认门达成：确认态落 `af_pages`（DB 单源），chunk 重建只聚合已确认页，未确认页在响应与 ledger 中**可见排除**（不静默）；确认接口三形态齐备（单页 / 页区间批量 / 按页重解析后回到未确认），写入走管理面口径（token 校验，判例同 PLAN-010），**系统不得自动整本背书** |
| S6 | 过时清点交付：作用域已裁＝**只有生效版本目录 + 在飞/可续跑任务目录留存**，其余为过时。只读清单给出 `(正本配对唯一性 / 残骸 / 待清理)`：每个 `active_job_id` 恰对应一个版本目录、每个非 `legacy` 任务行恰对应一个版本目录或为已清理态；`.tmp-`/`.old-` 三式残骸出现在清单中。**页图例外**：图像类产物保留物理形态（用户理由＝截图存物理链路更合适），清理不得毁掉根视图 `pages/` 与生效版本页图（硬链接语义须自证）。自动删除的执行判据成文（执行本身属 D 类，另批批准） |

## 范围与非目标

- **范围**：`af_parse_jobs`/`af_pages` 扩列 + 迁移；`orchestrator/` 受理前重跑判定与落库顺序；
  `normalize/` 策略表版本化 + ledger；chunking 确认门；管理面/读取面最小端点增量；
  `api.md`/`database-design.md`/`design/` 同步；过时与残骸只读清点。
- **非目标**：知识库面 R1~R4（ARCH-023，另壳）；P4 多引擎、P5 双轨 IR；区域驱动重解析与块结构
  编辑本体（PLAN-018，本壳只处理与其的迁移链和语义衔接）；8903 前端界面；**任何业务库真实删除
  执行**（W6 只出清单与判据，批量/全项目清理属 D 类，逐步批准）；引擎侧参数与模型部署（ADR 0004）。

## 前置条件

1. **迁移链串行**：PLAN-018 已排迁移 `20260926_0002`（`af_page_regions`，待 ADR 0010 确认）。
   本壳迁移 `20260928_0001` 的 `down_revision` **接在 PLAN-018 合入之后**，不并写同版本号
   （判例＝PLAN-018 前置 2）。若 PLAN-018 确认前本壳先行，则占位改指当时 head，开工时以
   `alembic heads` 实读为准。
2. **表数措辞零增量**：本壳只加列、不新增表 ⇒ 不触发 PLAN-018「第 11 节」的 4→5 表同步面；
   加列的列清单同步由 `test_column_list_matches_orm_columns` 守护，与迁移同批晋升。
3. **模型版本探针可用性未证**：P1 签名输入是否含 MinerU 模型版本，取决于 `:5002/health` 是否
   回版本字段（现有 `model_load()` 只读 `queued_tasks/processing_tasks`）。探不到则退化为
   `engine + 输出开关 + 窗口` 并在设计留注（Q3）。模型部署与版本管理不归本仓库（ADR 0004）。
4. **与 PLAN-018 的语义衔接**：`af_page_regions`（页级解析输入，跨版本）与本壳的页确认态
   （页级质量判定，版本作用域）是两回事，不得共表共列；开工前对齐一次口径（Q9）。
5. **晋升纪律**：确认端点与重跑清单端点在挂载前不得进 `api.md` 端点总表
   （`test_api_endpoint_table_matches_runtime_routes` 双向对账）；草案先留本壳，随实现同批晋升。
6. **真机面**：业务库 `qed` 已升 `20260926_0001`（2026-09-28 事实更正，见 W8）；本壳新迁移在
   业务库上的 apply 与真实数据根的清理执行，全部归 ARCH-011 窗口或单独 D 类批准，不在默认门禁。

## 契约草案（评审前唯一承载处，未晋升）

### C1 · `parser_signature`（P1）

- **输入集（2026-09-28 已裁 R4，取证见「W1 勘察登记」节）**：`AXIOM_OCR_ENGINE`、MinerU `/health`
  的 `version` **与 `protocol_version`**（两者均已实测可读）、`_OUTPUT_FLAGS` 产物开关
  （`engines/mineru.py:44`，代码级常量 ⇒ 并入 `policy_version`）、`AXIOM_PARSE_WINDOW_SIZE`
  （影响 PDF 提交跨度与跨页推断 ⇒ 影响产物）。
  **排除 `AXIOM_OCR_TIMEOUT`**：只影响成败与进度、不改块内容（判据：影响产物才入签名）。
  程序约束（用户裁决）：**W1 已把 `/health` 实际回文取证成文**（原文字段名与读数见「W1 勘察登记」节，
  含留档字节 sha256）；探不到版本字段时不臆造，退化为
  `engine + 输出开关 + 窗口` 三段并在设计留注「模型版本变化由人工触发重跑清单复核」（实测无需退化）。
- **canonical 形式**：键按字典序的 JSON 规范化串 → SHA-256；落 `af_parse_jobs.parser_signature`
  （`String(64)`，迁移回填哨兵 `''` ＝ 未知代际，**不回填伪造值**，判例同 `ingested_at` 不回填）。
- **判定语义**：`need_reparse` ⟺ 生效版本 `(source_sha256, parser_signature)` 与当前书快照 +
  当前签名不等；`resume` ⟺ 同 `(book_id, job_id)` 续跑（现状语义不变）；双全等 ⇒ `up_to_date`。

### C2 · `POST /api/v1/parse-jobs` 补句（重跑门）

> 受理前对 `pages:null`（全本）请求判签名命中：命中 ⇒ 默认 **409**（响应含生效 `job_id` 与
> 判定原因），携带 `"force": true` 才创建新任务；`force` 为 `ParseJobCreate` 新增可选布尔字段，
> 缺省 false ⇒ 现有契约形状向前兼容。页区间/逐页请求**不受门**（人工意图明确，判例同区域驱动）。

### C3 · `GET /api/v1/admin/reparse-plan`（草案，管理面挂载纪律）

> 复用 PLAN-010 装配开关与 token（不挂即 404、`X-QED-Admin-Token` 逐字比对）。只读返回逐本
> `{book_id, active_job_id, source_sha256, parser_signature, verdict, reason}`；
> 无 `active_job_id` 的书 verdict=`never_parsed`。8900 **不得透传**（管理面纪律沿用）。

### C4 · 页确认态与确认接口（P3 之半，2026-09-28 已裁 R2）

> `af_pages` 新增 `confirm_state`（候选：`String(16)`，`unconfirmed`/`confirmed`/`rejected`，
> 默认 `unconfirmed`；作用域随 `(book_id, job_id, page_no)`＝**版本内判定，不跨版本继承**，
> 判例同 `af_block_edits`）。
>
> **确认粒度三形态（用户裁决 R2，三者必须都可用）**：
> ① **单页确认**（整页 PASS）；② **页区间/页列表批量确认**（`pages:[a,b]` 或 `{from,to}`，
> 一次确认一段）；③ **按页重解析**（复用既有 `POST /parse-jobs {pages:[n]}`，重解析后该页
> 确认态回到 `unconfirmed`＝人的旧背书不自动转移到新结果）。
> 原草案「不做批量自动确认」的措辞**作废**——批量由**人显式发起**即有效，禁止的是**系统自动**
> 把整本置为 `confirmed`（无请求体的整本一键确认不提供）。
>
> 端点草案：`PUT /api/v1/admin/books/{book_id}/pages/confirm`
> （body `{pages:[…]|{from,to}, state:"confirmed"|"rejected", job_id?}`；`job_id` 缺省取生效版本；
> token 校验沿用 PLAN-010；响应回该批页的 `confirm_state` 读数）。管理面挂载纪律同 C3
> （不挂即 404，8900 不透传）。`GET …/pages/{no}` 响应追加 `confirm_state`（向前兼容）；
> 页清单端点追加确认态计数，供「还剩哪些页没过」的推进视图使用。

### C5 · ledger 与 chunk 门（P3 另半，落点 2026-09-28 已裁 R3）

> **ledger 落点已定＝`af_pages.quality`（JSON）追加键，零迁移成本、作用域天然随页行**：
> `dropped_items: [{literal, count, sample}]` 逐字面值明细 + `policy_version`。守护面只涉
> `quality` 键形状（单测断言），不触 `test_column_list_matches_orm_columns`。
> 未知引擎字面值不再丢弃：收为兜底类型（候选 `PARAGRAPH`，Q6-a **仍待裁**——新增 `unknown`
> 语义要动 `BlockType` 十种契约，PLAN-018 K4 判例示警）并计数入 ledger。
> chunk 门：`rebuild_chunks` 只聚合 `confirm_state=confirmed` 的页；响应与 ledger 可见排除
> （`chunks.json` 记 `excluded_unconfirmed: [page_no,…]`，不静默）。门只在**重建时**读 DB，
> 不在读取时过滤（`GET /chunks` 契约保持产物原样）。

## W1 勘察登记（2026-09-28，`/health` 回文取证成文）

R4 的程序约束在此了结：签名是否含模型版本，**以实际回文字段名为准，不臆造**。

### 取证来源

| 项 | 读数 |
| --- | --- |
| 实时探针 | `GET http://127.0.0.1:5002/health` → **连接拒绝**（WinError 10061，MinerU 容器未启动）⇒ 本轮以真机留档取证；字段形状由 W9 复验、ARCH-011 窗口真机时再核 |
| 留档载体 | `tmp/probes/mineru-3.4.4-20260920/raw/001_health_200.bin`（与 `013_health_200.bin` 逐字相同，各 257 B） |
| 字节指纹 | sha256 `e8890befd049e0cd36a7c641d52047cca332716fda251f0823707795a3d81f7d` |
| 汇总记录 | `tmp/probes/mineru-3.4.4-20260920/summary.md`（PLAN-003 W0 探测结论） |

### `/health` 回文原文（11 字段，按字典序）

```json
{"completed_tasks": 2, "failed_tasks": 0, "max_concurrent_requests": 3, "processing_tasks": 0,
 "processing_window_size": 64, "protocol_version": 2, "queued_tasks": 0, "status": "healthy",
 "task_cleanup_interval_seconds": 300, "task_retention_seconds": 86400, "version": "3.4.4"}
```

### 逐字段裁决（判据＝影响块产物内容才入签名）

| 字段 | 读数 | 入签名 | 理由 |
| --- | --- | --- | --- |
| `version` | `"3.4.4"` | **是** | 服务版本＝解析实现代际；`tmp/probe_mineru_api.py:332` 已判「真实服务版本以 `/health` 为准（openapi info 只有 FastAPI 框架版本）」 |
| `protocol_version` | `2` | **是** | 决定结果载荷形状（`content_list`/`middle_json` 语义），变化必然改产物 |
| `status` | `"healthy"` | 否 | 运行态读数，与内容无关 |
| `queued_tasks` / `processing_tasks` / `completed_tasks` / `failed_tasks` | 计数 | 否 | 负载观测（`model_load()` 已消费），与内容无关 |
| `max_concurrent_requests` | `3` | 否 | 并发度不改块内容；本仓单占用闸门已使其对本侧无意义 |
| `processing_window_size` | `64` | 否 | 服务内部分批窗口；改我方产物跨度的是 `AXIOM_PARSE_WINDOW_SIZE`（PDF 提交窗口，已在签名，Q4） |
| `task_retention_seconds` / `task_cleanup_interval_seconds` | `86400` / `300` | 否 | 服务端任务表寿命，与产物内容无关 |

### 结论与残余盲区

1. **可记**：`/health` 确实回版本 ⇒ 签名含 `service_version` + `protocol_version` 成立，**无需退化**。
2. **残余盲区（新增悬置 Q11）**：`/health` **不回模型/后端标识**（无 `backend`/`model` 字段）。而
   **结果载荷回**——留档 `raw/023_single-result_200.bin` 顶层为
   `{backend:"hybrid-engine", version:"3.4.4", results:{…}}`（`024` 同）。⇒ 同一 `version` 下换后端/换权重
   （8900 侧部署动作，ADR 0004 不归本仓）对签名**不可见**。建议：签名形状不变，pipeline 在首个结果
   返回时把 `backend` 写入 `af_parse_jobs.progress`（JSON 追加键，零迁移），重跑清单在
   「生效版本 backend ≠ 最近任务 backend」时以 `model_drift_suspect` **报警而不自动判过时**。
3. **策略版本承载位定判**：`policy_version` 取 `normalize/` 侧**代码级派生**值（`_TYPE_MAP` 全量映射
   ＋分块跳过集＋`_OUTPUT_FLAGS` 三处常量的规范化哈希）。不加列、不加配置位；理由＝三者都是代码级
   常量，改动对开发者可见，另设配置位会造出「配置写一套、代码算一套」的第二判据（违裁决 ②）。
4. **迁移链实读**：`alembic heads` → `20260926_0001`（PLAN-018 的 `20260926_0002_page_regions` 尚未落
   文件）。⇒ 本壳迁移取 `20260928_0001`，`down_revision = '20260926_0001'`；**PLAN-018 后续合入时须把
   自己的 `down_revision` 改指本壳迁移**，否则出现双 head（前置 1 的占位改判已发生）。

## 工作项

| # | 工作项 | 依赖 | 状态 |
| --- | --- | --- | --- |
| W0 | 立项收编：本壳入册 + todo 镜像（ARCH-022 改指本壳、PLAN-019 新行）+ PLAN-009 P 系列表注记改挂；治理契约绿 | — | 完成 2026-09-28 |
| W1 | 勘察冻结：`/health` 版本字段**取证成文**（回文原文入本壳登记，R4 程序约束）、签名输入集定判、策略版本承载位定判（C1 落档）；产出后**停下等用户确认无争议**再进 W2 | — | 完成 2026-09-28（取证与定判见「W1 勘察登记」节） |
| W2 | P1 落库：`parser_signature` 加列迁移 + 建/读/判仓储 + canonical 串单测（先红后绿） | W1 | 待开始 |
| W3 | P1 受理门：`force` 字段、全本命中 409、页区间不受门；契约测试（含 force 缺省形状不变反向判据） | W2 | 待开始 |
| W4 | S2 重跑清单端点 + ARCH-011 W0 供数口径成文 | W2 | 待开始 |
| W5 | P2 DB 单口径：落库提交序成文（页行提交先于/伴产物写，现状保持）+ 「manifest 非就绪判据」守护用例 + 设计措辞同步 | — | 待开始 |
| W6 | P3 分诊：策略版本表 + 未知宁收 + ledger 明细（落 `af_pages.quality`，R3）+ chunk 确认门 + 确认端点三形态（R2：单页/批量/按页重解析回未确认）+ 页清单确认态计数 | W5 | 待开始 |
| W7 | 过时清点（裁决 ①，作用域已裁 R1）：只读清点脚本/端点——正本配对唯一性、孤儿任务行、孤儿版本目录、`.tmp-`/`.old-` 三式残骸、**页图物理保留例外与硬链接自证**；清理执行顺序与安全边界成文（先磁盘后库、重跑幂等判例沿用 PLAN-010）；**执行另批 D 类批准** | W2/W6 | 待开始 |
| W8 | 文档晋升：`api.md`（C2/C3/C4 小节与总表，随挂载同批）、`database-design.md` 列清单、`parsing-pipeline/management` 口径、`project-status` 刷新；**同批更正** PLAN-009/ARCH-011 中「业务库升迁移属窗口前置」的过时记载（业务库已 `20260926_0001`） | W3~W7 | 待开始 |
| W9 | 门禁与真机：默认门禁全绿 + `QED_TEST_MYSQL=1` 迁移往返 + 变异复验（撤签名门/撤确认门 ⇒ 对应用例转红）；真机面只并入 ARCH-011 窗口 | W8 | 待开始 |

## 验证与验收

- TDD 先红后绿；单测只用临时目录与 `tmp_path`，禁读写真实数据根（ADR 0009）；
  真 MySQL 面只在 `qed_test`（ADR 0005 铁律：只 DROP 自有 `af_*` + `alembic_version`）。
- 关键反向判据：① 伪造/删除 manifest ⇒ 各 API 就绪读数不变（S3）；② 未确认页不出现在
  chunks 且排除可见（S5）；③ 签名相同不带 force ⇒ 409 且零任务行、零模型调用（S1）；
  ④ 未知字面值不再使块消失、ledger 计数与原始项对账闭合（S4）；
  ⑤ 人工显式批量确认 ⇒ 生效，而**无任何请求可让系统把整本自动置 `confirmed`**（R2）；
  ⑥ 清理作用域判据在「生效版本 + 在飞」两种目录上**均不判过时**，且页图物理保留例外可证
  （根视图 `pages/` 与生效版本页图在清理判据下恒不被列入待删，R1）。
- 终验（本壳自身判据，不依赖真机窗口）：以上四项 + 全量门禁 + 治理契约。
  ARCH-011 窗口的批量重跑读数属 S2 的下游使用，不在本壳关闭条件内。

## 回滚

- 加列迁移带 downgrade（新列可空或带默认，不删数据文件）；新端点默认不挂载/向前兼容字段可
  单独停用；确认门与签名门各有一个开关位（代码级常量或配置），回退＝恢复现状行为。
- **不自动删除任何历史产物**：W7 只出清单；已执行的清理（另批）不可回滚部分以备份先行判据约束
  （operations.md runbook）。

## 关闭与归档

- 关闭条件：S1~S6 逐条判定达成（真机面按前置 6 移交窗口，窗口读数在关闭时以「移交成文」计达标）；
  ARCH-022 todo 行同批关闭。
- 按 ADR 0006 六步收口：结论入本壳 → todo 压缩关闭 → completed.md → `plans/index.md` 登记 →
  project-status 刷新 → 门禁全绿。归档判定 Retain。

## 评审裁决（2026-09-28 第二轮，用户已定，口径上界）

| # | 原悬置项 | 裁决 |
| --- | --- | --- |
| R1（原 Q1） | 正本作用域 | **只有生效版本目录 + 在飞/可续跑任务目录留存**，其余判过时并清理。用户补充：DB 一份即可、物理上可不留，但**截图（页图/图像产物）留在物理链路更合适** ⇒ 图像类产物列为清理例外（判据入 S6/W7，硬链接语义须自证不得毁掉根视图页图） |
| R2（原 Q5） | 确认门强度 | **三形态都必须可用**：逐页确认（整页 PASS）、按页区间/页列表批量确认、按页重新解析；并要求**确认相关 API 接口齐备**。禁止项收窄为「系统自动整本背书」，人工显式发起的批量确认合法（C4 已改写） |
| R3（原 Q6） | ledger 落点 | **确认落 `af_pages.quality` JSON 追加键**（零迁移、作用域随页行）。兜底类型子项 Q6-a **未裁**，仍待定 |
| R4（原 Q3） | 签名含模型版本 | **可以记录，要求先记录好文本**——W1 把 `/health` 实际回文取证成文后，**确认没有继续讨论的需要才动工**（W1 因此带一个显式停顿点，见工作项） |

原 Q8 由裁决 ② 自动了结（S3 改由 DB 提交序保证，PLAN-009 壳已注记「口径以 PLAN-019 为准」）。

## 仍待定口径（不阻塞 W1，开工前需逐条裁定）

| # | 悬置问题 | 建议与理由 |
| --- | --- | --- |
| Q2 | 过时版本的删除时机 | 沿用 REQ-097 判例：新产物激活/确认**之后**才删旧，严格在指针提交后；删除失败只告警保留、终态不翻转 |
| Q4 | `AXIOM_PARSE_WINDOW_SIZE` 入不签名 | 建议入（PDF 提交跨度影响跨页推断与产物）；代价＝调窗口参数会触发全量重跑判定，需在清单 reason 里可读 |
| Q6-a | 未识别引擎字面值的兜底类型 | 建议收为 `paragraph` 并计 ledger；备选＝新增 `unknown` 语义，但要动 `BlockType` 十种契约（PLAN-018 K4 判例示警：改枚举会使 `index` 语义漂移、既有编辑失效） |
| Q7 | W7 清理的执行面 | 建议扩 `POST /admin/purge` 的 dry-run 输出（复用既有守卫与 token，不新开端点），真删走既有 `confirm:"PURGE"` 流程＝天然 D 类留痕；独立端点为备选 |
| Q9 | 与 PLAN-018 的衔接面 | 页级区域输入（`af_page_regions`，跨版本）≠ 页级确认（版本内质量判定）；区域驱动重解析产出的页确认态回到 `unconfirmed`（与 R2 第三形态同一条规则）——待确认 |
| Q10 | `force` 的粒度 | 建议全本级布尔；页区间不受门已覆盖「局部强制重做」诉求，不引入 `pages`+`force` 组合复杂度 |
| Q11 | 同 `version` 下换模型后端对签名不可见（W1 取证新发现：`/health` 无 `backend`，结果载荷有） | 建议**不改签名形状**：pipeline 把首个结果的 `backend` 记入 `af_parse_jobs.progress`（零迁移），重跑清单遇「生效版本 backend ≠ 最近任务 backend」以 `model_drift_suspect` **报警不自动判过时**（模型部署归 8900，本仓不自动翻转人的判定） |
