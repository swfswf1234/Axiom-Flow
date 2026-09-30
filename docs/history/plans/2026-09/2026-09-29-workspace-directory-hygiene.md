# 工作区目录卫生清理轮（workspace-directory-hygiene）

状态：Completed
任务类型：B
最后更新：2026-09-29
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、
[ADR 0009](../adr/0009-data-root-must-be-explicit.md)（决定 2 把仓内 `data/` 定性为「永久留档与备份区」，
本壳按 Q1 处置其定性走向）、[ADR 0008](../adr/0008-fixed-doc-form-and-metadata-genres.md)（固定文档骨架，
W6/W7 同步时受其约束）
关联设计：[overview.md](../architecture/overview.md)（数据根顶层白名单哨兵区）、
[code-map.md](../architecture/code-map.md)（`web/`、`scripts/tmp/` 两行归属）、
[parsing-pipeline.md](../design/parsing-pipeline.md)（留档与探针根口径）、
[operations.md](../guides/operations.md)（数据目录节与 runbook 脚本路径）
关联 Tracker：docs/trackers/todo.md（PLAN-021 镜像行；本任务唯一台账行）
归档判定：Retain（W3 是已执行的 D 类数据操作，前后指纹不可再生，判例同 PLAN-011）；已归档至
`docs/history/plans/2026-09/2026-09-29-workspace-directory-hygiene.md`

## 立项根据与进场裁决

用户 2026-09-29 提出四条目录卫生要求，进场询问后当日定判：

| # | 要求 | 进场裁决 |
| --- | --- | --- |
| 1 | 清理 `data/`，解析数据按规定落 `QED_DATA_ROOT` | 直接清空仓内 `data/`（含 137 MB `probes/` 留档与 3 份 `af_backup_*.sql`） |
| 2 | 临时脚本与临时数据改放仓库根 `tmp/`，在 `.gitignore` 标注 | `scripts/tmp/` 的 21 个 tracked 脚本 `git mv` 到根 `tmp/` 并停止入库 |
| 3 | 本项目不再有前端部分 | 删 `web/`（tracked 3 文件），并一并物理删除 v1 残骸 `app/` |
| 4 | `logs/` 固定不超过 3 个文件，且只写本项目日志 | 只成文口径，不改服务脚本代码 |

## 进场取证事实（W1，只读）

1. **仓内不存在正式解析产物**：`.env:8` `QED_DATA_ROOT=D:\coding\QED-Engine\dataset`，产物与 `af_*`
   回写早已落在真实数据根；ADR 0009 决定 1/2 已把「仓内 `data/` 当数据根」判为启动期拒绝，
   `test_repo_data_dir_rejected_as_data_root` 常驻守护。⇒ 要求 1 的数据面**已合规**，本壳实际动作
   是清掉留档与备份内容并封死后续写入，不是改落盘代码。
2. **`data/` 无 tracked 文件、无运行时读取者**：全仓无代码打开仓内 `data/` 文件；
   `test_repo_archive_dir_has_no_product_shape` 对目录不存在走 `pytest.skip`；
   `tests/unit/test_normalize.py:131`、`test_engines.py:436`、`src/axiom_flow/engines/mineru.py:11` 只在
   docstring/注释里以路径**记载取证来源**，不读文件。⇒ 清空不挂门禁，代价是**证据不可复查**。
3. **`scripts/tmp/` 是 tracked 且被活跃面引用**：21 个脚本入库，活跃引用面为
   `docs/guides/operations.md`（runbook 按路径给命令）、`docs/architecture/code-map.md`、
   `docs/design/parsing-pipeline.md`、`tests/smoke/test_live_mineru_chain.py:3`（docstring）、
   `docs/trackers/todo.md`（ARCH-010 长期行把 `scripts/tmp` 列为遗留项）与
   `docs/trackers/project-status.md` 若干轮次叙述；`docs/history/**` 另有 8 个归档壳引用。
4. **`web/` 活跃引用只剩一行**：`code-map.md:91` 已标「移交根仓库 QED-Engine（本仓库不再维护）」；
   `operations.md:114`、PLAN-018 壳的 `web-ui/...` 指的是**根仓库**前端目录，与本仓 `web/` 无关。
