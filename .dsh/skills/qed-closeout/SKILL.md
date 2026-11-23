---
name: qed-closeout
description: Use when a QED task is implemented and gates pass — runs the full gate, closes the plan (Retain/Delete), moves todo to completed.md, and syncs design/architecture docs. Trigger on 收尾, 关闭任务, 任务完成, 收口.
---

# QED 任务收尾（门禁 → 关闭 → 同步）

声称完成前先调用 `verification-before-completion`，本技能负责流程性收尾。
判据正文只在 `docs/standards/task-lifecycle.md`（收口节拍与关闭判定）与
`docs/standards/doc-governance.md`（归档与删除），本技能只给步骤与指针。

## 1. 门禁全绿

运行当前仓库 `AGENTS.md`「标准映射」表中「开发/联调门禁」对应指南（本仓库为
`docs/guides/development.md`）「门禁」节所列命令，保留真实输出
（各仓库命令不同，以本仓库指南为准）：

```powershell
pytest tests -q
ruff check src tests
```

本仓库**无远端 CI，本地门禁是唯一门禁**。DSH 会话内执行时注意缓存目录对 Low 完整性 shell
不可写（`RUFF_CACHE_DIR=%TEMP%\…`、`-p no:cacheprovider`），否则失败与代码质量无关。

## 2. 逐条对照关闭

1. 打开该任务台账行的 `成功标准` 列，逐条核对是否达成，把每条的达成证据写进承载壳的
   `## 过程与证据` 节（带日期戳）。
2. 关闭结果取值按 task-lifecycle 的关闭判定：行内判据全达成 → `Achieved`；有未做的核心项
   → `Partial`。
3. 证据允许「待提交」占位（无 hash 不构成拒绝关闭的理由），提交后由收尾会话补登。
4. 壳的归档判定（Retain / Delete）按 doc-governance「归档与删除」节执行，用户可指定；
   两态均从 todo 移除该任务的唯一任务行，并在 `docs/plans/index.md` 登记去处。

## 3. 台账与文档同步

- 完成任务从 `todo.md` 原子移除并写入 `completed.md`（关闭结果 + 证据摘要）；**不把进展写回 todo**。
- 六步收口顺序（ADR 0006，缺任一项视为未收尾）：① 结论写入承载文档 → ② todo 行压缩并关闭 →
  ③ 原子迁移 `completed.md` → ④ `plans/index.md` 登记 Retain/Delete → ⑤ `project-status.md` 刷新 →
  ⑥ 全量门禁。
- 顺带自查：状态枚举、成功标准列上限与禁词、承载列单指针、行内文档锚、在途壳有带日期戳的
  过程与证据节——判据唯一正文见 task-lifecycle「任务台账与承载」节，本技能不另立说法。
- 检查涉及模块的 `architecture/`、`design/` 文档是否需增改删；契约变化同步对应文档；
  `design/` 做三态梳理（Superseded 删除 / 存档移 `history/` / 有效保持）。

## 4. 跨项目回执

跨项目任务向需求方回执：本仓库提交号 + 门禁输出 + 联调结果（按
`docs/standards/cross-project-collaboration.md`）；回执与承接记录写承载壳的过程与证据节。

## 5. git

未获用户明确要求不提交、不推送、不打标签。普通开发提交与推送进入 `develop`；
只有用户明确要求并完成适用门禁后，才把 `develop` 合并回 `main`（ADR 0014）。
