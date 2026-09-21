# 解析产物按版本划分：versions/&lt;job_id&gt; 落盘与生效指针（req080-parse-version-layout）

状态：In Progress
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
5. **P0 达成与开发/真机解耦（2026-09-21 用户裁决）**：用户选择「解除过度阻塞并开工实施」，V1~V10 与
   裁决 1~4 视为评审通过 → 本计划转 `In Progress`，P2~P6 **立即在本地自闭环实施**；同时裁决
   **REQ-081 的 b01 受害页重解析不在旧（扁平）布局上重复执行**，改到版本布局落地后一次到位（见「真机取证
   窗口与恢复」
   的 B→C→D 单窗口顺序）。**依据**：裁决 4 已排除真机依赖，而 `tests/unit/test_db_migrations.py:18-33` 证明
   Alembic 升降级可在 SQLite 临时文件取证，`api/main.py:140-144` 与 `src/axiom_flow` 全量 grep 证明
   **应用不自动迁移**（无 `create_all`/`upgrade` 启动路径，迁移是运维命令）⇒ P2~P6 的代码合入对共享
   `qed` 库零写入，原「前置条件 1 = PLAN-003 全部收尾」把外部依赖错误地当成了开发前置。

## 真机取证窗口与恢复

> 本节只覆盖**真机面**：2026-09-21 裁决 5 后，P2~P6 的开发与门禁取证不再受阻（在 SQLite 临时库 +
> `tmp_path` 数据根 + fake engine 上自闭环），下表所列项全部是「实施完成后的取证/关闭」前置。

阻塞证据（**2026-09-21 R1 窗口后判定：真机面三项已全部达成，本节保留为原推导记录**）：
开发与门禁取证面无阻塞——P2 的 Alembic 升降级与 backfill 判据沿用既有 SQLite 临时库模式
（`tests/unit/test_db_migrations.py:18-33` 的 `command.upgrade/downgrade`），P4/P5 用 `FakeEngine` + 内存库 +
临时数据根（`tests/unit/test_orchestrator.py`、`tests/contract/test_api_v1_contract.py`）；归一化前置
DEFECT-004（坐标空间）与 DEFECT-005（类型字面值与可见丢弃）已于 2026-09-21 关闭，版本目录内的块坐标、
块类型与 `index` 自此可信（比对基准无已知漂移源）。**曾受阻的真机面三项现状**：① 真机 `qed` 库
`alembic upgrade 20260920_0002` ✅ 已在新代码同窗口执行（`alembic_version` 自证于 `S1_before.json`）；
② PLAN-003 的 D3（真机删除演练）**仍未执行**（属 PLAN-003 关闭条件，不阻塞本计划）；
③ `af_pages` 第 999 页幽灵行 ✅ 由 D-1 re-ingest 的 `reset_legacy()` 整批作废为 0 行（原计划的手工
DELETE 授权因此取消）。
另 PLAN-003 的 D5/W3-b 受 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md) Draft 评审阻塞，
**该阻塞已于 2026-09-21 解除**（ADR 0005 接受 + `tests/integration/` 落地）。

真机执行顺序（2026-09-21 裁决 5，最小化真机功与失效证据）：

| 步 | 内容 | 为什么这个顺序 |
| --- | --- | --- |
| B | 本地实施 P2~P6 并全量门禁绿（fake engine + SQLite + 临时数据根） | 真机面零依赖，先把代码与迁移脚本定型 |
| C | **单一批准窗口**：`af_*` 四表备份 → 真机 `alembic upgrade 20260920_0002` → 释放新代码。**2026-09-21 R0 修订**：原「清 `af_pages` 幽灵行」的手工 DELETE 取消——D 步强制 re-ingest 的 `reset_legacy()` 只清 `job_id='legacy'` 一批，幽灵行随之作废，无需单独授权删除；窗口前取证改由脚本 `S1_before.json` 记录 legacy/幽灵行数 | 备份/迁移/发布必须同一窗口，否则新代码读旧表结构即故障 |
| D | 在**版本布局上**单次重解析 `linearalgebra-b01` 前 20 页（含根仓库报告的受害第 7 页），激活后兼作 PLAN-003 W8 取证。`scripts/tmp/live_reparse_b01_pages.py` **2026-09-21 R0 已重写为版本布局 + HTTP 驱动**（旧扁平路径版留档，见 I9）。**新增前置**：根仓库 2026-09-21 已清空 `dataset/parsed/` 的旧代际产物，故 D 步必先 `POST /books/{id}/ingest` 重建根视图页图，再提交解析任务 | 若先在旧布局重解析，V7 会把 b01 的存量 `af_pages`（9 个 job，非「一书一 job」）回填成 `legacy` 哨兵；而 legacy 只在 `active_job_id IS NULL` 时经根视图可读，一旦本窗口激活新版本即不再被端点读到，真机证据一步即作废；故**不在旧布局重复真机解析** |

