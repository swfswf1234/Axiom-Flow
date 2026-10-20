---
name: qed-plan
description: Use when a QED task needs an implementation plan in docs/plans/ — writes the plan following the task-lifecycle plan contract (metadata + sections). Trigger on 写计划, 建计划, plans, 实施计划.
---

# QED 实施计划编写

计划的格式与治理以当前仓库 `AGENTS.md`「标准映射」表中「任务生命周期」对应文件为准，本技能只给执行步骤，不复制正文。

## 步骤

1. 确认任务已按 `qed-intake` 定级并登记 todo。
2. 选择承载形态：
   - **独立计划文件**：多步/跨天/D 类任务；命名 `YYYY-MM-DD-<slug>.md`，`<slug>` 可选以关联
     编号开头（如 `req080-parse-version-layout`），全小写连字符。
   - **承载节**：轻量缺陷或主线细则，在承载计划内新增 `## 承载 <任务ID>：<一句话目的>` 节，
     节内四要素：目的、成功标准、工作项、状态；不建独立文件。
3. 声明必需元数据：`状态`、`任务类型`、`最后更新`、`关联 ADR`、`关联设计`、`关联 Tracker`、`归档判定`。
4. 写入必需章节：目标与成功标准、范围与非目标、前置条件、工作项、验证与验收、回滚、关闭与归档。
5. 计划状态只用 `Accepted / In Progress / Blocked / Completed / Cancelled / Superseded`；`Blocked` 必须写阻塞证据、恢复条件、责任位置、复核触发点四项（契约测试逐断言）。
6. 在 `docs/trackers/todo.md` 登记镜像行：壳链接写在「承载」列（链接文本＝计划正文 H1 标题，
   「状态」列与正文 `状态：` 逐字一致），「任务」列只写范围描述、不放链接（计划正文是状态事实源）。

## 边界

- 计划只链接事实源，不复制通用命令、设计正文、实验结果或逐日工作日志。
- **进度单源（ADR 0006/0013）**：任务行的日期化进度、现状与证据只写入计划（工作项表/登记节/承载节），
  todo 行不跟现状（「承载」列只有那一篇承载文档的指针）；todo 收口前的历史压缩进计划「台账历史归档」节。
- 待评审设计内容随计划承载；用户确认后按标准映射「文档治理」以稳定名称迁入 `docs/design/`。
- 关闭时按 `qed-closeout` 做 Retain/Delete 两态判定。
