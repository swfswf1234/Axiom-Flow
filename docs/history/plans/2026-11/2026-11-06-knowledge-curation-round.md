# 知识梳理轮接口与链路统一实施（knowledge-curation-round）

状态：Completed
任务类型：B
最后更新：2026-11-24
关联 ADR：[ADR 0004](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900：新增的习题腿与详情腿同样只经适配层出站）、
[ADR 0007](../../../adr/0007-parse-status-semantics.md)（进度与结局分列是本案梳理结局位的形状来源）、
[ADR 0015](../../../adr/0015-db-single-source-for-readiness.md)（就绪的唯一事实源是 DB，本案不把产物文件升成判据）、
[ADR 0016](../../../adr/0016-af-family-rebuild-into-four-tables.md)（决定 2 的「当前态四源」与决定 10 ① 的「同步 upsert 禁写 `af_books.meta`」＝本案必须加真列的直接依据；决定 11 已立「加列不加表」的先例）、
[ADR 0013](../../../adr/0013-ledger-row-three-elements.md)／[ADR 0018](../../../adr/0018-task-column-bound-and-progress-ban.md)（本案台账行的三列口径）
本案另需一枚新 ADR（ADR 0019）：把「梳理结局」写进 `af_books` 当前态清单，并为档 B 加列放行——W1 的前置立法。
关联设计：[knowledge-analysis.md](../../../design/knowledge-analysis.md)（`KA-1~KA-50` 在册条文＝本案改动面基线，新条从 `KA-51` 起）、
[parsing-management.md](../../../design/parsing-management.md)（`PM-13` 单实例串行前提）、[model-integration.md](../../../design/model-integration.md)（出站与槽位登记）、
[database-design.md](../../../architecture/database-design.md)（`af_books` 列清单正本）、[api.md](../../../architecture/api.md)（④ 类端点形状）、
[operations.md](../../../guides/operations.md)（真库 ALTER 窗口的备份与回滚 runbook）
关联 Tracker：docs/trackers/todo.md（PLAN-028 镜像行与 ARCH-035 聚合行；本案承载 ARCH-036/037/038/039/040/041 六枚功能任务行与 REQ-155 出站请求行；随步壳行 PLAN-025／026／027／029 与入站 REQ-139／142／143／146 在 todo「普通任务轮」节，承载仍指各自壳）
归档判定：Retain（种类分流与四链路的裁决推导、真库加列窗口的前后指纹、与根侧 ADR 0028 两级闸的对账读数不可再生；判据本体晋升到 `docs/design/knowledge-analysis.md`）

> **本案的性质**：它不重复 PLAN-025／PLAN-026 已经交付的通路，只收它们的**形状欠账**。用户 2026-11-06 把
> 知识点梳理模块重新定义为「解析完成之后对已解析文档的分析与梳理」并给出三块内容（目录探索／内容解析／
> 知识点详情），要求把分散任务与根侧入站请求统一进本轮。比对现有条文与代码后的结论是：通路已全，**分流
> 没有**——「教材 vs 习题集」这一维在 schema、ORM、仓储、编排、prompt 五层零落点，习题只作为教材内部的
> `toc_kind='exercise'` 目录行存在（`KA-46`），且被明令排除在总结／导读／抽点三条生成腿之外。本案交付的
> 就是这一层分流与它的接口面；第二轮另加一条——教材内部的习题要先按「章 → 题号」成行（`KA-53`），它是
> 习题集合并的前置，故「习题」在教材侧也有一条自己的腿。
>
> **任务面**（用户 2026-11-06 第二轮裁决）：按功能分块，切成**四个大任务**（三块＋一次全流程联调验收）与
> **两枚重要任务**（底座＝种类与流程判据、习题集题目腿与跨书挂章）；具体小任务与在册壳移入 todo 的
> 「普通任务轮」节，随对应大任务收尾而关闭。v1.0 轮的梳理侧（PLAN-025／026／027、REQ-139／142／143／146）
> 随本轮迁出，v1.0 此后只做文档解析与知识库优化——这是本轮「拒绝过多任务导致混乱」的落地形状。
>
> **执行序**＝重要任务一 → 大任务一 → 大任务二 → 重要任务二 → 大任务三 → 大任务四。习题集合并要落在
> 教材侧的题行上，而题行由大任务二产出，故重要任务二排在内容解析之后；每行的启动条件＝上一行交付面在本地
> 可读，同一时刻只有一行 In Progress。

## 目标与成功标准

1. 一本书的种类可判且被判：`af_books.doc_kind` 有值有写者，梳理受理门按种类分流，未声明者**当场拒绝并回显告警**。
2. 「解析完成 → 开始梳理 → 梳理完成」三段各有一个在册判据位，书目列表一次读全，不需要客户端猜。
3. 教材走目录探索与内容解析，并先把**教材内部习题**按「章 → 题号」成行（习题集合并的前置）；习题集走题目腿：习题集批次**不产出目录行**，其题目与解答按同一把键挂进对应教材的题行。
4. 四类行（章节／知识点／其他／习题）各有自己的链路判据与产物落点；「其他」维持零生成腿。
5. 知识点详情给出「这个点要掌握什么」与关键定义清单，章行给出本章学习内容与导读。
6. 重试面按部分拆三处：目录探索的重试只对教材成立且＝**同范围重发**（无页子集重跑）；按失败节点重跑只对 `content` 成立；习题腿按**选中题行**重发只对 `stage='exercise'` 成立（新批次、只覆盖未确认机器行）。批次号一律新发、「无续跑」判据不改。
7. 真库加列窗口有前后指纹、可升可降，共享主表**永不含习题行**（与根侧 ADR 0028 两级闸对齐）。
8. 任务面收得起来：**四个大任务＋两枚重要任务**（见「核心任务与在册行的挂靠」表），具体小任务与在册壳在
   todo「普通任务轮」节随大任务收尾；大任务四把三块串成一条全流程回归，并给根侧一份前后端联调所需的字段
   与错误码清单。

## 范围与非目标

**范围**＝`af_books` 两枚真列（种类＋梳理结局）与同步腿；受理门的种类闸；教材侧「章 → 题号」的习题梳理腿与
习题集侧的题目/解答配对腿及跨书挂章；四链路分流与裁剪产物落盘；知识点详情的读数形状与提示词；重试面拆分
（目录同范围重发、内容按失败节点重跑）；④ 类端点判定序随步；新 `KA-51~KA-53` 条文与
`api.md`／`database-design.md`／`code-map.md` 晋升；真库 ALTER 窗口的驱动与取证；总流程回归与联调字段清单。

**非目标**＝① 不新增表（`af_*` 四表封闭，ADR 0016）；② 不改 `BlockType` 闭集、不改解析管线产物形状；
③ 不做 8903 界面与 8900 透传/提升作业的实现（归根仓库，本案默认只发 REQ-155 一枚出站请求，W15 的联调清单若
   发现缺口再按需另立一行）；
④ 不代替 PLAN-027 跑整本真机梳理窗口（本案只在 W14 的加列窗口里做 DDL 与指纹，梳理轮次不重复发令）；
⑤ 不在本案解禁 `QED_TEST_MYSQL=1`（业务库 `qed` 保持为空的口径不变，加列窗口的连库面另行批准）；
⑥ 梳理质量配方的真机标定属 C 类，本案只交形状与冻结件，不交「跑得好不好」的结论。

## 前置条件

1. 基线＝`develop` 当前工作树（含 PLAN-025／026 的实现批与未入库的 `KA-46~KA-50` 条文）；进场前先把这部分
   随步入库，避免本案的条文号与它们抢位。
2. **ADR 0019 先立**：`af_books` 当前态清单里没有「梳理」这一维（ADR 0016 决定 2 只列了进度／结局／生效指针
   三项且专指解析），加列与加语义都要先有决定，再动 DDL。
3. **REQ-155 先到**：`doc_kind` 的输入集是根侧 8900 `POST /books/sync` 的 payload。现网取证＝
   `backend/qed_engine/api/axiom.py:134-147` 逐键白名单构造，**不含 `roles`**；而 `qt_books.roles`
   （取值 `textbook`／`exercises`／`solutions`）在 8901 `list_books()` 已出全列（根侧 `learn.py:577-604` 的
   `_tracker_book_rows`／`_is_textbook` 就是它的现成消费方）。这一键不到，W1 的写者就是空的。
4. 与根侧 ADR 0028 的对齐判据：关口①＝本仓 `toc_kind='exercise'`／`node_type='exercise'` 语义保持稳定
   （根侧已据此升格为提升判据输入且明文**不向本仓发新增改码请求**）；关口②＝根侧写共享表时再筛一次。
   本案新增的习题行必须继续落在这两把闸的覆盖域内，**不得**让习题行进共享主表。
5. PLAN-027 的 A 档闸门（R7／R11／R17）与本案的加列窗口分属两件事：本案不解除也不重复它的批准。
6. 台账形状已定：todo 的「知识梳理轮」节挂 ARCH-035（聚合）＋本壳镜像行＋ARCH-036~041 六枚功能行，
   「普通任务轮」节挂在册壳与 REQ 行；本壳的工作项只在这六行内分派，增删行位随本壳同步。

## 已裁定的形状（用户 2026-11-06 裁决）

| 编号 | 裁定 | 落点 |
| --- | --- | --- |
| C1 | 种类**同步即得**：`doc_kind` 由根侧 `qt_books.roles` 派生，经 `POST /books/sync` 入 `af_books` **真列**（不是 `meta`——同步 upsert 禁写 `meta`，ADR 0016 决定 10 ①） | `BookSyncItem`、`BookRepository.upsert`、迁移 |
| C2 | 未声明种类的书**不按教材走**：这是异常，受理时**拒绝**并回显告警（与根侧 `_is_textbook` 的「空即教材」容错**有意不同**，本案不做同款放宽，差异写进条文） | `accept_kp_job` 种类闸 |
| C3 | 「梳理完成」要有**一个判别位**：`af_books.last_curation_result`（`''`／`completed`／`partial`／`failed`／`interrupted`）；「开始梳理」不另立位，沿用在册的 `gen_status='draft'` 存在性判据（`KA-4`） | `af_books`、`finalize_batch`、启动期归一 |
| C4 | 目录探索**只做教材**；习题集的内容归纳进对应教材梳理出的章节中 | 种类闸＋挂章腿 |
| C5 | 「其他」类**不需要梳理什么**：维持零生成腿（在册行为就是终态） | 条文随步，无码改 |
| C6 | 习题**和习题集一起解析**：题目/解答的抽取与配对发生在习题集自己的批次里，产物行落在**教材**的 `af_kp` 行集、父＝教材章行（`KA-15` 导出面与章节树因此零适配） | 新题目腿 |
| C7 | 落点确认：结构读数进**数据库** `af_kp`（题号、解答页、配对来源进 `summary`＋`meta` 闭集键），正文进 **`QED_DATA_ROOT`** 的书目录长文通路（`KA-20` 派生式，零新列） | `KP_META_KEYS` 扩展、`write_narrative` |
| C8 | 梳理只做**两类书**：`doc_kind` 两值 `textbook`／`exercise`，由同步行 `roles` 派生；`roles` 同时含教材与习题两义时**先按教材梳理**（习题内容先留在教材侧成行，等习题集批次按同一把键合并），不再设 `mixed` 第三条腿；两值都落不出（`roles` 缺位）即未声明，受理门当场拒 | `doc_kind` 派生纯函数、受理门种类闸 |
| C9 | 新增**习题梳理腿**（`stage='exercise'`）：教材侧先按「章 → 题号」把习题段逐题成行（点层、规则分题、零模型调用），它是习题集梳理的**前置**；习题集侧再按同一把键（章＋题号）把题面与解答合并进教材侧的题行 | `KpStage` 第四值、`exercise_item_entries` 规则腿、合并腿 |
| C10 | **重试面拆两处**：目录探索的重试＝同范围重发（无页子集重跑）；按失败节点重跑只对 `content` 成立，`only_failed` 因此移入大任务二 | `KA-52` 条文、`accept_kp_job` 组合门、`extract_points` 发起面 |
| C11 | 未声明种类的拒绝码位＝**422**（受理前的前置门，与「无生效版本」同形）；REQ-155 未到货期间照此启用——`roles` 到货前所有未声明书一律被受理门拒 | `accept_kp_job` 种类闸、`api.md` 受理门表 |
| C12 | **题包落点＝文件＋章级索引**（用户 2026-11-06 裁甲）：一个题目一份文件，内容＝题面＋解答＋来源三段固定标题，正文不进库；每章一份索引只存指针与状态、不复制正文；零新表零新列 | `<book_dir>/kp/<node_id>.md`（`KA-20` 算式不变）、`<book_dir>/kp/exercises/<章行 node_id>.json` |
| C13 | **教材门槛＝整本绿才放行**：习题集首次批次（`stage='exercise'`、无 `retry_node_ids`）受理前要求目标教材 `stage.done=true`，不绿即 422 零写入；修补批次不受此门，只校验选中行可回指且未确认 | `accept_kp_job` 习题门 |
| C14 | **习题集目录＝允许 `outline` 结构腿**：`doc_kind='exercise'` 的合法阶段由 `{exercise}` 扩为 `{outline, exercise}`；目录腿对习题集只产结构行、不产总结、不进详情，`content`／`full` 仍拒 | `KA-51` 组合闭集改判、`accept_kp_job` |
| C15 | **配对＝人工指定目标教材**：`KpJobCreate.target_book_id` 可选，有值＝配套模式（合并进该教材），缺省＝独立模式（本书自成一册）；不自动配对、不读配对表 | `schemas.py:KpJobCreate`、受理门 |
| C16 | **未匹配解答＝只披露不入格式**：解答读出却对不上题号（或章无法定位）不写题包、不落行，只进批次 `unmatched_solution_details` 与来源书 `meta.json`（供屏上提示）；不阻断习题集完成读数 | 执行体、`KP_RESULT_KEYS`、`meta.json` |
| C17 | **裁剪切片不落盘（Q2 甲，2026-11-07 裁）**：每节点「裁剪好的结果」＝工作区行的 `page_start`／`page_end` ＋ `summary`／`narrative_ref`，正文由根侧按该行 `job_id` 版本的 blocks 与页区间现取现渲染；不写 `<book_dir>/kp/<node_id>.excerpt.md`，不加清理腿、不加读面腿 | `KA-42` 的派生读面（`_kp_page_spans`／`KpNodeOut`）、W6 判据 |

## 待裁点（本案不代裁）

| 编号 | 归行 | 要裁的一件事 | 两案与代价 |
| --- | --- | --- | --- |
| Q2（已裁 2026-11-07） | 大任务二 | ~~裁剪后的原文切片是否落盘（`<book_dir>/kp/<node_id>.excerpt.md`）~~ **已裁＝甲（见 C17）**：不落盘，「裁剪好的结果」＝工作区每行的起止两页＋总结/导读，正文由根侧按版本产物现取现渲染 | 乙案（落盘、盘上有可指认产物）的代价＝删除通路多清一类文件、读面多一条腿，不为本案支付 |
| Q3（已取消 2026-11-24） | 重要任务一 | ~~本案 DDL 与 PLAN-027 真机梳理窗口的先后~~ **用户裁决＝取消**：事实已过期，W14 真库加列窗口已于 2026-11-24 执行完毕（见「过程与证据」），PLAN-027 真机窗口尚未发令，先后已由既成事实确定，无需再裁 | 原两案（先窗口读旧形状／先 DDL 重取指纹）均不再适用 |
| Q5（已裁 2026-11-07） | 大任务三 | ~~关键定义的结构化程度~~ **已裁＝甲**：`meta.must_master`（2~4 条「要能做到什么」）＋`meta.key_definitions`（2~6 条 `{term, definition}`）两枚闭集键，工作区与导出面同投影透传；章侧不加键（`chapter_summary`＝学习内容、`narrative`＝导读）；坏条目丢弃不丢行。乙案（只写提示词、库里不建结构）的代价＝屏侧拿不到稳定清单，与成功标准 5 的「清单」字样相悖 | 甲可被屏侧稳定渲染，代价是 `meta` 闭集扩两键、读面加两枚派生键 |
| Q6（已取消 2026-11-24） | 重要任务一 | ~~书目列表三段里的「梳理完成」取哪一枚读数（与并行壳 PLAN-029 撞同一句话）~~ **用户裁决＝取消**：徽章取数属 8903 消费面的展示口径，不阻塞本壳功能达成与本行关闭；底座两列与三写者已就位，两枚读数并存且各答一问（批次维／内容维），最终取数由消费面按需自取，本案不再代裁 | 原甲（批次结局，对人工确认盲）／乙（内容维严口径，列表需批量派生）／丙（并存）三案均不再在本案裁决 |

## 习题集梳理方案与边界（2026-11-06 用户四裁）

> **待评审设计**：本节承载 C12~C16 的落地形状，用户确认后随 W13 改写 `KA-51`／`KA-52`／`KA-53`；
> 未确认前不动 `knowledge-analysis.md` 的条文。本节另登记对 `KA-53` 的两处修正与对 `KA-52` 的一处改判。

### 一、两种模式（`target_book_id` 的有无即模式）

| 模式 | 触发 | 题行落点 | 题包落点 | 目录与前置 |
| --- | --- | --- | --- | --- |
| 配套 | `target_book_id` 有值且目标为 `doc_kind='textbook'` | 目标教材的题行（教材侧 W7 已成的行命中即补、未命中在对应章下新增） | 目标教材书目录 | 本书 `outline` 先跑；目标教材须整本绿 |
| 独立 | `target_book_id` 缺省 | 本书自己的题行（父＝题目所在的章／节行） | 本书自己的书目录 | 本书 `outline` 先跑 |

- 习题集批次**不产目录行**：`stage='outline'` 才是目录腿；`stage='exercise'` 只切题、成题行、写题包。
- 教材侧（W7）与独立模式同用一条规则分题腿，只是父行不同：教材侧父＝习题段行（`KA-46`），独立模式父＝题目所在的章／节行。
- 独立模式与配套模式可先后在同一本书上跑，行集互不覆盖：独立行挂本书，配套行挂目标教材。

### 二、题包与章级索引（落盘形状）

- **题包**＝该题行的长文（`KA-20` 同一算式）：`<book_dir>/kp/<node_id>.md`。**不改派生式**：不按章建目录，因为路径里的章键会在改父后留下孤儿文件；章的组织由索引给。
- **题包格式（固定三段，纯文本 markdown）**：`# <题号>` → `## 题目`（题面正文；取不到正文时写一行指针「见《书》p.X」）→ `## 解答`（模型读出的解答正文；未配到时写一行「未配到解答」）→ `## 来源`（教材名与题面页；解答书与解答页）。
- **章级索引**＝`<book_dir>/kp/exercises/<章行 node_id>.json`，一章一文件，批次收尾**整份重写**（派生视图，不追加）：`{"chapter": {"node_id", "name", "order_hint"}, "items": [{"node_id", "item_no", "parent_id", "page_start", "page_end", "status", "source_book_id", "source_pages"}], "updated_batch": "<gen_batch>"}`。
- `status` 闭集三值（键名不变，屏上文案固定中文）：`complete`＝「题解齐」／`problem_only`＝「仅题面」／
  `source_missing`＝「题面未取到」。中文文案是界面契约的一部分，唯一出处是根侧一枚常量表（8902 只出键名，
  不出口径词），前后端不得各写一套。
- **来源书清单**＝`<来源书 book_dir>/kp/exercises/meta.json`：模式、`target_book_id`、最近批次、未匹配解答清单。它只服务「这本书的提示」，不复制题包正文。
- **删除通路**：题包与索引都在 `kp/` 下，逐本删除天然覆盖；不新增清理腿。

### 三、受理门与执行序

- 首发（`stage='exercise'`、无 `retry_node_ids`）：① 种类 × 阶段闭集（改判后习题集为 `{outline, exercise}`；`target_book_id` 只许与习题集同批，教材带它即 422）；② 本书 `af_kp` 有在册目录行（没目录切不出章）；③ 配套模式另加：目标是 `doc_kind='textbook'` 的书 ∧ 目标 `stage.done=true`（整本绿）；④ 在飞唯一：本书与目标书任一枚有在飞批次即 409。任一不成立 422／409 且零写入。
- 修补（带 `retry_node_ids`）：只对 `stage='exercise'` 合法；逐枚校验「行在目标书 ∧ `meta.source_book_id`＝本书 ∧ `confirmed=0`」，任一不成立 422 零写入；不受整本绿门限制（修补不是重发首轮）。
- 批次号一律新发；无续跑、无增量状态（`KA-10` 不动）。

### 四、切分判据（题号闭集＋单调序）

- 闭集在既有 `N.`／`N、`／`(N)`／`N-M` 之外扩 `【N】` 一形（b07 的现网形状）；全半角折叠后判。
- **单调序**：同一父行下只有 `题号 > 上一枚已接受题号` 才开新题；编号回退（小问重新从 (1) 起）一律视为正文。跨章重置：按父行各自独立计序。
- 题号识别不出或序号不成立 ⇒ 该段零行，不回落模型分题；这是披露，不是失败。
- 题面／解答的**内容**由模型读出（习题集腿本来就是模型腿）；**边界**只由规则给，模型不得自行分题（每笔照旧进账，`KA-23`）。

### 五、未匹配与重解析

- **未匹配**（解答无题号可挂／章无法定位／形状不合）：不写题包、不落行，进 `result.unmatched_solutions`（计数）与 `unmatched_solution_details`（`item_no`／`source_pages`／`reason` 三项）；来源书 `meta.json` 同列，屏上据此提示「N 条解答未对上题目（未入库）」。
- **重解析**（用户勾选题目框或全选）：`KpJobCreate.retry_node_ids`（载荷第八键）只对 `stage='exercise'` 合法；执行体只对选中题号重新取证与配对，覆盖**未确认的机器行**与题包；`confirmed=1` 的行不覆盖（`KA-13`）。
- 命中但被确认位挡下的行不写：`merged_exercise_rows` 记命中数、`exercise_pack_rows` 记实际写出数，两者之差就是被挡下的行（屏上提示）。
- 习题集的「完成」不要求未匹配为零：它是披露，不是缺项。

### 六、读面与完成读数

- `KpJobCreate` 加 `target_book_id`；`KP_PAYLOAD_KEYS` 由五扩八（W8 的 `only_failed` 第六、`target_book_id` 第七、`retry_node_ids` 第八）。
- `KP_RESULT_KEYS` 由二十八扩三十三：`exercise_item_rows`（29）／`exercise_pack_rows`（30）／`merged_exercise_rows`（31）／`unmatched_solutions`（32）／`unmatched_solution_details`（33）。
- `KP_META_KEYS` 扩两键：`source_book_id`／`source_pages`（只进 meta 闭集，不建列）。
- `KpNodeOut` 加两枚读时派生键：`source_book_id`、`source_pages`（取自 meta；非习题行恒空串／空表）。
- `KpWorkspaceOut` 加 `exercise`：`{"kind": "companion"|"standalone", "target_book_id", "items", "packs", "pending_reparse", "unmatched": [], "done": false}`，全部读时派生（读索引与 `meta.json` ＋当前行），零写库。
- **完成读数**：`exercise.done` ＝ 题行 > 0 ∧ 题包数 == 题行数 ∧ `pending_reparse` == 0。习题书的完成只认这一枚；不复用 `stage.done`（其统计域只有章／点，在习题书上零行），也不得由根侧另算一份。
- 题行 `narrative_ref` 非空即「题包已写」；屏读题面／解答走既有 `GET …/narrative`，**不加新端点**。

### 七、边界（不改的东西）

- **零 DDL**：`af_*` 四表与 `af_kp` 列数不动；两道 meta 闭集扩键有名有判据。
- **不改 `KA-20` 派生式**：题包仍在 `kp/<node_id>.md`，章的组织靠索引。
- **题面不进 `summary`**（`KA-53` 边界不动）；题面进题包文件（用户 2026-11-06 令）。
- **解答页不进锚点**（对 `KA-53` 的修正）：锚点必须与行的取证版本同书同版（`KA-18`）；配套模式下解答页只进 `meta.source_pages`；独立模式下解答页与本行同书，可作第二枚锚点。
- **确认行不可被机器覆盖**（`KA-13`）；合并只补空位。
- **重试面由两条改三条**（对 `KA-52` 的改判）：新增「习题腿按选中题行重发」一路，仍一律发新批次号、不设续跑；W13 改判条文时同步。
- **共享主表零习题行**（根侧 ADR 0028 两级闸）不放宽；`KA-15` 导出面只多两枚 meta 键。
- **不自动配对**：不读 `qt_knowledge` 的教材↔习题集配对字段自作主张，目标教材由人指定。

### 八、并行边界（与内容解析轮同树并行）

内容解析轮（W6／W7／W8）由另一会话在同一工作树上推进。本行只碰下表左列，右列一行不改：

| 面 | 本行写入 | 本行不碰 |
| --- | --- | --- |
| 受理与调度 | `accept_kp_job` 的习题门、`run_knowledge_job` 的 exercise 分支、`KP_STAGES_BY_KIND`／`UNSHIPPED_KP_STAGES` 的放行 | W6 的四链路分流与裁剪渲染、W8 的 `only_failed` 与 `failed_section_details.node_id` |
| 分题规则 | 新建 `normalize/exercise.py`（题号闭集＋单调序纯函数） | `normalize/toc.py` 的 `exercise_entries`／`is_exercise_label`（`KA-46` 块判据） |
| 适配器 | `engines/base.py` 加一枚习题协议方法与 `engines/kp_gateway.py` 的实现 | 既有 `outline`／`points`／`chapter` 三腿的 prompt 与解析 |
| 读写面 | `schemas.py` 的 `KpJobCreate` 两键、`KpNodeOut` 两键、`KpWorkspaceOut.exercise`；`db/repositories.py` 的 meta 闭集扩键与题行查询 | 教材侧题行（W7）的产出逻辑与 `CONTENT_LEGS_BY_NODE_TYPE` |
| 文档 | `knowledge-analysis.md` 的 `KA-51`／`KA-52`／`KA-53`（W13 随步） | W6／W8 的条文 |

- 教材侧题行（W7）与本行的独立模式**共用同一套分题规则**：本行先交 `normalize/exercise.py` 的纯函数，W7 落地时
  直接调用；若 W7 已在本树另写一份，收口时以本文件为准合并，不并存两套题号口径。
- 两边都不得改对方已在树上的行；收口前以 `git diff --stat` 与逐文件对账，只提交本行清单。

## 核心任务与在册行的挂靠（本轮统一）

用户 2026-11-06 的口径＝**按功能划分模块、确认每部分的任务，拒绝过多任务导致的混乱**；分块整理后最后做一次
总流程回归＋前后端联调。本轮因此切成**四个大任务**（三块＋收口验收）与**两枚重要任务**（底座、习题集题目腿与
跨书挂章）共六行，按依赖排成一条可顺序执行的链；在册各行的**梳理侧新增面**一律挂到对应行下，不再另立行。
具体小任务与在册壳（PLAN-025／026／027／029 与 REQ-139／142／143／146／155）的**行位**在 todo
「普通任务轮」节，随挂靠的大任务收尾而关闭，本节只登记归属与裁决。

**合并的边界（先说清不能合的部分）**：三枚上游壳（PLAN-025／026／027）**不并**——一任务一承载，镜像守护要求
每枚计划文件恰好对应一行 `PLAN-*`，且每壳自带自己的关闭判据（PLAN-027 的 17 条真机判据不能被本案吸收）。
本轮合的是**归属与冲突裁决**：一件事只归一枚任务行，在册行与本轮行落到同一处代码时，以在册行的判据为基线，
本轮行只加不改；下表「冲突消解」列就是这条规矩的逐行写法。

| 任务行 | 这一行只交付什么 | 本轮行与步 | 挂靠的在册行（并入本行，不再另立行） | 冲突消解（谁不得改谁） |
| --- | --- | --- | --- | --- |
| 重要任务一：底座 | 种类可判、受理被判、三段流程各有判据位、结局位有写者 | ARCH-036（W1~W4、W14） | PLAN-025 W2 受理门（在册五道门之后加两道：未声明种类、组合非法；既有判码不动）；PLAN-026 W5 取材剔除（种类闸只作用在受理层，不下沉到取材腿）；PLAN-027 W4 窗口前体检（加列后列数与索引名指纹重取） | `last_curation_result` 只由批次收尾与启动期归一写，任何端点不得直写；`doc_kind` 只由同步腿派生写，人工端点与状态机不得写 |
| 大任务一：目录探索（只教材） | 教材的章级目录、组合闭集与目录重试语义 | ARCH-037（W5） | 入站 REQ-139 甲・A1（多阶段改判；A2 目录互验已裁撤）；PLAN-025 W3 两阶段执行体；PLAN-026 `stage=content`（`KA-43`）；PLAN-027 W6 只目录轮 | 目录重试只写语义（同范围重发）、不加页子集入参，**不新增续跑通路**——「批次没有续跑通路」这条在册判据由本行守护；习题集不发起目录三腿（C4），其内容归重要任务二与大任务二 |
| 大任务二：内容解析 | 按在册目录裁剪渲染，四类行各走各的链路；教材内部习题按章与题号成行；内容批次可按失败节点重跑 | ARCH-038（W6~W8） | PLAN-026 W5/W6/W7（取材、适配器、长文写腿）；入站 REQ-143 第九令三件（`KA-41`／`KA-42`／`KA-43`）；PLAN-027 W8 只补内容轮 | 「其他」零生成腿即终态，任何一行不得为它补腿；**教材侧**题行由本行按 `KA-53` 产出，独立模式的题行由重要任务二在本书产出（本行不重复造行）；`only_failed` 只开在 `content` 上，目录阶段不得借它重跑 |
| 重要任务二：习题集题目腿与跨书挂章 | 习题集批次里的题目／解答成对抽取与配对：配套模式按（章＋题号）合并进教材侧题行并写题包，独立模式在本书成题行与题包；另交章级索引与选中题重解析 | ARCH-041（W9、W10、W16、W17） | 入站 REQ-146 章末习题段成行（只管**教材内部**，与本行的**习题集整本**腿分工不重叠）；PLAN-025 W3/W4 生成腿与收尾（调度壳不动，只加一条腿）；PLAN-029 W1（整本绿判据纯函数，复用不自造） | 教材侧行集只由大任务二产出，本行只补空位；重解析只覆盖未确认机器行；题目行落**教材**行集，故 `KA-15` 导出面与章节树零适配；习题行进共享主表由根侧 ADR 0028 两级闸挡住，本行不放宽也不自造配对表 |
| 大任务三：知识点详情 | 每章「学什么」＋导读，每点「要掌握什么」＋关键定义 | ARCH-039（W11） | PLAN-026 W6 适配器（同一枚 `KnowledgeEngine`，本行只加 prompt 与读数形状）；入站 REQ-143「一键总结／详解」写腿；`KA-20`／`KA-30` 长文通路 | 详情只在已确认目录与已梳理章节上生成，人工确认门不因本行放宽；本行零新列，结构化读数只走 `meta` 闭集键 |
| 大任务四：全流程联调验收 | 三块串成一条链的总流程回归＋前后端联调面 | ARCH-040（W12、W13、W15） | PLAN-025 W8 文档晋升；PLAN-026 W8 文档随步；入站 REQ-139 乙／丙面形状核对的产出 N1（已交付＝`qed-gateway` 槽位在册）、N2（`kp` 路由在网生效与 `openapi.json` 对账）、N3（人工直登记知识点的创建端点）后两枚仍待判；PLAN-027 与 PLAN-029 的真机读数作验收参照 | 端点面与条文随步**唯一归本行**，其余五行不得各自改 `api.md`；PLAN-027 的真机读数与本案判据分列，本案关闭不以真机读数作为条件 |
| 并行壳（只划界，不挂靠） | 根侧「梳理屏选书三态」要的读时派生 `stage` 五键 | 归重要任务一与大任务四之间，本轮不另立行 | [PLAN-029](2026-11-06-kp-book-stage-derived-read-request.md)（入站 REQ-156，同节并行；2026-11-07 关闭，Retain）：W1 派生纯函数 → W2 `KpWorkspaceOut` 加 `stage` → W3 契约与单元 → W4 两侧回执 | PLAN-029 答「这本书梳理到哪一步」（内容维、读时派生、零 DDL），本案 W4 答「上一批跑到什么结局」（批次维、写列）——两枚各答一问，**谁也不得改成对方那枚**；徽章取数归 Q6。本案的加列窗口不做 `stage` 派生，PLAN-029 明文不消费 `last_curation_result`，故两案在 DDL 与读面上互不阻塞 |

## 工作项

| # | 任务行 | 工作项 | 落点 | 依赖 | 状态 |
| --- | --- | --- | --- | --- | --- |
| W1 | 重要任务一 | 前置立法：ADR 0019（梳理结局位入 `af_books` 当前态清单＋档 B 加列放行）＋ `database-design.md` 的 `af_books` 列清单与迁移序随步 | `docs/adr/`、`database-design.md` | — | 已完成（ADR 0019 已转 Accepted） |
| W2 | 重要任务一 | 种类列的入站：`BookSyncItem` 收 `roles`、`doc_kind` 派生纯函数（闭集、混合按教材、未声明处理按 C1/C2/C8）、`af_books` 加 `doc_kind` 与 `last_curation_result` 两列与迁移 | `schemas.py`、`db/models.py`、`db/repositories.py`、`migrations/` | W1、REQ-155 回执 | 已完成（迁移 `20261106_0001`，SQLite 升降级往返；`roles` 的实际到货仍等 REQ-155） |
| W3 | 重要任务一 | 受理门的种类闸：`accept_kp_job` 在册五道门之后加两道中的第一道——未声明即拒并回显告警（码位按 Q4 出裁）、习题集不发起目录三腿、教材不受影响；`GET /books` 与 `BookOut` 透出种类与梳理结局两列 | `orchestrator/knowledge.py`、`api/main.py`、`schemas.py` | W2 | 已完成（Q4 裁 422；`TestKindGate` 与 `TestBooksEndpoints` 两列读面、HTTP 未声明拒各有用例） |
| W4 | 重要任务一 | 梳理结局位的写者：执行体收尾、异常腿与启动期归一三个写者把结局写进 `af_books`（`KA-54`），书目读面的「梳理中」以一次分组查询派生（`KA-55`）、不做 N+1；「梳理完成」徽章取哪一枚读数仍待 Q6 | `run_knowledge_job`／`jobs.py:_run_knowledge`、`reconcile_draft_on_startup`、`BookRepository`／`KpRepository`、`api/main.py` | W2（Q6 未裁，不阻塞本行） | 已完成（`curation_result_for`／`mark_curation_result`／`curating_book_ids`；`TestCurationResult` 覆盖干净／失败节／缺页／未解析／异常／中断不回写失败六形，契约用例覆盖书目两读面） |
| W5 | 大任务一 | 组合闭集与目录重试语义：`stage` 与种类的合法组合成文并当场拒非法组合（第二道门；第四值 `exercise` 在此登记域值）；目录阶段的「重试」＝同范围重发（`KA-52`），不加页子集入参、不加续跑通路 | `schemas.py`、`accept_kp_job`（`api.md` ④ 类由 W12 随步） | W3 | 已完成（四值闭集、种类 × 阶段两道门、`exercise` 腿未交付期间受理面 422、请求体无页子集键；`api.md` 的阶段取值与受理门表已同步）。真机读数 2026-11-06（`qed_test`，见「过程与证据」）：整本 317 页 `stage=outline` 批次 `afkp-b79732ef1000` 流水 `ok`、116 行目录、零失败节、书目读面 `completed`／在飞 `false`；习题集 `stage=outline` 与未声明种类两枚 422 零写入 |
| W6 | 大任务二 | 四链路分流与裁剪渲染：章节／知识点走既有腿，「其他」明文零腿，习题不并进本行三条生成腿（教材侧题行由 W7 产出）；按在册目录裁出每节点正文切片，Q2 甲 ⇒ 切片不落盘，读面以 `page_start`／`page_end`＋`summary`／`narrative_ref` 派生回读 | `orchestrator/knowledge.py`、`api/main.py`（派生读面核验） | W2 | 已完成（`CONTENT_LEGS_BY_NODE_TYPE`／`content_legs_for` 一条分派表：章节→章腿、知识点→点腿、习题与「其他」→∅；点腿取材与读面共用 `section_spans_of` 树序区间，Q2 甲 ⇒ 零切片落盘；`TestSectionSpansOf`、`TestContentOnlyStage`（含 `test_the_four_node_types_have_one_dispatch_table`、`test_other_and_exercise_rows_get_no_generated_legs`）全绿，「其他」零腿做过变异复验） |
| W7 | 大任务二 | 教材内部习题梳理（`stage='exercise'` 的教材侧，`KA-53`）：按章扫描既有习题段行，按行首题号闭集（含 `【N】` 形、同父单调序，见「习题集梳理方案」四）逐题成点层行（`name`＝题号、`summary` 空串、`meta.toc_kind='exercise'`、`model_ref='rule:exercise_item'`），父＝该段习题行；识别不出即零行、零模型调用、零文件；行数进流水第二十九键 | `normalize/toc.py`、`orchestrator/knowledge.py`、`db/repositories.py` | W5、W6 | 已完成（`normalize.toc.exercise_item_entries` 闭集 `【N】`／`(N)`／`1-3`／`1.`／`1、`＋同父单调序；`extract_exercise_items` 零模型零文件成点层行（父＝习题段行、`model_ref='rule:exercise_item'`、`meta.toc_kind='exercise'`、`summary` 空串），习题阶段的作废面只碰题行不碰模型点；`TestExerciseItemEntries`＋`TestExerciseStage` 全绿，单调序做过变异复验。与重要任务二在飞的 `normalize/exercise.py` 暂时并存，收口时按「习题集梳理方案」合并为止一套题号口径——本行不擅自改对方在飞文件） |
| W8 | 大任务二 | 按失败节点重跑（`KA-52`）：`failed_section_details` 每项加 `node_id`；`KpJobCreate.only_failed` 只与 `content` 合法（其余组合 422 且零写入）；执行体只对上一批点名的节点发起该节调用、取证集仍按原页区间读全；`payload` 加第六键 | `schemas.py`、`orchestrator/knowledge.py`、`db/repositories.py` | W6 | 已完成（`failed_section_details` 每项带 `node_id`；`KpJobCreate.only_failed` 仅与 `content` 组合、其余三值 422 零写入；受理期把上一批点名解析进 `KpAcceptance.retry_node_ids`，执行体只对名单发问、取证仍按原页区间读全；收尾作废面按 `parent_ids` 收窄，名单外的旧点行不判死；`payload` 第六键 `only_failed`；`TestFailedNodeRetry` 全绿，组合门做过变异复验） |
| W9 | 重要任务二 | 习题集题目腿（配套／独立两用）：适配器加题目／解答配对方法与 prompt（`KnowledgeEngine` 协议扩一条）；题号边界由规则给（闭集＋单调序），题面／解答内容由模型读出并按形状闭集校验；独立模式成题行（父＝题目所在章／节行）并按题包三段格式写 `<book_dir>/kp/<node_id>.md`、设 `narrative_ref`；读不出或形状不合不写并进失败披露；`exercise_item_rows`／`exercise_pack_rows` 落账 | `engines/base.py`、`engines/kp_gateway.py`、`engines/factory.py`、`orchestrator/knowledge.py` | W3、W5、W7、W8 | 已完成（2026-11-07；`KnowledgeEngine.exercise` 与网关一调用一行；题号边界走规则 `normalize.exercise.split_exercise_items`（闭集＋同父单调序），题面／解答由模型读；独立模式 `extract_exercise_book_items` 逐题成行、按三段格式写 `<book_dir>/kp/<node_id>.md` 并设 `narrative_ref`，零题段零调用；`exercise_item_rows`／`exercise_pack_rows` 落账。`TestSplitExerciseItems`／`TestExerciseBookStage`／`TestExerciseRows` 全绿，Axiom-Flow 全量 1389 passed／18 skipped） |
| W10 | 重要任务二 | 跨书挂章（配套模式）：`KpJobCreate.target_book_id` 由人工指定（不读配对表、不自动配）；受理门加「目标教材 `stage.done=true`」（复用 PLAN-029 W1 的判据纯函数，不自造第二份）与「本书与目标书均无在飞批次」；按教材在册章行与 W7 的题行以（章＋题号）匹配——命中只补空位、未命中在对应章下新增；题包写目标教材书目录、题面正文取教材锚点页文本（取不到写指针）、来源书与来源页记 `meta` 两键（解答页不进锚点，见方案七）；`merged_exercise_rows`／`unmatched_*` 落账 | `schemas.py`、`orchestrator/knowledge.py`、`db/repositories.py` | W9、PLAN-029 W1 | 已完成（2026-11-07；`KpJobCreate.target_book_id` 只与习题集＋`exercise` 同批，目标书须教材身份且整本绿（复用 PLAN-029 W1 纯函数），两书各自在飞门；`merge_exercise_book_items` 按教材在册章行＋题号匹配，命中补空位、未命中在对应章下新增，题包写目标教材书目录，来源书与来源页进 `meta` 两键；`merged_exercise_rows`／`unmatched_*` 落账。`TestExerciseBookAcceptance`／`TestExerciseBookStage` 全绿；2026-11-16 起该跨书合并腿由配套提取取代——`merge_exercise_book_items` 已删除，改由 `extract_companion_exercise_items` 把题面取目标教材本章习题段、解答取本书同章、行与题包落本书自己的章下（见 PLAN-036 W13~W15 与 `KA-53`）） |
| W11 | 大任务三 | 知识点详情形状：每点给「要掌握什么」与关键定义清单（Q5 出裁）、每章给学习内容与导读；prompt 侧按「先说要什么」的口径改写并冻结样本/哈希/预算/采纳门槛 | `engines/kp_gateway.py`、`db/repositories.py`、`schemas.py` | W6、W8、Q5（已裁甲） | 已完成（2026-11-07；Q5 取甲——点腿回复加 `must_master`（2~4 条）与 `key_definitions`（2~6 条 `{term, definition}`），`_point_row` 净化后落 `meta` 两枚闭集键（坏条目丢弃、点行照留；上限 6／8 条、120／120／500 字）；`KpNodeOut` 加两枚派生键，工作区／单行／导出同一投影透传、坏值归空表；章侧不加键，`chapter_summary` 明确为「本章学习内容」。提示词按「先说要什么」改写并冻结哈希／样本／预算／采纳门槛（见证据节）。`TestPointsDetailPrompt`／`TestPointDetailReads`／`TestKpDetailReads` 九例全绿，全量 `1406 passed／18 skipped`＋`ruff` 绿） |
| W12 | 大任务四 | 端点面与判定序随步：④ 类新增/改动端点入 `api.md` 总表与小节（含 `GET …/kp/export` 透传新 `meta` 键），与运行时路由表双向对账 | `api/main.py`、`api.md`、`tests/contract/test_api_v1_contract.py` | W3~W11 | 已完成（2026-11-07；`api.md` ④ 类总表与小节按运行时实序重写：阶段段改四值闭集并写明 `exercise` 两模式已交付；受理门表由「七道单门」改「单门＋组合门」排列——`only_failed` 只与 `content`、`target_book_id` 只与「习题集＋`exercise`」且目标书须在册／教材／不在飞／整本绿、`retry_node_ids` 只与 `exercise`，目标书在飞 409 单列；工作区 JSON 补 `"exercise": null` 与节点 `source_book_id`／`source_pages`，导出节写明与工作区同一投影（`_kp_node_out`）只加过滤与排序；422／409 错误码行同步扩写。契约网新增 `test_the_export_face_carries_the_same_projection` 与 `TestFullChainRegression` 三例；④ 类端点表 11 行与运行时路由逐行对账，公共端点 30 条；文档三套契约 52 枚绿） |
| W13 | 大任务四 | 条文晋升与文档随步：`KA-51~KA-53` 新条（种类闸与组合闭集、重试两条路、习题梳理腿）＋ W1~W12 的实现落点写入 `knowledge-analysis.md`，`code-map.md` 五元组与 `overview.md` 能力矩阵同步 | `docs/design/`、`docs/architecture/` | W2~W12 | 已完成（2026-11-07；`knowledge-analysis.md` 加验收判据 24（详情读数一处成形、工作区／单行回显／导出三面共用；坏条目丢弃、不参与去重与门）与 25（端点与判定序：七道单门后接组合门，④ 表 ↔ 运行时路由双向对账、每端点有同名小节），最后更新改 2026-11-07；`overview.md` 知识能力行改「种类分流、四值阶段、批次三态、确认与只读导出」，补受理组合门、两条习题腿、详情两枚读数与十一条端点，设计引用扩到 `KA-1`~`KA-57`；`code-map.md` 条文范围改 `KA-1~KA-57`、`api/main.py` 行改「④ 十一条」；`KA-51`／`KA-52`／`KA-53` 条文已在 W3～W11 随步落正文） |
| W14 | 重要任务一 | 真库加列窗口（D 类）：按 `operations.md` runbook 取备份与逐表 DDL 双份、留 sha256，迁移升→降→升三次往返，前后列数与索引名逐名指纹；发令前单独批准 | `migrations/`、本壳「过程与证据」 | W1~W2 | 已完成（2026-11-24 用户单独发令；业务库 `qed` 停服后取备份 sha256 `653dc40c…`、逐表 DDL 双份留档；迁移 `20261022_0001`→`20261106_0001` 升→降→升，实测列数 22→24→22→24、`alembic_version` 终值 `20261106_0001`、索引名逐名不变 `PRIMARY`／`ix_af_books_course`／`ix_af_books_status`、两列就位、`af_books` 0 行保持；读数见「过程与证据」） |
| W15 | 大任务四 | 总流程回归与前后端联调面：一条链的回归判据（解析完成→开始梳理→梳理完成三段一次读全、教材一轮从目录到详情可指认、习题集一轮在教材章下可回指来源书与来源页）本地取数；给根侧 8900／8903 一份响应字段与错误码清单，缺口按需另立一枚出站请求行 | `tests/`、本壳「过程与证据」、todo 出站行 | W3~W13 | 已完成（2026-11-07；本地一条链回归＝`TestFullChainRegression` 三例：① 三段状态由同一 `GET /books/{id}` 一次读全（`last_parse_result`／`last_curation_result`／`curation_in_flight` 与批次流水）；② 教材一轮从目录行到点行到详情（父指针在目录集内、总结非空、页区间有值、详情两键可读、`exercise` 为空）；③ 习题集跨书行回读（`type=exercise`、`source_book_id`／`source_pages`、父行可指认）。8900／8903 响应字段与错误码清单见「2026-11-07 大任务四」节；清点结论＝现有端点字段已覆盖，`roles` 到货由 REQ-155 承载、人工建点（N3）未裁，故不新增出站行） |
| W16 | 重要任务二 | 题包索引与习题读面：批次收尾整份重写章级索引 `<book_dir>/kp/exercises/<章行 node_id>.json` 与来源书 `kp/exercises/meta.json`；`KpWorkspaceOut.exercise` 读时派生（kind／target_book_id／items／packs／pending_reparse／unmatched／done），`KpNodeOut` 加 `source_book_id`／`source_pages` 两枚读键；习题书完成读数只此一处（不复用 `stage.done`）；零写库零 DDL | `orchestrator/knowledge.py`、`db/repositories.py`、`schemas.py`、`api/main.py` | W9、W10 | 已完成（2026-11-07；收尾整份重写章级索引 `<book_dir>/kp/exercises/<章行 node_id>.json` 与来源书 `kp/exercises/meta.json`；`KpWorkspaceOut.exercise` 读时派生 kind／target_book_id／items／packs／pending_reparse／unmatched／done，`done` 只认 `items>0 ∧ packs==items ∧ pending_reparse==0`；`KpNodeOut` 带 `source_book_id`／`source_pages`；零写库零 DDL。`TestExerciseIndexAndReadFace`／`TestKpExerciseFace` 全绿） |
| W17 | 重要任务二 | 选中题行重解析：`KpJobCreate.retry_node_ids`（载荷第八键）只与 `stage='exercise'` 合法（其余组合 422 零写入）；受理面逐枚校验「行在目标书 ∧ `meta.source_book_id`＝本书 ∧ `confirmed=0`」；执行体只对选中题号重新取证与配对、覆盖未确认机器行与题包，确认行 422；不受整本绿门限制；批次号新发 | `schemas.py`、`orchestrator/knowledge.py`、`db/repositories.py` | W16 | 已完成（2026-11-07；`KpJobCreate.retry_node_ids` 载荷第八键、只与 `stage='exercise'` 合法；受理面逐枚校验「行在归属书 ∧ 来源书＝本书 ∧ `confirmed=0`」，一次至多 200 枚，缺失目标时按本书最近一批习题流水推导归属；`retry_exercise_book_items` 只重取选中题号并覆盖未确认机器行与题包，不触发首轮作废面；批次号新发。`TestExerciseBookAcceptance` 与契约受理门用例全绿） |
| W18 | 重要任务二（提速） | 题腿改为「模型只确认切分点、程序拼接」（`KA-56`）：输入把每题正文逐行编号，回复只给 `solution_start`；适配层按行号切原文拼成 `problem`／`solution`，越界或非整数＝未确认（两段空串，走待重解析），`ExerciseDraft` 形状与题包格式不变；输出量由 O(本节正文) 降为 O(本题数) | `engines/kp_gateway.py` | W9、W10 | 已完成（2026-11-07；`_EXERCISE_PROMPT` 改为「题目与解答切分器」，输入按「行号＋竖线＋原文」逐行编号，回复只给 `{"item_no","solution_start"}`（`0`＝无解答、`1`＝整题为解答、`-1`＝无法判定）；`_slice_exercise_text` 按行号切该题原文拼成 `problem`／`solution`，缺键／非整数／越界一律两段空串；`exercise()` 按折叠后的题号取回原文，旧 `problem`／`solution` 键不再采信；`ExerciseDraft` 形状与题包格式零改动。`TestExerciseSplitPointReply` 五例全绿，全量 `1397 passed／18 skipped`＋`ruff` 绿） |

## 过程与证据

### 2026-11-06 大任务一真机 dry-run（`qed_test`，业务库 `qed` 零写入）

- **环境与前置**＝`qed_test` 的 `af_*` 四表。先备份（sha256 `f4891a8d3653caadb6d1583853974f9a92b7b2099cf44f3b401cfb93242046b1`，`af_books` 17／`af_operations` 22／`af_pages` 3512／`af_kp` 536 行），再迁移 `20261022_0001`→`20261106_0001` 升→降→升：列数 22→24→22→24、索引名不变、`alembic_version` 终值 `20261106_0001`；停服期间起 8902（当前代码）。REQ-155 未到货，`roles` 由本次同步显式补上。
- **种类入站与两道负例**＝同步 `mathanalysis-b05 roles=[textbook]`、`mathanalysis-b07 roles=[exercises]` 后读面 `doc_kind=textbook`／`exercise`、`curation_in_flight=false`。b07 临时同步 `roles=[]` 后提交 `stage=outline` → 422「未声明书种类」；恢复 `roles=[exercises]` 后同请求 → 422「阶段与书种类组合非法（合法值：exercise）」。两枚前后 `af_kp` 536 行／`op_kind='kp'` 流水 7 行逐字不变。
- **整本目录轮**＝`POST /kp-jobs {"book_id":"mathanalysis-b05","stage":"outline"}` → 202，批次 `afkp-b79732ef1000`（取证版本 `4af1b233c252`，页 1~317）。856.4 s 跑完，四次模型调用全部 `success`（`toc_pages`×1、`toc_entries`×2、`toc_verify`×1）；流水 `status=ok`：`outline_rows=116`、`reference_rows=19`、`superseded_rows=118`、`failed_sections=0`、`failed_chapters=0`、`missing_page_nos=[]`、`unparsed_page_nos=[]`、`outline_fallback_pages=[8]`、`unlocated_outline_entries=["习题"]`。
- **读面收口**＝批次结束即 `in_flight=false`；书目读面 `doc_kind=textbook`、`last_curation_result=completed`、`curation_in_flight=false`。b05 `af_kp` 终态 116 candidate＋352 rejected（上一批 118 枚 candidate 由本批判为 superseded，行不删除），全库 `op_kind='kp'` 流水由 7 行增至 8 行。
- **判读**＝大任务一三条判据逐条成立：① 目录重试＝同范围重发，请求体无页子集键、本批为新批次号；② 非法种类×阶段 422 且零写入；③ 教材批次结构不变（116 行目录，`unlocated_outline_entries` 由上一批 5 条降为 1 条「习题」）。真库加列窗口（W14）与业务库同步仍待单独发令，不在本证据内。

- 2026-11-06｜**习题集四裁**（用户 AskUserQuestion 逐项取推荐案）：题包文件＋章级索引／整本绿才放行／允许 `outline` 结构腿／人工指定目标教材。立项取证：`UNSHIPPED_KP_STAGES={"exercise"}`（`knowledge.py:133`）、`KpJobCreate` 无 `target_book_id`、`KP_RESULT_KEYS` 28 键、`KP_META_KEYS` 两键、`_narrative_path` 按 `kp/<node_id>.md` 现算；b07 现网 `doc_kind=exercise`／439 页／92 行章→节结构、`【N】` 题号 ⇒ 题号闭集须扩该形；b05 在册 116 行、确认位全 0 ⇒ 配套首发在 b05 转绿前被整本绿门挡住，独立模式不受此门。b05 11 章与 b07 8 章不是配套 ⇒ 配套模式无现成真机样本，真机验收走独立模式＋构造数据。对 `KA-53` 的两处修正在此登记：解答页不进锚点（`KA-18` 同书同版）、题面进题包但不进 `summary`。

### 2026-11-07 大任务二 W6~W8 本地实现与复验（零真机、零 MySQL 层）

- **范围**＝只做 W6~W8（教材侧内容链路、教材内部习题行、按失败节点重跑）；习题集题腿（W9/W10）、
  Q3／Q5／Q6 与真库窗口仍待裁，不在本证据内。
- **W6 分流**＝`CONTENT_LEGS_BY_NODE_TYPE`／`content_legs_for` 一张表决定生成腿：章节→章腿、知识点→点腿、
  习题与「其他」→∅；点腿取材与读面共用 `section_spans_of` 树序区间（含习题段按树序取真实区间、reference
  退化为单页），Q2 甲 ⇒ 切片不落盘。
- **W7 习题行**＝`exercise_item_entries` 闭集（`【N】`／`(N)`／`1-3`／`1.`／`1、`，点号后带 `(?!\d)`）＋
  同父单调序；逐题成点层行，父＝该习题段行，`name`＝印刷题号、`summary` 空串、`meta.toc_kind='exercise'`、
  `model_ref='rule:exercise_item'`；识别不出即零行，全程零模型调用、零文件；`exercise_item_rows` 进流水
  第二十九键。
- **W8 重跑**＝`failed_section_details` 每项加 `node_id`；`only_failed` 只与 `content` 合法（`full`／
  `outline`／`exercise` 三值均 422 零写入）；受理期解析上一批 `stage='content'` 流水里的失败详单进
  `KpAcceptance.retry_node_ids`，执行体只对名单内节点发问、取证集仍按原页区间读全；收尾的作废面在
  `model_ref`／`node_kind` 之外再按 `parent_ids` 收窄——不这样做，重跑一节会把其他节的旧候选点行一并判死；
  `payload` 加第六键 `only_failed`（缺省 `false`，旧请求读数不变）。
- **变异复验**＝三处守卫各摘一次、用例转红后原样恢复：① 撤「其他」零腿（分派表补点腿）→
  `test_other_and_exercise_rows_get_no_generated_legs` 红；② 撤题号同父单调序（守卫恒放行）→
  `TestExerciseItemEntries` 的单调序用例红；③ 撤 `only_failed` 的「只与 content 组合」门 →
  `test_only_failed_only_combines_with_content` 红。恢复后定向 17 枚全绿。
- **门禁现状（当时）**＝受影响单元套 575 绿／1 红：`TestModelLedOutline::test_the_fallback_page_rows_land_in_the_batch_ledger`
  的 `len(KP_RESULT_KEYS) == 29` 断言被并行会话在飞扩键打破（33 键）；`ruff` 触及文件绿；文档三套契约 21 枚绿。
  全量 `pytest tests -q` 当时被 `engines/kp_gateway.py` 的 `IndentationError`（第 708 行）挡住。
- **两处阻断已随 W9／W10 同批清掉（2026-11-07 复验）**＝键数断言改 33、网关语法拆回两行；全量
  `pytest tests -q` → **1392 passed, 18 skipped**，`ruff check src tests` → **All checks passed**。

### 2026-11-07 重要任务二 W9／W10／W16／W17 收口与全量门禁

- **启动阻断已清**＝`engines/kp_gateway.py:708` 的 `IndentationError`（`def _post_text` 签名与 docstring 挤在同一行）
  已按 HEAD 形状拆回两行，语义零变化；`py_compile` SYNTAX OK，`ruff check src tests` All checks passed。
  8902 启动失败（`logs/qed-axiom-serve.log` 的 traceback）根因即此。
- **断言同步**＝并行会话扩键后的三处契约断言按新口径改齐：①习题集 `full` 仍 422「组合」，`outline` 改判
  202（结构腿放行）；②`exercise` 无本册在册目录 → 422「先解析目录」，有目录 → 202 且流水 `ok`；
  ③`payload` 由六键扩八键（`only_failed`／`target_book_id`／`retry_node_ids`），缺省 `(None, [])`。
- **时钟抖动收口**＝Windows `datetime.now()` 分辨率约 16ms，两条既有用例（`set_narrative_ref` 时刻列、
  逐页落行的 `list_batch` 库序）在同刻内偶发判红；前者先把旧 `updated_at` 压到 2000 年再写，后者改判
  「两行都可读」不判库序——判据强度不变、与时钟无关。
- **全量门禁**＝`pytest tests -q` → **1389 passed, 18 skipped**（57.49s）；`ruff check src tests` →
  **All checks passed**。

### 2026-11-07 大任务二与重要任务二收尾结论

- **大任务二（ARCH-038／W6~W8）**＝四条判据逐条有读数：① 四类行各有链路，习题与「其他」零生成腿
  （`TestContentOnlyStage`、`TestSectionSpansOf`）；② 教材习题按章与题号成点层行、父＝习题段行、零模型零文件
  （`TestExerciseStage`、`TestExerciseItemEntries`）；③ 按失败节点重跑只问名单、取证读全区间、作废面按
  `parent_ids` 收窄（`TestFailedNodeRetry`）；④ 切片不落盘、读面按行自身 `page_start`／`page_end` 派生。
  终态＝Completed／Achieved。
- **重要任务二（ARCH-041／W9、W10、W16、W17）**＝题面／解答成对读出并按闭集校验；独立模式成题行与题包、
  配套模式按（章＋题号）合并且只补空位，来源书与来源页进 `meta`、共享主表零习题行；章级索引与工作区
  `exercise` 读面；选中题行重解析有界（只覆盖未确认机器行、至多 200 枚）；每笔调用一行账。终态＝
  Completed／Achieved。
- **闸门**＝`pytest tests -q` 1392 passed／18 skipped、`ruff check src tests` 全绿。PLAN-028 壳仍 `In Progress`：
  剩余＝W11~W13、W15 与 W14（真库窗口，D 类待发令）。
- **已知下一轮优化项（不阻塞本条终态）**＝习题集的题面／解答正文原先由模型直接输出，输出量大、耗时长；
  已在本轮 W18 改为「模型只确认切分点，程序按原文切片拼接」（`KA-56`，读数见下条），本条终态不依赖它。

### 2026-11-07 W18 题腿改切分点（`KA-56`，零真机）

- **改前**＝`_EXERCISE_PROMPT` 要模型把每题的 `problem`／`solution` 正文整段抄回来，输出量随本节正文
  线性增长；一节几十题时回复可达数千字，延迟与花费都花在让模型重打一遍原文上，且抄错即污染题包。
- **改后**＝输入把每题正文按「行号＋竖线」逐行编号；回复每题只给 `{"item_no", "solution_start"}`
  （`0`＝无解答、`1`＝整题即解答、`-1`＝无法判定）。`exercise()` 先把折叠后的题号映射回输入原文，
  再由 `_slice_exercise_text` 按行号切出 `problem`／`solution` 两段——正文与输入逐字相等，模型输出
  量降为 O(本题数)。缺键／非整数／越界一律两段空串，沿用既有「题面未取到」披露与待重解析。
- **不动面**＝`ExerciseDraft` 形状、编排层分组、配套合并腿与题包三段格式零改动；题号闭集与单调序
  仍由规则给（`KA-53`）；`KA-23` 调用账与 `KA-31` 形状闸不变。
- **判据**＝`TestExerciseSplitPointReply` 五例（提示词逐行编号且不再出现 `problem` 键、切出的两段与
  输入逐字相等、`【1】` 折叠同键、`1` 整题解答、`-1`／越界／非整数三形取空、旧抄正文键不再采信）全绿。
- **门禁**＝`pytest tests -q` → **1397 passed, 18 skipped**；`ruff check src tests` → **All checks passed**。
- **遗留披露**＝切分点协议下模型不再给出正文，`_group_exercise_drafts` 的「题号不在本节切分内」与
  「题号认不出」两类不再产生 `unmatched_solution_details` 条目（按题号取不回原文、两段为空，而该披露
  只记有正文的草稿）；「题号重复」仍会记一条。未读出的题按既有「题面未取到」与待重解析披露，键本身
  保留（配套模式合并未命中仍走它）。

### 2026-11-07 大任务三 W11 知识点详情（`KA-57`，本地、零真机、零 MySQL 层）

- **现状与缺口**＝点腿原先只出 `name`／`type`／`role`／`summary`／`anchors`／`links`，点行 `meta` 恒空，
  读面只有 `toc_role`／`toc_kind`／`source_*` 四枚逐键取值——「要掌握什么」与「关键定义」既没有生成要求，
  也没有稳定读键。章侧 `chapter_summary`（`KA-45`）与 `narrative`（`KA-30`／`KA-47`）已在，
  缺口只是措辞没把它们写作「本章学习内容」与「本章导读」。习题集不跑 `content`／`full`（`KA-51` 组合闭集），
  故本条只影响教材。
- **形状（Q5 甲）**＝点腿每个点加 `must_master`（2~4 条动作句）与 `key_definitions`（2~6 条 `{term, definition}`）；
  `_point_row` 净化后写 `meta` 两枚闭集键（`KP_META_KEYS` 登记），坏条目丢弃、点行照留；上限＝6／8 条、
  120／120／500 字（提示词要得更紧：60／30／120，程序上限只挡离谱值）。`KpNodeOut` 加 `must_master`／
  `key_definitions` 两枚派生键，工作区、单行回显与导出面同一份投影；槽里的坏值读面归空表。章侧不加键。
- **提示词与冻结件**＝
  - `_POINTS_PROMPT`（1707 字符）sha256 `4c8f3e3c827b58be6c37b491017f193e38e8a6e38b6cb7332cbdbae00232d819`；
    `_CHAPTER_PROMPT`（891 字符）sha256 `cfdedd1cefbbe865e15e3d176994b0a903d356f658c9046f97952903a20ccc68`。
  - 冻结样本＝合成两页一节（标题「2.1 数列的极限」，页 7~8，正文「设 {x_n} 为数列，正文甲。」／「正文乙。」），
    整段点腿 prompt 的 sha256 `1a5297339e5034a408c7cd8ffadd28ff110b8f50868429c59e3deaf08ad8248e`；
    正文不入库，重建方式＝同一 `SectionInput` 调 `_points_prompt` 后逐字比对哈希。
  - 预算＝单节输入 ≤ 24 页（沿用点腿既有取证上限，不改）；输出上界＝每点 ≤ 1200 字符（`summary`＋两枚读数）、
    每节 ≤ 20 点；越界字段由程序截断或丢弃。
  - 采纳门槛（真机标定时判，本轮只冻结不上报读数）＝结构合法率 ≥ 90%；含 ≥2 条 `must_master` 的点 ≥ 90%；
    `key_definitions` 非空且每条 `term` 能在本节正文按字面找到的 ≥ 80%；未达门槛回改提示词并重取哈希。
- **不动的面**＝零新列、零 DDL、零新端点；人工确认门与目录确认要求一字未动；`summary` 单点重生成只覆盖
  `summary`（`KA-45`），两枚详情键按批次产出原样保留；点行的成立判据仍是四件（`KA-32`）。
- **门禁**＝`pytest tests -q` → **1406 passed, 18 skipped**；`ruff check src tests scripts` → **All checks passed**；
  文档／tracker 契约随全量一并通过。

### 2026-11-07 大任务四 W12／W13／W15 收口（本地、零真机、零 MySQL 层）

- **W12 端点面**＝判定序按实码落表：七道单门（书 → 生效版本 → 页区间 → 已登记目录 → 同书在飞 → 未声明种类 →
  种类×阶段）之后才是组合门（`only_failed` 只与 `content`、`target_book_id` 只与「习题集＋`exercise`」、
  `retry_node_ids` 只与 `exercise`），目标书在飞单列 409；旧表中「`exercise` 尚未交付、受理面暂判 422」一行删除。
  导出面写明与工作区同一投影，只多「已确认」过滤与父先于子排序。
- **W13 条文与映射**＝`knowledge-analysis.md` 验收判据补 24／25；`overview.md` 能力行与设计引用同步；
  `code-map.md` 两处口径（条文范围、④ 端点数）校正。
- **W15 全链回归**＝`TestFullChainRegression` 三例覆盖一条链的三个可指认点（见 W15 行）。
- **8900／8903 响应字段与错误码清单**＝
  - 字段：书目读面 `doc_kind`／`last_curation_result`／`curation_in_flight`；受理回执 `gen_batch`／`book_id`／`stage`；
    批次进度 `status`／`failed_section_details`（带 `node_id`）／`unmatched_solution_details` 与流水各键；
    工作区 `outline`／`points`／`unlocated`／`exercise`（`kind`／`target_book_id`／`items`／`packs`／`pending_reparse`／`unmatched`／`done`）；
    节点行 `must_master`／`key_definitions`／`source_book_id`／`source_pages`；导出面同工作区投影。
  - 错误码：404（书／批次／节点）；409（同书在飞、目标书在飞、`if_rev` 冲突）；422（未声明种类、种类×阶段、
    页区间、未登记目录、三类组合门、目标书身份或整本绿、载荷形状）；503（文本槽位未接线或闭集外槽位名）。
  - 结论＝两侧所需字段与判码都在已交付端点内，不新增出站行；`roles` 入站归 REQ-155，人工建点（N3）仍待裁，
    若裁「要」再另立一枚出站请求行。
- **门禁**＝定向契约 6 枚、文档契约 52 枚绿；全量 `pytest tests -q` → **1410 passed, 18 skipped**（84.62s，较 W11 基线 +4＝导出同投影 1＋全链回归 3）、`ruff check src tests scripts` → **All checks passed**。真库加列窗口（W14）与业务库同步不在本证据内。

### 2026-11-24 W14 真库加列窗口（D 类，用户单独发令后执行）

- **发令原文**＝用户「W14确认开始实现」（2026-11-24），依「不隐式执行」条款单独批准；本段即批准原文随读数留档。
- **窗口前状态（只读探针实读）**＝业务库 `qed`（`QED_DB_HOST=127.0.0.1:3306`、`QED_DB_USER=root`，`trust_env=False` 直连），
  `alembic_version=20261022_0001`、`af_books` 22 列、无 `doc_kind`／`last_curation_result`、索引名
  `PRIMARY`＋`ix_af_books_course`＋`ix_af_books_status`、0 行；模型服务 5002 `/health` 无排队（窗口排他判据成立）。
- **停服与备份**＝`scripts/axiom_flow_service.py stop`（窗口前 8902 监听 PID 34296，stop 幂等确认）；按 `operations.md` runbook
  以 `mysqldump --single-transaction` 备份 `af_books`／`af_operations`／`af_pages`／`af_kp`／`alembic_version` 五件，
  `SHOW CREATE TABLE` 逐表 DDL 双份留档；备份 sha256 `653dc40c916c69afbae8cbd34947fb0715b40f625e3be034429e5c8783531ce8`、
  窗口前 `ddl_before_af_books.txt` sha256 `98f4b5297a397e6928fd72e74ef69bb34c2056e1bf504ce1bf899c25cbecf2a8`。
  留档落仓库根 `tmp/w14_window_20261124/`（`%TEMP%` 同份，ADR 0012 临时区，不作事实源）。
- **迁移往返（alembic `-x db_url` 直连 `qed`）**＝`20261022_0001`→`20261106_0001`（upgrade）→`20261022_0001`（downgrade）
  →`20261106_0001`（upgrade），三步均 exit 0、迁移日志三行齐全。复核读数：列数 **22→24→22→24**（升降级后实测下退 22、
  再升 24），`alembic_version` 终值 `20261106_0001`；终态两列就位（`doc_kind`／`last_curation_result`）、
  索引名逐名不变（`PRIMARY`／`ix_af_books_course`／`ix_af_books_status`）、`SHOW INDEX` 无新增、`af_books` 0 行保持。
  窗口后 `SHOW CREATE TABLE af_books` 留档 sha256 `3c3bf5d59fdf061d818479f0c697091bffe6c753c140b06896a5770c9b915ed6`。
- **恢复**＝窗口结束即 `scripts/axiom_flow_service.py start --wait`，8902 新 PID 46076、`/api/v1/health` 200，端口监听复核在册；
  业务库 `qed` 仍 0 行（保持「业务库保持为空直到用户明确说同步 OK」的既有口径，本窗口只做结构不回填数据）。
- **判读**＝成功标准第 7 条「真库加列窗口有前后指纹、可升可降」逐项成立；W14 由「待开始」转完成，PLAN-028 的 W1~W18 在册项全部有判定。

## 验证与验收

- **可判据**＝每一枚新增值都要能被一条反向用例打到（未声明被拒、非法 `stage`×种类被拒、形状不合的题目草稿不落行、习题行不出现在共享主表导出面）。
- **零回落自证**＝种类派生的未知值不得静默当教材（与根侧 `learn.py:598-604` 的容错差异在条文里点名，两侧各自的判据互不冒充）。
- **无续跑自证**＝任何重试提交都发新批次号（`gen_batch` 与上一批不同），请求模型里没有页子集字段；`only_failed` 与 `content`
  之外的 `stage` 逐值 422 且零写入（`KA-52`）。
- **习题两段可对**＝教材侧题行按「章 → 题号」成行、父＝习题段行、零模型调用；习题集侧按同一把键合并且只补空位；
  共享主表导出面零习题行（`KA-53`）。
- **习题集两模式**＝配套：目标不绿 422 零写入；绿则按（章＋题号）命中只补空位、未命中在章下新增，来源书与来源页进 `meta`、题包进目标教材 `kp/`；独立：题行父＝题目所在章／节行，题包与题行 1:1，未匹配零写入且只进披露。
- **题包一题一份**＝`exercise_pack_rows` 与题行数（独立）或合并数（配套）相等；每份含 `## 题目`／`## 解答`／`## 来源` 三段标题；`narrative_ref` 非空数＝题包数。
- **未匹配只披露**＝未匹配解答不落行、不写题包；批次 `unmatched_solution_details` 与来源书 `meta.json` 清单逐项相等。
- **重解析有界**＝`retry_node_ids` 只与 `exercise` 合法（逐值 422 零写入）；只覆盖未确认机器行，确认行 422；批次号与上一批不同。
- **习题集完成口径**＝零题行 `done=false`；题行齐而题包缺 `false`；题行、题包齐且待重解析零枚 `true`。
- **跨书不碎树**＝习题行落教材后，`GET …/kp` 的章节树、`order_for_export` 的父先于子、`KA-18` 的锚点按行自身取证版本解释三条同时成立，各一条断言。
- **账目齐**＝题目腿每笔调用在共享 `qed_llm_calls` 留一行账（`KA-23` 的自报口径不变），并把 `step` 的新增值登记进闭集。
- **形状不越界**＝`af_kp` 列数与索引名在本案结束时与窗口前逐名相等（只加 `af_books` 两列）；`meta` 两枚闭集（`KP_META_KEYS`、`_KP_ENDORSEMENT_WRITABLE`）的扩展有名有判据。
- **块界不重叠**＝六行任务各自只承一处判据，同一处代码的改判只出现在一行的挂靠里；「冲突消解」列与三枚上游
  壳的成功标准逐条不冲突——本案不改写它们的任何一条。
- **顺序可执行**＝六行按表序推进（重要任务一→大任务一→大任务二→重要任务二→大任务三→大任务四），每行启动
  条件＝上一行交付面在本地可读；任一时刻至多一行 In Progress，跨行依赖只走表内已交付的列与行集。
- **一条链**＝收口一次取全链读数：三段状态一次读全、教材一轮从目录走到详情可指认、习题集一轮可指认（配套＝教材章下
  回指来源书与来源页、独立＝本书章下题行与题包 1:1），三件同时成立才算回归通过。
- **门禁**＝`pytest tests -q` ＋ `ruff check src tests scripts`；MySQL 层仍不含（`QED_TEST_MYSQL=1` 未解禁）。

## 回滚

- **代码**＝W3~W13 各条可逐条 `git revert`；种类闸一旦回滚，受理面回到「不区分种类」的在册行为，不产生数据修复。
- **数据**＝`last_curation_result` 可由 `op_kind='kp'` 流水与 `gen_status` 分布重算（一次性回填脚本，重算后丢弃即无害）；`doc_kind` **不可**由本仓数据重算，只能由同步腿再灌一次（REQ-155 到位后天然幂等），这一点是它必须做真列、且必须早于任何依赖它的写入的理由。
- **题包与索引**＝都在 `<book_dir>/kp/` 下（题包 `kp/<node_id>.md`、索引 `kp/exercises/`），逐本删除天然覆盖；回滚＝按 `gen_batch` 撤题行、删对应题包与索引，零库结构回滚。
- **库结构**＝迁移 downgrade 删两列即回到旧形状；真库窗口一旦执行过，回滚形态＝按 W14 备份重建该表并重灌书目。
- **不隐式执行**＝W14 的每一次真库动作都要用户单独批准，批准原文随读数留档本壳。

## 关闭与归档

- 关闭判据＝W1~W18 全部有判定，且「目标与成功标准」八条逐条有本地读数；真机侧的整本梳理读数不在本案关闭条件内（归 PLAN-027），本案只交形状。**2026-11-24 复核：W14 已执行完毕、Q3／Q6 已由用户取消，在册 W1~W18 全部有判定，八条成功标准逐条有读数，本案满足关闭条件（见文末 PLAN-028 收口结论）。**
- 归并去向＝见「核心任务与在册行的挂靠」表：本轮合的是归属与冲突裁决，不是壳的合并——PLAN-025／026／027 三枚壳各自保留承载与关闭判据（镜像守护要求每枚计划文件恰好一行 `PLAN-*`）；入站 REQ-139／142／143／146 的承载指针不迁移（一任务一承载）；本案与上游壳的前后依赖写在各自「前置条件」里，不改写它们的成功标准。
- 行位去向＝具体小任务与在册壳（PLAN-025／026／027／029、REQ-139／142／143／146／155）的 todo 行在「普通
  任务轮」节，随挂靠的大任务收尾逐个关闭；本案收口只关 PLAN-028 镜像行与 ARCH-035~041 七行。
- 归档判定 **Retain**：种类分流的裁决推导、真库加列窗口的前后指纹、与根侧两级闸的对账读数不可再生。
- 收口按 [任务生命周期](../../../standards/task-lifecycle.md)「计划生命周期」第 5 步的六步顺序执行，缺任一项视为未收尾。

## ARCH-036 收口结论（2026-11-24）

**关闭结果：`Achieved`。** 本行范围＝「重要任务一：底座」（壳「核心任务与在册行的挂靠」表：ARCH-036 承 W1~W4、W14）。
功能判据 W1~W4 已全部达成并有代码、迁移与真机 dry-run 证据；剩余 W14、REQ-155、Q6 三项经判定均非功能缺口，
本行据此关闭。PLAN-028 壳本身仍 `In Progress`（W14 属其工作项但未发令），本次只关 ARCH-036 一枚任务行，
壳不关闭、`plans/index.md` 不新增登记（无计划归档动作）。

| 功能判据 | 达成证据 |
| --- | --- |
| W1 前置立法 | [ADR 0019](../../../adr/0019-curation-outcome-and-book-kind-columns.md) `状态：Accepted`，`af_books` 两列入当前态清单；`database-design.md` 列清单随步 |
| W2 种类列入站 | `db/models.py:72` `derive_doc_kind`（两义按教材、缺位未声明）；`migrations/20261106_0001_af_books_kind_and_curation.py` 两列升降级往返；`db/repositories.py:589-626` 同步腿每次重算 `doc_kind`，是唯一写者 |
| W3 受理门种类闸 | `orchestrator/knowledge.py:446-458` 未声明 422「先同步书目」、非法 `stage`×种类 422；`api/main.py:181-182` 书目读面透出两列；`TestKindGate`／`TestBooksEndpoints` 有用例 |
| W4 结局位写者 | `knowledge.py:7215` `curation_result_for`、`repositories.py:681` `mark_curation_result`、`repositories.py:1499` `curating_book_ids`；`TestCurationResult` 六形覆盖（干净／失败节／缺页／未解析／异常／中断不回写失败） |

**收尾复核（2026-11-24）**：定向 `TestKindGate`／`TestCurationResult`／`TestBooksEndpoints`／schema／db／migration
共 **210 passed**；壳 2026-11-06 `qed_test` dry-run 已证两列 22→24→22→24 升降往返、b05/b07 同步后种类可读、
两枚 422 零写入、317 页 outline 批次完结。全量门禁读数见 `completed.md` 本行。

**三项剩余的去向（2026-11-24 更新）**：

1. **W14 真库加列窗口（D 类）＝已执行完毕**：用户 2026-11-24 单独发令后按 `operations.md` runbook 在业务库 `qed`
   完成备份/DDL/指纹与升→降→升往返（列数 22→24→22→24、终值 `20261106_0001`、索引名不变、0 行保持），
   读数见本壳「2026-11-24 W14 真库加列窗口」节。本项从 ARCH-036 关闭时的非功能欠账转为已交付运维里程碑，
   不再是 PLAN-028 的在册剩余。
2. **REQ-155（根侧 8900 `books/sync` 补 `roles`）**：本仓 W2 已能派生两值，按 C11 在到货前对未声明书一律 422 兜底，
   dry-run 已手工补 roles 取证；该键到货是**根侧转达**事项，本仓对根零写入，REQ-155 保留 Open 为跨项目长线行，
   不随本行关闭，也不构成此处功能欠账。
3. **Q3／Q6＝用户 2026-11-24 裁决取消**：Q3（DDL 与真机窗口先后）因 W14 已执行、PLAN-027 未发令而事实过期；
   Q6（徽章取数）属 8903 展示口径，底座两列与写者已就位、两读数并存各答一问，由消费面按需自取。两项已在壳
   「待裁点」表标注取消，不再是 PLAN-028 的在册剩余。

**归档判定**：本行无独立计划壳（承载为 PLAN-028 壳），无 `plans/index.md` 单独归档动作；结论正文留在本壳，
证据摘要迁 `completed.md`。

## PLAN-028 收口结论（2026-11-24）

**关闭结果：`Achieved`。** 依据「关闭与归档」节判据逐条复核：W1~W18 全部有判定（W14 已于 2026-11-24 执行完毕），
「目标与成功标准」八条逐条有本地读数；待裁点 Q2／Q3／Q5／Q6 均已裁或取消，其中 Q3／Q6 由用户 2026-11-24 明确取消。
PLAN-028 镜像行与 ARCH-035~041 七行随本次一并收口；承载壳判 `Retain`（裁决推导、真库加列前后指纹、与根侧两级闸的
对账读数不可再生，判据本体已晋升 `docs/design/knowledge-analysis.md`），迁 `docs/history/plans/2026-11/`。

| 成功标准 | 达成证据 |
| --- | --- |
| 1 种类可判且被判、未声明拒 | ARCH-036 W1~W3：`derive_doc_kind`、两真列、受理门两道 422，`TestKindGate` |
| 2 三段判据一次读全 | W4 三写者＋`GET /books` 透出两列，`TestCurationResult`／`TestBooksEndpoints`，全链回归三例 |
| 3 教材目录与习题两路 | W5~W7、W9／W10：四值阶段闭集、教材题行、习题集题目腿与跨书挂章，`TestFullChainRegression` |
| 4 四类行各有链路判据 | W6 `CONTENT_LEGS_BY_NODE_TYPE`：章节→章腿、知识点→点腿、习题与「其他」→∅ |
| 5 详情两读数 | W11：`must_master`／`key_definitions` 两枚闭集键三面同投影 |
| 6 重试三处各自有界 | W5 同范围重发、W8 `only_failed`×`content`、W17 `retry_node_ids`×`exercise`，各自 422 零写入反向用例 |
| 7 真库加列有指纹可升降 | W14 2026-11-24 业务库 `qed` 升→降→升，列数 22→24→22→24、索引名不变、备份 sha256 留档，见本壳证据节 |
| 8 任务面收得起来、一条链 | 四大任务＋两重要任务各自关闭（ARCH-036~041），W12 端点面与 W15 一条链回归三例，根侧字段清单成文 |

**门禁**：全量 `pytest tests -q` 与 `ruff check src tests` 见本次收尾会话读数（随 `completed.md` 迁移）。
**未随本行关闭**：REQ-155（根侧补 `roles`，跨项目长线行，本仓 422 兜底）、REQ-139 N3／C7（邻壳新增端点待裁）、
PLAN-027（真机整本梳理窗口，独立 D 类）、PLAN-034／REQ-163（独立 Accepted 壳）、PLAN-026 W12②（邻壳已裁待执行）；
以上均非 PLAN-028 在册项。