5. **`app/` 为未 tracked 的 v1 字节码残骸**：`git ls-files app` = 0，目录内只剩 `__pycache__`。
6. **`logs/` 当前 2 个文件**：`qed-axiom-serve.log`（712 KB，服务脚本 `"ab"` 追加、无轮转）与
   `qed-axiom-mode`（内容 `local`，**全仓无任何读写者**，属 2026-09-17「无 `--mode`」简化的孤儿残骸）；
   `qed-axiom.pid` 由 `start` 写、`stop` 删。
7. **并行会话未提交改动在场**：`docs/adr/index.md`、`docs/plans/2026-09-22-v1-*.md`、
   `docs/standards/task-lifecycle.md`、`docs/trackers/todo.md`、`docs/trackers/project-status.md` 为 `M`，
   未跟踪面有 `docs/adr/0010`、`docs/adr/0011`、PLAN-018/PLAN-019 壳、今日新立的 PLAN-020 壳
   （`2026-09-29-af-table-family-governance.md`，其 todo 镜像行尚未落）与
   `scripts/tmp/_plan018_adjudication_edits.py`。⇒ 本壳**不碰他人 WIP**，见 Q3。

## 目标与成功标准

**目标**：把「哪些目录可以放什么」从各处散文与习惯收成一条可守护的仓库级口径，并一次性清掉
与该口径冲突的仓内残骸。

| ID | 标准（可检验） |
| --- | --- |
| S1 | 仓内无解析产物与窗口留档：`data/` 清空或整目录移除，`test_repo_archive_dir_has_no_product_shape` 与 `test_repo_data_dir_rejected_as_data_root` 双绿；新窗口的证据与备份只落 `QED_DATA_ROOT` 白名单区或根 `tmp/`，其落点口径在治理文档成文 |
| S2 | 仓库根 `tmp/` 成为唯一临时区：`scripts/tmp/` 不再存在；21 个 tracked 脚本迁出后 `git ls-files scripts/tmp tmp` = 0；`.gitignore` 有 `/tmp/` 条目；活跃文档与代码的路径引用零残留（`docs/history/**` 与 trackers 的当时记录按 Q2 处置） |
| S3 | `web/` 与 `app/` 从仓库消失：`git ls-files web` = 0、两目录物理不存在；活跃面 `web` 引用只剩指向根仓库 `web-ui/` 者 |
| S4 | `logs/` 守则成文且当前合规：口径（≤3 个文件、只写本项目日志）写入治理落点；本批执行后 `logs/` 文件数 ≤3 且全部为本项目前缀，孤儿 `qed-axiom-mode` 的处置按 Q3 |
| S5 | 卫生断言就位且先红后绿＋变异复验：新增契约断言覆盖「`scripts/tmp` 不存在」「`tmp/` 未被 tracked」「`web/` 不存在」，撤掉断言即转红 |
| S6 | 全量门禁绿（`pytest tests -q` + `ruff check src tests`）＋六步收口完成 |

## 范围与非目标

- **范围**：`.gitignore`、`scripts/tmp → tmp` 迁移与引用改写、`web/` 与 `app/` 移除、`data/` 清空
  （D 类）、`code-map.md` 两行、`overview.md` 哨兵区相邻口径、`operations.md` 数据目录节与 runbook
  路径、`local-dev.md` 工作区目录约定、`doc-governance.md` 或新 ADR 的守则落点（Q1）、守护测试、
  todo/project-status/completed 台账收口。
- **非目标**：不改 `axiom_flow_service.py` 的日志写入与轮转逻辑（用户裁决 4「只成文口径」）；
  不动 `docs/history/**` 归档正文（历史资料只解释背景）；不动真实数据根 `QED_DATA_ROOT` 内任何
  产物与业务库 `qed`；不动他人 WIP（Q3）；不恢复 v1 前端任何能力。

## 前置条件

1. **评审准入**：本壳经用户评审、Q1~Q4 定判后方可实施（文档先行，人类审理明确「开始执行」才动码）。
   任务类型为 B（结构清理＋治理成文），其中 W3 清空仓内 `data/` 属 **D 类子动作**：
   须执行前逐步批准、前后指纹留档，不得隐式执行。
2. **并发位已核**：支线行 `In Progress` 当前 0 个（仅长期行 ARCH-010 占位，不计上限）；
   「本轮主攻」已由 **PLAN-018** 占据（你 2026-09-29 确认 ADR 0010 并定判其 Q1~Q5 ⇒ 转主攻实施位，
   PLAN-020 为其加表前置）。⇒ 本壳**不主张主攻位**，作支线行走卫生清理，随时可被主线打断。
