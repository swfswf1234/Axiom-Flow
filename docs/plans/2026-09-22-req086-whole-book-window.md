# 全本解析按页窗口分块提交与空白页合法化（REQ-086 / REQ-087）

状态：In Progress
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
| S1 | 全本 job 内每块提交**独立计 deadline**，单块超时/失败只损失该块（该块页行 `failed`），job 继续推进 | 契约测试新增「单块失败不置整本 failed（块级失败隔离；**不做块级重试**，见「实施登记」设计裁决）」 |
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
3. **W6 真机复测落 `qed_test` + 隔离探针数据根（用户 2026-09-22 批准实施并指定库）**：服务进程以
   `QED_DB_NAME=qed_test` 启动（真实环境变量优先于 `.env`，不改 `.env` 即回业务库），数据根指向
   探针目录（b05 源 PDF 复制进其 `raw/`，在其下 sync + ingest + 解析）。**业务库 `qed` 与真实
   `dataset/parsed/` 零写入**——原判据「写真实数据根 + 共享 `qed`」据此收窄，S3 的真机性不打折
   （真 MinerU、真 MySQL、真 317 页书）。副作用与恢复：`qed_test` 的 `af_*` 四表由本仓库自有
   （ADR 0005 边界，其 `qt_*` 镜像表不触达），复测留档在探针目录，随时整目录删除。
4. ARCH-019 映射目标**已由用户裁决为 (A) `PARAGRAPH`**（2026-09-22）⇒ W4 解除阻塞，见「承载 ARCH-019」。

## 工作项

| # | 工作项 | 产物 | 门禁 | 状态 |
| --- | --- | --- | --- | --- |
| W0 | 窗口协议冻结探测（①~④） | `data/probes/window-req086-20260922/` + 结论表 | 只读 | Done（④＝245s/56 页，见「实施登记」） |
| W1 | 引擎窗口参数 + 按块 deadline + 绝对页号还原 | `mineru.py` 改动 + `tests/unit/test_engines.py` 新增（先红：表单带窗口字段 0 基含两端、页号跨窗还原为绝对页号） | 单测 | Done（`TestWindowSubmission` 7 条） |
| W2 | 空白页合法化 + 质量标记 | `mineru.py`/`schemas.py`/`normalize` + 单测（先红：空 `content_list` 不再抛 `EngineError`；反向判据：`pdf_info`/`content_list` 双缺仍抛错） | 单测 | Done（`real-blank-only` 形状 + `TestBlankPageSignal` 3 条） |
| W3 | pipeline 串行窗口循环 + 块级失败隔离 + 按块进度 | `pipeline.py` + `tests/unit/test_orchestrator.py`（假引擎计数：每块一次提交、块失败仅该块页 `failed`）+ `config.py`/`jobs.py`/`api/main.py` 接线 | 单测 | Done（`TestWholeBookWindows` 6 条，含 `window_size` 接线守护） |
| W4 | ARCH-019 `ref_text` 映射补齐（带回归网） | `normalize/blocks.py` `_TYPE_MAP` + 丢弃对账单测 | 单测 | Done（`TestRefTextMapping`：映射 + 反向判据 `aside_text` 仍可见丢弃；抽样页 `dropped_types` 归零待 W6 取证） |
| W5 | 契约与文档晋升 | `tests/contract/test_api_v1_contract.py` 两条（S1/S2 判据）+ `parsing-pipeline.md` 条文 + `model-integration.md` 协议与配置键 + guides/standards 三处 600s 口径 + `code-map.md` | 契约 | Done（变异复验：撤掉块级隔离或取消分块后，契约两条与单测同时转红） |
| W6 | b05 全本真机复测（S3，**`qed_test` + 探针数据根**）+ 根侧回执 | 留档（版本目录、job 行、`_version_is_complete` 结果、空白页清单）+ 回执正文（REQ-086/REQ-087） | smoke/MySQL 层显式跑 | Blocked（S2 与「块级隔离/块间止退/ARCH-019 归零」已成真机直证，112/317 页。WSL 内存已抬到 23 GiB 且 `dmesg` 无新 OOM，但尝试 3 起 `mineru-api` 每 1~3 分钟被外部 SIGTERM 重启、Windows→`127.0.0.1:5002` HTTP 通路断开 ⇒ 提交前 ABORT（未占算力、未建新版本）。恢复条件＝①停掉反复重启容器者 ②恢复 5002 端口通路；责任位置＝根仓库 8900 / 本机 Docker 与 WSL 网络） |
| W7 | 收口（六步，ADR 0006） | 本计划关闭与归档 | 全量门禁 | Pending |

