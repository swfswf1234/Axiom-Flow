# 知识点梳理消费通路实现批（kp-curation-implementation）

状态：In Progress
任务类型：B
最后更新：2026-11-01
关联 ADR：[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)（四表形状与 `links` 越闸例外——本案是它的消费批，不动形状）、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900，梳理调用只经适配层）、
[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 白名单＝自有 `af_*`＋`alembic_version`，本案调用账写共享 `qed_llm_calls` 只写行不改结构）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪唯一源＝DB，取证页集据此构造）、
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（页级意图跨版本存活的先例，锚点越界的判读同形）、
[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)／[ADR 0011](../adr/0011-ledger-one-row-per-task.md)／[ADR 0013](../adr/0013-ledger-row-three-elements.md)（承载与台账行契约）
关联设计：[knowledge-analysis.md](../design/knowledge-analysis.md)（`KA-1~KA-45` 与「验收判据」17 条＝本案判据正本，本案一处不复制；立案时为 `KA-1~KA-28`／13 条，`KA-29~KA-45` 与判据 14~17 由 PLAN-026 追加）、
[database-design.md](../architecture/database-design.md)（`af_kp`／`af_operations` 列清单与四节不变式）、
[api.md](../architecture/api.md)（新端点的参数与响应形状归它）、[parsing-pipeline.md](../design/parsing-pipeline.md)（`PP-4`/`PP-13`/`PP-19`）、
[parsing-management.md](../design/parsing-management.md)（`PM-13` 单实例串行前提）、[code-map.md](../architecture/code-map.md)（分析模块登记面）
关联 Tracker：docs/trackers/todo.md（PLAN-025 镜像行；本案承接 PLAN-024 的 W10，并自 PLAN-024 收口起承载入站请求 REQ-139 的「承载 REQ-139」节）
归档判定：Retain（17 条判据的逐条取证、批次状态机的变异复验与真机梳理窗口读数属不可再生证据；判据本体在 `docs/design/knowledge-analysis.md`）

> **本案的性质**：PLAN-024 交付了 `af_kp` 的**形状与判据**并明文「建值不建写者」，本案就是那个「写者批」。
> 因此本案的两条硬边界是派生的而非自选的：**零 ALTER、零新列、零新域值**（形状已由 PLAN-024 封闭），
> 且**不得新增或改写任何判据**——本案只把 `KA-n` 落成代码与测试，实现中发现形状不足时在本壳登记
> 新的结构请求（R4 起，接续 PLAN-024 的 R1~R3）并等裁，不在本案内改表。

## 目标与成功标准

**目标**：让分析模块从「有表有判据、零实现」变成一条可跑的通路——人工发起一次梳理，机器按生效版本
产出目录与知识点草稿，人逐条背书，根侧从只读导出面拉走已确认结论。

**成功标准（终态形状，逐条判据正文见 [knowledge-analysis.md](../design/knowledge-analysis.md)「验收判据」）**：

- `af_kp` 有写者：受理门三形可判（422／409／建批回批次号），批次三态由 `gen_status` 自身表达，
  机器写来源域、人工写背书域与 `confirmed`，重启残留 `draft` 整批归 `rejected`。
- 每个知识点钉回产物：`anchors` 按其行上 `job_id` 那一版可回指，换版后读数不变、越界行带待复核标记仍可读。
- 批次流水落 `op_kind='kp'` 一行，`result` 给出成功/失败节计数；`gen_status` 域外值仍计数恒 0。
- 只读导出面＝`confirmed=1` 且父先于子、逐行带 `rev`；本组件对共享主表零写入。
- 梳理调用与 `qed_llm_calls` 追加行同批交付，且该表结构未被本组件改动。
- 设计篇 `实现状态` 不再是 Not Started，`database-design.md` 的「本轮范围」四条计数恒 0 断言按写者到齐情况逐条改判并留反向判据。

## 范围与非目标

**范围**：

- `orchestrator/knowledge.py`（新）、`db/repositories.py:KpRepository` 的三条写入腿与读面、
  `api/main.py` 的梳理提交／进度／复核确认／目录重排／只读导出端点、`engines/factory.py` 的文本引擎槽位、
  `infrastructure` 侧调用账写入通路、长文文件通路（`<book_dir>/kp/<node_id>.md`）。
- 文档随步：`knowledge-analysis.md` 的 `实现状态` 与「实现落点」列、`database-design.md` 的 `af_kp`
  「本轮范围」段与不变式行的计数断言口径、`api.md` 新端点条目、`code-map.md` 分析模块行、
  `overview.md` 能力矩阵、`operations.md` 的梳理窗口口径。
- 测试：`tests/unit/test_knowledge_pipeline.py`、`tests/unit/test_db_knowledge.py`（新）、
  `tests/contract/test_api_v1_contract.py`、`tests/integration/` 的调用账与双会话 `rev` 用例。

**非目标（各归其主）**：

- 提示词工程与梳理质量配方＝C 类实验，须先冻结假设／样本／内容哈希／预算／采纳门槛（AGENTS.md 前置），另批。
- 8903 的人工界面与 8900 的提升作业实现＝根仓库承担；本案只交付只读导出面与 `af_node` 幂等锚的约定形状。
- 检索配方、向量索引与知识图谱＝独立线（`ARCH-023`／roadmap），本案不为其预留列或端点。
- 表结构与域值变更＝`database-design.md` 与 PLAN-024 的形状已封闭，本案零改表。
- 「重新摄取时复位 `last_parse_result`／生效指针／进度」与 `confirm`／`ingest`／`sync`／`purge` 四个无写者值的写者
  ＝按用户裁决**不在本案**（REQ-123 已按「关闭且不记欠账」处置，本案不得顺手接写者）。
- 页级人工确认门（PLAN-018 W8）＝另一条线；本案按 `KA-3` 的分离口径执行，确认门未启用不阻断梳理。

## 前置条件

1. PLAN-024 W7/W8 已交付：四表在真库存在、唯一 base `20261022_0001`、`af_kp` 23 列与三个索引名在册；
   本案开始前须实读 `af_kp` 行数（预期 0）并留档。
2. 判据正本 `docs/design/knowledge-analysis.md` 为 `设计状态：Accepted`／`确认状态：已确认`；
   本案实施中发现某条 `KA-n` 无法落地时，**先改判设计再写代码**，不得在代码里留第四种读法。
3. `af_kp` 的存储节已在 `database-design.md` 升为**已确认**（`links` 越闸追认＝PLAN-024 裁决⑲），
   形状讨论不得在本案重开。
4. 库面纪律：业务库 `qed` **保持为空**直到用户明确说「同步 OK」；`qed_test` 现有 17 本 `af_books` 是演示数据
   ⇒ 涉及真 MySQL 的层（`QED_TEST_MYSQL=1`）与真机窗口一律先按 `operations.md` runbook 备份再动，
   且默认门禁不得连带跑 MySQL 层（夹具 teardown 会清空 `qed_test`）。
5. 模型侧：梳理需要一个文本类引擎槽位，由 8900 起停（`MI-2~MI-5`）；未接入时该作业种类判 503，
   本案按 503 语义写用例，不静默回落到解析引擎。
6. 并发前提：单 worker 串行队列与单实例部署（`PM-13`）不变；跨书不并发，同书在飞唯一性按书算（`KA-4`/`KA-25`）。
7. 守护改判是本批验收面而非清理动作：`tests/unit/test_db.py` 现有四条「计数恒 0」断言中，
   `op_kind='kp'` 那条要随插入腿交付**改判为正向断言**（写者到齐后必须有行且字段形状可判），
   其余三条（非空 `embedding_signature`／`op_kind='index'`／`gen_status` 域外值）**保持恒 0 不动**。
8. 用户在 PLAN-024 收口之后另令「开始执行」才动 `src/`（本壳立项只动文档）。

### 前置取证（W0）

只读探针：连 `information_schema` 取表与列面、逐表 `COUNT(*)`，未执行任何 DDL 与 DML；配置目标库实读为
`qed_test`（凭据取自根仓库 `.env` 的 `QED_DB_*`，留档不含口令值）。探针脚本与原始输出留档
`tmp/_plan025_w0_probe.py`／`tmp/_plan025_w0_probe.txt`，按
[ADR 0012](../adr/0012-workspace-directory-hygiene.md) 该目录**不是事实源**，读数以下表为准。

| 取证项 | `qed` | `qed_test` | 判定 |
| --- | --- | --- | --- |
| `af_kp` 行数 | 0 | 0 | 前置条件 1 达成：预期 0 得证，两库梳理面起点干净 |
| `af_books`／`af_operations`／`af_pages` | 0／0／0 | 17／3／1195 | 前置条件 4 成立：业务库仍为空 ⇒ 本案零写 `qed`；`qed_test` 的 17 本是演示数据 |
| `af_*` 表集合 | 恰四张 | 恰四张 | PLAN-024 档 B 形状未被外来对象增删，「零 ALTER」基线可逐名自证 |
| `qed_llm_calls` | 在册，18 列，116 行 | 在册，18 列，116 行 | `KA-23` 有列可写：`service varchar(32) NOT NULL` 在册（余列＝`mode`/`provider`/`model`/`endpoint`/`prompt_template`/`prompt`/`response`/`duration_ms`/`status`/`error`/`created_at`/`task`/`step`/`review_status`/`review_note`/`meta`），W7 只追加行、结构不动 |

两处随步登记（只定本批做法，不改判据）：

- **调用账的行级清理**：`qed_llm_calls` 是共享表，而 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md)
  白名单只允许 DROP 自有 `af_*`＋`alembic_version` ⇒ W7 的 `tests/integration/` 用例必须删掉自己刚插入的那几行
  （按插入返回的自增 `id`），不得把清理寄托在夹具 teardown 上。
