# PDF 解析与渲染管线设计（parsing-pipeline）

设计状态：Accepted
实现状态：Partial（ingest/schemas/fallback 已实现，orchestrator 待 V2-004；fallback 为 V2-010 实验版本）
最后更新：2026-08-21
关联代码：`src/axiom_flow/ingest/`、`src/axiom_flow/fallback/`、`src/axiom_flow/schemas.py`、`src/axiom_flow/orchestrator/`（待实现）
关联测试：`tests/unit/test_ingest.py`、`tests/unit/test_schemas.py`、`tests/unit/test_fallback.py`（orchestrator 待对应任务）
关联 ADR：`docs/adr/0001-v2-exploration-direction.md`

> 本文档承接原 pdf-parsing-and-rendering.md（v2 探索基线）的解析管线主体，v0.1 版本文档体系
> 对齐（V2-015）后替代之。对外 API 契约见 `../architecture/api.md`（8902 API 接口文档，REQ-046
> 分类）；服务生命周期脚本契约见 `../architecture/api.md` ①生命周期与健康分类。

## 背景与决策记录

2026-08-14 用户裁决：Axiom-Flow 转入探索型重构，与 QED-Engine 合作展示。已下载 12 本数学分析
书籍（`D:\coding\QED-Engine\dataset\qed-tracker\raw\books\math-qe\01_math_analysis`），第一版
聚焦「PDF 完整解析 + 统一格式 + 高还原展示」的**后端解析能力**，前端展示由 QED-Engine 负责。

关键决策：

| 维度 | 决策 |
| --- | --- |
| 重构策略 | 推倒重来：旧代码与 115+ 测试不沿用，git 历史与 docs/history 保留作经验参考 |
| 第一版范围 | 解析 + 渲染数据供给；Milvus/LangChain/知识图谱/数学解析器列为后续探索项 |
| 解析引擎 | MinerU vlm/hybrid 后端（mineru-api 单体：内嵌 vLLM + MinerU2.5 VLM，OmniDocBench 95.39） |
| 兜底通道 | MinerU 质量不达标时按页调用百炼 qwen-vl-plus，标记 source 区分 |
| 展示形态 | 对照模式：原页高清图 + 解析重渲染，由 QED-Engine 前端实现 |
| 首批书目 | 2 本代表样本（Rudin 英文 + 陈纪修中文）跑通验收后批量处理其余 10 本 |
| 持久化 | 文件系统 + 轻量 SQLite（`data/books/<book_id>/`），Milvus 后续接入 |
| 部署 | 分层混合：WSL Docker Compose 容器化（mineru-api 单体，后续 Milvus），Windows 本地跑编排层与 API |
| 前端 | QED-Engine 负责；本仓库只保证 API 契约与产物格式稳定 |

## 总体架构

```mermaid
flowchart TB
    subgraph QED["QED-Engine（前端/展示）"]
        UI["前端对照展示"]
    end
    subgraph WIN["Windows 本地"]
        API["FastAPI 后端<br/>解析编排 + 对外 API v1"]
        FALLBACK["百炼 qwen-vl-plus 兜底通道"]
        DATA["data/books/&lt;书名&gt;/<br/>页图 + markdown + blocks + sqlite"]
    end
    subgraph WSL["WSL Ubuntu 24.04 + Docker Compose"]
        MA["mineru-api 服务<br/>(内嵌 vLLM, hybrid-engine)"]
        MV["Milvus（后续接入，占位）"]
    end

    UI -->|"REST /api/v1"| API
    API -->|"PDF 上传 / 结果返回"| MA
    API -->|"质量不达标兜底"| FALLBACK
    API -->|"产物落盘"| DATA
```

依赖关系：Windows 应用层 → WSL 推理服务只经 HTTP 双向调用（PDF 上传、解析结果返回），WSL
不直接读写 Windows 文件系统（避免 `/mnt/d` 慢路径），产物一律由 Windows 编排层落盘
`data/`。容器化边界：仅推理服务（GPU 密集型、依赖复杂）进容器；应用层留在 Windows（开发
调试顺手、QED_env 现成）。

