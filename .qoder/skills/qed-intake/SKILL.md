---
name: qed-intake
description: Use when starting any task in QED-Engine / QED-Tracker / Axiom-Flow — reads required docs, classifies the change level (变更分级), sets up todo/plans before coding. Trigger on 接任务, 任务开始, 定级, 立项.
---

# QED 任务进场（读必读 → 定级 → 立项）

任务动手前的统一入口。**未定级不实施**；无法判定时询问用户。

## 1. 读必读文档

读当前仓库 `AGENTS.md`，按其「标准映射」表定位并阅读：

1. 项目状态快照（现状）
2. 本地环境（环境与命令）
3. 文档治理（文档链路）
4. 任务生命周期（任务分类与计划准入）
5. 涉及契约：对应 `architecture/` 文档与模块映射（code-map）

## 2. 变更分级

按当前仓库 `AGENTS.md`「变更分级与边界」表定级：

- `architecture/` 改动、`design/` 大变更 → 大修改：先建 todo + `plans/` 计划
- `docs/` 目录结构变更 → **默认阻止**，仅用户明确同意且建 todo + 计划后执行
- `design/` 小修/bug → 登记对应长期台账并补设计文档
- 一般小改（措辞/链接/错别字/无行为修正）→ 豁免
- `standards/` 实质规则变更 → 先立 ADR

## 3. 声明本轮行与承载（ADR 0006）

- 读 `docs/trackers/todo.md`，声明**本轮操作的行**（可多行，但「本轮主攻」全库唯一，与
  `project-status.md` 对照）。
- 检查并发上限：支线行 `In Progress` 已有 3 个时，先引导用户收口/改判一个，或取得显式豁免。
- 新行必须写得出**一句话成功标准**（完成时的具体终态）；写不出则不成为行（A 类小改直接实施，不入台账）。
  三列的语义、列界数值与禁词清单的**唯一正文**＝`docs/standards/task-lifecycle.md`「任务台账与承载」
  （ADR 0013 立法，只链接不复制）；进场时按那一节逐列核对，不在本技能里另立一套说法。
- 确定承载位置：独立计划文件，或既有计划内的 `## 承载 <ID>` 节（轻量缺陷/细则优先用承载节）；
  缺陷登记为支线行，不再新增 `DEFECT-*` 前缀。

## 4. 立项

- 大修改：在 `docs/trackers/todo.md` 建任务条目，在 `docs/plans/` 建计划（见 `qed-plan`）。
- 跨项目需求：按标准映射「跨项目协作」承接登记。
- 豁免级：直接实施，以差异与验证记录承接。

定级与立项完成后，进入实现（`qed-implement`）。