恢复条件（2026-09-21 更新）：C 窗口与 D 步已于 R1 窗口执行成功（备份 17/10/39/1 行 → 迁移 → 发布 →
D-1 re-ingest → D-2 单次重解析并激活 `86ab56ae2b6c`）；REQ-080 回执随 commit `95e1d37` 发出。
**本计划当前的唯一等待项是根侧对「请确认清单」四条的回应**（外部责任：QED-Engine 根仓库），
不再是本方的取证或批准缺口。计划关闭另需 P7 文档晋升与 P8 回执（均 ✅）。

责任位置：本仓库（P1~P8 开发、门禁、真机窗口与回执稿——均已完成）；根仓库（四条确认，决定 REQ-080 与
本计划终态）；用户（PLAN-003 D3 批准，与本计划无关）。

复核触发点：C 窗口获批排期时；或根仓库对 REQ-080 / `dataset-conventions.md` 措辞提出新要求时；或
ARCH-005/006 需要「同一本书多版本产物」维度而要求提前取证时。

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

1. PLAN-003 的 **C1（真机打通小样）与 C3（数据根收口）已达成**、条文 1~9/11 已晋升且自洽（W7 完成），
   本计划在收口后的基线上改布局。**C2 的 D3/D5 只阻塞真机取证与本计划关闭**（并入「真机取证窗口与恢复」的
   B→C→D 单窗口），不阻塞 P2~P6 的 SQLite + `tmp_path` 开发与门禁取证（2026-09-21 裁决 5；证据见该裁决）。
2. 数据布局改动的治理入口为本仓库 `AGENTS.md`「变更分级与边界」大修改路径（todo + `plans/` 计划
   + `architecture`/`design` 同步），**不需要新增 ADR**（2026-09-20 用户裁决；根侧
   `storage-conventions.md:62` 只约束根仓库自身）。
3. 真机 `qed` 库四表变更需用户批准备份与 `alembic upgrade/downgrade` 窗口（与 ARCH-013 同流程）；
   未获批前，改动只在本机 SQLite 与临时目录层面成立，不推真机。
4. 根仓库保持只读；需求对表以本计划「背景」一节登记的 `path:line` 为准。

## 契约条文草案（晋升输入，ADR 0002）

> **晋升状态（2026-09-21，P7 完成）**：V1~V5/V10 已写入 `design/parsing-pipeline.md`
> （「解析版本与生效指针」节 + 产物树）、V2/V7 写入 `architecture/database-design.md`（DDL +
> Mermaid + 生效版本指针语义）、V3/V5/V6/V9 写入 `architecture/api.md`（生效版本读法 + `/versions`
> + 404 语义）、V6/V8 写入 `design/parsing-management.md`（版本隔离原则 + 端点表），运维面写入
> `guides/operations.md`（单窗口 runbook）。本节保留为条文**推导依据**（Retain），措辞以运行文档为准。

