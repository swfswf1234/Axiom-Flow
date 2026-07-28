# v0.3 本地运行

设计状态：Accepted
实现状态：Implemented
最后更新：2026-07-27

在 `.env` 中设置 `AXIOM_API_KEY` 和 `AXIOM_MYSQL_*`。密钥使用 `SecretStr` 加载，不得写入
命令、日志、工作簿或报告。安装核心依赖并升级非破坏性迁移：

```powershell
python -m pip install -r requirements.txt
python -m alembic upgrade head
```

分别启动 API 和 Worker：

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
python -m backend.worker
```

打开 `http://127.0.0.1:8000`。导入 PDF 后，解析和知识抽取返回持久任务；关闭浏览器不会取消
任务。Worker 重启后会领取排队或租约过期的任务。

开发检查使用：

```powershell
python -m ruff check backend evaluation tests
python -m pytest -q
```

开发库重建不是迁移步骤。只有确认数据可以丢弃时执行
`python -m backend.tools.reset_dev_database --database axiom_flow_test`，并按提示输入完整确认词。
运行库还必须显式增加 `--allow-runtime`；操作前清单写入被忽略的 `data/backups/`。
