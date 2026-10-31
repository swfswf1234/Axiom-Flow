# 块类型改判回放、页域识别与梳理文本引擎接入（annotation-replay-and-kp-text-engine）

状态：In Progress
任务类型：B
最后更新：2026-10-26
关联 ADR：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900、梳理调用只经适配层，且无静默兜底），
[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 白名单＝自有 `af_*`＋`alembic_version`；本案只写共享 `qed_llm_calls` 的行、不改结构），
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（页级人工意图跨版本存活的先例，`corrected_type` 的绑定形状沿用），
[ADR 0013](../adr/0013-ledger-row-three-elements.md)／[ADR 0018](../adr/0018-task-column-bound-and-progress-ban.md)（台账行列界），
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪唯一源＝DB，页域判据不得反过来改判页状态），
[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)（四表形状已封闭：本案**零 ALTER、零新列、零新域值**）
关联设计：[knowledge-analysis.md](../design/knowledge-analysis.md)（`KA-2`／`KA-5`／`KA-20`／`KA-22`／`KA-29` 的取材、目录阶段输入、长文与槽位、取材剔除判据＝本案条文正本），
[parsing-pipeline.md](../design/parsing-pipeline.md)（`PP-4` 产物通路、`PP-13` 就绪口径），
[parsing-management.md](../design/parsing-management.md)（`PM-*` 人工意图行语义与 `PM-13` 单实例串行），
[api.md](../architecture/api.md) ③/④（实核＝块编辑与页数据属 **③ 解析结果与 PDF 对照**、七条 `kp` 属 **④ 知识点梳理**，分类表正本在 `api.md:86`／`:87`；扩载荷动 ③ 的 `PUT …/blocks/{index}/edit`，长文端点若落地动 ④——原写 ② 是本会话笔误，② 是 af_* 数据查询类），
[database-design.md](../architecture/database-design.md)（`af_pages.section_kind` 列语义与闭集），
[code-map.md](../architecture/code-map.md)（分析模块与适配层登记面）
关联 Tracker：docs/trackers/todo.md（PLAN-026 镜像行；并承载根侧入站请求 REQ-142／REQ-143 两行的本仓侧通路）
归档判定：Retain（「人工改判进取材视野」这一改动的两侧对账读数、页域判据在真书页面上的标定证据、
以及梳理文本适配器的首轮调用账形状属不可再生证据；条文本体在 `design/knowledge-analysis.md` 与 `architecture/api.md`）

> **授权前提（必须先记）**：本案的代码由根仓库会话直接实施，依据＝用户 2026-10-26 指令原文
> 「**授权你动Axiom-Flow代码开始代码部分**」，**仅限本轮、仅限本仓**；根 `AGENTS.md` 的
> 「在 Axiom-Flow 工作区只读 + 写文档」就此**逐次**解除，不是标准变更，故不立 ADR、不改 `standards/` 正文。
> 本会话在本仓**零 git 操作**（提交归对方会话或人工）。
>
> **叠加基线（用户裁甲・直接叠加＋逐文件清单）**：落笔时本仓 `develop`（HEAD `46f5f3f`）不干净＝PLAN-025 批
> 未提交工作 **已跟踪 21 文件 +2622/-185**（`api/main.py` +358、`db/repositories.py` +643、`schemas.py` +209、
> `engines/factory.py` +48、`engines/base.py` +70、`tests/contract/test_api_v1_contract.py` +593 等）
> ＋ **未跟踪新件 7 文件 +4831 行**（`orchestrator/knowledge.py` 1315、`db/llm_ledger.py` 136、
> 四枚测试与 `tests/support/llm_ledger_schema.py`）。本案在其上**只加不改其行**，交付时按
> 「本会话改的文件／对方在途的文件」两列分列（见「逐文件归属」节），以免 `git add -A` 误并、`git checkout --` 误抹。

## 目标与成功标准

**目标**＝把三件本仓缺的东西一次接通，使根侧「人工改标注 → 梳理取材 → 真实生成一轮目录」这条链在 8902 侧成立：

1. **块类型可人工改判，且改判进入本仓自己的消费视野**——今天 `block_type` 只是产物快照的只读回声
   （`api/main.py:574` 从 `block.type.value` 取，`EDIT_PAYLOAD_KEYS` 里没有类型改判键），
   且 `orchestrator/knowledge.py:244` 直接读 `pXXXX.blocks.json` 重放**零编辑回放**，
   所以「人工把页眉改成 `header`」对目录抽取毫无作用。本案让它有作用。
2. **页域识别补一枚只有整本视野才有的判据**（跨页重复线），把误判成 `paragraph` 的页眉页脚从源头改判，
   并给 `af_pages.section_kind`（列在册、无写者、恒 `''`）补上写手。
3. **梳理文本引擎接入一枚**：`TEXT_ENGINE_NAMES` 与 `IMPLEMENTED_TEXT_ENGINES` 现皆空
   （`engines/factory.py:25-26`），`build_text_engine`（`:76`）自陈「当前一条都构造不出来」⇒ `POST /kp-jobs`
   恒 503。本案要接入一枚适配器使「真实生成一轮目录」可跑；并补长文写腿的 HTTP 面
   （`write_narrative` 已在册、有测试，但 `api/main.py` 无路由消费它）。
   **但这两条的落点各撞一枚在册条文**（适配器出站走哪＝R8；长文端点与 `narrative_ref` 写列＝R9），
   2026-10-26 对账后改判为「等裁不动」，本壳先只交付第 1／2 条与判据纯函数。

判据正文在根侧 [REQ-142 壳](../../../docs/plans/2026-10-26-block-label-and-furniture-identification.md)
与 [REQ-143 壳](../../../docs/plans/2026-10-26-kp-generation-leg-and-one-click-summary.md) 的台账行，本案不复制条目、只落本仓侧通路。

## 范围与非目标

**范围内**（七处）：

1. **扩载荷 `corrected_type`**：`EditRequest`（`schemas.py`）加一枚可空字段，值受 `BlockType` 十枚闭集校验；
   `EDIT_PAYLOAD_KEYS`（`db/repositories.py:63`）加同名键；`upsert_edit` 落载荷；`EditRecord` 回显。
   零新列——类型改判随既有 `af_operations` 的 edit 意图行载荷走。
2. **读侧回放（两处，缺一无效）**：`GET /pages/{page_no}` 的块序列与 `build_evidence_set`／`_heading_evidence`
   的取材，都按「同 `job_id` 同页同块」的 edit 行以 `corrected_type` 覆盖产物 `type`；
   换版不自动套用（与既有 edit 绑 `job_id` 的语义一致）。
3. **页域跨页重复线纯函数**：只读整本已生效版本的页块序列，判「同一文本在多数页的同一纵向位置出现」，
   产出 `furniture_hint`；**读时计算、零写库、零重解析**，与根侧 [REQ-140 壳](../../../docs/plans/2026-10-24-parsing-layout-geometry-replica.md)
   R3 的三条件合取分层＝本案只供给 ① 的类型维，页边带几何与短文本条件留给根侧渲染腿。
4. **`af_pages.section_kind` 写手**（**等裁不动＝R10**）：拟取与 ③ 同源的判据，八值闭集
   （`cover/preface/ack/toc/chapter/appendix/back/other`，`db/models.py:246`）可核，`PageHeadings.section_kind`
   （`engines/base.py`）就是它的消费者。受阻原因见结构请求 R10：`database-design.md` 对该列写的是「本轮只建列」，
   且 `knowledge-analysis.md` 里没有授予写手的条文位。
5. **文本引擎适配器一枚**（**等裁不动＝R8**）：新建 `engines/kp_gateway.py` 实现 `KnowledgeEngine` 协议
   （`outline(pages)`／`points(section)`／`health()`，`engines/base.py:139-158`），`TEXT_ENGINE_NAMES`／
   `IMPLEMENTED_TEXT_ENGINES` 登记该一枚名字、`build_text_engine` 构造、`create_app(knowledge_engine_factory=...)`
   接上；调用账走在册 `db/llm_ledger.py`（`service="axiom_flow"`、`task="kp-curation"`），`AXIOM_KP_ENGINE`
   默认**仍为空**（见 AF-T2）。受阻原因＝适配器**出站走哪**与**账由谁写**两处各与在册条文相撞（R8）。
   本壳原稿在此直写「调用一律经 8900 `POST /api/v1/llm/text`」是把未裁项当已裁，2026-10-26 对账后撤回。
   **同日裁并落地（R8 甲＋AF-T2 甲）＝W6**：出站唯一＝8900 该端点、`qed_llm_calls` 只由 8900 落一行、槽位默认值
   即内置实名 `qed-gateway`——本段的「账走在册 `db/llm_ledger.py`」与「`AXIOM_KP_ENGINE` 默认仍为空」是裁前原稿，
   取值以「裁决回填」节为准。
6. **长文写腿端点**（**等裁不动＝R9**）：拟新增 `PUT /api/v1/kp/nodes/{node_id}/narrative` 消费在册
   `write_narrative`（`orchestrator/knowledge.py:1258`）；路径派生式与 jail **一字不放宽**。受阻原因＝
   PLAN-025 W6 明写「本案不新增端点」，且 `narrative_ref` 由谁写属 PLAN-025 **R5 未裁**（在册守护用例现断言
   「`write_narrative` 不写该列」）。
7. **契约随步**：本轮只随**已落地净集**改——`api.md` ③ 的块编辑载荷与错误面、`knowledge-analysis.md` 的取材条文、
   `parsing-management.md` 的人工意图语义、`code-map.md` 五元组；④ 的端点表与 `database-design.md` 的
   `section_kind` 披露随 R9／R10 裁定后再动。

**非目标**（五条）：

- 不改 `BlockType` 闭集、不加新类型值；不把「章／节」层级塞进块类型（层级属 `Block.level` 与 `af_kp` 结构态）。
- 零 ALTER、零新列、零新表、零新域值——四表形状已由 PLAN-024 封闭；本案发现形状不足时在本壳登记结构请求并等裁。
- 不做批量自动回填历史页的 `section_kind`（属 D 类写库窗口，归人工发令）。
- 不实现「只解析目录」端点、不做合并梳理批次；`chunks.json` 不进通路与既有判据一字不动。
- 根侧的渲染判据、按钮与屏形态不在本案（归 REQ-140／REQ-141／REQ-142／REQ-143 根侧工作项）。

## 前置条件

1. **本轮净集（W2~W4）的前置已齐**＝用户对「甲・AF 侧扩载荷」的裁定覆盖 W2／W3，W4 是零写库的读时纯函数、
   不撞任何在册条文。**受阻集（W5~W7）另需 R8／R9／R10 出裁**，加待裁点 AF-T1~T4 与根侧对应项定档后才动码。
2. PLAN-025 批的在途工作树保持可用（本案与其同文件叠加，开工前重新取一次 `git status --short` 与 `git diff --stat` 指纹）。
3. 若 R8 裁甲或丙（出站指向 8900 网关）：8900 需在听且 `POST /api/v1/llm/text` 可用；
   AF 侧**不因 8900 离线而启动失败**（ADR 0004 的降级纪律）。R8 未裁前本案不依赖 8900。
4. 真机窗口需要人工发令：一次梳理批次会写 `af_kp` 与 `qed_llm_calls`，不在默认测试里跑。

## 待裁点（立项轮不默认采纳）

- **AF-T1 `section_kind` 写手落点**＝甲・解析落库时同批写（每页落 `af_pages` 那一步顺手判一次，历史页保持 `''`
  等下一次重解析或人工回填窗口）（**建议**，理由＝零额外通路、判据只在一处跑）；
  乙・单一枚幂等回填端点（可随时重算，但给「页状态由谁写」开第二个入口）；
  丙・只在读侧派生、`af_pages` 列继续恒空（与列已在册却无写者的现状同形，但 `PageHeadings` 每页都要重算）。
- **AF-T2 槽位默认值**＝甲・登记名字但 `kp_engine` 默认仍 `""`，配了才非 503（**建议**，
  理由＝ADR 0004 无静默兜底；现网不配就不会打模型）；乙・默认为该适配器名（省事，但「服务起来就可能在跑模型」）。
- **AF-T3 prompt 组装归谁**＝甲・本仓适配器组 prompt、8900 只管一次调用与记账（**建议**，
  理由＝梳理的取材边界是解析侧知识，prompt 与 `KA-5` 阶段划分同处才好维护）；
  乙・根侧另出「梳理专用」端点（根侧要理解 `af_kp` 形状，越过模型边界）。
- **AF-T4 长文写腿的写列范围**＝甲・`narrative_ref` 只由本端点写、其余结构域不动（**建议**）；
  乙・并入 `PATCH /kp/nodes/{node_id}`（省一枚端点，但把「写字节文件」混进「改结构态」的乐观并发域）。

> 四枚待裁点与结构请求的挂接（2026-10-26 对账后补）：AF-T1／AF-T4／AF-T3 分别落在 R10／R9／R8 之下——
> R8~R10 未裁时，对应待裁点无处生效；本轮净集（W2~W4）不依赖这四枚，可先行。

## 结构请求登记（R8~R10，接续 PLAN-025 的 R4~R7）

**R8｜梳理文本调用的出站点与记账归属**（本案立项时把「经 8900 网关」当成已裁，2026-10-26 三路对账发现撞两处在册条文；本案不动码，等裁）。

- 在册条文：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md) 决定 2「解析直连模型服务，**不再经 8900 网关**；模型服务地址可配置（部署形态需要时**可指向 8900 代理，契约不变**）」、
  决定 6「`qed_llm_calls` 调用记录由本仓库在直连成功路径**自写**（`service=axiom_flow`）」；
  PLAN-025 R6 甲案的落码形状明写「`mode` 由注入方给（**本组件出站恒直连**，按注释取 `local`）」；
  `api.md:21-23` 与根侧 `api-contracts.md:738` 各自重申同一句。
- 本案要的两件事与它的冲突：① 根侧 REQ-143 行的 S 判据现写「生成调用**全经 8900 网关**、密钥不下发」；
  ② 若出站落到 8900 的 `POST /api/v1/llm/text`，同一次调用会在 `qed_llm_calls` 留下**两行**
  （8900 网关自记一行＋本仓 `KA-23` 包装器再记一行，`orchestrator/knowledge.py:854`），与「一次调用一行账」的归因互斥。
- 现场实读（三条）：`config.py:221` 的 `llm_api_key()` **在 `src/` 内零调用点**（只有 `tests/unit/test_config.py` 用它），
  即本仓今天没有一条文字模型出站通路；`.env:22` 与 `.env.example:30` 的 `API_KEY=` **均为注释态**；
  8900 侧 `POST /api/v1/llm/text`（`backend/qed_engine/api/control.py`）已在册且自带 `qed_llm_calls` 记账。
- 三案（等裁；本案 W6 在裁定前不动码）：
  - 甲・出站地址可配置、缺省指向 8900 网关，本仓**不再自写**梳理账：走 ADR 0004 决定 2 括号里的「指向 8900 代理」通路，
    字面不破（解析仍直连）、密钥仍集中（本仓不需要 `API_KEY`）、账只一行（8900 侧）。
    代价＝`KA-23` 的「本仓在直连成功路径自写」要改判成「梳理腿由网关侧记账」，`meta` 里的三枚归因键（`task`／`step`）改由 8900 请求体承载。
  - 乙・本仓直连文字模型服务并自写账（`mode=local`，与 R6 甲案形状一字不动）：不破任何在册条文。
    代价＝本仓需自备 `API_KEY` 与模型地址（与根侧「集中管理模型/API-key」的目标相背），且 8900 看不到这次调用。
  - 丙・两案并存、按 `AXIOM_KP_ENGINE` 的登记名分派（网关名走甲、本地引擎名走乙）：把选择做成配置。
    代价＝闭集登记表要区分两类语义，`build_text_engine` 的构造分派多一条腿，503 文案要能分辨「缺配」与「网关不可达」。
- 与 PLAN-025 的关系：R6 裁的是**身份三列由谁给**（结论＝装配点注入），R8 裁的是**请求出哪个门、账由谁落**，二者正交；
  R6 甲案的「恒直连」这句在 R8 裁甲时需同步改判，否则两处条文打架。

**R9｜长文写腿的 HTTP 面与 `narrative_ref` 写者**（本案要新增一枚端点，PLAN-025 的 W6 与其在册守护用例都站在对面；等裁）。

- 在册条文：PLAN-025 的 W6 行明写「**不新增端点**……加端点属契约变更须先改设计，本案禁止新增判据」；
  其 R5 三案（甲・归入人工背书域九列／乙・立第四条系统腿 `set_narrative_ref`／丙・`create` 时预填）**未裁**，
  落码形状是「`write_narrative` 只落文件、**不碰这一列**」，并且 `tests/unit/test_db.py` 与
  `tests/unit/test_knowledge_pipeline.py` 现有断言即「该列在梳理通路上无写者、计数恒 `''`」。
- 冲突形状：根侧 REQ-143 的 S4 要「详解长文经**新增写腿**落工作区并由既有只读端点读回」——
  「新增写腿」正是 W6 禁止的那一类，而写腿必然要一枚写者来落 `narrative_ref`，即 R5 的裁量。
- 三案（等裁；本案 W7 在裁定前不动码）：
  - 甲・由 PLAN-026 承接「新增 `PUT /kp/nodes/{node_id}/narrative`」，同批改判 PLAN-025 R5 为乙案（第四条系统腿）、
    `api.md` ④ 端点表与两侧守护用例同步；代价＝一次动三处在册判据，须对方会话认可本仓由根侧会话代改。
  - 甲’・新端点但**不写列**：读侧继续用 `KA-20` 授予的派生路径 `<book_dir>/kp/<node_id>.md`，
    文件在位＝有正文；代价＝`narrative_ref` 列永久闲置（在册却无写者），与「列的形状闸」的交付初衷相背。
  - 乙・不新增端点，把长文正文塞进 `PATCH /kp/nodes/{node_id}` 的背书域（`summary` 之外的九列方案即 R5 甲案）：
    零新端点，但把长文混进乐观并发的结构态写面，且受列宽与 422 白名单约束。
- 本案立场：三条都不落码。R9 与 R5 属同一裁量面，**裁 R5 即裁 R9**，请勿只裁其一。

**R10｜`af_pages.section_kind` 的写手与条文位**（本案目标 2 的写侧半条，撞在册的「本轮只建列」；等裁）。

- 在册条文：`database-design.md` 对 `section_kind` 的披露写「**本轮只建列**」，并把版本换代后的重判明确排除
  （「版本换代……不重判」）；`knowledge-analysis.md` 的 `KA-*` 条文里**没有**一条授予页级区段类型的写手；
  根侧 REQ-139 的 **A3**（`section_kind` 写者与剔除条）在对方台账里的取值仍是「待判」。
- 现场实读：`db/models.py:246` 列在册、八值闭集、现网恒 `''`；`orchestrator/knowledge.py:253` 以
  `row.section_kind or ""` 消费它，`engines/base.py` 的 `PageHeadings.section_kind` 正等这个值；
  即**读侧已就位、写侧零生产者**。
- 三案（等裁；本案 W5 在裁定前不动码）：
  - 甲・解析落库时同批写（＝AF-T1 甲案）：动页落库那一步，需在 `database-design.md` 撤掉「本轮只建列」并补一枚
    `KA-*` 条文位；历史页保持 `''`，回填属 D 类窗口。
  - 乙・只读侧派生、列继续恒空：零写库、零改在册条文，本案 W4 的纯函数即可供根侧消费；
    代价＝列成为「在册无写者」的第二例（与 `narrative_ref` 同形），每页重算。
  - 丙・单一枚幂等回填端点：可随时重算，代价＝给「页行的值由谁写」开第二个入口，且同撞「不新增端点」。