| # | 条文（新契约成文，实现后晋升） | 晋升目标 |
| --- | --- | --- |
| V1 | **版本目录唯一落点**：解析产物只落 `<book_dir>/versions/<job_id>/`，内部布局与 ingest 根布局同构（`pages/p<NNNN>.{png,md,blocks.json}`、`chunks.json`、`manifest.json`）。`job_id` 为 `uuid4().hex[:12]`，仍须过 `ensure_safe_segment()` 纵深防御。版本目录从**第一页产物写入起**就是目标目录（不存在「攒完再搬」阶段）。 | `design/parsing-pipeline.md` |
| V2 | **生效指针唯一事实源 = `af_books.active_job_id`**（`NULL` = 根视图，即 legacy）。`af_parse_jobs` **不**存布尔「生效」列，避免同一事实两处漂移；「哪一版生效」经 `af_books` 单行读取即可判定。 | `architecture/database-design.md` |
| V3 | **激活语义**：任务终态为 `completed` 且版本目录校验通过（页数齐备 + `manifest` 重算成功）后，才允许把 `active_job_id` 指向该 job；激活 = 单列 UPDATE。未激活版本（`failed`/中断/校验不过）的文件保留在 `versions/` 下供排查，**不自动删除**，且永远读不到（端点只认指针）。 | `design/parsing-pipeline.md`、`architecture/api.md` |
| V4 | **页图自包含 = 硬链接**：写页产物前，把根 `pages/p<NNNN>.png` 以 `os.link` 链入版本目录；不支持硬链接（跨卷/文件系统限制）时回落 `copy2` 并 `WARNING`。重复 ingest 替换根页图**不影响**既有版本目录的链接内容，故历史版本的 bbox 基准不失真。 | `design/parsing-pipeline.md` |
| V5 | **manifest 基准 = 版本目录内 POSIX 相对路径**（如 `pages/p0001.blocks.json`），**不含** `versions/<job_id>/` 前缀（防经 8900 透传泄漏到前端）。`book_dir` 根的 `manifest.json` 只描述 ingest 级文件，且 `build_manifest()` 必须排除 `versions/` 子树与隐藏临时文件。 | `architecture/api.md`、`design/parsing-pipeline.md` |
| V6 | **读取解析单一入口**：所有产物读取经「生效版本目录」解析函数（`active_job_id` 非空 → `versions/<job_id>/`；`NULL` → `book_dir` 根，legacy 视图）。五类读取端点（`pages/{no}`、`pages/{no}/image`、`manifest`、`chunks`、`blocks` 相关）的**URL 与响应字面量零变化**。 | `architecture/api.md` |
| V7 | **版本作用域**：`af_pages` 主键 → `(book_id, job_id, page_no)`；`af_block_edits` 唯一键 → `(book_id, job_id, page_no, block_index)`，`edit_id` 摘要纳入 `job_id`；`af_parse_jobs` 增 `source_sha256`、`page_count`（版本与源快照自证对应）；`af_books` 增 `active_job_id`。存量**不自动迁移**：旧 `af_pages`/`af_block_edits` 行的 `job_id` 在「该书只有一个 job」时回填该 job，否则填哨兵 `legacy` 且不再被端点读到。 | `architecture/database-design.md` |
| V8 | **ingest/purge 与版本的关系**：重复 ingest 只原子替换 `book.json` + `pages/`（staging 目录交换：新页图写临时目录 → 旧 `pages/` 改名让位 → 新目录上位 → 删除改名后的旧目录，避免出现「`pages/` 不存在」的读空窗），`versions/` 完整保留；同时 `active_job_id = NULL`、`parse_status = pending`、`pages_done = 0`，并**不再删除**任何版本的 `af_pages` 行（历史版本状态随版本目录继续可查）。页行失效范围只限 **`job_id = 'legacy'`** 一批（它们描述的正是刚被替换掉的根视图），真实 job 作用域行全部保留（2026-09-21 实现口径；否则 `reset_for_book()` 的全书删除会连带毁掉历史版本状态）。`DELETE /books/{id}` 删 `af_books` 与该书全部版本 `af_pages`/`af_block_edits` 行；`?purge=true` 连产物目录（含 `versions/`）一并删除；`af_parse_jobs` 保留为审计。 | `design/parsing-management.md`、`architecture/database-design.md` |
| V9 | **只读版本列举**：新增 `GET /api/v1/books/{book_id}/versions` → 每版本 `job_id/engine/status/page_count/parsed/source_sha256/created_at/active`；`GET /books/{id}` 响应增 `active_job_id` 字段（可空）。切换/回滚写入端点**不在本契约内**。 | `architecture/api.md` |
| V10 | **不引入数据根暂存**（2026-09-20 改判，取代原「暂存目录落地」条文）：引擎中间产物只在内存/临时文件生命周期内存在，落盘目标只有版本目录；`<data_root>/tmp/axiom-flow/` **恒不应存在，存在即缺陷**（PLAN-003 条文 12，比根侧 `storage-conventions.md:30-31`「任务结束清理」更严格）。本计划只新增约束性断言：版本目录之外不得产生中间产物。 | `design/parsing-pipeline.md`、`architecture/database-design.md` |

## 工作项

