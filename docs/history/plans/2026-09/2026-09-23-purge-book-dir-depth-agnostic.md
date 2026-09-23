# 清除端点书目录枚举改深度无关（ARCH-026）

状态：Completed
任务类型：B
最后更新：2026-09-23
关联 ADR：[ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md)（本计划承载台账事实源）
关联设计：[parsing-management.md](../../../design/parsing-management.md)、[api.md](../../../architecture/api.md)
关联 Tracker：docs/trackers/todo.md（PLAN-011、ARCH-026）
归档判定：Delete（条文并入 `api.md`/`code-map.md`/`operations.md`，红灯用例进默认门禁；无不可替代证据）

> **编号说明**：本行取 `ARCH-026` 而非 `ARCH-025`——`ARCH-025` 已被根仓库 v1.0 优化轮占用
> （见 todo v1.0 主线节标题），同号会造成跨项目引用歧义；本仓库号位跳号不回收（ADR 0006）。

> 本计划是 [PLAN-010](./2026-09-23-admin-purge-endpoint.md)（已关闭）交付的
> `POST /api/v1/admin/purge` 在**业务侧首次 dry-run** 时暴露的缺陷收口，不扩大端点范围。

## 目标与成功标准

**目标**：书目录的识别不再依赖「`parsed/<domain>/<course>/<book>` 恒为三层」这一假设。

**缺陷事实（2026-09-23 业务侧 dry-run 留档 `data/probes/purge-business-20260923/dry_run.json`）**：
`af_books` 17 本的 `domain_id` 全为空串，`book_dir_for` 拼接时**空段被 pathlib 折叠**，真实书目录只有
两层（`parsed/linear_algebra/linearalgebra-b01/`）。原枚举用固定三层 glob（`*/*/*`、`*/*/{book_id}`），
于是把 `pages/` 与 `versions/` 当成「书目录」：`targets` 回出 8 条子目录、`directories=8`，真删会留下
`book.json`/`manifest.json` 与空书目录，第二次清除的清单不为空 ⇒ PLAN-010 成功标准 6「重跑幂等」在
真实布局下不成立。

| # | 成功标准 | 判据 |
| --- | --- | --- |
| S1 | 空 `domain_id`（两层）书目录被当成**一个**目录整体清除 | 默认门禁 `test_empty_domain_book_dir_is_purged_whole`：`targets == ["parsed/<course>/<book>"]`、真删后目录不存在 |
| S2 | 三层布局行为不变（不回归 PLAN-010 已交付判据） | W6 演练 K1~K9 全 PASS + 契约 14 例全绿 |
| S3 | 幂等重跑在两种布局下都成立 | 同一用例二次清除 `targets == []`、`directories == 0` |
| S4 | 版本目录不被误认成书目录 | 识别只看产物标记且命中即剪枝；S1 用例断言 `targets` 恰一条 |
| S5 | 条文与运维口径同步（枚举规则可读、可复核） | `api.md` 不变量 + `code-map.md` + `operations.md` runbook 同步 |
| S6 | 业务侧真删在修复后完整收口 | 清除后四表归零、`parsed/` 无书目录残留（含 `book.json`），书目经 `/books/sync` 回灌 |

## 范围与非目标

**范围**：`api/admin.py` 的书目录枚举（`_all_book_dirs` / `_book_dirs_for_book`）、对应契约测试、
`api.md`/`code-map.md`/`operations.md` 的枚举口径。

**非目标**：不改双确认与 token 守卫、不改作用域校验次序（`confirm` → 作用域 → 在飞 → 删除）、
不改删除顺序与失败语义、不动 `af_*` 表结构与仓储、不引入「按 DB 行推导目录」的新作用域语义
（磁盘孤儿目录仍按标记识别）。

## 前置条件

1. PLAN-010 已关闭（端点已具备真机演练证据），本轮只补真实布局的枚举缺陷。
2. 业务侧真删需用户逐次批准（D 类）；本计划的代码与文档改动不需要真机。

## 工作项