- 备注：本案 W4（跨页重复线判据，读时纯函数）**不受 R10 阻**，可先落地；R10 只裁「判据结果是否落库」。

**R11｜目录阶段取材扩到「书自身目录页的条目行」**（`KA-5` 改判稿；2026-10-27 真机第一轮的定性发现，需求方已裁「先丙后甲」的方向、判据正文待批。本案不动码）。

- 触发事实（同轮真机取证）：**现网解析产物整本零 `type=heading` 块、`level` 恒 `None`**（b07 全 439 页扫描；
  b05 抽 p5/40/60/120/200/240/300/330/420 同形——mineru 把章名与节名一律落成 `paragraph`），
  书自身目录页（b07 p0007–p0009）的条目行同样落 `paragraph`。
- 目录页的**现网形状**（2026-10-27 补测 b05，即需求方点名要正式重梳的那本）＝整页目录被压成**一枚 `paragraph` 块**、
  条目靠块内换行分隔、行尾带引导点与**印刷页码**：p7 idx=1（873 字符，首行 `第1章 实数系和复数系…… 1`）、p8 两栏续页两块（287＋307 字符），
  p9 起才是正文（同一章名以 `paragraph` 出现在 p9 idx=0）。⇒ 印刷页码与绝对页号差一枚常量偏移（此例 `+8`），
  **不是零碎失真就是整块多条目**，与 `DEFECT-025` 在根侧登记的那两环根因同形。
- 撞到的在册条文：`knowledge-analysis.md:99`（`KA-5`「目录阶段只读每页的 heading 版面块序列 ＋ `section_kind` ＋ 页序」）；
  `orchestrator/knowledge.py:198,218` 的取材判据字面就是 `block.type is BlockType.HEADING`；
  `knowledge.py:364-366` 把这条写成**构造性事实**（`PageHeadings` 类型里没有正文字段）。
  对面是 `engines/kp_gateway.py:61-62`——`_OUTLINE_PROMPT` **早已要求**模型「若某页是书自身的目录页……把那些条目按层级认下来」，
  即 prompt 有这条规则、取材层不给输入，规则在现网**空转**；`:64` 的兜底＝「认不出输出 `[]`」，
  再叠上 `run_knowledge_job`（`knowledge.py:992`）**没有「零产出即失败」闸**，合起来的形状＝整本跑完得到一批空行、批次照常收口。
- 通路本身已验绿（同日人工旁证＝「先丙」那轮）：把正文页 p10[0]／p10[1]／p14[21]／p33[0]／p35[19] 五行 `corrected_type`
  改判 `heading` → 提交 `p10..p40` 小批次 → 一枚 `outline` 调用出 5 条 draft 目录行且锚点逐条对上 → 逐节 `points` 调用在跑 →
  `qed_llm_calls` 有 `service=axiom_flow`／`task=kp-curation`／`step=outline|points` 可指认 → 8903 读回 5 行、
  两枚解析钮单枚点亮律正常。**缺的只有目录阶段的输入，不是链路。**
- 改判稿（四案，推荐甲-B）：
  - 甲-A・只扩取材不扩锚点：把目录页条目行喂给模型，锚点仍由模型给。
    代价＝所有目录行的 `page` 都落在 p0007–p0009，`_section_spans`（`knowledge.py:456`）「按目录行页序切区间」立刻退化成
    整本一段或空段，知识点阶段的页区间全废；要救它就得把条目右侧的印刷页码折成绝对页号，而 `af_pages` **无印刷页码列**
    （`db/models.py:217` 起只有 `meta` 扩展槽且无写者）⇒ 需新增一枚标定通路。不推荐。
  - 甲-B・名称回定位（**建议**）：模型仍只从读数里给「名称＋层级」，**页号锚由引擎算**——引擎拿名称在正文页做规范化匹配
    （复用 `norm_key_for`，与 `normalize.furniture._fold` 同一条算法），命中的最早正文页即该行的 `page`／`block_index`；
    等于把上面那五枚手工改判自动化。代价三条：①目录行锚点从「模型给」变「引擎算」，`build_outline` 的 malformed 判据
    （`page not in usable_pages` 即丢，`knowledge.py:393`）与 `_OUTLINE_PROMPT:59`「page 必须是读数里出现过的值」两处同批改判；
    ②匹配不到名称的行必须有明确去向（子一）；③**只对有目录页的书有效**，无目录页的书仍落回人工改判或换版重解析。
  - 甲-C・印刷页码标定（与甲-B 同族，锚点算法不同）：目录页条目给出「名称＋层级＋**印刷页码**」，引擎把印刷页码折成绝对页号——
    偏移由「少数能回定位的行」拟合（此例 `+8`），其余行只做算术，不必每行都命中。
    优点是**对多条目行与续页天然成立**、匹配失败面小；代价＝偏移不恒定时（前置页罗马数字与正文阿拉伯数字混排，b05 p6 就是 `V`）
    需要分段拟合，判据比甲-B 重。
  - **现成资产与第二处事实源风险（裁案前必读）**＝这套「块内拆条目＋剥引导点页码＋碎片拼回＋偏移标定」的正体**今天已在根仓库存在**：
    `QED-Engine/web-ui/src/tocParse.ts`（`tocEntriesFromLine` 承守则一、`parseTocLines` 承守则二、`estimateTocOffset` 承偏移标定，
    由 `DEFECT-025` 的两枚清洗守与 `estimateTocOffset` 用例钉住）。甲-B／甲-C 任一落地 AF，就是把同一套判据在两个仓库各写一份——
    需求方要裁的不只是「AF 加不加这条路」，还有**「目录页判据的正本归哪一侧」**（本仓按 `KA-*` 是解析侧知识、归本仓才不破 `KA-5` 的单一判据面；
    归根侧则本仓只收成品）。另需知一条硬形状：现有 `PUT /api/v1/books/{book_id}/kp/outline`（`api/main.py:1155`，`KA-8` 整批声明）
    **只改身份与挂载、不建新行**，所以「根侧算好目录树交进来」这条路**今天不存在**，要走就得另请求一枚创建腿。
  - 乙・取材从「heading 块」换成「每页前若干行正文」让模型自己找标题：零新判据但 token 爆（9B 槽实测上下文 40960，
    整本每页几行远超），且把结构判断全押给模型，破 `KA-5` 理由列「目录阶段的输入全是结构化读数，失败面与吞吐都小」。不推荐。
  - 丙・不改 AF，全靠人工 `corrected_type` 改判：今天可用（已实证），但整本要人手点几十到上百次，**不能当正式通路**，
    只宜作为甲-B 匹配失败时的兜底人工腿。
- 若批甲-B，随批要定的三枚子问题：
  - 子一・匹配不到的目录行去向＝甲・丢弃并新增一枚 `dropped_unlocated` 计数（与 `dropped_depth`／`dropped_malformed` 同形，
    进度面可读）／乙・保留但锚到目录页那行（`_section_spans` 会退化）／丙・保留并入 `review_flags` 等人工挂锚。
  - 子二・「目录页」判据落点＝甲・AF 侧新增读时纯函数单点（与 `furniture_hints` 同处 `normalize/`，判据＝页内出现
    「目录／Contents」标题行或条目行密度达阈；**不新增 `BlockType`**）／乙・复用 `section_kind` 列（仍无写者，等于回到 R10 未裁状态）／
    丙・只由人工 `corrected_type` 判 `toc`——十枚闭集（`schemas.py:46`，闭集外 422 见 `api/main.py:599`）里**没有 `toc`**，
    此路要先扩闭集，与本轮 W12「不改 `BlockType` 闭集」的前提相抵。**（与上面「现成资产」条同一次裁：条目拆分与偏移判据的正本只能有一处，两侧各写一份必漂移。）**
  - 子三・整本成本预算先确认＝甲-B 后整本梳理＝1 次 `outline` ＋（目录行数）次 `points`。本轮实测单枚 `outline` 149.5s、
    `points` 每节 2~4 分钟；b05 若出 80~120 行目录，一轮＝**数小时串行**（`KA-4` 跨书不并发、单 worker）。
    需求方要的「正式的方式重新梳理数学分析原理」属这一档，是否按页区间分批（如每 60 页一批）请一并定。
- 本案立场：R11 批文到达前**不动 AF 码**，`knowledge-analysis.md:99` 的 `KA-5` 正文维持原样；批文到达后按
  「先改判 `KA-5` 与 `_OUTLINE_PROMPT` 两处条文 → 再落 `orchestrator/knowledge.py` 的取材与回定位 → 同批补 malformed 判据的守护用例」顺序执行。

## 裁决回填（2026-10-26，需求方直接出裁并批准本案动码）

令文（需求方原话摘录）＝「后端内容由8902服务提供，8900只是中转，**选甲，我批准你动手**；AXIOM_KP_ENGINE不做配置，.env的配置已足够，
直接调用8900服务的LLM服务即可；槽位默认值确认选甲；AF-T3选甲；R9（＝PLAN-025 R5）选甲；AF-T4选甲，这些prompt都是 axiom-flow负责，
调用也是，8900只是个统一中转的；R10：在梳理知识点、章节的时候，默认剔除页眉页脚的部分即可，当然页可以走API调用AXIOM-FLOW」。

| 裁点 | 取值 | 本案的形状落点 |
| --- | --- | --- |
| R8 出站点与记账 | **甲**（批准代改在册措辞） | 适配器出站＝8900 `POST /api/v1/llm/text`，prompt 与调用都由本仓发起；`qed_llm_calls` **只由 8900 落一行**，本案梳理通路的 `KA-23` 自写腿停用，`task`／`step` 归因经请求体上送（8900 侧净缺＝`LlmTextRequest` 无归因三键、`gateway.call_text` 的 `service` 形参未被端点透传）；ADR 0004 决定 2 的括号「可指向 8900 代理」即本案依据，决定 6「本仓自写」与 PLAN-025 R6 甲案「恒直连」两处措辞随步改判 |
| AF-T2 槽位默认值 | **甲**（与「不做配置」合取） | 本案取「默认值即内置实名 `qed-gateway`、`.env` 零改动」，`_kp_text_engine` 的「请配置 `AXIOM_KP_ENGINE`」文案撤除；T2 甲要防的「服务一起来就在跑模型」由 8902 无自动调度、梳理批次只能人工发起保证（需求方在根侧 REQ-143 壳的「解读①」处可复核） |
| AF-T3 prompt 归谁 | **甲** | prompt 组装正体在本仓适配器内，与 `KA-5` 阶段划分同处；根侧不新增梳理专用端点 |
| R9（＝PLAN-025 R5） | **甲＝R5 的乙案** | 新增 `PUT /api/v1/kp/nodes/{node_id}/narrative`；`narrative_ref` 由第四条系统腿 `set_narrative_ref` 写；同批改判 PLAN-025 W6「不新增端点」、`api.md` ④ 端点表、以及 `tests/unit/test_db.py`／`test_knowledge_pipeline.py` 中「该列无写者／计数恒空串」的守护断言 |
| AF-T4 写列范围 | **甲** | `narrative_ref` 只由该端点写，背书域与结构域不动 |
| R10 | **需求方改判（原三案都不取）** | **不给 `af_pages.section_kind` 造写手**——`database-design.md` 的「本轮只建列」维持、该列继续在册无写者；改为梳理章与知识点阶段**默认剔除页眉页脚**，消费本案 W3 的 `corrected_type` 意图回放与 W4 的 `furniture_hints` 跨页重复线；页级信息由根侧经 8902 页数据端点取（令文「页可以走API调用AXIOM-FLOW」） |
| R11（2026-10-27 批文） | **甲-B・名称回定位＝全案批准**（需求方第五令随步） | 方向定档＝目录阶段的页号锚由**引擎算**而不是模型给：模型仍只出「名称＋层级」，引擎把**书自身目录页**的条目拆出来、标定印刷页码偏移、用条目名回正文定位真实页与块序，合成读数与真 heading 走同一份输入（把丙案那五枚手工改判自动化）。**三枚随批子问题同日出齐**＝子一**保留为目录行＋账面点名**（窗口外命中不认，条目仍成行、锚落回目录页、条目名原文进 `result.unlocated_outline_entries`）；子二**正本归本仓**＝拆分与偏移标定由 `normalize/toc.py` 三条纯函数持，根侧 `web-ui/src/tocParse.ts` 降为显示层清洗、其 `estimateTocOffset` 不再作为第二处取材事实源；子三＝需求方另裁**给 8902 加一枚只目录入参**（`KpJobCreate.stage∈{full, outline}`、缺省 `full`），「这次只梳理目录」由此不必把页区间划窄——整本一轮只剩一枚 outline 调用（真机读数 149.5 秒）。判据落点＝`KA-5` 改判正文＋新条 `KA-33`／`KA-34`，`KA-27` 的 `payload` 四键扩五键、`result` 十五键扩十六键；实现批＝本案 **W14** |
| DEFECT-026 承接（2026-10-27 出裁） | **甲・容错腿＋可指认披露同批**（需求方批准本案动本仓码） | 同批改两处出口＝① `engines/kp_gateway.py` 的 `_drafts` 补**非法转义修复腿**（真机原证＝`qed_llm_calls` id 130 `status=success`、3617 字符而 `json.loads` 报 `Invalid \escape: line 75 column 99`）；② `orchestrator/knowledge.py` 的按节失败披露从「只有一个计数」改成**可指认节名与页区间**（真机表征＝`failed_sections=1` 而进度面读不出是哪一节）。判据晋升一枚 `KA-` 新条，`api.md` ④ 进度面示例随步，根侧 todo `DEFECT-026` 行按 R1~R3 逐条回执 |
| 三案采纳（2026-10-27 真机第一轮后出裁） | **Q1 甲・命中率判据／Q2 甲・源头拆黏行／Q3 甲・区间内旧候选作废**（需求方三问全取推荐案） | ① Q1＝目录页加一道**页级命中率复核**：偏移标定成功时某页条目过半回定位不中 ⇒ 该页整页不算目录页（条目不成行，页码进 `result.dropped_toc_pages`），符号表／索引页由此剔除；偏移标定不出时**不做这一步**（宁可多行，不可丢目录）。② Q2＝`toc_entries` 拆块时**行内出现第二处「引导点＋页码」即以那处为界再拆**（只切分、不改任何字），解 `第4章 连续性 …… 73函数的极限` 与英文条目折行丢字两形。③ Q3＝`finalize_batch` 翻面前**作废旧候选**：同书、非本批、`gen_status='candidate'`、`confirmed=0`、任一锚点页 ∈ [page_start,page_end] ⇒ 翻 `rejected` 并计 `result.superseded_rows`；confirmed 行与区间外行零触碰。判据落点＝`KA-33` 两条新增＋新条 `KA-36`、`KA-27` 的 `result` 十六键扩十八键；实现＝本案 **W17** |
| 第六令・目录解析重做（2026-10-27 真机第二轮后出裁；令文＝「目录解析这块要重做，真机效果很糟糕……按照这个方式进行分析吧……开始梳理并执行跑一轮（之前的删除）」） | **模型判页＋逐页整理为正腿／印刷页码直查定锚／坏行攒批一次模型核对／附件页成行标非知识点／重新解析按目录行选队头队尾**（五件同批，判据先落文档再动码） | 方向改判＝`KA-35` 从「目录阶段确定性成行、模型不参与」改为「**模型判页与整理条目为正腿，确定性条目腿退为回落**」，`model_ref` 三值 `llm:toc`／`rule:toc`／`rule:heading`，只目录批次不再是零调用。五条落点＝① `KA-37` 判页＝一次调用（输入＝取证页集里前／末各 `TOC_PAGE_SCAN_LIMIT`(10) 页样本、每页截 `TOC_PAGE_SAMPLE_MAX_CHARS`(400) 字符，输出＝目录页号集＋正文前后附件页集；域外页号整条弃用并披露），整理＝逐页一次调用（输出三段以 ASCII 竖线相连的竖线行而不是 JSON；单页失败 ⇒ 该页回落 `KA-33` 条目腿并把页号进 `result.outline_fallback_pages`，判页失败或空 ⇒ 全书回落）；② `KA-38` 印刷页码只用来定锚：复用 `KA-33` 的偏移标定与窗口命中，命中即定锚零调用，**窗口内查不中的坏行攒成一批一次模型核对**（只改「落在哪一页」，不改名不改层级），判出页号仍须在取证页集内，命中行进 `result.verified_outline_entries`；③ `KA-39` outline 行带 `meta.toc_role∈{knowledge, reference}` 与 `meta.toc_kind`（**零 ALTER**，两枚键只进 `meta` JSON），引言／重要符号表／附录／索引／习题照常成行但注明非知识点；④ `KA-25` 加「重新解析＝先选队头知识点、再选队尾知识点，两枚选择分置选择面最前与最后，提交的仍是起止两页（由所选行锚页换算），只有已定位到正文页的行可作端点」；⑤ `KA-27` 的 `result` 十八键扩二十二键（`outline_toc_pages`／`outline_fallback_pages`／`verified_outline_entries`／`reference_rows` 逐一登记）。失败根因五条与真机读数见「过程与证据」同日期行。**旧批次按令删除**＝`afkp-89dff7b22d57` 的 156 行与对应 `af_operations` 流水行，逐表逐行清单交签认后才动，本案不自行删库。实现批＝本案 **W18** |
| 第八令三件＋第九令（2026-10-29 需求方授权「我授权改 Axiom-Flow部分」；第九令令文＝「目录选择还有问题，例如数学分析习题集重第三章和第四章的知识点，目录后面的……也不慎加上了，并且直接显示了目录页而非知识点，这应该是没对应上导致的。重新解析目录的顺序按照页数来排序，并且从X页到Y页一定要写准确，可以直接拼到下一个节点-1页这样；内容解析在有目录之后一定要能按，并且走一轮真实调用数学分析原理试试」） | **三件同批动码：条目名共用一把行清洗／读面派生起止两键／`stage:'content'` 只补内容；书名副判＝「习题集」＝`mathanalysis-b07`、「原理」＝`mathanalysis-b05`** | 五件归属先切净：件①「引导点残留」与件②「显示目录页而非知识点」在库侧是**同一处根因**（`build_outline` 的整理腿把模型给的行逐字入册，脏名让前缀命中永不成立 ⇒ 未定位 ⇒ 锚落回目录页）⇒ 合并成一条判据 **`KA-41`**、实现 **W20**；件③「下拉按页数排序」纯屏侧（本案零参与）；件④「从 X 页到 Y 页写准」＝第八令 Q3 甲的改判（Q3 甲′），取法正本由需求方当场给出＝拼到下一枚节点起点页 −1，与在册 `_section_spans` 同一条算式 ⇒ 由读面派生两键（**`KA-42`**、实现 **W21**），屏侧仍不自推＝不建第二处事实源；件⑤「内容解析一定要能按」＝`stage:'content'`（**`KA-43`**、实现 **W22**），同批带节级 `summary`。授权范围两条边界＝不含本仓 git 提交／推送、不含 `dataset/**` 写入；第八令那枚「页数据投影补家具读数」（根侧 W20）**不在本轮**，仍按「请求面变更另开窗口」处理。真跑窗口＝第九令那句「走一轮真实调用数学分析原理试试」即为人工发令，本轮只取**有界页区间**（b05 现 115 枚目录行、每节 137~205 秒 ⇒ 全本一轮数小时，全本另候令），且 8902 须重启才吃到新码 |

> 由此 W5 从「等裁不动」改判为「按剔除腿实施」，W6／W7 解除冻结；本案 W1（demo 门）后半条（真实批次产出的目录不含页眉行）
> 与根侧 REQ-143 W1 同一条真跑窗口，仍须人工发令。

## 需求方第三令承接（2026-10-26）：三部分形态的本仓侧净缺与四枚待裁点

