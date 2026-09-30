# 台账行三要素改造（ledger-row-three-elements）

状态：In Progress
任务类型：B
最后更新：2026-09-30
关联 ADR：[ADR 0013](../adr/0013-ledger-row-three-elements.md)（本壳的实施依据：成功标准＝完成时的具体终态、承载列＝唯一承载文档指针、行内禁日期）、[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载与进度单源——本壳只收紧其列名与列语义表述）、[ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md)（固定文档正文去日期化，同一纪律延伸到台账行）、[ADR 0011](../adr/0011-ledger-one-row-per-task.md)（一任务一行不变，请求接收标记改写入「任务」列）
关联设计：无代码面设计契约变更；治理面依据 [task-lifecycle.md](../standards/task-lifecycle.md)（列契约）、[doc-governance.md](../standards/doc-governance.md)
关联 Tracker：docs/trackers/todo.md（PLAN-022 镜像行；本轮改造的台账自身即验收面）
归档判定：Retain（含三轮列界/禁词/指针数读数与「补前删」零丢失核对记录，判例同 PLAN-021 的实施载体留档）

> **立项依据（第一轮，2026-09-30）**：用户裁决「我们最关键的是任务描述（我们到底要做什么事情）和
> 成功标准（固定的验收标准，怎么算完成），细节的证据/下一条件写入 plans/设计文档，只需要指引好文档
> 即可，按这个标准构建台账」。前一轮按「事项展开」改写台账行的方向据此修正。
>
> **立项依据（第二轮，2026-09-30 评审第一轮产出后再裁）**：① 承载列应当**只作指引**（plans/ 或
> design/ 文档），「描述与现状」本身要活在文档里；② 成功标准不得写成「S1~S6 逐条实施带契约测试且
> 全量门禁绿」这类**壳内验收判据的引用**，要写**具体实现终态**。两处经问答定形：承载列＝纯链接
> （来源标记挪进「任务」列）、成功标准＝禁词表＋≤80 字＋禁日期。
>
> **立项依据（第三轮，2026-09-30 评审第二轮产出后再裁）**：「承载应该主要是 plans/ 的设计文档，我们
> 根据承载的文档进行优化；任务列里还有对应的设计文档链接，这个应该放过去；理论上承载只一篇文档，避免
> 文档冲突。」据此把「承载」列定为**唯一承载文档指针（恰好 1 个链接）**：镜像行的壳链接从「任务」列
> 挪进「承载」列（链接文本＝壳 H1 标题），`design/`/`architecture/` 引用退回承载文档自身的「关联设计」
> 元数据，「任务／成功标准」两列不再出现文档链接。

## 目标与成功标准

**目标**：把 `docs/trackers/todo.md` 从「日期化流水的第二事实源」改回**纯导航表**——每行只回答
「做什么／完成时系统长什么样／去哪读」，现状、日期与验收判据只在承载文档里。

| # | 固定判据（本壳的验收面，与 todo 行的「成功标准」列无关） |
| --- | --- |
| S1 | todo 表七列为 `ID / 类别 / 优先级 / 状态 / 任务 / 成功标准 / 承载`，`test_tracker_governance.py` 的 `TODO_COLUMNS` 与表头一致且全绿 |
| S2 | 所有活跃行的「任务」列按承载计划工作项列出范围（做什么），无进度与裁决史、**无文档链接**；跨项目 `**请求接收…**` 标记在本列句首 |
| S3 | 「成功标准」列 ≤80 字、写完成时的**具体终态**，禁词（`门禁`/`收口`/`全绿`/`成文`）与 `S\d+~S\d+` 形壳内判据引用均被守护挡住 |
| S4 | 「承载」列＝**唯一承载文档指针**：剥去链接与分隔符后残余为空、无自然语言，且 markdown 链接数**恰好为 1**；镜像行指向自身壳（链接文本＝壳 H1 标题）、支线行指向 `../plans/*.md` 承载壳、长期行指向其治理文档 |
| S5 | 三列剥去 markdown 链接目标后无 `YYYY-MM-DD` 字面量；断言到位并做变异转红复验（五条各红一次） |
| S6 | 从行内删除的每一段现状/细节/日期叙述，改写前已在承载计划（`plans/`）、`completed.md` 或 `project-status.md` 安家——逐行核对零丢失 |

## 范围与非目标

**范围**
1. `docs/adr/0013-ledger-row-three-elements.md`（含第二轮修订文本）与 `docs/adr/index.md` 登记行。
2. `docs/standards/task-lifecycle.md`「列契约」「一任务一行」「进度单源」「计划生命周期」第 2/4 步。
3. `tests/contract/test_tracker_governance.py`：`TODO_COLUMNS`、`test_success_criteria_are_concrete_and_bounded`
   （列界＋禁词＋S 区间）、`test_pointer_column_is_a_single_document_link`（唯一指针＋链接只登记在此列）、
   `test_rows_are_date_free_and_carrier_reference_registered`（禁日期＋承载引用只在「承载」列查）、
   镜像与主线聚合行判定改按 ID 前缀 `PLAN-*`（不再依赖「任务」列的链接前缀）。
4. `docs/trackers/todo.md`：节首段、表头、「规则」节 + v1.0 主线 6 行 + 治理轮 1 行 + 长期 2 行。
5. 措辞同步：`docs/standards/doc-governance.md` 三处（承载列口径两处、「三列（任务／成功标准／承载）」一处）、
   `docs/trackers/project-status.md` 的 PLAN-022 条目、本地技能 `.qoder/skills/qed-{intake,plan,closeout}`。

**非目标**
- `completed.md` 已关闭行、`docs/history/**` 当时记录、ADR 0006/0007/0011 原文保留旧列名叙述
  （ADR 0013 后果节明示不回改）。
- [PLAN-018](2026-09-26-parsing-region-edit-typography-request.md)、[PLAN-019](2026-09-28-parse-artifact-trust.md)
  壳内正文（并行会话的活跃壳，本轮不动，只从 todo 行把现状退回它们已有的表格）。
- 不改任何 `src/` 代码、不改数据根、不触碰真实库与磁盘。
- 「本轮主攻」仍为 PLAN-018 侧，本壳是治理支线且只动 `docs/` 与 `tests/contract/`。

## 前置条件

1. ADR 0013 为 Accepted（用户 2026-09-30 两轮指令即为确认；第二轮在同批未提交前修订本文）——满足。
2. 并发上限：本壳行按 `PLAN-*` 镜像行登记为**主线**（不占支线 `In Progress` ≤3 名额）——满足。
3. 守护先行：每轮改契约后、todo 未改前必须**实测转红**（第一轮＝表头不匹配 ⇒ todo 行为空；
   第三轮＝指针数 2≠1 与镜像行「承载」列非 `plans/` 指针），红证据记入「台账历史归档」节——三轮均已取。
4. 工作树：改动只落本仓库；`tmp/_arch011_bak/*.bak`、`tmp/_r3_bak/` 与 `tmp/_mutation_report*.txt`
   为回滚锚点与原始读数（仓库根 `tmp/` 不入库，ADR 0012）。

## 工作项

| # | 工作项 | 状态 |
| --- | --- | --- |
| W0 | 勘察：旧列界只作用于 todo 行、`_table()` 按表头精确匹配 ⇒ 改列名即真红；镜像用例按 `fullmatch` 识别 ⇒ 需改前缀识别才能容纳「任务」列描述 | 完成 2026-09-30 |
| W1 | 立 ADR 0013（第一轮文本：三列语义、列界 80/100、禁日期、取代 ADR 0006 列名表述） | 完成 2026-09-30（被 W7 修订取代） |
| W2 | `docs/adr/index.md` 登记 0013 行、下一可用编号改 0014 | 完成 2026-09-30 |
| W3 | `docs/standards/task-lifecycle.md` 列契约与计划生命周期改写（第一轮） | 完成 2026-09-30（被 W7 修订） |
| W4 | `test_tracker_governance.py` 改到第一轮列契约，取真实红读数 | 完成 2026-09-30（红读数见下节） |
| W5 | `todo.md`「规则」节 + 8 个活跃行第一轮重写，并新增 PLAN-022 镜像行（治理轮节） | 完成 2026-09-30（被 W9 修订） |
| W6 | 第一轮措辞同步（doc-governance 三处「证据列」）＋ 门禁 ＋ 三条变异复验 | 完成 2026-09-30 |
| W7 | 第二轮修订：ADR 0013 文本（承载列改纯指针、成功标准改具体终态＋禁词、来源标记挪「任务」列）＋ `adr/index.md` 行 ＋ task-lifecycle 列契约 | 完成 2026-09-30 |
| W8 | `test_tracker_governance.py` 第二轮：列名改 `承载`，拆出 `test_success_criteria_are_concrete_and_bounded` 与 `test_pointer_column_is_links_only`，禁日期与承载引用合并为一例；先红后绿 | 完成 2026-09-30（红读数见下节） |
| W9 | `todo.md` 第二轮：表头 + 9 行「成功标准」改具体终态、「承载」改纯指针、节首段去现状叙述、「规则」节改写；固定文档与技能措辞同步 | 完成 2026-09-30（核对读数见下节） |
| W10 | 全量门禁 ＋ 五条变异复验（承载列塞现状／成功标准用禁词／成功标准超界／指针超 3／注入日期） | 完成 2026-09-30（**560 passed / 10 skipped** + ruff clean） |
| W11 | 第三轮修订 ADR 0013（承载＝唯一承载文档指针、任务列不放链接、镜像判定按 ID 前缀）＋ 背景 6/7 两条判例 ＋ `adr/index.md` 行 | 完成 2026-09-30 |
| W12 | `task-lifecycle.md` 列契约／守护判据①~④／一任务一承载／计划生命周期第 2 步 ＋ `doc-governance.md` 三处 ＋ ADR 索引措辞 | 完成 2026-09-30 |
| W13 | `test_tracker_governance.py` 第三轮：`POINTER_LINKS_EXACT = 1`、新增「任务／成功标准」列禁链接断言、镜像与聚合判定改 ID 前缀、承载引用只查「承载」列；先取红读数 | 完成 2026-09-30（红读数见下节） |
| W14 | `todo.md` 第三轮：9 行「承载」改单指针、4 个镜像行的壳链接挪入「承载」列（链接文本＝壳 H1）、「任务」列去链接、节首段与「规则」节同步 | 完成 2026-09-30（`11 passed`） |
| W15 | 第三轮五条变异复验 ＋ 指针退回壳的零丢失核对 ＋ 全量门禁 ＋ 状态快照/技能/提交稿措辞同步 | 完成 2026-09-30（**560 passed / 10 skipped** + ruff clean） |

## 验证与验收

- 先红（每轮）：改守护后、todo 未改前运行 `pytest tests/contract/test_tracker_governance.py -q`，
  记录红色用例名；todo 未改前不得转绿。
- 后绿：行改写完成后同文件全绿，再跑 `pytest tests -q` + `ruff check src tests` 全量门禁。
- 变异复验（每轮五条各转红一次后当场还原，驱动脚本 `tmp/_plan022_mutations_r2.py`、
  `tmp/_plan022_mutations_r3.py`）：第二轮＝① 承载列加「W2 待开工」散文；② 成功标准加「全量门禁绿」；
  ③ 成功标准撑过 80 字；④ 承载列第 4 个链接；⑤ 任务列注入 `2026-09-30`。第三轮＝① 承载列加第二篇
  链接；② 文档链接挪回「任务」列；③ 承载列指针后追加现状散文；④ 镜像行「承载」列改指非 `plans/`
  文档；⑤ 成功标准注入日期。
- 零丢失核对：逐行列出本轮从 todo 删除的现状/裁决/读数片段，指向其在 `plans/`、`completed.md` 或
  `project-status.md` 的安家位置；核对表写入本壳「台账历史归档」节。
- 单测与契约测试只用临时目录，禁读写真实数据根（ADR 0009）。

## 回滚

- 全部为文档与测试文件改动，无数据落盘：以 `tmp/_arch011_bak/`（第一、二轮前）与 `tmp/_r3_bak/`
  （第三轮前，含 ADR 0013、task-lifecycle、doc-governance、todo、project-status、本壳、
  `test_tracker_governance.py` 与 `adr-index.md`）逐文件覆盖回滚，新文件（ADR 0013、本壳）直接移除并
  撤销索引登记。
- 不使用 `git checkout --`（共享工作树内有并行会话未提交内容）。

## 台账历史归档

**W4 先红读数（第一轮，改守护后、todo 未改前 ⇒ todo 行为空）**
`4 failed, 5 passed`——`test_task_ids_are_stable_unique_and_rows_are_complete`（新增 `assert todo`）、
`test_todo_plan_rows_exactly_mirror_active_plan_bodies`、`test_main_aggregate_rows_list_branch_children`、
`test_roadmap_is_status_free_and_references_registered_tasks`（注册集缺 `ARCH-012`）；行改写后 `9 passed`。
镜像识别由 `PLAN_LINK.fullmatch` 改为 `match`（链接在「任务」列开头，其后允许一句话范围与来源标记），
使 S2 与镜像守护共存。

**W8 先红读数（第二轮，列名 `承载/现状` ⇒ `承载`）**
同四处转红（`4 failed, 7 passed`，新增两例在空表下真空通过，故红证据取自上述四例）；
行改写后 `11 passed`。

**W13 先红读数（第三轮，指针数改 ==1 与镜像判定改 ID 前缀后、todo 未改前）**
`2 failed, 9 passed`——`test_pointer_column_is_a_single_document_link`
（`ARCH-021: 承载列指针数 2，一任务只有一个承载文档`，`assert 2 == 1`）、
`test_todo_plan_rows_exactly_mirror_active_plan_bodies`（镜像行的「承载」列唯一指针必须指向
`../plans/` 下的壳）。todo 行改写后同文件 `11 passed`。

**W14/W15 第三轮「补前删」零丢失核对（被移出台账的指针都在承载文档安家，实测存在）**

| 从 todo 行移出的指针 | 安家位置 | 核对 |
| --- | --- | --- |
| ARCH-021/023、PLAN-009 行的 `api.md` 与 `parsing-pipeline.md` | PLAN-009 壳「关联设计」行逐字列出 `parsing-pipeline.md`（R1~R4 与检索配方）与 `api.md`/`database-design.md`（R2 锚点契约）；设计条文本体在 `design/parsing-pipeline.md`（R1~R4／检索配方）与 `architecture/api.md`（`/api/search` 契约） | 命中 |
| ARCH-022 行的 `parsing-pipeline.md` | PLAN-019 壳「关联设计」行（`parsing-pipeline.md` 归一化与分块、`api.md` 只读端点、`database-design.md`/`parsing-management.md`） | 命中 |
| PLAN-018 行的 `parsing-pipeline.md`/`database-design.md`/`api.md` | PLAN-018 壳「关联设计」行三篇齐备（含「R3 区域投影→`parsing-pipeline.md`」「R1/R2 表与端点→`database-design.md`/`api.md`」逐条映射） | 命中 |
| PLAN-019 行的 `parsing-management.md`/`database-design.md` | PLAN-019 壳「关联设计」行（`database-design.md` 表结构与迁移链、`parsing-management.md` 任务/页状态） | 命中 |
| PLAN-022 行的 `ADR 0013`/`task-lifecycle.md` | 本壳「关联 ADR」「关联设计」两行 | 命中 |
| ARCH-010 行的 `code-map.md` | 该行「任务」列文本仍点名 `code-map.md` 五元组对账；对账规则本体在 `doc-governance.md`（本行新唯一指针） | 命中 |
| ARCH-012 行的 `PLAN-009` 指针 | `roadmap.md` 向量段「关联任务」列登记 PLAN-009/ARCH-012，知识图谱段写明「知识图谱暂不做（PLAN-009 非目标节，理由见其壳）」；PLAN-009 壳非目标节原文保留 | 命中 |
| 4 个镜像行「任务」列开头的壳链接 | 位置改变而非删除：同一链接现居「承载」列，链接文本＝壳 H1 标题，标题与 `状态：` 由镜像守护逐字比对 | 命中 |

**W5/W9「补前删」零丢失核对（每条被删叙述都在指向文档安家，实测存在）**

| 从 todo 行删除的内容 | 安家位置 | 核对 |
| --- | --- | --- |
| 「W0/W1 完成，W2~W9 待开始」「下一步＝悬置项逐条裁定后开 W2」（ARCH-022/PLAN-019 行） | PLAN-019 壳工作项表与 Q 表（含 `Q2/Q4/Q6-a/Q7/Q9~Q11` 与 `/health` 取证） | 命中 |
| 「本轮主攻」「W2 前置已解除（PLAN-020 关闭放行）」「W0~W0.6 完成」（PLAN-018 行） | `project-status.md` 当前主线条目；PLAN-018 壳 W2 行前置注记 | 命中 |
| 「P 系列已改挂 PLAN-019」「ARCH-011 承载节在本壳关闭并留档规程」（PLAN-009 行） | PLAN-009 壳 P 系列表指向与「承载 ARCH-011」节标题 | 命中 |
| 「S1~S6 逐条实施带契约测试且全量门禁绿」「R1~R6 逐条回执成文…六步收口」（原成功标准列） | PLAN-019 / PLAN-018 壳「验证与验收」与 S 判据表（原文未动） | 命中 |
| 「遗留＝②③ 两项待排」「③ 的实测读数见『下一轮候选 ⑧』」（ARCH-010 行） | `project-status.md` 下一轮候选段（②③ 与 ⑧ 条目均在文中）；①②③ 范围仍在该行「任务」列 | 命中 |
| 「已裁知识图谱暂不做（PLAN-009 非目标节）」（ARCH-012 行） | PLAN-009 壳非目标节；`roadmap.md` 关联任务列 | 命中 |
| 节首段的「当前三条实施线＝PLAN-018 在推／PLAN-019 待裁／ARCH-023 待排期」 | `project-status.md` 当前主线（主攻位与待裁清单的唯一登记处） | 命中 |

**W15 第三轮变异复验（原始读数留档 `tmp/_mutation_report_r3.txt`，当场还原复绿）**

| 变异 | 转红用例 | 结果 |
| --- | --- | --- |
| M1 ARCH-022 承载列加第二篇链接（`api.md`） | `test_pointer_column_is_a_single_document_link` | `1 failed, 10 passed` |
| M2 ARCH-023「任务」列注入 `api.md` 文档链接 | `test_pointer_column_is_a_single_document_link` | `1 failed, 10 passed` |
| M3 PLAN-019 承载列指针后追加「、W0/W1 已完成」散文 | `test_pointer_column_is_a_single_document_link` | `1 failed, 10 passed` |
| M4 PLAN-022 镜像行承载列改指 ADR 0013（非 `plans/`） | `test_todo_plan_rows_exactly_mirror_active_plan_bodies` | `1 failed, 10 passed` |
| M5 ARCH-010 成功标准注入 `2026-09-30` | `test_rows_are_date_free_and_carrier_reference_registered` | `1 failed, 10 passed` |
| 全部还原 | — | `11 passed` |

**W6/W10 变异复验（原始读数留档 `tmp/_mutation_report.txt`、`tmp/_mutation_report_r2.txt`，
仓库根 `tmp/` 不入库，ADR 0012；变异当场还原并复绿）**

| 变异（第二轮） | 转红用例 | 结果 |
| --- | --- | --- |
| M1 承载列混入「W2 待开工」散文 | `test_pointer_column_is_links_only` | `1 failed, 10 passed` |
| M2 成功标准加「全量门禁绿」 | `test_success_criteria_are_concrete_and_bounded` | `1 failed, 10 passed` |
| M3 成功标准撑过 80 字 | `test_success_criteria_are_concrete_and_bounded` | `1 failed, 10 passed` |
| M4 承载列第 4 个链接 | `test_pointer_column_is_links_only` | `1 failed, 10 passed` |
| M5 任务列注入 `2026-09-30` | `test_rows_are_date_free_and_carrier_reference_registered` | `1 failed, 10 passed` |
| 全部还原 | — | `11 passed` |

第一轮三条（指引列撑过 100 字／任务列注入日期／支线行删 `../plans/` 承载引用）各 `1 failed, 8 passed`，
还原后 `9 passed`。

## 关闭与归档

- 关闭条件：S1~S6 逐条判定成文，W11~W15 完成且全量门禁绿（S4 现按「恰好 1 个链接」判定）。
- 收口按 ADR 0006 六步：结论入本壳 → todo 行关闭并原子迁移 → `completed.md`（判据与读数安家）→
  `plans/index.md` 登记 Retain 去处 → `project-status.md` 刷新 → 门禁全绿。
- 归档判定 Retain：本壳的三轮列界/禁词/指针数读数与两轮零丢失核对是列契约的判例来源，供后续
  规范化整理轮复用。