- **共享 `qed_*`／`qt_*` 表的接触面**：本案只在 `KA-16`（只读导出面，提升作业归根侧）与 `KA-23`（追加调用账）
  两处接触共享表，其余一律不读写。

## 工作项

| # | 工作项 | 判据位 | 状态 |
| --- | --- | --- | --- |
| W0 | 前置取证：实读两库 `af_kp` 行数与 `qed_llm_calls` 列面并留档（见「前置取证（W0）」节），同时定死调用账用例的行级清理口径 | 前置条件 1／4 | 已执行（2026-10-23：`af_kp` 两库均 0 行；`qed` 的 `af_*` 四表全空、`qed_test` 为 17／3／1195；`qed_llm_calls` 两库各 18 列且含 `service varchar(32)`。只读探针，零 DDL／DML） |
| W1 | `KpRepository` 三条写入腿：机器来源域（身份/结构/锚点/`summary`/`gen_batch`/`model_ref`/`gen_status`，`confirmed` 恒 0）、人工背书域（名称/挂父/`links`/`anchors`/`role`/`type`/`summary` 与 `confirmed` 位，`rev+1` 且不改来源两列）、系统两条（归一与裁剪）；读面 `list_for_book` 按行取证版分组 | `KA-11`/`KA-12`/`KA-17`/`KA-18` | 已执行（2026-10-23：人工腿白名单换成 `_KP_ENDORSEMENT_WRITABLE` 八列＝背书域全集，来源三列／身份／确认位／`rev`／时刻列在语句发出前拒绝（`KA-11`/`KA-12`）；`confirm` 改名 `set_confirmed` 并写明它**不做**前置校验（可回指校验随 W5 的 `confirm` 交付，`KA-14`）；读面新增 `list_by_evidence_version`，分桶键取行自身 `job_id` 而非生效指针（`KA-18`）。**系统两条腿的分工**＝本项交付的是「系统腿不共用这两个通用入口」的列域收口，归一的具体 UPDATE 随 W4（`reconcile_draft_on_startup`），裁剪按「残余移交」③另批。守护＝新增 `tests/unit/test_db_knowledge.py` 13 例；`tests/unit/test_db.py` 的 `test_confirm_is_the_only_human_gate` 与 `test_gen_status_is_machine_only` 由新文件 1:1 承接后删除。**变异复验三处**：放开 `gen_status` ⇒ 白名单例转红；分组键改 `gen_batch` ⇒ 两条 `KA-18` 例转红；撤 `if_rev` 比对 ⇒ 新旧两文件的 `rev` 冲突例双红；逐处还原后门禁 718 passed／14 skipped + ruff clean） |
| W2 | 受理门与批次身份：无生效版本 422 零写入、同书存在 `draft` 行 409、正常建批发 `afkp-<hex12>`；可选单章范围参数（取证集按页区间构造、裁剪仍按 `book_id + gen_batch` 计数） | `KA-1`/`KA-4`/`KA-10`/`KA-24`/`KA-25` | 已执行（2026-10-23：新增 `orchestrator/knowledge.py` 的 `accept_kp_job`——按书目 404 → 无生效版本 422 → 页区间 422 → 同书在飞 409 的**固定顺序**判定，四道全在写任何行之前返回（`KA-1`「零写入」由「此函数不触任何仓储写方法」守住）；发号 `new_batch_id()` 只产 `afkp-<hex12>`（`KA-10`）；在飞判据读 `af_kp` 自身的 `draft` 行、**不读** `op_kind='kp'` 流水（`KA-4`，流水禁作当前态判据）；默认区间＝整本 `1..page_count`，单章区间须同时给起止。**受理→首行落库之间的窗口**不新增判据：由 `PM-13` 的单 worker 串行队列 + W3 执行期复查承接，已写进 `accept_kp_job` 的 docstring。**未随本项交付的一半**＝422/409 的**端点面契约测试**，随 W5 的梳理提交端点落地（门在本项、HTTP 映射在 W5，两段各自可取证）。守护＝新增 `tests/unit/test_knowledge_pipeline.py` 12 例（含两次发号不同、`re.fullmatch` 形状、parametrized 五种非法区间、无自动排批 `KA-24`） |
| W3 | `orchestrator/knowledge.py` 两阶段执行体：目录阶段（只读 heading＋`section_kind`＋页序，两层封顶）先于知识点阶段（逐节读页正文，缺页剔除且计数可见）；未归档池一等公民；锚点越界只标记不改写 | `KA-2`/`KA-5`/`KA-6`/`KA-7`/`KA-19` | 已执行（2026-10-24：**适配层先立形状**——`engines/base.py` 新增 `KnowledgeEngine` 协议与两阶段输入类型，其中 `PageHeadings` **没有正文字段**，故「目录阶段读不到正文」（`KA-5`）是构造性事实而非调用方自觉；点阶段用 `SectionInput` 逐节给页正文，梳理与解析同走适配层（`KA-22`）。**执行体**（`orchestrator/knowledge.py`）：`build_evidence_set` 三道门槛＝`status='parsed'`（DB 唯一就绪源）／`.md` 与 `.blocks.json` **成对**在位／请求页区间，缺页与未解析页各自计数可见（`KA-2`）；`build_outline` 只读 heading＋`section_kind`＋页序，`depth>2` 拒收并单列计数（`KA-6`），章→节的父只取「上一条 `depth=1` 的节」而非按名猜（`KA-26`）；`run_knowledge_job` 开头复查在飞（他人 `draft` ⇒ 409 点名既有批次、同批不自我否决），目录行**全部先于**点行落库提交（`KA-5` 的次序断言）；`extract_points` 逐节调模型、逐节提交，单节 `EngineError` 只跳该节并计入 `failed_sections`（失败域不拖整批），形状不合的草稿（缺 `name`／`anchors` 空／闭集外 `type`·`role`·`links[].kind`／`links.to` 回指不到）不写且计入 `dropped_nodes`；挂父合法来源只有两种＝喂给模型的那一节、草稿 `parent_ref` 能在**同批目录行**回指到，回指不到落 `NULL` 进未归档池并计 `unarchored`（`KA-7`/`KA-26`，不替机器认一个不存在的父）；`norm_key_for`＝NFKC＋空白折叠＋casefold，写 `norm_key` 折叠而 `name` 逐字保持。**只读复核面** `anchor_review_flags`：按**行自身** `job_id` 那一版解释锚点（`KA-18`），页不在该版 parsed 集／块序越出该版 `blocks.json` 空间 ⇒ 只出标记、`anchors` 逐字不改（`KA-19`），`job_id=''` 的人工直填行逐锚标「无取证版本可回指」。**未随本项交付的一半**＝收尾 `draft→candidate`（W4）、端点与 `op_kind='kp'` 流水（W5）、长文与删除通路（W6）、`engines/factory.py` 槽位与 `qed_llm_calls` 调用账（W7）、`code-map.md` orchestrator 行并入梳理职责（W8）。**随步登记（零新列、零新域值）**：`anchors[].kind` 机器侧取值定为 `page_block`（根侧 `anchors` 形状未命名机器侧 kind，属 JSON 内容约定）；设计里闭集外「先入 `note`」那句读作**人工背书腿**的动作，机器腿遇闭集外值不折、不写、只计数——若真机要机器侧「先入 note」，按 R4 起登记结构请求再裁。守护＝`tests/unit/test_knowledge_pipeline.py` 由 12 例扩到 47 例（含目录阶段输入的 `not hasattr(page,"markdown")`、只有 `.md` 无 `.blocks.json` 的半批不算成对、`isinstance(FakeKnowledgeEngine(), KnowledgeEngine)` 协议形）。**变异复验**：撤 `OUTLINE_MAX_DEPTH` 封顶 ⇒ 5 例转红；把缺产物页错判成「已取证」（`missing.append` 改 `continue`）⇒ 2 例转红；`_resolve_parent` 的回指不到由 `return None` 改成退回本节 ⇒ 1 例转红；另有一处**等价变异**（撤「`.md` 也在位」判断）不转红，因 `except (OSError, ValueError)` 把该页重新归为缺页，遂补一条半批用例把判据本身钉住。逐处还原后门禁 765 passed／14 skipped + ruff clean（MySQL 层按用户裁决仍禁跑）。 |
| W4 | 收尾与启动期归一：本批 `draft→candidate` 单语句条件 UPDATE、让位分支（同父同 `norm_key` 且已 `confirmed=1` ⇒ 新机器行判 `rejected`）、残留 `draft` 整批 `rejected`、整批一次 DFS 次序边校验与 `ix_af_kp_norm` 查重 422、机器新父必属同批目录 | `KA-9`/`KA-13`/`KA-26` | 已执行（2026-10-24：**收尾四步的次序本身就是判据**——`finalize_batch` 先按 `KA-13` 让位（新行撞上 `confirmed=1` 的同一身份 ⇒ 只作废**新行**、已确认行逐列不动，被作废的行因此不再进图、不占边），再按 `KA-26` 校验挂父来源，再做边的对称归一与整批一次 DAG 裁剪，最后**一条** `UPDATE ... WHERE gen_status='draft'` 把本批翻成 `candidate`；翻面置于末步正是「中途死 ⇒ 残留 `draft` ⇒ 启动期归一」这条通路成立的前提。**系统腿落进 `KpRepository`**（八法＝`list_batch`／`confirmed_identity_keys`／`find_norm_conflict`／`flip_batch_status`／`reject_draft_nodes`／`rewrite_draft_links`／`detach_draft_parent`／`reconcile_draft_on_startup`），`KA-9` 的「改写方向唯一」做成构造性＝`GEN_FORWARD_TRANSITIONS` 只放三对前进边，反向与终态回改在语句发出前抛 `ValueError`；每条系统语句自带 `WHERE gen_status='draft'`（批次级再叠 `gen_batch`＋来源态），「`candidate` 及以上行零被触碰」由语句写不出来、不靠调用方自觉。**让位身份**＝`(node_kind, parent_id, norm_key)` 三元组：`node_kind` 进身份因为 `ix_af_kp_norm` 的命名空间含它（同名的目录行与点行是两个结论），对手集合只读 `confirmed=1` ⇒ 机器批次之间不互相否决，否则 `rejected` 就成了机器自相残杀的位。**DAG**＝`ORDERING_LINK_KINDS` 只含 `prerequisite` 与 `extends`，`co_requisite`／`explains`／`exemplifies` 成环合法（同批场景里行上的 `explains` 边因此被保留）；DFS 用显式栈、根与边一律 `sorted` 定序 ⇒ 拒哪条边可复现；环里断的是**本批 `draft` 行**那条边并回指环路径，整环都长在别人行上时只回报、一行不改（`KA-11` 的「零被触碰」优先于修环）；`rejected` 行退出活图——它不进导出面也不占「最新 2 批」名额，其边永不被读，不该拦下本批的边。**对称归一**＝`co_requisite` 的反向重复声明按 `min,max` 归一后去掉多写的那条（处置本就是「静默归一、不报错」），且只清可写行：正向长在 `candidate` 行上、反向在本批行上时不去替人归一，也不把边迁到对端——「写侧不得为『整齐』去改对端行」是同一节的另一条不变式，读侧并集本来就认它。**启动期归一的装配点**＝`api/main.py` 的 lifespan，紧跟 `runner.reconcile_on_startup()`、与解析侧同一条「首次响应之前归一」通路，逐 `(book_id, gen_batch)` 一组一条 UPDATE＋一条 `log.warning`，返回处置清单可取证、二次调用必为空。**顺带补掉的两处 W3 形状缺口**＝①`_section_spans` 在目录阶段零产出时 `ordered[0]` 直接 `IndexError`（认不出任何标题的书会当场崩），现按「整段正文自成一段、抽出的点全入池」早退；②机器草稿的 `links[].to` 原样存的是**名称线索**，违反「边的端点存在」（读侧拿不到端点身份，8903 与根侧都定位不到那一条行），改为落库前解析成 `node_id`（`_endpoint_id`＋`_resolve_links` 取代 `_links_resolve`），同名多行取 `confirmed=1` 那一行——`KA-13` 的「不与人工争同一身份」在边上也成立。**`ix_af_kp_norm` 查重 422**＝判据位单点收在 `find_norm_conflict`（空父走 `IS NULL`，未归档池与挂在某父下是两个作用域；`exclude_node_id` 自免；`order_by(created_at, node_id)` 保证点名可复现），HTTP 的 422 映射与响应点名随 W5 的声明端点交付，与 W2 的「门在本项、映射在 W5」同形。**随步登记（零新列、零新域值）**：①系统腿每条归一都按列契约 `rev+1` 并前移 `updated_at`，故 `KA-15` 导出面的 `rev` 游标会把一次启动期归一读成整批前移——这是「三态只由 `gen_status` 表达」的代价，不改判，W5 的导出面按 `gen_status` 过滤即可抵消；②让位与退池各只改一列（`gen_status`／`parent_id`），不删行、不改 `name`；③`KA-13` 的「同父」在跨批重跑下几乎恒不成立，形状缺口按 R4 登记等裁（见下节），本案按字面判据实现。**未随本项交付的一半**＝422 的端点面与 `op_kind='kp'` 流水行及其 `result` 计数（W5）、长文与删除通路（W6）、`engines/factory.py` 槽位与 `qed_llm_calls` 调用账（W7）、`code-map.md`／设计篇「实现落点」／四条恒 0 断言的晋升改判（W8）。守护＝`tests/unit/test_knowledge_pipeline.py` 由 47 例扩到 72 例（翻面是作业末步且中途恒 `draft`、在飞随之解除、非本批行逐列「零被触碰」快照、方向表拒三对反向、让位五形、DAG 四形、端点身份两形、`ix_af_kp_norm` 判据四形、启动期归一的逐批清单与 `confirmed` 不动＋`rev` 只加一、挂父退池、空目录区间不塌），`tests/contract/test_api_v1_contract.py` 新增 1 例证明归一发生在**首次响应之前**。**变异复验七处**：撤让位分支 ⇒ `KA-13` 两用例转红、其余 70 例不动（让位与其余步骤互不遮蔽）；撤 `GEN_FORWARD_TRANSITIONS` 判断 ⇒ 方向表用例转红；`ORDERING_LINK_KINDS` 扩到全闭集 ⇒ 跨批环用例转红（被拒边的 `kind` 变了，正是「DAG 只覆盖次序语义」那句话）；撤端点改写 ⇒ 两条端点用例转红；撤空目录早退 ⇒ 5 例转红并实证 `IndexError`；撤 lifespan 的归一装配 ⇒ 契约用例转红（残留行仍是 `draft`）；撤对称归一的 `writable` 作用域 ⇒ 「反向重复长在别人行上」用例转红（该用例即为此次变异而补）。另有一处**不可达的等价变异**如实明记、不谎称有守护＝`reject_draft_nodes`／`rewrite_draft_links`／`detach_draft_parent` 里的 `WHERE gen_status='draft'` 从调用方不可达（可写集合本就由 `gen_status='draft'` 查得），属纵深防御，无独立用例能单独把它证红。逐处还原后门禁 791 passed／14 skipped + ruff clean（MySQL 层按用户裁决仍禁跑）。 |
| W5 | 端点面与批次流水：梳理提交／进度／复核确认（`if_rev` 乐观并发）／目录整批声明（缺席只降级、锁内整批替换）／只读导出（`confirmed=1`、父先于子、逐行 `rev`）；`op_kind='kp'` 插入腿与 `result` 计数形状 | `KA-8`/`KA-14`/`KA-15`/`KA-27` | 已执行（2026-10-24：**七条端点各自只多一个读法**——`POST /api/v1/kp-jobs` 的判定序＝书目 404 → 无生效版本与非法区间 422 → 同书在飞 409 → 槽位与探活 503 → 202，四道门全排在 `_kp_text_engine()` 之前（非法请求不打模型，`KA-22` 的成本归因才成立），受理成功后 `session.commit()` 释放读事务再排队（与 `create_parse_job` 同一条纪律，否则执行体在同一连接上写不了库）；进度端点的 `counts` 五键恒发且作用域＝**本批**，`in_flight` 读 `af_kp` 的 `draft` 行、**不读**流水状态（`KA-4` 的分列到端点面才可取证）；工作区端点不跟生效指针，逐行按自身 `job_id` 解释锚点（`KA-18`）；`PATCH /api/v1/kp/nodes/{node_id}` 只写请求体里出现过的键（缺席与显式空串不是一回事），判定序 404 → `if_rev` 409 → 形状 422；`POST /api/v1/kp/nodes/{node_id}/confirm` 的可回指前置校验只在置位跑、撤回永远可执行（`KA-14`/`KA-17`），422 的正文与 200 **同形**（`node_id`／`confirmed`／`rev`／`missing` 四键，不套 `detail`）且 `rev` 不自增；`PUT /api/v1/books/{book_id}/kp/outline` 在 `lock_book` 那把 `af_books` 行锁内「读现有 → 算差异 → 写全部」（CW-4），缺席的目录行**不下线、不删**、只让它名下的点退池（`KA-8`），形状校验全部先于写入；`GET /api/v1/books/{book_id}/kp/export` 只给 `confirmed=1`，`review_flags` 只承载 `KA-3` 的外发提示，逐行带 `rev` 与 `updated_at` 而服务端不记「谁拉过什么」。**流水插入腿**＝`KpBatchRow`＋`OperationRepository.create_kp_batch` **整批一行**（`lifecycle='event'`、`actor='system'`、`job_id`＝取证版本、`target_id`＝批次号、`page_no` 留 NULL、`engine` 留空——该列的文档语义是 parse 行的显式引擎名，梳理引擎名留在 `af_kp.model_ref` 逐行上），`result` 由 `kp_batch_row()` 构造、只放执行体账面已有的十四键读数（`KP_RESULT_KEYS` 闭集，含缺页与未解析页码数组不截断）；**发出点在 `JobRunner._record_kp_batch`**（执行批次的会话此刻要么已提交要么正回滚，失败批次的账面不能跟着消失，异常路径各写一行 `status='failed'`），执行体本身不写流水。**前置条件 7 的改判**＝`test_remaining_writerless_op_kinds_are_never_written` 的循环由五值收成四值（`confirm`／`ingest`／`sync`／`purge`），其余三条恒 0 断言不动；改判**前**先跑一次实证 `AssertionError: kp`／`1 == 0`——写者到齐后「计数恒 0」已不可能成立，改判是必然不是放宽；`_exercise_every_writer` 现**含** kp 插入腿，故那三条是在梳理写者也在跑的状态下成立，顺带证明插入腿不越界替别的种类造行；新增 `TestKpLedgerRow` 四条正向断言＝有行且按 `(op_kind, book_id, target_id)` 读回、「行种类与坐标自证」的 kp 半边、`payload` 三键与 `result` 闭集、事件行不被状态机三条回写路径推进且 `updated_at` 恒等于 `created_at`。**导出序补齐的守护**＝`order_for_export` 此前只经端点间接经过、没有会红用例（变异复验时暴露这一空档），补 `TestExportOrdering` 5 例：点行早于父入库仍排在父后、章与节按 `order_hint` 而非入库序、同父的点之间也按 `order_hint`、未归档池与悬父同尾置、人工改挂成环时每行仍出一次不静默丢。**随步订正四处「尚未交付」陈述**＝`orchestrator/__init__.py` 的「流水随 W5」改成本案实际次序、`knowledge.py::_live_graph` 的「归 W5 的受理门判 422」改成 `validate_endorsement_links`、`test_knowledge_pipeline.py` 的两处 W5 指针改成落点名；`api.md` 四处随步（confirm 422 正文同形、人工直填行的真实文案、`counts` 的本批作用域、`parent_id` 只写列不校验父是否存在）。**随步登记（零新列、零新域值）**＝`target_id` 在列注释与 `database-design.md` 里写作「kp 相关行＝node_id」，本案按 `KA-27` 授予的「整批一行与逐节点一行由实现批定」取**批次号**（`result` 的失败节计数没有节点可挂），措辞随 W8 改判。**未随本项交付的一半**＝长文与删除通路（W6）、`engines/factory.py` 的槽位注册与 `qed_llm_calls` 调用账（W7；本项只交付端点面的 503 判定＝`settings.kp_engine` 空或注入工厂缺失，不回落）、`code-map.md` 与设计篇「实现落点」及 `database-design.md` 恒 0 措辞的晋升改判（W8）。守护＝`tests/contract/test_api_v1_contract.py` 新增 `TestKpCurationEndpoints` 23 例，`tests/unit/test_db.py` 新增 4 例，`tests/unit/test_knowledge_pipeline.py` 新增 5 例。**变异复验十处**：`create_kp_batch` 的 `lifecycle` 改 `intent` ⇒ 自证例转红；注释掉 `_run_knowledge` 成功腿的流水发出 ⇒ 流水／在飞分列／声明三例转红；撤 `confirm` 的可回指校验 ⇒ 契约 1 例与 `TestConfirmPrechecks` 6 例转红；撤导出面 `confirmed=1` 过滤 ⇒ 导出例转红；撤 `group.sort` ⇒ 章节序例转红；撤 `points.sort` ⇒ 同父点序与尾置两例转红；撤 `emitted` 环保护 ⇒ 5 例全转红（行被重复外发）；把缺席目录行下线 ⇒ 契约 1 例与仓储 3 例转红；把形状校验提到 `if_rev` 之前 ⇒ 判定序契约例与仓储例双红；把空槽位回落成 `"mineru"` ⇒ 503 例读成 202。逐处还原（四个被改文件按 sha256 与变异前备份逐字节比对一致）后门禁 845 passed／14 skipped + ruff clean（MySQL 层按用户裁决仍禁跑）。 |
| W6 | 长文与删除通路：临时文件→校验→原子替换、`narrative_ref` 恒为单段文件名、删节点与清引用边同事务且文件在提交后清、孤儿只读清单 | `KA-20`/`KA-21` | 已执行（2026-10-24：**本项只立库面通路、不新增端点**——`KA-20`/`KA-21` 的落点是 `orchestrator/knowledge.py` 的新节（`NARRATIVE_DIRNAME`／`narrative_file_name`／`_narrative_path`／`write_narrative`／`remove_node`／`list_narrative_orphans`）与 `KpRepository.delete_node`；`api.md` ④ 仍是七条，加端点属契约变更须先改设计，本案禁止新增判据。**派生路径唯一算式**＝`_narrative_path` 按**行所属书**的 `domain_id/course_id` 现算 `<book_dir>/kp/<node_id>.md`、**不读 `narrative_ref`**（文件名即节点身份），宿主书目缺席判 404；两道闸＝`ensure_safe_segment`（单段名）＋`ensure_within`（根内），故越根的 `node_id`（`afkp-../outside`）拒在拼路径之前、一个字节都不落盘。「**长文写在行提交之后**」做成构造性事实而非调用方自觉＝`write_narrative` 先 `KpRepository.get`，读不到行就 404 ⇒ 一条从未提交的行没有可写的路径；落盘走 `atomic_write_text`（`PP-4` 同目录 `.<name>.tmp-<hex8>` → `os.replace`），原地替换且不留瞬态残留。**R5 的形状（零改判、不等裁）**＝`write_narrative` 返回单段名但**不碰这一列**，读侧只用派生式，两种读法都不新增；列的形状闸落在机器腿 `create` 的发出前校验，段规则与产物路径共用 `schemas.SAFE_SEGMENT_PATTERN` 一处正则（`repositories.py` 不引 `ingest`，免得把 `pymupdf` 拖进 db 层——`ingest/__init__` 会拉起 `importer`）。**删除通路**＝`delete_node` 在 `lock_book` 的 `af_books` 行锁内「删行＋清引用边」同一事务完成，逐持边行**整列覆写** `links` 并 `rev+1`、前移 `updated_at`，其余列零动（`confirmed` 仍是 1）；处置面**只点名 `links`**，`parent_id` 不在 `KA-21` 范围（删父留下的悬子已由 `KA-14` 确认门与导出面尾置各自取证）；属系统「裁剪」一路，故不复用人工腿 `update`（域白名单只放背书域），也**不写** `op_kind='purge'` 流水（该值仍「建值不建写者」，`test_db.py` 三条恒 0 断言不动）。清边按 `book_id` 逐行扫、在 Python 里判空，不用 SQL `links IS NOT NULL`：JSON 列把 Python `None` 存成序列化 `null`，SQL 的空判据筛不掉它——该非显然约束已写进 docstring。**文件只在提交之后清**＝`remove_node` 先事务后 `commit`，`unlink` 失败只 `logger.warning`、不回滚也不抛（行已提交，把「文件删不掉」升级成「删除失败」只会让人对着一个仍在表里的正文反复重试）；留下的残件正是 `list_narrative_orphans` 该点名的东西。**孤儿只读清单**＝只点名、一个都不删；回指判据用派生 stem 而非列，**任何 `gen_status` 都算活**（作废行也留在表里读批次账面，`KA-9`/`KA-13`）；`.tmp-`／`.old-` 瞬态残留不混入，交 `PP-6` 的识别式与告警面；书目不在库里、`kp/` 目录不存在都按空表算，故裁剪流程能逐书空跑一遍再拿清单去要批准。**零改形状自证**＝`models.py` 与迁移零动（零 ALTER／零新列／零新域值），`narrative_ref` 在梳理通路上本轮无写者。守护＝`tests/unit/test_db_knowledge.py` 新增 `TestNarrativeRefShape` 7 例（五形越根值拒在插入前／单段名收／空值仍表「无」）与 `TestNodeDeletion` 7 例（清边、同其余边逐条不动、行与边同事务可回滚、缺行零写、只有 `links`＋记账两列动、`parent_id` 在清除范围外、不造流水行），`tests/unit/test_knowledge_pipeline.py` 新增 `TestNarrativePlacement` 5 例、`TestNodeDeletionPath` 5 例、`TestNarrativeOrphanList` 5 例，本轮合计 29 例；R5 末句所指的守护名＝`TestNarrativePlacement::test_the_narrative_path_does_not_write_the_narrative_ref_column`（同形改判时它随裁定翻成正向断言）。**变异复验十二处**：撤清边 ⇒ 3 例转红；清成全列覆写 ⇒ 1 红；清边语句带上 `confirmed=0` ⇒ 1 红；在仓储里替调用方 `commit` ⇒ 1 红；撤 `create` 的列闸 ⇒ 5 红；撤单段名闸 ⇒ 1 红（读成 `FileNotFoundError`）；`write_narrative` 代写该列 ⇒ 1 红（`'afkp-a1.md' != ''`）；把清文件提到提交之前 ⇒ 1 红；清理失败改抛错 ⇒ 1 红；孤儿清单改读 `narrative_ref` 而非派生 stem ⇒ 4 红；撤 `is_transient_name` 过滤 ⇒ 1 红；让清单动手删文件 ⇒ 1 红。逐处还原（两个被改文件按 sha256 与变异前备份 `d0006f10…`／`984d68ab…` 逐字节比对一致）后门禁 874 passed／14 skipped + ruff clean（MySQL 层按用户裁决仍禁跑）。**未随本项交付的一半**＝`engines/factory.py` 槽位与 `qed_llm_calls` 调用账（W7）、`database-design.md` 的 `narrative_ref`「相对路径」措辞改判为单段文件名与设计篇「实现落点」晋升（W8）、真机梳理窗口（W9）。） |
| W7 | 适配层槽位与调用账：`engines/factory.py` 文本引擎槽位（未接入判 503、闭集外拒绝）、`qed_llm_calls` 追加行（`service='axiom_flow'`，只写行不改结构） | `KA-22`/`KA-23` | 已执行（2026-10-24：**槽位面（`KA-22`）先立登记形状**——`engines/factory.py` 新增 `TEXT_ENGINE_NAMES`／`IMPLEMENTED_TEXT_ENGINES` 两个元组（当前皆空：梳理引擎名须与 8900 的槽位身份一致，不在实现批凭空立名），`resolve_text_engine_name` 三形各判（空名／未登记／登记未接入）一律 `TextSlotError` 且**不回落**，`build_text_engine` 先过 resolve 再抛＝接适配器时唯一改动点，登记面与实现面都在它前面把关（只加适配器不登记、或反之，当场报错而不是静默返回一个没人认得的引擎）；`config.py` 新增 `kp_engine: str = ""` 与 `AXIOM_KP_ENGINE` 映射，缺名不回落成任何具体引擎（与 `ocr_engine` 同一条纪律，ADR 0004 无静默兜底）。**账位面（`KA-23`）＝新模块 `db/llm_ledger.py` 对共享表只有一条 `INSERT`**：12 列写入面＝常量三列（`service='axiom_flow'`／`endpoint='text'`／`task='kp-curation'`）＋身份三列（`mode`/`provider`/`model`，只能由装配点注入）＋`step`（两阶段名）＋`duration_ms`/`status`/`error`/`created_at`/`meta`；**不建 ORM、不进 `Base.metadata`**（`af_` 表族守护扫的就是元数据，挂上去等于认领这张共享表），`prompt`/`response`/`prompt_template` 留 NULL（完整文本只在适配器手里，账层拿摘要冒充就是往共享表写假事实），审核两列一列不写（`unreviewed`／`''` 由库侧默认给），归因（哪本书、哪个批次）进 `meta` 扩展槽（根侧定义＝只读不筛、不进 WHERE／索引）。**R6 甲案落地**＝身份由装配点注入 `LlmCallIdentity`，「有引擎无账」与「有账无身份」都在**受理期当场拒跑**（`KpLedgerWiringError` → API 503、零写入；宁可 503 也不跑一批查无此账的调用，那正是把账推成永久欠账的形状）。**发出前的形状闸抛、不发**＝`step`/`status` 闭集、`mode` 落在根侧两形、三枚身份列非空、`duration_ms` 非负——身份给空值会让整条 INSERT 被 MySQL 拒掉、账反而更少，那种错必须当场抛给调用方；其余失败只 `logger.warning` 降级为不落账（调用账是审计线索，不能反过来把模型调用打挂）。`error` 由写入器截到 500（VARCHAR(500) 超长整条拒收）；`created_at` 由写入器自盖且**不接受调用方给值**（DATETIME 秒精度，绑 `strftime` 字符串而不是 `datetime`，避开 Python 3.12 已弃用的 sqlite3 默认适配器）；`lastrowid` 回传自增 id 供行级精确清理（共享表不做整表清理）。**包装器 `LedgeredKnowledgeEngine`**（`orchestrator/knowledge.py`，挂在 `JobRunner._run_knowledge` 这个梳理通路唯一引擎入口之外，管线与判据一条不改）＝一调用一行；失败带原因后**原样抛出**（`extract_points` 的节失败域靠的就是同一个 `EngineError`，包装器不能把它吞成「这节没产物」）；`name` 与协议之外的能力一律透传（`_run_knowledge` 的 `finally` 用 `getattr(engine, "close", None)` 收尾，不转发就漏掉适配器自己的资源）；`duration_ms` 由包装器两侧 `time.perf_counter` 实测（是「调用耗时」，不是适配器自报的数字）；账走 `LlmCallLedger` 自己的会话 ⇒ 批次回滚带不走已经发生过的调用。**真机取证**（`tmp/w7_ledger_probe.txt`，只读 SHOW／SELECT）＝`qed_test` 里该表在位、18 列、116 行、`AUTO_INCREMENT=117` 且全部 `service='qed_engine'` ⇒ 追加面与根侧不冲突。**随步订正**＝真机 DDL 注释写明 `provider` 在 `api` 渠道是厂商名、在 `local` 模式是 **runtime 名**（lmstudio/llamacpp/mineru），`LlmCallIdentity` 的 docstring 与三处测试取值随之改成本层不校验也不反着写的口径。**随步登记（零 ALTER、零新列、零新域值）**＝`models.py` 与迁移链本轮零动；共享表 18 列由 `tests/support/llm_ledger_schema.py` 一处 SQLite 镜像供单测与集成层共用，本仓不认领其结构。**未随本项交付的一半**＝`knowledge-analysis.md` 的 `KA-22`/`KA-23`「实现落点」晋升、`database-design.md` 共享表节改判、`code-map.md` 新模块行、`operations.md` 的 `AXIOM_KP_ENGINE` 口径（W8），真表实跑与「结构未被 ALTER」取证（W9，`tests/integration/test_llm_call_ledger.py` 4 例现默认 skip）。守护＝新增 `tests/unit/test_db_llm_ledger.py` 27 例（追加面／发出前闸／降级／不碰结构四族），`tests/unit/test_knowledge_pipeline.py` 新增 `TestCallLedger` 11 例，`tests/unit/test_engine_factory.py` 文本槽位 6 例，`tests/contract/test_api_v1_contract.py` 新增 2 例（未接线 503 零写入、一批次一调用一行与逐列账面）。**变异复验 15 处**：撤 `error` 截断／写死 `created_at`／放开 `step` 域／放开身份空值闸／放开负 `duration_ms`／降级改抛／写入器多认领 `prompt` 列／成功不记行／`duration_ms` 写死 0／包装器吞异常／包装器改名／`__getattr__` 改抛 `AttributeError`／`meta` 丢 `gen_batch`／撤受理期拒跑闸／503 映射改捕 `OSError`——逐条转红。两处**首轮 SURVIVED 如实补测**＝`created_at` 原断言只验真值（改为 `utc_now()` 前后夹逼），`__getattr__` 原用例只验协议三件（补 `test_the_wrapper_forwards_an_out_of_protocol_capability` 经 `close` 收尾点取证）；另复验脚本自身首跑漏了替换动作导致 15 条全「SURVIVED」，已加**空操作自证**（写盘字节与原文件相同即报 `NO-OP-MUTATION`）后复跑。逐处还原（四个被改文件与变异前备份 `tmp/_w7pre/` 逐字节 `cmp` 一致）后门禁 921 passed／18 skipped + ruff clean（MySQL 层按用户裁决仍禁跑）。 |
| W8 | 文档晋升随步：`knowledge-analysis.md` 的 `实现状态` 与「实现落点」列、`database-design.md` 的 `af_kp`「本轮范围」段与不变式行按写者到齐改判（`op_kind='kp'` 由计数恒 0 转为正向断言，另三条不动）、`api.md` 新端点条目、`code-map.md` 分析模块行与 `overview.md` 能力矩阵、`operations.md` 梳理窗口口径 | `KA-28` 与「验证与验收」的晋升对账 | 已执行（2026-10-24：**本项零代码改动**——门禁读数与 W7 收尾时一致（921 passed／18 skipped + ruff clean），故晋升只动文档面、不影响实现判据。五个文件的改判＝**设计篇 `knowledge-analysis.md`**：`实现状态` 由 Not Started 转 In Progress（51 字，守 ≤60 字行限）、28 条 `KA-n` 的「实现落点 → 守护测试」列全部去掉「（待实现）」写实名、元数据补齐 `engines/`＋`api/` 与七个测试文件、验收判据与「已知欠账」段按交付面改写、`KA-27` 的 `target_id` 措辞落「批次号（整批一行：失败节计数没有节点可挂）」；**`database-design.md`**：元数据三行（`确认状态`／`实现状态` 52 字／`最后更新`）与前言「`af_kp` 的现状」条按写者到齐改判、表总览 `af_kp` 行的「本轮无写者」改「本侧写者已就位」、`op_kind` 写者面由三值扩到四值并把 `kp` 的守护写明为**正向断言**（有行、`result` 为闭集键、且不越界替别的种类造行）、`kp` 行语义的 `target_id` 落批次号、「当前范围」段由四条恒 0 重写为三条恒 0 ＋ `kp` 正向断言 ＋ 未交付两段（8900 侧拉取式提升作业、「保留最新 2 批」裁剪腿），承载力审计的恒零段同批改判；**`overview.md`**：能力矩阵的梳理条目转态；**`code-map.md`**：api/main.py、orchestrator/、jobs.py、engines/、db/ 五行职责与事实源随步，按锚点插入 `orchestrator/knowledge.py` 与 `db/llm_ledger.py` 两条模块行（列形 6 竖线与既有模块行同形）；**`project-status.md`**：`最后更新` 转当日、能力矩阵端点计数 18→**26**、新增「知识点梳理与复核通路（本侧）」能力行、当前主线增 PLAN-025 W1~W8 条目。**晋升对账（`KA-28` 与「验证与验收」）**＝`tmp/_w8_ka_audit2.py` 把「实现落点」列的每个反引号 token 拿去代码与测试做存在性对账：28 行 4 列齐，`文件:符号` 22／路径 35／端点串 6／测试节点 id 3／裸标识符 43 全 OK，1 个根侧名（`qed_knowledge_point`）不计本仓存在性，2 个 MISS 是格式名 `.md`／`.blocks.json` 被误判成路径（对账器的分类缺陷，非文档缺陷），另以 `grep -lw` 逐条复核 26 个关键符号（`accept_kp_job`…`build_text_engine`）在 `src/axiom_flow` 全部命中；「待实现」残留 0，「未实现」残留 1＝`KA-25` 的裁剪清单（写者未交付，如实保留）。`api.md` ④ 对账＝运行时七条路由 ↔ 端点总表七行 ↔ 七个 `####` 小节逐字一致（守护逐行比对，234 passed）。**本轮不落笔的一项**＝`operations.md` 的梳理窗口口径：`AXIOM_KP_ENGINE` 是本案新增配置键而 MI-13 要求新键走 ADR，R7 未裁 ⇒ 按 R7 末句「裁定前不得把它当既有配置面写进 `operations.md`」执行，本项的运维口径随 R7 裁定另步。改判手法＝两条脚本（`tmp/_w8_promote_arch.py`／`tmp/_w8_promote_arch2.py`）每条锚点断言 `count==1`、失配即整文件不动并 ABORT；备份 `tmp/_w8pre/`（轮前一）与 `tmp/_w8pre2/`（轮二前＋project-status 改前），报告 `tmp/w8_docs_report.txt`、`tmp/w8_docs2_report.txt`、`tmp/w8_ps_report.txt`、`tmp/w8_ka_audit2.txt`。**体裁自证**＝正文去日期化守护（`_contract_body` 起于首个 H2、止于追溯段）与八章骨架守护、设计篇 60 字行限均绿：`database-design.md` 正文任务编号与绝对日期命中 0。**未随本项交付的一半**＝`operations.md` 梳理窗口口径（待 R7）与真机梳理窗口（W9）。） |
| W9 | 真机梳理窗口（D 类，逐次批准）：b05 单本整本书梳理跑通＋17 条判据逐条取证＋备份与回滚指纹 | 「验证与验收」全清单 | 待开始 |

