"""
模块职责：提供 v0.3 `/api/v1` 协议、文件边界和静态 Web 入口。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v03_api.py
"""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.schemas import (
    CommandResponse,
    DocumentResponse,
    JobResponse,
    KnowledgeEdgeResponse,
    KnowledgeNodeResponse,
    PageResponse,
    ReviewRequest,
)
from backend.app.config import Settings
from backend.app.pipeline import PipelineService
from backend.app.providers import BailianProvider
from backend.app.workbook import WorkbookService
from backend.application.jobs import JobApplicationService, ProviderFactory
from backend.infrastructure.mysql import V03Store


def create_app(
    settings: Settings | None = None,
    provider_factory: ProviderFactory | None = None,
) -> FastAPI:
    """组装 API；测试可注入确定性供应商工厂，生产任务仅由 Worker 调用供应商。"""
    resolved = settings or Settings()
    store = V03Store(resolved.mysql_url, resolved.mysql_pool_size, resolved.mysql_max_overflow)
    jobs = JobApplicationService(store, resolved, provider_factory)
    workbooks = WorkbookService(store, resolved.data_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.require_schema()
        try:
            yield
        finally:
            store.dispose()

    app = FastAPI(title="Axiom-Flow", version="0.3.0", lifespan=lifespan)
    app.state.store = store
    app.state.jobs = jobs
    app.state.settings = resolved
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(KeyError)
    async def key_error_handler(_: Request, exc: KeyError) -> JSONResponse:
        return _error(404, "not_found", str(exc).strip("'"))

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return _error(409, "invalid_state", str(exc))

    @app.exception_handler(HTTPException)
    async def http_error_handler(_: Request, exc: HTTPException) -> JSONResponse:
        codes = {400: "invalid_request", 404: "not_found", 413: "file_too_large"}
        return _error(exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = {"errors": exc.errors(include_url=False)}
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "请求参数校验失败", "details": details}},
        )

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": "0.3.0"}

    @app.get("/api/v1/documents", response_model=list[DocumentResponse])
    def list_documents() -> list[dict]:
        return store.list_documents()

    @app.post("/api/v1/documents", status_code=201, response_model=DocumentResponse)
    async def upload_document(file: Annotated[UploadFile, File()]) -> dict:
        if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="只接受 PDF 文件")
        temporary = await _receive_upload(file, resolved.data_dir / "uploads", resolved.max_upload_bytes, ".pdf")
        try:
            provider = BailianProvider(resolved)
            return PipelineService(store, resolved, provider, provider).import_pdf(temporary, file.filename)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法导入 PDF：{str(exc)[:500]}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/api/v1/documents/{document_id}", response_model=DocumentResponse)
    def get_document(document_id: str) -> dict:
        return _document(store, document_id)

    @app.post("/api/v1/documents/{document_id}/parse-jobs", status_code=202, response_model=CommandResponse)
    def submit_parse(document_id: str) -> dict:
        job, created = jobs.submit_parse(document_id)
        return {"job": job, "created": created}

    @app.post("/api/v1/documents/{document_id}/extraction-jobs", status_code=202, response_model=CommandResponse)
    def submit_extraction(document_id: str) -> dict:
        job, created = jobs.submit_extraction(document_id)
        return {"job": job, "created": created}

    @app.get("/api/v1/jobs", response_model=list[JobResponse])
    def list_jobs(document_id: str | None = None) -> list[dict]:
        return store.list_jobs(document_id)

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> dict:
        job = store.get_job(job_id)
        if not job:
            raise KeyError("任务不存在")
        return job

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str) -> dict:
        return store.request_job_cancel(job_id)

    @app.get("/api/v1/documents/{document_id}/parse-runs")
    def list_parse_runs(document_id: str) -> list[dict]:
        _document(store, document_id)
        return store.list_parse_runs(document_id)

    @app.get("/api/v1/parse-runs/{run_id}/pages", response_model=list[PageResponse])
    def list_run_pages(run_id: str) -> list[dict]:
        pages = store.list_pages_for_run(run_id)
        for page in pages:
            page["image_url"] = f"/api/v1/pages/{page['id']}/image"
        return pages

    @app.get("/api/v1/documents/{document_id}/pages", response_model=list[PageResponse])
    def list_latest_pages(document_id: str) -> list[dict]:
        _document(store, document_id)
        pages = store.list_pages(document_id)
        for page in pages:
            page["image_url"] = f"/api/v1/pages/{page['id']}/image"
        return pages

    @app.get("/api/v1/pages/{page_id}/image")
    def page_image(page_id: str) -> FileResponse:
        page = store.get_page(page_id)
        if not page or not Path(page["image_path"]).is_file():
            raise KeyError("页面图像不存在")
        return FileResponse(page["image_path"], media_type="image/png")

    @app.post("/api/v1/pages/{page_id}/reviews", response_model=PageResponse)
    def review_page(page_id: str, request: ReviewRequest) -> dict:
        store.review_page(page_id, request.status, request.reason)
        page = store.get_page(page_id)
        if not page:
            raise KeyError("页面不存在")
        page["image_url"] = f"/api/v1/pages/{page_id}/image"
        return page

    @app.get("/api/v1/documents/{document_id}/knowledge-nodes", response_model=list[KnowledgeNodeResponse])
    def list_nodes(document_id: str) -> list[dict]:
        _document(store, document_id)
        return store.list_candidates(document_id)

    @app.post("/api/v1/knowledge-nodes/{node_id}/reviews", response_model=KnowledgeNodeResponse)
    def review_node(node_id: str, request: ReviewRequest) -> dict:
        store.review_candidate(node_id, request.status, request.reason)
        node = store.get_candidate(node_id)
        if not node:
            raise KeyError("知识节点不存在")
        return node

    @app.get("/api/v1/documents/{document_id}/knowledge-edges", response_model=list[KnowledgeEdgeResponse])
    def list_edges(document_id: str) -> list[dict]:
        _document(store, document_id)
        return store.list_edges(document_id)

    @app.post("/api/v1/knowledge-edges/{edge_id}/reviews", response_model=KnowledgeEdgeResponse)
    def review_edge(edge_id: str, request: ReviewRequest) -> dict:
        store.review_edge(edge_id, request.status, request.reason)
        edge = store.get_edge(edge_id)
        if not edge:
            raise KeyError("知识关系不存在")
        return edge

    @app.post("/api/v1/documents/{document_id}/workbook-exports")
    def export_workbook(document_id: str) -> dict:
        _document(store, document_id)
        return workbooks.export_draft(document_id)

    @app.post("/api/v1/documents/{document_id}/workbook-imports")
    async def import_workbook(document_id: str, file: Annotated[UploadFile, File()]) -> dict:
        _document(store, document_id)
        if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
            raise HTTPException(status_code=400, detail="只接受 XLSX 工作簿")
        temporary = await _receive_upload(file, resolved.data_dir / "uploads", resolved.max_upload_bytes, ".xlsx")
        try:
            return workbooks.import_draft(document_id, temporary)
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/api/v1/documents/{document_id}/workbook")
    def download_workbook(document_id: str) -> FileResponse:
        _document(store, document_id)
        revision = store.latest_workbook_revision(document_id)
        if not revision or not Path(revision["path"]).is_file():
            raise KeyError("尚无工作簿草稿")
        return FileResponse(revision["path"], filename="axiom-flow-draft.xlsx")

    @app.post("/api/v1/documents/{document_id}/releases")
    def publish(document_id: str) -> dict:
        _document(store, document_id)
        release = workbooks.publish_latest(document_id)
        store.update_document_status(document_id, "published")
        return release

    @app.get("/api/v1/documents/{document_id}/graph")
    def graph(document_id: str) -> dict:
        _document(store, document_id)
        release = store.latest_release(document_id)
        if not release:
            raise KeyError("尚未发布知识图谱")
        return {"release_id": release["id"], **release["snapshot"]}

    web_dir = Path(__file__).resolve().parents[2] / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


def _document(store: V03Store, document_id: str) -> dict:
    document = store.get_document(document_id)
    if not document:
        raise KeyError("文档不存在")
    return document


async def _receive_upload(file: UploadFile, directory: Path, limit: int, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{uuid.uuid4()}{suffix}"
    size = 0
    try:
        with temporary.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(status_code=413, detail="上传文件超过大小限制")
                output.write(chunk)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": {}}})


app = create_app()
