# 计划索引

状态：Current
最后更新：2026-09-21

本目录保存已批准且尚未关闭的执行计划。活跃计划的镜像行见
[待做任务](../trackers/todo.md)，计划正文是范围、验证和回滚的事实源。

## 关闭登记

- PLAN-001 文档清理与任务台账重构（Axiom-Flow）已完成（2026-09-11），计划按 `Delete` 删除；
  事实并入 tracker 与相关文档。
- V2-017 文档治理体系对齐（Axiom-Flow）已完成（2026-09-11），计划按 `Delete` 删除；
  事实并入 `docs/standards/`、`AGENTS.md`、`docs/guides/development.md`。
- PLAN-005 台账与计划承载契约治理（Axiom-Flow）已完成（2026-09-21），计划按 `Delete` 删除；
  事实并入 [ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)、
  [任务生命周期](../standards/task-lifecycle.md)、[文档治理](../standards/doc-governance.md)
  与压缩后的 todo；壳的完整内容见提交 `b42540b`（`git show b42540b:docs/plans/2026-09-21-ledger-plan-carrier-contract.md`），
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-005 行。
- PLAN-002 Axiom-Flow 配置简化与解析重构（Axiom-Flow）已完成（2026-09-21），计划按 `Retain` 归档至
  [history/plans/2026-09](../history/plans/2026-09/2026-09-17-axiom-flow-refactor.md)（v2 重构基线：
  2026-09-17 七条用户裁决 + af_* 真机迁移里程碑 + 8 条成功标准逐条判定）；
  关闭证据见 [completed.md](../trackers/completed.md) PLAN-002 行。
- PLAN-006 解析状态语义收口（Axiom-Flow）已完成（2026-09-21），计划按 `Delete` 删除；语义事实源
  固定为 [ADR 0007](../adr/0007-parse-status-semantics.md) + [api.md 状态机表](../architecture/api.md)
  + [database-design.md 列语义](../architecture/database-design.md)
  + [parsing-pipeline.md 条文 S1~S7](../design/parsing-pipeline.md)，运维边界见
  [operations.md](../guides/operations.md)；壳的完整内容见提交 `9034a19`
  （`git show 9034a19:docs/plans/2026-09-21-parse-status-semantics.md`），关闭证据见
  [completed.md](../trackers/completed.md) PLAN-006 / DEFECT-006 / DEFECT-007 行。