令文摘录（只摘与本仓有关的部分）＝「目录部分……《重新解析》（可以按照选择章节范围内进行重新解析，添加备注即人工评审意见）……
**这里的接口和变更都是由 axiom-flow 实现并提供接口过来的，数据库也是 af 表变动**；展示部分……另外需要 **axiom-flow 自动实现一个美化功能**（按钮），
**剔除页眉页脚页数这些**，然后对公式等进行渲染，对字体格式等进行优化（像书本），也可以由人工编辑（进入编辑界面后）**可以删除，也可以标粗**（按钮，显示重点）；
知识点详情只负责章节总结及下述的知识点总结和**导读（学什么、为什么要学，有什么作用）**……如果有其他任务冲突**以此为准**，**先更新文档再动代码**」。

**本仓侧净缺四条**（逐条给在册证据与是否撞条文；本轮按令文**只落文档、零行码**）：

1. **范围＝零变更即可跑**：`KpJobCreate`（`schemas.py:604-611`）已有 `page_start`／`page_end` 两枚可空键，受理门已校页区间
   （`orchestrator/knowledge.py:121-141`＝404→422→422→409→503→503，全零写入），`KA-25` 已把「整本默认／可单章」写成条文。
   ⇒ 界面对选中目录行取其 anchors 页区间填这两枚键，本仓不改一行。
2. **备注＝净新字段**（撞 `extra="forbid"`）：现请求体三键之外一律拒，且 AF 侧没有任何「人工评审意见」的在册载体。
   ⇒ 见 **AF-T5**；落码要同批改 `schemas.py`、受理门用例、批次流水（`op_kind='kp'` 行的 payload）与 `api.md` ④ 的 `POST /kp-jobs` 行。
3. **美化的剔除半边在本仓、渲染半边不在**：本仓已有判据无消费方——`normalize/furniture.py:50 furniture_hints` 全仓零调用者，
   `build_outline` 只喂 `BlockType.HEADING` 块（页眉页脚因非标题被自然滤除，**不是显式剔除**），而 `extract_points` 把整页
   `PageBody.markdown` 直喂引擎 ⇒ **喂给模型的正文里页眉页脚页数一条没剔**（这与 W5／R10 改判的「默认剔除」是同一条腿的两个出口，
   也与「已知未净半条」那行记的 `markdown` 未被覆盖是同一处）。⇒ 见 **AF-T6**。
   「删除／标粗」两枚人工动作则撞意图闭集：`EDIT_PAYLOAD_KEYS`（`db/repositories.py:65`）＝`block_type`／`verdict`／
   `corrected_text`／`corrected_bbox`／`corrected_type` 五枚，无删除无强调；块编辑无独立表，一律落 `af_operations`
   的 `op_kind='edit'` 意图行 payload JSON（零 DDL 可行）。`RegionOp=add/delete/merge` 属页级结构意图，不可挪为块编辑。⇒ 见 **AF-T7**。
4. **导读无落点**：`af_kp`（`db/models.py:253-325`）在册可用位只有 `summary String(1000)`、`narrative_ref String(200)` 与
   `meta JSON`，而读投影 `_kp_node_out`（`api/main.py:182-185`）明确**不发 `meta`** ⇒ 「学什么／为什么要学／有什么作用」
   要么进 `narrative_ref` 长文（＝W7 那枚端点的成文规格，prompt 正体按 AF-T3 甲在本仓），要么本仓加发 `meta`。⇒ 见 **AF-T8**。

**四枚待裁点（立项轮不默认采纳；**2026-10-26 同日需求方四问四答已全数出裁——T5／T7／T8 取甲、T6 取甲＋乙合取**，逐条取值见本节末尾回填表，以下三案原文留档不作默认采纳依据）**：

- **AF-T5 备注的载体与用途**＝甲・`KpJobCreate` 加可选 `review_note`，随批次流水 payload 存档**并注入该轮 prompt**
  （**建议**，理由＝不注入模型则「重新解析」与首解析毫无差别，人工意见等于没人听；注入则是 `KA-22` 适配层内的一枚上下文段）；
  乙・只注入不存档；丙・只存档不注入。三案都要动请求闭集与其契约测试，且新增键名进 `api.md` ④ 行。
- **AF-T6 家具剔除作用在哪一层**＝甲・只作用在**梳理取材**（`build_outline` 与 `extract_points` 两处按家具线剔块／切段，
  产物文件与 `markdown` 一字不改，**建议**，理由＝零写库、零重解析、不破 `PM-5`）；
  乙・同时在**页数据读投影**上标一记「家具」标记供根侧渲染腿一键隐藏（多一枚读侧字段，不改产物）；
  丙・改写 `.md` 产物正文（动磁盘产物＝`PM-4` 通路变更，风险最高，不取）。
- **AF-T7 「删除」「标粗」两枚意图键**＝甲・`EDIT_PAYLOAD_KEYS` 扩两枚（拟名 `removed`／`emphasis`），仍走同一条
  `PUT …/blocks/{index}/edit`、同一条 `job_id` 绑定语义、**只隐不删**，回放腿消费（**建议**，与 W3 已落的 `corrected_type`
  回放同形、零 DDL）；乙・复用 `verdict='bad'` 表达「删除」（省键，但把「判废」与「人工隐藏」两义混在一枚值上）；
  丙・页级 `RegionOp` 承载（语义错位，不取）。**归属另裁**＝这两枚键落本壳（W11）还是根侧 REQ-142 的意图闭集延长线，
  由需求方定，本壳不擅自挪他行归属。
- **AF-T8 导读的成文位**＝甲・导读＝详解长文的**成文规格**（`narrative_ref` 文件里按三段写），本仓只改 W7 端点的 prompt 规格、
  零新列零新端点（**建议**）；乙・导读落 `af_kp.meta` 并在 `_kp_node_out` 加发 `meta` 键（读投影变更，与 `narrative_ref` 并存两源）；
  丙・只扩 `summary` 列宽（动 DDL，且与 `af_kp` 形状封闭相背，不取）。

**四枚出裁回填（2026-10-26，需求方四问四答；令文仍为「先更新文档再动代码」，故本表只落裁值，动码等需求方一句「开始执行」）**：

| 裁点 | 取值 | 本仓形状落点 |
| --- | --- | --- |
| AF-T5 备注载体与用途 | **甲**（出裁标签「映射页区间＋备注入 prompt」） | `KpJobCreate` 加可选 `review_note`（进 `extra="forbid"` 闭集与其契约测试）→ 随 `op_kind='kp'` 批次流水 payload 存档 → 该轮 prompt 注入为上下文段（`KA-22` 适配层内）；`api.md` ④ 的 `POST /kp-jobs` 行同步。范围半边＝零变更（`page_start`／`page_end` 已在册已校）。 |
| AF-T6 家具剔除作用层 | **甲＋乙合取**（出裁标签「判据归 AF：只作用取材＋读侧标记」） | 判据与剔除**都归本仓**：① 取材两处（`build_outline`／`extract_points`）按 `furniture_hints` ∧ `corrected_type` 剔块或切段，产物文件与 `PageBody.markdown` 一字不改；② 页数据读投影加一记家具标记供根侧渲染腿一键隐藏（「美化」的稳定依赖，不落根侧自算几何）。丙（改写 `.md` 产物）不取。 |
| AF-T7 「删除」「标粗」两枚意图键 | **甲**（出裁标签「意图闭集扩两枚，**归本轮 REQ-143**」） | `EDIT_PAYLOAD_KEYS` 扩 `removed`／`emphasis`，仍走同一条 `PUT …/blocks/{index}/edit`、同一 `job_id` 绑定、**只隐不删**、由 W3 回放腿消费，零 DDL；归属半条已裁＝消费与实施口径归根侧 REQ-143 本行（不挪 REQ-142），本仓侧实施落本壳 W12。 |
| AF-T8 导读成文位 | **甲**（出裁标签「导读＝详解长文的成文规格」；**同日第二批问答补裁**＝「导读和详解就是一回事，帮助学习者理解该部分做什么，以易读、方便人理解为核心」） | 导读不是新列、不是新端点、**也不是与详解并列的第二枚物件**：只有 W7 长文腿这一枚正文，其 prompt 以「易读、帮助理解这部分做什么」为纲，「学什么／为什么要学／有什么作用」是**取材取向而非强制段式**（章态与点态各一档），prompt 正体在本仓（AF-T3 甲）；`meta` 加发半条（乙案）不取。 |

> **与既有工作项的关系**＝AF-T5／T6／T7／T8 都不解除 W6／W7 的既有裁值，而是给它们各加一枚延长件；
> W5（梳理阶段默认剔除）与 AF-T6 是同一件事的两个层次（W5 已裁「做」，T6 裁的是「作用在哪一层」＝取材层＋读侧标记，产物不动）。

## 工作项

