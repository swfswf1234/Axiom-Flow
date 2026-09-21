# 待做任务

状态：Current
最后更新：2026-09-21

本表是全部未关闭工作的统一导航，按「轮次 → 阶段」分节。本表只回答「做什么、为了什么目的、
现在到哪/卡在哪」；日期化进度与证据一律写入承载计划（[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)）。
已关闭任务见[关闭台账](completed.md)，实时现状见[项目状态快照](project-status.md)。v1 台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。

## v0.1 主线：解析管理闭环（对齐根仓库 ARCH-020 第三轮）

链路：书目同步（af_books）→ ingest（页图 + 产物落 `QED_DATA_ROOT/parsed/`）→ 解析（引擎适配，
逐页/全本）→ 对照渲染 → 块级人工校验修改。设计见 [parsing-management](../design/parsing-management.md)、
[parsing-pipeline](../design/parsing-pipeline.md)、[model-integration](../design/model-integration.md)。

**推进顺序**：产物按版本划分（**PLAN-004，本地已交付，剩真机 C/D 窗口**）→ 真机打通收尾（PLAN-003，
仅剩外部/裁决依赖）→ 对照与块编辑联调（ARCH-005/006）→ 代表书验收（ARCH-009）→ 跨项目回执
（REQ-001/080/081）；裁决项 DEFECT-006/007 与 ADR 0005（DEFECT-001）择机插入，DEFECT-008 无阻塞可
随时开工。各支线过程史实在其承载计划正文。

### 主线聚合

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-001 | 主线 | 高 | In Progress | 解析管理闭环（聚合行）：同步/ingest/解析/对照/块编辑全链路达成（对齐根仓库 ARCH-020） | 下列全部支线行关闭且代表书验收达标 | 子行：ARCH-003/004/005/006/009/016、DEFECT-001/006/007/008、REQ-001/080/081。现状：引擎对齐、归一化与台账承载契约已关闭，版本划分本地完成、真机窗口待批。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| PLAN-002 | 主线 | 高 | In Progress | [Axiom-Flow 配置简化与解析重构（axiom-flow-refactor）](../plans/2026-09-17-axiom-flow-refactor.md) | 8 条成功标准逐条判定：标准 6/7 随 PLAN-003 关闭即达成，标准 8 回执完成 | Step 1/2-A~E 已交付并真机验证；Step 3 由 PLAN-003 承接。判定表见计划「2026-09-20 复核与收窄」节。 |
| PLAN-003 | 主线 | 高 | In Progress | [解析真机打通：设计偏差核对与契约修正（parse-live-bringup）](../plans/2026-09-20-parse-live-bringup.md) | C2 的 D3 删除演练与 D5/W3（ADR 0005）执行并登记 | W0~W8 与 C1/C3 达成，DEFECT-002~005 已关闭（门禁 303）；剩余全为外部/裁决依赖。判据与证据见计划「收尾执行设计/登记」节。 |
| PLAN-004 | 主线 | 高 | In Progress | [解析产物按版本划分：versions/&lt;job_id&gt; 落盘与生效指针（req080-parse-version-layout）](../plans/2026-09-20-req080-parse-version-layout.md) | 条文 V1~V10 晋升 + S1~S6 本地全绿 + B→C→D 窗口真机取证 + REQ-080 回执 | **2026-09-21 P2~P7 完成**：版本布局代码 + 迁移 0002 + 六份文档晋升，S1~S6 本地取证矩阵见计划「实施登记」，门禁 `352 passed, 2 skipped` + ruff。下一条件：B→C→D 单窗口批准（备份+迁移+幽灵行清理+单次重解析）与回执发出。 |

### 真机打通后的遗留卡点：落库回归网与状态语义

**推进顺序**：DEFECT-008 可直接开工（B 类小改）；DEFECT-006/007 属用户裁决项（状态机语义）；
DEFECT-001 受 ADR 0005 评审阻塞；ARCH-003/016 随上述各项收口。

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| DEFECT-001 | 支线 | 高 | Blocked | MySQL 级提交边界回归网：进度与页状态落库有测试守护（代码侧页级 commit 已交付） | ADR 0005 执行后 MySQL 双 Session 提交边界集成测试就位 | 真机 L4~L6 已证不落库问题不存在；剩余是回归网缺位。阻塞：[ADR 0005](../adr/0005-mysql-integration-test-isolation.md) Draft 评审（责任：用户裁决）。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| ARCH-003 | 支线 | 高 | Blocked | 解析编排交付收口：af_parse_jobs + af_pages 全本/逐页/重试/质量信号语义完备并经真机验证 | DEFECT-006/007 语义成文实施且冒烟复跑通过 | 代码、真机四条链路与固化冒烟均已交付；阻塞于 DEFECT-006/007 裁决（责任：用户）。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| ARCH-016 | 支线 | 高 | Blocked | MinerU 引擎适配器与统一 blocks 归一化稳定化（PaddleOCR-VL/qwen-vl 预留） | 代表书类型分布取证（随 ARCH-009）+ 真机零丢弃对账复跑 | 直连、端点契约、槽位分派、DEFECT-004/005 修复均已关闭；复跑需 MinerU 5002 在线。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| DEFECT-006 | 支线 | 中 | Open | 范围校验与部分失败语义成文实施：pages 越界拒收、completed 不掩盖失败页、书级状态拆分 | pages 越界 422 + 失败语义经裁决成文实施 + 真机幽灵行清理执行 | 待用户裁决「部分成功」判据；幽灵行清理并入 PLAN-004 C 窗口。取证见 [PLAN-003](../plans/2026-09-20-parse-live-bringup.md)「收尾执行登记」。 |
| DEFECT-007 | 支线 | 中 | Open | 被 kill 任务不再遗留假 running：状态机语义成文（reaper/启动期归一/已知语义三选一） | 处理方式经 ADR 级裁决成文并实施带测试 | 取证：`5f43593a12cc` 至今 running（`L9_final_jobs.txt`）；不阻断续跑但与 database-design 不一致。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| DEFECT-008 | 支线 | 中 | Open | `.env` 值尾行内 `#` 注释剥离：防上层 .env 带注释值成为坏 URL/路径 | 单测红→绿：值尾 ` #…` 剥离且引号内 `#` 不误伤 | B 类一行改动 + 单测，可直接开工；来源 PLAN-003 G5 走查（留档 D4_G5）。根仓库 `.env` 只读。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |

