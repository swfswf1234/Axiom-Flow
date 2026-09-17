# PDF 解析与渲染管线设计（parsing-pipeline）

设计状态：Accepted
确认状态：暂定
实现状态：In Progress（ingest/schemas 已实现；orchestrator/normalize/engines 待 ARCH-003/016）
最后更新：2026-09-17
关联代码：`src/axiom_flow/ingest/`、`src/axiom_flow/schemas.py`、`src/axiom_flow/orchestrator/`（待实现）、`src/axiom_flow/engines/`（待实现）、`src/axiom_flow/normalize/`（待实现）
关联测试：`tests/unit/test_ingest.py`、`tests/unit/test_schemas.py`（orchestrator/engines 待对应任务）
关联 ADR：`docs/adr/0001-v2-exploration-direction.md`、`docs/adr/0004-model-boundary-and-pipeline-ownership.md`

> 本文档定义解析管线的内部结构：PDF ingest → 引擎适配 → 归一化 → 编排落盘 → 质量信号。
> 对外 API 契约见 `../architecture/api.md`；数据库表见 `../architecture/database-design.md`；
> 模型/引擎配置与对接见 [model-integration.md](model-integration.md)；解析管理闭环见
> [parsing-management.md](parsing-management.md)。

## 背景与决策记录

2026-08-14 用户裁决：Axiom-Flow 转入探索型重构，聚焦「PDF 完整解析 + 统一格式 + 高还原展示」
的后端解析能力。2026-09-14（根仓库 ARCH-020，[ADR 0004](../adr/0004-model-boundary-and-pipeline-ownership.md)）
进一步裁决：**模型生命周期归 8900、解析管线归本仓库**，解析直连模型服务。

关键决策：

| 维度 | 决策 |
| --- | --- |
| 重构策略 | 推倒重来：旧代码与 115+ 测试不沿用，git 历史与 docs/history 保留作经验参考 |
| 管线归属 | 全部在本仓库（ingest/引擎适配/归一化/编排/落盘）；模型生命周期在根仓库 8900 |
| 解析引擎 | 引擎适配器可插拔：MinerU 先落地，PaddleOCR-VL / qwen-vl 预留（见 [model-integration.md](model-integration.md)） |
| 模型调用 | 直连本地模型服务（不经 8900 网关）；统一 blocks 归一化在适配层完成 |
| 展示形态 | 对照模式：原页高清图 + 解析重渲染 + 块级人工校验修改 |
| 持久化 | 产物文件（`QED_DATA_ROOT/parsed/`）+ af_* 四表元数据（见 [parsing-management.md](parsing-management.md)） |
| 前端 | QED-Engine 负责；本仓库只保证 API 契约与产物格式稳定 |

## 总体架构

```mermaid
flowchart LR
    subgraph WIN["Axiom-Flow 8902（Windows）"]
        API["api/ 协议适配"]
        OR["orchestrator/ 任务编排<br/>af_parse_jobs + af_pages"]
        ING["ingest/ PDF→页图<br/>book.json（sha256）"]
        ENG["engines/ 引擎适配器"]
        NRM["normalize/ 统一 blocks"]
        SCH["schemas.py 统一模型"]
        ST["产物落盘<br/>QED_DATA_ROOT/parsed/"]
    end
    subgraph MODELS["本地模型服务（8900 启停托管）"]
        MRU["MinerU 5002"]
        PVL["PaddleOCR-VL（预留）"]
        QVL["qwen-vl（api 档位）"]
    end
    RAW["QED_DATA_ROOT/raw/ 源 PDF（只读）"]

    API --> OR
    OR --> ING
    OR --> ENG
    ENG --> MRU
    ENG -.-> PVL
    ENG -.-> QVL
    ENG --> NRM
    NRM --> SCH
    NRM --> ST
    ING --> RAW
    ING --> ST
```

## 统一格式与产物

产物落 `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`：

```text
<book_id>/
├── book.json            # book_id/title/authors/page_count/sha256/source_file/engine/ingested_at
├── manifest.json        # 产物清单（路径 + 大小 + SHA-256）
└── pages/
    ├── p0001.png        # 原页图（150 DPI，对照左栏 + bbox 基准）
    ├── p0001.md         # 页级 Markdown（$$...$$ / $...$ 内嵌公式）
    └── p0001.blocks.json# 统一 blocks（结构化，含 bbox）
```

统一 blocks schema（`schemas.py` 单一事实源）：

```json
{
  "page": 1,
  "image_size": [1240, 1754],
  "source": "mineru",
  "blocks": [
    {"index": 0, "type": "heading", "level": 1, "text": "...", "bbox": [72, 54, 540, 80], "order": 0, "confidence": 0.97},
    {"index": 1, "type": "formula", "latex": "x + y = y + x", "display": true, "bbox": [72, 150, 540, 175], "order": 1}
  ],
  "quality": {"empty_block_ratio": 0.0, "avg_formula_confidence": 0.95, "table_count": 1, "text_length_ratio": 0.98}
}
```