| # | 内容 | 产出 / 红灯判据 | 依赖 |
| --- | --- | --- | --- |
| P0 | 本计划转 Accepted（用户评审 V1~V10 与裁决 1~4）；治理入口按 `AGENTS.md` 大修改路径由本计划 + todo 承载，**不立 ADR**（2026-09-20 裁决） | **2026-09-21 达成**（裁决 5：用户选择「解除过度阻塞并开工实施」= V1~V10 评审通过；计划状态转 `In Progress`）；todo PLAN-004 行同步 | PLAN-003 收口（C1/C2/C3）——**已按裁决 5 改判为「C1/C3 达成即可开发」，C2 的 D3/D5 只关真机取证面** |
| P1 | 向根仓库发**确认请求**（新 REQ 行）：指针式是否满足 R2「根目录即当前版」、manifest 基准（R8）、版本对比/回滚是否需要契约、人工编辑不随版本继承的可见性 | **2026-09-21 改判：不新增独立 REQ 行**，确认清单并入 REQ-080 回执稿的「请根仓库确认/回应的清单」四条（差异 1~4 同节成文），由根仓库在其 REQ-080 行一并登记——同一需求的两面（实施回执 + 契约确认）不分列两行。todo REQ-080 行的下一条件已含该项 | P0 |
| P2 | Alembic 迁移 `20260920_0002`：`af_books.active_job_id`、`af_parse_jobs.source_sha256/page_count`、`af_pages` PK 重建、`af_block_edits` 唯一键与 `edit_id` 重建 + backfill（含 `legacy` 哨兵）；`db/models.py`、`repositories.py` 同步 | **达成（2026-09-21）**：迁移 `src/axiom_flow/migrations/versions/20260920_0002_parse_version_scoping.py`（主键/唯一键走「建新表 + 拷贝 + 改名」，同一份 DDL 兼容 SQLite 与 MySQL）；`test_db_migrations.py` 升/降/backfill/死默认值 8 例先红后绿，升降级可重复 | P0 |
| P3 | `ingest/artifacts.py`：新增 `version_dir_for()`（含 `ensure_safe_segment(job_id)`）、`link_pages_into()`（`os.link` 回落 `copy2`）、`build_manifest()` 排除 `versions/`；`BookRepository.set_active_job()` 仓储方法 | **达成**：`version_dir_for`/`link_pages_into`/`seed_version_from`/`VERSIONS_DIRNAME` 落地，根 `build_manifest()` 排除 `versions/` 子树；`set_active_job()` 为激活唯一入口；红灯见 `tests/unit/test_ingest.py`（根清单不含 `versions/`、越界 `job_id` 报错、硬链接与根页图同 inode、不支持平台回落复制） | P2 |
| P4 | `orchestrator/pipeline.py`：产物写版本目录、建页图硬链接、job 行写 `source_sha256/page_count`、终态校验后激活（单列 UPDATE）；保持条文 3 的页级提交与节流顺序；中间产物不落数据根（V10） | **达成**：写目标自第一页起即版本目录；`_inherit_page_rows` 保证页行与版本产物同构；续跑判据在 `(book_id, job_id)` 作用域内且要求 `blocks.json` 在盘；未 `completed`/有失败页不激活；`tests/unit/test_orchestrator.py` 全本/逐页/kill 续跑判据全绿 + 新增版本维度断言 | P3 |
| P5 | `api/main.py`：读取路径统一走生效版本解析（含 `active_job_id IS NULL` 的 legacy 回落）、`GET /books/{id}/versions`、`BookOut.active_job_id`、提交任务不再隐式依赖根目录产物清单 | **达成**：`_read_scope()` 为读取面唯一入口，五类只读端点 + 块编辑端点全部改造；`ParseVersionOut` 与 `GET /books/{id}/versions` 上线；契约 `TestParseVersions`（S1 两版本并存读生效版、S4 `image_url` 字面量常量、manifest 基准=版本目录、生效目录缺失 404 不回落、重 ingest 后历史版本按 `job_id` 可直读） | P4 |
| P6 | ingest/purge 改造（V8）：`ingest_book()` 只交换 `book.json` + `pages/`、保 `versions/`；API 侧不再调 `reset_for_book()` 删全部页行，改置 `active_job_id=NULL`；purge 删含 `versions/` 的整本目录 | **达成**：`importer._swap_root_view()`（改名让位 → 上位 → 清旧目录 + 失败回滚，无「`pages/` 不存在」读空窗），`reset_legacy()` 只清根视图行；`DELETE` 清全部版本 `af_pages` + `af_block_edits`（`delete_for_book`），purge 删整本目录；红灯：`test_reingest_preserves_versions_directory` + 契约「重 ingest 后版本目录仍在、根视图回落 legacy」 | P4 |
| P7 | 文档晋升与联动：`parsing-pipeline.md`（产物树/版本目录/V10）、`parsing-management.md`（V8/端点表）、`api.md`（V5/V6/V9）、`database-design.md`（V2/V7 + Mermaid + DDL）、`code-map.md`、`guides/operations.md`；销账 PLAN-003「W7 完成登记」发现 2（`engine` 死默认值随本迁移一并清理）；发现 1（`tmp/`）已由条文 12 改判，随 V10 一并成文 | **2026-09-21 成文**：六份文档 + 两份台账已同步（`design/parsing-pipeline.md` 新增「解析版本与生效指针」节、`architecture/api.md` 新增 `/versions` 与生效版本读法、`database-design.md` 四表 DDL/Mermaid 重写、`operations.md` 新增单窗口 runbook）；PLAN-003 发现 2 已随 `20260920_0002` 的 `alter_column(server_default=None)` 销账并有守护用例 | P2~P6 定型 |
| P8 | 门禁与收口：全量 `pytest` + `ruff`；REQ-080 实施回执稿（含差异点与待确认清单）、PLAN-003 W8 待办补「新布局上重跑冒烟」、`project-status.md` 刷新 | **进行中**：本地门禁 `376 passed, 10 skipped` + `ruff` 全绿；**真机 D 步已达成（2026-09-21 20:35~20:36，job `86ab56ae2b6c`）**——版本布局上单次重解析 20 页全部 parsed 并**首次激活** `active_job_id`，`versions/<job_id>/` 产物 529 个（含 `chunks.json`/`manifest.json`）、根视图无 `chunks.json`、页图硬链接同 inode、丢弃 0；取证 `data/probes/live-window-20260921/{S1c_exclusive,S2_after}.json`（过程与两处脚本缺陷见 [PLAN-003](2026-09-20-parse-live-bringup.md)「承载 ARCH-017」节 G4/G5）。剩 S7：REQ-080/081/083 回执发出 + 根仓库确认 | P7 |

**D 步真机结论（2026-09-21，条文逐条判定）**：V1（不回写根视图）、V2（页行按 `(book_id, job_id)`
作用域）、V3（`completed` + 产物齐备才激活，单列 UPDATE）、V5/V6（读取面走生效版本，`image_url`
字面量不变）、V8（re-ingest 只换根视图、`versions/` 保留且 39 行 legacy 整批作废）、V9（`/versions`
列出 12 个历史版本，失败版本 `parsed=0` 不激活）在本窗口一次运行内同时成立；V4/V7/V10 的本地判据
不变。**串行前提**：本窗口 D-2 首次尝试因根侧并发提交 + 容器被重启而失败（归因失真的「模型服务不可用」），
已由 ARCH-017 的单一占用闸门与出站请求 REQ-085 收口——版本布局本身无回归（零页行、指针未动、产物保留）。

