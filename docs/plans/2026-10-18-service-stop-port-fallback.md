# 服务停止脚本的端口兜底真停（service-stop-port-fallback）

状态：In Progress
任务类型：B
最后更新：2026-10-20
关联 ADR：[ADR 0006](../adr/0006-ledger-plan-carrier-contract.md)（一任务一承载）
关联设计：[api.md](../architecture/api.md) ① 生命周期与健康（停止与退出码契约正本）、[operations.md](../guides/operations.md)（服务启停操作口径）、[overview.md](../architecture/overview.md)（8902 与 8900 的托管边界）
关联 Tracker：docs/trackers/todo.md（PLAN-023 镜像行；入站请求 REQ-126 同壳承接，见「承载 REQ-126」节）
归档判定：Retain（含停止契约的改判依据，关闭时按当时判据归档）

## 目标与成功标准

目标：让 `scripts/axiom_flow_service.py stop` 在「实例在听但本脚本没有 PID 文件」时也能真停，
并使调用方（QED-Engine 8900 控制中心）能从退出码与输出上区分「真停」与「无从处置」。

验收判据的逐条正文见根仓库 `docs/trackers/todo.md` 的 REQ-126 行「成功标准」列（跨项目请求的
判据正本在发起方行内，本壳只声明目标与指针，不复制条目以免形成第二事实源）。

## 范围与非目标

- 范围＝`scripts/axiom_flow_service.py` 的 `cmd_stop` 分支与退出码语义；`docs/architecture/api.md`
  ① 类的停止/退出码契约与 `docs/guides/operations.md` 的停止口径随步；回归用例落
  `tests/unit/test_service_scripts.py`（脚本层单测）。
- 非目标＝不改 8902 的 API 契约、不改解析管线、不动 `logs/` 目录布局；不引入 PID 文件持久化到
  8900 侧（8900 明确不持有子进程句柄，见根仓库 `docs/design/service-hosting.md`）。

## 前置条件

- 8900 侧已具备停止后端口复核（根仓库 OPS-001 已实施：未纳管时返回 409 并给出端口反查到的
  监听 PID，不再返回裸 `stopping`），因此本壳交付前控制中心只是「报错但不误报成功」，无数据风险。
- 端口反查需只读命令（`netstat -ano` 形态）与既有「优雅停止 → `taskkill /T /F` 兜底 → 存活复核」链。

## 工作项

| 编号 | 内容 | 状态 |
| --- | --- | --- |
| W1 | `cmd_stop` 增「无 PID 文件但 8902 端口在听」分支：按端口反查监听 PID，走既有优雅停止+强杀兜底链，成功后删除陈旧 PID 文件路径逻辑保持一致 | 已完成（2026-10-20，`_parse_netstat_pid`／`_listening_pid`／`_stop_unmanaged` 三件 + 停止链抽为 `_graceful_then_force`） |
| W2 | 退出码与输出与该形可判定：真停 / 未纳管已停 / 停不下来三形各给可区分的退出码与一行输出，供 8900 原因文本引用 | 已完成（2026-10-20，见上表「停止分诊口径」；`UNMANAGED_LISTENER_EXIT = 3` 专用于「端口在听但反查不到 PID」） |
| W3 | 回归用例覆盖 W1/W2 三形 + `docs/guides/operations.md` 停止口径随步 | 已完成（2026-10-20，`tests/unit/test_service_scripts.py` 新增 7 枚；`operations.md`「停止可靠性语义」与 `api.md` ① 生命周期段随步） |
| W4 | `cmd_restart` 取回 `cmd_stop` 返回码（非 0 即中止并原样返回该码，不再拉起第二实例）——与根仓库 DEFECT-020 S2 同形的顺带修复，超出 REQ-126 S3 字面，登记在此以供复核 | 已完成（2026-10-20，`restart aborted` 回显 + 1 枚回归用例） |

### 停止分诊口径（W1/W2 的判定形态）

`cmd_stop` 的「无 PID 文件」分支不再无条件以 0 退出，改为按端口分诊；本脚本是 8902 端口的
生命周期属主，因此对**该端口上的外部实例执行真停**（与根仓库自有 8900/8903 脚本的「只检测不杀」
有意不同：控制中心对非托管进程无处置权，脚本对自己端口的实例有）。

| 形态 | 判据 | 输出（一行） | 退出码 |
| --- | --- | --- | --- |
| 真停（托管实例） | 有 PID 文件且进程存活，链后确认终止 | `stopped` / `stopped (forced)` | 0 |
| 真停（端口兜底） | 无 PID 文件、端口在听、反查到监听 PID，链后确认终止 | `stopped (unmanaged listener pid <n>)` | 0 |
| 真未运行 | 无 PID 文件且端口不通；或 PID 文件陈旧（进程已死） | `not running (no pid file)` / `not running (stale pid file)` | 0 |
| 停不下来 | 优雅 + `taskkill /T /F` 后仍存活 | `stop failed: ...` | 1 |
| 无从处置 | 端口在听但 `netstat` 反查不到监听 PID（命令不可用/输出无法解析） | 打印端口与「PID 未取得」，**不终止任何进程** | 3 |

调用方（8900 控制中心）以 0/3 一并交端口复核：端口已释放→ `stopping`，仍监听→ 409 并引用本行
输出与反查到的 PID（根仓库 `docs/design/service-hosting.md`「停止后端口复核」）。1 沿既有语义
映射为 500。

## 承载 REQ-126