## 结构请求登记（R4 起，接续 PLAN-024 的 R1~R3）

**R4｜`KA-13` 的「同父」在跨批重跑时几乎恒不成立**（W4 实现中发现；本案不改判据、按字面实现，等裁后再动码）。

- 现象：让位身份是三元组 `(node_kind, parent_id, norm_key)`，而 `parent_id` 指向目录行的 `node_id`——目录行的
  `node_id` 每批随机发放（`KA-10`），同一章在新批里必然换一个 id。于是「人已确认的父」与「新批重跑出的父」
  不是同一个值，整本书重跑时三元组只在**双方都落在未归档池**（`parent_id IS NULL`）这一格命中；挂在目录下的点
  永不让位。同一形状也影响 `ix_af_kp_norm` 的「同父重名」查重（422）：同一章重跑出的同名点被判为不冲突。
- 三案（等裁，本案实现取乙案形状）：
  - 甲：身份改用**父的 `norm_key`** 而非父的 `node_id`（零新列；比对时多读一次父行），代价＝父改名后仍算同一身份，
    且每次收尾多一轮父行读取。
  - 乙：维持字面判据，接受「只有未归档池参与让位」，结构侧的重复交人工重排（`KA-26` 本就把池的收敛判给人工），
    零改码，代价＝重跑会在每个新父下各留一份同名点。
  - 丙：把身份降为 `(node_kind, norm_key)`（不含父），代价＝不同章下同名的节互相否决，与 `KA-7` 的池语义冲突。
