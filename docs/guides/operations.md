# 运维指南

状态：Current
最后更新：2026-10-22

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
- PID 文件 `logs/qed-axiom.pid`，子进程日志 `logs/qed-axiom-serve.log`（`"ab"` 追加、无轮转）。
- `logs/` 守则（[ADR 0012](../adr/0012-workspace-directory-hygiene.md) 决定 4，人工执行、无代码轮转）：
  只写本项目日志与运行时文件、名字一律以 `qed-axiom` 开头（含 `qed-axiom.pid`）、**区内文件数固定不超过 3 个**，
  超出即删最旧者；外部组件（8900/8903/MinerU 容器）的日志不得写进本目录。
- 端口来源：`QED_AXIOM_URL` 解析 > `AXIOM_PORT` > 8902。
- 配置语境：脚本不再传 `--mode`（2026-09-17 配置简化，PLAN-002）——解析始终直连模型服务
  （[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)），配置由 `config.py`
  从仓库根向上走查 `.env` 自读；变量清单见[开发指南](development.md)。

### 停止可靠性语义（2026-09-04，与根仓库 8900/8903 脚本同构）

- 判活用内核句柄探测（`_proc_alive`，探测失败不再误判为「已死」）；优雅信号（CTRL_BREAK）
  未生效由 taskkill 强杀兜底成功时回显 `stopped (forced)`——正常现象，conda run 等跨
  console 语境常见，无需处理。
- **无 PID 文件时按端口分诊（REQ-126，2026-10-20）**：端口不通＝真未运行，幂等退出 0；
  端口仍在监听＝该 8902 实例非本脚本启动（PID 文件丢失或被移走），脚本按 `netstat -ano` 反查
  监听 PID 并走同一条「优雅停止 → 强杀兜底 → 存活复核」链**真停**它，成功回显
  `stopped (unmanaged listener pid <n>)`。本脚本是 8902 端口的生命周期属主，所以在这里执行真停；
  8900 控制中心对同一形态只检测不杀（根仓库 `docs/design/service-hosting.md`「停止后端口复核」）。
  反查不到监听 PID（netstat 不可用）时不终止任何进程，退出码 **3** 回报「无从处置」。
- 优雅+强杀后仍存活则退出码 1 显式报 `stop failed`（绝不假 stopped）：按提示手动
  `taskkill /PID <pid> /T /F`，确认进程退出后重试 `stop` 清理 PID 文件。
- `restart` 取回 `stop` 的返回码：非 0 即中止并回显 `restart aborted`（停止失败不再拉起第二实例）。
- 退出码：`0` 成功或幂等（含按端口真停外部实例）、`1` 运行失败（含 stop 无法终止）、`2` 参数错误、
  `3` 端口由非本脚本启动的进程监听且反查不到监听 PID。

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

`<QED_DATA_ROOT>` 为**必填配置**（缺配、指向仓内 `data/` 或目录不存在即启动期报错，无仓内默认值，[ADR 0009](../adr/0009-data-root-must-be-explicit.md)）。其顶层是白名单登记制（正文事实源在[系统概览](../architecture/overview.md)「数据边界与产物布局」节与根仓库 ADR 0018）：只允许 `raw/`、`tmp/`、`parsed/`、`backups/`，白名单外即野生区，发现即登记处置。**本服务只写 `parsed/`**。

