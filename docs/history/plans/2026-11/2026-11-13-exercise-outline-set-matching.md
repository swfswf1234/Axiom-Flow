# 习题集目录套级匹配与一层章（exercise-outline-set-matching）

状态：Completed
任务类型：B
最后更新：2026-11-25
关联 ADR：[ADR 0004](](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、管线归本仓）、
[ADR 0015](](../../../adr/0015-db-single-source-for-readiness.md)（就绪单源）、
[ADR 0018](](../../../adr/0018-task-column-bound-and-progress-ban.md)（列宽与进展禁写）
关联设计：[knowledge-analysis.md](](../../../design/knowledge-analysis.md)（`KA-6` 两层封顶、`KA-44` 四值类型、
`KA-51` exercise 必须带目标教材、`KA-53` 来源书清单、`KA-68` 无内容子行降 other）、
[api.md](](../../../architecture/api.md)（`kp-jobs` 受理与 `books/{id}/kp` 响应形状）
关联 Tracker：docs/trackers/completed.md（PLAN-036 关闭行；请求来源＝QED-Engine 根仓库 REQ-170，
根侧实施行＝REQ-169）
归档判定：Retain（套级匹配改判推导、b07／b17 真机链读数与未执行样本的范围处置属不可替代证据；本壳未提交，不删除）

**执行分工**：需求方＝QED-Engine 根仓库；目标项目＝Axiom-Flow；接口面＝`POST /api/v1/kp-jobs`
受理与习题集目录匹配腿、`GET /api/v1/books/{id}/kp` 的 `exercise` 读面、数据根来源书清单
（`parsed/<course>/<book>/kp/` 的 meta JSON）；评审方＝用户；执行方＝Axiom-Flow；验收标准＝本文件
「验证与验收」节与台账行 PLAN-036 的 S 条目。

## 目标与成功标准

**目标**：习题集的目录部分与教材明确分形——匹配候选落在「套」内（一套可含多本教材、多本习题集），
AI 按章分派到套内不同教材，目录只留一层章不含知识点；同时解开一层章带来的 `KA-68` 死锁，
并给「整本替换目录」一条不连带题行的作废面。

1. **S1 套级候选**：`_exercise_match_candidates` 由 `course_id` 粗筛收窄到同 `knowledge_id`
   （`af_books.knowledge_id` 已在库、现网 17 本全填，零 ALTER）；仍保留 `doc_kind=textbook` ∧
   整本绿两道闸；`knowledge_id` 为空时退回现行 `course_id` 口径并在回执写明。b07 候选只含 b05，
   b15/b16 候选只含 b12/b13/b14。
2. **S2 章级分派**：匹配 prompt 与输出由「选一本」改为「逐章选一本」，章对应带自己的目标
   `book_id`；请求 schema、`exercise` 读面、数据根清单三处同步由单值改章级映射且**一次写全**
   （清单是整文件覆写，分批写会抹前批）。b15 不同章可分别落 b12/b13/b14。
3. **S3 一层章**：`doc_kind=exercise` 的书目录只成 depth=1 章行，零 `§N` 节行、零知识点行；
   教材侧 `KA-6`／`KA-62` 与 PLAN-035 R1 的 `§N` 归节逐字不动，分流判据是 `doc_kind`。
   b07 重跑后目录行全 `depth=1`／`node_type=chapter`；b05/b12/b13/b18 目录行集合与身份位逐行不变。
4. **S4 豁免 KA-68**：`node_types_of` 第二趟降级对 `doc_kind=exercise` 跳过，一层章直接认
   `chapter`。b07 一层章且题行 0 时 `stage.rows>0`、exercise 受理门放行；教材侧 KA-68 用例不改即绿。
5. **S5 作废面收窄**：outline 重跑批的 `KA-36` 作废面按 `node_kind` 收窄到目录行，已梳理题行
   （`node_kind='point'`）的 `gen_status` 与 `rev` 零变化。

> 2026-11-17 追加范围（根仓库 REQ-178 请求）：习题书三阶段类型判定（`KA-71`）与审核三态
> （`KA-70` 升级），由 W18~W21 承接；S1~S5 原文不改。

## 范围与非目标

**范围内**：

- `orchestrator/knowledge.py`：`_exercise_match_candidates`（:3581-3618）候选判据、
  `match_exercise_outline`（:3641-3701）逐章分派、`write_exercise_meta`（:3514-3551）清单形状、
  `build_outline` 成行处（:881-888）按 `doc_kind` 分流、`finalize_batch`（:5724-5823）作废面参数；
- `engines/kp_gateway.py`：`_EXERCISE_MATCH_PROMPT`（:585-598）与组装（:601-617）、
  调用解析（:1062-1098）；
- `normalize/toc.py`：`node_types_of`（:1025-1076）第二趟按书种类豁免；
- `schemas.py`：请求 target（:684-689）与 `KpExerciseOut`（:891-904）；
- `db/repositories.py`：`reject_superseded_candidates`（:1869-1937）调用侧收窄（函数签名已支持
  `node_kind`，无需改签名）；
- 条文随步：`KA-6`（习题集一层章边界）、`KA-51`（章级目标）、`KA-68`（习题书豁免）、`api.md`。

**非目标**：

- **不做题行与练习面**：题包三段、根表同步、练习面消费归根仓库 REQ-168；
- **不做点级映射**：归属粒度仍章节级；
- **不改教材两层口径**：S3／S4 只按 `doc_kind` 分流；
- **不加列、不改列形**：复用 `af_books.knowledge_id`，零 ALTER、零新表；
- **不真删数据**：b07 清理走 `KA-36` 作废（`rejected` 留痕），不开 admin `/purge`；
- **不改提示词调用次数**：S2 只改输出形状与候选面，匹配仍是一次调用（`exercise_match` 腿）。

## 前置条件

1. 根仓库四项用户裁决已落（2026-11-13）：套级候选复用 `knowledge_id`／章级分派／b07 走 KA-36
   作废／习题书豁免 KA-68；
2. `af_books.knowledge_id` 已由 `POST /books/sync` 填齐（现网只读核过 17 本全有值）；
3. **与 PLAN-035 协调（同处分流，不得各写一次）**：该壳 R1 要把模型整理行过
   `normalize_entry_levels`（`normalize/toc.py:207`）使 `§N` 归节、纯编号细目按 `KA-62` 折入，
   那是**教材**口径；本壳 S3 要习题集**只留一层章**。两者方向相反但对象不同，必须在同一处后置里
   按 `doc_kind` 分流，不得让习题集先归节再撤节。本壳不改写 PLAN-035 的 S 判据；
4. b07 重跑 outline 批属 LLM 机时，须用户逐段发令；本壳落地前不得重跑（否则新批仍是两层）；
5. 新增文档须同步本仓文档白名单（见 `AGENTS.md`）。

## 接口契约（拟，最终形状由本仓定并回执）

**匹配腿输出**（`exercise_match`）：

```json
{"chapters": [{"source": "习题集章名原文", "target": "教材章名原文", "book_id": "候选编号"}]}
```

- 顶层单值 `book_id` 撤下或保留为「无章级目标时的兜底」，由本仓定；对不上的章不给对象（沿用现行
  「不猜」纪律，`_validated_chapter_pairs` 的两边都认得出才采信）。
- 候选面＝同 `knowledge_id` ∧ `doc_kind=textbook` ∧ 整本绿（`book_stage_reading`）。

**`exercise` 读面**（`GET /api/v1/books/{id}/kp`）：现行七键（`schemas.py:891-904`）保留，
`target_book_id` 单值语义改为「章级目标的聚合或兜底」，新增章级映射键（名由本仓定），
使根侧能逐章渲染目标教材。旧键不删，根侧不消费新键即回旧形态。

**数据根来源书清单**：`write_exercise_meta` 的 payload 由单键 `target_book_id` 扩为章级映射，
`atomic_write_text` 整文件覆写语义不变，但**一次写全**（习题腿重写清单时原样带着章级映射走，
沿用现行 `match` 可选键的纪律）。

**受理门**：`knowledge.py:450-456` 现行「outline 阶段带 target 即 422」须随 S2 复核——
若章级目标只在 outline 批的匹配旁路里产生（不经请求载荷），该门可逐字不动；若需经载荷下发，
则须放开「习题集＋outline」组合并在 `KA-51` 写明边界。**由本仓判并在回执写明取了哪条**。

## 工作项

| 条目 | 内容 | 状态 |
| --- | --- | --- |
| W1 | 现状复核：b07 的 61 行错误读数、套级成员表、`KA-68` 死锁链路三处只读取证（根侧已录，见「过程与证据」） | 已完成（2026-11-13 只读复核） |
| W2 | S1 候选面收窄到 `knowledge_id`（含空值退回旧口径）＋单测 | 已完成（代码与既有单测在册；b17 真机候选仅 b05） |
| W3 | S2 prompt／schema／读面／清单四处改章级分派，一次写全＋单测 | 已完成（b17 八章映射与 194 题包真机读数） |
| W4 | S3 习题集一层章：与 PLAN-035 R1 同处按 `doc_kind` 分流＋单测 | 已完成（b07／b17 均八章一层；四本教材未独立重跑，保留既有回归守护） |
| W5 | S4 `node_types_of` 习题书豁免第二趟＋受理门放行用例 | 已完成（b07／b17 exercise 受理与成行真机通过） |
| W6 | S5 outline 重跑作废面按 `node_kind` 收窄＋题行零变化用例 | 已完成（b17 真机旧题行 `rev` 不变） |
| W7 | 用户发令后重跑 b07 全书 outline 批：旧行 `rejected`、新行一层章；b15/b16 章级分派样本 | b07 已由 PLAN-037 八章重跑覆盖（895 行／题包，旧 521 行撤回退休）；b15/b16 未单独执行，按本轮范围处置不阻塞主功能关闭 |
| W8 | 回归 b05/b12/b13/b18 教材目录逐行相等＋条文随步（`KA-6`／`KA-51`／`KA-68`／`api.md`）＋门禁＋回执根侧 | 代码回归与文档随步完成；四本教材未独立重跑，b05 在 b17 配套链真机零新增；根侧回执由根仓库自读登记 |
| W9 | 习题集目录确定性化（S3 的落地路径）：目录页章行定位（`_EXPLICIT_CHAPTER` 章优先，篇／卷／部仅在零章时取）＋b17 无目录由 `Problem N.M` 章号派生；零模型调用＋单测 | 已完成 |
| W10 | 题号闭集扩到 `\d{1,4}` 并新增英文 `Problem N.M:`（形状族 `problem`；`Solution`／`Solution (sketch)`／`Proof`／`Alternate solution` 属解答正文）＋单测 | 已完成 |
| W11 | b11 答案段按题号回配：`答案` 连续运行页窗口、全局严格单调题号切分、只填空白解答、按题重写题包＋单测 | 已完成 |
| W12 | 三本真机：b07／b11／b17 outline 批与 exercise 批读数（章行集合、题行、配对命中）、b05 教材回归、门禁、回执根侧 | b07 由 PLAN-037 覆盖、b17 已读；b11 按用户令暂缓，未执行不虚构读数 |
| W13 | 目录复用收口：同套单候选确定性命中（零模型调用）、习题集章行直接取目标教材章名、候选面放宽到浅绿（REQ-170 追加请求 A）＋单测 | 已完成 |
| W14 | 分题规则修 104 道：`Problem N.M` 冒号必填（正文交叉引用不充题）、显式英文族锁死、族内乱序容忍＋单测 | 已完成 |
| W15 | 配套提取（REQ-170 主链路）：教材习题段按本章同版页产物重切定题，「章号＋章内题号」配对本书解答；行与题包落**本书**自己的章下、教材零写入；未配上点名原因、多余解答成尾行＋单测 | 已完成（真机读数待 W17） |
| W16 | `exercise_verify` 腿：按章批检「题面／解答是否同一题、切分是否正确」，读数写 `meta.verify_state`；不否决成行、跳过与失败如实披露＋单测 | 已完成 |
| W17 | 读面／作废面收口＋门禁＋b17 真机重跑：上一轮旧行由新配套批取代、读面只读本书行；`pytest tests -q`＋`ruff check src tests`；b17 outline→exercise 全链读数（104／103 幻题复算）＋回执根侧 | 已完成 |
| W18 | **配套 80% 门（KA-71）**：`match_exercise_outline` 去单候选自动命中，加书名同族（书名包含／去后缀归一）与章覆盖 ≥80% 两道确定性门；低于 80% 交模型，0%＋明确章名＋无同族判独立；`match.state` 增 `red` 并持久化；红灯零目录行且不回退教材腿＋单测 | 已完成（`EXERCISE_COMPANION_COVERAGE=0.8`；全局解答证据门与红灯守护在册） |
| W19 | **审核三态（KA-70 升级）**：`EXERCISE_VERIFY_VERDICTS` 增 `reject`；prompt 判切分／对应／正确性／完整性，英文先译后判、note 中文；独立模式在 `_fill_textbook_answers` 后补跑并把三键写回行 `meta`（`set_exercise_verify`）＋单测 | 已完成（三态与坏轮重试守护在册；b07 新批审核读数由 PLAN-037 覆盖） |
| W20 | **b07／b11 分开重跑**：用户发令后分别跑 outline→exercise，b07 与 b11 各按本书 8 章收录、不配 b05；b17 书名同族配 b05 的读数不变；教材 b05/b12/b13/b18 目录行集合逐行不变 | b07 已由 PLAN-037 覆盖；b17 原读数保持；b11 按用户令暂缓，不阻塞本轮关闭；四本教材未独立重跑 |
| W21 | **读面与文档**：`api.md` 补五键、`KA-70`／`KA-71` 条文随步、`exercise_item_ref` 五键透传用例、AF 门禁全绿、回执根仓库 REQ-178 | 已完成（读面、条文与契约守护在册；本轮收口复跑全量门禁） |
| W22 | **审核配对机械修复（根仓库 REQ-177／178 用户令，2026-11-18）**：题号归一（NFKC 全角→半角、剥 `[]（）()【】` 包装、去尾随 `、。：.`）后两侧再逐字匹配；prompt 给「输入 `[2.5]` → 输出 `"2.5"`」样例并明确不带括号；坏轮或零读数同轮重试一次；新增一次性补跑脚本只对 `verify_state='failed'` 且未确认行重发校验并回写 `meta` | 机械修复已完成；旧批全量补跑不再适用——PLAN-037 已用新八批全量替换并确认，旧 521 行撤回退休，本轮不再补跑旧批 |

## 过程与证据

- 2026-11-13｜请求到达（根仓库 REQ-170，根侧实施行 REQ-169）。用户令原文要点：未梳理目录时可
  一键梳理目录，优先匹配**已生成的套**的教材（一套不是一本，一套可有多本教材，一套习题集可有多本
  习题集）；首先 AI 匹配，匹配完成后可重新自动匹配生成然后替换；完成后习题集目录**只有章节、
  一层、不再包含知识点**（与教材区分）；已生成的是错误数据，清理掉再优化；先完成目录部分。
- 2026-11-13｜根侧只读取证移交（`GET 8902 /api/v1/books`，17 本全填 `knowledge_id`）：
  `kt-mathanalysis-3`＝教材 b05＋习题集 b07/b11/b17；`kt-mathanalysis-4`＝教材 **b12/b13/b14
  三本**（微积分学教程 Vol.1/2/3）＋习题集 b15/b16；`kt-mathanalysis-2`＝b18；
  `kt-mathanalysis-1`＝b01。现行 `orchestrator/knowledge.py:3590-3597` 按 `course_id` 粗筛
  → b07 匹配时 b01/b05/b12/b13/b14/b18 **六本混选**；`engines/kp_gateway.py:590` prompt 明写
  「最可能配套哪**一本**候选教材」、:595 输出章对无 `book_id`。
- 2026-11-13｜b07 错误数据读数（`GET 8902 /api/v1/books/mathanalysis-b07/kp`，61 行）：
  `model_ref='llm:toc'`、`gen_batch=afkp-d94471b5ba6c`、`job_id=31c52a287f44`、`node_kind` 全
  `outline`、`gen_status` 全 `candidate`、`confirmed` 全 **0**；depth=1 仅 **6** 行、depth=2 有
  **55** 行「§ N. …」；读面派生 `node_type`＝**point 55／chapter 5／other 1**、`toc_role`＝
  reference 1／knowledge 60。父挂错乱：**「前言」（`afkp-728c1aa9a547`）下挂 5 枚第三章的节**
  （§2 有理函数的积分法 … §6 求函数积分的各种例子），第一/二/三章章行未生成，章号跳号只有
  第四~八章；「前言」显示段 `page_start=6, page_end=158` 吞 152 页。锚点页跨 **6~434**
  （正文页而非目录页）→ 目录腿把正文节标题当成了目录行。题行 0、题包 0、
  `exercise.kind=standalone`、`target_book_id=null`、`stage.done=false`。其余习题集无错数据：
  b11 目录 0 行，b15/b16/b17 `pages_done=0`。
- 2026-11-13｜清理路径（根侧只读代码核对，本仓复核）：`accept_kp_job` 不传页区间时默认全书
  `start=1, end=page_count`（`orchestrator/knowledge.py:392-393`）；`KA-36` 作废面＝同书 ∧
  非本批 ∧ `candidate` ∧ `confirmed=0` ∧ 任一锚点页入区间（`db/repositories.py:1905-1924`）。
  b07 的 61 行锚点 6~434 全落在 1..page_count 内且 `confirmed=0` → **重跑一次全书 outline 批
  即把 61 行全部作废**，无需单独作废腿、无需真删。另核：`reject_draft_nodes`
  （`db/repositories.py:1939`）只在 `finalize_batch` 内部被调（`knowledge.py:5774`，KA-13 让位），
  无 API 端点暴露；`PUT /books/{id}/kp/outline`（KA-8，`api/main.py:1503-1547`）经
  `replace_outline` 只写 `parent_id`/`order_hint` 两列，不能删行也不能改 depth/name。
- 2026-11-13｜`KA-68` 死锁链路：`normalize/toc.py:1073-1075` 把无内容子行的章降 `other`；
  `book_stage_reading` 统计域只认 chapter/point（`knowledge.py:295-297`），exercise 受理门要求
  本书在册目录行非零（`knowledge.py:397-406`）。习题集只留一层章且题行未落 → 全部章判 `other`
  → 本书 0 行 → **永远无法进 exercise 阶段**。S4 即为此设。
- 2026-11-13｜承接状态：本行登记当刻**待本仓会话评审与实施**；根侧只在 W8 回执时复核。
- 2026-11-16｜**改判与授权（REQ-170 追加请求 A）**：用户令原文「REQ-170 追加请求 A（浅绿放行）
  已入 R1 这个事情我授权你改AF项目。当前我掌握的信息是这三本习题集都可以找到完整目录，而且都可以
  对应习题和习题解，先保证这两个流程正确且成功，然后再来处理核心外的细节和异常处理」。
  授权面＝本仓代码改动（原文「我授权你改AF项目」），优先序＝先跑通「完整目录」与「习题↔习题解
  对应」两条流程，外围异常后置。R1 已完成（`book_stage_reading.ready` 浅绿口径，见 `KA-53` 节）。
  本令把「只改匹配面／读面」扩到**目录生成路径本身**，W9~W11 即按此新增；S1~S5 判据字面不动。
- 2026-11-16｜三本真机只读取证（`tmp/probe_chapters.py`／`tmp/probe_b11.py`，产物在 `tmp/`，
  不改数据根、不调模型）：
  - b07（`31c52a287f44`，439 页）：目录页 `[7,8,9]`，`toc_entries` 出 8 条章、偏移 `+9`，
    回定位全中（10／81／149／185／222／285／348／374），无独立答案段（提示／解题思路／解随题）。
  - b11（`49a3950dc972`，406 页）：目录页 `[9,10,11,12]`，含 8 条章＋「第一部分／第二部分」两条
    分部行（回定位失败，须按章优先剔掉），偏移 `+12`，章页 15／75／128／152／181／231／275／292；
    `答案` 运行页眉连续覆盖 **348~400**（401 起为人名对照表／译后记，无该页眉），第八章正文止于 347；
    题号全局递增至 4462（章尾：820／1627／2180／2545／3135／3710／3900／4462），答案键至 4460。
    答案页 OCR 已退化：p348 下半为重复残行、答案 1~15 缺失、多条跳号——配对只能「切得出多少配多少」，
    不得把残行当答案。
  - b17（`b07131ed6289`，107 页）：无目录页；103 道 `Problem N.M:`，章号由题号首段派生，
    章首页 `[(1,1),(2,7),(3,14),(4,31),(5,43),(6,53),(7,62),(8,79)]`，答案随题（`Solution` 等）。
- 2026-11-16｜**b17 第 5 章题号族锁死缺陷（真机发现，已修）**：根因＝章界按页切，第 5 章首现页
  p43 的行首是上一章的公式编号 `(7)`；`split_exercise_items` 把这枚弱形状当首题接受，族锁成括号、
  单调序水印抬到 7，同节其后全部 `Problem 5.x:` 因族不符被判成正文 → 第 5 章只剩 1 题（离线规则
  读数 80 题，章分布 7/15/9/13/**1**/7/14/14）。修法＝开切前先定族：本节出现显式 `Problem N.M:`
  行即锁 `problem`，弱形状行一律算正文（`normalize/exercise.py` `_has_problem_label` 与
  `split_exercise_items` 初始族）。回归用例
  `tests/unit/test_normalize_exercise.py::test_explicit_problem_family_outranks_a_stray_bracketed_line`
  先红（`['7']`）后绿；离线复算 b17 章分布 7/15/9/13/**10**/7/14/14＝89 题（余下差值是严格单调序
  的正常少切：3.6~3.16 在 PDF 页序里排在 3.21 之后、5.12 排在 5.13 之后，按「宁可少切」留下）。
  门禁：`pytest tests -q`＝1674 passed／18 skipped；`ruff check src tests` 全绿。
- 2026-11-16｜**b17 章界页串题缺陷（旧批收尾读数暴露，已修）**：修复前批 `afkp-c1d3b557cfca`
  收尾 94 行、章分布 12/18/11/14/**1**/8/16/14。根因＝`section_spans_of` 按页给区间、块界写在
  `start_block`／`end_block`（Homework 1 止于 p7 块 1、Homework 2 从 p7 块 2 起），而习题腿的
  `_page_bodies` 只按页取、丢块界 → 边界页整页喂给两节，下一章开头的题被上一章再切一遍
  （Homework 1 里出现 `Problem 2.2…2.6`、Homework 4 末尾出现 `Problem 5.1`）。修法＝新增
  `_section_block_bodies`（起点页／终点页只留界内块、`blocks_to_markdown` 重渲染后剔家具；块产物
  缺该页退回整页原文），习题腿四处取材改用它（独立模式、配套模式、题行重跑两处）。回归用例
  `tests/unit/test_knowledge_pipeline.py::TestSectionBlockBodies` 三枚；块级渲染与磁盘原文对同一题
  逐字相同（b17 Homework 1 七题同文）。离线复算八章分布 7/15/9/13/10/7/14/14＝89 题。
- 2026-11-13｜登记当刻门禁读数（根侧只写文档后跑，供本仓会话接手时对照）：本仓
  `tests/contract/test_tracker_governance.py`＋`test_plan_governance.py`＋`test_markdown_links.py`
  ＝**16 passed**；本仓全量 `tests/contract/`＝**2 failed, 328 passed**。两枚红用例＝
  `test_api_v1_contract.py::TestKpCurationEndpoints::test_a_content_round_reports_its_six_readings`
  与 `…::test_regenerating_a_chapter_summary_uses_the_chapter_material`（后者断言
  `stub.chapter_calls` 的章材料名单多出一枚同名章）。当刻本仓工作区 `develop` 自带
  **13 个源文件／3861 insertions** 未提交改动（`api/main.py`／`engines/kp_gateway.py`／
  `normalize/toc.py`／`db/repositories.py`／`schemas.py` 等），两枚红用例属该在途状态、
  与本轮入站请求的文档写入无因果关系。**W1 现状复核须先判这两枚红是否本仓在途改动的回归**，
  再动 S1~S5；根侧按协作规范不产生本仓代码改动，此项归本仓会话。

- 2026-11-16｜**配套提取取代跨书合并（REQ-170 用户定链路，W13~W15）**：用户令原文「第一步：匹配上
  《数学分析原理》教材，目录可以直接复用《数学分析原理》教材章节段（只筛选出章节）的目录；第二步：
  判断出《数学分析原理》教材中有题目，b17是对应的习题解，然后按照章节和块提取出题目和习题解；第三步：
  按照章节进行梳理，将对应的习题和习题解拼上每个习题+习题解是一块，按照章节落在知识点梳理的正文部分，
  并让LLM判断每个题目+习题解是否正确切分，是否对应」。配套读数裁决原文「进行整理，能配对上的，需要人
  确认的，不能配对的（下列可选解答，或者没有解答），按照顺序排列（原始题目顺序），后续右侧栏可以选项
  勾选批量确认和批量重新梳理」；旧跨书合并的处置裁决「由新配套提取取代（推荐）」。
  实施（`orchestrator/knowledge.py`）：新增 `extract_companion_exercise_items`——题面＝教材该章习题段按
  **自身版本**页产物重切（`_target_problem_table`；习题段起点页早于本章起点页整章跳过并披露，b05 第 4 章
  旧锚 p7 由此挡住）；解答＝本书同章切分（`_section_block_bodies`）；配对键＝`_chapter_local_item_no`
  （章号＋章内题号，`7-1` 与 `1.` 同键）；行与题包落本书自己的章下，锚点只指本书本版（块界
  `_exercise_item_bounds`／`_exercise_item_anchors`）；`meta.pair_state` 三态（`matched`／`unpaired`）＋
  `pair_reason`，多余解答成尾行（「教材本章未收录本题」）、教材有题无解成行留占位（「习题集未收录本题」／
  「本章解答读取失败」）；教材零行零包。删除 `merge_exercise_book_items`／`_match_chapter_aliases`／
  `_chapter_exercise_targets`；选中题重解析改在本书内重写并回读教材题面；读面恒读本书行；目录腿写入的
  目标由习题批自动沿用（不再要求每次重带）。测试：配套块 7 枚重写（配对落本书／教材有题无解／多余解答
  尾行／读取失败点名／教材章缺位披露／目录批记住目标／教材零新增），`TestExerciseOutlineMatch` 与读面
  用例随改判重写，重试两枚旧目标行用例保留（旧合并残留行仍可修补）。门禁：`pytest tests -q`＝
  **1684 passed／18 skipped**；`ruff check src tests` 全绿。
- 2026-11-16｜**校验腿（W16，REQ-170 第三步的 LLM 判读）**：设计落 `KA-70`。缺陷面：题对成行后模型不再参与「这一刀切得对不对」——`pair_state` 只答配对，屏侧挑不出可疑题批量复核。实施：
  - 引擎面：`engines/base.py` 增 `ExerciseVerifyItem`／`ExerciseVerifyInput`／`ExerciseVerifyReading`／`ExerciseVerifyReply` 与 `KnowledgeEngine.exercise_verify`（可选腿，与 `exercise_classify` 同轴）；`engines/kp_gateway.py` 增 `_EXERCISE_VERIFY_PROMPT`（两问一判、只出 JSON）与 `exercise_verify`——题号照抄、`verdict` 闭集外钳 `suspect`、空回复与缺 `items` 数组抛 `EngineError`。
  - 编排面：`orchestrator/knowledge.py` 增 `EXERCISE_VERIFY_TEXT_BUDGET`(12000)／`EXERCISE_VERIFY_VERDICTS`／`_verify_pair_rounds`（按题面＋解答累计字符分轮、保序）／`_exercise_verify_fragments`（失败只记 `verify_state='failed'`，不否决成行、不碰 `failed_sections`）；`extract_companion_exercise_items` 改为先建 plans／pairs 两表、校验后逐行落 `meta`；`exercise_item_ref` 读面透传 `verify_state`／`verify_verdict`／`verify_note`；`LedgeredKnowledgeEngine.exercise_verify` 一调用一行账（内层无此腿回 `None`）。
  - 用例：`TestExerciseBookAcceptance` 四枚（逐题读数落 `meta` 且配对不变、调用失败两行全 `failed` 且 `failed_sections==0`、无该腿零 `verify_` 键、长章分两轮各带章内题号）；`TestExerciseVerifyLeg` 四枚（提示词带章名／`[1]`／题面／解答且步骤名 `exercise_verify`、回复保留 `verdict`＋`note`、闭集外结论钳 `suspect`、空回复抛错）。
  - 门禁：`pytest tests -q`＝**1692 passed／18 skipped**（67.02s）；`ruff check src tests`＝`All checks passed!`。b17 exercise 批真机读数待 W17 重跑取证。
- 2026-11-16｜**W6 作废面收窄（S5）**：`finalize_batch` 增 `doc_kind` 参（`run_knowledge_job` 传 `book.doc_kind`），
  `stage='outline'` ∧ `doc_kind='exercise'` 时作废面收到 `NODE_KIND_OUTLINE`——目录重跑只作废旧目录行，
  `llm:exercise` 题行的 `gen_status`／`rev` 零触碰；教材侧目录重跑维持旧口径（不在 S5 范围内）。
  用例 `TestExerciseBookStage::test_an_outline_rerun_only_replaces_the_chapter_skeleton` 先红（题行被判
  `rejected`）后绿；条文并入 `KA-36`（作废面按 `stage` 收口段）。门禁：`pytest tests -q`＝
  **1694 passed／18 skipped**（65.72s）；`ruff check src tests`＝`All checks passed!`。

- 2026-11-16｜**b17 真机全链（W12 首批／W17）**：outline 批（复用目标教材目录，零模型调用）落 8 行一层章
  「第1章 实数系和复数系」…「第8章 一些特殊函数」（章名取自 b05），上一轮 8 条旧目录行 `rejected`；
  同刻上一轮 `llm:exercise` 旧题行 `rev` 不变（W6 收窄真机生效）。exercise 批（配套模式）：
  `meta.kind=companion`、`match.state=hit`（候选仅 `mathanalysis-b05`）、章级映射 8 条、`unmatched_solutions=4`；
  读数＝`candidate_rows=194`／`exercise_item_rows=194`／`exercise_pack_rows=194`；配对 `matched=89`／`unpaired=105`，
  状态 `complete=103`／`problem_only=91`；上一轮 `llm:exercise` 批 94 行全 `rejected`（本批 `superseded_rows=89`）；
  `failed_sections=0`／`failed_chapters=0`；教材 b05 零新增（行父全为本习题集章行、锚点只指 b17 本版）。
  磁盘：`dataset/.../b17/kp/exercises/meta.json`＋8 枚章索引 JSON 合计 194。读面样本（`afkp-aa42bddb8976`，
  Problem 3.1）：题面引自教材 p.75、解答引自 b17 p.14、来源两行齐。根侧 `GET /books/{id}/kp` 只增补 `stage` 键、
  其余行字段原始字节透传（`api/axiom.py` `kp_relay`），配套／校验读面直达屏面。
- 2026-11-16｜**校验腿真机读数（如实披露，本轮不动码）**：194 行中 91 行占位（`problem_only`）按设计不送检；
  送检 103 行＝`done/ok=22`、`done/suspect=55`、`failed=26`（`verify_note` 空）。26 枚失败样本的网关回复均为
  JSON 被截断（末尾停在 `\int _ { a } ^ {  {`、`{"item_no": "23", "verdict": "suspect"` 一类），`_object` 能修
  转义不能修截断；分布 ch1=4／ch3=5／ch4=3／ch7=5／ch8=9。属核心链路外的异常处理面，候选修法（按题数或字符
  再缩轮／提示词限短注记／`KA-70` 开部分回收口）待用户裁决后另轮实施。
- 2026-11-16｜**W17 门禁**：`pytest tests -q`＝1694 passed／18 skipped（141.92s）；`ruff check src tests`＝
  `All checks passed!`。

- 2026-11-17｜**配套读面改挂目标章（根侧待梳理清零）**：`exercise_workspace` 在 `kind='companion'` 时把 `items[].parent_id` 按章号改挂目标教材同号章（读时派生，库行零改动、教材零写入；目标书缺同号章退回本书章）。新增用例
  `TestExerciseIndexAndReadFace::test_a_companion_read_face_reparents_items_to_the_target_chapter`（读面父＝目标章、库行仍为本书章、目标书零写入）。门禁：`pytest tests -q`＝1695 passed／18 skipped；
  `ruff check src tests`＝`All checks passed!`。真机：8902 重启后 `GET /books/mathanalysis-b17/kp` 的 194 枚题行父 id 全落在 b05 的 8 枚章；8903 屏上「待梳理」消失，章 1~8 分别铺 20／30／25／14／29／19／26／31 个方框，
  章 9~11 无题（b17 未收录），控制台零报错、请求全 200。

- 2026-11-17｜**三阶段类型判定与审核三态请求到达（根仓库 REQ-178）**：用户两裁——① 预处理先判配套型，
  不成立才判独立型，两者皆无＝红灯零目录；② 配套按书名同族或章覆盖 ≥80% 放行，不要求逐章全中。
  据此拆 W18（`KA-71` 配套门与红灯）、W19（`KA-70` 三态＋四判＋英文先译＋独立模式补 verify）、
  W20（b07／b11 分开重跑）、W21（读面文档与回执）。只读取证：b17 书名含 b05 书名⇒配套；
  b07／b11 书名不含且章名不同⇒独立型；三本均有解答证据（b07 中文行首 945 处、b11 答案段
  p348-400、b17 英文 `Solution/Proof` 221 处），红灯不会误杀。

- 2026-11-18｜**W22 审核配对机械修复（根仓库 REQ-178 用户令，先取证后落码）**：用户判读「不是解答
  判错，是题号配对机械失败」。b07 实况 662 笔 `verify_state='failed'` 拆因：472 笔模型把输入
  `[N]` 原样抄回、98 笔抄成 `（N）`，两侧逐字比全部落空；92 笔坏 JSON／空回复且无重试。落码三处：
  ① `_exercise_verify_fragments` 新增 `_verify_readings`，回复题号经 `exercise_item_key` 折键
  （NFKC 全半角、剥 `[]（）【】` 包装、尾随标点）后与书名一侧的裸键比；② 坏轮（`EngineError`）或
  回了但零域内读数，同轮原样重试一次，两次都坏才记 `failed`；③ `_EXERCISE_VERIFY_PROMPT` 输出样例
  改为裸题号 `"2.5"` 并明说「外层方括号不要带回来」。用例先红后绿：`TestExerciseItemKey`（包装／
  全角／尾点／点号折键）、`TestExerciseVerifyLeg` 提示词样例与禁令、`TestExerciseBookStage` 的
  `[1]`／`（2）` 折算命中、坏轮重试成功、零读数重试成功、连续坏轮恰两次；长章分轮用例的桩改回
  逐题 `ok`（空回复在新口径下是坏轮，不再当作终局）。另按契约修两处日期字面量（设计正文 KA-70／
  KA-71 的「（2026-11-17 用户令）」改「（用户令）」、台账 PLAN-036 行去掉「；2026-11-17 追加」），
  纯体裁不改语义。一次性补跑脚本 `tmp/verify_backfill.py`（verify-only：读题包重建题目／解答两段、
  按顶层章分组、同章撞号只交首个、`confirmed=0` 守卫、写回 `meta` 三键）；dry-run 盘点：662 笔
  failed 全部有完整材料（entries=662、skipped 零），库内现状 state=failed 662／done 217／无键 16，
  verdict=ok 110／suspect 81／reject 26，与屏面读数逐项相等。门禁：`pytest tests -q`＝1715 passed／
  18 skipped（75.03s）；`ruff check src tests`＝`All checks passed!`。8902 经 8900 控制面重启
  （服务名 `axiom`，pid 29592，online，库 `qed_test`）。**未做**：b07 的 LLM 补跑（机时，需用户
  单独发令）与补跑后 8903 徽章核对。

- 2026-11-18｜**补跑发车后按用户令暂停＋已验部分置确认（根仓库 REQ-178 用户令续）**：补跑发车
  （批号 `verify-backfill-20261118135834`，662 笔材料齐），跑至 **17 轮模型调用**（全部 success，
  含第 8 章截断坏轮经同轮重试、1 笔非法转义宽松修复）后用户令暂停——`_verify_independent_pairs`
  整批只在结尾一次 `commit()`，**这 17 轮读数未落库**，暂停后库内仍 `failed 662`。用户裁「先部分
  完成」＝把已持久化、结论 ok 的题行经确认腿置「审核成功」：新增一次性 `tmp/confirm_ok_b07.py`，
  逐行 `POST /api/v1/kp/nodes/{id}/confirm`（`if_rev` 守卫、不直改库），**110/110 成功、零失败**。
  核对：8902 与 8900 中转读面同口径（items=895，confirmed=1 共 191＝新确认 110＋既有 81；
  state failed 662／done 217／无键 16；verdict ok 110／suspect 81／reject 26）；8903 习题面
  `#/admin/kp-review?book=mathanalysis-b07` 徽章实测 ok 110／suspect 81／reject 26／failed 662
  （与读面逐项相等），控制台零错误（仅既有 KaTeX 数据噪声告警）；导出面 `GET …/kp/export`
  191 行全 `confirmed=1`（练习消费门已通）。全量补跑（662 笔）与 suspect/reject 的后续确认
  待用户再发令。

- 2026-11-22｜**根仓库回执（REQ-169 根侧已按 Achieved 收尾；全局红灯由根会话代实施）**：用户在根仓库裁决「REQ-169 按照这个口径收尾」并确认链路口径＝①习题集优先匹配已有教材，提取目录后比对，基本一致直接复用教材一层章；②无教材匹配选自身一层章；③配不出「习题＋解答」配对时红灯零目录。查证结论＝①②已在本仓实现（`match_exercise_outline` 书名同族／章覆盖 ≥80% 门、`_companion_outline_match` 章名复用、`doc_kind='exercise'` 一层章分流）；③原实现只对独立型判红，经用户裁决**红灯扩为全局门**——由根仓库会话经用户授权代改本仓 `orchestrator/knowledge.py`：`_companion_outline_match` 与模型命中出口统一加 `_exercise_solution_evidence` 证据门（`evidence` 非空时校验，缺证据＝`red` 零目录；`evidence` 缺席不判红），`KA-71` ④段随步为「全局证据门（2026-11-22）」。本仓门禁＝全量 **1721 passed／18 skipped**、`ruff check src tests` 净、设计文档契约 10 passed；新增守护 `test_a_companion_hit_without_solution_evidence_is_also_red`；配套无证据的四个旧用例按新语义补证据后转绿。8902 已经 8900 `/services/axiom/restart` 重启加载新逻辑，b07 现网零误伤（8 章＋895 题完好），b17／b11 均有解答证据。**本仓待办提示**＝W2~W5 状态列与代码实况滞后（S1/S2/S3/S4 语义均已落地），W18（80% 门）代码已在册（`EXERCISE_COMPANION_COVERAGE=0.8`），请本仓会话按其台账自行核验收口；根侧 REQ-169 壳已 Retain 归档 `docs/history/plans/2026-11/2026-11-13-exercise-outline-set-matching.md`。

## 验证与验收

- **单测**：同 `knowledge_id` 候选收窄／`knowledge_id` 空退回旧口径／章级分派逐章带目标／
  习题集只成一层章／习题书豁免 KA-68 后受理门放行／outline 重跑只作废目录行且题行 `rev` 零变化。
- **真机**：b07 重跑后旧 61 行 `gen_status='rejected'`、新目录行全 `depth=1`／`node_type=chapter`、
  零 `§N` 与零 `point`、匹配落 b05；b15/b16 逐章分派到 Vol.1/2/3 的样本读数。
- **回归**：b05/b12/b13/b18 教材目录行集合与身份位逐行相等；既有批次账本零变化；
  教材侧 `KA-68` 与 PLAN-035 用例不改即绿。
- **门禁**：`pytest tests -q`＋`ruff check src tests`；改类型派生与持久化语义须补 MySQL 集成层。
- **文档**：`KA-6`／`KA-51`／`KA-68` 与 `api.md` 随步，新增文档同步白名单。

## 回滚

- S1／S2＝匹配腿与读面改动，退回 `course_id` 粗筛＋单值 target 即恢复旧行为；读面新增键为增量，
  根侧不消费即回旧形态。
- S3／S4＝按 `doc_kind` 分流，撤分流即回两层；教材侧从未改动。
- S5＝作废面收窄是调用侧 `node_kind` 实参，传 `None` 即回旧口径（函数签名不变）。
- b07 清理＝`KA-36` 只翻 `gen_status`，行仍在库可按 `gen_batch` 反查；`rejected` 是终态，
  恢复旧读数须人工发令。零 ALTER、零迁移、零真删。

## 关闭与归档

### S1~S5 与残余处置（2026-11-25）

| 判据 | 功能判定 | 读数与边界 |
| --- | --- | --- |
| S1 套级候选 | 达成 | `_exercise_match_candidates` 按 `knowledge_id` 收窄并保留空值退回；b17 真机候选仅 b05。b15/b16 未单独跑模型样本 |
| S2 章级分派 | 达成 | prompt／schema／读面／清单四处同形；b17 八章映射、194 题行与题包、89 配对／105 未配对均有真机读数 |
| S3 一层章 | 达成 | b07／b17 均八章 `depth=1`；教材分流守护在册。b05/b12/b13/b18 未独立重跑，不以推测读数补证 |
| S4 豁免 KA-68 | 达成 | `node_types_of(..., exercise_book=True)` 与受理门放行；b07／b17 exercise 阶段真实成行 |
| S5 作废面收窄 | 达成 | outline 重跑只作废旧目录；b17 真机旧题行 `rev` 不变，b07 新批全量替换由 PLAN-037 留证 |

**范围收口**：W1~W6、W9~W19、W21 的代码、守护与可用真机读数均已落册；W7／W20 的 b07
由 PLAN-037 八章重跑覆盖；W22 的旧批补跑被新批全量取代，判为不适用。b11 按用户令暂缓，
b15/b16 与四本教材的独立真机重跑未执行，明确排除在本轮收口证据之外；本轮只清数据与范围残余，
不改配套提取主功能。按用户「主线已实现并分开验收」的收口口径，上述未执行样本不阻塞 PLAN-036 关闭。

**关闭结论**：PLAN-036 于 2026-11-25 关闭，功能目标达成；归档判定 **Retain**，保留改判推导、
b07／b17 真机链与未执行样本的明确边界。根侧 REQ-170／REQ-178 的回执由根仓库自读登记，本仓对根零写入。
