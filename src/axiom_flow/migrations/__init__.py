"""Alembic 迁移目录：af_* 表结构（四表一次建全）。

- `env.py`：URL 解析（`-x db_url` > `AXIOM_DB_URL` > alembic.ini > `QED_DB_*`）；
- `versions/`：迁移脚本；表结构事实源 `docs/architecture/database-design.md`。

用法（仓库根）：`python -m alembic upgrade head`（或 `alembic downgrade base`）。
"""
