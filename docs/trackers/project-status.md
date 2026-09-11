# 项目状态快照

设计状态：Accepted
实现状态：Implemented
确认状态：暂定
最后更新：2026-09-11
关联代码：无（状态快照，不映射具体模块）
关联测试：无
关联 ADR：[ADR 0001](../adr/0001-v2-exploration-direction.md)

## 用途

本文件是 Axiom-Flow 开发状态的单一事实源入口：进场先读本表，30 秒掌握「项目现在到哪了」。
具体任务状态以[待做任务](todo.md)为准，未来方向以[能力路线图](roadmap.md)为准；本表只保存
「当前实现状态」快照。

**当前版本 v0.1 目标**：解析对照闭环（MinerU）——解析（OCR）→ 分块 → 对比解析效果 → 渲染
回去 → QED-Engine 前端对照展示，对齐根仓库第三轮主线 ARCH-020。

## 服务状态

| 能力 | 端口/形态 | 状态 | 说明 |
| --- | --- | --- | --- |
| 8902 HTTP 服务（`/api/v1`） | 8902 | 已服务化 | FastAPI；books/pages/image/manifest/parse-jobs；契约测试通过；生命周期脚本 `scripts/axiom_flow_service.py`（start/stop/restart/status，`--mode local|qed-engine`）。 |
| 解析管线（ingest/schemas） | Windows 编排层 | 已实现 | PDF 导入、页图渲染（150 DPI+）、book.json（SHA-256）、统一 blocks/page/book/job 模型（V2-002/V2-003）。 |
| 模型调用（local/qed-engine） | qwen-vl-plus / 8900 网关 | 已实现 | `llm_client.py` 双模式兼容层：local 直连 qwen-vl，qed-engine 经 8900 网关；**MinerU 仅经网关可达**（V2-014）。 |
| MinerU 推理与容器 | 根仓库 `scripts/image-model/` 托管 | 已就绪 | 容器脚本随 V2-014 移交根仓库；本仓库不维护容器，经 8900 `/llm/vision` 调用。 |
| orchestrator 任务编排 | state.sqlite（jobs/pages）+ 质量信号 | 待实现 | ARCH-003（原 V2-004）；根仓库 C 组联调前置。 |
| 分块与解析对照 | blocks → chunk + 页图 vs 重渲染对照 | 待实现 | ARCH-004/ARCH-005；v0.1 闭环核心。 |
| af_* 书目同步与块判定 | MySQL `qed` 库 af_* 私有表 | 规划 | ARCH-008（原 V2-013）；契约草案见 [af-books-sync](../design/af-books-sync.md)，待用户评审冻结。 |
| 套1 代表书验收 | 教程 Rudin 中译 + 习题集吉米多维奇 | 待验证 | ARCH-009（原 V2-008）；分阶段验证（前 20 页冒烟 → 全量解析）。 |

## 当前主线

- **v2 探索纪元**：Axiom-Flow 为 QED-Engine 后端解析组件（[ADR 0001](../adr/0001-v2-exploration-direction.md)），
  推倒 v1 重建，聚焦解析与渲染数据供给。
- **已交付**：WSL 容器基建（V2-001）、新包结构与 schemas（V2-002）、ingest（V2-003）、
  qwen-vl 最小闭环（V2-010）、生命周期脚本（V2-011/012）、模型模式与 MinerU 移交（V2-014）、
  文档体系范本对齐（V2-015）、文档治理体系对齐（V2-017）。
- **进行中**：ARCH-001 v0.1 解析对照闭环（MinerU）；ARCH-007 API 契约冻结待根仓库 REQ-034
  评审；ARCH-008 af_* 待用户评审确认。
- **待启动**：ARCH-002 MinerU 接入、ARCH-003 orchestrator、ARCH-004 分块、ARCH-005 对照度量、
  ARCH-006 渲染对接、ARCH-009 代表书验收；REQ-001 请求网关回传页级 blocks+bbox。
- **长期**：ARCH-010 文档体系对齐维护；ARCH-011 批量解析；ARCH-012 探索路线图。

## 维护规则

- 服务实现状态、当前主线或能力归属变化时，更新本表并刷新「最后更新」日期。
- 本表不保存任务细节与未来规划（分别见 [todo.md](todo.md) / [roadmap.md](roadmap.md)）。
