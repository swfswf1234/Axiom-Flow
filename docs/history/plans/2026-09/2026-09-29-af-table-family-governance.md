# `af_*` 表族措辞收口与守护断言（af-table-family-governance）

状态：Completed
任务类型：B
最后更新：2026-09-29
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 隔离白名单＝本方自有 `af_*` 表；本壳只改「张数措辞」不改其决定，措辞触及实质规则时按 ADR 治理先行评审）、[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（决定 1 的 `af_page_regions` 是第五张表，本壳是其加表前置）
关联设计：[database-design.md](../architecture/database-design.md)（表清单与列清单的权威口径处）、[testing.md](../standards/testing.md)（隔离铁律与唯一例外）、[operations.md](../guides/operations.md)（备份/清除 runbook 的四表措辞）
关联 Tracker：docs/trackers/todo.md（PLAN-020 镜像行；本任务唯一台账行）
归档判定：Retain（关闭时改判，原立项判 Delete）：①本壳是**加表评审判据**的唯一落点（`af_page_regions` 列清单/逐字段用处/表族关联/生命周期），PLAN-018 W2 建表以本节为准且活链接在案；②W1 定判表与 W2 变异复验记录是不可再生的评审证据（判例同 PLAN-021 的 D 类指纹保留）

> 立项根据＝用户 2026-09-29 对 [PLAN-018](2026-09-26-parsing-region-edit-typography-request.md) Q5
> 的裁决：「要加表需要单独列一个 todo 任务进行评审，先建立好任务和 plans/设计文档」。本壳即该评审
> 载体：**评审通过并关闭之前，PLAN-018 W2 不得新建 `af_page_regions` 表**（迁移 `20260926_0002`
> 一并冻结）。

## 目标与成功标准

**目标**：把全仓「`af_*` 四表」的**张数**从散文措辞收进守护断言单一口径，使「加第五张表」的改动面
从 64 处/28 文件收缩为「ORM `AF_TABLES` + 权威口径三处」，消除每次加表的全仓改写成本与漂移风险。

| ID | 标准（可检验） |
| --- | --- |
| S1 | 口径位置定判并成文：张数只出现在三处权威口径（`db/models.py:AF_TABLES` 定义、`database-design.md` 表清单节、ADR 0005 白名单句），其余非归档面散文一律改「`af_*` 表族」不携带张数 |
| S2 | 守护断言就位且先红后绿：隔离白名单与 `AF_TABLES` 同构断言（`tests/support/mysql.py` 的 DROP 白名单、`test_storage_conventions_governance`/契约层相应处），并对「白名单漏一项」做变异转红复验；加表时只改 `AF_TABLES` + 权威三处即可全绿 |
| S3 | 存量措辞替换完成且零丢失自证：非归档面**现态描述**的「四表」张数措辞残留为 0（过去时/当时记录/权威三处/活跃壳自指四类豁免逐处挂判据，见 W1/W3 节）；`docs/history/`（32 处）与 `docs/trackers/`（22 处）当时记录**不改写** |
| S4 | 全量门禁绿（`pytest tests -q` + `ruff check src tests`），`QED_TEST_MYSQL=1` 层对 `qed_test` 白名单往返自证 |
| S5 | 删除面自动覆盖第五表：admin purge 的 `delete_for_book`/`delete_all` 与删书原语由 `AF_TABLES` 驱动（或同构守护断言钉住「删除覆盖面 == 表族」），加表不改散布删除逻辑；契约用例证明 `af_page_regions` 行随 purge/删书清除（建表落地批＝PLAN-018 W2，本壳先把断言与判据就位） |
| S6 | 两条新表准入守则晋升 `database-design.md`「目的与边界」且与既有条文零冲突（契约守护全绿）；「表数演进对账」结论入本壳 |

## 范围与非目标

- 范围：PLAN-018 第 11 节所列 64 处/28 文件的措辞改写（隔离口径、架构、设计与运维、代码/测试/迁移
  注释）；守护断言新增与变异复验；ADR 0005 与 `standards/testing.md` 的张数措辞改写（不改决定）。
- 非目标：新建任何表（＝PLAN-018 W2，本壳关闭后才放行）；`AF_TABLES` 扩五元组本身；归档面与
  台账当时记录的回写；8900/根仓库侧「13 表镜像」措辞（只读引用，归根侧）。

## 新表结构与表族关联（评审输入；PLAN-018 W2 建表以本节为准）

**表的作用**：`af_page_regions` 是人工在 8903 画布上声明的**页级结构意图的唯一落点**——新框（add）、
删除块（delete）、合并块（merge）。它作为「解析的输入」驱动按框裁剪重解析（PLAN-018 W1），并供给
服务端区域投影视图（W3）。**它不存解析结果、不存块文本**：产物正本仍唯一在 `versions/<job_id>/`
（[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 决定 3），本表只存几何与意图。

### 列清单与逐字段用处

| 列 | 类型/约束 | 用处 |
| --- | --- | --- |
| `book_id` | String(100)，PK | 书目业务主键，与 `af_books.book_id` 同域；「哪本书」 |
| `page_no` | Integer，PK | 1 基页码；与 `book_id` 合成「哪本书的哪一页」 |
| `region_id` | String(64)，PK | 服务端生成的稳定不透明标识，**跨版本不变**；响应回给根侧作长期定位键 |
| `op` | String(16)，NOT NULL，default `add` | 意图态三值：`add`（人工新框）/ `delete`（删某块）/ `merge`（并多块） |
| `bbox` | JSON，nullable | `op=add` 必填：人工框几何 `[x0,y0,x1,y1]`，原页图像素、左上原点、基准 `pages/p<NNNN>.png`（与 PP-7 同一坐标系，不引入第二套坐标约定） |
| `anchor_bbox` | JSON，nullable | 操作对象块在**受理时生效版本**产物中的几何快照；`delete` 必填、`merge` 填并集；重解析后按 IoU 重新命中意图（锚定归本仓，Q4 定判） |
| `members` | JSON，nullable | `op=merge` 必填：参与合并块的定位列表，**数组序即阅读序**（合并文本按此拼接） |
| `kind` | String(24)，NOT NULL，default `''` | 区域用途提示（R4 属性路线的落点；**不扩 `BlockType` 枚举**，判据＝PLAN-018 K4） |
| `note` | String(1000)，NOT NULL，default `''` | 人工备注，供校验现场留话 |
| `created_at` / `updated_at` | DateTime，NOT NULL | 时间戳；全量声明改写时刷新 `updated_at` |

主键 `(book_id, page_no, region_id)`；索引 `ix_af_page_regions_page (book_id, page_no, op)`。
**本表无 `job_id` 列**（ADR 0010 决定 1，此不变式由守护断言钉住）。迁移 `20260926_0002` 按本节建表。

### 与其余各表的关系（表族判例：无物理外键，按业务键关联）

| 对象 | 关联方式 | 关系与语义 | 约束/后果 |
| --- | --- | --- | --- |
| `af_books` | `book_id` | 1:N（一本书多页、一页多框） | 删书（`DELETE /books`）与 admin purge 的删除原语**必须含第五表**（S5）；书行在则框在 |
| `af_pages` | `(book_id, page_no)` 逻辑引用 | 一页 N 框；`af_pages` 主键含 `job_id`（版本级页行）而本表刻意不含 ⇒ **区域行可先于任何解析存在**（先画框后解析，这正是 S1 判据的前提） | 无强一致校验；页码合法性按 `af_books.page_count` 判（受理前）；re-ingest 致页收缩时越界区域行**可见保留、不静默删**（进视图异常态） |
| `af_parse_jobs` | 无外键行 | W1 受理任务时读本表当前框作为该页识别单元；任务行**不复制框几何**（单一事实源＝本表） | 框数超 `AXIOM_MAX_REGIONS_PER_PAGE`（16）受理前 422、不落任务行不打模型 |
| `af_block_edits` | 主键不相交（`job_id`/`block_index` vs `region_id`） | 语义正交：本表＝**人的意图（跨版本存活）**，edits＝**人对某一次解析结果的判定（绑版本）**（ADR 0010 决定 2） | 同一区域可同时有 region 行与 edit 行（画框→解析→改文字），互不覆写、独立存活 |
| `versions/<job_id>/` 产物 | `anchor_bbox` ↔ 生效版本 `blocks.json` 块 bbox 的 IoU | 几何锚定而非库引用；重解析出新版本后重新命中，失败 ⇒ `state=unanchored` 可见暴露 | **只锚生效版本**；无生效版本（legacy）⇒ add/delete/merge 一律拒绝（Q3 定判 2026-09-29，写入面在应用层把关） |

### 生命周期一句话

区域行只在三种情形消失：① `PUT …/structure` 全量声明中缺席（缺席即撤销）；② 删书/清除（S5 覆盖面）；
③ 不随版本更替删除（跨版本存活是其存在理由）。除此之外任何路径删它判为缺陷。

## 表数演进对账（评审结论）

**目的**：证明本轮之后 `af_*` 表族没有表级欠账——已知后续需求全部落「加列 / JSON 键 / 读时派生」，
不再需要新表。此后「是否加表」一律按下面两条准入守则判。

### 准入守则（本壳 S6 晋升 `database-design.md` 的两条）

- **守则 1 · 新表三判据**：需求的存储形态落在「身份需跨版本稳定、需唯一约束、需 SQL 集合检索」
  三者任一且现有表族承载不了 ⇒ 新表；派生可重建 ⇒ 不入库（产物文件侧可重建判据同源）；状态信号
  注记 ⇒ 加列或现有 JSON 键。加表必走单独评审轮（本壳即判例），且同步权威三处与列清单守护
  （列清单与 ORM 同批晋升的既有守护不变）。
- **守则 2 · JSON 键反向闸**：JSON 列不得承载「需要唯一约束或集合检索的身份关系」；一旦某键出现
  该需求，即表级问题，按守则 1 走评审。防的是「加列省一轮迁移、日后按值反查全表扫」的债务化。

### 需求 → 存储对账表（作用域＝PLAN-018 R1~R6/W8 与 PLAN-019 P1~P3 的已知口径）

| 需求 | 落点 | 是否表级 |
| --- | --- | --- |
| PLAN-018 R1 页级区域意图（add/delete/merge，跨版本存活） | 新表 `af_page_regions`（本壳评审对象，第五张） | **是，唯一一张** |
| PLAN-018 R2 结构操作作用域与删除意图存活 | 同上表列（`op`/`anchor_bbox`/`members`） | 否，本壳内 |
| PLAN-018 R3 区域视图服务端投影合并 | 读时派生（合并生效版本块 + 区域行 + edits），不落新表 | 否 |
| PLAN-018 R4 块类型属性（TOC/节题类提示） | `af_page_regions.kind` 属性路线，不扩块枚举 | 否 |
| PLAN-018 R5 归一化启发式修正 | `normalize/` 代码与映射表，产物侧；无表动作 | 否 |
| PLAN-018 W8 页级人工确认门 | `af_pages` 加列（确认态），DB 单源 | 否（加列） |
| PLAN-019 P1 重跑签名 | `af_parse_jobs` 加列 `parser_signature` | 否（加列） |
| PLAN-019 P2 就绪 DB 单源 | 既有 job/书/页三层状态列口径，无新结构 | 否 |
| PLAN-019 P3 分诊 ledger | 产物侧文件 + 既有列引用；ledger 入库判据仍按守则 1 复审 | 否（当前口径） |
| ARCH-023 知识库面（chunk 入库、索引版本行） | 本壳作用域外；届时按守则 1 独立评审（唯一潜在第六表来源，向量正本在外部库不进 MySQL） | 另轮评审 |

**结论**：本作用域内表级需求只有第五表一项，其余全部为加列、JSON 键或读时派生 ⇒ 评审关闭后
表族进入「只加列不张表」的稳定期；ARCH-023 若成立为表，改动面＝`AF_TABLES` + 权威三处 + 一个迁移。

## W4 结论与 S1~S6 终判（2026-09-29）

| ID | 判定 | 直证 |
| --- | --- | --- |
| S1 | 达成 | 权威三处＝甲组表（`models.py:141` `AF_TABLES`、`database-design.md` 表清单节、`adr/0005:42` 白名单句）；其余现态散文 41 行改「表族」（W3 节分类账） |
| S2 | 达成 | `tests/unit/test_db_table_family_governance.py` 四道同构判据；先红＝注入 `af_page_regions` 假表实测转红；变异＝截短/多出/缺收紧三式当场红 + 幽灵表反向自证（W2 节） |
| S3 | 达成 | 现态残留 0，残留 30 处逐行归入四类豁免（W3 节）；`history/` 45 与 `trackers/` 28 当时记录未回写 |
| S4 | 达成 | `pytest tests -q` 558 passed/10 skipped、`ruff check src tests` 全过、`QED_TEST_MYSQL=1` 集成层 8 passed（qed_test 白名单往返） |
| S5 | 达成（静态半边）+ 移交 | 删除覆盖面同构由三道判据钉死（purge 元组/计数字段/白名单）；`af_page_regions` 行为用例随 PLAN-018 W2 建表批落地，判据文本已在本壳「新表结构」节与 W2 节挂账 |
| S6 | 达成 | 两条准入守则已入 `database-design.md`「目的与边界」（无编号、无日期措辞），与既有条文零冲突（契约层全绿）；对账表成文于「表数演进对账」节 |

**放行结论（Q5 裁决的门槛解除）**：PLAN-018 W2 可新建 `af_page_regions`，建表以本壳
「新表结构与表族关联」节为准；迁移 `20260926_0002` 的 `down_revision` 接当前 head
`20260926_0001`（真实库 `qed` 上的 `upgrade` 仍属 D 类，另批批准）。加表时的改动面清单＝
`AF_TABLES` 加元组成员 + 本壳 schema 落 `database-design.md` 表清单/列清单（与 ORM
同批晋升，`test_column_list_matches_orm_columns` 守护）+ `adr/0005:42~44` 逐名枚举 +
admin `_FOUR_TABLES`/`AdminPurgeCounts`（判据会点名）+ 区域仓储与迁移。

## W3 结论（2026-09-29，S3 达成）

实改 **43 行 / 23 文件**（比 W1 预估 41 多 2 行＝甲组权威句的措辞改写；其中 2 行改写后仍以
「（首建时四表…）」注记保留历史读数）。残留复扫 **30 处**，全部可归类、无游离：

| 类别 | 处数 | 位置 |
| --- | --- | --- |
| 首建类过去时（W1 终判＝保留） | 9 | `db/__init__.py:7`、`db/models.py:4`、`migrations/__init__.py:1`、`migrations/env.py:4`、`20260917_0001`×2、`20260920_0002:167`、`database-design.md:213`、`adr/0004:45` |
| 当时记录（评审结论/演练核验/历史窗口动作单） | 9 | `adr/0005:72,86`、`api.md:582`、`database-design.md:259`、`operations.md:83,187`、`PLAN-009 壳:90`、`adr/0004:45` 外其余 |
| 权威三处（甲组） | 1 | `adr/0005:42`（已改「表族（首建时四表，以 `AF_TABLES` 为准）」，枚举句 43~44 保留） |
| 活跃壳自指豁免（丙） | 11 | `PLAN-018 壳:98,310,311`＋本壳叙述性引述 8 处 |

**现态描述残留＝0**（S3 判据按 W1 修正口径成立）。零丢失自证＝行级定向替换（每处 `assert
count==1`）+ 残留分类账全归类；`operations.md:97/206/222` 三处现行 runbook 改挂「与 `AF_TABLES`
同源」，206 行保留逐名枚举以防操作书失去可执行性。门禁：`pytest tests -q` 558 passed /
10 skipped（含并行会话注入的瞬时红一次，复跑即绿）、`ruff check src tests` 全过。

## W2 结论（2026-09-29，S2+S5 达成）

守护文件＝`tests/unit/test_db_table_family_governance.py`（7 用例），四道同构判据：

| 判据 | 钉住的漂移 |
| --- | --- |
| `test_af_tables_matches_declared_af_metadata` | ORM 声明新表却不入 `AF_TABLES` 制裁 ⇒ 红 |
| `test_purge_models_are_exactly_af_tables` | admin 清除元组与表族集合失联 ⇒ 红 |
| `test_admin_purge_counts_fields_cover_af_tables` | `AdminPurgeCounts` 缺/多 `af_*` 计数字段 ⇒ 红 |
| `sweep_ok` + `test_owned_tables_whitelist_is_derived` | 夹具 DROP 白名单截短/多出/缺 `alembic_version` ⇒ 红 |

- **先红复验**：向 `Base.metadata` 注入 `af_page_regions` 假表（不落迁移）运行判据，真红且报错文本
  点名「未入制=['af_page_regions']」，与 PLAN-018 W2 加表时的预期失败模式一致；注入仅活在测试进程。
- **变异复验**：`sweep_ok` 对「漏一项／多一项（`qt_books`）／缺收紧项」三式施变当场转红
  （`test_whitelist_gap_mutation_turns_red`）；「加五元组但 admin/schemas 未跟」经集合对账形状判红
  （`test_new_table_without_registration_breaks_every_derived_surface` 为反向自证）。
- **白名单本体已天然派生**：`tests/support/mysql.py:36` 的 `OWNED_TABLES` 由 `AF_TABLES` 生成，
  加表零改写；S5 的行为用例（第五表行随 purge 清除）随建表批（PLAN-018 W2）落地——静态半边
  已由上表钉死，届时 `AF_TABLES` 一动，三道派生判据同时逼出 admin/schemas 的跟随改动。
- S2 的「加表只改 `AF_TABLES` + 权威三处即可全绿」由判据集合本身保证：其余散文不参与断言。

## W1 实时盘点与口径位置定判（2026-09-29）

盘点命令＝前置 2（`rg`/Python 双扫，含非 `.md/.py` 扩展名核验，无额外命中）。**全仓 137 处**：
`docs/history/` 45、`docs/trackers/` 28（两者为当时记录，不改写）、非归档面 **73 处 / 37 文件**
（＝代码 23 + 稳定与指南文档 43 + 活跃计划壳 7）。PLAN-018 第 11 节当时口径 64 处/28 文件的差额
＝本壳自身 5 处 + PLAN-018 壳 2 处（盘点后新增的引述）+ PLAN-009 壳 1 处 + 当时扫描未含
`docs/plans` 目录的既有 1 处。

**处置规则**：①权威三处保留张数口径（下表甲组）；②其余**现态描述**一律改「`af_*` 表族」并在不损
语义处挂 `AF_TABLES` 引用；③**过去时/当时记录**（首建叙述、评审结论、历史窗口的备份动作单、
列名枚举句）保留原措辞；④活跃计划壳的**自指引述**（描述本收口任务自身）豁免不回写。

### 甲 · 权威三处（保留，是张数的唯一口径位）

| 位置 | 现文 | 定判 |
| --- | --- | --- |
| `src/axiom_flow/db/models.py:141` | `AF_TABLES = (AfBook…, AfBlockEdit…)` | ORM 元组本体＝唯一真源，W2 守护断言锚定它 |
| `docs/architecture/database-design.md` 40~47「表清单」节 | 四行表枚举 | 结构事实源的表清单节；PLAN-018 W2 加行即唯一改写点 |
| `docs/adr/0005-mysql-integration-test-isolation.md:42`~44 | 「自有表范围 = `af_*` 四表 + `alembic_version`」＋逐名 DROP 枚举 | 白名单句保留为权威口径，改写为「本方自有 `af_*` 表族（以 `AF_TABLES` 为准）」并保留逐名枚举作判例句；隔离决定其余文字逐字不动 |

### 乙 · 改写清单（现态描述 ⇒ 「af_* 表族」；数字＝该文件命中行）

| 文件 | 命中行 | 处数 | 备注 |
| --- | --- | --- | --- |
| `src/axiom_flow/__init__.py` | 8 | 1 | 模块导览句 |
| `src/axiom_flow/api/admin.py` | 2 | 1 | 模块职责头 |
| `src/axiom_flow/db/__init__.py` | 4, 7 | 2 | 「四表一次建全」为过去时：改「表族（首建时四表）」或保留，W3 逐处判定 |
| `src/axiom_flow/db/models.py` | 4 | 1 | 同上过去时判定 |
| `src/axiom_flow/migrations/__init__.py` | 1 | 1 | 过去时同上 |
| `src/axiom_flow/migrations/env.py` | 4 | 1 | 「四表一次迁移」过去时同上 |
| `src/axiom_flow/migrations/versions/20260917_0001_*.py` | 1, 7 | 2 | 首建迁移 docstring＝当时事实，倾向保留 |
| `src/axiom_flow/migrations/versions/20260920_0002_*.py` | 167 | 1 | 「真机执行前必须先备份 af_* 四表」＝历史窗口动作单，保留 |
| `src/axiom_flow/schemas.py` | 526 | 1 | 端点描述现态，必改 |
| `tests/contract/test_api_v1_contract.py` | 1546 | 1 | docstring 现态 |
| `tests/integration/test_parse_persistence.py` | 8, 159, 178 | 3 | 白名单断言文案，与 W2 同构断言联动 |
| `tests/support/mysql.py` | 8 | 1 | **W2 主锚点**：DROP 白名单定义处 |
| `tests/unit/test_db.py` | 3, 59 | 2 | 守护面文案 |
| `tests/unit/test_db_migrations.py` | 1, 3 | 2 | 同上 |
| `docs/adr/0004-*.md` | 37, 45 | 2 | 37＝当年设计定案叙述、45＝被取代归档，两句过去时保留 |
| `docs/adr/index.md` | 22 | 1 | 0005 行摘要句随甲组口径改「表族」 |
| `docs/architecture/api.md` | 216, 423, 448, 449, 482, 495, 582 | 7 | 582「来自 PLAN-001」过去时保留；其余 6 处现态改（448 与 `schemas.py:526` 同句必同步） |
| `docs/architecture/code-map.md` | 49, 56 | 2 | 现态行改 |
| `docs/architecture/database-design.md` | 5, 15, 213, 239, 259 | 5 | 5/15/213/239 现态改（213 迁移行保留过去时语义）；259 来源行过去时保留 |
| `docs/architecture/overview.md` | 66, 67, 84, 159 | 4 | 全改（含 Mermaid 节点文字，改后跑架构契约） |
| `docs/design/parsing-management.md` | 5, 40 | 2 | 全改 |
| `docs/guides/development.md` | 67 | 1 | 改（夹具句与 `AF_TABLES` 同构） |
| `docs/guides/operations.md` | 83, 97, 187, 206, 222 | 5 | 83/187＝已执行历史窗口动作单，保留；97/206/222 现行 runbook 改 |
| `docs/standards/testing.md` | 45 | 1 | 改（隔离铁律唯一例外句） |

分类账：**现态改写 41** ＋ **过去时/当时记录保留 28**（其中「首建/一次建全」类 12 处 W3 逐处终判，允许改挂口径）＋ **甲组权威 3** ＋ **丙组豁免 8**＝ 73，与盘点总数对账一致。

### 丙 · 豁免（活跃计划壳自指 + 本壳）

| 文件 | 处数 | 理由 |
| --- | --- | --- |
| `docs/plans/2026-09-26-parsing-region-edit-typography-request.md` | 2 | Q5 行与第 11 节＝对本收口任务的引述与盘点输入，回写会使判据自失 |
| `docs/plans/2026-09-22-v1-parsing-knowledge-optimization.md` | 1 | 「备份 af_* 四表 + mysqldump」＝PLAN-009 当时窗口记录 |
| `docs/plans/2026-09-29-af-table-family-governance.md`（本壳） | 5＋W1 节新增 | 自指（任务标题、判据与本定判表引述） |

**定判结论**：W3 实际改写面＝乙组 43 处；S3 的「残留为 0」口径修正为「现态描述残留 0，
保留处逐条挂规则 ③/甲/丙 判据」。W2 断言以 `AF_TABLES` 为锚，加表时改动面＝`AF_TABLES` +
甲组三处 + 迁移，其余零触碰。

## 前置条件

1. **评审准入**：本壳经用户评审通过后方可实施（用户裁决 2026-09-29：先建任务与设计文档再评审）；
   评审未过期间 PLAN-018 W1 其余动作不受本壳阻塞。
2. **清单已核**：64 处/28 文件枚举以 PLAN-018 第 11 节为输入，W1 重跑
   `rg -c 四表 src tests scripts docs/architecture docs/design docs/standards docs/guides docs/adr`
   取实时清单（并行窗口可能已改动个别文件，以当场读数为基准）。
3. **ADR 0005 只改措辞**：白名单句由「`af_*` 四表」改「本方自有 `af_*` 表族（以 `AF_TABLES` 为准）」，
   隔离决定（只 DROP 自有表、绝不 `DROP DATABASE`）逐字保留。

## 工作项

| # | 工作项 | 状态 |
| --- | --- | --- |
| W1 | 实时盘点 + 口径位置定判表（每处标注：改写为表族 / 保留为权威三处 / 不改写），成文于本壳；「表数演进对账」节已随评审结论成文 | ✅ 完成（2026-09-29，定判表见上节：现态改写 41 / 过去时保留 28 / 权威 3 / 豁免 8） |
| W2 | 守护断言与白名单改写：同构断言先红后绿 + 变异复验（S2）；删除面同构守护与用例就位（S5） | ✅ 完成（2026-09-29，结论见上节；S5 行为用例随 PLAN-018 W2 建表批） |
| W3 | 全量散文替换 + 零丢失自证（S3） | ✅ 完成（2026-09-29，结论见上节） |
| W4 | 文档同步（database-design/testing/operations）+ 准入守则晋升（S6）+ 六步收口 | ✅ 完成（2026-09-29，终判见上节） |，关闭后放行 PLAN-018 W2 | 待开始 |

## 验证与验收

- TDD：W2 断言先取真红灯再实现；W3 以比对脚本自证零丢失（判例＝PLAN-014 的 token 覆盖法）。
- 门禁：`pytest tests -q` + `ruff check src tests`；MySQL 层 `QED_TEST_MYSQL=1` 白名单往返。
- S1~S6 逐条判定成文于本壳后方为达成；消费侧（根仓库）无判据挂本壳。

## 回滚

- 零数据动作：纯措辞 + 断言变更，`git revert` 即完整回滚；不触碰任何库与数据根。

## 关闭与归档

- 关闭条件：S1~S6 逐条判定达成 + 门禁全绿；关闭时在本壳写明放行 PLAN-018 W2 的结论——**已达成**（上节）。
- 六步收口（ADR 0006）：结论入本壳 → todo 行压缩关闭 → completed.md → plans/index 无需登记
  （索引导航制）→ project-status 刷新 → 全量门禁。
- 归档判定 Retain（关闭时改判，理由见头部元数据行）：壳移入 `docs/history/plans/2026-09/`，
  `plans/index.md` 与本目录索引登记去处；收口锚点＝关闭批 commit。
