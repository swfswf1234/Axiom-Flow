# 设计叙述框架重整（design-narrative-framework）

状态：Completed
任务类型：A
最后更新：2026-09-23
关联 ADR：[ADR 0002](../../../adr/0002-pending-design-location.md)（待评审设计随计划承载）、
[ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）、
[ADR 0007](../../../adr/0007-parse-status-semantics.md)（`S1~S7` 条文的重编号对象）
关联设计：[parsing-pipeline.md](../../../design/parsing-pipeline.md)、[parsing-management.md](../../../design/parsing-management.md)、
[model-integration.md](../../../design/model-integration.md)、[design/index.md](../../../design/index.md)
关联 Tracker：docs/trackers/todo.md（PLAN-012、ARCH-010）
归档判定：Retain（原判定 `Delete`；关闭时壳与 `docs/design/` 改动均未提交 ⇒「Git 锚点有效」不成立，
按 PLAN-011 先例改判。保留价值：三篇「现有章节 → 新骨架」逐节映射与 S1~S6 判定，是六段归属
**为什么这样分**的唯一推导依据）

> **本壳是「叙述框架」的评审载体**：批准「叙述框架」节后才执行 W2~W7，届时才动 `docs/design/` 正文。
> **编号说明**：本计划不新增 `ARCH` 行——版本末期文档整理的长期承载是
> [ARCH-010](../../../trackers/todo.md)（`standards/doc-governance.md`「版本末期文档整理」明定长期执行由它承载），
> 本壳即该轮的第一次实质执行；本仓库 ARCH 序列与根仓库 ARCH 序列各自独立（根侧现用至 ARCH-028），
> 引用根侧一律写「根仓库 ARCH-0xx」，因此不再为本行让号。
> **任务类型 A 的理由**：本计划不改任何运行时代码与接口行为，只重整文档叙述框架并新增
> `tests/contract/` 守护用例。

## 目标与成功标准

**目标**：让 `docs/design/` 三篇成为**可独立阅读的设计事实源**——每篇只回答一个唯一问题、边界写死、
条文编号在本篇内可解析、元数据只描述状态不叙述历史。

**为什么现在做**：2026-09-23 的 design/ 审视批（登记在 ARCH-010 证据列）落出七条可验证缺陷，其中三条
是**读者会读到错事实**级别的：非法枚举、已退役组件仍写成设计、条文编号指向归档壳。同时 v0.1 已收口
（本轮无并行实现任务抢文档），是重整窗口。

**成功标准**：

| # | 标准 | 判据 |
| --- | --- | --- |
| S1 | 三篇同构：六段骨架齐备，H2 白名单可被测试穷举校验 | `test_design_documents.py` 新增「章节骨架」用例 |
| S2 | 元数据合法：`实现状态` 取值属合法枚举且 ≤ 60 字，日期化过程不在元数据里 | 守护用例 + 全库 `rg "实现状态：Current" docs/design docs/architecture` 归零 |
| S3 | 条文自证：design/ 正文不再出现 `PLAN-\d+ 条文` 形式的跨壳编号，改为 `PP-n`/`PM-n`/`MI-n` | 守护用例（正则命中即红）+ 篇末「条文表」逐条有实现落点 |
| S4 | 无第二份契约表：design/ 不出现数据库列名清单与端点字段清单，端点只写语义 + 链接 | 守护用例（表头含「类型」+「约束」或出现列名清单即红） |
| S5 | 术语消歧：`block`/`window`/`chunk` 三义定名，正文无裸「块」歧义 | `rg` 复核清单 + 人工抽查三处高危段 |
| S6 | `design/index.md` 回归纯导航（唯一问题 + 管/不管），日期化「三态核对」段退役 | 索引不含日期化章节；`test_markdown_links.py` 绿 |

## 范围与非目标

**范围**：`docs/design/` 三篇 + `design/index.md`、`docs/architecture/` 四篇的**元数据取值**（只改枚举与
长度，不改正文契约）、`tests/contract/test_design_documents.py` 与 `test_architecture_documents.py` 的守护
新增、`docs/plans/index.md` 与 ARCH-010 证据列的登记同步。

**非目标**：

- **不改任何运行时代码与接口行为**（本计划是文档契约重整；`src/`、`schemas.py` 字段名不动）。
- 不新增/删除/移动 `docs/` 下的目录（「文档架构变更约束」阻止项；三篇篇目保持现状＝**保持三篇**）。
- 不改 `standards/doc-governance.md` 的枚举规则 ⇒ **不需要新 ADR**（把非法取值改回合法枚举属纠错）。
- 不重写 `architecture/api.md`、`database-design.md` 正文（它们是本计划的收敛目标，不是对象）。
- 不把「暂定」设计整体转正为「已确认」：转正只在 S1~S6 达成后逐篇由你确认（转正评审触发条件见治理规范）。

## 前置条件

1. **你批准「叙述框架」节**（六段骨架 + 三条硬规则 + 条文重编号 + 术语定名）——W2 之前的唯一闸门。
2. v0.1 主线无未收口的实现轮（PLAN-008 已 `Blocked` 等根侧，不抢 design/）。
3. 工作树保留用户既有变更；本计划所有改动均在 `release`。

## 叙述框架（待你评审的部分）

### 1. 每篇的唯一问题

| 文档 | 唯一问题（一句话） | 越界即删的内容 |
| --- | --- | --- |
| `parsing-management.md`（PM） | 一本书从「已验证书目」到「人工校验完成」，**状态如何流转、职责落在哪一段** | 端点参数/响应字段清单、`af_*` 列清单（→ `architecture/`） |
| `parsing-pipeline.md`（PP） | 一份 PDF **怎样变成可消费的产物**，以及**什么算合格** | 任务/书目状态语义（→ PM）、引擎配置键（→ MI）、DDL |
| `model-integration.md`（MI） | 本仓库**如何与模型服务对话**（配置键、适配器契约、调用失败语义），模型生命周期不在内 | 管线编排顺序（→ PP）、8900 启停实现（→ 根仓库） |

### 2. 六段骨架（三篇同构，H2 固定顺序）

1. **目标与非目标**——各 ≤5 条；每条非目标必须写「归谁」。
2. **边界与事实源**——管/不管表 + 链接（端点→`api.md`，DDL→`database-design.md`，模块→`code-map.md`，
   运维→`operations.md`，状态机→`api.md` 状态机表）。
3. **核心设计**——不变式 + **就地编号条文**（`PM-n` / `PP-n` / `MI-n`），篇末「条文表」是自证清单：
   每条给出实现落点（模块/函数）与守护测试名。
4. **失败与质量语义**——错误码归属、可见丢弃、降级与恢复路径。
5. **验收判据**——每条判据指向一个**可运行**的测试/冒烟名；不写「某日已验收」的过程。
6. **追溯**——关联代码/测试/ADR/承载计划（与元数据一处，不重复维护）。

### 3. 三条硬规则（各配守护用例）

- **R1 元数据禁历史 + 取值合法**：`实现状态` ∈ {`Not Started`,`In Progress`,`Implemented`,`Verified`,`Blocked`,`Completed`}
  且 ≤ 60 字；日期与轮次过程一律写 `completed.md`/计划壳。
  **取值口径建议**：三篇 design 与四篇 architecture 统一 `Verified`（本仓库无远端 CI，以
  「全量本地门禁 + 真机取证在案」为 `Verified` 的等价判据，`Blocked` 时改标并写恢复条件）。
  `tests/contract/*.py` 文件头的 `实现状态：Current` **不改**——测试文件不是受治理文档，守护只扫
  `docs/design|architecture|standards`。
- **R2 条文编号必须在本篇内解析**：只允许 `PP-n`/`PM-n`/`MI-n` 与跨文档合法引用 `ADR 00nn`；
  出现 `PLAN-0xx 条文 V1~V10` / `条文 12` 一律改写，原编号降级为篇末一行「来源：PLAN-004（已归档）」。
- **R3 不做第二份契约表**：design 的端点/表小节只保留「语义与不变式 + 指向 `architecture/` 的链接」；
  出现列名清单或响应字段清单即红。

### 4. 术语消歧（现「块」三义）

| 新定名 | 含义 | 主要落点 | 现混用处 |
| --- | --- | --- | --- |
| `block`（版面块） | 引擎归一化后的最小可渲染单元（段落/公式/图/表） | `schemas.py`、产物 `blocks.json` | PP「统一格式与产物」「分块」两节 |
| `window`（解析窗口） | 全本按页区间串行提交单位（默认 56 页） | `orchestrator/pipeline.py`、`engines/mineru.py` | PP「全本窗口分块提交」节里与版面块同称「块」 |
| `chunk`（知识块） | blocks → 检索/展示切片 | `normalize`/分块节、`chunks.json` | PP「分块（blocks → chunk）」 |

行文规则：中文一律写限定词（版面块/解析窗口/知识块），**禁止裸「块」**；字段名保持英文原样。

### 5. 现有章节 → 新骨架 映射（逐节，不改结论只改归属）

**parsing-pipeline.md**（368 行，11 个 H2/H3）

| 现有节 | 去向 |
| --- | --- |
| 背景与决策记录 | 目标与非目标（决策链只留 ADR 链接，日期叙述移 `completed.md`） |
| 总体架构 | 核心设计（PP-1~）；与 `architecture/overview.md` 重叠部分改为链接 |
| 预处理、调用与后处理 | 核心设计（PP 段）；「调用」细节归 MI |
| 全本窗口分块提交与空白页语义 | 核心设计（`window` 条文）+ 失败与质量语义（`blank_page`） |
| 统一格式与产物 | 核心设计（`block`/产物布局条文）；字段清单→链接 `api.md`/`database-design.md` |
| 解析版本与生效指针 | 核心设计（版本条文，替代「PLAN-004 条文 V1~V10」引用） |
| 任务与书目状态语义 | **整节移出 PP → 并入 PM**（状态流转是管理面职责），PP 只留一句链接 |
| 分块（blocks → chunk） | 核心设计（`chunk` 条文） |
| 组件职责 | 删除，改为一行「模块清单与 DesignRef 以 `code-map.md` 为唯一事实源」 |
| 错误处理与质量验收 | 失败与质量语义 + 验收判据（503/400 归属与 PP 条文对齐） |
| 备注 | 吸收进对应段或删除 |

**parsing-management.md**（142 行，8 个 H2）

| 现有节 | 去向 |
| --- | --- |
| 背景与动机 | 目标与非目标 |
| 设计原则 | 核心设计（`PM-n` 条文） |
| 数据流 | 核心设计（状态流转图，Mermaid 保留） |
| 表结构 | **删除正文** → 边界与事实源（链接 `database-design.md`）+ 只留「版本作用域」不变式 |
| API 契约（8902） | **删除字段表** → 端点语义清单（每条一句话 + `api.md` 锚点链接） |
| 降级与独立性 | 失败与质量语义（含 ADR 0007 `S1~S7` 就地重编号为 `PM-n`） |
| 验证 | 验收判据 |
| 备注 | 吸收；`REQ-001` 改判过程叙述移出（属 trackers/completed） |

**model-integration.md**（199 行，8 个 H2）

| 现有节 | 去向 |
| --- | --- |
| 背景与定位 | 目标与非目标 + 边界（与 8900 的边界并入此处，不再单列一节两遍） |
| 引擎配置 | 核心设计（`MI-n` 配置键条文） |
| 引擎适配接口 | 核心设计（适配器契约 + `bbox_space`） |
| 各引擎接入（MinerU / PaddleOCR-VL / qwen-vl） | 核心设计（按引擎分小节；预留槽位显式写「未实现 ⇒ 400」） |
| 与 8900 的边界 | 并入「边界与事实源」 |
| 退役清单（已执行，2026-09-17） | **整节移出** → `completed.md` 已有落点，正文只留一句「llm_client/fallback 已退役」 |
| 验证 | 验收判据 |
| 「调用记录 `qed_llm_calls`」两处（`:174`、`:199`） | **删除**（与退役清单自相矛盾；当前设计无调用记录表） |

## 工作项

| # | 工作项 | 关闭判据 | 状态 |
| --- | --- | --- | --- |
| W1 | 立框架：本壳成文 + todo 登记（PLAN-012 行 + ARCH-010 证据列指向本壳） | 本壳存在且门禁绿；你批准「叙述框架」节 | **Done（待你批准）** |
| W2 | 元数据与枚举统一（design 三篇 + architecture 四篇的 `实现状态` 取值与长度；日期叙述外移） | S2 判据；`rg "实现状态：Current" docs/` 归零 | Done（2026-09-23，见「实施登记」） |
| W3 | 三篇六段骨架落地（按映射表搬位，H2 顺序固定；**只改归属不改结论**） | S1 判据（骨架守护用例绿） | **Done（2026-09-23）** |
| W4 | 条文就地重编号（`PP-n`/`PM-n`/`MI-n` + 篇末条文表含实现落点与测试名）；全库残余跨壳编号同步 | S3 判据 + `rg "PLAN-[0-9].*条文" docs/design` 归零 | **Done（2026-09-23）** |
| W5 | 边界去重（PM 端点/表结构、PP 组件职责、MI `qed_llm_calls` 与退役清单） | S4 判据 + 无信息丢失复核（删掉的字段清单在 `architecture/` 逐条对得到） | **Done（2026-09-23）** |
| W6 | 术语消歧 sweep（`block`/`window`/`chunk`；裸「块」清零）+ `design/index.md` 重写为纯导航 | S5 + S6 判据 | **Done（2026-09-23）** |
| W7 | 守护补齐（R1/R2/R3 三条用例，各做一次**变异转红**复验）+ 台账收口 | 三条守护语义正确 + 全量门禁 + 收口六步 | **Done（2026-09-23）** |

## 实施登记

### W2 元数据与枚举统一（2026-09-23，你批准框架后执行）

- **守护先行（红灯）**：`tests/contract/test_design_documents.py` 与 `test_architecture_documents.py`
  各新增 `test_*_metadata_declares_legal_bounded_status`——`实现状态` 取值的枚举成员、单行 ≤60 字、
  **且不得换行续写**（`_status_line()` 检查下一行非元数据键即红）。首跑即因「换行续写轮次历史」转红，
  符合预期红灯。
- **取值口径（你裁决）**：design 三篇 + architecture 三篇正文统一 `Verified（…）`，`最后更新：2026-09-23`：
  `parsing-pipeline` 50 字、`parsing-management` 40 字、`model-integration` 54 字、`api.md` 50 字、
  `database-design.md` 59 字、`overview.md` 22 字。日期化过程原写在这六行里的部分已在
  `trackers/completed.md` 有对应落点，无信息丢失。
- **`code-map.md` 与 `index.md` 不在本守护内**：它们声明 `状态`（表格/索引类）而非 `实现状态`，
  其补登记归 ARCH-010 遗留项；架构侧守护用 `STATUS_DOCUMENTS` 显式圈定三篇正文并注明原因。
- **`tests/contract/*.py` 文件头 `实现状态：Current` 不改**（框架 R1 已登记：测试文件非受治理文档）。
- **定向验证**：`pytest tests/contract/test_design_documents.py tests/contract/test_architecture_documents.py -q`
  → 12 passed。

### W3~W5 六段骨架 / 条文就地重编号 / 边界去重（2026-09-23）

- **三篇落地六段骨架**：`parsing-pipeline.md`（368 → 377 行）、`parsing-management.md`（140 → 224 行）、
  `model-integration.md`（199 → 234 行）。H2 逐字为六段，唯一问题写在题注里；日期化叙述（原「背景与
  决策记录」「背景与动机」的 2026-08-14 / 09-14 / 09-17 裁决段）压缩为「形态决策」一段（只留**为什么**），
  过程指向 `trackers/completed.md`。
- **条文化**：`PP-1~PP-20`、`PM-1~PM-17`、`MI-1~MI-14`，每篇篇末「条文表」逐条给实现落点与守护测试名
  （落点按 `code-map.md` 核对到函数级：`_page_windows`、`_version_is_complete`、`split_math_delimiters`、
  `seed_version_from`、`_await_model_idle` 等；`PM-13` 单实例前提与 `MI-14` 出站要求属部署 / 跨项目边界，
  落点写口径归属而非函数）。
- **跨篇搬家（只改归属不改结论）**：原 PP 的「任务与书目状态语义」S1~S7 整节移入 PM ⇒ 现为
  `PM-8~PM-14`；原 PP「组件职责」表删除，模块清单归 `code-map.md`；PM 的 `af_*` 列清单与端点字段表
  删除，事实源为 `database-design.md` / `api.md`（删除前逐条核对：DDL 与响应字段在那两篇里都在）；
  MI 的「与 8900 的边界」并入「边界与事实源」，「退役清单」整节移出（`code-map.md` 已有退役登记），
  `qed_llm_calls` 两处删除。
- **同批去重**：503 不建任务行、进度写入节流、引擎名解析此前在 PP/PM/MI 各写一遍，现各留一半并互链
  （产物侧后果 → PP，状态机后果 → PM，协议侧判据 → MI）。
- **全库残余跨壳编号同步**：`database-design.md` 的 `（V2/V3/V7/V8）` 括注改指 `PM-*`/`PP-*`（6 处）、
  `api.md` 的 `（V3）`/`（V8）`（3 处）、`plans/index.md` 的「条文 S1~S7」与「解析版本与生效指针」指向
  （2 处）、`operations.md` 的「PLAN-003 条文 12 / PLAN-004 V10」⇒ `PP-6`、`development.md` 的
  「PLAN-003 条文 10」⇒ 去编号化、[ADR 0007](../../../adr/0007-parse-status-semantics.md)「取代」节的承载
  指针补上 `PM-8~PM-14`（并注明原编号，属指针修正不改裁决）。
- **守护发现并修掉一个真缺陷**：`CONTRACT_TABLE` 正则缺 `re.MULTILINE`，表头行在文档中部时永不命中——
  变异复验时暴露，已补标志并就地注明原因。

### W6~W7 术语消歧 / index 纯导航 / 变异复验（2026-09-23）

- **术语定名（你裁决 `window`＝解析窗口）**：PP「边界与事实源」落术语表，`design/index.md` 落三术语的
  归属条文；三篇裸「块」清零（逐处判语义后改版面块 / 解析窗口 / 知识块，共 23 处；「块编辑」→
  「版面块编辑」、「空块率」→「空版面块率」、「不静默丢块、不占位补块」→「不静默丢版面块、不以占位
  条目补齐」等）。扫描判据：正则列出所有「块」字的左右邻字，非 版面/知识/解析窗口/模块/分块/整块
  者逐条人工判定。
- **`design/index.md` 回归纯导航**：删除「三态核对（2026-09-14）」日期化章节（其事实已在
  `history/design/` 与 `completed.md`），表格改为「唯一问题 / 设计状态 / 实现状态 / 条文号 / 不管」，
  实现状态与三篇一致取 `Verified`；三篇链接保持 `](parsing-pipeline.md)` 形状以满足目录守护。
- **变异复验（13 例）**：S1 越界 H2、R1 非法枚举、R1 超 60 字、R2 跨壳条文、R3 的字段/列/参数/响应字段
  四种表头、S3 的条文表缺行 / 落点为破折号 / 正文与表行不符 / 编号断号——**全部按预期转红且只红对应
  用例**；改动逐例还原并断言文件与原文逐字相同。唯一一次未转红是变异串自身写错（把破折号留在长句中间，
  `cells[0]` 并非「—」），修正变异后转红。
- **门禁**：`pytest tests -q` → 485 passed / 10 skipped；`ruff check src tests scripts` → All checks
  passed。本计划无 MySQL 面变更，未跑 `QED_TEST_MYSQL=1` 层（该层判据不覆盖文档改动）。
- **移交 ARCH-010 的残余**（不在本壳范围，`src/` 与 `tmp/` 的文件头 docstring 仍写旧壳编号
  「条文 3/7/9」「V1/V3/V8」；以及 `qed_llm_calls` 的归属矛盾）：见 [todo.md](../../../trackers/todo.md)
  ARCH-010 证据列。

## 验证与验收

- 定向：`pytest tests/contract/test_design_documents.py tests/contract/test_architecture_documents.py -q`
  → 再全量 `pytest tests -q` + `ruff check src tests scripts`（本计划无 MySQL 面变更，仍按标准跑一次）。
- **新增守护必须做变异复验**：故意在 design 里放回一处 `实现状态：Current`、一处 `PLAN-004 条文 V3`、
  一处列名清单 ⇒ 三条用例各转红，再撤销（防止守护写成永真）。
- 无信息丢失核对：W3/W5 每删一节，在壳的映射表里勾掉去向，并确认 `architecture/` 对应事实源存在。

## 回滚

纯文档改动，无迁移、无数据写入：`git diff` 逐项复核后 `git checkout -- docs/design docs/architecture` 即可整体回退
（未提交状态下改动完全可逆）。分批执行（W2/W3/W4/W5/W6 各一次复核），任一批语义有问题只回退该批。

## 关闭与归档

- **关闭条件**：S1~S6 全达成且 W2~W7 关闭；你逐篇确认后才把 `确认状态` 改「已确认」（可分批）。
- **归档判定**：`Retain`（原判 `Delete`，理由见元数据行）。壳归档至
  `docs/history/plans/2026-09/2026-09-23-design-narrative-framework.md`；三篇 `design/` 与
  `project-status.md` 中指向本壳的链接同批改指归档位置。
  **后续提交完成后可在下一轮按 `Delete` 重判并删壳。**
- 收口顺序按 [ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md) 六步；ARCH-010 证据列登记本轮执行结论。

### 关闭登记（2026-09-23）

- **S1~S6 逐条判定**：

| # | 判据 | 判定 | 证据 |
| --- | --- | --- | --- |
| S1 | 六段骨架守护 | 达成 | 三篇 H2 逐字为「目标与非目标 / 边界与事实源 / 核心设计 / 失败与质量语义 / 验收判据 / 追溯」，由 `test_design_documents_follow_six_section_skeleton` 穷举校验 |
| S2 | 元数据枚举合法且日期化叙述外移 | 达成 | `test_design_metadata_declares_legal_bounded_status` + `test_architecture_documents.py` 同形用例；design 三篇 + architecture 三篇取 `Verified（…）` 且 ≤60 字、无续写行，`Current` 取值归零 |
| S3 | 条文在本篇内解析 + 条文表有落点 | 达成 | `PP-1~PP-20`/`PM-1~PM-17`/`MI-1~MI-14` 三张条文表；守护 `test_design_clauses_resolve_inside_their_own_document`（跨壳编号即红）与 `test_design_documents_declare_numbered_clauses_with_carriers`（表行与正文声明一一对应、编号连续、落点非空） |
| S4 | 无第二份契约表 | 达成 | PM 列清单与端点字段表、PP 组件职责表、MI `qed_llm_calls` 与退役清单已删；`test_design_documents_hold_no_second_contract_table` 覆盖字段/列/参数/响应字段四种表头（含本批修掉的 `CONTRACT_TABLE` 缺 `re.MULTILINE`） |
| S5 | 术语消歧 | 达成 | 裸「块」在 `docs/design/` 只剩两处**规则自述**（`index.md:24`、`parsing-pipeline.md:56`），其余 23 处按语义改版面块/解析窗口/知识块 |
| S6 | `design/index.md` 纯导航 | 达成 | 「三态核对（2026-09-14）」日期化章节删除，表头改「唯一问题 / 设计状态 / 实现状态 / 条文 / 不管」+ 术语归属表 |

- **门禁**：`pytest tests -q` → 485 passed / 10 skipped；`ruff check src tests scripts` → All checks
  passed。A 类计划无 MySQL 面变更，未跑 `QED_TEST_MYSQL=1` 层。
- **`确认状态` 保持「暂定」**：三篇待你逐篇确认后才改「已确认」（可分批），本壳关闭不依赖该动作。
- **移交 ARCH-010 的残余**（长期行保持 `In Progress`）：`src/` 与 `tmp/` 文件头 docstring 的旧壳
  编号（「条文 3/7/9」「V1/V3/V8」）与 `qed_llm_calls` 归属矛盾，属 design/ 之外的叙述面清扫，不在本壳范围。