| 路径 | 内容 |
| --- | --- |
| `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/` | **根视图**：`book.json`、`pages/p<NNNN>.png`（150 DPI 页图正本，ingest 产物）、`manifest.json`；重新 ingest 只原子替换这三者 |
| `…/<book_id>/versions/<job_id>/` | **一次解析任务 = 一个版本**（PLAN-004 / REQ-080）：`book.json`、`pages/p<NNNN>.{png,md,blocks.json}`、`chunks.json`、`manifest.json`；生效版本由 `af_books.active_job_id` 指认，页图为根视图页图的**硬链接**（同一页图在 N 个版本不重复占盘） |
| `<QED_DATA_ROOT>/raw/<domain_id>/<course_id>/*.pdf` | 源书库（只读，QED-Tracker 写入；如 `math-advanced/math_analysis/`） |
| `<QED_DATA_ROOT>/backups/` | D 类数据操作的备份留痕例外区（根仓库 ADR 0018）：命名 `YYYY-MM-DD-<任务标识>`，对应操作必须在本仓 todo/收口记录登记；本仓一次性窗口的备份件落**仓库根** `tmp/backups/`（不在数据根内、`.gitignore` 排除、可随手清理，[ADR 0012](../adr/0012-workspace-directory-hygiene.md)）；需长期保值的回滚锚点必须写进本行数据根路径 |
| `<QED_DATA_ROOT>/tmp/` | **本服务不写**：引擎中间产物只在内存（[PP-6](../design/parsing-pipeline.md)），`tmp/axiom-flow/<job_id>/` 一旦存在即缺陷 |
| `<仓库工作区根>/tmp/` | **仓库内唯一临时与留档区**（[ADR 0012](../adr/0012-workspace-directory-hygiene.md)）：一次性窗口驱动与探针脚本、真机取证留档（`tmp/probes/<窗口>/`）、备份件（`tmp/backups/`）；`.gitignore` 排除 ⇒ **不入库、不作为事实源、可随手清理**，需长期保存的结论一律写进 `docs/`。其内 `tmp/probes/<窗口>/root/` 是窗口脚本显式声明的**探针数据根**，与真实数据根同受「只写 `parsed/`」约束。**与上一行的数据根 `tmp/` 是两个不同位置**，本仓凡指仓内者一律写「仓库根 `tmp/`」 |

**留档区与瞬态核对步**（发现即登记处置，勿随手删）：

1. 仓内**不得存在 `data/`**（留档区已按 [ADR 0012](../adr/0012-workspace-directory-hygiene.md) 决定 2 退役，
   2026-09-29 清空且不备份）；仓库根 `tmp/` 内的留档件不受产物形状约束（非数据根、内容可弃），
   但探针数据根 `tmp/probes/<窗口>/root/` 内的布局仍须与真实数据根同构——由
   `tests/contract/test_storage_conventions_governance.py` 在默认门禁断言。
2. 数据根 `tmp/axiom-flow/<job_id>/` 一旦存在即缺陷（PP-6 字面判据；治理条文里该目录只允许以
   「禁止它」的语境出现，同守护文件按行断言）。
3. 瞬态残留（名字含 `.tmp-`/`.old-`）：服务**启动期**与**管理面清除**都会计数告警（只告警不清理，
   PP-4 / PP-6）；手动核对按同一识别式，重清归版本就绪判据（PLAN-009 P2）。

产物只落 Windows 本地；WSL 不读写 Windows 文件系统（MinerU 容器不挂载 Windows 盘）。

老版本布局（扁平产物）的兼容：升级与迁移**不搬移**旧产物、不改 `af_pages` 行内容，端点读到
`versions/<job_id>/` 缺失时按 `af_books.active_job_id IS NULL` 走**根视图**（旧布局的
`pages/p<NNNN>.{md,blocks.json}` 与 `chunks.json` 直接可读），因此重解析前的窗口内 8900/8903
功能不中断。

## af_* 整族重建单窗口 runbook（PLAN-024 W8，**D 类：执行前须用户批准**）

档 B＝**DROP 现役 `af_*` 全部表 + 迁移链重置**后由唯一 base 迁移 `20261022_0001_af_family_rebuild` 重建
四表形状，且**必须与新代码同窗口发布**——ORM 已声明四表列集，库未按新形状重建时读列即故障。旧链六枚
迁移（`20260917_0001`…`20260928_0001`）随重置**已从仓库删除**，任何写死旧 revision 号的操作指令都不再可执行；
历史窗口的执行读数在 [trackers/completed.md](../trackers/completed.md)。顺序固定为 B（本地全绿）→ C（单窗口）
→ D（逐本重新 ingest + 解析），**不在旧布局上重复真机解析**（避免解析完即被回填成 `legacy` 而失效）：

