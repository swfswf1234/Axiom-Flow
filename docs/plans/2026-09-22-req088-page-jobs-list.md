# 页级失败清单与 jobs 列表端点（REQ-088，原 REQ-087）

状态：Accepted
任务类型：B
最后更新：2026-09-22
关联 ADR：[ADR 0007](../adr/0007-parse-status-semantics.md)（本计划只是把已裁决的语义暴露为只读端点，不新增语义）
关联设计：[parsing-management.md](../design/parsing-management.md)、[parsing-pipeline.md](../design/parsing-pipeline.md)、[api.md](../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-008、REQ-088、ARCH-005、ARCH-006）
归档判定：Delete（端点契约晋升 `api.md` 与 `code-map.md`，事实有 Git 锚点；无不可替代证据时删壳）

> 本计划从 [PLAN-003](../history/plans/2026-09/2026-09-20-parse-live-bringup.md) 关闭时转移承载。
> **编号沿革**：本行 2026-09-21 首登为 `REQ-086` → 2026-09-22 00:57 改号 `REQ-087`（根侧把 086 用给了
> 「全本分块提交」）→ 同日再改号为 **`REQ-088`**：根仓库 2026-09-22 00:35 已用 `REQ-087` 登记
> 「空白页合法化」（本仓库同号承接，见 [PLAN-007](2026-09-22-req086-whole-book-window.md)），
> 本仓库为该号的**后占者**，按「后占者让号」（判例同 `REQ-001`/`REQ-086`）让号。改号零外部代价：
> 本行由本仓库自登、根侧从未引用该号。

## 目标与成功标准

**目标**：把 ADR 0007 已经落地并实施完成的状态语义，暴露成前端可直接消费的两个**只读**数据源，
使根仓库 ARCH-020-E 的「任务中心抽屉」与「失败页重解析按钮」可排期，并借这两个端点完成
ARCH-005（对照与质量度量）与 ARCH-006（块编辑真机闭环）的联调收口。

**为什么现在可以做**：登记时（2026-09-21）刻意不实施的理由已被解除——`api.md` 是根侧 8900 透传的
契约事实源，端点一旦开出即成契约，而当时 DEFECT-006/007 的状态语义未定，先开列表端点等于把
错误语义固化给前端（ADR 0004：不做静默兼容层）。现语义已由 [ADR 0007](../adr/0007-parse-status-semantics.md)
裁决并由 PLAN-006 实施关闭 ⇒ 数据来源四条（`failed_pages`、`last_parse_result`、`status` 六态、
`/versions`）**全部具备**，只剩端点本身。

**成功标准**：

| # | 标准 | 判据 |
| --- | --- | --- |
| S1 | `GET /api/v1/parse-jobs` 返回可按书/状态过滤、带分页的作业列表，**含 `queued`/`running`/`completed`/`partial`/`failed`/`interrupted` 六态原值**，不做任何「看起来更好」的改写 | 契约测试（六态各一条 + 过滤 + 分页 + 排序稳定） |
| S2 | `GET /api/v1/books/{book_id}/pages` 一次查出**生效版本**的逐页状态（含 `failed` 与「无产物」两类），使前端不再 1..page_count 逐页试 | 契约测试（含失败页书、legacy 书、未 ingest 书三种形态） |
| S3 | 两端的读取作用域与既有页读取端点**同一函数**（`api/main.py:185-203` `_read_scope`），不出现第二套作用域实现 | 代码走查 + 契约测试断言 `scope`/`job_id` 自证字段 |
| S4 | 零写入面、零凭据外泄、响应不含磁盘绝对路径 | 契约测试 + 现有 404/422 约定不变 |
| S5 | ARCH-005/006 的 8903 联调真机通过（根侧 ARCH-020-D/UI 确认） | 根侧回执 |

## 范围与非目标

**范围**：

- `api/main.py`：新增两条 GET 路由（现共 16 条，无 `GET /api/v1/parse-jobs` 列表端点，
  `main.py:526` 只有 `GET /parse-jobs/{job_id}`）。
- `db/repositories.py`：`ParseJobRepository` 增全局列表查询（现仅 `list_for_book(book_id)`
  `:231`，无状态过滤/分页）；`PageRepository` 增按 `(book_id, job_id)` 的**逐页状态清单**查询
  （现 `list_for_book` `:341` 返回整页内容、`count_parsed` `:349` 只数 `parsed`，**无 failed 查询**）。
- `schemas.py`：两个响应模型（列表项复用 `ParseJob`；页清单为轻量 `{page_no, status, has_artifact, error?}`
  + 顶层 `job_id`/`scope`/`total`）。
- 文档：`api.md` ③类端点两节（该文件 `:294` 治理规则＝**先更新本文件与契约测试，再实现**）+
  `code-map.md` 行更新 + `operations.md:134` 的「按既有 job_id 续跑属 REQ-087 范围」措辞随改号更新。
- 联调面：与根仓库约定消费方式（轮询间隔、抽屉只列活跃 or 全书历史的默认取法），ARCH-005/006
  的对照与块编辑写入随联调收口。

**非目标**：

- 不新增任何**写入**端点（重解析按钮复用既有 `POST /parse-jobs` 的 `pages:[n]`，本计划不加批价格式，
  除非根侧裁决要求——见「前置条件」问题 3）。
- 不做 WebSocket/SSE 推送（前端轮询即可，避免新增长连接运维面）。
- 不改 `JobStatus`/`LastParseResult`/`progress` 字段形状（ADR 0007 冻结）。
- 不做跨版本聚合视图（只读生效版本 + 显式 `scope`，旧代际 `legacy` 行不混入）。
- 不复活 `web/app.js` 的旧 `/jobs` 门面（那是独立 demo 前端，未接 `/api/v1`，不在本契约内）。

## 前置条件

1. **三条根侧裁决输入**（PLAN-003 承载节已发出，作为排期输入而非本方阻塞；未回时按**保守默认**实现）：
   ① 任务中心要「全书 jobs 历史（分页）」还是「仅活跃 jobs」→ 默认：两者同一端点，`status` 可选过滤、
   默认不过滤、按 `created_at` 倒序 + 分页；
   ② 页级清单是否覆盖 `legacy` 哨兵行 → 默认：跟随 `_read_scope`，`active_job_id` 为 NULL 时读 legacy
   并在响应里显式标 `scope="legacy"`；
   ③ 失败页重解析粒度 → 默认：单页 `pages:[n]`（现有能力），批价格式等根侧明确要求再加。
2. ARCH-005/006 联调需 8903 前端排期（根侧动作）；本方端点与产物就绪不构成联调完成的证据。
3. 可联调产物面现状：三本生效版本（b01 `86ab56ae2b6c`、b05 `185a37d55985`、b11 `bbe0d401ce83`，
   各 20 页几何与类型可信），`af_block_edits` 现 0 行。

## 工作项

| # | 工作项 | 产物 | 门禁 | 状态 |
| --- | --- | --- | --- | --- |
| W1 | 契约成文：`api.md` 两节 + 响应字段表 + 错误语义（404 书不存在 / 404 版本目录缺失） | 文档改动（先于实现） | 契约 | Pending |
| W2 | 仓储查询 + 红灯契约测试（复用 `tests/contract/test_api_v1_contract.py:88-109` 的 `_build`/`client` fixtures 与 `_sync_book`/`_ingest` 助手） | `repositories.py` 两方法 + 契约测试先红后绿 | 契约 | Pending |
| W3 | `GET /parse-jobs` 端点（过滤/分页/排序稳定；响应字段与 `ParseJob` 一致） | `main.py` 路由 + 契约测试 | 契约 | Pending |
| W4 | `GET /books/{id}/pages` 端点（逐页状态清单 + `scope`/`job_id` 自证） | 同上 | 契约 | Pending |
| W5 | 文档与映射收口：`code-map.md`、`operations.md` 改号措辞、`parsing-management.md` 端点面 | 文档改动 | 架构/设计契约测试 | Pending |
| W6 | 8903 联调（ARCH-005 对照与质量度量、ARCH-006 块编辑写入与合并回显）+ 跨项目回执 | 联调记录 + 回执正文（根侧自读登记） | 真机 | Pending |
| W7 | 收口（六步，ADR 0006） | 本计划关闭与归档 | 全量门禁 | Pending |

## 验证与验收

- 每条端点先写红灯契约测试（TDD），断言形状与错误码；六态各造一条作业行验证**原值透传**。
- 常规门禁：`pytest tests -q` + `ruff check src tests scripts`；架构/设计文档触发项按
  `tests/contract/test_architecture_documents.py`、`test_design_documents.py`、`test_code_document_mapping.py`。
- 若 W6 涉及真机重解析或块编辑写入：显式跑 `QED_TEST_MYSQL=1 pytest tests/integration` 与
  `QED_SMOKE_LIVE=1 pytest tests/smoke -m smoke`（`guides/development.md` 收口轮口径）。
- S1~S4 由本方判定；S5 由根侧确认，未确认前 REQ-088 可关闭（端点交付判据）但 ARCH-005/006 保持开放。

## 回滚

- 纯只读端点：删除两条路由 + 两个仓储方法即回滚，无数据迁移、无磁盘写、无 DDL。
- 若根侧消费后要求改形状：按「契约事实源先改文档」的顺序另立计划，本计划不留兼容层（ADR 0004）。

## 关闭与归档

- **关闭条件**：S1~S4 达成且根侧确认可用 ⇒ REQ-088 行关闭；ARCH-005/006 需 S5（真机联调通过）才关闭。
  三行全关后本计划按 **Delete** 删壳（事实已在 `api.md`/`code-map.md`，Git 锚点有效），
  在 `plans/index.md` 登记去处与锚点。
- 收尾顺序（ADR 0006 六步）：本文写结论 → todo 行压缩关闭 → `completed.md` 迁移 →
  `plans/index.md` 登记 → `project-status.md` 刷新 → 全量门禁。

## 承载 REQ-088（根侧 ARCH-020-E 的数据源需求：页级失败清单与 jobs 列表）

**目的**：为根侧「任务中心抽屉」与「失败页重解析按钮」提供可信只读数据源。
**关闭条件**：两端点成文实施带契约测试，根侧确认可用。
**工作项**：W1~W5（+ W6 联调）。
**状态**：Pending（计划已立，实施未开工）。
**登记事实**：根侧未单独立号（其 ARCH-020-E 证据列「待 8902 页级失败清单与 running jobs 数据源
确认后排期」），本仓库登记为请求接收行；本仓库现况确无该两端点（路由清单 16 条，见「范围」）。

## 承载 ARCH-005（解析对照与质量度量：页图 vs 重渲染叠框与质量信号进对照视图）

**目的**：让前端对照视图拿到可信的叠框与质量信号。
**关闭条件**：8903 前端真机联调通过（根仓库 ARCH-020-D/UI）。
**工作项**：W6。代码侧前置已全清（DEFECT-004/005/018 关闭），可联调产物为三本生效版本各 20 页；
质量信号字段随页产物落盘并由页端点返回。
**状态**：Open（等 8903 排期）。

## 承载 ARCH-006（渲染数据供给与块编辑真机闭环：edit 写入与 pages 合并回显）

**目的**：块编辑写入 + 页读取合并回显在真机上闭环一次。
**关闭条件**：代表书重解析后 `af_block_edits` 真机写入且联调通过。
**工作项**：W4（页清单暴露编辑存在性）+ W6（真机写入与回显）。端点与仓储已交付、读取面 L7 全绿，
块下标与 `latex` 在三个生效版本上可信，`af_block_edits` 现 0 行（无迁移负担）。
**状态**：Open（等 8903 排期）。