| ID | 工作项 | 落点 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| W1 | demo 门（真机最小闭环，判据与根侧 REQ-142 W1 同一条）：把 b05 某页页眉人工改判 `header` → 8902 读回可指认 → `GET /pages` 与梳理取材两处都不再把它当正文 →（后半条等裁）一次真实批次产出的目录不含该行 | `api/main.py`／`orchestrator/knowledge.py` 现场 | W2~W4（前半条即达成；后半条依赖 R8 落地，本案先按前半条验收） | 前半条已达成（2026-10-26：同一枚改判行让页数据块序列与取证标题序列两处读数同变，8 枚用例两侧同钉；W5 后再加半条＝改判成家具的那行**从送模型的正文副本里也消失**，`TestCurationStripsFurniture` 五枚守）；后半条（一次真实批次产出的目录不含该行）需 W10 窗口 |
| W2 | 扩载荷 `corrected_type`：闭集校验、载荷落库、回显；先红后绿 | `schemas.py`、`db/repositories.py`、`api/main.py` | —（用户 2026-10-26 已裁甲・AF 侧扩载荷，不挂 AF-T1~T4） | 已执行（2026-10-26：十枚闭集外 422、`EDIT_PAYLOAD_KEYS` 加同名键、`EditRecord` 回显；零新列由表族守护复验） |
| W3 | 读侧回放两处（页数据端点＋梳理取材），含「改判后换版不套用」「无改判行时读数不变」两条反向断言 | `api/main.py`、`orchestrator/knowledge.py` | W2 | 已执行（2026-10-26：回放正体新建 `normalize/intents.py`；两处各一次仓储查询；四条判据形状见「过程与证据」） |
| W4 | 跨页重复线页域判据纯函数（读时、零写库），含「正文里重复出现的定义句不被误判」反例 | `normalize/furniture.py` | — | 已执行（2026-10-26：`furniture_hints` 三条件合取只供给跨页重复线那一维；6 枚用例含两条反例；本轮无消费方已写进 `PM-19` 边界列） |
| W5 | ~~`section_kind` 写手~~ **2026-10-26 需求方改判**＝不造写手，改为梳理阶段默认剔除页眉页脚（`corrected_type` 回放 ∧ `furniture_hints`） | `normalize/furniture.py`（两枚新纯函数＋`FURNITURE_TYPES`）、`orchestrator/knowledge.py`（`_heading_evidence`／`build_evidence_set`／`_page_bodies` 三处）、`normalize/blocks.py`（一行改引同源常量） | W4 | 已执行（2026-10-26：剔除只落**两处出口**——目录阶段的标题筛选与抽点阶段的正文副本；两腿合取的逐页读数由 `furniture_texts` 给出、整行删除由 `strip_furniture` 执行；产物 `.md` 与 `GET /api/v1/pages/{page_no}` 的 `markdown` 逐字不改（`EvidencePage.markdown` 维持原文，剔除不进事实源）。判据本体晋升 `KA-29`。定向 15 枚新用例；读数与四枚变异校验见「过程与证据」） |
| W6 | 文本引擎适配器一枚＋槽位登记＋`create_app` 接线＋调用账三键可指认（原判据「默认仍 503」随 AF-T2 甲改判为**默认即接通**）—— 出站路径与记账 **2026-10-26 出裁＝R8 甲** | `engines/kp_gateway.py`（新）、`engines/factory.py`、`config.py`、`api/main.py`、`orchestrator/jobs.py`、`orchestrator/knowledge.py` | AF-T2、AF-T3、R8（均已裁） | 已执行（2026-10-26：两阶段 prompt 正体在本仓适配器内、出站唯一＝8900 `POST /api/v1/llm/text`、归因四键随体；四类故障含**空回复**一律归一 `EngineError`，保住 `KA-5` 的按节失败域；自写账跳腿判据＝适配器自报 `accounts_externally` 而非引擎名，批次身份经 `bind_batch` 这道协议外缝交给适配器（未声明者零影响）；槽位配置只落新键 `QED_ENGINE_GATEWAY_URL`／`AXIOM_KP_TIMEOUT`，退役键一条不复活，`.env` 零改动） |
| W7 | 长文两条腿＋`narrative_ref` 写列 —— **2026-10-26 出裁＝R9 甲（＝PLAN-025 R5 乙案）**：`PUT /api/v1/kp/nodes/{node_id}/narrative` 消费在册 `write_narrative`、列由第四条系统腿 `set_narrative_ref` 写；**同路径 `GET` 读腿同批**（根壳实核已把净缺定成两枚＝写端点＋正文读端点，屏侧详解框无读面即无正文）；jail 不放宽、正文超限判 422 不截断、零 DDL | `api/main.py`、`orchestrator/knowledge.py`、`db/repositories.py`、`schemas.py` | AF-T4、R9、PLAN-025 R5（均已裁） | 已执行（2026-10-26：两条腿成对开在节点路径上；`narrative_ref` 在 UPDATE 通路上只有 `set_narrative_ref` 一枚写者，由整表逐列对账守着；jail 与产物通路同一条、不放宽（`ensure_safe_segment`＋`ensure_within`，调用方给不了文件名也给不了路径）；判定序＝422（形状闸在请求模型里，**先于查行**）→404→409（**先于任何磁盘写入**）→先落盘再落列→200 回显正文；缺文件读回 `exists=false` 且两腿都不改列；上限 `NARRATIVE_MAX_CHARS=512_000`，超限判 422 不截断（与 `summary` 的入库截断故意相反）。判据已晋升 `KA-30`，`KA-11` 的写者分域随步改判为四条腿；`api.md` ④ 两条小节与总表两行已落。定向 14 枚契约＋7 枚仓储用例；TDD 红证、判定序对账改判与两处文档门自纠见「过程与证据」） |
| W8 | 文档随步：`api.md` ③/④、`knowledge-analysis.md`、`parsing-management.md`、`database-design.md`、`code-map.md` 五元组 | `docs/` | W2~W4（本轮净集）；R8~R10 落地件随其后 | 逐轮随步已执行（2026-10-26：净集五处 ＋ W6 三处 ＋ W5 三处 ＋ W7 四处 ＋ W11 四处＝`knowledge-analysis.md` 新增 `KA-31` 与 `KA-27` 的 `payload` 四键闭集、`api.md` ④ 的请求体段与门槛表首行与进度面示例／一条新bullet、`database-design.md` 的 `payload` 行加 kp 形状＋当前范围段两处更正（适配器已接入、九条端点）、`overview.md` 能力矩阵一行。`database-design.md` 的 `section_kind` 披露随 R10 改判**维持「本轮只建列」不动**，该案已由 W5 的「不造写手」承载） |
| W9 | 三门禁：定向用例、全量 `pytest tests -q`、`ruff check src tests scripts` | — | W1~W8 | 逐轮随步已执行（2026-10-26：净集 953 → W6 985 → W5 1001 → W7 收口 1026 → **W11 收口 1030 passed／18 skipped** → W17 收口 1072 → **W18 收口 1127 passed／18 skipped** → **W19 收口 1136 passed／18 skipped（49.48s）** → W20~W22 收口 1176 → **W23~W25 收口 1210 passed／18 skipped（51.94s）**，逐轮 `ruff check src tests scripts` 均 All checks passed；读数逐条见「过程与证据」） |
| W10 | 真机梳理窗口（D 类，人工发令）：一本书真实生成一轮目录并在根侧读回 | — | W9 | **前半条已达成**（2026-10-27 丙案轮＝零改码、用在册 `corrected_type` 人工改标五枚行跑一次小页区间真实批次，「受理 → outline → `af_kp` 行 → 调用账 → 根侧屏上读回」五段全绿，收口读数与两枚新事实见「过程与证据」同日期行）；**后半条＝整本目录排在两枚裁点之后**＝根侧 `DEFECT-026`（`_drafts` 缺非法转义容错腿，实测已吃掉一整节知识点）与本壳 `R11`（KA-5 目录阶段取材是否扩到书自身目录页条目行，四案未裁） |
| W11 | **备注通道**（需求方第三令）＝`KpJobCreate` 加可选 `review_note`＋批次流水可指认＋注入该轮 prompt，受理门与契约测试同批 | `schemas.py`、`api/main.py`、`orchestrator/knowledge.py`、`db/repositories.py`、`engines/kp_gateway.py` | AF-T5（**已裁甲**，在 W6 已裁的形状之内） | 已执行（2026-10-26：TDD 先红后绿，红证＝`KpBatchRow.__init__() got an unexpected keyword argument 'review_note'` 与网关 `bind_batch` 的 `TypeError`，五枚用例 5 failed／124 passed 落定后再动码；落点比原表多两件＝`db/repositories.py`（`KP_PAYLOAD_KEYS` 三键→四键、`KpBatchRow.review_note`）与 `engines/kp_gateway.py`（`bind_batch` 认这枚关键字参数、两阶段 prompt 各追加「人工评审意见」上下文段）；上限 `KP_REVIEW_NOTE_MAX_CHARS=2000`，超限判 422 **不截断**（与 `KA-30` 同一条口径）；备注落 `payload` 而非 `note` 实体列（它是批次输入，不是「读这行的人留下的话」），读侧 `ledger.review_note` 恒发、缺省空串；**不进归因 `meta`**，请求体闭集一字未加；空备注时上下文段整段不出现。判据晋升 `KA-31`，`KA-27` 的 `payload` 闭集与 `database-design.md` 的载荷行、`api.md` ④ 的请求体／门槛表／进度面示例随步） |
| W12 | **家具剔除的作用层＋读侧家具标记＋两枚新意图键**（第三令「美化」与「删除／标粗」的本仓半边）＝取材两处（`build_outline`／`extract_points`）按家具线剔块或切段（W5 的层次细化，产物文件与 `markdown` 一字不改）＋页数据读投影加一记家具标记供根侧渲染腿一键隐藏＋`EDIT_PAYLOAD_KEYS` 扩两枚＋回放腿消费，零 DDL、零写库、不改产物文件 | `orchestrator/knowledge.py`、`normalize/furniture.py`、`db/repositories.py`、`api/main.py` | W5、AF-T6（**已裁＝甲＋乙合取**）、AF-T7（**已裁甲・归根侧 REQ-143 本行**） | **三半拆分**：① 取材剔除半边**已随 W5 落地**（两处出口＝`_heading_evidence` 与 `_page_bodies`，判据入 `KA-29`）；② 两枚意图键 `removed`／`emphasis` **未开始**（AF-T7 已裁甲，动码等需求方「开始执行」）；③ 读侧家具标记半边**已落地（2026-11-01，取三案之甲）**——`PageData.furniture_blocks`＝类型维∪线维的块下标（纯函数 `furniture_blocks`），进程内缓存按 `(book_id, job_id)`、块编辑后作废本书本版键；产物与 `markdown` 一字不改，见「过程与证据」末行与 `api.md` ③ |
| W13 | **导读的成文规格**（第三令「知识点详情只负责总结与导读」的本仓半边）＝W7 长文腿的 prompt 按「学什么／为什么要学／有什么作用」三段成文，章态与点态各一档；prompt 正体在本仓（AF-T3 甲） | `engines/kp_gateway.py`、`orchestrator/knowledge.py` | W6、W7、AF-T8（**已裁甲**＝导读是详解长文的成文规格，零新列零新端点） | 未开始（随 W6／W7 落规格；动码等需求方「开始执行」） |
| W14 | **R11 甲-B 落地件・目录页条目腿**＝`normalize/toc.py` 三条纯函数（`toc_entries` 拆条目只剥行末引导点与印刷页码／`estimate_print_offset` 取 (命中页−印刷页) 众数／`locate_entries` 只认 `印刷页＋偏移±1` 窗口内命中）＋ `orchestrator/knowledge.py` 取证第三趟把定位结果并成合成 heading 读数（真实页＋块序＋由条目行形状给的层级）＋ 窗口外条目仍成行、条目名进 `result.unlocated_outline_entries`（`KP_RESULT_KEYS` 第十六键）。判据正文＝`KA-33`，`KA-5` 同批改述 | `normalize/toc.py`（新建）、`orchestrator/knowledge.py`、`db/repositories.py`、`docs/design/knowledge-analysis.md`、`docs/architecture/api.md` | 需求方 2026-10-27 批文（R11＝甲-B 全案、子一保留点名、子二正本归本仓） | 判据已成文（`KA-33` 与 `KA-5` 已落）；已实施（2026-10-27） |
| W15 | **R11 批文的随步件・只目录 `stage`**＝`KpJobCreate.stage∈{full,outline}`（缺省 `full`、域外 422 先于六道受理门）→ `KpAcceptance.stage` → `run_knowledge_job` 的 points 腿条件发起（收尾与流水照跑）→ `KP_PAYLOAD_KEYS` 四键扩五键并在进度面回显。判据正文＝`KA-34` | `schemas.py`、`orchestrator/knowledge.py`、`db/repositories.py`、`api/main.py`、两份 docs | 需求方 2026-10-27 第五令（「这次只梳理目录」的载体＝加一枚只目录入参，不按页区间划窄） | 判据已成文（`KA-34` 与 `KA-27` 键数已改）；已实施（2026-10-27） |
| W16 | **目录阶段退出模型（需求方 2026-10-27 裁决・甲）**＝`build_outline` 改确定性成行（条目腿为正本、零条目退 heading、层级取读数 level、排序取条目序、父取前置章、`summary` 空、`model_ref` 记 `rule:toc`／`rule:heading`）＋撤 `KnowledgeEngine.outline` 协议腿与 `kp_gateway` 的目录调用及其提示词＋`LedgeredKnowledgeEngine` 只包知识点腿；`KA-5`／`KA-33`／`KA-34` 三处同批改判、新条 `KA-35` | `orchestrator/knowledge.py`、`engines/base.py`、`engines/kp_gateway.py`、`docs/design/knowledge-analysis.md`、`docs/architecture/api.md`、`docs/architecture/code-map.md` | 需求方 2026-10-27 裁决（真机只目录一轮在目录步 `ReadTimeout`：本机 8~21 字符/秒、b05 输出约 1.3 万字符，300 秒墙必挂） | 已执行（2026-10-27：`build_outline` 改确定性成行、引擎协议腿与目录提示词撤除、`LedgeredKnowledgeEngine` 只包点腿；定向与全量门禁见「过程与证据」同日行；真机整本只目录一轮 202／0.14s 收口、156 行、零调用账） |
| W17 | **三案落地件**（真机第一轮后出裁，三问全取甲案）＝① `toc_entries` 行内第二条目再拆（只切分不改字）；② 目录页**页级命中率复核**（偏移标定成功且某页过半未定位 ⇒ 整页剔除，页码进 `result.dropped_toc_pages`＝第十七键）；③ `finalize_batch` 翻面前**作废旧候选**（同书／非本批／`candidate`／`confirmed=0`／锚点页在区间内 ⇒ `rejected`，计数进 `result.superseded_rows`＝第十八键）。判据正文＝`KA-33` 两条新增与 `KA-36`；`KP_RESULT_KEYS` 十六键扩十八键 | `normalize/toc.py`、`orchestrator/knowledge.py`、`db/repositories.py`、`docs/design/knowledge-analysis.md`、`docs/architecture/api.md` | W16 已落地、真机第一轮读数出三处质量缺陷（符号表页误收／黏行与折行丢章／重跑叠加） | 已执行（2026-10-27：三案落地＋四类守护用例；全量 **1072 passed／18 skipped**、`ruff check src tests scripts` All checks passed；真机复验＝8902 重启后「整本只目录」与「窄区间重新解析」两轮，读数见「过程与证据」） |
| W18 | **第六令落地件・目录腿重做（模型判页＋逐页整理为正腿，确定性条目腿退为回落）**＝`engines/base.py` 的 `KnowledgeEngine` 协议加三枚目录腿（`toc_pages`／`toc_entries`／`toc_verify`，docstring 随 `KA-35` 改判撤「目录阶段不在这里」）＋ `engines/kp_gateway.py` 三腿提示词（判页回页号集、整理走三段竖线行而不是 JSON、核对每条一形；请求体闭集不变）＋ `orchestrator/knowledge.py:build_outline` 两段式改造（重新收引擎参数：判页 → 逐页整理 → 单页失败该页回落 `KA-33` 条目腿 → 印刷页码走偏移标定直查定锚 → 窗口内查不中的坏行 `_verify_outline_entries` 攒批一次核对 → 附件页成行并标 `meta.toc_role`）＋ `normalize/toc.py` 两枚常量（`TOC_PAGE_SCAN_LIMIT`／`TOC_PAGE_SAMPLE_MAX_CHARS`）与 `reference_role_for_name` ＋ `db/repositories.py:KP_RESULT_KEYS` 十八键扩二十二键与 `kp_batch_row` 四新键。判据正文＝`KA-35`（改判）／`KA-37`／`KA-38`／`KA-39`／`KA-25`／`KA-27`／`KA-33`（回落腿定位） | `engines/base.py`、`engines/kp_gateway.py`、`orchestrator/knowledge.py`、`normalize/toc.py`、`db/repositories.py`、`docs/design/knowledge-analysis.md`、`docs/architecture/api.md` | 需求方第六令（真机第二轮目录完全不可验收）；判据已于 2026-10-27 落文档、五枚文档门 61 passed | 判据已成文（2026-10-27）＋**码已实施**（2026-10-28，TDD 先红后绿）＝五件落点全部落地：`engines/base.py` 协议三腿＋六枚读数 dataclass、`engines/kp_gateway.py` 三腿提示词（`toc_pages:337`／`toc_entries:379`／`toc_verify:410`）、`orchestrator/knowledge.py:build_outline` 两段式（判页→逐页整理→单页回落→偏移标定直查→坏行攒批核对→附件页成行）、`db/repositories.py:KP_RESULT_KEYS` 二十二键（`repositories.py:94-97`）、`normalize/toc.py` 两枚常量＋`page_sample_text`＋`reference_role_for_name`＋`anchor_block_for_name`。**本轮另补一处实现与条文不符的缺陷**＝`page_sample_text` 原「整块丢弃」在目录页每块 ≥400 字符时出**空样本**（真机 b05 目录页约 1.5 KB／3 枚块），判页腿因此把整本读成「没有目录页」⇒ 改按预算逐块截字，`KA-37` 落点格随步点名该函数。守护补 27 枚缺席用例（较 W17 收口 +55）＝`TestModelLedOutline` 11／`TestPrintPageAnchorAndVerify` 6／`TestFrontMatterRows` 5／`test_normalize.py::TestPageSampleAndIdentityBits` 5（含 KA-39 回落腿身份位接线的先红后绿），适配器侧另有 `TestTocPageSelectionAndOrganizeLegs` 20＋`TestTocVerifyLeg` 7；`TestDeterministicOutline` docstring 随 `KA-35` 改判改述为回落面。全量 **1127 passed／18 skipped（45.22s）**、`ruff check src tests scripts`＝All checks passed；两枚变异校验见「过程与证据」同日行。8903 侧「重新解析」弹窗改队头／队尾两枚下拉归根侧承接（透传入参形状不变，本案 W18 不含该半边） |
| W19 | **丙案落地件・目录行边整理边落库（消掉受理到落行之间的 404 窗口）**＝`orchestrator/knowledge.py:build_outline` 加可选 `sink`（`land` 逐页交新一批 `draft` 行、`repoint` 交定稿时读数变了的已在册行；`sink=None` 时逐字同 W18 形状）＋逐页落行面只收**模型整理成功**的目录页（回落页要全书偏移才能做 `KA-33` 页级复核，先落再删等于造出没人认领的行，留在末批）＋附件行与 heading 回落行的成行位置判据一字不动（`KA-39` 的前后置判断要全书知识点页，仍在末批成行；真机 b05 把 p313 的参考书目排到第 1 章之前那条形状另案登记，不在本案）＋ `db/repositories.py:KpRepository.repoint_draft_outline`（同批定稿写手，`WHERE` 四条件＝`book_id`∧`gen_batch`∧`gen_status='draft'`∧`confirmed=0` 再逐条钉 `node_id`，只动 `anchors`／`order_hint`／`parent_id` 且**不递增 `rev`**）＋ `orchestrator/jobs.py:JobRunner` 的受理在途登记＋ `api/main.py:get_kp_job` 的 404 判据改「行／流水／在途登记三者全无」并与该端点 docstring 对齐。判据正文＝新条 `KA-40`；随步改判＝`KA-5` 落点一句、`KA-39` 边界一句、`## 失败与质量语义` 新增一行、`## 验收判据` 第 14 条 | `orchestrator/knowledge.py`、`orchestrator/jobs.py`、`db/repositories.py`、`api/main.py`、`docs/design/knowledge-analysis.md`、`docs/architecture/api.md`、`docs/architecture/code-map.md`、两份测试 | 需求方 2026-10-28 裁决「按丙方案改」：真机批次 `afkp-8d5978d985eb` 的进度面在判页→整理→核对约 10 分钟窗口内连吃 19 枚 404，而同期三笔调用账全部 `success`——读面报「不存在」把活批次读成了死链路 | 已执行（2026-10-29：判据已成文＋TDD 先红后绿（红＝`TestProgressiveOutlineLanding` 首跑 **8 failed／2.34s**，绿＝同批 8 passed）＋五处文档随步（`KA-40` 条文与四条件改判、`KA-5`／`KA-39`／失败语义表／验收第 14 条、`api.md` ④ 与门槛表与守护列、`database-design.md` 的 `rev` 行与第 9 步、`code-map.md` 四行）；全量 **1136 passed／18 skipped（49.48s）**、`ruff check src tests scripts`＝All checks passed。真机一轮仍待发＝8902 需重启才吃到本轮码（重启归人工发令），落点与两处自纠见「过程与证据」同日行） |
| W20 | **第九令件①②落地件・条目名只过一把行清洗**＝`normalize/toc.py` 新增 `clean_entry_rows`（黏行拆分 → 行末引导点与印刷页码剔离 → 无页码碎片拼回，三步与 `toc_entries` 回落腿同一条腿），`_split_block` 改调它、`build_outline` 的整理腿逐行入册处也改调它（回收出的行末数字填模型 `printed_page` 的缺位）＋ `_squash` 加一步闭集标点折算（`。`→`.`、`、`→`,`，不删其它标点、不折编号）＋ 清洗后的名字才派生 `norm_key`。判据正文＝`KA-41`；随步改判＝`KA-33` 边界一句、`KA-38` 比对口径一句 | `normalize/toc.py`、`orchestrator/knowledge.py`、`tests/unit/test_normalize.py`、`tests/unit/test_knowledge_pipeline.py` | 第九令件①②（根侧 `DEFECT-027`）＋ 2026-10-29 授权 | 已实施（2026-10-29，TDD 先红后绿）＝`normalize/toc.py` 新增 `clean_entry_rows`／`_strip_trailing_leaders`／`entry_level`，`_squash` 折标点异形；`_entries_from_pages` 的模型腿经 `_cleaned_model_entries` 走同一把清洗。红＝新 helper 未在时 `ImportError`；首绿 **1 failed／262 passed**（黏行拆分那条），修后 **263 passed**。AF 全量 **1159 passed／18 skipped（55.16s）**、`ruff` All checks passed；`KA-41` 两句随步 |
| W21 | **第九令件④落地件・读面派生正文区间两键**＝`orchestrator/knowledge.py` 把 `_section_spans` 的算式抽成纯函数 `section_spans_of(ordered_pages, *, first_page, last_page)`（原函数改调它，逐字同读数）＋ `api/main.py:_kp_node_out`／`get_kp_workspace` 用同一处算式给每枚目录行发 `page_start`／`page_end`（末行到 `af_books.page_count`，该列缺 ⇒ 折回起点页；点行两键＝锚点 min／max；`rejected` 行不进序）＋ `schemas.py:KpNodeOut` 两枚可空键。零 DDL、零新端点，根侧 8900 字节转发零改码。判据正文＝`KA-42` | `orchestrator/knowledge.py`、`api/main.py`、`schemas.py`、`docs/architecture/api.md`、两份测试 | 第九令件④（＝第八令 Q3 甲改判为 Q3 甲′，根侧 W19①） | 已实施（2026-10-29，TDD 先红后绿）＝`section_spans_of` 纯函数抽出、`api/main.py:_kp_page_spans`／`_page_range_of`／`_kp_node_out` 三处发两枚派生键（目录行只认 `candidate` 行、末行到 `af_books.page_count`，该列缺 ⇒ 折回起点页；点行＝自己锚点 min／max），`KpNodeOut` 两枚可空键。红＝unit `ImportError` ＋ contract **5×`KeyError: 'page_start'`** → 绿 12 枚新例。AF 全量 **1159 passed／18 skipped（55.16s）**、`ruff` 全绿；`KA-42` 与 `api.md` 两处随步。消费面＝根侧 W21 两枚下拉与中栏渲染 |
| W22 | **第九令件⑤落地件・`stage:'content'` 只补内容**＝`schemas.py:KpStage` 加第三值 ＋ `accept_kp_job` 插一道在册目录行门（零枚 ⇒ 422 点名「先解析目录」，排在页区间之后、在飞之前）＋ `run_knowledge_job` 该值下跳过 `build_outline`、由 `_outline_nodes_from_book` 重构在册目录行 ＋ `extract_points` 的节调用回复多带一枚节级总结并写进该节目录行（第五条系统腿 `set_outline_summary`，`WHERE` 四条件＋逐条钉 `node_id`、不递增 `rev`）＋ `finalize_batch`／`reject_superseded_candidates` 的作废面按 `stage` 收到点行 ＋ `KP_RESULT_KEYS` 二十三键（`outline_summary_rows`）＋ `engines/kp_gateway.py:_points_prompt` 补节级总结一段。判据正文＝`KA-43`；随步改判＝`KA-26` 合法父来源、`KA-34` 值域三值、`KA-36` 作废面收口、`KA-27` 键数 | `schemas.py`、`orchestrator/knowledge.py`、`db/repositories.py`、`engines/kp_gateway.py`、`api/main.py`、`docs/architecture/api.md`、三份测试 | 第九令件⑤（根侧 W18）＋ 2026-10-29 授权 | 已实施（2026-10-29，TDD 先红后绿）＝`KpStage` 三值／受理门在册目录行（零枚 422）／`_registered_outline_rows`（本行起点页 ∈ `[page_start,page_end]`）／`_outline_nodes_from_book`／`extract_points` 跳过锚在目录页的行并把名字并入 `unlocated_outline_entries`／`set_outline_summary` 第五条系统腿（`WHERE` 五条件、不递增 `rev`）／`KP_RESULT_KEYS` 二十三键（`outline_summary_rows`）。红＝`TestContentOnlyStage` 与契约三形先落、`SectionPoints` 导入前套件红 → 绿 `test_knowledge_pipeline.py` **184 passed**（`-k TestContentOnlyStage` **10 passed**）、AF 全量 **1176 passed／18 skipped（68.98s）**、`ruff` 全绿。真跑窗口由第九令「走一轮真实调用数学分析原理试试」承接，只取有界页区间 |
| W23 | **目录行派生区间改树序判＋reference 行不拼**（`DEFECT-028`；根侧 REQ-143 W28）＝`section_spans_of` 的终点由「排序后下一行起点 − 1」改为**树序**（下一条 `depth <= 本行 depth` 的行起点 − 1，无则到全书末页）；`toc_role='reference'` 的行只发起点＝终点（它们的物理正文不在目录条目页，按树序拼会造出跨章巨区间）；起点页超出 `last_page` 的行不参与切分、读面发空键而不编假终点；末行是附件行时不补书尾段。`api/main.py:_kp_page_spans` 随步（在册 `candidate` 目录行、`af_books.page_count` 缺则折回起点页的既有判据不动）。判据正文＝`KA-42`（改判） | `orchestrator/knowledge.py:section_spans_of`、`api/main.py:_kp_page_spans`、`tests/unit/test_knowledge_pipeline.py::TestSectionSpansOf`、`tests/contract/test_api_v1_contract.py` | 2026-10-30 二次令文实核①（b05 章行区间退化为单页）＋ 2026-10-29 授权 | 已实施（2026-10-31，TDD）＝单测以「ch1 p9／首节 p9／ch2 p29」钉住章 9–28、节 9–19，reference 行只占自身一页；点腿取材与读面派生仍调同一处纯函数。AF 全量 **1210 passed／18 skipped（51.94s）**、`ruff` All checks passed |
| W24 | **四值类型读键**（根侧 REQ-143 W26；原 T13 甲的可写键改判为自动派生）＝`normalize/toc.py` 新增 `node_type_of`／`node_types_of`：按序首中＝本行或任一祖先 `meta.toc_kind='exercise'` ⇒ `exercise`；其余 `meta.toc_role='reference'` ⇒ `other`；剩下的 `depth<=1` ⇒ `chapter`、`>=2` ⇒ `point`（沿父链取祖先 `toc_kind`，父不在这一批时只按本行判）。`schemas.py:KpNodeOut.node_type` 四值 `Literal`，`_kp_node_out`／`get_kp_workspace`／`declare_kp_outline`／`export_kp_book` 逐行发键；生成侧 `extract_points`（`content` 只对 `point` 发问）／`extract_chapters`（只取 `chapter`）／`regenerate_section_summary`（非 `point` 判 422）共用同一处判据。**零写键、零 DDL、零 ALTER**；`stage='content'` 不建点行已由 `KA-43` 承载（`full` 旧通路仍落，界面不发起）。判据正文＝`KA-44` | `normalize/toc.py`、`schemas.py`、`api/main.py`、`orchestrator/knowledge.py`、`tests/unit/test_knowledge_pipeline.py::TestNodeTypeOf`、`tests/contract/test_api_v1_contract.py` | 2026-10-30 二次令文「标注正确的类型」＋ 2026-10-29 授权 | 已实施（2026-10-31）＝`TestNodeTypeOf` 钉章／节／习题含子行／符号表四值；契约 `test_the_read_face_reports_the_four_value_type` 钉整册工作区逐行四值。AF 全量 **1210 passed／18 skipped**、`ruff` All checks passed |
| W25 | **总结生成腿**（根侧 REQ-143 W25）＝① 内容批次**先逐节、后逐章**两遍制：`extract_chapters` 按列现读节腿刚写下的 `summary` 与 `narrative_ref`，`_chapter_material` 组装三块（章首正文＝章起点页到首个子行起点页之前、为空时用章标题页兜底、其下各点 `summary` 逐条），每章一次调用出 `chapter_summary`／`chapter_narrative`：总结走 `set_outline_summary`（原值为空才写、不递增 `rev`）、导读走 `write_narrative`＋`set_narrative_ref`（已有指针或文件时**不覆盖**）；章腿只由 `content` 批次发起。② #129 容错腿 `_salvage_field`：回复整体不可解析时抢回已读完的字段（节腿 `section_summary`、章腿 `chapter_summary`／`chapter_narrative`），一个字段都抢不到才判该节／该章失败。③ 新增 `POST /api/v1/kp/nodes/{node_id}/summary`（`KpSummaryRegenIn` 只有 `if_rev`）＋`regenerate_section_summary`：复用单节取材，**覆盖**该行 `summary` 并 `rev+1`，判定序＝404→409（`if_rev`）→422（非知识点）→409（在飞批次）→422（无取证版本／无锚点页）→502（模型失败）→422（没给总结）→200 回显新 `rev`。`KP_RESULT_KEYS` 二十三→**二十八键**。判据正文＝`KA-45`；随步＝`KA-27` 键数与 `api.md` ④ 的 `/summary` 小节 | `orchestrator/knowledge.py`、`engines/kp_gateway.py`、`api/main.py`、`schemas.py`、`db/repositories.py`、`docs/design/knowledge-analysis.md`、`docs/architecture/api.md`、三份测试 | 2026-10-30 二次令文（章级总结／知识点只写总结／「重新生成总结」钮）＋ 2026-10-29 授权 | 已实施（2026-10-31）＝`TestChapterLeg`（章材料＝章首正文＋节腿刚写的总结、同页章改用标题页、已有导读不覆盖、失败章计数点名、只目录批次零章调用）、`TestRegeneratePointSummary`（覆盖并 `rev+1`、本节树序区间取材、非知识点 422 零调用、在飞 409、`if_rev` 409 旧值保留、模型失败 502 旧值保留）、`TestTruncatedReplyTolerance` 五形（节／章截断各保字段、无字段仍故障、空回复不折算）；契约三形（200 且 `rev+1`、章与习题 422、未知节点 404）。AF 全量 **1210 passed／18 skipped（51.94s）**、`ruff` All checks passed |