- 已钉住的现场＝`tests/unit/test_knowledge_pipeline.py::TestLetAway::test_a_different_parent_is_not_the_same_identity`
  （不同父 ⇒ 不让位）；裁决若改为甲案或丙案，该用例随之改判，不需再找判据。

**R5｜`narrative_ref` 落在 `KA-11` 三写者的列举之外**（W6 实现中发现；本案不改判据、按不依赖裁定的形状交付，等裁后再动码）。

- 现象：`KA-20` 要求「长文写在行提交之后」，而列值只可能在该刻存在——于是这一列必须由一次**行之后的 UPDATE** 写。
  三条腿里没有一条允许这种 UPDATE：机器 `create` 只在插入时给值、人工 `update` 的白名单
  （`_KP_ENDORSEMENT_WRITABLE` 八列）不含它、系统两条只碰 `gen_status`／`parent_id`／`links`。
  `database-design.md` 的列注释与 `KpNodeRow` 的输入面把它算进机器来源域，而 `KA-11` 那句列举里没有它——
  两种记法谁赢，正是本条要裁的。
- 三案（等裁）：
  - 甲：归入**人工背书域**（八列 → 九列）。理由＝长文正文由人撰写（`summary` 列注释已明说「长叙述不落本列」）。
    代价＝`KA-11` 的背书域列举要改判；机器若将来产长文仍写不了这一列。
  - 乙：立第四条系统腿 `set_narrative_ref`（只写这一列＋`rev+1`）。代价＝`KA-11` 的「系统只写归一与裁剪两条」
    改判成三条，`tests/unit/test_db.py` 的写者集合守护随之加一形。
  - 丙：机器腿在 `create` 时按 `<node_id>.md` 预填。代价＝`''＝无` 失去意义（无正文的行也带名），读侧只能改读
    「文件在位＝有正文」——正是前置条件 2 禁止的第四种读法。