### 对照、分块与验收

**推进顺序**：代表书在修复后代码上重解析（随 PLAN-004 D 步）后：ARCH-009 取证 → ARCH-005/006
前端联调 → ARCH-004 约定收口。

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-004 | 支线 | 中 | In Progress | 分块交付收口：chunks 随真机新产物重建且版本目录归属约定成文 | 新产物 chunks 真机生成 + 归属约定写入 parsing-pipeline | 归属约定**已成文**（`chunks.json` 每版本一份、落 `versions/<job_id>/`；见 parsing-pipeline 产物树与「分块」节）。下一条件：PLAN-004 D 步真机重解析生成新 chunks。承载：[PLAN-004](../plans/2026-09-20-req080-parse-version-layout.md) |
| ARCH-005 | 支线 | 高 | Open | 解析对照与质量度量：页图 vs 重渲染叠框与质量信号进对照视图 | 8903 前端真机联调通过（根仓库 ARCH-020-D/UI） | 代码侧前置（DEFECT-004/005）已关闭；需重解析代表书 + MinerU 在线 + 8903 排期。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| ARCH-006 | 支线 | 高 | Open | 渲染数据供给与块编辑真机闭环：edit 写入与 pages 合并回显交付 | 代表书重解析后 af_block_edits 真机写入且联调通过 | 端点与仓储已交付、L7 读取面全绿；编辑键不回溯，写入前必须重解析（现 af_block_edits 0 行）。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |
| ARCH-009 | 支线 | 中 | Open | 套1 代表书验收：Rudin 中译 + 吉米多维奇全量解析质量达标 | 前 20 页冒烟→全量公式可渲染 ≥90% + 类型分布取证登记 | 前置 DEFECT-002/004/005 已全部关闭；需 MinerU 在线 + 修复后代码重解析。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md) |

### 跨项目请求

**推进顺序**：PLAN-004 的 B→C→D 单窗口 + 重解析完成后发 REQ-081 二次回执并收 REQ-080；REQ-001
等根仓库读取复核材料后回执。

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| REQ-001 | 支线 | 高 | Open | **请求发出（目标：QED-Engine）**——8900 数据域适配 af_* 新契约并回执 | 根仓库读取复核材料并在其 todo 登记回执 | 复核材料（W8-g）已出；版本布局二次回执随 PLAN-004 收口发出。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md)「跨项目回执稿」 |
| REQ-081 | 支线 | 高 | Blocked | bbox 失真（根仓库报告）修复回执并支持其 §13 复测 | 根仓库 §13 复测通过并回执 | 本方修复（DEFECT-004/005）已关闭；受害面为全部已解析页（38/486），复测须等 PLAN-004 D 步单次重解析后二次回执。承载：[PLAN-003](../plans/2026-09-20-parse-live-bringup.md)「跨项目回执稿（REQ-081）」 |
| REQ-080 | 支线 | 高 | In Progress | **请求接收（QED-Engine）**——解析产物按版本划分实施并发实施回执 | PLAN-004 关闭且回执发出 | 本地实施面已完成，**回执稿 + 待根确认清单（指针式是否满足「根目录即当前版」、manifest 基准、编辑不随版本继承、回滚端点）已成文**于计划「REQ-080 实施回执稿」节。下一条件：C 窗口迁移 + D 步重解析后正式发出。承载：[PLAN-004](../plans/2026-09-20-req080-parse-version-layout.md) |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 关闭条件 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护：固定文档随实现同步 + 版本末期规范化整理轮 | 无单一终态；版本末期统一定去留 | 台账承载契约治理已随 PLAN-005 关闭并入 ADR 0006；遗留：design/architecture 确认状态补登记、空目录清理、跨仓库相对链接清扫。 |
| ARCH-011 | 长期 | 低 | Open | 批量处理其余书籍 | 无单一终态；立项即拆 D 类计划并关闭本行 | 前置：ARCH-009 关闭。 |
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
  可显式豁免）；Blocked 必须声明证据、恢复条件与责任位置。`PLAN` 行状态与计划正文 `状态：`
  逐字一致（契约测试守护）。
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
