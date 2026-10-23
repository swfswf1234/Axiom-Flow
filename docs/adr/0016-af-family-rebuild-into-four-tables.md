# ADR 0016：af_* 表族整族重建为四表（事件与意图两态承载、档 B 迁移链重置）

状态：Accepted
最后更新：2026-10-22
关联代码：`src/axiom_flow/db/models.py`（`AfOperation`／`AfKp`／`AF_TABLES`）、
`src/axiom_flow/db/repositories.py`（`OperationRepository`／`KpRepository`／`job_timing`／`public_progress`）、
`src/axiom_flow/migrations/versions/20261022_0001_af_family_rebuild.py`
关联测试：`tests/unit/test_db.py`、`tests/unit/test_db_table_family_governance.py`、
`tests/unit/test_db_migrations.py`、`tests/integration/test_parse_persistence.py`、
`tests/contract/test_fixed_document_form.py`、`tests/contract/test_architecture_documents.py`

## 背景

现役五表把「一次解析发生过什么」切成三张表存（`af_parse_jobs` 的版本自证、`af_block_edits` 的版本内
人工判定、`af_page_regions` 的跨版本人工意图），由此产生三处不可归一：

1. 读「这一册书做过什么」要 UNION 三张表再各自排序，而事件流要给的正是这一屏。
2. 版本内与跨版本两种作用域各建一张表 ⇒ 作用域差异由表名表达。分析模块（知识点梳理）有同类需求
   （结论跨版本存活、取证版本只作溯源），照此只能再建第三种作用域的表；
   [PLAN-020 归档壳](../history/plans/2026-09/2026-09-29-af-table-family-governance.md)裁的「表族进入
   只加列不张表的稳定期」在作用域变化面前自己失效。
3. 根仓库 REQ-136 要的「一张表写明所有事件」与「结果索引表只要一张」是同一件事的两面：版本身份与
   操作流水本来就是一件事的两面，分表存即同一事实两处存。

形状由用户在 [PLAN-024](../history/plans/2026-10/2026-10-21-af-table-family-rebuild.md) 的裁决①~⑯定档，
2026-10-22 用户批准动代码与真库（档 B）。

## 决定

1. **表族收成四表**（`af_books`／`af_operations`／`af_pages`／`af_kp`，净变化 5→4）。三张退役表的旧列
   逐列指名接收：`af_parse_jobs` → `af_operations` 的 `op_kind='parse'`、`lifecycle='event'` 行；
   `af_block_edits` → `op_kind='edit'`、`lifecycle='intent'` 行；`af_page_regions` → `op_kind='structure'`、
   `lifecycle='intent'`、`job_id=''` 行。逐列类型／可空／键／语义与约束索引名的正本在
   [database-design.md](../architecture/database-design.md)，本篇不复制。
2. **`lifecycle` 一列把两种语义分开**——这是「一张表写明所有事件」能被守护的全部依据：`event` 行只答
   「发生过什么」，只可读出与计数，任何端点与状态机不得据它决定「现在是什么状态」；当前态只有四个来源
   ＝`af_books`（进度／结局／生效指针）＋`af_pages`（页级就绪与确认）＋`af_operations` 的 `intent` 行
   （人工意图）＋`op_kind='parse'` 行的 `status`（版本自身进度）。四源之外的读数一律是派生视图，不得再落列。
3. **生效指针与值形一字不改**：`af_books.active_job_id` → `af_operations.op_id`，`NULL` 仍是根视图
   `legacy`；`op_id` 沿用 `uuid4().hex[:12]` ⇒ 产物目录名 `versions/<job_id>/` 与根侧已见值形零改动，
   改动面收在持久层。`af_operations` 不设任何布尔「生效」列。
4. **幂等由 `intent_key` 一列承担**：`intent` 行＝`md5(op_kind:book_id:job_id:page_no:block_index:target_id)`
   前 16 位，`event` 行恒 `NULL`；MySQL 唯一键允许多行 `NULL` ⇒ 这就是只对意图行生效的部分唯一索引。
   坐标列本身不设唯一键。当前态写入只允许两种形态：唯一键单语句 upsert、`af_books` 行悲观锁内的整批
   替换——禁止「先读后判再写」。
5. **档位＝档 B 整族重建，迁移链重置**：原 6 个 revision 从链上删除（git 历史留档），新写一条 base
   `20261022_0001_af_family_rebuild`（`down_revision=None`）一次建齐四表；**不写跨表数据迁移脚本**，
   真实数据按「备份 → 用户批准 → `DROP`/`CREATE` → 逐本重新 ingest 与解析」重建。被拒的另一案＝保留旧链
   追加一条 DROP+CREATE 迁移，代价＝测试夹具每次从 base 重建要先建旧五表再删、且旧链每一条都在为不存在
   的形状跑 DDL 与断言。
6. **`af_pages` 是唯一的结果索引表**，扩 `artifacts`／`confirm_state`／`section_kind`／`meta` 四列。格式
   仍是**版本级**属性（`af_operations.render_profile`），页级不各自声明格式；`artifacts` 只作完整性披露、
   **不作就绪判据**——[ADR 0015](0015-db-single-source-for-readiness.md) 的单源口径不因换表松动。
7. **`af_kp` 只建存储形状，本轮不建消费通路**：本表由分析模块的写入路径消费，那条通路的条文（`KA-n`）
   已按 PLAN-024 的 W12 晋升为 [knowledge-analysis.md](../design/knowledge-analysis.md) 并成为已确认事实源，
   其**实现**另批立项。本决定只交付形状：`af_kp` 在本案内零写者，四条「计数恒 0」守护断言据此成立。
