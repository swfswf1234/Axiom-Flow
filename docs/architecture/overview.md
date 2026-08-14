# 系统概览（v2）

设计状态：Accepted
实现状态：Pending
最后更新：2026-08-14
关联代码：无（新包结构实现中，见 code-map）
关联测试：无（新测试体系建立中）
关联 ADR：`docs/adr/0001-v2-exploration-direction.md`

## 定位

Axiom-Flow 是 QED-Engine 的**后端解析组件**：把 PDF 教材完整解析为统一格式（Markdown/LaTeX/
结构化块），支撑 QED-Engine 前端做高还原对照展示。前端由 QED-Engine 负责，本仓库只保证
API 契约与产物格式稳定。

## 运行拓扑

```mermaid
flowchart TB
    subgraph QED["QED-Engine（前端/展示）"]
        UI["前端对照展示"]
    end
    subgraph WIN["Windows 本地"]
        API["FastAPI 后端<br/>解析编排 + 对外 API v1"]
        FALLBACK["百炼 qwen-vl-plus 兑底通道"]
        DATA["data/books/&lt;书名&gt;/<br/>页图 + markdown + blocks + sqlite"]
    end
    subgraph WSL["WSL Ubuntu 24.04 + Docker Compose"]
        MA["mineru-api 服务<br/>(MinerU 编排/任务)"]
        V["vLLM 推理服务<br/>(MinerU2.5 VLM, GPU 穿透)"]
        MV["Milvus（后续接入，占位）"]
    end

    UI -->|"REST /api/v1"| API
    API -->|"PDF 上传 / 结果返回"| MA
    MA -->|"OpenAI 兼容推理"| V
    API -->|"质量不达标兑底"| FALLBACK
    API -->|"产物落盘"| DATA
```

## 边界原则

- Windows 应用层 → WSL 推理服务只经 HTTP 双向调用，WSL 不直接读写 Windows 文件系统。
- 容器化边界：仅推理服务（GPU 密集型、依赖复杂）进容器；应用层留在 Windows。
- 产物一律由 Windows 编排层落盘 `data/`，milvus 后续接入不改变产物格式。

## 第一版范围

解析 + 渲染数据供给（导入 → MinerU 解析 → 质量校验 → 兜底 → 产物落盘 → API 暴露）。
Milvus 检索、知识图谱、数学解析器为后续探索项，仅预留字段。