- 本案实现取的形状（零改判、不等裁）：`write_narrative` 只落文件并返回单段名，**不碰这一列**；读侧只用 `KA-20`
  授予的派生路径 `<book_dir>/kp/<node_id>.md`，两种读法都不新增。列的形状闸（单段名＋根内）落在机器腿 `create`
  的发出前校验，判据位在 `database-design.md` 的「双闸沿用产物约定，拒绝越根」，故验收判据 9 的
  「`narrative_ref` 值恒为单段文件名」当场可守。该列在梳理通路上本轮无写者 ⇒ 「计数恒 `''`」守护先立着，
  裁决落地时按前置条件 7 的同形改判为正向断言；守护名见工作项 W6 行。

**R6｜`qed_llm_calls` 的三枚 NOT NULL 身份列只有适配器知道**（W7 实现中发现；本案不改判据、按不依赖裁定的形状交付，等裁后再动码）。

- 现象：`KA-23` 要「梳理作业与调用账同批交付」、验收判据 10 要「一次梳理作业在该表留下对应追加行」，但根侧 DDL
  把 `mode`／`provider`／`model` 定成 `NOT NULL` 且无默认，三列的取值（api/local、厂商名、实际模型名）只有
  **适配器**知道；而 `KA-22` 明写梳理槽位未接入前判 503，本案又不新增配置键（新键按 `MI-13` 须走 ADR）。
  于是本批能交付**通路**，交付不了**生产者**。
