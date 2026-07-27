"""
模块职责：初始化 v0.1 PostgreSQL 数据表。
设计关联（DesignRef）：docs/history/2026-07-mineru-baseline/environment.md
实现状态：Legacy
关联测试：无；当前主链将在新架构重构时替换。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 必须先导入模型，才能让 Base.metadata 收集全部旧表定义。
from app.models import Document, LayoutBlock  # noqa
from app.core.database import engine, Base
from app.core.config import Settings


async def init_db():
    settings = Settings()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("Tables created successfully")

    tables = list(Base.metadata.tables.keys())
    print(f"Tables: {tables}")
    await engine.dispose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
