# 解析产物可信与增量重跑实施（parse-artifact-trust）

状态：In Progress
任务类型：B
最后更新：2026-10-01
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0007](../adr/0007-parse-status-semantics.md)（`completed` 与激活同一判据——P2 的 DB 单口径必须与之对齐）、[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（**就绪与激活判据的 DB 单源**，取代 ADR 0007 决定 1 的「产物在位」子句；S3/W5 的立法依据）、[ADR 0009](../adr/0009-data-root-must-be-explicit.md)（数据根 fail-closed，W6 残骸清点只读不自动创建）、[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型版本探针归 8900 侧能力，P1 只消费 `/health` 读数）
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
| ③ | 分块（RAG 消费的前置）必须在**每页由管理员用户确认之后**才做 | 原 P3 只管归一分诊。现新增「人工确认门」：chunking 只聚合已确认页，未确认页逐页可见排除。P3 扩为「语义分诊 ledger + 确认门」两件（**2026-09-29 起「确认门」半侧的实施承载移入 [PLAN-018](2026-09-26-parsing-region-edit-typography-request.md) W8/S7**，单侧登记；本壳只留 ledger 半侧与衔接，逐处注记见 S5/W6/C4/C5） |
| ④ | P4 多引擎 bridge 暂缓、P5 双轨 IR 无动作（维持 PLAN-009 原判） | 不入本壳 |

> 第二轮（R1~R4，2026-09-28）与第三轮（R5~R8，2026-10-01）评审见文末「评审裁决」两表；**R 表是本壳的最上界，同一条目以最新一轮为准**，与之冲突的本文其他措辞以 R 表为准。

## 目标与成功标准

**目标**：解析层从「能解析出来」升级为「**增量可判、就绪单一口径、取舍有账、消费过人工门、
旧数据可清**」。**消费现场（2026-10-01 改判）**：原写法「服务的现场＝ARCH-011 批量重建窗口
（重跑清单作为其 W0 输入）」随该行 2026-09-30 关闭而失效（终态 Superseded/Partial，逐本重解析
改由用户按需承担），现挂三处——① 人工/运维会话：要不要重跑一本书以重跑清单读数为据，不再靠
整本试错；② [PLAN-018](2026-09-26-parsing-region-edit-typography-request.md) 区域驱动重解析的
常态化重跑前置；③ 存量 17 本的代际甄别（签名哨兵 `''`，判据见 C1）。

| ID | 成功标准（可检验） |
| --- | --- |
| S1 | `af_parse_jobs` 有 `parser_signature` 列（迁移落地），签名输入集冻结成文且**只含影响块产物的参数**；`(source_sha256, parser_signature)` 双全等 ⇒ 默认不新建重跑；哨兵 `''`（未知代际）只判需重跑、不判过时（C1 边界三条）；重跑判定单测 + 契约测试常驻 |
| S2 | 只读重跑清单端点交付：逐本给出 `need_reparse`/`resume`/`up_to_date` 判定与 `reason`（含 `signature_unknown` 代际与 `model_drift_suspect` 分列），人打开即能决定「这本书要不要重跑」 |
| S3 | 就绪、**激活与续跑**判据只取 DB（`af_pages` 页行 + `af_books.active_job_id` + `af_parse_jobs` 终态，[ADR 0015](../adr/0015-db-single-source-for-readiness.md)）：`_resumed_pages`/`_version_is_complete`/`_inherit_page_rows` 不再 stat 产物文件，`GET /chunks` 不以 `chunks.json` 在否决定 404；守护证明删除或伪造产物文件（manifest **与** `pages/p<NNNN>.blocks.json`）不改变任何就绪、终态与清单读数，而 `has_artifact` 一类完整性披露仍如实反映磁盘缺失 |
| S4 | 块策略表版本化（`_TYPE_MAP` + 分块过滤规则），未知字面值**宁收不丢**：收为兜底类型 `PARAGRAPH` 并落逐页 ledger（明细可定位 字面值/计数/原文摘要/策略版本/处置）；ledger **只落 `af_pages.quality`、不进 `QualityMetrics`、不进 `blocks.json`、不进 API 回包**（R8），且与既有 `dropped_types`/`dropped_block_count` 计数对账闭合＝同一事实单源，不出现两套丢弃口径 |
| S5 | **（承载已移出，2026-09-29 → [PLAN-018](2026-09-26-parsing-region-edit-typography-request.md) W8/S7，判据以该壳为准）** 页级确认门：确认态落 `af_pages`、chunk 重建只聚合已确认页且**可见排除**、确认接口三形态齐备、写入走管理面 token 口径、**系统不得自动整本背书**；本壳关闭时以 PLAN-018 S7 达成证据引用计达标 |
| S6 | 过时清点交付：作用域已裁＝**只有生效版本目录 + 在飞/可续跑任务目录留存**，其余为过时。只读清单给出 `(正本配对唯一性 / 残骸 / 待清理)`：每个 `active_job_id` 恰对应一个版本目录、每个非 `legacy` 任务行恰对应一个版本目录或为已清理态；`.tmp-`/`.old-` 三式残骸出现在清单中。**页图例外**：图像类产物保留物理形态（用户理由＝截图存物理链路更合适），清理不得毁掉根视图 `pages/` 与生效版本页图（硬链接语义须自证）。自动删除的执行判据成文（执行本身属 D 类，另批批准）；**签名哨兵 `''` 的存量书目不得进待清理判据**（未知代际只判需重跑，C1 边界 ②） |

## 范围与非目标

- **范围**：`af_parse_jobs`/`af_pages` 扩列 + 迁移；`orchestrator/` 受理前重跑判定与落库顺序；
  `normalize/` 策略表版本化 + ledger；`orchestrator/` 激活与续跑判据去 fs（ADR 0015）；
  管理面/读取面最小端点增量；
  `api.md`/`database-design.md`/`design/` 同步；过时与残骸只读清点。
  （chunking 确认门 2026-09-29 移出至 PLAN-018 W8；本壳只交付 `af_pages.quality` ledger 键形状供其消费）
- **非目标**：知识库面 R1~R4（ARCH-023，另壳）；P4 多引擎、P5 双轨 IR；区域驱动重解析与块结构
  编辑本体（PLAN-018，本壳只处理与其的迁移链和语义衔接）；8903 前端界面；**任何业务库真实删除
  执行**（W7 只出清单与判据，批量/全项目清理属 D 类，逐步批准）；引擎侧参数与模型部署（ADR 0004）。

## 前置条件

1. **迁移链串行（2026-10-01 收口：本条是唯一正文，「W1 勘察登记」结论 4 只指向此处）**：规则＝
   **先合入者占 head，后合入者将 `down_revision` 改指前者**，绝不并写同版本号。开工实读
   （本壳 W2 起做时 `alembic heads`）：已提交 head ＝ `20260926_0002`（PLAN-018 W2 已于 2026-10-01
   提交），本壳取 `20260928_0001`、`down_revision = 20260926_0002` 接其后。谁先谁后不改变判据，
   只改变 `down_revision` 的字面值——本条只记实读，规则句以上一行正文为准。
2. **表数措辞零增量**：本壳只加列、不新增表 ⇒ 不触发 PLAN-018「第 11 节」的 4→5 表同步面；
   加列的列清单同步由 `test_column_list_matches_orm_columns` 守护，与迁移同批晋升。
3. **模型版本探针已证（2026-09-28 W1 取证，见「W1 勘察登记」节）**：`:5002/health` 实回
   `version` 与 `protocol_version` ⇒ 签名含模型服务版本成立，原 Q3 的「探不到即退化」路径
   **不需要**。残余盲区（同版本换后端）由 Q11 裁定处置。模型部署与版本管理不归本仓库（ADR 0004）。
4. **与 PLAN-018 的语义衔接**：`af_page_regions`（页级解析输入，跨版本）与本壳的页确认态
   （页级质量判定，版本作用域）是两回事，不得共表共列；开工前对齐一次口径（Q9）。
5. **晋升纪律**：新端点与新增字段（C2 的 `force`、C3 的重跑清单）在挂载前不得进 `api.md` 端点总表
   与契约测试（`test_api_endpoint_table_matches_runtime_routes` 双向对账）；草案先留本壳，随实现同批
   晋升。确认端点已随确认门移入 PLAN-018，其晋升纪律以该壳为准（2026-09-30 同步）。
6. **真机面**：业务库 `qed` 已升 `20260926_0001`（2026-09-28 事实更正，见 W8）；本壳新迁移在
   业务库上的 apply 与真实数据根的清理执行，全部另批 D 类批准（原 ARCH-011 窗口行已于
   2026-09-30 关闭，不再存在承接窗口），不在默认门禁。

## 契约草案（评审前唯一承载处，未晋升）

### C1 · `parser_signature`（P1）

- **输入集（2026-09-28 已裁 R4；2026-09-30 Q4/H3 裁决收口后**冻结**，取证见「W1 勘察登记」节）**：
  `AXIOM_OCR_ENGINE`、MinerU `/health` 的 `version` **与 `protocol_version`**（两者均已实测可读）、
  `block_policy_version`（`_TYPE_MAP` + `_OUTPUT_FLAGS` 两处代码级常量的规范化哈希；拆域见
  「W1 勘察登记」结论 3 的 2026-09-30 补裁）。
  **排除 `AXIOM_OCR_TIMEOUT`**：只影响成败与进度、不改块内容（判据：影响产物才入签名）。
  **排除 `AXIOM_PARSE_WINDOW_SIZE`（Q4 裁定，2026-09-30）**：窗口只改提交批次与执行方式，签名不随
  窗口翻转；代价成文＝调窗口**不**触发 `need_reparse`，窗口变更后的产物复核由人工经 `force` 或
  重跑清单人工判定。
  **不入签名**：`chunk_policy_version`（分块跳过集等，只落 ledger 与 chunk 重建触发）。
  程序约束（用户裁决）：**W1 已把 `/health` 实际回文取证成文**（原文字段名与读数见「W1 勘察登记」节，
  含留档字节 sha256）；探不到版本字段时不臆造，退化为
  `engine + block_policy_version` 两段并在设计留注「模型版本变化由人工触发重跑清单复核」（实测无需退化）。
  **白名单断言**：canonical 串单测断言签名 JSON 的键恰为上述冻结集合，字段漂移即转红。
- **canonical 形式**：键按字典序的 JSON 规范化串 → SHA-256；落 `af_parse_jobs.parser_signature`
  （`String(64)`，迁移回填哨兵 `''` ＝ 未知代际，**不回填伪造值**，判例同 `ingested_at` 不回填）。

- **哨兵 `''` 的判据边界（2026-10-01 补写，评审洞 4）**：迁移回填后**全部存量生效版本**都带 `''`，
  与当前签名必不等 ⇒ 重跑清单上线首日会把现有 17 本全判 `need_reparse`。据此定三条：
  ① `''` **只**产出 `verdict=need_reparse` 与 `reason=signature_unknown`，不得产出「参数已变」类结论；
  ② `''` **不是**过时判据 ⇒ S6/W7 的待清理清单只按目录配对关系判定，绝不因签名为 `''` 把在用版本
  列入待清理（与裁决 ① 的清理链隔离）；③ 该代际**不回算补签**（历史参数不可考，回算＝伪造第二
  事实源，违裁决 ②），只能靠一次真实重跑写新行消除。清单 `reason` 文案必须让人一眼分辨「未知
  代际」与「参数确实变了」，否则首日读数会被误读成全量重跑指令。
- **判定语义**：`need_reparse` ⟺ 生效版本 `(source_sha256, parser_signature)` 与当前书快照 +
  当前签名不等；`resume` ⟺ 同 `(book_id, job_id)` 续跑（现状语义不变）；双全等 ⇒ `up_to_date`。

### C2 · `POST /api/v1/parse-jobs` 补句（重跑门）

> 受理前对 `pages:null`（全本）请求判签名命中：命中 ⇒ 默认 **409**（响应含生效 `job_id` 与
> 判定原因），携带 `"force": true` 才创建新任务；`force` 为 `ParseJobCreate` 新增可选布尔字段，
> 缺省 false ⇒ 现有契约形状向前兼容。页区间/逐页请求**不受门**（人工意图明确，判例同区域驱动）。
>
> **在飞判据（Q12，2026-09-30 已裁）**：生效版本之外，同签名 `queued`/`running` 任务存在时
> （从未解析或生效版本已过时书目的首跑场景）同样 **409** 并回该在飞 `job_id`；判定与 S2 清单
> `resume` 共用同一纯函数，堵住并发提交/双击双跑；`force: true` 同样穿透在飞判据。
>
> **原子性判据（2026-10-01 补写，评审洞 5）**：签名门与在飞判据都是「先读后判」，两个全本 POST
> 同时到达时互相看不见 ⇒ 双跑（下游 `_await_model_idle()` 只把两次跑串行，白跑一遍仍在）。
> ⇒ 判定与任务行创建必须落在**同一事务的同一把锁内**：对目标 `af_books` 行加悲观行锁（MySQL
> `SELECT … FOR UPDATE`；SQLite 单写者天然串行）后再读生效版本签名与在飞行，锁内不发起模型调用。
> 反向判据：双 Session 并发提交同一本书 ⇒ 恰一个创建任务行、另一个 409（`QED_TEST_MYSQL=1` 层
> 取证，判例同 DEFECT-001 的双 Session 提交序用例）。

### C3 · `GET /api/v1/admin/reparse-plan`（草案，管理面挂载纪律）

> 复用 PLAN-010 装配开关与 token（不挂即 404、`X-QED-Admin-Token` 逐字比对）。只读返回逐本
> `{book_id, active_job_id, source_sha256, parser_signature, verdict, reason}`；
> 无 `active_job_id` 的书 verdict=`never_parsed`。8900 **不得透传**（管理面纪律沿用）。

### C4 · 页确认态与确认接口（P3 之半，2026-09-28 已裁 R2；**2026-09-29 起实施承载移入 [PLAN-018](2026-09-26-parsing-region-edit-typography-request.md) W8/S7，口径以该壳为准，本草案留档仅为衔接参考**）

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

### C5 · ledger 与 chunk 门（P3 另半，落点 2026-09-28 已裁 R3；**chunk 门半侧 2026-09-29 移入 PLAN-018 W8，ledger 半侧留本壳**）

> **ledger 落点＝`af_pages.quality`（JSON）追加键，作用域天然随页行，DB 层零迁移**（R3）；明细形状
> `dropped_items: [{literal, count, sample}]` + `policy_version`。守护面只涉 `quality` 键形状（单测断言），
> 不触 `test_column_list_matches_orm_columns`。
>
> **「零迁移」只对 DB 列成立（2026-10-01 已裁 R8，评审洞 2）**：`af_pages.quality` 的写入与回包都走强类型
> `QualityMetrics`（`orchestrator/pipeline.py:_handle` 写页行处的 `page.quality.model_dump()`；
> `schemas.py` 的 `QualityMetrics`，且
> `BlocksPage.quality` 随 `blocks.json` 与单页端点出），而 pydantic 默认丢弃未声明键 ⇒ 追加键会被静默冲掉。
> 据此定：ledger **只落库，不进 `QualityMetrics`、不进 `blocks.json`、不进任何 API 回包**（消费路径＝S6/W7
> 只读清点与 SQL）。落地两条：① 写页行时按「模型字段 ∪ ledger 键」合并，不得让 `model_dump()` 覆盖已有
> ledger 键，守护＝先写 ledger 键再 upsert 模型读数后键仍在；② 与既有 `dropped_types` /
> `dropped_block_count` 定为**同一事实单源**——`dropped_types` 保留（模型字段、契约可见），`dropped_items`
> 是其明细扩展（多 `count`/`sample`），两者计数由单测对账。
>
> 未知引擎字面值不再丢弃：**Q6-a 已裁（2026-10-01 R6）＝收为兜底类型 `PARAGRAPH` 并计 ledger**，不新增
> `unknown` 语义（`BlockType` 十种契约不动，规避 PLAN-018 K4 判例示警的 `index` 语义漂移与既有编辑失效）。
>
> **分块跳过集的 `IMAGE` 维持排除（2026-10-01 已裁 R7）**：PLAN-009 原 P3「保留公式/表/图」的「保留图」
> 措辞**不采纳**——图块进 chunk 的取值方式（占位文本？标题？坐标？）属 ARCH-023 R4 数学感知切分的对象，
> 本轮不混装。现状 `normalize/chunking.py` 的 `_SKIP_TYPES`（`header`/`footer`/`page_number`/`image`）
> 即为 `chunk_policy_version` 的取值基线，改动须与策略版本号同步。
>
> chunk 门（**2026-09-29 承载已移 PLAN-018 W8，留档参考、本壳 W6 不实现**）：`rebuild_chunks`
> 只聚合 `confirm_state=confirmed` 的页；响应与 ledger 可见排除
> （`chunks.json` 记 `excluded_unconfirmed: [page_no,…]`，不静默）。门只在**重建时**读 DB，
> 不在读取时过滤（`GET /chunks` 契约保持产物原样）。

## W1 勘察登记（2026-09-28，`/health` 回文取证成文）

R4 的程序约束在此了结：签名是否含模型版本，**以实际回文字段名为准，不臆造**。

### 取证来源

| 项 | 读数 |
| --- | --- |
| 实时探针 | `GET http://127.0.0.1:5002/health` → **连接拒绝**（WinError 10061，MinerU 容器未启动）⇒ 本轮以真机留档取证；字段形状由本壳 W9 真机面复验（原改核对象＝已关闭的 ARCH-011 窗口，2026-09-30 起该读数归 W9） |
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
| `processing_window_size` | `64` | 否 | 服务内部分批窗口；我方提交跨度是 `AXIOM_PARSE_WINDOW_SIZE`，而它按 Q4 裁定同样**不入签名**（只改提交批次，不改块内容） |
| `task_retention_seconds` / `task_cleanup_interval_seconds` | `86400` / `300` | 否 | 服务端任务表寿命，与产物内容无关 |

### 结论与残余盲区

1. **可记**：`/health` 确实回版本 ⇒ 签名含 `service_version` + `protocol_version` 成立，**无需退化**。
2. **残余盲区（Q11，2026-09-30 已裁＝按下述建议执行）**：`/health` **不回模型/后端标识**（无 `backend`/`model` 字段）。而
   **结果载荷回**——留档 `raw/023_single-result_200.bin` 顶层为
   `{backend:"hybrid-engine", version:"3.4.4", results:{…}}`（`024` 同）。⇒ 同一 `version` 下换后端/换权重
   （8900 侧部署动作，ADR 0004 不归本仓）对签名**不可见**。建议：签名形状不变，pipeline 在首个结果
   返回时把 `backend` 写入 `af_parse_jobs.progress`（JSON 追加键，零迁移），重跑清单在
   「生效版本 backend ≠ 最近任务 backend」时以 `model_drift_suspect` **报警而不自动判过时**。
3. **策略版本承载位定判（2026-09-30 补裁：拆两个版本域）**：仍取 `normalize/` 侧**代码级派生**值、
   不加列不加配置位（理由＝代码级常量改动对开发者可见，另设配置位会造出「配置写一套、代码算一套」
   的第二判据，违裁决 ②）；但按 S1「只含影响块产物的参数」拆域——
   `block_policy_version` = `_TYPE_MAP` 全量映射 + `_OUTPUT_FLAGS`，**入签名**；
   `chunk_policy_version` = 分块跳过集等 `chunking.py` 侧常量，**不入签名**，只落 ledger 与
   chunk 重建触发。理由＝分块规则不改 blocks 内容，混装会使改一行分块规则触发全本重跑判定。
4. **迁移链实读（唯一正文＝前置 1）**：2026-09-28 取证时 head ＝ `20260926_0001`；2026-10-01 复核时
   PLAN-018 的 `20260926_0002_page_regions` 已在工作区落地（未提交）。⇒ 取号与 `down_revision` 的
   规则只写一处，见「前置条件 1」；此处不重复其措辞。

## 工作项

| # | 工作项 | 依赖 | 状态 |
| --- | --- | --- | --- |
| W0 | 立项收编：本壳入册 + todo 镜像（ARCH-022 改指本壳、PLAN-019 新行）+ PLAN-009 P 系列表注记改挂；治理契约绿 | — | 完成 2026-09-28 |
| W1 | 勘察冻结：`/health` 版本字段**取证成文**（回文原文入本壳登记，R4 程序约束）、签名输入集定判、策略版本承载位定判（C1 落档）；产出后**停下等用户确认无争议**再进 W2 | — | 完成 2026-09-28（取证与定判见「W1 勘察登记」节）；停顿点 2026-09-30 用户确认无争议，输入集随 Q4/H3 裁决最终冻结 |
| W2 | P1 落库：`parser_signature` 加列迁移 + 建/读/判仓储 + canonical 串单测（先红后绿，含**输入集白名单断言**与判定纯函数） | W1 | 完成 2026-10-01（结论与偏差见「W2 落地结论与偏差登记」节；受理门与清单端点未挂载） |
| W3 | P1 受理门：`force` 字段、全本命中 409、在飞同签名 409（Q12）、页区间不受门；契约测试（含 force 缺省形状不变反向判据） | W2 | 待开始 |
| W4 | S2 只读重跑清单端点（消费方＝「目标」段 2026-10-01 改判的三处现场）；`reason` 分列出 `signature_unknown` 代际（C1 边界 ①）；原「ARCH-011 W0 供数口径」随该行关闭撤除 | W2 | 待开始 |
| W5 | P2 DB 单源（[ADR 0015](../adr/0015-db-single-source-for-readiness.md)）：`_resumed_pages`/`_version_is_complete`/`_inherit_page_rows` 的「产物在位」判据去 fs、改读 `af_pages` 页行，`GET /chunks` 不以文件存在定 404；提交序现状保持（页产物写先于页行提交）；`has_artifact` 等完整性披露原样保留；守护＝删/造产物文件不改就绪与终态 ＋ 设计措辞同步（`orchestrator/pipeline.py` 文件头与 `parsing-pipeline.md`/`parsing-management.md`/`api.md` 三篇的「产物在位」句；三篇「关联 ADR」已于 2026-10-01 先行接入 ADR 0015，正文措辞与实现状态按前置 5 晋升纪律留在本项） | ADR 0015 | 待开始 |
| W6 | P3 分诊：策略版本表（拆 `block_policy_version`/`chunk_policy_version`）+ 未知宁收（**Q6-a 已裁 R6＝兜底 `PARAGRAPH`**）+ ledger 明细（**只落 `af_pages.quality`、不回包不进 blocks.json**，R8）+ 分块跳过集口径显式记入（**IMAGE 维持跳过**，R7）；chunk 确认门与确认端点三形态**已移 PLAN-018 W8**，本壳只交付 ledger 键形状供其消费 | W5 | 待开始 |
| W7 | 过时清点（裁决 ①，作用域已裁 R1；删除时机 Q2＝沿用 REQ-097 判例、执行面 Q7＝扩 `admin/purge` dry-run）：只读清点——正本配对唯一性、孤儿任务行、孤儿版本目录、`.tmp-`/`.old-` 三式残骸、**页图物理保留例外与硬链接自证**；清理执行顺序与安全边界成文（先磁盘后库、重跑幂等判例沿用 PLAN-010）；**执行另批 D 类批准** | W2/W6 | 待开始 |
| W8 | 文档晋升：`api.md`（C2/C3 小节与总表，随挂载同批；C4 随 PLAN-018）、`database-design.md` 列清单、`parsing-pipeline/management` 口径、`project-status` 刷新；**同批核对**（2026-10-01 复核：原更正对象「业务库升迁移属窗口前置」在活文档已无载体——ARCH-011 承载节 2026-09-30 关闭时已记项①执行、`history/` 冻结面按规则不追改 ⇒ 本项从「更正」降为「核对」：W8 只确认活文档不再把业务库迁移写成窗口前置） | W3~W7 | 待开始 |
| W9 | 门禁与真机：默认门禁全绿 + `QED_TEST_MYSQL=1` 迁移往返 + 变异复验（撤签名门 ⇒ 对应用例转红；撤确认门用例归 PLAN-018）；真机面按前置 6 另批 D 类窗口（原 ARCH-011 窗口 2026-09-30 已关闭，读数改挂本壳 W9） | W8 | 待开始 |

## W2 落地结论与偏差登记（2026-10-01）

结论：**W2 已实施并通过本地门禁**——`af_parse_jobs.parser_signature` 加列（迁移 `20260928_0001`，
`down_revision = 20260926_0002`）＋ `orchestrator/signature.py`（canonical 签名与重跑判定纯函数）＋
`ParseJobRepository.find_in_flight`。W2 只交付**写入面与判据**：受理门（W3）与只读重跑清单（W4）未挂载
⇒ `api.md`、端点总表与任何 API 回包**零改动**（前置 5 晋升纪律）。业务库 `qed` 的 `20260926_0002` 与
`20260928_0001` 属 D 类窗口，本批**未执行**。

### 交付面与守护

| 交付 | 位置 | 守护（会红的判据） |
| --- | --- | --- |
| 签名列 ORM | `src/axiom_flow/db/models.py`（`AfParseJob.parser_signature`） | `test_fixed_document_form.py::test_column_list_matches_orm_columns[af_parse_jobs]`（列清单↔ORM 双向对账，含 VARCHAR 长度与可空性） |
| 迁移 | `src/axiom_flow/migrations/versions/20260928_0001_parser_signature.py` | `tests/unit/test_db_migrations.py`（存量回填恰为哨兵 `''`、列 NOT NULL、加列不带走任务行与索引、降级干净可重复）＋ `QED_TEST_MYSQL=1` 集成层在真 MySQL 从 base 重建到 head |
| canonical 与输入集 | `src/axiom_flow/orchestrator/signature.py`（`SignatureInputs`、`compute_signature`、`block_policy_payload`） | `tests/unit/test_parser_signature.py`（键集合恰等冻结四元组、非内容参数不在串内、字典序 JSON→SHA-256、四个输入项任一变化必翻转、缺读数 fail-closed、改 `_TYPE_MAP`/`_OUTPUT_FLAGS` 即翻转、分块侧常量不影响） |
| 判定纯函数 | 同上（`decide_rerun`、`VersionIdentity`、`RerunDecision`） | 同上 `TestRerunDecision`（`up_to_date`/`resume`/`need_reparse`＋`signature_unknown`/`never_parsed` 逐条，含当前侧空值报错） |
| 仓储写入与在飞读数 | `src/axiom_flow/db/repositories.py`（`ParseJobRow.parser_signature`、`find_in_flight`） | `tests/unit/test_db.py::TestParseJobRepository`（签名回读、缺省即 `''`、在飞按「书＋签名＋活动态」过滤取最早） |
| 文档同步 | `database-design.md`（列清单行＋任务表语义签名段＋迁移序列行＋业务库 head 现状）、`code-map.md`（`orchestrator/signature.py` 行＋迁移行措辞） | `test_fixed_document_form.py`、`test_architecture_documents.py`、`test_code_document_mapping.py` |

门禁读数（本批收口时复跑）：`pytest tests -q` **624 passed / 10 skipped**；`ruff check src tests scripts`
全过；`QED_TEST_MYSQL=1 pytest tests/integration -q` **8 passed**；`alembic heads` ＝ `20260928_0001`。
守护变异复验（各撤一处、对应用例转红后从 `tmp/` 备份还原）：`canonical_json` 去字典序 ⇒ 字典序用例红；
`signature_unknown` 写成 `signature_changed` ⇒ 哨兵用例红；`find_in_flight` 去掉签名过滤 ⇒ 在飞用例红。

### 偏差登记（与草案 C1 / 工作项表的差异）

| # | 差异 | 定判理由与落点 |
| --- | --- | --- |
| D1 | canonical 计算与判定纯函数落 `orchestrator/signature.py`，不塞进 `db/repositories.py` | 签名是「一版产物用哪一代参数跑的」编排判据，不是数据访问；且它要引用 `normalize/`＋`engines/` 的代码级常量，放进持久层会把数据访问层绑到引擎形状上。`code-map` 同批加行 |
| D2 | `ParseJobRow.parser_signature` 带默认 `''`（草案未写写入面形状） | 受理面在 W3 才接线；缺省 `''` 与迁移回填同一语义＝「本行未记录签名」。漏传的后果是**保守方向**（该书落进 `signature_unknown` 判需重跑），不会被误判 `up_to_date` |
| D3 | 当前侧 `''` 直接 `ValueError`（C1 只写了生效侧 `''` 的三条边界） | 「没算签名」不是一个代际，把它读成哨兵会静默吞掉真正的代际变化；两条 raise 分别锁 `current_signature` 与 `current_source_sha256` |
| D4 | 白名单断言写成字面四元组，不从 dataclass 字段反射 | 反射等于「加个字段就自动合法」，冻结集合的意义就在于加字段必须显式改判据并留下红转记录 |
| D5 | `up_to_date` 优先于 `resume`（生效版本已双全等时不再指向在飞任务） | 清单口径回答「这本书需不需要重跑」；已有可用产物时把在飞任务算作结论会让 8900 侧读数随并发时序抖动 |
| D6 | 前置 1 的实读改写（head 已推进），规则句不动 | PLAN-018 W2 于 `e4dd55d` 提交；易漂移的版本号只记一次实读，判据仍以规则句为准 |
| D7 | 业务库待应用迁移由一支变两支（`20260926_0002` ＋ `20260928_0001`） | D 类窗口按用户排期执行；`database-design.md` 迁移现状段已并列标注，测试库 `qed_test` 由夹具每次重建到 head 故 DDL 已在门禁真实执行 |

### 遗留（不在 W2 面）

- W3：受理门（`force`、全本命中 409、在飞同签名 409、`af_books` 行锁内的原子判定）＋ 签名注入。
  `/health` 的 `version`/`protocol_version` 由受理面读入后构造 `SignatureInputs`，`signature.py` 不发探活
  ——默认门禁不许打外部模型服务（测试隔离铁律）。
- W4：只读重跑清单端点与 `reason` 文案（`signature_unknown` 代际分列、Q11 的 `model_drift_suspect` 报警）。
- W6：策略拆域若改动 `_TYPE_MAP`/`_OUTPUT_FLAGS` 的位置或形状，签名随之翻转属**预期代际变化**；但改
  `block_policy_payload` 的键集合时，白名单断言与 `database-design.md` 任务表语义段须同批跟进。

## 验证与验收

- TDD 先红后绿；单测只用临时目录与 `tmp_path`，禁读写真实数据根（ADR 0009）；
  真 MySQL 面只在 `qed_test`（ADR 0005 铁律：只 DROP 自有 `af_*` + `alembic_version`）。
- 关键反向判据（逐条独立可证伪）：
  ① 伪造/删除产物文件（manifest **与** `pages/p<NNNN>.blocks.json`）⇒ 各 API 就绪读数、任务终态
  与激活结果全不变（S3／ADR 0015），同时 `has_artifact` 仍如实反映磁盘缺失；
  ② 未确认页不出现在 `chunks.json` 且排除可见（S5 承载＝PLAN-018 W8/S7，本壳只交付 ledger 键形状）；
  ③ 签名相同且不带 `force` ⇒ 409、零任务行、零模型调用（S1）；
  ④ 未知字面值不再使块消失、ledger 计数与原始项对账闭合（S4）；
  ⑤ 人工显式批量确认 ⇒ 生效，而**无任何请求可让系统把整本自动置 `confirmed`**（R2）；
  ⑥ 清理作用域判据在「生效版本 + 在飞」两种目录上**均不判过时**，且页图物理保留例外可证
  （根视图 `pages/` 与生效版本页图恒不被列入待删，R1）；
  ⑦ 签名哨兵 `''` 的书目 ⇒ 清单给 `need_reparse`/`signature_unknown`，且**不出现在待清理清单**
  （C1 边界 ②）；
  ⑧ 双 Session 并发提交同一本书的全本请求 ⇒ 恰一任务行、另一 409（C2 原子性，`QED_TEST_MYSQL=1` 层）；
  ⑨ 已写 ledger 键的页行再经模型 `upsert` ⇒ 键仍在且 `dropped_types` 与 `dropped_items` 计数相等（R8）。
- 终验（本壳自身判据，不依赖真机窗口）：上述判据 ①~⑨ 全条 + 全量门禁 + 治理契约。
  业务库上新迁移的 apply 与真实数据根的清理执行另批 D 类批准（前置 6），其读数在关闭时
  以「移交成文」计达标。

## 回滚

- 加列迁移带 downgrade（新列可空或带默认，不删数据文件）；新端点默认不挂载/向前兼容字段可
  单独停用；确认门与签名门各有一个开关位（代码级常量或配置），回退＝恢复现状行为。
- **不自动删除任何历史产物**：W7 只出清单；已执行的清理（另批）不可回滚部分以备份先行判据约束
  （operations.md runbook）。

## 关闭与归档

- 关闭条件：S1~S6 逐条判定达成（真机面按前置 6 另批 D 类窗口，窗口读数在关闭时以「移交成文」计达标）；
  ARCH-022 todo 行同批关闭。
- 按 ADR 0006 六步收口：结论入本壳 → todo 压缩关闭 → completed.md → `plans/index.md` 登记 →
  project-status 刷新 → 门禁全绿。归档判定 Retain。

## 评审裁决（2026-09-28 第二轮，用户已定，口径上界）

| # | 原悬置项 | 裁决 |
| --- | --- | --- |
| R1（原 Q1） | 正本作用域 | **只有生效版本目录 + 在飞/可续跑任务目录留存**，其余判过时并清理。用户补充：DB 一份即可、物理上可不留，但**截图（页图/图像产物）留在物理链路更合适** ⇒ 图像类产物列为清理例外（判据入 S6/W7，硬链接语义须自证不得毁掉根视图页图） |
| R2（原 Q5） | 确认门强度 | **三形态都必须可用**：逐页确认（整页 PASS）、按页区间/页列表批量确认、按页重新解析；并要求**确认相关 API 接口齐备**。禁止项收窄为「系统自动整本背书」，人工显式发起的批量确认合法（C4 已改写） |
| R3（原 Q6） | ledger 落点 | **确认落 `af_pages.quality` JSON 追加键**（作用域随页行）。兜底类型子项 Q6-a 当时未裁 ⇒ 2026-10-01 R6 已裁；「零迁移」的适用面由 R8 更正（只对 DB 列成立） |
| R4（原 Q3） | 签名含模型版本 | **可以记录，要求先记录好文本**——W1 把 `/health` 实际回文取证成文后，**确认没有继续讨论的需要才动工**（W1 因此带一个显式停顿点，见工作项） |

原 Q8 由裁决 ② 自动了结（S3 改由 DB 提交序保证，PLAN-009 壳已注记「口径以 PLAN-019 为准」）。

## 评审裁决（2026-10-01 第三轮，R5~R8；用户已定，同一条目以本轮为准）

本轮由 ARCH-022 行的精炼性与完备性评审引出，四条全部落正文：

| # | 悬置项 | 裁决 | 正文落点 |
| --- | --- | --- | --- |
| R5（评审洞 1） | S3 的真实对手不是 manifest，而是产物文件落盘判据 | **升级为 DB 单源并改激活门**：`_version_is_complete`/`_resumed_pages`/`_inherit_page_rows` 去掉「产物在位」的 fs 判据、只读 `af_pages` 页行；fs 只保留完整性披露与清点两类用途 | S3／W5／判据 ①；立法＝[ADR 0015](../adr/0015-db-single-source-for-readiness.md) |
| R6（原 Q6-a） | 未识别引擎字面值的兜底类型 | **收为 `PARAGRAPH` 并计 ledger**；不新增 `unknown` 语义 | S4／C5／W6 |
| R7（评审洞 3） | 原 P3「保留公式/表/图」与现状 `_SKIP_TYPES` 含 `IMAGE` 冲突 | **图块维持跳过**：PLAN-009 的「保留图」措辞不采纳，图块入 chunk 的取值方式属 ARCH-023 R4 的对象，本轮不混装 | C5／W6 |
| R8（评审洞 2） | ledger 落 `af_pages.quality` 的「零迁移」只对 DB 成立 | **ledger 只落库**：不进 `QualityMetrics`、不进 `blocks.json`、不进 API 回包；与 `dropped_types` 定为同一事实单源并对账 | S4／C5／W6／判据 ⑨ |

### 本轮补写判据（评审发现的洞，按既有裁决延伸，非新裁决；如需改判请指明）

| 洞 | 补写内容 | 落点 |
| --- | --- | --- |
| 4 | 签名哨兵 `''` 的三条边界（只判需重跑、不判过时、不回算补签） | C1／S1／S6／判据 ⑦ |
| 5 | 受理门与在飞判据的原子性（`af_books` 行锁内判定并建行） | C2／判据 ⑧ |
| 6 | S2 消费方改判（ARCH-011 行已关闭 ⇒ 人工会话 + PLAN-018 重跑前置 + 存量代际甄别） | 目标段／W4 |
| 7 | 迁移链口径自相矛盾收口（前置 1 原写「接在 PLAN-018 之后」，与 W1 结论 4 互斥） | 前置 1 |

## 口径台账（悬置项现状；只列结论与去处，细节在上文各节）

| # | 事项 | 现状 |
| --- | --- | --- |
| Q2 | 过时版本的删除时机 | 已采纳：沿用 REQ-097 判例（新产物激活之后才删、删除严格在指针提交后、失败只告警保留不翻转终态），W7 正文即此口径 |
| Q4 | `AXIOM_PARSE_WINDOW_SIZE` 入不签名 | 已裁（2026-09-30）：**排除**，窗口只改提交批次；代价与复核路径见 C1 |
| Q6-a | 未识别字面值兜底类型 | 已裁（2026-10-01 R6）：`PARAGRAPH` |
| Q7 | W7 清理的执行面 | 已采纳：扩 `POST /admin/purge` 的 dry-run 输出（复用守卫与 token，真删走 `confirm:"PURGE"`），W7 正文即此口径 |
| Q10 | `force` 的粒度 | 已随 C2 定形：全本级可选布尔，页区间不受门 ⇒ 不引入 `pages`+`force` 组合 |
| Q11 | 同 `version` 换后端对签名不可见 | 已裁（2026-09-30）：签名形状不变，`backend` 记入 `progress`，清单以 `model_drift_suspect` 报警不自动判过时（见 W1 结论 2） |
| Q9 | 与 PLAN-018 的衔接面 | **仍待定**：页级区域输入（`af_page_regions`，跨版本）≠ 页级确认（版本内质量判定）；区域驱动重解析产出的页确认态是否回到 `unconfirmed`（与 R2 第三形态同规则）需 PLAN-018 侧确认，责任位置＝PLAN-018（本壳不代写其裁决），复核触发点＝本壳 W6 开工前 |
| Q12 | 在飞任务是否挡重跑 | 已裁（2026-09-30）：生效版本之外同签名 `queued`/`running` 存在时同样 409 并回该在飞 `job_id`，`force: true` 穿透；判定与 S2 清单 `resume` 共用同一纯函数（正文见 C2） |

## 本轮整理批落点（2026-10-01，文档接线与残留清扫；不改判据）

- **ADR 0015 接线**：`design/parsing-pipeline.md`、`design/parsing-management.md`、`architecture/api.md`
  三篇的「关联 ADR」元数据已加入 ADR 0015；三篇正文的「产物在位」措辞与 `实现状态` **未动**——按前置 5
  晋升纪律随 W5 的代码同批改（`orchestrator/pipeline.py` 文件头也在 W5 同步面内）。
- **指向已关闭 ARCH-011 窗口的活文档措辞改判**：本壳「目标」消费现场、W1 探针改核对象、W8 由「更正」
  降为「核对」（活文档已无该过时记载的载体）、W9 读数归属；PLAN-018 前置 6 的「与批量窗口同批出效」
  标注失效并指向 PLAN-009 承载节的改挂表；`project-status.md`「下一轮候选 ⑥」去掉「产物重建」前置。
  `docs/history/**` 与已关闭台账里的 ARCH-011 字样按规则**不回改**。
- **状态同步**：本壳 `状态` 与 todo 的 PLAN-019 镜像行同批 `Accepted → In Progress`（与 ARCH-022 行一致）。
- **引用去漂移**：易漂移的 `文件:行号`（`pipeline.py:489`、`pipeline.py:187/288`）改符号引用；本壳长段落
  按仓库折行风格重排，「验证与验收」反向判据改为 ①~⑨ 逐条独立行。
- **守护**：文档治理契约子集（`test_adr_governance`/`test_plan_governance`/`test_tracker_governance`/
  `test_architecture_documents`/`test_design_documents`/`test_code_document_mapping`/
  `test_fixed_document_form`/`test_standard_governance`）**74 passed**；整理批完成时全量门禁
  **591 passed / 10 skipped** + `ruff check src tests scripts` clean（该读数含并行会话 PLAN-018 W2 的
  未提交面，提交前须复跑）。
- **W2 开工闸（用户 2026-10-01 指令）**：等 PLAN-018 W2 那批**提交**后起本壳 W2；开工第一步仍是前置 1 的
  `alembic heads` 实读。**该闸已于同日达成**（PLAN-018 W2 提交为 `e4dd55d`，本壳 W2 结论见
  「W2 落地结论与偏差登记」节）。悬置项只剩 Q9（责任在 PLAN-018 侧，复核触发点＝本壳 W6 开工前）。