- 三案（等裁）：
  - 甲：身份由装配点注入（`LlmCallIdentity` 三列交给接线的人），执行体在「给了引擎却没给账、或给了账却没给身份」时
    **当场拒跑**——受理期抛错，不静默跑一批无账的调用。代价＝真机窗口要跑梳理，接线方必须同时给引擎与身份。
  - 乙：把槽位名同时填进 `provider` 与 `model`，零注入即可写行。代价＝在共享表上写不属于本仓的语义，
    污染 8900 的 `GET /llm/calls?provider=` 筛选面（该表读权与检索面归根侧）。
  - 丙：只交付写入器、不接执行体。代价＝判据 10 没有生产者，正是 `KA-23` 警告的「把账目推到下一批等于永久欠账」。
- 本案实现取的形状（零改判、不等裁）＝甲案：写入器 `db/llm_ledger.py`（只 `INSERT`、不建 ORM 模型、不进
  `Base.metadata` ⇒ `tests/unit/test_db_table_family_governance.py` 的 `af_` 命名空间守护照旧绿，「零 ALTER」当场自证）
  ＋执行体唯一入口（`JobRunner._run_knowledge`）外的记账包装器。列的填法一律按根侧 DDL 注释的字面用途：
  `service` 恒 `axiom_flow`、`endpoint` 恒 `text`（本通路常量，不接受调用方覆盖）、`mode` 由注入方给（本组件出站恒直连，
  按注释取 `local`）、`task`＝`kp-curation` 与 `step`＝`outline`/`points` 取「任务／步骤标识」的自由形状、
  `prompt`/`response` **留 NULL**（真实串只有适配器知道，不拿摘要冒充「完整 prompt／模型原始响应文本」）、
  输入与产出的可归因事实进 `meta`（根侧定义即「扩展槽：未定档字段先入本列」）、`review_status`/`review_note` 一列不写
  （审核动作归 8900 控制台）。若裁定改为乙案，改的只有默认身份来源，包装器与写入器不动。
- 前提的现场订正（W7 落码后补记）：上面「本案又不新增配置键」这句**已被实现推翻**——`KA-22` 的「缺配」要落地就必须有个可读的键，
  本案交付了 `Settings.kp_engine`／`AXIOM_KP_ENGINE`，新键的 ADR 缺口另登 R7 等裁。甲案的**身份注入形状不受影响**：三枚
  `NOT NULL` 身份列仍只能由装配点给，键只承载「槽位名」，不承载身份。

**R7｜`AXIOM_KP_ENGINE` 是本案新增的配置键，而 MI-13 要求新键走 ADR**（W7 实现中发现；键已随 W5／W7 落码，本壳不改判据、等裁补 ADR 或改判）。

- 现象：`KA-22` 定「引擎名缺配即受理报错，不静默回落」，落地需要一个承载「缺配」的键，于是本案交付了
  `Settings.kp_engine`（默认 `""`）与 `AXIOM_KP_ENGINE` 映射，`api.md` ④ 的两处 503 读法（提交端点面与错误面汇总）已按此键写。
  但 [MI-13](../design/model-integration.md) 的启用路径明写「需要新配置键时按 ADR 流程新增」（`AXIOM_PADDLEOCR_URL` 退役即为此例），
  而 `docs/adr/` 现在没有任何条目授权这个键。
- 与 R6 前提的冲突：R6 把「本案不新增配置键」当成三案选择的约束，实际交付推翻了这句——键已经在装配点上，删掉它就没有「缺配」
  可读的形状，`KA-22` 的 503 判据会退化成「槽位名恒未知」，而闭集拒绝与未接入拒绝也分不开。
