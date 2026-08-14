# 计划 2026-08：v2 docs 重构轮

状态：Accepted
任务类型：B
最后更新：2026-08-14
关联 ADR：`docs/adr/0001-v2-exploration-direction.md`
关联设计：`docs/design/pdf-parsing-and-rendering.md`
关联 Tracker：`docs/trackers/todo.md`（DOCS-001）
归档判定：关闭后归档至 `docs/history/v1-20260814/plans/`

## 目标与成功标准

- 目标：把 v1 文档体系完整归档，重建为 v2 探索型轻量文档体系（架构/设计/ADR/标准/指南/计划/追踪器），
  只保留必要任务与必要设计。
- 成功标准：
  1. v1 全部文档归档至 `docs/history/v1-20260814/`（只移动不删除，git mv 保证可回滚）。
  2. 新文档体系齐备：architecture（overview/code-map）、design（唯一活跃设计）、adr（新纪元 0001）、
     standards（4 份核心）、guides（development/operations）、trackers（todo/roadmap/completed）。
  3. `docs/trackers/todo.md` 建立 v2 任务清单（V2-001 起）并按依赖排序。
  4. 契约测试适配新体系，全量 `pytest tests/contract/ -q` 全绿；`pytest tests -q` 无新增失败、
     `ruff check src tests` 通过。

## 范围与非目标

- 范围：docs/ 目录重构、契约测试适配、README/AGENTS 同步。
- 非目标：代码重构（V2-001 起为下一轮计划）；旧 src 模块删除或重写；QED-Engine 前端对接。

## 前置条件

- 无（依赖 2026-08-14 用户批准的设计与 ADR 0001）。

## 工作项

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| P1 | v1 全量归档至 `docs/history/v1-20260814/` | 完成 |
| P2 | architecture/：index、overview（新）、code-map（新） | 完成 |
| P3 | adr/：index、0001-v2-exploration-direction | 完成 |
| P4 | standards/：4 份核心标准重写 + index | 完成 |
| P5 | guides/：development/operations/index | 完成 |
| P6 | trackers/：todo/roadmap/completed/index | 完成 |
| P7 | docs/index.md 与全目录 index 导航 | 完成 |
| P8 | 契约测试适配 + 全量门禁 | 完成（42 契约全绿 + ruff 通过） |
| P9 | README/AGENTS 同步 + 提交 | 完成（git 提交待用户确认） |

## 验证与验收

- `pytest tests/contract -q` 全绿。
- `pytest tests -q`：无新增失败（既有环境红保持 6 个 fixture 乱码问题，属外部数据问题，
  证据与恢复条件已在 todo 记录）。
- `ruff check src tests` 通过。
- 文档链接检查（test_markdown_links）通过。

## 回滚

- 全部归档与改动经 git 完成；单一提交可整体 revert。归档采用 git mv，不删除任何 v1 内容。
- 契约测试适配记录在提交信息中，回滚提交即恢复 v1 测试体系。

## 关闭与归档

- 关闭后本计划归档至 `docs/history/v1-20260814/plans/`；DOCS-R1 迁移至 completed.md。