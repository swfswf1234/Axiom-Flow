# 运维指南

状态：Current
最后更新：2026-09-23

## 8902 API 服务生命周期

本仓库 8902 API 服务（`uvicorn axiom_flow.api.main:app`，Windows 侧）启停由生命周期脚本
`scripts/axiom_flow_service.py` 管理（契约见 [8902 API 接口文档](../architecture/api.md) ①生命周期与健康）：

```powershell
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py start --wait
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py status
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py restart --wait
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py stop
```

- 就绪判据：`GET /api/v1/health` 返回 200（8900 控制中心与 `start-all.ps1` 探测路径一致）。
- PID 文件 `logs/qed-axiom.pid`，子进程日志 `logs/qed-axiom-serve.log`。
- 端口来源：`QED_AXIOM_URL` 解析 > `AXIOM_PORT` > 8902。
- 配置语境：脚本不再传 `--mode`（2026-09-17 配置简化，PLAN-002）——解析始终直连模型服务
  （[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)），配置由 `config.py`
  从仓库根向上走查 `.env` 自读；变量清单见[开发指南](development.md)。

### 停止可靠性语义（2026-09-04，与根仓库 8900/8903 脚本同构）

- 判活用内核句柄探测（`_proc_alive`，探测失败不再误判为「已死」）；优雅信号（CTRL_BREAK）
  未生效由 taskkill 强杀兜底成功时回显 `stopped (forced)`——正常现象，conda run 等跨
  console 语境常见，无需处理。
- 优雅+强杀后仍存活则退出码 1 显式报 `stop failed`（绝不假 stopped）：按提示手动
  `taskkill /PID <pid> /T /F`，确认进程退出后重试 `stop` 清理 PID 文件。
- 退出码：`0` 成功/幂等、`1` 运行失败（含 stop 无法终止）、`2` 参数错误。

## 推理基础设施（根仓库托管）

`mineru-api`（MinerU FastAPI 单体，内嵌 vLLM，hybrid-engine，GPU 穿透）与容器启停脚本
（`compose.yaml`、`infra-up/down/status/reset.ps1`、`Dockerfile`）已随 V2-014 移交 QED-Engine
根仓库 `scripts/image-model/`（2026-08-20）；本仓库不再维护容器脚本，只读引用其文档。

- 调用路径：Axiom-Flow 经引擎适配器**直连模型服务**（MinerU `QED_OCR_MODEL_URL`，默认
  `http://127.0.0.1:5002`，健康端点 `/health`），不经 8900 网关；引擎与地址配置见
  [model-integration](../design/model-integration.md)。模型生命周期（启停/探针/资源互斥）由
  8900 `/models/vision` 托管，本仓库只连接、不启停。
- 容器启停、健康检查、GPU 穿透与 WSL keepalive 排障以根仓库 `scripts/image-model/` 为准，
  本仓库不复制其正文。

## 数据目录

| 路径 | 内容 |
| --- | --- |
| `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/` | **根视图**：`book.json`、`pages/p<NNNN>.png`（150 DPI 页图正本，ingest 产物）、`manifest.json`；重新 ingest 只原子替换这三者 |
| `…/<book_id>/versions/<job_id>/` | **一次解析任务 = 一个版本**（PLAN-004 / REQ-080）：`book.json`、`pages/p<NNNN>.{png,md,blocks.json}`、`chunks.json`、`manifest.json`；生效版本由 `af_books.active_job_id` 指认，页图为根视图页图的**硬链接**（同一页图在 N 个版本不重复占盘） |
| `<QED_DATA_ROOT>/raw/<domain_id>/<course_id>/*.pdf` | 源书库（只读，QED-Tracker 写入；如 `math-advanced/math_analysis/`） |
| `<QED_DATA_ROOT>/tmp/` | **本服务不写**：引擎中间产物只在内存（[PP-6](../design/parsing-pipeline.md)），`tmp/axiom-flow/<job_id>/` 一旦存在即缺陷 |
| `data/` | 仓库内探针与合成样本留档区（`data/probes/`，`.gitignore` 内不入版本库）；**不作数据根候选**，真实数据根唯一事实源是 `QED_DATA_ROOT` |

