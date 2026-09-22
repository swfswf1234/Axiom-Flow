# 解析真机打通：设计偏差核对与契约修正（parse-live-bringup）

状态：Completed
任务类型：B
最后更新：2026-09-22
关联 ADR：[ADR 0002](../../../adr/0002-pending-design-location.md)（待评审设计先入 plans/）、[ADR 0004](../../../adr/0004-model-boundary-and-pipeline-ownership.md)（引擎由配置选择、不设自动兜底）、[ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md)（MySQL 集成测试隔离边界）
关联设计：[model-integration.md](../../../design/model-integration.md)、[parsing-pipeline.md](../../../design/parsing-pipeline.md)、[parsing-management.md](../../../design/parsing-management.md)
关联 Tracker：docs/trackers/todo.md（PLAN-003、ARCH-001/003/004/005/006/009/016/017/018/019、REQ-001、REQ-075、REQ-080、REQ-081、REQ-083、REQ-084、REQ-085、REQ-036/057/074/086/087、DEFECT-001/002/003 与本轮新登 DEFECT-004/005/006/007/008）
归档判定：Retain（契约条文晋升 design/architecture、实现收口后，计划壳归档 `docs/history/plans/2026-09/`）

> **2026-09-20 收尾范围（用户裁决）**：本计划的执行面收窄为「**解析真机打通（小样止）** + **数据库
> 存储收口** + **QED_DATA_ROOT 收口**」三块，详见 [收尾执行设计](#收尾执行设计真机打通--数据库与数据根收口2026-09-20-裁决后重排)。
> 产物按版本划分（REQ-080）已由 [PLAN-004](../../../history/plans/2026-09/2026-09-20-req080-parse-version-layout.md) 立项并**挂起**
> 在本部分之后，本计划不做版本布局与 DDL。

## 背景：三方对表暴露的问题比单个 404 更大

af_* 四表与 `QED_DATA_ROOT/parsed/` 布局已于 2026-09-20 真机验证并关闭（ARCH-013/014/015 入
`completed.md`）。链路上唯一未通的是**解析本身**：真机任务 `queued → running → failed`，根仓库
REQ-075 实证原因是 `engines/mineru.py` 轮询 `GET /get_task_results/{task_id}`，而 MinerU 3.4.4
容器没有该路由。

把**设计文档**、**代码**、**真机事实**三方对表后，暴露出三类断裂：

1. **设计过期**：`model-integration.md:85-86` 与 `parsing-pipeline.md:81` 承诺的是同步
   `/file_parse`；代码实现的是异步提交 + 轮询；真机需要的是第三种异步协议
   （`/tasks/{task_id}` 状态 + `/tasks/{task_id}/result` 结果），且**响应形状在两个仓库里都没有
   留档**（根仓库唯一成功证据字段是 `md_content`，来自 `local-model-management.md:219-221`）。
2. **代码违约**：三条已核实的设计承诺在代码里落空——提交前 503 探活零实现（B1）、进度与页状态
   不落库（C1）、`AXIOM_OCR_ENGINE` 从不分派（D1，违反 ADR 0004 级承诺）。
3. **设计缺口**：轮询失败语义、error 双载体截断分工、`af_pages.status=pending` 的表示方式、
   ingest 幂等对产物与页表的处置、book_id 与 file_path 的数据根边界，设计文档均未成文。

因此本轮**先做设计**：本计划作为待评审设计载体（ADR 0002），承载偏差台账、契约条文草案与
W0~W8 工作项；实现完成后统一晋升 `docs/design/` 与 `docs/architecture/`。REQ-080（产物按解析
版本划分）不属本计划，拆为 PLAN-004。

## 用户裁决（2026-09-20）

1. 新建本计划（PLAN-003）取代 PLAN-002 Step 3 的承载位置；PLAN-002 Step 3 表保留但降级为历史索引。
2. MySQL 双 Session 集成测试**先立 ADR 0005**（隔离边界变更，按 `testing.md:53`），评审通过后再
   动 `docs/standards/testing.md` 与测试代码。
3. 探测实验授权，并在单页样本之外**加跑 5 页含公式/表格小样**；预算放宽到 ≤25 请求、≤3 次提交、
   ≤20 分钟。
4. REQ-080（`versions/<job_id>/` + 生效版本指针 + af_parse_jobs 新列迁移）拆 PLAN-004 后置，
   本计划不做任何 DDL 迁移。

## 目标与成功标准

**目标**：消除「设计—代码—真机」三方偏差，在真机上把 PDF 到统一产物（页图 + blocks + markdown +
chunks + manifest）的解析链路打通，并把过程中确立的契约成文，作为晋升 design/architecture 的
输入。

**成功标准**：

1. S1 端点契约以**留档证据**为准：MinerU 3.4.4 的提交、状态、结果三个端点与响应关键字段，全部
   有 `/openapi.json` 或真实响应留档（sha256）支撑，写入本计划 W0 冻结清单。
2. S2 真机整本解析成功：`linearalgebra-b01` 5 页小样在 MinerU 5002 上 `parsed` 终态，产物 blocks
   与 markdown 归一化质量可抽查（块类型、坐标、页序正确）。
3. S3 进度与页状态真落库：任务运行中 `GET /api/v1/parse-jobs/{id}` 可见 `progress.parsed` 递增；
   整本失败时已解析页的 `af_pages` 行保留；kill 服务后重启按 `af_pages` 续跑未完成页。
4. S4 设计违约清零：提交期 503、引擎显式分派（未配置槽位即报错、绝不回落）、book_id 与 file_path
   数据根边界、ingest 重做后状态回置，四者有测试覆盖。
5. S5 隔离不破：默认门禁不连公网、不连运行库；MySQL 侧只有 `QED_TEST_MYSQL=1` 显式 opt-in 才跑，
   且硬校验目标 schema 为 `qed_test`。
6. S6 文档自洽：F1~F6 三处状态口径矛盾、ADR 0004 正文 8002 残留、死引用与空目录清理完成；
   `tests/contract/` 文档守护全绿。
7. S7 门禁全绿且台账一致：`pytest tests -q`（基线 245 passed 之上）+ `ruff check src tests scripts`，
   PLAN-003 行状态与本文件 `状态：` 字段逐字一致。

## 范围与非目标

**范围内**：`engines/mineru.py`（三段化与错误语义）、`engines/factory.py`（新增）、
`orchestrator/pipeline.py` 与 `jobs.py`（页级持久化与进度落库）、`api/main.py`（提交期探活、引擎
分派接线、ingest/purge 状态回置）、`db/repositories.py`（`reset_for_book`）、`schemas.py`（入参
校验收紧）、`ingest/importer.py` 与 `artifacts.py`（数据根边界）、`config.py` 与
`ingest/importer.py` 数据根推导收口、`tests/`（单元/契约/集成/冒烟四层补齐）、
`scripts/tmp/probe_mineru_api.py`、`docs/plans`、`docs/adr`、`docs/standards/testing.md`、
`docs/trackers/`，以及偏差台账涉及的 `docs/design/` 与 `docs/architecture/` 条文修订（晋升动作在
W7，`architecture/` 两文档按大修改路径由本计划承载）。

**非目标**：不修改 QED-Engine 根仓库与 QED-Tracker（只读）；不实现 paddleocr-vl / qwen-vl 引擎
本体（只补分派与显式报错）；不引入任何自动兜底或静默降级；不做 REQ-080 版本布局与
`af_parse_jobs` DDL（PLAN-004）；不做 Rudin 全量验收（ARCH-009）；不做 `release` 合并回 `main`
与打标签；不新增远端 CI。

## 前置条件

1. MinerU 3.4.4 容器可由根仓库 8900 启停，`http://127.0.0.1:5002/health` 可达（本仓库只连接，
   不接管启停）。
2. `qed` 库中 af_* 四表已建（`alembic_version = 20260917_0001`，2026-09-20 真机确认）。
3. ADR 0005 经用户评审通过，方可编写任何连真实 MySQL 的测试（W3 前置）。
4. `QED_DATA_ROOT` 下存在 `linearalgebra-b01` 已 ingest 书籍，用作失败页回归对照（其第 3 页为
   真机已知的 failed 页）。

## 偏差台账（核对基准）

判定口径：**代码违约** = 设计已成文、代码未遵守；**设计过期** = 设计描述的接口/事实已不成立；
**设计缺口** = 行为需要规则但无处成文；**代码异味** = 功能正确但存在重复事实源或死代码。
行号为本轮逐一验证过的实际位置（2026-09-20，提交 `a5d2e96` 之后）。

### A 组：引擎调用面（DEFECT-002）

| # | 设计说什么 | 代码做什么 | 判定 | 处置 |
| --- | --- | --- | --- | --- |
| A1 | `model-integration.md:85-86`「`POST /file_parse`（multipart，**同步**）」；`parsing-pipeline.md:81` 同述 | `mineru.py:142-171` 提交后取顶层 `task_id` 并轮询（`:160` `data.state`） | 设计过期 + 代码需重定向 | W0 探测定契约 → W1 三段化 → 晋升 MI |
| A2 | 轮询失败语义零成文（`parsing-pipeline.md:174` 只定义单页重试 1 次） | `mineru.py:161-164` 一次 HTTP 异常即 `EngineError`，经 `pipeline.py:211-212` 整本 failed | 设计缺口 | 成文「瞬时失败阈值 3、deadline 前不中断」 |
| A3 | `pipeline.py:6` docstring「整本失败 → 已解析页保留（可重跑）」 | `mineru.py:165` `result.json()` 无 try，`JSONDecodeError` 穿透 `except EngineError`，`jobs.py:62-66` 回滚吞掉全部页行 | **代码违约** | 非 JSON 归一 `EngineError` + 页级 commit 双保险 |
| A4 | 响应形状无条文 | `mineru.py:39-100` 读 `data.state`、`data.err_msg`、`content_list`、`markdown`、`middle_json.pdf_info`、`images` | 设计缺口（最高优先） | W0 以 `/openapi.json` + 留档哈希为准；未探明项显式登记 |
| A5 | 设计未提任何请求表单字段（`model-integration.md:85-86` 只写「multipart」） | openapi 实测：`return_content_list`、`return_middle_json`、`return_images` **默认 false**，`return_md` 默认 true；现有代码不发送这些字段 | **新发现违约风险**（W0 探测出，台账外新增） | 适配器显式发送产物形态开关；成文「只设形态开关、不设解析调参字段」边界（条文 1b） |
| A6 | 设计未提连接形态；PLAN-002 裁决已删除 `QED_PROXY` 配置键，语义上本地模型流量不经代理 | `mineru.py:124` `httpx.Client(timeout=...)` 默认 `trust_env=True` → 读 Windows 注册表代理（实测 `getproxies() = http://127.0.0.1:7890`，且 `proxy_bypass("http://127.0.0.1:5002") = 0` 不豁免）→ 本地 5002 流量被系统代理劫持，代理在线时 502/0 字节/约 2s，直连时 200 | **代码缺陷（环境陷阱）**，W0 探测中实证 | 引擎客户端以 `trust_env=False` 构造（直连回环），W1 内实现并加单测；条文 11 |

### B 组：健康检查与错误边界（DEFECT-003 部分）

| # | 设计说什么 | 代码做什么 | 判定 | 处置 |
| --- | --- | --- | --- | --- |
| B1 | `parsing-pipeline.md:176`「提交前健康检查返回 503」；`api.md:192` 错误表列 503；`api.md:211-212` 只读端点不依赖模型服务 | `api/main.py` 全文无 503；`create_parse_job`（`:381-411`）不探活；`health()` 唯一生产调用在 `pipeline.py:150`；`engines/base.py:14` 注释虚称已有映射 | **代码违约** | W5 提交期探活 503；注释与实现对齐 |
| B2 | `parsing-pipeline.md:176` 提交期 503 与 `parsing-management.md:93` 执行期 failed 的边界未划分 | 两段都无实现，语义无法验收 | 设计缺口 | 成文两段并存：受理前 503 且不落 job 行，受理后 job `failed` |

### C 组：持久化与进度（DEFECT-001）

| # | 设计说什么 | 代码做什么 | 判定 | 处置 |
| --- | --- | --- | --- | --- |
| C1 | `database-design.md:124/137-139` 进度自持与 `progress` 结构；`parsing-pipeline.md:177/190` 断点续跑与 kill 恢复验收 | `db/repositories.py` 只 flush（11 处），`update_progress`（`:196`）**生产零调用**，唯一成功路径 commit 在 `orchestrator/jobs.py:60` | **代码违约** | W2 页级 commit + 节流 `update_progress`；W3 真实 DB 测试 |
| C2 | `api.md:180` `progress.error` 截 200 与 `database-design.md:139` `error` 截 500 的分工未定义 | `pipeline.py:212` 用 `[:500]`、`:215` 用 `[:200]`、`repositories.py:216` 用 `[:500]` | 设计缺口 | 成文：`job.error` 为终态摘要 ≤500，`progress.error` 为进行中失败页摘要 ≤200 |
| C3 | `database-design.md:154` `af_pages.status` 三值 pending/parsed/failed | 代码只写 parsed/failed，pending 行从不预建 | 设计缺口（小） | 成文「无行即 pending；续跑＝无行或 failed 页重跑」 |

### D 组：引擎分派（DEFECT-003，违反 ADR 0004 级承诺）

| # | 设计说什么 | 代码做什么 | 判定 | 处置 |
| --- | --- | --- | --- | --- |
| D1 | `adr/0004:31-32`「引擎由配置选择、换模型只改配置」；`api.md:178-179` `engine?` 可覆盖；`parsing-pipeline.md:175` 与 `model-integration.md:87-88` 不设自动兜底 | `settings.ocr_engine` 唯一读取点在 `main.py:283`（只写 book.json 元数据）；引擎硬编码在 `main.py:108-110`；`payload.engine`（`schemas.py:362` 裸 str）只入库不回读 | **代码违约（违 ADR 级承诺）** | W4 新增 `engines/factory.py` 显式分派 + 未实现/未知即报错；`schemas.py:362` 收紧为 Literal |
| D2 | `model-integration.md:48` 说 `AXIOM_PADDLEOCR_URL` 已移除，`:94` 又说启用时新增该键 | 文档自相矛盾，无代码影响 | 设计过期（自相矛盾） | W7 统一措辞 |

### E 组：数据边界与幂等（DEFECT-003 部分、REQ-080 邻接）

| # | 设计说什么 | 代码做什么 | 判定 | 处置 |
| --- | --- | --- | --- | --- |
| E1 | `parsing-management.md:98`「ingest 幂等」；`parsing-pipeline.md:103`「原子替换不留半成品」 | `ingest/importer.py:114-116` 直接 `rmtree(book_dir)`（含 blocks/md/chunks），`main.py:288` 不回置 `parse_status`、`pages_done`，不清 `af_pages` | 设计缺口 | W6 回置 `parse_status=pending, pages_done=0` + 新增 `PageRepository.reset_for_book` |
| E2 | 未成文 | `main.py:234-238` `?purge=true` 删目录与 af_books 行，不删 af_pages | 设计缺口 | W6 成文并实现（af_parse_jobs 历史行保留为审计） |
| E3 | — | `config.py:27-29` 与 `importer.py:32-37` 两套数据根推导**当前同值**（实测均为仓库根 `data/`），但 `default_data_dir()` 不走 config 的「env 优先 → 自身 `.env` → 父 `.env`」次序，且只被 `tests/unit/test_ingest.py` 引用 | 代码异味（重复事实源） | W7 删 `default_data_dir`，测试改测 `load_settings` |
| E4 | `parsing-pipeline.md:87-101` 隐含 book_id 是安全路径段，无成文边界 | `book_id` 任意 str 进入 `book_dir_for`（`main.py:141/234/387`）与 glob（`:144`）且无 `is_relative_to`；`BookSyncItem`（`schemas.py:279-291`）全裸 str；`resolve_source_pdf`（`importer.py:50-53`）绝对路径原样使用、不限数据根；对照 `pipeline.py:82-87` 图片落盘**有**防护 | 设计缺口 + 防护不对称 | W6 schemas pattern（422）+ `artifacts.ensure_within()` 纵深防御 |

### F 组：文档与残留自相矛盾

| # | 现象 | 判定 | 处置 |
| --- | --- | --- | --- |
| F1 | `parsing-pipeline.md:148`「覆盖后重算」与 `api.md:172`「实时重算、不落盘」表述不一（实现按后者，合规） | 设计措辞漂移 | W7 统一为「端点实时重算、`chunks.json` 由解析落盘」 |
| F2 | 实现状态口径三处矛盾：`parsing-pipeline.md:5/7`、`parsing-management.md:5` 称 Current，`api.md:100-101` 称「其余端点为规划契约」，`database-design.md:5` 与 `:33-36/193` 自相矛盾 | 设计过期 | W7 统一为「af_* 四表与读取端点已真机验证，解析执行面为部分验证」 |
| F3 | `adr/0004:26` 正文仍写 MinerU 8002（`:29-30` 已有勘误） | 设计过期 | W7 正文内联修正并保留勘误记录 |
| F4 | 页文件命名占位在设计里出现两种写法 | 设计过期 | W7 以 `artifacts.py` 实际格式为唯一事实源 |
| F5 | `src/axiom_flow/__init__.py:7` 仍列已删除的 `fallback/`；`tests/conftest.py:16` 引用不存在的 `axiom_flow.infrastructure.config`；`tests/integration`、`tests/smoke`、`tests/support` 等 5 个目录为空 | 残留 | W7 清理；W3/W8 顺带把 `tests/support`、`tests/integration`、`tests/smoke` 用真实测试填充 |
| F6 | `scripts/tmp/smoke-api.sh` 使用旧轮询路径 | 残留 | 随 W1 同步 |

## 契约条文草案（晋升输入）

以下条文为本轮新确立或修正的契约，先在本计划成文（ADR 0002），实现与真机验证通过后由 W7 晋升到
对应设计/架构文档。

1. **引擎调用三段化（→ `design/model-integration.md`）**：适配器对内固定为
   `_submit` → `_wait_done` → `_fetch_result` 三段；端点与载荷按 W0 端点终版表成文（MinerU 3.4.4：
   `POST /tasks` 202 取顶层 `task_id`；`GET /tasks/{id}` 顶层 `status`；`GET /tasks/{id}/result`
   202＝未就绪继续轮询、200＝取 `results[<file_stem>]`）。单页重试次数（1 次）与轮询瞬时失败容忍
   （连续 3 次）分列，互不吞并；轮询必须受 `deadline`（`AXIOM_OCR_TIMEOUT`）约束。
1b. **只设产物形态开关，不设解析调参（→ `design/model-integration.md`）**：请求必须显式发送
   `return_content_list=true`、`return_middle_json=true`（openapi 默认 false，否则 blocks 与页尺寸
   恒缺失），并按需 `return_md=true`；**不发送** `backend`、`effort`、`parse_method`、`lang_list`、
   `formula_enable`、`table_enable`、`image_analysis`、`server_url`，让容器环境配置生效（模型调参
   归 8900，ADR 0004）。不请求 `return_images`（页图由本仓库 ingest 自产）。
2. **响应解析防御式双兼容（→ `design/model-integration.md`）**：结果先取
   `results[<file_stem>]`（单文件提交时按唯一键回落），正文字段接受 `md_content` 与 `markdown`
   任一来源，块结构取 `content_list`、页尺寸取 `middle_json.pdf_info[*].page_size`，且三者均须兼容
   「JSON 字符串」与「已解析对象」两种载体；状态取值兼容顶层 `status` 与旧式 `data.state`；
   非 JSON 或非 2xx 一律归一为 `EngineError`，`JSONDecodeError` 等内部异常不得穿透到编排层。
3. **失败隔离与持久化不变式（→ `design/parsing-pipeline.md`）**：单页失败只标该页
   `af_pages.status=failed`；整本失败时此前已成功页的行与产物必须保留（**产物写先于页行提交**，
   保证「有页行必有产物」）；`progress` 按「距上次写入 ≥1s 或每 10 页」节流，任务终态必写一次。
4. **错误双载体分工（→ `architecture/api.md` 与 `architecture/database-design.md`）**：
   `af_parse_jobs.error` 为终态摘要（≤500 字符），`af_parse_jobs.progress.error` 为进行中失败页
   摘要（≤200 字符）；两者可同时存在，终态后 `error` 为准。
5. **健康检查两段语义（→ `architecture/api.md`）**：`POST /parse-jobs` 在参数校验后、落库前探活，
   模型服务不可用返回 **503 且不创建 job 行**；任务受理后的模型服务故障记为 job `failed`。只读
   产物端点（books/pages/manifest/chunks/get）不依赖模型服务。
6. **引擎分派（→ `design/model-integration.md`）**：解析实际引擎 = 请求体 `engine`（可选）优先，
   缺省回落 `AXIOM_OCR_ENGINE`；未在 `engines/factory.py` 注册的引擎名返回 400、非法字面值由
   schema 返回 422；**任何情况下不得回落到其他引擎**（不设自动兜底）。`af_parse_jobs.engine` 存
   解析后实际使用的引擎名。
7. **数据根边界（→ `design/parsing-pipeline.md`）**：`domain_id`、`course_id`、`book_id` 必须匹配
   安全路径段 pattern（拒绝 `/`、`..`、前导点、空串），违规返回 422；源 PDF 与页图落盘路径必须
   位于 `QED_DATA_ROOT` 之内（`resolve_source_pdf` 对绝对路径同样受限）；落盘前经
   `artifacts.ensure_within()` 做纵深防御校验。
8. **ingest 幂等与删除语义（→ `design/parsing-management.md`）**：重复 ingest 原子替换产物目录后
   必须回置 `parse_status=pending`、`pages_done=0` 并删除该书 `af_pages` 行；`af_parse_jobs` 历史
   行保留为审计。`DELETE /books/{id}?purge=true` 删产物目录、`af_pages` 与 `af_books` 行，保留
   `af_parse_jobs`。
9. **`af_pages.status` 表示法（→ `architecture/database-design.md`）**：设计三值中 `pending` 以
   「无行」表示，不预建占位行；续跑集合 = 无行页 ∪ `failed` 页。
10. **测试隔离例外（→ `standards/testing.md`，经 ADR 0005）**：集成层允许在 `QED_TEST_MYSQL=1`
    显式 opt-in 下使用同实例独立 schema `qed_test`，硬校验 `db_name`，缺 opt-in 时 skip 而非 fail。
11. **本地模型服务直连，不经系统代理（→ `design/model-integration.md`）**：引擎 HTTP 客户端必须以
    `trust_env=False` 构造。W0 实证：Windows 注册表代理（`127.0.0.1:7890`）对
    `http://127.0.0.1:5002` **不做豁免**（`proxy_bypass` 返回 0），默认 `trust_env=True` 会把回环
    流量交给代理，代理规则变化时返回 502/0 字节，表现为「端点不存在」的假象。此边界与 PLAN-002
    删除 `QED_PROXY` 配置键的裁决一致：本地模型流量恒为直连。

## 工作项

### W0 C 类冻结探测（先冻结，后动手）

外部模型服务实验：假设、样本、内容哈希、预算、采纳门槛先冻结（AGENTS.md「事实来源与决策」与
`docs/standards/doc-governance.md` 要求）。

| 要素 | 冻结内容 |
| --- | --- |
| 假设 H1 | 提交 `POST /file_parse`（multipart 字段名 `files`）响应**顶层**含 `task_id` |
| 假设 H2 | 状态端点为 `GET /tasks/{task_id}`，进度字段为 `data.state`，字面值含排队/运行/完成/失败四态 |
| 假设 H3 | 结果端点为 `GET /tasks/{task_id}/result` |
| 假设 H4 | 结果体含 `content_list`，且正文字段存在于 `markdown` / `md_content` 至少其一 |
| 样本 S-a | 合成 1 页 PNG 内容 PDF（走逐页路径），生成规则固定、SHA-256 留档 |
| 样本 S-b | 合成 5 页含公式/表格/页眉 PDF（走全本路径），生成规则固定、SHA-256 留档 |
| 样本 S-c | `linearalgebra-b01` 第 3 页（真机已知 failed）作为失败页回归对照，不重新生成 |
| 预算 | ≤25 个 HTTP 请求、≤3 次任务提交、≤20 分钟挂钟 |
| 门槛 M1 | `GET /openapi.json` 返回 200，路由清单留档为第一证据源 |
| 门槛 M2 | 观测到 ≥2 个不同状态字面值（证明状态机可映射，而非只见过一态） |
| 门槛 M3 | `/tasks/{id}/result` 响应全部顶层字段与关键嵌套字段留档 |
| 门槛 M4 | 至少一个正文字段可直接喂进 `mineru.py:39-100` 现有解析面（不改解析器即可出块） |

产出：`scripts/tmp/probe_mineru_api.py` + `data/probes/mineru-3.4.4-<日期>/`（逐份响应原文 +
SHA-256 清单；`data/` 已在 `.gitignore` 内，不新建 docs 子目录）。

失败处置：任一门槛不满足 → 在本计划登记「未探明」条目，W1 只改端点路径与错误语义，结果解析层
保持防御式双兼容（条文 2），不猜测字段形状。

#### W0 探测结论（2026-09-20 已冻结，留档 `data/probes/mineru-3.4.4-20260920/`）

门槛判定：**M1/M2/M3/M4 全部 PASS**（`summary.md`，逐次往返含 sha256）。实耗 26 请求（其中 12 次
为系统代理 502 空转，见 A6）、2 次提交、约 18 分钟——**请求数超出冻结的 ≤25**，超出的 1 次用于
补测终态字面值 `completed`，如实登记于此。

冻结假设核对（不推翻冻结清单，只登记结果）：**H1 部分成立**（顶层 `task_id` 成立，但提交端点是
`/tasks` 且返回 **202**，不是 `/file_parse` 200）；**H2 不成立**（状态端点 `/tasks/{id}` 存在，但
状态字段是顶层 `status`，不是 `data.state`）；**H3 成立**（`/tasks/{id}/result`，但未就绪时返回 202
而非 404）；**H4 部分成立**（`content_list` 存在但为 JSON 字符串；正文键是 `md_content`，
`markdown` 不存在；外层多一层 `results[<file_stem>]`）。

端点终版表（服务版本 `3.4.4`，`protocol_version 2`，路由清单仅 5 条）：

| 段 | 端点 | 请求 | 响应 |
| --- | --- | --- | --- |
| 提交 | `POST /tasks` | multipart，字段名 `files`（数组），可带表单开关 | **202** + `{task_id, status:"pending", backend, file_names, created_at, started_at, completed_at, error, status_url, result_url, queued_ahead, message}` |
| 状态 | `GET /tasks/{task_id}` | 路径参数 | **200** + 同上载荷，`status` 为状态字段 |
| 结果（未就绪） | `GET /tasks/{task_id}/result` | 路径参数 | **202** + 同上载荷 + `message:"Task result is not ready yet"`（**不是 404，也不是错误**） |
| 结果（就绪） | `GET /tasks/{task_id}/result` | 路径参数 | **200** + `{backend, version, results: {"<file_stem>": {md_content, content_list, middle_json}}}` |
| 探活 | `GET /health` | — | 200 + `{status,version,protocol_version,queued_tasks,processing_tasks,completed_tasks,failed_tasks,max_concurrent_requests,...}` |
| legacy | `GET /get_task_results/{task_id}` | — | **404** `{"detail":"Not Found"}` ← DEFECT-002 根因，已留档 |

状态机字面值（实测）：`pending → processing → completed`；**`failed` 未观测到**（记入未探明）。
`error` 是顶层字段（终态时为文本），不是状态值。

响应信封与现有代码的三处不符（A1/A4 的具体化）：

1. 载荷**顶层即状态对象**，现有代码读 `data.state` / `data.err_msg` → 恒为空；
2. 结果按 **`results[<file_stem>]`** 分组（文件名去扩展名），现有代码无此层；
3. 正文字段名为 **`md_content`**（`markdown` 不存在），现有代码读 `data.get("markdown")` → 恒空；
   `content_list` 与 `middle_json` 为 **JSON 字符串**（现有 `_load_json_field` 已兼容，保留）。

表单字段实测（openapi `Body_submit_parse_task_tasks_post`）：`return_md` 默认 **true**，
`return_content_list`、`return_middle_json`、`return_images`、`return_model_output`、
`response_format_zip`、`return_original_file`、`client_side_output_generation` 默认 **false**；
解析调参字段为 `backend`（默认 `hybrid-engine`）、`effort`、`parse_method`、`lang_list`、
`formula_enable`、`table_enable`、`image_analysis`、`server_url`；分页字段 `start_page_id`（默认 0）
与 `end_page_id`（默认 99999）存在（逐页路径的潜在优化，本轮不改语义）。

`content_list` 实测条目：`{type, text, text_level, bbox:[x0,y0,x1,y1], page_idx}`，`page_idx` 0 基、
5 页样本得 `page_idx ∈ {0..4}`；块类型观测到 `text`、`header`；`middle_json.pdf_info[*].page_size`
= `[595, 842]`（与 `mineru.py:50-59` 的读取路径一致，可复用）。

未探明（显式登记，W1 按防御式双兼容处理，W8 真机冒烟时补证）：

- `failed` 终态字面值与 `error` 文本形状（本轮无失败样本可造）。
- 真实教材页的 `interline_equation` / `table` 块（合成样本是纯文本 PDF，公式未被识别为公式块）。
- 486 页整本的 result 体积与 `results` 分组行为（合成样本仅 1/5 页；W8 用 `linearalgebra-b01`
  的 5 页区间补证，若形状再变则**追加**冻结清单，不推翻本表）。
- `return_images=true` 时的 `images` 载荷形态（本仓库页图由自有 ingest 产生，**不请求该开关**）。

结论对 A1 的裁定：设计文档的「同步 `/file_parse`」在本版本**不成立**（`/file_parse` 仍在路由表内，
但异步 `/tasks` 才是 202 + 轮询语义）；`/file_parse` 是否等价同步**不在本轮采用**，本仓库统一走
`/tasks` 三段。


### W1 引擎适配修正（DEFECT-002）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 1.1 | `mineru.py` 三段化：`_submit`（`POST /tasks`，接受 202）/ `_wait_done`（`GET /tasks/{id}`，终态 `completed`）/ `_fetch_result`（`GET /tasks/{id}/result`，**202＝未就绪继续等**、200＝取 `results[<file_stem>]`） | 消除 `get_task_results` 死路径与 `data.state` 误读 |
| 1.2 | 请求表单只送产物形态开关 `return_content_list` / `return_middle_json` / `return_md`，不送解析调参字段 | 修 A5（默认 false 导致 blocks 恒空） |
| 1.3 | 响应解析按条文 2 双兼容重写：`results` 分组、`md_content`、JSON 字符串/对象两态、状态顶层 `status` | 修 A1/A4 的三处信封不符 |
| 1.4 | 轮询瞬时失败容忍：连续 3 次 HTTP 层失败才判失败，deadline 前继续轮询 | 单次抖动不再整本 failed |
| 1.5 | 结果侧异常归一：非 2xx 与非 JSON → `EngineError`（附可诊断摘要，含 `error` 字段文本） | 修 A3 的一半（穿透源） |
| 1.6 | `httpx.Client(trust_env=False)` 直连回环（条文 11） | 修 A6（系统代理劫持） |
| 1.7 | `scripts/tmp/smoke-api.sh` 端点同步（F6） | 脚本与新协议一致 |

TDD 红灯：提交返回 202 且载荷顶层 `status:"pending"` 时能取到 `task_id`；`/result` 先 202 后 200 时
不失败且解析出页；轮询中途 500 一次后转 `completed` 不得失败、连续 3 次 500 必须失败；`completed`
后 200 响应非 JSON 必须得 `EngineError` 而非 `JSONDecodeError`；`md_content` 与 `content_list`
（字符串态）能产出页级 blocks；构造客户端时断言 `trust_env=False`。

#### W1 完成登记（2026-09-20）

1.1~1.7 全部落地（`engines/mineru.py` 重写、`tests/unit/test_engines.py` 按新契约重写 23 例、
`scripts/tmp/smoke-api.sh` 端点同步）。先红后绿：新契约测试在旧实现上 17 failed，改写后全绿。

实现要点与条文的对应：

| 条文 | 落点 |
| --- | --- |
| 1 三段化 | `_submit`（`POST /tasks`，接受 200/202）/ `_wait_done`（`GET /tasks/{id}`）/ `_fetch_result`（`GET /tasks/{id}/result`，202 继续等）；三段共享同一 `deadline`（提交后起算，`AXIOM_OCR_TIMEOUT`） |
| 1 轮询与单页重试分列 | 轮询瞬时失败阈值 `_TRANSIENT_FAILURE_LIMIT = 3`（传输层异常与非 2xx 合并计数，成功一次即清零）；单页重试 1 次仍在 `pipeline.py`，未改动 |
| 1b 只设形态开关 | `_OUTPUT_FLAGS = {return_md, return_content_list, return_middle_json}`；测试断言 multipart 内不含 `backend`/`effort`/`parse_method`/`lang_list`/`formula_enable`/`table_enable` |
| 2 双兼容 | `_status_of`（顶层 `status` ∪ `data.state`）、`_result_payload`（`results[<stem>]`，单文件时按唯一键回落）、`_markdown_of`（`md_content` ∪ `markdown`）、`_load_json_field` 保留（字符串/对象两态）；`_json_object` 把非 JSON 与非对象响应归一为 `EngineError`，`JSONDecodeError` 不再穿透（A3） |
| 11 直连 | `httpx.Client(timeout=…, trust_env=False)`，并有 `TestDirectConnection` 守护 |

未做（按台账归属后续工作项）：`base.py:14` 的 503 映射注释（W5）、`/file_parse` 同步语义文档修订
（W7）。`failed` 终态与整本 result 形状仍属
W0 未探明项，本次仅按防御式双兼容覆盖，真机证据随 W8 补。

新增风险登记（W0 未覆盖，W8 必测）：W0 的 S-a 样本是**一页 PDF**，而 `parse_page` 真机提交的是
**PNG 页图**（`filename=p0001.png`），该形态未被探测过。若容器拒收图片输入，症状是逐页重解析路径
失败而全本路径正常 → W8 冒烟必须先跑单页再跑 5 页，并把该形状追加进冻结清单（不推翻既有结论）。

### W2 页级持久化与进度落库（DEFECT-001）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 2.1 | `pipeline.py` 每页成功/失败后 `session.commit()`，产物写先于提交 | 「有页行必有产物」不变式 |
| 2.2 | `update_progress` 按 ≥1s 或每 10 页节流调用，收尾必写一次 | 实时进度查询成立 |
| 2.3 | `_fail` 路径先提交已累计页与 progress，再写终态 | 整本失败保留已解析页 |
| 2.4 | `persist_per_page` 参数保留（默认 True） | 性能异常时可退化的显式开关，不是静默兜底 |

TDD 红灯：引擎抛非 `EngineError` 异常时，前序页 `af_pages` 行必须已存活（在**文件 SQLite** 上以
独立读连接断言；内存 SQLite 单连接会共享未提交数据，守护不了本不变式）。

#### W2 完成登记（2026-09-20）

2.1~2.4 全部落地（`orchestrator/pipeline.py`）。红灯计划的一处修正：原写「内存 SQLite」，实测
`sqlite+pysqlite:///:memory:` 每连接是独立库、共享连接时又看得见未提交行，两个方向都无法复现
MySQL 的连接隔离，故改用**文件库 + 独立读 `Session`**（`tests/unit/test_orchestrator.py::_file_env`）。

| # | 实现 | 守护测试 |
| --- | --- | --- |
| 2.1 | `_handle`：`_write_page_artifacts` → `page_repo.upsert` → `_commit()`；`_mark_failed` 同构 | `test_pages_visible_to_other_connection_during_run`（引擎在第 n 页被调用前，读连接已见 n-1 页 `parsed`） |
| 2.2 | `_flush_progress(force)`：`≥1s` 或 `parsed % 10 == 0` 才覆写 `progress`；`mark_finished` 后 `_commit()` 保证终态收尾必写 | `test_progress_written_every_ten_pages`（12 页快跑循环内恰好 1 次写入，`parsed==10`；终态 `progress.parsed==12`） |
| 2.3 | `_fail` 前置 `session.commit()`（已解析页不被终态写入牵连），并写入进行中 `progress` | `test_non_engine_error_keeps_earlier_pages`（A3：第 2 页抛 `ValueError` → 第 1 页行与产物仍存活）、`test_whole_book_failure_keeps_parsed_pages` |
| 2.4 | `run_parse_job(..., persist_per_page: bool = True)`，False 时全量退回调用方提交 | `test_persist_per_page_off_defers_to_caller` |

失败页摘要（条文 4 的 `progress.error`）改为**随失败即时更新并 force 写入**，取代原先只在收尾拼装；
`≤200` 截断不变。门禁：`261 passed`、`ruff` 全绿。真机侧证据（运行中 `GET /parse-jobs/{id}` 递增、
kill 续跑）仍待 W8；DEFECT-001 按计划于 W3 关闭。

### W3 真实 MySQL 集成测试（依赖 ADR 0005 评审通过）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 3.1 | `docs/adr/0005-mysql-integration-test-isolation.md` 立项与登记 | 隔离边界变更的合法依据 |
| 3.2 | `tests/support/mysql.py`：`prepare_mysql_settings` + `isolated_mysql_store`（自建自删 `qed_test`、硬校验 `db_name`） | 兑现 `tests/conftest.py:56-70` 既有夹具引用 |
| 3.3 | `tests/integration/test_parse_persistence.py`：双 `sessionmaker` 断言页级可见与 `progress.parsed` 单调增 | 补上「共享 Session 掩盖未提交」的测试盲区 |
| 3.4 | `docs/standards/testing.md` 两处联动（分层表 integration 行、编写约定例外句） | 标准与实现一致 |

验证：默认门禁下该用例 skip 且整体绿；`QED_TEST_MYSQL=1` 下真跑绿。

### W4 引擎显式分派（DEFECT-003，修 D1）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 4.1 | 新增 `engines/factory.py`：`ENGINE_NAMES` 与 `model-integration.md:40` 三值同源，`build_engine(name, settings)` | 分派入口唯一 |
| 4.2 | `main.py` 去掉硬编码 `MinerUEngine`；`payload.engine` 优先、缺省回落 `settings.ocr_engine` | 配置真正生效 |
| 4.3 | 未实现引擎（paddleocr-vl、qwen-vl）→ 提交期 400；非法字面 → 422（`schemas.py:362` 收 `Literal`） | 显式报错，**绝不回落 mineru** |
| 4.4 | `JobRunner.submit(..., engine=...)` 显式传参；`af_parse_jobs.engine` 存解析后实际引擎名 | 任务行与真实执行一致 |

TDD 红灯：`AXIOM_OCR_ENGINE=paddleocr-vl` 时提交必须 400 而非静默用 MinerU；未设
`AXIOM_OCR_ENGINE` 时默认 mineru 仍可用。

### W5 提交期探活 503（修 B1、B2）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 5.1 | `POST /parse-jobs` 在 `main.py:385-389` 校验之后、`:392` 落库之前调用 `engine.health()` | 不可用时 503 |
| 5.2 | 503 文案复用 `pipeline.py:151` 的模型服务不可用措辞；不创建 job 行 | 两段语义分离成文（条文 5） |
| 5.3 | `engines/base.py:14` 注释与实现对齐 | 消除虚称 |
| 5.4 | 契约用例：假引擎 `health() → False` 时 `POST /parse-jobs` 得 503，且全部只读端点仍 200 | 守护「只读不依赖模型」 |

### W6 数据边界与 ingest/purge 幂等（修 E1、E2、E4）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 6.1 | `BookSyncItem` 的 `book_id`/`domain_id`/`course_id` 加安全段 pattern，`file_path` 加 `max_length=512` 且禁前导 `/` | 越界入参 422 |
| 6.2 | `artifacts.ensure_within(root, target)` 并在 `book_dir_for`、`resolve_source_pdf`、产物读写路径统一使用（绝对路径亦限数据根） | 纵深防御（条文 7） |
| 6.3 | 重复 ingest 后回置 `parse_status=pending, pages_done=0`；新增 `PageRepository.reset_for_book` | 幂等语义成立（条文 8） |
| 6.4 | `?purge=true` 追加删 `af_pages`，保留 `af_parse_jobs` | 删除语义成文 |

契约用例：ingest → 解析 → 再 ingest 后，`parse_status=pending`、`pages_done=0`、旧 job 行仍在、
`GET /books/{id}/pages/3` 返回 404。

#### W4~W6 完成登记（2026-09-20）

先红后绿：新增用例在旧实现上 `25 failed / 24 passed`（`test_api_v1_contract.py` +
`test_ingest.py`，含 `JobRunner.submit()` 缺 `engine` 实参的 `TypeError`），实现落地后全绿；
门禁 285 passed + ruff 全绿。

| # | 实现 | 守护测试 |
| --- | --- | --- |
| 4.1 | 新增 `engines/factory.py`：`ENGINE_NAMES`（三槽位，与 `model-integration.md` 同源）/`IMPLEMENTED_ENGINES`、`resolve_engine_name`、`build_engine(name, settings)`、`EngineSelectionError` | `tests/unit/test_engine_factory.py`（8 例：优先级/回落/未实现/未知/空值/槽位清单） |
| 4.2 | `api/main.py` 去掉硬编码 `MinerUEngine`，`engine_factory` 改为 `Callable[[str], ParseEngine]`，缺省装配 `build_engine(name, settings)` | `test_omitted_engine_uses_configured_default`、`test_payload_engine_wins_over_config` |
| 4.3 | 未实现槽位 → 400（文案含「尚未实现」）；非法字面 → 422（`ParseJobCreate.engine` 收 `Literal`，缺省 `None` 表示回落配置） | `test_unimplemented_config_engine_returns_400_not_silent_mineru`（并断言**零 job 行**）、`test_unimplemented_payload_engine_returns_400`、`test_unknown_engine_literal_returns_422`、`test_create_job_rejects_unregistered_engine` |
| 4.4 | `JobRunner.submit(..., engine=<已解析名>)`，`af_parse_jobs.engine` 存实际执行引擎名 | 同 4.2（读回 job 行 `engine == "mineru"`） |
| 5.1/5.2 | 提交期门槛顺序固定：书目/产物校验 → 引擎名解析 → `_probe_model_service()` → 才落 job 行；503 文案与执行期共用 `pipeline.MODEL_UNAVAILABLE` 常量 | `test_model_down_returns_503_and_creates_no_job_row`、`test_read_only_endpoints_unaffected_by_model_down`（只读端点 + 页图仍 200，`pages/1` 因未解析 404） |
| 5.3 | `engines/base.py` `EngineError` docstring 改写为「执行期 → 单页失败/任务 failed；提交期探活另有 503 两道映射」，消除虚称 | 代码审查项，由 5.1/5.2 用例间接守护 |
| 6.1 | `schemas.py` 新增 `SAFE_SEGMENT_PATTERN`/`OPTIONAL_SEGMENT_PATTERN`/`RELATIVE_PATH_PATTERN`（pydantic-core 不支持 look-around，故以 `\w` 起始字符类天然排除 `.`/`/`，并显式 `^…$` 锚定）；`BookSyncItem` 三 ID + `file_path`（`max_length=512`、禁前导 `/`）套用 | `test_sync_rejects_unsafe_path_segments`（`../evil`、`a/b`、`.hidden`、`/etc/passwd` 一律 422） |
| 6.2 | `ingest/artifacts.py`：`ensure_safe_segment()`（单段校验）+ `ensure_within()`（数据根闸），`book_dir_for` 双闸（`..` 即使在数据根内也拒绝）、`resolve_source_pdf` 绝对路径同样受限；`orchestrator/pipeline._store_images` 改用同一 `ensure_within`（消除重复实现）；路径参数 `book_id` 收 `SafeBookId` | `test_book_dir_for_rejects_traversal_segment`、`test_resolve_source_pdf_rejects_absolute_path_outside_root`、`test_resolve_source_pdf_rejects_relative_escape`、`test_resolve_source_pdf_accepts_within_root`、`test_parse_job_rejects_unsafe_book_id` |
| 6.3 | `ingest` 端点在 `update_ingest` 后回置 `parse_status=pending, pages_done=0` + `PageRepository.reset_for_book`（批量删页行）；`af_block_edits` **不清**（人工判定是用户资产，块下标重映射归 REQ-080 版本划分一并处理） | `test_reingest_resets_parse_state_and_keeps_job_history`（旧 job 行仍可查、`af_pages` 空、页产物 404） |
| 6.4 | `DELETE /books/{id}` 无论是否 `purge` 都删 `af_pages`（无 af_books 行时页行即孤儿），`purge` 只额外删产物目录；`af_parse_jobs` 保留为审计 | `test_purge_deletes_page_rows`（af_pages 空 + job 行 1 条） |

计划外但同批落地（避免两处事实源）：`orchestrator/__init__.py` 导出 `MODEL_UNAVAILABLE`；
`engines/__init__.py` 模块清单补 `factory`。`default_data_dir` 仍保留（删除属 7.3）。

### W7 文档自洽与死引用清理（F1~F5 + 条文晋升准备）

| # | 工作项 | 产出 |
| --- | --- | --- |
| 7.1 | F2 三处状态口径统一；F3 ADR 0004 正文 8002 内联修正；F1/F4 措辞统一 | 文档内部一致 |
| 7.2 | D2 `AXIOM_PADDLEOCR_URL` 自相矛盾统一为「当前无该键，启用 paddleocr-vl 时按 ADR 流程新增」 | 消除矛盾 |
| 7.3 | 删 `importer.default_data_dir`，`tests/unit/test_ingest.py` 改测 `load_settings`（E3） | 数据根单一事实源 |
| 7.4 | 清 `__init__.py:7` 的 `fallback/` 残留、`conftest.py:16` 死引用、空目录（F5） | 无死引用 |
| 7.5 | 本计划条文 1~11 晋升 `design/model-integration.md`、`design/parsing-pipeline.md`、`design/parsing-management.md`、`architecture/api.md`、`architecture/database-design.md`（`architecture/` 两文档属大修改，由本计划 + todo 承载，与代码分开 commit） | 待评审设计转正（ADR 0002） |

#### W7 完成登记（2026-09-20）

| # | 落地 | 证据 / 守护 |
| --- | --- | --- |
| 7.1a | F2 状态口径统一：`database-design.md` 表清单四行由「**规划**（ARCH-002/003/007）」改为「**已实现**（真机已建表）」并标注各自收尾位置；「规划边界 / ARCH-001 / 根仓库 REQ-027 回执」旧表述改为「实现边界（迁移 `20260917_0001` 建表）+ 后续 DDL（REQ-080 拆 PLAN-004）」；`api.md` 无残留「规划契约」表述 | `test_architecture_documents.py` 绿 |
| 7.1b | F3：`adr/0004:26` 正文 `MinerU 8002` → `5002`，原 `:29-30` 勘误记录保留不动 | `test_adr_governance.py` 绿 |
| 7.1c | F4：页文件名占位统一为 `p<NNNN>`（`NNNN` = 页码 1 基四位零填充），并在 `parsing-pipeline.md` 产物树后成文为唯一格式；`api.md`、`operations.md`、`schemas.py`、`importer.py` 文档串同步 | 全库 `pXXXX` 零残留 |
| 7.2 | D2：`model-integration.md` 统一为「当前无 `AXIOM_PADDLEOCR_URL` 键，启用 paddleocr-vl 时按 ADR 流程新增」 | 文档内自洽 |
| 7.3 | E3：删 `ingest/importer.default_data_dir`（连带未用 `os` 导入与包导出），`tests/unit/test_ingest.py` 删除 `TestDefaultDataDir`，数据根改由 `tests/unit/test_config.py`（`AXIOM_FLOW_DATA_DIR` 键 + 退役键忽略用例）承载 | 基线 285 → **283 passed**（净减 2 例即被删的重复事实源用例），`ruff check` 全绿 |
| 7.4 | F5：`src/axiom_flow/__init__.py` 重写为当前模块清单 + 退役清单（不再列 `fallback/`）；`tests/conftest.py` 死引用改为 `axiom_flow.config.Settings`；`find -type d -empty` 现为 0（`tests/integration`、`tests/smoke`、`tests/support` 仅剩 `__pycache__`，由 W3/W8 用真实测试填充） | 无死引用 |
| 7.5 | 条文 1、1b、2、6、11 → `model-integration.md`（三段协议表 + 产物形态开关 + 双兼容解析 + 直连 `trust_env=False` + `factory.py` 分派）；3、7 → `parsing-pipeline.md`（错误处理表 + 节流段 + 数据根边界）；8 → `parsing-management.md`（ingest/DELETE 行）；4、5 → `api.md`（进度语义 + 错误表 503/failed 分段 + 只读端点不依赖模型）；4、9 → `database-design.md`（新增「任务表语义」「页表语义」） | 逐条 grep 命中，见本轮 7.5 校验 |
| 7.5-例外 | **条文 10（`standards/testing.md` 隔离例外）本轮不晋升**：用户裁决要求 ADR 0005 评审通过后方可改 `standards/`，而 ADR 0005 仍为 Draft → W3 与条文 10 一并挂起 | `docs/standards/testing.md` 未改动（git status 无该项） |

W7 期间发现并登记（不在本轮静默修复）：

1. `parsing-pipeline.md` 产物树声明的 `tmp/axiom-flow/<job_id>/` 暂存目录**代码未实现**（引擎暂存
   不落数据根）。已在该树行内标注「未实现：当前不落数据根」，是否实现随 REQ-080 产物版本划分
   （PLAN-004）一并裁决。
2. `af_parse_jobs.engine` 列的 DDL 默认值 `'mineru'` 在 W4 后成为死默认值（写入路径恒显式传解析后
   引擎名）。清理需改列 → 属 DDL，本轮无迁移，随 PLAN-004 的 `af_parse_jobs` 变更一并处理。
   **2026-09-21 已销账（PLAN-004 P2）**：迁移 `20260920_0002` 以
   `batch_alter_table + alter_column("engine", server_default=None)` DROP 掉该兜底（SQLite 重建表 /
   MySQL DROP DEFAULT 同一份脚本），并把 `ParseJobRow.engine` 改为**必填**；守护用例
   `test_upgrade_0002_drops_dead_engine_default` + `test_job_row_requires_explicit_engine`。真机侧随
   C 窗口生效；`downgrade()` **有意不恢复**该死默认值（恢复即重新打开静默回落入口，ADR 0004）。
3. 仓库根 `app/`（v1 遗留，未跟踪、仅含 `__pycache__/*.pyc`、无源文件）删除命令被自动模式分类器
   阻止，**保留待用户授权**；不影响门禁与包结构（`src/axiom_flow` 为唯一包）。

### W8 真机冒烟与跨项目回执

| # | 工作项 | 产出 |
| --- | --- | --- |
| 8.1 | `tests/smoke/` 首个用例（单页 + 5 页全本），默认跳过策略与 W3 同精神 | **2026-09-21 完成**：`tests/smoke/test_live_mineru_chain.py`（`QED_SMOKE_LIVE=1` 开启；默认门禁 `2 skipped`），真机 `2 passed in 7.08s`，5002 `completed_tasks` 48→50 证明确有提交 |
| 8.2 | 运行中 `GET /api/v1/parse-jobs/{id}` 进度递增留证；整本失败时页行保留取证 | S3 证据（**L4/L5 已达成**，见「收尾执行登记」） |
| 8.3 | kill 服务后重启，按 `af_pages` 续跑未完成页 | `parsing-pipeline.md:190` 验收 #5（**L6 已达成**） |
| 8.4 | REQ-075（MinerU 直连配置同步）端点面回执稿；REQ-001 复核材料 | **2026-09-21 完成**：见「跨项目回执稿（W8-g）」一节 |

## 台账联动

| 任务 | 联动 |
| --- | --- |
| DEFECT-002 | W1 起转 In Progress；W8 冒烟绿后关闭。**2026-09-20 真机证据齐**（L2 第 3 页 + L3 整本 + L7 读取面）。**2026-09-21 关闭**：8.1 冒烟用例固化并真机 `2 passed`，判据（关闭条件修订「DEFECT-002 关闭 = L2 + L3」）全达成，行已原子移入 `completed.md` |
| DEFECT-001 | W2/W3 关闭；同步回改 `project-status.md`「断点续跑基础」措辞。**2026-09-20 用户裁决：不随本轮真机证据关闭**（L4/L5/L6 已证，但 D5/W3-b 双 Session 回归网仍受 ADR 0005 评审阻塞） |
| DEFECT-003 | W4/W5/W6 覆盖其全部子项，W6 后关闭（**2026-09-20 已关闭**，见 `completed.md`） |
| DEFECT-004 | 本轮新登（bbox 归一化 0–1000 被误当 PDF 点，含 PNG 路径 `page_size` 错配）；前置 ARCH-005/006 联调，需先补契约条文再按 TDD 修。**2026-09-21 关闭**：契约条文晋升两份 Accepted 设计 + 9 例单测先红后绿 + 真机端到端锚点复验（IoU 0.9314 / 偏移 ≤7px）+ 冒烟对齐判据（反转即红），详「三轮开工登记」，行已原子移入 `completed.md` |
| DEFECT-005 | 本轮新登（`header`/`footer` 字面值未入 `_TYPE_MAP`，块被静默丢弃 + 下标漂移）；修复需成文「未知类型如何处理」（丢弃需可见计数，不静默）。**2026-09-21 现状**：仍是 ARCH-005/006 与 ARCH-009 的唯一归一化前置（坐标空间面已随 DEFECT-004 解除）。**2026-09-21 四轮关闭**：可见丢弃条文补入 Accepted 设计并加「映射表变更的追溯影响」条；`_TYPE_MAP` 新旧字面值并存 + `dropped_block_count`/`dropped_types` 计数 + `index` 连续编号 + 整页回退条件收紧；新增 11 例单元（两轮红 `6 failed`/`1 failed`）与冒烟零丢弃对账断言，详「四轮开工登记」，行已原子移入 `completed.md` |
| DEFECT-006 | 本轮新登（`pages` 无上界/存在性校验 + `completed` 掩盖部分失败 + 书级 `parse_status` 语义混用），属契约裁决项。**2026-09-21 裁决 A1 + B1**：任务结局三态（`completed`/`partial`/`failed`，与激活门槛同源）+ `failed_pages` 结构化 + 书级 `parse_status`（进度）与 `last_parse_result`（结局）分列，成文为 [ADR 0007](../../../adr/0007-parse-status-semantics.md)，**实施承载改为 PLAN-006**（同日实施并关闭、按 Delete 删壳，正文见锚点 `9034a19`，S1~S7 判据入 `completed.md`）；页码越界子项已随 R0-3 交付 |
| DEFECT-007 | 本轮新登（被中断任务永久 `running`，无 reaper/心跳），修复选项需 ADR 级裁决。**2026-09-21 裁决 C1**：启动期把遗留 `queued`/`running` 归一为 `interrupted`（单实例部署为其正当性前提，写入运维指南），同样由 [ADR 0007](../../../adr/0007-parse-status-semantics.md) 承载语义、PLAN-006 承载实施（同上，已关闭删壳） |
| ARCH-003 / ARCH-016 | W8 真机冒烟绿后关闭。**2026-09-21 复核后仍开放**：8.1 已绿，但本行判据被本轮新缺陷接续——ARCH-003 剩 DEFECT-006/007 语义成文与修复（需用户裁决/ADR），ARCH-016 剩 **DEFECT-005**（DEFECT-004 已于 2026-09-21 关闭）与代表书类型分布取证（ARCH-009）。**2026-09-21 四轮后**：DEFECT-005 亦关闭，ARCH-016 只剩 ARCH-009 类型分布取证 + 真机零丢弃复跑（待 5002 在线）；ARCH-003 仍剩 DEFECT-006/007 语义成文与修复（需用户裁决/ADR）。**2026-09-21 主线审计后 ARCH-016 关闭**（零丢弃复跑达成；类型分布取证与 ARCH-009 判据逐字重复，移交之，`title`/`table` 缺口随之登记），ARCH-003 仍开放。**2026-09-21 十二轮后**：DEFECT-006/007 随 PLAN-006 实施并关闭（[ADR 0007](../../../adr/0007-parse-status-semantics.md) 决定 1~7 落地，门禁 405 passed），ARCH-003 的**裁决依赖解除**，只剩真机冒烟复跑这一条件（连同 `20260921_0001` apply 与孤儿行 `5f43593a12cc` 归一），故本计划对它的接续关系不变 |
| REQ-075 | 端点面随 W8 回执后关闭。**2026-09-21 关闭**：配置面（Step 1/2-A + G5 三段走查）与端点面（DEFECT-002 真机证据 + 冒烟用例）两项齐备，回执稿见「跨项目回执稿（W8-g）」；按协作标准，根仓库自读本表登记 |
| REQ-001 | 复核材料随 W8 就绪后发出回执。**2026-09-21 改判承载行**（根侧 `REQ-001` 号已被 2026-08 端口迁移占用）⇒ 指向其 `PLAN-044`/`ARCH-020-D`。**2026-09-22 关闭**：根侧 PLAN-044 行已登记读取复核材料（锚点 `95e1d37`），判据达成，详「根侧回执已到：六个跨项目行关闭」 |
| REQ-080 | 维持 Open，指向 PLAN-004（本计划不动 DDL、不改产物布局）。**2026-09-21**：坐标空间前置解除，版本目录内块坐标可信；回执随 PLAN-004 收口发出。**2026-09-22 关闭**：根侧四条确认全部接受 ⇒ REQ-080 与 PLAN-004 同批入 `completed.md` |
| REQ-081 | 2026-09-21 接收（QED-Engine 报告前端叠框失真）→ 判定与 DEFECT-004 同一缺陷，随其关闭发出回执（见「跨项目回执稿（REQ-081）」）；本方 todo 行保持 In Progress，等根仓库 §13 复测。受害页真机重解析属 D 类，待用户批准。**2026-09-22 关闭**：根侧 §13 双面复测通过（数据面零夹取 + 视觉面用户确认叠框对齐），受害页重解析已随 R1 窗口 D-2 在版本布局上完成 |
| REQ-083 / 084 / 085 | REQ-083 回执见同名小节（2026-09-21 发出）；REQ-084/085 为本计划发出的两条出站请求。**2026-09-22 三行同批关闭**：根侧撤销改名并 sync 复通（REQ-083）、BUGFIX-010 采纳建议语义（REQ-084）、以确认约定路径关闭并写入其设计约束 9/10（REQ-085），逐行判据见「根侧回执已到」节 |
| PLAN-002 | Step 3 表加注「由 PLAN-003 承接，本表仅作历史索引」，关闭条件改为标准 6/7 由 PLAN-003 关闭即判达成、标准 8 随 W8。**2026-09-21 主线审计后 PLAN-002 关闭**（8 条标准按其**判据自身证据**逐条判定，不再绑定本计划——本计划受 D3 与 DEFECT-006/007 裁决长期开放；标准 7 的就地真机抽样取证见其「关闭判定」节），壳按 Retain 移至 `docs/history/plans/2026-09/` |
| ARCH-004 | chunks 在 W2 后随页级持久化成立；`chunks.json` 归属版本目录的约定随 REQ-080 在 PLAN-004 处理 |

## 验证与验收

- **每步 TDD 先红后绿**，红灯用例写进对应 W 工作项并在提交信息中引用 DEFECT/台账 ID。
- **隔离铁律不破**：探测与冒烟只用临时目录与合成样本；默认测试不写 `qed` 业务库（W3 只写
  `qed_test`）；`tests/conftest.py:43-53` 回环守卫保持不动；TLS 校验默认开启。
- **单元/契约**：`D:/software/anaconda3/envs/QED_env/python.exe -m pytest tests -q`（基线 245
  passed 之上只增不减）+ `-m ruff check src tests scripts`。
- **文档门禁**：按触发项跑 `tests/contract/test_design_documents.py`、
  `test_architecture_documents.py`、`test_code_document_mapping.py`、`test_plan_governance.py`、
  `test_tracker_governance.py`、`test_test_suite_governance.py`。注意 todo 的 PLAN 行状态须与本
  文件 `状态：` 逐字一致，且每个表格行必须物理单行。
- **真机验收**：S2（5 页小样 `parsed`）、S3（运行中进度递增 + 失败保留页 + kill 续跑）、
  S4（四组违约各有测试覆盖）。
- **MySQL 侧**：默认 `pytest tests -q` 下 integration/smoke skip 且全绿；
  `QED_TEST_MYSQL=1 pytest tests/integration -q` 真跑绿。

## 回滚

- 本轮**无 alembic 迁移**（REQ-080 已拆出），全部代码改动可 `git revert` 逐 commit 回滚。
- W1/W2 若真机失败：引擎侧回退到「只改端点路径、不改错误语义与持久化」的最小集，页级 commit 由
  `persist_per_page=False` 退回原 flush-only 语义（显式开关，非静默降级）。
- W3 若 `qed_test` schema 建立失败：删除 `tests/support/mysql.py` 与集成用例，ADR 0005 转
  Superseded，DEFECT-001 退回 Open 并记录阻塞证据。
- `architecture/` 两文档改动属大修改路径，由本计划 + todo 承载，且与代码**分开 commit**，文档回滚
  不影响代码。
- 探测留档在 `data/probes/`（gitignore 内），回滚只需删目录。**副作用登记**：`data/` 整体被忽略 →
  留档不进版本库，结论文本必须写入本计划正文（已执行）；是否豁免 `data/probes/` 待用户裁决。

## 收尾执行设计（真机打通 + 数据库与数据根收口，2026-09-20 裁决后重排）

### 本部分的三个收口面

| 面 | 收口判据（一句话） | 明确不含 |
| --- | --- | --- |
| C1 解析真机打通 | 单页（PNG 提交形状）+ 5 页合成全本 + 真书单页回归三条链路真机成立；运行中可见 `progress.parsed` 递增；kill 重启按 `af_pages` 续跑 | 486 页整本、KaTeX ≥90% 抽查与代表书验收（ARCH-009）、批量 12 本（D 类） |
| C2 数据库存储收口 | `af_*` 四表的写入面（sync/ingest/job 行/progress/页行/edit）与读取面（9 个只读端点）在真机 MySQL 上逐项取证；W3 双 Session 测试给出可重复的提交边界证明 | 版本列与主键迁移（PLAN-004）、共享 `qed_*` 读写、`qed_test` 之外的库 |
| C3 QED_DATA_ROOT 收口 | `parsed/`（含 domain 省略层级现状）、`raw/` 只读、`tmp/` 恒不存在、`data/` 定位、`.env` 三段走查，全部有成文判据与取证 | 存量迁移、旧产物清理、`versions/` 布局（PLAN-004） |

### 用户裁决（2026-09-20，本轮三项）

1. **W3 保留（路 B）**：先评审 [ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md)
   （Draft → Accepted），接受后才写真 MySQL 双 Session 测试并联动 `standards/testing.md`。
   真机 MySQL 不替代它（真机是手工一次性证据，测试是回归网）。
2. **真机小样止**：单页 + 5 页 + 真书单页回归；全量 486 页与质量抽查归 ARCH-009。
3. **`tmp/axiom-flow/<job_id>/` 改判**：不是「设计有文、代码未实现」，而是**设计表述错误**——
   引擎中间产物只在内存，不写数据根。按条文 12 修正表述，零代码（撤销 PLAN-003「W7 完成登记」发现 1）。

### 条文 12（新增契约，随 W8-f 晋升 `design/parsing-pipeline.md`）

模型调用**不向数据根写中间产物**：请求载荷（PDF 字节 / 页图字节）与响应信封只在进程内存流转；
文档内嵌图片按 `pages/<相对路径>` 作为**终态产物**落盘。因此 `<data_root>/tmp/axiom-flow/`
**恒不应存在**，存在即缺陷（比根侧 `storage-conventions.md:30-31`「tmp 不保留终态文件」更强：
不产生）。`parsing-pipeline.md` 产物树中的该行为错误表述，W8-f 一并改判。

### C1 真机取证清单（每条都要留档，不许口头通过）

| # | 动作 | 期望 | 证据落档 | 失败首查 |
| --- | --- | --- | --- | --- |
| L1 | 双探活：`GET 5002/health` 与 `GET 8902/api/v1/health`（8902 经 `scripts/axiom_flow_service.py start --wait`） | 均 200 | `data/probes/live-bringup-20260920/health.json` | 5002 不通 → 根仓库 `scripts/image-model/` 启停（本仓库只连接）；8902 不通 → `logs/qed-axiom-serve.log` |
| L2 | 单页：对已 ingest 的真书提交 `pages:[3]`（linearalgebra-b01 已知 failed 页，DEFECT-002 的 404 根因回归对照） | 202 受理 → 终态 `completed`；`af_pages(book,3)=parsed`；`pages/p0003.blocks.json` 与 `p0003.md` 存在且 blocks 非空 | 响应 + SQL 输出留档 | 404 类 → 端点终版表（条文 1）；PNG 提交形状属 A4 未探明项，现场补齐并追加冻结记录 |
| L3 | 5 页合成小样（W0 样本，含公式/表格/页眉）走全本路径（`pages` 缺省） | `parse_document` 一次上传成功并按页分发；5 页产物齐；`chunks.json` 与 `manifest.json` 重算生成 | 样本 SHA-256 + 结果留档 | 页数缺/页序乱 → 结果解析面（条文 2 双兼容）与 `content_list` 页号改写 |
| L4 | 运行中观测：执行期间对 `GET /api/v1/parse-jobs/{id}` 连续采样 ≥3 次 | `progress.parsed` 单调不减且至少前进一次；`status=running`；`progress.error` ≤200 字符 | 逐份采样留档 | 恒 0 → 节流条件（≥1s 或每 10 页）或 `update_progress` 未提交，由 W3-b 双 Session 测试精确定位 |
| L5 | 失败注入：把 `QED_OCR_MODEL_URL` 指向不通端口并重启，提交单页任务 | 任务 `failed`，`error` ≤500 摘要；**此前已解析页的页行与产物保留**（条文 3） | SQL + 目录清单留档 | 页行丢失 → `jobs.py` 异常回滚吞行（A3 回归） |
| L6 | kill 续跑：跑到一半 `stop`（含 `stopped (forced)`）→ 重启 → 同书重提同一页集 | 已完成页不重复解析（以 `parsed_at` 与文件 mtime 为据），未完成页补齐；「无行 ∪ failed」成立 | 前后 SQL 快照留档 | 全部重跑 → 续跑集合判定（条文 9）或页行未提交（条文 3） |
| L7 | 只读端点全面：`books`、`books/{id}`、`pages/{no}`、`pages/{no}/image`、`manifest`、`chunks`、`parse-jobs/{id}` | 全 200、结构与 `schemas.py` 一致；`image_url` 字面量仍为 `/api/v1/books/{id}/pages/{no}/image`（根仓库 8900 依赖此不变量） | 响应留档 | 5xx → `api/main.py` 读取路径；404 → 条文 9 的 pending 表示法 |
| L8 | 提交期门槛复验：在 L5 的断连状态下提交任务 | `POST /parse-jobs` 返 503 且**不创建 job 行**；全部只读端点仍 200 | 留档 | 落了 job 行 → 探活位置在落库之后（违条文 5） |

### C2 数据库存储取证清单

| # | 项 | 判据 |
| --- | --- | --- |
| D1 | 写入面逐端点 | sync 幂等（重复 sync 行数不增）；ingest 回写 `ingest_status/page_count/sha256`；`af_parse_jobs.engine` = 解析后实际引擎名；`progress` 覆写生效；页行 upsert（parsed/failed）；edit 同键覆盖 |
| D2 | 真机 MySQL 方言 | JSON 列（`progress`/`quality`/`authors`/`corrected_bbox`）读写往返一致；`error`（500）与 `progress.error`（200）截断在真库不报错；DATETIME 全为 UTC（`utc_now`） |
| D3 | 删除与审计 | `DELETE /books/{id}`（含 `?purge=true`）后 `af_parse_jobs` 历史行保留、`af_pages` 与产物目录删净（版本化前的现行语义，不动） |
| D4 | 凭据与库边界 | 只读共享 `qed_*`、只写 `af_*`；`QED_DB_PASSWORD` 不出现在任何响应、日志或异常文本（grep 取证） |
| D5 | 提交边界回归网 | W3-b 双 Session 测试在 `QED_TEST_MYSQL=1` 下真跑绿（默认门禁 skip） |

### W3 设计（路 B：先评审，后实施）

1. **前置门槛**：ADR 0005 由 Draft → Accepted（用户评审）。未接受前**不动** `docs/standards/testing.md`，
   **不写**任何触库测试。
2. `tests/support/mysql.py`：兑现 `conftest.py` 既有夹具引用；读 `QED_DB_*` 构造 `qed_test` DSN；
   连上后**硬校验** `db_name == "qed_test"`，不符直接 `pytest.exit`（不是 skip——防止误连运行库）；
   fixture 自建 schema → `alembic upgrade head` → 用例结束 `DROP SCHEMA`；缺 `QED_TEST_MYSQL=1` 时整体 skip。
3. `tests/integration/test_parse_persistence.py`：两个独立 `sessionmaker`（写/读）+ fake engine，
   断言 ①写 session 未 commit 时读 session 看不到页行 ②每页 commit 后读 session 立即可见 ③
   `progress.parsed` 采样序列单调不减 ④`_fail` 路径下前序页行存活（A3 回归）。
4. `standards/testing.md` 两处联动（仅在 ADR 接受后）：分层表 integration 行补「mock 必跑 /
   真 MySQL 需 `QED_TEST_MYSQL=1` 显式 opt-in、默认 skip」；编写约定 `:42-43` 补 ADR 0005 例外句。
5. 门禁不变：默认 `pytest tests -q` 仍不连库、不连网（`conftest.py` 回环守卫不动）。

### C3 数据根收口清单（全部零代码：判定 + 取证 + 文档口径）

| # | 项 | 现状 | 收口动作 |
| --- | --- | --- | --- |
| G1 | `parsed/<domain>/<course>/<book>/` | 真机为 `parsed/linear_algebra/linearalgebra-b01/`（domain 为空 → 省略层级，符合根侧约定） | `parsing-pipeline.md` 成文「domain 空则省略层级」；取证真实目录树 |
| G2 | `raw/` 只读 | `resolve_source_pdf` 限数据根、不复制 | L2 前后各取一次源 PDF 的 sha256 与 mtime，证明未被改写 |
| G3 | `tmp/axiom-flow/` | 代码不写它 | 按条文 12 改判 + 真机跑完断言该目录不存在（存在即缺陷） |
| G4 | `data/` | 仓库内 gitignore，现仅存 `data/probes/` 留档 | `operations.md` 把「过渡期工作目录（落点迁移后退役）」改为「探针与合成样本留档区，不作数据根候选」 |
| G5 | 配置链路 | `env > 仓库 .env > 根 .env` 三段走查 | 真机记录三键（`QED_DATA_ROOT`/`QED_DB_*`/`QED_OCR_MODEL_URL`）实际生效来源；一致即收口 |
| G6 | 死默认值 `af_parse_jobs.engine='mineru'` | 属 DDL | 明确移交 PLAN-004，本部分不动表结构 |

### 工作项重排（取代上文 W3 / W8 的粗粒度划分）

| # | 内容 | 产出 | 依赖 |
| --- | --- | --- | --- |
| W3-a | ADR 0005 提交评审（Draft → Accepted / Rejected） | 评审结论；若否决则 W3 取消，D5 判据并入 L4/L6 真机证据，并在 `completed.md` 显式登记「无 MySQL 级回归网」这一代价 | 用户 |
| W3-b | `tests/support/mysql.py` + `tests/integration/test_parse_persistence.py` | 默认门禁 skip 绿；`QED_TEST_MYSQL=1` 真跑绿 | W3-a |
| W3-c | `standards/testing.md` 两处联动 + 条文 10 晋升 | 标准与测试实现一致 | W3-a、W3-b |
| W8-a | 真机前置：5002 在线 + 8902 起 + 目标书已 sync/ingest | L1 证据 | 用户给窗口 |
| W8-b | L2 真书单页（含未探明的 PNG 提交形状） | DEFECT-002 关闭证据之一 | W8-a |
| W8-c | L3 五页合成全本 | 整本路径真机成立 | W8-b |
| W8-d | L4 运行中进度 + L8 提交期 503 复验 | DEFECT-001 关闭证据之一 | W8-c |
| W8-e | L5 失败注入 + L6 kill 续跑 + L7 只读端点全面 | 条文 3/5/9 真机成立 | W8-d |
| W8-f | C2（D1~D4）+ C3（G1~G5）取证；条文 12 与 G4 口径晋升文档 | 数据根与 DB 收口报告 | W8-e |
| W8-g | REQ-075 端点面回执稿 + REQ-001 复核材料 + PLAN-004 解冻条件写明 | 跨项目收口 | W8-f |

### 关闭条件修订（覆盖下文「关闭与归档」中的旧表述）

- C1：L1~L8 全绿（小样止即算打通）；A4 未探明项由 L2 现场补齐并追加到 W0 冻结清单（日期化登记，
  不推翻既有结论）。
- C2：D1~D4 取证齐；D5 随 W3-a 结论二选一（接受 → 测试绿；否决 → 判据并入真机证据 + 代价登记）。
- C3：G1~G5 有成文口径与取证，G6 明确移交 PLAN-004。
- 台账：DEFECT-001 关闭 = W3（按 W3-a 结论）+ L4 + L5 + L6；DEFECT-002 关闭 = L2 + L3；
  ARCH-003 / ARCH-016 随之关闭；REQ-075 端点面回执发出后关闭；REQ-001 复核材料随 W8-g 就绪。
- **不属于**本部分：全量 486 页与代表书质量验收（ARCH-009，保持 Open）、版本布局（PLAN-004）。

## 收尾执行登记（2026-09-20 真机取证结果）

留档目录 `data/probes/live-bringup-20260920/`（`data/` 在 `.gitignore` 内，故本正文是结论事实源；
逐条判据与原始响应见该目录 `summary.md`）。取证脚本在 `scripts/tmp/`（只读取证：真机 HTTP + 仓库内
临时数据根与 SQLite，未把业务数据写入共享 `qed` 库以外的库，也未向真实数据根写文件）。

### C1 真机取证清单结果

| # | 结论 | 关键证据 |
| --- | --- | --- |
| L1 | PASS（5002 healthy / 8902 ok 双探活） | `summary.md` 首段 |
| L2 | **PASS**（真书 `linearalgebra-b01` 第 3 页 job `2a87818fdb7f` completed，页行 `parsed`，blocks 非空）——DEFECT-002 的 404 根因回归对照成立 | `L2_submit.json`、`L2_poll*.json`、`L2_page3.json` |
| L3 | **PASS**（5 页合成小样走 `pages=null` 整本路径：`parse_document` 一次上传、5/5 `parsed`、18 个产物文件 + `chunks.json`(2 块) + `manifest.json`(17 项)）；数据根与库改用仓库内临时根（写真实数据根被工作区边界拦截，属正确行为） | `L3_report.json` |
| L4 | **PASS**（两批任务运行中 `progress.parsed` 单调不减并前进：0→1→2→3、9→16；终态与产物文件数一致） | `L4_polls.txt`、`L4b_polls.txt` |
| L5 | **PASS**（`pages=[40,999]`：第 40 页 `parsed`、第 999 页 `failed 页图缺失`，任务继续收尾，`error` 摘要「失败页：999」） | `L5_polls.txt` |
| L6 | **PASS**（跑到中途 `stop` 后重启：真机 MySQL 已解析 21 页行与产物保留；按「无行 ∪ failed」提交补齐集 `def52f2987d2` → 16/16 completed；`parsed_at` 证明前序页未重跑） | `L6_db_after_kill.txt`、`L6_resume_polls.txt`、`L6_final_state.txt` |
| L7 | **PASS**（9 个只读端点全可用、`image_url` 字面量不变量成立、未解析页 404） | `L7_read_endpoints.txt` |
| L8 | **PASS**（模型 URL 不可达 → 503 中文提示 + `af_parse_jobs` 行数不变；临时实例已回收） | `L8_503.json`、`L8_no_row_evidence.txt` |

### 真机新发现（本轮取证暴露、原计划未覆盖）

| # | 发现 | 判据/证据 | 处置 |
| --- | --- | --- | --- |
| N1 | `job.status=completed` **掩盖部分失败**（有失败页仍 `completed`） | `L5_polls.txt` | 成文缺口：需裁决「部分成功」表达（新增状态字面值 or 只看 `error`/页行），登记 [DEFECT-006](../../../trackers/todo.md) |
| N2 | 被中断任务**永久停留 `running`**（kill 后 `5f43593a12cc` 仍 `running parsed 14/30`），无 reaper/心跳超时；续跑实际只依赖 `af_pages`，与 job 行无关 | `L9_final_jobs.txt` | 同上：语义需成文 + 修复选项（启动期把遗留 `running` 归 `failed` or 心跳列），登记 [DEFECT-007](../../../trackers/todo.md) |
| N3 | `pages` **无上界/存在性校验**：486 页书接受 `pages=[40,999]`，并在真机 `af_pages` 写出幽灵行（第 999 页 `failed`） | `L5_submit.json` + `L6_final_state.txt` | 校验缺口，登记 [DEFECT-006](../../../trackers/todo.md)；幽灵行清理属 D 类运行库写操作，**待用户批准**（不自行删） |
| N4 | 书级 `parse_status` 与「本轮任务完成」共用一个字段：整本任务下正确（L3 `completed 5/5`），逐页子集任务把它置 `failed`（真书 38/486） | `L9_book_state.txt`、`L3_report.json` | 语义需成文（`parse_status` = 全书覆盖 or 最近任务结果？），并入 N1 裁决 |
| N5 | **`content_list[].bbox` 是逐轴 0–1000 归一化坐标，不是 PDF 点**（锚点实验：放置 y=780pt → 实测 910；归一化预期 926；x=500pt → 实测 836，预期 840） | `L3d_coord_space.json` | 阻断对照与块编辑 → 登记 [DEFECT-004](../../../trackers/todo.md) |
| N6 | 逐页（PNG）路径叠加坐标系错配：PNG 输入的 `middle_json.pdf_info[0].page_size=[446,631]`（像素按 200DPI 折算的标称点），与归一化 bbox 组不出自洽映射 → 真书第 3 页产物出现 `x0=1240,x1=1241` 的 1px 宽块与贴边钳制 | `L3c_page0003.json`、`L7_page3_bbox.txt` | 并入 DEFECT-004 修复范围 |
| N7 | 真机类型字面值是 `header`/`footer`，`_TYPE_MAP` 只有 `page_header`/`page_footer` → 页眉页脚块被 `block_from_raw` 静默丢弃（样本第 1 页 8 项 → 7 块） | `L3d_coord_space.json`、`L3b_raw_bbox.json` | 登记 [DEFECT-005](../../../trackers/completed.md)（**2026-09-21 四轮修复并关闭**，见「四轮开工登记」）；连带块 `index` 下标漂移风险（与 PLAN-004 ③ 相关） |
| N8 | `title`/`equation`/`table` 等语义类型在合成小样上**不出现**（40 项只有 `text`/`header`），类型映射整体可用性未在真机取证 | `L3b_raw_bbox.json` | 不据此判定可用；真实分布取证归 ARCH-009（代表书） |
| N9 | L3/L5/L6 的整本与失败注入证据必须在**版本布局前**取，PLAN-004 的 L10 前提措辞（「解析从未真机成功」）已被本轮证伪 | 本节 | 已回改 PLAN-004 与 `project-status.md` 措辞 |
| N10 | 2026-09-21 只读配置走查发现：根 `.env:20` 的 `QED_OCR_MODEL_URL` 行带**行内 `#` 注释**，而 `config._env_file_values` 只跳过整行注释、不剥离行内注释 → 本仓库 `.env` 若缺该键，生效值会连注释一起读入并成为坏 URL（当前因本仓库 `.env:17` 优先生效而未触发） | `data/probes/live-bringup-20260920/D4_G5_config_credential.txt`（逐键三层来源表） | 登记 [DEFECT-008](../../../trackers/todo.md)（B 类一行修复 + 用例）；根仓库 `.env` 属只读范围，**不由本仓库改动** |

### 收尾二轮（2026-09-21：冒烟固化 + C2/C3 可自查残留）

| # | 项 | 结论 | 证据 |
| --- | --- | --- | --- |
| 8.1 | `tests/smoke/` 真机冒烟固化 | **PASS**：`test_smoke_single_page_png_shape`（逐页 PNG 提交形状 + 归一化产出 + bbox 落在页图像素内）与 `test_smoke_full_document_five_pages`（`pages=None` 整本 → 5/5 `parsed`、job `completed`、`progress {5,5}`、`af_pages` 5 行、`p000N.md/.blocks.json` + `chunks.json` + `manifest.json` 齐套、数据根 `tmp/` 不存在） | 本机 `QED_SMOKE_LIVE=1 pytest tests/smoke -q` → `2 passed in 7.08s`；默认门禁 `-rs` → `2 skipped`；5002 `/health` `completed_tasks` 48 → 50 |
| D4 | 凭据与库边界 | **PASS**：`repr(Settings)` 不含密码（`db_password` 声明 `repr=False`）；`logs/*.log` 命中 0；五个只读端点响应命中 0；ORM 层与迁移只出现 `af_*` 四表、无裸 DML | `data/probes/live-bringup-20260920/D4_G5_config_credential.txt`（脚本 `scripts/tmp/live_check_config_credentials.py`，全程只读、密码仅以遮蔽标记与命中计数出现） |
| G5 | 配置三段生效来源 | **PASS**：`QED_DATA_ROOT`/`QED_DB_*`/`QED_OCR_MODEL_URL`/`AXIOM_*` 逐键走查——真实环境变量均未设，生效层一律为**本仓库 `.env`**，与根 `.env` 值一致（`data_dir=D:\coding\QED-Engine\dataset`、`db_name=qed`、`ocr_model_url=http://127.0.0.1:5002`）；同时暴露行内注释解析缺口 → N10 | 同上 |
| D3 | 删除与审计（`DELETE /books/{id}?purge=true` 后 `af_parse_jobs` 保留、`af_pages` 与产物删净） | **未做**：属真机共享库写 + 产物目录删除（D 类），需用户单独批准；本轮不对 `linearalgebra-b01` 做删除演练 | 待收尾三轮（可用合成探针书，仍属共享库写） |

### 三轮开工登记（2026-09-21：DEFECT-004 坐标空间修复）

变更定级：B 类确定性实现（缺陷修复，契约已于 2026-09-20 晋升 Accepted 设计）；TDD 先红后绿。

| 步骤 | 内容 | 结论 |
| --- | --- | --- |
| 契约成文 | `parsing-pipeline.md`「坐标空间契约」条 + `model-integration.md`「原始块坐标空间与类型字面值」条：`PageResult.bbox_space ∈ {pdf_points, normalized_1000, pixels}`，**只有 `pdf_points` 空间允许以 `page_size` 为缩放基准**；`page_size` 在 PNG 输入下只留档不换算；缺 `bbox` 的块直接铺满页图不再二次映射 | 完成（含 code-map 备注与 DesignRef 同步） |
| 先红 | `tests/unit/test_normalize.py::TestBBoxCoordinateSpace`（逐轴 `bbox/1000*image_size`、忽略引擎 `page_size`、真机锚点落位、`pixels` 只夹取、`pdf_points` 为默认、`normalize_page` 按声明分派、缺 bbox 铺满页）+ `tests/unit/test_engines.py::TestBBoxSpaceDeclaration`（整本与逐页两条路径均声明 `normalized_1000`） | 首轮 `8 failed, 43 passed`（红成立） |
| 实现 | `engines/base.py` 新增 `BboxSpace` 与 `PageResult.bbox_space`；`engines/mineru.py` 以 `_BBOX_SPACE="normalized_1000"` 常量声明（含 WHY 注释）并贯穿两条提交路径；`normalize/blocks.py` 的 `scale_bbox`/`block_from_raw`/`normalize_page` 按空间分派 | 全绿 |
| 真机端到端复验 | `scripts/tmp/live_verify_bbox_fix.py`：合成 A4（595x842pt）内把 5 行文本钉在已知点坐标 → 真实 5002 + 真实 `run_parse_job`（整本路径、SQLite + 仓库内探针数据根）→ 读 `p0001.blocks.json` 与 PDF 原生矩形（点 × dpi/72）逐块比对 | **PASS**：`job=completed 1/1`、`af_pages` 1 行 `parsed`、5/5 块 **平均 IoU 0.9314 / 最大绝对偏移 7px**；同一判据下旧实现复算 **IoU 0.0114**，且 ECHO/DELTA 两块被夹取到 `x1=1240`（页图右缘）——与根仓库 REQ-081 报告的 `x1=1241` 症状同源。留档 `data/probes/live-bbox-fix-20260921/E1_anchor_after_fix.json` |
| 回归网固化 | `tests/smoke/test_live_mineru_chain.py` 增「标题块与 PDF 原生文字矩形 IoU ≥ 0.5」对齐判据（逐页 PNG 路径 + 整本路径 5 页逐页） | 修复后 `2 passed in 11.88s`；把 `_BBOX_SPACE` 反转为 `pdf_points` 即 `2 failed`（IoU 0.000 / 0.157，`parsed=[267,206,1240,267]`）→ 判据可独立检出坐标空间错配，非恒真 |
| 门禁 | `pytest tests -q` → **292 passed, 2 skipped**（基线 283 → +9）；`pytest tests/contract -q` → 82 passed；`ruff check src tests` 全绿 | 达成 |

**未做项（需用户批准，不自行执行）**：`linearalgebra-b01` 受害页的真机重解析（写真实 `dataset/parsed/` + 共享
`qed`，D 类）。既有落盘产物是修复前的错误坐标，换算修复不回溯历史产物 → REQ-081 的根侧 §13 复测需在重解析后才有意义。

### 跨项目回执稿（W8-g，2026-09-21）

按[跨项目协作流程](../../../standards/cross-project-collaboration.md)，回执只落本仓库 todo/计划，根仓库自读登记，
本仓库不写根仓库文件。

**REQ-075 —— MinerU 直连配置同步 + 新异步 API 端点对齐（回执 QED-Engine，本方关闭）**

- 配置面：`QED_OCR_MODEL_URL` 为唯一模型地址键（`QED_MINERU_URL`、`AXIOM_PADDLEOCR_URL` 已删除且无别名），
  生效端口 5002；三段走查证明本仓库 `.env` 与根 `.env` 值一致（G5）。
- 端点面（MinerU 3.4.4 实测）：`POST /tasks`（multipart 字段 `files`，202，任务号在**顶层** `task_id`）→
  `GET /tasks/{task_id}`（状态取**顶层** `status`：`pending→processing→completed`）→
  `GET /tasks/{task_id}/result`（202＝未就绪、200＝载荷 `results[<file_stem>]`，键 `md_content` /
  `content_list` / `middle_json`）。必须显式 `return_content_list=true`、`return_middle_json=true`；
  legacy `/file_parse` 与 `/get_task_results/{task_id}` 在 3.4.4 均 404。
- 证据：W0 冻结表（`data/probes/mineru-3.4.4-20260920/`）、真机 L2/L3/L7、`tests/smoke/test_live_mineru_chain.py`。
- 需根仓库注意（不要求根侧改动）：根 `.env:20` 的值后带行内 `#` 注释，本仓库解析器不剥离行内注释，
  该写法只在本仓库 `.env` 缺键时才会暴露；本方以 DEFECT-008 自行修复解析器。

**REQ-001 —— 8900 数据域适配 af_* 新契约（复核材料，本方仍开放等待根仓库回执）**

- 端点面事实源：[api.md](../../../architecture/api.md)；真机只读面 L7 九端点全可用，`image_url` 字面量不变量成立，
  未解析页返回 404。
- 根仓库 2026-09-20「联调最小打通轮」自修的四处断裂（review→edit 门面、sync `file_path`、parse-jobs
  `engine`/`id`/`progress`、`page_no`/`image_url` 补写）与本仓库契约一致，`tests/contract/test_api_v1_contract.py`
  全绿可作对照。
- 本方延后项：① ~~块坐标在 DEFECT-004 修复前不可用于叠框~~ **2026-09-21 已解除**（见下节 REQ-081 回执）；
  ② REQ-080/PLAN-004 版本布局落地后 `versions/<job_id>` 与生效指针需再回执一次。

**REQ-081 —— MinerU 解析块 bbox 坐标失真（回执 QED-Engine，本方已修复，待根侧 §13 复测）**

根仓库 2026-09-20 的请求与本方 DEFECT-004 是**同一缺陷的两个视角**（根侧看前端叠框，本方看适配层定因），
根侧怀疑的两条路径均成立。回答根仓库提出的判定问题：

- **`content_list[].bbox` 的实际坐标空间**：相对页框、**逐轴归一化到 0–1000**（既不是 PDF 点也不是页图像素），
  且整本（PDF）与逐页（PNG）两条提交路径同一空间。锚点法实证：A4（595x842pt）内把文本钉在已知点坐标，
  放置 `y=780pt` → 引擎回 `910`（点空间应约 780，归一化预期 926）；`x=500pt` → 回 `836`（归一化预期 840）。
- **`middle_json.pdf_info[].page_size` 与 bbox 是否错配**：成立。PDF 输入时它是真实 PDF 点，**PNG 输入时它是
  引擎按 200 DPI 折算的标称点**（1241x1755 页图 → `[446, 631]`，与页图像素比约 1:2.78）。旧实现把归一化值
  当点、再按 `image_size/page_size` 缩放，等效 x 轴过放 **1.68 倍**、y 轴 **1.19 倍** → 超出页图的分量被
  夹取，因此贵方观察到「全部段落块 `x1` 恒等于 1241（图像右缘精确值）」与 1–3px 病态窄块——**这是夹取
  残留，不是引擎返回的真实宽度**。
- **修复后的契约（对贵方接口零变化）**：`bbox` 语义不变仍是 `pages/p<NNNN>.png` 的页图像素 `[x0,y0,x1,y1]`，
  只是换算不再依赖引擎回传的 `page_size`；适配器改为显式声明坐标空间（`PageResult.bbox_space`），归一化按
  空间分派。前端叠框代码无需改动。
- **复验证据**：真机端到端锚点复验 5/5 块平均 IoU **0.9314**、最大偏移 **7px**（旧实现同一判据 0.0114 且两块
  贴边夹取）；`tests/smoke/test_live_mineru_chain.py` 已把「块与 PDF 原生文字对齐（IoU ≥ 0.5）」固化为判据，
  反转坐标空间即红。留档 `data/probes/live-bbox-fix-20260921/E1_anchor_after_fix.json`。
- **待办与限制（请贵方注意）**：① 修复不回溯历史产物——贵方 §13 复测前需对 `linearalgebra-b01` 受害页
  **重新解析**（本方侧属 D 类真机写，待用户批准；批准前本方不自行重跑，若贵方急需可协调时间窗）。
  **2026-09-21 时点更新（裁决 5）**：重解析改到 PLAN-004 版本布局 + 真机 `af_*` 迁移窗口之后**单次执行**
  （前 20 页，含贵方报告的第 7 页），理由是若先在旧扁平布局重解析，迁移后这些页行会被回填成 `legacy`
  哨兵而不再被端点读到——真机证据一步即作废。故贵方 §13 项 3/5 的复测时点 = 本方「版本布局落地 →
  迁移窗口 → 单次重解析」三步完成后（本方届时发二次回执）；在此之前拿到的仍是修复前的错误坐标。
  另**订正贵方前提**：REQ-081 证据列「b01 当前仅 2 页产物」过时，本方收尾轮取证为 **38/486 页**
  （第 3~40 页 `parsed`，见 `data/probes/live-bringup-20260920/summary.md`）；坐标空间误判是**全局换算**错误、
  与页无关，故受害面是全部已解析页而非单页，复测样本请按页区间取；
  ② ~~块**类型**映射仍有独立缺陷（DEFECT-005：真机 `header`/`footer` 字面值未覆盖 → 该两类块被丢弃，
  页内块数与 `index` 会少一项）~~ **2026-09-21 四轮已解除**：`header`/`footer` 已入映射，未覆盖字面值改为
  **可见丢弃**并落进 `quality.dropped_block_count`/`dropped_types`，同页块数与 `index` 不再出现静默漂移；
  贵方按 `index` 定位的对照视图请改用**重解析后**的产物（旧产物是修复前下标语义）；
  ③ 本方 `bbox` 与真实文字仍有约 7px 的系统性左偏（引擎自身检测框特性，非坐标空间问题），叠框建议保留
  少量外扩容差。

### 四轮开工登记（2026-09-21：DEFECT-005 类型字面值与可见丢弃）

| # | 工作项 | 交付 | 验证 |
| --- | --- | --- | --- |
| 1 | 契约先行（按「未知类型如何处理」条文补实现口径） | [parsing-pipeline.md](../../../design/parsing-pipeline.md)「引擎类型字面值」条改写为已实现语义（新旧字面值并存、`normalize_page` 逐条对账、必需字段缺失同样计入、不做占位补块），质量信号节补「可见丢弃计数」；新增「映射表变更的追溯影响」条：`af_block_edits` 以 `(book_id, page_no, block_index)` 为唯一键，`index` 是归一化后下标 → **映射表版本与产物必须成对**，历史产物不回溯修正 | 契约测试（设计文档守护）全绿 |
| 2 | 真机证据复核 | 从留档 `data/probes/live-bringup-20260920/L3d_coord_space.json` 直接读出同一次响应的 `type="header"`（TOPLEFT 锚点）与 `type="footer"`（BOTTOMRIGHT 锚点）；`L3b_raw_bbox.json` 40 项 = `text` 35 + `header` 5（样本第 1 页 8 项 → 旧码归一 7 块） | 判据来自留档，不依赖本次网络 |
| 3 | 映射与计数实现 | `_TYPE_MAP` 增加 `header`/`footer`，并保留 `page_header`/`page_footer`（旧版字面值并存以免版本回退）；`compute_quality(..., dropped_literals=...)` 产出 `dropped_block_count` + `dropped_types`（去重升序）；`normalize_page` 逐条对账，`index` 只在存活块上连续编号 | 新增 11 例单元（`test_normalize.py::TestVisibleDrop` 8 例 + `TestNormalizePage` 对账 1 例 + `test_schemas.py` 默认值与下界 1 例 + 既有回退例补断言），第一轮红 `6 failed` → 全绿 |
| 3b | 整页回退条件收紧（顺带检出的过度兜底） | 原实现只要「归一后无块」就合成整页 paragraph，会把映射缺口伪装成正文块；收紧为**只在引擎未回任何结构化条目**时回退，并成文对账不变式 `原始条目数 = 存活块数 + dropped_block_count` | 第二轮红 `1 failed`（条目全未归一时仍合成块）→ 绿 |
| 4 | 回归网（避免「测试通过但真机仍丢块」） | `tests/smoke/test_live_mineru_chain.py` 逐页路径新增对账断言：`len(raw) == len(blocks) + dropped_block_count`，并要求真机样本 `dropped_block_count == 0`（出现未覆盖字面值即红并打印 `dropped_types`）；同一不变式另有默认门禁单元例（`test_accounting_invariant_on_mixed_page`） | 默认门禁 `2 skipped`；真机复跑待 MinerU 5002 在线（本轮 `curl /health` 连接拒绝，属外部依赖，登记为待办复跑而非关闭条件缺项） |
| 5 | 门禁 | 全量 `pytest tests -q` → **303 passed, 2 skipped**（基线 292 → +11）；`tests/contract` **82 passed**；`ruff check src tests` 全绿 | 本地门禁为本仓库唯一门禁 |

**未做项**：① `title`/`equation`/`table` 的真实类型分布取证仍归 ARCH-009 —— 留档中出现过的原始字面值
**只有** `text`/`header`/`footer`（合成小样 40 项 = `text` 35 + `header` 5、坐标锚点样本含 `footer`、
真书第 3 页 `L3c_page0003.json` = `text` 8），故这三类的映射正确性目前只有单元层构造用例支撑，无真机证据；
② 真机零丢弃对账复跑待 5002 起服务；
③ 修复前落盘的 `parsed/` 产物块 `index` 与丢弃口径均为旧语义，仍需重解析（与 DEFECT-004 同一批，待批准）。

### 收口面判定（本轮）

- **C1 真机打通**：L1~L8 **全绿（小样止）** → 关闭条件达成；新缺陷 N1~N7 属「打通后暴露的设计缺口」，
  不撤销 C1 结论，但 DEFECT-004/005 必须前置于 ARCH-005/006（对照与块编辑）联调。
- **C2 数据库收口**：D1（写入面：页行 upsert / `progress` 覆写 / `af_parse_jobs.engine` 实际引擎名 /
  ingest 回写）与 D2（JSON 列与 DATETIME 在真 MySQL 往返）随 L2~L6 取证成立；**D4 于 2026-09-21 取证成立**
  （凭据不外泄 + 只有 `af_*` 四表可触达）。**仅剩 D3**（删除与审计，真机共享库写 + 产物删除，待用户批准）
  与 **D5/W3-b/W3-c**（受 [ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md)
  评审阻塞）——按用户裁决「DEFECT-001 不落库（不关闭）」，本计划不因 L4/L5/L6 通过而关闭 DEFECT-001。
- **C3 数据根收口**：G2（`raw/` 只读，L2 前后 sha256 一致）与 G3（`tmp/axiom-flow/` 恒不存在，条文 12）
  成立；G1/G3/G4 口径已于 2026-09-20 晋升成文（`parsing-pipeline.md`：domain 空则省略层级 + 不写数据根
  `tmp/`；`operations.md`：`data/` 重定义为探针留档区且不作数据根候选）；**G5 于 2026-09-21 三段走查成立**
  （生效层=本仓库 `.env`，三键值与根 `.env` 一致）。G6 维持移交 PLAN-004 → **C3 全部达成，本收口面关闭**。
- **W8 收口**：8.1/8.2/8.3/8.4 **全部完成**（8.1 冒烟用例真机绿；8.4 与 W8-g 回执稿见上节）。
  PLAN-003 剩余仅为**外部与裁决依赖**：D3（删除演练需批准）、D5/W3（ADR 0005 评审）、REQ-001（等根仓库回执）、
  以及本轮新登 DEFECT-004~008 的修复。
- **三轮开工后（2026-09-21，DEFECT-004）**：C1 的归一化正确性前置解除一半——坐标空间已修复并真机复验，
  ARCH-005/006 的叠框与 `corrected_bbox` 不再被坐标失真阻塞（仍被 DEFECT-005 的 `index` 漂移阻塞）。
  本计划关闭依赖不变：D3（需批准）、D5/W3（ADR 0005）、REQ-001/REQ-081（等根仓库回执）、DEFECT-005/006/007/008。
- **四轮开工后（2026-09-21，DEFECT-005）**：归一化正确性前置**全部解除**（坐标空间 + 类型映射与可见丢弃），
  C1 的已知设计缺口只剩「未成文语义」三项（DEFECT-006/007 + 幽灵行待批清理）。本计划关闭依赖收窄为：
  D3（需批准）、D5/W3（ADR 0005 评审）、REQ-001/REQ-081（等根仓库回执）、DEFECT-006/007/008，
  以及真机侧两项复跑（b01 受害页重解析、`QED_SMOKE_LIVE=1` 零丢弃对账）——前者待用户批准，后者待 5002 在线。
- **五轮复核后（2026-09-21，PLAN-004 解除过度阻塞与真机窗口合并）**：本计划剩余的真机待批项与 PLAN-004 的
  真机取证**合并为同一批准窗口**（B→C→D：本地全绿 → 单窗口 `af_*` 备份 + `alembic upgrade 0002` +
  清 `af_pages` 第 999 页幽灵行 + 同窗口发布 → 在版本布局上单次重解析 b01 前 20 页）。因此：
  ① D3 删除演练与 W8「必须在版本布局上取证」自此**同源执行**，不再排两次真机窗；
  ② 本计划关闭依赖中的「b01 受害页重解析」一项改由 PLAN-004 的 D 步承载（REQ-081 回执的时点随之更新，
  见上节 caveat ①）；③ 幽灵行清理从「独立待批」并入 C 窗口。PLAN-004 的开发面（P2~P6）经审核不依赖本计划
  的 C2，已于同日转 In Progress（裁决依据见其「已裁决事项」第 5 条）。
- **六轮复核后（2026-09-21，PLAN-004 实施完成）**：PLAN-004 的 P2~P7 全部落地并本地闭环（门禁
  303 → **352 passed, 2 skipped**，契约 82 → 94 + ruff 全绿），「W7 发现 2」（`af_parse_jobs.engine`
  死默认值）随其迁移 `20260920_0002` **销账**（见上节该项登记）。对本计划的影响：① W8 冒烟用例
  `tests/smoke/test_live_mineru_chain.py` 的产物断言已改到 `versions/<job_id>/` 并加「激活」判据，
  故 D 窗口的复跑**不再需要改代码**，只需 5002 在线；② 本计划关闭依赖的**外部项清单未变**（D3、
  D5/W3=ADR 0005、REQ-001/081 等根回执、DEFECT-006/007/008、幽灵行清理并入 C 窗口）。
- **七轮复核后（2026-09-21，ADR 0005 接受 + W3 落地）**：C2 的 **D5 达成**（见下节「承载 DEFECT-001」），
  DEFECT-001 与 REQ-075 之后本计划的第三个硬依赖关闭；外部项清单缩短为 **D3 真机删除演练**（随 R1
  窗口）、REQ-001/081/083/084 根侧回执、DEFECT-006/007 裁决。门禁基线 **366 passed, 2 skipped →
  366 passed, 10 skipped**（新增 `tests/integration/` 8 例在默认门禁下 skip）。

- **九轮（2026-09-21，跨项目回执批次发出）**：本计划末的「跨项目回执发出（2026-09-21，R1 窗口后）」一节
  把 W8-g 的待发材料转为**已发出**（锚点 commit `95e1d37`，门禁 `376 passed, 10 skipped` / 契约 96 / `QED_TEST_MYSQL=1`
  集成 8 passed）：REQ-080 实施回执 + 待根确认四条、REQ-081 **二次回执**（根侧 §13 复测前提成立，
  产物 = 生效版本 `86ab56ae2b6c` 第 1~20 页）、REQ-083 回执，以及 REQ-084/085 两条出站转达稿。
  对本计划的影响：**回执发出这一项依赖自此销账**，外部项清单收窄为 ① D3 真机删除演练（需用户批准）、
  ② 根侧回应（REQ-001 登记复核材料、REQ-080 四条确认、REQ-081 §13 复测、REQ-083 撤销改名、
  REQ-084/085 登记）、③ DEFECT-006/007 语义裁决。本计划仍**不因九轮关闭**（①③ 未达成）。

## 承载 DEFECT-001（W3：ADR 0005 接受 + MySQL 双 Session 提交边界回归网）

**目的**：DEFECT-001 的原始成因是「共享 Session 看得见未提交数据」，因此 SQLite 单测结构性地抓不到
它。W3 补一层在真实事务隔离下断言提交边界的回归网（ADR 0005 提供合法依据，PLAN-003「W3 设计」节）。

**关闭条件**：ADR 0005 转 Accepted，且 `tests/integration/test_parse_persistence.py` 在
`QED_TEST_MYSQL=1` 下真跑绿、默认门禁下整层 skip 绿。

| # | 工作项 | 状态 | 结果与证据 |
| --- | --- | --- | --- |
| W3-a | ADR 0005 评审（Draft → Accepted） | **完成 2026-09-21** | 用户确认接受，并**改判决定 3**：由「`qed_test` 整库自建自删」改为「复用 `qed_test`、夹具只拥有本方 `af_*` 四表 + `alembic_version`，绝不 `DROP DATABASE`」——因为刚核验到根仓库 2026-09-21 已把 `qed_test` 灌成 `qed` 的 13 表镜像，按草案执行会删掉别人的资产。决定 2 同时补「库名不符即 `pytest.exit` 终止会话」。修订写入 [ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md)「评审与实施记录」节 |
| W3-b | `tests/support/mysql.py` + `tests/integration/test_parse_persistence.py` | **完成 2026-09-21** | 先红（`ModuleNotFoundError: tests.support.mysql`，7 errors）后绿：opt-in 真跑 **8 passed**，默认门禁 **8 skipped**。断言面 ①未 commit 的页行对他连接不可见、commit 后可见 ②第 n 页调用引擎前前 n-1 页已提交（`parsed_snapshots == [0,1,2]`）③`progress.parsed` 采样单调且在运行中确实前进过 ④非 `EngineError` 穿透与整本中途失败时前序页行仍在、`active_job_id` 不激活 ⑤**反向判据** `test_deferred_commits_are_genuinely_invisible_mid_run`：`persist_per_page=False` 时独立连接什么都看不到，证明正向断言不恒真 ⑥边界自证：engine URL 与 `SELECT DATABASE()` 均为 `qed_test`、Alembic 落在 `20260920_0002` |
| W3-c | `standards/testing.md` 两处联动 + 指南 | **完成 2026-09-21** | 分层表 integration 行改为「mock 面门禁必跑 / MySQL 面需 `QED_TEST_MYSQL=1`，默认 skip」；编写约定的隔离铁律补**唯一例外**句（回环 + `qed_test` 自有表白名单 + 数据根 `tmp_path` + 缺开关 skip）；`guides/development.md` 增该命令块与「执行时机：改 `orchestrator/`、`db/` 持久化语义后必须跑一次」；code-map 补 `tests/support/` 行与 orchestrator 的测试列 |

**边界核验（跑完后只读查库）**：`qed` 业务库四表与 `alembic_version` 未变（17/10/39/1 行，
`20260917_0001`）；`qed_test` 内 `qt_*`/`qed_*` 8 张镜像表保留，其 `af_*` 四表与 `alembic_version`
如裁决所料被夹具收尾删除（根侧可重建，事实源仍是 `qed`）。

**本节不声称**：`qed` 已迁移（真机 `alembic_version` 仍是 `20260917_0001`，`20260920_0002` 属 R1 的
C 窗口）；DEFECT-006/007 的状态语义裁决；任何真机解析结果。


## 承载 REQ-083 / DEFECT-008 / DEFECT-006（2026-09-21 R0：真机窗口前置批次）

**目的**：把「不依赖真机、但窗口一旦开启就必须在场」的三项修复与窗口工具一次性收口，使 C/D
单窗口不需要中途改代码（受「重解析只做一次」裁决约束，见 PLAN-004「已裁决事项」）。

**关闭条件**：三项红灯转绿且门禁全绿 + 窗口脚本在版本布局上可跑 `--check-only`；DEFECT-006 的
状态语义子项（部分成功/假 running）不在本节，另由 DEFECT-006/007 行承载裁决。

**工作项与状态（2026-09-21 全部达成，门禁 366 passed, 2 skipped + ruff 全绿）**：

| # | 项 | 交付 | 判据 |
| --- | --- | --- | --- |
| R0-1 | **REQ-083**（入站：根仓库请求）`file_path` 段字符类放宽 | `schemas.py:31-41` 把 `RELATIVE_PATH_PATTERN` 改为**黑名单段**（`_PATH_SEGMENT`）：拒 `\`、半角 `:`、`/ : * ? " < > |`、控制字符、前导 `.`、首尾空白/句点，其余放行；因 pydantic-core 不支持前后视断言，`BookSyncItem.file_path` 改用 `field_validator` + Python `re`（`schemas.py:41`） | 红灯→绿：`test_book_sync_item_accepts_fullwidth_punctuation`、`test_book_sync_item_still_rejects_unsafe_path[7 例]`、契约 `test_sync_accepts_fullwidth_punctuation_in_file_path`（两条含 `（）：` 的路径整批 synced）；`test_sync_rejects_unsafe_path_segments` 四例**不改动仍绿** |
| R0-2 | **DEFECT-008** `.env` 值尾行内 `#` 注释剥离 | `config._strip_env_comment()`：引号内原样保留；未加引号时按 `\s#.*$` 剥离（`#` 前无空白视为值的一部分，与 python-dotenv 同语义） | 新增 `TestDotenvInlineComment` 4 例（含 `QED_DB_PASSWORD` 剥离后不进 repr） |
| R0-3 | **DEFECT-006 页码越界子项** | `api/main.py` 提交门槛扩为四道：书目 → 已 ingest → **`pages` 越界 422** → 引擎名 → 探活 503；越界判在探活**之前**（非法请求不打模型） | 契约 `test_pages_out_of_range_returns_422_and_creates_no_job`：故意用 `healthy=False` 假引擎，红灯实测原为 **503**（先探活）→ 绿为 422 且 `af_parse_jobs` 零行 |
| R0-4 | 窗口工具改版本布局（销 PLAN-004 **I9** 挂账） | `scripts/tmp/live_reparse_b01_pages.py` **重写**：只经 8902 API 驱动（不再进程内跑管线）；`--check-only` 前置取证；模型/服务**轮询到 200** 才算就绪（默认 300s）；自证 `alembic_version==20260920_0002` + `active_job_id` 字段在场，否则拒绝跑（防旧部署写出幽灵行）；页图不全时先 re-ingest；越界 422 探针复验后才提交解析；产出 `S0/S1/S2` 三份留档（含 clip/degenerate/dropped/硬链接/根视图无 chunks/数据根 `tmp` 不存在） | 语法与 ruff 通过；真机判据待 C/D 窗口执行（本节不声称真机结果） |
| R0-5 | runbook 重核（根仓库 2026-09-21 清了 `dataset/parsed/`） | `guides/operations.md`：C-5 幽灵行手工删除**取消**（re-ingest 按 V8 `reset_legacy` 自动作废，取证转 `S1_before.json`）；新增 C-7 `--check-only` 前置；D 步写明**必须先 re-ingest 补齐页图**；新增「模型就绪不以 8900 一次提示为准」条 | 与 `af_*` 迁移单窗口顺序一致，无未落地命令 |

**根侧「启动即失败」的定因（2026-09-21 只读走查，不由本仓库修复）**：8900 前端
`operateModel` 以 `POLL_TIMEOUT_MS=15000`（`web-ui/src/stores/runtime.ts:30,351,366`）轮询
`getSlotStatus(slot).ready`，超时即显示「收敛超时/失败」；而 vLLM 权重加载为分钟级，且
`model_start` 恒回 `status="starting"`（`backend/qed_engine/api/control.py:190`）、
`qed_mineru_service.py cmd_start` 不带 `--wait`（`:127`）→ `docker compose up -d` 在就绪前即返回。
`ready` 无持久化，是每次 `GET /health`（1s 超时、无重试，`runtimes/base.py:52-58`）现算，因此
一次「超时」不污染状态，稍后再探即 `true`。本方处置：窗口脚本自行轮询到就绪（R0-4）；根侧处置
（放宽截止/不显示为失败/对齐 `infra-up.ps1` 的 120s 与 `compose.yaml` `start_period`）以发起请求
`REQ-084` 承载（见 todo「跨项目请求」）。

**本节不声称**：任何真机结果、根仓库改动、REQ-083 的根侧关闭（须根侧撤销改名并回执）。

## 承载 ARCH-017 / REQ-085（模型服务单一占用闸门与窗口排他）

**目的**：R1 窗口 D-2 实证——本机 GPU 只容得下一个 MinerU 解析，而 MinerU 自身
`max_concurrent_requests=3` 不替调用方排队。根侧控制台在同一时间向 8902 并发提交并重启了 vision
容器，导致排队 9.5 分钟后的任务在启动瞬间探活失败，却被记成「模型服务不可用」（故障归因失真，
且我方无法自证「没有并行打模型」）。本节把**串行占用**变成本仓库可执行、可测试的契约。

**关闭条件**：`model_load()` + 闸门带测试落地且门禁全绿，并在窗口排他条件下重跑 D-2 成功激活版本。
**2026-09-21 达成并关闭 ARCH-017**（G1/G2/G4 完成，G3 出站请求转入 todo REQ-085 由根侧关闭；过程史实在本表）。

| # | 工作项 | 状态 | 结果与证据 |
| --- | --- | --- | --- |
| G1 | 契约成文（design + architecture + runbook） | **完成 2026-09-21** | `model-integration.md` 适配器契约新增 `model_load()` 与「不臆造占用」条、`parsing-pipeline.md` 错误处理表新增「模型服务被其他解析占用」行 + 成文理由段、`api.md` `GET /parse-jobs/{id}` 说明「queued 可以很久」并在错误表分列两种文案、`operations.md` runbook 新增 **C-0 窗口排他**前置 |
| G2 | 实现：`MinerUEngine.model_load()` + `pipeline` 启动前闸门 | **完成 2026-09-21** | 红灯先行：`tests/unit/test_engines.py::TestModelLoad` 6 例（`queued_tasks`/`processing_tasks` → `ModelLoad`；非 200 / 传输异常 / 非 JSON / 字段缺失一律 `None`）+ `tests/unit/test_orchestrator.py::TestSingleOccupancyGate` 4 例（排队→在飞→空闲三轮复探后放行、恒占用超时判 `failed` 且文案含「占用」不含「不可用」且 `parse_calls == 0`、`started_at` 为 NULL、`af_pages` 零行、不激活；`None` 即放行且只探一次；常量 `(5.0, 900.0)`）。实现：`engines/base.py` 新增 `ModelLoad(queued, processing).busy` 与协议成员 `model_load()`、`engines/mineru.py` 的 `_count()` 采信非负整数、`orchestrator/pipeline.py` 的 `_await_model_idle()` 接在 `health()` 之前（等待期不 `mark_running`），`MODEL_BUSY` 与 `MODEL_UNAVAILABLE` 分列；三处测试替身（契约 `FakeEngine`、集成 `_ProbeEngine`、单测 `FakeEngine`）同步实现协议。**门禁**：`pytest tests -q` → `376 passed, 10 skipped`、`ruff check src tests scripts` 全绿、`QED_TEST_MYSQL=1 pytest tests/integration -q` → `8 passed` |
| G3 | 出站请求 REQ-085（目标：QED-Engine） | 转达稿已成文，待用户转达 | 8900 在 8902 尚有 `queued`/`running` 任务时不得重启/换起 vision 槽位（本仓库无写权限，经用户转达登记）。已在 todo 登记为 `REQ-085`，**转达稿见本计划「本轮发出的两条出站请求」节（锚点 `95e1d37`）**（**不影响 ARCH-017 关闭**：本方串行闸门已可自证，容器生命周期权在 8900） |
| G4 | R1 窗口 D-2 在排他条件下重跑 | **完成 2026-09-21** | 首次尝试失败证据：`D_run.log` + `S1b_gate_probe_fail.json`（19:16~19:34）；job `471761dd16f7` failed、`af_pages` 零行、`active_job_id` 未动。留档口径：`S1_before.json`/`S2_after.json`/`S0_preflight.json` 已被 20:39 的 `--evidence-only` 补证据**覆盖**，故其内容属**激活后**快照（`active_job_id=86ab56ae2b6c`、页行 20），首次尝试的「前态」以 `D_run.log` 的 19:2x 行为准。重跑前置：8902 重启加载闸门代码（pid 207560）+ 窗口脚本新增 **C-0 排他取证**（`S1c_exclusive.json`）。**重跑结果：job `86ab56ae2b6c` 20/20 页 parsed、`parse_status=completed`、`active_job_id` 置位（真机首次激活）**，`versions/86ab56ae2b6c/` = `book.json`+`chunks.json`+`manifest.json`+526 个页级文件（20×blocks.json + 20×md + 486 页图硬链接，实测 `st_ino` 一致 20 个）、根视图无 `chunks.json`；块类型分布 `caption/footer/formula/page_number/paragraph`、**丢弃 0**、越框裁剪 right/bottom/degenerate = 1/0/0；MinerU `completed_tasks` 由 1 → 21 自证「确实逐页打过模型」。**串行性自证**：提交时 `queued_tasks+processing_tasks=0` 且本机无 `queued` 行，20 页在 52 秒内完成（上一次同区间在并发下 10 分钟） |
| G5 | 窗口脚本两处缺陷修正（同窗口实证） | **完成 2026-09-21** | ① `_metrics()` 把页端点响应当作 BlocksPage 顶层使用，而真机信封是 `{blocks, markdown, image_url, edits}` → 解析成功后取证段 `AttributeError`；已改为读 `response["blocks"]` 子对象，并新增 `--evidence-only <job_id>`（**只为补证据而不再解析一次**，跳过 ingest/门槛探针/C-0/提交）。② 脚本裸用 `httpx.get/post` 走系统代理：Windows 注册表代理不豁免 `127.0.0.1`，代理被打开后 5002/8902 探针全变 **502**（`curl` 直连 200 对照），窗口 15 分钟空转 → 统一改为模块级 `httpx.Client(trust_env=False)`，与 MinerU 适配器条文 11 同一不变式 |

**窗口实证时间线（本地时刻，DB 存 UTC）**：19:19~19:22 `909ce841b85c`（mathanalysis-b05，根侧提交）
completed → 19:24:34 `bac16a3e7605`（mathanalysis-b05）开始 → **19:25:01 `471761dd16f7`（我方 D-2）入队**
→ 19:34:37 两者同时终态：根侧任务 `MinerU 轮询失败（连续 3 次）：Server disconnected`、我方任务
`模型服务不可用`（`started_at` 为 NULL，即从未进入执行）→ 19:35:16 `88de6a2f682e`（mathanalysis-b01）
running。MinerU `/health` 的 `completed_tasks`/`failed_tasks` 由 50 归 0，证明容器在此期间被重启。

**同窗口已达成（不受本节影响，事实源在此）**：D-1 re-ingest 成功——真实数据根
`parsed/linear_algebra/linearalgebra-b01/` = 486 张页图 + `book.json` + `manifest.json`；`af_pages`
39 行 legacy（含第 999 页幽灵行）整批作废为 **0 行**，即 PLAN-004 取消手工 DELETE（C-5）的判据成立；
`pages` 越界探针在 ingest 后转为 `[491]/[0]/[1,487]` 全部 **422**、任务行增量 0（DEFECT-006 受理前
门槛真机自证）。同时修正窗口脚本的顺序假设：**未 ingest 的书打不到 422 门槛**（404 在前），
`--check-only` 下该项记为待复验而非失败。

**同窗口的两条如实登记（不判为缺陷）**：① 取证字段 `data_root_tmp_absent=False`——真实数据根
`D:\coding\QED-Engine\dataset\tmp\` **确实存在**，但其下两项 `qed-tracker`（2026-08-28）与
`参考书籍`（2026-09-03）的 mtime 都早于本仓库任何写入，属**根侧历史目录**，不是条文 12 意义上
「本组件产生的中间产物」；本仓库在该目录下零写入，故不据此判缺陷，也不越界删除他人文件。
② 孤儿 `running` 行 `5f43593a12cc`（空闲 106683 秒）使「DB 状态」不能作为排他判据——窗口脚本的
C-0 因此改以**模型侧计数**（`queued_tasks + processing_tasks`）为主判据、本机只判 `queued` 行，
这正是 DEFECT-007 的实证之一（裁决未下之前，孤儿行只登记不阻塞）。

## 跨项目回执发出（2026-09-21，R1 窗口后）

按[跨项目协作流程](../../../standards/cross-project-collaboration.md)第 4 步：回执只落本仓库 todo/计划，
根仓库自读登记，本仓库不写根仓库文件；出站请求在本仓库登记后由**用户转达**根仓库。

**锚点（根侧复核的坐标）**：代码/测试/台账 = commit `95e1d37`（分支 `release`）；本节文本随其后的
docs commit 同批推送，**根侧拉取 `release` HEAD 即得全部材料**。门禁真实输出
`pytest tests -q` → **376 passed, 10 skipped**、`ruff check src tests scripts` → All checks passed、
`tests/contract` → **96 passed**、`QED_TEST_MYSQL=1 pytest tests/integration -q` → **8 passed**；
真机取证 `data/probes/live-window-20260921/{S0_preflight,S1_before,S1c_exclusive,S2_after}.json`。

### REQ-080 —— 解析产物按版本划分（回执 QED-Engine，本方实施完成，等根侧确认四条）

事实源：[PLAN-004「REQ-080 实施回执稿」](../../../history/plans/2026-09/2026-09-20-req080-parse-version-layout.md)（差异 1~4 +
请根确认清单四条 + 真机取证段）。要点：`versions/<job_id>/` 落盘 + `af_books.active_job_id` 生效指针
（NULL = 根视图）+ 只读 `GET /api/v1/books/{id}/versions`；迁移 `20260920_0002` 已在真实 `qed` 库生效；
**8900 与前端零改造**（端点路径、响应结构、`image_url` 字面量、manifest 相对路径基准全部不变）。

**待根侧回应（本方 REQ-080 关闭门槛）**：① 差异 1~4 是否满足 REQ-080 的关闭语义；② 版本对比/回滚
是否需要端点契约（当前无写入端点，切换是一条 UPDATE）；③ 人工编辑不随版本继承是否可接受；
④ 旧版本清理策略属 D 类需另行立项。

**根侧回应已到 → REQ-080 与 PLAN-004 关闭（2026-09-22 复核其 `docs/trackers/completed.md` REQ-080 行，
关闭结果 Achieved）**：**四条全部接受**——① 差异 1~4 满足关闭语义；② 版本对比/回滚**不开**写入端点
契约（将来需求另立 REQ）；③ 人工编辑不随版本继承接受；④ 旧版本磁盘清理属 **D 类另行立项**。根侧并附
实测佐证「8900 门面读取路径不变即工作正常（parsing-tree/页数据/书页状态一致）」。据此本方 REQ-080 行
与 PLAN-004 行同日原子迁入 [completed.md](../../../trackers/completed.md)，PLAN-004 壳按 **Retain** 归档。
**四条的长期后果**（后续任务的边界，不是待办）：② ⇒ 不做切换/回滚端点，除非根侧另立 REQ；
④ ⇒ `versions/` 磁盘清理始终是 D 类，需用户批准，无自动删除；③ ⇒ 块编辑绑定其所属 `job_id`
（条文 V6/V9），重解析后旧编辑不回显属既定语义而非缺陷。

### REQ-081 —— bbox 坐标失真的**二次回执**（回执 QED-Engine，根侧 §13 复测前提已达成）

首次回执见本计划「跨项目回执稿（W8-g，2026-09-21）」一节的 REQ-081 段。当时本方承诺的三步
（版本布局落地 → 迁移窗口 → 单次重解析）**已于 2026-09-21 R1 窗口全部完成**，故本条即为可复测通知：

- **可复测的产物**：`linearalgebra-b01` **生效版本 `86ab56ae2b6c`** 的第 **1~20 页**（含贵方报告的第 7 页），
  20/20 页 `parsed`。读取路径不变：`GET /api/v1/books/linearalgebra-b01/pages/{page_no}`（块含 `bbox`）
  与 `.../pages/{page_no}/image`；`GET .../versions` 可看到 12 个历史版本。
- **三点变化，请贵方复测时注意**：
  ① 坐标已按归一化 0–1000 → 页图像素正确换算，不再出现「`x1` 恒等于 1241」的夹取残留；本方端到端
  复验 5/5 块平均 IoU **0.9314**（旧实现同判据 0.0114）。
  ② DEFECT-005（类型字面值）同已解除：`header`/`footer` 入映射，未覆盖字面值转为**可见丢弃**并落进
  `quality.dropped_block_count`/`dropped_types`；本次 20 页丢弃 **0**，故按 `index` 定位的对照视图可直接用，
  但**必须换用重解析后的产物**（旧产物是修复前的下标语义）。
  ③ 约 **7px 的系统性左偏**仍在（引擎检测框自身特性，非坐标空间问题），叠框请保留少量外扩容差。
- **旧代产物已作废**：R1 窗口 D-1 re-ingest 把 39 行 legacy 页行（含第 999 页幽灵行）整批作废为 0 行，
  因此贵方此前缓存的任何 b01 页数据都不再是端点会返回的内容；页图计数 486 未变。
- 首轮回执里「受害面是全部已解析页而非单页」的判断成立，本轮取证页区间按 1~20 取即可。

### REQ-083 —— `file_path` 含全角标点不再整批 422（回执 QED-Engine，本方侧交付完成）

- 贵方为绕过本方校验所做的**改名止血**（全角 → `_`/`-`）自此无必要：`BookSyncItem.file_path` 的段规则
  由 `\w` 白名单改为字符黑名单，全角 `（）`、`：`、`【】` 及空格放行；防遍历（`..`、前导/尾随 `.` 与空白）、
  `\`、半角 `:`（盘符）、`/ : * ? " < > |` 与控制字符仍拒（422）。测试：`tests/unit/test_schemas.py`
  新增全角标点通过 + 六类违规仍拦的用例；真机 R1 窗口书目 sync 读取正常。
- 实现细节供贵方参考：pydantic 的 `pattern=` 走 Rust regex、不支持前后视断言，故该模式由 Python `re`
  在 `field_validator` 内执行，端点行为（422 + detail 文案）不变。
- **对贵方的操作含义**：09-20 的临时改名（全角 → `_`/`-`）现在**可以撤销**，这是 REQ-083 的根侧关闭动作。
  撤销代价取决于改名范围：只动 `display_title` ⇒ 重新 sync 一条 PUT 即可覆盖；若改到了目录名或
  `book_id` 这类键 ⇒ 本方需重新 ingest（旧 `versions/<job_id>/` 整树会随 purge 作废，属 D 类操作，
  请先与本方确认窗口）。建议在回执里一并说明改名范围。

### REQ-001 —— 延后项 ② 随之解除（本方仍开放等待根侧复核）

首次回执「本方延后项 ②：版本布局落地后 `versions/<job_id>` 与生效指针需再回执一次」由本节的
REQ-080 回执承接完毕；REQ-001 行保持开放，等根侧读取本节并复核后关闭。

### 本轮发出的两条出站请求（需用户转达根仓库）

**REQ-084 —— 8900 模型启动「假失败」误报修复（目标：QED-Engine）**

- 现象：控制台点启动后约 15 秒即报启动失败，而 vLLM/MinerU 加载常需数分钟；本方窗口期间被此误报
  误导过一次判断。
- 定因（根仓库只读走查，未做任何写入）：`web-ui/src/stores/runtime.ts` 的 `POLL_TIMEOUT_MS = 15000`
  与 `infra-up.ps1` 在 120 秒后 `exit 1`，两处都以固定超时判成败，而非以模型服务真实就绪判成败。
- 建议：轮询目标服务的 `GET :5002/health` 直到 200（本方窗口脚本 `scripts/tmp/live_reparse_b01_pages.py`
  即此做法，可作参考实现），把「已提交启动」与「已就绪」分成两个提示；超时只报「仍在加载」而非失败。
- 本方侧状态：不受该误报影响（自行轮询 `/health`），故不阻塞本方任何任务；关闭权在根侧。

**REQ-085 —— 8900 不得在 8902 尚有 queued/running 解析任务时重启或换起 vision 槽位（目标：QED-Engine）**

- R1 窗口实证（时间线见上节「承载 ARCH-017」）：19:25:01 本方 D-2 任务入队等待，19:34:37 根侧提交的任务
  与本方任务**同时判死**，`/health` 的 `completed_tasks`/`failed_tasks` 由 50 归 0 —— 容器在此期间被重启，
  两个任务的失败归因都是假的。
- 请求：① 8902 有在飞任务时不重启/换起 vision 槽位；若必须重启，先给出互斥信号或让 8900 侧查询
  `GET /api/v1/parse-jobs/{id}` 的 `queued`/`running` 计数；② 允许贵方在 UI 上把「模型被占用/正在排队」
  与「模型不可用」分列（本方 `/parse-jobs` 的 detail 文案已按此分列，可直接透传）。
- 本方侧状态：**已闭环**——ARCH-017 单一占用闸门落地（同一时刻只有一个解析打 MinerU，等待期保持
  `queued` 且 `started_at` 为空）并在排他窗口重跑成功。但容器生命周期权在 8900，本方无法自证根侧不重启，
  故 REQ-085 保持开放直到根侧登记/确认。

### 根侧回执已到：六个跨项目行关闭（2026-09-22 复核，A 类零代码）

**判据**：根仓库 `docs/trackers/` 自身登记即为「根侧已回执」的事实源（本仓库只读，不写根侧文件；
按[跨项目协作流程](../../../standards/cross-project-collaboration.md)，回执由用户转达）。2026-09-22 逐行
读取其 `completed.md`/`todo.md` 后，本计划承载的六行全部满足关闭条件。

| 行 | 根侧登记位置 | 根侧结论原文要点 | 本方关闭判据 |
| --- | --- | --- | --- |
| REQ-080 | 其 `completed.md` REQ-080 行（Achieved，2026-09-21） | 四条确认**全部接受**：差异 1~4 满足语义 / 对比回滚不开写入端点 / 编辑不随版本继承 / 旧版本清理属 D 类另立；并附「8900 门面读取路径不变即工作正常」实测佐证 | 见上节「REQ-080」⇒ 同批关 PLAN-004 |
| REQ-081 | 其 `completed.md` REQ-081 行（Achieved） | **§13 复测通过**：数据面 b01 生效版本 `86ab56ae2b6c` p3/p7/p15 实测**零夹取**（`x1==W` 恒夹特征消除、无 <6px 细条、20 页丢弃 0）+ 视觉面用户浏览器确认「显示正常，叠框对齐」（7px 左偏容差知悉）+ 旧代产物作废确认 | 本方二次回执（上节）被采信，复测判据在根侧达成 |
| REQ-083 | 其 `completed.md` REQ-083 行（Achieved） | 双侧闭环：**改名已物理撤销**（两本 PDF 恢复全角原名 + `qt_books` 两库 4 行 `file_path` 还原）+ `POST /books/sync` 真机复通（200、updated 17、**零 422**）+ `GET /books/{id}/file` 两本 200 | 本方回执（上节）+ 根侧撤销改名这一唯一根侧动作已完成 |
| REQ-001 | 其 `todo.md` **PLAN-044** 行 | 「2026-09-21 对方 REQ-001 复核材料读取登记（锚点 `95e1d37`）：8902 af_* 新契约 + 版本布局回执已由本轮联调承接（生效指针/versions 语义/端点契约零变化见 REQ-080 行与其 `api-contracts.md`）」；ARCH-020-D 已 Achieved | 本方关闭条件是「根侧在 PLAN-044/ARCH-020-D 登记读取」⇒ 已登记；其行末「对方关闭等用户转达」= 等本行 |
| REQ-084 | 其 `completed.md` REQ-084 行（Achieved） | 转达到手即 **BUGFIX-010** 已修：启停**派发即返回**（后台线程 + 槽位级 in-flight 409）、`MODEL_POLL_TIMEOUT_MS=300s`、「启动中→可用」两段式；实测 4ms 返回、409 生效、约 18s 收敛 | 建议语义（轮询 `/health` 到 200 + 区分已提交/已就绪）已被采纳实现 |
| REQ-085 | 其 `completed.md` REQ-085 行（Achieved） | 用户裁决走**确认约定**（不做硬互斥）：边界审核实证 MinerU 单实例 + 解析调用唯一链路 = 8902 直连；约定成文于其 `design/local-model-management.md` **已知约束 9/10** | 本方请求 ① 的两种满足路径（互斥信号**或**确认约定）之「确认约定」达成；本方闸门（ARCH-017）已在位 |

**关闭动作**：六行自 todo.md 原子迁入 [completed.md](../../../trackers/completed.md)；`ARCH-001` 聚合行子行
清单随之收缩为 `ARCH-003/005/006/009、REQ-086/087`。

**同轮编号冲突处置（新入站）**：根仓库 2026-09-21 把 **`REQ-086`** 用给了「全本解析按窗口分块提交 +
超时按块计」（其 `todo.md` REQ-086 行，高，进行中），与本方 2026-09-21 新登的「页级失败清单与 jobs
列表数据源」撞号（同号不同事，判例同 `REQ-001`）。按「后占者让号」把**本方那行改号为 `REQ-087`**
（本行由本仓库自登、根侧从未引用该号，改号零外部代价），根侧 `REQ-086` 在本方以**同号入站登记**，
承载见下节「承载 REQ-086」。

## 承载 REQ-036 / REQ-057 / REQ-074（2026-09-21 跨项目欠账回收）与 REQ-075 点名

**目的**：根仓库台账里有四条**等的是我方动作**、但我方从未承接或从未点名的入站项。本轮全部按
A 类（文档/回执，零代码）收口：先核对事实，再在本节写结论，回执由根仓库自读登记。

**关闭条件**：本节四条结论成文 + todo/completed 同步 + 门禁全绿（本方侧即告完成；根侧读取后关闭其台账行）。

| # | 根侧行（关闭条件原文） | 我方实况与结论 | 回执要点 |
| --- | --- | --- | --- |
| X1 | **REQ-036**（高，「对方承接回执后关闭」；它同时卡住根侧 REQ-034 前置解除与 C 组第一阶段联调） | ① V2-003 越界产物**已于 2026-08-20 审阅并自行提交**（`importer.py` + 8 测试，81 passed + ruff clean，见 completed 该行），此后 ingest 已推进到真机 486 页 E2E（ARCH-015 关闭）；② V2-004 orchestrator / V2-005 MinerU 接入 / V2-007 API v1 **全部交付**：`orchestrator/pipeline.py`（全本+逐页+页级持久化+断点续跑+单一占用闸门）、`engines/mineru.py`（3.4.4 `/tasks`→`/tasks/{id}`→`/tasks/{id}/result` + `model_load()`）、15 条 `/api/v1/*` 路由 + `tests/contract/test_api_v1_contract.py`，契约事实源 [api.md](../../../architecture/api.md)；③ 8902 真机只读面 L7 九端点全可用，根侧 2026-09-20「联调最小打通轮」的四处自修与我方契约一致 | **正式承接并回执**：三项子请求均由本仓库实施完毕，根侧可据此解除 REQ-034 的「等 8902 API 建立」前置。风险声明：本仓库不感知 8900 侧改造，联调结论以根侧实测为准 |
| X2 | **REQ-057**（中，「Axiom-Flow 需完成文档审阅后回执关闭」；根侧记我方「已建文档但未审阅」） | 本仓库 [ADR 0002](../../../adr/0002-pending-design-location.md)（待评审设计先入 `plans/`，确定后晋升 `design/`）**状态 = Accepted，2026-08-23**，由 `tests/contract/test_adr_governance.py` 守护；且一直在实际执行：本计划「契约条文草案（晋升输入）」与 PLAN-004 条文 V1~V10 即该 ADR 的产物，晋升动作发生在 W7 / PLAN-004 P7 | 根侧台账「未审阅」一行**已过时**：审阅与接受均已完成 3 周，无需本仓库改动，请根侧改记「已审阅」并关闭 REQ-057 |
| X3 | **REQ-074**（中，「对方回执后关闭」——Agent 标准体系对齐根 REQ-071 口径） | 本仓库走的是 [ADR 0003](../../../adr/0003-standards-governance-alignment.md) 的**同源不同名**路线（范本取自 QED-Tracker 成熟版 + 根仓库治理对齐）：根 `code-document-traceability.md` ≡ `standards/doc-governance.md`「代码与文档追溯」节 + `code-map.md` **逐模块登记职责/状态/设计/测试** + `test_code_document_mapping.py` 双向守护；根 `code-standards.md` ≡ `AGENTS.md`「变更分级与边界」「中文与注释」+ `guides/development.md` 六步 + `standards/testing.md` 分层与隔离铁律 + **ruff 门禁**（机器可判定，比条文更硬）；根 `storage-conventions.md` ≡ `design/parsing-pipeline.md` 条文 12（本组件不产生数据根中间产物，比「任务结束清理 tmp」更严）+ `operations.md` 数据根口径 + 测试只用 `tmp_path` | 回执「**等价覆盖，不新增同义文件**」。三条**明确差异**一并声明：① 根 `storage-conventions.md:62`「改数据根/三区/tmp/原子落盘规则需先立 ADR」本仓库**不作为 ADR 触发条件**（2026-09-20 用户裁决：该条只约束根仓库），改走本仓库「变更分级」大修改路径（todo + `plans/` 计划 + architecture/design 同步）；② **唯一实质不等价处（本轮实测）**：本仓库 `src/` 26 个 Python 模块**未普遍**采用根侧推荐的「模块职责 / 设计关联（DesignRef） / 实现状态」文件头（实测仅 `ingest/importer.py` 与 `ingest/__init__.py`，测试侧 12/29 份带「模块职责」行），追溯事实源集中在 code-map。是否补齐 26 份文件头属**新增代码规范执行**，本轮不做，留给用户裁决（见下「REQ-074 遗留裁决项」）；③ 若根侧坚持三仓库文件同名镜像，那属本仓库 `standards/` 实质规则变更，**必须先新增 ADR** 才能落地 |
| X4 | **REQ-075**（高，「对方回执后关闭」；根侧 2026-09-21 复核仍写「待对方回执」） | 本仓库**已关闭**（completed 2026-09-21 行），回执正文就在本计划「跨项目回执稿（W8-g，2026-09-21）」的 REQ-075 段：唯一键 `QED_OCR_MODEL_URL` / 生效端口 5002、无旧键别名；MinerU 3.4.4 三段端点与 `return_content_list`/`return_middle_json` 必开；legacy `/file_parse` 与 `/get_task_results/{id}` 在 3.4.4 均 404 | 本批回执**点名**根侧读取该段并关闭 REQ-075；连带 **ARCH-020-E 的「前置阻塞 REQ-075」可解除**（其证据列仍记「HTTP 404」属修复前旧状态） |

**本方侧终态**：X1~X4 无遗留动作，全部转为「等根仓库读取并关闭其台账行」。三条入站行的关闭结果已写入
[completed.md](../../../trackers/completed.md)（REQ-036/057/074），REQ-075 指向其原关闭行。

**REQ-074 遗留裁决项（不阻塞回执，登记在 ARCH-010 长期行）**：差异 ② 的两种处置——
(A) 按根侧范本补齐 `src/` 26 个模块的「模块职责 / 设计关联（DesignRef） / 实现状态」文件头，并在
`test_code_document_mapping.py` 把它变成硬判据（A 类批量文档改动，一次可做，代价是全库 diff）；
(B) 明确接受现状（追溯事实源= code-map，文件头只写职责说明），把该决定写进 `doc-governance.md`
追溯节并回执根侧「本仓库有意不同名」。**未获用户裁决前不实施**：本仓库 `AGENTS.md` 要求契约/规范类
改动先定级，且补齐文件头会让 26 个模块同时进入 diff 面。

## 承载 REQ-087（根侧 ARCH-020-E 的数据源需求：页级失败清单与 jobs 列表）

> **本计划已于 2026-09-22 关闭，本节不再是该行的承载**：本行现登为 **`REQ-088`**（根侧同日把
> `REQ-087` 用于「空白页合法化」，本仓库为后占者故让号），活承载见
> [PLAN-008](../../../plans/2026-09-22-req088-page-jobs-list.md)。以下为登记与裁决过程原文，保留原貌。

**来源**：根仓库 `docs/trackers/todo.md` 的 **ARCH-020-E** 证据列原文——「同轮承载解析 UI 暂缓增强
（bbox 拖拽缩放手柄、**失败页重解析按钮**、**任务中心抽屉**、页内搜索、质量信号展示——**待 8902 页级
失败清单与 running jobs 数据源确认后排期**」。根侧未单独立号，本仓库登记为请求接收行。
**编号沿革**：2026-09-21 首登为 `REQ-086`；2026-09-22 根侧把 `REQ-086` 用给另一件事（全本分块提交，
见下节「承载 REQ-086」）⇒ 同号不同事（判例同 `REQ-001`），本行**改号为 `REQ-087`**（本行由本仓库
自登、根侧从未引用该号，改号零外部代价；根侧的 `REQ-086` 由本仓库同号承接）。

**现状事实（本轮实测核对，非推测）**：

- 路由清单只有 `GET /api/v1/parse-jobs/{job_id}`（`api/main.py:522`），**没有** jobs 列表端点；
  书页只能按页号单读（`GET /books/{id}/pages/{page_no}`），`af_pages.status` 里的 `failed` 集合
  **无法一次查出**——前端要知道「哪几页失败」目前只能自己 1..page_count 逐个试。
- `ParseJob.progress` 只有 `{parsed, total}` 与一个 `error` 摘要字符串；`BookOut` 有 `parse_status`
  /`pages_done`/`active_job_id`，但 `parse_status` 被「全书是否覆盖」与「最近任务结果」**共用一个字段**
  （PLAN-003 真机新发现 N4），且 `completed` 会掩盖部分失败（N1）。
- `running` 行存在无人推进的孤儿（DEFECT-007：`5f43593a12cc` 空闲 106683 秒），所以「running jobs」
  这个数据源本身语义就不可信。

**为什么登记时只登记不实施**（2026-09-21 的理由，已被 2026-09-22 的裁决解除）：`api.md` 是根侧 8900
透传的**契约事实源**，端点集一旦开出来即成契约。当时 DEFECT-006（部分失败/书级状态拆分）与
DEFECT-007（假 `running`）未成语义，先开列表端点等于把错误语义固化给前端（ADR 0004 铁律：不做静默
兼容层）。**现状**：两者已由 [ADR 0007](../../../adr/0007-parse-status-semantics.md) 裁决并由 PLAN-006 实施
关闭 ⇒ 数据来源四条缺口（`failed_pages`、`last_parse_result`、`status` 六态、`/versions`）全部具备，
本行**只剩端点实施本身**（属新立项，见关闭条件）。

**需要根侧先回答的三条（作为裁决输入，随本批发出）**：

1. 任务中心抽屉要的是**全书 jobs 历史**（分页、含 failed/completed）还是**仅活跃 jobs**（queued/running）？
2. 页级失败清单是否要覆盖 `legacy` 哨兵行（`active_job_id` 为 NULL 的旧代际产物），还是只列生效版本？
3. 失败页重解析按钮的提交粒度：单页 `pages=[n]` 够用，还是要支持「一键补齐全部 failed 页」的批价格式？

**关闭条件**：端点契约成文（`api.md` + 设计条文）→ 实施带契约测试 → 根侧确认数据源可用。三条根侧
问题属**排期输入**而非本方阻塞（先按保守默认实现亦可）。责任分布：数据源需求细节＝根仓库；实施＝本仓库。
**立项口径**：新计划承载（B 类，TDD + 契约测试），不塞进本计划（本计划范围已在 2026-09-20 裁决收窄为
真机打通三面）。

## 承载 REQ-086（根侧入站：全本解析按窗口分块提交 + 超时按块计）

**来源**：根仓库 `docs/trackers/todo.md` 的 **REQ-086** 行（支线，高，进行中，2026-09-21 用户裁决
「A 止血 + B 根治同轮」登记），请求目标为本仓库。

**根侧归因（其 ARCH-020-E 证据列原文要点）**：`pages:null` 全本走 `parse_document` 整本一次
`POST /tasks`，提交→轮询→取结果**共用**单一 `AXIOM_OCR_TIMEOUT`（其引我方
`engines/mineru.py` 的 `timeout=` 与 `orchestrator/pipeline.py` 的整本调用），而 MinerU 侧本就按
`processing_window_size=64` 分批推理 ⇒ 317 页书第 1 窗口即超 600s：**凡 >~60 页全本必 `failed`，
且进度恒为 0、容器抖动整本归零**。根侧止血：b05 改走 `pages:[1..317]` 逐页路径（job `7af72c68180e`，
实测约 6 页/分），取证 `_failed_0a543bc67ae7` 版本目录 + 容器日志（15:00:50 窗口起跑、15:10:43 报
`MinerU 解析超时（>600s）`）。

**本方核对（只读走查，未实施）**：归因与我方代码事实一致——
`config.py:54` `ocr_timeout: int = 600`（注释即「整本解析耗时长」）、`config.py:79` 键
`AXIOM_OCR_TIMEOUT`、`engines/factory.py:38` 以 `timeout=float(settings.ocr_timeout)` 构造
`MinerUEngine`；`parse_document` 路径把提交/轮询/取结果放在同一个墙钟内。**页区间路径不受影响**
（逐页各自计时 + 页级失败不杀 job + `_resumed_pages` 续跑），这正是根侧止血能跑通的原因。

**登记状态与责任**：本方以 `REQ-086`（支线，高，**Open**）承接登记，**本轮不实施**——它是解析管线的
执行结构变更（全本 job 内部分块提交 + deadline 按块计 + 契约测试补两条），属 B 类需另立计划并 TDD；
且其 ① 项与 PLAN-004 已冻结的条文冲突面需要先核对（分块写入必须仍落同一
`versions/<job_id>/`、页语义与激活门槛不变、`progress.parsed` 仍按页计）。**下一条件**：用户批准立项
⇒ 本方实施或提出替代方案并回执 ⇒ 根侧以 b05/b11 全本复测关闭其行。

**关闭条件（本方行）**：分块提交实施并通过 b05 全本真机复测（或根侧接受替代方案）后发出回执。

## 承载 ARCH-016 关闭（2026-09-21 主线审计，用户裁决「三件全做」）

**关闭条件（原文）**：代表书类型分布取证（随 ARCH-009）+ 真机零丢弃对账复跑。

**逐条判定**：

1. **真机零丢弃对账复跑：达成**。R1 窗口生效版本 `86ab56ae2b6c` 第 1~20 页逐页
   `dropped_block_count = 0`、`dropped_types = []`（`data/probes/live-window-20260921/S2_after.json`，
   本轮重新复核 20/20 页），且冒烟侧 `tests/smoke/test_live_mineru_chain.py` 以
   「原始项 = 存活块 + 丢弃」常驻守护。
2. **代表书类型分布取证：移交 ARCH-009，不构成本行未完成的实现**。理由：该判据与 ARCH-009 的关闭
   条件（「全量公式可渲染 ≥90% + **类型分布取证登记**」）**逐字重复**，两行等同一份证据属台账重复；
   引擎与归一化的**稳定化**本身（直连、三段端点、槽位分派、坐标空间、类型映射与可见丢弃、零丢弃
   对账）已交付并有回归网。

**移交时必须带走的已知缺口（不得因本行关闭而消失）**：b01 前 20 页真机块类型实际分布为
`paragraph` 121 / `page_number` 14 / `formula` 18 / `footer` 4 / `caption` 2（本轮
`scripts/tmp/live_check_chunk_formula_integrity.py` 留档
`data/probes/chunk-integrity-20260921/C1_chunks.json`），即 **`title` 与 `table` 两个映射条目尚未在真机
样本中出现**，`equation`/`page_header`/`page_footer` 旧版字面值同样只由单元测试覆盖。因此 ARCH-009 的
取证判据明确为：代表书前 20 页跑同一脚本，`_TYPE_MAP` 中**每个键至少命中一次**或出现即零丢弃
（`dropped_types` 为空），二者都不满足则该缺口重开为缺陷。

**关闭结果 Achieved**（判据 1 达成 + 判据 2 移交并被 ARCH-009 完整覆盖），行已原子移入
[completed.md](../../../trackers/completed.md)；`title`/`table` 取证缺口写入 ARCH-009 行与上方条文。

## 承载 ARCH-003 / ARCH-009 / ARCH-018 / ARCH-019（2026-09-22 真机窗口 R2：三件一次做完）

**目的**：把上一轮遗留的三项真机欠账在同一排他窗口内收掉——ARCH-003 的状态语义冒烟复跑（含业务库
`qed` 升 `20260921_0001` 与孤儿行归一）、PLAN-003 的 D3 删除演练、ARCH-009 代表书抽查口径验收。

**关闭条件**：① ARCH-003 的「语义经真机冒烟复跑通过」；② PLAN-003 的「D3 真机删除演练执行并登记」；
③ ARCH-009 的「两本代表书各 20 页：公式可渲染 ≥90% + 类型分布取证登记（`_TYPE_MAP` 每键至少命中一次
或出现即零丢弃）」（范围按用户 2026-09-22 裁决改为**抽查口径**，不做 723 页全量）。

**用户裁决（本轮）**：ARCH-009 用抽查口径（每本 20 页：首/中/尾 + 公式密集）；「KaTeX 可渲染」用
**程序化结构可解析 + 人工留档**，不引入渲染依赖；D3 演练对象用**合成 5 页探针书**，真实书目一律不动。

### 窗口前置与规程执行

- 备份：`data/backups/window-20260922/af_backup_20260922.sql`（184371 字节，5 表，
  `alembic_version='20260920_0002'`）——回滚按备份还原，不用有损 `alembic downgrade`。
- 排他（C-0）：每次提交前读 `5002/health` 的 `queued_tasks + processing_tasks` 与本机 `queued` 行，
  全部为 0 才提交；两本轮次**串行**（用户 2026-09-21 资源裁决）。留档
  `data/probes/window-20260922/preflight-0{1,2,3}.json` 与各 job 的 `P0_exclusive.json`。
- 窗口工具一律 `httpx.Client(trust_env=False)`：注册表代理会把回环探测伪装成 502（条文 11 同源）。

### W-A：ARCH-003 状态语义真机复跑（Achieved）

`qed` 业务库 `20260920_0002 → 20260921_0001`（升级前红灯本身即证据：新 ORM 读不到未迁移库），
换代码重启 8902 后：① 孤儿行 `5f43593a12cc`（`running`、idle 数万秒）由 `reconcile_on_startup()`
归一为 `interrupted` + `error="服务重启中断，可重跑"`、`failed_pages=[]`、`parse_status=pending`
（`A5_orphan_job_after.json`）；② `QED_SMOKE_LIVE=1 pytest tests/smoke` → **2 passed**
（`A6_live_smoke.log`/`A6_live_smoke_rerun.log`，两次跑各 5.1s/5.0s）。

红灯先行：冒烟首跑 `1 failed`——两处断言停在 PLAN-006 之前的旧契约（`progress` 缺 `failed` 键、
`parse_status` 兼任结局）。按 api.md:244 与 ADR 0007 决定 3 对齐测试而非改实现。**根因是冒烟层默认
skip、PLAN-006 收口时未跑** ⇒ 这正是 ARCH-003 判据要求真机复跑的理由，执行时机口径已写入
`guides/development.md` 的冒烟层说明（`standards/testing.md` 属实质规则变更，未单独立 ADR 故不动）。

### W-B：PLAN-003 D3 真机删除与审计演练（Achieved）

探针书 `probe-d3-20260922`（合成 5 页 PDF，`parsed/probe/d3-20260922/`）：同步 → ingest → 解析
（job `006e244b1966`，5/5 completed）→ 写入 1 条 `af_block_edits` → `DELETE /books/{id}`。

| 判据 | 删除前（`B1_before_delete.json`） | 删除后（`B2_after_delete.json`） |
| --- | --- | --- |
| `af_pages` 页行 | 5 | 0 |
| `af_block_edits` 编辑行 | 1 | 0 |
| 书目录 | 存在，`versions/` 1 个、页文件 5 | 不存在 |
| `GET /books/{id}` | 200 | 404 |
| `af_parse_jobs` 历史 | 1 | **1（保留为审计）**，`GET /parse-jobs/{id}` 仍 200 |
| `af_books` 总数 | 18（含探针） | 17（回到起点） |

`failures: []`（`B3_summary.json`），探针源 PDF 由脚本自行 unlink。**口径备注（不判失败）**：`DELETE`
契约只承诺书目录，因此**删除不裁剪空的父级目录 `parsed/probe/d3-20260922/`**——留空目录
属既定行为，如需裁剪须先改 `parsing-management.md` 的删除契约（未来若做旧版本磁盘清理的 D 类计划可顺带裁决）。
删除面的落库/磁盘与回滚口径已运维成文于 [operations.md](../../../guides/operations.md)
「书目删除的落库与磁盘口径（2026-09-22 D3 真机演练测定）」节。

### W-C：ARCH-009 代表书抽查验收（判据达成，过程中发现并修复 ARCH-018）

代表书 = **b05《数学分析原理》（Rudin 中译，317 页）+ b11《吉米多维奇数学分析习题集题解》（406 页）**。

1. **选页**（`C2_pages_b05.json`/`C2_pages_b11.json`）：首 3 + 中 7 + 尾 5 = 15 页定位页，另加 5 页
   「公式密集」。**两本 PDF 均无文本层**（0 字符/页全是图像），文本层记号密度恒 0 会让密集页退化成
   随机页，故 `live_pick_sample_pages.py` 增加**墨度模式**（0.3 缩放的深色像素占比排序）并在留档里
   记录 `density_mode`。质量信号 `text_length_ratio` 对扫描件恒跳过，本窗口不取其结论。
2. **解析**（串行）：b11 首次 ingest（406 页）→ job `383b44f14aaf` 20/20 完成并激活；b05 走既有生效
   版本 `7af72c68180e`（312/317 页，最近任务结局 `failed`，缺 48/98/136/162/282 五页，样本页与之无交集）。
3. **取证判据首跑双双变红**（`C4_forensic_*.json`）：b11 公式段 516 段可解析率 **87.79%**、
   b05 646 段 **91.95%**，且 63/52 处失败全部是 `md` 来源的 `unbalanced_inline`，形态一致为
   `$$$…$$$`；块级 `latex` 反而「结构自平衡」而漏检 ⇒ 定位为**归一层定界符缺陷 ARCH-018**，
   不是公式识别质量问题。检查器自身带 `--selftest` 反向判据（4 类人为缺陷必须变红），防止恒 PASS。
4. **修复 + 真机复验**：`split_math_delimiters` 落 `normalize/blocks.py`，6 例单元先红（4 failed，
   红得正是真机形态）后绿；重启 8902 后**同一批 20 页**重解析（b11 `bbe0d401ce83`、b05
   `185a37d55985`，均 completed 且激活）→ 公式段可解析率 **100% / 100%**，`$$$` 出现数 b11
   126 → 0，b05 采样 20 页归零（版本内 17 个完全落在样本页的 chunk 亦 0 命中）。
   修复规模对照：b05 旧版 1919 块 `latex` 全带定界符、`display` 恒 False；新版采样页 52 块转裸 + `display=true`。
5. **分块判据**：`live_check_chunk_formula_integrity.py` 对两个修复后版本 **PASS**（公式整块出现、
   溯源无悬空、非噪声块全覆盖）。同脚本对**修复前**的 b11 版本 `383b44f14aaf` 同样判 PASS，而该版本
   `chunks.json` 实测含 126 处 `$$$`（留档 `C8_chunk_prefix_b11.json`）⇒ 记录一条**判据边界**：四项判据
   看块边界与 `$` 奇偶，不看定界符包裹层级，故渲染前的定界符契约由 `normalize/` 单测与代表书取证把住。

**ARCH-009 逐条判定**：

| 判据（parsing-pipeline「验收标准」） | 结论 |
| --- | --- |
| ① 代表书全流程跑通（同步→ingest→解析→分块→落盘） | PASS（两本；b05 借既有版本 + 采样页新 job，b11 本窗口首次 ingest + 两次解析） |
| ② 抽查每本 20 页公式可渲染 ≥90% | PASS（100% / 100%，修复 ARCH-018 后） |
| ③ 分块无腰斩、可回跳页/块 | PASS（chunk 完整性检查两版本全绿） |
| ④ API 契约与 schemas 一致 | PASS（40 页页端点 + 版本/清单端点全部 200，契约测试层在门禁内） |
| ⑤ kill 后重启续跑 | 已由 PLAN-003 W8/L6 交付并有网（本窗口未重复消耗真机） |
| 类型分布取证（ARCH-016 移交） | **未达成 ⇒ 重开为 ARCH-019**：b05 第 313/314 页有 26 块按可见丢弃计入，原始字面值 `ref_text`（参考文献条目）未入 `_TYPE_MAP`；`heading`/`list` 两键在代表书样本中从未命中。已观测分布（两本各 20 页，修复后版本）：b05 `paragraph 227 / formula 52 / header 16 / page_number 12 / footer 1 / image 4 / table 1`；b11 `paragraph 227 / formula 63 / header 16 / page_number 13 / footer 4 / table 3 / image 2 / caption 1` |

### 本窗口产出（实现与文档）

- **ARCH-018 修复**：`normalize/blocks.py` 新增 `split_math_delimiters()` 并接入 FORMULA 分支
  （裸 `latex` + 由定界符决定 `display`）；`tests/unit/test_normalize.py::TestFormulaDelimiters` 6 例；
  契约成文于 [parsing-pipeline.md](../../../design/parsing-pipeline.md)「公式定界符契约」。
  影响面：`pages/*.md`、`chunks.json`、API `blocks[].latex` 与块编辑回显同时被修复。
- 窗口工具新增：`live_window_preflight.py`（只读快照，原生 SQL ∩ `SHOW COLUMNS` 故迁移前后可跑）、
  `live_d3_delete_drill.py`、`live_pick_sample_pages.py`（含墨度模式）、
  `live_forensic_representative.py`（含 `--selftest` 反向判据）、`live_parse_sample.py`（单任务串行 +
  C-0 自证 + 激活取证）。
- 门禁：`pytest tests -q` **411 passed / 10 skipped**（窗口前基线 405 + 本轮 6 例）、
  `ruff check src tests scripts` 全绿。**两个显式层在收口轮重跑**（`G1_mysql_layer.log`
  `QED_TEST_MYSQL=1 pytest tests/integration` → 8 passed；`G2_smoke_closeout.log`
  `QED_SMOKE_LIVE=1 pytest tests/smoke -m smoke` → 2 passed，5.06s），且二者都跑在 **ARCH-018 修复后**的
  代码上——W-A 期间那次冒烟还是修复前的实现，只覆盖状态语义面。

### 遗留与后续（不因本窗口关闭而消失）

1. **ARCH-019**：`ref_text` 字面值丢弃 26 块（映射目标待裁决）；`heading`/`list` 两键在两本抽样页内
   均未命中（`caption` 仅 b11 出现 1 块），属取证事实、非缺陷。
2. b05 生效版本 `185a37d55985` 是**混合版本**：20 页为 ARCH-018 修复后产物、292 页继承自修复前基线，
   其 `$$$` 残留 485/525 chunk（跨旧页的混合块）——只有整本重解析（ARCH-011 批次）才能全量重生。
3. b05 的 5 个缺页（48/98/136/162/282）与最近任务结局 `failed` 未在本窗口处理（不在抽查口径内）。
4. 真机冒烟/集成层默认 skip，**收口轮必须显式开 `QED_SMOKE_LIVE`/`QED_TEST_MYSQL` 跑一次**，
   否则契约漂移只会在下个真机窗口暴露（本轮实证）。**本轮已按此口径执行**（`G1`/`G2` 两份日志，
   均在 ARCH-018 修复后的代码上），长期口径写入 `guides/development.md`。

## 关闭与归档

- **口径优先级**：实际关闭判据以上节「关闭条件修订（2026-09-20 裁决后重排）」为准；S1~S7 中涉及
  整本/代表性的判据按「小样止」（单页 + 5 页 + 真书单页回归）解释，全量与质量验收归 ARCH-009。

- **关闭条件**：S1~S7 全部达成，DEFECT-001/002/003 关闭，ARCH-003/016 关闭，REQ-075 与 REQ-001
  回执发出，条文 1~11 晋升 design/architecture 且契约守护全绿。REQ-080 与 Rudin 验收（ARCH-009）
  **不属本计划关闭条件**。
  **2026-09-21 依赖转移**：DEFECT-006/007 已由用户裁决（A1/B1/C1）并成文为
  [ADR 0007](../../../adr/0007-parse-status-semantics.md)，其实施与 ARCH-003 收口一并由
  PLAN-006 承载（该计划同日关闭并按 Delete 删壳，正文见锚点 `9034a19`）⇒ 本计划剩余关闭依赖收窄为
  **D3 真机删除演练（需批准）+ REQ-001 根侧登记**，不再含状态语义裁决。
  **2026-09-22 R2 窗口：两项剩余依赖全部达成** —— D3 已执行并登记（见「W-B」），REQ-001 随六个
  跨项目行关闭 ⇒ **本计划自列的关闭条件已全部满足**。但正文此刻仍是三条**活行**的承载
  （REQ-086 / REQ-087 / ARCH-019，且 REQ-086/087 各含未实施的 B 类工作项）：按
  [ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md)「一任务一承载」，Retain 归档会把活承载
  一起搬进 `history/` 并使其脱离活跃计划集合（契约测试的镜像判定只扫 `plans/`）⇒ **故本计划保持
  In Progress，关闭与否作为待用户裁决项**：(A) 先为 REQ-086/087 另立 B 类计划、把 ARCH-019 挂到其下，
  再关本计划（Retain）；(B) 维持现状直到这三行全部关闭。默认取 (B)，不动。
  **2026-09-22 用户裁决取 (A) ⇒ 本计划关闭（Completed / Achieved，Retain）**：活行改挂两份新计划后
  本计划按 Retain 归档（见下「承载转移」）。**转移动机说明**：裁决文本只点名三条活行，实际引用本计划
  作承载的是**六条**（ARCH-005/006/009/019 + REQ-086/087）——归档后 `plans/` 扫描不再命中本计划，
  六行必须全部改挂，否则违反 ADR 0006「一任务一承载」。
- **承载转移（2026-09-22，本计划关闭时生效）**：
  - [PLAN-007 全本窗口分块提交与空白页合法化](../../../plans/2026-09-22-req086-whole-book-window.md) ←
    `REQ-086`（根侧入站，实施未开工）、`ARCH-009`（抽查口径已 PASS，剩全量取证）、
    `ARCH-019`（`ref_text` 映射待裁决）；同日新入站的根侧 `REQ-087`（空白页合法化 + 跨版本续跑语义）
    亦挂此计划——它与 REQ-086 共享同一个 b05 全本真机判据现场，拆开会互相等待。
  - [PLAN-008 页级失败清单与 jobs 列表端点](../../../plans/2026-09-22-req088-page-jobs-list.md) ←
    `ARCH-005`、`ARCH-006`（两者关闭判据都是 8903 真机联调，而联调消费的就是这两个端点）。
  - **二次编号冲突**：本计划的「承载 REQ-087」节改挂新计划时**再改号为 `REQ-088`**——根仓库
    2026-09-22 00:35 已用 `REQ-087` 登记「空白页合法化」（commit `3f0c60e`），本仓库 00:57（commit
    `1d4de56`）才把端点行改号为 087，属后占者 ⇒ 按「后占者让号」（判例同 `REQ-001`、`REQ-086`）让号。
    本节以下正文按关闭时点定稿，**不回改**旧号与旧承载路径；改号事实以本条为准。
- **归档判定 Retain**：W0 冻结清单与留档哈希、偏差台账、契约条文（晋升前的推导依据）需保留，
  故计划壳归档 `docs/history/plans/2026-09/`，不删除。
- 收尾动作：todo 关闭任务原子迁移 `completed.md`；`project-status.md` 现状与断点续跑措辞按事实
  刷新；`code-map.md` 补 `engines/factory.py`、`tests/support/`、`tests/integration/` 模块行。
  **2026-09-22 收口登记**：四条全部执行（`code-map.md` 三行已在 W7 轮补齐，本轮无新增模块）；
  本壳移至 `docs/history/plans/2026-09/` 后，壳内 43 处相对链接与外部四处引用（`adr/0007`、
  `trackers/completed.md`、`trackers/project-status.md`、`scripts/tmp/probe_mineru_api.py` 注释）同批改指，
  月份索引与 `plans/index.md` 登记去处。关闭轮门禁：`pytest tests -q` → **411 passed, 10 skipped**、
  `ruff check src tests scripts` → 全绿（本轮零代码，用例数与十四轮收口轮持平；治理契约测试
  `test_plan_governance` / `test_tracker_governance` / `test_markdown_links` 覆盖承载改挂、编号让号与
  链接改指三类不变式）。

## 台账历史归档（2026-09-21 承载契约压缩）

按 [ADR 0006](../../../adr/0006-ledger-plan-carrier-contract.md) 决定 4，todo 行证据列压缩为「最新结论 +
承载引用」，以下为被移除的历史日志原文（本计划为这些行的承载文档；行文中的 `~~划掉~~` 与日期段
保留原貌，不回改）。

### ARCH-001（主线聚合）原证据列

> 数据库与存储支线已关闭（ARCH-013/014/015，2026-09-20）；**2026-09-21 引擎对齐支线关闭**（DEFECT-002/003 + REQ-075）；剩余：~~归一化正确性（DEFECT-004/005）~~ **2026-09-21 两项均关闭** → 产物版本划分（REQ-080/PLAN-004）→ 对照联调（ARCH-005/006，前置已解除但需在修复后代码上重解析代表书）→ Rudin 端到端验收（ARCH-009）；DEFECT-001 按用户裁决保持开放（回归网缺位属已知代价）

### PLAN-003 原证据列

> **2026-09-20 立项并执行 W0**：三方对表产出偏差台账 23 项（A1~A6/B/C/D/E/F）+ 契约条文 11 条（含 1b）；W0 冻结探测 M1~M4 全 PASS（留档 `data/probes/mineru-3.4.4-20260920/`），端点终版与响应信封已成文，新增偏差 A5（`return_content_list` 等默认 false）与 A6（系统代理劫持回环流量）。**W1/W2/W4/W5/W6 代码侧完成**（`mineru.py` 三段化 + 信封双兼容 + 直连；`pipeline.py` 页级 commit + progress 节流；`engines/factory.py` 显式分派 + 提交期 503 + 数据根双闸 + ingest/purge 幂等），门禁 285 → **283 passed** + ruff 全绿（W7 删除重复事实源用例 2 例，基线相应下调），DEFECT-003 随 W6 关闭，完成登记见计划正文。**W7 完成**（F1~F5 文档自洽 + 条文 1~9/11 晋升 design/architecture，含 `database-design.md` 表状态口径改「已实现」与新增「任务表语义」「页表语义」；ADR 0004 正文 8002→5002）。**2026-09-20 收尾裁决**（取消延后、恢复推进，执行面收窄为三块）：C1 解析真机打通**小样止**（单页 + 5 页合成全本 + 真书单页回归，全量与质量验收归 ARCH-009）、C2 数据库存储收口（含 W3 走**路 B**：先评审 [ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md) 再写真 MySQL 双 Session 测试）、C3 数据根收口（`tmp/axiom-flow/` 改判为**设计表述错误** → 条文 12「不写即合规、存在即缺陷」，零代码；`data/` 重定义为探测/合成样本留档区）。判据清单 L1~L8 / D1~D5 / G1~G6 与工作项 W3-a~c、W8-a~g 见计划「收尾执行设计」。**2026-09-20 收尾轮真机取证完成 C1**：L1~L8 **全绿（小样止）**——L2 真书第 3 页、L3 五页合成整本（5/5 + 18 产物 + chunks 2 块）、L4 进度单调、L5 失败注入、L6 kill 续跑（真 MySQL 21 行留存 → 16/16 补齐）、L7 只读端点、L8 提交期 503 不落行，结论与原始留档见计划「收尾执行登记」与 `data/probes/live-bringup-20260920/`。同时暴露 4 项新缺陷（DEFECT-004 bbox 坐标空间 / DEFECT-005 类型字面值 / DEFECT-006 校验与状态语义 / DEFECT-007 遗留 running）与 1 项待批清理（真机 `af_pages` 第 999 页幽灵行）。**剩余**：C2 的 D3/D4/D5、C3 的 G1/G4/G5 口径晋升、W8 的 8.1 冒烟用例固化与 8.4/W8-g 跨项目回执；外部依赖仅 ADR 0005 评审。DEFECT-001 按用户裁决**不随真机证据关闭**。**2026-09-21 收尾二轮**：8.1 固化完成——`tests/smoke/test_live_mineru_chain.py`（逐页 PNG 形状 + 5 页整本落库与产物齐套 + 数据根 `tmp/` 不存在；默认门禁 `2 skipped`，`QED_SMOKE_LIVE=1` 真机 `2 passed in 7.08s`，5002 `completed_tasks` 48→50）；8.4/W8-g 回执稿成文（PLAN-003「跨项目回执稿」节）→ **DEFECT-002 与 REQ-075 关闭**；C3 的 D4/G5 只读取证成立（凭据零命中 + 仅 `af_*` 四表可触达；三段走查证明生效层恒为本仓库 `.env`，与根 `.env` 值一致）→ **C3 收口面关闭**；新登 DEFECT-008（`.env` 行内注释不剥离的潜伏坏 URL）。**本计划剩余全部为外部/裁决依赖**：C2 的 D3（真机删除演练，需批准）与 D5/W3（ADR 0005 评审）、REQ-001（等根仓库回执）、DEFECT-004~008 修复。**2026-09-21 DEFECT-004 修复并关闭**（本轮开工项，详证据与 REQ-081 回执见计划「DEFECT-004 修复登记」与「跨项目回执稿（REQ-081）」两节；门禁 283 → **292 passed** + 契约 82 + ruff 全绿，真机冒烟 `2 passed`）。**2026-09-21 四轮：DEFECT-005 修复并关闭**（`header`/`footer` 入 `_TYPE_MAP` 且与旧版字面值并存；`normalize_page` 逐条对账 → `QualityMetrics.dropped_block_count`/`dropped_types` 可见丢弃；`index` 只在存活块连续编号；整页 markdown 回退条件收紧为「引擎未回任何结构化条目」，对账不变式恒成立；新增 11 例单元（两轮红→绿：`6 failed` + `1 failed`）+ 冒烟新增「原始项 = 存活块 + 丢弃」零丢弃对账断言；门禁 292 → **303 passed, 2 skipped** + 契约 82 + ruff 全绿；**真机零丢弃复跑待 MinerU 起服务**，5002 当前 `/health` 连接拒绝）。本计划仍不关闭：剩余均为外部/裁决依赖项。

### DEFECT-001 原任务与证据列

> 任务：解析进度与页状态不落库：`orchestrator/pipeline.py` 全程只 flush，唯一 commit 在 `orchestrator/jobs.py` 任务结束时；`ParseJobRepository.update_progress` 无调用点 → 实时进度查询与断点续跑在 MySQL 上不成立
>
> 证据：来源：提交 a5d2e96 代码审查（2026-09-20）。复现缺口：`tests/integration/` 目前无测试文件，DB 测试全为内存/临时 SQLite（`test_db.py`、`test_db_migrations.py`），共享 Session 掩盖了未提交问题。**2026-09-20 W2 代码侧已落地**：页级 `commit`（产物写先于提交）+ `progress` 按 ≥1s 或每 10 页节流 + `_fail` 前置提交 + `persist_per_page` 开关；守护测试改用**文件 SQLite + 独立读连接**复现连接隔离（5 例先红后绿）。**2026-09-20 收尾轮真机证据已取得**：L4 运行中 `progress.parsed` 单调递增（0→1→2→3、9→16）、L5 单页失败不中断且已解析页保留、L6 kill 后真 MySQL 21 页行留存并按「无行 ∪ failed」续跑 16/16。**2026-09-20 用户裁决：本项不因真机证据关闭**（「DEFECT-001 不落库」= 不收口），仍开放的是 MySQL 级提交边界回归网：W3-b 双 Session 测试受 [ADR 0005](../../../adr/0005-mysql-integration-test-isolation.md) Draft 评审阻塞；关闭时回改 `project-status.md` 的「断点续跑基础」措辞

### ARCH-003 原证据列

> 代码与 mock 单测已交付（PLAN-002 Step 2-C），全本/逐页与单页失败不中断语义在测试内成立。**2026-09-20 收尾轮真机四条链路全绿**（整本 L3、逐页 L2、失败注入 L5、kill 续跑 L6 + 进度面 L4），DEFECT-002 的阻塞解除。**收口位置修订**：DEFECT-001 按用户裁决保持开放（回归网缺位属已知代价，不阻断本行的真机结论）；**2026-09-21 8.1 冒烟用例已固化并真机绿**（`tests/smoke/test_live_mineru_chain.py`），本行剩余关闭条件收窄为 **DEFECT-006/007 的语义成文与修复**（均需用户裁决 / ADR）

### ARCH-016 原证据列

> 直连配置已对齐（`QED_OCR_MODEL_URL` + 5002，Step 1/2-A，文档与脚本同步）；归一化 mock 单测通过；端点契约已按 3.4.4 改写并在真机两条路径（整本 PDF / 逐页 PNG）验证通过（L2/L3/L7）；引擎槽位分派已完成（`engines/factory.py`，随 DEFECT-003 关闭）。**2026-09-20 真机新发现两处归一化实质缺陷**：bbox 坐标空间误判（DEFECT-004）与 `header`/`footer` 字面值缺口（DEFECT-005），另 `title`/`equation`/`table` 类型真实分布未取证 → 本行改由「DEFECT-004/005 修复 + ARCH-009 代表书类型分布取证」关闭，不再由 W8 单点关闭。**2026-09-21 补**：端点面证据已固化为冒烟用例（8.1），本行与 DEFECT-002 解耦、继续开放。**2026-09-21 DEFECT-004 关闭**（`PageResult.bbox_space` 坐标空间契约 + `scale_bbox` 按空间换算，真机锚点复验 5/5 块 IoU 0.93 / 最大偏移 7px），本行剩余关闭条件收窄为 **DEFECT-005 类型映射修复 + ARCH-009 代表书类型分布取证**。**2026-09-21 DEFECT-005 关闭**（`header`/`footer` 与旧版 `page_header`/`page_footer` 并存入 `_TYPE_MAP`；`normalize_page` 逐条对账，`QualityMetrics.dropped_block_count` + `dropped_types` 使丢弃可见，`index` 只在存活块上连续编号；整页回退收紧为「引擎未回结构化条目」；11 例单元两轮红→绿），本行仅剩 **ARCH-009 代表书类型分布取证**（`title`/`equation`/`table` 真机未取证，合成小样不足）与真机零丢弃对账复跑（`QED_SMOKE_LIVE=1`，待 MinerU 起服务）

### DEFECT-006 原任务与证据列

> 任务：任务与页范围校验/状态语义未成文：① `pages` 无上界与存在性校验（486 页书接受 `pages=[40,999]` 并写出幽灵 `af_pages` 行）② `job.status=completed` 掩盖部分失败（有失败页仍 `completed`）③ 书级 `af_books.parse_status` 把「本轮任务结果」与「全书是否覆盖完成」混为同一字段
>
> 证据：来源：PLAN-003 收尾轮真机取证（L5：`L5_polls.txt` 显示 completed 且 `error=失败页：999`；`L9_book_state.txt` 显示真书 `failed 38/486`）。**待用户裁决**：是否新增「部分成功」字面值 or 明确「`completed` + `error`/页行」为唯一判据；`pages` 越界应 422 拒收。**运行库遗留**：本轮取证在真机 `qed.af_pages` 注入的 `linearalgebra-b01` 第 999 页 `failed` 幽灵行**尚未清理**（D 类运行库写操作，需用户批准，不自行删）

### DEFECT-007 原任务与证据列

> 任务：被中断的解析任务永久停留 `running`：编排进程被 kill 后无人回收 job 行（无 reaper/心跳/启动期归一），断点续跑实际只依赖 `af_pages`，`af_parse_jobs.status` 在此场景失真
>
> 证据：来源：PLAN-003 收尾轮 L6（kill 后 `5f43593a12cc` 至今 `running (parsed 14/30)`，见 `L9_final_jobs.txt`）。当前行为不阻断续跑（条文 9 的集合判定不看 job 行），但与 `database-design.md`「任务表语义」的状态描述不一致，且 `GET /parse-jobs/{id}` 会长期显示假运行。**下一条件**：属状态机契约，需 ADR 级裁决修复选项（启动期把遗留 `running` 归 `failed` / 心跳超时列 / 明确「遗留 running 为已知语义」成文）后实施

### DEFECT-008 原任务与证据列

> 任务：`.env` 解析不剥离**行内 `#` 注释**：`config._env_file_values` 只跳过整行注释，值后的注释会连同空格并入配置值 → 若上层 `.env` 写成 `KEY=value # 说明` 且本仓库 `.env` 缺该键，生效值即为坏 URL/坏路径
>
> 证据：来源：2026-09-21 PLAN-003 收尾二轮 G5 三段走查（根 `.env:20` `QED_OCR_MODEL_URL=http://127.0.0.1:5002   # 图像模型地址（5002）`，当前因本仓库 `.env:17` 优先生效未触发；留档 `data/probes/live-bringup-20260920/D4_G5_config_credential.txt`）。修复属 B 类一行改动 + 单测（值尾部 ` #…` 剥离，保持引号内 `#` 不误伤）；根仓库 `.env` 只读，不由本仓库改动

### ARCH-005 原证据列

> 质量信号字段随页产物落盘（Step 2-C），真机页产物与 `quality` 已在 L2/L3/L7 取证；对照视图待 8903 前端联调（根仓库 ARCH-020-D/ARCH-020-UI）。**2026-09-20 前置新增**：叠框依赖 bbox 正确性 → DEFECT-004（坐标空间）与 DEFECT-005（类型丢弃致块数不一致）修复前不得开始对照联调，否则前端按错误坐标定位会形成二次返工。**2026-09-21 前置收窄**：坐标空间面已随 DEFECT-004 关闭解除（块 `bbox` 已真机复验贴住原文字，IoU 0.93），本行仍受 **DEFECT-005** 阻塞（类型丢弃会造成同页块数与 `index` 漂移，对照两侧对不齐）。**2026-09-21 DEFECT-005 关闭**：归一化两处正确性缺陷（坐标空间 + 类型映射）均已修复，叠框与对照联调的代码侧前置解除；**但需在修复后代码上重解析代表书**再开联调（既有 `parsed/` 产物是修复前落盘，坐标与 `index` 均不回溯），且真机零丢弃对账复跑待 MinerU 起服务

### ARCH-006 原证据列

> 端点与 af_block_edits 仓储已实现（Step 2-C/2-E），契约测试全绿；真机读取面 L7 全绿（含 `image_url` 字面量不变量）；真机 af_block_edits 仍 0 行，edit 写入与 pages 合并回显待前端联调。**2026-09-20 前置新增**：`corrected_bbox` 与块 `index` 定位受 DEFECT-004/005 影响（下标漂移会污染编辑键），修复前不写入真机编辑数据。**2026-09-21 前置收窄**：`corrected_bbox` 的坐标空间已随 DEFECT-004 关闭恢复可用；仍受 DEFECT-005 的 `index` 漂移阻塞。**2026-09-21 DEFECT-005 关闭**：`index` 漂移根因消除（丢弃可见 + 存活块连续编号），但**编辑键不回溯**——修复前落盘产物的 `index` 含义已变，真机块编辑写入前必须先重解析（当前 `af_block_edits` 0 行，无迁移负担），联调排期与 ARCH-005 同步

### ARCH-009 原证据列

> 前 20 页冒烟 → 全量（公式可渲染 ≥ 90%），需 MinerU 5002 在线；~~前置 DEFECT-002 解除~~ **2026-09-20 真机证据已取得**（L2/L3）。剩余前置：~~DEFECT-004/005 修复~~ **两项已于 2026-09-21 全部关闭**（坐标空间 `bbox_space` 契约 + 类型映射与可见丢弃），归一化侧无已知正确性缺陷；本行现为 **MinerU 5002 在线 + 在修复后代码上重跑代表书页产物**（既有 `parsed/` 落盘块是修复前的错误坐标与旧 `index`，两项修复均不回溯历史产物）+ 真实 `title`/`equation`/`table` 类型分布取证（合成小样 40 项原始字面值只有 `text` 35 + `header` 5，`footer` 仅见于 `L3d_coord_space.json`，三者之外无样本，见 `L3b_raw_bbox.json`）

### REQ-001 原证据列

> 根仓库 2026-09-20「联调最小打通轮」已自行修复四处契约断裂（review→edit 门面、sync file_path、parse-jobs engine/id/progress、page_no/image_url 补写，见根仓库 BUGFIX-005）。**2026-09-21 本方复核材料已出**（PLAN-003「跨项目回执稿（W8-g）」：L7 九端点 + `image_url` 不变量 + 契约测试对照），两项延后告知已写明——① DEFECT-004 修复前块坐标不可用于叠框，② PLAN-004 版本布局后 `versions/<job_id>` 与生效指针需再回执。**2026-09-21 延后告知 ① 已解除**：DEFECT-004 关闭 + REQ-081 回执成文 → 块 `bbox` 恢复可用于叠框（新解析产物；历史产物需重跑）。**关闭条件**：根仓库读取材料并在其 todo 登记回执

### REQ-081 原任务与证据列

> 任务：**请求接收（QED-Engine）**——MinerU 解析块 bbox 坐标失真：前端对照框与原页文字不对齐（`GET /books/linearalgebra-b01/pages/7` 全部段落块 `x1` 恒等于 1241＝图像右缘，块高 1–3px 的病态窄块）
>
> 证据：2026-09-21 本方接收并判定：**与自家 DEFECT-004 同一缺陷**（根仓库从前端症状报，本方从适配层定因），根仓库怀疑的两条路径均成立。**答疑**：`content_list[].bbox` 实测为**相对页框逐轴归一化 0–1000**，既非 PDF 点也非像素；`middle_json.pdf_info[].page_size` 对 PNG 输入回的是按 200 DPI 折算的标称点（1241x1755 → `[446,631]`），与归一化 bbox **确实错配**，不能作缩放基准。**症状复算吻合**：把 0–1000 当点按 `image/page_size` 过放＝x 轴 1.68 倍、y 轴 1.19 倍 → 右缘/底缘精确夹取，正是 `x1=1241` 与贴底 1px 块。**修复与回执**：DEFECT-004 已关闭（`bbox_space` 契约 + 按空间换算；单测锚点判据 + 真机复验 IoU 0.93/偏移 ≤7px + 冒烟对齐断言，反转旧空间即红），回执正文见 PLAN-003「跨项目回执稿（REQ-081）」。**待本方**：`linearalgebra-b01` 受害页需真机重解析才能产出正确坐标（写真实 `dataset/parsed/` + 共享 `qed`，D 类，待用户批准；未批准前不自行重跑）。**2026-09-21 追加**：回执 caveat ②（块**类型**映射独立缺陷 DEFECT-005）亦已关闭——`header`/`footer` 不再被静默丢弃，同页块数与 `index` 稳定，重解析后前端可按 `index` 对齐叠框；坐标与类型两项修复都不回溯历史产物，故根侧复测须等本方重解析完成。**2026-09-21 重解析改判（用户裁决 5）**：受害页重解析**不在旧扁平布局上单独执行**，改为 PLAN-004 版本布局落地 + 真机 `af_*` 迁移窗口之后**单次完成**（前 20 页，含根仓库报告的受害第 7 页），以避免「先在旧布局解析、迁移后被 V7 判为 `legacy` 而作废」的真机功浪费。**给根仓库的时点承诺**：§13 复测（项 3 解析本页/全本 + 项 5 原页图 bbox 双向联动）需等本方「版本布局 + 迁移窗口 + 单次重解析」三步完成后本方发二次回执；在此之前根侧复测拿到的仍是修复前的错误坐标。**订正根侧前提**：其 REQ-081 证据列「b01 当前仅 2 页产物」已过时——本方收尾轮真机取证为 **38/486 页 `parsed`**（`af_pages` 第 3~40 页 + 第 999 页取证注入的 `failed` 幽灵行，见 `data/probes/live-bringup-20260920/summary.md` 与 PLAN-004 L9），受害范围按旧坐标推算为**全部已解析页**（不只第 7 页：坐标空间误判是全局换算错误，与页无关），故重解析须覆盖待展示页而非单页。**关闭条件**：根仓库以其 §13 验收复测通过后回执，本方随之关闭本行
