# 开发指南

状态：Current  
最后更新：2026-07-27

开始修改前遵循根目录 `AGENTS.md`。新功能先定位到一个计划；涉及重要取舍时新增 ADR。

v0.2 已完成 FastAPI、MySQL、Alembic、本地文件产物与百炼供应商边界的运行迁移。首次运行
或迁移版本更新时执行 `python -m alembic upgrade head`，再以
`python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000` 启动工作台；完整前置条件
和安全约束见 `v02-local-run.md`。

`requirements.txt` 仍包含 Legacy 与后续阶段的候选依赖，因此不得将 MinerU、PostgreSQL、
Qdrant、Celery 误认为 v0.2 的运行前提。依赖清理属于独立维护任务，不能在未验证替代方案时
删除现有条目。
