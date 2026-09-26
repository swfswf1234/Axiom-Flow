# ADR 0018 数据根治理承接确认包（adr0018-data-root-alignment-request）

状态：Accepted
任务类型：A
最后更新：2026-09-26
关联 ADR：无新增本仓 ADR（入站来源＝根仓库 ADR 0018「数据根顶层白名单与元数据 JSON 岛禁止」；
治理沿用 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md) 一任务一承载与
[跨项目协作流程](../standards/cross-project-collaboration.md)「接收跨项目请求」节）
关联设计：[parsing-pipeline.md](../design/parsing-pipeline.md)（**PP-6** 不写数据根 `tmp/`）、
[database-design.md](../architecture/database-design.md)（`active_job_id` 指针选版）、
[overview.md](../architecture/overview.md)（数据根顶层布局）、
[operations.md](../guides/operations.md)（数据根行与 D 类备份判例）
关联 Tracker：docs/trackers/todo.md（PLAN-015 镜像行；承载 REQ-095）
归档判定：Delete（A1~A5 结论并入[关闭台账](../trackers/completed.md)与根仓库 REQ-095 回执，无不可再生证据）
需求方：QED-Engine 根仓库（ARCH-032 存储链路规范化轮）
目标项目：Axiom-Flow
评审方：用户
执行方：Axiom-Flow（确认动作以文档与台账为主）

> **登记沿革**：2026-09-24 根仓库 ARCH-032 轮向本仓发入站确认包（根仓库 todo REQ-095）。用户同日
> 裁决：本包作为 **v1.0 主线下的一项临时承接任务**登记，**当前范围只限文档部分**（确认、留证、
> 回执），逐条怎么落地待讨论后再执行。

## 背景

根仓库 2026-09-24 存储链路全链路勘察确认：Axiom-Flow 侧多项实践是数据根治理的**正面资产**——
页图跨版本硬链接去重（`ingest/artifacts.py` link/seed）、生效版本 `active_job_id` 单列指针切换
（零磁盘写）、中间产物只在内存与同目录瞬态（本仓 PLAN-003 条文 12 / PP-6 早已把 `tmp/axiom-flow/`
改判为「设计表述错误，不写即合规」）。根侧 ADR 0018 与 dataset 目录约定修订即按本仓实况对齐成文。
本请求包请本仓逐条**确认口径一致**并登记一项触发型观察。

## 目标与成功标准

**目标**：把根仓库 ADR 0018 与 dataset 目录约定**对齐到本仓实况**并逐条给出书面判定；本仓零代码改动。

- **S1**：A1~A4 每条在本壳「登记与结论」给出二元判定（一致 / 有偏差），并附本仓事实源指针
  （文档条文号或 `src/` 文件＋函数），不接受「大致一致」。
- **S2**：形成给根仓库 REQ-095 行的回执正文（文字交付，由用户转达；本仓不写根仓库文件）。
- **S3**：A5 观察项登记触发条件，不进验收判据。
- **S4**：全程 `src/`、`tests/` 零改动自证（工作树差异只含 `docs/`），门禁全绿。

## 范围与非目标

**范围（本轮只限文档）**：本壳、todo 台账行、关闭台账回执行，以及确认所需的只读取证
（读本仓文档/代码、读根仓库文档）。A1 若需给根侧新规则加指针措辞，属可选文档改动。

**非目标**：任何 `src/`、`tests/` 代码改动；真实数据根的写入/清理/迁移（D 类窗口，另行批准）；
根仓库与 QED-Tracker 的文件写入（只读边界）；A5 的实施（插图去重、落盘策略、图片服务端点）。
确认过程中若发现需要代码或契约改动的偏差，按变更分级**另行立项**，不在本包内扩大范围。

## 前置条件

1. 需求方侧成文：根仓库 ADR 0018 与 `dataset-conventions.md` 数据根顶层白名单节（已完成）。
2. 用户认可本包 S1~S4 判据与 A1~A5 动作口径，并给出「开始执行」指令（先文档后实现）。
3. 只读取证可访问根仓库文档；本仓事实源（PP-6、`active_job_id`、`ingest/artifacts.py`）在 `release` 上可读。

## 工作项（A1~A5 承接确认表）