```powershell
# C-0 窗口排他（本机 GPU 只容得下一个解析；与 8900 侧约定静默后再开工）
#   **占用判据以模型侧计数为准**：GET http://127.0.0.1:5002/health → queued_tasks + processing_tasks == 0
#   本机侧只判 `af_operations` 的 parse 事件行有无 `queued` 行；服务重启后遗留的 `running` 行是**无人推进的孤儿**
#   （DEFECT-007），不阻塞窗口、只登记取证（2026-09-21 R1 实证：孤儿空闲 106683 秒）。
#   **PLAN-006 起此类孤儿行由冷启动的启动期归一自动改判 `interrupted`**（见下节），窗口脚本
#   仍把 `queued` 当作占用信号——只有真正在排队的任务才算占用
#   窗口期间不得经 8900 重启/换起 vision 槽位（2026-09-21 R1 实证：重启令排队与在飞任务同时判死）
# C-1 停服（避免运行中写库）
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py stop
# C-2 备份 af_* 表族（含 alembic_version），导出到带日期的 .sql 后核对行数、按 sha256 留档
#   表清单以 AF_TABLES 为准（现四表：af_books/af_operations/af_pages/af_kp）
#   mysqldump 不在 PATH，用 Program Files 全路径；**必须显式 -h/-P/-u**，缺省会拿当前系统用户名连
#   （2026-10-03 实测报 `1045 Access denied for user 'ODBC'@'localhost'`）；口令只经 MYSQL_PWD 注入，
#   不写 -p（会进命令行与历史）。`af_kp` 本轮无任何写者、备份时通常是空表，仍逐名列出以保证还原面完整。
& $env:ProgramFiles\MySQL\MySQL Server 8.0\bin\mysqldump.exe -h 127.0.0.1 -P 3306 -u root --single-transaction --no-tablespaces qed af_books af_operations af_pages af_kp alembic_version > af_backup_YYYYMMDD.sql
# C-3 整族重建（仓库根目录，读 .env 的 QED_DB_*）：先 DROP 现役 af_* 全部表（含退役的 af_parse_jobs/
#   af_block_edits/af_page_regions）与 alembic_version，再由唯一 base 建新形状。**只 DROP af_* 与
#   alembic_version，禁止 DROP DATABASE**（`qed` 是共享库）；DROP 前逐表 SHOW CREATE TABLE 留档
#   执行驱动＝`tmp/w8_rebuild.py <schema> ...`：逐库 DROP 自有表 → 跑唯一 base → 复核「恰四张 +
#   版本＝base + 四表行数全 0」并打印 `VERDICT=PASS|FAIL`，证据落 `tmp/window-20261022/rebuild_<schema>.txt`。
#   裸 `-m alembic upgrade head` 只在空库成立：旧链已删，而现役库里还留着退役表，它不会替你 DROP。
& D:\software\anaconda3\envs\QED_env\python.exe tmp/w8_rebuild.py qed qed_test
# C-4 校验：SHOW TABLES LIKE 'af\_%' 恰四张；alembic_version = 20261022_0001
#   SELECT COUNT(*) FROM af_operations;（预期 0）；SELECT COUNT(*) FROM af_books;（预期 0）
# C-5 空窗可见后果留档（本轮必测，档 B 四件必答③）：`GET /books` 返回**空列表**而非报错（读取面纯 DB
#     驱动）、书目详情/页清单/分块 404、8900 透传层如实上抛、8903 显示「暂无书目」空态；并向运维留一行
#     「af_* 已清空，逐本重新 ingest + 解析前解析管理面无数据」
# C-6 起服并只读冒烟
& D:\software\anaconda3\envs\QED_env\python.exe scripts/axiom_flow_service.py start --wait
# C-7 书目回填（PM-2 幂等，起服后即可）：POST /api/v1/books/sync 批量 upsert af_books
# C-8 窗口前置取证（不写不解析：就绪轮询 + 部署自证 + 越界 422 门槛复验）
& D:\software\anaconda3/envs\QED_env\python.exe tmp/live_reparse_b01_pages.py --check-only
```

