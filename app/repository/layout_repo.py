from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repository.base import BaseRepository
from app.models.layout_block import LayoutBlock


class LayoutRepo(BaseRepository[LayoutBlock]):
    def __init__(self, session: AsyncSession):
        super().__init__(LayoutBlock, session)

    async def get_by_page(self, doc_id: str, page_no: int) -> list[LayoutBlock]:
        stmt = (
            select(LayoutBlock)
            .where(LayoutBlock.doc_id == doc_id, LayoutBlock.page_no == page_no)
            .order_by(LayoutBlock.block_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_doc(self, doc_id: str) -> list[LayoutBlock]:
        stmt = (
            select(LayoutBlock)
            .where(LayoutBlock.doc_id == doc_id)
            .order_by(LayoutBlock.page_no, LayoutBlock.block_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_by_doc(self, doc_id: str):
        stmt = select(LayoutBlock).where(LayoutBlock.doc_id == doc_id)
        result = await self.session.execute(stmt)
        for block in result.scalars():
            await self.session.delete(block)
        await self.session.flush()