| # | 工作项 | 状态 |
| --- | --- | --- |
| W1 | 红灯：`test_empty_domain_book_dir_is_purged_whole`（S1/S3/S4，业务侧症状在门禁内复现） | Done（2026-09-23）：失败信息 `['…/pages','…/versions'] != ['parsed/math_analysis/mathanalysis-b09']` |
| W2 | 枚举改为**产物标记识别 + 命中剪枝 + 深度上限 3**（`_is_book_dir` / `_iter_book_dirs`） | Done（2026-09-23）：契约 14 例全绿，默认门禁 479 passed / 10 skipped，ruff clean |
| W3 | 无回归自证：W6 演练（`qed_test` + 探针根）K1~K9 重跑 | Done（2026-09-23）：`all_pass=True`、业务零写入 `True`、退出码 0 |
| W4 | 条文同步：`api.md` 磁盘作用域不变量补「层数由 `domain_id`/`course_id` 是否为空而变，识别只看产物标记」；`code-map.md` 模块行同步；`operations.md` runbook 第 2 步补 **targets 形状核对** | Done（2026-09-23）：三处文档同步，契约/文档治理测试全绿 |
| W5 | 业务侧执行（D 类，逐步批准）：preflight → 四表 JSON 备份 → 全项目真删 → 复核 → `POST /api/v1/books/sync` 回灌 17 本 | **Done（2026-09-23）**：修复后 dry-run `targets` 恰为 4 条书目录（修复前误报 8 条子目录）；真删 200、`deleted`={17,24,1015,2,4}、`failures=[]`、四表归零、`parsed/` 文件数 **7379 ⇒ 0**（3.90 GB 无残留）；回灌 200、`af_books=17` 且与根库 `qt_books` 的 17 本 verified 逐本对齐 |
| W6 | 收口：六步（承载结论 → todo 压缩 → completed.md → plans/index 登记 → project-status 刷新 → 门禁全绿） | Done（2026-09-23）：门禁 479 passed / 10 skipped、ruff clean、`QED_TEST_MYSQL=1 tests/integration` 8 passed |

## 验证与验收

```powershell
$env:PYTHONIOENCODING = "utf-8"
& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests -q
& D:\software\anaconda3\envs\QED_env\python.exe -m ruff check src tests scripts
& D:\software\anaconda3\envs\QED_env\python.exe scripts/tmp/live_admin_purge_plan010.py   # qed_test 探针根
```

业务侧（W5）判据：`purge_result.json` 的 `ok=true` 且 `targets` 与 `dry_run.json` 逐条相同、
`rows_after` 四表全 0、`disk_after.book_dirs == []` 且 `parsed/` 下无残留文件；`resync_result.json`
的 `af_books == 17`。留档目录：`data/probes/purge-business-20260923/`。

## 回滚

代码回滚即 `git checkout -- src/axiom_flow/api/admin.py tests/contract/test_api_v1_contract.py`
（红灯用例保留会让枚举缺陷重新暴露，不回滚测试）。文档回滚同轮撤销。W5 是**不可回滚的数据操作**：
四表行可从 `backup_af_tables.json` 逐行回灌，磁盘产物**不可恢复**（用户裁决 2026-09-23：不备份磁盘，
直接真删；恢复路径＝重新 ingest + 重新解析，依赖根侧图像模型服务）。

## 关闭判定（S1~S6，2026-09-23）

| # | 判据 | 判定 |
| --- | --- | --- |
| S1 | 两层（空 `domain_id`）书目录整体识别与清除 | **达成**：`test_empty_domain_book_dir_is_purged_whole` 入默认门禁；业务侧 dry-run `targets` 由 8 条子目录变为 4 条书目录 |
| S2 | 三层布局无回归 | **达成**：`TestAdminPurgeEndpoint` 14 例全绿；W6 演练 K1~K9 重跑 `all_pass=True`、`业务零写入=True`、退出码 0 |
| S3 | 两种布局重跑幂等 | **达成**：用例二次清除 `targets=[]`、`directories=0`；真机复核 `disk_after.file_count=0`、`book_dirs=[]` |
| S4 | 版本目录不被误认 | **达成**：`_is_book_dir` 命中即剪枝（`walk` 只在未命中时下钻，上限 3 层），S1 用例断言 `targets` 恰一条 |
| S5 | 条文与运维口径同步 | **达成**：`api.md` 磁盘作用域段新增识别规则与判例、`code-map.md` 模块行、`operations.md` runbook 第 2 步「核对 targets 层数形状」 |
| S6 | 业务侧真删完整收口 | **达成**：`purge_result.json` `ok=true`（四表 17/24/1015/2 + 4 目录、`failures=[]`、四表归零、`parsed/` 零文件）；`resync_result.json` 17 本回灌，`ingest_status=none`、`active_job_id` 全 NULL |

**连带事实（写进台账，不是本计划的范围扩张）**：三本生效版本产物（b01 `86ab56ae2b6c`、b05
`185a37d55985`、b11 `bbe0d401ce83`）与 2 条 `af_block_edits` 人工校验行随清除消失 ⇒
**ARCH-005/006 的 8903 联调前置回到「先 ingest + 再解析建产物」**，且解析依赖根侧图像模型服务
（用户裁决 2026-09-22：环境侧归 QED-Engine 图像模型维护线）。恢复路径唯一、不需要本仓库改动。

**未包含**：任何提交与推送；根仓库文件与数据库的任何写入（`qt_books` 只读比对）；重新 ingest/解析。

## 关闭与归档

S1~S6 逐条判定后关闭；`归档判定：Delete` 成立（条文与运维口径已在固定文档安家，红灯用例入默认门禁，
真机证据在 `data/probes/` 与 `completed.md`）。关闭时同步 todo 镜像与 `plans/index.md`。
