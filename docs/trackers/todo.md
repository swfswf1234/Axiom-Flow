# 待做任务

状态：Current
最后更新：2026-09-30

本表是全部未关闭工作的**导航表**，按「轮次 → 阶段」分节；每行只回答三件事——**做什么**（任务）、
**完成时系统长什么样**（成功标准）、**去哪读**（承载）。三列的口径正文（列界、禁词、唯一指针判据、
禁日期）只在[任务生命周期](../standards/task-lifecycle.md)「任务台账与承载」，立法见
[ADR 0013](../adr/0013-ledger-row-three-elements.md)（三要素）与
[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（承载与进度单源），本表不复制；现状、日期、
裁决全文与验收判据一律只活在承载计划与 `completed.md`。台账内唯一的进度信号是「状态」列。已关闭任务
见[关闭台账](completed.md)，实时现状见[项目状态快照](project-status.md)；**已收口版本不在本表设节**。
v1 台账已归档（`docs/history/v1-20260814/trackers/todo.md`）。

## v1.0 主线：解析与知识库优化轮（对齐根仓库 ARCH-025，2026-09-22 用户裁决立项登记）

根仓库 ARCH-025 轮（REQ-089 第一批 DeepTutor 对照调研）产出的优化项下沉本仓库承载，条目台账与
判定表在 [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md)。**推进顺序**（用户裁决
2026-09-22）＝先 v0.1 侧收口与真机联调、再排本表条目；本轮主攻位、实施次序、待裁清单与真机面排期
一律只登记在[项目状态快照](project-status.md)与各承载计划壳，本表不复制。原「实施前置＝产物重建
D 类窗口」随 ARCH-011 关闭而解除（判据重复，终态与操作规程见 `completed.md` 该行与 PLAN-009 承载节）。
本轮已关行（ARCH-011/034、REQ-095、PLAN-015/016/017/020/021/022）终态一律查
[completed.md](completed.md)，本表不重复登记；入站请求号不单独立行（一任务一行，
[ADR 0011](../adr/0011-ledger-one-row-per-task.md)）。

| ID | 类别 | 优先级 | 状态 | 任务 | 成功标准 | 承载 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-021 | 主线 | 中 | Open | V1.0 解析与知识库优化轮（聚合行）：把产物从「解析得出来」升级为「大模型易消费＋可检索」。解析面＝P1 重跑判定签名与受理门、P2 就绪口径 DB 单源、P3 块策略版本化＋逐块分诊 ledger（ARCH-022）；知识库面＝R1 索引版本行增量、R2 块级知识单元与 `self_ref` 锚点、R3 向量＋BM25＋RRF＋可选 rerank 检索配方、R4 数学感知切分（ARCH-023）；交付含 8902 检索端点契约 | ARCH-022/023 两支线终态齐备，且 b05 全链路检索命中、命中可回溯到页与块 | [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| ARCH-022 | 支线 | 中 | Open | 解析产物增量可判与可信消费（P 系列）：P1 `parser_signature` 只哈希影响块产物的参数，`(源哈希, 签名)` 双全等默认不重跑、`force` 才重跑，并配只读重跑清单与过时清点；P2 就绪以 DB 元数据为唯一事实源（manifest 降为完整性校验数据，不得作第二判据）；P3 `_TYPE_MAP` 与分块过滤规则版本化，未知字面值宁收不丢并落逐块 ledger | 双全等默认不重跑、`force` 才重跑；就绪只读 DB 判定；逐块分诊落 ledger 且未知字面值不丢 | [PLAN-019](../plans/2026-09-28-parse-artifact-trust.md) |
| ARCH-023 | 支线 | 中 | Open | 知识库面（R 系列，8902 检索端点契约先行）：R1 索引版本行增量（换模型/换维度写新版本，旧版可读可回滚，同签名只追加不重建）；R2 块级知识单元＋`self_ref="blocks.json#/{index}"` 页与块序号锚点；R3 向量与 BM25 各取 2×top_k → RRF 融合 → 可选 CrossEncoder rerank，索引持久化且 top_k 按语料量 clamp；R4 数学感知切分（以 blocks 为切分单元、公式/定理块原子性，我方自研点）。R5 查询改写后置、R6 无 DB 存储与 GraphRAG 拍平拒绝 | 索引按签名增量写新版本可回滚；块级单元带 `self_ref` 页块锚点；向量＋BM25＋RRF 端点可检索且 top_k 随语料 clamp | [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| PLAN-009 | 主线 | 中 | Accepted | 做什么＝V1.0 条目登记与判定台账（PLAN-009 自承载）：P 系列解析面判定表、R1~R6 知识库面判定表、批量重建窗口的操作规程留档 | P 系列与 R 系列判定表条目全部落地：产物可检索、命中可回溯到页与块 | [V1.0 解析与知识库优化轮（v1-parsing-knowledge-optimization）](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| PLAN-018 | 主线 | 中 | Accepted | **请求接收（QED-Engine 根仓库 REQ-099）**；做什么＝W2 建页级 `af_page_regions` 表＋删除/合并/新增区域 API → W1 逐框裁剪提交 `parse_page` 的区域驱动解析（框数上限＋受理前 422 门）→ W3 页数据回服务端投影的区域集合 → W8 每页人工确认门（确认态落 `af_pages`，chunk 只聚合已确认页）→ W7 未完成页可跳过或列清单 → W6 回执根仓库 REQ-099；W4 目录长线、W5 节题取证 | 按框重解析生效；区域可删/并/增并落 `af_page_regions`；每页人工确认后才进 chunk；未完成页可跳过或列清单 | [解析页区域驱动重解析与块结构编辑承接实施（parsing-region-edit）](../plans/2026-09-26-parsing-region-edit-typography-request.md) |
| PLAN-019 | 主线 | 中 | Accepted | 做什么＝W2 `parser_signature` 加列迁移 → W3 重跑受理门 → W4 只读重跑清单端点 → W5 就绪以 DB 元数据为唯一事实源 → W6 块策略版本表＋逐块 ledger → W7 过时版本只读清点 → W8 文档晋升 → W9 真机窗口 | 签名相同不重跑、`force` 才重跑；就绪判定只读 DB；策略版本表与逐块 ledger 落盘；过时版本可只读清点 | [解析产物可信与增量重跑实施（parse-artifact-trust）](../plans/2026-09-28-parse-artifact-trust.md) |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 成功标准 | 承载 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护（长期）：① 固定文档随实现同步——`code-map.md` 五元组映射、`architecture/api.md` 端点表与运行态路由双向对账、`database-design.md` 列清单与 ORM 模型对账、`design/` 条文与实现落点→守护测试成对；② 版本末期规范化整理轮（体裁、骨架、元数据、术语）；③ 遗留清扫＝`src`/`scripts` 文件头裸条文编号 `V*`/`S*` 的悬空引用、包版本双源 `pyproject 0.4.0` vs `__version__ 0.5.0` | 三项终态齐备即退：固定文档与代码对账零漂移、规范化整理轮跑完、悬空条文编号与包版本双源清零 | [doc-governance.md](../standards/doc-governance.md) |
| ARCH-012 | 长期 | 低 | Open | 探索路线图维护（长期）：Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验与重渲染）、学习交互闭环四条方向的立项前调研与去留裁决；外部实验须先冻结假设/样本/内容哈希/预算/采纳门槛 | 四条方向各自拆成计划行或裁撤，本行随拆随退 | [roadmap.md](roadmap.md) |

## 规则

- **口径正文的唯一位置**＝[任务生命周期](../standards/task-lifecycle.md)「任务台账与承载」：三列语义与
  列界、成功标准禁词、承载列唯一指针、行内禁日期、一任务一承载、一任务一行、进度单源、并发上限
  （支线 `In Progress` ≤3）以该节为准；收口顺序六步在同文档「计划生命周期」第 5 步。本表不复制其措辞。
- **本表自有的读表事实**：
  - **ID 前缀**：`ARCH`=主线/支线实现；`REQ`=跨项目请求；`PLAN`=计划类（必有 `plans/` 正文）；
    `DEFECT`=退役前缀，不再新增、存量 ID 保留至自然关闭。
  - **类别** `主线 / 支线 / 长期`：主线＝聚合行与计划类行，支线＝可独立收口，长期＝无终态。
  - **状态** `Open / In Progress / Blocked` 是本表唯一的进度信号；`PLAN-*` 镜像行另可为 `Accepted`，
    且与计划正文 `状态：` 逐字一致（镜像守护）。Blocked 的证据与恢复条件写在承载计划。
  - **排版**：每节一句推进顺序；跨项目请求以 `**请求接收（来源仓库）**` /
    `**请求发出（目标：仓库名）**` 粗体标记写在「任务」列句首。
- 涉及子项目改造的请求在本表登记并标注目标仓库；根仓库不直接修改子项目文件
  （见[跨项目协作流程](../standards/cross-project-collaboration.md)）。

## 探索路线图

Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验/重渲染）、学习交互闭环
——见 [路线图](roadmap.md)，按需立项。
