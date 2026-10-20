---
name: qed-closeout
description: Use when a QED task is implemented and gates pass — runs the full gate, closes the plan (Retain/Delete), moves todo to completed.md, and syncs design/architecture docs. Trigger on 收尾, 关闭任务, 任务完成, 收口.
---

# QED 任务收尾（门禁 → 关闭 → 同步）

声称完成前先调用 `verification-before-completion`，本技能负责流程性收尾。

## 1. 门禁全绿

运行当前仓库 `AGENTS.md`「标准映射」表中「开发/联调门禁」对应指南的「验证门禁」节全部命令，保留真实输出（各仓库命令不同，以本仓库指南为准）。

## 2. 计划关闭两态判定

按标准映射「文档治理」的归档与删除节：

- **Retain**：记录已执行数据操作、迁移/发布里程碑、事故复盘或不可替代外部证据 → 归档 `history/` 对应目录。
- **Delete**：事实已并入固定文档或同步于 tracker，且 Git 锚点有效 → 删除计划壳。
- 用户在任务关闭时可指定；未指定时按默认规则建议，经用户确认后执行。
- 两态均同步 todo 镜像并在 `plans/index.md` 登记去处。

## 3. 收口顺序（ADR 0006，缺任一项视为未收尾）

1. **结论先写承载文档**：计划正文工作项/登记节收口（承载节更新状态）；被压缩的 todo 历史
   日志下沉计划「台账历史归档」节。
2. **todo 行关闭**：现状与读数先在承载计划安家（「承载」列只有那一篇承载文档的指针，无需改写），
   随后整行从本表原子移除（ADR 0013）。
3. **迁移 completed.md**：关闭结果 + 证据（含日期化过程）在此安家。
4. **plans/index.md 登记** Retain/Delete 去处（见上文两态判定）。
5. **project-status.md 按需刷新**：能力状态、当前主线与本轮主攻行；主线任务另按标准映射
   「文档治理」做文档一致性梳理。
6. **全量门禁**（见第 1 节）。

同时检查涉及模块的 `architecture/`、`design/` 文档是否需增改删；契约变化同步对应文档；
`design/` 三态梳理（Superseded 删除 / 存档移 `history/` / 有效保持）。

## 4. 跨项目回执

跨项目任务向需求方回执：本仓库提交号 + 门禁输出 + 联调结果（按标准映射「跨项目协作」）。

## 5. git

未获用户明确要求不提交、不推送、不打标签。
