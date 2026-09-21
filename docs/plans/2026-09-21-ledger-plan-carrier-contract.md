# 台账与计划承载契约治理（ledger-plan-carrier-contract）

状态：In Progress
任务类型：B
最后更新：2026-09-21
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（本计划的规则事实源，Draft 待用户评审）
关联设计：无（流程治理，不涉及解析设计）
关联 Tracker：docs/trackers/todo.md（PLAN-005；收口面涉及 ARCH-001/003/004/005/006/009/010/011/012/016、DEFECT-001/006/007/008、REQ-001/080/081 行改造）
归档判定：Delete（事实并入 standards/ADR 与压缩后的 todo；Git 锚点有效后删壳）

## 背景与用户裁决

todo 台账与 plans/ 计划未有效对应：多数任务行无承载文档、进度日志在 todo 行内膨胀
（PLAN-002/003 行 800+ 字）、任务无关闭条件、收口不清理。2026-09-21 用户裁决三项修正：

1. 不强制「一 todo 行恰好一个计划文件」，允许计划文件内的**承载节**作为替代承载；
2. 允许多任务并行，但设上限（采纳 **In Progress ≤ 3**，主线聚合行不计入）；
3. `DEFECT` 前缀退役，不再新增；存量 DEFECT-001/006/007/008 **保留原 ID 至自然关闭**。

规则本体已成文为 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（Draft）。本计划承载
该 ADR 的执行面：文档、技能、测试与存量改造。

## 目标与成功标准

**目标**：将「一任务一承载、进度单源、todo 只写目的」固化为受契约测试守护的规则，并完成
存量台账改造。

**成功标准**：

1. ADR 0006 经用户确认转 Accepted，`standards/task-lifecycle.md` 与 `doc-governance.md`
   条文同步（含计划命名统一、todo 规则节重写）。
2. todo.md 全表符合新契约：新增「关闭条件」列；证据列 ≤200 字且只含最新结论 + 承载引用；
   每行（聚合行除外）有承载计划文件或承载节；DEFECT 前缀标注退役。
3. qed-intake / qed-plan / qed-closeout 三个技能按新契约修订，悬空引用与命名矛盾清除。
4. `test_tracker_governance.py` / `test_plan_governance.py` 新增守护：关闭条件非空、证据列
   长度上限、支线 In Progress 计数 ≤3、承载引用（计划文件或 `## 承载节` 标题）存在性。
5. PLAN-002/003 行证据墙压缩为目的 + 状态 + 链接，过程史实下沉至计划正文与 completed.md。
6. 全量门禁绿：`pytest tests -q` + `ruff check src tests`。

## 范围与非目标

**范围**：`docs/adr/`、`docs/standards/task-lifecycle.md`、`docs/standards/doc-governance.md`
（仅台账/承载/命名相关条文）、`docs/trackers/todo.md`（结构与存量行）、
`docs/plans/`（新增 PLAN-005 与 DEFECT 承载节所在计划的登记）、`.qoder/skills/qed-*`、
`tests/contract/` 两个治理测试、`AGENTS.md` 完成检查第 6 条措辞对齐。

**非目标**：不改任务分类 A/B/C/D 框架与变更分级表；不动解析侧设计与代码；不重命名存量
DEFECT ID；不处理与根仓库的台账对齐（跨项目协作标准不动）。

## 前置条件

1. 用户评审通过 ADR 0006（转 Accepted）——评审前仅允许继续完善本计划，不改 standards/skills/测试。
2. 工作树无冲突变更（当前 release 分支）。

## 工作项

| # | 工作项 | 产物 | 状态 |
| --- | --- | --- | --- |
| W1 | ADR 0006 评审与转正 | `docs/adr/0006-*.md` 状态 Draft→Accepted + 索引同步 | 完成（2026-09-21 用户确认） |
| W2 | task-lifecycle 重写计划准入/生命周期节（承载二选一、关闭条件、并发上限、收口顺序）；doc-governance 统一计划命名并同步生命周期图 | 两份 standards 正文 | 完成 |
| W3 | todo.md 改造：表头加「关闭条件」列、规则节重写（承载引用/证据列上限/A 类不入台账/DEFECT 退役）、全部存量行按新契约压缩重排 | todo.md | 完成 |
| W4 | 存量承载补建：DEFECT-006/007/008 在 PLAN-003（或所属计划）补「承载节」；REQ/ARCH 行补承载引用 | 计划正文承载节 | 完成（改用「关联 Tracker 登记 + 台账历史归档节」承载，未拆独立承载节；PLAN-003 关联 Tracker 补 ARCH-006/009） |
| W5 | 技能修订：qed-intake（删悬空引用 + 声明本轮行 + 上限检查 + 承载选择）、qed-plan（承载节约定 + 命名以本计划统一结果为准）、qed-closeout（六步收口清单） | 三个 SKILL.md | 完成 |
| W6 | 契约测试升级（成功标准 4 所列守护项），先红后绿 | 两个测试文件 | 完成（tracker 治理测试新增 3 项守护，红→绿；plan_governance 无需改动——命名规范以 slug 前缀解释统一） |
| W7 | project-status.md「当前主线」登记本轮主攻行；AGENTS.md 完成检查第 6 条对齐收口清单 | 两处正文 | 完成 |
| W8 | 全量门禁 + 收尾（按新收口清单执行本计划自身关闭） | 门禁输出 | 进行中（治理测试全绿；全量门禁受并行 PLAN-004 开发中间态影响，见收口登记） |

## 验证与验收

- 定向：`pytest tests/contract/test_tracker_governance.py tests/contract/test_plan_governance.py
  tests/contract/test_adr_governance.py tests/contract/test_standard_governance.py
  tests/contract/test_markdown_links.py -q`，先于全量运行。
- 全量：`pytest tests -q` + `ruff check src tests`（成功标准 6）。
- 人工验收：todo.md 渲染后一屏可尽（聚合行除外）；随机抽 3 行能顺链接到达承载位置并看到
  最新进度；对照 ADR 0006 决定 1~9 逐条核对条文落点。

## 回滚

纯文档 + 测试变更，全部在 release 分支单提交内完成，`git revert` 即可整体回滚；ADR 0006
未转 Accepted 前任何工作项可独立放弃，不影响解析主线。

## 关闭与归档

按 ADR 0006 决定 8 的收口顺序执行本计划自身关闭：结论写入本计划 → todo PLAN-005 行压缩
关闭 → completed.md → plans/index.md 登记（默认 Delete 删壳）→ project-status 刷新 → 门禁。
关闭条件即本计划「目标与成功标准」1~6 全部达成。