产物只落 Windows 本地；WSL 不读写 Windows 文件系统（MinerU 容器不挂载 Windows 盘）。

老版本布局（扁平产物）的兼容：升级与迁移**不搬移**旧产物、不改 `af_pages` 行内容，端点读到
`versions/<job_id>/` 缺失时按 `af_books.active_job_id IS NULL` 走**根视图**（旧布局的
`pages/p<NNNN>.{md,blocks.json}` 与 `chunks.json` 直接可读），因此重解析前的窗口内 8900/8903
功能不中断。

## af_* 迁移单窗口 runbook（PLAN-004 / REQ-080，**D 类：执行前须用户批准**）

`20260920_0002` 改四表结构（生效指针 + `job_id` 作用域），且**必须与新代码同窗口发布**——ORM 声明了
`active_job_id` 而库未迁移时读列即故障。顺序固定为 B（本地全绿）→ C（单窗口）→ D（版本布局上单次
重解析），**不在旧布局上重复真机解析**（避免解析完即被回填成 `legacy` 而失效）：

```powershell
# C-0 窗口排他（本机 GPU 只容得下一个解析；与 8900 侧约定静默后再开工）
#   **占用判据以模型侧计数为准**：GET http://127.0.0.1:5002/health → queued_tasks + processing_tasks == 0
#   本机侧只判 af_parse_jobs 有无 `queued` 行；服务重启后遗留的 `running` 行是**无人推进的孤儿**
#   （DEFECT-007），不阻塞窗口、只登记取证（2026-09-21 R1 实证：孤儿空闲 106683 秒）。
#   **PLAN-006 起此类孤儿行由冷启动的启动期归一自动改判 `interrupted`**（见下节），窗口脚本
#   仍把 `queued` 当作占用信号——只有真正在排队的任务才算占用
#   窗口期间不得经 8900 重启/换起 vision 槽位（2026-09-21 R1 实证：重启令排队与在飞任务同时判死）
# C-1 停服（避免运行中写库）
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py stop
# C-2 备份 af_* 四表（含 alembic_version），例：导出到带日期的 .sql 后核对行数
& mysqldump --single-transaction qed af_books af_parse_jobs af_pages af_block_edits alembic_version > af_backup_YYYYMMDD.sql
# C-3 迁移（仓库根目录，读 .env 的 QED_DB_*）
& D:\software\anaconda3\envs\QED_env\python.exe -m alembic upgrade 20260920_0002
# C-4 校验：版本行 + 存量作用域 + 生效指针恒 NULL（存量不自动改语义）
#   alembic_version = 20260920_0002；SELECT DISTINCT job_id FROM af_pages;（预期 legacy 或单书唯一 job）
#   SELECT book_id, active_job_id FROM af_books;（预期全 NULL）
# C-5 清理 af_pages 幽灵行（第 999 页等无产物行）——**2026-09-21 取消**：D 步 re-ingest 按 V8
#     只作废 legacy 一批（`reset_legacy`），取证由窗口脚本 S1_before.json 承担，不手工 DELETE
# C-6 起服并只读冒烟
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py start --wait
# C-7 窗口前置取证（不写不解析：就绪轮询 + 部署自证 + 越界 422 门槛复验）
& D:\software\anaconda3/envs\QED_env\python.exe scripts/tmp/live_reparse_b01_pages.py --check-only
```

- **窗口工具一律直连本机端口（`trust_env=False`）**：Windows 注册表代理开启后**不豁免** `127.0.0.1`，
  `httpx` 默认跟随环境代理会让 5002/8902 的探活全部变 **502**（同刻 `curl` 直连仍 200），窗口可因此
  空转到截止。适配器侧的同一条不变式见 [model-integration](../design/model-integration.md) 的
  「直连，不经系统代理」条；`scripts/tmp/` 下的
  窗口脚本与人工 `curl` 之外的一切自动化都必须显式关闭 `trust_env`。

