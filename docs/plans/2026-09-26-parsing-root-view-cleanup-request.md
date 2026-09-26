# 解析产物根视图清理实施（parsing-root-view-cleanup）

状态：Accepted
任务类型：B
最后更新：2026-09-26
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0009](../adr/0009-data-root-must-be-explicit.md)（数据根显式，ARCH-034 在飞）；无新增 ADR
关联设计：`docs/design/parsing-pipeline.md`（PP-2/PP-14/PP-15/PP-16 为 R5 修订对象）、`docs/architecture/database-design.md`（af_books 列迁移随 R1）；根仓库权威上下文（只读）：`QED-Engine/docs/design/parsing-flow.md` §5、`QED-Engine/docs/design/dataset-conventions.md`
关联 Tracker：docs/trackers/todo.md（REQ-097 支线行、PLAN-017 主线行）
归档判定：Retain（含 R1~R5 实施锚点、迁移版本号与门禁读数，作根仓库 REQ-097 回执关闭证据）

> 本壳为根仓库 REQ-097 跨项目请求（2026-09-26 发出，源自 ARCH-032 存储链路勘察）的承接实施计划。
> 原请求文档 `2026-09-26-parsing-root-view-cleanup-request` 随承接登记正规化为治理合规的计划形态
> （本文件名保留请求期 slug 以维持根侧引用）。白名单/守护同步：本壳 + todo 两行即 PLAN-017 承接
> 第一步，`test_plan_governance.py` / `test_tracker_governance.py` 以本壳现状为基准。

## 背景与根侧设计裁决

ARCH-032 勘察在解析侧发现三个「单一事实源」违反点：

1. **根视图双写**：`<book_id>/book.json` 与 `<book_id>/manifest.json` 同时被 ingest 写入，
   且版本目录继承复制一份 `book.json`，造成「哪份是正本」的歧义；
2. **旧版本驻留**：版本激活后旧版本目录无清理规则，磁盘长期堆积历史版本；
3. **文档布局表述过时**：根仓库 `parsing-flow.md §5` 旧表述已由根侧修正（W1，REQ-097 计划壳）。

根侧用户设计裁决（2026-09-26）：

- 根视图只保留 `pages/*.png`（ingest 产物，版本目录硬链接来源）
- 所有其他文件只在 `versions/<active_job_id>/`（版本目录为唯一正本）
- 激活新版本时自动删除旧版本目录
- `af_books.active_job_id` 设置 = 正式确认语义，无需额外步骤

## 目标与成功标准

**目标**：解析产物落盘收敛为「根视图唯一 `pages/*.png` + 版本目录唯一正本」，元数据事实源
统一到 af_books，激活即轮换（删旧版本目录）。

| ID | 标准（=根侧 REQ-097 S1~S5，S6 根侧消费面回归由根仓库验证） |
| --- | --- |
| S1 | 新 ingest 后根视图只有 `pages/p*.png`，无 book.json / manifest.json |
| S2 | 新版本激活后旧版本目录被删除，同一本书磁盘只有一个版本目录 |
| S3 | `parsing-pipeline.md` 产物布局图与实现一致 |
| S4 | af_books 包含全量 book.json 字段（含 ingest_sha256） |
| S5 | 本仓库门禁（pytest + ruff）全绿，现有契约测试通过（数据根不变式 + 产物三件套 + manifest 完整性） |

## 范围与非目标

- 范围：`ingest/importer.py`、`ingest/artifacts.py`、`orchestrator/pipeline.py`、`db/`（Alembic）、
  API 回落路径、`docs/design/parsing-pipeline.md`、相关单测与契约测试。
- 非目标：版本 `manifest.json` 内容格式（路径相对版本目录，PP-16 语义）不变；硬链接机制不变
  （根视图 `pages/*.png` 仍是版本目录页图硬链接来源，PP-15）；REQ-096 对账 doctor 实施不在本壳
  （S4 只要求 af_books 字段完整）；根仓库 8900 适配层消费面（S6）归根仓库自验。

## 前置条件

- **R1 迁移前置**：af_books 补列需 Alembic 迁移先行（对齐 `af_parse_jobs.source_sha256` 先例），
  真实库迁移窗口需用户批准（D 类操作）；ingest_sha256 回填存量书目的取数口径（磁盘重算 or 版本
  book.json 读取）实施时定。
