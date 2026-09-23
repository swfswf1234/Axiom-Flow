# 待做任务

状态：Current
最后更新：2026-09-24

本表是全部未关闭工作的统一导航，按「轮次 → 阶段」分节。本表只回答「做什么、为了什么目的、
现在到哪/卡在哪」；日期化进度与证据一律写入承载计划（[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)）。
已关闭任务见[关闭台账](completed.md)，实时现状见[项目状态快照](project-status.md)。v1 台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。

## v0.1 主线：解析管理闭环（**已收口 2026-09-24，本节无开放行**）

v0.1 的全部任务行均已进入终态，证据与逐条判定唯一安家于 [completed.md](completed.md)（实时现状见
[项目状态快照](project-status.md)）。收口批＝**PLAN-013**（A 类，W0~W6）关掉最后四行：
ARCH-005 `Achieved`、ARCH-006 `Partial`（按用户裁决「以清除前那次 PASS 为准」改判关闭）、PLAN-008
与主线聚合行 ARCH-001。链路事实：书目同步（af_books）→ ingest（页图 + 产物落 `QED_DATA_ROOT/parsed/`）
→ 解析（引擎适配，逐页/全本，ADR 0007 六态语义与质量信号）→ 对照渲染（只读端点）→ 块级人工校验修改；
设计见 [parsing-management](../design/parsing-management.md)、[parsing-pipeline](../design/parsing-pipeline.md)、
[model-integration](../design/model-integration.md)。

**版本声明**：v0.1 **开发完成**（交付/未交付边界见 [project-status.md](project-status.md)「当前版本」段
与 [roadmap.md](roadmap.md)）。唯一移交项＝重建后代际的块编辑真机写入证据，由根仓库在下一联调窗口自证。

**仍然生效的三条运维事实**（原「数据面运维」「文档形式正规化」「跨项目请求」三节压缩至此，均无开放行）：

1. **产物代际**：2026-09-23 的 `POST /api/v1/admin/purge` 全项目清除已把三本生效版本产物与 2 条人工
   校验行删净，当晚仅重建 b05（全本 317/317 激活）与 LA-b01（3/486）⇒ **任何新联调或验收必须先重新
   ingest + 解析**，下一重建窗口须用户另批 D 类（可与 ARCH-011 批量合并）。
2. **环境侧交接**：WSL 容量、发行版空闲回收与 `mineru-api` 容器归 QED-Engine 图像模型维护线（用户裁决
   2026-09-22），鉴别判据与处置见 [operations.md](../guides/operations.md)，本仓库不再投入。
3. **文档面**：形式正规化两轮（PLAN-012 design、PLAN-014 architecture）均已关闭且七篇 `确认状态` 转
   「已确认」；剩余长尾（`src`/`scripts` 文件头旧条文编号、`scripts/tmp/`、版本号双源）挂在长期行
   ARCH-010。新入站跨项目请求按 [跨项目协作流程](../standards/cross-project-collaboration.md) 登记。

## v1.0 主线：解析与知识库优化轮（对齐根仓库 ARCH-025，2026-09-22 用户裁决立项登记）

