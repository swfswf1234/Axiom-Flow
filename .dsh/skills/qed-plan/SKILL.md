---
name: qed-plan
description: Use when a QED task needs an implementation plan in docs/plans/ — writes the plan following the task-lifecycle plan contract (metadata + sections). Trigger on 写计划, 建计划, plans, 实施计划.
---

# QED 实施计划编写

计划的格式与治理规则以当前仓库 `AGENTS.md`「标准映射」表中「任务生命周期」对应文件
（`docs/standards/task-lifecycle.md`）为准，本技能只给执行步骤与指针，不复制正文。

## 步骤

1. 确认任务已按 `qed-intake` 定级并登记 todo。
2. 在 `docs/plans/` 建计划文件，命名 `YYYY-MM-DD-<slug>.md`（全小写连字符，可选以关联编号开头）。
3. 声明必需元数据（`状态`、`任务类型`、`最后更新`、`关联 ADR`、`关联设计`、`关联 Tracker`、
   `归档判定`）并写入必需章节（目标与成功标准、范围与非目标、前置条件、工作项、验证与验收、
   回滚、关闭与归档）；清单与格式以 task-lifecycle「计划生命周期」节为准。`Blocked` 必须写
   阻塞证据、恢复条件、责任位置、复核触发点四项（契约测试逐条断言）。
4. 在 `docs/trackers/todo.md` 由**该任务的唯一任务行**登记：壳链接写在「承载」列（链接文本＝
   壳 H1 标题，`状态` 与正文 `状态：` 逐字一致），「任务」列只写范围描述、不放链接。
5. 壳不复制判据条目：「目标与成功标准」节只写目标陈述 + 指向台账行的指针；执行进展、
   取证与裁决记录逐条写入壳的 `## 过程与证据` 节（带日期戳）。

## 指针（细则正文在 task-lifecycle，此处不重述）

- 状态枚举（`Accepted / In Progress / Blocked / Completed / Cancelled / Superseded`）、
  承载二选一（独立计划文件或 `## 承载 <ID>` 节）、进度单源与六步收口顺序
  → `docs/standards/task-lifecycle.md`；决策记录见 ADR 0006 / 0011 / 0013。
- **ADR 编号只在本仓库内解释**：引用决策时用本仓库 `docs/adr/` 的编号；不同仓库同一编号
  可能含义完全不同（本仓库 ADR 0006＝台账与计划承载契约）。引用上游决策写仓库名 + 编号，
  不写裸编号；本仓库没有对应 ADR 时改指 standard 的小节名（概念引用优先）。
- 上述判据的机械防线＝`tests/contract/test_tracker_governance.py` 与
  `test_plan_governance.py`；改标准正文前先改守护并使其先红。
- 计划只链接事实源，不复制通用命令、设计正文、实验结果或逐日工作日志；待评审设计内容随计划
  承载，用户确认后按 `docs/standards/doc-governance.md` 迁入 `docs/design/`。
- 关闭时按 `qed-closeout` 做 Retain/Delete 两态判定。
