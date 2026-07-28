# Axiom-Flow

> 本地优先的技术文档质量与知识审阅工作台。

## 项目定位

Axiom-Flow 将数学教材、计算机论文等 PDF 转为可追溯的规范化内容，并通过质量审阅、
知识工作簿和显式发布，形成可供后续学习交互使用的知识体系。当前扫描教材工程链路只使用
百炼 `qwen-vl-ocr`，其结果必须与原始页图对照审阅，不能直接视为质量真相。

当前优先完成解析质量工作台与知识发布闭环。详细架构、设计、决策和工作状态见
[`docs/README.md`](docs/README.md)。

## 当前状态

v0.3 工程基线已实现 MySQL 持久任务、独立 Worker、版本化解析/抽取历史、显式当前 ParseRun、
共享页图、API v1、单页 Web 对照审阅和 Excel 显式发布。v0.1 及 `backend/app/` 运行代码已移除，
原始资料只保留在历史文档中。Rudin 20 页链路可展示，但公式 OCR 质量仍处于 Blocked。运行步骤见
[`docs/guides/v03-local-run.md`](docs/guides/v03-local-run.md)。

请不要依据历史 MinerU、PostgreSQL、Qdrant 或 Celery 说明配置 v0.3；扫描件正式路由、批量导入、
向量检索、Neo4j 和学习聊天会在已发布知识快照稳定后逐步加入。

## 文档结构

```text
AGENTS.md                     # Agent 与开发执行协议
docs/                         # 当前设计、决策、计划和指南
├── architecture/             # 已接受的架构边界
├── design/                   # 目标设计
├── adr/                       # 关键决策记录
├── plans/                     # 可执行计划
├── trackers/                  # 当前状态与路线图
├── guides/                    # 可重复操作指南
└── history/                   # 已归档的 v0.1 基线
```

任何实现开始前阅读根目录 [`AGENTS.md`](AGENTS.md) 与
[`docs/README.md`](docs/README.md)。旧的 v0.1 文档已完整保存在
`docs/history/2026-07-mineru-baseline/`，仅作历史追溯。

## License

MIT
