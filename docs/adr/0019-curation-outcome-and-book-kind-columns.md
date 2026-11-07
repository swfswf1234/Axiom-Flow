# ADR 0019：梳理结局位与种类位入 af_books（两枚真列、base 之后加列）

状态：Accepted
最后更新：2026-11-06
关联代码：`src/axiom_flow/schemas.py`（`BookSyncItem.roles`）、`src/axiom_flow/db/models.py`（`DOC_KINDS`、
`derive_doc_kind`、`AfBook.doc_kind`／`AfBook.last_curation_result`）、`src/axiom_flow/db/repositories.py`
（`BookSyncRow.roles`、`BookRepository.upsert_many`）、
`src/axiom_flow/migrations/versions/20261106_0001_af_books_kind_and_curation.py`
关联测试：`tests/unit/test_db.py`（派生写与两列的保护区）、`tests/unit/test_schemas.py`（同步行收 `roles`）、
`tests/unit/test_db_migrations.py`（列集合、默认值与升降级往返）、
`tests/contract/test_fixed_document_form.py`（列清单 ↔ ORM 逐列对账）

## 背景

知识点梳理要按「教材／习题集」两类书分流，也要能回答「上一批梳理跑到什么结局」。这两个问题今天在
存储层没有合法判据位：

- [ADR 0016](0016-af-family-rebuild-into-four-tables.md) 决定 2 把当前态收成四个来源，其中 `af_books` 的
  职责只列为「进度／结局／生效指针」三项，且三项**专指解析**；种类与梳理结局不在这个清单里。
- 同一 ADR 的决定 10 ① 明文禁止同步 upsert 触碰 `af_books.meta`（该列是双写者风险列，人工侧走整列覆写）。
  把种类写成 `meta` 键，会让同步腿与人工写撞同一列，且同步重跑会把种类键当成人工读数保留或覆盖，
  两条判据互相抵消。
- 写成读时派生视图也不行：种类是**同步输入**（根侧 `qt_books.roles`）的函数，本仓自有数据里没有这份
  输入；梳理结局是批次收尾的结论，不落列就只能靠读流水现算，而流水按 ADR 0016 决定 2 永远不作当前态判据。

故先立本决定：按 [ADR 0016](0016-af-family-rebuild-into-four-tables.md) 决定 11 的「加列不加表」先例，
把两问各落一枚真列，形状仍为四表。

## 决定

1. **`af_books.doc_kind`**（VARCHAR(16)、非空、默认 `''`）只由同步腿派生写：同步行的 `roles` 含
   `textbook` ⇒ `textbook`；否则含 `exercises` 或 `solutions` ⇒ `exercise`；两者都落不出 ⇒ `''`
   （未声明）。两值闭集在代码里由 `DOC_KINDS` 守住，派生收敛在一枚纯函数里，人工端点与任何状态机
   不得写本列。两义并存**按教材**：同一本书「正文＋习题」是常态，而教材侧先整理出的「章 → 题号」
   正是习题集批次合并的落点。
2. **`af_books.last_curation_result`**（VARCHAR(16)、非空、默认 `''`）只由批次收尾与启动期归一写：
   闭集 `''`／`completed`／`partial`／`failed`／`interrupted`，与 `last_parse_result` 同形同轴
   （[ADR 0007](0007-parse-status-semantics.md) 的进度与结局分列）。端点不得直写；「梳理中」不另立列，
   仍由在册的 `gen_status='draft'` 存在性派生。
3. **未声明种类的书不受理梳理**：`doc_kind=''` 时受理门当场拒。这与根侧「空即教材」的显示容错
   **有意不同**——本侧若照抄，每一本未同步到 `roles` 的书都会白跑一轮目录与逐节模型调用并把花费挂账。
4. **加列走一条可往返的迁移**：在唯一 base `20261022_0001` 之后追加一条 revision，`upgrade` 加两列、
   `downgrade` 删两列；不新增表、不改既有列、不回填——存量行两列取默认 `''`，其读法就是「未声明／
   从未梳理」。
5. **`meta` 的两条红线不动**：同步 upsert 仍只写同步域快照列与时刻列；本次两列均不落 `meta`，
   人工写 `meta` 仍是整列覆写。

## 后果

- **正面**：种类与梳理结局各有一处可判、可读、可对账；书目列表一次读全「解析完成／开始梳理／梳理完成」
  三段，不必逐本猜；表族形状不变（仍四表），ADR 0016 的稳定期读法继续成立。
- **代价**：`doc_kind` 是同步输入的派生物，本仓**无法**从自有数据重算——同步腿一旦不送 `roles`，
  新书即未声明并被拒（这是披露不是故障）；`last_curation_result` 只答「上一批的结局」，答不了
  「梳理到哪一步」（后者是读时派生视图，归另一枚并行请求）。
- **缓解**：派生纯函数与两值闭集各有单测；列清单 ↔ ORM 由固定文档守护逐列对账；两列的写者各只有一处
  （同步 upsert／批次收尾与启动期归一），各带一条会红的反向用例。
- **边界**：本决定不定义未声明种类的拒绝码位（归条文与契约面）；不定义「梳理中」的派生口径；
  不动共享 `qed_*`／`qt_*` 的任何形状。

## 取代

- 补充：[ADR 0016](0016-af-family-rebuild-into-four-tables.md) 决定 2 的当前态清单由「进度／结局／生效
  指针」三项扩为「进度／结局／生效指针／种类／梳理结局」五项，四源结构与「流水不作判据」不变。
- 明文不改判：ADR 0016 决定 10 ①（同步 upsert 禁写 `meta`）与决定 11（加列不加表）照旧。
- 取代：无。被取代：无。
