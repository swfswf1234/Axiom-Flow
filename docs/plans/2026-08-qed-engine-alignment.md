# 计划 2026-08：QED-Engine 对齐请求登记与轻量更新

状态：In Progress
任务类型：A
最后更新：2026-08-04
关联 ADR：`docs/adr/0017-consolidated-task-ledgers-and-roadmap.md`（本仓库任务台账治理；端口与前端
决策见 QED-Engine `docs/adr/0002-frontend-and-port-centralization.md`，属跨仓库请求）
关联设计：`docs/design/web-workbench.md`
关联 Tracker：`docs/trackers/todo.md`（ALN-001）
归档判定：用户确认全部请求并完成迁移后按 ADR 0013 判定 Retain/Delete

## 目标与成功标准

1. 接收并登记 QED-Engine 根仓库发起的对齐请求：端口 8000→8902、数据目录指向根 dataset/、
   直读 QED_ 环境变量、web/ 前端迁入根仓库统一前端。
2. 本轮完成轻量文档表述更新：`overview.md` 与 `runtime-architecture.md` 说明前端归属规划，
   不修改任何行为代码。
3. 请求在用户评审确认前不执行；确认后按各自 B/D 类计划执行并关闭本计划。

## 范围与非目标

范围：todo 请求登记、计划登记、两份架构文档的表述级更新。

非目标：
- 本轮不修改 `src/`、`web/`、配置或任何行为代码。
- 不执行端口迁移、不迁移数据目录、不退役 load-env.ps1、不迁移前端。
- 不新增本仓库 ADR（跨仓库决策以 QED-Engine ADR 0002 为源）。

## 前置条件

- QED-Engine 根仓库已完成 ADR 0002 与对齐请求登记（2026-08-04）。
- 用户确认 QED-Engine 侧决策（端口 8902、前端统一、数据目录与环境变量方案）后进入迁移执行。

## 工作项

1. ✅ 本仓库 todo 登记请求行 ALN-002（端口）、ALN-003（数据目录与环境变量）、ALN-004（前端迁移）。
2. ✅ 本计划登记（ALN-001），与 todo Plan 行镜像。
3. ✅ `overview.md` 系统边界补充前端归属规划说明。
4. ✅ `runtime-architecture.md` 运行职责补充前端迁移方向说明。
5. 用户确认后（拆 B 类计划）：CORS/README/指南端口 8902 调整。
6. 用户确认后（拆 B 类计划）：数据目录与 QED_ 变量直读改造。
7. 用户确认后（拆 A/B 类计划）：web/ 迁入根仓库并退役本仓库前端。

## 验证与验收

- 本仓库契约测试全绿：`pytest tests/contract -q`（architecture / plan / tracker / document
  structure 定向运行）。
- 架构文档 Mermaid 视图与系统边界标签不变，只增表述。
- 用户核对 QED-Engine ADR 0002 端口表与本计划请求清单一致。

## 回滚

- 本轮仅文档变更，`git checkout` 可完全恢复；无数据、标签或发布操作。
- 迁移执行阶段另有独立 B 类计划与回滚方案，不在本计划内执行。

## 关闭与归档

- 全部请求迁移完成后，本计划转为 Completed，按 ADR 0013 判定 Retain/Delete。
- 若用户否决某请求，相应 todo 行标注取消并记录原因，不执行对应迁移。
