# 解析产物根视图清理实施（parsing-root-view-cleanup）

状态：Accepted
任务类型：B
最后更新：2026-09-26
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、[ADR 0009](../adr/0009-data-root-must-be-explicit.md)（数据根显式，其实施轮 PLAN-016/ARCH-034 已于 2026-09-26 关闭）；无新增 ADR
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
- **与 ARCH-034（PLAN-016，已关闭）边界**：该轮落地的改动（瞬态残留识别式、写入面锁 `parsed/`、
  数据根 fail-closed、深度无关枚举及其守护测试）本壳不得静默覆盖其守护口径，冲突处以 REQ-097
  目标状态为准并在 W 项记录（例：根视图 `book.json` 退役会改变 `is_book_dir` 的产物标记前提，
  W1/W2 实施时同批改口径并跑存储治理守护）。
- af_books 字段核查表（R1 第一步，来源＝根仓库 book.json 字段清单）：

| book.json 字段 | af_books 对应列 | 现状 |
| --- | --- | --- |
| book_id / title / authors / page_count / source_file | book_id / title / authors / page_count / file_path | 已有 |
| sha256（ingest 时计算） | `sha256 VARCHAR(64) NOT NULL`（首建即在，`update_ingest` 维护） | 核查表原判「缺」有误，见偏差 D1 |
| ingested_at（ingest 完成时刻） | `ingested_at DATETIME NULL`（`20260926_0001` 补列，不复用 created_at：后者是行创建时刻，语义不同） | 已落地，不回填（偏差 D2） |
| engine（最近解析引擎） | 不需补（per-job 在 af_parse_jobs.engine） | — |

## 工作项