台账联动：REQ-080 于 P8 关闭（回执发出且根仓库确认）；**ARCH-004（分块）于 2026-09-21 随 D 步真机
取证关闭**（`versions/86ab56ae2b6c/chunks.json` 生成 + 根视图无 `chunks.json`），ARCH-005/006（对照/
编辑）的产物读取前置在本计划解除；DEFECT-003 的「块下标 remapping」挂账项随 V7 收口；PLAN-003 W7 登记
的发现 1（`tmp/axiom-flow/`）改判为条文 12（不实现），发现 2（`af_parse_jobs.engine` 死默认值）在 P7 销账；
PLAN-003 W8 的复核触发点在 P8 更新为「必须在版本布局上取证」。

## 实施登记（P2~P6，2026-09-21，本地自闭环取证）

实现口径相对条文 V1~V10 的**必要补充**（均已随 P7 写进 `design/` 与 `architecture/`，此处只登记
「条文没说、实现必须决定」的部分与其理由）：

| # | 实现口径 | 为什么必须这样 |
| --- | --- | --- |
| I1 | **版本播种（copy-on-write）**：建版本目录时先 `seed_version_from(基线目录, 版本目录)`，基线 = `active_job_id` 指向的版本目录（指针为空则为根视图目录） | 满足根侧 R1「版本目录含完整产物」而**不必为逐页重解析重跑全书**：局部重解析的新版本 = 基线产物 + 本次重解析页。播种只在版本目录内进行，不触碰根视图（V8 的交换面不变） |
| I2 | **页行继承**：`_inherit_page_rows()` 把基线作用域中「产物确已继承到位」的 `parsed` 页行按 `(book_id, job_id)` 复制一份 | 与 I1 配套。否则逐页重解析出的版本「文件齐、状态缺」，`pages_done` 与续跑判据都会低估真实内容，违反「有页行必有产物」不变式（只复制 `blocks.json` 在盘的页） |
| I3 | **续跑判据加严**：在 `(book_id, job_id)` 作用域内取「无行 ∪ failed」，**且要求该页 `blocks.json` 在盘** | 页行与产物可能因外部清理磁盘而背离；缺文件即视为未解析重跑，避免把「有状态无产物」的页永久留在版本目录里 |
| I4 | **激活门槛**：`not failed` **且** `_version_is_complete()`（目标页齐备 + 产物在盘）才 `set_active_job()`；否则只记 `logger.warning` 并保持原指针 | V3 的「校验通过」具体化。**未激活不删文件**：半成品留在 `versions/<job_id>/` 供排查，且因端点只认指针而永远读不到（自然回滚） |
| I5 | **`book.json` 播种进版本目录** | 版本目录要能独立回答「这一版解析的是哪一册、多少页、源哈希多少」；`GET /manifest` 的形态也不因缺 `book.json` 而破 |
| I6 | **生效版本目录缺失 → 显式 404**（`生效版本产物缺失：{book_id} @ {job_id}`），**不回落根视图** | ADR 0004 无自动兜底：静默回落会让前端以为在读新版本、实际读旧数据，比报错更坏 |
| I7 | **`edit_id` 摘要纳入 `job_id`，但定位一律走唯一键** | 迁移前存量行保留旧摘要（不含 `job_id`）；若按新摘要查询，已有人工编辑会变成孤儿。重算历史主键属无收益的破坏性动作 |
| I8 | **`ParseJobRow.engine` 与 DDL 默认值同时收口**：仓储行数据类 `engine` 改为必填、迁移 `20260920_0002` 里 `alter_column("engine", server_default=None)` | PLAN-003「W7 发现 2」销账：`'mineru'` 的 DDL 兜底是静默回落入口（ADR 0004 禁止）。降级**不**恢复死默认值（有意的单向门） |
| I9 | **真机冒烟判据同步版本布局**：`tests/smoke/test_live_mineru_chain.py` 的整本用例改为断言 `versions/<job_id>/` 内产物齐套 + `active_job_id == job_id` + 根视图**不含** `chunks.json`（默认门禁 skip，故不改变 352 的计数）。**2026-09-21 R0 销账**：`scripts/tmp/live_reparse_b01_pages.py` 已在版本布局上重写为 HTTP 驱动（走 8902 端点提交/轮询，不再自拼旧扁平路径），并加部署自检（`alembic_version==20260920_0002` + `active_job_id` 字段在响应中）、`--check-only` 只读预检、pages 越界 422 复验与 re-ingest 自动化；旧扁平副本留档 `data/probes/window-prep-20260921/live_reparse_b01_pages.py.old-flat-layout`（见 [PLAN-003](2026-09-20-parse-live-bringup.md)「承载 REQ-083 / DEFECT-008 / DEFECT-006」节 R0-4） | 该用例是 D 窗口的取证工具；若仍断言旧扁平路径，D 步必然「代码对、用例红」，把实现问题误读成模型问题。脚本同理**必须在版本布局上重写后**才可用于 D 步，且重写要防「对未迁移部署跑真机」这类不可逆误操作 |

