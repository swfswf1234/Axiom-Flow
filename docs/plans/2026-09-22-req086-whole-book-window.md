# 全本解析按页窗口分块提交与空白页合法化（REQ-086 / REQ-087）

状态：Accepted
任务类型：B
最后更新：2026-09-22
关联 ADR：[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)（引擎由配置选择、不设自动兜底）、[ADR 0007](../adr/0007-parse-status-semantics.md)（任务六态与激活门槛，本计划**不改**其语义）
关联设计：[parsing-pipeline.md](../design/parsing-pipeline.md)、[model-integration.md](../design/model-integration.md)
关联 Tracker：docs/trackers/todo.md（PLAN-007、REQ-086、REQ-087、ARCH-009、ARCH-019）
归档判定：Retain（含 b05 全本真机复测与 D 类数据根/业务库写操作留档，属不可替代外部证据）

> 本计划从 [PLAN-003](../history/plans/2026-09/2026-09-20-parse-live-bringup.md) 关闭时转移承载，
> 承接根仓库 2026-09-21/22 的两条入站请求（`REQ-086` 分块提交、`REQ-087` 空白页合法化），
> 并把 ARCH-009 的收尾取证与 ARCH-019 的映射缺口挂在同一承载下——**四条行共享同一判据现场**：
> b05 全本真机复测。分开立项会让「全本跑通」这件事被切成三份互相等待的台账。

## 目标与成功标准

**目标**：让 60 页以上的书在一次全本任务（`pages: null`）里跑完并**能激活**，消除两处确定性失败：

1. **600s 单墙**：`parse_document` 整本一次提交，提交→轮询→取结果共用一个 deadline
   （`engines/mineru.py:321` 每次 `_run` 只算一次 `deadline = monotonic() + self.timeout`；
   `config.py:54` `ocr_timeout: int = 600`），而 MinerU 侧本就分批推理 ⇒ 317 页书第 1 窗口即超时。
2. **空白页被判失败**：引擎对空 `content_list` + 空 md 抛 `EngineError('MinerU 解析完成但无结构化产物')`
   （`engines/mineru.py:170-173`）⇒ 页级 `failed` ⇒ 激活门槛（请求页全齐，`pipeline.py:439`）永不满足
   ⇒ **含空白页的书永远无法激活**。

**成功标准（逐条可判）**：

| # | 标准 | 判据来源 |
| --- | --- | --- |
| S1 | 全本 job 内每块提交**独立计 deadline**，单块超时/失败只损失该块（该块页行 `failed`），job 继续推进 | 契约测试新增「单块失败不置整本 failed（带重试）」 |
| S2 | `progress.parsed` 随块单调递增且可在真机观测到非零值 | 契约测试新增「全本 job 进度单调递增」+ 真机留档 |
| S3 | b05（317 页，含 5 空白页）全本真机复测：`completed` **且激活**（`active_job_id` 指向新版本、317 页全覆盖、空白页计 parsed） | W6 真机复测留档 + 根侧复测确认 |
| S4 | 页语义、版本目录布局、激活门槛、单一占用闸门**零变化**（PLAN-004 条文 V1~V10 与 ADR 0007 不动） | 现有契约/集成测试全绿即证 |
| S5 | 空白页有专用质量标记，可在产物与页行上区分「空白页」与「解析失败」 | schemas/归一化单测 |

## 范围与非目标

**范围**（按模块）：

- `engines/mineru.py`：`parse_document()` 增窗口参数（映射到 MinerU 3.4.4 `/tasks` 表单字段
  `start_page_id`/`end_page_id`，二者已在本仓库 W0 冻结的 OpenAPI 留档
  `data/probes/mineru-3.4.4-20260920/raw/002_openapi_200.bin` 中确认可用，默认 `0`/`99999`）；
  deadline 改为**每次提交独立计算**；返回页号按窗口偏移还原为**全书绝对页号**。
- `orchestrator/pipeline.py`：全本分支（`:400-409`）由「一次调用 + 字典取页」改为**串行窗口循环**，
  每块完成即走既有 `_handle`（页产物写先于页行 commit）+ `_commit` + `_flush_progress`，
  块级 `EngineError` 捕获后只标记该块页失败（复用 `_mark_failed` 与 `_resumed_pages` 续跑）。
- `config.py`：新增窗口大小设置（建议 `parse_window_size: int = 56`，env 键 `AXIOM_PARSE_WINDOW_SIZE`，
  命名与 `_ENV_MAP` 现有 `AXIOM_*` 一致）；`ocr_timeout` 注释与语义改记「**单块**一次调用超时秒数」。
- 空白页合法化：`_pages_from_result` 的空产物分支不再抛错，产出 `blocks=[]` 的合法页结果并带质量标记；
  逐页路径同样受益（同一函数）。
- 文档：`parsing-pipeline.md` 增「全本窗口分块提交与按块 deadline」条文与空白页语义、
  `guides/local-dev.md`「解析超时」节与 `guides/operations.md:167`「容器已起但解析慢/超时」的
  600s 调参口径按块改写、`code-map.md` 相应模块行更新。

