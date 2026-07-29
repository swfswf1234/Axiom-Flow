# 本地开发与运行

状态：Current
最后更新：2026-07-29

## 前置条件

使用 Python 3.12、MySQL 8 和 Node.js。复制 `.env.example` 的字段到本地 `.env`，设置
`AXIOM_API_KEY` 与 `AXIOM_MYSQL_*`；密钥不得写入命令、日志、工作簿或报告。

`pyproject.toml` 是依赖事实源。仅运行项目时使用兼容入口：

```powershell
python -m pip install -r requirements.txt
```

参与开发时直接安装项目及 `dev` 可选依赖，不需要先执行运行安装命令：

```powershell
python -m pip install -e ".[dev]"
```

## 迁移与启动

```powershell
python -m alembic upgrade head
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
python -m backend.worker
```

API 与 Web 工作台位于 `http://127.0.0.1:8000`。API 和 Worker 是独立进程；关闭浏览器不会
取消持久任务。

扫描教材按 ADR 0010 只使用 `qwen-vl-ocr`。Rudin 工程试跑必须显式提交
`{"page_start":20,"page_end":39}`；没有新的实验 manifest 和 ADR 时不得运行 317 页整书。

## 本地门禁

```powershell
python -m ruff check .
python -m pytest -q
node --check web/app.js
git diff --check
```

开发库重建和解析运行清理属于受保护操作，执行前阅读 [`operations.md`](operations.md)。
