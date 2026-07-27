"""
模块职责：v0.1 SQLAlchemy 异步数据库连接和会话管理。
设计关联（DesignRef）：docs/history/2026-07-mineru-baseline/design/data_schema.md
实现状态：Legacy
关联测试：无；当前主链将在新架构重构时替换。
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from app.core.config import Settings

settings = Settings()

engine = create_async_engine(settings.db_url, echo=False, pool_size=5, max_overflow=10)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
