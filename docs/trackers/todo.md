# 待做任务

状态：Current
最后更新：2026-10-22

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
| ARCH-022 | 支线 | 中 | In Progress | 解析产物增量可判与可信消费（P 系列）：P1 `parser_signature` 只哈希影响块产物的参数，`(源哈希, 签名)` 双全等默认不重跑、`force` 才重跑，配只读重跑清单与过时清点；P2 就绪、激活与续跑判据去文件系统（改读 `af_pages` 页行与生效指针，manifest 降为完整性数据）；P3 `_TYPE_MAP` 与分块过滤规则版本化，未知字面值收为兜底类型不丢并落页级 ledger | 双全等默认不重跑、`force` 才重跑；就绪与激活只凭 DB 页行与生效指针判定；未知字面值收为兜底类型并逐页落 ledger | [PLAN-019](../plans/2026-09-28-parse-artifact-trust.md) |
| ARCH-023 | 支线 | 中 | Open | 知识库面（R 系列，8902 检索端点契约先行）：R1 索引版本行增量（换模型/换维度写新版本，旧版可读可回滚，同签名只追加不重建）；R2 块级知识单元＋`self_ref="blocks.json#/{index}"` 页与块序号锚点；R3 向量与 BM25 各取 2×top_k → RRF 融合 → 可选 CrossEncoder rerank，索引持久化且 top_k 按语料量 clamp；R4 数学感知切分（以 blocks 为切分单元、公式/定理块原子性，我方自研点）。R5 查询改写后置、R6 无 DB 存储与 GraphRAG 拍平拒绝 | 索引按签名增量写新版本可回滚；块级单元带 `self_ref` 页块锚点；向量＋BM25＋RRF 端点可检索且 top_k 随语料 clamp | [PLAN-009](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| PLAN-009 | 主线 | 中 | Accepted | 做什么＝V1.0 条目登记与判定台账（PLAN-009 自承载）：P 系列解析面判定表、R1~R6 知识库面判定表、批量重建窗口的操作规程留档 | P 系列与 R 系列判定表条目全部落地：产物可检索、命中可回溯到页与块 | [V1.0 解析与知识库优化轮（v1-parsing-knowledge-optimization）](../plans/2026-09-22-v1-parsing-knowledge-optimization.md) |
| PLAN-018 | 主线 | 中 | In Progress | **请求接收（QED-Engine 根仓库 REQ-099）**；进度＝**W2 已落地**：页级结构意图（当时建为第五表 `af_page_regions`，**现随 PLAN-024 档 B 折叠为 `af_operations` 的 `structure` 意图行**，页级、不绑 `job_id`）+ `PUT …/structure` 结构意图全量声明（超每页上限判 400、页越界判 404，都判在写入前；旧迁移 `20260926_0002` 与当时 head 读数按记录保留在壳，链已重置），偏差登记见壳「W2 落地结论与偏差登记」节；**W1 已落地**（受理门探活前 422＋管线逐框裁剪区域驱动分支，TDD 单元/契约守护，六条裁决与落地结论见壳「W1 落地结论」节；真机 S1 实测留消费侧批次），当前在 W3；做什么＝W2 建页级结构意图写入面＋删除/合并/新增区域 API → W1 逐框裁剪提交 `parse_page` 的区域驱动解析（框数上限＋受理前 422 门）→ W3 页数据回服务端投影的区域集合 → W8 每页人工确认门（确认态落 `af_pages`，chunk 只聚合已确认页）→ W7 未完成页可跳过或列清单 → W6 回执根仓库 REQ-099；W4 目录长线、W5 节题取证 | 按框重解析生效；区域可删/并/增并落结构意图行；每页人工确认后才进 chunk；未完成页可跳过或列清单 | [解析页区域驱动重解析与块结构编辑承接实施（parsing-region-edit）](../plans/2026-09-26-parsing-region-edit-typography-request.md) |
| PLAN-019 | 主线 | 中 | In Progress | 做什么＝W2 `parser_signature` 加列迁移 → W3 重跑受理门 → W4 只读重跑清单端点 → W5 就绪以 DB 元数据为唯一事实源 → W6 块策略版本表＋逐块 ledger → W7 过时版本只读清点 → W8 文档晋升 → W9 真机窗口 | 签名相同不重跑、`force` 才重跑；就绪判定只读 DB；策略版本表与逐块 ledger 落盘；过时版本可只读清点 | [解析产物可信与增量重跑实施（parse-artifact-trust）](../plans/2026-09-28-parse-artifact-trust.md) |
| PLAN-024 | 主线 | 中 | In Progress | **请求接收（QED-Engine 根仓库 REQ-136，并入 REQ-123／REQ-129）**；进度＝**W3 已执行**（`database-design.md` 落三类登记面、目标形状四表以**暂定**入篇、解析／分析两模块分列）＋**W11／W12 已执行**（分析模块通路设计原以 23 条 `KA-n` 入壳，六项结构请求与开放裁决同日给裁后按 W12 晋升为设计篇 `docs/design/knowledge-analysis.md`（`KA-1~KA-28`、验收判据 13 条），四篇同构的文档守护与索引随步）＋**W13 已执行**（承载力审计与流程落点入权威文档：十二步流程每步恰一个判据位、roadmap 四条与在飞三线逐条判定四表足够、唯一未来结构请求＝检索索引版本行）＋**W4~W7 已落地**（W4＝ADR 0016 立稿与 ADR 0005/0007/0010/0015 随步；W5＝`api.md` 字段来源随步；W6＝PM／PP／MI 条文、`code-map.md`、`overview.md`、`operations.md` runbook 随步；W7＝ORM＋仓储合成＋API＋orchestrator＋schemas 与迁移链重置进四表形状，守护与测试改指向；`database-design.md` **转正**＝解析模块三表已确认、`af_kp` 节随后随裁决⑲ 升为已确认（四表齐））＋**W14 已执行**（`database-design.md` 章体按根仓库总纲改为**八章编号体**，体裁冲突由 ADR 0017 以「骨架按体裁分治」立法，`doc-governance.md` 与 `test_fixed_document_form.py` 白名单随步；零代码、零迁移、零 DDL）＋**W8 已执行**（档 B 真机整族重建，D 类三件纪律齐备：两库 mysqldump＋逐表 DDL 双份留档 → **只** DROP 自有 `af_*` 与 `alembic_version`（绝不 `DROP DATABASE`）→ 唯一 base `20261022_0001` 建四表 → 两库 `VERDICT=PASS`、真库列数逐张＝22／27／12／23、`af_operations` 索引五名齐、非 `af_*` 附带面逐名零受损 → 空窗 11 条 GET 读数＝**空态而非故障** → `books/sync` 回灌 17 本且重放幂等；CW-3／CW-4／CW-6 双 Session 反向判据落 `tests/integration/test_concurrency_criteria.py` 4 例＋逐机制变异复验 3/3 转红；按用户裁决**本轮不重 ingest/不重解析**）＋**审计查出的两项待裁当日已了结并落文档面**（裁决⑮＝`af_books` 增 `meta`，四表扩展槽对称；裁决⑯＝检索索引签名以独立列 `embedding_signature` ＋ `op_kind='index'` 并进 `af_operations`，两者本轮**建列不建写者**、守护断言计数恒 0）；待用户逐条了结＝**零项**（末项 `links` 越闸追认已落＝裁决⑲ ⇒ `af_kp` 节升为已确认；结构请求 R1/R2 与开放裁决 Q1~Q4 由裁决⑰⑱全部给裁：R1＝`gen_status` 闭集删 `adopted`、R2＝`op_kind` 扩 `kp` 承载梳理批次流水，两者只改应用层常量 ⇒ 零 ALTER、零新迁移；W12 晋升已放行；迁移链重置已随 W7 执行；W10 已另立 PLAN-025）；做什么＝W1 本壳入册（目标形状四表＝`af_books` 保留并增一列扩展槽 + 事件明细表 `af_operations`（吸收 `af_parse_jobs`／`af_block_edits`／`af_page_regions`）+ 唯一结果索引表 `af_pages`（扩 `artifacts`／`confirm_state`／`section_kind`／`meta`）+ 单表知识点 `af_kp`（层级靠 `parent_id`、关系靠 `links` JSON、并发靠 `intent_key` 唯一键与行锁整批替换）→ W2 用户评审四处 → W3 `database-design.md` 按根侧总纲落三类登记面（共享 `qed_*` 与 `qt_*` 只登记不复制）、目标形状四表以暂定列清单入篇 → W4 新增 ADR 承载稳定期改判与越闸例外（已执行）→ W5 `api.md` 随步（已执行）→ W6 PM／PP 条文与 code-map 随步（已执行）→ W7 代码批（已执行，用户放行「开始正式代码改动」）→ W8 档 B 真机重建窗口（已执行；D 类，执行权由用户当日「你帮我执行」改判后代为驱动）→ W9 回执根侧 → W11 分析模块（知识点梳理）通路设计（已执行）→ W12 该设计评审后晋升为设计篇并同批守护与索引随步（**已执行**，用户裁决⑱；W10 已另立 PLAN-025 承载，本壳不持有其执行面）→ W13 四表承载力审计与两模块流程落点确认（已执行）→ W9 回执根侧（**本仓侧已执行**＝R1~R5 终态答卷、需根侧随步的六处与「契约不变承诺」成文于壳「W9 回执」节并转达用户，含 `af_pages` 列数 11→**12** 的事实错更正；根仓库文件由根仓库自改，本仓对根零写入。如实移交的写者缺口＝六个无写者 `op_kind` 值（`confirm`／`ingest`／`sync`／`purge`／`index`／`kp`）的写者，其中「重新摄取复位归零」（原 REQ-123 R1）已按裁决⑳ **裁撤、不记欠账**） | 四表逐列形状与判据入权威文档并转正（四表齐＋越闸例外已追认）；解析／分析两模块边界可指认；并发写入各有会红用例；档 B 四件必答交付；承载力逐条判定 | [af_* 表族整族重建设计（af-table-family-rebuild）](../plans/2026-10-21-af-table-family-rebuild.md) |
| PLAN-025 | 主线 | 中 | Accepted | 做什么＝`af_kp` 的消费通路实现批（承接 PLAN-024 的 W10）：W1 `KpRepository` 三条写入腿（机器来源域／人工背书域／系统归一）→ W2 受理门与批次身份（无生效版本 422、同书在飞 409、单章范围参数）→ W3 两阶段执行体（先目录后知识点、未归档池、锚点越界只标记）→ W4 收尾与启动期归一（让位分支、次序边 DAG 校验、同父重名查重）→ W5 端点面与 `op_kind='kp'` 批次流水（`if_rev` 乐观并发、只读导出）→ W6 长文与删除通路 → W7 文本引擎槽位与 `qed_llm_calls` 调用账 → W8 文档晋升随步与「计数恒 0」断言逐条改判 → W9 真机梳理窗口（D 类）；判据正本在 `docs/design/knowledge-analysis.md`，本案零 ALTER、零新增判据 | `af_kp` 有写者：受理门三形可判、批次三态由 `gen_status` 表达、人工背书才能外发、导出面只给已确认 | [知识点梳理消费通路实现批（kp-curation-implementation）](../plans/2026-10-23-kp-curation-implementation.md) |

## 长期任务

| ID | 类别 | 优先级 | 状态 | 任务 | 成功标准 | 承载 |
| --- | --- | --- | --- | --- | --- | --- |
| ARCH-010 | 长期 | 中 | In Progress | 文档体系对齐维护（长期）：① 固定文档随实现同步——`code-map.md` 五元组映射、`architecture/api.md` 端点表与运行态路由双向对账、`database-design.md` 列清单与 ORM 模型对账、`design/` 条文与实现落点→守护测试成对；② 版本末期规范化整理轮（体裁、骨架、元数据、术语）；③ 遗留清扫＝`src`/`scripts` 文件头裸条文编号 `V*`/`S*` 的悬空引用、包版本双源 `pyproject 0.4.0` vs `__version__ 0.5.0` | 三项终态齐备即退：固定文档与代码对账零漂移、规范化整理轮跑完、悬空条文编号与包版本双源清零 | [doc-governance.md](../standards/doc-governance.md) |
| ARCH-012 | 长期 | 低 | Open | 探索路线图维护（长期）：Milvus 向量检索、知识图谱（定义/定理语义）、数学解析器（LaTeX 校验与重渲染）、学习交互闭环四条方向的立项前调研与去留裁决；外部实验须先冻结假设/样本/内容哈希/预算/采纳门槛 | 四条方向各自拆成计划行或裁撤，本行随拆随退 | [roadmap.md](roadmap.md) |
| REQ-125 | 长期 | 低 | In Progress | **请求接收（QED-Engine 根仓库 REQ-125）**；做什么＝本仓 `.qoder/skills/` 四件套与 `.qoder/settings.json` 纳入版本控制（白名单形态 `.qoder/*` ＋ `!.qoder/skills/` ＋ `!.qoder/settings.json`）、`qed-intake` 去端专有工具名并把 `description` 值长收到 200 以内，同名技能与根仓库保持「同骨架、受控差异」；判据与证据锚点见根侧请求稿与本仓「标准映射机制」段 | 四件套技能文件已入库可追溯，`qed-intake` 不含端专有工具名且清单长度合规，骨架与根仓库对齐 | [development.md](../guides/development.md) |

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
