# 开发指南

状态：Current  
最后更新：2026-07-27

开始修改前遵循根目录 `AGENTS.md`。新功能先定位到一个计划；涉及重要取舍时新增 ADR。

v0.3 已完成 FastAPI、MySQL、Alembic、本地文件产物、持久任务与百炼供应商边界。首次运行
或迁移版本更新时执行 `python -m alembic upgrade head`，再以
`python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000` 启动 API，并在另一终端
执行 `python -m backend.worker`。完整前置条件和安全约束见 `v03-local-run.md`。

`pyproject.toml` 是依赖事实源；`requirements.txt` 安装核心运行依赖，`requirements-dev.txt`
安装测试和静态检查。扫描解析只配置百炼 OCR；PostgreSQL、Qdrant、
Celery 和 Redis 不是 v0.3 运行前提。