- **R4 删除语义前置**：删除旧版本目录属 D 类数据操作——仅在 `active_job_id` 更新事务成功后执行、
  记录删除路径与时刻日志；边界为只删被替换的那一个旧版本（不批量清历史）。
- **与 ARCH-034 边界**：该轮在飞改动（瞬态残留识别、写入面锁 `parsed/`）已完成 W1~W7；本壳改动
  不得静默覆盖其守护测试口径，冲突处以 REQ-097 目标状态为准并在 W 项记录。
- af_books 字段核查表（R1 第一步，来源＝根仓库 book.json 字段清单）：

| book.json 字段 | af_books 对应列 | 现状 |
| --- | --- | --- |
| book_id / title / authors / page_count / source_file | book_id / title / authors / page_count / file_path | 已有 |
| sha256（ingest 时计算） | — | **缺，需补 `ingest_sha256 VARCHAR(64) NULL`** |
| ingested_at（ingest 完成时刻） | created_at 可复用或另建 | 实施时确认 |
| engine（最近解析引擎） | 不需补（per-job 在 af_parse_jobs.engine） | — |

## 工作项

| # | 工作项（=根侧请求 R1~R5 + 回执） | 状态 |
| --- | --- | --- |
| W1（R1） | af_books 字段完整化：核查→Alembic 补 `ingest_sha256`（含 ingested_at 口径裁决）→`ingest_book()` 写入 sha256/ingested_at 并**停止向文件系统写 root `book.json`**→删除「空 af_books 回落文件系统 book.json」降级路径（书不在 af_books 即 404，与文件系统解耦） | 待开始 |
| W2（R2） | `ingest/importer.py` / `ingest/artifacts.py`：ingest 完成不再调用 `build_manifest()` 写根视图 `manifest.json`；完成后根视图仅有 `pages/p*.png` | 待开始 |
| W3（R3） | `seed_version_from()`（PP-15 播种）：版本目录 `book.json` 改从 af_books DB 读取生成（book_id/title/authors/page_count/ingest_sha256/source_file/ingested_at + 当次 job 的 engine），不再从 root 文件复制 | 待开始 |
| W4（R4） | `orchestrator/pipeline.py`：`af_books.active_job_id` 更新后删除被替换的旧版本目录 `versions/<old_job_id>/`；D 类留痕（路径+时刻入日志）；旧目录读取中可跳过删除并告警（简单处理即可） | 待开始 |
| W5（R5） | `parsing-pipeline.md` 同步：产物布局图更新为「根视图仅 pages/*.png + 唯一版本目录」；PP-2（不再写 root book.json/manifest.json）、PP-14（激活即删旧版本，替换「不自动删除」）、PP-15（播种 book.json 从 af_books 生成）、PP-16（删除根视图 manifest 描述） | 待开始 |
| W6 | 回执根仓库 REQ-097：实施锚点（git commit sha）、af_books 迁移版本号、门禁读数（pytest passed / ruff clean）、S1~S5 逐条判定 | 待开始 |

## 验证与验收

- TDD：每项先红后绿；ingest 产物面契约（三件套 + manifest 完整性 + 数据根不变式）必须全绿；
  W4 删除逻辑单测用临时目录构造双版本场景，禁读写真实数据根。
- 回归：`pytest tests -q` + `ruff check src tests` 全量门禁。
- 消费面（S6）由根仓库经 8902 API 自验，不属本壳门禁。

## 回滚

- W1~W4 为代码+迁移改动：逐文件回退 + Alembic downgrade；停用删除逻辑即回「旧版本驻留」现状。
- 已删除的旧版本目录不可回滚——故 W4 只在 active_job_id 事务成功后执行且保留日志留痕。

## 关闭与归档

- 关闭条件：S1~S5 全达成 + W6 回执成文；按 ADR 0006 六步收口（结论入本壳→todo 两行关闭→
  completed.md→plans/index 无需登记（索引导航制）→project-status 刷新→门禁全绿）。
- 归档判定 Retain：本壳随回执归档（根侧 REQ-097 关闭证据引用其门禁读数与锚点）。
