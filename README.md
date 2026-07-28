# Axiom-Flow

> 本地优先的技术文档质量与知识审阅工作台。

## 项目定位

Axiom-Flow 将数学教材、计算机论文等 PDF 转为可追溯的规范化内容，并通过质量审阅、
知识工作簿和显式发布，形成可供后续学习交互使用的知识体系。首期不把任何单一解析工具
或模型当作唯一真相：文字层、本地解析、百炼视觉模型和 MinerU 都在统一适配边界后运行。

当前优先完成解析质量工作台与知识发布闭环。详细架构、设计、决策和工作状态见
[`docs/README.md`](docs/README.md)。

## 当前状态

v0.3 已实现 MySQL 持久任务、独立 Worker、版本化解析/抽取历史、API v1、Web 审阅和 Excel
显式发布；旧 `app/` 仍是不可扩展的 MinerU 基线。运行步骤见
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