| # | 事项 | 根侧规则内容 | 本仓动作 | 状态 |
| --- | --- | --- | --- | --- |
| A1 | `tmp/` 不写数据根 = 根标准正式规则 | ADR 0018「解析中间产物不入 tmp」与本仓 PP-6/条文 12 同义；根侧已删除虚构的 `tmp/axiom-flow/<job-id>/` 约定 | 确认 `design/parsing-pipeline.md` PP-6、`guides/operations.md` 数据根行无需改动（本仓是正确方）；如措辞想指认根侧新规则可加指针（可选） | 待执行 |
| A2 | 产物内部文件 = JSON 岛白名单例外① | `book.json` / `manifest.json` / `pages/p*.md` / `p*.blocks.json` / `chunks.json` 属解析管线自身产物，合法落 `parsed/**`；「元数据 JSON 岛禁止」不约束它们 | 确认现状产物清单与例外清单一致，漂移即报 | 待执行 |
| A3 | 数据根顶层白名单与 `backups/` 例外区 | 顶层合法目录：`raw/`、`tmp/`、`parsed/`、`backups/`（D 类备份留痕，命名 `YYYY-MM-DD-<任务标识>` + 台账登记）；本仓既有 D 类备份判例在**仓内** `data/backups/`（数据根之外），不受影响 | 确认；未来若把备份写入数据根，改用 `dataset/backups/YYYY-MM-DD-<任务标识>` 并在本仓 todo 登记 | 待执行 |
| A4 | 硬链接去重与指针选版入根规范 | 根 `dataset-conventions.md` 已按本仓实现成文（页图硬链接、失败回落 copy2 告警、`active_job_id` 指针选版、NULL=根视图 legacy、重导只换根视图） | 确认表述与实现一致；若认为有任何语义偏差，回执更正（根侧以本仓事实源为准） | 待执行 |
| A5 | 观察预留：插图消费链与跨版本去重 | 当前 MinerU 适配器不请求 `return_images`，插图路径（`versions/<job>/pages/images/`）未触发；届时**无跨版本去重机制、无图片服务端点、前端不渲染**（根侧勘察实证）。根约定「触发时再立规则」 | 登记观察项不实施；触发条件＝任一引擎将返回内嵌图片时，本仓（去重/落盘策略 + 服务面）与根仓库（前端消费链）联动立项 | 待登记 |

## 验证与验收

- 验收＝S1~S4 逐条判定；A1~A4 结论每条一行写入本壳「登记与结论」节，回执正文一并成文于该节。
- 事实源核对方式：文档条文对文档条文（PP-6 ↔ ADR 0018）、实现对表述（`ingest/artifacts.py`、
  `active_job_id` 读写路径对根侧 `dataset-conventions.md` 措辞）。
- 门禁：`pytest tests -q` + `ruff check src tests scripts`（本包只动 `docs/`，全量门禁作为越界护栏）；
  无 MySQL、无真机数据根面变更，故不跑 `QED_TEST_MYSQL=1` 与 live 脚本。

## 回滚

纯文档改动：按文件还原本包涉及行即可（`git diff` 逐文件复核），无库、无产物、无磁盘副作用，
不需要备份。回执已转达根仓库时，回滚需同步在根台账标注撤回（经用户转达）。

## 关闭与归档

按 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md) 六步收口：结论写入本壳 → REQ-095 与
PLAN-015 两行压缩并关闭 → 迁移[关闭台账](../trackers/completed.md) → `plans/index.md` 登记去处 →
`project-status.md` 刷新 → 全量门禁。归档判定 **Delete**（确认结论与回执正文入关闭台账即够）；
若执行中发现偏差需另行立项，则本壳改 **Retain** 以保留偏差证据链。

## 登记与结论（已执行，2026-09-26 回填）

判定取证随 [PLAN-016](2026-09-24-storage-convention-alignment.md) 的 C1~C7 对账表与实施落地
（白名单哨兵区、存储治理守护、数据根 fail-closed、深度无关枚举、瞬态识别式、经批清理与指纹留档）。

