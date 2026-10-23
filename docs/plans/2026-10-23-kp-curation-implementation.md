# 知识点梳理消费通路实现批（kp-curation-implementation）

状态：Accepted
任务类型：B
最后更新：2026-10-23
关联 ADR：[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)（四表形状与 `links` 越闸例外——本案是它的消费批，不动形状）、
[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（模型生命周期归 8900，梳理调用只经适配层）、
[ADR 0005](../adr/0005-mysql-integration-test-isolation.md)（`qed_test` 白名单＝自有 `af_*`＋`alembic_version`，本案调用账写共享 `qed_llm_calls` 只写行不改结构）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪唯一源＝DB，取证页集据此构造）、
[ADR 0010](../adr/0010-page-regions-are-parse-inputs.md)（页级意图跨版本存活的先例，锚点越界的判读同形）、
[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)／[ADR 0011](../adr/0011-ledger-one-row-per-task.md)／[ADR 0013](../adr/0013-ledger-row-three-elements.md)（承载与台账行契约）
关联设计：[knowledge-analysis.md](../design/knowledge-analysis.md)（`KA-1~KA-28` 与「验收判据」13 条＝本案判据正本，本案一处不复制）、
[database-design.md](../architecture/database-design.md)（`af_kp`／`af_operations` 列清单与四节不变式）、
[api.md](../architecture/api.md)（新端点的参数与响应形状归它）、[parsing-pipeline.md](../design/parsing-pipeline.md)（`PP-4`/`PP-13`/`PP-19`）、
[parsing-management.md](../design/parsing-management.md)（`PM-13` 单实例串行前提）、[code-map.md](../architecture/code-map.md)（分析模块登记面）
关联 Tracker：docs/trackers/todo.md（PLAN-025 镜像行；本案承接 PLAN-024 的 W10，无新的入站请求号）
归档判定：Retain（13 条判据的逐条取证、批次状态机的变异复验与真机梳理窗口读数属不可再生证据；判据本体在 `docs/design/knowledge-analysis.md`）

> **本案的性质**：PLAN-024 交付了 `af_kp` 的**形状与判据**并明文「建值不建写者」，本案就是那个「写者批」。
> 因此本案的两条硬边界是派生的而非自选的：**零 ALTER、零新列、零新域值**（形状已由 PLAN-024 封闭），
> 且**不得新增或改写任何判据**——本案只把 `KA-n` 落成代码与测试，实现中发现形状不足时回 PLAN-024 壳登记
> 新的结构请求（R4 起）并等裁，不在本案内改表。

## 目标与成功标准

**目标**：让分析模块从「有表有判据、零实现」变成一条可跑的通路——人工发起一次梳理，机器按生效版本
产出目录与知识点草稿，人逐条背书，根侧从只读导出面拉走已确认结论。

**成功标准（终态形状，逐条判据正文见 [knowledge-analysis.md](../design/knowledge-analysis.md)「验收判据」）**：

- `af_kp` 有写者：受理门三形可判（422／409／建批回批次号），批次三态由 `gen_status` 自身表达，
  机器写来源域、人工写背书域与 `confirmed`，重启残留 `draft` 整批归 `rejected`。
- 每个知识点钉回产物：`anchors` 按其行上 `job_id` 那一版可回指，换版后读数不变、越界行带待复核标记仍可读。
- 批次流水落 `op_kind='kp'` 一行，`result` 给出成功/失败节计数；`gen_status` 域外值仍计数恒 0。
- 只读导出面＝`confirmed=1` 且父先于子、逐行带 `rev`；本组件对共享主表零写入。
- 梳理调用与 `qed_llm_calls` 追加行同批交付，且该表结构未被本组件改动。
- 设计篇 `实现状态` 不再是 Not Started，`database-design.md` 的「本轮范围」四条计数恒 0 断言按写者到齐情况逐条改判并留反向判据。

## 范围与非目标

**范围**：

- `orchestrator/knowledge.py`（新）、`db/repositories.py:KpRepository` 的三条写入腿与读面、
  `api/main.py` 的梳理提交／进度／复核确认／目录重排／只读导出端点、`engines/factory.py` 的文本引擎槽位、
  `infrastructure` 侧调用账写入通路、长文文件通路（`<book_dir>/kp/<node_id>.md`）。
- 文档随步：`knowledge-analysis.md` 的 `实现状态` 与「实现落点」列、`database-design.md` 的 `af_kp`
  「本轮范围」段与不变式行的计数断言口径、`api.md` 新端点条目、`code-map.md` 分析模块行、
  `overview.md` 能力矩阵、`operations.md` 的梳理窗口口径。
- 测试：`tests/unit/test_knowledge_pipeline.py`、`tests/unit/test_db_knowledge.py`（新）、
  `tests/contract/test_api_v1_contract.py`、`tests/integration/` 的调用账与双会话 `rev` 用例。

**非目标（各归其主）**：

- 提示词工程与梳理质量配方＝C 类实验，须先冻结假设／样本／内容哈希／预算／采纳门槛（AGENTS.md 前置），另批。
- 8903 的人工界面与 8900 的提升作业实现＝根仓库承担；本案只交付只读导出面与 `af_node` 幂等锚的约定形状。
- 检索配方、向量索引与知识图谱＝独立线（`ARCH-023`／roadmap），本案不为其预留列或端点。
- 表结构与域值变更＝`database-design.md` 与 PLAN-024 的形状已封闭，本案零改表。
- 「重新摄取时复位 `last_parse_result`／生效指针／进度」与 `confirm`／`ingest`／`sync`／`purge` 四个无写者值的写者
  ＝按用户裁决**不在本案**（REQ-123 已按「关闭且不记欠账」处置，本案不得顺手接写者）。
- 页级人工确认门（PLAN-018 W8）＝另一条线；本案按 `KA-3` 的分离口径执行，确认门未启用不阻断梳理。

## 前置条件

1. PLAN-024 W7/W8 已交付：四表在真库存在、唯一 base `20261022_0001`、`af_kp` 23 列与三个索引名在册；
   本案开始前须实读 `af_kp` 行数（预期 0）并留档。
2. 判据正本 `docs/design/knowledge-analysis.md` 为 `设计状态：Accepted`／`确认状态：已确认`；
   本案实施中发现某条 `KA-n` 无法落地时，**先改判设计再写代码**，不得在代码里留第四种读法。
3. `af_kp` 的存储节已在 `database-design.md` 升为**已确认**（`links` 越闸追认＝PLAN-024 裁决⑲），
   形状讨论不得在本案重开。
4. 库面纪律：业务库 `qed` **保持为空**直到用户明确说「同步 OK」；`qed_test` 现有 17 本 `af_books` 是演示数据
   ⇒ 涉及真 MySQL 的层（`QED_TEST_MYSQL=1`）与真机窗口一律先按 `operations.md` runbook 备份再动，
   且默认门禁不得连带跑 MySQL 层（夹具 teardown 会清空 `qed_test`）。
5. 模型侧：梳理需要一个文本类引擎槽位，由 8900 起停（`MI-2~MI-5`）；未接入时该作业种类判 503，
   本案按 503 语义写用例，不静默回落到解析引擎。
6. 并发前提：单 worker 串行队列与单实例部署（`PM-13`）不变；跨书不并发，同书在飞唯一性按书算（`KA-4`/`KA-25`）。
7. 守护改判是本批验收面而非清理动作：`tests/unit/test_db.py` 现有四条「计数恒 0」断言中，
   `op_kind='kp'` 那条要随插入腿交付**改判为正向断言**（写者到齐后必须有行且字段形状可判），
   其余三条（非空 `embedding_signature`／`op_kind='index'`／`gen_status` 域外值）**保持恒 0 不动**。
8. 用户在 PLAN-024 收口之后另令「开始执行」才动 `src/`（本壳立项只动文档）。

## 工作项

| # | 工作项 | 判据位 | 状态 |
| --- | --- | --- | --- |
| W1 | `KpRepository` 三条写入腿：机器来源域（身份/结构/锚点/`summary`/`gen_batch`/`model_ref`/`gen_status`，`confirmed` 恒 0）、人工背书域（名称/挂父/`links`/`anchors`/`role`/`type`/`summary` 与 `confirmed` 位，`rev+1` 且不改来源两列）、系统两条（归一与裁剪）；读面 `list_for_book` 按行取证版分组 | `KA-11`/`KA-12`/`KA-17`/`KA-18` | 待开始 |
| W2 | 受理门与批次身份：无生效版本 422 零写入、同书存在 `draft` 行 409、正常建批发 `afkp-<hex12>`；可选单章范围参数（取证集按页区间构造、裁剪仍按 `book_id + gen_batch` 计数） | `KA-1`/`KA-4`/`KA-10`/`KA-24`/`KA-25` | 待开始 |
| W3 | `orchestrator/knowledge.py` 两阶段执行体：目录阶段（只读 heading＋`section_kind`＋页序，两层封顶）先于知识点阶段（逐节读页正文，缺页剔除且计数可见）；未归档池一等公民；锚点越界只标记不改写 | `KA-2`/`KA-5`/`KA-6`/`KA-7`/`KA-19` | 待开始 |
| W4 | 收尾与启动期归一：本批 `draft→candidate` 单语句条件 UPDATE、让位分支（同父同 `norm_key` 且已 `confirmed=1` ⇒ 新机器行判 `rejected`）、残留 `draft` 整批 `rejected`、整批一次 DFS 次序边校验与 `ix_af_kp_norm` 查重 422、机器新父必属同批目录 | `KA-9`/`KA-13`/`KA-26` | 待开始 |
| W5 | 端点面与批次流水：梳理提交／进度／复核确认（`if_rev` 乐观并发）／目录整批声明（缺席只降级、锁内整批替换）／只读导出（`confirmed=1`、父先于子、逐行 `rev`）；`op_kind='kp'` 插入腿与 `result` 计数形状 | `KA-8`/`KA-14`/`KA-15`/`KA-27` | 待开始 |
| W6 | 长文与删除通路：临时文件→校验→原子替换、`narrative_ref` 恒为单段文件名、删节点与清引用边同事务且文件在提交后清、孤儿只读清单 | `KA-20`/`KA-21` | 待开始 |
| W7 | 适配层槽位与调用账：`engines/factory.py` 文本引擎槽位（未接入判 503、闭集外拒绝）、`qed_llm_calls` 追加行（`service='axiom_flow'`，只写行不改结构） | `KA-22`/`KA-23` | 待开始 |
| W8 | 文档晋升随步：`knowledge-analysis.md` 的 `实现状态` 与「实现落点」列、`database-design.md` 的 `af_kp`「本轮范围」段与不变式行按写者到齐改判（`op_kind='kp'` 由计数恒 0 转为正向断言，另三条不动）、`api.md` 新端点条目、`code-map.md` 分析模块行与 `overview.md` 能力矩阵、`operations.md` 梳理窗口口径 | `KA-28` 与「验证与验收」的晋升对账 | 待开始 |
| W9 | 真机梳理窗口（D 类，逐次批准）：b05 单本整本书梳理跑通＋13 条判据逐条取证＋备份与回滚指纹 | 「验证与验收」全清单 | 待开始 |

## 验证与验收

- **判据对账**＝`knowledge-analysis.md`「验收判据」13 条逐条有会红用例，且用例名或注释可指回 `KA-n`；
  本案不新增判据、不改写判据（新增者视为设计变更，回到设计篇先改判再实现）。
- **TDD**＝W1~W7 每腿先取真红灯（`af_kp` 无写者 ⇒ 先红形态明确）再实现；W5 的端点用例落契约层。
- **守护改判复验**＝对四条「计数恒 0」断言逐条给读数：`op_kind='kp'` 那条改判前后各跑一次，
  并做变异复验（撤掉让位分支 ⇒ `KA-13` 用例转红；把人工写入口放开 `confirmed` 由机器写 ⇒ `KA-11` 用例转红；
  撤掉 `if_rev` 比对 ⇒ 双会话用例转红），复验后按 sha256 逐字节还原。
- **零改形状自证**＝`af_kp`／`af_operations` 列数与索引名在窗口前后逐名相等（真库 `SHOW CREATE TABLE` 双份留档），
  `qed_llm_calls` 结构未被 ALTER。
- **门禁**＝`pytest tests -q` + `ruff check src tests`；固定文档七道守护同批复跑；
  MySQL 层只在用户批准的窗口内跑 `QED_TEST_MYSQL=1`，收尾复跑不含它（夹具 teardown 清 `qed_test`）。
- **人工核对**＝把设计篇任一条 `KA-n` 的「实现落点」文件名改掉，确认对应测试转红（防判据空转）。

## 回滚

- 代码批可整批 `git revert`；本案零 DDL、零迁移，故回滚不涉及库面动作。
- 真机窗口前按 `operations.md` runbook 取 `mysqldump` ＋逐表 DDL 双份备份并留 sha256；
  回滚形态＝按备份重放 `af_kp`／`af_operations` 两表，产物目录内 `kp/` 长文按只读清单人工处置。
- 调用账写入通路的回滚＝停用该腿（不删行），共享表数据不做反向清理（`KA-17` 同轴）。

## 关闭与归档

- 关闭条件＝「验证与验收」逐条达成且 13 条判据全部有读数；W9 未完时不得关闭。
- 归档判定 **Retain**：13 条判据的逐条取证表、批次状态机与让位分支的变异复验、真机窗口读数与备份指纹
  属不可再生证据；关闭后壳移入 `docs/history/plans/`，`plans/index.md` 与月度索引登记去处。
- 六步收口（ADR 0006）：结论入本壳 → todo 行压缩关闭 → `completed.md` → `plans/index.md` → `project-status.md` → 全量门禁。
- 残余移交：① 8903 界面与 8900 提升作业的实现与联调归根仓库；② 梳理质量配方另批（C 类）；
  ③ 「保留最新 2 批」的裁剪执行与触发阈值另批（`database-design.md`「保留与裁剪」只给口径）。
