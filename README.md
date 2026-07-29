# Axiom-Flow

Axiom-Flow 是本地优先的技术 PDF 质量与知识审阅工作台。它把数学教材和技术文档转换为
可定位、可审阅、可追溯的内容，再通过人工确认形成知识发布版本。

## 当前能力

当前工程链路包含 PDF 导入、MySQL 持久任务、独立 Worker、版本化 ParseRun、不可变解析产物、
原图/OCR 单页对照、知识候选审阅和 Excel 显式发布。扫描教材只使用百炼 `qwen-vl-ocr`；模型输出
必须经过人工对照，不能直接视为质量结论。

Rudin 物理页 20–39 已完成工程试跑，但公式 OCR 人工质量仍为 Blocked。远端 CI 仍有待定位的
Pytest 失败，因此 `v0.3.0` 尚未发布，317 页整书也未运行。

## 本地启动

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m backend.worker
```

环境变量、数据保护和完整验证命令见
[`docs/guides/local-development.md`](docs/guides/local-development.md)。

## 文档入口

- [`AGENTS.md`](AGENTS.md)：开发者与 Agent 必须遵守的执行协议。
- [`docs/README.md`](docs/README.md)：架构、设计、决策、计划和指南的统一入口。
- [`docs/trackers/current.md`](docs/trackers/current.md)：当前正在执行的工作。

历史 MinerU、PostgreSQL、Qdrant、Celery 和旧 API 资料只保存在 `docs/history/`，不代表当前
运行方式。

## License

MIT
