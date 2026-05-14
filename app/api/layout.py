from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.repository.layout_repo import LayoutRepo

router = APIRouter()


@router.get("/layout/{doc_id}")
async def get_layout(
    doc_id: str,
    page: int = Query(None, description="Page number (1-indexed)"),
    db: AsyncSession = Depends(get_db),
):
    repo = LayoutRepo(db)

    if page is not None:
        blocks = await repo.get_by_page(doc_id, page)
    else:
        blocks = await repo.get_by_doc(doc_id)

    if not blocks:
        return {"doc_id": doc_id, "page_no": page, "blocks": []}

    result = [
        {
            "block_id": b.block_id,
            "type": b.block_type.value,
            "content": b.content,
            "latex": b.latex,
            "bbox": b.bbox,
            "page_no": b.page_no,
            "confidence": b.confidence,
        }
        for b in blocks
    ]

    return {"doc_id": doc_id, "page_no": page, "blocks": result, "total": len(result)}