**非目标**（明确不做，越界即缺陷）：

- 不做并行提交、不起多个 MinerU 占用者（用户 2026-09-21 资源裁决：本 GPU 只够一个占用者，串行）。
- 不改激活门槛、不改任务六态/`last_parse_result`/`progress` 字段形状（ADR 0007 冻结）。
- 不做旧版本磁盘清理、不改 `versions/<job_id>/` 布局（PLAN-004 冻结）。
- 不引入 PDF 切分依赖（不装 `pypdf`）：窗口由引擎侧 `start_page_id/end_page_id` 表达，本仓库仍提交整本字节。
- 不改 `api.md` 端点集（列表/失败清单端点属 [PLAN-008](2026-09-22-req088-page-jobs-list.md)）。

## 前置条件

1. **W0 探测先于实现**（只读、单占用闸门下）：四项假设必须冻结为留档，任一与预期相反则回本计划改条文——
   ① `start_page_id`/`end_page_id` 是否含端点、0 基还是 1 基；② 窗口内返回的 `content_list[].page_idx`
   与 `middle_json.pdf_info[]` 索引是**窗口内**还是**全书**（决定偏移量算法；现实现按 0 基全书索引，
   `mineru.py:155-177`）；③ 空白页在窗口边界/整窗空白时的响应形状；④ 56 页窗口的真机耗时（定 deadline 默认值）。
   判据：留档 JSON + `summary.md` 四条结论，缺一不进 W1。
2. MinerU 5002 在线且 `queued_tasks + processing_tasks == 0`、本机无 `queued` 行（C-0 排他口径）。
3. W6 真机复测属 **D 类**（写真实 `dataset/parsed/` + 共享 `qed` 库）：需 `af_*` 备份 + 逐步批准。
4. ARCH-019 的映射目标需用户裁决（见「承载 ARCH-019」），未裁决前 W4 不实施。

## 工作项

| # | 工作项 | 产物 | 门禁 | 状态 |
| --- | --- | --- | --- | --- |
| W0 | 窗口协议冻结探测（①~④） | `data/probes/window-req086-2026092x/` + 结论表 | 只读 | Pending |
| W1 | 引擎窗口参数 + 按块 deadline + 绝对页号还原 | `mineru.py` 改动 + `tests/unit/test_engines.py` 新增（先红：断言表单带窗口字段、两块各算一次 deadline、页号跨窗连续） | 单测 | Pending |
| W2 | 空白页合法化 + 质量标记 | `mineru.py`/`schemas.py`/`normalize` + 单测（先红：空 `content_list` 不再抛 `EngineError`；反向判据：非空 md 仍走整段回退） | 单测 | Pending |
| W3 | pipeline 串行窗口循环 + 块级失败隔离 + 按块进度 | `pipeline.py` + `tests/unit/test_orchestrator.py`（假引擎计数：每块一次提交、块失败仅该块页 `failed`） | 单测 | Pending |
| W4 | ARCH-019 `ref_text` 映射补齐（带回归网） | `normalize/blocks.py` `_TYPE_MAP` + 丢弃对账单测 | 单测 | Blocked（待裁决） |
| W5 | 契约与文档晋升 | `tests/contract/test_api_v1_contract.py` 两条（S1/S2 判据）+ `parsing-pipeline.md` 条文 + guides 两处 600s 口径 + `code-map.md` | 契约 | Pending |
| W6 | b05 全本真机复测（S3）+ 根侧回执 | 留档（版本目录、job 行、`_version_is_complete` 结果、空白页清单）+ 回执正文（REQ-086/REQ-087） | smoke/MySQL 层显式跑 | Pending |
| W7 | 收口（六步，ADR 0006） | 本计划关闭与归档 | 全量门禁 | Pending |

## 验证与验收

- 每步先红灯（`test-driven-development`）：W1/W2/W3 各自的红必须落在**真机观察到的形态**上
  （窗口越界、空白页空 `content_list`、块超时），不接受「实现改了断言跟着改」。
- 常规门禁：`pytest tests -q` + `ruff check src tests scripts`。
- 显式层必须在收口轮重跑（PLAN-003 R2 窗口口径，见 `guides/development.md`）：
  `QED_TEST_MYSQL=1 pytest tests/integration`、`QED_SMOKE_LIVE=1 pytest tests/smoke -m smoke`。
- 真机验收只跑一次全本（补证据不得重解析；需要证据用 `--evidence-only`）。
- 交付判据以 S1~S5 表格逐条判定，任一条不成立本计划保持开放。

## 回滚

- 代码侧无 DDL、无迁移：`git revert` 即回旧行为（整本单任务）。窗口大小可经
  `AXIOM_PARSE_WINDOW_SIZE` 调大退化，但**不做静默降级开关**（ADR 0004）。
- 真机产物按版本目录隔离：新 job 未激活时读取面仍走旧生效版本，复测失败不污染现网读取；
  激活后回滚 = 按 `af_*` 备份还原指针 + 保留新版本目录（不删盘）。
