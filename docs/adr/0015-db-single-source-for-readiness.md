# ADR 0015：就绪与激活判据以 DB 为唯一事实源（取代 ADR 0007 决定 1 的「产物在位」子句）

状态：Accepted
最后更新：2026-10-01
关联代码：`src/axiom_flow/orchestrator/pipeline.py`（`_resumed_pages`/`_version_is_complete`/`_inherit_page_rows`）、`src/axiom_flow/api/main.py`（`_read_scope`、页清单 `has_artifact`、`/chunks` 读取面）
关联测试：`tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py`、`tests/contract/test_api_v1_contract.py`

## 背景

[PLAN-019](../plans/2026-09-28-parse-artifact-trust.md) 的用户裁决 ②（2026-09-28）要求「就绪以 DB
元数据为唯一事实源，文件系统不得成为第二判据」，并据此推翻 PLAN-009 原 P2 的「manifest-last 作就绪
标记」。2026-10-01 评审该计划的 S3 时发现两处事实：

1. **禁 manifest 已经是空条文**：PLAN-017（REQ-097）把读取面改为纯 DB 驱动、无文件系统回落，
   manifest 已退役为完整性数据 ⇒ 「不存在以 manifest 存在与否判就绪的路径」这条判据会空转通过。
2. **真正的第二判据不是 manifest，是页产物文件是否存在**：`orchestrator/pipeline.py:_resumed_pages`（
   以 `pages/p<NNNN>.blocks.json` 落盘与否决定页是否算已解析，进而决定续跑集合）、
   `orchestrator/pipeline.py:_inherit_page_rows`（以同一判据筛可继承页）、`api/main.py` 页清单的
   `has_artifact`（直接 stat 产物文件）与 `GET /chunks`（`chunks.json` 缺失即现场重算）。其中前两处
   经 `_version_is_complete()` 参与**版本激活与任务终态**判定——这正是 [ADR 0007](0007-parse-status-semantics.md)
   决定 1 里「目标页全部 `parsed` **且产物在位**」那句所授权的位置。

因此「DB 单源」不是一次措辞收紧，而是与一条 Accepted 决定的实现判据冲突，须由 ADR 处置。

## 决定

1. **就绪事实源唯一**：一本书「哪一版可读」＝`af_books.active_job_id`；一个任务/版本「是否齐备」
   ＝`af_pages` 页行状态集合与 `af_operations` 的 `op_kind='parse'` 行终态（2026-10-22 整族重建后
   `af_parse_jobs` 退役为这些行，见 [ADR 0016](0016-af-family-rebuild-into-four-tables.md)）；页级可读性＝该版本作用域 `(book_id, job_id,
   page_no)` 的 `af_pages.status`。三者之外不存在就绪判据源。
2. **单判据不变式保留**：`completed` 与版本激活仍共用同一函数、同一判据（ADR 0007 决定 1 的骨架
   不变，「`completed` 但未激活」仍结构上不可能），本决定只把该判据的**输入源**从文件系统改为 DB。
3. **文件系统读数的合法用途收窄为两类，且均不得反向改写 DB 读数**：
   - **完整性披露**：`has_artifact` 一类字段如实暴露「状态说已解析、磁盘上没有文件」的组合且
     不改判（沿用现状口径）——磁盘事故必须可见，但可见性不进入就绪判定。
   - **清点与清理输入**：过时/残骸清单（PLAN-019 S6/W7）按磁盘目录形态枚举，输出只作人工决策
     材料，不改写任何状态列。
4. **抗崩溃语义由 DB 提交序承担**：「崩溃/半写不产生假就绪」由逐页提交序（页产物写入先于页行
   提交、终态收尾必写）与 `QED_TEST_MYSQL=1` 层的双 Session 回归网保证（DEFECT-001 的关闭判据），
   **不引入** manifest-last 或任何文件型就绪标记。
5. **实施边界**：本 ADR 只冻结事实源归属与两类合法用途；具体判据、守护用例与真机面归
   [PLAN-019](../plans/2026-09-28-parse-artifact-trust.md) S3/W5，不在本决定内。

## 后果

- **正面**：就绪口径只有一个可读源，DB 与磁盘不再可能各说一套；「产物被外部删除」从静默改判
  变成显式披露；PLAN-019 的 S3 有了可证伪的对象（撤销文件而不改 DB 终态）。
- **代价**：`_version_is_complete()` 的既有语义变更会触及已提交的真机证据（激活与续跑判据的回归
  用例需按新输入源改写）；磁盘产物丢失时任务仍可能判 `completed`，可靠性从「判据自证」降级为
  「披露 + 清点」，需要人（或 S6 清单）承担发现责任。
- **缓解**：`has_artifact` 与只读清点端点保留 fs 观测面，事故仍可定位；提交序回归网
  （`tests/integration/test_parse_persistence.py`）与「伪造/删除产物文件 ⇒ 就绪与终态读数不变」
  的反向判据同批就位，防的是新口径被悄悄回退。

## 取代

取代：[ADR 0007](0007-parse-status-semantics.md) 决定 1 中的「**且产物在位**」子句（该决定的其余
部分、以及「`completed` 与激活门槛同一判据、同一函数」的不变式继续生效）。
被取代：无。
关联：[ADR 0009](0009-data-root-must-be-explicit.md)（数据根显式且 fail-closed，本决定不改变写入
边界）、[ADR 0005](0005-mysql-integration-test-isolation.md)（提交序回归网的隔离边界）、
实施载体 [PLAN-019](../plans/2026-09-28-parse-artifact-trust.md)（ARCH-022）。