**2026-10-22 窗口的实测读数（PLAN-024 W8 执行完，档 B 四件必答③就此了结）**

- **C-3／C-4**：`qed` 与 `qed_test` 两库各 `VERDICT=PASS`——`af_*` 恰四张（`af_books`／`af_operations`／
  `af_pages`／`af_kp`）、`alembic_version`＝`20261022_0001`、四表行数全 0。真库列数逐张等于设计篇的列清单
  （22／27／12／23），`af_operations` 的索引实读＝`PRIMARY` + `uq_af_operations_intent` +
  `ix_af_operations_book` + `ix_af_operations_version` + `ix_af_operations_sig`。DROP 前逐表
  `SHOW CREATE TABLE` 与 mysqldump 双份留档（`qed` 备份 sha256 `29bb119e…`、`qed_test` `519d4da0…`）。
  **附带面零受损**＝非 `af_*` 表集合与 DROP 前逐名相等且行数不变（`qed` 9 张 `qed_*`/`qt_*`、
  `qed_test` 20 张含 `qe_*` 学习面镜像），判据脚本 `tmp/w8_collateral_check.py`。
- **C-5 空窗读数**：本机 8902 按 `.env` 的 `QED_DB_NAME` 指向 `qed_test`，故读数即在测试库上取（读取面纯
  DB 驱动，业务库同形）。`GET /books`＝**200 `[]`**、`GET /books/<原在册 id>`＝404「书目不存在」、
  `/manifest`／`/pages`／`/pages/1`／`/chunks`＝404「书目不存在或产物缺失」、`/versions`＝404、
  `GET /parse-jobs`＝200 `{"jobs":[],"total":0}`、未知 `job_id`＝404
  ⇒ 空窗对 8900/8903 是**空态而不是故障**：透传层如实上抛，界面显示「暂无书目」。
- **C-7 书目回填**：`POST /api/v1/books/sync` 17 本 → `synced=17, updated=0`；同批重放 →
  `synced=0, updated=17`（PM-2 幂等成立）。回读的 17 本 `parse_status=pending`、`active_job_id=NULL`、
  `page_count=NULL`、`ingest_status=none` ⇒ 回填只恢复**书目面**，页行与版本按四件必答②必须重新
  ingest + 解析才回来（本次窗口按用户裁决**不重解析**）。
- **业务库 `qed` 的书目回填不在本窗口内**：本机 `.env` 的 `QED_DB_NAME` 指 `qed_test`，故 C-6/C-7 的起服与
  回灌只作用在测试库；`qed` 现为**新形状四表全空**（`af_books` 0 行、`alembic_version`＝`20261022_0001`）。
  业务面恢复的下一步＝把 `QED_DB_NAME` 指回 `qed` 后起服，`POST /api/v1/books/sync` 回灌（17 本的 payload 已留档
  `tmp/window-20261022/sync_payload_qed.json`），或由根侧首次同步触发；页行与产物仍须逐本重新 ingest＋解析。
- **两条顺序硬约束（本窗口实证到的坑）**：
  ① `QED_TEST_MYSQL=1` 的夹具在每条用例后**清空 `qed_test` 的 `af_*` 与 `alembic_version` 且不重建**
  （`tests/support/mysql.py` 的 `isolated_mysql_store`），跑完 MySQL 层后该库是零 `af_*` 状态；
  **必须先 `python tmp/w8_rebuild.py qed_test` 重上 base，再起 8902**，否则启动期归一的 SELECT 撞
  `1146 Table 'qed_test.af_operations' doesn't exist` 而 `Application startup failed`。
  C-4/C-5 的校验与空窗读数因此排在测试层**之前**取。
  ② 8902 与集成夹具指向同一个 `qed_test`：服务在跑时夹具会抽掉它正在用的表，池内旧语句与读数都不可信
  ⇒ 跑 MySQL 层前先 `scripts/axiom_flow_service.py stop`，跑完重建再起。
