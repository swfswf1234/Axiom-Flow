"""
模块职责：提供 v0.2 本地知识审阅工作台的 HTTP API 与静态 Web 入口。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py
"""

from pathlib import Path
from contextlib import asynccontextmanager
import shutil
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.config import Settings
from backend.app.models import ReviewRequest
from backend.app.pipeline import PipelineService
from backend.app.providers import BailianProvider, KnowledgeProvider, VisionProvider
from backend.app.store import MySQLStore
from backend.app.workbook import WorkbookService


def create_app(
    settings: Settings | None = None,
    vision: VisionProvider | None = None,
    knowledge: KnowledgeProvider | None = None,
) -> FastAPI:
    """组装可替换模型依赖的应用，便于本地运行和确定性端到端测试。"""
    resolved_settings = settings or Settings()
    store = MySQLStore(
        resolved_settings.mysql_url,
        resolved_settings.mysql_pool_size,
        resolved_settings.mysql_max_overflow,
    )
    provider = BailianProvider(resolved_settings)
    pipeline = PipelineService(store, resolved_settings, vision or provider, knowledge or provider)
    workbooks = WorkbookService(store, resolved_settings.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.require_schema()
        store.recover_interrupted_runs()
        try:
            yield
        finally:
            store.dispose()

    app = FastAPI(title="Axiom-Flow v0.2", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.pipeline = pipeline
    app.state.workbooks = workbooks
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def document_or_404(document_id: str) -> dict:
        document = store.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="文档不存在")
        return document

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.2.0"}

    @app.get("/api/documents")
    def list_documents() -> list[dict]:
        return store.list_documents()

    @app.post("/api/documents", status_code=201)
    async def upload_document(file: UploadFile = File(...)) -> dict:
        if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="只接受 PDF 文件")
        upload_dir = resolved_settings.data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = upload_dir / f"{uuid.uuid4()}.pdf"
        with temporary_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        try:
            return pipeline.import_pdf(temporary_path, file.filename)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法导入 PDF：{exc}") from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str) -> dict:
        return document_or_404(document_id)

    @app.post("/api/documents/{document_id}/parse")
    async def parse_document(document_id: str) -> dict:
        document_or_404(document_id)
        try:
            return await pipeline.parse_document(document_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"解析失败：{exc}") from exc

    @app.get("/api/documents/{document_id}/pages")
    def list_pages(document_id: str) -> list[dict]:
        document_or_404(document_id)
        pages = store.list_pages(document_id)
        for page in pages:
            page["image_url"] = f"/api/pages/{page['id']}/image"
        return pages

    @app.get("/api/pages/{page_id}/image")
    def page_image(page_id: str) -> FileResponse:
        page = store.get_page(page_id)
        if not page or not Path(page["image_path"]).is_file():
            raise HTTPException(status_code=404, detail="页面图像不存在")
        return FileResponse(page["image_path"], media_type="image/png")

    @app.post("/api/pages/{page_id}/review")
    def review_page(page_id: str, request: ReviewRequest) -> dict:
        try:
            store.review_page(page_id, request.status, request.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return store.get_page(page_id) or {}

    @app.post("/api/documents/{document_id}/candidates")
    async def generate_candidates(document_id: str) -> list[dict]:
        document_or_404(document_id)
        try:
            return await pipeline.generate_candidates(document_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"知识抽取失败：{exc}") from exc

    @app.get("/api/documents/{document_id}/candidates")
    def list_candidates(document_id: str) -> list[dict]:
        document_or_404(document_id)
        return store.list_candidates(document_id)

    @app.post("/api/candidates/{candidate_id}/review")
    def review_candidate(candidate_id: str, request: ReviewRequest) -> dict:
        try:
            store.review_candidate(candidate_id, request.status, request.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return store.get_candidate(candidate_id) or {}

    @app.get("/api/documents/{document_id}/edges")
    def list_edges(document_id: str) -> list[dict]:
        document_or_404(document_id)
        return store.list_edges(document_id)

    @app.post("/api/edges/{edge_id}/review")
    def review_edge(edge_id: str, request: ReviewRequest) -> dict:
        try:
            store.review_edge(edge_id, request.status)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return store.get_edge(edge_id) or {}

    @app.post("/api/documents/{document_id}/workbook/export")
    def export_workbook(document_id: str) -> dict:
        document_or_404(document_id)
        return workbooks.export_draft(document_id)

    @app.post("/api/documents/{document_id}/workbook/import")
    async def import_workbook(document_id: str, file: UploadFile = File(...)) -> dict:
        document_or_404(document_id)
        if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
            raise HTTPException(status_code=400, detail="只接受 XLSX 工作簿")
        upload_dir = resolved_settings.data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = upload_dir / f"{uuid.uuid4()}.xlsx"
        with temporary_path.open("wb") as output:
            shutil.copyfileobj(file.file, output)
        try:
            return workbooks.import_draft(document_id, temporary_path)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            temporary_path.unlink(missing_ok=True)

    @app.get("/api/documents/{document_id}/workbook/download")
    def download_workbook(document_id: str) -> FileResponse:
        document_or_404(document_id)
        revision = store.latest_workbook_revision(document_id)
        if not revision or not Path(revision["path"]).is_file():
            raise HTTPException(status_code=404, detail="尚无工作簿草稿")
        return FileResponse(revision["path"], filename="axiom-flow-draft.xlsx")

    @app.post("/api/documents/{document_id}/publish")
    def publish(document_id: str) -> dict:
        document_or_404(document_id)
        try:
            release = workbooks.publish_latest(document_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        store.update_document_status(document_id, "published")
        return release

    @app.get("/api/documents/{document_id}/graph")
    def graph(document_id: str) -> dict:
        document_or_404(document_id)
        release = store.latest_release(document_id)
        if not release:
            raise HTTPException(status_code=404, detail="尚未发布知识图谱")
        return {"release_id": release["id"], **release["snapshot"]}

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


app = create_app()