| # | 工作项（=根侧请求 R1~R5 + 回执） | 状态 |
| --- | --- | --- |
| W1（R1） | af_books 字段完整化：核查→Alembic 补 `ingest_sha256`（含 ingested_at 口径裁决）→`ingest_book()` 写入 sha256/ingested_at 并**停止向文件系统写 root `book.json`**→删除「空 af_books 回落文件系统 book.json」降级路径（书不在 af_books 即 404，与文件系统解耦） | Done（本地判据，2026-09-26，锚 `f2fd689`）——按偏差 D1/D2 落地 |
| W2（R2） | `ingest/importer.py` / `ingest/artifacts.py`：ingest 完成不再调用 `build_manifest()` 写根视图 `manifest.json`；完成后根视图仅有 `pages/p*.png` | Done（本地判据，锚 `f2fd689`）；`read_book_meta()` 随读取面一并删除，manifest 端点 legacy 作用域固定 404（不重算根清单，避免复活被退役文件） |
| W3（R3） | `seed_version_from()`（PP-15 播种）：版本目录 `book.json` 改从 af_books DB 读取生成（book_id/title/authors/page_count/ingest_sha256/source_file/ingested_at + 当次 job 的 engine），不再从 root 文件复制 | Done（本地判据，锚 `f2fd689`）——按偏差 D3 并入 W1 批次：`book.json` 入播种黑名单 `_NOT_SEEDED`，版本正本由 `run_parse_job` 从 af_books 行生成 |
| W4（R4） | `orchestrator/pipeline.py`：`af_books.active_job_id` 更新后删除被替换的旧版本目录 `versions/<old_job_id>/`；D 类留痕（路径+时刻入日志）；旧目录读取中可跳过删除并告警（简单处理即可） | Done（本地判据，锚 `f2fd689`）：删除只在指针提交成功后执行；`legacy`/同 job 续跑不删；`OSError` 仅告警保留目录、任务终态不翻转。真机数据根上的删除效果待 D 类窗口（W7） |
| W5（R5） | `parsing-pipeline.md` 同步：产物布局图更新为「根视图仅 pages/*.png + 唯一版本目录」；PP-2（不再写 root book.json/manifest.json）、PP-14（激活即删旧版本，替换「不自动删除」）、PP-15（播种 book.json 从 af_books 生成）、PP-16（删除根视图 manifest 描述） | Done（锚随文档批）：布局图/PP-2/14/15/16 + 非目标删「旧版本磁盘清理不做」条 + 失败语义加删除持有一行；连带 `parsing-management.md`（mermaid 激活轮换 + 无回落 + manifest legacy 404）、`api.md`（ingest/manifest/pages/书目面/契约测试面五处）、`code-map.md` 三行、`database-design.md`（列清单/迁移表/无回落条文） |
| W6 | 回执根仓库 REQ-097：实施锚点（git commit sha）、af_books 迁移版本号、门禁读数（pytest passed / ruff clean）、S1~S5 逐条判定 | 正文已成文（见下「REQ-097 回执正文」，含真机递延声明）；S2 真机判定待 W7 后补记，届时随六步收口关闭 |
| W7 | 真机 D 类窗口（排期在用户）：业务库 `qed` 升 `20260926_0001` + 真实数据根重 ingest/激活删旧实测；恢复 ARCH-011 批量重建的前置之一 | Blocked（待用户批准窗口） |

## 实施偏差登记（2026-09-26 W1~W4 批次）

- **D1 不新增 `ingest_sha256` 列**：字段核查表「sha256 缺列」判定有误——`af_books.sha256`
  （VARCHAR(64) NOT NULL，`20260917_0001` 首建）本就承载 ingest 源文件指纹且由 `update_ingest`
  维护。S4 的达成口径改为「af_books 覆盖 book.json 全字段」，非「再加一列」。
- **D2 `ingested_at` 不回填**：迁移 `20260926_0001` 纯加列；存量行 `NULL` 如实表示「本列规则
  确立之前 ingest」，不猜值、不重算。
- **D3 W3 并入 W1/W2 批次实施**：根视图 `book.json` 退役与版本 `book.json` 生成是同一契约的
  两面，拆开任一批都会出现「版本目录也无 book.json」的中间态；故播种改黑名单 + 任务生成与
  W1/W2 同批落地（锚点同一）。

## S 判据本地判定（真机项挂 W7）

| ID | 判定 |
| --- | --- |
| S1 | ✅ 本地：`test_ingest_writes_only_page_images_into_root_view`（含残留旧布局文件被替换时清退） |
| S2 | ✅ 本地：`TestActivationRetiresReplacedVersion` 四例 + 契约 `test_second_version_replaces_the_effective_one`；真机磁盘效果待 W7 |
| S3 | 随 W5 文档同步判定 |
| S4 | ✅（按 D1 口径）：`ingested_at` 补列 + `test_update_ingest_stamps_ingested_at`；列清单对账守护通过 |
| S5 | ✅ 门禁 548 passed / 10 skipped + ruff 全绿 + `QED_TEST_MYSQL=1` 集成层 8 passed（`qed_test` 实跑新迁移升降级） |

## 验证与验收

- TDD：每项先红后绿；ingest 产物面契约（三件套 + manifest 完整性 + 数据根不变式）必须全绿；
  W4 删除逻辑单测用临时目录构造双版本场景，禁读写真实数据根。
- 回归：`pytest tests -q` + `ruff check src tests` 全量门禁。
- 消费面（S6）由根仓库经 8902 API 自验，不属本壳门禁。

## 回滚

- W1~W4 为代码+迁移改动：逐文件回退 + Alembic downgrade；停用删除逻辑即回「旧版本驻留」现状。
- 已删除的旧版本目录不可回滚——故 W4 只在 active_job_id 事务成功后执行且保留日志留痕。

## REQ-097 回执正文（2026-09-26，本地判据口径）

- **实施锚点**：`f2fd689`（W1~W4 代码批 + database-design 同步）。
- **迁移版本**：`20260926_0001`（`af_books.ingested_at` 纯加列，不回填）；head 序列
  `20260917_0001 → 20260920_0002 → 20260921_0001 → 20260926_0001`。
- **门禁读数**：`pytest tests -q` **548 passed / 10 skipped**；`ruff check src tests scripts` 全绿；
  `QED_TEST_MYSQL=1` 集成层 **8 passed**（`qed_test` 从 base 重建，经新迁移真实 MySQL DDL）。
- **S1~S5 判定**：S1 ✅（本地）；S2 ✅（本地），真机磁盘效果待 W7；S3 ✅（parsing-pipeline.md
  布局图与条文已同步）；S4 ✅（按偏差 D1 口径：复用 `sha256` 列 + 补 `ingested_at`，列清单对账守护
  通过）；S5 ✅。
- **真机递延声明**：业务库 `qed` 的 `20260926_0001` 迁移与真实数据根上的激活删旧实测属 D 类
  操作，未获窗口批准前不执行（W7 Blocked）；根仓库消费面 S6 回归归根仓库自验。
- **契约面变化提示（供根侧适配）**：① 根视图不再出现 `book.json`/`manifest.json`；② manifest
  端点在未激活书目上固定 404「尚无生效版本」；③ 页清单 `total` 只取 `af_books.page_count`
  （NULL → 404，无文件回落）；④ 解析提交门槛只看 `ingest_status`；⑤ 激活后被替换版本目录即删，
  前端不应缓存旧 `job_id` 的产物路径。

## 关闭与归档

- 关闭条件：S1~S5 全达成 + W6 回执成文；按 ADR 0006 六步收口（结论入本壳→todo 两行关闭→
  completed.md→plans/index 无需登记（索引导航制）→project-status 刷新→门禁全绿）。
- 归档判定 Retain：本壳随回执归档（根侧 REQ-097 关闭证据引用其门禁读数与锚点）。