- **当前库面口径（2026-10-22 根侧确认后的运维判读）**：业务库 `qed` **保持为空**直到用户明确说「同步 OK」，
  验证与演示一律在 `qed_test`（其 17 本 `af_books` 是在用演示数据）⇒ 本批**禁跑 `QED_TEST_MYSQL=1`**：
  夹具 teardown 会清空 `qed_test` 的 `af_*` 且不重建（上一条①），跑一次就抹掉演示书目并触发启动失败。
  默认门禁不连库，照常跑。**读数判据**：若服务读回 `af_books` 为空且本机并未跑过 MySQL 层，先查 `.env`
  的 `QED_DB_NAME` 是否指向了 `qed`——业务库的空态正是「错连到正式库」的信号，不是数据丢失。
- **列注释滞后不是形状漂移**：R1（`gen_status` 闭集删 `adopted`）与 R2（`op_kind` 扩 `kp`）只改**应用层常量**，
  MySQL 侧零 ALTER、零新迁移，因此两库 `SHOW CREATE TABLE` 里这两列的**注释文本**仍写着重建当日的旧枚举。
  没有任何守护比较注释，闭集的正本以 `src/axiom_flow/db/models.py` 的常量与
  [database-design.md](../architecture/database-design.md) 的列行为准。自愈路径＝`qed_test` 在
  `python tmp/w8_rebuild.py qed_test` 重上 base 时随 DDL 更新，`qed` 在下一次整族重建／回灌窗口随 base 更新。
- **窗口工具一律直连本机端口（`trust_env=False`）**：Windows 注册表代理开启后**不豁免** `127.0.0.1`，
  `httpx` 默认跟随环境代理会让 5002/8902 的探活全部变 **502**（同刻 `curl` 直连仍 200），窗口可因此
  空转到截止。适配器侧的同一条不变式见 [model-integration](../design/model-integration.md) 的
  「直连，不经系统代理」条；`tmp/` 下的
  窗口脚本与人工 `curl` 之外的一切自动化都必须显式关闭 `trust_env`。

- **模型就绪不以 8900 的一次提示为准**：5002 的 vLLM 权重加载是分钟级，而根侧前端 `operateModel`
  轮询截止只有 15s（`web-ui/src/stores/runtime.ts`），超时即显示「失败」但容器仍在起。判据用
  `GET http://127.0.0.1:5002/health` **轮询到 200**（窗口脚本已内置，默认 300s 截止）。

- **回滚**：新链只有 base 一枚，`alembic downgrade base` 等于 **DROP 四表**（数据全失，不做部分回滚），
  所以窗口内的回滚口径是：按 C-2 的备份 SQL 还原 `af_*` + 从 git 取回旧链六枚迁移文件后再
  `alembic upgrade head`。产物文件不随迁移删除，DB 与文件解耦——回滚库不需要重渲染页图，但页行必须
  重新 ingest + 解析才能回来（档 B 四件必答②：不提供从盘上产物反向灌页行的脚本）。
- **D 步（单次重解析）**：`python tmp/live_reparse_b01_pages.py 1 20` —— 脚本**只经 8902 API**
  驱动（提交期门槛、生效指针、只读端点一并取证），先按需 re-ingest 补齐根视图页图（**根仓库
  2026-09-21 已清空 `dataset/parsed/` 的 4 本旧代际产物，故页图必须重渲染；`af_pages` 里的旧
  `parsed` 行描述的文件已不存在**），再提交一次解析任务；产物落 `versions/<job_id>/`，成功后
  `active_job_id` 指向该版本；重解析同时作为 PLAN-003 W8 的续跑证据。
