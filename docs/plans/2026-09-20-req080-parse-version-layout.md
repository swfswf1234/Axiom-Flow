# 解析产物按版本划分：versions/&lt;job_id&gt; 落盘与生效指针（req080-parse-version-layout）

状态：Blocked
任务类型：B
最后更新：2026-09-21
关联 ADR：[ADR 0002](../adr/0002-pending-design-location.md)（待评审设计先入 plans/）、[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型边界与产物落点）、根仓库 [ADR 0014](https://github.com/QED-Engine/qed-engine/blob/main/docs/adr/0014-parsing-ownership-and-model-boundary.md)
关联设计：[parsing-pipeline.md](../design/parsing-pipeline.md)、[parsing-management.md](../design/parsing-management.md)、[database-design.md](../architecture/database-design.md)、[api.md](../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-004、REQ-080、ARCH-001/003/004/005/006、PLAN-003、DEFECT-001/003/004/005）
归档判定：Retain（版本布局的推导依据、根仓库需求对表和未决清单需长期保留，计划壳归档 `docs/history/plans/2026-09/`）

> **一句话目标**：让「重新解析一本书」不再覆盖上一次的产物——每次解析 job 的产物独立落
> `versions/<job_id>/`，「当前生效版本」由数据库指针唯一决定，读取端点的路径与响应字面量保持不变。
> 本计划承接根仓库 **REQ-080**（`docs/trackers/todo.md` 中 REQ-080 行的实施方即本仓库）。

## 背景与需求拉通（根仓库 × 本仓库）

### 根仓库侧要求（只读引用，证据为 `path:line`）

| # | 要求原文要点 | 出处 | 对本计划的约束 |
| --- | --- | --- | --- |
| R1 | 每次解析 job 产物按版本独立落盘 `parsed/<d>/<c>/<book>/versions/<job_id>/`，内含该版本**完整产物**（页图 `pages/`、blocks、markdown、manifest.json），job 完成校验通过后**原子写入** | `../docs/design/dataset-conventions.md:64-67` | 版本目录必须自包含（含页图）；「原子写入」需在 P2~P4 找到不推翻页级持久化的实现方式 |
| R2 | `<book_id>/` 根目录即**当前生效版本**；读取方（8902 页/块/manifest 端点、8900 代理、前端）**只认根目录**，路径与端点契约不变；新版本生效时**原子切换**；`af_parse_jobs` 记录生效版本指向 | `../docs/design/dataset-conventions.md:68-70` | 不变量是「端点契约不变 + 切换原子」；根目录是否物理承载文件未被证明是必要条件（见 R6） |
| R3 | 历史版本保留供对比与回滚；**旧版本清理属 D 类数据操作**（备份 + 用户确认），不自动删除 | `../docs/design/dataset-conventions.md:71` | 本计划不做清理；只保证「可列举、可直读、不自动删」 |
| R4 | domain 为空时省略层级，版本化规则不受影响 | `../docs/design/dataset-conventions.md:72` | 版本目录挂在 `book_dir_for()` 结果之下，与层级省略正交 |
| R5 | 规则事实源：写盘先临时文件、校验通过后原子落盘（`:31`）；`tmp/` 不保留终态文件、任务结束清理（`:30`）；`tmp/<project>/` 不跨项目读取（`:32`）；`raw/` 不可变、先落盘后登记（`:37-40`）；测试不得触碰真实数据根（`:44-45`）；**改动数据根/三区/tmp/原子落盘规则需先立 ADR**（`:62`） | `../docs/standards/storage-conventions.md` | `:62` 与 `:44-45` 是**根仓库自身**的治理条款，对本仓库不具直接约束力：本仓库改数据布局走 `AGENTS.md`「变更分级」的大修改路径（todo + `plans/` 计划 + `architecture`/`design` 同步），**不需要新增 ADR**（2026-09-20 用户裁决）。`tmp/` 不保留终态文件的要求由 PLAN-003 条文 12 以更严格方式满足（不产生中间产物） |
| R6 | 8900 后端**唯一**的磁盘读取是源 PDF（`backend/qed_engine/api/axiom.py:148-170`），页数据/manifest/页图全部经 8902 HTTP 代理（`axiom.py:189-225`，契约 `../docs/architecture/api-contracts.md:558-585`）；前端不碰文件（`web-ui/src/api/axiom.ts:173,199-200`） | 根仓库代码与契约 | R2 的「只认根目录」实质是**只认端点路径** ⇒ 指针式布局成立 |
| R7 | 8902 返回的 `image_url` 会被 8900 重写为自身代理 URL（`axiom.py:197-202`），但字面量 `/api/v1/books/{id}/pages/{no}/image` 必须保持 | 根仓库代码 | 端点内 URL 组装不得改为绝对路径或带版本前缀 |
| R8 | `GET /books/{id}/manifest` 的路径字段被 8900 **原样透传**给前端（`axiom.py:206-209`；`api-contracts.md:577`「文件路径/大小/哈希」） | 根仓库代码 | manifest 是唯一可能把 `versions/<job_id>/` 泄漏到前端的出口 → 条文 V5 定为版本目录内相对路径 |
| R9 | REQ-080 关闭条件 = 本方确认约定 + **实施回执**；现状「覆盖落盘、无版本留痕」 | `../docs/trackers/todo.md:32` | 关闭需要向根仓库发回执（P7），并说明与 R2 字面措辞的实现差异 |
| R10 | 根侧明确未规定：切换机制、页图归属、指针列名与语义、manifest 基准目录、版本对比/回滚 API、保留数量与清理策略、失败半成品处置、局部重解析是否新建版本、Windows/WSL 目录交换限制 | 上述文档 grep 无相关条文 | 全部由本计划裁决（条文 V1~V10），随回执请根仓库确认 |

### 本仓库侧要求（现状实现约束）

| # | 现状约束 | 证据 | 对方案的作用 |
| --- | --- | --- | --- |
| L1 | 条文 3（PLAN-003）不变式：**产物写先于页行提交**、每页成功即 `commit`、重启按 `af_pages` 续跑「无行 ∪ failed」 | `orchestrator/pipeline.py:246-257`、`docs/design/parsing-pipeline.md:187` | 排除「先在别处攒一份、成功末尾再原子搬运到版本目录」的做法 → 版本目录必须**从第一页起就是写入目标** |
| L2 | 页级持久化与 `progress` 节流刚在 W2 落地并有守护用例 | `tests/unit/test_orchestrator.py` | 版本改造必须让这批用例继续成立（改判据不删判据） |
| L3 | ingest 幂等实现是 `rmtree(book_dir)` + 临时目录 `rename`，会连带删掉 `versions/` | `ingest/importer.py:82-117` | 必须改为只交换 `book.json` + `pages/`（条文 V8） |
| L4 | W6 幂等回置：`parse_status=pending`、`pages_done=0` + `reset_for_book()` **删除全书 af_pages 行** | `api/main.py` ingest 分支、`db/repositories.py` | 版本化后页行按 job 作用域，删全部行会毁掉历史版本状态 → 改为置空指针（条文 V8） |
| L5 | `af_pages` 主键 `(book_id, page_no)`；`af_block_edits` 唯一键 `(book_id, page_no, block_index)`，`edit_id = md5(book:page:index)` | `db/models.py:67-97` | 版本作用域必须动这两张表的键（条文 V7）；W6 明确挂账的「块下标 remapping」在本计划收口 |
| L6 | `build_manifest()` 用 `book_dir.rglob("*")`，只排除 `manifest.json` 与隐藏文件 | `ingest/artifacts.py:106-122` | `versions/` 挂在 `book_dir` 之下会把历史版本全部计入根清单（性能与语义双重错误）→ 必须显式排除 |
| L7 | 页图由 ingest 产出，逐页解析路径直接读 `book_dir/pages/p<NNNN>.png`；486 页书约 100MB 量级 | `pipeline.py:79,272`、真机 ARCH-015 证据 | 「每版本完整复制页图」按版本线性放大磁盘 → 用硬链接自包含（裁决 2） |
| L8 | `tmp/axiom-flow/<job_id>/` 暂存目录：经 PLAN-003 条文 12 改判为「设计表述错误」——引擎中间产物只在内存，本就不写数据根 | PLAN-003「W7 完成登记」发现 1 与其条文 12 | 本计划**不实现** tmp 暂存；改为约束：版本目录之外不得产生中间产物，`tmp/axiom-flow/` 存在即缺陷 |
| L9 | 真机存量（2026-09-20 取证轮之后）：`af_books` 4 行、`af_parse_jobs` 9 行（含 2 行遗留 `running`）、`af_pages` 39 行（`linearalgebra-b01` 第 3~40 页 `parsed` + 第 999 页取证注入的 `failed` 幽灵行）、`af_block_edits` 0 行；产物根目录已有页图 + blocks/md + book.json/manifest/chunks | `data/probes/live-bringup-20260920/summary.md`、PLAN-003「收尾执行登记」 | 存量收编成本仍低，但**收编前需清幽灵行**（D 类运行库写操作，待批准）；「不自动迁移」原则（`storage-conventions` / `dataset-conventions:104-106`）可无损成立 |
| L10 | ~~解析从未真机成功~~ **已证伪**（2026-09-20 PLAN-003 收尾轮：真书单页 L2、整本 5 页小样 L3、失败注入 L5、kill 续跑 L6 全绿） | PLAN-003「收尾执行登记」C1 表 | 本计划的版本布局改造可**直接在真机链路上取证**（不必只靠 fake engine）；但仍先用 fake engine + 仓库内临时数据根做默认门禁，真机证据并入 PLAN-003 8.1 冒烟固化 |

## 已裁决事项（2026-09-20 用户）

1. **生效机制 = 指针式**：产物只落 `versions/<job_id>/`，`<book_id>/` 根目录只保留 ingest 级
   `book.json` 与页图正本；「哪一版生效」由数据库指针决定，8902 内部把端点路径解析到版本目录。
   端点路径与响应字面量不变（满足 R2 的不变量，不满足其字面措辞，随 R9 回执请根仓库确认）。
2. **页图归属 = 硬链接自包含**：版本目录内 `pages/p<NNNN>.png` 用 `os.link` 指向根页图正本，失败回落
   复制并告警；既满足根侧「版本目录含页图」，又不产生 100MB×N 的重复。
3. **DB 作用域 = 页状态随版本 + 块编辑随版本**：`af_pages` 主键与 `af_block_edits` 唯一键纳入
   `job_id`（一次 Alembic 迁移）。新版本默认不继承人工编辑（继承/复制属新契约，另任务裁决）。
4. **顺序 = PLAN-004 自闭环验证**：本计划不依赖真机模型服务，全部用 fake engine + 临时数据根；
   真实磁盘与真机证据统一并入 PLAN-003 W8（PLAN-003 收尾三块先行，本计划挂起在其之后）。

## 阻塞与恢复

阻塞证据：前置条件 1 未达成——PLAN-003 的 **C1 解析真机打通已达成**（2026-09-20 收尾轮 L1~L8
全绿，见其「收尾执行登记」）、**C3 数据根收口面已于 2026-09-21 关闭**、**W8 全部完成**（8.1 冒烟用例
真机绿 + 8.4/W8-g 回执稿），**DEFECT-004（bbox 坐标空间）已于 2026-09-21 修复关闭**（版本目录内的块坐标
自此可信）；仍缺的是 PLAN-003 的 **C2 收口**：D3 真机删除演练需用户批准、D5/W3-b 受
[ADR 0005](../adr/0005-mysql-integration-test-isolation.md) Draft 评审阻塞，另 **DEFECT-005（块类型字面值）
仍会改变 `blocks.json` 内容口径**（类型丢弃致块数与 `index` 变化），须在其修复后再固化版本布局，否则
版本比对基准本身不稳。此外前置条件 3（真机 `qed` 库四表迁移窗口）未获用户批准，P2 无法取证；真机存量还含
取证注入的 `af_pages` 幽灵行（第 999 页），收编前需清理授权。

恢复条件：PLAN-003 关闭条件（见其「收尾执行设计」一节）全部达成并留档；DEFECT-005 关闭；用户批准
`alembic upgrade/downgrade` 窗口与 `af_*` 备份方案；用户对本计划 V1~V10 与裁决 1~4 转 Accepted。

责任位置：本仓库（PLAN-003 D3/W3 执行与收口、DEFECT-005 修复）；用户（ADR 0005 评审、真机窗口排期、本计划评审）。

复核触发点：DEFECT-005 关闭时；或 PLAN-003 的 D3/D5 达成时；或 ARCH-004/005/006 需要「同一本书多版本产物」维度
（提前恢复本计划并向用户申请窗口）；或根仓库对 REQ-080 措辞提出新要求时。

## 目标与成功标准

| # | 成功标准 | 判据 |
| --- | --- | --- |
| S1 | 同一 book 连续两次解析后，第一次的产物仍完整可读（按 `job_id` 直读版本目录），根视图只反映生效版本 | 契约用例：两次 job → 两版本目录并存 + 端点返回生效版内容 |
| S2 | 激活是单列 UPDATE，不搬运/不复制文件；崩溃在中途不可能产生「半生效」视图 | 条文 V3 + 用例：写完最后一页前 kill，`active_job_id` 仍指旧版 |
| S3 | 页级持久化与断点续跑语义（PLAN-003 条文 3）在版本目录上继续成立 | W2 既有判据全绿 + 新增「续跑落在同一 job 版本目录」用例 |
| S4 | 端点契约零破坏：路径、`image_url` 字面量、manifest 相对路径基准均不变 | 契约测试断言字符串常量 + R7/R8 用例 |
| S5 | 数据库迁移可升可降，四表语义与 `database-design.md` 一致，存量行为无损（不自动迁移旧数据） | `test_db_migrations.py` 升降级 + backfill 用例 |
| S6 | 重复 ingest / purge 不再毁版本历史，且解析状态回置语义明确 | 条文 V8 + 更新后的 W6 守护用例 |
| S7 | REQ-080 实施回执发出（含与根措辞的差异点与需根仓库确认清单），PLAN-003 W8 待办同步更新 | `completed.md` REQ-080 行证据 + 回执稿留档 |

## 范围与非目标

**范围内**：版本目录布局与写入、生效指针与 DDL、8902 读取路径解析、manifest 基准改造、
ingest/purge 与版本的交互、只读版本列举端点、文档晋升与回执。

**非目标（明确不做）**：

- 旧版本清理、磁盘配额、自动删除（R3：D 类数据操作，另立任务并经用户确认）。
- `<data_root>/tmp/axiom-flow/<job_id>/` 暂存落地（2026-09-20 改判为设计表述错误，见 V10）。
- 人工编辑跨版本继承/复制、块下标 remapping 到新版本的映射算法（只保证「编辑绑定其所属版本」）。
- 版本对比 UI 与**切换/回滚写入端点**（指针式下切换只是一条 UPDATE，但仍属人工数据操作，
  待根仓库/前端提出契约后再开任务；本计划只提供只读 `GET /books/{id}/versions`）。
- 修改根仓库任何文件；8900/前端侧改造（只发回执与确认请求）。
- 实现 paddleocr-vl / qwen-vl 引擎本体；Rudin 全量验收（ARCH-009）；`release` 合并回 `main`。

## 前置条件

1. PLAN-003 收尾三块（C1 真机打通 / C2 数据库存储收口 / C3 数据根收口）完成，且 W7 已晋升的条文
   1~9/11 保持自洽；本计划在收口后的基线上改布局。
2. 数据布局改动的治理入口为本仓库 `AGENTS.md`「变更分级与边界」大修改路径（todo + `plans/` 计划
   + `architecture`/`design` 同步），**不需要新增 ADR**（2026-09-20 用户裁决；根侧
   `storage-conventions.md:62` 只约束根仓库自身）。
3. 真机 `qed` 库四表变更需用户批准备份与 `alembic upgrade/downgrade` 窗口（与 ARCH-013 同流程）；
   未获批前，改动只在本机 SQLite 与临时目录层面成立，不推真机。
4. 根仓库保持只读；需求对表以本计划「背景」一节登记的 `path:line` 为准。

## 契约条文草案（晋升输入，ADR 0002）

| # | 条文（新契约成文，实现后晋升） | 晋升目标 |
| --- | --- | --- |
| V1 | **版本目录唯一落点**：解析产物只落 `<book_dir>/versions/<job_id>/`，内部布局与 ingest 根布局同构（`pages/p<NNNN>.{png,md,blocks.json}`、`chunks.json`、`manifest.json`）。`job_id` 为 `uuid4().hex[:12]`，仍须过 `ensure_safe_segment()` 纵深防御。版本目录从**第一页产物写入起**就是目标目录（不存在「攒完再搬」阶段）。 | `design/parsing-pipeline.md` |
| V2 | **生效指针唯一事实源 = `af_books.active_job_id`**（`NULL` = 根视图，即 legacy）。`af_parse_jobs` **不**存布尔「生效」列，避免同一事实两处漂移；「哪一版生效」经 `af_books` 单行读取即可判定。 | `architecture/database-design.md` |
| V3 | **激活语义**：任务终态为 `completed` 且版本目录校验通过（页数齐备 + `manifest` 重算成功）后，才允许把 `active_job_id` 指向该 job；激活 = 单列 UPDATE。未激活版本（`failed`/中断/校验不过）的文件保留在 `versions/` 下供排查，**不自动删除**，且永远读不到（端点只认指针）。 | `design/parsing-pipeline.md`、`architecture/api.md` |
| V4 | **页图自包含 = 硬链接**：写页产物前，把根 `pages/p<NNNN>.png` 以 `os.link` 链入版本目录；不支持硬链接（跨卷/文件系统限制）时回落 `copy2` 并 `WARNING`。重复 ingest 替换根页图**不影响**既有版本目录的链接内容，故历史版本的 bbox 基准不失真。 | `design/parsing-pipeline.md` |
| V5 | **manifest 基准 = 版本目录内 POSIX 相对路径**（如 `pages/p0001.blocks.json`），**不含** `versions/<job_id>/` 前缀（防经 8900 透传泄漏到前端）。`book_dir` 根的 `manifest.json` 只描述 ingest 级文件，且 `build_manifest()` 必须排除 `versions/` 子树与隐藏临时文件。 | `architecture/api.md`、`design/parsing-pipeline.md` |
| V6 | **读取解析单一入口**：所有产物读取经「生效版本目录」解析函数（`active_job_id` 非空 → `versions/<job_id>/`；`NULL` → `book_dir` 根，legacy 视图）。五类读取端点（`pages/{no}`、`pages/{no}/image`、`manifest`、`chunks`、`blocks` 相关）的**URL 与响应字面量零变化**。 | `architecture/api.md` |
| V7 | **版本作用域**：`af_pages` 主键 → `(book_id, job_id, page_no)`；`af_block_edits` 唯一键 → `(book_id, job_id, page_no, block_index)`，`edit_id` 摘要纳入 `job_id`；`af_parse_jobs` 增 `source_sha256`、`page_count`（版本与源快照自证对应）；`af_books` 增 `active_job_id`。存量**不自动迁移**：旧 `af_pages`/`af_block_edits` 行的 `job_id` 在「该书只有一个 job」时回填该 job，否则填哨兵 `legacy` 且不再被端点读到。 | `architecture/database-design.md` |
| V8 | **ingest/purge 与版本的关系**：重复 ingest 只原子替换 `book.json` + `pages/`（staging 目录交换），`versions/` 完整保留；同时 `active_job_id = NULL`、`parse_status = pending`、`pages_done = 0`，并**不再删除**任何版本的 `af_pages` 行（历史版本状态随版本目录继续可查）。`DELETE /books/{id}` 删 `af_books` 与该书全部版本 `af_pages`/`af_block_edits` 行；`?purge=true` 连产物目录（含 `versions/`）一并删除；`af_parse_jobs` 保留为审计。 | `design/parsing-management.md`、`architecture/database-design.md` |
| V9 | **只读版本列举**：新增 `GET /api/v1/books/{book_id}/versions` → 每版本 `job_id/engine/status/page_count/parsed/source_sha256/created_at/active`；`GET /books/{id}` 响应增 `active_job_id` 字段（可空）。切换/回滚写入端点**不在本契约内**。 | `architecture/api.md` |
| V10 | **不引入数据根暂存**（2026-09-20 改判，取代原「暂存目录落地」条文）：引擎中间产物只在内存/临时文件生命周期内存在，落盘目标只有版本目录；`<data_root>/tmp/axiom-flow/` **恒不应存在，存在即缺陷**（PLAN-003 条文 12，比根侧 `storage-conventions.md:30-31`「任务结束清理」更严格）。本计划只新增约束性断言：版本目录之外不得产生中间产物。 | `design/parsing-pipeline.md`、`architecture/database-design.md` |

## 工作项

| # | 内容 | 产出 / 红灯判据 | 依赖 |
| --- | --- | --- | --- |
| P0 | 本计划转 Accepted（用户评审 V1~V10 与裁决 1~4）；治理入口按 `AGENTS.md` 大修改路径由本计划 + todo 承载，**不立 ADR**（2026-09-20 裁决） | 计划状态 Accepted；todo PLAN-004 行同步 | PLAN-003 收口（C1/C2/C3） |
| P1 | 向根仓库发**确认请求**（新 REQ 行）：指针式是否满足 R2「根目录即当前版」、manifest 基准（R8）、版本对比/回滚是否需要契约、人工编辑不随版本继承的可见性 | 跨项目请求登记（本方 todo + 回执稿） | P0 |
| P2 | Alembic 迁移 `20260920_0002`：`af_books.active_job_id`、`af_parse_jobs.source_sha256/page_count`、`af_pages` PK 重建、`af_block_edits` 唯一键与 `edit_id` 重建 + backfill（含 `legacy` 哨兵）；`db/models.py`、`repositories.py` 同步 | `test_db_migrations.py` 升/降 + backfill 三例先红后绿 | P0 |
| P3 | `ingest/artifacts.py`：新增 `version_dir_for()`（含 `ensure_safe_segment(job_id)`）、`link_pages_into()`（`os.link` 回落 `copy2`）、`build_manifest()` 排除 `versions/`；`BookRepository.set_active_job()` 仓储方法 | 单元红灯：根 manifest 不含 `versions/`；越界 `job_id` 报错；硬链接与根页图同 inode（不支持平台跳测） | P2 |
| P4 | `orchestrator/pipeline.py`：产物写版本目录、建页图硬链接、job 行写 `source_sha256/page_count`、终态校验后激活（单列 UPDATE）；保持条文 3 的页级提交与节流顺序；中间产物不落数据根（V10） | 红灯：未 completed 不激活；kill 中途旧指针不变；续跑写入同一版本目录；数据根无 `tmp/axiom-flow/` | P3 |
| P5 | `api/main.py`：读取路径统一走生效版本解析（含 `active_job_id IS NULL` 的 legacy 回落）、`GET /books/{id}/versions`、`BookOut.active_job_id`、提交任务不再隐式依赖根目录产物清单 | 契约用例 S1/S4/S6；`image_url` 与 manifest 字面量常量断言 | P4 |
| P6 | ingest/purge 改造（V8）：`ingest_book()` 只交换 `book.json` + `pages/`、保 `versions/`；API 侧不再调 `reset_for_book()` 删全部页行，改置 `active_job_id=NULL`；purge 删含 `versions/` 的整本目录 | 红灯：ingest→解析→再 ingest 后版本目录仍在、根视图 404、历史版本按 `job_id` 可直读 | P4 |
| P7 | 文档晋升与联动：`parsing-pipeline.md`（产物树/版本目录/V10）、`parsing-management.md`（V8/端点表）、`api.md`（V5/V6/V9）、`database-design.md`（V2/V7 + Mermaid + DDL）、`code-map.md`、`guides/operations.md`；销账 PLAN-003「W7 完成登记」发现 2（`engine` 死默认值随本迁移一并清理）；发现 1（`tmp/`）已由条文 12 改判，随 V10 一并成文 | 文档守护三件套 + `test_plan_governance.py` 绿 | P2~P6 定型 |
| P8 | 门禁与收口：全量 `pytest` + `ruff`；REQ-080 实施回执稿（含差异点与待确认清单）、PLAN-003 W8 待办补「新布局上重跑冒烟」、`project-status.md` 刷新 | S1~S7 证据齐备后关闭 | P7 |

台账联动：REQ-080 于 P8 关闭（回执发出且根仓库确认）；ARCH-004（分块）与 ARCH-005/006（对照/编辑）
的产物读取前置在本计划解除；DEFECT-003 的「块下标 remapping」挂账项随 V7 收口；PLAN-003 W7 登记
的发现 1（`tmp/axiom-flow/`）改判为条文 12（不实现），发现 2（`af_parse_jobs.engine` 死默认值）在 P7 销账；
PLAN-003 W8 的复核触发点在 P8 更新为「必须在版本布局上取证」。

## 验证与验收

- **TDD**：P2~P6 每项先写红灯（迁移升降级、manifest 排除、硬链接、激活门槛、legacy 回落、
  ingest 保版本），再实现转绿；不改写 W2/W6 既有判据，只补版本维度断言。
- **隔离铁律**：全部用 `tmp_path` 假数据根 + fake engine；不连模型服务、不写真实数据根、
  不碰 `qed` 业务库（真机迁移只在用户批准的窗口执行，属 D 类动作，需确认）。
- **门禁命令**（`QED_env` 解释器）：
  `D:/software/anaconda3/envs/QED_env/python.exe -m pytest tests -q`（基线 283 passed）
  `+ -m ruff check src tests scripts`；文档触发项追加
  `tests/contract/test_architecture_documents.py`、`test_design_documents.py`、
  `test_code_document_mapping.py`、`test_plan_governance.py`、`test_tracker_governance.py`。
- **磁盘与性能**：单版本新增体积 = blocks+md+chunks+manifest（MB 级），页图经硬链接零复制；
  用例断言「二次解析后根目录页图数量不翻倍」以钉住回归。
- **跨平台**：`os.link` 在 Windows/NTFS 无特权要求、POSIX 同；非硬链接文件系统回落复制路径要有
  单测覆盖（monkeypatch 抛 `OSError`）。

## 回滚

- **代码**：P3~P6 按 commit 逐个 `git revert`；无自动兜底、无静默降级（ADR 0004 铁律）。
- **布局天然回滚**：`active_job_id` 恒为 `NULL` 即退回当前（根视图）行为，不删任何数据；
  这是选择指针式的主要风险对冲。
- **迁移**：`20260920_0002` 提供 `downgrade()`（删新增列、恢复旧主键/唯一键）。真机 downgrade
  属 D 类操作：先备份 `af_*` 四表（沿用 ARCH-013 与根侧 `backups/` 流程）→ 用户批准 → 执行；
  版本目录产物不随 downgrade 删除（DB 与文件解耦）。
- **文档**：本计划若被否决（状态转 Cancelled/Superseded），条文 V1~V10 全部撤回 plans/，
  `design/architecture` 保持 PLAN-003 W7 晋升后的状态不回退。

## 关闭与归档

- **关闭条件**：S1~S7 全达成；REQ-080 回执发出并获根仓库确认（或按其反馈改判）；迁移在真机
  执行成功（或明确挂到 W8 同一窗口执行并在 todo 留恢复条件）。
- **归档判定 Retain**：根仓库需求对表（R1~R10）、现状约束（L1~L10）与条文推导需保留，
  计划壳归档 `docs/history/plans/2026-09/`。
- **收尾动作**：todo 的 PLAN-004/REQ-080 行原子迁移 `completed.md`；`project-status.md` 能力表
  补「产物按版本」；`code-map.md` 的 `ingest/`、`orchestrator/`、`db/` 行同步版本解析职责；
  PLAN-003「收尾执行设计」的 W8 复核触发点保持有效。
