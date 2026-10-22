# af_* 表族整族重建设计（af-table-family-rebuild）

状态：In Progress
任务类型：B
最后更新：2026-10-22
关联 ADR：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型边界与管线归属，本案不动）、
[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（集成测试白名单逐名枚举，加/删表必改）、
[ADR 0007](../adr/0007-parse-status-semantics.md)（进度与结局分列、六态语义——本案换承载表不换语义）、
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（区域是跨版本页级解析输入——折叠后仍是该判据）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪 DB 单源，本案的 `artifacts` 列不得升为判据）、
[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)（本案四表形状与档 B 整族重建的决策正本）、
[ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md)（W14：`database-design.md` 改用八章编号体）、
[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)／[ADR 0011](../adr/0011-ledger-one-row-per-task.md)／
[ADR 0013](../adr/0013-ledger-row-three-elements.md)（承载与台账行契约）、
根仓库 ADR 0024（1.0 前 schema 重建档位与 demo 门；本案取**档 B 整族重建**）、
根仓库 ADR 0025（前缀即所有权；`af_*` 列级正本在本仓库文档，根侧只随表级）
关联设计：[database-design.md](../architecture/database-design.md)（`af_*` 结构与迁移链唯一事实源，W3 晋升对象）、
根仓库总纲 `QED-Engine/docs/architecture/database-design.md`（全库通则 R1~R12、共享 `qed_*` 列级正本、
他项目表族同步契约的**唯一正本**；本案对它只按指针引用，见「三类登记面与根侧总纲对齐」节）、
[parsing-management.md](../design/parsing-management.md)（PM-3/5/6/8/9/10/11/16/17/18 随步）、
[parsing-pipeline.md](../design/parsing-pipeline.md)（PP-14/17/21 随步）、
[api.md](../architecture/api.md)（端点形状不变、字段来源改）、[code-map.md](../architecture/code-map.md)
关联 Tracker：docs/trackers/todo.md（PLAN-024 镜像行；入站请求 REQ-136 由本行单行承载（ADR 0011），
并入根侧已取消 ID 的 REQ-123／REQ-129 两行，见「承载 REQ-136」节）
归档判定：Retain（四表逐列形状、折叠映射、并发写入判据与稳定期改判属不可替代决策证据）

> **本壳只动文档**（W1 立项时的边界）：零 `src`、零迁移、零库内 DDL。档 B 的代码批（W7）与真机重建窗口（W8）
> 都必须等用户对本壳形状说「开始执行」，且后者属 D 类（备份 → 批准 → 执行）。
> **两条门均已于 2026-10-22 由用户放行并执行完毕**（W7＝代码批；W8＝两库整族重建，其执行权当日由
> 「我驱动、执行前逐次批准」改判为「现在进行下一步，你帮我执行」⇒ 由本会话驱动）。因此本壳正文里的
> 「待执行／待排期／执行权在用户」等字样只描述当时的批准状态，逐批结局一律以「步骤」表的末列为准。

## 用户裁决（本壳形状的上界，2026-10-21）

| # | 裁决 | 对本案的约束（逐条落到哪一节） |
| --- | --- | --- |
| ① | **整体三张表合适**：一张事件明细表写明所有事件、一张结果索引表、一张知识点表 | 目标形状＝4 张（三张 + `af_books`）；`af_page_blocks`／`af_kp_edges`／独立的解析结果表全部**不建**（「结论先行」节） |
| ② | **`af_books` 不动**（「零改列」半句已由裁决⑮改判） | 生效指针部分照旧：`active_job_id` 仍叫这个名字、仍是生效指针唯一事实源（PM-6），只是它指向的行搬到 `af_operations`；本表**增一列** `meta` 扩展槽，落点与代价见「af_books 增列」节 |
| ③ | **`af_parse_jobs` 加入明细表** | 任务表被 `af_operations` 吸收，一个解析任务＝一行 `op_kind='parse'` 的事件＝一个解析版本；`op_id` 就是版本号（折叠映射节） |
| ④ | **块编辑与区域声明也进事件明细表** | `af_block_edits`／`af_page_regions` 同样折叠为 `af_operations` 的 `intent` 行；`af_*` 表族由 5 张收成 4 张 |
| ⑤ | **结果索引表只要一张** | `af_pages` 就是那张表，扩 `artifacts`／`confirm_state`／`section_kind`／`meta` 四列；页级**不**各自声明格式 |
| ⑥ | **知识点表只要一张，关系简单写** | 单表 `af_kp`，层级靠 `parent_id`，关系靠 `links` JSON；**这是对本仓「JSON 列反向闸」的一次有代价的采纳**（例外登记与转表触发判据见「准入守则自查与例外登记」节） |
| ⑦ | **并发是核心判据** | 写入路径七条判据（CW-1~CW-7）+ 每条的反向判据（「并发写入判据」节）；本案所有当前态写入都归到「唯一键 upsert」或「行锁内整批替换」两种形态，不引入第三种 |
| ⑧ | **AF 只存结构 + 摘要，长文放 `QED_DATA_ROOT` 文件** | `af_kp.summary` ≤1000 字为列内上限，长叙述落数据根文件、列内只存相对路径 `narrative_ref`（入库边界不破） |
| ⑨ | **落地档位＝档 B 整族重建** | `DROP` 现有五表 + 迁移链**重置**为一条新 base 迁移；四件必答逐条给答（「档 B 重建批次」节） |
| ⑩ | **知识点保留最新 2 次梳理结果** | 用 `gen_batch` + 「同书保留最近 2 批」表达，不建批次表；不可裁剪清单见「保留与裁剪判据」节 |
| ⑪ | **方向确认**（2026-10-21 用户原话「这个方向没有问题，先确定这个方向」） | 裁决①~⑥的表形状（4 张表 + 吸收 `af_parse_jobs`）随方向一并定档；W2 停顿点剩余两处（`links` 越闸、迁移链重置）仍需逐条了结，且「方向确认」**不等于**「开始执行」——W7/W8 的批准门不变 |
| ⑫ | **文档按根侧总纲优化，本篇只登记三类** | 本仓 `database-design.md` 的重构目标＝**三类登记面**（共享 `qed_*` 表／需从 QED-Tracker 同步的形状／`af_*` 项目专用表），其余通则按指针引用根仓库总纲、不复制；落点＝「三类登记面与根侧总纲对齐」节 + W3 + S7 |
| ⑬ | **批准动文档，且本篇改判为「暂定」**（2026-10-21 用户原话「先变更吧我改变主意了，将其变为暂定状态」） | `database-design.md` 的 `确认状态` 由已确认改为**暂定**，`实现状态` 改为 In Progress（现役五表已验证、目标四表未实现），`设计状态` 记为「方向已确认、现役形状是其前身」；**元数据七字段不得加键**（`_metadata_block` 把任何 H2 前含「：」的行计为元数据 ⇒ 多一个键即转红），暂定原因与三项转正评审项写进引言 blockquote；此项批准只覆盖文档，W7/W8 的批准门照旧 |
| ⑭ | **拆成两个模块：解析模块／分析模块（知识点梳理），先梳理结构再做分析模块**（同批用户原话） | 现役五表全部属**解析模块**，分析模块在现役库内**没有对应表**；目标形状只交付分析模块的存储形状（`af_kp`），其写入路径与消费通路另批出设计（W11 + S8），且以本篇暂定结构为输入——**不得**为迁就分析模块改动解析模块的表形状；`code-map.md` 的模块面按两模块登记（随 W6） |
| ⑮ | **给 `af_books` 加 `meta`**（＝Q5 的乙案；与本壳建议的甲案相反，用户裁决） | 四表各有一个同名 `meta` JSON 扩展槽，这就是「留一些扩展空间」的落点；裁决②的「零改列」半句就此作废（生效指针与进度列不变）。**代价随裁决同批写死**＝双写者列域不重叠（KA-11 同一原则）：同步作业（`repositories.py:169-211` 的 `upsert_many`，现役已是「只覆盖同步字段、保留本侧字段」的逐列赋值）只需把 `meta` **加入那份保留清单**；人工写走整列覆写（CW-5）。反向判据＝同步重跑后 `meta` 键集合不变。落点＝「af_books 增列」节 + `database-design.md` 的同名 H4 + W7 |
| ⑯ | **检索索引版本行并进 `af_operations`，本轮就建列**（＝R3 的甲案且本轮落地；与本壳建议的「甲但不在本轮落地」相反，用户裁决） | `op_kind` 闭集扩第八值 `index` ＋ 增**独立**列 `embedding_signature`（与 `parser_signature` 分列不复用，避免一列两义），`af_operations` 26→27 列。**本轮建列不建写者**：索引行的 `job_id`／`status`／`lifecycle` 映射属检索面自身设计，本轮不定，任何路径不得写出非空签名（守护断言计数恒 0，与空置域值同一处置口径）。「现在就建」的第一性理由＝档 B 重置迁移链后本轮建与日后 ALTER 成本相同，而日后再动列集合等于把形状重开一轮。落点＝「af_operations 列清单」节 + 承载力审计表的甲案行 + W7 |

| ⑰ | **R1 采纳（从 `gen_status` 闭集删 `adopted`）＋ R2 采纳（`op_kind` 扩 `kp` 承载梳理流水）**（2026-10-22 用户原话「R1、R2采纳」） | R1＝域值**收窄**：闭集改三值 `draft`/`candidate`/`rejected`，人工终态只由 `confirmed` 一列表达 ⇒ 同批改 `database-design.md` 的 `gen_status` 列行、`RP-3` 第四类（去掉那个无写者的「或」）与本壳同名行，代码侧 `models.py:GEN_STATUSES` 成新的事实源，守护从「`adopted` 计数恒 0」改判为「**域外值**计数恒 0」。R2＝闭集**扩第九值** `kp` 承载 `lifecycle='event'` 的批次流水（`job_id`＝取证版本、`target_id`＝节点、`result`＝成功/失败节计数），**禁作当前态判据**（OB-1 对它同样成立），并**本轮建值不建写者**（写者属 W10 实现批），与 `index` 同受计数恒 0 守护。两条都**零 ALTER、零新迁移**：两处闭集都是 VARCHAR 上的应用层常量、不是 MySQL ENUM，故形状不变，唯一残余代价＝真库 `information_schema` 里的列注释文本滞后（`qed_test` 在下一次 MySQL 层跑时由夹具自愈，`qed` 在其回灌／重建窗口自愈；无任何守护比对注释，VERDICT 只看表名／head／行数）。落点＝本壳 `op_kind`／`gen_status` 两行 + `database-design.md` 同两处 + W7 代码批 + 新设计篇 `KA-27`/`KA-28` |
| ⑱ | **Q1~Q4 逐条了结＋ W12 确认晋升**（2026-10-22 用户原话「梳理由人工发起；一批为整本书一次，也可以一章一次，待发起；Q3也是人工；Q4两条都算；W12确认晋升」） | Q1＝梳理**只由人工发起**，机器不得自动排批（生效版本换版不产生批次）⇒ 新条文 `KA-24`。Q2＝**整本书为默认粒度**，允许人工发起时指定**单章范围**，且不自动续排；「最新 2 批」仍按 `book_id` 计数、不按章计数（否则同书各章批次互相挤占名额，`RP-2` 判据变形）⇒ `KA-25`。Q3＝未归档池的收敛责任归**人工**，机器不得为散点自动补挂父 ⇒ `KA-26`。Q4＝并发两轴**都算**（`co_requisite` 的关系语义 ＋ `CW-1~CW-7` 的写入并发），两条都不降级。W12＝设计正文整段以稳定名迁为 `docs/design/knowledge-analysis.md`（`KA-n` 编号原样保留，新增条文从 24 起续编），本壳该节只留承载与沿革 |

## 结论先行

**目标形状＝4 张表**（`af_books` 保留并增一列 + 新 1 + 扩 1 + 新 1），净变化 **5 → 4**（-3 张、+2 张）：

| 表 | 本案判定 | 承载什么 | 一句话理由 |
| --- | --- | --- | --- |
| `af_books` | **保留 + 增一列**（扩展槽 `meta`，裁决⑮） | 已验证书目快照 + 生效指针 `active_job_id` + 进度/结局三列 + 一个 JSON 扩展槽 | 裁决②（生效指针唯一性 PM-6、进度/结局分列 PM-10 的落点本来就在本表，换表不解决任何问题）＋裁决⑮（四表对称留扩展空间；代价＝同步写者不得触碰该列，见「af_books 增列」节） |
| `af_operations` | **新建**（吸收 `af_parse_jobs` + `af_block_edits` + `af_page_regions`） | 所有事件：一次解析（＝一个版本）、一次块编辑、一次结构声明、一次确认、一次 ingest/同步/清除；并按裁决⑯为检索索引版本预留 `op_kind='index'` 与 `embedding_signature`（本轮无写者） | 用户要的「哪本书、什么操作、操作结果、日期」在一张表里一次读全；版本身份与操作流水本来就是一件事的两面 |
| `af_pages` | **保留并扩四列**＝唯一的结果索引表 | 一页在一个版本内的状态、质量、**在位产物索引**、确认态、页域 | 裁决⑤；正文仍在文件（入库边界），本表只给「去哪读、用什么格式渲染回去」 |
| `af_kp` | **新建**（单表） | 章/节/知识点的工作区结构 + 一句总结 + 锚点 + 简单关系 + 长文指针 | 裁决⑥⑧；层级用自引用列、关系用 JSON 列，两张表的评审合并成一张 |

**格式与渲染的落法（裁决⑤⑦的交点）**：格式是**版本级**属性，不是页级——`af_operations.render_profile`
（`op_kind='parse'` 行）声明「这一版产物按什么渲染回去」，`af_pages.artifacts` 声明「这一页实际有哪些文件」。
页级各自声明格式会让同一版内出现互相矛盾的渲染结论，且无法用一条判据校验。

**本案明确不建的东西**：`af_page_blocks`（块正文入库，REQ-134 乙案）、`af_kp_edges`（关系边表）、
`af_llm_calls`（私有调用账）、`af_kp_batches`（批次表）、`af_pages` 的页级格式列、**检索索引版本表**
（第五表；其签名按裁决⑯以独立列并进 `af_operations`）。
每一件的「不建」都有判据与**触发转建判据**，见对应节。

## 三层职责与判据边界

| ID | 守则（结论 ＋ 判据） | 理由／代价 |
| --- | --- | --- |
| OB-1 | **事件行只答「发生过什么」，永不参与当前态判据**：`lifecycle='event'` 行只可作流水读出与计数，任何端点与状态机不得据它决定「现在是什么状态」 | 一条流水如果被读成状态，同一目标的多条流水就必须由读侧自己排序——判据会从库里漂到代码里。代价：块编辑/结构声明的**逐次修改史不保留**（与现状一致，非回归；要修改史另批双写） |
| OB-2 | **当前态只有四个来源**：`af_books`（书目进度/结局/生效指针）＋ `af_pages`（页级就绪与确认）＋ `af_operations` 的 `lifecycle='intent'` 行（人工意图）＋ `op_kind='parse'` 行的状态列（版本自身进度） | 前案（事件流可作判据）被否；四源之外的读数一律是派生视图，不得再落列 |
| OB-3 | **生效指针仍只有一列**：`af_books.active_job_id` → `af_operations.op_id`（同值同形状），`NULL` = 根视图 `legacy`；`af_operations` 不设任何布尔「生效」列 | PM-6 原样保留：同一事实两处存必漂移。改名会牵动产物目录名与根侧已见值形，本案只换表不换值 |
| OB-4 | **入库边界不破**：列与 JSON 键只存**状态、坐标、指纹与相对路径**；页正文、块文本、图像全在 `<book_dir>/versions/<job_id>/pages/`（PP-3/PP-14） | 裁决⑧同向；`payload.corrected_text` 是人工校正文本（现状 `Text` 列），属「人工判定」不属「产物正文」，边界逐条写明 |
| OB-5 | **就绪与激活仍只读 DB**（[ADR 0015](../adr/0015-db-single-source-for-readiness.md)）：`af_pages.status` + `af_books.active_job_id` + `af_operations` 的 parse 行状态；`artifacts` **只作完整性披露，不作判据** | 这是 ADR 0015 的直接推论；`artifacts` 与判据同时可读会造出第二事实源（磁盘）。守护＝删/造产物文件不改就绪读数 |
| OB-6 | **排序不用时钟**：同一坐标取「最新」一律按 `(updated_at, op_id)` 的字典序**在同一事务内**决定，且唯一键保证同坐标只有一行，读侧不需要排序 | 见 OB-2：`intent` 行按 `intent_key` 唯一 ⇒ 没有并列行，也就没有「时钟精度决定谁生效」的洞 |

## 并发写入判据（裁决⑦的核心节）

本案唯一需要防的并发形态是**「先读后判再写」**。七条写入判据，每条自带反向判据（会红的用例）：

| ID | 判据 | 为什么它并发安全 | 反向判据（会红的用例） |
| --- | --- | --- | --- |
| CW-1 | **事件行只 INSERT**：`lifecycle='event'` 行的写入路径只有插入，没有 UPDATE/DELETE（唯一例外见 CW-2） | 无共享目标行 ⇒ 无读写竞态、无丢失更新 | 用例断言「非 parse 的 event 行被 UPDATE」计数恒为 0 |
| CW-2 | **parse 行的回写只许向「更终」方向**：`queued → running → 终态`，回写目标行必须用带条件的 UPDATE（`WHERE status IN (允许的前序态)`），条件不匹配即不写并记日志 | 单 worker 队列下本已串行；加条件后「启动期归一」与「迟到的 worker 写」不会互相覆盖 | 双写同一 job：先 `mark_finished(completed)` 后到的 `reconcile_interrupted` 不得改写终态 |
| CW-3 | **意图行一律唯一键 upsert**：`intent` 行的定位键 `intent_key`（由 `op_kind + 坐标` 确定性摘要）上有唯一约束，写入是单语句 upsert，**禁止**「先查有没有、再决定插入或更新」 | 两个会话同时声明同一坐标 ⇒ 命中同一 `intent_key`，后到的覆盖前到的，不会各造一行、也不会双双插入 | 双 Session 并发 upsert 同一 `(book,job,page,block)` ⇒ 表内恰一行、读回后写值 |
| CW-4 | **整批声明必须在同一事务的同一把锁内**：`PUT …/structure` 的「缺席即撤销」是读现有行→算差异→写，必须对目标 `af_books` 行加悲观锁（MySQL `SELECT … FOR UPDATE`；SQLite 单写者天然串行）后才读，锁内不发起模型调用 | 判例＝PLAN-019 C2 的原子性判据（同形问题：两个全本 POST 互相看不见⇒双跑）。不加锁则两个并发声明会各自算出「对方缺席」，撤销丢失 | 双 Session 并发声明同页、集合互不包含 ⇒ 终态只保留后提交者且**无复活行**；双 POST 全本 ⇒ 恰一个建版本行、另一个 409 |
| CW-5 | **JSON 列不做增量改**：`links`／`payload`／`artifacts`／`meta` 的写入是**整列覆写**，需要「读旧值改一点」的形态必须落在 CW-4 的锁内，或改用整节点全量声明 | 部分更新（读 JSON→改一个键→写回）在并发下必丢；整列覆写 + 唯一键 = 幂等 | 两并发整节点声明 ⇒ 恰一个终态、无键丢失（先写侧的键若不在后写侧集合中即按缺席断开） |
| CW-6 | **`af_kp` 带乐观序号**：`rev` 列每次成功写入自增；8903 长表单可传 `if_rev`，不匹配即 409（人不在页面时的旧表单不得静默覆盖新结论） | 唯一键 upsert 解决「同一坐标并发」，`rev` 解决「人基于旧读数提交」——这是两类不同的并发 | 用例：读 `rev=3` 后他人写到 `rev=5` ⇒ 带 `if_rev=3` 的提交 409、不带者按 CW-3 覆盖 |
| CW-7 | **受理门与在飞判据共用同一把锁内的同一纯函数**（沿用 PLAN-019 C2/Q12） | 避免签名门与在飞判据两次读库看到不同世界 | 同 CW-4 的第二条 |

## af_books 增列（一列：扩展槽，裁决⑮）

现役 `af_books` 的列（`models.py:24-68`）逐列不变，只追加一列。列清单正本在 `database-design.md` 的
「af_books 目标增列」H4，本节只给裁决与其**代价**——代价不与列同批写死，扩展槽就会退化成第二事实源：

| 列 | 类型 | 空/默认 | 键 | 用处（为什么必须有这一列） |
| --- | --- | --- | --- | --- |
| `meta` | JSON | 可空 | — | 书目侧扩展槽（用户所指的 message 列，按共享 `qed_*` 表实际列名取 `meta`，提升时零转换）。三条红线照共享表 R7：**不进 WHERE、不建函数索引、不参与唯一键**；不放密钥与个人信息；键定档后升实体列并清空对应键 |

**双写者的列域必须不重叠**（KA-11 的同一原则，这是本列的全部代价）：

1. **同步写者不得触碰 `meta`**：`repositories.py:169-211` 的 `upsert_many` 现役已是「只覆盖同步字段、
   保留本侧字段」的**逐列赋值**（docstring 明写保留 `page_count`/`sha256`/`ingest_status`/`parse_status`/
   `pages_done`/`notes`），故 `meta` 只需**加入那份保留清单**，不是新增一条纪律。反向判据＝把它改成
   `session.merge()` 或整行覆写后，「同步重跑 ⇒ 先前写入的 `meta` 键集合不变」的用例转红（W7 落地）。
2. **人工写者走整列覆写**（CW-5），不做「读 JSON→改一个键→写回」。
3. `notes`（TEXT，人写自由文本）与 `meta`（结构化临时读数）**不互替**：散文进 `notes`，键值进 `meta`。

## af_operations 列清单（事件明细表，27 列）

一行＝一次操作。命名按族内既有风格（属性词后置、`note` 单数、时间列 `-at` 后缀）。表族内无物理外键、
按业务键关联（沿用现状）；`job_id` 与 `op_id` 的关联是逻辑关联。

| 列 | 类型 | 空/默认 | 键 | 用处（为什么必须有这一列） |
| --- | --- | --- | --- | --- |
| `op_id` | VARCHAR(64) | NOT NULL | PK | 事件身份，值形沿用 `uuid4().hex[:12]`（PP-14）。`op_kind='parse'` 行的 `op_id` **就是版本号**：产物目录 `versions/<op_id>/`、`af_books.active_job_id`、`af_pages.job_id` 都按值指它。值形不改的理由＝只换表不换目录名，把改动面收在持久层 |
| `book_id` | VARCHAR(100) | NOT NULL | i | 作用域（哪本书）。事件流的主屏读法是「一本书做过什么」，故它必须是索引首列 |
| `job_id` | VARCHAR(64) | NOT NULL `''` | i | 版本坐标。四条取值：parse 行**强制等于 `op_id`**（守护：单测断言该行 `job_id = op_id`）；`edit`/`confirm` 行＝其绑定的生效版本（编辑结论绑版本，PM-5/PP-9）；`kp` 行＝该批取证版本（裁决⑰R2：结论钉回那一版的产物）；`structure`/`ingest`/`sync`/`purge` 行＝`''`（结构意图跨版本存活，ADR 0010 决定 1 的原样平移） |
| `op_kind` | VARCHAR(24) | NOT NULL | i | 闭集 `parse`/`edit`/`structure`/`confirm`/`ingest`/`sync`/`purge`/`index`/`kp`（9 值）。扩值须评审——种类是判据的分母，随手扩值会让「什么操作」这个问题失去定义。`index`＝检索索引版本（裁决⑯）、`kp`＝梳理批次流水（裁决⑰R2，行语义见新设计篇 `KA-27`），两者**本轮建值不建写者**：索引行的 `job_id`／`status`／`lifecycle` 映射属检索面自身设计，本轮不定；`kp` 行的写者属实现批（W10）。守护断言任何路径写出的这六个值（`confirm`／`ingest`／`sync`／`purge`／`index`／`kp`）计数恒 0（与「域外 `gen_status` 值」同一处置口径） |
| `lifecycle` | VARCHAR(16) | NOT NULL `event` | — | 闭集 `event`（不可变流水，OB-1 禁作判据）/`intent`（人工意图当前态，OB-2 唯一合法判据位）。这一列是本案「一张表两用」能被守护的全部依据 |
| `actor` | VARCHAR(16) | NOT NULL `human` | — | 闭集 `human`/`system`/`model`。人工确认门与自动归一必须可分列：「谁做的」不是细节而是裁决（PAGE 确认与页域自动判定的可信度不同） |
| `page_no` | INTEGER | 可空 | i | 页坐标（edit/structure/confirm/单页 parse 请求）。NULL＝不落在具体页 |
| `block_index` | INTEGER | 可空 | i | 块坐标（edit）。边界沿用 PP-9：`index` 语义随版本产物重排，故编辑绑版本（`job_id`）而非跨版本身份 |
| `target_id` | VARCHAR(64) | NOT NULL `''` | — | 被操作对象的稳定身份。structure 行＝服务端按几何派生的 `region_id`（原 `af_page_regions.region_id`，跨版本不变、响应回给根侧长期定位）；kp 相关行＝`node_id`；其余 `''` |
| `intent_key` | VARCHAR(64) | 可空 | UQ | **意图幂等键**：`intent` 行＝`md5(op_kind:book_id:job_id:page_no:block_index:target_id)` 前 16 位；`event` 行恒 NULL。MySQL 唯一键允许多行 NULL ⇒ 这就是「只对意图行生效的部分唯一索引」，CW-3 的全部承载。代价写明：唯一性由本列而非坐标列保证，坐标列本身不设唯一键 |
| `engine` | VARCHAR(24) | NOT NULL `''` | — | parse 行的显式引擎名。**不做引擎兜底**（缺引擎名应在受理时报错，不静默回落 MinerU；旧链 `20260920_0002` 已 DROP `'mineru'` 兜底）。新 base 带 `server_default ''`：它只服务折叠后不写引擎的 `intent` 行与「未记录」哨兵，parse 行读到空串即写侧缺陷（W7 落地后随步更正本行——原陈述「无默认值」与 ORM/DDL 实形不符） |
| `pages` | JSON | 可空 | — | parse 行的请求页集（`null`＝全本）。受理门与 `find_in_flight` 都读它；非 parse 行 NULL |
| `page_count` | INTEGER | 可空 | — | 建任务时的全书页数快照（PP-17 写入时机不变） |
| `source_sha256` | VARCHAR(64) | NOT NULL `''` | — | 版本自证：本版实际是按哪份源文件跑的（PP-17） |
| `parser_signature` | VARCHAR(64) | NOT NULL `''` | i | 代际签名（PLAN-019 C1）。哨兵 `''`＝未知代际，**只判需重跑、不判过时**（C1 边界三条原样平移，不因换表而改） |
| `embedding_signature` | VARCHAR(64) | NOT NULL `''` | — | **检索索引代际签名**（裁决⑯）：嵌入侧输入集（绑定、模型、维度、服务端点）的规范化哈希，与 `parser_signature` **分列不复用**——复用即一列两义，属族内红线。哨兵 `''` 语义同上。**本轮建列不建写者**：不建索引（无查询即无索引，索引随检索面第一个消费方同批加）；守护断言任何路径写出的非空值计数恒 0 |
| `render_profile` | VARCHAR(32) | NOT NULL `''` | — | **版本级格式档案**（裁决⑤落点）：本版产物用哪种渲染口径回显，取值是 `schemas.py` 里的闭集常量（如 `md+blocks/v1`）。用处＝8903 拿到版本就能确定渲染器，不必逐页猜；闭集外拒绝。代价＝同代内不能逐页异构（这是**特性**，不是限制） |
| `status` | VARCHAR(16) | NOT NULL `queued` | i | 结局/进度。parse 行用 ADR 0007 的六态**原值透传**（`queued`/`running`/`completed`/`partial`/`failed`/`interrupted`，PM-8）；非 parse 行只用 `ok`/`failed`。不做「看起来更好」的归并 |
| `progress` | JSON | NOT NULL `{}` | — | parse 行进度读数（节流写与终态必写沿用 PM-15）；`meta` 类杂项不得混入本列（本列是被轮询读的热列） |
| `failed_pages` | JSON | NOT NULL `[]` | — | 结构化失败页码数组，不截断（PM-9）；`error` 只是给人看的摘要，摘要截断不丢页码 |
| `error` | VARCHAR(500) | NOT NULL `''` | — | 人读摘要（≤500，现状口径） |
| `payload` | JSON | NOT NULL `{}` | — | 操作载荷（「什么操作」的结构化侧）。edit：`{block_type, verdict, note, corrected_text, corrected_bbox}`；structure：`{op, bbox, anchor_bbox, members, kind, note}`；confirm：`{state}`；parse 的 `pages` 之外的参数不入本列（保持热列扁平）。**形状闭集由单测断言**，禁自由键 |
| `result` | JSON | 可空 | — | 操作结果读数（「操作结果」的结构化侧）：受影响页/块、生成条数、拒绝原因码。与 `status` 分工＝`status` 是判据、`result` 是明细；NULL＝无额外读数 |
| `note` | VARCHAR(1000) | NOT NULL `''` | — | 人写备注（单数，族内较新两表口径；`af_books.notes` 的复数不一致属 REQ-136 R6，本案新表只选择跟哪个口径，不重开该判） |
| `meta` | JSON | 可空 | — | 扩展槽（用户所指的 message 列，按共享 `qed_*` 表实际列名取 `meta`，提升时零转换）。三条红线照共享表 R7：**不进 WHERE、不建函数索引、不参与唯一键**；不放密钥与个人信息；定档后升实体列并清空对应键 |
| `created_at` / `updated_at` | DATETIME | NOT NULL | — | UTC naive 时刻对（族内口径）。`created_at`＝事件发生（用户要的「日期」）；`updated_at`＝意图行最后改写，`event` 行恒等于 `created_at` |

索引与键：`uq_af_operations_intent(intent_key)`、`ix_af_operations_book(book_id, op_kind, created_at)`
（时间序事件流主屏读法）、`ix_af_operations_version(job_id, page_no)`（版本作用域读意图行）、
`ix_af_operations_sig(book_id, parser_signature, status)`（受理门与在飞判据、`count_active` 的 purge 闸门）。
`embedding_signature` 本轮**不建索引**——无查询即无索引，索引随检索面第一个消费方同批加（裁决⑯）。

**宽表与稀疏列的代价（写明，不粉饰）**：列域分三段而互不共用——**parse 段**
（`engine`/`pages`/`page_count`/`source_sha256`/`parser_signature`/`render_profile`/`status`/`progress`/
`failed_pages`/`error`）只有 parse 行写；**坐标与载荷段**（`page_no`/`block_index`/`target_id`/`intent_key`/
`payload`/`result`/`note`）由 edit/structure/confirm 行按各自语义部分使用；**索引段**
（`embedding_signature`）本轮整列全空。一行里必有整段空列。反面方案是把 parse 专列
（`status`/`engine`/`parser_signature`/`failed_pages`）塞进 `payload` JSON——那会让受理门、作业列表、
重跑清单、启动期归一四类 SQL 全部退化为 JSON 取值，
既违 `meta` 三红线所依据的同一条「只读不筛」纪律，也让准入守则 2 的反向闸失去意义。
本案按裁决①③④⑯采纳宽表，并把「稀疏度」交给 W7 的表统计复测。

## af_pages 列清单（唯一结果索引表，PK 不变 + 新增四列）

现列全部保留（`book_id`+`job_id`+`page_no` 三列复合 PK、`status`、`source`、`quality`、`error`、`parsed_at`）：
一页在一个版本内的状态与质量信号。本案只加不加删。

| 新增列 | 类型 | 空/默认 | 用处 |
| --- | --- | --- | --- |
| `artifacts` | JSON | 可空 | 页级**在位产物索引**：`{png:{present,bytes,sha256}, md:{…}, blocks:{present,bytes,sha256,block_count}}`。**只存在位、字节数与指纹，不存正文、不存绝对路径**——路径按 PP-3/PP-14 的版本目录约定派生，本列是「去了能不能读到」的读数。用处＝用户要的「结果索引」本体，且逐页清单可一次 SQL 给出缺哪类产物。边界：OB-5——本列**不作就绪判据**，Axiom-Flow 的就绪仍只读 `status` |
| `confirm_state` | VARCHAR(16) | NOT NULL `unconfirmed` | 页级人工确认态（`unconfirmed`/`confirmed`/`rejected`），作用域随 `(book_id, job_id, page_no)`＝**版本内判定、不跨版本继承**（PLAN-019 C4 与 `af_block_edits` 同判例）。这是 PLAN-018 W8 确认门的落点；chunk 重建只聚合 `confirmed` 页 |
| `section_kind` | VARCHAR(16) | NOT NULL `''` | 页域角色闭集 `cover`/`preface`/`ack`/`toc`/`chapter`/`appendix`/`back`/`other`，`''`＝未判。用处＝「只取封面介绍、去掉正文前与致谢」要能**可筛可判**；版本换代走既有页行继承机制（`_inherit_page_rows`）不重判。判据＝状态信号一律加列不张表（准入守则 1） |
| `meta` | JSON | 可空 | 扩展槽，三红线同 `af_operations.meta` |

**不加的列（逐条给理由）**：`render_profile`（版本级，放这里会允许同版异构）、`text`/`markdown`
（正文属文件，入库边界）、`format`/`bbox_space`（逐页坐标空间由 `PageResult.bbox_space` 在产物内声明，
PP-8，DB 重复一份即第二事实源）、`page_kind`（与 `section_kind` 同义，不留两个名字）。

## af_kp 列清单（知识点表，单表 23 列）

一行＝一个节点（章／节／知识点）的当前结论。层级自引用、关系 JSON——按裁决⑥「简单写」。
列名与域值刻意对齐共享表 `qed_knowledge_point` 与根侧候选池，使提升零转换。

| 列 | 类型 | 空/默认 | 键 | 用处 |
| --- | --- | --- | --- | --- |
| `node_id` | VARCHAR(64) | NOT NULL | PK | 稳定身份 `afkp-<hex12>`，服务端随机发放。用随机而非内容摘要＝改名不该变成新节点，重挂父级同理；提升后根侧按它做幂等锚，故一经发放不可变 |
| `book_id` | VARCHAR(100) | NOT NULL | i | 梳理作用域＝一册书（目录/章/节/点都从这册书的解析产物长出来）。跨书合并归根侧主表，本表不承担 |
| `job_id` | VARCHAR(64) | NOT NULL `''` | — | 该行取证时生效的版本（`''`＝人工直填或根视图）。**只作溯源，不进作用域键**——这是本表最重要的一个决定，见「跨版本存活」 |
| `node_kind` | VARCHAR(16) | NOT NULL `point` | i | 闭集 `outline`＝目录节点／`point`＝细知识点。所有「只数知识点」的查询按它过滤 |
| `parent_id` | VARCHAR(64) | 可空 | i | 自引用父节点：章＝NULL、节＝章、point＝所属 outline。NULL 的 point 行＝未归档池（「重新编排目录」这个动作就是改本列与 `order_hint`） |
| `depth` | INTEGER | 可空 | — | outline 层级 1=章 2=节；point 恒 NULL。有它就不必为判层级做递归 |
| `order_hint` | INTEGER | 可空 | — | 目录前序序号（outline）／学习顺序提示（point）。梳理产物序即这一列 |
| `name` | VARCHAR(500) | NOT NULL | — | 展示名（章名／知识点 canonical 名） |
| `norm_key` | VARCHAR(200) | NOT NULL | i | 去重键（全角转半角＋空白折叠＋小写），与根侧同口径。同父唯一由**服务端校验、不建唯一键**（跨章同名合法；`parent_id` 为 NULL 时 MySQL 唯一键也无法去重） |
| `role` | VARCHAR(16) | NOT NULL `''` | — | 闭集 `''`／`core`＝核心／`explainer`＝解释性／`example`＝举例。**「核心」是节点在所属章内的地位，不是边**——这是把用户六项里的「核心」从关系里摘出来的唯一判据 |
| `type` | VARCHAR(16) | NOT NULL `''` | — | point 行四分类 `memory`/`procedure`/`concept`/`design`（与根侧同域，间隔复习序列依据）；outline 行必须 `''` |
| `summary` | VARCHAR(1000) | NOT NULL `''` | — | 一句话总结（章与点都可写），上限取根侧同值使提升原样平移零截断。**长叙述不落本列**（裁决⑧） |
| `links` | JSON | 可空 | — | 关系简单写：`[{kind, to, note}]`，`kind` 闭集 `prerequisite`（先学，有向、参与 DAG）/`co_requisite`（**并发/需同时掌握**，对称，写前按 `min,max` 归一端点序使反向重复声明幂等）/`explains`/`exemplifies`/`extends`（有向，参与 DAG）。用户六项的逐条映射见「关系与不变式」节 |
| `anchors` | JSON | 可空 | — | 来源锚点 `[{kind, ref_id, page, block_indexes, snippet}]`，页序＝**PDF 页序**（印刷页码只存在于解析草稿）。梳理结论必须能回指到第几页哪几块，否则无从复核 |
| `narrative_ref` | VARCHAR(200) | NOT NULL `''` | — | 长文在数据根下的**相对路径**（裁决⑧的落点）：讲解/证明/例题正文写文件，本列只存指针，`''`＝无。路径基准与数据根双闸沿用 PP-5/PP-16（单段名 + 根内路径，拒绝越根） |
| `gen_status` | VARCHAR(16) | NOT NULL `candidate` | i | 闭集 `draft`/`candidate`/`rejected`，**只有机器写**（裁决⑰R1：删 `adopted`——人工终态只由下面的 `confirmed` 一列表达，域内不设第二个人工终态位） |
| `confirmed` | INTEGER | NOT NULL 0 | — | 人工确认位（0/1），**只有人工可置**。与 `gen_status` 分列＝机器与人工各写一列互不覆据（根侧候选池同分工）；置位才可被提升 |
| `gen_batch` | VARCHAR(64) | NOT NULL `''` | i | 梳理批次＝一次重编排作业。整批复核、整批清理、整批回滚与「保留最新 2 批」全据此，**故不需要批次表** |
| `model_ref` | VARCHAR(128) | NOT NULL `''` | — | 产出者标识（`manual`/模型名）。自由文本列不得含密钥与个人信息 |
| `rev` | INTEGER | NOT NULL 0 | — | 乐观并发序号（CW-6）。每次成功写入自增；长表单可传 `if_rev` |
| `meta` | JSON | 可空 | — | 扩展槽，三红线同前 |
| `created_at` / `updated_at` | DATETIME | NOT NULL | — | UTC 时刻对 |

索引：`ix_af_kp_book(book_id, node_kind, gen_status)`（工作区主屏）、`ix_af_kp_parent(parent_id)`（展开子树）、
`ix_af_kp_batch(gen_batch)`（整批核销）、`ix_af_kp_norm(book_id, node_kind, norm_key)`（普通索引，服务端查重）。

### 跨版本存活（为什么 `job_id` 不进作用域键）

三条独立依据同向：① 本仓准入守则第一句就是「身份需跨版本稳定」才值得入库；② `af_page_regions`
已确立「人工结构意图刻意不含 `job_id`、跨版本存活、不随版本更替删除」的先例（ADR 0010），知识点是
同一类人工资产；③ 根侧 `qed_knowledge_point.book_ref` 的口径是「版本换代不碎树」。重解析是**产物换代**，
不该抹掉人已确认的知识结构。代价一起写明：换版或重新 ingest 后锚点可能失效（页序变了），处理口径
照 structure 意图行的「越界行可见保留、不静默删」，由 `job_id` 自证该行取证于哪一版——失效可见，
不静默改写。

## 关系与不变式（DDL 表达不了、必须在受理门成文的）

| ID | 不变式 | 违规处置 |
| --- | --- | --- |
| KP-1 | `links[].to` 必须存在于同 `book_id` 的节点集中；跨书关系归根侧主表，不落本表 | 422，响应点名缺失的 `to` |
| KP-2 | 自环（`to == node_id`）一律拒绝 | 422 |
| KP-3 | `prerequisite` 与 `extends` 两类合起来**必须无环**；落库前对现有图做 DFS，成环即拒绝并在响应里回指环路径。`co_requisite`/`explains`/`exemplifies` 允许成环（它们不是次序语义）——这一区分是「哪些边参与 DAG」的唯一口径 | 422 + 环路径 |
| KP-4 | `co_requisite` 端点序写入前按 `min,max` 归一；读时按「两端任一方向的并集」解释 | 静默归一（幂等），不报错 |
| KP-5 | `links` 是**单向存储、双向可读**：A 声明「A 需先于 B」只在 A 行落一条，B 行不自动镜像。读侧必须做并集，写侧不得为了"整齐"去改对端行（那是第二次写，违 CW-5） | 读侧并集（仓储唯一入口） |
| KP-6 | `node_kind='outline'` ⇒ `type=''`、`depth∈{1,2}`；`point` ⇒ `depth IS NULL` | 422 |
| KP-7 | 删除节点必须显式清引用边（无物理外键）：删 A 时对所有 `links` 含 A 的行做一次锁内整列覆写 | 与删除同事务 |
| KP-8 | `confirmed` 只允许 `actor='human'` 的行置位；系统路径永远写 0 | 守护用例断言自动批次跑完后 `confirmed` 计数不变 |

**「先学／后学／需同时掌握／核心／解释／举例」六项的逐条映射（裁决⑥的完整答案）**：
先学／后学＝同一条 `prerequisite` 边的两个读法（**不建两条反向边**）；需同时掌握／并发＝
`co_requisite`（对称，幂等归一）；解释＝`explains`；举例＝`exemplifies`；**核心＝`role='core'`（地位，不是边）**。
另保留 `extends`（推广延伸），因教材目录重排必然产生上下位之外的次序语义。闭集外的关系一律先入
`links[].note`，不随意扩 `kind`。

## 保留与裁剪判据（同时答 REQ-136 R5／原 REQ-129）

| ID | 判据 | 理由 |
| --- | --- | --- |
| RP-1 | **`af_operations` 与 `af_kp` 一律不清理**（明文「不清理」是本案的自定口径，不选保留窗数值） | 版本自证史与人工结论是审计唯一来源；根侧只声明边界不代设数值，本侧选择不设数值 |
| RP-2 | **`af_kp` 的「保留最新 2 次」＝按 `book_id + gen_batch` 分组，只保留最近 2 个批次中「未确认的机器产物行」** | 用户裁决⑩针对的是**机器梳理结果**的并存份数，不是人工资产。已确认行不属于「最新 2 次」的管辖范围 |
| RP-3 | **不可裁剪行（四类，永不清理）**：① 被 `af_books.active_job_id` 引用的 parse 行；② `lifecycle='intent'` 的全部意图行；③ `status∈(queued,running)` 的活动行；④ `af_kp` 中 `confirmed=1` 或被任一 `links` 引用或被 `af_books` 书目引用的节点行（裁决⑰R1：判据位只有 `confirmed` 一个，旧文里的「或 `gen_status='adopted'`」因无写者而不产生保护面，就此删除） | 前三类是指针与在飞；第四类是人已背书的知识结构。触发阈值与执行另批 D 类 |
| RP-4 | 任何裁剪执行必须是**只读清单先行 → 用户批准 → 逐书执行**，且清单本身要能同机复算 | 判例＝PLAN-019 W7 的过时清点口径；先删后解释会毁掉自证链 |

## 提升通路（唯一写者不变，本案不动它）

- 共享 `qed_knowledge_point` 的当前唯一写者是根侧 8900，本仓库对该表**只读不写**（现状定据在
  `database-design.md` 的「目的与边界」共享表段）。**AF 不得直写共享知识点表**，这不是可协商项。
- 通路＝**8900 拉取式单向**：8902 提供只读导出端点（形状由 W5 在 `api.md` 定稿；本案只声明「必须存在、
  只读、需管理面挂载纪律」），8900 侧提升作业把 `confirmed=1` 的行按 outline→point 两层写入主表，
  `summary` 原样平移，`anchors` 追加 `{kind:'af_node', ref_id:<node_id>}` 作**幂等锚**（重复提升命中同一行、
  不重复建主表行；锚落 `anchors` 而非 `meta`，因为 `meta` 禁进 WHERE 无法反查）。
- **AF 不回写提升结果**：`af_kp` 不设 `promoted_kp_id` 列。提升事实的唯一正本在主表，工作区侧的终态**只由
  `confirmed` 一列表达**（裁决⑰R1：`gen_status` 域内不设第二个人工终态位）。跨仓回写会造出第二条写路径（OB-4 同源：同一事实两处存必漂移）。
- LLM 调用账**不进 `af_*`**：视觉解析与梳理文本调用都记共享 `qed_llm_calls`（`service='axiom_flow'`），
  该表只做追加式 ALTER、绝不 DROP 重建。**已知事实错**：本仓文档现自陈「local 模式直写该表」，
  但 `src/` 内对任何 `qed_*` 表的引用为**零**——该陈述超前于实现（更正项②，见「事实错更正」节）。

## ③ 解析正文不入库（判据沿用，不逐处补句）

本案**不建** `af_page_blocks`（块正文入 DB 的乙案）。判据成文一次，此后不再每处遇到就补一句：

> 只有当出现**一条必须用 SQL 跨页检索块文本才能在服务端完成**的梳理／抽题需求，
> 且甲案（经 8902 逐页读产物的通路）的实测吞吐被真机演练否证时，才评审 `af_page_blocks`。
> 判据触发前的任何一次「读起来慢」都按甲案优化（批量端点、单次多页），不建表。

`af_pages.artifacts.block_count` 与 `af_pages.quality` 的 ledger 键给出「不读正文也能计数」的那一半需求，
这正是把乙案继续挡在门外的原因。丙案（正文的抽取投影行）同样不采：snippet 是正文副本的副本，
同一事实三处存（文件／snippet／根侧锚点），漂移面比乙案更难看。

## 分析模块设计（W11 交付：已晋升为 `docs/design/knowledge-analysis.md`）

> **一行指针**：本节正文已由 W12 晋升整段迁出为
> [`docs/design/knowledge-analysis.md`](../design/knowledge-analysis.md)（六段骨架、`KA-1~KA-28`、
> 通路 mermaid、失败与质量语义、13 条验收判据都在那一篇）。本壳**不再保留第二份正文**（一个事实
> 一个维护位置）。晋升条件＝用户对本节原载的两项**结构请求**与四项**开放裁决**逐条给裁，已于
> 当日全部了结（见下「裁决与编号沿革」与「用户裁决」表 ⑰⑱ 两行）。

### 裁决与编号沿革（判据在设计篇，过程只留在这里）

| 项 | 用户裁决（原话口径） | 落点 |
| --- | --- | --- |
| R1 | 采纳：从 `gen_status` 闭集删 `adopted` | 设计篇 `KA-28`；同批改 [`database-design.md`](../architecture/database-design.md) 的 `gen_status` 列行与「保留与裁剪」第四类、`models.py:GEN_STATUSES`、`tests/unit/test_db.py` 的域外值断言。原「验收判据 11＝不得写出 `adopted`」改判为「不得写出域外值」 |
| R2 | 采纳：`op_kind` 扩 `kp` 承载梳理批次流水 | 设计篇 `KA-27`；同批改 `database-design.md` 的 `op_kind`／`job_id` 两行与「行种类与坐标自证」不变式、`models.py:OP_KIND_KP`、`tests/unit/test_db.py` 的无写者值断言。R2 未采时的旧读法（明细只进 `logs/`）作废 |
| Q1 | 梳理由人工发起 | 新增 `KA-24`；原建议升为判据，并在「失败与质量语义」加「版本激活不排批」一行 |
| Q2 | 一批为整本书一次，也可以一章一次，待发起 | 新增 `KA-25`（默认整本书、人工发起时可指定单章、批次名额按 `book_id` 计），「不自动续排」写入 `KA-24` 的边界 |
| Q3 | 未归档池的收敛也是人工 | 新增 `KA-26`，并在 `KA-7` 补「机器不得自动补挂父」一句 |
| Q4 | 两条并发轴都算 | 写入设计篇「目标」第 6 条，两条都不降级：关系轴＝`database-design.md`「服务端不变式」的对称边归一条，通路轴＝同篇「键与并发写入形态」七条 |
| W12 | 确认晋升 | 整段迁出，`KA-1~KA-23` 编号原样保留、新增条文从 24 起续编（不重排既有编号＝不造二次漂移源）；同批随步 `test_design_documents.py`（`CURRENT_DOCUMENTS` 与 `carriers` 加 `KA`）、`test_fixed_document_form.py`（`DESIGN_DOCUMENTS`）、`test_code_document_mapping.py`（`ACTIVE_DOCUMENTS`）、`docs/design/index.md`（三篇→四篇、条文枚举加 `KA-n`、术语表补「知识点节点」行）、`overview.md`（不管表条文枚举加 `KA-n`、能力矩阵加「知识点梳理与提升通路」一行）、`code-map.md`（覆盖范围与「本篇不管」加 KA、`src/axiom_flow/db/` 事实源接设计篇）、`database-design.md`（「流程落点」与「承载力审计」两处把「分析模块设计篇」改为实链接） |

**随步的引用改写（跨壳编号必须降级）**：迁移时把本节原稿里指向「本壳 `CW-1~CW-7`／`KP-1~KP-8`／
`OB-1`／`RP-2~RP-4`」的跨壳编号，改为对 [`database-design.md`](../architecture/database-design.md)
的「服务端不变式」「键与并发写入形态」「保留与裁剪」「跨版本存活」四个小节的链接。理由＝设计篇的
条文编号必须篇内解析（[doc-governance.md](../standards/doc-governance.md) 的硬规则 R2，由
`test_design_documents.py::test_design_clauses_resolve_inside_their_own_document` 守护），而那四条
判据的**正本在权威文档那四个小节里已经存在**（W7-F 定稿时并入），本壳的同名节自此只作裁决过程与
反向判据的台账，不再是设计篇的依赖。同批把权威文档 `op_kind` 行里那处孤立引用（原写作 `OB-1`）
也改为「与『服务端不变式』的『行种类与坐标自证』条同判据」，全库 `OB-n`／`RP-n`／`CW-n`／`KP-n`
在 `architecture/` 与 `design/` 内的字面命中数归零。

**验收判据的对应**：设计篇现有 13 条（原 11 条 ＋ 新增「发起面唯一」「单章批次按书计数」两条；
第 11 条按 R1 改判为域外值口径）。实现批 **W10** 逐条取证时以设计篇那张表为准，本壳不再另列一份。

## 承载力审计与流程确认（W13 交付：四表够不够，含未来规划）

> **承载位置**：本节是审计的**台账面**（可带任务编号、原文引用与在飞线状态）；审计的**结论正文**＝
> `database-design.md` 目标形状节的「流程落点（两模块十二步）」与「承载力审计（现役能力与规划能力
> 逐条落点）」两个 H4——那边禁任务编号与日期，故编号与状态只在本节存，两边不重述同一事实。
> **第一性判据只有两条**：入库三判据（身份需跨版本稳定／需唯一约束／需 SQL 集合检索，任一命中且
> 现有表族承载不了才加表）与入库边界（产物正本在文件系统，DB 只存元数据与状态）。审计＝把每条能力
> 按这两条过一遍，而不是「暂时没想到还要加什么」。

### 规划能力逐条判定（roadmap 四条 + 在飞三线 + 两个消费方）

| 能力线 | 状态 | 存储判定 | 依据与代价 |
| --- | --- | --- | --- |
| 解析管理闭环（roadmap 第 1 条） | 已交付 | **三表足够**：`af_books`＋`af_operations`＋`af_pages` | 现状即此，本案只换承载表不换语义 |
| 分块与向量检索（roadmap 第 2 条，ARCH-004／ARCH-012） | 未立项 | **足够**：向量正本在外部向量库、不进 MySQL；`chunks.json` 是派生文件不入库；块级检索单元按 `self_ref` 锚回 `af_pages` ⇒ 块正文表的转表判据**不触发** | PLAN-009 的 R6 拒绝的是「纯文件无 DB 存储」这一**模式**，不是拒绝 DB 本身；其 R3 的 BM25 与向量索引都在 MySQL 之外持久化，故不存在「必须用 SQL 跨页检索块文本」的需求 |
| 检索索引的版本管理（ARCH-023 R1，判定＝吸收并改造为 DB 承载） | 未排期 | **四表可承载（裁决⑯＝甲案，本轮建列）**：`af_operations` 扩 `op_kind='index'` 一值 ＋ 增独立列 `embedding_signature`；结构请求 **R3** 就此了结（见下） | ARCH-023 R1 原文「MySQL 承载：af_* 或新表签名列 + 版本行」；归档壳 PLAN-020 已把它登记为「唯一潜在第六表来源」，本壳准入自查表已判「预留位继续空置、独立评审不并轮」；本节补的是**形状结论**（与解析版本同构、甲案、**本轮建列但不建写者**，行语义随检索面消费方来） |
| 知识图谱（roadmap 第 3 条，ARCH-012） | 暂不做（PLAN-009 非目标节） | **关系面足够**（`af_kp.links`）；定义／定理／公式的语义实体分类是**域值扩张请求**（`role`／`type` 闭集扩值须评审），不是新表 | 实体分类不命中入库三判据第②条（无唯一约束需求）；关系边表的转表判据已登记 |
| 数学解析器（roadmap 第 4 条，ARCH-009／ARCH-012） | 未立项 | **足够**：逐块验证结果属产物（`blocks.json`），页级聚合落 `af_pages.quality`，未定档度量先进 `af_pages.meta` | 入库边界——验证结果是**派生量**，可由产物与规则重算 |
| 学习交互闭环（roadmap 第 5 条，ARCH-012） | 未立项 | **不属本组件存储**：本方只供给检索与知识点结构；对话／图片问答／练习／学习进度归根仓库与书目侧 | 前缀即所有权，本壳只管 `af_*` |
| 解析面优化（ARCH-021 的 P1 重跑判定签名与受理门、P2 就绪口径 DB 单源、P3 块策略版本化＋逐块分诊 ledger） | P1／P2 已落地，P3 未排期 | **足够**：P1＝`af_operations.parser_signature`（本案平移），P2＝ADR 0015 的就绪三源，P3 的分诊 ledger 属块级派生量、落产物侧不落 DB | P3 若将来要求「按分诊结论做 SQL 集合检索」即触发块正文表判据，届时独立评审 |
| 页确认门（PLAN-018 W8） | 待真机窗口 | **足够**：`af_pages.confirm_state` 一列 | 本案已给列，PLAN-018 只消费不新增 |
| 管理面一键清除（PM-17） | 已交付 | **足够**：一行 `op_kind='purge'` 流水 | 清除审计不独立张表（状态信号加列不张表） |
| LLM 调用账（KA-23） | 未实现 | **足够且不进 `af_*`**：追加写共享 `qed_llm_calls` | 登记面 A；该表绝不 DROP 重建 |

**结论**：十行里九行判「足够」或「不属本方存储」，唯一承载不了的检索索引版本行已由裁决⑯以「一值一列」
并进 `af_operations`（本轮建列不建写者）。故**四表对本项目核心（文档解析 ＋ 文档知识点分析）与全部
已登记规划能力足够**；第五表**当前无来源**——已具名的那一个已并进四表，其余各线各有触发转建判据，
不再是开放风险。

### 本轮新增的结构请求 R3 与开放裁决 Q5（两项均已裁，落点见裁决⑯／⑮）

| 编号 | 缺口 | 原临时读法（裁决后作废） | 裁决与落地 |
| --- | --- | --- | --- |
| R3 | 检索索引版本行在四表内无落点：`parser_signature` 的输入集是**冻结集合**（引擎名、模型服务 `version`／`protocol_version`、块策略版本），把嵌入侧签名（绑定、模型、维度、服务端点）塞进同一列＝一列两义，属族内红线 | 不建任何索引签名列、`op_kind` 闭集不含索引值 | **已裁＝甲且本轮建列**（裁决⑯，与本节原建议「甲但不在本轮落地」相反）：`op_kind` 扩第八值 `index` ＋ 增**独立**列 `embedding_signature`（不与 `parser_signature` 复用），`af_operations` 26→27 列，本轮**不建索引**（无查询即无索引）。**行语义仍随消费方来**＝索引行的 `job_id`／`status`／`lifecycle` 映射属检索面自身设计，本轮不定；守护断言任何路径写出的非空签名与该 `op_kind` 值计数恒 0（与空置域值同一处置口径）。原建议的理由（新 base 上追加一列是廉价 ALTER）被同一档位抵消：本轮建与日后建成本相同，而日后再动列集合等于把形状重开一轮 |
| Q5 | **扩展空间不对称**：`af_operations`／`af_pages`／`af_kp` 各有一个 `meta` JSON 扩展槽，`af_books` 因裁决②「零改列」而没有。本轮用户要求「留一些扩展空间例如 message 的 json 列」与该裁决直接冲突，不可兼得 | `af_books` 无扩展槽；书目侧要临时读数只能加实体列（走加列评审） | **已裁＝乙**（裁决⑮，与本节原建议的甲相反）：`af_books` 增 `meta`，四表对称，裁决②的「零改列」半句作废。**原建议甲的第一性理由经查证有一半不成立，据实更正**＝理由写的是「`af_books` 的行由同步作业整行 upsert」，而现役 `repositories.py:169-211` 的 `upsert_many` 是**逐列赋值**，其 docstring 明写「只覆盖同步字段、保留本侧进度字段」，故双写者互相抹掉不是必然结果，而是一条可守护的判据。理由背后的**原则**（写者列域必须不重叠，KA-11）仍然成立，它以判据形式随列落地：同步写者不得触碰 `meta`（只需把它加入那份保留清单）、人工写者整列覆写（CW-5）、反向判据＝同步重跑后 `meta` 键集合不变。落点＝「af_books 增列」节 |

### 流程确认（十二步的条文归属与两处随步缺口）

流程正本＝`database-design.md`「流程落点（两模块十二步，每步只有一个判据位）」表：第 1~8 步属解析
模块（条文 `PP-n`／`PM-n`），第 9~11 步属分析模块（条文 `KA-n`，本壳承载、W12 晋升），第 12 步属
运维面（`api.md` 管理面节）。两模块的**唯一接口**是「生效版本的解析产物」：分析模块只读
`af_pages.status='parsed'` 的页与该版本目录下的产物文件，不读 `chunks.json`、不写解析模块任何列
（裁决⑭的反向禁令）。

审计中查出两处随步缺口（此前不在任何工作项清单内）：

- `overview.md:159`「元数据入 DB」的枚举仍是「书目 / 任务 / 页状态 / 人工编辑 / 页级结构意图」，
  重建后应为「书目 / 操作事件与意图 / 页状态与确认 / 知识点结构」⇒ 补入 **W6** 的随步清单。
- `overview.md` 能力矩阵（v0.1）无分析模块行 ⇒ 已在 **W12** 清单内，不重复登记。

### 追溯（本节）

- **结论正文**在 `database-design.md` 的两个 H4；本节只存在飞线编号、原文引用、状态与两处随步缺口。
- **承接**：本节＝**W13** 交付物。两项待裁已由用户当日了结（裁决⑮＝Q5 采乙、裁决⑯＝R3 采甲且**本轮
  建列**），文档面同批落进 `database-design.md`（`af_books` 增列 H4、`af_operations` 27 列清单、扩展槽
  分布段、承载力审计表的甲案行、目标 ER 图）与本壳（「af_books 增列」节、`af_operations` 列清单、
  结论先行、「明确不建」清单）；**代码面全部随 W7**（`AfBook` 与 `AfOperation` 两处 ORM、`op_kind`
  闭集、两条「本轮无写者」守护断言、`upsert_many` 的 `meta` 保留清单用例）。R3 的**行语义**（索引行
  怎么映射 `job_id`／`status`／`lifecycle`）仍属检索面，另立项、不在本壳。
- **本节修正一处此前措辞**：W11 的追溯段曾写「检索与知识图谱线（roadmap）与本篇无交集」。审计后
  该句不成立——检索线的索引版本行确实触及 `af_operations` 的未来列集合，已改为「与本篇**本轮形状**
  无交集，其未来列请求登记为 R3」。

## 准入守则自查与例外登记（对 `af_*` 的两条守则逐条给答）

| 守则 | 本案的答卷 |
| --- | --- |
| 守则 1（新表三判据：身份需跨版本稳定／需唯一约束／需 SQL 集合检索，三者任一且现有表承载不了） | `af_operations`＝①②③全中（意图身份跨版本、`intent_key` 唯一键、事件流按坐标与签名做 SQL 集合检索）；`af_kp`＝①②③全中（节点身份跨版本、`node_id` 唯一、按书/类/态集合检索）。两件各走本案一次合并评审（裁决①把三次加表合成一次） |
| 守则 2（JSON 列反向闸：JSON 列不得承载需要唯一约束或集合检索的身份关系） | **本案故意越闸一次**：`af_kp.links` 承载的是「需要反向集合检索的身份关系」——按对端反查「哪些点以 X 为先修」只能全表扫 JSON。采纳理由＝用户裁决⑥明确要「一张表、简单写关系」，且当前消费现场（展开一册书的图）是正向读。**例外登记 + 转表触发判据**＝一旦出现「按对端反查是主屏读法」或「需要 DAG 完整性集合校验」，`links` 即升 `af_kp_edges`，届时走独立加表评审。这条例外必须写在 `database-design.md` 正文里，否则守则 2 会退化成建议 |
| 归档壳的稳定期裁决（PLAN-020「表族进入只加列不张表的稳定期」「唯一潜在第六表来源＝ARCH-023」） | **明文改判**（W4 新增 ADR 承载）：稳定期是「PLAN-018/019 作用域内无表级需求」的结论，本案是作用域变化（新增知识点模块 + 根侧综合请求），不是原结论被推翻；ARCH-023 的第六表预留位**继续空置**，与本案两张新表无关（R4 择①：独立评审、不并轮）。**裁决⑯不占用该预留位**＝检索索引版本以「一值一列」并进 `af_operations`，本轮零新表，预留位仍是表级空位 |

## 三类登记面与根侧总纲对齐（裁决⑫）

根仓库 `QED-Engine/docs/architecture/database-design.md`（下称**总纲**）是三项目共享库的**全库正本**，
它自己就声明「`qt_*`／`af_*` 两族的列级内容仍在其各自仓库文档，本文只做总览登记与路由」，并在其 6.3
路由表把 `af_*` 的**形状事实源指向本仓这一篇**。所以「其余结构按总纲做」的准确落地是：
**通则与内容维度按总纲，本篇只登记三类 + 做 `af_*` 的列级正本**，不是把总纲抄一份过来。

| 登记面 | 本篇承载什么 | 本篇**不得**承载什么 | 该面的结构正本 |
| --- | --- | --- | --- |
| **A 共享 `qed_*` 四表** | 表名 + 一句话功能 + **我方读写权**（`qed_domain`／`qed_course`／`qed_knowledge_point` 只读不写；`qed_llm_calls` 只写审计行不改结构）+ 同步契约指针 | **零列清单、零索引名**。列级正本在总纲五章；本篇复制一份即造出第二事实源，且总纲 6.4 反向禁止他项目表在本篇之外出现逐列小节 | 总纲三章总览行 + 五章逐列与 DDL + 6.2 同步矩阵（R3：共享表变更先改总纲、通知消费方，再由所有权方执行迁移） |
| **B 需从 QED-Tracker 同步的表结构** | 两组：① **受托建表** `qed_domain`／`qed_course`——所有权与可执行面是 Tracker 的 ORM，本篇只写「我方读哪几列、经什么通道、形状漂移谁负责、两侧任一改动未随步即契约红」；② **`qt_*` 只读消费面** `qt_books`（`af_books` 的快照源）与 `qt_knowledge`，一律经 8901 HTTP，不直连（R2） | 不复制 Tracker 侧列定义与索引名；不声称本仓迁移能建他人表；`af_books` 的快照字段清单**留在 `af_books` 列清单里**（那是我方列，不是对方列），且该快照字段清单原样不动（裁决⑮只追加扩展槽 `meta`，它不属 Tracker 快照域、不由同步作业写） | `QED-Tracker/src/qed_tracker/db/models.py`（受托两表的形状）、`QED-Tracker/docs/architecture/database-private-tables.md`（`qt_*`） |
| **C `af_*` 项目专用四表** | **列级唯一正本**：逐列（类型／可空／默认／键／用处）+ 约束与索引名 + PK 序 + 迁移链与 DDL 语义 + 本仓自定守则（入库边界、新表准入三判据、JSON 反向闸与其例外） | 不声明共享 `qed_*` 与根私有 `qe_*` 的任何形状；不写端点响应形状（→ `api.md`）；不写状态流转判据（→ `design/parsing-management.md`）；不写迁移执行的运维步骤（→ `guides/operations.md`） | 本篇（总纲 6.3 路由行指向本篇） |

**按指针引用、不复制的通则清单**：R1 前缀即所有权、R2 命名空间互不直连、R3 共享表变更先经根登记、
R4 逻辑引用不建跨命名空间外键、R5 只引用不复制、R6 人工位机器不写、**R7 `meta` 三红线**（本案三处
`meta` 列直接引该条，不在本篇重述三条内容）、R9 存储与字符集、R11 敏感字段、7.3 档 A／档 B 档位定义、
7.5 四步迁移窗口、7.9 账本与作业表保留期口径。判据＝同一规则两处成文必漂移，且总纲 R12 已把「正文只写
事实、不写裁决过程」定成全库口径。

**骨架冲突与本案裁决（不擅自改体裁）**：总纲用的是它自己那侧的**八章编号体**（前言／规则／总览／架构／
详细设计／同步契约／变更运维／守护），而本仓 `docs/standards/doc-governance.md`「固定文档骨架与可对账契约」
节由 [ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md) 立法，把 `architecture/` 契约正文的 H2
**白名单穷举**固定为四段（目的与边界／当前契约／失败与兼容语义／验证与追溯），守护＝
`tests/contract/test_fixed_document_form.py:103-106` 的 `test_architecture_documents_follow_four_section_skeleton`
（H2 序列逐等，多一段少一段都转红）。本案因此把「按总纲做」落地为**内容维度按总纲补齐、H2 骨架守本仓 ADR**：
三类登记面（A/B）与「不复制的通则」指针落 `目的与边界`，表清单 + `af_*` 四份列清单 + 同步矩阵落 `当前契约`，
空窗与失败语义落 `失败与兼容语义`，读写对账与守护落 `验证与追溯`。
**备选（当时未采）**＝改成八章编号体：属 `standards/` 实质规则变更，须**先立 ADR**，同批改 `doc-governance.md`
骨架节与 `test_fixed_document_form.py:34` 的 `SKELETON` 白名单，且总纲 6.4 的全库对账判据不受影响——代价大、
收益只是排版同形，本案不采。若用户后续点名要八章，另批立项。

**该备次于 2026-10-22 被用户点名启用（W14）**：用户要求本篇「按 一、前言／二、遵循的规则／三、本项目表总览／
四、整体架构／五、详细设计／六、与 QED-Engine 的交互与同步契约／七、变更与运维／八、守护与验证
重新梳理」，与总纲按章对读的对账收益从此由用户判定而非由本壳判定。按上段的既有路径另批：先立
[ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md)（骨架体裁分治，部分取代 ADR 0008
决定 2／3 的**适用范围**——四段体仍是其余三篇与 `design/` 的默认，八章体只给 `database-design.md`），
再同批改 `doc-governance.md`「固定文档骨架与可对账契约」节与守护白名单（`EIGHT_CHAPTER_SKELETON` +
按体裁取追溯段边界：本篇的一~七章仍是零过程叙述的契约正文段，任务编号与日期只许出现在第八章）。
对账锚点与章号解耦＝改章不改 `### <table> 列清单` 与 `#### METHOD /path` 的形状，列清单仍全部落在第五章。

**跨仓引用写法（本案定档，已于 W4／W6 执行完毕）**：总纲 6.4 定「跨仓引用一律写**纯路径**，不做相对链接、不猜 URL」；
本仓旧口径则是**另一种口径**——用 GitHub URL 引根侧 ADR，在册 **7 处**（`database-design.md`、
`parsing-management.md`、`adr/0002`×2、`adr/0004`×2、`adr/index.md`），`history/` 内另有 **4 处不改**（已归档资料按当时记录保留）。
两口径的代价差得很实在：URL 一切链接守护都放过（`test_markdown_links.py` 只校验仓内相对链接），但根侧纪元整理会把
旧 ADR 整批迁入 `docs/history/adr/v0.1/`——根侧总纲自己的「关联 ADR」行就同时出现 `docs/history/adr/v0.1/0003`
与 `docs/adr/0014` 两种前缀，即路径会变；纯路径不可点，但同机可 `rg` 复算、断链当场可见。W3 采纳纯路径，
W4／W6 已把 7 处全部改完，S7 的复算判据＝`rg -n "github\.com" docs/architecture docs/design docs/adr docs/standards` **零命中**
（W14 复核实测：排除 `docs/history/` 后全仓仅 `tmp/` 内一份备份命中，在册文档零残留）。

**本案 5→4 的根侧同步面**：`af_*` 表数变更＋三表消失＋两表新增＋迁移链 head 改变（总纲 6.3 路由行现记
「线性迁移链（head `20260928_0001`）」，链重置后该读数必须随步）。按总纲 6.2「他项目专用表**表级**变更＝
只同步表信息」行处理：通知根 → 根改其总览行（表名／表族／一句话功能／事实源指针）＋ 4.4 读写矩阵 ＋ 6.3
路由行；**列级细则不同步**（总纲明写「本文不承载其细则设计」）。落 W9，回执内容即本案「承载 REQ-136」节。

## 三表折叠映射（旧列 → 新列，逐张给去向）

### `af_parse_jobs` → `af_operations`（`op_kind='parse'`、`lifecycle='event'`）

| 旧列 | 新列 | 说明 |
| --- | --- | --- |
| `job_id`(PK) | `op_id`(PK) + `job_id` | 同值双列（CW/OB-3）：`op_id` 是事件身份、`job_id` 是让版本作用域读法一条 WHERE 搞定的冗余列。守护单测断言 parse 行 `job_id = op_id` |
| `book_id`/`pages`/`engine`/`status`/`progress`/`failed_pages`/`error`/`source_sha256`/`parser_signature`/`page_count`/`created_at` | 同名列 | 原样平移，取值与写时机不变（PP-17、PM-8/9/15） |
| `started_at`/`finished_at` | **合并进 `progress` + `updated_at`** | 代价：不再能用 SQL 直接算「跑多久」。给出替代读数：`progress.duration_seconds` 由终态写入时算好落列内。反面判据＝若 W4 的真机耗时统计要按 SQL 聚合，则恢复两列（触发即加列，不加表） |
| `ix_af_parse_jobs_book` | `ix_af_operations_book` | 索引改名随表；约束/索引名必须在列清单正文出现（`test_table_constraints_and_indexes_are_documented`） |

### `af_block_edits` → `af_operations`（`op_kind='edit'`、`lifecycle='intent'`）

| 旧列 | 新列 |
| --- | --- |
| `edit_id`(PK) | `op_id`（值形改：`ed_<md5>` → `afop-<hex12>`）；**定位幂等改由 `intent_key` 承担**，`edit_id` 的「确定性摘要即身份」被 `intent_key` 取代（后者同样确定性，且直接挂唯一键）。旧列的历史可达性判例（PM-3：仓储按唯一键读写，迁移前存量行仍可达）随档 B 清库**失效**——本案不承接该判例，因为五表 DROP 后没有存量行 |
| `book_id`/`job_id`/`page_no`/`block_index` | 同名四列 |
| `block_type`/`verdict`/`corrected_text`/`corrected_bbox` | `payload` 内四键（闭集形状单测断言） |
| `note` | 提升到实体列（族内 `note` 口径统一，顺带答 REQ-136 R6 的命名一致性） |
| `edited_at`/`updated_at` | `created_at`/`updated_at`（`edited_at` 语义即「首次编辑」＝`created_at`） |
| `uq_af_block_edits_pos(book_id,job_id,page_no,block_index)` | `uq_af_operations_intent(intent_key)`（等价：坐标集 → 确定性摘要，且顺带把 structure 纳入同一键机制） |

### `af_page_regions` → `af_operations`（`op_kind='structure'`、`lifecycle='intent'`、`job_id=''`）

| 旧列 | 新列 |
| --- | --- |
| `book_id`/`page_no`/`region_id`(三列 PK) | `book_id`/`page_no`/`target_id`（`region_id` 的几何派生式**原样不动**：`rgn_<md5(book:page:op:identity-geometry)[:12]>`），当前态定位改由 `intent_key` |
| `op`/`bbox`/`anchor_bbox`/`members`/`kind` | `payload` 内五键（`op` 三值闭集 add/delete/merge 不变） |
| `note`/`created_at`/`updated_at` | 同名列 |
| `ix_af_page_regions_page(book_id,page_no,op)` | `ix_af_operations_version(job_id,page_no)` **不等价**：索引不再含 `op`。替代判据＝structure 行恒 `job_id=''`，按 `(book_id,page_no)` 读全部意图行再在内存分 `op`；若 W7 实测该读法成为热点，加 `ix_af_operations_page(book_id,page_no)`（加索引，不加表） |
| 「主键不含 `job_id`＝跨版本存活」（ADR 0010 决定 1） | **不变式原样保留**：structure 行的 `job_id` 恒 `''`，守护单测断言「`op_kind='structure'` 行的 `job_id` 全为 `''`」——这条是 ADR 0010 在折叠后唯一的落点，必须在 W4 同步进 ADR |

## 档 B 重建批次（四件必答逐条给答）

| 问 | 本案答案 | 依据／代价 |
| --- | --- | --- |
| ① 作业行（版本自证史）怎么处置 | **随 DROP 一并清除，不迁移**：现有 `af_parse_jobs` 与 `af_block_edits`/`af_page_regions` 的全部行都是「可再生产物 + 未积累人工结论」的组合，且 8903 的对照/编辑链路尚未有真实人工使用量（现状读数见「事实错更正」节）。清理前必须 `mysqldump` 逐表备份并按 sha256 留档 | 用户前提＝「旧表结构乱、且没有重要数据」；备份红线＝AGENTS.md 的 D 类要求 |
| ② `af_pages` 的回归路径 | **不回填**：DROP 后 `af_pages` 为空，读取面纯 DB 驱动（af_books 空表即空列表/404，不回落文件系统），因此页行的唯一恢复路径＝重新 ingest + 重新解析逐本重建。不提供「从盘上产物反向灌页行」的脚本——那会让 DB 行成为产物的派生物，与 ADR 0015「DB 是就绪唯一事实源」反向 | 判例＝`parsing-management.md` 的「读取面纯 DB 驱动」句；代价＝重建窗口内的算力与时间在飞 |
| ③ 空窗对 8900／8903 的可见后果 | 空窗期＝8902 停服 → `DROP`/`CREATE` → 起服。期间：`GET /books` 返回**空列表**（不是报错，因读取面纯 DB 驱动）、书目详情/页清单/分块 404、8900 的 `axiom` 透传层把 404 如实上抛、8903 显示「暂无书目」空态；`POST /books/sync` 在 8902 起服后立即可用且幂等（PM-2）。**必须有可见提示**：窗口开始时向运维留一行「af_* 已清空，逐本重新 ingest + 解析前解析管理面无数据」 | 判据＝PM-2 幂等 + 空表空列表；不粉饰成「无感知」——这是数据清空窗口，用户可见 |
| ④ `legacy` 哨兵的收法 | **保留为页行作用域哨兵、不再作版本行**：`af_pages.job_id` 仍允许 `'legacy'`（根视图存量产物），`af_books.active_job_id IS NULL` 仍是根视图判据；但 `af_operations` 里**不出现** `job_id='legacy'` 的行（事件行必须有真实 `op_id`），重建后新写入直接落版本目录，根视图只在「已 ingest 未解析」阶段被读。终局＝当所有书目都至少有一个版本行时，`legacy` 只作为 `af_pages` 的取值域残留，不再有写入方 | 现状是双哨兵（NULL 指针 + `'legacy'` job_id）；本案把它们分工明确化而不是合并（合并＝用一列同时表达「哪一版生效」与「页行属哪个作用域」，两个事实共用一处必漂移；裁决②原禁改 `af_books`，裁决⑮只放开扩展槽一列，都不构成合并理由） |

**迁移链重置方案**（推荐，另一案被拒）：

- **采**＝删除现有 6 个迁移文件（`20260917_0001` … `20260928_0001`，git 历史与 `docs/history/` 留档），
  新写一条 base 迁移 `20261022_0001_af_family_rebuild`（`down_revision = None`）一次建齐 4 表；
  `tests/unit/test_db_migrations.py` 的逐 revision 步进测试整体重写为「base 建全族 + 列/键/索引逐张对账 +
  downgrade 干净且可重复」；业务库 `qed` 的 `alembic_version` 在 D 类窗口内先 DROP 后重建（`stamp` 不需要，
  新 base 直接建）。
- **拒**＝保留旧链、追加一条 DROP+CREATE 迁移。被拒理由＝旧链的每一条都在为**不存在的形状**跑 DDL 与
  断言（测试库夹具每次从 base 重建会先建旧五表再删），且 `test_db_migrations.py` 的逐 revision 测试
  无法原样保留（它们 INSERT 旧表名）。这是「兼容负担假装便宜」的形态，档 B 的存在意义正是取消它。
- `downgrade` 必须是**可重复**的（DROP 4 表 → upgrade 回同形），因为 `qed_test` 由集成夹具每次从 base 重建，
  新表的全部 MySQL DDL 在门禁里就被真实执行一次；`qed` 库上的执行另批 D 类批准。

## 改动面清单（W7 代码批要动的每一处，带行锚）

**行锚口径**：本表是**施工前**的改动面清单，行锚按当时文件读写。W4／W6／W7／W14 执行后代码与文档行位已变，
尤其 `database-design.md` 在 W14 改用八章编号体，其行锚一律按**节名**复核（三类登记面＝一、前言，表清单＝三、
本项目表总览，关系视图与跨族主线＝四、整体架构，四份列清单＝五、详细设计，同步面＝六、交互与同步契约，
迁移序列与准入守则＝七、变更与运维）。

| 面 | 位置 | 必须发生的事 |
| --- | --- | --- |
| ORM 与注册 | `src/axiom_flow/db/models.py:24-68`（`AfBook`）、`:71-101`（`AfParseJob`）、`:121-144`（`AfBlockEdit`）、`:147-181`（`AfPageRegion`）、`:184-190`（`AF_TABLES`） | 三表类删除、`AfOperation` + `AfKp` 新增、`AF_TABLES` 收为 4 元组；`AfBook` **增一列** `meta`（裁决⑮，现役各列逐列不动、不新增索引）；`AfOperation` 含 `embedding_signature` 与 `op_kind` 第八值 `index`（裁决⑯，两者本轮**无写者**）；`db/__init__.py:10-42` 的导出集随步（现状已不对称：`AfPageRegion`/`RegionRepository`/`RegionIntent`/`region_id_for` 未导出） |
| 仓储 | `db/repositories.py`：`BookRepository.upsert_many:169-211`、`ParseJobRepository:309-490`、`RegionRepository:583-667`、`EditRepository:668-764`、`region_id_for:127`、`edit_id_for:139`、`_geometry_key:105`、`ParseJobRow:55-74`、`RegionIntent:89-102` | `upsert_many` 的「保留本侧字段」清单**加 `meta`**（裁决⑮的判据①：同步写者不得触碰该列；现役已是逐列赋值，故这是清单加名而非改写形态）；三个仓储类合成 `OperationRepository`（`append_event` / `upsert_intent` / `replace_page_intents`（锁内）/ `list_events` / `find_in_flight` / `mark_running` / `mark_finished` / `reconcile_interrupted` / `count_active` / `delete_for_book` / `delete_all`）；`EditRepository`/`RegionRepository` 退为 `OperationRepository` 的意图读法；新增 `KpRepository` |
| API | `api/main.py:102-145`（`_edit_record`/`_region_record`）、`:232-250`（`_read_scope`）、`edit_block:464-488`、`put_structure:494-581`、`list_versions:592-611`、`create_parse_job:639-708`、`list_parse_jobs:710-752`、`get_parse_job:754-769`、`list_page_states:390-418`、`delete_book:298-318`、`ingest:347-375` | **端点 URL 与响应形状不变**（`/parse-jobs` 等仍返回同一形状），字段来源改读 `af_operations`；`put_structure` 的读现有行改意图行读法并进 CW-4 锁 |
| 管理面 | `api/admin.py:50`（`ACTIVE_JOB_STATUSES`）、`:55`（`_AF_TABLE_MODELS`）、`:57`（`_ROW_COUNT_FIELDS`）、`:127-162`（计数与删除）、`:279-308`（`purge`） | `_AF_TABLE_MODELS` 与 `AF_TABLES` 两处独立书写必须同批改（governance `:71` 用 AST 对账）；`AdminPurgeCounts` 字段名＝表名，随 `AF_TABLES` 重排 |
| 编排 | `orchestrator/pipeline.py:171-184`（`_region_frames`）、`_fail:347-376`、`run_parse_job:430-758`（含 `mark_running:512`、`record_source_snapshot:518`、`set_active_job:741`、`mark_finished:752-758`）、`orchestrator/jobs.py:69-78`、`:85-108`、`orchestrator/signature.py:1,95,100-108`（`VersionIdentity`） | 写入口随仓储改名；**语义判据一字不动**（PM-11 齐备与激活同源、PM-12 启动期归一、PP-13 产物写先于页行提交） |
| schemas | `schemas.py:69`（`JobStatus`）、`:100-105`（`RegionOp`）、`:401-416`（`EditRecord`）、`:418-432`（`ParseVersionOut`）、`:435-441`（`EditRequest`）、`:447-501`（`BlockLocator`/`MergeGroup`/`StructureRequest`/`RegionRecord`）、`:504-515`（`PageStateOut.edit_count`）、`:553-579`（`ParseJob*`）、`:614-625`（`AdminPurgeCounts`） | 对外模型保持可向前兼容的字段名（`job_id` 等），新增 `render_profile`/`artifacts`/`confirm_state`/`section_kind` 的**读取侧**字段随 W5 晋升；`RegionRecord`/`EditRecord` 是否改名由 W5 定（倾向不改：响应形状属契约） |
| 迁移 | `migrations/versions/` 全部 6 个文件（见上「链重置」） | 删旧链 + 一条新 base；`20260928_0001` 的 `parser_signature` 加列被吸收，PLAN-019 W2 的落库面在本案里**改判为形状的一部分**（不是回退，是合并） |
| 测试夹具 | `tests/support/mysql.py:30,36,116-117`（`OWNED_TABLES` 由 `AF_TABLES` 派生 + DROP 名白名单断言） | 随 `AF_TABLES` 自动收窄；**ADR 0005 的白名单句必须同批逐名枚举新表** |
| 守护（会红清单） | `tests/unit/test_db_table_family_governance.py:48,71,81,92,104,120`；`tests/contract/test_fixed_document_form.py:270,291,296-307`（`### <table> 列清单` 标题 + 约束/索引名 + PK 序）；`tests/contract/test_architecture_documents.py:120`（**硬编码 4 表名，漏 `af_page_regions`**）；`tests/contract/test_code_document_mapping.py:91,108`；`tests/unit/test_db_migrations.py:60,71,106,121,236,277,284,461-482,494,528`；`tests/unit/test_db.py:75-81`（硬编码 5 表名）；`tests/unit/test_db_page_regions.py:55`（`AF_TABLES[-1] is AfPageRegion.__table__`）；`tests/contract/test_api_v1_contract.py:1918-1921,1988`（purge 计数字典硬编码三表名） | 逐条同批改写。**行为用例不减少**：区域幂等/缺席即撤销/超限判 400/legacy 拒绝、编辑覆盖与版本作用域互不干扰、启动期归一、purge 闸门——每条从「旧表断言」改挂「新表 + `lifecycle`/`intent_key` 断言」。**本轮新增三条**（裁决⑮⑯）：① 非空 `embedding_signature` 计数恒 0、② `op_kind='index'` 计数恒 0（两条＝「建列不建写者」的守护，撤掉即无人发现检索面被偷偷接线）、③ 同步作业重跑后先前写入的 `af_books.meta` 键集合不变（把 `upsert_many` 改成整行覆写即转红） |
| 文档 | `database-design.md`（**结构改造**＝`目的与边界` 内新增三类登记面（A 共享 `qed_*`／B Tracker 同步面／C `af_*` 正本）并把 R1~R12 类通则改为指针、`:14` 的跨仓 GitHub URL 改纯路径（另 6 处随 W6，见「跨仓引用写法」）；**形状随步**＝表清单 `:44-52`、mermaid `:65-111`、三份列清单 `:156-192`/`:213-235`/`:237-262`、`af_pages:194-211`、迁移序列 `:264-279`、`AF_TABLES` 改动面段 `:301-304`、准入守则 `:33-37`、入库边界 `:31-32`）；`parsing-management.md` PM-3/5/6/8/9/10/11/16/17/18（`:100-140`）；`parsing-pipeline.md` PP-14/17/21（`:212-229`）；`api.md`（13 处表名引用）；`code-map.md`；`operations.md:110`（mysqldump 逐名五表）；`adr/0005:42-44`（五表白名单）；`adr/0010`（10 处） | 全部随步，逐处不留「旧表名 + 新表名」并存的过渡措辞；H2 骨架在 W4／W6 时按 ADR 0008 的四段体**保持不变**，2026-10-22 由 W14 按用户点名改判为八章编号体（[ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md)），该行的「不动」口径已被 W14 取代 |

## 事实错更正（本案查证到的三处，随 W3/W4 同批改）

| # | 现状陈述 | 实读 | 处置 |
| --- | --- | --- | --- |
| ① | `database-design.md` 迁移序列节（W14 后＝「七、变更与运维」内的迁移序列段）自陈「业务库 `qed` 停在 `20260926_0001`」 | `project-status.md` 记 2026-10-03 D 类窗口已升 head `20260928_0001`，根侧只读探针实测 `qed` 与 `qed_test` 两库 `alembic_version` 俱为 `20260928_0001` | 落后的是**陈述不是库内指针**。档 B 落地前该段按实读更正；W7 定稿已整段重写为「链重置为单条 base `20261022_0001`＋本篇不记录库内 head 读数（随窗口变，读数归 `project-status.md`／`operations.md`）」 |
| ② | 工作区未提交段（`database-design.md` 目的与边界节）自陈本仓库「local 模式直写」`qed_llm_calls` | `src/` 内对任何 `qed_*` 共享表的引用**计数为 0** | 陈述超前于实现。改为「按共享表契约写入，实现状态＝未实现（待立项）」，并挂 REQ-136 R3 的覆盖面核对项。**注**：该段是并行会话的未提交 WIP，本壳只登记更正判据，不代改 |
| ③ | `parsing-management.md` PM-3 的实现落点写 `BlockEditRepository.upsert` | 实际类名 `EditRepository`（`repositories.py:668`），且不存在 `BlockEditRow`/`RegionRow` | W6 随步改正；顺带答 REQ-136 R6 的命名一致性（`note` 单数在本案新表统一） |

## 目标与成功标准

**目标**：把「af_ 表族要怎么改」一次裁死成**可施工的文档事实源**——四张表的逐列形状、事件与意图的
判据分工、并发写入的七条判据、档 B 的重建批次与空窗后果、以及对自家两条准入守则的明文改判，
全部落在本壳与晋升后的 `database-design.md`，使 W7 代码批拿到形状即可施工、无需再问任何设计问题。
**形状入篇口径＝暂定**（裁决⑬）：目标形状在实现前不得当作已确认事实源引用；**结构先行**（裁决⑭）——
先把这套形状梳理清楚，分析模块（知识点梳理）的设计篇再以它为输入。

| ID | 成功标准（可检验） |
| --- | --- |
| S1 | `af_*` 表族的**目标形状＝4 张**进入 `database-design.md` 的表清单与目标列清单：逐列给类型/可空/默认/键/用处，约束与索引名在正文出现。目标未实现 ⇒ 列清单以 H4 挂在「目标形状（暂定，未实现）」内、置于首个现役 `### af_books 列清单` 之前（避开 `_column_rows` 的扫描边界），**列清单↔ORM 与约束索引两条对账随 W7 实现同批转绿**；现役五表的 `### <table> 列清单` 逐字保持为代码事实，PK 序与 ORM 一致 |
| S2 | **折叠映射逐列有去向**：`af_parse_jobs`／`af_block_edits`／`af_page_regions` 的每一旧列在「折叠映射」节被指名接收（同名列、`payload` 键、`intent_key`、合并进 `progress`、或明文放弃 + 替代读数），零「其他/待定」 |
| S3 | **并发判据 CW-1~CW-7 各挂一条会红的反向判据**：唯一键 upsert 的双会话用例、整批声明的行锁用例、parse 行只向更终回写的用例、`af_kp` 的 `if_rev` 409 用例，全部在 W7 落地并挂到 `lifecycle`/`intent_key` 列上 |
| S4 | **两条守则的改判与例外有正文**：稳定期裁决与 ARCH-023 第六表预留位在新增 ADR 里明文处置；`af_kp.links` 越 JSON 反向闸的例外与**转表触发判据**写进 `database-design.md` 的守则节（不是脚注） |
| S5 | **档 B 四件必答逐条给答**：作业行处置、`af_pages` 回归路径、空窗对 8900/8903 的可见后果、`legacy` 哨兵终局，外加迁移链重置方案（采/拒各一句理由）与 D 类窗口的备份/回滚判据 |
| S6 | **台账与回执闭合**：todo 有 PLAN-024 一枚镜像行并承载 REQ-136（单行，ADR 0011），REQ-123／REQ-129 两行按并案口径处置并在本壳留下处置记录；三处事实错按实读更正 |
| S7 | **三类登记面成立且不复制**：`database-design.md` 的 H2 等于该篇体裁的骨架白名单（W14 后＝八章编号体，守护转红即违 ADR 0008 决定 2 与 ADR 0017），但正文含 A／B／C 三个登记小节——共享 `qed_*` 与 `qt_*` 小节内**零列清单、零索引名**（逐小节核对），全库通则只以指针出现（R1~R12 在本篇不复述条文），跨仓引用改纯路径（现存 **7 处** URL 全部改完，`rg` 读数＝`architecture/`/`design/`/`adr/` 三处零命中）；表级变更（5→4 与新迁移 head）已按总纲 6.2 通知根并留下回执编号 |
| S8 | **两模块边界可指认且暂定口径不漂移**：`database-design.md` 的目标形状节逐表标明解析／分析归属，并写清「分析模块在现役库内无表」；`确认状态＝暂定` 与三项转正评审项在 header＋blockquote 双处可读、未新增元数据键；分析模块的写入路径与消费通路在 W11 设计（按 ADR 0002 先在本壳承载，晋升＝W12），不回改解析表形状 |
| S9 | **分析模块通路自洽到可施工**：批次三态（在飞／收尾／中断）只由 `gen_status` 表达且改写方向唯一，三写者的列域不重叠，确认与外发是两个各自成立的判据，锚点按取证版本解释、失效可见不改写；两处结构请求与四项开放裁决**已逐条了结并各有一条条文承载**（R1→`KA-28`、R2→`KA-27`、Q1→`KA-24`、Q2→`KA-25`、Q3→`KA-26`、Q4→「目标 6」），13 条验收判据每条给会红形式；正文已晋升为 `docs/design/knowledge-analysis.md` 并被四道文档守护穷举 |
| S10 | **当日裁决（⑮⑯⑰）落进形状且代价可守**：`af_books` 增 `meta` 后四表扩展槽对称，同步写者与人工写者列域不重叠且有会红用例（同步重跑 ⇒ 先前写入的 `meta` 键集合不变）；`af_operations` 的 `embedding_signature` 与 `op_kind='index'`／`'kp'` **建列（值）不建写者**，四条「计数恒 0」守护断言随 W7 落地并在 W8 收尾批补全（① 非空签名 ② `op_kind='index'` ③ 其余五个无写者值＝`confirm`／`ingest`／`sync`／`purge`／`kp` ④ `GEN_STATUSES` 之外的域外值；③ 的反向判据＝把任一条生产插入腿的 `op_kind` 改成 `sync` ⇒ 断言转红，已实机复验并按 sha256 还原 `repositories.py`；④ 的反向判据＝把任一 `af_kp` 写入口的域值改成被删除的旧值 ⇒ 转红），且索引行的行语义（`job_id`／`status`／`lifecycle` 映射）在本壳与权威文档里都**仍未被定义**——定义它属检索面另立项，本轮任何一篇写出映射即越界 |

## 范围与非目标

- **范围（本壳与 W3~W6、W11、W13、W14）**：四张表的逐列设计与判据；三表折叠映射；并发写入判据；知识点单表的
  关系形状与例外登记；保留与裁剪判据；档 B 重建批次与迁移链重置方案；`database-design.md` 的
  **三类登记面重构**（按根侧总纲的内容维度、按裁决⑬转暂定；章体后经 W14 改为根侧八章编号体，
  立法＝[ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md)）；**解析／分析两模块的
  边界与承接顺序**（裁决⑭，W11 设计篇）；**承载力审计与流程落点**（W13：roadmap 四条与在飞三线逐条
  给存储判定、扩展槽分布（裁决⑮后四表对称）、唯一未来结构请求的落法（裁决⑯＝甲且本轮建列）、
  十二步流程的判据位归属）；
  `api.md`/`code-map.md`/
  `parsing-management.md`/`parsing-pipeline.md`/ADR 的随步；改动面清单与会红守护清单。
- **非目标**：
  - **任何 `src/` 改动、任何迁移、任何库内 DDL**（W7/W8 各自另批，且 W8 属 D 类：备份 → 用户批准 → 执行）。
  - 8903 前端界面与 8900 消费侧实现（根侧另批；本案只到契约形状）。
  - 检索配方与向量库（ARCH-023；`af_kp` 不是它的替代品，向量正本仍不进 MySQL）。
  - 知识点长文的生成质量与提示词工程（本案只规定「结构+摘要入库、长文落数据根」的存储分工）。
  - 共享 `qed_*` 四表与根私有 `qe_*` 七表的任何**结构定义**（前缀即所有权；本篇只按登记面 A 写
    「表名＋我方读写权＋正本指针」，不复制其列与索引；`qed_llm_calls` 只声明「绝不 DROP 重建」）。
  - 块正文入库（`af_page_blocks`）与关系边表（`af_kp_edges`）——各有**触发转建判据**，本案不采。
  - 真实数据的跨表迁移脚本（档 B 清库重建，不迁移）。

## 前置条件

1. **用户评审本壳形状**（W2 停顿点）：目标 4 表与 `af_parse_jobs` 被吸收已随裁决⑪的**方向确认**定档；
   仍待逐条点头的是 `af_kp.links` 越 JSON 反向闸、迁移链重置两处。任何一处被否，后续批次不开；
   方向确认**不构成**「开始执行」——W7 代码批与 W8 真机窗口的批准门各自独立。
2. **并行窗口协调**：当前工作树有并行会话的未提交 WIP 覆盖 `database-design.md`、`api.md`、
   `parsing-pipeline.md`、`orchestrator/pipeline.py`、`tests/*`（PLAN-018 W3）。W3 动 `database-design.md`
   前必须确认该批已提交或已合入，否则同文件冲突；本壳不代改他人 WIP。
3. **PLAN-019 的口径衔接**：W2 已落地的 `parser_signature` 加列迁移（`20260928_0001`）在档 B 下被吸收进
   新 base；PLAN-019 的 C1（签名输入集冻结、哨兵 `''` 三条边界）、C2（受理门 + 原子性判据）语义**不变**，
   只是落表变了。C2 的行锁判据与本案 CW-4 同形，共用同一实现与同一用例（不另造第二套锁）。
4. **PLAN-018 的 W8 衔接**：页确认门落点＝`af_pages.confirm_state`（本案已给列）；`af_page_regions`
   折叠后，PLAN-018 的 PM-18 不变式（PK 不含版本、跨版本存活、缺席即撤销）必须在 `af_operations` 上
   继续成立。本案不实现确认门，只保证「有列可写、有判据可守」。
5. **晋升纪律**：新列与新响应字段在挂载前不得进 `api.md` 端点总表与契约测试
   （`test_api_endpoint_table_matches_runtime_routes` 双向对账）；草案先留本壳，随实现同批晋升。
6. **表数同步面**：`AF_TABLES` + 表清单 + 四份列清单 + ADR 0005 白名单逐名枚举 + 一个迁移，
   一处不漏；`AdminPurgeCounts` 字段名与 `_AF_TABLE_MODELS` 由守护点名（现状是两处独立书写）。
7. **真机面**：`qed` 与 `qed_test` 的现状读数（两库 head `20260928_0001`）是档 B 窗口的起点；
   `qed_test` 由集成夹具每次从 base 重建，故新表的 MySQL DDL 在门禁里就被真实执行一次。
8. **与根侧总纲的分工（裁决⑫的实读依据）**：总纲现记 `af_*`＝5 表，且其 6.3 路由行把 `af_*` 的
   形状事实源**指向本篇**、其 6.1 明写「`qt_*`／`af_*` 的列级正本在各自仓库文档」。所以 W3 只改本篇，
   总纲的总览行／4.4 读写矩阵／6.3 路由行（含「head `20260928_0001`」读数）由**根侧**按表级通知随步（W9）；
   本仓库不改根仓库文件（AGENTS.md 工作区边界，口头指令不豁免）。
9. **分析模块的晋升前置（W12，六项已全部了结）**：W11 设计节里两处**结构请求**（R1＝`gen_status` 的
   `adopted` 无合法写者、R2＝`op_kind` 闭集缺承载梳理流水的值）与四项**开放裁决**（Q1 触发方、Q2 批次粒度、
   Q3 未归档池收敛责任、Q4 「并发核心重点」的原始所指）由用户于同日逐条给裁（裁决⑰⑱），故设计正文已整段
   晋升为 `docs/design/knowledge-analysis.md`、本壳该节只留指针与沿革。**了结的连带后果**＝R1 使域值**收窄**、
   R2 使闭集**扩第九值**，两处都改在应用层常量上 ⇒ 零 ALTER、零新迁移；代码与守护同批改完，
   「不得写出 `adopted`」的旧断言改判为「不得写出 `GEN_STATUSES` 之外的值」。
10. **形状定档前置（W13 新增，两项已了结）**：**Q5** 与 **R3** 都改列集合，未了结即动码等于把形状再开
    一轮。用户已裁决（⑮＝`af_books` 加 `meta`；⑯＝检索索引签名并进 `af_operations` 且**本轮建列**），
    文档面同批改完，故 **W7 的形状输入现在是封闭的**＝四表逐列形状以三份目标列清单（`af_operations`
    27 列、`af_pages` 现役 PK ＋ 新增四列、`af_kp` 23 列）加「af_books 增列」一节为准，W7 不得自行
    增删列或域值；实现中若发现形状不足，回本壳登记为新的结构请求（R4 起）并等裁，不擅改。

## 工作项

| # | 工作项 | 依赖 | 状态 |
| --- | --- | --- | --- |
| W1 | 本壳入册（四表形状 + 折叠映射 + 并发判据 + 档 B 批次 + 改动面清单）；todo 建 PLAN-024 一枚镜像行并承载 REQ-136，REQ-123／REQ-129 两行按并案口径处置；治理契约绿 | — | 已执行 |
| W2 | **停顿点**：用户评审四处（4 表形状 / 吸收 `af_parse_jobs` / `links` 越闸 / 迁移链重置）；逐处采否在此了结 | W1 | 部分通过：裁决⑪确认**方向**（前两处随方向定档），后两处待逐条点头；「开始执行」另属 W7 门 |
| W3 | `database-design.md` 晋升（结构对齐与形状晋升**合一批**，同一文件不拆两次改）：**结构**＝按裁决⑫落三类登记面（A 共享 `qed_*` 只登记不复制、B Tracker 同步面给「读哪几列＋通道＋漂移责任」、C `af_*` 列级正本），通则改按指针引用总纲、跨仓 URL 改纯路径；**形状**＝现役表清单保持五表逐字为代码事实，另立「目标形状（暂定，未实现）」节承载 4 张表 + 两模块归属 + mermaid ER + 四份目标列清单（H4，含约束/索引名与 PK 标记）+ 入库边界与准入守则的「本案例外与触发判据」正文 + 整族重建档位；**状态**＝按裁决⑬改判 `确认状态＝暂定`（原因与转正评审项写 blockquote，不加元数据键）、`实现状态＝In Progress`；**迁移序列**＝本篇正文不再自陈库内 head 读数（唯一读点＝`project-status.md`/`operations.md`，此前自陈值属漂移，故**删除而非改写**——事实错①就此永久修正；实读探针属人工窗口，本壳零代码零 DDL 故不需要）。列↔ORM 对账随 W7 转绿（S1）。**H2 骨架保持本仓四段**（见「骨架冲突与本案裁决」） | W2 | **已执行**（2026-10-21，暂定形状入篇；并行 WIP 的共享 `qed_*` bullet 已收进登记面 A 并更正其事实错，未删除他人工作；其余 6 处跨仓 URL 随 W4/W6） |
| W4 | 新增 ADR（承载四件事：四表形状与事件/意图分工、稳定期与 ARCH-023 预留位的明文改判（含裁决⑯＝索引版本以「一值一列」并进 `af_operations`，预留位仍是表级空位）、`links` 越闸的例外与转表判据、**两项当日裁决的代价条款**（⑮ 双写者列域不重叠、⑯ 建列不建写者与「计数恒 0」守护））；`adr/0005` 白名单与 `adr/0010` 不变式随步；`adr/0002:10,36`／`adr/0004:19,54`／`adr/index.md:11` 五处跨仓 URL 改纯路径 | W3 | **已执行**（2026-10-22：ADR 0016 立稿 Accepted、取代＝无；0005 白名单逐名四表、0007 `failed_pages` 承载改判、0010 决定 1/2 加承载随步、0015 就绪源改 `af_operations` parse 行；跨仓 URL 全改纯路径） |
| W5 | `api.md` 随步：端点 URL 与响应形状不变、字段来源改 `af_operations`；新增字段的读取侧形状按前置 5 晋升；导出端点只声明「必须存在 + 只读 + 管理面挂载纪律」 | W3 | **已执行**（2026-10-22：端点 URL 与响应形状零改动，字段来源全改 `af_operations`／`af_pages`，退役表名残留 0） |
| W6 | `parsing-management.md`（PM-3/5/6/8/9/10/11/16/17/18 换承载表不换语义 + PM-3 类名事实错更正 + `:9` 跨仓 URL 改纯路径）与 `parsing-pipeline.md`（PP-14/17/21 表名随步）、`code-map.md`、`operations.md` 的 mysqldump 逐名表清单、`overview.md:159`「元数据入 DB」的枚举改四表口径（W13 查出的缺口） | W3/W4 | **已执行**（2026-10-22：PM／PP／MI 条文换承载不换语义＋三处事实错更正、`code-map.md` 按解析／分析两模块登记、`operations.md` runbook 改写为档 B 重建窗口与四表逐名清单、`overview.md` 两处缺口补齐；同批给 PLAN-018／PLAN-019 两枚在飞壳加「承载随步」注记与 ADR 0016 关联，其历史落地记录按当时口径保留不回改） |
| W7 | **代码批（需用户说「开始执行」）**：ORM/`AF_TABLES`/仓储合成/`KpRepository`/api 读源/admin/schemas/orchestrator 随步 + 迁移链重置 + 守护改写 + 行为用例逐条从旧表断言改挂新表（按「改动面清单」的会红清单逐条销） | W6 | **已执行**（2026-10-22，用户当日放行「现在开始正式代码改动、数据库改动」；`AF_TABLES`＝四表、`OperationRepository` 合成三表面并带 `intent_key` 幂等与 `_lock_book`、`KpRepository` 落形不接公开端点、迁移链重置为唯一 base `20261022_0001`、`api`/`admin`/`schemas`/`orchestrator` 改读源、守护与行为用例逐条改挂新表；门禁 704 passed / 10 skipped + ruff clean。**形状未扩**：零新增列、零新增域值，R4 未触发） |
| W8 | **D 类重建窗口**：逐表 `mysqldump` 备份并留 sha256 → 用户批准 → 停服 → DROP 五表 + 新 base 迁移 → 起服 → 起服 → 真机读数留档（空窗可见后果按四件必答③实测）；原计划的「逐本重 ingest/重解析」按本轮用户裁决**不在窗口内** | W7 | **已执行**（2026-10-22，D 类纪律全程遵守＝备份先行 → 批准 → 完整差异复核。执行驱动由用户当日改判「现在进行下一步，你帮我执行」接回本会话。两库按 C-1~C-7 走完：C-2 `mysqldump`＋逐表 `SHOW CREATE TABLE` 双份留档（sha256 前缀 `29bb119e…`／`519d4da0…`）→ C-3 `tmp/w8_rebuild.py qed qed_test` **只** DROP 自有 `af_*`（含退役三表）与 `alembic_version`、**未 `DROP DATABASE`** → 唯一 base `20261022_0001` 建四表 → C-4 两库各 `VERDICT=PASS`＝`af_*` 恰四张、库内版本＝base、四表行数全 0，真库列数逐张与设计篇列清单相等（22／27／12／23），`af_operations` 索引实读＝`PRIMARY` + `uq_af_operations_intent` + `ix_af_operations_book`/`_version`/`_sig`；**附带面零受损**＝非 `af_*` 表集合与 DROP 前逐名相等且行数不变（`qed` 9 张、`qed_test` 20 张含 `qe_*` 学习面镜像），判据脚本 `tmp/w8_collateral_check.py` → C-5 空窗读数 11 条 GET（`GET /books`＝200 `[]`、书目详情 404「书目不存在」、manifest/pages/page/chunks 404「书目不存在或产物缺失」、`/versions` 404、`GET /parse-jobs`＝200 `{"jobs":[],"total":0}`、未知 `job_id` 404）⇒ **四件必答③就此了结：空窗对 8900/8903 是空态而不是故障** → C-6 起服只读冒烟（`/health` 200）→ C-7 `books/sync` 回灌 17 本：首趟 `synced=17, updated=0`、同批重放 `synced=0, updated=17`（PM-2 幂等成立），回读 17 本全 `parse_status=pending`／`active_job_id=NULL`／`page_count=NULL`／`ingest_status=none` ⇒ 只恢复书目面，页行与版本须重新 ingest＋解析。**并发判据在真 MySQL 层补齐**＝新增 `tests/integration/test_concurrency_criteria.py` 4 例（CW-3 同坐标并发声明经行锁排队后坍缩为一行且保留后写值／事件行不入 `intent_key` 唯一键、CW-4 二发页声明在 `_lock_book()` 的书行锁上阻塞至 `1205`、CW-6 `rev` 冲突判的是最新已提交行而非本会话快照），`QED_TEST_MYSQL=1` 层 **12 passed**；对三处被守护机制逐个撤除做**变异复验 3/3 转红**，随后按 sha256 逐字节还原。留档＝`tmp/window-20261022/`，文档随步后的收尾复跑＝默认门禁 **705 passed / 14 skipped** + ruff clean、固定文档七道守护 **73 passed**（`QED_TEST_MYSQL=1` 层的 12 passed 是窗口内读数，复跑会经夹具 teardown 清空 `qed_test`，故不在收尾时重跑）；运维口径与两条顺序坑（夹具 teardown 清空 `qed_test` 且不重建、8902 与集成夹具共用同库）已写入 [operations.md](../guides/operations.md) runbook。**残余（转 W9 与后续窗口）**＝业务库 `qed` 现为**新形状四表全空**（本机 `.env` 指 `qed_test`，故 C-7 回灌只落在测试库），书目回填的补法在 runbook；根侧 8903 在业务库恢复前显示「暂无书目」空态，这正是四件必答③的预期后果而非故障。**形状未扩**：零新增列、零新增域值，R4 未触发） |
| W9 | 回执根仓库 REQ-136（R1~R5 逐条 + 三处事实错 + 四件必答），并按总纲 6.2「表级变更只同步表信息」通知根改其**三处**：三章总览行（`af_*` 5→4 与新表功能）、4.4 读写矩阵、6.3 路由行（迁移链 head 与建表面读数）；根侧文件由根仓库自己改 | W6 | **本仓侧已执行**（2026-10-22：R1~R5 终态答卷、需根侧随步的六处与「契约不变承诺」已写入本节「W9 回执」子节，经用户转达根仓库；根侧文件的改动由根仓库自身执行，本仓零写入） |
| W10 | 知识点面的消费闭环另批：`af_kp` 的写入路径（谁产生 draft/candidate）、8903 的确认界面、8900 的拉取式提升作业——三者都不在本壳内，本案只交付存储形状与并发判据；**通路判据已晋升为 `docs/design/knowledge-analysis.md`，实现批按其「验收判据」13 条逐条取证** | W7/W11 | 待立项 |
| W11 | **分析模块设计**（裁决⑭的第二步，基于 W3 的暂定结构）：在 `docs/design/` 出分析模块（知识点梳理）一篇——产出方与写入路径（谁写 draft/candidate、批次与 `gen_batch` 的关系）、`af_kp` 的读写契约与 `rev`/`if_rev` 的消费时序、与解析模块的衔接点（锚点指向 `af_pages` 与产物文件）、8903 确认界面与 8900 拉取式提升的分工（与 W10 划清：本篇给通路，W10 是实现批）；`code-map.md` 按解析／分析两模块登记。**顺序硬约束**＝先评审并转正 `database-design.md` 的结构，再写本篇；分析模块不得反向改动解析模块的表形状 | W3 | **已执行**（原交付＝本壳「分析模块设计」节 23 条 `KA-n` + 11 条验收判据 + 结构请求 R1/R2 + 开放裁决 Q1~Q4；按 ADR 0002 未评审设计留在计划内。六项已于同日全部给裁 ⇒ 正文随 W12 晋升，本壳该节自此只留指针与沿革） |
| W12 | **分析模块设计的晋升**（W11 的收口动作，需用户逐条了结 R1/R2 与 Q1~Q4）：整段以稳定名迁为 `docs/design/knowledge-analysis.md`（`KA-n` 编号原样保留），本壳该节位置只留一行指针；同批随步 `test_design_documents.py` 的 `CURRENT_DOCUMENTS`/`carriers`（加 `KA`）、`test_fixed_document_form.py:31` 的 `DESIGN_DOCUMENTS`、`test_code_document_mapping.py:15-22` 的 `ACTIVE_DOCUMENTS`、`docs/design/index.md`（三篇→四篇 + 术语表补「知识点节点」行）、`overview.md` 能力矩阵与 `code-map.md` 两模块登记；正文迁移时把「本壳某节」式引用改为篇内条文号链接 | W11 | **已执行**（2026-10-22，用户裁决⑱「W12确认晋升」放行：`docs/design/knowledge-analysis.md` 立稿，六段骨架齐备、单一 mermaid、`KA-1~KA-28` 按序连续（原 23 条编号原样保留，R1/R2/Q1~Q3 的落点续编为 24~28）、验收判据增至 13 条。**零丢失核对**＝23 条加粗结论与 16 个结构片段逐条比对在原节文本中命中（`tmp/w12_zero_loss.py`）。跨壳编号已降级：`CW-n`/`KP-n`/`OB-n`/`RP-n` 一律改指 `database-design.md` 的四个不变式小节，`architecture/` 与 `design/` 内此类字面命中归零。守护随步＝三道契约文件的常量各加一项，`docs/design/index.md`／`overview.md`／`code-map.md`／`database-design.md` 四处同步，文档层七道 **75 passed**（较晋升前 +2＝新篇进参数化）） |
| W13 | **承载力审计与流程确认**（本轮用户指令「详细梳理状况和项目设计（包括未来规划）确认是否足够」的交付）：`database-design.md` 目标形状节新增两个 H4——「流程落点（两模块十二步，每步只有一个判据位）」与「承载力审计（现役能力与规划能力逐条落点）」，含扩展槽分布现状与唯一未来结构请求的甲／乙落法；本壳新增同名节承载 roadmap 四条与在飞三线的逐条判定、R3／Q5 两项新待裁（**当日均已由用户裁决**＝⑯ 采甲且本轮建列、⑮ 采乙加 `meta`）、两处 `overview.md` 随步缺口；W11 追溯段一句措辞按审计结论改判 | W3/W11 | **已执行**（2026-10-21；含裁决⑮⑯的文档面落地＝本壳「af_books 增列」节、`af_operations` 27 列清单与 `embedding_signature` 行、结论先行与「明确不建」清单、W7 改动面三处（ORM／仓储／守护）、前置条件 10 转为已了结、新增 S10；权威文档同批改＝`af_books` 增列 H4、扩展槽分布段、审计表甲案行、目标 ER 图；零代码、零迁移、零 DDL，契约层守护全绿。**节位随 W14 改章**＝「流程落点（十二步）」现为 `### 流程落点`（四、整体架构），「承载力审计」现为 `### 承载力审计`（七、变更与运维），层级由 H4 升为 H3，「目标形状节」这一旧节名已不存在） |
| W14 | **`database-design.md` 改八章编号体**（本轮用户指令「也按照 一、前言／二、遵循的规则／三、本项目表总览／四、整体架构／五、详细设计／六、与 QED-Engine 的交互与同步契约／七、变更与运维／八、守护与验证 重新梳理一遍」的交付）：先立 ADR 0017（骨架体裁分治，部分取代 ADR 0008 决定 2／3 的适用范围），同批改 `doc-governance.md`「固定文档骨架与可对账契约」节、`test_fixed_document_form.py`（`EIGHT_CHAPTER_SKELETON` 白名单 + 按体裁取追溯段边界）、`architecture/index.md`（骨架列）；搬运判据＝**只搬位与连接句，零增删事实** | W6 | **已执行**（2026-10-22：正文按映射表搬位，`### <table> 列清单` 等对账锚点原样保留在第五章（四份列清单 22／27／12／23 列，与 ORM 逐列对账绿）；零丢失机械计数＝**17 张表 186 条管道行**逐张清点、**30 个 H3 节锚**覆盖一~八章、去日期化 token（`REQ\|PLAN\|ARCH\|DEFECT-\d` 与日期）在一~七章计数＝0 而 8 处全落第八章；守护读数＝契约层七道 + 计划/台账两道共 **73 passed**，全量门禁 **704 passed / 10 skipped** + `ruff check src tests` clean；零代码、零迁移、零 DDL） |

## 验证与验收

- **本壳（W1）**：`pytest tests/contract/test_plan_governance.py tests/contract/test_tracker_governance.py -q`
  绿（新壳元数据七字段与七节齐、镜像行三列与承载单指针合契约）；全量门禁 `pytest tests -q` + `ruff check src tests`
  的读数须归因于并行 WIP（本壳零代码，不可能引入红）。
- **文档批（W3~W6）**：`test_fixed_document_form.py`（列清单↔ORM、约束与索引名、PK 序，骨架 H2 序列**按体裁取**
  ＝其余三篇四段体、`database-design.md` 八章编号体（W14 后口径））、
  `test_architecture_documents.py`（含 :120 硬编码表名集修正）、`test_code_document_mapping.py`、
  `test_db_table_family_governance.py` 四道同构判据全绿；S7 的人工核对＝共享 `qed_*`／`qt_*` 小节内
  逐小节确认零列清单零索引名、跨仓引用零 URL（`rg -n "https?://" docs/architecture docs/design docs/adr` 为空）；
  变异复验＝注入一张未注册表名与截短一份列清单，各点名转红的那道守护。
  **W3 的读法限制**：目标列清单以 H4 入篇且目标表尚无 ORM，故上述对账绿**不构成**目标形状已成立的证据，
  它只证明现役五表形状未被改写、骨架未被破坏；目标形状的证据链在 W7（列↔ORM 转绿）与 W8（真机 DDL 与读数）。
- **代码批（W7）**：默认门禁 + `QED_TEST_MYSQL=1` 集成层（新 base 在真 MySQL 上从 base 建到 head、
  downgrade 可重复）；并发反向判据的双 Session 用例（CW-3/CW-4/CW-6）在 `QED_TEST_MYSQL=1` 层取证；
  撤锁变异 ⇒ 对应用例转红。
- **设计批（W11 交付、W12 晋升为 `docs/design/knowledge-analysis.md`）**：**列对账**＝凡引用列名者都能在
  `database-design.md` 的 `af_kp`／`af_operations` 清单里逐名实读到（零新列；R1 收窄域值、R2 扩第九值
  都只改应用层常量，故零 ALTER、零新迁移）；
  **判据对账**＝13 条验收判据逐条有会红形式，且与 [database-design.md](../architecture/database-design.md)
  的「服务端不变式」「键与并发写入形态」「跨版本存活」「保留与裁剪」四节无一条冲突或重述（`CW-n`／`KP-n`／
  `OB-n`／`RP-n` 是本壳的过程编号，不作为晋升正文的引用面）；
  **晋升对账**＝W12 清单逐文件点名（守护三处常量 + 设计索引 + 能力矩阵与 code-map）；
  **零丢失对账**＝23 条加粗结论与 16 个结构片段逐条在原节文本命中（`tmp/w12_zero_loss.py` 读数 `missing=0`）；
  人工核对＝把该篇任一条 `KA-n` 的落点列名删掉，确认对应验收判据能转红（防止判据空转）。
  **裁决⑰⑱的执行读数**＝变异复验 2/2 转红并逐字节还原（`tmp/w12_mutation_verify.py`：把 `KpRepository.create`
  改成插一行 `op_kind='kp'` ⇒ 「其余五个无写者值计数恒 0」转红；把同一腿改成写 `gen_status='adopted'` ⇒
  域外值断言转红；还原后 `repositories.py` 的 sha256 与变更前相等 `611107be…`）；默认门禁 **706 passed / 14 skipped**
  ＋ `ruff check src tests` clean ＋ 文档层七道守护 **75 passed**。**本批库面零动作**：按用户当日口径业务库 `qed`
  保持为空、验证与演示在 `qed_test`（其 17 本 `af_books` 是在用数据），故**禁跑 `QED_TEST_MYSQL=1`**（夹具
  teardown 清空 `af_*` 且不重建），R1/R2 因此不经任何 DDL 生效；两条运维判读（空态＝错连正式库的信号、
  列注释滞后不是形状漂移）已写进 [operations.md](../guides/operations.md) runbook。
- **审计批（W13，`database-design.md` 的两个 H4 ＋ 本壳「承载力审计与流程确认」节）**：**列对账**＝流程表与
  审计表凡引用列名者都能在四份目标列清单里逐名实读到（审计当轮零新列、零域值扩张，两处缺口只登记为
  R3／Q5 而不在篇内擅改）；**裁决后对账**＝裁决⑮⑯新增的 `af_books.meta`、`embedding_signature` 与
  `op_kind='index'` 三处在权威文档与本壳**各有正文落点＋代价段＋守护形式**（列域不重叠／无写者计数恒 0），
  且列数标题（`af_operations` 27、`af_kp` 23）与实际行数一致（时刻对按两列计）；
  **能力对账**＝roadmap 四条里程碑与在飞三线各有一行判定，无一条留「以后再说」；
  **唯一性对账**＝十二步流程每步恰一个判据位，且与「事件与意图的分工」节的四源判据不冲突不重述；
  **去过程叙述对账**＝结论正文所在的两个 H4 内零任务编号、零绝对日期（越界即
  `test_fixed_document_form.py::test_architecture_body_carries_no_process_narrative` 转红）；
  人工核对＝把审计表任一行的落点列名删掉，列对账脚本须点名该列缺失（防止审计空转）。
- **真机批（W8，已执行）**：备份文件 sha256 前缀留档（`29bb119e…`／`519d4da0…`）＋DROP 前逐表 `SHOW CREATE TABLE`
  双份留档、空窗期 8900/8903 的可见后果按四件必答③逐端点实测（11 条 GET 全为 200/404 的正确语义、零 5xx，
  读数 `tmp/window-20261022/empty_window_readings.txt`）、两库重建后各 `VERDICT=PASS` 且真库列数逐张等于设计篇
  列清单（22／27／12／23）、附带面逐名零受损、`books/sync` 回灌 17 本的重放幂等读回；
  **门禁面**＝默认门禁 **705 passed / 14 skipped** + `ruff check src tests` clean（文档随步后的收尾复跑）＋
  `QED_TEST_MYSQL=1` 层窗口内 12 passed，其中
  `tests/integration/test_concurrency_criteria.py` 4 例覆盖 CW-3／CW-4／CW-6 的双 Session 反向判据，
  并对三处被守护机制做变异复验（逐条撤除 ⇒ 对应用例转红，随后按 sha256 逐字节还原）。
  **「重建后首本全链路（ingest → 解析 → 对照 → 编辑 → 结构声明 → 确认）」按用户裁决不在本轮**，
  该判据随 W10 的立项或后续真机窗口取证。

## 回滚

- **文档批**：本壳、todo 行与三处更正由 git 提交锚定，逐批 `git revert` 即可；无库内动作。
- **代码批（W7）未进 W8 前**：`develop` 分支上 revert 提交即回到旧五表形状；`qed_test` 每次从 base 重建，
  回滚后自动一致；业务库 `qed` 在 W8 前**未被触碰**（这是本案的硬要求：W7 只改代码与迁移文件，
  不在真库执行）。
- **W8 窗口内**：回滚＝按备份 SQL 还原五表 + 恢复旧链（6 个迁移文件从 git 取回）+ `alembic upgrade head`。
  因档 B 清空的是可再生产物与未积累的人工结论，回滚的数据损失面在备份留档时逐表计行写明。
  **窗口已于 2026-10-22 收口，回滚锚点即刻可用**＝`tmp/window-20261022/af_backup_qed_20261022.sql` 与
  `af_backup_qed_test_20261022.sql`（sha256 前缀见上）＋旧链 6 个迁移文件的 git 锚点；DROP 前的旧形状 DDL
  另存 `ddl_before_*.sql`（`qed`）与 `af_backup_qed_test_20261022.sql`（`qed_test`，6 条 `CREATE TABLE`）。
- 库内操作的执行权在用户（AGENTS.md：D 类须备份、批准、完整差异复核，不得隐式执行）；本案 W8 的执行权由用户
  2026-10-22 显式改判「现在进行下一步，你帮我执行」后代为驱动，三件纪律逐件留档，未因代驱动而减免。

## 关闭与归档

- 关闭条件＝S1~S10 逐条达成（判据正本在本壳与台账行；S8/S9 对应 W11 设计与 W12 晋升，S10 对应裁决⑮⑯
  的落地）；W7/W8/W10 各自另批立项或另批批准，
  不以本壳关闭代替其完成。
- 归档判定 **Retain**：四表逐列形状、折叠映射、越闸例外与转表触发判据、档 B 四件必答、
  会红守护清单属不可替代决策证据，关闭后按当时里程碑留档在 `docs/plans/`（或移 `history/plans/` 并保持活链接）。
- 残余移交：① 8903/8900 侧因形状变更需做的适配由根仓库自行承担（本壳只发回执，不代改）；
  ② 长文文件布局（`af_kp.narrative_ref` 指向的路径规范）需在 `parsing-pipeline.md` 或新篇设计里定档，
  本案只声明「落数据根、相对路径、禁绝对路径与越根」；③ `af_operations` 是否补回 `started_at`/`finished_at`
  实体列，由 W7 表统计与 W4 真机耗时读数定；
  ④ **四个无写者 `op_kind` 值（`ingest`／`confirm`／`sync`／`purge`）的写者，以及「重新摄取时把
  `last_parse_result`／生效指针／进度一并复位」的行为，本案不接**（＝原 REQ-123 R1 的欠账，已并入 REQ-136 R4
  并在「W9 回执」如实写明），须由后续消费批立项；本案交付的只有形状、判据与「计数恒 0」守护。

## 承载 REQ-136

**请求接收（QED-Engine 根仓库 REQ-136「af_ 表族重构综合请求」，挂其 ARCH-025 轮线 2）**——本壳是它的
唯一承接行（ADR 0011 一任务一行：入站请求与其专属承接计划不拆两行）。根侧把 REQ-123／129／133／135
四枚在册请求合并为一枚，并要求对方为综合请求**立一枚承接行**；本行的 类别=主线、承载指向本壳，
即该承接动作。原 REQ-123（`af_books` 三列回写与跨库分叉归一）与原 REQ-129（`af_parse_jobs` 保留期）
在本仓库 todo 里的两枚支线行按下方「并案处置」处理。

| 根侧条目 | 本案的答卷 | 落点 |
| --- | --- | --- |
| R1 两库现状读数与文档自陈更正 | 实读：`qed` 与 `qed_test` 两库 `alembic_version` 俱为 `20260928_0001`（含 `af_page_regions` 与 `parser_signature` 实存），本仓文档 `:275-277` 的「业务库停在 `20260926_0001`」是**陈述落后** | 更正项① + W3 |
| R2 档 B 整族重建采否四件必答 | **采档 B**（用户裁决⑨），四件必答逐条给答，含空窗可见后果与 `legacy` 终局 | 「档 B 重建批次」节 |
| R3 逐列形状、双列权威收敛与净新增两表准入 | 逐列形状已按本案裁决改判为**净新增 2 表**（`af_operations`＋`af_kp`）而非「2 枚窄表 + 保留旧表」；两表各走准入三判据自查；三对同事实双列的权威归属：生效指针唯一在 `af_books.active_job_id`、就绪唯一在 `af_pages.status`（`artifacts` 不作判据）、意图唯一在 `af_operations` 的 `intent` 行 | 「准入守则自查与例外登记」节 + W4 |
| R4 书级三列自洽与两库分叉归一（原 REQ-123 R1/R2） | `af_books` 的**快照列与进度列不动**（裁决②；裁决⑮只追加扩展槽 `meta`，与本条无关），故复位口径改由「`last_parse_result` 的取值与 `ingest` 事件行配对」表达：ingest 时除归零进度外，同批落一行 `op_kind='ingest'`、`status='ok'` 的事件，使「书目结局」与「事件流」不再互相打脸（**本案交付这一口径的形状与守护，不交付写者**：`op_kind='ingest'` 建值不建写者，归零动作亦未接，见下方「W9 回执」R4 行——原 REQ-123 R1 的复位归零仍是欠账）；两库分叉的归一口径（不重跑解析）在 W3 的迁移节给 SQL 级判据 | 「三层职责」OB-2 + W3/W7；原 PLAN-019 的 W10/W11 与本条**并案**，由本壳单点承接 |
| R5 `af_parse_jobs` 保留期与不可裁剪不变量（原 REQ-129） | 明文**「不清理」**（RP-1）+ 四类不可裁剪行（RP-3，含被 `active_job_id` 引用的 parse 行、全部 `intent` 行、在飞行、已背书知识节点）；触发阈值与执行另批 | 「保留与裁剪判据」节 + W3 |

**并案处置（本仓 todo 两枚旧行）**：根侧已把 REQ-123／REQ-129 的任务行原子移出并置 `Superseded`，
其残余移交项 1 明确要求对方「改指 REQ-136 并合并为一枚承接行」。本案的处置分两段：

- **本批已做**＝todo 新增 PLAN-024 一枚镜像行承载 REQ-136（单行，ADR 0011）；`REQ-123` 与 `REQ-129`
  两行的「任务」列首各追加**已并入 REQ-136**的改指标记并写明本壳的判据落点——**非破坏式**，
  两行的原 R 项文字与各自承载指针（PLAN-019 壳 / `database-design.md`）原样保留。
- **待批准才做**＝两旧行的原子移除与 `completed.md` 迁移（关闭结果 Not Applicable，取代关系写明
  REQ-136 → 本行）。理由＝这两行是并行会话在本工作区的**未提交登记**，擅自移除会毁掉他人留痕；
  移除批次须与 W3 的 `database-design.md` 晋升同批（否则台账出现「指向已关闭行」的空档）。

本壳留下这条处置记录，使「谁取代谁、做到哪一步」在同机可复算。

### W9 回执（执行结果，供根仓库自行登记）

按 `cross-project-collaboration.md`「回执」条：本仓不写根仓库文件，完成后以本仓 todo＋本节为证据，
根侧读数与登记由根仓库自己做。以下 R1~R5 是 REQ-136 五问在 W7／W8 之后的**终态答卷**（判据正本仍在上表）。

| 根侧条目 | 终态 | 证据 |
| --- | --- | --- |
| R1 两库现状读数与文档自陈更正 | **已了结**：更正落文档面（本壳「事实错更正」①②③），且库内现状已被档 B 重建整体取代＝两库 `alembic_version`＝`20261022_0001`、`af_*` 恰四张 | `tmp/window-20261022/rebuild_*.txt` 的 `VERDICT=PASS`；本仓 runbook 的 C-3/C-4 读数 |
| R2 档 B 四件必答 | **已了结**：①②在 W7 代码与迁移里成文，③在 W8 窗口按 11 条 GET 实测（空态非故障），④`legacy` 只作 `af_pages.job_id` 作用域哨兵、`af_operations` 内零 `job_id='legacy'` 行（表空即成立，且写入路径不给 `legacy`） | 本壳「档 B 重建批次」节＋`tmp/window-20261022/empty_window_readings.txt` |
| R3 逐列形状与双列权威收敛 | **已了结**：四表逐列以 ORM 为执行事实源、设计篇第五章为正本；三对同事实双列的权威归属在 W7 落成代码不变式（生效指针只 `af_books.active_job_id`、就绪只 `af_pages.status`、意图只 `intent` 行） | 真库列数逐张＝22／27／12／23（与设计篇列清单相等）；`tests/unit/test_db_table_family_governance.py` |
| R4 书级三列自洽与两库分叉归一（原 REQ-123） | **形状面已了结、数据面不适用**：`qed` 与 `qed_test` 两库的 `af_*` 都由重建归一（各表 0 行 ⇒ 结构上不存在分叉行）；「ingest 事件行与书级结局配对」的写法只到**形状与判据**：`op_kind='ingest'` 与 `confirm`／`sync`／`purge` 同属
   「建值不建写者」，ingest 端点只回写 `af_books` 的 `page_count`／`sha256`／`ingest_status`／`ingested_at` 四列，
   **不写事件行、也不归零 `last_parse_result` 与生效指针** ⇒ **原 REQ-123 R1 的「复位归零」仍是欠账**，
   须由后续消费批立项接写者（本壳「流程落点」第 2 步与 `op_kind` 行都已就地标注该口径） | 本壳「三层职责」OB-2；两库 `af_books` 行数读数（`qed` 0／`qed_test` 17，后者为回灌所得） |
| R5 `af_parse_jobs` 保留期与不可裁剪不变量（原 REQ-129） | **已了结**：本表不复存在（折叠为 `op_kind='parse'` 事件行），口径改写为「事件表一律不清理」＋四类不可裁剪行（RP-1/RP-3） | `database-design.md`「保留与裁剪」表；`tests/contract/test_architecture_documents.py` |

**需根仓库随步的六处**（本仓只通知、不代改）：

1. §三 `af_books` 行备注的「其库内重建窗口未执行」→ **已执行**（两库 `alembic_version`＝`20261022_0001`、
   `af_*` 恰四张）。
2. §7.8 T11 末段「其**库内升级窗口未执行**＝对方业务库仍在旧五表…旧表消失前根侧不直连消费新表」→
   窗口已执行，**旧五表（`af_parse_jobs`／`af_block_edits`／`af_page_regions`）已在两库消失**；
   「旧表消失前」这一前置条件解除，但 R2「根侧不直连、经 8902 透传」不变。
3. §7.8 T11 登记的 `af_pages`＝**11 列**是**事实错**，实为 **12 列**（`book_id`／`job_id`／`page_no`／`status`／
   `source`／`quality`／`error`／`artifacts`／`confirm_state`／`section_kind`／`meta`／`parsed_at`）；
   真库 `SHOW CREATE TABLE` 与只读探针两次复算一致。列级正本＝`Axiom-Flow/docs/architecture/database-design.md`
   第五章（表级登记不写逐列形状，此处只更正计数）。
4. §7.8 T8 的两库 `20260928_0001` 与「`af_page_regions` 实存、`af_parse_jobs.parser_signature` 实存」读数
   **已被档 B 取代**：旧链 6 个迁移文件退役，`parser_signature` 迁为 `af_operations.parser_signature`。
   按「历史读数按当时口径保留」的治理，建议标注为「已被 T11 的新读数取代」而非删除。
5. §7.9 触发阈值取证行「`af_operations`（现名 `af_parse_jobs`）7 行」→ 现名已定为 `af_operations`，
   重建后两库各 **0 行**；「不设固定天数、只设触发条件」的口径与阈值（≥1 GB 或 ≥10 万行）**不变**，无需改规则。
6. **业务库现状对消费方的可见后果**：`qed` 现为**新形状四表全空**（`af_books` 0 行），因本机 `.env` 的
   `QED_DB_NAME` 指 `qed_test`，C-7 的 `books/sync` 回灌（17 本、重放幂等）只落在测试库 ⇒ 8900/8903 走业务库
   透传时看到的是**空态「暂无书目」而不是故障**。业务面恢复＝对方按 runbook 以 `QED_DB_NAME=qed` 起服回灌，
   再逐本重新 ingest＋解析（页行与版本**不回填**，四件必答②）。本窗口未调用任何 ingest/解析/purge，
   故盘上产物目录零改动——「DB 无页行而盘上有产物」的不一致正是「不回填」判据的预期后果，
   就绪判定仍以 DB 为唯一事实源（ADR 0015）不变。

**契约不变承诺（根侧无需改码的依据）**：8902 端点 URL 与响应形状**零改动**（`api.md` 端点总表 18 条公共契约
逐条不变，仅字段来源由退役三表改指 `af_operations`），故根侧透传腿（总纲 6.3 登记的 `api/axiom.py` +
`clients/axiom_client.py`）不需要适配；读取面已在新形状上按空窗读数复验（`/books` 空列表、详情/页/manifest/
chunks/versions 404、`/parse-jobs` 空列表、未知 `job_id` 404），写入面（逐页落库、结构声明、块编辑）待对方
后续真机解析窗口取证。

**裁决⑰⑱与 W12 的随步增量（同日第二批，根侧只需知会、无需改码）**：表级事实**不变**（仍四张、head 仍
`20261022_0001`、零 DDL），改动全在列级闭集与设计链路 ⇒ 按总纲 6.1「`af_*` 列级正本在各自仓库文档」，
根侧不需要跟着改域值枚举。三条知会＝① `gen_status` 闭集**收窄为三值**（`draft`/`candidate`/`rejected`，
`adopted` 已删，人工终态只由 `confirmed` 表达）；② `op_kind` 闭集**扩为九值**（新增 `kp`＝梳理批次流水，
与 `index` 同属「本轮建值不建写者」）；③ 本仓 `docs/design/` 由三篇增至**四篇**，新增
`knowledge-analysis.md`（`KA-1~KA-28`，知识点梳理与提升通路），根侧 8903 确认界面与 8900 拉取式提升作业的
**通路判据从此有篇可引**（发起面＝人工、批次粒度＝整本书默认可指定单章、未归档池收敛＝人工挂父）。
业务库 `qed` 按用户当日口径**继续保持空**，书目同步延后到用户确认 OK，`qed_test` 的 17 本是在用演示数据。