根仓库 ARCH-025 轮（REQ-089 第一批 DeepTutor 对照调研）产出的优化项下沉本仓库承载，
作为本仓库 1.0 版本任务登记。**本轮只立项登记，不排实施**：实施前置 = v0.1 已收口（ARCH-005/006、
PLAN-008 与 ARCH-001 于 2026-09-24 关闭）⇒ 只剩产物重建（D 类窗口）与你的排期裁决；条目清单与判据见
[PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md)。

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-021 | 主线 | 中 | Open | V1.0 解析与知识库优化轮（聚合行）：解析产物易消费优化 + 知识库面（RAG 切片与检索）向本仓库 1.0 版本收敛 | PLAN-009 登记条目全部关闭且 V1.0 端到端验收通过 | 子行：ARCH-022/023、PLAN-009。登记来源＝根仓库 ARCH-025 滚动壳「REQ-089 第一批调研承接」节；本轮只登记不排期（用户裁决 2026-09-22） |
| ARCH-022 | 支线 | 中 | Open | 解析产物大模型易消费优化（P1 重跑判定签名、P2 manifest-last 就绪、P3 blocks 语义分诊 ledger） | P1~P3 实施带契约测试且全本复测通过 | 承载 ⇒ [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md)；前置＝产物重建（D 类窗口）+ 用户排期裁决（v0.1 已收口） |
| ARCH-023 | 支线 | 中 | Open | 知识库面（R1 索引版本行增量、R2 块级知识单元 self_ref、R3 向量+BM25+RRF+rerank 检索配方、R4 数学感知切分；检索端点入 8902 契约） | R1~R4 实施带契约测试且根侧探索验证界面验收 | 承载 ⇒ [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md)；知识图谱暂不做（roadmap 预留） |
| PLAN-009 | 主线 | 中 | Accepted | [V1.0 解析与知识库优化轮（v1-parsing-knowledge-optimization）](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) | 登记条目全部关闭且 V1.0 形态交付 | 2026-09-22 登记（用户裁决下沉本仓库为 1.0 任务）；条目台账与目标形态见壳 |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护：固定文档随实现同步 + 版本末期规范化整理轮 | 无单一终态；版本末期统一定去留 | 遗留：`src`/`scripts` 文件头旧条文编号 `V*`/`S*`、`scripts/tmp`、版本号双源。已关：PLAN-012（design 六段骨架）、PLAN-014（architecture 体裁化 + 四段骨架 + 契约表运行态对账 + 七篇转已确认）。证据见 [completed.md](completed.md) |
| ARCH-011 | 长期 | 低 | Open | 批量处理其余书籍 | 无单一终态；立项即拆 D 类计划并关闭本行 | 前置 ARCH-009 **已关闭**（2026-09-22，随 PLAN-007）；「先清后建」的运维工具＝ARCH-024（**2026-09-23 已交付关闭**，`POST /api/v1/admin/purge`）。立项仍需用户排期 + D 类窗口批准（真实数据根写入）。 |
| ARCH-012 | 长期 | 低 | Open | 探索路线图：Milvus 检索 / 知识图谱 / 数学解析器 / 学习交互闭环 | 无单一终态；逐项立项即拆出 | 见[路线图](roadmap.md)，本表无执行状态。 |

## 规则

- **一任务一承载**（[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)）：每个支线行必须在
  「任务」或「证据/下一条件」列引用承载计划文件（`../plans/*.md`），且该计划的「关联 Tracker」
  登记了本行 ID，或计划内含 `## 承载 <本行ID>` 节；长期行免承载；主线聚合行必须列全支线行 ID。
- **进度单源**：日期化进度与证据只写入承载文档；「证据/下一条件」列只保留最新一条结论 +
  承载引用（≤200 字），收口时**就地覆盖**，历史下沉承载计划「台账历史归档」节与 completed.md。
- **关闭条件**：每行必填一句话可检验判据（≤80 字）；写不出者不得成为行（A 类小改不入台账，
  以 git 提交与验证记录承接）。
- **ID 前缀**：`ARCH`=主线/支线实现；`REQ`=跨项目请求；`PLAN`=计划类（必有 `plans/` 正文）。
  `DEFECT`=退役前缀：不再新增，新缺陷登记为支线行挂承载；存量 ID 保留至自然关闭。
- **类别** `主线 / 支线 / 长期`；主线=聚合行与计划类行，支线=可独立收口，长期=无终态。
- **状态** `Open / In Progress / Blocked`；支线行同时 In Progress ≤3（超出须先收口或改判，用户
  可显式豁免）；Blocked 必须声明证据、恢复条件与责任位置（责任与恢复条件写进「证据/下一条件」
  列）。`PLAN` 行状态与计划正文 `状态：` 逐字一致（契约测试守护），故可为 `Accepted / In Progress /
  Blocked`。
- **排版**：按「轮次 → 阶段」分节，每节一句推进顺序；跨项目请求以
  `**请求接收（来源仓库）**——` / `**请求发出（目标：仓库名）**——` 粗体标记。
- Plan 行镜像 `docs/plans/` 活跃计划正文（标题、链接、状态、关联 Tracker）。
- **收口顺序**（缺任一项视为未收尾）：① 结论写入承载文档 → ② 本行压缩并关闭 →
  ③ 迁移 [completed.md](completed.md)（证据在此安家）→ ④ `plans/index.md` 登记 Retain/Delete →
  ⑤ `project-status.md` 刷新 → ⑥ 全量门禁。
- 涉及子项目改造的请求在本表登记并标注目标仓库；根仓库不直接修改子项目文件
  （见[跨项目协作流程](../standards/cross-project-collaboration.md)）。

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
