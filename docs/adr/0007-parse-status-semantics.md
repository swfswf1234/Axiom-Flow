# ADR 0007：解析状态语义三分（结局三态、进度/结局分列、启动期归一）

状态：Accepted
最后更新：2026-10-22
关联代码：`src/axiom_flow/schemas.py`（`JobStatus`/`ParseStatus`）、`src/axiom_flow/orchestrator/pipeline.py`（终态判定）、`src/axiom_flow/orchestrator/jobs.py`（执行器）、`src/axiom_flow/db/models.py`（`AfBook` 的进度/结局两列与 `AfOperation` 的 parse 行状态列）、`src/axiom_flow/api/main.py`（lifespan）
关联测试：`tests/unit/test_schemas.py`、`tests/unit/test_orchestrator.py`、`tests/unit/test_db_migrations.py`、`tests/contract/test_api_v1_contract.py`、`tests/integration/test_parse_persistence.py`

## 背景

DEFECT-006/007 的取证（[PLAN-003](../history/plans/2026-09/2026-09-20-parse-live-bringup.md) R0/R1 节）暴露出解析
状态三处语义缺陷，均为代码事实而非推测：

1. **任务终态掩盖失败页**：`pipeline.py` 的判据是 `COMPLETED if parsed else FAILED`，`parsed` 为
   「已解析页数」。20 页目标失败 3 页 → 任务判 `completed`，而同一判据在激活门槛处又判「未齐备、
   不激活」，于是出现「`completed` 但从未生效」的自相矛盾。失败页只存在于 `ParseOutcome` 返回值与
   `progress.error`/`error` 字符串（截断 200/500 字符），无结构化字段可读。
2. **同一次运行在两张表给出矛盾答复**：书级判据是 `COMPLETED if not failed else FAILED`，同一运行
   下书判 `failed`、任务判 `completed`；且 `af_books.parse_status` 一字段两用（既是进度
   `pending`/`parsing` 又是结局 `completed`/`failed`），而「有无可读版本」的真实事实源是
   `af_books.active_job_id`，两者不一致。
3. **进程被杀遗留假活动态**：`running` 永久停留（实证任务 `5f43593a12cc` 空闲 106683 秒），
   排队中被 `shutdown(cancel_futures=True)` 取消的任务同样永久停在 `queued`。表上无心跳列，
   以 `started_at` 距今判死会把「闸门最长等待 900 秒」的活任务误杀。

副作用已实证过一次：R1 真机窗口的排他判据只能改用「模型侧计数」绕开该幽灵 `running` 行。

## 决定

1. **任务结局三态**：`JobStatus` 扩为 `queued`/`running`/**`completed`**/**`partial`**/**`failed`**/
   **`interrupted`**（后四个为终态）。终态判据唯一定义在「本次任务目标页集合」上：
   - `completed` ⟺ 目标页全部 `parsed` 且产物在位（与 V3 激活门槛**同一判据、同一函数**，不再分叉）；「产物在位」子句自 2026-10-01 起被 [ADR 0015](0015-db-single-source-for-readiness.md) 取代，现判据只取 DB 页行，同一函数与同一判据的骨架不变；
   - `partial` ⟺ 至少 1 页 `parsed` 且不满足 `completed`；
   - `failed` ⟺ 0 页 `parsed`，或引擎异常导致执行提前终止；
   - `interrupted` ⟺ 终态由启动期归一写入（见决定 4），管线自身不写。
   `partial` 不激活版本（沿用 V3：目标页齐备才交给生效指针），产物保留在 `versions/<job_id>/` 供排查。
2. **失败页结构化**：`af_parse_jobs` 新增 `failed_pages`（JSON 整型数组，不截断；该表已由
   [ADR 0016](0016-af-family-rebuild-into-four-tables.md) 折叠为 `af_operations` 的 parse 事件行，
   **列同名平移、写时机不变**），页级失败原因继续
   逐页写 `af_pages.error`；`progress` 增 `failed` 计数。`error`/`progress.error` 字符串只作为人类
   可读摘要保留，不再是失败清单的唯一载体。
