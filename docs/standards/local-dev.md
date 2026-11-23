# 本地开发环境

状态：Current
确认状态：已确认
最后更新：2026-11-22
治理对象：本地机器标识、环境依赖、配置走查链与开发约定（仅机器绑定事实）
依据：QED-Tracker `docs/standards/local-dev.md` 治理模式（[ADR 0003](../adr/0003-standards-governance-alignment.md)），
结构对齐 QED-Engine 根仓库 `docs/standards/local-dev.md`（环境事实唯一维护位置）
关联测试：无

## 目的与边界

本文档标注 Axiom-Flow 本地开发环境的机器绑定事实，供开发者快速确认环境一致性、减少重复
探索与误判。本文档仅在 UUID 为 `2C6ECD2C-BBEE-11ED-8A95-F0D4154ABBA8` 的机器上生效；
其他环境需复制并修改。

**本地 vs 可移植边界**：本文只登记机器绑定事实（机器标识、绝对路径、conda 环境名、端口、
本机配置实际取值）；可移植配置以仓库内文件为唯一事实源——Python 版本与依赖看 `pyproject.toml`，
变量语义看 [model-integration](../design/model-integration.md)，运行与数据操作看
[开发指南](../guides/development.md)与[运维指南](../guides/operations.md)。可移植事实与本文
冲突时，以仓库内文件为准并回修本文。

## 机器标识

| 项目 | 值 |
| --- | --- |
| UUID | `2C6ECD2C-BBEE-11ED-8A95-F0D4154ABBA8` |
| 主机名 | `WENFU` |
| 仓库路径 | `D:\coding\QED-Engine\Axiom-Flow` |
| 数据根 | `D:\coding\QED-Engine\dataset`（`raw/`、`parsed/`、`tmp/`、`backups/` 四项齐备） |
| Shell | PowerShell 5.1（Windows） |

> UUID 是**登记值**：本机 WMI（`Win32_ComputerSystemProduct`）在受限完整性级别下返回拒绝访问，
> 复核 UUID 需在完全权限会话执行。主机名可用 `hostname` 独立复核。

## Git 配置

| 项目 | 值 |
| --- | --- |
| 版本 | `git version 2.41.0.windows.1` |
| 用户名 | `swfswf1234` |
| 邮箱 | `812146364@qq.com` |
| 当前开发分支 | `develop`（[ADR 0014](../adr/0014-develop-branch-rename.md)） |

## Python 环境

| 项目 | 值 |
| --- | --- |
| Conda 环境 | `QED_env` |
| 环境路径 | `D:\software\anaconda3\envs\QED_env` |
| Python 版本 | 3.12.0（以 `pyproject.toml` `requires-python` 为准） |
| pytest | 9.0.3 |
| ruff | 0.16.1 |

命令统一经环境内解释器执行（与[开发指南](../guides/development.md)一致）：

```powershell
& D:\software\anaconda3\envs\QED_env\python.exe -m <命令>
```

Windows 文件系统大小写不敏感，历史文档曾写作 `qed_env`，与本仓库 `QED_env` 指向同一环境。

## 服务端口速查

| 服务 | 端口 | 本机实况 |
| --- | --- | --- |
| Axiom-Flow API | 8902 | 监听中；生命周期属主 `scripts/axiom_flow_service.py` |
| MinerU 解析服务 | 5002 | **未监听**（容器按需启停，属常态）；WSL 容器 `mineru-api`，健康端点 `/health`，生命周期由 8900 托管 |
| QED-Engine 后端 | 8900 | 监听中（模型生命周期与文本网关托管方） |
| QED-Tracker | 8901 | 监听中；同级子仓库，本仓库只读 |
| QED-Engine 前端 | 8903 | 监听中；根仓库 |
| MySQL | 3306 | 监听中；`qed` 为业务库、`qed_test` 为测试库 |

上文「历史文档」指本文此前版本：其端口表曾写「旧端口兼容保留」。该说法**已失效**——`src/`、
`scripts/` 与 `tests/` 内对旧端口的引用为 0，8902 是本仓库唯一监听端口；旧字号按当时记录保留在
`docs/history/`，本文不再复述。

## 推理基础设施