- 三案（等裁）：
  - 甲：补一条 ADR，立「梳理文本槽位由 `AXIOM_KP_ENGINE` 配置、空值即受理期 503 且不回落」的口径，MI-13 的字面要求由此满足；
    本案代码与 `api.md` 不动，只在 `KA-22` 的「实现落点」列指到该 ADR（随 W8）。
  - 乙：不新增键，把梳理引擎名并入既有 `ocr_engine`。代价＝两种引擎种类挤一个键，「已配 vision 未配文本」与「两者都缺配」
    在 503 文案里分不开，闭集（`TEXT_ENGINE_NAMES` 与解析登记元组）也失去各自的把关点。
  - 丙：键不动、但按 MI-13 的例外把新键视为「槽位登记表的一部分」免 ADR。代价＝治理规则要先改判（MI-13 属设计篇的扩展条），
    本案禁止改写判据，故不自取。
- 已钉住的现场＝`tests/unit/test_engine_factory.py::TestTextSlot` 四例（空名不回落成任何引擎、已登记未接线报「尚未接入」、
  文本解析永不返回解析引擎名、`build_text_engine` 对任何给定名字都拒）与 `config.py` 的 `AXIOM_KP_ENGINE` 映射一行；
  `docs/architecture/api.md` 三处把该键写成对外读法。裁定为甲案前，本键的真实身份是「设计未授权的实现键」，
  收尾时不得把它当成既有配置面写进 `operations.md`（W8 的梳理窗口口径按裁定落笔）。

**R12~R16｜2026-11-01 合流树审理登记的五处欠账**（PLAN-025 W1~W8 与 PLAN-026 W1~W25 同树后的整体审理产出；
**本壳一处不改码、一处不改判据**，逐条等裁。落点全在 PLAN-026 那一批的件里，动码归该案，编号接续 PLAN-026 的 R8~R11）：

- **R12｜`full` 批次里被容错腿抢回总结的节，读成「本节没有知识点」且零计数**。`engines/kp_gateway.py:685-688`
  的 #129 容错腿在整段 JSON 不可解析时返回 `SectionPoints([], salvaged_summary)`；`orchestrator/knowledge.py:1363-1369`
  把写总结这条腿门在 `content_only` 里，`full` 批次既不写总结也不计失败——`for draft in replied.drafts` 零次通过，
  `failed_sections` 与 `dropped_nodes` 都不动。这与 `KA-32`「坏形状是故障，不是『这一节没有知识点』」正面相冲，
  真机读数（该壳记的 6/98 节有总结）说明它已在产。等裁二案：甲＝`full` 也写总结并新增一枚 `salvaged_sections`
  计数进 `result`；乙＝该档直接判 `EngineError` 计入 `failed_sections`（丢掉抢回来的总结）。
- **R13｜调用账的 `step` 闭集少 `chapter` 一值**。`db/llm_ledger.py:38` 的 `KP_LEDGER_STEPS` 只有四值，
  而 `orchestrator/knowledge.py:2105-2115` 的章腿以 `step="chapter"` 记账。`_checked` 在 `record` 的 `try`
  **之外**发出（`llm_ledger.py:94`），所以对任何不自报 `accounts_externally` 的引擎：成功腿的模型回复被 `ValueError`
  丢掉、异常腿的原始故障被盖掉。当前唯一在册适配器自记账（`engines/kp_gateway.py:388`）⇒ 未犯真机；
  `tests/unit/test_knowledge_pipeline.py::TestChapterLeg` 跑裸 fake 引擎，包装器不在路径上，故用例不红。
  等裁二案：甲＝`KP_LEDGER_STEPS` 扩 `chapter`；乙＝形状闸降级为 warning（与「记账失败不得影响调用」同口径），
  但那就把域值把关让给闭集外的一条静默。
- **R14｜人工改名不重算 `norm_key`，且服务端查重在 PATCH 通路上没有调用方**。`db/repositories.py:1328` 的背书域
  含 `name`，而 `norm_key` 只在 `create`（`repositories.py:1424`）写过一次；`find_norm_conflict`
  （`repositories.py:1702`）的调用方只有测试。后果＝`KA-13` 的让位身份 `(node_kind, parent_id, norm_key)` 与
  `ix_af_kp_norm` 的查重都读旧键，[database-design.md](../architecture/database-design.md) 的「同父唯一由
  **服务端校验**、不建唯一键」（该文件 `norm_key` 行）在人工改名这条腿上不落。等裁二案：甲＝背书域写 `name`
  时同权重算 `norm_key` 并按 `find_norm_conflict` 判 422；乙＝`norm_key` 改读时派生、列降为缓存并加一致性守护。
- **R15｜`PATCH /api/v1/kp/nodes/{node_id}` 的 `parent_id` 不过任何服务端判据**。`schemas.py:810` 收任意 ≤64 字符串，
  `api/main.py:1166-1209` 只给 `links` 挂 `validate_endorsement_links`，父的存在性／同书／`node_kind`／成环一条不判
  （`order_for_export` 只靠 `emitted` 保证每行出一次，不拦环）。`KA-26` 的「合法父来源」目前只约束机器腿。
  等裁：是否给背书域补一条父判据（含「不许把人挂到自己或自己后代之上」），还是明文把该判据交给根侧 8903 的 UI。
- **R16｜家具线判据的分母两侧不一致，与印刷页码偏移的投票按命中计**。
  `normalize/furniture.py` 的模块前提写「输入是整本生效版本的页块序列」，而 `orchestrator/knowledge.py:398-399`
  只喂本次取证窗口的页、`api/main.py:508-518` 喂整本 ⇒ `MIN_PAGE_RATIO` 在 4 页窗口里恒过，同一行可能一边被剔出
  送模型的正文、一边在 `/pages` 上不被标记。另一处：`normalize/toc.py:311-318` 把每一枚**命中页**都算一票，
  一条泛名（如「极限」前缀命上 20 块）能压过真正的众数，`±1` 窗口随之锚错页。等裁：分母是否固定整本
  （取材侧改整本二次扫描或进程内缓存，`KA-29`／`PM-19` 落点随步）；投票是否改为每枚条目一票。
- **R17｜一次未复现的门禁变红，登记为观察项而非缺陷**。本轮第三次全量门禁（与一枚 Python 探针并行跑）里
  `tests/unit/test_knowledge_pipeline.py:1504` 的「落库即可读」断言变红（同批 `test_tracker_governance` 也红，
  那枚是本案文档随步写进 `todo.md`「任务」列的 ISO 日期字面量，已由 ADR 0018 的守护抓住并改掉）。随后三次串行
  全量复跑（61.51s／49.54s／48.81s）与单独复跑该类八条全绿 ⇒ **未复现**。已排查掉的两个漂移面：该断言核对
  `db/repositories.py:list_batch` 的 `(created_at, node_id)` 读数序，而 `created_at` 由 `db/engine.py:34`
  `utc_now()` 逐行取（微秒分辨率，不随秒边界并列），`node_id` 的随机值只在同刻并列时决定次序——两条都不成立，
  剩余可能是那次并行负载。等裁：① 真机窗口前用重复跑（`pytest --count` 或循环 N 次）定位并留差异；
  ② 接受为未复现观察项，再红时按 `-vv` 差异取证后再判。

> R7 的随步补充（2026-11-01）＝本案新增配置键已由 PLAN-026 W6 扩为三枚（`AXIOM_KP_ENGINE`／
> `QED_ENGINE_GATEWAY_URL`／`AXIOM_KP_TIMEOUT`），且 `kp_engine` 默认值由空串改为 `qed-gateway`＝起服即接通；
> 三枚都仍无 ADR、也没进 `operations.md` 与 `.env.example`。另查出 `config.py` 的空值跳过（`os.environ.get(...) or ...`
> 与 `.env` 的 `if not value: continue`）使「`AXIOM_KP_ENGINE=` 清空」这条 kill switch 在两条配置路上都不可达，
> 503 分支只剩测试注入可达——R7 的三案裁定时一并了结。

## 验证与验收

- **判据对账**＝`knowledge-analysis.md`「验收判据」17 条逐条有会红用例，且用例名或注释可指回 `KA-n`；
  本案不新增判据、不改写判据（新增者视为设计变更，回到设计篇先改判再实现）。
- **TDD**＝W1~W7 每腿先取真红灯（`af_kp` 无写者 ⇒ 先红形态明确）再实现；W5 的端点用例落契约层。
- **守护改判复验**＝对四条「计数恒 0」断言逐条给读数：`op_kind='kp'` 那条改判前后各跑一次，
  并做变异复验（撤掉让位分支 ⇒ `KA-13` 用例转红；把人工写入口放开 `confirmed` 由机器写 ⇒ `KA-11` 用例转红；
  撤掉 `if_rev` 比对 ⇒ 双会话用例转红），复验后按 sha256 逐字节还原。
- **零改形状自证**＝`af_kp`／`af_operations` 列数与索引名在窗口前后逐名相等（真库 `SHOW CREATE TABLE` 双份留档），
  `qed_llm_calls` 结构未被 ALTER。
- **门禁**＝`pytest tests -q` + `ruff check src tests`；固定文档七道守护同批复跑；
  MySQL 层只在用户批准的窗口内跑 `QED_TEST_MYSQL=1`，收尾复跑不含它（夹具 teardown 清 `qed_test`）。
- **人工核对**＝把设计篇任一条 `KA-n` 的「实现落点」文件名改掉，确认对应测试转红（防判据空转）。

## 回滚

