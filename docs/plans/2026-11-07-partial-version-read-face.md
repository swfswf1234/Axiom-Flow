# 部分解析版本读面可见与补页续跑（partial-version-read-face）

状态：In Progress
任务类型：B
最后更新：2026-11-07
关联 ADR：[ADR 0007](../adr/0007-parse-status-semantics.md)（`completed` 与激活门槛同源，本案不改该判据）、
[ADR 0015](../adr/0015-db-single-source-for-readiness.md)（就绪的唯一事实源是 DB）、
[ADR 0016](../adr/0016-af-family-rebuild-into-four-tables.md)（版本作用域仍是 `af_operations` 的 parse 行）
关联设计：[parsing-pipeline.md](../design/parsing-pipeline.md)（读面作用域与版本基线的正本）、
[parsing-management.md](../design/parsing-management.md)（`pages_done` 与进度读数）、
[api.md](../architecture/api.md)（④ 类端点 `scope` 字段）
关联 Tracker：docs/trackers/todo.md（PLAN-030 镜像行与 REQ-160 入站请求行，挂在 ARCH-021 聚合行下）
归档判定：Delete（读面口径晋升 `parsing-pipeline.md`，过程只在本壳与 git 历史）

## 目标与成功标准

**目标**：让「已解析的页」在版本未激活时也可读，并让补页任务从最全的既有版本继承，从而
「部分解析 → 补页 → 激活 → 进入知识点梳理」这条链在真实书上能走通。

1. 读面作用域＝**该书记录中已解析页数最多的可见版本**（`active_job_id` 只在并列时优先），
   而不是只看 `af_books.active_job_id`：`partial`／`failed` 版本里已落盘的页立即可读。
2. 解析任务基线取同一作用域：补页任务从最全版本硬链接继承产物与页行，`pages_done` 不被
   小版本覆盖；目标页齐备后照旧激活并删除被替换版本与基线版本目录。
3. `active_job_id` 非空但目录缺失仍显式 404（不静默回落到别的版本或根视图）。

判据正本在 `docs/trackers/todo.md` 的 REQ-160 行，本壳不复制 S 条目。

## 范围与非目标

**范围**＝`_read_scope` 的作用域解析、`run_parse_job` 的基线选择与激活后的版本目录清理、
`GET /books/{id}/pages` 的 `scope` 取值、对应单测／契约测试与设计条文。

**非目标**＝① 不改激活门槛（仍是「本次任务目标页齐备」，ADR 0007）；② 不改 `af_*` 表结构与
任何 DDL；③ 不改块产物形状与归一化；④ 不修补现网产物目录（不做数据迁移，只改代码）；
⑤ 不重发未经用户发令的解析批次。

## 前置条件

1. 现网取证（2026-11-07）：`mathanalysis-b11` 的 `active_job_id=f9c20a1f7c17` 只含 50 页，
   同书另有 356 页版本目录未激活；`mathanalysis-b13` 无活动版本、300 页版本目录不可读。
2. 根仓库侧 `GET /books/{id}/pages` 透传已就绪（8900 契约测试在册），本仓返回结构不变。

## 工作项

| 条目 | 内容 | 状态（含证据） |
| --- | --- | --- |
| W1 | 读面作用域解析器：候选＝本书记录中非 `queued`/`running` 且版本目录存在的 parse 版本，按已解析页数取最大，并列时 `active_job_id` 优先、再按创建序取最新；无候选回落 legacy 根视图；active 目录缺失仍 404 | 完成（2026-11-07）：`orchestrator/versions.py:resolve_visible_version()`；候选另要求已解析页 >0 且 `created_at >= af_books.ingested_at`（重入库前的旧版本不复活）；排序＝已解析页数 → active 指针 → 创建时刻 → `job_id`；`api/main.py:_read_scope()` 调它，指针目录缺失仍抛 404「生效版本产物缺失」 |
| W2 | `run_parse_job` 基线改用同一解析器；激活后删除被替换的 `active` 与基线两个版本目录（同目录去重） | 完成（2026-11-07）：`orchestrator/pipeline.py` 基线改取 `resolve_visible_version()`；激活后按 `dict.fromkeys((replaced_job_id, baseline_job_id))` 逐个 `_retire_replaced_version`；真机 b11 补页激活后盘上 `248a8bfb1a3c`／`f9c20a1f7c17` 两目录已删，`91dde1c8a7e8`／`41218cb55e81`（未继承的旧版本）按 PP-14 保留 |
| W3 | `GET /books/{id}/pages` 的 `scope` 自证随作用域：非 legacy 一律 `active`（该值语义改为「版本作用域」，口径写进 api.md） | 完成（2026-11-07）：真机 b11 在指针另指 50 页版本时返回 `job_id=248a8bfb1a3c`、`scope=active`（语义＝读某个解析版本目录，含未激活版本）；`api.md` 生效版本段、③ 节、`PageListOut`、`GET /books`、唯一入口规约五处已改写 |
| W4 | 单测与契约测试先红后绿：partial 版本可读、补页继承 356 页、激活后全页可读、active 目录缺失 404、legacy 回落不变 | 完成（2026-11-07）：`tests/contract/test_api_v1_contract.py:TestPartialVersionReadFace` 三例（partial 可读／补页继承并激活／指针目录缺失 404）；全量 `1419 passed／1 failed／18 skipped`，唯一失败为既存断链（AF 计划指向根仓库已迁走的 `2026-10-26-block-label-and-furniture-identification.md`，HEAD 即在、非本轮改动）；`ruff check src tests` 全绿 |
| W5 | 设计条文晋升：`parsing-pipeline.md` 读面作用域与基线两节改写；`api.md` 的 `scope` 说明随步 | 完成（2026-11-07）：新增 `PP-22` 读面作用域条文，`PP-14`／`PP-15`／`PP-18`、电源中断与磁盘产物降级行改指 `PP-22`；`api.md` 五处随步；追溯节补 `PP-22` 来源；`tests/contract/test_design_documents.py` 9 passed |

## 验证与验收

- `pytest tests -q` 与 `ruff check src tests` 全绿；
- 真机（用户 2026-11-07 发令）：`mathanalysis-b11` 补页 201-250 后 406/406 激活且全页 200；
  `mathanalysis-b13` 补页 301-688 后 688/688 激活；两书解析完成后梳理受理门（`active_job_id`
  非空）可通过。

- **真机（2026-11-07）**：b11 补页 `49a3950dc972`（201-250，50/50）→ `pages_done=406`、
  `active_job_id=49a3950dc972`、读面 406 全 `parsed`；b13 补页 `521d13cb765d`（301-688，388/388）
  → 688/688、`active_job_id=521d13cb765d`、读面全 `parsed`；两书 `POST /kp-jobs` 越界探针
  均越过「无生效解析版本」门、止于页区间 422（零写入），梳理受理门已通。
## 回滚

代码回滚＝还原 `_read_scope` 与 `run_parse_job` 基线两处改动（git 反向提交）；数据面无回滚需求
（不写 DDL、不迁移产物）；被删除的旧版本目录只在新版本激活并提交后删除，回滚不恢复旧目录，
但指针仍指向有效版本，读面不受影响。

## 关闭与归档

判据全达成后按 `qed-closeout` 关闭：读面口径写入 `parsing-pipeline.md`，本壳按 `Delete` 删除，
REQ-160 行从 todo 原子移入 `completed.md`。