## 实施登记（2026-09-22 开工轮）

### W0 窗口协议冻结结论（判据：`data/probes/window-req086-20260922/`）

真机 MinerU 3.4.4，提交前 `/health` queued=0/processing=0（单占用自证），5 次提交全部 `completed`。
① ② 用**合成标记 PDF** 冻结（b05 全本无文本层，每页 `get_text()` 长度 0，无法用源文本反查页号；
页索引属 PDF 层，结论可迁移），③ ④ 用 b05 真书冻结。

| # | 假设 | 冻结结论 | 直接证据 |
| --- | --- | --- | --- |
| ① | `start_page_id`/`end_page_id` 端点性与基数 | **0 基、含两端**：`start=2,end=4` → `middle_json.pdf_info` 长度 3、md 命中 1 基第 3/4 页（第 5 页为样本空白页）；真书 `start=46,end=48` → 覆盖 1 基 47/48/49，其中 48 无条目 | `windows.jsonl` 的 `mk-a`/`real-blank` 行 |
| ② | 返回索引空间 | **窗口内相对（自 0 起）**：`content_list[].page_idx` 与 `pdf_info[].page_idx` 都是 `0…n-1`（`real-wide56` 提交 0/55 时恰好看不出来，故以 `real-blank` 与 `mk-*` 为准）⇒ 适配器必须按窗口起点还原绝对页号；**现实现按全书 0 基解释，是缺陷** | `pdf_info_idx` 全为 `0…len-1` |
| ③ | 空白页响应形状 | 任务正常 `completed`；空白页**只出现在 `pdf_info`**（`pdf_info_len` 含它），`content_list` 与 `md_content` **完全省略该页**；整窗只有空白页时 `content_list` 条目 0、`md` 0 字符、`pdf_info_len=1` ⇒ 现有 `_pages_from_result` 正是此时抛 `EngineError`，合法化判据改为**以 `pdf_info` 枚举页** | `real-blank-only`（1 基第 48 页）整行 |
| ④ | 56 页窗口真机耗时 | `start=0,end=55`（b05 前 56 页，6.2 MB 扫描版 dense 数学）：**245.0 秒**（55 次轮询），≈4.4 秒/页 ⇒ 默认 `parse_window_size=56`、`ocr_timeout` 语义改记「**单块**一次调用超时」后保持 600 秒默认（2.4 倍余量）；56 ≤ 容器 `processing_window_size=64`，一块落在单个处理窗内 | `real-wide56` 行 |

### 实施设计裁决（W1~W5 落地时定，2026-09-22）

1. **块级不重试**：最坏挂钟 = 块数 × 单块超时，块级重试把它翻倍；恢复路径是同 job 重跑续跑
   （`_resumed_pages` 只补未 `parsed` 的页）。逐页路径的「重试 1 次」保留，两层不互相冒充。
   据此本计划 S1 原措辞「（带重试）」已改正。
2. **块间探活即止**：每块提交前查 `engine.health()`，不健康即停止提交剩余块并把剩余目标页记 `failed`
   （文案 `MODEL_UNAVAILABLE`），避免僵尸任务逐个撞超时。
3. **失败域只到块**：块级 `EngineError` 走 `_mark_window_failed`（页行 `failed` + `progress.error` 写
   `窗口 {a}-{b}：{原文}`，终态 `af_parse_jobs.error` 同源）；`_handle`/落盘期的 `EngineError` 仍上抛到
   任务级 `_fail`，保持 ADR 0007 既有语义与测试不变。
4. **空白页判据**：以 `middle_json.pdf_info` 枚举窗口内页（冻结 ③），空白页产 `blocks=[]` 合法页；
   `QualityMetrics.blank_page = total == 0 and not dropped_literals`——映射缺口页不得伪装成白页
   （反向判据已入单测）。
5. **窗口大小接线**：`config.parse_window_size`（默认 56）→ `api/main.py` → `JobRunner(window_size=…)`
   → `run_parse_job(window_size=…)`；非法值（≤0）在 `_page_windows` 直接抛错，不静默改默认（ADR 0004）。