| # | 判定 | 事实源指针 | 结论 |
| --- | --- | --- | --- |
| A1 | 一致（本仓是正确方） | `design/parsing-pipeline.md` PP-6（含瞬态三式识别式，PLAN-016 W1 改写）；`architecture/overview.md` 数据根顶层白名单哨兵区；守护 `tests/contract/test_storage_conventions_governance.py`（`tmp/axiom-flow` 字样双作用域命中为 0） | 根侧 ADR 0018 第③④条与本仓 PP-6/条文 12 同义，无需本仓措辞改动；原「缺声明缺守护」缺口已由 PLAN-016 W1/W2 补成文 + 守护，虚构约定不复活有契约把守 |
| A2 | 一致（零漂移） | PLAN-016 对账 C5 行；`ingest/artifacts.py:book_dir_for/version_dir_for`（构造器一律 `parsed/` 前缀）＋`ensure_within`；`api/admin.py` 清除作用域限 `parsed/`；`operations.md` 留档区判据（仓内 `data/` 不入岛、顶层无产物形状，探针根 `data/probes/*/root/` 例外放行） | 本仓写入只在 `parsed/`，产物内部文件全落根侧例外①；「例外①清单＋留档区不算岛」已由 PLAN-016 W1/W2 成文入册并有形状守护。**附注**：根视图 `book.json`/`manifest.json` 的退役属根侧后续裁决（REQ-097/PLAN-017 承接），A2 判定按对账时点的产物清单成立，不受其影响 |
| A3 | 一致（本实施轮后命名式全成文） | PLAN-016 对账 C3/C4 行；`operations.md` 数据目录表（`<QED_DATA_ROOT>/backups/YYYY-MM-DD-<任务标识>` 与本仓仓内 `data/backups/` 留档区两行，W6 成文）；W7 清理执行与前后指纹 `data/probes/storage-align-2026-09-24/`；变异复验 M4（命名式无门禁，经裁不补，runbook 人工核对步承担） | 顶层白名单沿用根侧 ADR 0018 不自行增删（本仓 ADR 0009 决定 3）；本仓既有 D 类备份判例在仓内 `data/backups/` 属留档区、不在数据根，不受命名式约束；历史判例 `window-20260922/` 保留留痕不追改；未来写入数据根备份一律用根侧命名式并在 todo 登记 |
| A4 | 一致（表述与实现逐点对上） | `ingest/artifacts.py:seed_version_from/link_pages_into`（`os.link`、失败回落 `copy2` 记 WARNING，PP-15）；`active_job_id` 单列指针选版（`architecture/database-design.md`、PP-14 齐备与激活同判据）；`ingest/importer.py` 原子交换（重导只换根视图、`versions/` 完整保留）；NULL=根视图 legacy 读取（`api/main.py` 回落枚举，已由 PLAN-016 W4 改深度无关） | 根 `dataset-conventions.md` 按本仓实况成文的四处表述全部与实现一致；唯一语义更正请求＝读取侧固定三层 glob 的 C7 缺口，本仓已自修（PLAN-016 W4 两形态回归网），根侧无需改动其措辞 |
| A5 | —（观察项） | — | 触发条件已登记（任一引擎将返回内嵌图片时，本仓与根仓库联动立项），未触发、不实施 |

**S1~S4 逐条判定**：S1 达成（上表 A1~A4 每条二元判定 + 文档条文号或 `src/` 文件＋函数级事实源，无「大致一致」）；
S2 达成（回执正文见下节，文字交付由用户转达，本仓未写根仓库文件）；S3 达成（A5 只登记触发条件，不进验收判据）；
S4 **范围性达成**——本包自身零 `src/`、`tests/` 改动（全部 A1~A4 判定为文档对文档、实现核对只读），
工作树中的 `src/`/`tests/` 差异全部属同批 PLAN-016（B 类实施壳）判据覆盖，不归本包；门禁全绿随收口轮复跑。

## 根仓库 REQ-095 回执正文

> 判据口径＝「A1~A4 逐条书面判定」，本回执按 PLAN-015 壳上表逐条对应；根侧可自读本壳（Delete 判定，
> 正文以收口轮锚点提交找回）登记关闭 REQ-095。

1. **A1 一致**：本仓 PP-6 即根侧第③④条的正确方；「不写数据根 `tmp/`」已由哨兵区成文 +
   `tmp/axiom-flow` 字样双作用域守护（命中为 0）把守，瞬态残留有三式识别式与启动期/purge 两路径告警。
2. **A2 一致**：写入面锁 `parsed/`（构造器前缀 + `ensure_within` + 清除作用域三重），产物内部文件与
   根侧例外①清单零漂移；仓内留档区「不算岛」判据与探针根例外已入本仓文档与守护。
3. **A3 一致**：顶层白名单沿用根侧 ADR 0018（本仓 ADR 0009 决定 3 声明不自行增删）；数据根内备份用
   `backups/YYYY-MM-DD-<任务标识>` 命名式并已在本仓 `operations.md` 成文（该面经裁不设门禁，由
   runbook 人工核对步承担）；仓内 `data/backups/` 属留档区不受约束，既有 D 类判例保留留痕。
4. **A4 一致**：硬链接去重、回落 copy2 告警、`active_job_id` 指针选版、NULL=根视图 legacy、重导只换
   根视图——根侧成文与本仓实现逐点对上；本仓读取侧曾有的固定三层枚举缺口已自修为深度无关并有
   两形态回归网，根侧文档无需更正。
5. **A5 不触发**：观察项登记维持（插图消费链触发时两端联动立项）。
6. **实施面说明**：以上「一致」的成文与守护落地随本仓 PLAN-016（ARCH-034）同批完成（白名单哨兵区、
   存储治理契约守护、数据根 fail-closed＝本仓 ADR 0009、经批残骸清理含前后指纹留档）；根视图元数据
   退役与版本目录唯一正本属 REQ-097/PLAN-017，为后续独立承接项，不影响本回执 A1~A4 判定。

**请根侧据此关闭 REQ-095**（根侧 todo 行判据＝「A1~A4 回执后关闭」）。