- **模型就绪不以 8900 的一次提示为准**：5002 的 vLLM 权重加载是分钟级，而根侧前端 `operateModel`
  轮询截止只有 15s（`web-ui/src/stores/runtime.ts`），超时即显示「失败」但容器仍在起。判据用
  `GET http://127.0.0.1:5002/health` **轮询到 200**（窗口脚本已内置，默认 300s 截止）。

- **回滚**：`python -m alembic downgrade 20260917_0001` 是**有损**操作（旧结构无法表达多版本，按
  `(书, 页)`/`(书, 页, 块)` 只保留 `job_id` 最小的一行），因此回滚优先用 C-2 的备份还原；产物文件
  不随迁移删除，DB 与文件解耦。
- **D 步（单次重解析）**：`python scripts/tmp/live_reparse_b01_pages.py 1 20` —— 脚本**只经 8902 API**
  驱动（提交期门槛、生效指针、只读端点一并取证），先按需 re-ingest 补齐根视图页图（**根仓库
  2026-09-21 已清空 `dataset/parsed/` 的 4 本旧代际产物，故页图必须重渲染；`af_pages` 里的旧
  `parsed` 行描述的文件已不存在**），再提交一次解析任务；产物落 `versions/<job_id>/`，成功后
  `active_job_id` 指向该版本；重解析同时作为 PLAN-003 W8 的续跑证据。
- **补证据不得再解析一次**：解析成功但取证段失败时，用 `--evidence-only <job_id>` 只读重取
  （跳过 ingest/门槛探针/C-0/提交）。单次重解析是用户裁决的窗口约束，重新解析会制造新版本目录并
  让已激活指针被移动。首次窗口（2026-09-21）即以此方式补齐 `S2_after.json`。
- **探针窗口（`qed_test` + 隔离数据根，PLAN-007 W6 起可用）**：`python scripts/tmp/live_wholebook_req086.py`
  用**真实环境变量**把库换成 `qed_test`、数据根换成 `data/probes/<窗口目录>/root`（不改 `.env`，退出即回
  业务口径），并在解析前硬闸门断言二者到位；探针服务起在独立端口（默认 8912，**不写 `logs/qed-axiom.pid`**，
  与业务 8902 并存）。脚本自带「业务侧零写入」前后比对（业务库行 + 真实 `dataset/parsed/` 目录指纹），
  不一致即判 FAIL。**失败重跑不算「为补证据再解析一次」**——那条约束的是成功之后。

- **模型容器与 WSL 环境本身归 QED-Engine，不归本仓库（ADR 0004 + 2026-09-22 PLAN-007 W7 用户裁决）**：
  下面两条判据只用于让本仓库真机窗口能区分「这不是本仓库代码的失败」并把现象**交接**给根侧图像模型
  维护线；容器编排、`.wslconfig` 容量与发行版生命周期由根仓库环境线负责，本仓库不投入、不改这些配置。
- **模型容器「中途自己重启」的判据是 WSL 全局 OOM，不是本仓库超时**：长解析中途出现
  `[WinError 10054] 远程主机强迫关闭了一个现有的连接`（连续 3 次）+ 剩余页 `MODEL_UNAVAILABLE` 时，
  先查 `wsl docker inspect mineru-api --format '{{.RestartCount}} {{.State.StartedAt}}'` 与
  `wsl dmesg | grep -i -E "oom|killed process"`。若 dmesg 有 `Out of memory: Killed process (mineru-api)`
  （RSS 峰值 9.6~11.2 GiB）而 `free -g` 显示 VM 只有 15 GiB，则是宿主内存闸门：MinerU hybrid
  （vLLM + Layout + OCR-det）单进程在连续窗口负载下触顶，`ExitCode=0` 后由 `--restart=unless-stopped` 拉起。
  处置属环境容量（`.wslconfig` 的 `[wsl2] memory=` 在本仓库工作区之外，只能由用户改后 `wsl --shutdown` 生效），
  **不以缩小 `AXIOM_PARSE_WINDOW_SIZE` 绕开**——那会把容量问题伪装成参数问题。