- 代码批可整批 `git revert`；本案零 DDL、零迁移，故回滚不涉及库面动作。
- 真机窗口前按 `operations.md` runbook 取 `mysqldump` ＋逐表 DDL 双份备份并留 sha256；
  回滚形态＝按备份重放 `af_kp`／`af_operations` 两表，产物目录内 `kp/` 长文按只读清单人工处置。
- 调用账写入通路的回滚＝停用该腿（不删行），共享表数据不做反向清理（`KA-17` 同轴）。

## 关闭与归档

- 关闭条件＝「验证与验收」逐条达成且 17 条判据全部有读数；W9 未完时不得关闭。
- 归档判定 **Retain**：17 条判据的逐条取证表、批次状态机与让位分支的变异复验、真机窗口读数与备份指纹
  属不可再生证据；关闭后壳移入 `docs/history/plans/`，`plans/index.md` 与月度索引登记去处。
- 六步收口（ADR 0006）：结论入本壳 → todo 行压缩关闭 → `completed.md` → `plans/index.md` → `project-status.md` → 全量门禁。
- 残余移交：① 8903 界面与 8900 提升作业的实现与联调归根仓库；② 梳理质量配方另批（C 类）；
  ③ 「保留最新 2 批」的裁剪执行与触发阈值另批（`database-design.md`「保留与裁剪」只给口径）。

## 承载 REQ-139

**请求接收（QED-Engine 根仓库 REQ-139「知识点梳理模块 Axiom-Flow 承接请求」，挂其 ARCH-025 轮线 2）**——由需求方按其
`docs/standards/cross-project-collaboration.md` 允许的「创建或修改其文档」动作代登本仓 todo 的 `REQ-139` 行与本节
（根仓库请求稿＝其 `docs/plans/2026-10-23-kp-review-af-request.md`，判据来源＝其 REQ-138 核查壳的梳理稿三段）。本行的
类别＝支线、状态取 `Open`（与其 REQ-123／REQ-126 入站行同口径，不占支线并发位）、承载指针指向本壳。**本仓未因此改动任何
`src`／`tests`／迁移**；下表「本仓判定」列在判定前一律为待判，逐条判定与排期归本仓会话。

**载体沿革**：本节原载于 PLAN-024 壳，2026-10-23 随 PLAN-024 收口改由本壳承载；四表形状与判据已由 PLAN-024 封闭，本次搬载不把任一「待判」改成已判。

| 根侧条目 | 请求内容 | 本仓现状 | 本仓判定 |
| --- | --- | --- | --- |
| A1 | `KA-5` 的「一个批次内固定两阶段」改判为多阶段（目录→页域角色判定→章级总结与导读→章下知识点→收尾），次序判据不放宽 | `KA-5` 现只写 outline 与 point 两段 | 待判 |
| A2 | 目录互验条：以 `section_kind='toc'` 页的 heading 块为书内原目录证据，与实判 outline 逐条比对，不一致节数结构化落 `op_kind='kp'` 流水 `result`，逐节复核标记走 `meta` | `KA-1~KA-28` 无此条 | 待判 |
| A3 | `section_kind` 的写者与「前置／后置不进取证集」条（`KA-11` 三写者口径扩到 `af_pages`，`KA-2` 的取证集加一条域过滤） | 列已在册、本轮只建列（其计数恒 0 守护） | 待判 |
| A4 | `narrative_ref` 取值归一：DB 文档写「数据根下的相对路径」，`KA-20` 写「只存单段文件名」 | 本壳 W6 已自陈**单段文件名**，而根侧共享主表存**数据根相对路径全串**，两处相左 | 待判（须给根侧无需向 8902 询问即可自拼的派生规则） |
| A5 | 确认后再总结条：`confirmed=1` 之后的二次总结写哪一列、由谁触发、覆写属机器写域还是人工写域 | 无条文 | 待判 |
| A6 | 页确认门（PLAN-018 的 W8）与梳理取证集的接线：`confirm_state` 写者及其与 `KA-3` 的关系成文 | `confirm_state` 只建列；`KA-3` 预留未启用 | 待判 |
| C7 | 批量取页端点（单次多页、限 `parsed` 页集）＝根侧拼章与逐页渲染的前提 | 现只有逐页端点，整章＝N 次 HTTP | 待判（属解析侧，不在本壳范围） |
| B1~B4、C1~C6 | 写者三条、四条恒 0 守护的插入腿与断言同批改、批次归一用例、文本引擎槽位与调用账；梳理提交／进度／复核确认／整批声明／导出端点 | **已由本壳承接**（W1／W2／W4／W5／W6／W7／W8） | 根侧已降为形状核对，本节不重复索求；**须核对的四件＝四条恒 0 断言是否全在本壳 W8 改判清单内** |
| X1 | 原条件项：给 `af_kp` 的 outline 行加 `page_start`／`page_end` | 未涉 | **已作废**＝根侧 Q4 裁为甲案（由该节全部 point 行 `anchors[].page` 取 min/max 读时派生，零改表）⇒ 本仓无需承接、无需回执，`af_kp` 列面保持 23 列不动 |
| N1 | 文本引擎适配器接入与槽位登记（＝B4／`KA-22` 的核对未闭合件）：`TEXT_ENGINE_NAMES` 与 `IMPLEMENTED_TEXT_ENGINES` 登记一枚可用名字并交出与之对应的适配器，使 `POST /api/v1/kp-jobs` 不再恒判 503；`AXIOM_KP_ENGINE` 新键按 R7 走 ADR 后再进 `operations.md` | 本仓实读＝`engines/factory.py:25-26` 两元组皆空、`:76` 自陈「当前一条都构造不出来」、`config.py:94` 的 `kp_engine` 默认空串 ⇒ 梳理的生成腿现下没有一条可跑路径 | 待判（根侧梳理屏的「提交批次」与「生成总结／导读」两枚动作的可点性以此为前置） |
| N2 | 梳理面在网生效：本壳 W1~W8 的七条 kp 路由现只在工作树且 8902 在跑的进程早于该改动，实测 `openapi.json` 的 16 条路径里 kp 命中 0、`POST /api/v1/kp-jobs` 得 404；根侧 8900 亦无 kp 透传（后端检索零命中）⇒ 8903 此刻无任何合法通路读或写 `af_kp` | 本仓 `api.md` ④ 的七条与 `api/main.py` 的实码在册，本请求不改其形状；归因读数见本节末「根侧代登的形状核对」 | 待判（提交与重启归本仓会话，根侧只等面可指认、不代其执行 git 操作） |
| N3 | 人工直登记知识点的端点：现七条无创建入口（`POST …/kp/nodes` 不存在，`PATCH` 只改既有行），而 `KA-11` 的人工背书域与 `job_id=''` 的人工直填行形状已在册——缺的正是把「人直接填一条点」落库的那一条通路 | 本壳 W5／W6 两处叙述了人工直填行的**读法**与置位时报文案，创建面无处；根侧阶段一以其自有写面顶上，阶段二须归本仓 | 待判（新增端点＝契约变更，按本壳「加端点须先改设计」的口径执行，本案零新增判据） |

**并案关系**：本壳实现的是「已有条文的通路」，本请求要的是「条文本身的增补与归一」，两者不互为替代。若本仓判定
A 面若干条属扩判据（本壳明文「零新增判据」），须另批承接或改判该行范围——由本仓决定并回填本节。

**回执口径**：按本仓 `docs/standards/cross-project-collaboration.md` 与根仓库同名规范，本仓不写根仓库文件；判定与落地
以本仓 todo 的 `REQ-139` 行＋本节为证据，由根侧自行读取登记。

### 根侧代登的形状核对（2026-10-24，需求方现场取证；本仓 `src`／`tests`／迁移零改动）

- **现网读数**：`GET http://127.0.0.1:8902/openapi.json` ⇒ `paths` 共 16 枚，含 `kp` 者 **0 枚**；
  `POST /api/v1/kp-jobs`（请求体 `{"book_id":"mathanalysis-b05"}`）⇒ **404** `{"detail":"Not Found"}`。
- **归因**：在跑的 8902 python 进程 `StartTime` 为 2026-10-23，而 `src/axiom_flow/api/main.py` 的 mtime 为
  2026-10-24 06:35 ⇒ 路由码在位、进程早于该码（本仓服务不热重载）；`git status --porcelain` 计数 21 枚 `M` ＋
  7 枚 `??`，本壳 W1~W8 的改动尚未提交。**故 N2 不是形状缺陷，是生效问题**——C1~C6 的核对结果＝形状相符、通路未到，
  根侧无需返工请求面。
- **槽位读数**：`engines/factory.py:25-26`（`TEXT_ENGINE_NAMES` 与 `IMPLEMENTED_TEXT_ENGINES` 皆空元组）、
  `:76`「当前一条都构造不出来」、`config.py:94` `kp_engine: str = ""`、本仓 `.env` 无 `AXIOM_KP_ENGINE` 命中。
  **故 B4 的核对结果＝未闭合**，据此新立 N1；`KA-22` 的「未接入判 503」本身按条文正确执行，本仓不因此改判据。
- **创建面读数**：`api.md` ④ 与实码均无节点创建端点（`PATCH /api/v1/kp/nodes/{node_id}` 只改既有行）⇒ 新立 N3。
- **对根侧的影响（只登记，不替本仓判定）**：根侧梳理屏的阶段一写面选择因此重新提交其需求方裁决——其 REQ-138 壳
  的 Q3 原裁「阶段不等对方、先做前端界面」的前提是「对方丙面端点未在册」，现该前提已变（端点在册、只欠生效与适配器）。
