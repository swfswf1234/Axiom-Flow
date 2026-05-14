from typing import Generic, TypeVar, Optional
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def get(self, id: str) -> Optional[ModelType]:
        stmt = select(self.model).where(self.model.doc_id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(self, skip: int = 0, limit: int = 100):
        stmt = select(self.model).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, obj: ModelType) -> ModelType:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, obj: ModelType) -> ModelType:
        await self.session.merge(obj)
        await self.session.flush()
        return obj

    async def delete(self, obj: ModelType):
        await self.session.delete(obj)
        await self.session.flush()

    async def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        result = await self.session.execute(stmt)
        return result.scalar()

    async def exists(self, **kwargs) -> bool:
        stmt = select(self.model).filter_by(**kwargs)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none() is not None
