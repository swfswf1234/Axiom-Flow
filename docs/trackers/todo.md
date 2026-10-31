# 待做任务

状态：Current
最后更新：2026-11-01

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
| ARCH-021 | 主线 | 中 | Open | V1.0 解析与知识库优化轮（聚合行）：把产物从「解析得出来」升级为「大模型易消费＋可检索」。解析面＝P1 重跑判定签名与受理门、P2 就绪口径 DB 单源、P3 块策略版本化＋逐块分诊 ledger（ARCH-022）；知识库面＝R1 索引版本行增量、R2 块级知识单元与 `self_ref` 锚点、R3 向量＋BM25＋RRF＋可选 rerank 检索配方、R4 数学感知切分（ARCH-023）；交付含 8902 检索端点契约；入站请求 REQ-139 由 PLAN-025 承载壳承接、REQ-142／REQ-143 由 PLAN-026 承载壳承接 | ARCH-022/023 两支线终态齐备，且 b05 全链路检索命中、命中可回溯到页与块 | [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| ARCH-022 | 支线 | 中 | In Progress | 解析产物增量可判与可信消费（P 系列）：P1 `parser_signature` 只哈希影响块产物的参数，`(源哈希, 签名)` 双全等默认不重跑、`force` 才重跑，配只读重跑清单与过时清点；P2 就绪、激活与续跑判据去文件系统（改读 `af_pages` 页行与生效指针，manifest 降为完整性数据）；P3 `_TYPE_MAP` 与分块过滤规则版本化，未知字面值收为兜底类型不丢并落页级 ledger | 双全等默认不重跑、`force` 才重跑；就绪与激活只凭 DB 页行与生效指针判定；未知字面值收为兜底类型并逐页落 ledger | [PLAN-019](../plans/2026-09-28-parse-artifact-trust.md) |
| ARCH-023 | 支线 | 中 | Open | 知识库面（R 系列，8902 检索端点契约先行）：R1 索引版本行增量（换模型/换维度写新版本，旧版可读可回滚，同签名只追加不重建）；R2 块级知识单元＋`self_ref="blocks.json#/{index}"` 页与块序号锚点；R3 向量与 BM25 各取 2×top_k → RRF 融合 → 可选 CrossEncoder rerank，索引持久化且 top_k 按语料量 clamp；R4 数学感知切分（以 blocks 为切分单元、公式/定理块原子性，我方自研点）。R5 查询改写后置、R6 无 DB 存储与 GraphRAG 拍平拒绝 | 索引按签名增量写新版本可回滚；块级单元带 `self_ref` 页块锚点；向量＋BM25＋RRF 端点可检索且 top_k 随语料 clamp | [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| PLAN-009 | 主线 | 中 | Accepted | 做什么＝V1.0 条目登记与判定台账（PLAN-009 自承载）：P 系列解析面判定表、R1~R6 知识库面判定表、批量重建窗口的操作规程留档 | P 系列与 R 系列判定表条目全部落地：产物可检索、命中可回溯到页与块 | [V1.0 解析与知识库优化轮（v1-parsing-knowledge-optimization）](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| PLAN-018 | 主线 | 中 | In Progress | **请求接收（QED-Engine 根仓库 REQ-099）**；做什么＝W2 页级结构意图写入面（`af_operations` 的 `structure` 意图行，页级、不绑 `job_id`）＋ `PUT …/structure` 全量声明与区域删除／合并／新增端点（超每页上限与页越界的判码归属见壳）→ W1 逐框裁剪提交 `parse_page` 的区域驱动解析（框数上限＋受理前 422 门）→ W3 页数据回服务端投影的区域集合 → W8 每页人工确认门（确认态落 `af_pages`，chunk 只聚合已确认页）→ W7 未完成页可跳过或列清单 → W6 回执根仓库 REQ-099 → W4 目录长线、W5 节题取证 | 按框重解析生效；区域可删/并/增并落结构意图行；每页人工确认后才进 chunk；未完成页可跳过或列清单 | [解析页区域驱动重解析与块结构编辑承接实施（parsing-region-edit）](../plans/2026-09-26-parsing-region-edit-typography-request.md) |
| PLAN-019 | 主线 | 中 | In Progress | 做什么＝W2 `parser_signature` 加列迁移 → W3 重跑受理门 → W4 只读重跑清单端点 → W5 就绪以 DB 元数据为唯一事实源 → W6 块策略版本表＋逐块 ledger → W7 过时版本只读清点 → W8 文档晋升 → W9 真机窗口 | 签名相同不重跑、`force` 才重跑；就绪判定只读 DB；策略版本表与逐块 ledger 落盘；过时版本可只读清点 | [解析产物可信与增量重跑实施（parse-artifact-trust）](../plans/2026-09-28-parse-artifact-trust.md) |
| PLAN-025 | 主线 | 中 | In Progress | 做什么＝`af_kp` 的消费通路实现批（判据正本在 `docs/design/knowledge-analysis.md`，本案零 ALTER、零新增判据）：W1 `KpRepository` 三条写入腿（机器来源域／人工背书域／系统归一）→ W2 受理门与批次身份（无生效版本 422、同书在飞 409、单章范围参数）→ W3 两阶段执行体（先目录后知识点、未归档池、锚点越界只标记）→ W4 收尾与启动期归一（让位分支、次序边 DAG 校验、同父重名查重）→ W5 端点面与 `op_kind='kp'` 批次流水（`if_rev` 乐观并发、只读导出）→ W6 长文与删除通路 → W7 文本引擎槽位与 `qed_llm_calls` 调用账 → W8 文档晋升随步与「计数恒 0」断言逐条改判 → W9 真机梳理窗口（D 类） | `af_kp` 有写者：受理门三形可判、批次三态由 `gen_status` 表达、人工背书才能外发、导出面只给已确认 | [知识点梳理消费通路实现批（kp-curation-implementation）](../plans/2026-10-23-kp-curation-implementation.md) |
| PLAN-026 | 主线 | 高 | In Progress | 做什么＝块类型人工改判的本仓侧通路＋梳理生成腿接入（根侧入站请求 REQ-142／REQ-143 的本仓承载，两行判据正本在根侧台账）：W1 demo 门（页眉改判后目录取材不再吃该块）→ W2 `EditRequest` 扩载荷 `corrected_type`（受 `BlockType` 十枚闭集校验、落 edit 意图行载荷、零新列）→ W3 读侧回放两处（页数据端点＋`build_evidence_set`，缺一即判未达成）→ W4 跨页重复线页域判据纯函数（读时计算、零写库）→ W5 梳理取材默认剔除页眉页脚（人工改判的类型维 ∧ 跨页重复线的线维两腿合取，只作用在送模型的取证读数上，磁盘产物与页数据 `markdown` 逐字不改；`af_pages.section_kind` 写手经需求方改判取消，该列维持在册无写者）→ W6 文本引擎适配器一枚＋槽位登记与 `create_app` 接线（出站唯一＝8900 文字网关、归因四键随体、一次调用只落一行账；默认槽位名就是内置实名，起服即接通）→ W7 长文写腿端点（`write_narrative` 已在册，补 HTTP 面与 `narrative_ref` 写列；端点与写列＝壳 R9，路径约束不放宽）→ W8 文档随步；零 ALTER、不改 `BlockType` 闭集 | 人工改判块类型进入梳理取材；家具两腿合取后既不进目录标题也不进送模型的正文，且磁盘产物逐字不变；非 503 批次可产出章级目录且每笔调用有账 | [块类型改判回放、页域识别与梳理文本引擎接入（annotation-replay-and-kp-text-engine）](../plans/2026-10-26-annotation-replay-and-kp-text-engine.md) |
| REQ-139 | 支线 | 高 | Open | **请求接收（QED-Engine 根仓库 REQ-139「知识点梳理模块 Axiom-Flow 承接请求」）**；做什么＝甲・条文面 A1~A6（`KA-5` 的两阶段改判为多阶段、目录互验条、`section_kind` 写者与剔除条、`narrative_ref` 取值归一、确认后再总结条、页确认门与取证集接线）＋批量取页端点（现只有逐页）；其「章页区间加列」条件项**已作废**（根侧 Q4 裁为甲案＝由 `anchors[].page` 的 min/max 读时派生，本仓零改列请求）；其乙面写者与通路、丙面梳理端点已由本仓 PLAN-025 承接，本行对这两段只作形状核对；形状核对的产出＝三枚新增面 N1~N3（文本引擎适配器接入与槽位登记、`kp` 路由的在网生效与 `openapi.json` 对账（立案时七条在册、本轮审理时实测十条）、人工直登记知识点的创建端点），判据与逐条映射见承载壳「承载 REQ-139」节 | 梳理四步各有 `KA-` 条文与写者；目录互验与剔除有条文位；`narrative_ref` 两处取值归一；批量取页单次可多页；梳理提交在非 503 下可受理 | [知识点梳理消费通路实现批（kp-curation-implementation）](../plans/2026-10-23-kp-curation-implementation.md) |
| REQ-142 | 支线 | 高 | Open | **请求接收（QED-Engine 根仓库 REQ-142「块类型人工标注通路、页域几何识别与取材回放」）**；做什么＝本仓侧三段：`EditRequest` 扩载荷 `corrected_type` 受 `BlockType` 十枚闭集校验并落 `af_operations` 的 edit 意图行载荷（零新列）→ 页数据端点与梳理取材两处读侧回放该改判（今天 `block_type` 只是产物快照的只读回声、取材腿直读 `blocks.json` 零回放）→ 跨页重复线页域判据以读时纯函数供给根侧 REQ-140 R3 三条件合取中的类型维（页边带与短文本两条件归根侧渲染腿，本仓不重复立法）。判据与逐条映射见承载壳「承载 REQ-142／REQ-143」节与本仓 W1~W4 | 改判块类型可写可读，且页数据与目录取材两处按改判读数；页域判据零写库、零新列 | [块类型改判回放、页域识别与梳理文本引擎接入（annotation-replay-and-kp-text-engine）](../plans/2026-10-26-annotation-replay-and-kp-text-engine.md) |
| REQ-143 | 支线 | 高 | Open | **请求接收（QED-Engine 根仓库 REQ-143「梳理生成腿与一键总结／详解」）**；做什么＝本仓侧四段：文本引擎适配器一枚实现 `KnowledgeEngine` 协议（`outline`／`points`），调用一律经 8900 文字网关、本仓不持密钥也不直连模型厂商，且每笔调用在共享 `qed_llm_calls` 留账 → `TEXT_ENGINE_NAMES`／`IMPLEMENTED_TEXT_ENGINES` 登记该一枚槽位名，默认槽位值就是内置实名（起服即接通；显式清空或配成闭集外名字仍当场拒，无静默兜底）→ 梳理取材默认剔除页眉页脚（类型维＝人工改判回放 ∧ 线维＝跨页重复线），目录与正文两处出口合取后剔，磁盘产物与页数据 `markdown` 不动；`af_pages.section_kind` 维持在册无写者 → 长文写腿补 HTTP 面供根侧「一键详解」写入。本行零新表、零新列 → 第九令三件＝整理腿与条目腿共用同一把行清洗（`KA-41`）、整册工作区每行派生本节正文起止两页（`KA-42`）、`stage` 加第三值 `content` 拿在册目录只补知识点与节级总结（`KA-43`）；`KpStage` 域值由两枚扩为三枚、批次流水 `result` 加一枚计数 | 非 503 批次可产出章级目录、每笔调用有账；长文有可写通路且 jail 不放宽；目录行名无引导点残留且锚在正文页、每行给出本节起止页、有目录后可只补内容 | [块类型改判回放、页域识别与梳理文本引擎接入（annotation-replay-and-kp-text-engine）](../plans/2026-10-26-annotation-replay-and-kp-text-engine.md) |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 成功标准 | 承载 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护（长期）：① 固定文档随实现同步——`code-map.md` 五元组映射、`architecture/api.md` 端点表与运行态路由双向对账、`database-design.md` 列清单与 ORM 模型对账、`design/` 条文与实现落点→守护测试成对；② 版本末期规范化整理轮（体裁、骨架、元数据、术语）；③ 遗留清扫＝`src`/`scripts` 文件头裸条文编号 `V*`/`S*` 的悬空引用、包版本双源 `pyproject 0.4.0` vs `__version__ 0.5.0` | 三项终态齐备即退：固定文档与代码对账零漂移、规范化整理轮跑完、悬空条文编号与包版本双源清零 | [doc-governance.md](../standards/doc-governance.md) |
| ARCH-012 | 长期 | 低 | Open | 探索路线图维护（长期）：Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验与重渲染）、学习交互闭环四条方向的立项前调研与去留裁决；外部实验须先冻结假设/样本/内容哈希/预算/采纳门槛 | 四条方向各自拆成计划行或裁撤，本行随拆随退 | [roadmap.md](roadmap.md) |
| REQ-125 | 长期 | 低 | In Progress | **请求接收（QED-Engine 根仓库 REQ-125）**；做什么＝本仓 `.qoder/skills/` 四件套与 `.qoder/settings.json` 纳入版本控制（白名单形态 `.qoder/*` ＋ `!.qoder/skills/` ＋ `!.qoder/settings.json`）、`qed-intake` 去端专有工具名并把 `description` 值长收到 200 以内，同名技能与根仓库保持「同骨架、受控差异」；判据与证据锚点见根侧请求稿与本仓「标准映射机制」段 | 四件套技能文件已入库可追溯，`qed-intake` 不含端专有工具名且清单长度合规，骨架与根仓库对齐 | [development.md](../guides/development.md) |

## 规则

- **口径正文的唯一位置**＝[任务生命周期](../standards/task-lifecycle.md)「任务台账与承载」：三列语义与
  列界、成功标准与「任务」列的禁词、承载列唯一指针、行内禁日期、一任务一承载、一任务一行、进度单源、
  并发上限（支线 `In Progress` ≤3）以该节为准；收口顺序六步在同文档「计划生命周期」第 5 步。本表不
  复制其措辞。
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
