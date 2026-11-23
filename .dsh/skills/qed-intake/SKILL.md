---
name: qed-intake
description: Use when starting any task in Axiom-Flow — reads required docs, classifies the change level (变更分级), sets up todo/plans before coding. Trigger on 接任务, 任务开始, 定级, 立项.
---

# QED 任务进场（读必读 → 定级 → 立项）

任务动手前的统一入口。**未定级不实施**；无法判定时询问用户。
登记与收口细则的唯一正文是 `docs/standards/task-lifecycle.md`，本技能只给步骤与指针。

## 1. 读必读文档

读当前仓库 `AGENTS.md`，按其「标准映射」表定位并阅读：项目状态快照、本地环境、文档治理、
任务生命周期，以及本任务涉及的 `architecture/` 文档与模块映射（code-map）。

## 2. 定级

按 `AGENTS.md`「变更分级与边界」表判定变更对象属于大修改 / 阻止项 / 小修改 / 先立 ADR / 豁免；
判据只在该表，本技能不复述。无法判定或涉及新增轮次、改判他轮归属时问用户。
- **形态不清先做 prior-art**：用户说不清要做成怎样时，用联网检索、网页读取与浏览器能力在 GitHub 挖掘成熟项目作借鉴；产出＝**设计提案交用户裁决**，不直接落码（探索结论先入 `design/` 或 ADR，经用户拍板后才成为实施范围）。

## 3. 立项

1. **先找承载再新建**：能在既有行/壳加条目就不加行，能挂支线就不开轮次；默认落点是
   `普通任务轮` 壳的登记表条目（判据：task-lifecycle「收口节拍与承载归属」节）。
2. **一任务一行一壳**（ADR 0011）：在 `docs/trackers/todo.md` 建一个任务行并在 `docs/plans/`
   建其唯一计划壳（见 `qed-plan`）；不为一个任务发两个 ID。
3. 行的写法：`任务` 列＝壳链接 + 一句话「做什么 + 目标/效果」且含仓库内文档锚（`承载` 列只放
   那一个指针）；`成功标准` 列＝一句话固定终态（≤80 字，写定不改）；`状态` 列只用进行中态。
   执行进展与证据不写进行——落壳的 `## 过程与证据` 节。三列列界与禁词以 task-lifecycle
   「任务台账与承载」节为唯一正文（ADR 0013 / ADR 0018）。
4. 缺陷（改代码行为）登记为支线行并挂承载，不再新增 `DEFECT-*` 前缀；只改文档正文的
   小修入 `docs/plans/design-bugfix-log.md`，不入 todo。
5. 跨项目需求按 `docs/standards/cross-project-collaboration.md` 登记承接与回执口径。
6. 豁免级直接实施，以差异与验证记录承接。

定级与立项完成后进入实现（`qed-implement`）。

## 指针

- 判据正文：`docs/standards/task-lifecycle.md`（任务分类、任务台账与承载、计划生命周期、
  并发上限）；决策记录 ADR 0006 / 0011 / 0013 / 0018。
- 机械守护：`tests/contract/test_tracker_governance.py`、`tests/contract/test_plan_governance.py`。
- **工作区边界**：本仓库是 agent 唯一合法工作区；QED-Engine 根仓库与 QED-Tracker 一律只读。
