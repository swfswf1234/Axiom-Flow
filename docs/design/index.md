# 设计索引

状态：Current
最后更新：2026-09-23

本目录保存当前流程、接口、数据模型与验收契约（v2 纪元）。元数据规则见
[文档治理规范](../standards/doc-governance.md)；历史设计一律在 `docs/history/`（v1 在
`history/v1-20260814/design/`，使命完成的 Superseded 文档在 `history/design/`），本索引不登记它们。

本目录是**设计职责登记处**：每篇回答一个唯一问题，边界在此一次定死；后续 plans/ 文档归档晋升或新增
设计文档，先在此确定唯一归属，**禁止同类内容多文并存**。三篇同构于六段骨架（目标与非目标 /
边界与事实源 / 核心设计 / 失败与质量语义 / 验收判据 / 追溯），条文编号只在篇内解析
（`PP-n` / `PM-n` / `MI-n`；跨篇引用一律写成 `篇的条文号` 的链接，日期化过程一律在
[trackers/completed.md](../trackers/completed.md)）。`核心设计` 内的守则段统一名为**「确定规范」**：按主题以 H4 分组，每个主题一张表，一行 = `编号 | 守则（结论 ＋ 判据）| 理由／例外／边界 | 实现落点 → 守护测试`（原篇末「条文表」已并入，登记表每篇只此一处）。

## 当前设计

| 文档 | 唯一问题 | 设计状态 | 实现状态 | 条文 | 不管（指向事实源） |
| --- | --- | --- | --- | --- | --- |
| [PDF 解析与渲染管线](parsing-pipeline.md) | 一份 PDF 怎样变成可消费的产物，以及**什么算合格** | Accepted | Verified | `PP-1~PP-20` | 状态机与 af_* 列（→ [parsing-management](parsing-management.md)、[architecture/database-design](../architecture/database-design.md)）；端点契约（→ [architecture/api](../architecture/api.md)）；引擎配置与协议（→ [model-integration](model-integration.md)）；模块清单（→ [code-map](../architecture/code-map.md)） |
| [解析管理闭环](parsing-management.md) | 一本书从「已验证书目」到「人工校验完成」，**状态如何流转、职责落在哪一段** | Accepted | Verified | `PM-1~PM-17` | 表结构 DDL（→ [architecture/database-design](../architecture/database-design.md)）；端点参数与响应（→ [architecture/api](../architecture/api.md)）；产物生成与质量判据（→ [parsing-pipeline](parsing-pipeline.md)） |
| [模型与引擎对接](model-integration.md) | 本仓库**怎样与模型服务对话**（配置、适配器契约、失败语义） | Accepted | Verified | `MI-1~MI-15` | 模型生命周期与调参（→ 根仓库 8900）；归一化映射与坐标换算（→ [parsing-pipeline](parsing-pipeline.md)）；任务状态机（→ [parsing-management](parsing-management.md)） |

## 术语（三篇共用，裸「块」禁用）

| 定名 | 标识 | 归属条文 |
| --- | --- | --- |
| 版面块 | block | [PP-7~PP-10](parsing-pipeline.md)（结构与归一化）、[MI-7、MI-8](model-integration.md)（引擎侧事实） |
| 解析窗口 | window | [PP-11](parsing-pipeline.md)（切分与失败域）、[MI-6](model-integration.md)（协议字段） |
| 知识块 | chunk | [PP-19](parsing-pipeline.md) |