- WSL Ubuntu 24.04 + Docker Compose：`mineru-api`（MinerU FastAPI 单体，内嵌 vLLM，
  hybrid-engine 后端，需 GPU 穿透）；容器与启停脚本由根仓库 `scripts/image-model/` 托管，
  启停/探针/资源互斥/调参由 8900 统一管理，本仓库只连接 `QED_OCR_MODEL_URL`
  （默认 `http://127.0.0.1:5002`，健康端点 `/health`）。
- 5002 未监听是本机常态（容器按需启动），不是配置故障；此时解析类调用按设计返回 503。
- 详细操作与排障见[运维指南](../guides/operations.md)与根仓库 `scripts/image-model/`。

## 本地配置链与有效值

配置由 `config.py` 向上走查 `.env` 自读，优先级为**真实环境变量 > 本仓库 `.env` > 上一级
`D:\coding\QED-Engine\.env` > 内置默认值**。本机两处 `.env` 并存，故「文档默认值」与
「本机有效值」可能不同——下表是 2026-11-22 由 `load_settings()` 实测的本机有效值：

| 键 | 本机有效值 | 来源 | 说明 |
| --- | --- | --- | --- |
| `AXIOM_PORT` | `8902` | 本仓库 `.env` | 服务监听端口 |
| `QED_DATA_ROOT` | `D:\coding\QED-Engine\dataset` | 本仓库 `.env` | **必填**，缺配/指向仓内退役 `data/`/目录不存在即启动期报错（[ADR 0009](../adr/0009-data-root-must-be-explicit.md)） |
| `QED_DB_NAME` | `qed_test` | 本仓库 `.env` | 本机指向**测试库**；业务库 `qed` 不由本机默认连接 |
| `QED_DB_HOST` / `QED_DB_PORT` | `127.0.0.1` / `3306` | 本仓库 `.env` | — |
| `AXIOM_OCR_ENGINE` | `mineru` | 本仓库 `.env` | 闭集内显式分派，不自动兜底 |
| `QED_OCR_MODEL_URL` | `http://127.0.0.1:5002` | 本仓库 `.env` | 图像槽位端点 |
| `AXIOM_OCR_TIMEOUT` | `600` | 本仓库 `.env` | **单块**一次调用超时，每次独立计 |
| `AXIOM_PARSE_WINDOW_SIZE` | **`50`** | **上一级根 `.env`** | 与代码默认 `56` 不同；全本按此页数串行分块 |
| `AXIOM_KP_ENGINE` | `qed-gateway` | 内置默认 | 梳理文本槽位，未在任何 `.env` 中配置 |
| `QED_ENGINE_GATEWAY_URL` | `http://127.0.0.1:8900` | 内置默认 | 梳理出站唯一通路 |
| `AXIOM_KP_TIMEOUT` | `600` | 内置默认 | 与 8900 自身调用预算同值 |
| `AXIOM_MAX_REGIONS_PER_PAGE` | `16` | 内置默认 | 每页人工结构意图上限 |
| `QED_ADMIN_ENABLED` | 未设置（关） | — | 关即管理面路由不挂载，`QED_ADMIN_TOKEN` 无需配置 |

**两处易错**：① `AXIOM_PARSE_WINDOW_SIZE` 的实际值由根 `.env` 决定（本机 `50`），只看代码默认
`56` 会误判窗口数与整本挂钟上限；② 本仓库 `.env` 的 `QED_DB_NAME=qed_test` 意味着起服后
`GET /books` 读到的是测试库书目，读回空列表是「连错库」的信号而非数据丢失。

## 常见误判注记

- **API 503 / MinerU 未起**：经 8900 `/models/vision` 或根仓库 `scripts/image-model/` 启动
  mineru-api 容器（5002）后重试；首次请求含 vLLM warmup（约 1 分钟）。
- **配置语境（2026-09-17 简化）**：Axiom-Flow 只做「模型 API 调用 + 预处理/后处理」，配置
  三段式（服务 / 数据与库 / 解析引擎）；服务脚本无 `--mode`，配置由 `config.py` 向上走查
  `.env` 自读。
- **解析超时**：全本按 `AXIOM_PARSE_WINDOW_SIZE`（本机 `50` 页/块）**串行分块**提交，超时读
  `AXIOM_OCR_TIMEOUT`（默认 600s）且**每次调用独立计**，故整本挂钟上限 = 块数 × 600s、单块失败只损失
  该块页；模型调参仍在 QED-Engine 侧（模型服务配置），本仓库不暴露调参入口。