3. **PLAN-019 关系已核**：其裁决 ① 的「旧数据与 `tmp` 残骸清理」作用域是**数据根**
   （`QED_DATA_ROOT` 内过时产物与 `.tmp-` 残骸），与本壳的**仓内**目录卫生正交，不重叠、不互相阻塞。
4. **W3 前的 D 类动作**：清空 `data/` 前须完成只读盘点留档（文件数＋字节数＋`data/backups` 三份 sql
   的 sha256），并按 Q1/Q4 用户选择是否先把备份 sql 迁出到仓库外。

## 工作项

| # | 工作项 | 状态 |
| --- | --- | --- |
| W1 | 只读盘点与事实取证（本壳「进场取证事实」7 条） | 已完成（2026-09-29） |
| W2 | 用户评审本壳并对 Q1~Q4 定判 | 已完成（2026-09-29 当日定判，见「裁决落地」） |
| W3 | D 类：清空仓内 `data/`（先盘点留档，再删；`probes/` 137 MB + `backups/` 3 份 sql） | 已完成（前后指纹见「W3 D 类前后指纹」） |
| W4 | `git rm web/`；物理删除未 tracked 的 `app/` 残骸 | 已完成（`web/` 3 文件 staged 删除、`app/`+根 `__pycache__` 物理移除） |
| W5 | `scripts/tmp` → 根 `tmp/`：21 个 tracked 脚本 `git mv`（移动后天然不入库）、`.gitignore` 加 `/tmp/`、活跃引用改指 | 已完成（20 个 `.py` + `smoke-api.sh`；`_plan018_adjudication_edits.py` 由并行会话先行删除） |
| W6 | 文档同步：`code-map.md` 的 `web/`、`scripts/tmp/` 两行；`operations.md` 数据目录节与 runbook 路径；`local-dev.md` 工作区目录约定（`tmp/`、`logs/`）；`overview.md` 留档口径；按 Q1 落 ADR 或改 ADR 0009 | 已完成（ADR 0012 新立 + ADR 0009/`overview.md`/`code-map.md`/`operations.md`/`local-dev.md`/`AGENTS.md` 六处同步） |
| W7 | 守护断言：先取真红灯再实现（S5），并对每条做变异转红复验 | 已完成（首跑真红＝`scripts/tmp` 空目录在场；变异 M1~M5 全部转红并还原转绿） |
| W8 | 全量门禁 + 六步收口（结论入本壳 → todo 行压缩 → completed.md → project-status 刷新 → 门禁） | 已完成（2026-09-29，执行记录见「关闭与归档」） |

## 验证与验收

- 分层判据：S1/S2/S3 用 `git ls-files` 与目录存在性断言机械可判；S4 用 `logs/` 清单读数成文；
  S5 用变异转红自证守护有效（判例＝PLAN-014/PLAN-016 的变异复验法）。
- 门禁：`pytest tests -q` + `ruff check src tests`（本轮不改 `src` 行为，用例数应持平；新增断言为增量）。
- 引用零残留判据：`rg -n "scripts[/\\\\]tmp" --glob '!docs/history/**'` 在非归档面命中为 0
  （trackers 当时记录按 Q2 处置后判定）。
- S1~S6 逐条判定成文于本壳后方为达成；本轮无对外回执（不涉跨项目请求）。

## 回滚

- W4/W5（git 动作）：`git revert` 即完整回滚，被移动与删除的文件按原路径复现。
- W3（清空 `data/`）：**不可逆**，无任何 git 锚点可恢复。唯一补救是 W3 前先把 `data/backups/`
  的 3 份 `af_backup_*.sql`（R2 `20260921_0001` 与 0928 `20260926_0001` 窗口的回滚锚点）迁到仓库外
  保留，见 Q4。业务库 `qed` 与真实数据根本壳零接触，不需要额外回滚点。
- 并行会话工作树：本壳只 stage 自己的文件，绝不 `git add -A`、绝不代替他人提交。

## 待裁项（四条，评审输入原文；定判结果见「裁决落地」表）

