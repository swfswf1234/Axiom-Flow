# ADR 0002：待评审设计的目录流转（Draft 设计先入 plans/，确定后落 design/）

状态：Accepted
最后更新：2026-08-23
关联代码：无
关联测试：`tests/contract/test_adr_governance.py`

## 背景

QED-Engine 根仓库经 [ADR 0011](https://github.com/QED-Engine/qed-engine/blob/main/docs/history/adr/v0.1/0011-pending-design-location.md) 裁决（范本，
同 [ADR 0001](0001-v2-exploration-direction.md) 对齐根仓库 ADR 0010 的模式）：待评审的设计
内容（接口契约草案、流程方案等）不再直接进入 `design/`，一律随计划在 `plans/` 承载，经用户
评审完全确定后以稳定名称迁入 `design/` 或合并固定文档；`design/` 目录本身即「已确定」可信度
标签。该裁决属文档分类边界变更，用户指令同步至本仓库，按 ADR 治理登记本决定。

## 决定

1. **Draft 设计不进 `docs/design/`**：任何未定稿、待评审的设计内容随其所属计划在
   `docs/plans/` 承载。
2. **确定的迁移时机**：设计内容经用户评审完全确定后以稳定文件名迁入 `docs/design/`
   （设计状态标 Accepted）；计划壳按任务生命周期关闭归档。
3. **被否决的设计**随计划关闭处理，不进 `design/`；含审计价值的按归档规则进
   `docs/history/`。
4. **存量豁免**：已存在于 `design/` 的 Draft 文档就地保留，仅新文档适用本规则。

## 后果

- 好处：与根仓库、QED-Tracker 规则一致——目录即可信度标签，评审状态与物理位置一致。
- 成本：设计确定时多一次迁移与引用更新；计划文档承载设计正文体积增大。
- 风险：迁移遗漏产生双份事实；由任务关闭时的三态梳理检查兜底。

## 取代

取代：无（新增规则，细化 [文档治理规范](../standards/doc-governance.md) 的 design/plans 分类边界）。
被取代：无。来源范本：QED-Engine 根仓库
[ADR 0011](https://github.com/QED-Engine/qed-engine/blob/main/docs/history/adr/v0.1/0011-pending-design-location.md)。