- **`.env` 值内联注释**：值中 ` #`（空白+井号）会被视为注释并剥离；带 `#` 的真实值请确保
  `#` 前不留空格。
- **工作区目录卫生（[ADR 0012](../adr/0012-workspace-directory-hygiene.md)）**：临时脚本、探针与
  窗口留档一律落仓库根 `tmp/`（不入库、不作事实源），`scripts/` 只放长期受管脚本，`logs/`
  只写本项目日志且文件数 ≤3；根 `tmp/` 与 `<QED_DATA_ROOT>/tmp/` 是两个位置，后者服务永不写入（PP-6）。
  **该口径只约束「落在哪里」，不保证 shell 写得进去**——见下节完整性级别边界。
- **停止脚本回显**：`stop` 回显 `stopped (forced)` 表示 taskkill 强杀兜底成功（conda run 等
 跨 console 语境常见），属正常现象；`stop failed` 需按提示手动 `taskkill /PID <pid> /T /F`。

## Agent 工具层（DSH 端）

本机同时使用 Qoder 与 DeepSeek Harness（DSH）。两端的技能正本不同，工具层约束也不同，
2026-11-22 实测记录如下。

### shell 写入权限的真实边界

DSH 的 shell 进程运行在 **Low 完整性级别**（`S-1-16-4096`），可写范围由「对象自身的完整性标签」
决定，不由 ACL 授权决定：

| 位置 | 宿主 `write`/`edit` | shell（cmd/pwsh） |
| --- | --- | --- |
| 工作区根下**新建**文件（继承 Low 标签） | 可写 | 可写 |
| `tmp/`、`logs/`、`.qoder/`、`docs/`、`src/`、`tests/`、`scripts/`（既有目录为 Medium） | 可写 | **拒绝**（`Access is denied`） |
| `%TEMP%` | 可写 | 可写 |

**判据：shell 报 Access denied 不是权限配置错误，是这条链路的固有边界**——改用宿主文件工具，
不要原样重试或提权探测。需要 shell 落盘时用 `%TEMP%`，不要用 `tmp/`。

宿主 `read`/`write`/`edit`/`glob`/`grep` 不经该令牌，对同样路径正常。**改仓库文件永远走宿主工具**：
既避开完整性边界，也拿到 `edit` 的字面量定位与可审 diff。

> 2026-11-22 本机对仓库根补过一条「当前用户完全控制权」（解决宿主侧工作区授权失败），
> 备份与逐条回滚命令在 `tmp/acl-report/`。**该修复只影响宿主侧授权，不改变上表的 shell 边界**：
> 两者是不同机制（ACL 授权 vs 完整性级别强制）。

### shell 与解释器事实

| 项目 | 事实 |
| --- | --- |
| `pwsh` 实际版本 | PowerShell **5.1.26100**（非 7）：无 `-SkipHttpErrorCheck`；`Start-Process -ArgumentList` 报 Access denied |
| `cmd` | 内建命令无上述故障，中文直出正常；短命令优先 `cmd /c "chcp 65001 >nul && …"` |
| 裸 `python` | **3.10.9**，`import tomllib` 直接 `ModuleNotFoundError`（会伪装成项目故障） |
| 固定解释器 | `D:\software\anaconda3\envs\QED_env\python.exe`（3.12.0，pytest 9.0.3 / ruff 0.16.1） |
| Python 中文输出 | 未设 `PYTHONUTF8=1` 时按 **gbk** 编码（实测 `中文` → 字节 `3F 3F 3F 3F`）；经 `cmd` 调用需 `chcp 65001` 与 `PYTHONUTF8=1` **两层都设** |
| `findstr` | 对 UTF-8 中文**返回空**且退出码 1（会假报「没有这段」）；搜内容用宿主 `grep`（ripgrep） |
| `conda run` | 23.3.1：**单行** `python -c` 正常（实测 rc=0）；**多行**必败（实测 rc=1，报 `An unexpected error has occurred` 并夹带 `KeyError('pkgs_dirs')` 噪音，读起来像环境坏了，实际只是多行不被支持）——多行脚本先写文件再执行 |

