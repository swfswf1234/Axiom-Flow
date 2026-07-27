# API 接口规范

## POST /ingest

提交 PDF 进行解析和索引。

```json
// Request
POST /ingest
{
    "pdf_path": "D:/coding/QED-Engine/QED-Tracker/dataset/textbooks/01_math_analysis/rudin.pdf",
    "title": "Principles of Mathematical Analysis",
    "batch_mode": false
}

// Response (同步模式)
{
    "doc_id": "uuid-xxx",
    "status": "parsing",
    "progress": 0.0,
    "estimated_time": "30s"
}

// Response (异步模式)
{
    "task_id": "celery-task-xxx",
    "status": "queued",
    "position": 1
}
```

## GET /status/{doc_id}

查询文档处理状态。

```json
{
    "doc_id": "uuid-xxx",
    "status": "indexed",         // pending | parsing | parsed | indexing | indexed | failed
    "progress": 1.0,
    "total_pages": 345,
    "parsed_pages": 345,
    "node_count": 1520,
    "layout_blocks": 8900,
    "error": null                // 失败时的错误信息
}
```

## GET /query (Phase 2)

> **注意**：此端点依赖 Phase 2 的 Qdrant 向量索引，当前 Phase 1 暂不实现。

语义检索知识节点。

```json
// Request
GET /query?q=介值定理&top_k=5&doc_id=uuid-xxx

// Response
{
    "results": [
        {
            "node_id": "uuid-node",
            "node_type": "THEOREM",
            "title": "定理 1.1",
            "content": "$$\\lim_{x \\to 0} \\frac{\\sin x}{x} = 1$$",
            "page_no": 12,
            "bbox": {"x0": 72, "y0": 100, "x1": 500, "y1": 140},
            "score": 0.95,
            "doc_title": "Principles of Mathematical Analysis"
        }
    ]
}
```

## GET /layout/{doc_id}?page={page_no}

获取指定页面的布局信息。

```json
{
    "page_no": 12,
    "width": 595.28,
    "height": 841.89,
    "blocks": [
        {
            "block_id": "uuid-block",
            "type": "formula",
            "bbox": {"x0": 72, "y0": 100, "x1": 500, "y1": 140},
            "latex": "\\lim_{x \\to 0} \\frac{\\sin x}{x} = 1"
        }
    ]
}
```

## 技术选型

| 端点 | 同步/异步 | 实现方式 | 实现 Phase |
|------|----------|---------|-----------|
| `/ingest` | 异步 | BackgroundTasks (轻量) / Celery (批量) | Phase 1 |
| `/status` | 同步 | 直接查询 DB | Phase 1 |
| `/layout` | 同步 | 直接查询 PG | Phase 1 |
| `/query` | 同步 | Qdrant 向量检索 + PG 元数据 | Phase 2 |
