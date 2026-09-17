# 待做任务

状态：Current
最后更新：2026-09-17

本表是全部未关闭工作的统一导航。v1 台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。任务 ID 采用稳定前缀-序号；已关闭任务见
[关闭台账](completed.md)。当前版本 v0.1 目标见 [项目状态快照](project-status.md)。

## 本期主线：解析管理闭环（对齐根仓库 ARCH-020）

链路：书目同步（af_books）→ ingest（页图）→ 解析（引擎适配，逐页/全本）→ 对照渲染 →
块级人工校验修改（判定/备注/文字/范围）。设计见
[parsing-management](../design/parsing-management.md)、[parsing-pipeline](../design/parsing-pipeline.md)、
[model-integration](../design/model-integration.md)。

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-001 | 主线 | 高 | In Progress | 解析管理闭环：af_* 四表 + 同步/ingest + 引擎适配 + 编排 + 对照编辑（对齐根仓库 ARCH-020） | 支线关闭；Rudin 教程端到端验收 |
| PLAN-002 | 主线 | 高 | In Progress | [Axiom-Flow 配置简化与解析重构（axiom-flow-refactor）](../plans/2026-09-17-axiom-flow-refactor.md) | 配置简化 → 契约 → DB/骨架 → 基本解析 → 分块；每阶段门禁 |
| REQ-075 | 支线 | 高 | Open | 请求接收：QED-Engine——MinerU 直连配置同步（端口 8002→5002、键 `QED_MINERU_URL`→`QED_OCR_MODEL_URL`，不保留别名） | PLAN-002 Step 1 承载；对齐后回执根仓库 |
| ARCH-013 | 支线 | 高 | Open | af_* 四表建表（Alembic）+ 仓储层（af_books/af_parse_jobs/af_pages/af_block_edits） | 已实现（PLAN-002 Step 2-B）；迁移升降级单测通过；待 qed 库真机迁移冒烟 |
| ARCH-003 | 支线 | 高 | Open | 解析编排：af_parse_jobs + af_pages + 逐页/全本 + 重试 + 质量信号（取代 state.sqlite 方案） | 已实现（PLAN-002 Step 2-C）；待 MinerU 真机冒烟 |
| ARCH-014 | 支线 | 高 | Open | 书目同步与 CRUD：POST /books/sync + GET/PATCH/DELETE /books | 已实现（PLAN-002 Step 2-C）；幂等 upsert；契约测试全绿 |
| ARCH-015 | 支线 | 高 | Open | ingest：PDF→页图 + book.json（sha256/page_count），产物落 `<QED_DATA_ROOT>/parsed/<domain>/<course>/<book_id>/` | 已实现（PLAN-002 Step 2-C）；单测 + 幂等覆盖 |
| ARCH-016 | 支线 | 高 | Open | 引擎适配器：MinerU 接入 + 统一 blocks 归一化（PaddleOCR-VL/qwen-vl 预留）；llm_client/fallback 退役 | 已实现（PLAN-002 Step 2-B/2-C）；mock 单测通过；真实冒烟待做 |
| ARCH-005 | 支线 | 高 | Open | 解析对照与质量度量：页图 vs 重渲染 + 质量信号落盘 | 质量信号已落盘（Step 2-C）；对照视图待前端联调 |
| ARCH-006 | 支线 | 高 | Open | 渲染数据供给与块编辑：pages/blocks/image/manifest + edit 端点（af_block_edits） | 端点已实现（Step 2-C/E）；前端对照视图联调待做 |
| ARCH-009 | 支线 | 中 | Open | 套1 代表书验收（教程 Rudin 中译 + 习题集吉米多维奇） | 前 20 页冒烟 → 全量（≥90% 公式可渲染） |
| ARCH-004 | 支线 | 中 | Open | 分块模型：blocks → chunk（paragraph/formula/table 为切分点，为 RAG 预留） | 已实现（PLAN-002 Step 2-D：chunks.json + GET /chunks）；规则单测通过 |
| REQ-001 | 支线 | 高 | Open | 请求：QED-Engine——8900 数据域适配 af_* 新契约（books CRUD/ingest、`/review`→`/edit`、chunks 透传） | PLAN-002 Step 1 契约冻结后发起/回执；ARCH-014/015/006 前置 |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护：固定文档随实现同步；版本末期规范化整理轮 | 每次版本确认前执行一轮 |
| ARCH-011 | 长期 | 低 | Open | 批量处理其余书籍 | ARCH-009 关闭后拆 D 类计划执行 |
| ARCH-012 | 长期 | 低 | Open | 探索路线图：Milvus 检索 / 知识图谱 / 数学解析器 / 学习交互闭环 | 见 [路线图](roadmap.md)，按需立项 |

## 规则

- **ID 前缀**：`ARCH`=主线/支线实现；`REQ`=跨项目请求；`PLAN`=计划类（必有 `plans/` 正文）；
  `DEFECT`=缺陷。
- **类别**只允许 `主线 / 支线 / 长期`：主线 = 大类目标（聚合支线）；支线 = 主线细则，可独立
  完成；长期 = 持续存在、无单一终态。
- **状态**只允许 `Open / In Progress / Blocked`；阻塞必须声明证据、恢复条件和责任位置。
- **优先级**只允许 `高 / 中 / 低`。
- 涉及子项目改造的请求在本表登记并标注目标仓库（`请求：<目标仓库>`）；根仓库不直接修改子项目
  文件（见[跨项目协作流程](../standards/cross-project-collaboration.md)）。
- Plan 行镜像 `docs/plans/` 活跃计划正文（标题、链接、状态、关联 Tracker）。
- 任务终态时从本表原子移除并写入 [关闭台账](completed.md)。

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