## 逐文件归属（交付清单，甲案的硬性交代）

| 文件 | 归属 | 说明 |
| --- | --- | --- |
| `src/axiom_flow/normalize/intents.py` | **本案新建** | 未跟踪、独立文件，可单独 `git add`（W3 回放正体：`relabels_by_block`／`apply_relabels`，`RelabelIntent` 用 `Protocol` 使 `normalize` 不依赖 `db`） |
| `src/axiom_flow/normalize/furniture.py` | **本案新建** | 未跟踪、独立文件（W4 `furniture_hints` 读时纯函数，零写库 ＋ W5 追加 `FURNITURE_TYPES` 与两枚取材纯函数 `furniture_texts`／`strip_furniture`，docstring 补「剔除只发生在读取投影」一段） |
| `src/axiom_flow/normalize/blocks.py` | **本案改动（三行）** | 对方 PLAN-025 未碰该文件（`git diff --stat` 该件只有本案 +3/−1）：import 一枚 `FURNITURE_TYPES`、注释一行、`_SKIP_IN_MARKDOWN` 的字面集合改为引该同源常量——页级 Markdown 组装与梳理取材剔的是同一组类型，一处判三枚另一处判两枚就会留下缝隙 |
| `src/axiom_flow/normalize/__init__.py` | **本案改动（一行）** | 包 docstring 补 `intents` 一句；其余行未碰 |
| `src/axiom_flow/normalize/toc.py` | **本案新建** | 未跟踪、独立文件（W14 三条纯函数 `toc_entries`／`estimate_print_offset`／`locate_entries` ＋ W17 的 `_INNER_PAGE` 行内第二条目再拆与 `drop_unlocated_toc_pages` 页级复核 ＋ W18 的四件：两枚封顶常量 `TOC_PAGE_SCAN_LIMIT`／`TOC_PAGE_SAMPLE_MAX_CHARS`、判页样本取材腿 `page_sample_text`（**本轮修一处实现与条文不符**：原「整块丢弃」在目录页每块 ≥400 字符时出**空样本**＝判页腿零输入，改为预算内逐块截字并保留块类型标注）、`reference_role_for_name` 回落腿身份位集合、`anchor_block_for_name` 核对命中落块辅助）。它是 `KA-33`／`KA-37`／`KA-39` 三处判据在本仓的正本落点 |
| `src/axiom_flow/engines/base.py` | **两方混合（W16 起）** | 原列在下方「本案零行」清单：对方 PLAN-025 在途 +70 ＋ W16 撤 `KnowledgeEngine.outline` 协议腿 ＋ W18 依 `KA-35` 改判重新把目录交回模型——协议加三枚目录腿（`toc_pages`／`toc_entries`／`toc_verify`，`engines/base.py:203/207/211`）与六枚读数 dataclass（`PageSample`／`ReferencePage`／`TocPagePick`／`OutlineEntry`／`TocVerifyItem`／`TocVerifyAnswer`），协议与 `points` 的 docstring 撤「目录阶段不在这里」改述为三条目录腿的正腿口径 |
| `src/axiom_flow/engines/kp_gateway.py` | **本案新建** | 未跟踪、独立文件（W6 生成腿正体：两阶段 prompt 组装＋唯一出站与四类故障归一＋`bind_batch` 批次身份缝 ＋ W11 三处＝新增模块级纯函数 `_review_note_block`（空备注返回空列表＝整段不出现）、`bind_batch` 多收一枚 `review_note` 并存字段、`outline`／`points` 的 prompt 组装各追加该段（备注只进 prompt，不进 `meta`、不加新请求键） ＋ W18 三枚目录腿（`toc_pages:337`／`toc_entries:379`／`toc_verify:410`）＝判页回页号集与附件页集（**域外页号整条弃用并披露**）、逐页整理回**三段竖线行而不是 JSON**（层级只认 1／2、页码只认阿拉伯数字，畸形一行只坏一行）、核对按「每条一形」回页号或「正文里没有这一行」；请求体闭集一字未加，故障仍归一 `EngineError` 保住单页失败域 |
| `tests/unit/test_engine_kp_gateway.py` | **本案新建** | 未跟踪（W6 定向 24 枚：出站形状／回复解析／故障归一／引擎身份与连接纪律 ＋ W11 的 `TestReviewNoteGoesIntoThePrompt` 两枚：绑定后备注入两阶段 prompt 且不进 `meta`／请求体闭集不变；空备注不留空标题） ＋ W18 的目录三腿用例两枚类＝`TestTocPageSelectionAndOrganizeLegs` 20（域外页号弃用、竖线行三种畸形容错、判页样本的截断与封顶）与 `TestTocVerifyLeg` 7（核对腿请求体闭集、「每条一形」的回复解析、判不出的条目回 `page_no=None`） |
| `src/axiom_flow/config.py` | **两方混合** | 对方 PLAN-025 在途 ＋ 本案 W6 三处：`kp_engine` 默认值改内置实名、新增 `engine_gateway_url`／`kp_timeout` 两键与其 `_ENV_MAP` 与尾斜杠归一、docstring 一段（退役键不复活的声明） |
| `src/axiom_flow/engines/factory.py` | **两方混合** | 对方在途 ＋ 本案 W6 的槽位登记（两元组各由空改一枚实名）与 `build_text_engine` 构造分支、`resolve_text_engine_name` 空名文案 |
| `src/axiom_flow/orchestrator/jobs.py` | **两方混合** | 对方在途 ＋ 本案 W6 的记账分流：`submit_knowledge` 提交期构造引擎、`_accounted` 抽出（自报 `accounts_externally` 者不包本仓账） |
| `tests/unit/test_engine_factory.py`、`tests/unit/test_config.py` | **两方混合** | 对方在途 ＋ 本案 W6 的槽位面两枚改判用例与配置面四枚新用例（含「退役键不是新键别名」的强化断言） |
| `src/axiom_flow/schemas.py` | **两方混合** | 对方 PLAN-025 在途 +209 ＋ 本案 `EditRequest.corrected_type` 与十枚闭集校验、`EditRecord` 回显字段 ＋ W7 三处＝`NARRATIVE_MAX_CHARS`（512_000，注释标明这一枚不背列宽、管的是单次请求能吃下多大一篇）、`KpNarrativeWriteIn`（`extra="forbid"`＋`content` 的 min/max 闸＋`if_rev`）、`KpNarrativeOut`（`narrative_ref`／`exists`／`content`／`bytes`／`rev`／`updated_at`） ＋ W11 三处＝`KP_REVIEW_NOTE_MAX_CHARS`（2000；注释写明「超限判 422 不截断」与 `NARRATIVE_MAX_CHARS` 同口径、`payload` 是 JSON 故落库不受列宽约束）、`KpJobCreate.review_note`（可选键＋`max_length` 闸）、`KpLedgerOut.review_note`（恒发，缺省空串而不是缺键） |
| `src/axiom_flow/db/repositories.py` | **两方混合** | 对方在途 +643 ＋ 本案 `EDIT_PAYLOAD_KEYS` 加同名键、`upsert_edit` 落载荷、两条 `list_edits_for_*` 的读侧复用 ＋ W7 的 `set_narrative_ref`（第四条系统腿，UPDATE 通路上这一列的唯一写者，值先过单段名闸、`if_rev` 钉在 WHERE、成功即 `rev+1`）与类 docstring「三个写者 → 四个写者」、`create()` 里 PLAN-025 R5 等裁那句的改判 ＋ W11 三处＝`KP_PAYLOAD_KEYS` 由三键扩为四键（加 `review_note`，注释交代「落载荷不落 `note` 实体列」的理由）、`KpBatchRow` 末尾加同名字段（默认空串，语义＝批次输入而非读者留言）、`create_kp_batch` 把该值写进 `payload` ＋ W18 一处＝`KP_RESULT_KEYS` 由十八键扩为二十二键（`outline_toc_pages`／`outline_fallback_pages`／`verified_outline_entries`／`reference_rows`，`src/axiom_flow/db/repositories.py:94-97`），零新列、零 DDL——四枚都是批次读数的计数与点名面，落在既有 `result` JSON 列里 |
| `src/axiom_flow/api/main.py` | **两方混合** | 对方在途 +358 ＋ 本案 `PUT …/blocks/{index}/edit` 的闭集错误面与 `get_page` 的回放 ＋ W6 的 `create_app` 默认装配（`make_knowledge_engine`）与 `_kp_text_engine` 文案改档（撤「请配置」）＋ W7 两条路由 `put_kp_narrative`／`get_kp_narrative`（成对开在 `…/kp/nodes/{node_id}/narrative`，插在 `confirm_kp_node` 与 `declare_kp_outline` 之间）与模块级投影辅助 `_kp_narrative_out`；改动集中在四处函数体内与 import 两行 ＋ W11 两处＝`create_kp_job` 把 `payload.review_note` 交给 `accept_kp_job`、`_kp_ledger_out` 从 `payload` 取该键回显（缺键给空串） |
| `src/axiom_flow/orchestrator/knowledge.py` | **两方混合** | 对方在途 1315 行（未跟踪）＋ 本案 `build_evidence_set` 的整本改判行装载与 `_heading_evidence` 的先回放后筛 ＋ W6 的 `bind_batch` 协议外缝调用（`getattr`＋`callable` 两行守卫）＋ W5 三处＝`EvidencePage` 加 `furniture` 只读投影字段、装载与派生分两趟并逐页挂家具读数、`_page_bodies` 送出剥离后的正文副本（`markdown` 本身不动） ＋ W7 的读腿 `read_narrative`（按 `KA-20` 同一条派生式现算路径、缺文件返回 `(名字, None)`、查不到行抛 `KpGateError(404)`）与 `write_narrative` docstring 末句改判（该列的写者＝第四条系统腿，不再写「R5 等裁」）、文件头与模块 docstring 的条文区间随步（`KA-29`／`KA-30` 入列，「长文与删除通路」改「长文两条腿与删除通路」） ＋ W11 四处＝`KpAcceptance` 加 `review_note` 字段、`accept_kp_job` 收该入参、`kp_batch_row` 把值交给流水行、bind 调用多传一枚关键字参数（注释：声明了 `bind_batch` 就必须认这一枚参数） ＋ W18 两处＝`build_outline` 两段式改造（重新收 `engine` 参数：`_toc_pages` 判页 → 逐页 `engine.toc_entries` 整理 → 单页 `EngineError` 只回落那一页的条目腿并把页号进读数 → 印刷页码偏移标定直查定锚 → 窗口外坏行 `_verify_outline_entries` 攒批一次核对 → 判页给出的附件页经 `_reference_readings` 各成一枚顶级行）与成行循环的**身份位两路分派**（`rule:toc` 回落行按 `reference_role_for_name` 判、`rule:heading` 行恒 `knowledge`，模型正腿的附件行由判页读数给——本轮 KA-39 的先红后绿缺口就补在这一格，原码回落行永远记 `knowledge`） |
| `tests/unit/test_db.py`、`tests/contract/test_api_v1_contract.py`、`tests/unit/test_normalize.py` | **两方混合** | 对方在途 ＋ 本案新增用例一律追加在文件／类末尾，不动其断言；`test_api_v1_contract.py` 另含 W6 的两处改判（空槽位文案）与一枚默认装配新用例；`test_normalize.py` 另含 W5 的 `TestFurnitureTexts`（4 枚＝类型维三值／空正文不充数／线维只加给携带页）与 `TestStripFurniture`（6 枚＝整行相等才剔／包含不剔／空集恒等／全角与空白变体命中／ATX 标题行照剔／其余逐行原样回传）共 10 枚与 `_typed_block` 辅助 ＋ W7 的 `TestKpNarrativeLegs` 14 枚（两条腿正反对账：写落文件又落列并回显／读腿读回同一篇／同文重发幂等且 `rev` 递增／章与点各带自己的长文／陈旧 `if_rev` 判 409 且先于任何磁盘写入／空或超限 422 且不留痕／上限含端点／形状闸先于查行／无行 404 且两腿不碰磁盘／请求模型给不出路径与文件名／缺文件 `exists=false` 且不改列／从未有过长文的行／盘上有文件而列为空仍按派生名读出／整册工作区不带正文），路径由用例按 `KA-20` 派生式独立算一遍而不是复用返回体 ＋ W11 的 `test_db.py::TestKpLedgerRow`「载荷三键」改判为**四键**（含值断言，seed 里带上中文备注）与 `test_api_v1_contract.py` 一枚新用例（提交→202、进度面回显、仓储 `payload` 存档、第二次无备注提交回空串、超长 422 且流水行数不变——六条全零写入，备注由请求模型 `max_length` 当场拒） ＋ W18 的 `test_normalize.py::TestPageSampleAndIdentityBits` 五枚（判页样本带块类型标注且空块跳过／一枚 ≥400 字符的块仍出**非空**样本并逐字不越封顶／两枚封顶常量数值钉死＝`(10, 400)`／`reference_role_for_name` 七类判名与判序／`anchor_block_for_name` 包含命中与回落页首块），同批把该件的 `normalize.toc` 导入改多行以带上新符号 |
| `tests/unit/test_knowledge_pipeline.py` | **两方混合** | 对方未跟踪新件 ＋ 本案 `TestHumanRelabelIsReplayed` 四枚与 `_relabel` 辅助 ＋ W6 的 `_GatewayShapedEngine` 与三枚分流用例 ＋ W5 的 `TestCurationStripsFurniture` 五枚与四枚场景辅助（`_write_furniture_page`／`_furniture_scene`／`_captured_bodies`／`_FURNITURE_HEAD`）＋ W7 的两处 docstring 改判（`TestNarrativePlacement` 头与 `test_the_narrative_path_does_not_write_the_narrative_ref_column` 正文：原写「R5 未裁故该列无写者」，现改「文件腿的越域判据＝写文件不顺手改列，写列一侧由 `TestNarrativeRefSystemLeg` 守」）——**断言一条未改、仍绿** ＋ W11 两处＝`_GatewayShapedEngine` 的 `bind_batch` 认 `review_note` 关键字参数并记进 `notes`（形状桩与真适配器同形），`TestCallLedger` 新增一枚两半用例（备注既到绑定面也进流水 `payload`）——**该用例首版把断言写在 `run_knowledge_job` 上，跑绿了绑定半边却查不到流水行**：批次流水的发出点在 `JobRunner._record_kp_batch` 的单独会话里，执行体本身不写它，故改走 `runner.submit_knowledge` 一途，见「过程与证据」 ＋ W18 的三枚新类与一枚夹具＝`TestModelLedOutline` 11（模型条目为正本含条目序／父链／`steps` 读数、单页故障只回落那页且 `model_ref` 两值并存、回落页进账面 `outline_fallback_pages`、判页空集整本回落且整理腿零调用、判页 `EngineError` 同形、`level=3` 计 `dropped_depth`、无前置章不造父、调用上界、判页样本＝首尾各 `TOC_PAGE_SCAN_LIMIT` 页、短书逐页一次不重复）、`TestPrintPageAnchorAndVerify` 6（查得中零调用、坏行一批一次、候选面逐条给行、域外页号 99 整条不认、判「不存在」走未定位、偏移标定不出则核对腿零调用）、`TestFrontMatterRows` 5（附件页成顶级行标 `reference`、`kind` 空值折名字判、取证集外页不成行、**回落腿按名字判**、零 ALTER）；同批新增模块级夹具 `_two_toc_page_scene`（两枚目录页＋五枚正文页，绝对页＝印刷页＋2），并把 `TestDeterministicOutline` 的 docstring 随 `KA-35` 改判改述为**回落面**（原写「条目腿为正本」）——该件断言一条未改、仍绿 |
| `tests/unit/test_db_knowledge.py` | **两方混合（W7 起）** | 原列在下方「本案零行」清单：W7 打破该口径——追加 `TestNarrativeRefSystemLeg` 七枚（整表逐列对账只动指针与两枚序号／时刻列、不改判确认位与来源列、背书域白名单不含这列且 `update()` 抛、非法指针参数化含空串、陈旧 `if_rev` 不落列、不存在的行指不过去、其余三条腿仍把这列留空），模块 docstring「三个写者 → 四个写者」与 DesignRef 加 `KA-30`；同批删掉一处本会话自己误留的重复 `WATCH` 元组 |
| `docs/architecture/api.md`、`docs/architecture/database-design.md`、`docs/architecture/code-map.md`、`docs/architecture/overview.md`、`docs/design/parsing-management.md`、`docs/design/knowledge-analysis.md`、`docs/design/model-integration.md`、`docs/adr/0004-…md`、`docs/trackers/todo.md` | **两方混合** | 对方在途 ＋ 本案 W8 净集随步段（`api.md` ③ 段、`database-design.md` 的 edit 载荷行、`code-map.md` 的 normalize 行、`parsing-management.md` 的 PM-5 改判与新增 PM-19、`knowledge-analysis.md` 的 KA-2 末列、todo 的 PLAN-026 镜像行状态）＋ W6 随步段（`api.md` ④ 的槽位与探活两行、`code-map.md` 的 engines／config 两行、`knowledge-analysis.md` 的 KA-22／KA-23 两行与「实现批／已知欠账」两处、`model-integration.md` 配置键表三行、ADR 0004 决定 6 的修订括注）＋ W5 随步段（`knowledge-analysis.md` 新增 `KA-29` 与 `KA-2` 末列改指、`parsing-management.md` 的 PM-19 落点列两枚新函数、`code-map.md` 的 normalize 行与 `orchestrator/knowledge.py` 行两处条文区间）＋ W7 随步段（`knowledge-analysis.md` 新增 `KA-30` 与 `KA-11` 的写者分域改判为四条腿、`api.md` ④ 的 `PUT`／`GET …/narrative` 两条小节与端点总表两行、`code-map.md` 的 `api/main.py` 行「④ 梳理与复核九条，长文两条腿成对开在节点路径上」与条文列表加 `KA-30`、`orchestrator/knowledge.py` 行的「长文两条腿」措辞与条文区间 `KA-29~KA-30` ＋ W11 随步段（`knowledge-analysis.md` 新增 `KA-31` 与 `KA-27` 的 `payload` 四键闭集／落点格补 `KP_PAYLOAD_KEYS`、`api.md` ④ 的请求体段与门槛表首行与进度面示例及一条新 bullet、`database-design.md` 的 `payload` 行加 kp 形状；`code-map.md` 本轮**无行需改**（模块行不涉新增的函数级键））；**另有四处为前轮漏随的事实更正**＝`database-design.md` 的「仍缺两段」按 W6 落地改判为一段（文本引擎适配器已接入）、`database-design.md` 当前范围段与 `knowledge-analysis.md`「实现批」行、`overview.md` 能力矩阵一行的端点数按 W7 落地由七条改为**九条**（运行时实测九条路由，`overview.md` 由此从下方「本案零行」清单移出，本案在它就一行）；＋ W18 随步段（`knowledge-analysis.md`＝`KA-5` 的「目录行确定性成行、不再调模型」随 `KA-35` 改判改述为三档成行、`KA-37` 落点格点名 `page_sample_text` 的预算内截字、`KA-5`／`KA-37`／`KA-39` 三处守护列补上新类名；`api.md` ④＝`stage` 段两行改判（`outline` 不再是零调用批次，目录步上界＝1＋目录页数＋核对 1 次）、进度面示例 `result` 加四键与四条 bullet；`code-map.md`＝`orchestrator/knowledge.py` 行的两阶段描述与条文区间 `KA-29~KA-39`、`engines/` 行的目录三条腿描述与 `KA-35／KA-37／KA-38`、`normalize/` 行的 `toc.py` 描述与 `KA-36／KA-37／KA-39`）。**本轮清一处前轮漏随**＝W14~W17 的目录轮当时只随到 `knowledge-analysis.md`，`code-map.md` 里 `KA-36~KA-39` 零读数、`api.md` ④ 的 `result` 示例止于十八键且 `stage` 段仍写「目录阶段确定性成行」——三处按现状补齐后复跑文档门；提交归对方或人工，本案不碰 git |
| `docs/plans/2026-10-26-annotation-replay-and-kp-text-engine.md` | **本案新建** | 本壳 |

