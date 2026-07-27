# v0.2 本地运行

v0.2 是单用户、本地优先的 PDF 审阅闭环。运行前确认 Python 环境已安装
`requirements.txt` 中的依赖，并在 `.env` 中设置 `AXIOM_API_KEY`（兼容 `API_KEY`）和
MySQL 连接（优先 `AXIOM_MYSQL_*`，兼容现有 `XQFM_MYSQL_*`）。无关变量会被忽略；
不得把密钥写入命令、日志、工作簿或评测结果。

首次运行或版本更新后先执行迁移。该命令仅创建或更新 `af_` 前缀表，不接触 Legacy 表：

```powershell
python -m alembic upgrade head
```

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000` 后依次完成：导入 PDF、开始解析、逐页审阅、生成并审阅知识
候选和关系、导出或导入工作簿、显式发布。运行事实保存在 MySQL 的 `af_` 表，本地 `data/`
只保存原 PDF、页图和工作簿，且不纳入版本控制。

开发验证使用如下命令，不会调用百炼。测试会在不存在时创建 `axiom_flow_test`，且拒绝测试库
与运行库同名：

```powershell
python -m pytest -q
```