## 数据模型与产物格式

```
data/books/
└── <book_id>/                    # 如 01-rudin-en-principles-of-math-analysis
    ├── book.json                 # 元数据：书名/作者/页数/SHA-256/解析策略
    ├── state.sqlite              # 轻量状态：任务、页状态（pending/parsed/fallback/failed）
    ├── pages/
    │   ├── p0001.png             # 原页高清图（150 DPI 以上，QED-Engine 对照左栏）
    │   ├── p0001.md              # 页级统一 Markdown（人类可读+可再渲染）
    │   └── p0001.blocks.json     # 结构化块（机器可读，Milvus 切分点）
    └── manifest.json             # 产物清单（文件路径+大小+哈希，防篡改/增量）
```

每页三件套：`blocks.json`（核心结构化数据，QED-Engine 渲染 + 后续向量化共同基座）、`md`
（页级统一 Markdown，公式以 `$$...$$`/`$...$` 内嵌，KaTeX/MathJax 可渲染）、`png`（原页高清
渲染，坐标换算基准）。

`blocks.json` 结构（块类型枚举：`heading / paragraph / formula / table / image / list /
caption / header / footer / page_number`，Pydantic 模型见 `src/axiom_flow/schemas.py`）：

```json
{
  "page": 1,
  "source": "mineru",
  "blocks": [
    {"type": "heading", "level": 1, "text": "1. The Real and Complex Number Systems",
     "bbox": [72, 54, 540, 80]},
    {"type": "paragraph", "text": "We assume...", "bbox": [72, 96, 540, 132]},
    {"type": "formula", "latex": "x + y = y + x", "display": true,
     "bbox": [72, 150, 540, 175], "confidence": 0.97},
    {"type": "table", "html": "<table>...</table>", "bbox": [72, 200, 540, 300]},
    {"type": "image", "path": "images/p0001_im1.png", "caption": null, "bbox": [...]}
  ]
}
```

设计预留：blocks 的 `paragraph/formula/table` 块即 Milvus 天然切分点（块级向量化，无需
LangChain 文本切分器）；`formula.latex` 字段为数学解析器探索入口；块类型 + text 支持后续
定义/定理语义识别（知识图谱探索）。

## 组件职责