- **同一现象的第二种根因是 WSL 空闲回收发行版，不是 OOM**：若 `RestartCount` 始终为 **0**、`ExitCode=0`、
  uvicorn 存活约 14 秒后消失且 `netstat -ano` 里查不到 `:5002` 监听，则容器不是被 `--restart` 策略拉起的，
  而是整个发行版被关掉后随下一次 `wsl.exe` 调用重新开机。判据在 journald 而非 dmesg：
  `wsl journalctl -b 1 | grep -E "Stopping docker|final.target|Kernel command line"` 同时出现
  「Stopping docker.service」与新的内核开机行即确诊；触发条件是**最后一个 Linux 进程退出**
  （`[boot] systemd=true` + mirrored networking 下发行版约 15 秒后停机）。`.wslconfig` 的
  `vmIdleTimeout=2147483647` 在该环境下**不生效**，别指望配置项。处置＝解析窗口全程让一个常驻 Linux
  进程存活：`scripts/tmp/live_wholebook_req086.py` 自带该前置（`--wsl-holder 300` 默认开，滚动重拉
  `wsl bash -c "sleep 300"`，退出时收掉；`--wsl-holder 0` 显式关闭并只留 WARN），手工驱动容器时可用
  `nohup bash -c 'for i in $(seq 1 40); do wsl bash -c "sleep 600"; done' &` 顶同样的位置。
  该处置属机器侧配合，**不改任何业务参数**（窗口/deadline 原样）；脚本里的 `--wsl-holder` 只是本仓库
  探针窗口的自便实现，**不代表本仓库接管 WSL 环境**——根侧长驻服务链路若见同一形态，按上一条自查。
  两条根因用 `dmesg` 有无 `Killed process` 区分：有＝上一条（容量），无而 `RestartCount=0`＝本条（空闲）。
- 迁移期间 8902 离线：8900 返回 503 并降级显示（独立性铁律），不影响配置域与其他界面。

## 单实例前提与启动期归一（PLAN-006 / ADR 0007）

**部署边界（显式承担，不是疏漏）**：8902 按**单实例**运行。任务是后台线程内的单 worker 队列，
`af_parse_jobs.status` 的 `queued`/`running` 只描述「本进程在推进它」；一旦起第二个实例，
它的启动期归一会把第一个实例**正在跑**的任务一并判成 `interrupted`。要多实例必须先重开这条
裁决（引入心跳/租约，`queued`/`running` 需可证活）。

- **归一时刻**：`api/main.py` 的 lifespan 在**受理首个请求之前**调用 `runner.reconcile_on_startup()`，
  把上次进程遗留的 `queued`/`running` 任务行改写为 `interrupted`（`error` 写「服务重启中断，可重跑」，
  置 `finished_at`），书目侧进度回 `pending`。**结局只在该书最新一次任务时置 `interrupted`**——更早的
  中断行不得覆盖后来的真实结局（`last_parse_result` 语义）。
- **可观测**：归一发生时会打 `WARNING 启动期归一：N 个遗留活动任务改写为 interrupted`；N=0 时静默。
  只想确认现状不必起服：`SELECT job_id, status FROM af_parse_jobs WHERE status IN ('queued','running')`。
- **`interrupted` 的续跑语义 = 同一 `job_id` 重放**，不新建版本：版本作用域是 `(book_id, job_id)`，
  已 `parsed` 且产物在位的页跳过引擎调用，只补剩余页；重放前**不需要**手工清行、也不需要删版本目录。
  **但重放入口目前只有代码路径**（`orchestrator.run_parse_job(job_id=…)`，`scripts/tmp/` 窗口脚本按此
  用法）：`POST /parse-jobs` 每次生成新 `job_id`，按既有 `job_id` 续跑的端点**至今没有承载行**——原挂
  本方 `REQ-087`，2026-09-22 该号让给根侧「空白页合法化 + 跨版本续跑语义」后（本方端点行改号
  `REQ-088`），续跑端点本身不再归属任何在办计划，PLAN-006/007 都未开放它。因此真机上被打断的版本
  要么经脚本重放、要么重新提交为一版（旧版保留不删）。
  **定位待重放任务不必再猜 id**：`GET /api/v1/parse-jobs?status=interrupted&status=partial` 直接给出
  清单（含 `failed_pages`），`GET /api/v1/books/{id}/pages` 给出该作用域逐页形态（PLAN-008 / REQ-088）。