**空白页清单修正（W6 判据基准）**：本计划目标节原记「b05 空白页 47/48/49/98/136/162/282」为**误记**——
b05 无文本层，`textlen==0` 对全部 317 页成立，不能作空白判据。以「整页零墨」像素法重测，
真实空白页为 **1 基 48/98/136/162/282 共 5 页**（与根侧 REQ-087 所述「空白五页」一致）；
`real-wide56` 的 `page_idx` 缺 47（= 1 基 48）与之互相印证。S3 的「5 空白页计 parsed」按此清单判定。

### W6 真机复测登记（2026-09-22，留档 `data/probes/wholebook-req086-20260922/`）

驱动脚本 `scripts/tmp/live_wholebook_req086.py`：**真实环境变量**注入 `QED_DB_NAME=qed_test` +
`QED_DATA_ROOT=<探针根>`（不改 `.env`），解析前硬闸门断言二者到位；探针服务起在**独立端口 8912**
（不碰 `logs/qed-axiom.pid`，业务 8902 实例全程在位未受影响），全流程只经 HTTP 驱动。

| 事实 | 值 |
| --- | --- |
| 探针库迁移 | `qed_test` 由 `alembic upgrade head` 建 `af_*` 四表，版本 `20260921_0001`（与业务库同 head） |
| 源 PDF | 复制进探针根 `raw/math-advanced/math_analysis/数学分析原理_341544f3.pdf`，sha256 `341544f3…` 与 `af_books` 登记一致 |
| ingest | 317 页 150 DPI，**27.0 秒**，页图 317/317 全在探针根内 |
| 业务侧零写入自证 | 窗口前后比对业务库 `qed` 的 b05 行 + 真实 `dataset/parsed/.../mathanalysis-b05` 目录（4449 文件 / 945,726,371 字节 / 最新 mtime），逐项一致 |

**尝试 1（job `c3fffbb40608`，归档 `attempt1-container-restart/`）＝外部依赖事件，判据不成立**：
提交 120 秒后终态 `failed`。页行分布给出两份**设计行为的真机直证**——

- 窗口 1（1~56 页）：`窗口 1-56：MinerU 轮询失败（连续 3 次）：[WinError 10054]` ⇒ **失败域只到块**；
- 其余 261 页：`模型服务不可用：请经 8900 /models/{slot} 启停模型后重试` ⇒ **块间探活即止**，
  未逐个撞 6×600s 僵尸挂钟（若按旧「整本单任务」实现，此处只会得到一个无差别的整本超时）。

根因在容器侧：`mineru-api` 于 `2026-09-22T03:59:55Z` 退出（`ExitCode=0`）并被 `--restart=unless-stopped`
拉起（`RestartCount=4`，`OOMKilled=false`），重启后 `/health` 计数归零。模型生命周期归根仓库 8900
（ADR 0004），本仓库不改容器；恢复条件＝容器稳定在线且 `queued+processing==0`，责任位置＝根仓库
8900 侧。据此重跑属**失败恢复**，不是「为补证据再解析一次」——「验证与验收」的「真机验收只跑一次全本」
约束的是**成功之后**补证据的情形。

**尝试 2（job `53e1dedaa8ab`，归档 `attempt2-oom-window3/`）＝同一外部依赖事件，走到 112/317 页**：

| 观察 | 值 / 结论 |
| --- | --- |
| 进度采样（S2 判据） | `+50`（120.2s）→ `+60`（180.3s）→ `+2`（200.3s），单调递增且非零 ⇒ **S2 成立**；跳变粒度是页级 commit + 进度节流，块边界（56/112）在终态对账中一致 |
| 终态 | `partial`，`progress={total:317, parsed:112, failed:205}`，未激活（`active_job_id=null`，`last_parse_result=partial`）——目标页未齐备时不激活，符合 ADR 0007 |
| 失败域 | 窗口 3（113~168）56 页：`窗口 113-168：MinerU 轮询失败（连续 3 次）：[WinError 10054]`；其余 149 页：`模型服务不可用…`（**块间探活即止**，与尝试 1 同形） |
| 已解析部分的质量对账 | `dropped_block_count_total=0`、`dropped_types=[]`（**ARCH-019 在真机上归零**）、空白标记 `[48, 98]`（恰为清单中落在前 112 页内的两页）、112 页 `blocks.json`+`md` 与 `chunks.json`/`manifest.json` 全在位 |
| 业务侧 | `business_zero_write=true`（业务库行与真实 `dataset/parsed/` 指纹逐项一致），`qed` 与真实产物全程未被写过 |

