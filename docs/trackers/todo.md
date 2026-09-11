# 待做任务

状态：Current
最后更新：2026-09-11

本表是全部未关闭工作的统一导航。v1 台账已归档
（`docs/history/v1-20260814/trackers/todo.md`）。任务 ID 采用稳定前缀-三位序号；已关闭任务见
[关闭台账](completed.md)。当前版本 v0.1 目标见 [项目状态快照](project-status.md)。

## 本期主线：v0.1 解析对照闭环（MinerU）

对齐根仓库第三轮主线 ARCH-020（与 Axiom-Flow 联调，至少完成一个教程解析）。链路：
解析（MinerU，经 8900 网关）→ 分块 → 对比解析效果 → 渲染回去 → QED-Engine 前端对照。

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-001 | 主线 | 高 | Open | v0.1 解析对照闭环：MinerU 解析 → 分块 → 对比解析效果 → 渲染回去 → QED-Engine 前端对照 | 支线 ARCH-002~009 关闭；一个教程端到端对照验收 |
| ARCH-002 | 支线 | 高 | Open | MinerU 解析接入（qed-engine 模式经 8900 网关，替换当前 qwen-vl 逐页） | 单页 PDF 经网关解析成功；产物 `source=mineru` |
| ARCH-003 | 支线 | 高 | Open | orchestrator：任务编排 + state.sqlite（jobs/pages）+ 质量信号（空块率/公式置信度/表格成功数） | 单元 + 集成（mock）测试通过；根仓库 C 组联调前置 |
| ARCH-004 | 支线 | 高 | Open | 分块模型：blocks → chunk（paragraph/formula/table 为切分点，为 RAG 预留） | 设计 + 契约测试；产物 chunk 结构落盘 |
| ARCH-005 | 支线 | 高 | Open | 解析对照与质量度量：页图 vs 重渲染对照 + 质量信号落盘 | 对照视图数据契约 + 质量信号测试 |
| ARCH-006 | 支线 | 高 | Open | 渲染数据供给对接：blocks+md+页图+bbox → QED-Engine 前端（REQ-034/042 衔接） | 前端对照视图联调通过 |
| ARCH-007 | 支线 | 中 | In Progress | API v1 契约冻结（parse-jobs/books/pages/manifest） | 契约冻结待根仓库 REQ-034 评审确认 |
| ARCH-008 | 支线 | 中 | Open | af_* 书目同步与块判定（原 V2-013，根仓库 REQ-042）：af_books/af_block_reviews + `POST /books/sync` + 块判定端点 | 用户评审确认后执行；回执 REQ-042 |
| ARCH-009 | 支线 | 中 | Open | 套1 代表书验收（教程 Rudin 中译 + 习题集吉米多维奇） | 前 20 页冒烟 → 全量（≥90% 公式可渲染） |
| REQ-001 | 支线 | 高 | Open | 请求：QED-Engine——8900 `/llm/vision` 回传页级 blocks+bbox（对照与渲染需要） | 根仓库回执后关闭；ARCH-006 前置 |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 证据/下一条件 |
| --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护：固定文档随实现同步；版本末期规范化整理轮（原 V2-016） | 每次版本确认前执行一轮 |
| ARCH-011 | 长期 | 低 | Open | 批量处理其余书籍（原 V2-009） | ARCH-009 关闭后拆 D 类计划执行 |
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