- **`20260921_0001` 迁移窗口**（D 类，执行前须用户批准）：停服 → 备份 `af_*` 四表 →
  `python -m alembic upgrade 20260921_0001` → 校验 `alembic_version` 与
  `SELECT book_id, parse_status, last_parse_result FROM af_books`（预期进度全 `pending`、
  存量终态值平移进 `last_parse_result`）→ 起服。**降级有损**：`partial` 在旧结构无对应值，
  回滚时按旧语义归 `completed`。**已于 2026-09-22 R2 窗口在真机 `qed` 执行完毕**（备份
  `data/backups/window-20260922/af_backup_20260922.sql`，起服后启动期归一把孤儿行
  `5f43593a12cc` 改判 `interrupted`，见 PLAN-003 R2 W-A）。

## 书目删除的落库与磁盘口径（2026-09-22 D3 真机演练测定）

`DELETE /books/{book_id}` 是 D 类数据操作（不可逆），演练判据（合成 5 页探针书，真实书目零改动）：

- **删除**：`af_pages` 页行、`af_block_edits` 编辑行、`af_books` 书行，以及整棵书目录
  （`versions/` 全部版本 + 根视图页图）；`GET /books/{id}` 随后 404。
- **保留（审计）**：`af_parse_jobs` 的历史任务行**不删**，`GET /parse-jobs/{id}` 删除后仍 200 ——
  任务历史是跨书目的运营留痕，不随书目生命周期消失。
- **不裁剪空父级目录**：如 `parsed/probe/d3-20260922/` 删除后其父 `parsed/probe/` 留在盘上。
  契约只承诺书目录，空目录不算残留缺陷；若要裁剪须先改 `design/parsing-management.md` 的删除契约。
- **回滚**：无「撤销删除」路径，只能按备份还原 `af_*` + 重新 ingest/解析重建产物；执行前必须先
  导出 `af_books/af_pages/af_block_edits/af_parse_jobs` 四表并核对行数。

## 管理面一键清除 runbook（`POST /api/v1/admin/purge`）

契约与不变式见 [api.md「非公开管理面」节](../architecture/api.md)（PLAN-010 / ARCH-024）。这里只写
操作口径。**它与 `DELETE /books/{id}` 的分工**：后者是面向消费方的常规删除，保留 `af_parse_jobs`
审计行；前者是「先清后建」的运维重置，**连任务行一起清**、可按书目或全项目，且默认不挂载。

1. **启用**：`.env` 加 `QED_ADMIN_ENABLED=1` 与 `QED_ADMIN_TOKEN=<自行生成的长随机串>`，重启服务。
   只配 `ENABLED=1` 不配 token ⇒ 启动期 `AdminConfigError` 直接拒绝起服（fail-closed，不留无凭据的
   删除接口）。用完把两行删掉再重启，让路由回到 404。token 绝不进 git、日志与任何回执文本。
2. **演练**：`dry_run` 缺省即 true，响应给出 `targets`（相对 `parsed/` 的书目录）与将删计数。
   把这份清单留存为本次操作的证据；数字与预期不符就停手，别按真删往下走。**核对 `targets` 的层数形状**：
   每条都应是**书目录本身**，而不是它下面的 `pages/` 或 `versions/…`——出现后者说明目录识别被误判
   （`domain_id`/`course_id` 为空时书目录只有两层，判例 ARCH-026 / PLAN-011），此时真删会留下
   `book.json`/`manifest.json` 与空目录，必须停手。
3. **备份**：导出 `af_books`/`af_parse_jobs`/`af_pages`/`af_block_edits` 四表并核对行数——本端点
   **没有 undo**，产物目录只能靠重新 ingest + 解析重建。
