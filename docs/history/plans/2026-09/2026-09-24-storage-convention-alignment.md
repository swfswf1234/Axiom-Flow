# 存储规范对齐实施（storage-convention-alignment）

状态：Completed
任务类型：B
最后更新：2026-09-26
关联 ADR：[ADR 0009](../../adr/0009-data-root-must-be-explicit.md)（本壳 Q4 裁决升格：数据根必须显式配置、仓内 `data/` 去留档区双身份、写入面锁 `parsed/`）；
入站来源＝根仓库 ADR 0018「数据根顶层白名单与元数据 JSON 岛禁止」（`docs/adr/0018-data-root-whitelist-and-meta-json-ban.md`，跨仓库只读引用、不建相对链接），
治理沿用 [ADR 0004](../../adr/0004-model-boundary-and-pipeline-ownership.md)（无静默兜底）与
[ADR 0006](../../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）
关联设计：[parsing-pipeline.md](../../design/parsing-pipeline.md)（PP-3/PP-4/PP-5/PP-6）、
[overview.md](../../architecture/overview.md)（数据边界与产物布局）、
[api.md](../../architecture/api.md)（书目枚举）、[database-design.md](../../architecture/database-design.md)
（`active_job_id` 指针选版）、[operations.md](../../guides/operations.md)（数据目录表与 D 类 runbook）
关联 Tracker：docs/trackers/todo.md（PLAN-016 镜像行；承载 ARCH-034；与 REQ-095 确认包同批不同判——
REQ-095 只出书面判定与回执，本壳是**本仓侧实施**）
归档判定：Retain（C3 清理已获批准，执行清单与前后指纹属不可再生的数据操作留档；ADR 0009 同批落地）
需求方：QED-Engine 根仓库（ARCH-032 存储链路规范化轮，其 ADR 0018 把落盘行为的守护**明确下放给子项目**）
执行方：Axiom-Flow

## 背景

根仓库 ADR 0018 把数据根治理成文为四条决定：① 顶层白名单登记制（`raw/`、`tmp/`、`parsed/`、
`backups/`）；② 元数据 JSON 岛禁止（例外①`parsed/**` 产物内部文件、例外②`raw/**` 知识正本）；
③ `tmp/` 语义收口（解析中间产物不入 tmp，本仓瞬态为「产物目录同目录隐藏临时文件 + ingest 暂存
子目录，用完即清」）；④ 删除虚构约定 `tmp/axiom-flow/<job-id>/`。该 ADR 的「执行与门禁」节明写
**「数据根落盘行为由子项目各自契约测试守护」**——即根侧立规则，本仓负责自证。

本轮把这条委派落地：先给出**逐条对账的偏差清单**（C1~C7，全部带文件与行号证据），再给工作项。
下列判定取证于 `release` 工作树与真实数据根 `.env`（`QED_DATA_ROOT=D:\coding\QED-Engine\dataset`）。

## 现状对账（规则 → 本仓实况 → 判定）

| # | 根侧规则 | 本仓实况（证据） | 判定 |
| --- | --- | --- | --- |
| C1 | 顶层白名单登记制，白名单外即野生区 | 本仓文档只画两区：`overview.md:111` 的 `<QED_DATA_ROOT>/` 树只有 `raw/` 与 `parsed/`，`operations.md:48-55` 逐条列目录但**没有「顶层只允许什么」的边界规则**；无任何契约守护声明白名单（唯一近似断言散在 `tests/unit/test_orchestrator.py:260`，只覆盖 `raw`+`parsed` 且不叫这个名字） | **缺声明缺守护**（写入行为本身合规） |
| C2 | 数据根唯一（`QED_DATA_ROOT`）；不把临时文件写入仓库工作区或数据根 | `config.py:47-49` 无 `QED_DATA_ROOT` 时**静默回退仓内 `data/`**（`config.py:74` 为该默认值），而 `operations.md:55` 明写 `data/`「**不作数据根候选**」；`tests/unit/test_config.py:73,190,196` 三处把该回退钉成契约 | **文档与代码互相打脸**，且回退目标正是野生区最密集处（见 C3） |
| C3 | 白名单外目录发现即登记处置 | 仓内 `data/` 顶层实测：`books/`（v1 扁平布局残骸 `01-rudin-trial/`，**399 文件 / 1.2 GB**，`src`/`tests`/`scripts` 零引用，只在 `docs/history/design/*` 出现）、`probes/`（留档，含 542 个 JSON）、`backups/`、`api-8902.log`、`api-8902.err.log`（旧 runbook 落点，现服务写 `logs/`：`scripts/axiom_flow_service.py:36`）；`data/` 整目录 gitignore（`git ls-files data` = 0） | **三处野生**（`books/` 与两个 `.log` 为纯残骸；`probes/`/`backups/` 为有意留档） |
| C4 | `backups/` 命名 `YYYY-MM-DD-<任务标识>` 且台账登记 | 本仓既有 D 类备份落**仓内** `tmp/backups/window-20260922/`（`operations.md:175`），命名式相反（任务在前、日期在后），且本仓无「备份目录命名」守则成文 | **命名式不合**（留痕与台账登记本身合规，PLAN-011 有判例） |
| C5 | 元数据 JSON 岛禁止，例外①＝`parsed/**` 产物内部文件 | 本仓写入只在 `parsed/`（`artifacts.py:52-69` 两个路径构造器一律前缀 `parsed/`；`admin.py:51-52` 清除作用域限 `parsed/`），产物内 `book.json`/`manifest.json`/`blocks.json`/`chunks.json` 全属例外①；仓内 `tmp/probes/**` 的 JSON 是**证据留档**不在数据根内；`tmp/probes/<窗口>/root/parsed/**` 是显式声明的**探针数据根**（其内产物形状正当，守护必须放行） | **合规**，但「例外①清单」与「留档区不算岛」从未在本仓文档成文 ⇒ 日后易被误判或被新代码破坏 |
| C6 | 解析中间产物不入 tmp；瞬态「用完即清」 | 同目录隐藏瞬态：`artifacts.py:148-167`（`.name.tmp-<hex>`，`finally` 清理✓）、`importer.py:106`（兄弟隐藏暂存目录，`finally` rmtree✓）、`importer.py:66-76`（`.book.old-<hex>` 交换垃圾——异常回滚分支只在 `not old_pages.exists()` 时换回，**可能留残骸**；进程被 kill 时三者都留残骸）；`manifest`/读取端按点开头文件天然排除（`artifacts.py:109,194`） | **正常路径合规，崩溃/失败路径无「清」的责任人与识别式** |
| C7 | 目录骨架可枚举、盘↔库可对账 | 书目回退枚举仍用固定三层：`api/main.py:231` `data_root.glob("parsed/*/*/*/book.json")`、`api/main.py:183` `parsed/*/*/{book_id}`——PLAN-011/ARCH-026 已证明 `domain_id` 为空串时真实布局只有两层，`admin.py:70` 已改深度无关而此处未改；`_books_from_filesystem` 是活路径（`main.py:263,271`） | **同一缺陷类在读取侧未收口**（当前 `parsed/` 零文件所以未暴露） |

## 目标与成功标准

**目标**：让「我们往哪里写、能写什么」成为**文档声明 + 代码闸门 + 契约守护**三件齐备的约束，
而不是靠约定与运气。

- **S1**：顶层白名单在本仓成文为一条可断言规则（`overview.md` 数据边界节 + 哨兵区四条目录），
  并有契约守护同时核对：哨兵区存在、虚构约定 `tmp/axiom-flow` 在全部治理文档命中为 0。
- **S2**：数据根不再存在「静默落到仓内 `data/`」这条路径——未显式配置时的行为由你在 **Q1** 裁定，
  裁定后代码、文档、`test_config.py` 三处一致。
- **S3**：本仓写入面被守护钉死在 `parsed/` 之内：任何向数据根非 `parsed/` 子树的写路径（含 `raw/`、
  `tmp/`、白名单外顶层）在默认门禁里转红。
- **S4**：C7 的书目枚举改深度无关，两层与三层布局下均命中书目录本身，补两形态回归网。
- **S5**：C3 清理清单经你批准后执行，留下前后指纹（哪些路径消失、`data/` 顶层终态）。
- **S6**：C4/C6 的本仓口径成文（备份目录命名式、瞬态残留的识别式与责任人），并有对应守护或
  runbook 核对步骤。
- **S7**：全量门禁绿（`pytest tests -q` + `ruff check src tests scripts`），并把 C1~C7 判定回写
  REQ-095 回执正文（PLAN-015 的 A2/A3/A4 项）。

## 范围与非目标

**范围**：`config.py`（数据根解析）、`ingest/`（瞬态与路径守卫，只加判定不改语义）、
`api/main.py` 书目枚举、`api/admin.py` 作用域守卫（如需并入统一判定）、`tests/contract/`（新增
存储治理守护）、`docs/architecture/overview.md`+`api.md`+`code-map.md`、`docs/design/parsing-pipeline.md`
（PP-4/PP-6 条文补充）、`docs/guides/operations.md`（数据目录表与 D 类 runbook）、仓内 `data/` 残骸清理
（**仅在你批准后**）。

**非目标**：改数据根顶层白名单本身（那是根仓库 ADR 0018 的权限，新增条目须根侧立 ADR）；
库↔盘对账 doctor 工具（根 REQ-096，落点在根侧 8900 或 CLI）；`dataset/` 内的存量清理
（根 REQ-094，属根侧数据，本仓不写）；真实数据根的写入与解析（无 D 类批准）；`qed_test` 之外的库操作；
页图/插图的跨版本去重实施（PLAN-015 A5 触发型观察，未触发）；`tmp/` 的历史窗口脚本清理
（属 ARCH-010 文档规范化长尾，不在本壳）。**与 PLAN-009 P2（manifest-last 就绪 + 半写目录失败重清）
重叠**：本轮只定 C6 的识别式与告警口径，重清的实施归 P2，避免两处各写一套。

## 前置条件

1. ~~四项裁决 Q1~Q4~~ **已答复**（见「裁决记录」节），本条满足。
2. 根仓库 ADR 0018 与 `dataset-conventions.md`/`storage-conventions.md` 保持当前正文（已 Accepted，
   只读参照）。
3. ~~C3 属删除本地文件（不可逆）：逐路径 dry-run 清单过目~~ **已满足**（2026-09-24 清单出示并获用户
   批准后执行，见 W7 行）。
4. C7 与 S3 涉及 `architecture/`+`design/` 条文变化 ⇒ 已按「大修改」立本壳，未定级不动码。

## 裁决记录（2026-09-24 用户裁决，已定）

| # | 问题 | 裁决 | 落地含义 |
| --- | --- | --- | --- |
| Q1（C2） | 未配置 `QED_DATA_ROOT` 时的行为 | **(A) 直接报错** | W3 去掉 `Settings.data_dir` 的仓内默认值，缺配置即启动期失败（同 `require_admin_configured` 纪律）；`test_config.py:73,190,196` 三处断言改写为「缺失即报错」 |
| Q2（C3） | 仓内残骸是否清理 | **进行清理**（dry-run 实测：`data/books/` 399 文件 **1.2 GB**、两个 `.log` 各 0 MB） | W7 解锁：`data/books/01-rudin-trial/` 与 `data/api-8902*.log` 删除；`tmp/probes/`、`tmp/backups/` 保留并升格为声明过的留档区。执行前逐路径 dry-run 过目，前后指纹留档 |
| Q3（C6） | 崩溃残留的处置深度 | **本轮只做识别式＋告警**，重清归 PLAN-009 P2 | W5 范围收窄：`.tmp-`/`.old-`/暂存目录三式成文（已随 W1 写入 `PP-6`）+ 启动期与 purge 路径的告警计数；不做失败重清 |
| Q4 | 是否升格为本仓 ADR | **立 ADR 0009** | 已成文 [ADR 0009](../../adr/0009-data-root-must-be-explicit.md)（状态 Accepted）并登记进 [ADR 索引](../../adr/index.md)（下一可用编号 0010） |

## 工作项

| # | 工作项 | 落点 | 状态 |
| --- | --- | --- | --- |
| W1 | 白名单成文：`overview.md` 数据边界节加「顶层只允许 `raw/`+`tmp/`+`parsed/`+`backups/`，本仓只写 `parsed/`」+ 哨兵区；`operations.md` 数据目录表补野生区判据；`parsing-pipeline.md` PP-6 收口根侧第③④条（虚构约定不复活 + 瞬态两形态与识别式） | 同上三篇 + `docs/adr/0009-data-root-must-be-explicit.md`、`docs/adr/index.md` | **已完成（文档批）**：哨兵区与「唯一写入 `parsed/`」入册，PP-5/PP-6 就地改写，ADR 0009 登记；守护待 W2 |
| W2 | 新增本仓存储治理守护（先取红灯）：哨兵区一致性、`tmp/axiom-flow` 命中为 0、写入面只能落 `parsed/`（用假数据根探针断言越界写必抛）、留档区不入岛清单、`data/` 顶层无产物形状（放行 `tmp/probes/*/root/` 探针根） | `tests/contract/test_storage_conventions_governance.py` | **已完成**（8 例，先取 3 红）：哨兵区、字样双作用域、越界写、`parsed/` 前缀、缺配报错、仓内 `data/` 拒绝、递归产物形状。**判据解读**＝固定域（`standards/`+`architecture/`）字面为 0；条文域（`design/`/`guides/`/`adr/`）按行否定门控；历史/台账/计划壳不入扫描（2026-09-26 用户追认） |
| W3 | 数据根解析收口（Q1=A 已定）：去静默回退；同步 `test_config.py` 三处、`config.py` 文件头、`local-dev.md`/`operations.md` 配置表 | `src/axiom_flow/config.py`、`tests/unit/test_config.py` | **已完成**：`DataRootError` 三拒（缺配 / 指向仓内 `data/` / 目录不存在）且**不自动创建目录**（fail-closed 从严解读，2026-09-26 用户追认）；`test_config` 夹具注入临时根、`test_engine_factory` 两处改写；development/local-dev/operations 三文档同步 |
| W4 | 书目枚举改深度无关（复用 `admin.py:70` 的产物标记判据，命中即剪枝），补两层/三层两形态回归；`api.md` 枚举口径同步 | `src/axiom_flow/api/main.py`、`tests/contract/test_api_v1_contract.py`、`docs/architecture/api.md` | **已完成**（4 例，先取 2 红）：`is_book_dir`/`iter_book_dirs` 升公有供 `main.py` 回退与全项目枚举复用，修两处固定三层 glob；`api.md` 枚举口径同步，契约文件 82 例全绿 |
| W5 | 瞬态残留识别式与告警（Q3 已定：只告警不重清）：三式常量成文 + 启动期与 purge 路径计数告警；`PP-4`/`PP-6` 条文与 `operations.md` runbook 核对步骤 | `src/axiom_flow/ingest/artifacts.py`、`importer.py`、`api/admin.py`、`docs/design/parsing-pipeline.md` | **已完成**（6 例，先取 4 红）：`TRANSIENT_TMP_INFIX`/`TRANSIENT_OLD_INFIX` 两常量 + `is_transient_name`/`scan_transient_residue`/`warn_transient_residue`；lifespan 与 purge 两路径接线；`importer`/`artifacts` 生成式改常量；`PP-4`/`PP-6` 条文与 `operations.md`「留档区与瞬态核对步」同步 |
| W6 | 备份与留档口径成文：`tmp/backups/YYYY-MM-DD-<任务标识>` 命名式 + 「仓内留档区不算 JSON 岛、不得出现产物形状」判定 | `docs/guides/operations.md`（数据目录表两行） | **已完成（文档批）**：新增 `<QED_DATA_ROOT>/backups/` 行与本仓备份落点说明；旧式判例 `window-20260922/` 保留为历史留痕不追改 |
| W7 | C3 残骸清理（Q2 已批准；执行前逐路径 dry-run 过目）：dry-run 清单 → 你确认 → 删除 → 前后指纹留档 `tmp/probes/storage-align-<日期>/` | 仓内 `data/` | **已完成**（2026-09-24 清单过目获批后执行）：`data/books/` 399 文件 / 1,280,259,077 字节 + 两日志 7,416 字节删除；前后指纹入 `tmp/probes/storage-align-2026-09-24/`；`git status` 前后 22 项零差异；形状守护随之转绿 |
| W8 | 回执与收口：C1~C7 判定写入 PLAN-015「登记与结论」并成文 REQ-095 回执正文；code-map 行同步；六步收口 | PLAN-015 壳（已按 `Delete` 删除，正文锚点 `a97ccc7`）、`docs/architecture/code-map.md` | **已完成**（2026-09-26 口径确认）：code-map 行同步与台账回写先行落地；A1~A4 判定与 REQ-095 回执正文已回填 PLAN-015 壳并随实现批入库（`a97ccc7`）；六步收口（todo 四行关闭 → completed 迁移 → plans/index 登记 → 本壳 Retain 迁移 → project-status 刷新 → 门禁复跑）随收口批执行 |

## 验证与验收

- **默认门禁**：`pytest tests -q` 全绿（新增守护用例逐条先取真红灯再实现；W4 的两形态回归网须先
  复现两层布局下的漏读）＋ `ruff check src tests scripts` clean。
- **变异复验**：新守护每条断言各做一次反向变异（改哨兵区、写入 `raw/` 造一个越界写、复活
  `tmp/axiom-flow` 字样、命名式改回旧式），确认**只**红对应用例后逐例还原。
- **MySQL 层**：W3/W4 不碰 DDL 与 `af_*` 结构；若实现触到持久化路径，按纪律跑
  `QED_TEST_MYSQL=1` 集成层（只 DROP `af_*` 四表 + `alembic_version`，绝不 `DROP DATABASE`）。
- **真实数据根**：本轮**不写** `D:\coding\QED-Engine\dataset`；C7 的两层形态用临时根复现
  （`domain_id=""` 的真实形状），不靠真实数据背书。
- **零事实丢失**：W1 的文档改写以反引号 token 覆盖比对自证（沿用 PLAN-014 的判据替代口径）。
- **S5 判据**：清理前后 `data/` 顶层清单与消失路径入档，且 `git status` 前后无差异（`data/` 全忽略）。

## 回滚

- W1/W6/W8（纯文档）：按文件还原本壳涉及行。
- W2（守护）：删除新增测试文件即回到无守护态；不改既有测试。
- W3（行为）：`config.py` 的回退分支是唯一行为改动，`git revert` 单提交可逆；Q1 选 (A) 后若需临时
  跑旧口径，显式设 `QED_DATA_ROOT` 即可，无需改码。
- W4/W5（读取与告警）：不改产物格式、不迁库、不改 `af_*` 列，回滚＝还原代码，产物零影响。
- W7（清理）：**不可逆**——执行前对每个待删路径出 `dry_run` 清单并给你过目；`01-rudin-trial` 与
  日志若你要求可先移动到 `tmp/probes/legacy-v1/` 代替删除（可逆）。
- 本壳全程不提交、不推送（按「暂不推送」）。

## 关闭与归档

按 [ADR 0006](../../adr/0006-ledger-plan-carrier-contract.md) 六步收口：结论写入本壳「登记与结论」→
ARCH-034 与 PLAN-016 两行压缩并关闭 → 迁移[关闭台账](../../trackers/completed.md) →
`plans/index.md` 登记去处 → `project-status.md` 刷新 → 全量门禁。归档判定见元数据行：
执行了 W7 即 **Retain**（数据操作留档），否则 Delete。

## 登记与结论（执行时填写）

| # | 结论 | 证据 |
| --- | --- | --- |
| W1/W6 | 文档批达成（S1/S6 成文面） | 哨兵区与白名单入 `overview.md`；`PP-5`/`PP-6` 改写；`operations.md` 数据目录表 + 「留档区与瞬态核对步」；ADR 0009 Accepted 并入索引 |
| W2 | 达成（8 例，先取 3 红） | `tests/contract/test_storage_conventions_governance.py`；判据双作用域 2026-09-26 追认入行状态 |
| W3 | 达成（S2） | `config.py` `DataRootError` 三拒、不自动创建（追认）；`test_config` 5 例改写 |
| W4 | 达成（S4，两形态回归网） | 4 例先取 2 红；`api.md` 枚举口径同步；契约文件 82 例 |
| W5 | 达成（S6 识别式与告警半边） | 6 例先取 4 红；lifespan + purge 两路径计数告警 |
| W7 | 达成（S5） | 指纹 `tmp/probes/storage-align-2026-09-24/`（before/after 两文件）；`git status` 前后零差异 |
| 变异复验 | M1/M3 单红精确、M2 触发三层防线（36 红）、M4 0 红 | M4＝备份命名式无门禁（用户裁决不补，该面由 runbook 人工核对承担）；逐例字节级还原后回基线 |
| 变异独立复验（第二窗口逐条重做） | 每条新断言各咬一次且只红对应用例，全部字节级还原（`restored=True`）：哨兵区插第五条目录→仅 `test_whitelist_sentinel_matches_constant`；`tmp/axiom-flow` 复活进 `standards/`→仅固定域守护、进 `guides/` 中性句→仅否定语境守护；`_resolve_data_dir` 缺配改回静默落仓内 `data/`→仅 `test_missing_data_root_fails_closed` + `test_missing_data_root_raises`；`data/` 造 `book.json`→仅形状守护；`_books_from_filesystem` 改回固定三层 glob→仅两层 listing 用例（另两处调用点未改，红点精确归位）；`TRANSIENT_TMP_INFIX`/`TRANSIENT_OLD_INFIX` 改名→识别式 2 红 + 清除路径告警接线 1 红。**首轮一条变异写法无效已重做**：把缺配分支改成 `if False:` 仍会走「目录不存在」报错，不构成静默回退，第二轮改为显式 `return _repo_archive_dir()` 才取证 | M4-archive 与 W7 指纹同轮复核：`tmp/probes/storage-align-2026-09-24/` 两文件在，before 记 `books/` 399 文件 / 1,280,259,077 字节 + 两日志，after 只剩 `backups/` 与 `probes/`，数字与本壳 W7 行逐字一致 |
| ADR 0009 决定 5 措辞对齐 | 独立复验发现原文比实现宽：守护实际断言＝构造器一律前缀 `parsed/` + `ensure_within` 越出**数据根**必抛，而非任意 `parsed/` 越界写必抛（决定 4 的「向其他顶层区写入判为缺陷」仍是评审判据，M2 变异时 36 红为行为侧证据）。已按实况收窄措辞 | 未新增运行期闸门：`atomic_write_*` 不知数据根，加闸需改签名与全部调用点，超出本壳范围 |
| 门禁 | 全绿（本壳实施时） | `537 passed / 10 skipped / 0 failed` + `ruff check src tests scripts` clean（起点 517 passed） |
| 门禁（第二窗口复跑） | 本壳改动零红点，全量 `536 passed / 10 skipped / 1 failed` + ruff clean | 唯一红灯 `test_todo_plan_rows_exactly_mirror_active_plan_bodies` 属并行会话新落的 `docs/plans/2026-09-26-parsing-root-view-cleanup-request.md`（尚无 todo 镜像行），不入本壳归属；本壳收口第 6 步（门禁全绿）在该行登记前无法闭合 |
| W8 | 回执成文 + 六步收口完成（2026-09-26 口径确认后执行） | A1~A4 判定与 REQ-095 回执正文回填 PLAN-015 壳（正文锚点 `a97ccc7`）；收口批＝todo 四行关闭 → completed 迁移 → plans/index 登记 → 本壳 Retain 迁移 → project-status 刷新 → 门禁复跑全绿 |

**S1~S7 逐条判定**：S1 达成（哨兵区成文 + 守护）；S2 达成（fail-closed 去静默回退）；S3 达成
（写入面守护，M2 变异时治理/单测/作用域三层防线同时转红）；S4 达成（深度无关 + 两形态回归）；
S5 达成（清理经批执行、前后指纹、`git status` 零差异）；S6 达成（识别式与命名式成文 + runbook
核对步；M4 证明命名式无门禁，经裁不补）；S7 达成——门禁全绿已取，REQ-095 回执正文随 2026-09-26
口径确认成文于 PLAN-015 壳正文（锚点 `a97ccc7`）。

**Q1~Q4 裁决**：见上「裁决记录」节（已定，ADR 0009 据 Q4 成文）。

**终态**：W1~W8 全部完成。W8 的回执成文与六步收口随 2026-09-26 收口批执行；壳按 **Retain**
迁 `docs/history/plans/2026-09/`（W7 属已执行的 D 类数据操作，前后指纹为不可再生留档）。