**S1~S6 本地取证矩阵**（全部在 `tmp_path` 假数据根 + `FakeEngine` + SQLite 上成立；真机面见下一节）：

| 标准 | 证据用例 |
| --- | --- |
| S1 两次解析后第一版仍可读 | `tests/contract/test_api_v1_contract.py::TestParseVersions`（两版本并存 + 端点返回生效版 + `/versions` 列举） |
| S2 激活是单列 UPDATE / 无半生效 | `tests/unit/test_orchestrator.py`（未 completed 不激活、kill 中途旧指针不变）+ `set_active_job()` 唯一入口 |
| S3 页级持久化与续跑在版本目录上成立 | W2 既有判据全绿 + 续跑写入同一 `versions/<job_id>/` |
| S4 端点契约零破坏 | 契约 `IMAGE_URL_TEMPLATE` 常量断言 + manifest 路径不含 `versions/<job_id>/` 前缀 |
| S5 迁移可升可降、存量无损 | `tests/unit/test_db_migrations.py`（升降级、重复执行、backfill 两判据、存量 `active_job_id` 恒 NULL） |
| S6 ingest/purge 不毁历史 | `tests/unit/test_ingest.py::test_reingest_preserves_versions_directory` + 契约「重 ingest 后版本目录可直读 / purge 删整棵树」 |

## REQ-080 实施回执稿（P8；**已于 2026-09-21 发出，锚点 commit `95e1d37`**）

> 载体：本仓库 `docs/trackers/todo.md` REQ-080 行 + 本节正文；根仓库自行读取登记，本仓库不写根
> 仓库文件（[跨项目协作](../standards/cross-project-collaboration.md) 第 4 步）。

**已完成（本仓库侧，本地门禁可复现）**：解析产物按 `versions/<job_id>/` 划分；生效指针
`af_books.active_job_id`（NULL = 根视图）；`af_pages` 主键与 `af_block_edits` 唯一键纳入 `job_id`；
`af_parse_jobs` 增 `source_sha256`/`page_count` 自证；新增只读 `GET /api/v1/books/{id}/versions`；
重复 ingest 只交换根视图并保留历史版本。**8900/前端零改造**：端点路径、响应结构、`image_url`
字面量与 manifest 相对路径基准全部不变。

**与根 `dataset-conventions.md:68-70` 字面措辞的差异（请根仓库确认）**：

1. **「`<book_id>/` 根目录即当前生效版本」→ 实现为「根目录是 ingest 级正本 + 端点契约不变」**：
   生效版本由 `af_books.active_job_id` 指认，8902 内部把读取解析到 `versions/<job_id>/`。理由：
   根侧 R6/R7 已证明 8900 与前端**只认端点路径**（8900 唯一磁盘读取是源 PDF），物理根目录承载当前版
   文件不是必要条件；而「把当前版拷/链到根目录」会造成同一事实两处存在、切版需搬运文件。
2. **「`af_parse_jobs` 记录生效版本指向」→ 生效指针落在 `af_books` 单列**：避免任务表与书目表两处
   布尔/指针漂移（一书一行，读指针不必扫任务表）。
3. **manifest 路径基准 = 版本目录内相对路径**（如 `pages/p0001.blocks.json`，不含 `versions/<job_id>/`
   前缀）：8900 `axiom.py:206-209` 原样透传路径给前端，加前缀即泄漏内部布局。
4. **「原子切换」= 单列 UPDATE**：不存在文件搬运，因此不存在切换中途的半状态；代价是根目录不再
   物理等同当前版（见差异 1）。

**请根仓库确认/回应的清单**：

- 上述差异 1~4 是否满足 REQ-080 的关闭语义（若不满足，需要的是根目录硬/符号链接镜像，属新契约）；
- 版本对比/回滚是否需要端点契约（当前只提供只读 `GET /books/{id}/versions`；切换/回滚在指针式下
  只是一条 UPDATE，尚未开写入端点）；
- 人工编辑**不随版本继承**是否可接受（新版本的块下标与内容可能不同，跨版本套用编辑即污染）；
- 旧版本清理策略（磁盘配额）仍属 D 类，需另行立项并经用户确认，本计划不自动删除。