**本案零行、纯对方在途的文件**（回退与提交都别看错）：
`engines/__init__.py`、`orchestrator/__init__.py`、`db/llm_ledger.py`、
`tests/unit/test_db_llm_ledger.py`、
`tests/integration/test_llm_call_ledger.py`、`tests/support/llm_ledger_schema.py`、
`docs/trackers/project-status.md`、`docs/plans/2026-10-23-kp-curation-implementation.md`、`tests/contract/test_architecture_documents.py`。
（`docs/architecture/overview.md` 原在此列，W11 补前轮漏随的端点数更正后移出——本案在它就一行，改的是能力矩阵那格的措辞与计数。）
（`engines/base.py` 亦原在此列，W16 撤 `KnowledgeEngine.outline` 协议腿、W18 依 `KA-35` 改判重新收三枚目录腿后移出——协议是「目录阶段以模型为正腿」这条判据的形状面。）

> W6 落地把原列在此处的 `src/axiom_flow/config.py`、`engines/factory.py`、`orchestrator/jobs.py`、
> `tests/unit/test_engine_factory.py` 四件移入上表「两方混合」；W7 再把 `tests/unit/test_db_knowledge.py`
> 一并移入（该件的 `TestNarrativeRefSystemLeg` 是「UPDATE 通路上唯一写者」这条判据在库内侧的守护）——
> 立案时的「本案零行」到 2026-10-26 已不成立，交接口径随步。

> **给对方的免误操作提示**：本案代码入库前，若要回退请在上述「混合」文件用逐 hunk 选择（`git checkout -p`），
> 不要对整文件 `git checkout --`——那会连带抹掉 PLAN-025 批的在途行。

## 过程与证据

| 日期 | 事项 | 读数与结论 |
| --- | --- | --- |
| 2026-10-26 | 三条决定性事实取证（本轮立项前核查） | ① `EDIT_PAYLOAD_KEYS = ("block_type","verdict","corrected_text","corrected_bbox")`（`db/repositories.py:63`）无类型改判键，且 `block_type` 是产物快照的只读回声（`api/main.py:574`）——**人工改块类型今天无路**；② `orchestrator/knowledge.py:244` 直接 `BlocksPage.model_validate_json` 读产物、`:253` 取 `row.section_kind or ""`，取材腿**零编辑回放**，故改判不改目录；③ `engines/factory.py:25-26` 两元组皆空、`build_text_engine`（`:76`）恒抛，`api/main.py:873` 因 `knowledge_engine_factory is None` 或空名判 503——**梳理生成腿恒不可用**。 |
| 2026-10-26 | 本仓工作树基线指纹 | HEAD `46f5f3f`；`git diff --stat`＝21 文件 +2622/-185；未跟踪 7 文件 4831 行（`wc -l` 读数见「授权前提」）。七条 `kp` 路由已在在册（`api/main.py:886/919/946/972/1017/1055/1094`），`write_narrative`（`orchestrator/knowledge.py:1258`）有测试无 HTTP 面。 |
| 2026-10-26 | 根侧八枚四裁的落点核对 | 标注集＝`BlockType` 十枚全集（`schemas.py:46-58`）／扩载荷落 AF 侧／两腿同轮／总结入根侧 `summary`、详解入本仓 narrative 写腿——均与本壳范围一一对应，无第五处需要本仓改表。 |
| 2026-10-26 | 开工前对账（本仓代码进度／三份契约正本／根侧消费面三路）出三处冲突 ⇒ R8~R10 | ① **适配器出站与记账**：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md) 决定 2「不再经 8900 网关」＋ PLAN-025 R6 甲案「本组件出站恒直连，按注释取 `local`」＋ `api.md:21-23` 与根侧 `api-contracts.md:738` 各自重申，vs 根 REQ-143 行 S1「生成调用全经 8900 网关、密钥不下发」；且若走网关，8900 自记一行＋本仓 `KA-23` 包装器（`orchestrator/knowledge.py:854`）＝一次调用两行账 ⇒ 登 **R8**，W6 停手。② **长文写腿**：PLAN-025 W6「本案不新增端点，加端点属契约变更须先改设计」＋ 其 R5 三案未裁＋ 守护用例现断言 `write_narrative` **不写** `narrative_ref` ⇒ 登 **R9**，W7 停手。③ **`section_kind` 写手**：`database-design.md`「本轮只建列」＋ `knowledge-analysis.md` 无授予写手的 `KA-*` 条文位＋ 根侧 REQ-139 的 A3 仍「待判」 ⇒ 登 **R10**，W5 停手。**净集 W2~W4 零冲突**，按用户「你判断梳理没问题就开始执行」放令先开工。 |
| 2026-10-26 | 两处分类纠错（本壳原稿之错，非对方条文） | ① 「`api.md` ②」错＝块编辑与页数据按 `api.md:86` 属 **③ 解析结果与 PDF 对照**，② 是 af_* 数据查询（`:85`）——已改「关联设计」与 W8 行。② 根侧透传面的落点是 `api-contracts.md` **§④**（`:730`「④ 数据透传·Axiom-Flow」；`:761` 的「规划契约（8902 已实现、8900 透传待实施）」表现列 4 枚，`kp` 七条应入此表），**不是 §⑥**（`:987` §⑥＝8900 自有学习域）；对方 `api.md:47` 自己也指向 ④。根侧 REQ-141 行 S1 里的「§⑥」按根侧 G2 冻结不改，落点以 §④ 为准并在根壳登记。 |
| 2026-10-26 | W2 扩载荷落地（先红后绿） | 红＝`EditRequest` 无 `corrected_type` 字段、`EDIT_PAYLOAD_KEYS` 四键不含它 ⇒ 提交类型改判被当未知键拒；绿＝`schemas.py` 加可空字段并由 `BlockType` 十枚闭集校验（闭集外 422、`null` 合法＝「只记别的维」）、`db/repositories.py` 键入载荷、`EditRecord` 回显。零新列由 `tests/unit/test_db.py` 的表族形状守护复验。定向门禁后全量＝939 passed／18 skipped。 |
| 2026-10-26 | W3 读侧回放两处落地（含一处框架事实取证） | 红＝同一条改判行下 4 枚用例分别读出 `'header'` 未被覆盖、取证 headings 未变；绿＝新建 `normalize/intents.py`（`relabels_by_block` 只收闭集内取值、`apply_relabels` 用 `model_copy` 只换 `type`），`api/main.py:get_page` 复用**一次** `list_edits_for_page`、`knowledge.py:build_evidence_set` 用**一次** `list_edits_for_book` 按页分桶（逐页查询会打出 N 条 SELECT）。**框架事实（决定判据形状）**＝pydantic v2 `model_copy(update=…)` 不重跑 `model_validator`，且 FastAPI `response_model` 对同型实例 `revalidate_instances='never'` 直通 ⇒「判成 `formula` 而产物无 `latex`」返回 **200**、`latex` 为 `null`，不炸 500；该形状由 `test_relabel_does_not_backfill_a_field_the_artifact_lacks` 钉死（回放只叠加判别维，绝不补内容字段）。匹配键＝`Block.index` 而非数组位置。定向＝`TestPagesAndManifest` ＋ `TestHumanRelabelIsReplayed` 30 passed；全量 947 passed／18 skipped。 |
| 2026-10-26 | W4 跨页重复线纯函数落地 | 红＝`ModuleNotFoundError: axiom_flow.normalize.furniture`；绿＝`furniture_hints`＝三条合取里的**跨页重复线**一维（≥3 页 **且** ≥半数页、纵向极差 ≤32px、分母为不同页号），文本折叠＝NFKC＋折空白＋casefold。反例两枚专防误判：正文重复定义句因纵向散开不成线、只在少数页出现的重复行不成线；同页两处不充数；无 `text` 的块不参与。定向 `tests/unit/test_normalize.py` 54 passed；全量 953 passed／18 skipped。 |
| 2026-10-26 | 文档门禁两红与修法（本轮踩到，写给后续轮） | ① `test_design_documents.py` 要求 PM 编号按**文档顺序**从 1 连续 ⇒ 新条 PM-19 不能插在 PM-5 之后，只能落在 PM-18 表之后另开小节；② `test_fixed_document_form.py::test_design_body_carries_no_process_narrative` 用 `\b(?:REQ\|PLAN\|ARCH\|DEFECT)-\d` 与 `\b20\d\d-\d\d-\d\d\b` 禁 design/architecture 正文出现根侧 ID 与日期 ⇒ 新条正文的原稿措辞（「本轮还没有消费方」「等裁 R10」「PLAN-026」）全部改写为设计态，分层理由改述为「另两条属读取方的渲染判据」，`furniture.py` docstring 改引本仓 PM-19。复跑四枚文档门＝46 passed。 |
| 2026-10-26 | 净集收口三门禁与工作树指纹 | `pytest tests -q`＝**953 passed／18 skipped（39.12s）**；`ruff check src tests scripts`＝**All checks passed**；四枚文档门＝46 passed。指纹：HEAD 仍 `46f5f3f`（本案零 git 操作），`git diff --stat`＝24 文件 +2964/−203（较立案指纹 +3 文件／+342/−18），未跟踪 10 件（本案新增 `normalize/intents.py`、`normalize/furniture.py` 与本壳三件）。逐文件归属见上表。 |
| 2026-10-26 | 已知未净半条（登记于 W4 收口；**取材半边已由 W5 收口，页数据半边转为待裁**） | 改判只作用在**判别维**：`GET /pages` 的 `markdown` 与磁盘 `.md` 产物一字不改，故「梳理展示去掉页眉页脚」目前只在标题序列／类型判据上生效，正文面仍含被改判成 `header` 的那行文本。该半条的消费面在根侧渲染腿（REQ-141 中栏共用同一渲染腿的裁决）与后续对 `markdown` 是否叠加覆盖层的裁定，本案不改产物文件（`PM-5`）。 |
| 2026-10-26 | W6 文本引擎适配器落地（先红后绿）与三处形状取舍 | 红＝`ModuleNotFoundError: axiom_flow.engines.kp_gateway`（收集期即炸，槽位面与配置面 5 枚默认值用例同红）；绿＝新建适配器＋`factory.py` 登记＋`config.py` 两键，定向 `test_engine_kp_gateway.py`／`test_engine_factory.py`／`test_config.py`＝**74 passed**。**三处取舍**：① **配置只落新键** `QED_ENGINE_GATEWAY_URL`／`AXIOM_KP_TIMEOUT`（内置默认 8900 与 360s，后者必须长于 8900 侧 300s 调用预算，由用例钉死大小关系）——根 `.env` 里在身的 `QED_LLM_GATEWAY_URL`／`QED_LLM_TIMEOUT` 是已退役键，复活等于推翻 `config.py` docstring 的退役清单；强化断言「设退役键读数不变」把这条钉在配置面。② **跳过自写账的判据＝适配器自报 `accounts_externally`**，不按引擎名判：名字是配置面可读的字符串，把它当记账判据等于让 `.env` 决定「一次调用落几行账」；`KpLedgerWiringError` 对桩引擎的既有守护因此一条不动仍绿。③ **批次身份经 `bind_batch` 这道协议外缝**交给适配器（`KnowledgeEngine` 协议不携带受理结果，而 8900 那条通路只认请求体里的归因）；调用点用 `getattr`＋`callable` 守卫，未声明者（含全部桩与 `LedgeredKnowledgeEngine`）零影响；未绑定即拒跑，宁可 500 前失败也不跑一批查不到主人的调用。**空回复单列一条故障**：思考模型把预算吃满时正文为零（根侧 `local_adapt` 已按「空正文一律不可用」定性），把它折算成「零产出」会让一节静默消失，故归一 `EngineError` 走 `KA-5` 的按节失败披露。 |
| 2026-10-26 | W6 一处门禁自纠：默认装配分支的 `NameError`（写给后续轮） | `ruff check` 报 **F821 Undefined name `build_text_engine`**（`api/main.py` 的 `create_app` 默认 `or` 分支）——`pytest` 全量却全绿：**④ 面契约用例恒注入桩工厂，正好把线上起服走的那条分支整个遮住**，缺 import 在真实读回 202 之前不会有任何人看见。补一枚契约用例 `test_the_default_wiring_reaches_the_slot_factory`（monkeypatch 同签名假工厂＋不注入 `knowledge_engine_factory`，断言默认分支收到槽位名并真跑出一批行）。变异校验＝临时撤掉那行 import → 该用例红（`monkeypatch.setattr` 找不到属性），恢复即绿。全量＝**985 passed／18 skipped**（较净集 +32），`ruff check src tests scripts`＝**All checks passed**。⇒ 本仓教训：**桩工厂覆盖不到的装配面要用例专门点名**，否则「接上了」只是没被证伪。 |
| 2026-10-26 | W6 措辞随步的落点与一处不动（登记不改的理由） | 已改三处＝`docs/adr/0004-…md` 决定 6 加「修订文本梳理半边」括注（直连自写的语义限定在图像槽位，文本槽位以 `KA-22`／`KA-23` 为准）、`knowledge-analysis.md` 的 `KA-22`（默认即内置实名＋两枚新键与超时大小关系）与 `KA-23`（一次调用只落一行；跳过本侧包装器的判据＝适配器自报而不是引擎名）。**不动的一处**＝PLAN-025 壳 R6 甲案那句「本组件出站恒直连」：该壳是对方的未提交在途件，本案按其交付纪律**只加不改其行**，措辞冲突由上面的 ADR 修订覆盖并在本行登记交接口径——对方收口时以 ADR 0004 现文为准。 |
| 2026-10-26 | W6 留一枚未裁参数（不擅自定值） | 适配器**不发 `max_tokens`**，取 8900 网关／provider 默认（本仓现有代码零处发该键，`clients.py` 只在非 `None` 时写入请求体）。根侧同类腿按用途给值（`services/learn/annotations.py` 的 700／900），而梳理整本目录的 JSON 数组回复长度无在册依据；AF 侧无 8900 本地通道的服务端默认值取证。**处置**＝真跑窗口（W10）若出现截断——表征是 `_drafts` 报「不是 JSON」或「空回复」——再由需求方定值并随步 `api.md` ④ 与该参数注释，本案不猜数。 |
| 2026-10-26 | W5 梳理取材默认剔除落地（第二令 R10 改判的落码）与三处形状取舍 | 红＝`TestCurationStripsFurniture` 首枚读出跑动书名仍进 `HeadingEvidence`、`_page_bodies` 送出的副本含重复线；绿＝`normalize/furniture.py` 追加 `FURNITURE_TYPES` 与两枚纯函数、`orchestrator/knowledge.py` 三处接线。**三处取舍**：① **剔的是送模型的正文副本，不是产物**——`EvidencePage.markdown` 与磁盘 `.md`、`GET /api/v1/pages` 的 `markdown` 逐字不改（`KA-19` 的越界复核要有原文对照，`PM-5` 的覆盖层方向是读时叠加），故新增用例专门钉「跑完一批后磁盘文件字节不变」。② **两腿合取只在 `furniture_texts` 一处算**：类型维在 `apply_relabels` **之后**判（未回放则人工改判进不了剔除面），线维取 `furniture_hints`；输出稀疏——无家具的页不出键，「没有家具的书逐字照读」由反向断言守着。③ **整行折叠相等才剔、包含不剔，且比较前折掉 ATX 的 `#` 前缀**：引擎把跑动书名判成 `heading` 时产物里就是 `# TEXT`，不折前缀这一行永远剔不掉（变异 C 证明该半条被守）；正文里引用同一串书名的句子必须留下（反例用例）。**不设「纯页码行」正则**：第三枚启发未经裁定，页码已由类型维（人工改判 `page_number`）与线维（重复页脚）两路承载，擅自加正则等于把渲染侧阈值冻进解析侧，与 `PM-19` 的边界列同轴。 |
| 2026-10-26 | W5 四枚变异校验（守质量自证，每枚反向 Edit 恢复后复绿） | A＝`furniture_texts(replayed, hints=())`（只留类型维）→ 管道面 **4 枚**红，证明线维真进了取材读数；B＝`PageBody(page.page_no, page.markdown)`（不剔）→ **2 枚**正文副本用例红，证明送模型的确实是剥离后的那份；C＝去掉比较里的 `.lstrip("#")` → ATX 那枚红，证明「引擎把页眉判成标题」这条路被守；D＝整行相等改成包含 → 「包含不剔」用例**与**改判页码那枚同时红，后者证明「`第 1 页 正文` 不被过剔」这条断言真咬而不是恒绿。 |
| 2026-10-26 | W5 收口三门禁与工作树指纹 | `pytest tests -q`＝**1001 passed／18 skipped（40.18s）**（较 W6 收口 +15＝`TestFurnitureTexts` 4＋`TestStripFurniture` 6＋`TestCurationStripsFurniture` 5）；`ruff check src tests scripts`＝**All checks passed**；文档门随全量复跑同绿。指纹：HEAD 仍 `46f5f3f`（本案零 git 操作），`git diff --shortstat`＝**28 文件 +3174/−207**，未跟踪 **12** 件；`src/axiom_flow/normalize/blocks.py` 是本案本轮新增的改动面，该文件对方未碰（只有本案 +3/−1＝import 一枚、注释一行、`_SKIP_IN_MARKDOWN` 改引同源常量）。 |
| 2026-10-26 | AF-T6 读侧家具标记半边：一条成本事实与三案（登记待裁，本案不擅自定形） | 裁点＝页数据读投影要不要替渲染腿把「线维」标出来。**成本事实取证**：判一条重复线的分母是**整本的页集**，而 `GET /api/v1/pages/{page_no}` 只拿得到一页——逐请求判线即逐请求整本读盘；本仓 `src/` 零处 `lru_cache`／holder／跨请求读缓存（grep 取证在册），现网最大书 439 页（b07 全本生效）。三案＝**甲**：加一记按 `(book_id, job_id)` 的 hints 持有者，一次整本派生、后续页读命中——新增一份进程内状态，失效条件要另立条文；**乙**：新增整本只读端点（形如 `GET /api/v1/kp/furniture?book_id=`），根侧渲染腿每本取一次自行合取——多一条契约面（`api.md` ③＋根侧 §④ 透传），但本仓零状态；**丙**：读投影只显**类型维**（块 `type` 三枚家具值本就在页数据里，改判回放后天然可见），线维不进页端点——零新面零状态，代价是「未改判的引擎漏判页眉」在美化按钮下仍可见，剔除只保证进不了梳理。本案按现状交付＝**取材面按甲＋乙合取的正体已落（`furniture_texts` 两腿合取），页端点仍是丙的形状**；其余两案由需求方裁。 |
| 2026-10-26 | W7 两条腿落地（先红后绿） | 红＝`tests/contract/test_api_v1_contract.py` **收集期** `ImportError: cannot import name 'NARRATIVE_MAX_CHARS' from 'axiom_flow.schemas'`，`tests/unit/test_db_knowledge.py` 随 `KpRepository.set_narrative_ref` 缺失同红；另有 `api.md` 端点总表对账门 `test_api_endpoint_table_matches_runtime_routes` 先红（总表已登两行而运行时路由缺）⇒ 本轮是**契约先行**形状：文档与用例都站在路由前面。绿＝`schemas.py` 三处（上限常量＋写读两枚模型）、`db/repositories.py` 一条单列 UPDATE 系统腿、`orchestrator/knowledge.py` 的 `read_narrative`、`api/main.py` 两条路由与一枚 `_kp_narrative_out` 投影辅助。定向复跑＝`TestKpNarrativeLegs` 14 ＋ `TestNarrativeRefSystemLeg` 11（七枚定义，其中非法指针一枚四参参数化）＝**25 passed**；两条腿的路径断言由用例按 `KA-20` 派生式独立算一遍（`tmp_path/data/parsed/<domain>/<course>/<book>/kp/<node_id>.md`），不复用返回体自证。 |
| 2026-10-26 | W7 一处实现对账：判定序以代码为准回改文档 | `KA-30` 原稿写「404 → 409 → 422」，落码时空文与超限两判在请求模型 `Field(min_length=1, max_length=NARRATIVE_MAX_CHARS)` 里，FastAPI 的形状校验先于函数体 ⇒ 真序是 **422 先于查行**。取舍＝保留形状闸（一处判定、两条腿共用、超限当场不落盘），把 `knowledge-analysis.md` 的 `KA-30` 与 `api.md` ④ 的判定序句子和状态码表**改判成代码的形状**，而不是在 handler 里重复一枚上限检查、制造第二种口径。409 仍排在**任何磁盘写入之前**（端点先预读 `rev` 判一次，`if_rev` 再钉在 `WHERE` 里守竞态的后半）——这条是刻意的：磁盘写入不可回滚，旧表单先把文件改了再被序号拒，就留下一份没人认领的正文。 |
| 2026-10-26 | W7 两处口径精度（不拿干净好听的句子过事实） | ① **「唯一写者」加限定**＝`narrative_ref` 只在 **UPDATE 通路上**由 `set_narrative_ref` 独写；机器腿 `create` 仍能在插入时给值（梳理通路恒给空串），把这列从插入输入面整个剔出去是宽得多的变更，不为一句话去动。四处统一带限定＝两条代码 docstring、`KA-11`、`KA-30`、`api.md` ④ 的 PUT 小节。② **读腿的指针回显规则**＝文件在位回**派生名**、缺文件回**列原值**：列可能非空而文件不在，读侧既不替它圆场也不修列不删文件（两处都不是它的职权）；缺文件判 **200 带 `exists=false`** 而非 404——判 404 会把「节点存在但正文缺」谎报成「节点不存在」，而这个矛盾正是读侧要显示出来的东西。形状由 `test_a_file_on_disk_reads_under_its_derived_name_even_with_an_empty_column` 与 `test_missing_file_reads_as_exists_false_without_touching_columns` 两枚钉住，未来生成腿留下车内文件外空列时不必再裁一次。 |
| 2026-10-26 | W7 一处假绿自纠（写给后续轮） | 「整表逐列对账」首版拿 `repo.get()` 写入前后的两个句柄比属性，断言 `changed == set()` **恒绿**：SQLAlchemy 的 identity map 让同一行返回同一对象，批量 `update()` 又就地同步该对象，于是两个句柄永远相等——一条什么都没守的断言。改法＝写入**前**用 `_column_values(...)` 快照全表值、写入后与快照对账，真读数 `changed == {"narrative_ref", "rev", "updated_at"}`（`rev` 随系统腿自增与 `KA-11` 各条同形，故序号不是「只有人改过」的计数器，判据位是 `confirmed`）；原第二枚「句柄别名」用例替换为「这条腿不搬确认位与来源列」。同轮还清掉一处本会话自己的编辑事故：想删一枚未用的 `WATCH` 元组却先加了 docstring、又把元组复制成两份，删净后复跑该件 60 passed。 |
| 2026-10-26 | W7 文档门禁两红与修法 | ① `test_design_documents_declare_numbered_clauses_with_carriers` 要求每条条文**恰好三格**（守则／理由／落点），`KA-30` 原稿把「边界」写成第四格 ⇒ 并进食「理由」格（「……两处都不是它的职权。边界：……」续写）；② 同枚门要求「落点」格含一枚 ASCII 的 `" → "` 以分隔实现落点与守护测试 ⇒ 补两侧空格。两处均为文档形状门，判据正文一字未改。 |
| 2026-10-26 | W7 收口三门禁与工作树指纹 | `pytest tests -q`＝**1026 passed／18 skipped（42.95s）**（较 W5 收口 +25，全部来自 W7 两枚新类：契约 `TestKpNarrativeLegs` 14＋仓储 `TestNarrativeRefSystemLeg` 11）；`ruff check src tests scripts`＝**All checks passed**；四枚文档门随全量复跑同绿。指纹：HEAD 仍 `46f5f3f`（本案零 git 操作），本案改动面自 W5 收口的 28 文件 +3174/−207 起继续叠加。PLAN-025 壳 W6 那行「本案不新增端点」**不改**：该壳是对方的未提交在途件，按其交付纪律只加不改其行，交接口径已在上面 W6 措辞随步那行同例登记——R9 甲案的裁值以本壳「裁决回填」表与晋升后的 `KA-30`／`api.md` ④ 为准。 |
| 2026-10-26 | W7 交付后更正本壳一处**裁前原稿事实错**（R8 行的括号，裁值不动） | R8 行括号写「8900 侧净缺＝`LlmTextRequest` 无归因三键、`gateway.call_text` 的 `service` 形参未被端点透传」——根侧透传轮实核推翻：四键（`service`／`task`／`step`／`meta`）在 `api/schemas.py:86-96` 已在册、端点在 `api/control.py:436-450` 已透传、`services/llm/gateway.py:64-83` 单点落账，守护见其 `tests/test_llm_gateway.py:70` 与 `tests/test_llm_endpoints.py:91-113`。⇒ 本案 W6 适配器上送的 `task`／`step` **已被 8900 受理并入 `qed_llm_calls`，根侧零改码**，R8 甲的记账半边没有未做的依赖方。改面＝只在过程节登记更正，**R8 裁决行一字不改**（裁值＝甲不变，错的是其括号里的事实陈述）；该括号原为出裁前的一次读数，写成「净缺」属把裁前状态当现状。 |
| 2026-10-26 | W11 落码：备注通道的两半、一处**用例自身写错**的自纠、一处归属改判 | 先红＝定向四文件 **5 failed／124 passed**（`KpBatchRow.__init__() got an unexpected keyword argument 'review_note'` ＋ 网关 `bind_batch()` 的 `TypeError`）；后绿＝同四文件 **411 passed（28.7s）**，全量 **1030 passed／18 skipped**（动码后与文档随步后各一轮，43.5s／43.8s 同读数），`ruff check src tests scripts`＝All checks passed（两轮）。自纠一处：新用例首版拿 `run_knowledge_job` 断言流水 `payload`，绑定半边绿而流水行为 `None`——批次流水的发出点在 `JobRunner._record_kp_batch`（`orchestrator/jobs.py:194`）的**单独会话**里，执行体本身从不写它；改走 `runner.submit_knowledge` 一途才与真实通路同形（「流水单独会话」这条既有设计就写在 `KA-27` 的落点格里，是我读浅了）。归属改判一处：`overview.md` 原列「本案零行、纯对方在途」，本轮补 W6／W7 **漏随的事实更正**（适配器已接入、端点七条→九条）后移出该清单——计数由 `create_app` 运行时实测打印为证（恰九条），本案在它只有一行。`code-map.md` 本轮**无行需改**（模块级行不涉函数级新键），未随步不等于漏随。 |
| 2026-10-27 | W10 前半条达成（丙案＝人工改标验通路，**零改码**）与两枚新事实（根侧 `DEFECT-026`／本壳 `R11`） | 判据＝需求方裁「先丙后甲」：用在册的 `corrected_type` 把 b07 五枚行标成 `heading`（p10 块 0／1、p14 块 21、p33 块 0、p35 块 19），走一次**小页区间**真实批次证明「受理 → `build_outline` → `af_kp` 行 → 调用账 → 根侧屏上读回」五段通路，本案因此不动一行码、不改 `KA-5`。**收口读数**＝批次 `afkp-215187d641e2`（`mathanalysis-b07`，p10..40，带人工评审意见备注）`status=ok`、`candidate_rows=22`、`confirmed=0`、`review_flags=0`、`dropped_nodes=5`、**`failed_sections=1`**；`qed_llm_calls` id 127–131 五行的归因三键齐（本仓适配器上送的 `task`／`step` 与 `meta` 一比一入账，R8 甲的记账半边至此有真机凭据）；根侧刷新后屏上目录五行、知识点十七行全可读。**两处设计内读数澄清**（避免后续误判为缺陷）＝① 批次刚受理时进度腿读 **404**：`rows` 与 `ledger` 皆空，`KA-4`／`KA-9` 的在飞判据只看 draft 行，属形状不是故障；② 「本课程已梳理教材」在飞与收口两态都读 **0 本**：判据含确认位与零空总结，本轮 22 枚全是 candidate。⇒ **净缺陷一枚（登记为根侧 `DEFECT-026`，请求本仓）**：`failed_sections=1` 的原证在 AF 日志＝「节抽取失败，跳过该节：mathanalysis-b07 的第 33..34 页（§ 3. 函数的概念）」，对应 `qed_llm_calls` id **130**：`status=success`、正文 3617 字符，而 `json.loads` 报 **`Invalid \escape: line 75 column 99`**。根因落在本仓 `engines/kp_gateway.py` 的 `_drafts`——容错只有「剥代码围栏」与「去前后杂讯」两条，**没有非法转义修复腿**，于是模型回复里一处坏转义经 `EngineError` 被 `orchestrator/knowledge.py` 的按节失败披露吞成一条计数，**该节知识点整节静默丢失**。定性＝净缺陷面在本仓适配器、根侧零改码可解；本案**未动码**（裁决先落文档，等需求方「开始执行」）。**成本读数（交整本重梳理排期用）**：五枚改标行换出四节、约 13 分钟跑完 31 页，线性外推整本（b07 439 页）为**数小时级**，故整本窗口须排在 `DEFECT-026` 与 `R11` 两枚裁点之后——后者已在「裁决回填」前另立 `R11` 节（目录阶段取材是否扩到书自身目录页的条目行，四案：甲-A 标题类型扩集／**甲-B 名称回定位（本案建议）**／甲-C 印刷页码标定／乙／丙），并登记三枚随批子问题（匹配失败行去向、目录页判据正本落哪侧、整本成本预算）。**交人工的屏侧三事实**＝梳理屏默认落 `mathanalysis-b05` 而非本轮真跑的 b07（验收须手选）、标题栏「刷新」钮缺 `data-testid`、批次受理瞬间进度 404 需按上面口径显示而不是报错。指纹：HEAD 仍 `46f5f3f`，本轮零改码、零 git、零库内写（批次写库属人工发令的 W10 窗口本身）。 |
| 2026-10-27 | W16 落地后真机第一轮只目录（整本）与三处质量发现 ⇒ Q1~Q3 全取甲案 | 判据＝需求方开工令「这次只梳理目录，这应该很快……保证目录的显示、一键解析、重新解析生效」。**收口读数**＝批次 `afkp-89dff7b22d57`（`mathanalysis-b05` 整本、`stage=outline`）受理 **202／0.14s**、`status=ok`、`outline_rows=156`、`candidate_rows=156`、`point_rows=0`、`failed_sections=0`、`dropped_depth=0`；`qed_llm_calls` **零新增**（最新 axiom_flow 行仍是 id 138@05:14）⇒ `KA-35` 的「本阶段零模型调用」在真机成立；屏上 156 行目录全可读、两钮可用、「重新解析目录」弹窗出「选择目录行＋人工评审意见」两件。**三处质量发现**＝① 156 行里 101 行锚在正文页（≥9），**49 行锚回目录页自身**——p5 是**数学符号表**（`∈ 属于`／`sup 最小上界` 一行一条、行末带页码），与目录页行形状相同，被 `toc_entries` 整页收下；另有 p7 四行、p8 两行（未定位合计 55）。② 11 章中 9 章定位成功，**第4章**（`第4章 连续性 …… 73函数的极限`——两条目黏成一行、整条名字在正文永不命中）与**第6章**（英文条目折行丢字）未定位；`0 零向量` 被单级编号形状误判成章。③ 读面 `list_for_book` 无批次过滤、旧 `candidate` 行无取代路径 ⇒ **同一区间重跑必叠加**。三问裁决见「裁决回填」表末行，实现＝W17。 |

