# 项目状态快照

设计状态：Accepted
实现状态：In Progress
确认状态：暂定
最后更新：2026-09-17
关联代码：无（状态快照，不映射具体模块）
关联测试：无
关联 ADR：[ADR 0001](../adr/0001-v2-exploration-direction.md)、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)

## 用途

本文件是 Axiom-Flow 开发状态的单一事实源入口：进场先读本表，30 秒掌握「项目现在到哪了」。
具体任务状态以[待做任务](todo.md)为准，未来方向以[能力路线图](roadmap.md)为准；本表只保存
「当前实现状态」快照。

**当前版本 v0.1 目标**：解析管理闭环——书目同步（af_books）→ ingest（页图）→ 解析（引擎适配，
逐页/全本）→ 对照渲染 → 块级人工校验修改，对齐根仓库第三轮主线 ARCH-020。

## 能力状态

| 能力 | 形态 | 状态 | 说明 |
| --- | --- | --- | --- |
| 8902 HTTP 服务（`/api/v1`） | 8902 | 已服务化 | FastAPI；现有 books/pages/image/manifest/parse-jobs 读文件系统；生命周期脚本 `scripts/axiom_flow_service.py`。 |
| 解析管线（ingest/schemas） | Windows 编排层 | 已实现 | PDF 导入、页图渲染（150 DPI+）、book.json（SHA-256）、统一 blocks/page/book/job 模型。 |
| 模型与引擎对接 | 引擎适配器 | 规划 | **解析直连本地模型服务**（不经 8900 网关）；MinerU 先落地，PaddleOCR-VL/qwen-vl 预留（[model-integration](../design/model-integration.md)，[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)）。 |
| 模型生命周期 | 根仓库 8900 托管 | 已就绪 | 模型文件、启停、探针与资源互斥归 QED-Engine（`scripts/image-model/`、`/models`）；本仓库只消费模型服务端点。 |
| orchestrator 任务编排 | af_parse_jobs + af_pages | 待实现 | ARCH-003（取代原 state.sqlite 方案）。 |
| af_* 书目同步与块编辑 | MySQL `qed` 库 af_* 私有四表 | 规划 | ARCH-013/014/006；表结构见 [database-design](../architecture/database-design.md)。 |
| 分块与解析对照 | blocks → chunk + 页图 vs 重渲染 | 待实现 | ARCH-004/005；v0.1 闭环组成部分。 |
| 套1 代表书验收 | 教程 Rudin 中译 + 习题集吉米多维奇 | 待验证 | ARCH-009；分阶段验证（前 20 页冒烟 → 全量）。 |

## 当前主线

- **v2 探索纪元**：Axiom-Flow 为 QED-Engine 后端解析组件（[ADR 0001](../adr/0001-v2-exploration-direction.md)），
  推倒 v1 重建，聚焦解析与渲染数据供给。
- **ARCH-020 重构（2026-09-14）**：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)
  确立模型生命周期归 8900、解析管线归本仓库并直连模型服务；af_* 重定义为四表；本仓库文档
  体系（AGENTS/architecture/design/trackers/guides）按新形态重构。
- **已交付**：WSL 容器基建（V2-001）、新包结构与 schemas（V2-002）、ingest（V2-003）、
  qwen-vl 最小闭环（V2-010）、生命周期脚本（V2-011/012）、模型模式与 MinerU 移交（V2-014）、
  文档体系范本与治理对齐（V2-015/017）。
- **进行中**：ARCH-001 解析管理闭环（本期主线）；PLAN-002 配置中心对齐与分阶段重构
  （2026-09-17 起：先配置/骨架、再功能；对齐根仓库 PLAN-046/REQ-075——`QED_OCR_MODEL_URL`
  5002、模型生命周期归 8900）。
- **待启动**：ARCH-013/003/014/015/016/005/006/009/004；REQ-001 请求 8900 适配新契约。
- **长期**：ARCH-010 文档体系对齐维护；ARCH-011 批量解析；ARCH-012 探索路线图。

## 维护规则

- 服务实现状态、当前主线或能力归属变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 [todo.md](todo.md) / [roadmap.md](roadmap.md)）。