**真机取证（2026-09-21 R1 窗口，回执可据此发出）**：真实部署 `alembic_version = 20260920_0002`；
D-1 re-ingest 补齐 486 张页图并把 39 行 legacy（含第 999 页幽灵行）整批作废为 0 行；D-2 在版本布局上
**单次**重解析 `linearalgebra-b01` 前 20 页 → job `86ab56ae2b6c` 20/20、52 秒，`parse_status=completed`、
`pages_done=20`、`active_job_id=86ab56ae2b6c`（**首次真机激活**）。`versions/86ab56ae2b6c/` = 529 文件
（`book.json` + `chunks.json` + `manifest.json` + 20×`blocks.json` + 20×`md` + 486 页图，其中 20 张与根
视图**同 inode** 硬链接），根视图 `chunks.json` 不存在（`root_view_has_no_chunks=True`）；只读端点回显
正常、块丢弃 0、`GET /books/{id}/versions` 列 12 个历史版本（失败版本 `parsed=0` 且不激活）。
取证：`data/probes/live-window-20260921/{S0_preflight,S1_before,S1c_exclusive,S2_after}.json`。
**给根侧的一条操作前提**：本机 GPU 只容得下一个 MinerU 解析，8900 侧不得在 8902 尚有在飞任务时并发提交
或重启 vision 槽位（出站请求 REQ-085；本方已加闸门，等待期任务保持 `queued` 且 `started_at` 为空）。

**REQ-081 时点承诺**：本方在真机 C 窗口（`af_*` 备份 + `alembic upgrade 20260920_0002` + 幽灵行清理
+ 同窗口发布）与 D 步（版本布局上单次重解析 `linearalgebra-b01` 前 20 页）完成后向根仓库发**二次
回执**（**两前提均已于 2026-09-21 达成 ⇒ 二次回执即刻可发**），届时根侧 §13 项 3/5 的复测数据才具
代表性；根侧「b01 仅 2 页产物」的前提已过时（现为生效版本 `86ab56ae2b6c` 的第 1~20 页 `parsed`）。

## 验证与验收

- **TDD**：P2~P6 每项先写红灯（迁移升降级、manifest 排除、硬链接、激活门槛、legacy 回落、
  ingest 保版本），再实现转绿；不改写 W2/W6 既有判据，只补版本维度断言。
- **隔离铁律**：全部用 `tmp_path` 假数据根 + fake engine；不连模型服务、不写真实数据根、
  不碰 `qed` 业务库（真机迁移只在用户批准的窗口执行，属 D 类动作，需确认）。
- **门禁命令**（`QED_env` 解释器）：
  `D:/software/anaconda3/envs/QED_env/python.exe -m pytest tests -q`（基线由 **303 passed, 2 skipped**
  增长到 **352 passed, 2 skipped**，2026-09-21 P2~P6 实施后；`tests/contract` 由 82 → **94**；本计划
  每步只增不减。R1 窗口后的当前全量为 **376 passed, 10 skipped** + 契约 **96**，增量来自 ARCH-017
  闸门用例与 ADR 0005 的 MySQL 层默认 skip）
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

- **关闭条件（2026-09-21 拆分为两面，避免把真机外部依赖误读成开发前置——本轮曾因此过度阻塞）**：
  - **本地面（已达成，2026-09-21）**：条文 V1~V10 晋升 `design`/`architecture`（P7）+ S1~S6 在
    `tmp_path` + `FakeEngine` + SQLite 上有守护用例 + 全量门禁绿（`352 passed, 2 skipped`、契约 94、
    ruff）。P1 确认清单并入回执稿成文。
  - **真机面（2026-09-21 R1 窗口达成）**：① C 窗口执行成功（备份 + `alembic upgrade
    20260920_0002` + 同窗口发布）✅；② D 步在版本布局上单次重解析并激活成功 ✅（`86ab56ae2b6c`）；
    ③ S7 = REQ-080 回执**发出** ✅（commit `95e1d37`，见 [PLAN-003](2026-09-20-parse-live-bringup.md)
    「跨项目回执发出」）。三项均达成 ⇒ 本计划关闭的**唯一剩余门槛是根侧对「请确认清单」四条的回应**
    （外部依赖，非本方待办；若根侧改判差异 1~4 不满足语义，则本计划按反馈改判而非直接关闭）。
- **当前差距小结（2026-09-21 回执发出后）**：S1~S6 ✅ / P1 ✅ / P7 ✅ / P8 ✅（含 C/D 真机取证）/
  真机 ①②③ ✅；**仅剩根侧确认四条**（REQ-080 的外部关闭条件，责任在根仓库）。连带保持开放：
  REQ-081（等根侧 §13 复测）、REQ-083（等根侧撤销改名）。ARCH-004 已关闭。
- **归档判定 Retain**：根仓库需求对表（R1~R10）、现状约束（L1~L10）与条文推导需保留，
  计划壳归档 `docs/history/plans/2026-09/`。
- **收尾动作**：todo 的 PLAN-004/REQ-080 行原子迁移 `completed.md`；`project-status.md` 能力表
  补「产物按版本」；`code-map.md` 的 `ingest/`、`orchestrator/`、`db/` 行同步版本解析职责；
  PLAN-003「收尾执行设计」的 W8 复核触发点保持有效。

## 台账历史归档（2026-09-21 承载契约压缩）

按 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md) 决定 4 压缩 todo 行证据列，以下为
PLAN-004、REQ-080、ARCH-004 行被移除的历史日志原文（保留原貌，不回改）。

### PLAN-004 原证据列

