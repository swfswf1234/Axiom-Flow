# 数据库设计（Axiom-Flow af_* 命名空间）

设计状态：Accepted（四表形状的方向与列清单已经确认，列由守护与 ORM 逐列对账）
确认状态：已确认（解析模块 `af_books`／`af_operations`／`af_pages`）／暂定（分析模块 `af_kp` 的消费通路，判据见「af_kp 列清单」节末）
实现状态：Implemented（四表代码与迁移 base 已落；两库真库整族重建已执行完毕）
最后更新：2026-10-22
关联代码：`src/axiom_flow/db/`（models/repositories）、`src/axiom_flow/migrations/`（Alembic 迁移）
关联测试：`tests/unit/test_db.py`、`tests/unit/test_db_table_family_governance.py`、`tests/unit/test_db_migrations.py`、`tests/integration/test_parse_persistence.py`、`tests/integration/test_concurrency_criteria.py`、`tests/contract/test_api_v1_contract.py`、`tests/contract/test_architecture_documents.py`、`tests/contract/test_fixed_document_form.py`
关联 ADR：[ADR 0001](../adr/0001-v2-exploration-direction.md)、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)、
[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)、
[ADR 0007](../adr/0007-parse-status-semantics.md)、
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（页级区域是解析输入）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪与激活以 DB 为唯一事实源，故 `artifacts`
不作判据）、[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)（本篇四表形状与档 B 整族重建）、
[ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md)（本篇改用根侧八章编号体）；根仓库
`QED-Engine/docs/history/adr/v0.1/0003-shared-qed-database-independence.md`（共享 qed 库独立性例外）、
`QED-Engine/docs/adr/0025-database-design-master-doc-and-ddl-ownership.md`（总纲正本与前缀即所有权）

## 一、前言

本篇回答唯一问题：**af_\* 表族各自存什么、列的语义是什么、结构如何演进**。

- **本篇不写的东西有唯一去处**：端点参数与响应结构在 [api.md](api.md)，状态流转与职责分段的判据在
  [design/parsing-management.md](../design/parsing-management.md)（`PM-n`），模块映射在
  [code-map.md](code-map.md)。
- **全库通则与共享表列级的正本是根仓库总纲** `QED-Engine/docs/architecture/database-design.md`
  （下称总纲，三项目共用库的总设计正本）：本篇只登记**三类**（共享 `qed_*`、需从 QED-Tracker 同步的
  形状、`af_*` 专用表），通则按指针引用、不复制；总纲则只登记 `af_*` 的总览行与路由指针，其 6.3
  路由行把 `af_*` 的形状事实源指向本篇。**跨仓引用一律写纯路径，不写 URL**（不猜 URL、断链可 `rg` 复算）。
- **本篇的章体与总纲同形**（八章编号体：前言／规则／表总览／整体架构／详细设计／同步契约／变更运维／
  守护），使同一库的两篇设计文档可按章对读；章内小节标题仍承载对账锚点（`### <table> 列清单`）。
  体裁改判由 [ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md) 立法。
- **`af_kp` 仍为暂定的原因**：表与列都已落在代码与迁移 base 里，未成形的只有分析模块的**消费通路**——
  ① `af_kp.links` 越过 JSON 列反向闸的例外与其转表判据待终审；② 梳理作业、复核端点与提升作业尚未立项，
  故 `af_kp` 有形状而没有写者。两处了结前，`af_kp` 可被引用为设计依据，**不得**当作「已有能力」向根侧承诺。
- 库内换形状属 D 类人工窗口（逐表备份留指纹 → 用户批准 → 执行），本篇不安排执行。

### 三类登记面

本仓库在共享库里只拥有 `af_*` 一个命名空间，因此本篇按前缀所有权（总纲 R1）登记三类表，
**除 `af_*` 外一律不写列级内容**：

| 登记面 | 本篇写什么 | 本篇不写什么 | 该面的形状正本 |
| --- | --- | --- | --- |
| **A 共享 `qed_*`（四张）** | 表名、一句话功能、**本仓库的读写权**、变更通路 | 列名、类型、索引名（复制一份即造第二事实源） | 总纲三章总览行 + 五章逐列与 DDL + 6.2 同步矩阵 |
| **B 需从 QED-Tracker 同步的形状** | 本仓库读哪几列、经什么通道、形状漂移由谁负责 | 对方侧的列定义与索引名；不代对方建表或迁移 | 受托建表两表＝`QED-Tracker/src/qed_tracker/db/models.py`；`qt_*`＝`QED-Tracker/docs/architecture/database-private-tables.md` |
| **C `af_*` 项目专用** | **列级唯一正本**：逐列语义、键与索引、迁移序列、演进判据 | 端点响应形状（→ [api.md](api.md)）、状态流转与版本作用域判据（→ `PM-n`）、迁移的运维执行步骤（→ [operations.md](../guides/operations.md)） | 本篇 |

登记面 A 与 B 的逐表内容在「六、与 QED-Engine 的交互与同步契约」，登记面 C 的列级正本在
「五、详细设计」。

## 二、遵循的规则

### 全库通则（按指针引用，不在本篇复述）

- **按指针引用、不在本篇复述的全库通则**（复述即漂移）：R1 前缀即所有权、R2 命名空间互不直连
  （直连只限自己的命名空间与共享 `qed_*`，其余一律 HTTP 透传）、R3 共享表变更先经总纲登记、
  R4 逻辑引用不建跨命名空间外键、R5 只引用不复制（`ref_id` + `label` 快照）、R6 人工位机器不写、
  R7 `meta` 扩展槽三红线、R9 存储与字符集、R11 敏感字段、1.0 前 schema 重建档位（档 A／档 B）、
  四步迁移窗口、账本与作业表保留期口径。本篇的 `meta` 列全部直接引用 R7，不另立一套说法。
- 表命名空间隔离：QED-Tracker 用 `qt_*`，本仓库用 `af_*`，**互不读取对方表**；MySQL 8，库名 `qed`，
  三项目共用同一实例与库（根仓库独立性铁律的唯一明确例外）。

### 本仓自定守则

- **入库边界**：文件系统承载产物正本（`parsed/` 页图 / Markdown / blocks / 分块），DB 只存元数据与
  状态；`chunks.json` 是派生文件，**不入库**（可重建）。
- **新表准入（表级问题的判据）**：需求的存储形态落在「身份需跨版本稳定、需唯一约束、需 SQL
  集合检索」三者任一且现有表族承载不了，才新增表，且必须走独立评审轮并同步表清单、列清单与
  `AF_TABLES`；状态信号注记一律以加列或现有 JSON 键承载，不张表。
- **JSON 列反向闸**：JSON 列不得承载需要唯一约束或集合检索的身份关系——某键一旦有此需求即为
  表级问题，按上一条判据走评审，不以塞键回避。**本篇有一处明文越闸例外**（`af_kp.links`）及其
  转表触发判据，见「准入守则的答卷与例外登记」。
- 凭据与库名的唯一事实源是根 `.env` 的 `QED_DB_*` 变量；密码值绝不下发。
- **本篇不写**：列的取值域如何被端点暴露（→ [api.md](api.md)）、页与编辑的版本作用域判据
  （→ `PM-3`/`PM-6`/`PM-11`/`PM-16`）、迁移的运维执行步骤（→ [operations.md](../guides/operations.md)）。

## 三、本项目表总览

**本组件分两个模块**，表族按模块分组：**解析模块**（PDF → 页图 / Markdown / 结构化块 / 分块）与
**分析模块**（在解析产物之上梳理章／节／知识点结构与叙述）。四表形状，一次评审建齐：
一张事件明细表承载全部操作流水与人工意图，结果索引表只有 `af_pages` 一张，知识点表只有 `af_kp` 一张。

