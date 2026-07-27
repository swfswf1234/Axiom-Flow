"""
模块职责：v0.1 文档处理状态查询接口。
设计关联（DesignRef）：docs/history/2026-07-mineru-baseline/design/api_spec.md
实现状态：Legacy
关联测试：无；当前主链将在新架构重构时替换。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.repository.document_repo import DocumentRepo
from app.repository.layout_repo import LayoutRepo

router = APIRouter()


@router.get("/status/{doc_id}")
async def get_status(doc_id: str, db: AsyncSession = Depends(get_db)):
    repo = DocumentRepo(db)
    doc = await repo.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    layout_repo = LayoutRepo(db)
    block_count = await layout_repo.count()

    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "status": doc.status.value,
        "total_pages": doc.total_pages,
        "layout_blocks": block_count,
        "error": doc.error_message,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