> **2026-09-20 立项**（需求对表 + 四项裁决；**同日裁决挂起在 PLAN-003 收尾之后**，当前不实施）。需求对表已完成：根仓库侧 R1~R10（`dataset-conventions.md:64-72` 版本目录与生效视图、`storage-conventions.md:30-31/44-45/62` 原子落盘与测试隔离、`api-contracts.md:558-585` 与 8900 代码证明读取方全走 HTTP、REQ-080 关闭条件需实施回执）；本仓库侧 L1~L10（页级持久化不变式排除「攒完再搬」、ingest 现会 `rmtree` 连带删 `versions/`、`build_manifest` rglob 需排除、页图 100MB 级不宜复制、af_pages/af_block_edits 键无版本维度）。**四项裁决**：① 指针式生效（`af_books.active_job_id`，端点契约零变化）② 页图硬链接自包含（`os.link` 回落 copy）③ 页状态与块编辑随版本（一次 Alembic 迁移，收口 W6 挂账的块下标 remapping）④ 本计划自闭环验证，真机证据并入 PLAN-003 W8。条文 V1~V10 待晋升（V10 已按条文 12 改判为「不引入数据根暂存」），工作项 P0~P8。**布局改动的治理入口是本仓库大修改路径（本计划 + todo），不新增 ADR**（2026-09-20 裁决：根侧 `storage-conventions.md:62` 只约束根仓库）。**2026-09-21 解除过度阻塞并开工（裁决 5）**：审核发现原「Blocked」把 PLAN-003 的 C2（D3 需批准 / D5 受 ADR 0005 Draft）与真机迁移窗口当成了**开发**前置，而裁决 4 早已排除真机依赖——证据：`tests/unit/test_db_migrations.py:18-33` 已在 SQLite 临时文件上跑 Alembic 升降级、`test_orchestrator.py` 用 FakeEngine、`test_api_v1_contract.py` 用 SQLite 建 API，且**应用从不自动迁移**（`api/main.py:140-144` lifespan 只关停，`src/axiom_flow` 内无 `create_all`/`upgrade`）⇒ P2~P6 合入对共享 `qed` 库零写入。故 P0（V1~V10 评审）视为达成，计划转 In Progress，P2~P6 在本地自闭环实施。**真机面按 B→C→D 单窗口执行**：B 本地全绿 → C 单一批准窗口（`af_*` 备份 + `alembic upgrade 0002` + 清 `af_pages` 第 999 页幽灵行 + 同窗口发布新代码）→ D 在**版本布局上**单次重解析 b01 前 20 页（兼作 PLAN-003 W8 取证）；**不在旧布局重复真机解析**（否则 V7 会把 b01 的 39 行 `af_pages` 回填成 `legacy` 哨兵，一步即作废）。~~恢复条件见计划「阻塞与恢复」（2026-09-21 两次复核后收窄）：PLAN-003 收尾关闭（现仅剩 C2 的 D3 需批准 + D5/W3 等 ADR 0005；C1/C3/W8 已达成）+ DEFECT-005 修复~~ → **2026-09-21 复核**：归一化前置 DEFECT-004/005 均已关闭（版本目录内的块坐标、块类型与 `index` 自此可信），开发面无阻塞项；剩余阻塞只在真机取证面（C 窗口 + D 步批准）

### REQ-080 原证据列

> 2026-09-20 本方接收确认（现状：重复解析覆盖落盘、无版本留痕）；本方产物布局事实源见 [parsing-pipeline](../design/parsing-pipeline.md)，根仓库侧约定见其 dataset-conventions 正文；实施需 af_parse_jobs 增量迁移 + 落盘改造，属数据布局变更，按变更分级先立计划。**2026-09-20 裁决**：拆 PLAN-004 后置，不在 PLAN-003 内做 DDL；**2026-09-20 PLAN-004 立项**（需求对表 R1~R10 / L1~L10 + 四项裁决 + 条文 V1~V10 见计划）。**2026-09-21 前置更新**：归一化前置全部解除（DEFECT-004 坐标空间 + DEFECT-005 类型映射与可见丢弃均关闭，版本目录内的块坐标、块类型与 `index` 自此可信）。**2026-09-21 优先级对齐根仓库（其 REQ-080 行标「高」，本方原记「中」）并转 In Progress**：PLAN-004 的 Blocked 经审核属过度阻塞（其 P2~P6 只需 SQLite + 临时数据根，应用不自动迁移 ⇒ 合入不写共享 `qed`），已按裁决 5 解除并开工；实施承载体是 PLAN-004（其「阻塞与恢复」为状态事实源）。**下一条件**：P2~P6 本地闭环取证（S1~S6）→ P7 文档晋升 → P8 回执；真机取证与关闭并入 B→C→D 单窗口（迁移 + 幽灵行清理 + 版本布局上单次重解析）

### ARCH-004 原证据列

> 已实现（Step 2-D：`chunks.json` + `GET /chunks` 参数可重算），规则单测通过；真机派生文件已在 linearalgebra-b01 产物目录生成。**2026-09-20 待办**：chunks 随真机产物重建才具意义（PLAN-003 W2 页级持久化 → W8 冒烟）；REQ-080 版本划分落地后 chunks.json 归属版本目录需同步约定（PLAN-004）