| ID | 问题 | 备选项与建议 |
| --- | --- | --- |
| Q1 | 仓内 `data/` 清空后，**未来真机窗口的证据与 D 类备份落在哪里**？ADR 0009 决定 2 现写明仓内 `data/` 是「永久留档与备份区」，直接清空与该定性冲突 | (A) 新增 ADR 0012 统一立「工作区目录卫生」守则（仓内留档区退役 → 窗口证据与备份落 `QED_DATA_ROOT/backups/`（根侧 ADR 0018 白名单内合法区）或根 `tmp/`；根 `tmp/` 为唯一临时区；`logs/` ≤3 且只写本项目日志），并修订 ADR 0009 决定 2 —— **建议 (A)**；(B) 只清内容、ADR 0009 定性不动（口径继续允许往仓内 `data/` 写，日后必然复积）；(C) 不出 ADR，只在 `doc-governance.md`/`local-dev.md` 成文 |
| Q2 | `docs/history/**` 与 trackers 的当时记录里的 `scripts/tmp/...` 路径要不要改指新位置 | (A) 一律不改写（历史资料不覆盖当前设计，且 `project-status.md` 的轮次叙述是当时的真实观测记录，重写等于篡改证据）—— **建议 (A)**；(B) 全量改写为新路径（会污染历史记录，且归档面 8 个壳需连带改） |
| Q3 | 孤儿 `logs/qed-axiom-mode`（内容 `local`，全仓无读写者，属 09-17「无 `--mode`」简化的残骸）与 `scripts/tmp/_plan018_adjudication_edits.py`（他人未提交 WIP）如何处置 | mode 文件建议**直接删除**；他人 WIP 建议**原地保留、不动**，`scripts/tmp/` 的「不存在」断言待 PLAN-018 收口后由后续轮补一条（或按用户授权现在就移入 `tmp/`） |
| Q4 | 3 份 `af_backup_*.sql`（合计约 460 KB）是业务库回滚锚点，删后无法把 `qed` 还原到 R2/0928 时点；137 MB 窗口证据删后不可复查。确认全部删除，还是先迁出仓库外？ | (A) 照用户已给的「直接清空」全删（现状业务库已在 `20260926_0001`，回滚需求理论上已过期）；(B) 备份 sql 迁到 `QED_DATA_ROOT/backups/`、`probes/` 全删 —— **建议 (B)**（成本 460 KB，保住回滚锚点）；(C) 两者都先迁出再清空 |

## 执行登记（2026-09-29，W2~W7）

### 裁决落地

| ID | 用户裁决（2026-09-29） | 落地动作 |
| --- | --- | --- |
| Q1 | 「真机窗口的证据和备份放 `tmp/` 因为不重要，将『ADR 0009 决定 2』删除」⇒ 比备选项 (A) 更激进 | 新增 [ADR 0012](../adr/0012-workspace-directory-hygiene.md) 统一立「工作区目录卫生」守则（根 `tmp/`＝唯一临时与留档区）；ADR 0009 决定 2 的「永久留档与备份区」定性按**取代**处理——0009 正文就地注记 + 取代节登记，决定 1/3/4/5 与「仓内目录不得当数据根」的代码守卫**继续有效** |
| Q2 | 「需要改位置，改到 `tmp/` 目录下」⇒ 取 (B)，与壳内建议 (A) 相反 | 全仓字面改写 157 处/26 文件（含 `docs/history/**` 与 trackers 当时记录）：`scripts/tmp/…` ⇒ `tmp/…`、`data/probes/…` ⇒ `tmp/probes/…`；代价见「语义损失注记」 |
| Q3 | **未答**（本批不裁） | `logs/qed-axiom-mode` **原地保留**，未获裁不删；`scripts/tmp/_plan018_adjudication_edits.py` 已由并行会话自行删除，留下的空目录随本批 `rmdir` 移除 ⇒「`scripts/tmp/` 不存在」断言本批即落地，无需后续轮补 |
| Q4 | 「可以直接删，除非我要求不然不用备份」⇒ 取 (A) | `data/` 整目录真删，3 份 `af_backup_*.sql` 不迁出，业务库历史时点回滚锚点按裁决**放弃** |

### W3 D 类前后指纹（不可再生读数）

- **前**：`data/` 共 2468 个文件 / 488,642,257 字节（466.0 MiB）/ 61 个目录；其中 `probes/`
  2465 文件、488,177,294 字节，`backups/` 3 文件、464,963 字节。顶层无产物形状
  （`book.json`/`pages/`/`versions/` 零命中，ADR 0009 决定 2 旧口径下本已合规）。