入站请求 REQ-126（QED-Engine 根仓库发起，请求正文见其 [普通任务轮壳 W25](../../../docs/plans/2026-09-29-ordinary-tasks-round.md)
的 R2 项）由本壳承接；执行方＝本仓库 agent 或开发者，评审方＝用户；完成或改期时回根仓库 todo
更新请求行并留回执。

## 验证与验收

- `pytest tests -q` 全绿 + `ruff check src tests` 无错误（本仓库门禁即本地门禁）。
- 人工验收＝在 8902 由脚本以外方式起进程（或移走 PID 文件）后跑 `stop`，观察端口释放与退出码；
  随后经 8900 控制台停止一次，确认 8900 的停止复核不再报 409。

## 回滚

脚本分支只影响停止路径，回滚＝还原 `cmd_stop` 单函数改动；无数据写入，无迁移。

## 过程与证据

| 日期 | 项 | 事实与证据 |
| --- | --- | --- |
| 2026-10-18 | 承接登记 | 根仓库把 OPS-001 残余「外部实例真停」按 ADR 0022 决定 3 另登为跨项目请求 REQ-126（正文见其 `docs/plans/2026-09-29-ordinary-tasks-round.md` W25 行），本壳与其 todo 的 `PLAN-023` 镜像行、`REQ-126` 支线行、`ARCH-021` 聚合行同批由**根仓库会话代登**（用户当令放行），执行与回执原归本仓库会话。 |
| 2026-10-20 | 执行方改判 | 用户当令「我给与你权限直接对子仓库动手修改，因为只涉及启停脚本这些」→ 本轮由**根仓库会话直接实施**（非误产生改动，无需移交），范围严格限定启停脚本与其定向测试、承接行状态；根侧参考实现＝其 DEFECT-020／W28（同族缺陷的根侧尾巴，提交 `5ab5bea`），但**判据形态有意不同**：根侧 8900/8903 脚本「只检测不杀」（控制中心对非托管进程无处置权），本脚本对 8902 端口有属主身份，故按端口**真停**（见上表「停止分诊口径」）。 |
| 2026-10-20 | W1~W4 实施 | `scripts/axiom_flow_service.py`：新增 `UNMANAGED_LISTENER_EXIT = 3`、`_parse_netstat_pid()`、`_listening_pid()`（字节捕获 + `errors="replace"`，形态同其 `_pid_is_alive` 的 V2-012 判例）、`_stop_unmanaged()`，并把既有停止链抽为 `_graceful_then_force(pid) -> (确认终止, 是否强杀)` 供托管／未纳管两路共用；`cmd_restart` 取回停止码，非 0 打印 `restart aborted` 并原样返回。TDD＝先红实录 `tests/unit/test_service_scripts.py` 7 枚（`AttributeError: module 'axiom_flow_service' has no attribute 'UNMANAGED_LISTENER_EXIT'` 等）后绿 40 passed。既有 `test_stop_no_pid_file` 补 `_port_open` 打桩（新分诊下不得探测真实 8902，测试隔离要求）。 |
| 2026-10-20 | 真机验收（W1/W2 三形） | 不触碰在跑的 8902：脚本模块以 `PID_FILE` 指向不存在路径注入，另起 `python -m http.server 18903`（`CREATE_NEW_PROCESS_GROUP`，本轮自起进程）作外部实例。实测＝`netstat` 反查得监听 PID 230000 → `cmd_stop` 回显 `stopped (unmanaged listener pid 230000)`、退出码 **0**、端口释放、该进程 `poll()` 非 None；同参数再跑一次（端口已不通、仍无 PID 文件）→ `not running (no pid file)`、退出码 **0**；8902 全程在听、`logs/qed-axiom.pid` 内容 218544 未被改写。第三形（退出码 3）为 `netstat` 反查失败降级路径，本机不可自然复现，由定向用例守护（零终止调用断言）。停不下来形（1）沿用 2026-09-04 既有链，未改判据。 |
| 2026-10-20 | 门禁 | `pytest tests -q`＝**647 passed / 10 skipped**（基线 640，本轮 +7 枚）；`ruff check src tests scripts`＝All checks passed。中途两枚红由本轮文档改动引起并已修：① `test_architecture_body_carries_no_process_narrative[api.md]`＝新增契约条目带了 REQ 号与日期，改为纯设计态措辞；② `test_todo_plan_rows_exactly_mirror_active_plan_bodies`＝壳状态 `Accepted`→`In Progress` 后镜像行未随，随步其 `PLAN-023` 行状态列并把「三工作项」改「四工作项」。 |
| 2026-10-20 | 提交边界（未覆盖面） | 本轮**只提交** `scripts/axiom_flow_service.py` 与 `tests/unit/test_service_scripts.py` 两枚（整文件差异均属本任务）。`docs/architecture/api.md`、`docs/guides/operations.md`、`docs/trackers/todo.md` 三枚文档随步**留在工作树未提交**＝三文件各含并行会话（区域编辑／解析产物信任线）的在飞 hunk，整文件提交会把其未落地代码的文档认领写进 HEAD；本壳同样留在工作树未提交（其 todo 镜像行在彼文件未提交差异里）。文档随步正文已在工作树就位，待该线入库或用户放行代提交。 |

## 关闭与归档

W1~W3 全部达成且回执登记后关闭（`关闭结果` 按达成取值），归档判定 Retain；被否决或改期时
按 Rejected / Not Applicable 关闭并把残余退回根仓库 REQ-126 行。
