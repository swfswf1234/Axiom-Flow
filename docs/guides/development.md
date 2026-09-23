# 开发指南

状态：Current
最后更新：2026-09-17

## 环境

| 项 | 值 |
| --- | --- |
| Python | 3.12（Anaconda 环境 `QED_env`：`D:\software\anaconda3\envs\QED_env\python.exe`） |
| 包管理 | `pip install -e ".[dev]"`（pyproject.toml 为依赖事实源） |
| 推理服务 | WSL Ubuntu 24.04 + Docker Compose（见 [运维指南](operations.md)） |

机器绑定事实（UUID、绝对路径、端口速查）见[本地开发环境](../standards/local-dev.md)。

## 必需环境变量

| 变量 | 说明 | 默认值 |
| --- | --- | --- |
| `AXIOM_PORT` | 本服务监听端口 | `8902` |
| `QED_DATA_ROOT` | 数据根：产物落 `parsed/`，源 PDF 读 `raw/` | `<workspace>/dataset` |
| `QED_DB_*` | MySQL 连接（qed 库 af_* 表；`qed_test` 为测试库） | — |
| `AXIOM_OCR_ENGINE` | 解析引擎：`mineru`（先落地）/ `paddleocr-vl` / `qwen-vl`（预留） | `mineru` |
| `QED_OCR_MODEL_URL` | 图像槽位本地端点（MinerU 容器，`/health`） | `http://127.0.0.1:5002` |
| `AXIOM_OCR_TIMEOUT` | **单块**一次解析调用的超时秒数（全本按窗口分块，每块独立计） | `600` |
| `AXIOM_PARSE_WINDOW_SIZE` | 全本解析每块页数（串行提交，真机 W0 冻结） | `56` |
| `API_KEY` / `AXIOM_VISION_MODEL` | 仅 qwen-vl 云端档位预留（`.env` 注释态） | — |

> **ARCH-020（2026-09-14）**：解析直连模型服务、不经 8900 网关；引擎与对接见
> [model-integration](../design/model-integration.md)。
>
> **配置简化（2026-09-17，PLAN-002）**：本服务只做「模型 API 调用 + 预处理/后处理」，模型
> 生命周期与调参归 QED-Engine（8900）；配置三段式——服务 / 数据与数据库 / 解析引擎。
> `QED_MINERU_URL` 删除改为 `QED_OCR_MODEL_URL`（MinerU 端口 8002 → 5002，无旧键别名）。
> 本服务支持独立部署：自身 `.env` 含 `QED_DB_NAME=qed` 与引擎配置；由 QED-Engine 启动时经
> 向上走查跟随根 `.env`（两边配置允许不一致）。

## 常用命令

```powershell
# 安装
& D:\software\anaconda3\envs\QED_env\python.exe -m pip install -e ".[dev]"

# 测试
& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests -q

# 真机冒烟（需 MinerU 5002 在线；默认门禁下 skip，须显式开启）
$env:QED_SMOKE_LIVE = "1"
& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests/smoke -q -m smoke

# MySQL 提交边界集成测试（需本机 mysqld 在线；默认门禁下 skip，须显式开启）
$env:QED_TEST_MYSQL = "1"
& D:\software\anaconda3\envs\QED_env\python.exe -m pytest tests/integration -q

# 静态检查
& D:\software\anaconda3\envs\QED_env\python.exe -m ruff check src tests

# 推理基础设施由根仓库 scripts/image-model/ 托管（见运维指南）
```

> 真机冒烟（`tests/smoke/`）只连本机回环上的模型服务，数据根与数据库用 `tmp_path` 内的合成 PDF 与
> SQLite，不读写真实 `QED_DATA_ROOT`、不写共享 `qed` 库；开启后模型不可达即**失败**，不静默跳过。
> 该「显式开启」口径与 PLAN-003 的集成隔离要求同批，已随 [ADR 0005](../adr/0005-mysql-integration-test-isolation.md)
> 晋升 `standards/testing.md`。
>
> `QED_TEST_MYSQL=1` 的隔离边界同见 ADR 0005：只连回环 MySQL、只用 `qed_test` 库内本方自有的
> `af_*` 四表与 `alembic_version`（夹具 DROP 前逐名断言，绝不 `DROP DATABASE`），数据根用 `tmp_path`。
> **执行时机**：改动 `orchestrator/` 或 `db/` 的提交/持久化语义后必须跑一次该层，默认门禁的 skip
> 不等于这条路径已验证。
>
> **冒烟层同理（2026-09-22 实证）**：改动 `schemas` 契约字段、任务状态机或落库语义后，收口轮必须
> 显式开 `QED_SMOKE_LIVE=1` 跑一次 `tests/smoke/`。PLAN-006 收口时未跑该层，两处断言停在旧契约
> （`progress` 缺 `failed` 键、`parse_status` 兼任结局），漂移直到下个真机窗口才暴露。

## 门禁

推送前必须通过：`pytest tests -q` + `ruff check src tests`（本地唯一门禁，无远端 CI）。

## 新增/改动文档

活跃架构、设计、ADR 或 code-map 变更后，运行契约测试（`tests/contract/`）并保持元数据
（设计状态/实现状态/最后更新/关联代码/关联测试/关联 ADR）同步。文档分类、确认状态与归档
规则见[文档治理规范](../standards/doc-governance.md)。

## AI 开发工作流程

每个任务按六步执行（本指南是流程正文事实源；全局 agent 工具层与技能只引用概念，实际文件由
[AGENTS.md](../../AGENTS.md)「标准映射」定位）：

1. **进场读必读**：读本仓库 `AGENTS.md` → `docs/trackers/project-status.md`（30 秒现状）→
   `docs/standards/local-dev.md`（环境与命令）→ `docs/standards/doc-governance.md`（文档链路）→
   `docs/standards/task-lifecycle.md`（任务分类与计划准入）；涉及契约再读对应 `architecture/`
   文档与 `docs/architecture/code-map.md`。
2. **定级**：按 `AGENTS.md`「变更分级与边界」表判定；未定级不实施，无法判定时询问用户。
3. **立项**：大修改先建 todo 任务 + `docs/plans/` 计划；design 小修/bug 登记对应台账并补设计
   文档；豁免级直接实施。
4. **实现**：用 `code-map.md` 定位模块与关联设计；先写测试再写实现（TDD），遵循
   [测试架构与门禁](../standards/testing.md)的分层与隔离铁律。
5. **验证**：运行本指南「门禁」的完整命令，展示真实输出后才可声称完成。
6. **收尾**：计划关闭两态判定（Retain/Delete）、todo 移入 `completed.md`、同步受影响的
   design/architecture 文档、契约测试全绿。

### 标准映射机制

全局 agent 工具层（`~/.config/opencode/`）与 `qed-*` 技能只引用「概念」（如「文档治理」
「任务生命周期」），不硬编码路径；本仓库实际文件由 `AGENTS.md`「标准映射（概念 → 本仓库
文件）」表定位。新增或改名标准后，同步更新该表与 `docs/standards/index.md`。