- **补证据不得再解析一次**：解析成功但取证段失败时，用 `--evidence-only <job_id>` 只读重取
  （跳过 ingest/门槛探针/C-0/提交）。单次重解析是用户裁决的窗口约束，重新解析会制造新版本目录并
  让已激活指针被移动。首次窗口（2026-09-21）即以此方式补齐 `S2_after.json`。
- **探针窗口（`qed_test` + 隔离数据根，PLAN-007 W6 起可用）**：`python tmp/live_wholebook_req086.py`
  用**真实环境变量**把库换成 `qed_test`、数据根换成 `tmp/probes/<窗口目录>/root`（不改 `.env`，退出即回
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
  进程存活：`tmp/live_wholebook_req086.py` 自带该前置（`--wsl-holder 300` 默认开，滚动重拉
  `wsl bash -c "sleep 300"`，退出时收掉；`--wsl-holder 0` 显式关闭并只留 WARN），手工驱动容器时可用
  `nohup bash -c 'for i in $(seq 1 40); do wsl bash -c "sleep 600"; done' &` 顶同样的位置。
  该处置属机器侧配合，**不改任何业务参数**（窗口/deadline 原样）；脚本里的 `--wsl-holder` 只是本仓库
  探针窗口的自便实现，**不代表本仓库接管 WSL 环境**——根侧长驻服务链路若见同一形态，按上一条自查。
  两条根因用 `dmesg` 有无 `Killed process` 区分：有＝上一条（容量），无而 `RestartCount=0`＝本条（空闲）。
- 迁移期间 8902 离线：8900 返回 503 并降级显示（独立性铁律），不影响配置域与其他界面。

## 单实例前提与启动期归一（PLAN-006 / ADR 0007）

**部署边界（显式承担，不是疏漏）**：8902 按**单实例**运行。任务是后台线程内的单 worker 队列，
`af_operations` 的 parse 事件行 `status` 的 `queued`/`running` 只描述「本进程在推进它」；一旦起第二个实例，
它的启动期归一会把第一个实例**正在跑**的任务一并判成 `interrupted`。要多实例必须先重开这条
裁决（引入心跳/租约，`queued`/`running` 需可证活）。

- **归一时刻**：`api/main.py` 的 lifespan 在**受理首个请求之前**调用 `runner.reconcile_on_startup()`，
  把上次进程遗留的 `queued`/`running` 任务行改写为 `interrupted`（`error` 写「服务重启中断，可重跑」，
  终态时刻写在 `progress.finished_at`），书目侧进度回 `pending`。**结局只在该书最新一次任务时置 `interrupted`**——更早的
  中断行不得覆盖后来的真实结局（`last_parse_result` 语义）。
- **可观测**：归一发生时会打 `WARNING 启动期归一：N 个遗留活动任务改写为 interrupted`；N=0 时静默。
  只想确认现状不必起服：`SELECT op_id, status FROM af_operations WHERE op_kind = 'parse' AND status IN ('queued','running')`。
- **`interrupted` 的续跑语义 = 同一 `job_id` 重放**，不新建版本：版本作用域是 `(book_id, job_id)`，
  已 `parsed` 且产物在位的页跳过引擎调用，只补剩余页；重放前**不需要**手工清行、也不需要删版本目录。
  **但重放入口目前只有代码路径**（`orchestrator.run_parse_job(job_id=…)`，`tmp/` 窗口脚本按此
  用法）：`POST /parse-jobs` 每次生成新 `job_id`，按既有 `job_id` 续跑的端点**至今没有承载行**——原挂
  本方 `REQ-087`，2026-09-22 该号让给根侧「空白页合法化 + 跨版本续跑语义」后（本方端点行改号
  `REQ-088`），续跑端点本身不再归属任何在办计划，PLAN-006/007 都未开放它。因此真机上被打断的版本
  要么经脚本重放、要么重新提交为一版（旧版保留不删）。
  **定位待重放任务不必再猜 id**：`GET /api/v1/parse-jobs?status=interrupted&status=partial` 直接给出
  清单（含 `failed_pages`），`GET /api/v1/books/{id}/pages` 给出该作用域逐页形态（PLAN-008 / REQ-088）。
