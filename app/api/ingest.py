from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.config import Settings
from app.models.document import Document, DocStatus
from app.repository.document_repo import DocumentRepo
from app.services.mineru_service import MinerUService

settings = Settings()
router = APIRouter()


class IngestRequest(BaseModel):
    pdf_path: str
    title: str = ""
    course_number: str = ""


class IngestResponse(BaseModel):
    doc_id: str
    status: str
    message: str = ""


@router.post("/ingest", response_model=IngestResponse)
async def ingest_pdf(req: IngestRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    repo = DocumentRepo(db)

    existing = await repo.get_by_path(req.pdf_path)
    if existing:
        raise HTTPException(status_code=409, detail=f"Document already ingested: {existing.doc_id}")

    doc = Document(
        title=req.title or Path(req.pdf_path).stem,
        source_path=req.pdf_path,
        course_number=req.course_number,
        status=DocStatus.PENDING,
    )
    doc = await repo.create(doc)
    await db.commit()

    from pathlib import Path
    background_tasks.add_task(process_pdf_background, doc.doc_id, req.pdf_path)

    return IngestResponse(doc_id=doc.doc_id, status="queued", message="PDF queued for parsing")


async def process_pdf_background(doc_id: str, pdf_path: str):
    from app.core.database import SessionLocal
    from app.repository.layout_repo import LayoutRepo
    from app.models.layout_block import LayoutBlock, BlockType

    async with SessionLocal() as db:
        repo = DocumentRepo(db)
        layout_repo = LayoutRepo(db)

        await repo.update_status(doc_id, DocStatus.PARSING)
        await db.commit()

        try:
            mineru = MinerUService()
            result = mineru.parse_pdf(pdf_path)

            doc = await repo.get(doc_id)
            if result.pages:
                doc.total_pages = len(result.pages)

            for page_data in result.pages:
                page_no = page_data.get("page_no", 0)
                for block_data in page_data.get("blocks", []):
                    btype = block_data.get("type", "text")
                    if btype not in ("text", "formula", "table", "figure", "heading"):
                        btype = "text"

                    block = LayoutBlock(
                        doc_id=doc_id,
                        page_no=page_no,
                        block_type=BlockType(btype),
                        content=block_data.get("content", "") or block_data.get("text", ""),
                        latex=block_data.get("latex"),
                        bbox=block_data.get("bbox") or block_data.get("box", {}),
                        confidence=block_data.get("confidence", block_data.get("score", 1.0)),
                    )
                    await layout_repo.create(block)

            doc.layout_ref = str(mineru.output_dir / Path(pdf_path).stem)
            await repo.update_status(doc_id, DocStatus.PARSED)
            await db.commit()

        except Exception as e:
            import traceback
            await repo.update_status(doc_id, DocStatus.FAILED, error_message=str(e))
            await db.commit()
            traceback.print_exc()