8. **明文改判「稳定期」与 ARCH-023 的第六表预留位**：检索索引版本**不占**该预留表位，而以「一值一列」
   并入 `af_operations`＝`op_kind` 扩第八值 `index` ＋ 独立列 `embedding_signature`（与 `parser_signature`
   **分列不复用**，两者输入集不同，共用一列即一列两义）。稳定期是「PLAN-018/019 作用域内无表级需求」的
   结论，本案属作用域变化而非原结论被推翻；预留位仍是表级空位。
9. **`af_kp.links` 是本仓 JSON 列反向闸的一处明文例外**（例外与下列转表判据已由用户终审追认，
   故 `database-design.md` 的 `af_kp` 节升为已确认）：`links` 承载「需要反向集合检索的身份关系」
   （按对端反查「哪些点以 X 为先修」只能全表扫 JSON）。采纳理由＝单表简单写关系的裁决与当前主屏读法是
   正向读。**转表触发判据**＝一旦出现「按对端反查成为主屏读法」或「需要 DAG 完整性的集合校验」，`links`
   升 `af_kp_edges` 并走独立加表评审。例外必须留在正文，否则反向闸退化成建议。
10. **两项当日裁决的代价与列同批写死**：① `af_books.meta` 有两个写者（同步作业按 `book_id` upsert 与人工
    临时读数），判据＝列域不重叠——同步 upsert 不得触碰 `meta`（只写同步域快照列与时刻列），人工写走
    **整列覆写**；反向判据＝同步重跑后先前写入的 `meta` 键集合不变。② `embedding_signature` 与
    `op_kind='index'` **建列建值不建写者**：索引行的 `job_id`／`status`／`lifecycle` 映射属检索面自身设计，
    本篇不定义；守护断言两者计数恒 0。
11. **时刻列折叠**：`started_at`／`finished_at` 不再是实体列，折进 `progress` 的同名键并在终态算
    `duration_seconds`；唯一读法＝`job_timing()`，API 回显走 `public_progress()` 剥掉这三个键以保持响应
    形状。代价＝不能用 SQL 直接聚合「跑了多久」；反面判据＝若真机耗时统计必须 SQL 聚合，则恢复两实体列
    （加列不加表）。

## 后果

- **正面**：一册书的事件流一次 SQL 读全；人工资产（结构意图与知识点结论）跨版本存活不再需要每种作用域
  各建一张表；分析模块有表可长；四表各有同名 `meta` 扩展槽，形状在本轮封闭。
- **代价**：宽表必有整段空列（parse 段／坐标与载荷段／索引段三段互不共用，且索引段本轮整列全空）；
  块编辑与结构声明的**逐次修改史不保留**（与五表时一致，非回归）；档 B 清空真实库，重建窗口内的算力与
  时间在飞，8903 在空窗期看到「暂无书目」空态而非报错（读取面纯 DB 驱动，空表即空列表）；`qed_test` 由
  集成夹具每次从 base 重建 ⇒ 四表全部 MySQL DDL 从下一次门禁起被真实执行；根侧 8900／8903 因形状变更
  需要的适配由根仓库自行承担。
- **缓解**：列清单↔ORM 由 `test_fixed_document_form.py` 逐列对账（类型／可空／主键标记／约束与索引名）；
  表集合单一口径由 `AF_TABLES` 派生并有 AST 对账；每条并发判据各带一条会红的反向用例；三处「本轮无写者」
  由计数恒 0 守护钉住（撤掉守护即无人发现检索面被偷偷接线）；D 类窗口逐表 `mysqldump` 并按 sha256 留档，
  回滚＝按备份还原五表 + 旧链从 git 取回。
- **边界**：本案不动共享 `qed_*` 与 `qt_*` 的任何形状；**SQLite 上 `with_for_update()` 是 no-op** ⇒ 行锁类
  判据（唯一键 upsert、锁内整批替换、`if_rev` 乐观锁）只能在 `QED_TEST_MYSQL=1` 层被证伪，该层须手动执行。

## 取代

取代：无。本决定只换承载表与写入形态，不改写 [ADR 0007](0007-parse-status-semantics.md)（六态与进度／结局
分列）、[ADR 0010](0010-page-regions-are-parse-inputs.md)（意图跨版本、判定绑版本）、
[ADR 0015](0015-db-single-source-for-readiness.md)（就绪 DB 单源）的任何语义裁决。ADR 0010 决定 1 的
不变式折叠后落为「`structure` 行的 `job_id` 恒 `''`」，由 `tests/unit/test_db.py` 断言。
被取代：无。
明文改判：PLAN-020 归档壳的「表族进入只加列不张表的稳定期」与「唯一潜在第六表来源＝ARCH-023」两条读法
（见决定 8）。
关联：[database-design.md](../architecture/database-design.md)（四表列级正本）、
[PLAN-024](../history/plans/2026-10/2026-10-21-af-table-family-rebuild.md)（形状、折叠映射与档 B 批次的正本承载）、
[ADR 0005](0005-mysql-integration-test-isolation.md)（`qed_test` 白名单随 `AF_TABLES` 同步）；
跨仓只读引用：根仓库 REQ-136 与总纲 `QED-Engine/docs/architecture/database-design.md`
（表级 5→4 与迁移 head 读数由根侧随步）。