- **`20260921_0001` 迁移窗口**（D 类，执行前须用户批准；**该 revision 已随 PLAN-024 档 B 迁移链重置不存在**，
  本条只留作历史执行记录，不可再照做）：停服 → 备份 `af_*` 四表 →
  `python -m alembic upgrade 20260921_0001` → 校验 `alembic_version` 与
  `SELECT book_id, parse_status, last_parse_result FROM af_books`（预期进度全 `pending`、
  存量终态值平移进 `last_parse_result`）→ 起服。**降级有损**：`partial` 在旧结构无对应值，
  回滚时按旧语义归 `completed`。**已于 2026-09-22 R2 窗口在真机 `qed` 执行完毕**（备份
  `tmp/backups/window-20260922/af_backup_20260922.sql`，起服后启动期归一把孤儿行
  `5f43593a12cc` 改判 `interrupted`，见 PLAN-003 R2 W-A）。

## 书目删除的落库与磁盘口径（2026-09-22 D3 真机演练测定）

`DELETE /books/{book_id}` 是 D 类数据操作（不可逆），演练判据（合成 5 页探针书，真实书目零改动）：

- **删除**：`af_pages` 页行、`af_books` 书行、`af_operations` 的 **`intent` 行**（`edit` + `structure`），以及整棵书目录
  （`versions/` 全部版本 + 根视图页图）；`GET /books/{id}` 随后 404。
- **保留（审计）**：`af_operations` 的 parse **事件行**不删，`GET /parse-jobs/{id}` 删除后仍 200 ——
  任务历史是跨书目的运营留痕，不随书目生命周期消失。
- **不裁剪空父级目录**：如 `parsed/probe/d3-20260922/` 删除后其父 `parsed/probe/` 留在盘上。
  契约只承诺书目录，空目录不算残留缺陷；若要裁剪须先改 `design/parsing-management.md` 的删除契约。
- **回滚**：无「撤销删除」路径，只能按备份还原 `af_*` + 重新 ingest/解析重建产物；执行前必须先
  导出 `af_*` 表族（`af_books`、`af_operations`、`af_pages`、`af_kp`，与 `AF_TABLES` 同源）并核对行数。

## 管理面一键清除 runbook（`POST /api/v1/admin/purge`）

契约与不变式见 [api.md「非公开管理面」节](../architecture/api.md)（PLAN-010 / ARCH-024）。这里只写
操作口径。**它与 `DELETE /books/{id}` 的分工**：后者是面向消费方的常规删除，保留 parse 事件行作审计；
审计行；前者是「先清后建」的运维重置，**连任务行一起清**、可按书目或全项目，且默认不挂载。

1. **启用**：`.env` 加 `QED_ADMIN_ENABLED=1` 与 `QED_ADMIN_TOKEN=<自行生成的长随机串>`，重启服务。
   只配 `ENABLED=1` 不配 token ⇒ 启动期 `AdminConfigError` 直接拒绝起服（fail-closed，不留无凭据的
   删除接口）。用完把两行删掉再重启，让路由回到 404。token 绝不进 git、日志与任何回执文本。
2. **演练**：`dry_run` 缺省即 true，响应给出 `targets`（相对 `parsed/` 的书目录）与将删计数。
   把这份清单留存为本次操作的证据；数字与预期不符就停手，别按真删往下走。**核对 `targets` 的层数形状**：
   每条都应是**书目录本身**，而不是它下面的 `pages/` 或 `versions/…`——出现后者说明目录识别被误判
   （`domain_id`/`course_id` 为空时书目录只有两层，判例 ARCH-026 / PLAN-011），此时真删会留下
   `book.json`/`manifest.json` 与空目录，必须停手。
3. **备份**：导出 `af_*` 表族全部表（与 `AF_TABLES` 同源）并核对行数——本端点
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
