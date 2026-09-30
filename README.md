# Axiom-Flow

> 解析文档，供给渲染。

Axiom-Flow 是 QED-Engine 的**后端解析组件**：把 PDF（教材、习题集等数学文档）完整解析为统一
格式（Markdown / LaTeX / 结构化块），支撑 QED-Engine 前端做高还原对照展示。前端由 QED-Engine
负责，本仓库只保证 API 契约与产物格式稳定。项目为探索型，当前处于推倒重来后的第一轮
（v2 重建中），详见 [ADR 0001](docs/adr/0001-v2-exploration-direction.md)。

## 在 QED 中的位置

```mermaid
flowchart LR
    A[PDF 教材] --> B[Axiom-Flow 解析]
    B --> C[统一格式产物]
    C --> D[QED-Engine 前端对照展示]
```

本仓库只实现图中的 Axiom-Flow。数据集采集、前端展示和最终学习体验属于外部或后续模块。

## 核心能力

- 导入 PDF（页范围可控），以持久任务方式提交解析。
- 经引擎适配器**直连**本地模型服务（MinerU 先落地）生成页级 Markdown、LaTeX 公式、表格与结构化块；镜像与启停由根仓库 8900 托管（[ADR 0004](docs/adr/0004-model-boundary-and-pipeline-ownership.md)）。
- 页级质量信号与知识块切分；**无自动兜底**：引擎名显式分派，未实现或未知即报错。
- 产物落盘 `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`：页图、markdown、blocks.json、chunks.json、manifest；数据根必须显式配置，仓内 `data/` 已退役（[ADR 0009](docs/adr/0009-data-root-must-be-explicit.md)／[ADR 0012](docs/adr/0012-workspace-directory-hygiene.md)）。
- 对外 `/api/v1` 契约：书目、任务、单页数据与产物清单，供 QED-Engine 前端调用。

## 技术栈

| 范围 | 技术 |
| --- | --- |
| Backend 与协议 | Python 3.12、FastAPI、Pydantic |
| PDF 解析 | MinerU（vlm/hybrid 后端，本仓库直连其服务端点；镜像与容器由根仓库 `scripts/image-model/` 托管） |
| 引擎槽位 | PaddleOCR-VL / qwen-vl 预留（未实现，无自动兜底） |
| 持久化 | 文件系统产物（`parsed/`）＋ `qed` 库 `af_*` 表族（MySQL 为运行库，Alembic 管结构并保持 SQLite 双栈 DDL） |
| 部署 | Windows 侧 8902 服务（`scripts/axiom_flow_service.py`）＋ WSL Ubuntu 24.04/Docker 内的模型服务（根仓库托管） |
| 工程门禁 | Pytest、Ruff（本地唯一门禁，无远端 CI） |

## 能力边界

| 类别 | 内容 |
| --- | --- |
| 输入 | 数学/技术 PDF（当前）、明确的页范围、引擎名（显式分派） |
| 核心职责 | 文档解析、统一格式产出、质量信号、任务编排 |
| 输出 | 原页高清图、页级 Markdown、结构化块（blocks.json）、可校验产物清单 |
| 不负责 | 前端展示（QED-Engine）、检索/向量化（Milvus 后续）、知识图谱（后续） |

开放工作见[待做任务](docs/trackers/todo.md)，探索方向见[路线图](docs/trackers/roadmap.md)。

## 快速启动

前置条件：Python 3.12（QED_env）、WSL Ubuntu 24.04 与 Docker。

```powershell
# 推理容器由根仓库 scripts/image-model/ 托管（见运维指南）；本仓库只需安装依赖
& D:\software\anaconda3\envs\QED_env\python.exe -m pip install -e ".[dev]"
```

解析服务启动与使用方式见[开发指南](docs/guides/development.md)，容器启停与故障排查见
[操作与运维指南](docs/guides/operations.md)。

## 典型流程

1. `POST /api/v1/parse-jobs` 提交解析任务（book_id、页范围缺省＝全本、引擎名）。
2. 编排层经引擎适配器直连模型服务逐页解析，质量信号与分块后原子落盘产物。
3. 未实现或未知引擎名即报错（400/422），不静默切换引擎。
4. QED-Engine 前端经 `/api/v1` 拉取页图与统一格式做对照展示。

## 仓库结构

| 路径 | 职责 |
| --- | --- |
| `src/axiom_flow/` | API、编排、导入、引擎适配、归一化与统一格式 schemas |
| `scripts/` | 长期受管脚本：`axiom_flow_service.py` 是 8902 唯一启停入口（容器脚本归根仓库） |
| `tests/` | 单元、契约、集成与冒烟门禁 |
| `docs/` | 当前架构、设计、决策、规范、指南、计划和追踪器 |
| `tmp/`（仓库根） | 仓内**唯一**临时与留档区：一次性窗口驱动、探针与取证留档；`.gitignore` 排除、不入库、可随手清理（ADR 0012 决定 1） |
| `logs/` | 本项目运行日志与运行时文件（`qed-axiom*`，文件数 ≤3，ADR 0012 决定 4） |

## 开发入口

首次参与开发时依次阅读：

1. [AGENTS.md](AGENTS.md)：强制执行协议。
2. [文档中心](docs/index.md)：按目录定位架构、设计、计划和追踪器。
3. [系统概览](docs/architecture/overview.md)：组件和边界。
4. [开发指南](docs/guides/development.md)：环境、测试与关闭门禁。

## 文档

文档按目录导航见[文档中心](docs/index.md)；工程治理规则以 [docs/standards/](docs/standards/index.md)
为唯一事实源；未关闭任务见[待做任务](docs/trackers/todo.md)；Agent 执行协议见 [AGENTS.md](AGENTS.md)。

## License

MIT
