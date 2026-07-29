"""
模块职责：提供 v0.3 `/api/v1` 协议、文件边界和静态 Web 入口。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_v03_api.py
"""

import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from axiom_flow.api.schemas import (
    ArtifactResponse,
    CommandResponse,
    CurrentParseRunRequest,
    DocumentResponse,
    JobResponse,
    KnowledgeEdgeResponse,
    KnowledgeNodeResponse,
    PageResponse,
    ParseJobRequest,
    ReviewRequest,
)
from axiom_flow.application.ports import ProviderFactory
from axiom_flow.bootstrap import build_container
from axiom_flow.domain.models import ConflictError, NotFoundError


def create_app(
    settings: Any | None = None,
    provider_factory: ProviderFactory | None = None,
) -> FastAPI:
    """组装 API；测试可注入确定性供应商工厂，生产任务仅由 Worker 调用供应商。"""
    container = build_container(settings, provider_factory)
    resolved = container.settings
    documents = container.documents
    jobs = container.jobs
    reviews = container.reviews
    workbooks = container.workbooks

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        container.start()
        try:
            yield
        finally:
            container.close()

    app = FastAPI(title="Axiom-Flow", version="0.3.0", lifespan=lifespan)
    app.state.jobs = jobs
    app.state.settings = resolved
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(404, exc.code, str(exc))

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError) -> JSONResponse:
        return _error(409, exc.code, str(exc))

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
        return documents.list_documents()

    @app.post("/api/v1/documents", status_code=201, response_model=DocumentResponse)
    async def upload_document(file: Annotated[UploadFile, File()]) -> dict:
        if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="只接受 PDF 文件")
        temporary = await _receive_upload(file, resolved.data_dir / "uploads", resolved.max_upload_bytes, ".pdf")
        try:
            return documents.import_pdf(temporary, file.filename)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无法导入 PDF：{str(exc)[:500]}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/api/v1/documents/{document_id}", response_model=DocumentResponse)
    def get_document(document_id: str) -> dict:
        return documents.get_document(document_id)

    @app.post("/api/v1/documents/{document_id}/parse-jobs", status_code=202, response_model=CommandResponse)
    def submit_parse(document_id: str, request: ParseJobRequest | None = None) -> dict:
        selection = request or ParseJobRequest()
        job, created = jobs.submit_parse(document_id, selection.page_start, selection.page_end)
        return {"job": job, "created": created}

    @app.post("/api/v1/documents/{document_id}/extraction-jobs", status_code=202, response_model=CommandResponse)
    def submit_extraction(document_id: str) -> dict:
        job, created = jobs.submit_extraction(document_id)
        return {"job": job, "created": created}

    @app.get("/api/v1/jobs", response_model=list[JobResponse])
    def list_jobs(document_id: str | None = None) -> list[dict]:
        return jobs.list_jobs(document_id)

    @app.get("/api/v1/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str) -> dict:
        return jobs.get_job(job_id)

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=JobResponse)
    def cancel_job(job_id: str) -> dict:
        return jobs.request_cancel(job_id)

    @app.get("/api/v1/documents/{document_id}/parse-runs")
    def list_parse_runs(document_id: str) -> list[dict]:
        return documents.list_parse_runs(document_id)

    @app.get("/api/v1/documents/{document_id}/current-parse-run")
    def current_parse_run(document_id: str) -> dict:
        return documents.current_parse_run(document_id)

    @app.post("/api/v1/documents/{document_id}/current-parse-run")
    def select_current_parse_run(document_id: str, request: CurrentParseRunRequest) -> dict:
        return documents.select_current_parse_run(document_id, request.run_id, request.reason)

    @app.get("/api/v1/parse-runs/{run_id}/pages", response_model=list[PageResponse])
    def list_run_pages(run_id: str) -> list[dict]:
        pages = documents.list_run_pages(run_id)
        for page in pages:
            page["image_url"] = f"/api/v1/pages/{page['id']}/image"
        return pages

    @app.get("/api/v1/parse-runs/{run_id}/pages/{page_no}", response_model=PageResponse)
    def get_run_page(run_id: str, page_no: int) -> dict:
        page = documents.get_run_page(run_id, page_no)
        page["image_url"] = f"/api/v1/pages/{page['id']}/image"
        return page

    @app.get("/api/v1/parse-runs/{run_id}/artifact-summary")
    def get_artifact_summary(run_id: str) -> dict:
        return documents.artifact_summary(run_id)

    @app.get("/api/v1/parse-runs/{run_id}/page-index")
    def get_page_index(run_id: str) -> list[dict]:
        return documents.page_index(run_id)

    @app.get("/api/v1/parse-runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
    def list_run_artifacts(run_id: str) -> list[dict]:
        artifacts = documents.list_artifacts(run_id)
        for artifact in artifacts:
            artifact["download_url"] = f"/api/v1/artifacts/{artifact['id']}/content"
        return artifacts

    @app.get("/api/v1/artifacts/{artifact_id}/content")
    def artifact_content(artifact_id: str) -> FileResponse:
        resource = documents.artifact_file(artifact_id)
        return FileResponse(resource.path, media_type=resource.media_type, filename=resource.filename)

    @app.get("/api/v1/documents/{document_id}/pages", response_model=list[PageResponse])
    def list_latest_pages(document_id: str) -> list[dict]:
        pages = documents.list_current_pages(document_id)
        for page in pages:
            page["image_url"] = f"/api/v1/pages/{page['id']}/image"
        return pages

    @app.get("/api/v1/pages/{page_id}/image")
    def page_image(page_id: str) -> FileResponse:
        resource = documents.page_image(page_id)
        return FileResponse(resource.path, media_type=resource.media_type, filename=resource.filename)

    @app.post("/api/v1/pages/{page_id}/reviews", response_model=PageResponse)
    def review_page(page_id: str, request: ReviewRequest) -> dict:
        page = reviews.review_page(page_id, request.status, request.reason)
        page["image_url"] = f"/api/v1/pages/{page_id}/image"
        return page

    @app.get("/api/v1/documents/{document_id}/knowledge-nodes", response_model=list[KnowledgeNodeResponse])
    def list_nodes(document_id: str) -> list[dict]:
        return reviews.list_nodes(document_id)

    @app.post("/api/v1/knowledge-nodes/{node_id}/reviews", response_model=KnowledgeNodeResponse)
    def review_node(node_id: str, request: ReviewRequest) -> dict:
        return reviews.review_node(node_id, request.status, request.reason)

    @app.get("/api/v1/documents/{document_id}/knowledge-edges", response_model=list[KnowledgeEdgeResponse])
    def list_edges(document_id: str) -> list[dict]:
        return reviews.list_edges(document_id)

    @app.post("/api/v1/knowledge-edges/{edge_id}/reviews", response_model=KnowledgeEdgeResponse)
    def review_edge(edge_id: str, request: ReviewRequest) -> dict:
        return reviews.review_edge(edge_id, request.status, request.reason)

    @app.post("/api/v1/documents/{document_id}/workbook-exports")
    def export_workbook(document_id: str) -> dict:
        return workbooks.export_draft(document_id)

    @app.post("/api/v1/documents/{document_id}/workbook-imports")
    async def import_workbook(document_id: str, file: Annotated[UploadFile, File()]) -> dict:
        documents.get_document(document_id)
        if not file.filename or Path(file.filename).suffix.lower() != ".xlsx":
            raise HTTPException(status_code=400, detail="只接受 XLSX 工作簿")
        temporary = await _receive_upload(file, resolved.data_dir / "uploads", resolved.max_upload_bytes, ".xlsx")
        try:
            return workbooks.import_draft(document_id, temporary)
        finally:
            temporary.unlink(missing_ok=True)

    @app.get("/api/v1/documents/{document_id}/workbook")
    def download_workbook(document_id: str) -> FileResponse:
        resource = workbooks.download(document_id)
        return FileResponse(resource.path, media_type=resource.media_type, filename=resource.filename)

    @app.post("/api/v1/documents/{document_id}/releases")
    def publish(document_id: str) -> dict:
        return workbooks.publish_latest(document_id)

    @app.get("/api/v1/documents/{document_id}/graph")
    def graph(document_id: str) -> dict:
        return workbooks.graph(document_id)

    web_dir = resolved.web_dir.resolve()
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app


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