根因定位到**宿主机内存闸门**，非本仓库代码：`wsl dmesg` 给出两次 `global_oom` 直接击杀——

```
Out of memory: Killed process 17155 (mineru-api) ... anon-rss:11253480kB   ← 内核时戳 64559
Out of memory: Killed process 47927 (mineru-api) ... anon-rss:9653556kB    ← 内核时戳 65093
```

两条相差 534 秒，与两次容器重启（`03:59:55Z` / `04:08:49Z`，`RestartCount` 4→5，`ExitCode=0` 后由
`--restart=unless-stopped` 拉起）的间隔**逐秒吻合**，故两次失败同为 WSL 全局 OOM。WSL VM 只分到
15 GiB（未设 `.wslconfig memory=`，取宿主 32 GiB 的默认一半），而 MinerU hybrid（vLLM async-engine +
外部 Layout + OCR-det）单进程 RSS 峰值已达 9.6~11.2 GiB，**连续窗口负载下必然触顶**；空闲 3 小时
无重启（容器 `Up 3 hours`）反向印证触发条件是解析负载而非时间。dmesg 另有更早两条同类击杀
（时戳 16962/18898），说明这是本环境的既有条件，不是本轮改动引入。

**用户裁决 2026-09-22**：恢复路径＝把 WSL 内存抬到 24 GiB 后**单跑**全本（`.wslconfig` 在本仓库工作区
之外，由用户手工修改并 `wsl --shutdown` 生效；`[wsl2]` 加 `memory=24GB`）。窗口大小维持 W0 冻结的 56
不改——OOM 属环境容量问题，不以缩小业务参数来绕（若抬内存后仍 OOM，再按参数侧改判并回写 W0）。
W6 的 S3 判据在此之后复跑判定；本轮留档已把「块级失败隔离 + 块间止退 + 已解析页质量归零」三条
钉成真机直证。

**尝试 3（14:52–15:08，`run-3.log`）＝未提交任何解析即 ABORT，环境出现新的第二个故障面**：
`.wslconfig memory=24GB` 已生效（`wsl free -g` = 23 GiB），本轮 `dmesg` 无新 OOM 记录，但
`_wait_model` 在 900 秒内拿不到 `/health` 的 200，脚本在**提交解析之前**退出——
因此没有新 `job_id`、没有新版本目录、MinerU 未被占用，`qed_test` 仍是尝试 2 的 112/317 `partial`。

排查记录（都指向容器/网络侧，不在本仓库）：

| 现象 | 证据 |
| --- | --- |
| 容器被反复停起 | `docker logs --timestamps` 里 `INFO: Shutting down` 共 17 次，14:50 之后每 1~3 分钟一次（`Up 2/15/27 seconds` 采样）；uvicorn 收到的是 **SIGTERM**（干净退出 `ExitCode=0`），而 `RestartCount` 始终为 **0** ⇒ 拉起者不是 `--restart=unless-stopped` 策略；本轮据此猜过「外部 actor 在执行 stop/start」，**该归因已被下文更正** |
| 服务本身没问题 | 容器活着的窗口内，WSL 侧 `curl http://127.0.0.1:5002/health` 返回 200、容器 IP 直连也 200，`ip_forward=1`，健康检查 `exec_die exitCode=0` |
| Windows→5002 断 | Windows 侧 HTTP 全程失败；`netstat -ano` 里**没有 5002 条目**，TCP 能连上是因为打到 WSL 里的 `docker-proxy` 监听（容器一停 HTTP 即失败）。12:05 之前同一路径可用 ⇒ `wsl --shutdown` 清掉了原有的端口暴露路径 |
| 根侧判据也失真 | 根仓库 8900 `GET /api/v1/monitor/mineru` 回 `{"reachable":false,"reason":"mineru docker 容器未运行…"}`——Windows 的 `docker` 管道（Docker Desktop）已死（`dockerDesktopLinuxEngine` npipe 不存在），而容器实际跑在 `Ubuntu-24.04` 的原生 dockerd 里，该判断看不到它。若某处存在「探到没起就重启」的循环，即为本现象来源 |