| 模块 | 职责 |
| --- | --- |
| `api/` | 对外 `/api/v1` 路由，只做协议适配，不掺业务（契约见 `../architecture/api.md`） |
| `orchestrator/` | 解析任务编排：接收 job → 逐页调 MinerU → 校验质量 → 需要时走兜底 → 产物落盘 → 更新 state.sqlite（V2-004 承接） |
| `ingest/` | PDF 导入：渲染页图（150 DPI+）、生成 book.json（含 SHA-256）；源 PDF 留在 `dataset/` 只读不复制（sha256 保证校验） |
| `fallback/` | qwen-vl-plus 客户端（复用现有百炼凭据配置），输出归一化为同一 blocks 结构 |
| `schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job）——内外契约单一事实源 |

与旧代码关系：全新包结构，旧 `pdf_pipeline/bailian/mysql` 等全部不引入，仅参考其配置与百炼
凭据接入方式。

### MinerU 接入（WSL / Docker Compose）

- `mineru-api`（官方镜像，内嵌 vLLM 推理引擎；`POST /file_parse` 同步返回解析结果，
  多文件字段名 `files`；另有 `POST /tasks` 异步）；实测版本 3.4.4，`backend=hybrid-engine`
- 数据流：Windows 编排层经 HTTP 上传 PDF → WSL 内解析 → 结果经 HTTP 返回 → 编排层落盘
  `data/books/<book_id>/`
- 模型：MinerU2.5 VLM，模型固化在镜像内（`MINERU_MODEL_SOURCE=local`，约 4.6GB 缓存）；
  容器不挂载 Windows 盘，文件交互经 HTTP 上传
- 第一版固定：单 PDF 顺序页解析（`pipeline` 流式落盘）；GPU 穿透 `--gpus all`
- 部署实测：router 模式（`mineru-router`，独立 vLLM worker）在本环境 worker 502 循环重启
  不可用，采用单体 `mineru-api`；镜像与模型下载在 WSL 侧一次性完成（构建时拉取）
- 模型调用形态：V2-014 后经 `llm_client.py` 双模式兼容层（`local` 直连 / `qed-engine` 经
  8900 网关），编排与容器脚本归根仓库 `scripts/image-model/`（设计见 `model-mode-config.md`）

### 兜底通道（qwen-vl-plus）

- 触发：页级质量信号不达标（空块率高 / formula 置信度 < 阈值 / 解析失败）+ 任务策略为
  `hybrid` 时
- 输入：该页原图（复用 p0001.png）+ 页码提示；输出：归一化 blocks（同一枚举，
  `source=qwen-vl-plus`）
- 阈值与触发规则第一版可配置化（`config.yaml`），先粗后细

### 明确不做（第一版）

- 检索/向量化/知识图谱/数学解析器（仅预留字段）
- 多书并发、断点续跑页级任务（job 幂等即可）
- QED-Engine 前端对接联调（只保证 API 契约与产物格式稳定）

## 错误处理与质量验收

### 错误处理

| 场景 | 行为 |
| --- | --- |
| MinerU 单页失败（超时/空输出/异常） | 重试 1 次 → 仍失败标记 `failed`，job 不中断，汇总报告列出失败页 |
| 页质量信号不达标 | 走兜底通道（strategy=hybrid 时）；纯 local 策略标记 `fallback_skipped` + 记录原因 |
| 兜底也失败 | 页标记 `failed` 保留原因，可单独重跑单页（job 支持页范围） |
| vLLM/容器未启动 | 编排层启动前健康检查，返回 503 + 提示运行容器编排脚本（见 guides/operations.md） |
| 电源中断/进程被杀 | state.sqlite 页状态持久化 → 重启后按状态续跑未完成页（幂等） |
| WSL 不可达 | API 层错误映射为可读中文错误信息，不泄漏堆栈 |

质量信号（每页随 blocks.json 落盘，`QualityMetrics`）：空块率、formula 平均置信度、table
解析成功数、页文本长度与原生文本长度比值（扫描件无原生文本则跳过）。

### 验收标准

1. 2 本代表书（Rudin 英文 + 陈纪修中文）全流程跑通：导入 → 解析 → 产物落盘
2. 页级质量：抽查每本 20 页（首/中/尾 + 公式密集页），公式 LaTeX 可被 KaTeX 正确渲染 ≥ 90%
   （人工抽查），块类型标注无明显错乱
3. API 契约：`/api/v1` 全部端点可用，返回结构与 schemas 一致（见 `../architecture/api.md`）
4. 兜底通道：构造低质量页（如手写笔记扫描页），验证 qwen-vl-plus 兜底触发与 source 标记
5. 失败恢复：kill 编排进程后重启，未完成页续跑成功

### 测试策略（探索项目，分层但轻量）

| 层 | 内容 | 工具 |
| --- | --- | --- |
| 单元 | schemas 校验、质量信号计算、产物文件名/哈希、状态机转换 | pytest |
| 集成 | 编排层对 WSL 服务的调用（mock 服务代替，CI 无 GPU） | pytest + mock |
| 冒烟 | 真实调用：单 PDF 小样（5 页含公式）跑通全链路 + 兜底触发 | pytest + `@smoke`，本地手动跑 |
| 契约 | API 返回结构与 schemas 一致性、产物三件套完整性 | pytest（轻量） |

本地门禁 `pytest + ruff`（不引入远端 CI）。

## 备注

本文档为解析管线契约（ingest/fallback/orchestrator/schemas 的 DesignRef 事实源）。实现中
允许按实际情况调整（技术选型、端口、产物字段等），调整时同步更新本文档与后续计划。