- **备份 sql sha256 前缀**（只记录、不入仓）：`af_backup_20260921.sql` `7f9248f8886680e4…`
  （22,994 B）、`window-20260922/af_backup_20260922.sql` `699e7ca83d5f292d…`（184,371 B）、
  `window-20260928/af_backup_20260928.sql` `af36f7a32bdd0dc3…`（257,598 B）。
- **后**：`data/` 目录不存在（`rm -rf`，无 git 锚点、无副本，**不可逆**）；`.gitignore` 的
  `data/` 条目保留，防止后续误建再入库。业务库 `qed` 与真实数据根 `D:\coding\QED-Engine\dataset`
  本壳零接触。

### S1~S6 逐条判定

| ID | 判定 | 证据 |
| --- | --- | --- |
| S1 | 达成 | 仓内 `data/` 整目录移除，正式产物本就落在 `QED_DATA_ROOT`（`.env:8`）；`test_repo_data_dir_rejected_as_data_root` 绿，原「留档区形状」断言按 ADR 0012 决定 2 改判为「仓内不得存在 `data/`」；新窗口落点口径成文于 ADR 0012 决定 1/2、`operations.md` 数据目录表与 `overview.md`「仓库工作区目录卫生」 |
| S2 | 达成 | `scripts/tmp/` 不存在；21 个脚本迁出后 `git ls-files -- tmp scripts/tmp` = 空；`.gitignore` 含 `/tmp/` 与 `/tmp-*/`；活跃面引用零残留——剩余字面命中只在 ADR 0012/0009、本壳与守护常量中，以「已退役／禁止」语境点名。迁后脚本 `parents[2]`⇒`parents[1]` 逐只改并 `py_compile` 全绿；`tests/unit/test_wholebook_probe_holder.py:17` 的 `SCRIPT_PATH` 同步改指（本批唯一被门禁发现的漏改） |
| S3 | 达成 | `git ls-files -- web` = 空、`web/` 与 `app/` 均不存在；活跃面 `web` 引用只剩根仓库 `web-ui/`（`code-map.md` 的移交行同步） |
| S4 | 达成（附注） | 口径落 ADR 0012 决定 4、`operations.md` 守则条、`code-map.md` `logs/` 行、`local-dev.md` 注记与 `AGENTS.md` 工作区边界节。实测 `logs/`＝3 个文件（`qed-axiom-serve.log`、`qed-axiom.pid`、`qed-axiom-mode`）⇒ 上限合规。**前缀口径按实测收窄**：原写 `qed-axiom-*` 会把服务脚本固定的 `qed-axiom.pid` 判为违规，而改文件名属代码改动（用户裁决 4「只成文口径」），故口径改为前缀 `qed-axiom`，守护常量 `LOG_PREFIX` 同步 |
| S5 | 达成 | 先红后绿：断言首次运行时 `scripts/tmp/` 空目录在场，`test_retired_repo_dirs_absent` 取到**真红灯**，`rmdir` 后转绿；变异复验 M1~M5 逐条转红并还原转绿（下表） |
| S6 | 达成 | 全量门禁 `558 passed, 10 skipped` + `ruff check src tests` 全绿（本壳净增 3 例：形状断言 1 删、卫生断言 4 加）。六步收口执行记录见「关闭与归档」 |

**收口后终检补漏（同日）**：以正则扫活跃治理面（`standards/`、`architecture/`、`design/`、
`guides/`、`adr/`、`src`、`tests`、`scripts`）找旧定性残留，抓出两处仍在把仓内 `data/` 称作
「留档区」的口径并改指 ADR 0012 决定 2 ——`design/parsing-pipeline.md` `PP-5` 的理由列与
`tests/unit/test_config.py::test_repo_data_dir_is_rejected` 的 docstring。其余命中均属合法点名
（ADR 0009 被取代正文与其「取代」节、ADR 0012 自身、守护常量与断言消息、`code-map.md` 的
`web/` 退役映射行）。改后门禁复跑同读数。

### 变异复验（W7，S5 自证）

| # | 变异 | 结果 |
| --- | --- | --- |
| M1 | 现场重建 `web/` 目录 | `test_retired_repo_dirs_absent` 转红；`rmdir` 后转绿 |
| M2 | `.gitignore` 的 `/tmp/` 改 `/tmp-guard-mutation/`（先做字节备份，结束逐字节还原并校验一致） | `test_scratch_area_ignored_and_untracked` 转红；还原转绿 |
| M3 | 入库面扫描目标改指 `scripts/`（确有 tracked 内容） | 同测试的 tracked 断言转红：`('scripts/axiom_flow_service.py',)` |
| M4 | `RETIRED_DIRS` 改指 `("scripts",)` | `test_retired_dirs_have_no_tracked_content` 转红 |
| M5 | `LOG_MAX_FILES` 改 2；`LOG_PREFIX` 改 `qed-engine` | `test_repo_logs_bounded_and_project_only` 分别转红（超上限／混入外部前缀） |