4. **真删**：`{"dry_run": false, "confirm": "PURGE", "scope": …}`。书目行落点越出 `parsed/` 返回
   400（零删除）；作用域内有 `queued`/`running` 任务返回 409（零删除）——**落点判定在先**，收到 409
   不代表库里干净，按 400 的条件再核一遍落点。响应 `failures` 非空就是
   **没删干净**，按条目重跑同一请求即可（幂等），不要当作成功继续。
5. **复核 + 回滚口径**：`GET /api/v1/books` 应为空或只剩未清除的书；重建走 `POST /api/v1/books/sync`
   （书目从根库快照回来）→ ingest → 解析。

**作用域纪律**：`scope.all=true` 打在真实数据根（`QED_DATA_ROOT=D:\coding\QED-Engine\dataset`）上就是
**全项目清除**，属另一次 D 类批准，须用户逐次点头；演练与联调一律用 `qed_test` + 独立探针数据根。

## 故障排查

| 现象 | 处置 |
| --- | --- |
| `stop` 回显 `stopped (forced)` | 优雅信号（CTRL_BREAK）未生效（conda run 等跨 console 语境常见），taskkill 强杀兜底成功——正常现象，无需处理 |
| `stop` 回显 `stop failed: pid …` | 按提示手动 `taskkill /PID <pid> /T /F`，确认进程退出后重试 `stop` 清理 PID 文件 |
| API 返回 503（模型服务不可用） | 确认 MinerU 服务在线：经 8900 `/models/vision/start`（旧名 `/models/mineru/start`）或根仓库 `scripts/image-model/` 脚本；再确认 `QED_OCR_MODEL_URL`（5002）可达 |
| 任务显示 `interrupted` | 上次进程遗留的活动行被启动期归一改判（不再假装 running），**不是**引擎故障。核对 `failed_pages` 与 `versions/<job_id>/` 在位页后按上节续跑；版本未激活属预期（激活判据未满足） |
| 任务显示 `partial` | 有页失败但任务正常结束：`GET /parse-jobs/{id}` 的 `failed_pages` 给出完整页码，`af_pages` 逐页 `error` 给出原因；生效指针**不会**指向该版本（判据未齐备），产物保留在 `versions/` 供排查。**批量定位**：`GET /parse-jobs?status=partial&status=failed&status=interrupted` 列全部待处理任务；「哪几页要重跑」直接读 `GET /books/{id}/pages`，判据一条 `not has_artifact or status == "failed"`（PLAN-008） |
| 只读端点 404 `生效版本产物缺失：<book_id> @ <job_id>` | `af_books.active_job_id` 指向的版本目录被外部删除/未落盘（不回落根视图，ADR 0004）。核对 `versions/` 是否在盘、`GET /books/{id}/versions` 各行 `parsed`；确认产物不可恢复后重新解析或把指针置 NULL（`UPDATE af_books SET active_job_id=NULL WHERE book_id=…`，置 NULL 属数据修正，按 D 类先备份） |
| 容器已起但解析慢/超时 | 由根仓库 `scripts/image-model/` 排查 GPU 穿透与 vLLM warmup（首次约 1 分钟）；超时读 `AXIOM_OCR_TIMEOUT`（默认 600s）且**按块计**——整本挂钟上限 = 块数 × 600s，单块超时只损失该块页（`partial`），调大 `AXIOM_PARSE_WINDOW_SIZE`（默认 56 页/块，须 ≤ 容器 `processing_window_size`）可减少块数与提交开销 |
| 模型异常 | 由根仓库 `scripts/image-model/` 重建容器/镜像 |
| WSL 不可达 | `wsl -l -v` 确认状态；容器由根仓库 `scripts/image-model/` 脚本恢复 |

## 数据操作分类

日常小规模数据操作（单书导入、重跑单页）为 A 类，git 提交留痕即可；大规模批量（如全部
12 本重解析）或受保护环境操作需按任务生命周期建立 D 类计划（备份 + 回滚 + 差异复核）。
