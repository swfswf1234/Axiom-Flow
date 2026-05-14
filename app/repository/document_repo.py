from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repository.base import BaseRepository
from app.models.document import Document, DocStatus


class DocumentRepo(BaseRepository[Document]):
    def __init__(self, session: AsyncSession):
        super().__init__(Document, session)

    async def get_by_path(self, source_path: str) -> Document | None:
        stmt = select(Document).where(Document.source_path == source_path)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(self, doc_id: str, status: DocStatus, error_message: str = None) -> Document | None:
        doc = await self.get(doc_id)
        if doc:
            doc.status = status
            if error_message:
                doc.error_message = error_message
            await self.session.flush()
        return doc