| 2026-10-27 | W17 三案落地（先红后绿）与一处文档守护落点回改 | 红＝定向 **7 failed／15 passed**（黏行拆分的条目读数、两枚页级复核的 `dropped_toc_pages` 属性、四枚 supersede 的 `page_start` 关键字皆未成立）；绿＝定向 93 passed、全量 **1072 passed／18 skipped（53.48s）**、`ruff check src tests scripts` **All checks passed**。落码三处＝`normalize/toc.py`（`_INNER_PAGE` 行内第二处「引导符＋页码」再拆、`locate_entries` 回 `TocLocation(offset, entries)`、新纯函数 `drop_unlocated_toc_pages` 按页过半未定位整页剔除且偏移未标定时不剔）、`orchestrator/knowledge.py`（取材第三趟做页级复核并进 `EvidenceSet.dropped_toc_pages`；`finalize_batch` 两枚可选页区间参数＋翻面前作废步；`KnowledgeOutcome` 两字段；`kp_batch_row` 两键）、`db/repositories.py`（`KP_RESULT_KEYS` 十六键扩十八键、`KpRepository.reject_superseded_candidates` 先按锚点页过滤再一条条件 UPDATE）。**一处文档自纠**＝`KA-36` 守护列原写 `tests/contract/test_api_v1_contract.py`（两次提交后工作区读数不叠加），而实际守护落在 `tests/unit/test_knowledge_pipeline.py::TestRerunSupersede` 的同名端到端用例 ⇒ 按代码改文档（该用例的判据就是「活行批次集合只剩第二批」），不补一条重复的契约用例。 |

| 2026-10-27 | W17 三案**只读真机取证**（零批次、零写库；按需求方裁定「批次由人工发」） | 取证法＝用 b05 生效版本 `4af1b233c252` 的**真实页产物**（317 页 `.blocks.json` 与 `.md` 成对在位）离线跑一遍新判据链（`_toc_pages` → `locate_entries` → `drop_unlocated_toc_pages`），不落库、不改数据根；改判回放对本腿零影响（`toc_entries` 只吃段落块、回定位只比文本前缀，而 b05 的 `confirmed` 全 0 且目录页无改标）。**读数**＝偏移 `offset=8`；目录页三枚 p5(49 条)／p7(66)／p8(43) 共 **158 条**；**`dropped_toc_pages=[5]`**——符号表页 49 行整页剔除，留 **109 行**；未定位从上一轮的 55 降到 **5**（`数 e`／`习题`／`第6章 RIEMANN-STIEI. TJES积分`／`Γ函数`／`$\mathscr{L}^2$ 类的函数`，两页各过半命中故不剔页，符合「宁可多行不可丢目录」）。黏行拆分实证＝`第4章 连续性` 与拆出的 `函数的极限` **双双定位到 p81**、`连续性与紧密性`→p85、`连续性与连通性`→p89；11 枚章行里 10 枚定位成功（第1章→p9…第11章→p283），唯一失手是第6章——名字被 OCR 拆成 `RIEMANN-STIEI. TJES`，属产物字符级破损而非形状判据。上一轮误判成章的 `0 零向量` 随 p5 整页剔除消失。**仍待人工发批次验证的两件**＝① 新键在真批次的落账（`dropped_toc_pages`／`superseded_rows`）；② `KA-36` 作废面在屏上的读数（现网 `afkp-89dff7b22d57` 156 行 candidate 全部 `confirmed=0`，重跑整本应读 `superseded_rows=156`、屏上只剩新批）。 |

| 2026-10-27 | **第六令前的目录失败根因取证**（只读、零批次、零写库；对照物＝需求方指认「数学分析原理 p7–p8 的书自身目录」与「数学分析习题集 p7–p9」已达验收级，而梳理出的实际目录完全不可验收） | 取证面两块＝① 现网批次 `afkp-89dff7b22d57` 的 156 行结构读数：**27 行顶级／129 行有父**、`model_ref` 清一色 `rule:toc`，其中 **49 行来自 p5 数学符号表且 `parent_id` 为空**（符号表条目被单级编号形状当成章），另有 **11 枚名字为「习题」的行**散布各章之下——人工在屏上读到的是「一本没有章、满是符号的目录」；② b05 前十二页真实产物形状（取证件 `tmp/kp-probe/front-pages.json`，经 8902 页数据端点只读取回、不碰数据根）＝p1 七枚全 `header`、p2 八枚（书名与作者以 `paragraph` 出现）、p3 25 枚段落块（版权页）、p4–p6 目录页与符号表混排、p7–p8 目录续页：**目录页与正文前的附件页在块类型上完全同形（清一色 `paragraph`）**。**五条根因**＝(一) **判页判据只有形状没有语义**——`_toc_pages` 认「同块内 ≥`TOC_MIN_ENTRIES`(3) 行且多数行以阿拉伯数字结尾」，符号表页与目录页行形状相同，两者互相误收；(二) **一枚全局偏移对不上双页码序列**——b05 前部走罗马页码、正文走阿拉伯，`estimate_print_offset` 只取一个众数，罗马行按无名行处理仍救不回正文前的附件页；(三) **名字前缀命中遇产物字符级破损即失效**——`第6章 RIEMANN-STIEI. TJES积分`、`$\mathscr{L}^2$ 类的函数` 这类 OCR 断词与 LaTeX 残留在正文里永不逐字命中；(四) **目录条目与知识点混淆且无非知识点身份位**——只剩「收 49 行假章」与「一行都不留」两种坏选择；(五) **整条链零语义判断**——一次模型都没读过目录页本身，而需求方对照的两本书目录本就是「人一眼读得懂」的形状，机器缺的正是这一眼。⇒ 判据改判与五件落点见「裁决回填」第六令行与 `KA-35`（改判）／`KA-37`／`KA-38`／`KA-39`／`KA-25`／`KA-27`，实现批＝本案 **W18**。本轮零改码、零 git、零库内写；`tmp/kp-probe/` 两件取证件暂不清（W18 判据复验仍要用）。 |