驱动与取证脚本＝`tmp/_plan021_guard_mutations.py`、`tmp/_plan021_{guard,prefix,entry,close_shell,
ledger_close}.py`（按 ADR 0012 决定 1 落根 `tmp/`，不入库、可随手清理）。

**收口时点的并发读数**：`scripts/tmp/` 空目录在 W7 转绿后被重新创建（16:34，同工作树并行会话的一次性
脚本习惯），本批再次 `rmdir` 后门禁转绿。该目录**不承载任何内容**（`rmdir` 对非空目录必失败，故无数据
风险），但并行会话若继续往 `scripts/tmp/` 写就会与本批守护冲突 ⇒ 口径已落 ADR 0012 决定 3、`AGENTS.md`
工作区边界节与 `overview.md`，后续窗口应改落仓库根 `tmp/`；本壳不代替其会话清理。

### 语义损失注记（Q2 与 Q4 的代价）

- 历史面与台账当时记录里的 `scripts/tmp/…`、`data/probes/…` 已按裁决改指 `tmp/…`、
  `tmp/probes/…`，但**新路径下并无对应内容**（探针留档随 W3 真删；根 `tmp/` 只剩 21 个 untracked
  脚本）。改写后的路径只表示「当时的落点」，不再能解析到证据。
- ADR 0009 决定 2 原承诺的「永久留档与备份区」由此失效：历次真机窗口的原始工件（含
  `probes/window-20260922/C8_chunk_prefix_b11.json` 一类判据边界证据、`backups/` 三份 sql 的
  历史时点回滚能力）不可复查、不可恢复；台账中的事件叙述与读数文本仍在，工件已消失。
  前后指纹与 sha256 前缀只在本节留档。

## 关闭与归档

- 关闭条件：S1~S6 逐条判定达成 + 门禁全绿 + W3 的 D 类前后指纹留档成文。
- 六步收口（ADR 0006）：结论写入本壳 → todo 行压缩并关闭 → completed.md 迁移 → `plans/index.md`
  登记 Retain 去处 → `project-status.md` 刷新（含主攻位释放）→ 全量门禁。
- 归档判定 Retain：W3 属已执行的 D 类数据操作（不可再生读数）；运行时事实源不在壳内，而以
  Q1 落地的 ADR/治理文档、`code-map.md`、`operations.md`、`local-dev.md` 与守护测试为准。

### 收口执行记录（2026-09-29）

1. **结论入本壳**：「执行登记」节（裁决落地、W3 前后指纹、S1~S6 判定、变异复验 M1~M5、语义损失
   注记）即结论正文；本壳按 Retain 迁 `docs/history/plans/2026-09/`，ADR 0012「实施载体」链接
   同批改指归档路径。
2. **todo 行压缩并关闭**：PLAN-021 镜像行整行移除；v1.0 主线「推进顺序」句补记本批收口；
   ARCH-010 长期行的遗留项 `scripts/tmp` 同步改判（已由 ADR 0012 决定 3 退役，不再作遗留）。
3. **completed.md 迁移**：新增 PLAN-021 行，证据列承载 S1~S6 判定摘要、D 类前后指纹与门禁读数。
4. **`plans/index.md` 登记**：Retain 条目入「关闭登记」，写明归档路径与运行时事实源清单。
5. **`project-status.md` 刷新**：新增「已关闭（2026-09-29）＝PLAN-021」条目；主攻位未受影响
   （本壳全程作支线，未主张主攻，主攻仍为 PLAN-018）。
6. **全量门禁**：`558 passed / 10 skipped` + `ruff check src tests` 全绿（收口批复跑同读数）。

**遗留（不在本壳关闭条件内）**：Q3 未裁 ⇒ 孤儿 `logs/qed-axiom-mode` 原地保留；`logs/` 现为
3 个文件＝上限值，后续新增须由人工按 ADR 0012 决定 4 删除最旧者（无代码轮转）。

## 收口后的规范梳理（2026-09-29，提交前补漏）