**归因更正（16:01–16:06 直证，留档 `_env_actor.txt` / `_env_stopper.txt` / `_env_unit.txt` /
`_env_wslps.txt`）＝不是「外部 actor 重启容器」，是 WSL 在空闲时把整个发行版关掉**：

| 证据 | 值 / 结论 |
| --- | --- |
| 停机是 systemd 的正常关机序列 | `journalctl`：16:03:33 `Stopping docker.service…` → 16:03:34 `Daemon shutdown complete` → `Reached target final.target - Late Shutdown Services`，随后 16:06:27 重新出现内核 `Command line: initrd=\initrd.img WSL_ROOT_INIT=1 …` 启动行 ⇒ **关掉的是 VM/发行版**，不是某个脚本在 `docker restart`；这同时解释了尝试 3 观察到的 `RestartCount=0` + `ExitCode=0`（容器是被 dockerd 优雅停掉的） |
| 触发条件是「最后一个 Linux 进程退出」 | 每次关机前一条都是 `login[…]: pam_unix(login:session): session opened`（即我的一次性 `wsl bash -lc …` 探测），会话结束十几秒后 VM 关机；`docker.service` 单元本身 `Restart=always`、`NRestarts=0`、无 `StopWhenIdle`，`docker.socket` 也无空闲配置 ⇒ 不是 unit 级自动停机 |
| `vmIdleTimeout` 未挡住该行为 | `.wslconfig` 里 `vmIdleTimeout=2147483647` 在位、`memory=24GB` 已生效（23 GiB、`dmesg` 无新 OOM），仍出现分钟级空闲关机 ⇒ 该键在本机当前 WSL 版本上不生效（机器侧待查项，本仓库不动 `.wslconfig`） |
| 端口通路随 VM 生死 | VM 关闭 ⇒ mirrored networking 的 `:5002` 转发消失（`netstat -ano` 无 5002 条目，Windows 侧 `RemoteDisconnected`/`10054`）；VM 保持活动时连续 12 次 `/health` 采样全 200 |
| 反证＝唯一需要的动作 | 起一个常驻 Linux 进程滚动续期（`while: wsl bash -c "sleep 600"`）后：dockerd 未再重启、`/health` 连续 5 分钟 200、`queued+processing` 只被我自己的 job 占用 ⇒ 尝试 4 得以**提交成功**（`job=652a3e3be33c`），而尝试 3 连提交都没做到 |

**据此 W6 的恢复条件由两条收敛为一条（原判断被更正）**：解析窗口全程必须有一个常驻 WSL 进程存活，
否则 VM 会在块与块之间的空闲窗口被关掉，表现为「MinerU 轮询 `10054` + 块间探活即止」——这与尝试 1/2
观察到的失败形态同源，也说明尝试 2 的 205 页失败**不能全部记在 OOM 头上**。该前置是机器侧配合，
**不改任何业务参数**（窗口仍 56，ADR 0004：本仓库不碰容器编排与机器配置）。

`_env_ps.txt` / `_env_inspect.txt` / `_env_logs.txt` 等同目录文件为本轮排查的原始输出留档。

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
**状态**：In Progress（2026-09-22 用户裁决映射目标取 **(A) `PARAGRAPH`**，W4 解除阻塞并开始实施）。
**裁决输入**：引擎字面值 `ref_text` = 参考文献条目。三个候选 `BlockType`——
(A) `PARAGRAPH`：最省事，但丢失「条目」语义，前端无法折叠成参考文献列表；
(B) `LIST`（`items[]` 每条一个文献条目）：语义最贴，代价是同页条目需聚合为单块，改变块下标 `index`
（块编辑键受影响，ARCH-006 需知悉）；
(C) `CAPTION`：不建议，语义错。
**裁决结果 (A)**：丢弃归零、块下标 `index` 与编辑键零影响、可 `git revert` 回退；(B) 的「折叠成参考
文献列表」属渲染面需求，根侧提出后再议（届时须评估块下标漂移对 ARCH-006 编辑键的影响）。
`heading`/`list` 两键在两本抽样内未命中属**取证事实**，不是缺陷，不为其加映射。
