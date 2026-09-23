# V0.1 收口与文档清理（v01-closeout-doc-cleanup）

状态：In Progress
任务类型：A
最后更新：2026-09-23
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（收口六步与一任务一承载）、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（根侧/环境侧边界）
关联设计：[parsing-management.md](../design/parsing-management.md)、[api.md](../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-013、ARCH-001、PLAN-008、ARCH-010）
归档判定：Delete（收口结论以 `completed.md` + `project-status.md` 为事实源，壳无不可替代证据；
Git 锚点＝本批提交）

## 目标与成功标准

**目标**：给 v0.1 主线（解析管理闭环，对齐根仓库 ARCH-020 第三轮）一个**可检验的终态**——四行开放
工作全部进入终态（关闭 / 显式改判移交），承载与台账三源一致，并把累积的文档欠账（叙述失真、
scratch 残留、文件头旧壳编号、`确认状态` 未登记）一次清干净，使 v0.1 可以对外声明「开发完成」。

**为什么现在可以做**：v0.1 的实现面与工具面已全部关闭（PLAN-003/004/006/007/010/011/012、
ARCH-003/009/016/018/019/024/026、REQ-001/080/081/083/084/085/086/087/088）。PLAN-008 只剩 W6
（8903 联调）与 W7（收口），且 2026-09-23 晚真机上已经出现过一次完整的「清除 → 回灌 → 重建产物」
闭环（见「W0 实测读数」），联调的物理前提不再缺失。

**成功标准**：

| # | 标准 | 判据 |
| --- | --- | --- |
| S1 | v0.1 节的每一行都有终态：ARCH-005/006/PLAN-008/ARCH-001 四行**要么关闭并在 completed.md 有证据行，要么正文写明「改判/移交」与责任位置**——不允许以「验证已通过」的口头结论留空 | todo v0.1 节零开放行；`test_todo_plan_rows_exactly_mirror_active_plan_bodies`、承载与主线聚合行列全子行的守护全绿 |
| S2 | 承载链完整：PLAN-008 壳按判定迁移，`plans/index.md` 登记去处与锚点，全库对旧路径零残余引用 | `rg` 残余引用 0 命中；`tests/contract/test_code_document_mapping.py`、链接守护绿 |
| S3 | 台账三源一致：completed.md（证据安家）、todo.md（终态）、project-status.md（版本口径与轮次）互不矛盾 | 人工对表 + 治理契约测试；`project-status.md` 有「二十一（V0.1 收口）」轮条目 |
| S4 | 文档清理面各自落地且**被守护覆盖**（不是顺手删字）：`scripts/tmp/` scratch、`src`/`scripts` 文件头旧壳编号、design/architecture `确认状态`、todo 空节 | 逐项处置记录写入本壳；`ruff check src tests scripts` clean + 全量门禁绿 |
| S5 | 「V0.1 开发完成」有明确边界：`project-status.md` 写清 v0.1 **交付了什么**、**未交付什么**（ARCH-011 批量、v1.0 线、业务侧其余 15 本产物）与版本落点（是否 tag 由用户裁决） | 快照正文 + roadmap 里程碑一致 |

## 范围与非目标

**范围**：全部为 `docs/` 与 `scripts/tmp/` 的治理动作；`src/`、`tests/` 只允许改**文件头注释与
DesignRef**（S4 第 2 项），不改任何行为。

**非目标**：

- 不实施 v1.0（PLAN-009 及其 ARCH-022/023 只登记，不排期）。
- 不做 ARCH-011 批量解析、不重新 ingest/解析任何书（真机写盘需另次 D 类批准）。
- 不改两条只读端点的契约形状（根侧若要改，按「契约事实源先改文档」另立计划）。
- 不写 QED-Engine 根仓库与 QED-Tracker 任何文件（工作区边界）。
- 不推送、不打标签、不合 `main`（除非用户在 W5 单独批准）。

## W0 实测读数（2026-09-23 收口进场取证，只读）

进场时对真实运行态做了一次**只读**核对（`af_*` SELECT + 磁盘计数 + 8902 访问日志 + 三条 GET）。
结论与台账现有叙述**存在四处失真**，收口必须先按这些读数改写：

1. **清理之后又重建过**：`QED_DATA_ROOT/parsed/` 现有 **5355 个文件**（二十轮记录为「零文件」），
   `af_pages` 957 行、`af_parse_jobs` 6 行（`completed` 4 / `partial` 2），2 本书有生效版本——
   b05《数学分析原理》`012d637df1eb` **317/317 全 `parsed` 且 `has_artifact` 全在位**、
   LA-b01 `ab91e2d19bf4` **486 页里只 3 页 `parsed`（483 页 `pending`）**。
   该窗口（含两次 `partial`：一次「模型服务不可用」、一次「窗口 251-300 轮询 404」）**尚未写入任何台账**。
2. **`af_block_edits` 现 0 行** ⇒ ARCH-006 判据「代表书重解析后 `af_block_edits` 真机写入」**在重建后的
   数据上未发生**（历史上那 2 行人工校验随 2026-09-23 全项目清除消失，最后一次 `PUT .../edit` 是清除前的
   `mathalysis-b05/pages/50/blocks/8`）。
3. **`GET /api/v1/parse-jobs` 已被真实消费**：访问日志 33 次 200，含 `?limit=1&offset=0&status=queued&status=running`
   （抽屉轮询）与 `?limit=50&...&book_id=<书>`（按书列活跃）两种形状，六态过滤原值可用 ⇒ S1 的根侧消费成立。
4. **`GET /api/v1/books/{book_id}/pages` 消费次数 0**，同时调用方仍在做逐页试错：
   `GET /api/v1/books/mathanalysis-b01/pages/1` → **404 共 401 次**（另有 b05/LA-b01/LA-b02/b07 的同形 404 共约 130 次）。
   这正是本端点立项要消灭的形态（S2 的动机）。
5. 我本轮对 8902 发的三条只读 GET 复核通过：b05 清单 317 行全 `parsed`、LA-b01 清单 486 行
   （3 `parsed` + 483 `pending`）、未解析书 404 `书目不存在或产物缺失`。**端点在真机数据上可用**，
   但「前端已消费页清单」无日志支撑。
6. 访问日志里两条历史 500（`POST /books/sync`、`POST /books/mathanalysis-b01/ingest`）发生在重建窗口之前，
   同形请求在重建后为 200 ⇒ 判为「清理后源文件落点未就位」的一次性形态，仍列入 W4 的**待复核**清单，
   不在本轮结案。

> **口径**：本节只报告读取作用域里确实存在的事实，不推断根侧前端内部状态。S5「前端验证通过」
> 由用户下达；其对上述 3、4 两条的覆盖范围在 W1 的裁决里落纸。

## 进场裁决（2026-09-23，用户四项决定）

| # | 议题 | 裁决 | 对计划的影响 |
| --- | --- | --- | --- |
| D1 | ARCH-005/006 判据走向 | **补做一次小窗口取证**（不缩减判据、不移交） | W1 变为「取证步骤下达 → 根侧动作 → 本仓库只读复验」三步；S1 的关闭证据必须是**复验读数**，不接受口头结论 |
| D2 | 今晚重建窗口入册 | **补记为 project-status「二十一轮·产物重建窗口」** | W6 增加一项：二十轮「`parsed/` 零文件」的过时读数按实测改写，含两次 `partial` 归因 |
| D3 | 积压与发布 | **按里程碑提交，暂不推送**（不打标签、不合 `main`） | W5 拆为「里程碑提交」＋「发布口径留待另批」；PLAN-011/012 的 `Delete` 重判在本批提交后可执行 |
| D4 | 文档清理范围 | 做 ①`scripts/tmp/` 残留处置 ②文件头旧壳编号清扫 ④todo v0.1 节收缩；**不做** ③design/architecture `确认状态` 登记（等用户逐篇确认，仍挂 ARCH-010） | W4 收敛为三项；③ 在 ARCH-010 证据列继续留位 |

**D1 的可执行性边界**（如实登记）：`PUT .../blocks/{i}/edit` 前端本来就有（历史上成功写入过 3 次），
补做无障碍；但 `GET /books/{id}/pages` 需要 8903 侧**存在消费点**才能产生日志。若根侧前端尚无该入口，
则页清单取证不成立 ⇒ 回到「改判移交」，并把它作为一条**已知的未消费契约面**写进 ARCH-005 行，
不以本仓库自己发一条 curl 冒充前端消费（那不是 S5 判据要的东西）。

## 前置条件

1. **用户裁决 ARCH-005/006 的判据走向**（三选一，写入本壳 W1）：
   - **(A) 补做小窗口**：请根侧在前端各点一次页清单 + 经 8903 写 1 条块编辑，本仓库只读取证
     （`/pages` 访问日志 + `af_block_edits` 出现 1 行），两行按原文关闭；
   - **(B) 改判移交**（判例＝PLAN-007 W7 把根侧复测与环境侧移交图像模型维护线）：ARCH-005 判据改为
     「端点与字段可信 + 根侧消费页清单」，ARCH-006 判据改为「`PUT`/合并回显端点交付且读取面绿」，
     真机写入与回显由根仓库自证；
   - **(C) 只关 ARCH-005**，ARCH-006 保持 `Open` 等下一次获批窗口。
2. 未提交积压：`657161e` 之后的 PLAN-009~012、两条端点实施、清除端点与深度无关修复、叙述框架重整
   全部在工作树（24 改 + 9 新）。**收口结论必须以提交为锚点**（Delete 归档判定的「Git 锚点有效」
   依赖它，PLAN-011/012 已因未提交被迫改判 Retain）。门禁现状：`pytest tests -q` **485 passed / 10 skipped**、
   `ruff check src tests scripts` clean。
3. 若走 (A)：需要 8903 侧一次真实点击（根仓库自身动作），本仓库不代做，也不改前端。

## 工作项

| # | 工作项 | 产物 | 门禁 | 状态 |
| --- | --- | --- | --- | --- |
| W0 | 收口进场只读取证（运行态四源核对） | 本壳「W0 实测读数」节 | 无 | Done（2026-09-23） |
| W1 | 判据取证补做（D1）：向根侧下达两步动作 → 本仓库只读复验（访问日志出现 `/pages` 一条 + `af_block_edits` 出现 1 行）→ 读数写入本壳与 PLAN-008 承载节；同时把壳内「三本生效版本各 20 页可联调」的失真口径按实测覆盖 | 取证读数 + 文档改动 | 契约 | **Done（2026-09-23，提交 `8fca39b`）**：复验读数写入 PLAN-008「阻塞与恢复」＋承载两节。**ARCH-005 侧达成**（`/pages` 3 次：b05 200、LA-b01 200、b01 404＝无产物契约语义；同窗口对照面 `books/{id}` 27／`pages/{n}` 25／`image` 36／`edits` 40 次，作业面列表两形状 22＋单 job 轮询 346+99 次）。**ARCH-006 侧未达成**（末次起服后 `PUT .../edit` 0 次、`af_block_edits` 0 行）⇒ 按用户裁决「以清除前那次 PASS 为准」改判关闭，代价（重建代际零写入样本）如实登记为 **Partial**，不以本仓库自发的 curl 冒充前端消费 |
| W2 | 关闭四行：ARCH-005/006 → PLAN-008（W6/W7）→ ARCH-001 聚合行；todo 行压缩 + completed.md 迁移（证据在此安家） | 台账改动 | 治理契约 | **Blocked（2026-09-23）**：`test_task_ids_are_stable_unique_and_rows_are_complete` 要求 ID 在 todo+completed 全域唯一 ⇒ **completed.md 新增行与 todo 删行必须同批提交**，而 todo 无法单独提交（并行 PLAN-014 轮正在改）。原文预置见「W2/W3 待执行批次」节。**责任位置：**本仓库（W2+W3 同批）。**恢复条件：**`git status --porcelain docs/trackers docs/design` 干净。**复核触发点：**该轮入库后的下一次进场 |
| W3 | PLAN-008 壳按 `Delete` 判定删壳（`git rm`，事实在 `api.md`/`code-map.md` 与本表锚点）+ `plans/index.md` 与该月索引登记 + 全库旧路径引用改指 | `git rm` + 链接同步 | 映射/链接契约 | **Blocked（2026-09-23，与 W2 同因同批）**：`docs/design/parsing-management.md:177` 指向本壳，该文件与 todo/project-status 同为并行轮在写；删壳还连带 todo 的 PLAN-008 主线行删除（`test_todo_plan_rows_exactly_mirror_active_plan_bodies`）⇒ 只能与 W2 一次做完 |
| W4 | 文档清理（D4 三项）：① `scripts/tmp/` scratch 处置 + 三个真机驱动脚本入库；② `src/`、`scripts`、`tests/` 悬空跨壳条文编号清扫；③ todo v0.1 节收缩为收口存根并删无开放行的小节；④ W0-6 两条历史 500 列入待复核 | 文档改动 | 架构/设计契约 + ruff | **部分 Done（2026-09-23）**：② 已完成并**超出原范围**——除文件头外，`src`/`tests`/`scripts` 正文的 13 处悬空 `条文 N`/`V2`/`S3` 一并改指 `PP-5`/`PP-13`/`PP-14`、`PM-10`/`PM-16`/`PM-17`、`MI-2`/`MI-10`（提交 `85ecbd3`，全仓 `.py` 残余 0 命中）；三个真机驱动脚本随积压批次入库（`fb7eb5a`）。**① 受阻**：`scripts/tmp/` 现有 `baseline2/`、`sec_mi.md`、`sec_pm.md`、`sec_pp.md`、`trial_pm_确定规范.md` 五条 scratch，mtime 全在本轮进行中 ⇒ 判为**并行呈现轮在办**，本轮不删不动（只登记，收尾时由用户或该轮处置）。③ 等 W2；④ 已列入待复核。**不做**：design/architecture `确认状态` 登记（D4，仍挂 ARCH-010） |
| W5 | 里程碑提交（D3）：把 `657161e` 之后的积压按 PLAN-010/011 → PLAN-012 → PLAN-008 收口 → 台账收口 分批入库；**不推送、不打标签、不合 `main`** | 提交序列 | 全量门禁 | **Done（2026-09-23）**：`fb7eb5a`（PLAN-010/011 清除线：代码+契约+架构/运维+两壳+三脚本）→ `23664c2`（PLAN-012 叙述框架：三篇 design + 守护 + adr0007 指针）→ `21bc8cf`（PLAN-008 本方侧收口壳）→ `ae499a6`（completed/todo/索引 + PLAN-013 进场）→ `85ecbd3`（W4-② 编号清扫）→ `3b0b924`（W6 二十一轮）。**归属取舍如实登记**：`api.md`/`database-design.md`/`code-map.md` 上 PLAN-012 的编号改括注与清除线同文件不可拆 ⇒ 随 `fb7eb5a` 入库并在正文注明；`completed.md`/`plans/index.md`/月索引含三行关闭证据，整体落 `ae499a6`；**PLAN-009 不在积压内**（其壳 `2026-09-22-v1-parsing-knowledge-optimization.md` 早已入库且无未提交改动）。每批之后门禁 485 passed / 10 skipped + ruff clean |
| W6 | V0.1 版本确认成文：`project-status.md` **二十一轮·产物重建窗口**（D2，含两次 `partial` 归因与 LA-b01 3/486 现状）+「当前版本」段改口（v0.1 已收口、下一版 v1.0）+ 能力表失真行刷新 + roadmap 里程碑对齐 | 文档改动 | 治理契约 | **部分 Done（2026-09-23，`3b0b924`）**：二十一轮补记 + 二十轮「`parsed/` 零文件」过时读数更正 + 下一轮候选①②③按实测改写 + 本轮主攻移交本计划。**剩余**＝「当前版本」改口与 roadmap 对齐，与 W2/W3 同批（该文件同为并行轮在写，动作已列为上表 B6）。判据走向已定：ARCH-005 Achieved、ARCH-006 **Partial** ⇒ 改口措辞按「能力面交付并经真机消费、块编辑当代际写入证据移交根仓库下一窗口」成文，不写成无条件「验证通过」 |

## W2/W3 待执行批次（原文预置，等并行 PLAN-014 轮入库）

**为什么现在做不了**：本批是**一次原子提交**，跨四个当前被并行轮占用的文件——

1. `completed.md` 新增行与 `todo.md` 删行受 **ID 全域唯一**守护绑定（`test_task_ids_are_stable_unique_and_rows_are_complete`），不可拆批；
2. `todo.md` 的 PLAN-008 主线行删除又受**计划镜像**守护绑定（壳删了行还在 ⇒ 红）；
3. 壳删迁要改 `docs/design/parsing-management.md:177`，而 `docs/design/`、`tests/contract/test_design_documents.py`、
   `docs/trackers/*` 均为并行 PLAN-014（ARCH-010 固定文档形式正规化）在写内容 ⇒ 按提交边界判例
   （W5「只按名暂存自己的文件」）**不代提交他人 WIP**。

**批次动作（恢复后照此执行，无需再取证）**：

| # | 动作 | 目标 |
| --- | --- | --- |
| B1 | 删除 `docs/plans/2026-09-22-req088-page-jobs-list.md`（`Delete` 判定；事实源已晋升 `api.md`/`code-map.md`，Git 锚点＝`8fca39b`）**与 B2~B6 在同一次提交入库** | 壳删除 |
| B2 | `parsing-management.md:177` 的 `Blocked`＋壳链接改为「联调已收口，证据见 `completed.md` ARCH-005/006 行」；同时把本壳「验证与验收」S2 判据行与「回滚」节里的字面旧路径改掉（否则 B1 之后 `rg` 仍有残余） | 链接零残余 |
| B3 | `plans/index.md` 登记 PLAN-008 的 Delete 去处与锚点 | 索引登记 |
| B4 | todo 删除 ARCH-005/006 两行 + PLAN-008 行；v0.1 节收缩为收口存根（W4-③）；ARCH-001 按下方裁决定终态 | 台账终态 |
| B5 | `completed.md` 表首插入下方三行 | 证据安家 |
| B6 | `project-status.md`「当前版本」改口（v0.1 已收口、下一版 v1.0）+ 二十二轮记本批 + roadmap 里程碑对齐 | W6 剩余 |

**B5 预置行**（列序＝日期/ID/任务/终态/关闭结果/证据；ARCH-006 判 **Partial** 是用户裁决的直接后果，不是本仓库自评）：

```
| 2026-09-23 | ARCH-005 | 解析对照与质量度量：页图 vs 重渲染叠框与质量信号进对照视图 | Completed | Achieved | 承载 PLAN-013 W1（D1 补做窗口）+ PLAN-008 W6。关闭条件「8903 前端真机联调通过」按末次起服之后的访问日志复验达成：`GET /api/v1/books/{id}/pages` 3 次（`mathalysis-b05` 200、`linearalgebra-b01` 200、`mathalysis-b01` 404＝无产物时的契约语义），同窗口对照面 `books/{id}` 27／`pages/{n}` 25／`pages/{n}/image` 36／`pages/{n}/edits` 40 次，作业面 `parse-jobs` 列表两形状 22 次＋单 job 轮询 346+99 次 ⇒ 页清单与叠框/质量信号两条只读数据源均被真实消费。可联调产物口径按重建后实测覆盖：b05 `012d637df1eb`（317/317 全 `parsed`）、LA-b01 `ab91e2d19bf4`（486 页中 3 页）；原「三本生效版本各 20 页」代际已随 2026-09-23 全项目清除消失（PLAN-011 留档）。门禁 485 passed / 10 skipped + ruff clean；壳按 Delete 删迁，锚点 `8fca39b` |
| 2026-09-23 | ARCH-006 | 渲染数据供给与块编辑真机闭环：edit 写入与 pages 合并回显交付 | Completed | Partial | **改判关闭**（用户裁决 2026-09-23「以清除前那次 PASS 为准」）。达成三面：`PUT /api/v1/books/{id}/pages/{n}/blocks/{i}/edit` 与页读取合并回显端点交付、读取面 L7 全绿、清除前曾在真机写入 2 行人工校验（`mathalysis-b05/pages/50/blocks/8`、`linearalgebra-b01/pages/7/blocks/2`）。**未达成**：字面判据「代表书**重解析后** `af_block_edits` 真机写入」在重建后的数据上无样本——末次起服之后 `PUT .../edit` 0 次、业务库 `qed` 的 `af_block_edits` **0 行**（原 2 行随全项目清除消失，备份见 PLAN-011 留档）。代价与后续：重建代际的真机写入证据由根仓库在下一联调窗口自证，本仓库不以自发 curl 代证。承载 PLAN-013 W1 + PLAN-008 W6 |
| 2026-09-23 | PLAN-008 | 页级失败清单与 jobs 列表端点（REQ-088）：两条只读端点交付并经真机消费复验 | Completed | Achieved | S1~S4 契约测试全绿（14 例 + 变异复验）、`api.md`/`code-map.md` 同步；S5 拆两面判定——ARCH-005 侧 Achieved、ARCH-006 侧 Partial（同批本表两行）。W0~W7 全消耗，壳按 `Delete` 删除，事实源为 `architecture/api.md` ③ 类两节 + 本表三行；Git 锚点 `8fca39b`（复验读数与判据消耗）、`21bc8cf`（本方侧收口）、`650cfef`/`a710ddf`（端点实施） |
```

**B4 的一处结构冲突**（预置时判定，避免恢复后卡住）：ARCH-001 的关闭条件是「下列全部**支线行**关闭且 8903 联调通过」，而并行轮新登记的 PLAN-014 是 `支线 / In Progress`，且 `test_main_aggregate_rows_list_branch_children` 要求主线聚合行列全支线 ID ⇒ **只要 PLAN-014 未关，ARCH-001 就无法按字面关闭**。三条出路，执行时取其一：

- **(i) 改挂类别**：PLAN-014 属文档形式正规化（ARCH-010 长期行的执行轮），不是「解析管理闭环」能力判据 ⇒ 其行类别改 `长期`，ARCH-001 在四行关闭后按原文收口（**推荐**，代价最小且语义准确）；
- **(ii) ARCH-001 改判**：关闭条件加限定「不含文档正规化轮」，正文写明；
- **(iii) 推迟**：ARCH-001 保持 `In Progress` 等 PLAN-014，v0.1 声明改为「能力面完成、聚合行待文档轮」。

## 验证与验收

- 每步之后跑 `tests/contract`（台账镜像 / 承载 / 链接 / code-map 四类守护），最后跑全量门禁：
  `pytest tests -q` + `ruff check src tests scripts`。
- S1 的反向判据：把任一行的终态结论删掉后，`todo` 治理测试或承载测试必须转红（防止「口头关闭」）。
- S2 的反向判据：故意留一条指向 `docs/plans/2026-09-22-req088-page-jobs-list.md` 的引用，链接守护必须转红。
- W1 真机面实际走向：D1 的 (A) 只达成 ARCH-005 一面，ARCH-006 依用户裁决转 Partial 关闭 ⇒ **不补跑**
  `QED_TEST_MYSQL=1 tests/integration`（该判据不再作为关闭前提；若根仓库在下一窗口自证写入，由其侧取证）。

## 回滚

- 全部为文档与台账动作，逐提交可 `git revert`；PLAN-008 壳删壳走 `git rm`（正文可用
  `git show 8fca39b:docs/plans/2026-09-22-req088-page-jobs-list.md` 找回）。
- 唯一非文档动作是 W4-① 删除 scratch 文件；删除前确认无引用，且内容可用
  `git show <本批提交>:<路径>` 找回（这些文件从未提交 ⇒ 删除前先把值得留的段落并入承载壳）。

## 关闭与归档

- 关闭条件：S1~S5 逐条判定，且本壳 W1~W6 全部 Done；W2/W3/W4-③/W6 剩余**在一次原子提交里完成**（步骤 B1~B6）。
- 收尾顺序（ADR 0006 六步）：本文写结论 → todo 行压缩关闭 → `completed.md` 迁移 →
  `plans/index.md` 登记 → `project-status.md` 刷新 → 全量门禁。
- 归档判定 Delete 的前提是「Git 锚点有效」；若到 W3 时提交仍未获批，按 PLAN-011/012 的判例改判 Retain
  并在 `plans/index.md` 写明原因。