收口后按用户要求把规范面（`README.md`、`AGENTS.md`、`pyproject.toml`、`.gitignore`、`docs/standards/`、
`docs/guides/`、`docs/architecture/`、`docs/design/`、`docs/adr/`）逐条对照 ADR 0009/0004/0012 与代码实况
扫一遍（驱动＝`tmp/_spec_audit.py`，命中 125 行读数入 `tmp/spec_audit.txt`；按 ADR 0012 决定 1 不入库）。
判据：活动文档不得指示已退役落点，不得与 Accepted ADR 的口径相斥。

### 发现与处置

| 位置 | 问题 | 处置 |
| --- | --- | --- |
| `README.md`「核心能力／技术栈／能力边界／典型流程／仓库结构」 | 整段仍是 v1 口径：产物写 `data/books/<book_id>/`＋`state.sqlite`、`scripts/`＝「WSL 容器启停脚本与 compose 配置」、`data/`＝「按需创建的本地解析产物」、MinerU「经 8900 网关调用」、qwen-vl-plus「兜底通道」、部署＝Docker Compose 启停 | 15 处按实况改写：产物落 `<QED_DATA_ROOT>/parsed/<domain>/<course>/<book>/` 并链接 ADR 0009／0012；`scripts/` 只写 8902 唯一启停入口；「仓库结构」表把 `data/` 行换成仓库根 `tmp/`（唯一临时与留档区，不入库）与 `logs/`（≤3、`qed-axiom*`）；解析通道改「引擎适配器直连」（overview「解析直连、无自动兜底」＋ ADR 0004）；兜底行改「PaddleOCR-VL / qwen-vl 预留，未实现」；持久化行按 overview 兼容基线写 MySQL 运行库＋Alembic 双栈 DDL |
| `docs/adr/0009-data-root-must-be-explicit.md`「取代」节 | 插入取代句后残留重复的「被取代：无。」，与本 ADR 已被部分取代的事实自斥；正文其余处仍写 `data/probes/…`、`data/backups/…` | 删重复行，取代句补一条读法：正文的 `data/probes/…`、`data/backups/…` 随 ADR 0012 决定 6 一律读作 `tmp/probes/…`、`tmp/backups/…`，「仓内路径不得当数据根」及守卫不变（ADR 正文按治理只做取代注记，不重写当时裁决） |
| `.gitignore`「# Runtime」段 | `scripts/.keepalive.pid` 全仓无读写者（keepalive 归根仓库 `scripts/image-model/`），属退役容器脚本的死条目 | 连同空段注释整条删除（`scripts/` 现只剩 `axiom_flow_service.py`，与该模式再无对应写入方） |

### 判定为无需改动的项

- `scripts/image-model/`、`经 8900` 一类出现在 overview/api/local-dev/development/AGENTS 的引用全部指**根仓库**
  路径与 8900 的模型生命周期职责，与 ADR 0004 一致，保留。
- `docs/adr/0009` 背景节与 `docs/history/**`、台账当时记录中的 `data/probes/…` 是「当时的落点」叙述，
  已由 ADR 0012 决定 6 的取代注记覆盖，不再逐字重写。
- `task-lifecycle.md` 的「D 类＝计划＋备份＋回滚＋差异复核」是通用治理，不与 ADR 0012 决定 1 冲突：
  仓内不存备份，长命回滚锚点按 ADR 0009 决定 3 落 `<QED_DATA_ROOT>/backups/`（overview 白名单／operations 核对步已写明）。
- `.gitignore` 保留 `data/`、`logs/` 两条：与「仓内不得复现 `data/`」「`logs/` 不入库」同向，属防御性排除。
- 入口文档链路（`docs/index.md`、`standards/index.md`、`doc-governance.md`、`testing.md`、
  `cross-project-collaboration.md`）扫描无退役落点命中。

### 读数

- 梳理后全仓 `rg` 复核：活动治理文档中除 ADR 0012 的禁止性表述与本壳/completed.md 的历史叙述外，
  无 `scripts/tmp`、`data/probes`、`data/backups`、`web/`、`app/` 的**指示性**引用。
- 本批属 `README.md`/ADR 取代注记/`.gitignore` 的文档一致性修正，零 `src` 行为改动、未新增用例，
  长期归属仍在 ARCH-010「文档体系对齐维护」行下。
- 补漏后门禁复跑：`558 passed / 10 skipped` + `ruff check src tests` 全绿，与收口批同读数。