**可写缓存目录的坑**：`.pytest_cache/` 与 `.ruff_cache/` 是 Medium 级别，Low 完整性 shell
写不进去——`ruff check src tests` 会以 `Failed to create temporary file` 失败，pytest 会报
`PytestCacheWarning`。这不是代码问题，改用 `RUFF_CACHE_DIR=%TEMP%\ruff-cache`（或 `--no-cache`）、
pytest 加 `-p no:cacheprovider` 即恢复。

### DSH 技能正本与扫描根

DSH 的技能扫描根按 rank 升序（rank 小者胜同名冲突），**项目根取最近的含 `.git` 祖先**：

| rank | 扫描根 | 本机 Axiom-Flow 会话下的实况 |
| --- | --- | --- |
| 100 | `<项目根>/.dsh/skills` | **本仓正本**：六枚治理技能（见下表） |
| 200 | `<项目根>/.agents/skills` | 不存在 |
| 400 | `~/.dsh/skills` | 不存在 |
| 500 | `~/.agents/skills` | 不存在 |
| 600 | 打包内置 | 内置技能（`dsh-doc`、`office` 等） |

**关键事实**：Axiom-Flow 是独立 git 仓库，其最近 `.git` 祖先就是它自己，因此**项目根
＝ `D:\coding\QED-Engine\Axiom-Flow`**——根仓库 `D:\coding\QED-Engine\.dsh\skills\` 里的九枚
QED 技能**不会**在 Axiom-Flow 会话中被扫描到（它不是本会话的项目根，也不是用户级目录）。
本仓库 `.qoder/skills/` 四件套只服务 Qoder 端，**不是** DSH 的扫描根。

本仓库 `.dsh/skills/` 是 **DSH 端技能正本**，随版本控制入库（`.gitignore` 用 `/.dsh/*` +
`!.dsh/skills/` 的内容模式白名单）。六枚技能与上游根仓库同名技能保持「同骨架、受控差异」：

| 技能 | 作用 | 受控差异 |
| --- | --- | --- |
| `qed-intake` | 任务进场：读必读 → 定级 → 立项 | 作用域只指本仓；ADR 编号改本仓同义编号（0006/0011/0013/0018）；台账列名按本仓七列 |
| `qed-plan` | 计划壳编写与 todo 镜像登记 | 同上；新增「ADR 编号只在本仓库内解释」条 |
| `qed-implement` | code-map 定位 + TDD + 隔离铁律 | 同上；隔离铁律补 `QED_TEST_MYSQL` 例外与工作区边界；工具选择改指针不复制 |
| `qed-closeout` | 门禁 → 关闭 → 同步 | 门禁命令改本仓两条；补六步收口顺序与 `develop` 分支纪律（ADR 0014） |
| `dsh-tooling-conventions` | 本机工具选择与环境摩擦 | 全部实测事实按本仓重写（完整性边界、缓存目录、扫描根、脚本位置） |
| `cjk-safe-edit-and-verify` | 中文检索/编辑的安全流水线 | 探针示例锚改本仓实体（`KA-22`、`/kp-jobs`）；补 DSH 缓存目录回避 |

**受控差异只允许三类**：① 作用域改写（跨仓表述收缩到本仓）；② 编号改写（ADR／守护测试／
命令入口换成本仓同义项；本仓无对应项时改指 standard 的小节名）；③ 本机实测事实（完整性级别、
路径、端口）。**H2 骨架与状态枚举一律不动**——骨架漂移会让两端技能长出两套说法。

**frontmatter 陷阱**：`description` 中含 `: `（冒号+空格）时必须加双引号，否则 YAML 解析失败，
该技能被 DSH **静默丢弃**——不报错，只是目录里没有它。技能目录被 watch，改动热更新到下一次
模型请求，无需重启会话。校验脚本在根仓库 `scripts/check_dsh_skills.py`（本仓库无此脚本，只读调用）。

## 变更与取代

环境变更时更新本文档对应字段；机器迁移时复制本文档并修改 UUID 和主机名。新增「非机器绑定」
事实请写入对应事实源（配置语义 → [model-integration](../design/model-integration.md)，命令 →
[开发指南](../guides/development.md)），本文只留链接。