3. **书级进度与结局分列**：`af_books.parse_status` 只表达**进度**，取值收缩为 `pending`/`parsing`；
   新增 `last_parse_result` 表达**最近一次任务结局**，取值 `''`（从未有任务）/`completed`/`partial`/
   `failed`/`interrupted`。`active_job_id` 仍是「哪一版可读」的唯一事实源，不因新列而改变语义。
   进度写回规则：任务受理即 `parsing`；任何终态即 `pending` + 对应 `last_parse_result`。
4. **启动期归一（单实例前提）**：服务启动、开始受理请求之前，把遗留的 `queued`/`running` 任务行
   归一为 `interrupted`（`error` 写「服务重启中断，可重跑」，`finished_at` 补齐），并把受影响书目的
   `parse_status` 归 `pending`、`last_parse_result` 归 `interrupted`（仅当该任务是该书最新任务）。
   崩溃与被 kill 走同一路径。**正当性前提：8902 单实例部署**——本服务无跨进程任务所有权标记，
   多实例部署前必须以心跳 + 后台回收（决定 4 的备选）替代本条，否则启动期归一会误杀其他实例的活任务。
5. **续跑语义不变**：`interrupted`/`partial` 的版本作用域 `(book_id, job_id)` 保留「无行 ∪ failed」
   页续跑能力，但续跑要求以**同一 job_id** 重放（运维脚本路径）；新提交任务总是产生新版本，
   绝不静默复用他人的版本作用域。
6. **兼容与迁移**：新列全部带缺省值、可降级；`ParseJob.status` 与 `af_books` 字段属**向前兼容扩展**
   （只增枚举值与字段，不改既有字段名）。迁移 `20260921_0001` 只回填历史值（现 `parse_status` 的
   `completed`/`failed` → `last_parse_result`，`parsing` → `pending`），**不改写历史任务状态**——
   幽灵 `running` 行由首次部署后的启动期归一处理，以保持「活动态只有一个写入者」。
7. **根侧契约影响**：8900/前端遇未知枚举值必须按「非终态或不可读」保守处理而非猜测；页级失败清单与
   任务列表的数据来源已具备（决定 1/2），但**本 ADR 不新增端点**，端点语义由 REQ-087 承载（该请求行
   2026-09-21 首登为 `REQ-086`，2026-09-22 因根仓库把 `REQ-086` 用给「全本分块提交」而改号，同号不同事）。

## 后果

- **正面**：`completed` 与「可激活」同源，掩盖失败页的判据分叉消失；书级进度与结局可分别成文，
  前端不再需要用 `parse_status` 猜可读性；幽灵 `running`/`queued` 有确定的收敛时刻（下次启动）。
- **代价**：`JobStatus` 增加三个值，8900 与脚本的状态分支需同步；新增两列 + 一次回填；启动期归一
  依赖单实例前提，属显式承担的部署约束。
- **缓解**：三态判据与激活门槛共用同一函数（回归测试锁死「`completed` 但未激活」不可能出现）；
  `interrupted` 与 `MODEL_BUSY`/`MODEL_UNAVAILABLE` 文案分列，不与「模型不可用」互相冒充；单实例
  前提写入 `docs/guides/operations.md` 与本 ADR，多实例改造前必须先立取代 ADR。

## 取代

取代：无。
被取代：决定 1 的「且产物在位」子句被 [ADR 0015](0015-db-single-source-for-readiness.md) 取代（2026-10-01，就绪与激活判据改以 DB 为唯一事实源）；本 ADR 其余决定与「`completed` 与激活同一判据、同一函数」的不变式继续生效。
关联：承接 DEFECT-006（部分成功语义）与 DEFECT-007（假 `running`）的关闭条件成文；解除 ARCH-003
的裁决阻塞；为 REQ-087 提供成文数据来源但不含其端点范围。实施载体 PLAN-006 已于 2026-09-21 关闭
并按 Delete 删除计划壳（正文见锚点 `9034a19`），结论落 `design/parsing-management.md` 的 `PM-8`~`PM-14`
（原编号 S1~S7，2026-09-23 叙述框架重整时就地改号）、`architecture/api.md` 状态机表与
`architecture/database-design.md` 列语义。