| 2026-10-28 | W18 收口：守护补齐＋两处**实现与条文不符**的缺陷落码＋文档随步清零 | 红三处＝① 文档守护列点名的 `TestModelLedOutline`／`TestPrintPageAnchorAndVerify`／`TestFrontMatterRows` 三类不存在（`engines/base.py` 的 `OutlineEntry`／`ReferencePage`／`TocVerifyAnswer` 三枚导入被 ruff F401 报出即其残留）——正解是补写守护用例把导入用上，**不是 `ruff --fix` 删导入**；② `KA-39` 回落腿身份位没接线，新用例读出 `('附录 习题解答', {'toc_role': 'knowledge'})` 而判据要 `reference`＋`appendix`；③ **`page_sample_text` 的「整块丢弃」读出一枚空样本**（探针＝3 枚 535 字符块 ⇒ `sample == ''`），而真机 b05 目录页约 1.5 KB／3 枚块，判页腿零输入会把整本误判成「这本书没有目录页」、整本被推回回落腿 ⇒ 改按预算逐块截字并保留块类型标注，`KA-37` 落点格随步点名该函数。**落码面**＝`orchestrator/knowledge.py` 的 `build_outline` 两段式与身份位两路分派（`reference_role_for_name(entry.name)` 在 :682）、`normalize/toc.py:page_sample_text`、`tests/unit/test_knowledge_pipeline.py` 三枚新类＋夹具 `_two_toc_page_scene`、`tests/unit/test_normalize.py::TestPageSampleAndIdentityBits`。**两枚变异校验（自证守护真咬）**＝把判页取样的尾窗那行改 `if False:` 只杀 `test_the_page_pick_samples_are_the_first_and_last_ten_pages`；把 `KA-38` 的「核对页号须在候选域内」闸改 `if False:` 只杀 `test_a_verified_page_outside_the_candidates_is_not_accepted`（`KeyError: 99`）；两枚均由备份完整还原，还原后 `knowledge.py` 无 `if False` 残留、原行在 :446／:611，两枚被改文件复跑＝243 passed。**三门禁**＝`pytest tests -q` **1127 passed／18 skipped（45.22s）**（较 W17 收口 +55）、`ruff check src tests scripts` All checks passed、文档门随全量同绿。**本轮另清三处前轮漏随**＝`code-map.md` 此前 `KA-36~KA-39` 零读数、`api.md` ④ 的 `result` 示例止于十八键且 `stage` 段仍写「目录阶段确定性成行」、`knowledge-analysis.md` 的 `KA-5` 正文与 `KA-35` 改判相左——三处按现状补齐后各加守护列点名新类。**三件仍开着（登记，不擅动）**＝① 模型整理腿的 `OutlineEntry` 不带 role/kind 通道，目录页内的「附录 习题解答」在**正腿**路径下仍记 `knowledge`（附件行只能由判页的 `reference_pages` 给）——协议形状限制而非实现错，放宽要改整理腿的回行形状；② `LedgeredKnowledgeEngine` 显式定义三腿（:1375/1382/1389），使「引擎无腿即回落条目腿」的注释承诺在包装态退化成 `AttributeError`；③ `db/repositories.py:75` 的 `KP_META_KEYS` 定义后零消费。指纹：HEAD 仍 `46f5f3f`，本案零 git 操作、零库内写；真机只目录一轮与旧批次 `afkp-89dff7b22d57` 的删除清单仍按令「先只读取证、批次与删除由人工发令」待发。 |

| 2026-10-29 | W19 丙案落地（目录行逐页落库＋同批定稿改写＋进度面两路在飞）与两处**测试夹具自身写错**的自纠 | **落码五处**＝① `orchestrator/knowledge.py`：`OutlineReading` 加 `entry_index`（同一条目在早落批与定稿批认得同一枚 `node_id` 的身份通道，附件行与 heading 行留 `None`）、新 `OutlineSink` 协议（`land`／`repoint`，`runtime_checkable`）与 `_OutlineAssembler`（把原内联成行循环抽成可重复调用的装配器，`reuse` 表按 `entry_index` 复用号）、`_entries_from_pages` 加 `on_page` 回调（**成功分支才发**，回落页不发＝`KA-33` 的页级复核要全书偏移）、`build_outline(sink=)` 内 `land_page` 闭包用「此刻已收到的条目」标定偏移并定锚、`run_knowledge_job` 换 `_KpOutlineSink` 并跳过已落行；② `db/repositories.py:KpRepository.repoint_draft_outline`（四条件 WHERE＋逐条 `node_id`、可写列先于发语句校验、不递增 `rev`）；③ `orchestrator/jobs.py`：`_kp_inflight` 集合＋`kp_in_flight` 谓词＋`finally` 里**先撤登记再关引擎**＋`shutdown` 清空；④ `api/main.py:get_kp_job` 的在飞两路与 404 判据；⑤ 两份 docs＋`code-map.md` 四行。**红绿读数**＝红 `TestProgressiveOutlineLanding` 首跑 **8 failed／2.34s**，绿同批 8 passed、`tests/unit/test_knowledge_pipeline.py` 165 passed、定向三件合跑 **10 passed／1.44s**、全量 **1136 passed／18 skipped（49.48s）**、`ruff check src tests scripts`＝All checks passed。**两处自纠都在用例侧、不在码侧**＝`_probe_model_service` 的 `finally` 会 `close()` 提交期那枚引擎，而 KP 槽位是**单例桩**（探活与执行体共用同一枚实例），于是 `close` 作「这批已结束」的信号两次给出假读数——第一次假绿（`active_job_id` 断言读 `None`：用例在解析真跑完前就放行），第二次假红（`counts.draft==2` 且 `ledger` 为 `None`：探活当场置位 `finished`，读面在批次还活着时就查账面）。修法＝完成信号只由**真的干过活**的那枚实例发（解析桩 gate 在 `parse_document` 跑过、KP 桩 gate 在 `release.is_set()` 之后），第二次假红读到的那 2 枚 `draft`（1 outline＋1 point）恰好是逐页落行在真端点路径上生效的正证。**两处判据自审**＝① `KA-40` 文档原写「`WHERE` 三条件」却同时要求「不碰 `confirmed=1` 行」——读 `confirm`／`set_confirmed`（`repositories.py:1466-1512`，确认位入口**不做前置校验**）证实 `draft ∧ confirmed=1` 可达，故按四条件实现、文档改判（本轮已落）；② 同批改写不自增 `rev` 与机器写面普遍自增（翻面／作废／归一／`set_links`／`detach`）不同形，已在 `database-design.md` 的 `rev` 行登记为**唯一例外**并写明理由（早落行与定稿行是同一批的两次读数，自增让人工 `if_rev` 平白撞 409），零变化由 `TestProgressiveOutlineLanding` 的逐列断言守着。**三件仍开着（登记，不擅动）**＝① **受理门仍只读 `draft` 行**⇒「受理到首枚目录页整理完成」那一窗（真机判页腿 152s）内第二枚提交不会被 409 拒，同书可排两批；`KA-40` 边界条明写受理门不变，要不要把在途登记也放进去是**方向取舍**（进程内状态当排他判据），交需求方裁；② 契约用例让执行体与读面共用一枚 `StaticPool` 单连接，5 轮连跑未见抖动但**未做专门并发证据**；③ 真机一轮仍待发＝8902 不热重载，须重启才吃到本轮码（重启与批次均由人工发令）。指纹：HEAD 仍 `46f5f3f`，本案零 git 操作、零库内写；工作树 41 项改动／未跟踪含本案与对方在途件；`tmp/kp-probe/` 取证件留到真机复验结束。 |
| 2026-10-29 | **第九令三件 W20~W22 落地（先红后绿）＋判据／契约随步**＝需求方同日「我授权改 Axiom-Flow部分」后本仓直接动码 | **W20（件①②）**＝`normalize/toc.py:clean_entry_rows`／`_strip_trailing_leaders`／`entry_level` 三件纯函数＋`_squash` 折标点异形（`。`→`.`、`、`→`,`），`orchestrator/knowledge.py` 的模型整理腿经 `_cleaned_model_entries` 走同一把清洗、回收的行末数字填 `printed_page` 缺位；红＝新 helper `ImportError`，首绿 **1 failed／262 passed**（黏行拆分），修后 **263 passed**。**W21（件④）**＝`section_spans_of` 纯函数抽出（原 `_section_spans` 改调它、读数逐字同）＋`api/main.py` 的 `_kp_page_spans`／`_page_range_of`／`_kp_node_out` 给工作区与导出面逐行发 `page_start`／`page_end`（目录行只认 `candidate`、`rejected` 不进序，末行到 `af_books.page_count`、缺列折回起点页；点行＝锚点 min／max）＋`KpNodeOut` 两枚可空键，单行回显同样带键；红＝unit `ImportError` ＋ contract **5×`KeyError: 'page_start'`**，绿＝12 枚新例。**W22（件⑤）**＝`schemas.py:KpStage` 三值、受理门插在册目录行门（零枚 ⇒ 422 点名「先解析目录」，排在页区间之后、在飞之前）、`_registered_outline_rows` 的区间判据＝**本行起点页 ∈ `[page_start,page_end]`**、`_outline_nodes_from_book` 由在册行重构、`extract_points` 在 `content` 下**跳过锚在目录页的行**并把名字并入 `result.unlocated_outline_entries`（认法＝确定性条目腿 `toc_entries`，一次模型调用都不发）、`set_outline_summary` 第五条系统腿（`WHERE` 四条件＋逐条钉 `node_id`、只写原值为空的未背书行、不递增 `rev`）、`finalize_batch`／`reject_superseded_candidates` 的作废面按 `stage` 收到点行、`KP_RESULT_KEYS` 二十三键（`outline_summary_rows`）、`engines/kp_gateway.py:_points_prompt` 回复多带节级总结一段；红＝`TestContentOnlyStage` 与契约三形先落、`SectionPoints` 导入前套件红 → 绿 `test_knowledge_pipeline.py` **184 passed**（`-k TestContentOnlyStage` **10 passed**）。**全量门禁**＝`pytest tests -q` **1176 passed／18 skipped（68.98s）**、`ruff check src tests scripts` All checks passed。**随步文档**＝`knowledge-analysis.md` 的 `KA-41`／`KA-42`／`KA-43` 与 `KA-26`／`KA-27`／`KA-34`／`KA-36` 四条款改判、`api.md` ④ 的第三值三处与 `result.outline_summary_rows`／派生两键两段落。**未做**＝本轮零 git 操作、零库内写、零 `dataset/**` 触碰；8902 不热重载，真跑前须人工重启，b05 先目录后内容的两轮批次同样归人工发令 |
| 2026-10-31 | **第十令 W23~W25 落地（AF 侧三件）＋`KA-44`／`KA-45` 成文、`KA-27` 键数改判** | **W23（`DEFECT-028`）**＝`section_spans_of` 终点改树序（下一条同级或更浅行起点 − 1），`toc_role='reference'` 行只发起点＝终点，超 `last_page` 的行不发区间；单测钉「ch1 p9／首节 p9／ch2 p29 ⇒ 章 9–28、节 9–19」与「符号表只占自身一页」，`_kp_page_spans` 的 `candidate` 过滤与缺 `page_count` 折回判据不动。**W24**＝`node_type_of`／`node_types_of` 四值派生（exercise 先判、reference→other、depth 1→chapter、≥2→point，沿父链取祖先 `toc_kind`），`KpNodeOut.node_type` 四值 `Literal`，读面三面（工作区／导出／目录声明）与生成腿三处（点腿只问 `point`、章腿只取 `chapter`、重生成非 `point` 422）共用；零写键零 DDL。**W25**＝`extract_chapters` 两遍制（按列现读节腿刚写下的总结与导读指针、章材料三块、一次调用出总结＋导读、导读已有指针或文件不覆盖、只 `content` 批次发起）、#129 容错腿 `_salvage_field`（节抢 `section_summary`、章抢两字段，一个都抢不到才判故障）、`POST /api/v1/kp/nodes/{node_id}/summary`＋`regenerate_section_summary`（八档判定序、覆盖写、`rev+1`）、`KP_RESULT_KEYS` 二十八键。**门禁读数**＝`pytest tests -q` **1210 passed／18 skipped（51.94s）**、`ruff check src tests scripts` **All checks passed**、`tests/contract` **284 passed**；定向四类（`TestSectionSpansOf`／`TestNodeTypeOf`／`TestChapterLeg`／`TestRegeneratePointSummary`）**27 passed**，网关两批（`TestTruncatedReplyTolerance`／`TestChapterLegOutbound`）**7 passed**。**一处自纠**＝章腿空回复那行误带一枚无占位符的 `f` 前缀，`ruff` F541 当场报出并已改（改后重跑 ruff 与 353 枚定向全绿）。**文档随步**＝`knowledge-analysis.md` 新增 `KA-44`／`KA-45`、`KA-27` 二十三→二十八键（24~28 为 `chapter_rows`／`chapter_summary_rows`／`chapter_narrative_rows`／`failed_chapters`／`failed_chapter_details`）、`api.md` ④ 十条腿＋`/summary` 小节与判定序表、`code-map.md` 与根侧 `api-contracts.md` 同步。**仍开着**＝根侧透传与屏侧「重新生成总结」接线分别归根侧 W25／W27；`TestProgressiveOutlineLanding` 的一条精确逐行断言在上一轮全量里偶发过一次红，单独与复跑均绿，登记为观察项（与本轮改动无关）；真机重跑仍待人工发令（8902 不热重载）。 |
| 2026-11-01 | **W12 ③（根侧 W20）读侧家具标记落地（先红后绿）＋一处形状定案** | 形状＝AF-T6 三案之**甲**（页数据读投影内联，不开新端点、不做纯类型维）：`PageData.furniture_blocks` 给类型维∪线维的块下标（`Block.index`、升序稀疏），屏侧逐块隐藏不必自算跨页线；**代价与失效条文**＝新增一份进程内状态，判一线要整本页集（现网最大 439 页），故按 `(book_id, job_id)` 缓存整本派生，失效只有两条——换版即换键、块编辑（同版本内唯一能改标记的写，`corrected_type`）后作废本书本版键；删书同步清键。红＝unit 收集期 `ImportError: cannot import name 'furniture_blocks'`、契约面 `set(payload)` 少一枚键；绿＝新 `TestFurnitureBlocks` 6＋`TestPageFurnitureMarks` 4（含「产物文件仍是 `paragraph`＋`markdown` 原样」与「缓存暖了两次后改判／撤回，下一次读跟着变」）。**全量门禁**＝`pytest tests -q` **1220 passed／18 skipped（83.89s）**、`ruff check src tests` **All checks passed**。**文档随步**＝`api.md` ③ 端点小节（派生口径＋缓存失效两条＋JSON 示例加键）。零 DDL、零写库、零 `dataset/**` 触碰、零 git；根侧 8900 透传 `dict` 原样、零改码，屏侧消费在 W21（默认关）。 |

## 验证与验收

- 逐工作项以用例为凭（先红后绿），每条判据的落点→守护成对登记在 `code-map.md`；
  回放两处**缺一即判未达成**（只改页数据端点＝目录照旧吃页眉）。
- 闭集与形状守护：`corrected_type` 十枚之外一律 422；`section_kind` 只可能取八值；
  本案零 ALTER 由 `tests/unit/test_db.py` 的表族守护复验。
- 适配器侧默认测试**不联网、不打 8900**：网关以注入的假客户端替身断言「一次调用＝一条账」，
  真机调用只在 W10 窗口发生且需人工发令。
- 三门禁输出与真机读数写入本壳「过程与证据」表，todo 行不带进度。

## 回滚

- 代码：本案新增行按「逐文件归属」表分列撤回；本案新建的五件（`normalize/intents.py`＋`normalize/furniture.py`＋
  `engines/kp_gateway.py`＋`tests/unit/test_engine_kp_gateway.py`＋本壳）可整文件撤，
  混合文件逐 hunk 选择，**不整树回退**对方在途工作。
- 行为：`AXIOM_KP_ENGINE` 保持空 ⇒ `POST /kp-jobs` 回到恒 503 形态；不写 `corrected_type` ⇒ 回放两处读数与改前一致。
- 数据：本案零 DDL；`section_kind` 写手一旦生效属页行的值变化，回滚只停写手、不批量改判历史行（历史行回填属 D 类，另议）。

## 关闭与归档

- 关闭判据＝W1 真机读数齐备且 W2~W9 逐条达成；根侧 REQ-142／REQ-143 的本仓侧通路回执写在本壳，
  两行由根侧自读登记关闭（本仓不代摘根行）。
- 归档判定 `Retain`：页域判据的真书标定证据与两侧对账读数不可再生；运行时事实源以晋升后的
  `api.md` ③/④、`knowledge-analysis.md`、`parsing-management.md`、`database-design.md`、`code-map.md` 为准。
- 状态推进节拍：`Accepted` →（放令后）`In Progress` → 关闭时翻 `completed.md`，todo 镜像行随壳状态同步。

## 承载 REQ-142／REQ-143（根侧入站请求）

**目的**＝根侧两行「人工改标注可用」与「目录真实生成一轮」的成功标准，其本仓侧通路全部落在这里。

**成功标准（本仓侧的分项，条目正本在根侧台账行）**＝① `corrected_type` 可写可读且改判进目录取材；
② 页域判据成读时纯函数**并被梳理取材消费**（`section_kind` 写手经需求方 2026-10-26 改判取消，该列维持在册无写者）、零 ALTER；
③ 非 503 的梳理批次可受理并产出章级目录、每笔调用有账；④ 详解长文有 HTTP 写腿且 jail 不放宽；
⑤（第三令补）重新解析可带范围与人工评审备注、块编辑可表达「隐藏」与「强调」两形且只隐不删；
⑥（第九令补）目录行名不带行末引导点与页码、锚指正文页（`KA-41`）；整册工作区逐行给出「本节从 X 页到 Y 页」的派生两键，
且与知识点腿实际取材的区间同源（`KA-42`）；`stage=content` 在有目录后可受理、只补知识点与节级总结、目录行逐列不变（`KA-43`）。

**工作项**＝上表 W1~W22；REQ-142 取 W2~W4（＋W1 的改判半条），REQ-143 取 W5~W7 与 W11~W13（＋W1 的生成半条）、
第六令起追加 W14~W19、第九令追加 W20~W22（件①②合并一件／件④／件⑤）。

**状态**＝`In Progress`（2026-10-26：REQ-142 的本仓侧通路 W2~W4 已达成并过三门禁，其「真实生成一轮目录」的半条等 W10 窗口；
REQ-143 的本仓侧通路 W5~W7 已随 R8／R9／R10 出裁后落地并过三门禁（W7 收口 1026 passed／18 skipped），
其四枚待裁点 AF-T1~T4 与第三令的 AF-T5~T8 全数出裁，剩下的只有 W11／W12 ②／W13 三枚延长件的**动码**（等需求方一句「开始执行」）
与 W10 的真跑窗口（D 类，人工发令）；AF-T6 的读侧家具标记已取**甲案**落地（2026-11-01，见 W12 ③ 与「过程与证据」末行）；
**第九令三件 W20~W22 已于 2026-10-29 落码（先红后绿，AF 全量 1176 passed／18 skipped＋`ruff` 全绿），判据与 `api.md` 随步**，其真跑窗口由「走一轮真实调用数学分析原理试试」那句发令承接（只取有界页区间，全本另候令）。
