# ADR 0015：DB 管状态、文件系统做完整性核验（就地修订 2026-11-23）

状态：Accepted
最后更新：2026-11-23
关联代码：`src/axiom_flow/orchestrator/pipeline.py`（`_resumed_pages`/`_version_is_complete`/`_inherit_page_rows`）、`src/axiom_flow/api/main.py`（`_read_scope`、页清单 `has_artifact`、`/chunks` 读取面）
关联测试：`tests/unit/test_orchestrator.py`、`tests/integration/test_parse_persistence.py`、`tests/contract/test_api_v1_contract.py`

## 背景

[PLAN-019](../history/plans/2026-09/2026-09-28-parse-artifact-trust.md) 的用户裁决 ②（2026-09-28）提出「就绪以 DB
元数据为准，文件系统不得成为第二判据」。2026-10-01 据此立稿，并于 2026-11-23 由用户就地修订：
**DB 控制一切状态，文件系统是内容仓库；确认 DB 状态后再检查文件系统真实状态是合理且必要的完整性核验，
不是第二状态源**。据此推翻 PLAN-009 原 P2 的「manifest-last 作就绪标记」，但保留文件存在性作为披露与
补跑输入。2026-10-01 评审时记录的两处事实（保留作历史推导）：

1. **禁 manifest 已经是空条文**：PLAN-017（REQ-097）把读取面改为纯 DB 驱动、无文件系统回落，
   manifest 已退役为完整性数据 ⇒ 「不存在以 manifest 存在与否判就绪的路径」这条判据会空转通过。
2. **真正的第二判据不是 manifest，是页产物文件是否存在**：`orchestrator/pipeline.py:_resumed_pages`（
   以 `pages/p<NNNN>.blocks.json` 落盘与否决定页是否算已解析，进而决定续跑集合）、
   `orchestrator/pipeline.py:_inherit_page_rows`（以同一判据筛可继承页）、`api/main.py` 页清单的
   `has_artifact`（直接 stat 产物文件）与 `GET /chunks`（`chunks.json` 缺失即现场重算）。其中前两处
   经 `_version_is_complete()` 参与**版本激活与任务终态**判定——这正是 [ADR 0007](0007-parse-status-semantics.md)
   决定 1 里「目标页全部 `parsed` **且产物在位**」那句所授权的位置。

因此「DB 单源」不是一次措辞收紧，而是与一条 Accepted 决定的实现判据冲突，须由 ADR 处置。

## 决定（2026-11-23 就地修订）

1. **状态事实源唯一在 DB；文件系统是内容仓库**：一本书「哪一版可读」的**状态**＝`af_books.active_job_id`；
   一个任务/版本的**状态**＝`af_operations` 的 `op_kind='parse'` 事件行终态（2026-10-22 整族重建后
   `af_parse_jobs` 退役为该事件行，见 [ADR 0016](0016-af-family-rebuild-into-four-tables.md)）与
   `af_pages` 页行状态集合；页级状态＝该版本作用域 `(book_id, job_id, page_no)` 的 `af_pages.status`。
   DB 之外的磁盘目录、manifest 与 `book.json` 都不是状态源（manifest 已退役为完整性数据）。
2. **状态确认后核验文件系统是合法的完整性检查**：`has_artifact` 一类字段如实暴露「DB 说已解析、
   磁盘上没有文件」的组合，既**不静默改判** DB 状态，也**不忽略矛盾**——该组合计入「建议补跑集合」，
   理由 `artifact_missing`，由人决定是否补跑。磁盘核验不反向改写状态，除非人显式提交补跑
   （届时受理面按 PLAN-019 的 scope 语义先把目标页状态写 `failed/error=理由`）。
3. **就绪/激活/续跑判据保留 DB 状态 + 文件核验双读**：`_resumed_pages`/`_version_is_complete`/
   `_inherit_page_rows` 的「页齐备」仍取 `af_pages.status='parsed'`，并核对 `pages/p<NNNN>.blocks.json`
   在位；文件缺失的页不计入可续跑集合，也不得被新版本继承为已解析页。这与本条修订前的现行代码
   一致，撤销「必须去掉 `is_file()`」的原决定。
4. **单判据不变式保留**：`completed` 与版本激活仍共用同一函数、同一判据（ADR 0007 决定 1 的骨架
   不变，「`completed` 但未激活」仍结构上不可能）。
5. **抗崩溃语义仍由 DB 提交序承担**：「崩溃/半写不产生假就绪」由逐页提交序（页产物写入先于页行
   提交、终态收尾必写）与 `QED_TEST_MYSQL=1` 层的双 Session 回归网保证（DEFECT-001 的关闭判据）；
   不引入 manifest-last 或任何文件型就绪标记。
6. **实施边界**：具体 scope 语义、建议清单、受理状态复位与守护用例归
   [PLAN-019](../history/plans/2026-09/2026-09-28-parse-artifact-trust.md) W3~W7，不在本决定内。

## 后果（2026-11-23 修订）

- **正面**：状态事实源唯一（DB），内容真实性可核验（文件系统），两者职责清楚；「DB=parsed 但文件缺失」
  不会再被静默当成可续跑/可继承页，而是显式披露并进入建议补跑集合，由人决定是否补跑；保留既有
  `_version_is_complete()` 语义，不动已提交的真机证据。
- **代价**：DB 与磁盘仍可能各说一套，但这是**如实暴露**而非判据冲突——系统的职责是揭示矛盾并给建议，
  最终由人决定；人提交补跑时受理面会先改当前版本的目标页状态，读面随之变化（这是人做决定的可见结果）。
- **缓解**：`has_artifact` 与只读建议清单保留 fs 观测面；提交序回归网
  （`tests/integration/test_parse_persistence.py`）与「文件缺失页不继承、入建议集合」的反向判据同批就位。

## 取代（2026-11-23 修订）

撤销本 ADR 原文对 [ADR 0007](0007-parse-status-semantics.md) 决定 1「**且产物在位**」子句的取代关系：
该子句按本修订后的语义**继续生效**（页齐备＝DB 状态 parsed + 产物在位），且「`completed` 与激活门槛
同一判据、同一函数」的不变式继续生效。
被取代：无。
关联：[ADR 0009](0009-data-root-must-be-explicit.md)（数据根显式且 fail-closed，本决定不改变写入
边界）、[ADR 0005](0005-mysql-integration-test-isolation.md)（提交序回归网的隔离边界）、
实施载体 [PLAN-019](../history/plans/2026-09/2026-09-28-parse-artifact-trust.md)（ARCH-022）。