- 块类型 10 种：`heading / paragraph / formula / table / image / list / caption / header / footer /
  page_number`；按类型强制必需字段（heading.text+level、formula.latex、table.html、image.path、
  list.items、其余 text）。
- `bbox`：原页图像素坐标 `[x0, y0, x1, y1]`（左上原点），基准 `pages/pXXXX.png`；模型坐标系差异
  由 `normalize/` 按 `image_size` 缩放归一。
- 人工编辑为覆盖层（`af_block_edits`），不改产物文件。

## 组件职责

| 模块 | 职责 |
| --- | --- |
| `api/` | 对外 `/api/v1` 路由，只做协议适配（契约见 `../architecture/api.md`） |
| `orchestrator/` | 解析任务编排：接收 job → 逐页/全本 → 调引擎 → 归一化 → 落盘 → 更新 af_parse_jobs/af_pages/af_books（ARCH-003） |
| `ingest/` | PDF 导入：渲染页图（150 DPI+）、生成 book.json（含 SHA-256/page_count）；源 PDF 只读不复制 |
| `engines/` | 引擎适配器：调用模型服务，返回原始输出（见 [model-integration.md](model-integration.md)） |
| `normalize/` | 引擎原始输出 → 统一 blocks（坐标归一、类型映射、阅读序） |
| `schemas.py` | 统一格式 Pydantic 模型（blocks/page/book/job/edit）——内外契约单一事实源 |

## 引擎接入

- 引擎由配置选择（`AXIOM_OCR_ENGINE`，默认 `mineru`）；适配器统一输入（页图字节 / PDF 字节）
  与输出（页级 blocks+markdown+bbox）。
- **逐页解析**：页图 → 引擎单页识别（用于重解析/修补）；**全本解析**：PDF → 引擎整本解析
  （跨页表格/阅读序更优）→ 逐页结果。
- MinerU 经 `POST /file_parse`（同步）或 `/tasks`（异步）调用，取 `content_list`/`middle.json`
  结构化输出（不再只取 markdown 字符串）。
- 详见 [model-integration.md](model-integration.md)。

## 错误处理与质量验收

| 场景 | 行为 |
| --- | --- |
| 单页失败（超时/空输出/异常） | 重试 1 次 → 仍失败标记 `failed`，job 不中断，汇总报告列出失败页 |
| 页质量信号不达标 | 记录质量信号并标记；不自动切换引擎（用户裁决不设自动兜底） |
| 模型服务未启动 | 任务提交前健康检查（`QED_OCR_MODEL_URL`），返回 503 + 提示经 8900 `/models/{slot}` 启停模型 |
| 电源中断/进程被杀 | af_pages 页状态持久化 → 重启后按状态续跑未完成页（幂等） |
| 模型服务不可达 | API 层错误映射为可读中文错误信息，不泄漏堆栈 |

质量信号（每页随 blocks.json 落盘，`QualityMetrics`）：空块率、formula 平均置信度、table
解析成功数、页文本长度与原生文本长度比值（扫描件无原生文本则跳过）。

### 验收标准

1. 代表书（Rudin 中译 + 吉米多维奇习题集）全流程跑通：同步 → ingest → 解析 → 产物落盘；
2. 页级质量：抽查每本 20 页（首/中/尾 + 公式密集页），公式 LaTeX 可被 KaTeX 正确渲染 ≥ 90%
   （人工抽查），块类型标注无明显错乱；
3. API 契约：`/api/v1` 全部端点可用，返回结构与 schemas 一致；
4. 失败恢复：kill 编排进程后重启，未完成页续跑成功。

### 测试策略（探索项目，分层但轻量）

| 层 | 内容 | 工具 |
| --- | --- | --- |
| 单元 | schemas 校验、质量信号计算、归一化、产物文件名/哈希、状态机转换 | pytest |
| 集成 | 编排层对模型服务的调用（mock 服务代替，CI 无 GPU） | pytest + mock |
| 冒烟 | 真实调用：单 PDF 小样（5 页含公式）跑通全链路 | pytest + `@smoke`，本地手动跑 |
| 契约 | API 返回结构与 schemas 一致性、产物三件套完整性 | pytest（轻量） |

本地门禁 `pytest + ruff`（不引入远端 CI）。

## 备注

本文档为解析管线契约（ingest/normalize/orchestrator/engines/schemas 的 DesignRef 事实源）。
实现中允许按实际情况调整（技术选型、端口、产物字段等），调整时同步更新本文档与后续计划。

**配置对齐（2026-09-17，根仓库 PLAN-046 / REQ-075）**：MinerU 端口 8002 → 5002，直连地址读
`QED_OCR_MODEL_URL`（`QED_MINERU_URL` 已删除，不保留别名）；模型生命周期归 8900，本仓库只
连接。v1 的 `llm_client.py` / `fallback/` 随 `engines/` 落地退役（见
[model-integration.md](model-integration.md) 退役清单）。