- 业务库备份：复测前 `af_*` 五表 dump（同 R2 窗口口径 `data/backups/`）。

## 关闭与归档

- **关闭条件**：S1~S5 全部达成 + 根仓库以其 b05/b11 全本复测确认并回执 ⇒ REQ-086/REQ-087 行关闭；
  ARCH-009/ARCH-019 按其自身判据关闭（可早于本计划）。四条活行全部关闭后本计划按 **Retain**
  归档 `docs/history/plans/2026-09/`，并在 `plans/index.md` 与 history 索引登记。
- 收尾顺序（ADR 0006 六步）：本文写结论 → todo 行压缩关闭 → `completed.md` 迁移 →
  `plans/index.md` 登记 → `project-status.md` 刷新 → 全量门禁。

## 承载 REQ-086（根侧入站：全本按页窗口分块提交 + 超时按块计）

**目的**：根治「整本单任务 × 600s 墙」，使 >60 页书的本全本 job 可完成、可观测、可续跑。
**关闭条件**：分块实施经 b05 全本真机复测通过（或根侧接受替代方案）后发出回执。
**工作项**：W0/W1/W3/W5/W6（本计划主体）。
**状态**：Pending（计划已立，实施未开工）。
**根侧原文要点**：其 `todo.md` REQ-086 行（2026-09-21 裁决「A 止血 + B 根治同轮」）建议
「50~64 页/次，带 `start_page_id/end_page_id`」——与本仓库 W0 冻结的 OpenAPI 留档一致，采纳。

## 承载 REQ-087（根侧入站：空白页合法化 + 跨版本续跑语义）

**目的**：空白页是合法产物，不得判失败页并永久阻塞全本激活。
**关闭条件**：含空白页的书全本可激活 + 跨版本续跑语义有成文结论（设计如此 or 缺陷），契约测试两条。
**工作项**：W2（空白页合法化与质量标记）+ W5（契约）+ 下述**②项单独结论**。
**状态**：Pending。

**编号沿革**：本行 2026-09-22 00:35 由根仓库登记为 `REQ-087`（空白页）；本仓库同日 00:57 曾把自登的
「页级失败清单与 jobs 列表端点」行改号为 `REQ-087`——**同号不同事，本仓库为后占者**，按「后占者让号」
（判例同 `REQ-001`/`REQ-086`）把端点行改号为 **`REQ-088`**，根侧 `REQ-087` 以同号入站承接登记。

**②项（跨版本续跑语义）本方结论待取证**：根侧实测「`active_job_id` 已指向 312 页版本时提交全本 job，
进度从 0 整本重解析」（job `550eeec91845`）。代码事实：`_resumed_pages` 只认**本 job 自身**的
`af_pages` 行（页行主键含 `job_id`，PLAN-004 版本作用域），新 job 从基线继承的是**磁盘产物**
（`seed_version_from`）而非页行 ⇒ 重解析是 PLAN-004 作用域规则的**既定结果**，不是抖动。
是否改为「按生效版本页行跳过」属契约变更（影响产物完整性判据与激活门槛），需用户裁决后再实施，
本计划先成文为**已知语义**并回执根侧。

## 承载 ARCH-009（套1 代表书验收：抽查口径已 PASS，剩余为全量取证）

**目的**：b05 + b11 解析质量验收收口（2026-09-22 R2 窗口已按**抽查口径**判定 ①~⑤ PASS）。
**关闭条件**：抽查页公式可解析率 ≥90%、丢弃为 0、`_TYPE_MAP` 缺口归零（等 ARCH-019）。
**工作项**：W4（映射缺口）+ W6（b05 全本复测顺带消除混合版本 chunks 与 5 个缺页，即抽查口径遗留）。
**状态**：In Progress。取证脚本沿用 `scripts/tmp/live_forensic_representative.py`（带 `--selftest` 反向判据）。

## 承载 ARCH-019（`ref_text` 未入 `_TYPE_MAP`，b05 抽样页 26 块可见丢弃）

**目的**：按 ARCH-016 移交条款补齐映射缺口，使代表书页丢弃归零。
**关闭条件**：映射补齐带回归网（单元 + 零丢弃对账），且代表书抽样页 `dropped_types` 为空。
**工作项**：W4。
**状态**：Blocked（待用户裁决映射目标）。
**裁决输入**：引擎字面值 `ref_text` = 参考文献条目。三个候选 `BlockType`——
(A) `PARAGRAPH`：最省事，但丢失「条目」语义，前端无法折叠成参考文献列表；
(B) `LIST`（`items[]` 每条一个文献条目）：语义最贴，代价是同页条目需聚合为单块，改变块下标 `index`
（块编辑键受影响，ARCH-006 需知悉）；
(C) `CAPTION`：不建议，语义错。
本方倾向 **(A) 先止血**（丢弃归零、块下标不变、可回溯），(B) 作为渲染面需求由根侧提出后再议。
`heading`/`list` 两键在两本抽样内未命中属**取证事实**，不是缺陷，不为其加映射。