| 模块 | 表 | 一行 = | 作用域键 | 谁写 | 列清单 |
| --- | --- | --- | --- | --- | --- |
| 解析 | `af_books` | 一册已验证书（qt_books 只读快照 + ingest/解析进度与结局 + 生效解析版本指针 + 扩展槽） | `book_id` | 同步、ingest 与解析收尾 | [↓](#af_books-列清单) |
| 解析 | `af_operations` | 一次操作＝一行：解析版本（事件）／块编辑／结构声明／页确认／ingest／同步／清除／检索索引版本／梳理批次流水 | `op_id`（意图行另由 `intent_key` 唯一定位） | 任务生命周期、人工校验与结构声明；余值建而不写 | [↓](#af_operations-列清单) |
| 解析 | `af_pages` | 一页**在某个解析版本内**的状态、质量、在位产物索引、确认态与页域 | (`book_id`, `job_id`, `page_no`) | 逐页持久化（产物写先于页行） | [↓](#af_pages-列清单) |
| 分析 | `af_kp` | 一个节点（章／节／知识点）的当前结论 | `node_id`（梳理作用域＝`book_id`，不含版本） | **本轮无写者**：形状先行，仓储与清除面已就位 | [↓](#af_kp-列清单) |

**四表各有一个 `meta` JSON 扩展槽**，这就是「留扩展空间」的落点；三条红线按通则指针引用，键定档后升
实体列并清空对应键。**明确不建**：块正文表、知识点关系边表、梳理批次表、私有 LLM 调用账表、检索索引
版本表（第五表）、`af_pages` 的页级格式列。每件的「不建」判据与**触发转建判据**见「承载力审计」与
「准入守则的答卷与例外登记」。

### 前身对照（退役表名的去处）

四表形状的前身是五表。逐列去向的正本在承载计划（见「八、守护与验证」的来源条）；这里只留**换表后仍然有效**的
名字与键的对应关系，供读到旧表名的文档与根侧对账时换算：

| 退役表 | 现在的形状 |
| --- | --- |
| `af_parse_jobs` | `af_operations` 的 `op_kind='parse'`、`lifecycle='event'` 行：列全部平移，`job_id` 强制等于 `op_id`；`started_at`／`finished_at` 两列折进 `progress` 的同名键，进入终态时另算 `duration_seconds` |
| `af_block_edits` | `af_operations` 的 `op_kind='edit'`、`lifecycle='intent'` 行：`block_type`／`verdict`／`corrected_text`／`corrected_bbox` 进 `payload`，`note` 用实体列，定位与幂等改由 `intent_key` 承担（`edit_id` 与 `uq_af_block_edits_pos` 退役） |
| `af_page_regions` | `af_operations` 的 `op_kind='structure'`、`lifecycle='intent'`、`job_id=''` 行：`op`／`bbox`／`anchor_bbox`／`members`／`kind` 进 `payload`，`note` 用实体列，`region_id` **原样落在 `target_id`**（派生式一字未改，根侧长期定位键不因换表失效）；`ix_af_page_regions_page` 由 `ix_af_operations_version` 与按书索引组合取代，等价性论证与热点回退判据在承载计划 |

`af_books` 与 `af_pages` 保留：前者增扩展槽 `meta`，后者增 `artifacts`／`confirm_state`／`section_kind`
／`meta` 四列。

## 四、整体架构（本项目所需部分）

### 关系视图

```mermaid
erDiagram
    af_books ||--o{ af_operations : "book_id（事件流）"
    af_books ||--o{ af_pages : "book_id"
    af_operations ||--o{ af_pages : "job_id = op_id（版本作用域）"
    af_books ||--o{ af_kp : "book_id（梳理作用域，不含版本）"
    af_kp ||--o{ af_kp : "parent_id（自引用层级）"
    af_books {
        varchar book_id PK
        varchar active_job_id "生效版本 = af_operations.op_id；NULL=根视图 legacy"
        varchar parse_status "只表达进度"
        varchar last_parse_result "只由终态写入"
        json meta "扩展槽（四表同名）；同步 upsert 不得触碰"
    }
    af_operations {
        varchar op_id PK "parse 行即版本号"
        varchar job_id "版本坐标；structure 行恒空串"
        varchar op_kind "parse/edit/structure/confirm/ingest/sync/purge/index/kp"
        varchar lifecycle "event=流水 / intent=当前态"
        varchar intent_key UQ "意图幂等键；event 行恒 NULL"
        varchar render_profile "版本级格式档案"
        varchar embedding_signature "检索索引代际签名；本轮无写者"
        varchar status "parse 用六态，其余 ok/failed"
    }
    af_pages {
        varchar book_id PK
        varchar job_id PK
        int page_no PK
        varchar status "pending/parsed/failed"
        json artifacts "在位产物索引，不作就绪判据"
        varchar confirm_state "版本内人工确认态"
        varchar section_kind "页域角色闭集"
    }
    af_kp {
        varchar node_id PK "afkp-<hex12>，服务端随机发放"
        varchar book_id
        varchar job_id "取证版本，只作溯源、不进作用域键"
        varchar node_kind "outline/point"
        varchar parent_id "自引用；NULL=章或未归档池"
        varchar role "core/explainer/example 或空"
        json links "关系简单写"
        json anchors "回指页与块"
        varchar narrative_ref "长文在数据根下的相对路径"
        int rev "乐观并发序号"
        varchar gen_batch "梳理批次，保留最新 2 批的分组键"
    }
```

ER 图只画关系与主键/唯一键的骨架；**列的完整事实源是五章每张表的列清单表**（与 ORM 由守护逐列对账）。

### 跨族数据主线与版本坐标

「哪一版生效」只看 `af_books.active_job_id`（`NULL` = 根视图 `legacy`），它指向 `af_operations` 的
parse 行 `op_id`。页表以 `job_id` 为作用域键的一分子，因此同一页在不同版本可有各自状态、旧版本的人工
编辑不会套到新版本上（块下标 `block_index` 只在版本内有意义）。产物文件侧对应
`parsed/…/<book_id>/versions/<op_id>/`，判据见 [parsing-pipeline.md](../design/parsing-pipeline.md)。

`op_kind='structure'` 的意图行**刻意不带版本坐标**（`job_id` 恒空串，
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 决定 1 折叠后的唯一落点，由守护断言钉住）：
人工框是**页解析的输入**，不是某一版结果上的覆盖层，所以「先画框后解析」与「换版后框仍在」都必须
成立。它与 `op_kind='edit'` 的意图行语义正交——前者存人对页面结构的意图（跨版本），后者存人对
**某一次解析结果**的判定（绑版本）；同一区域可以同时有结构行与编辑行，互不覆写。表族内无物理外键，
按业务键关联（`book_id` → `af_books`，`(book_id, page_no)` 逻辑引用页）。

### 事件与意图的分工（`af_operations` 的两态）

「一张表写明所有事件」要能被守护，靠的是 `lifecycle` 一列把两种语义分开——**流水永不参与当前态判据**：

| 判据 | 内容 | 代价／反面 |
| --- | --- | --- |
| 事件行只答「发生过什么」 | `lifecycle='event'` 只可读出与计数，任何端点与状态机不得据它决定「现在是什么状态」 | 块编辑与结构声明的**逐次修改史不保留**（与前身一致，非回归）；一条流水被读成状态就要求读侧自己排序，判据会从库里漂到代码里 |
| 当前态只有四个来源 | ① `af_books`（书目进度/结局/生效指针）② `af_pages`（页级就绪与确认）③ `af_operations` 的 `lifecycle='intent'` 行（人工意图）④ `op_kind='parse'` 行的状态列（版本自身进度） | 四源之外的读数一律是派生视图，不得再落列 |
| 生效指针仍只有一列 | `af_books.active_job_id` → `af_operations.op_id`（同值同形状），`NULL` = 根视图 `legacy`；`af_operations` 不设任何布尔「生效」列 | 同一事实两处存必漂移 |
| 排序不用时钟 | 同一坐标取「最新」由唯一键保证只有一行，读侧不需要按时刻排序 | 意图行定位靠 `intent_key`，不靠 `updated_at` 精度 |
| 就绪与激活仍只读 DB | 就绪判据＝`af_pages.status` + `af_books.active_job_id` + parse 行状态；`artifacts` **只作完整性披露，不作判据** | `artifacts` 与判据同时可读会造出第二事实源（磁盘） |
| 格式是版本级属性 | `af_operations.render_profile`（parse 行）声明本版产物按哪种口径回显；`af_pages.artifacts` 声明这页实际有哪些文件 | 页级各自声明格式会让同一版内出现互相矛盾的渲染结论 |

### 流程落点（两模块十二步，每步只有一个判据位）

本组件的核心只有两件事：**文档解析**与**文档知识点分析**，两者之间的接口是「生效版本的解析产物」。
下表把全流程逐步落到存储位置。末列是本表的全部意义——同一事实只有一处可判，其余列都是读数
（与「事件与意图的分工」节的四源判据同源）；判据的**内容**在各条文，本表只给判据的**位置**。
标「建值不建写者」的步，其 `af_operations` 行本轮不产生（判据位照旧有效，因为那一列在库里已就位）。

| 步 | 动作（模块·执行者） | DB 落点 | 文件落点 | 唯一判据位 |
| --- | --- | --- | --- | --- |
| 1 | 书目同步（解析·系统） | `af_books` 按 `book_id` upsert | — | 行存在即已同步，`synced_at` 只是读数 |
| 2 | ingest（解析·系统） | `af_books` 的 `page_count`／`sha256`／`ingest_status`／`ingested_at` ＋ `af_operations` 一行 `op_kind='ingest'`（建值不建写者） | `pages/` 页图、`book.json` | `af_books.page_count`＝页码域唯一来源 |
| 3 | 受理并建版本（解析·人工触发） | `af_operations` 一行 `op_kind='parse'`、`lifecycle='event'`、`status='queued'` | — | parse 行 `status`；受理门与在飞判据共用同锁内同一纯函数 |
| 4 | 逐页解析与落盘（解析·系统） | `af_pages` 每页一行 ＋ parse 行 `progress` 节流回写 | `versions/<op_id>/pages/` 三件套、`manifest.json`、`chunks.json` | `af_pages.status`（产物写先于页行提交） |
| 5 | 版本激活（解析·系统） | `af_books.active_job_id` 单列 UPDATE | 不搬文件 | `active_job_id`（`NULL`＝根视图 `legacy`） |
| 6 | 块编辑（解析·人工） | `af_operations` 的 `op_kind='edit'`、`lifecycle='intent'`，按 `intent_key` upsert | 不改产物（编辑是覆盖层） | intent 行 |
| 7 | 页级结构声明（解析·人工） | `af_operations` 的 `op_kind='structure'`、`job_id=''`（跨版本存活），整批在书行锁内替换 | — | intent 行；缺席即撤销 |
| 8 | 页确认（解析·人工） | `af_pages.confirm_state` ＋ `af_operations` 一行 `op_kind='confirm'` 流水（本轮只建列与建值，不建写者） | — | `confirm_state`（版本内判定；流水不是判据） |
| 9 | 知识点梳理（分析·机器） | `af_kp` 批次行：`gen_batch` 分组、`gen_status` 走 `draft`→`candidate`；批次流水另落 `af_operations` 一行 `op_kind='kp'`（建值不建写者） | `<book_dir>/kp/<node_id>.md` 长文 | 在飞判据＝该书存在 `gen_status='draft'` 行 |
| 10 | 复核与确认（分析·人工） | `af_kp` 的人工域列 ＋ `confirmed=1` ＋ `rev` 自增 | 长文改写 | `confirmed`（机器路径恒写 0） |
| 11 | 提升（分析·8900 拉取） | 只读导出；本方不写共享主表、不回写 | — | 提升事实的唯一正本在共享主表 |
| 12 | 清除（运维·人工双确认） | `af_operations` 一行 `op_kind='purge'` 流水（建值不建写者） | 删 `af_*` 行与 `parsed/` 子树 | — |

第 1~8 步的条文事实源是解析管线与解析管理两篇（`PP-n`／`PM-n`），第 9~11 步是分析模块设计
[knowledge-analysis.md](../design/knowledge-analysis.md)（`KA-n`）；第 12 步见 [api.md](api.md) 的非公开管理面节。

## 五、详细设计

### af_books 列清单

| 列 | 类型 | 可空 | 键 | 语义 |
| --- | --- | --- | --- | --- |
| `book_id` | VARCHAR(100) | 否 | PK | 同源 `qt_books.book_id`，同步幂等键 |
| `domain_id` | VARCHAR(32) | 否 | — | 领域归属；空串 = 未归属 |
| `course_id` | VARCHAR(64) | 否 | i | 课程归属；空串 = 未归属 |
| `course_name` | VARCHAR(200) | 否 | — | 课程名（冗余，建树不必读 8901） |
| `knowledge_id` | VARCHAR(100) | 否 | — | 上游知识标识 |
| `title` | VARCHAR(500) | 否 | — | 书名 |
| `part` | VARCHAR(32) | 否 | — | 分册标识 |
| `display_title` | VARCHAR(500) | 否 | — | 展示用标题 |
| `authors` | JSON | 否 | — | `list[{name, role}]` |
| `file_path` | VARCHAR(512) | 否 | — | 源 PDF 的数据根相对路径（同源 `qt_books.file_path`） |
| `page_count` | INT | 是 | — | 全书页数（ingest 写入；`NULL` = 尚未 ingest，页清单枚举域唯一来源） |
| `sha256` | VARCHAR(64) | 否 | — | 源 PDF SHA-256（ingest 写入；根 `book.json` 退役后的唯一事实源） |
| `ingest_status` | VARCHAR(16) | 否 | — | `none` / `ingesting` / `ingested` / `failed` |
| `ingested_at` | DATETIME | 是 | — | ingest 完成时刻（UTC naive）；`NULL` = 从未 ingest |
| `parse_status` | VARCHAR(24) | 否 | i | **只表达进度**：`pending` / `parsing` |
| `last_parse_result` | VARCHAR(16) | 否 | — | 最近一次任务结局：`completed`/`partial`/`failed`/`interrupted`；`''` = 从未有任务 |
| `pages_done` | INT | 否 | — | 生效版本已 `parsed` 页数（描述的是指针所指版本） |
| `active_job_id` | VARCHAR(64) | 是 | — | 生效解析版本＝`af_operations.op_id`；`NULL` = 根视图 `legacy` |
| `notes` | TEXT | 是 | — | 人工备注（自由文本只落在这一列） |
| `meta` | JSON | 是 | — | **扩展槽**（四表同名）：三条红线全部按通则指针引用（不进 WHERE、不建函数索引、不参与唯一键），不放密钥与个人信息，键定档后升实体列并清空对应键 |
| `synced_at` | DATETIME | 否 | — | 最近一次同步时刻 |
| `updated_at` | DATETIME | 否 | — | 最近一次本侧写入时刻 |

索引：`ix_af_books_course(course_id)`、`ix_af_books_status(parse_status)`。引擎 InnoDB / utf8mb4。

**同步与进度语义**：同步以 `book_id` 为幂等键，重复同步只 upsert 不产生重复行；qt_books 侧删除或
变更不回写已同步行（快照语义）。`ingest_status` / `ingested_at` / `parse_status` / `pages_done` 由
本侧维护，不依赖 `qt_books` 之外的上游字段；`ingested_at` 每次 ingest 成功即刷新。进度与结局分列：
`parse_status` 只表达做到哪，结局落在
`last_parse_result` 且**只在终态写入**，因此运行中刷新列表不会看到结局被进度覆盖；重新 ingest 只
回置进度（`pending` + `pages_done` 归零）并清空 `job_id='legacy'` 的页行，不改结局列。

**生效版本指针语义**：`af_operations` 上**不设布尔生效列**（同一事实两处存必漂移），切换与清空只经
`BookRepository.set_active_job()` 单列 UPDATE。写入门槛就是 `completed` 判据本身，同一次判定同时
决定任务终态与是否激活指针，所以「`completed` 但未激活」不可能出现；`partial`/`failed`/中断一律
不改指针，「回滚到上一版」= 什么都不做。`NULL` 不是「无数据」而是「根视图」，重新 ingest 后的
作废态也落在这里。

**`meta` 的双写者代价（与列同批写死的两条判据）**：本表行由同步作业按 `book_id` upsert，而 `meta` 的
写者可能是人工——两个写者共用一列就会互相抹掉。① 同步 upsert **不得触碰 `meta`**，它只写同步域的
九个快照列（`domain_id`/`course_id`/`course_name`/`knowledge_id`/`title`/`part`/`display_title`/`authors`/
`file_path`）与时刻列，实现上是按「只覆盖同步字段、保留本侧字段」的快照语义**逐列赋值**，`meta` 天然
不在其覆盖域内；② 人工写 `meta` 经 `BookRepository.update_meta()` 走**整列覆写**（与「JSON 列不做增量改」
同一口径），不做「读 JSON→改一键→写回」。反向判据（会红的用例）＝同步作业重复跑一遍后，先前写入的
`meta` 键集合不变；把逐列赋值改成整行覆写即转红。人写自由文本仍用 `notes`（TEXT），`meta` 只放
**结构化**临时读数，两者不互替。

### af_operations 列清单

命名按族内既有风格（属性词后置、`note` 单数、时间列 `-at` 后缀）。表族内无物理外键、按业务键关联；
`op_id` 与 `job_id` 的关联是逻辑关联。

| 列 | 类型 | 可空 | 键 | 语义 |
| --- | --- | --- | --- | --- |
| `op_id` | VARCHAR(64) | 否 | PK | 事件身份，值形沿用 `uuid4().hex[:12]`。`op_kind='parse'` 行的 `op_id` **就是版本号**：产物目录 `versions/<op_id>/`、`af_books.active_job_id`、`af_pages.job_id` 都按值指它。值形不改＝只换表不换目录名，把改动面收在持久层 |
| `book_id` | VARCHAR(100) | 否 | i | 作用域（哪本书）；事件流主屏读法是「一本书做过什么」，故它是索引首列 |
| `job_id` | VARCHAR(64) | 否 `''` | i | 版本坐标。parse 行**强制等于 `op_id`**（守护断言）；`edit`/`confirm` 行＝其绑定的生效版本（编辑结论绑版本）；`kp` 行＝其取证版本（结论钉回那一版的产物）；`structure`/`ingest`/`sync`/`purge` 行＝`''`（结构意图跨版本存活） |
| `op_kind` | VARCHAR(24) | 否 | i | 闭集 `parse`/`edit`/`structure`/`confirm`/`ingest`/`sync`/`purge`/`index`/`kp`。本轮有写者的只有 `parse`/`edit`/`structure`；其余六个值（`index`＝检索索引构建、**`kp`＝梳理批次流水**）**建值不建写者**——行语义在此定档，写者随各自消费批落地，本轮任何路径不得写出，守护断言计数恒 0。`kp` 行承载「一次梳理批次对一本书/一章做了什么」（`job_id`＝取证版本、`target_id`＝节点、`result`＝成功/失败节计数），**禁作当前态判据**（事件行只答「发生过什么」，与「服务端不变式」的「行种类与坐标自证」条同判据）。各值的行语义见「流程落点」。扩值须评审——种类是判据的分母 |
| `lifecycle` | VARCHAR(16) | 否 `event` | — | 闭集 `event`（不可变流水，禁作判据）/`intent`（人工意图当前态，唯一合法判据位）。这一列是「一张表两用」能被守护的全部依据 |
| `actor` | VARCHAR(16) | 否 `human` | — | 闭集 `human`/`system`/`model`：人工确认门与自动归一必须可分列，「谁做的」是裁决不是细节 |
| `page_no` | INTEGER | 是 | i | 页坐标（edit/structure/confirm/单页 parse）；`NULL`＝不落在具体页 |
| `block_index` | INTEGER | 是 | i | 块坐标（edit）；`index` 语义随版本产物重排，故编辑绑版本而非跨版本身份 |
| `target_id` | VARCHAR(64) | 否 `''` | — | 被操作对象的稳定身份。structure 行＝服务端按几何派生的 `region_id`（跨版本不变、回给根侧长期定位）；kp 相关行＝`node_id`；其余 `''` |
| `intent_key` | VARCHAR(64) | 是 | UQ | **意图幂等键**：`intent` 行＝`md5(op_kind:book_id:job_id:page_no:block_index:target_id)` 前 16 位；`event` 行恒 `NULL`。MySQL 唯一键允许多行 `NULL` ⇒ 这就是只对意图行生效的部分唯一索引。唯一性由本列保证，坐标列本身不设唯一键 |
| `engine` | VARCHAR(24) | 否 `''` | — | parse 行的显式引擎名。DDL 默认 `''` 只是「未记录」哨兵，**不是回落值**：缺引擎名在受理时即报错，早先 `'mineru'` 那种 DDL 兜底正是静默回落入口，已退役 |
| `pages` | JSON | 是 | — | parse 行的请求页集（`null`＝全本）；受理门与在飞判据都读它；非 parse 行 `NULL` |
| `page_count` | INTEGER | 是 | — | 建任务时的全书页数快照 |
| `source_sha256` | VARCHAR(64) | 否 `''` | — | 版本自证：本版实际按哪份源文件跑的；`''`＝尚未回写 |
| `parser_signature` | VARCHAR(64) | 否 `''` | i | 解析代际签名。哨兵 `''`＝未知代际，**只判需重跑、不判过时**；签名输入集是冻结集合 |
| `embedding_signature` | VARCHAR(64) | 否 `''` | — | **检索索引代际签名**：嵌入侧输入集（绑定、模型、维度、服务端点）的规范化哈希。与 `parser_signature` **分列不复用**——两者输入集不同，共用一列即一列两义（族内红线）。哨兵 `''` 同上，只判需重建、不判过时。**本轮建列不建写者**：索引行的 `job_id`／`status`／`lifecycle` 映射属检索面自身设计，本轮不定；任何路径不得写出非空值（守护断言计数恒 0） |
| `render_profile` | VARCHAR(32) | 否 `''` | — | **版本级格式档案**：本版产物的回显口径，取值是 `schemas.py` 的闭集常量（如 `md+blocks/v1`），闭集外拒绝；同代内不允许逐页异构（这是特性）。本轮只建列，写者与读侧随产物格式轮 |
| `status` | VARCHAR(16) | 否 `queued` | i | parse 行用六态**原值透传**（`queued`/`running`/`completed`/`partial`/`failed`/`interrupted`）；非 parse 行只用 `ok`/`failed`。不做「看起来更好」的归并 |
| `progress` | JSON | 否 `{}` | — | parse 行进度读数 `{"parsed":N,"total":M,"failed":N,"error":"…"}`（节流写与终态必写）；另**折叠** `started_at`／`finished_at`／`duration_seconds` 三个内部时刻键，见下「时刻折叠」。杂项不得混入本列（本列是被轮询读的热列） |
| `failed_pages` | JSON | 否 `[]` | — | 结构化失败页码数组，**不截断**；`error` 只是给人看的摘要，摘要截断不丢页码 |
| `error` | VARCHAR(500) | 否 `''` | — | 人读摘要（≤500，族内口径） |
| `payload` | JSON | 否 `{}` | — | 操作载荷。edit：`{block_type, verdict, corrected_text, corrected_bbox}`；structure：`{op, bbox, anchor_bbox, members, kind}`；confirm：`{state}`。**形状闭集由单测断言**，禁自由键；`note` 按裁决是实体列、不进载荷 |
| `result` | JSON | 是 | — | 操作结果读数：受影响页/块、生成条数、拒绝原因码。与 `status` 分工＝`status` 是判据、`result` 是明细；`NULL`＝无额外读数 |
| `note` | VARCHAR(1000) | 否 `''` | — | 人写备注（单数，族内较新表口径） |
| `meta` | JSON | 是 | — | 扩展槽，三条红线全部按共享表通则引用（见「三类登记面」的通则指针）；定档后升实体列并清空对应键 |
| `created_at` | DATETIME | 否 | — | 事件发生时刻（UTC naive） |
| `updated_at` | DATETIME | 否 | — | 意图行最后改写时刻。`event` 行里只有 parse 行会被条件回写（进度节流与终态），故其 `updated_at` 会移动；非 parse 的 `event` 行一经插入不再改写，`updated_at` 恒等于 `created_at` |

键与索引：`uq_af_operations_intent(intent_key)`、`ix_af_operations_book(book_id, op_kind, created_at)`、
`ix_af_operations_version(job_id, page_no)`、`ix_af_operations_sig(book_id, parser_signature, status)`。
`embedding_signature` 本轮**不建索引**——无查询即无索引，为没有消费方的列建索引只是白付写入开销；
索引随检索面第一个消费方同批加。

**宽表与稀疏列的代价（写明不粉饰）**：parse 行与 edit/structure 行各只用其中一段，一行里必有整段空列；
`index` 行是第三段，且本轮**整段全空**（只有列没有写者）。反面方案是把 parse 专列塞进 `payload`——
那会让受理门、作业列表、重跑清单、启动期归一四类 SQL 全退化为
JSON 取值，既违「只读不筛」纪律，也让 JSON 反向闸失去意义。

**时刻折叠（两列换一列的代价与读法）**：`started_at`／`finished_at` 不是独立列，写进 `progress` 的同名
键，进入终态时另算 `duration_seconds`。代价由读取面承担：`repositories.job_timing(row)` 是取回两个时刻
的**唯一读法**（值不可解析时按 `None` 处理，不抛错，因为 `progress` 是人机共读的热列）；
`repositories.public_progress(row)` 在对外序列化时剥掉这三个键，使 API 契约的 `progress` 仍只有业务读数、
两个时刻仍以**独立字段**呈现。折叠的意义是让「一版跑多久」这类读数与进度同列，不必为两个只在终态与
轮询界面出现的时刻保留两条永不参与判据的列。

**parse 行语义（解析版本）**：请求体 `engine` 优先、缺省回落配置项，行记录最终执行所用引擎；未实现或未注册
名称在提交期即报错，**不落行、不回落**。错误分双载体：`error`（≤500 字符）是终态摘要，`progress.error`
（≤200 字符）是进行中的失败摘要，两者可并存，进入终态后以 `error` 为准；**哪些页**失败由 `failed_pages`
结构化承载，摘要截断不丢页码。`status` 六态与激活判据同源：`completed` = 目标页齐备，`partial` = 至少一页
产出但未齐备，`failed` = 零产出或引擎整体异常，`interrupted` 的**唯一写入者是启动期归一**。`progress` 写入
节流（距上次 ≥1 秒或每满 10 页覆写，终态必写，单页失败节点强制写入），因此运行中读到的
`progress.parsed` 单调不减但可能滞后。提交期模型服务探活失败发生在落库之前，故 503 场景不产生行。
`parser_signature` 与 `source_sha256` 是版本自证的一对读数：`(source_sha256, parser_signature)` 与
「当前源指纹 + 当前签名」**双全等**才判「不必重跑」，受理门与只读重跑清单共用这一处判据。签名的输入集
是冻结集合（引擎名、模型服务的 `version` 与 `protocol_version`、块策略版本的规范化哈希），只收
**影响块产物内容**的参数——超时只改成败、提交窗口只改批次、分块规则只改 chunks，入签名等于让改一行
配置触发全本重跑判定。`''` 表示**未知代际**（未记录签名的行）：只判需重跑、不判过时、不回算补签，
因为历史参数组合已不可考，算出来的是第二份假事实源。
`DELETE /books/{id}`（含 `?purge=true`）**不清理 parse 行**——版本行是版本的自证记录，也是版本目录名的
来源；页行与意图行则按**全部版本**删除。

**edit 行语义（块编辑）**：同版本同块重复编辑 = 覆盖更新（`intent_key` 唯一键 + 单语句 upsert，幂等）。
人工编辑是**覆盖层**——产物文件是模型事实源，编辑不入文件，API 返回时按生效版本合并。坐标必须含
`job_id`：映射表或归一化规则一变，同页下标含义就变，跨版本共用位置等于把人工核对结论套到另一版产物上。
仓储一律按 `intent_key` upsert/查询、不重算摘要，因此定位不依赖客户端记住任何 id；`created_at` 是首次
编辑时刻、`updated_at` 是最近一次覆盖时刻。

**structure 行语义（页级结构意图）**：`target_id` 里的 `region_id` 是确定性派生
（`rgn_<md5(book_id:page_no:op:身份几何)[:12]>`）而非随机发放——重复提交同一组意图必须命中同一行，否则
「全量声明即幂等」要靠客户端记住服务端发过什么 id，两次并发提交还会各造一行。身份几何只看
`op` 决定性的那一份：`add` 看自身框、`delete` 看锚、`merge` 看**成员集合**（数组序不参与身份，
改判阅读序仍是同一个合并）；`kind`/`note` 有意不参与——改备注不是新意图。几何一律用原页图像素、
左上原点（与产物块同一坐标系，不引入第二套坐标约定）；`delete` 的锚是**受理时生效版本**产物的几何快照，
重解析后按 IoU 重新命中；`merge` 成员的快照**唯一**落在 `payload.members`，不另存并集键（同一事实两处
存必漂移）。行只在三种情形消失：全量声明中缺席（缺席即撤销）、删书或管理面清除、且**不随版本更替删除**；
其余任何路径删它判为缺陷。页码因重新 ingest 而收缩时，越界区域行可见保留、不静默删。

### af_pages 列清单

| 列 | 类型 | 可空 | 键 | 语义 |
| --- | --- | --- | --- | --- |
| `book_id` | VARCHAR(100) | 否 | PK | → `af_books.book_id` |
| `job_id` | VARCHAR(64) | 否 | PK | 所属解析版本＝`af_operations.op_id`；`'legacy'` = 根视图存量 |
| `page_no` | INT | 否 | PK | 1 基页码 |
| `status` | VARCHAR(16) | 否 | — | `pending`（DDL 默认值）/ `parsed` / `failed` |
| `source` | VARCHAR(24) | 否 | — | 实际引擎标识 |
| `quality` | JSON | 是 | — | 页级质量信号（与产物文件内的度量同源） |
| `error` | VARCHAR(500) | 否 | — | 该页失败原因 |
| `artifacts` | JSON | 是 | — | 页级**在位产物索引**：`{png:{present,bytes,sha256}, md:{…}, blocks:{present,bytes,sha256,block_count}}`。只存在位、字节数与指纹，**不存正文、不存绝对路径**（路径按版本目录约定派生）；本列不作就绪判据。本轮只建列 |
| `confirm_state` | VARCHAR(16) | 否 `unconfirmed` | — | 页级人工确认态 `unconfirmed`/`confirmed`/`rejected`，作用域＝`(book_id, job_id, page_no)` ⇒ **版本内判定、不跨版本继承**；分块重建只聚合 `confirmed` 页。本轮只建列 |
| `section_kind` | VARCHAR(16) | 否 `''` | — | 页域角色闭集 `cover`/`preface`/`ack`/`toc`/`chapter`/`appendix`/`back`/`other`，`''`＝未判。要能可筛可判；版本换代走页行继承机制不重判（状态信号一律加列不张表）。本轮只建列 |
| `meta` | JSON | 是 | — | 扩展槽，三条红线同上 |
| `parsed_at` | DATETIME | 是 | — | 该页产物落盘并提交的时刻 |

**页表语义**：主键纳入 `job_id`，新版本不覆盖旧版本的行，因此历史版本可逐页解释「当时这页成没成」。
`pending` 以「无行」表示——不为未解析页预建占位行，`status` 实际只写 `parsed` / `failed`；断点续跑
的集合在 (`book_id`, `job_id`) 作用域内判定 = 该版本无行页 ∪ `failed` 页，且要求产物在盘。
持久化不变式：**产物写先于页行提交**且每页成功/失败即提交，故「有页行必有对应版本目录里的产物文件」。
写新版本前把生效视图的页行按 `job_id` 继承一份，使 `parsed` 计数与版本目录内容一一对应。

**不加的列（逐条给理由）**：`render_profile`（版本级，放这里会允许同版异构）、`text`/`markdown`（正文属
文件，违入库边界）、`format`/`bbox_space`（逐页坐标空间由产物内的 `PageResult.bbox_space` 声明，DB 再存
一份即第二事实源）、`page_kind`（与 `section_kind` 同义，不留两个名字）。

### af_kp 列清单

列名与域值刻意对齐共享知识点表与根侧候选池，使提升零转换。

| 列 | 类型 | 可空 | 键 | 语义 |
| --- | --- | --- | --- | --- |
| `node_id` | VARCHAR(64) | 否 | PK | 稳定身份 `afkp-<hex12>`，服务端随机发放（用随机而非内容摘要＝改名不该变成新节点，重挂父级同理）；提升后根侧按它做幂等锚，一经发放不可变 |
| `book_id` | VARCHAR(100) | 否 | i | 梳理作用域＝一册书（章／节／点都从这册书的解析产物长出来）；跨书合并归根侧主表 |
| `job_id` | VARCHAR(64) | 否 `''` | — | 该行取证时生效的版本（`''`＝人工直填或根视图）。**只作溯源，不进作用域键**，理由见「跨版本存活」 |
| `node_kind` | VARCHAR(16) | 否 `point` | i | 闭集 `outline`＝目录节点／`point`＝细知识点；所有「只数知识点」的查询按它过滤 |
| `parent_id` | VARCHAR(64) | 是 | i | 自引用父节点：章＝`NULL`、节＝章、point＝所属 outline。`NULL` 的 point 行＝未归档池（「重新编排目录」就是改本列与 `order_hint`） |
| `depth` | INTEGER | 是 | — | outline 层级 1=章 2=节；point 恒 `NULL`。有它就不必为判层级做递归 |
| `order_hint` | INTEGER | 是 | — | 目录前序序号（outline）／学习顺序提示（point）；梳理产物序即这一列 |
| `name` | VARCHAR(500) | 否 | — | 展示名（章名／知识点 canonical 名） |
| `norm_key` | VARCHAR(200) | 否 | i | 去重键（全角转半角＋空白折叠＋小写），与根侧同口径。同父唯一由**服务端校验、不建唯一键**（跨章同名合法；`parent_id` 为 `NULL` 时唯一键也无法去重） |
| `role` | VARCHAR(16) | 否 `''` | — | 闭集 `''`／`core`＝核心／`explainer`＝解释性／`example`＝举例。**「核心」是节点在所属章内的地位，不是边** |
| `type` | VARCHAR(16) | 否 `''` | — | point 行四分类 `memory`/`procedure`/`concept`/`design`（与根侧同域，间隔复习序列依据）；outline 行必须 `''` |
| `summary` | VARCHAR(1000) | 否 `''` | — | 一句话总结（章与点都可写），上限与根侧同值使提升原样平移零截断；**长叙述不落本列** |
| `links` | JSON | 是 | — | 关系简单写：`[{kind, to, note}]`，`kind` 闭集 `prerequisite`（先学，有向、参与 DAG）/`co_requisite`（需同时掌握，对称，写前按 `min,max` 归一端点序使反向重复声明幂等）/`explains`/`exemplifies`/`extends`（有向，参与 DAG）。闭集外一律先入 `note`，不随意扩 `kind`。**本列是 JSON 反向闸的明文越闸例外**，判据见「准入守则的答卷与例外登记」 |
| `anchors` | JSON | 是 | — | 来源锚点 `[{kind, ref_id, page, block_indexes, snippet}]`，页序＝**PDF 页序**（印刷页码只存在于解析草稿）；梳理结论必须能回指到第几页哪几块，否则无从复核 |
| `narrative_ref` | VARCHAR(200) | 否 `''` | — | 长文在数据根下的**相对路径**：讲解/证明/例题正文写文件，本列只存指针，`''`＝无。路径基准与数据根双闸沿用产物约定（单段名 + 根内路径，拒绝越根） |
| `gen_status` | VARCHAR(16) | 否 `candidate` | i | 闭集 `draft`/`candidate`/`rejected`，**只有机器写**。域内**不设**表达「人已采纳」的值：人工终态的唯一判据位是 `confirmed`，同一事实两处存是族内红线 |
| `confirmed` | INTEGER | 否 0 | — | 人工确认位（0/1），**只有人工可置**。与 `gen_status` 分列＝机器与人工各写一列互不覆据；置位才可被提升 |
| `gen_batch` | VARCHAR(64) | 否 `''` | i | 梳理批次＝一次重编排作业。整批复核、整批清理、整批回滚与「保留最新 2 批」全据此，**故不需要批次表** |
| `model_ref` | VARCHAR(128) | 否 `''` | — | 产出者标识（`manual`/模型名）；自由文本列不得含密钥与个人信息 |
| `rev` | INTEGER | 否 0 | — | 乐观并发序号：每次成功写入自增，长表单可传 `if_rev`，不匹配即 409 |
| `meta` | JSON | 是 | — | 扩展槽，红线同上 |
| `created_at` | DATETIME | 否 | — | 节点建行时刻 |
| `updated_at` | DATETIME | 否 | — | 最近一次改写时刻 |

键与索引：`ix_af_kp_book(book_id, node_kind, gen_status)`、`ix_af_kp_parent(parent_id)`、
`ix_af_kp_batch(gen_batch)`、`ix_af_kp_norm(book_id, node_kind, norm_key)`（普通索引，服务端查重）。

**本轮范围（这就是 `af_kp` 仍暂定的全部含义）**：表与列随新 base 一次建齐，仓储读写面（`KpRepository`）
与删除面（删书、管理面整族清除）已就位，但**没有梳理作业、没有对外端点、没有提升作业**。因此有四条
计数恒 0 的守护断言：① 非空 `embedding_signature`；② `op_kind='index'`；③ `op_kind` 其余五个无写者值
（`kp`／`confirm`／`ingest`／`sync`／`purge`）；④ `gen_status` 的**域外值**（闭集
`draft`/`candidate`/`rejected` 三值之外——域内没有「人已采纳」这一档，人工终态只由 `confirmed` 表达）。
②③把仓储层全部生产插入腿（`OperationRepository.create`／`upsert_edit`／`replace_page_intents`）
跑一遍再计数，故「任何路径不得写出」是可执行判据而不是口头约定。
消费通路的形状（批次作业、复核端点、提升幂等锚）属分析模块设计篇，其立项与 `links` 越闸终审一并了结后，
本节才升为**已确认**。

### 服务端不变式（DDL 表达不了的）

| 判据 | 内容 | 违规处置 |
| --- | --- | --- |
| 边的端点存在 | `links[].to` 必须存在于同 `book_id` 的节点集；跨书关系归根侧主表，不落本表 | 422，响应点名缺失的 `to` |
| 无自环 | `to == node_id` 一律拒绝 | 422 |
| DAG 只覆盖次序语义 | `prerequisite` 与 `extends` 合起来**必须无环**，落库前对现有图做 DFS，成环即拒绝并回指环路径；`co_requisite`/`explains`/`exemplifies` 允许成环（它们不是次序语义） | 422 + 环路径 |
| 对称边归一端点序 | `co_requisite` 写入前按 `min,max` 归一；读时按两端任一方向的并集解释 | 静默归一（幂等），不报错 |
| 单向存储、双向可读 | A 声明「A 需先于 B」只在 A 行落一条，B 行不自动镜像；读侧做并集，写侧不得为「整齐」去改对端行 | 并集入口收在仓储 |
| 行类型与列取值耦合 | `node_kind='outline'` ⇒ `type=''`、`depth∈{1,2}`；`point` ⇒ `depth IS NULL` | 422 |
| 删除必须清引用边 | 无物理外键，删 A 时对所有 `links` 含 A 的行在锁内整列覆写 | 与删除同事务 |
| 人工位只有人工可写 | `confirmed` 只允许 `actor='human'` 的路径置位，系统路径永远写 0 | 守护用例断言自动批次跑完后计数不变 |
| 行种类与坐标自证 | `op_kind='parse'` ⇒ `lifecycle='event'` 且 `job_id == op_id`；`op_kind='structure'` ⇒ `job_id == ''`；`op_kind='kp'` ⇒ `lifecycle='event'` 且 `job_id`＝该行取证版本；`op_kind='index'` 与非哨兵域值的写出计数恒 0 | 守护断言；版本坐标不接受外部传入 |

### 键与并发写入形态

本案要防的唯一并发形态是**「先读后判再写」**，因此所有当前态写入只允许两种形状：唯一键 upsert、
行锁内整批替换。

| 判据 | 内容 | 反向判据（会红的用例） |
| --- | --- | --- |
| 事件行只 INSERT | `lifecycle='event'` 的写入路径只有插入，无 UPDATE/DELETE（唯一例外见下行） | 断言「非 parse 的 event 行被 UPDATE」计数恒为 0 |
| parse 行只向更终回写 | 回写用带前序态条件的 UPDATE（`op_kind='parse'` 写进 WHERE 而不是信任调用方），条件不匹配即不写并记日志 | 迟到的 `reconcile_interrupted` 不得改写已落的 `completed` |
| 意图行唯一键 upsert | 定位靠 `intent_key` 上的唯一约束，单语句 upsert，**禁止**「先查有没有再决定插入或更新」 | 双会话并发 upsert 同一坐标 ⇒ 表内恰一行、读回后写值 |
| 整批声明在同一把锁内 | 「缺席即撤销」的读现有行→算差异→写必须在对目标 `af_books` 行加悲观锁（MySQL `SELECT … FOR UPDATE`；SQLite 单写者天然串行）后进行，锁内不发起模型调用 | 双会话并发声明同页、集合互不包含 ⇒ 终态只留后提交者且无复活行；双 POST 全本 ⇒ 恰一个建版本行、另一个 409 |
| JSON 列不做增量改 | `links`／`payload`／`artifacts`／`meta` 的写入是**整列覆写**；需要「读旧值改一点」的形态必须落在上一行的锁内 | 两并发整节点声明 ⇒ 恰一个终态、无键丢失 |
| `af_kp` 带乐观序号 | 唯一键 upsert 解决「同一坐标并发」，`rev` 解决「人基于旧读数提交」——两类不同的并发 | 读 `rev=3` 后他人写到 `rev=5` ⇒ 带 `if_rev=3` 的提交 409 |
| 受理门与在飞判据同源 | 两个判据共用同一把锁内的同一纯函数，避免两次读库看到不同世界 | 同「整批声明」的第二条 |

SQLite 上 `with_for_update()` 是空操作，因此锁类判据（意图 upsert、整批替换、`rev` 冲突）在单测层只
能证其**语义**、不能证其**串行性**；真并发反向判据只在 `qed_test` 的 MySQL 层成立，执行面见
[operations.md](../guides/operations.md)。

### 跨版本存活（为什么知识点表与结构意图的 `job_id` 不进作用域键）

三条独立依据同向：① 准入守则第一句就是「身份需跨版本稳定」才值得入库；② 页级人工框早已确立
「刻意不含 `job_id`、跨版本存活、不随版本更替删除」的先例（[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)
决定 1），知识点是同一类人工资产；③ 根侧主表的书目引用口径是「版本换代不碎树」。重解析是**产物换代**，
不该抹掉人已确认的知识结构。代价一起写明：换版或重新 ingest 后锚点可能失效（页序变了），处理照结构
意图行的「越界行可见保留、不静默删」，由 `job_id` 自证该行取证于哪一版——失效可见，不静默改写。

折叠到 `af_operations` 后，[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md) 决定 1 的唯一落点是一条守护断言：
`op_kind='structure'` 行的 `job_id` 恒为 `''`。

### 读取与失败语义

- **读取端如实报错**：生效版本目录缺失即 404，页行 `parsed` 而产物不在位在逐页清单里原样暴露，
  不静默回落根视图——与「无自动兜底」同源（[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)）。
- **无生效版本时写入面拒绝**：结构意图的删除/合并要锚在生效版本产物的几何上，根视图存量没有可锚的块，
  因此结构声明在无生效版本时整体拒绝而不是猜一个位置（[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)
  决定 7）；结构意图行本身跨版本存活，激活新版本不会带走它。
- **形状切换后的读法**：本篇的表清单与列清单就是代码事实源；退役表名只在「前身对照」出现，作用是让
  旧文档与根侧对账可换算，**任何代码、端点或状态机不得再以退役表名为判据**，也不得留下「旧表名 +
  新表名」并存的过渡措辞。库内换形状属 D 类人工窗口（逐表备份留指纹 → 用户批准 → 执行）。

## 六、与 QED-Engine 的交互与同步契约

### 登记面 A：共享 `qed_*` 四表（只登记，不复制）

| 表 | 一句话功能 | 本仓库读写权 | 变更通路 |
| --- | --- | --- | --- |
| `qed_domain` | 一个学科领域 | 只读 | **受托建表**：所有权在根、可执行面在 QED-Tracker；变更走总纲 6.2（先改总纲 → 通知消费方 → 所有权方执行迁移） |
| `qed_course` | 一门课程（阶段、先修、别名、学习顺序） | 只读 | 同上 |
| `qed_knowledge_point` | 一个**已确认**知识点或目录节点 | **只读不写**（当前唯一写者 8900） | 结构正本是总纲五章；本仓库需要它加列须向根登记，不自行 ALTER |
| `qed_llm_calls` | 一次 LLM 调用记录 | 只写审计行、不改结构；**绝不 DROP 重建** | 三项目均可写是全库唯一跨项目直写例外（R2）。本仓库**尚未实现写入**（写入通路待立项） |

### 登记面 B：需从 QED-Tracker 同步的形状

- `af_books` 的只读快照源是 `qt_books`：同步只取已验证书行的书目元数据（`book_id`／`domain_id`／
  `course_id`／`course_name`／`knowledge_id`／`title`／`part`／`display_title`／`authors`／`file_path`），
  逐列落点见「af_books 列清单」——那是**本仓库的列**，不是对方列的复制。`qt_books` 侧删除或变更
  不回写已同步行（快照语义），任何外部故障或结构变更不进入本表读取路径。
- `qed_domain`／`qed_course` 的可执行形状是 QED-Tracker 的 ORM（模型即 schema），本仓库只按登记面 A
  读它；两侧任一改动未随步即总纲契约红，责任在所有权方，不在本仓库。
- `qt_knowledge`（教程／课程延展资料归类）经 8901 HTTP 透传消费，本仓库不直连、不入库。
- 本仓库用 Alembic 独立初始化 `af_*`，不依赖其他项目迁移，也不被其他项目迁移依赖；8901 离线只影响
  同步动作，不影响本表读取。

### 提升通路（分析模块 → 共享知识点表）

- 共享 `qed_knowledge_point` 的当前唯一写者是根侧 8900，本仓库对该表**只读不写**（见登记面 A）。
  **本组件不得直写共享知识点表**，这不是可协商项。
- 通路＝**8900 拉取式单向**：本组件提供只读导出端点（端点形状属 [api.md](api.md)），8900 侧提升作业把
  `confirmed=1` 的行按 outline→point 两层写入主表，`summary` 原样平移，`anchors` 追加
  `{kind:'af_node', ref_id:<node_id>}` 作**幂等锚**（重复提升命中同一行）。锚落 `anchors` 而非 `meta`，
  因为 `meta` 禁进 WHERE 无法反查。
- **不回写提升结果**：`af_kp` 不设 `promoted_kp_id` 列。提升事实的唯一正本在主表，工作区侧的终态只由
  `confirmed` 一列表达（`gen_status` 域内不设第二个人工终态位）；跨仓回写会造出第二条写路径。
- LLM 调用账**不进 `af_*`**：视觉解析与梳理文本调用都记共享 `qed_llm_calls`（登记面 A 已声明本仓库
  对该表的写入尚未实现），该表只做追加式 ALTER、绝不 DROP 重建。

### 表级变更的根侧同步面

`af_*` 是本仓库私有表族，按总纲 6.2「他项目专用表**表级**变更＝只同步表信息」处理：表数变更、前身表
消失、新表新增、迁移链 head 改变时，通知根 → 根改其总览行（表名／表族／一句话功能／事实源指针）
＋ 4.4 读写矩阵 ＋ 6.3 路由行；**列级细则不同步**（总纲明写「本文不承载其细则设计」）。本篇只声明
同步面，**不承载库内 head 的现状读数**（见「迁移序列与档 B 链重置」）。

### 外部故障不传导

8901 离线只影响同步动作，不影响本表读取。读取面**纯 DB 驱动**：`af_books` 为空即空列表 / 404，
不回落文件系统 `book.json`（根视图只剩 `pages/p*.png`，元数据唯一事实源是本表）。

## 七、变更与运维

### 变更顺序与改动面

- **表张数不是散文事实**：`AF_TABLES`（`db/models.py`）是表族登记的唯一口径，清除模型元组、将删
  行计数字段、集成夹具的 DROP 白名单全部由它派生，因此「漏改一处删除面」这类漂移在守护层即红
  （`tests/unit/test_db_table_family_governance.py`）。加表的改动面＝`AF_TABLES` + 本篇表清单与
  列清单 + [ADR 0005](../adr/0005-mysql-integration-test-isolation.md) 白名单逐名枚举 + 一个迁移；
  **并表／换表**另加：`api/admin.py` 的清除模型元组与 `AdminPurgeCounts` 字段名、`db/__init__.py`
  导出集、仓储类合成，以及本篇的表清单与全部列清单同批改写。表数不进任何标识符名字（张数只在权威
  口径出现，名字携带张数同样是漂移源）。
- **ORM 与真机迁移同窗口**：**ORM 新增列与真机迁移必须同窗口发布**，否则读该列即失败。服务启动
  **不自动迁移**——只做关停与状态归一，仓库内无 `create_all` / `upgrade` 调用，因此代码合入对共享
  `qed` 库零写入。
- **管理面清除是 DML-only 例外**：`af_*` 表族行都在清除范围内（含 parse 版本行），但**无 DDL、无
  `DROP DATABASE`**——共享库里另有其他项目的表；默认不挂载、须管理 token 与逐字确认，作用域内有
  活动态任务时拒绝执行。`af_books` 行同样可清：书目是同步来的快照，可经同步重建，不是持久资产。
  清除面由 `AF_TABLES` 派生，新表一旦挂上族内就自动进删除面。

### 准入守则的答卷与例外登记

| 守则 | 四表形状的答卷 |
| --- | --- |
| 新表准入三判据 | `af_operations`＝①②③全中（意图身份跨版本、`intent_key` 唯一键、事件流按坐标与签名做 SQL 集合检索）；`af_kp`＝①②③全中（节点身份跨版本、`node_id` 唯一、按书/类/态集合检索）。两件合并为一次评审 |
| JSON 列反向闸 | **明文越闸一次**：`af_kp.links` 承载的正是「需要反向集合检索的身份关系」——按对端反查「哪些点以 X 为先修」只能全表扫 JSON。采纳理由＝关系要求「一张表、简单写」，且当前消费现场（展开一册书的图）是正向读。**转表触发判据**＝一旦出现「按对端反查是主屏读法」或「需要 DAG 完整性集合校验」，`links` 即升关系边表，届时走独立加表评审。这条例外必须留在本节正文，否则反向闸退化成建议；其终审与 `af_kp` 转正同批 |
| 不建块正文表 | 触发转建判据成文一次：只有当出现**一条必须用 SQL 跨页检索块文本才能在服务端完成**的梳理／抽题需求，且经产物文件逐页读取的通路其吞吐被真机演练否证时，才评审块正文表；判据触发前的任何一次「读起来慢」都按现有通路优化（批量端点、单次多页），不建表。`artifacts.block_count` 与 `quality` 给出「不读正文也能计数」的那一半需求，这正是把该方案挡在门外的原因 |

### 承载力审计（四表够不够：能力与规划能力逐条落点）

审计只用两条第一性判据，都是二章守则：**入库三判据**（身份需跨版本稳定／需唯一约束／需 SQL
集合检索，三者任一且现有表族承载不了才新增表）与**入库边界**（文件系统承载产物正本，DB 只存元数据
与状态）。逐条给答，不留「以后再说」。

| 能力 | 存储判定 | 依据 |
| --- | --- | --- |
| 解析全链路（同步／ingest／逐页解析／版本激活／对照回显） | **三表足够**：`af_books`＋`af_operations`＋`af_pages` | 版本身份＝parse 行 `op_id`，页级就绪＝`af_pages.status`，两者都已在位 |
| 人工核对（块编辑／页级结构意图／页确认） | **足够**：前两者是 `af_operations` 的 intent 行，后者是 `af_pages.confirm_state` 一列 | 状态信号一律加列不张表（本篇自定守则） |
| 知识点梳理（章／节／点、关系、锚点、长文、批次、复核） | **足够**：`af_kp` 单表 ＋ 数据根长文文件 | 关系走 `links`（越闸例外已登记），批次走 `gen_batch` 分组键 ⇒ 关系边表与批次表都不需要 |
| 提升到共享知识点表 | **足够，且不需要本方新表**：拉取式单向 | 登记面 A 的只读不写；回写会造出第二条写路径 |
| 模型调用账 | **足够，且不进 `af_*`**：追加写共享 `qed_llm_calls` | 登记面 A；该表绝不 DROP 重建 |
| 数学解析器（LaTeX 校验／规范化／重渲染验证） | **足够**：逐块验证结果属产物落 `blocks.json`，页级聚合落 `af_pages.quality`，未定档的度量先进 `af_pages.meta` | 入库边界——验证结果是**派生量**，可由产物与规则重算，重算得出来的东西不占身份位 |
| 知识图谱（定义／定理／公式的语义抽取与关联） | **关系面足够**（`af_kp.links`）；语义实体分类是**域值扩张请求，不是新表**——`role`／`type` 都是闭集，扩值须评审 | 转表触发判据已在「准入守则的答卷与例外登记」；实体分类不命中入库三判据的第②条（无唯一约束需求） |
| 学习交互（对话／图片问答／练习／学习进度） | **不属本组件存储**：本方只供给检索与知识点结构，交互与进度归根仓库与书目侧 | 前缀即所有权；本篇只登记 `af_*` |
| 向量检索（分块→嵌入→混合检索） | **足够**：向量正本不进 MySQL（外部向量库），`chunks.json` 是派生文件不入库，块级检索单元按锚回指 `af_pages` | 块正文表的触发转建判据已登记且**不触发**——两类索引都在 MySQL 之外持久化，不存在「必须用 SQL 跨页检索块文本」的需求 |
| 检索索引的版本管理（换嵌入模型／换维度写新版本，旧版可读可回滚） | **四表可承载（甲案，列已在位）**：`af_operations` 的 `op_kind='index'` ＋ 独立列 `embedding_signature` | 索引版本要求「按签名做 SQL 集合检索」＋「同签名只追加不重建」的唯一性，入库三判据全中；不复用 `parser_signature`（输入集不同，共用一列即一列两义） |

**扩展槽的分布（即「留一个 message 类 JSON 列」这件事的落点）**：四表各有一个 `meta` JSON 扩展槽
（`af_books`／`af_operations`／`af_pages`／`af_kp`）。列名取 `meta` 而非 `message`，是为与共享表同名列
对齐、使提升零转换；三条红线按通则指针引用（不进 WHERE、不建函数索引、不参与唯一键），键定档后升
实体列并清空对应键。`af_books` 的那一个是四表里唯一有**双写者风险**的（同步作业 upsert 与人工写同一
列），故它的两条判据与反向用例随列同批写死，见「af_books 列清单」。

**检索索引版本行的落法（已裁＝甲，列已在位）**：该行与解析版本**同构**（一次作业＝一个版本＝一行、
按签名判增量、旧版可读可回滚），差别只在签名的输入集属嵌入侧（绑定、模型、维度、服务端点）而非
解析侧（引擎、模型服务版本、协议版本、块策略版本）。

| 落法 | 形状 | 代价 |
| --- | --- | --- |
| **甲（采）**：并进 `af_operations` | `op_kind` 扩 `index` 一值 ＋ 增一列**独立的** `embedding_signature`（不复用 `parser_signature`） | 事件表继续变宽，多出第三段稀疏列；「版本行」与「操作流水」两种语义共表 |
| 乙（拒）：独立第五表 | 一行＝一个索引版本，列只服务检索面 | 表数 4→5，与「四表足矣」的结论正面冲突，须独立加表评审并同步全部表清单守护 |

采甲的附带后果必须与列同批写明：**本形状产生暂无写者的列与暂无写者的闭集值**。处置口径与梳理流水值
相同——守护断言任何路径写出的非空 `embedding_signature`、`op_kind='index'` 与 `op_kind='kp'`
计数恒为 0，直到检索面立项并交出索引行的 `job_id`／`status`／`lifecycle` 映射（那属检索面自身设计，
本篇不定）、直到梳理实现批交出 `kp` 行的写者（其行语义已由「流程落点」第 9 步与
[knowledge-analysis.md](../design/knowledge-analysis.md) 的 `KA-27` 定档）。整族重建档位下这些列与其它列同批建在新 base 里，
故「现在建」与「以后 ALTER」成本相同，
不构成推迟理由；反过来说，它也不构成**提前定义行语义**的理由，行语义仍随消费方来。

### 迁移序列与档 B 链重置

形状切换落在**整族重建**档位（档 B）：前身五表随族 `DROP`，**迁移链重置**为一条新 base
`20261022_0001_af_family_rebuild`（`down_revision = None`），一次建齐四表。被拒的方案是「保留旧链、
追加一条 DROP+CREATE 迁移」——旧链每条都在为不存在的形状跑 DDL 与断言，且测试库夹具每次从 base
重建会先建旧五表再删。旧链文件的存在性由 git 历史与 `docs/history/` 留档，不靠保留旧链来「兼容」。

| 迁移 | 结构变化 | 存量处置判据 |
| --- | --- | --- |
| `20261022_0001` | 四表一次建全：`af_books`（含 `meta`）、`af_operations`（27 列：`intent_key` 唯一键 + 三个索引 + `parser_signature`／`embedding_signature` 两个**分列**签名 + `render_profile`）、`af_pages`（含 `artifacts`／`confirm_state`／`section_kind`／`meta`）、`af_kp`（23 列、四个索引） | 无存量可迁（档 B 整族重建）：前身作业行随 `DROP` 清除、**不迁移**；`af_pages` **不回填**，唯一恢复路径是逐本重新 ingest + 重新解析——不提供「从盘上产物反向灌页行」的脚本，那会让 DB 行成为产物的派生物，与「DB 是就绪唯一事实源」反向 |

**双栈一致**：同一份迁移 DDL 必须同时通过 SQLite（临时库）与 MySQL（`qed_test`）的升降级往返；
任何只对单一方言成立的写法都算缺陷。**降级有损且显式**：`downgrade` 回更早结构时整族 `DROP`
（档 B 的 base 之前没有形状可回），产物文件不随迁移删除（DB 与文件解耦）。

结构现状 = head 迁移之后的形状，也就是五章各张列清单表。**本篇不承载库内 head 的现状读数**——
「应用不自动迁移」使迁移文件落地 ≠ 业务库已执行，两库（`qed` 与 `qed_test`）的 `alembic_version`
是随时会变的库内事实，其唯一读点是 [project-status.md](../trackers/project-status.md) 与
[operations.md](../guides/operations.md)；此前本篇正文自陈的某个 head 值即因把库内读数写进固定文档
而落后于实读，该写法已废止。测试库 `qed_test` 由集成夹具每次从 base 重建到 head，因此新迁移的
MySQL DDL 在门禁里就被真实执行一次。迁移的运维执行步骤与备份要求见 [operations.md](../guides/operations.md)。

**空窗期读法（重建窗口的四条验收判据）**：① 前身作业行随 `DROP` 清除、不迁移；② `af_pages` 不回填
（同上）；③ 空窗期 `GET /books` 返回空列表（不是报错，因读取面纯 DB 驱动）、书目详情与页清单 404、
`POST /books/sync` 起服后立即可用且幂等，且必须留一行可见运维提示；④ `legacy` 保留为
`af_pages.job_id` 的作用域哨兵，但 `af_operations` 内不出现 `job_id='legacy'` 的行（事件行必须有真实
`op_id`）。

### 保留与裁剪

| 判据 | 内容 | 理由 |
| --- | --- | --- |
| 事件表与知识点表一律不清理 | 明文「不清理」是本仓库的自定口径，不选保留窗数值 | 版本自证史与人工结论是审计唯一来源；根侧只声明边界不代设数值 |
| 「保留最新 2 次」＝按 `book_id + gen_batch` 分组只保留最近 2 批的**未确认机器产物行** | 已确认行不属于「最新 2 次」的管辖范围 | 该裁决针对的是机器梳理结果的并存份数，不是人工资产 |
| 不可裁剪四类 | ① 被 `af_books.active_job_id` 引用的 parse 行；② 全部 `intent` 行；③ 活动态行（`queued`/`running`）；④ `af_kp` 中 `confirmed=1` 或被任一 `links` 引用的节点行 | 前三类是指针与在飞，第四类是人已背书的知识结构（判据位只有 `confirmed` 一个） |
| 任何裁剪执行必须只读清单先行 → 用户批准 → 逐书执行 | 清单本身要能同机复算 | 先删后解释会毁掉自证链 |

### 凭据与敏感字段

凭据（`QED_DB_PASSWORD` 等）绝不下发到任何接口响应、日志或异常信息；本仓库不暴露数据库连接端点，
数据库能力由 8900 的启动快照统一呈现（配置域契约归根仓库）。列里不存产物正文与大字段——页内容、
分块与图像全在文件系统。

## 八、守护与验证

### 形式与结构守护

- **章体骨架守护**：`tests/contract/test_database_design_document_follows_eight_chapter_skeleton`
  断言本篇 H2 逐字等于八章编号体（顺序与集合都固定，多一章少一章即红），
  `test_architecture_documents_follow_four_section_skeleton` 继续穷举其余三篇的四段骨架。
  体裁分治由 [ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md) 立法。
- **正文去日期化的边界**：本篇一~七章为契约正文段，八章（本章）为追溯段——任务编号与日期只允许
  出现在本章。
- **结构对账守护**：`tests/contract/test_fixed_document_form.py` 把本篇每张表的列清单与
  `src/axiom_flow/db/models.py` 的 SQLAlchemy 表定义双向比对（列名集合、可空、主键标记、VARCHAR 长度），
  并断言每张表的唯一键与索引名在本篇出现、被标 `PK` 的行恰为 ORM 的主键列。**对账集合参数化自
  `AF_TABLES`**，因此加一张表就自动进对账；被解析的锚点是 `### <table> 列清单` 这一标题形（故章体内
  的小节标题是可对账锚点，不随章号改名），列清单必须一列一行（合并行不被解析）。
- **表族治理守护**：`tests/unit/test_db_table_family_governance.py` 钉住「凡以 `af_` 前缀挂上
  `Base.metadata` 的表必须入 `AF_TABLES`」、清除模型元组与 `AdminPurgeCounts` 字段名与表名同构、
  夹具 DROP 白名单同构，以及 admin 侧不得逐字硬编码表名；并用变异用例证明「加表不登记」必然转红。

### 迁移与行为守护

- **迁移守护**：`tests/unit/test_db_migrations.py`——SQLite 临时库上升降级可重复、四表建齐、主键与
  唯一键形状、列默认值与哨兵、`legacy` 作用域，以及**反向判据**（新 base 之前没有旧链残留、
  退役表名不得再被建出）。
- **真 MySQL 层**：`tests/integration/test_parse_persistence.py`（显式 opt-in）从 base 升到 head，
  证明 JSON 列与表族在 MySQL 上的提交边界与可见性成立；隔离边界按
  [ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 内只碰自有 `af_*` 表，
  绝不 `DROP DATABASE`）。
- **行为守护**：`tests/unit/test_db.py`（同步幂等与 `meta` 保护区、编辑 upsert 覆盖、按版本作用域读写、
  结构意图的缺席即撤销与几何派生幂等、按书删除全部版本）、
  `tests/contract/test_api_v1_contract.py`（端点形状与清除面的库内同构）。
- **门禁**：`pytest tests -q` + `ruff check src tests`；解释器与命令矩阵见
  [开发指南](../guides/development.md)。

### 来源与追溯

- **来源**：四表重设计与命名空间规则来自 `docs/history/plans/` 的 ARCH-020 轮（已归档）；解析版本
  布局来自 PLAN-004 / REQ-080（已归档，去处见 [plans/index.md](../plans/index.md)）；进度与结局分列
  的裁决是 [ADR 0007](../adr/0007-parse-status-semantics.md)，实施轮为 PLAN-006（已归档）；管理面
  清除例外为 PLAN-010 / PLAN-011（已归档）。逐日过程与真机读数在
  [trackers/completed.md](../trackers/completed.md)。
- **形状裁决与三类登记面的承载**：登记面 A/B/C 的切法、前身五表 → 四表的**逐列去向**、并发
  写入形态的反向判据、整族重建（档 B）四件必答、`af_kp.links` 越闸例外的采纳过程，正本在
  [PLAN-024 壳](../plans/2026-10-21-af-table-family-rebuild.md)（在冊），该壳同时承接根侧入站请求
  REQ-136（并入原 REQ-123／REQ-129）。代码批与**库内整族重建窗口**（档 B，`qed`＋`qed_test` 两库）已于 2026-10-22 执行完毕，
  真机 DDL 逐列对账与空窗读数的留档见 [operations.md runbook](../guides/operations.md)；只有**根侧同步**待 W9 回执；
  全库通则与共享表列级正本在根仓库总纲 `QED-Engine/docs/architecture/database-design.md`（跨仓纯路径）。
- **章体改判**：本篇原用本仓四段骨架（[ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md)
  决定 2），2026-10-22 按用户点名改为根侧八章编号体，以与总纲按章对读；改判立法、体裁分治的守护
  随步与零丢失复验见 [ADR 0017](../adr/0017-database-design-eight-chapter-skeleton.md) 与
  [PLAN-024 壳](../plans/2026-10-21-af-table-family-rebuild.md) 的 W14 节。
