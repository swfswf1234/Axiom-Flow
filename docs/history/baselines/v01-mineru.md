# v0.1 MinerU 基线

状态：Historical
最后更新：2026-07-29
Git 锚点：`6cc4129`
取代起点：`4961cfa`

## 保留目的

本摘要用于定位 2026-05 的首次可运行基线，不作为当前架构、依赖或操作依据。原始代码、文档、
需求文件和工作日志完整存在于 Git commit `6cc4129`，当前树不再复制其内容。

## 当时边界

| 范围 | 历史状态 |
| --- | --- |
| PDF 解析 | `app/services/mineru_service.py` 使用 MinerU，支持单篇和批量调用。 |
| HTTP | FastAPI 提供 ingest、status 和 layout 接口，没有当前 `/api/v1` 契约。 |
| 持久化 | PostgreSQL 保存 Document 和 LayoutBlock，不是当前 MySQL `af_` 事实源。 |
| 本地文件 | 使用 `data/raw`、`data/parsed` 和 `data/exports` 约定。 |
| LlamaIndex、Qdrant、Redis、Celery | 文档中的后续规划，未作为该提交的已实现基线。 |

该路线已被当前[运行架构](../../architecture/runtime-architecture.md)、
[数据生命周期](../../architecture/data-lifecycle.md)、[ADR 0005](../../adr/0005-mysql-runtime-storage.md)、
[ADR 0006](../../adr/0006-persistent-jobs-and-api-v1.md)和
[ADR 0010](../../adr/0010-qwen-ocr-only-rudin-trial.md)取代。MinerU 旧命令、PostgreSQL schema 和
规划中的检索栈均不得用于解释当前实现。

## 恢复方式

以下只读命令可列出或读取原始快照，不需要在当前树保留副本：

```powershell
git ls-tree -r --name-only 6cc4129 docs app
git show 6cc4129:docs/architecture.md
git show 6cc4129:app/services/mineru_service.py
```

需要复现实验时应创建隔离 worktree 并单独评估旧依赖与数据安全；不得把旧文件直接复制回当前
`main` 或把历史结果解释为当前质量结论。
