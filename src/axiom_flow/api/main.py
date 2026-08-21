"""8902 API v1 路由：对外协议适配，返回结构与 schemas 一致（设计文档 §组件职责）。

设计关联（DesignRef）：docs/architecture/api.md、docs/design/parsing-pipeline.md
实现状态：Current（V2-007 第一版；parse-jobs 为内存态同步执行，V2-004 接手 state.sqlite 与后台任务）
关联测试：tests/contract/test_api_v1_contract.py
"""

import hashlib
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from axiom_flow.fallback import QwenVLPlusClient, VisionError, markdown_to_blocks
from axiom_flow.ingest import default_data_dir
from axiom_flow.schemas import BookMeta, JobStatus, ManifestEntry, ParseJob, ParseJobCreate

_JOBS: dict[str, ParseJob] = {}


def _book_dir(book_id: str) -> Path:
    book_dir = default_data_dir() / "books" / book_id
    if not (book_dir / "book.json").is_file():
        raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
    return book_dir


def _load_book_meta(book_dir: Path) -> BookMeta:
    return BookMeta.model_validate_json((book_dir / "book.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_app() -> FastAPI:
    """应用工厂：数据目录在创建时读取 AXIOM_FLOW_DATA_DIR（默认仓库 data/）。

    识别客户端惰性创建：books/pages/manifest 等读取端点不依赖凭据（独立性降级）；
    仅在提交解析任务（parse-jobs）时按需初始化 qwen-vl-plus 客户端。
    """
    app = FastAPI(title="Axiom-Flow", version="0.4.0")

    @app.get("/api/v1/health")
    def health() -> dict:
        """服务健康探针：8900 控制中心与生命周期脚本（--wait/status）的就绪判据。"""
        return {"status": "ok"}

    @app.get("/api/v1/books", response_model=list[BookMeta])
    def list_books() -> list[BookMeta]:
        """书目列表（含解析进度由页产物推断）。"""
        books: list[BookMeta] = []
        books_root = default_data_dir() / "books"
        if not books_root.is_dir():
            return books
        for book_dir in sorted(books_root.iterdir()):
            meta_path = book_dir / "book.json"
            if meta_path.is_file():
                books.append(BookMeta.model_validate_json(meta_path.read_text(encoding="utf-8")))
        return books

    @app.get("/api/v1/books/{book_id}", response_model=BookMeta)
    def get_book(book_id: str) -> BookMeta:
        """单书目元数据。"""
        return _load_book_meta(_book_dir(book_id))

    @app.get("/api/v1/books/{book_id}/pages/{page_no}")
    def get_page(book_id: str, page_no: int) -> dict:
        """单页完整数据：blocks（结构化）+ markdown（可再渲染）+ 原页图 URL。"""
        book_dir = _book_dir(book_id)
        meta = _load_book_meta(book_dir)
        if not 1 <= page_no <= meta.page_count:
            raise HTTPException(status_code=404, detail=f"页码不存在：{page_no}")
        blocks_path = book_dir / "pages" / f"p{page_no:04d}.blocks.json"
        if not blocks_path.is_file():
            raise HTTPException(status_code=404, detail=f"页产物缺失：第 {page_no} 页")
        blocks = json.loads(blocks_path.read_text(encoding="utf-8"))
        markdown_path = book_dir / "pages" / f"p{page_no:04d}.md"
        markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
        return {
            "blocks": blocks,
            "markdown": markdown,
            "image_url": f"/api/v1/books/{book_id}/pages/{page_no}/image",
        }

    @app.get("/api/v1/books/{book_id}/pages/{page_no}/image")
    def get_page_image(book_id: str, page_no: int) -> FileResponse:
        """原页高清图（对照左栏）。"""
        book_dir = _book_dir(book_id)
        meta = _load_book_meta(book_dir)
        if not 1 <= page_no <= meta.page_count:
            raise HTTPException(status_code=404, detail=f"页码不存在：{page_no}")
        image_path = book_dir / "pages" / f"p{page_no:04d}.png"
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail=f"页图缺失：第 {page_no} 页")
        return FileResponse(image_path, media_type="image/png")

    @app.get("/api/v1/books/{book_id}/manifest", response_model=list[ManifestEntry])
    def get_manifest(book_id: str) -> list[ManifestEntry]:
        """产物清单：文件路径 + 大小 + SHA-256（防篡改/增量）。"""
        book_dir = _book_dir(book_id)
        entries: list[ManifestEntry] = []
        for path in sorted(book_dir.rglob("*")):
            if path.is_file():
                entries.append(
                    ManifestEntry(
                        path=str(path.relative_to(book_dir)),
                        size=path.stat().st_size,
                        sha256=_sha256(path),
                    )
                )
        return entries

    @app.post("/api/v1/parse-jobs", response_model=ParseJob)
    def create_parse_job(payload: ParseJobCreate) -> ParseJob:
        """提交解析任务：同步执行逐页识别（qwen-vl-plus），产物覆盖落盘。

        第一版内存态同步执行（页数少时可用）；V2-004 接手 state.sqlite 与后台任务。
        """
        book_dir = _book_dir(payload.book_id)
        meta = _load_book_meta(book_dir)
        pages = payload.pages or list(range(1, meta.page_count + 1))
        job = ParseJob(
            id=uuid.uuid4().hex[:12],
            book_id=payload.book_id,
            pages=pages,
            strategy=payload.strategy,
            status=JobStatus.RUNNING,
            progress={"parsed": 0, "total": len(pages)},
        )
        _JOBS[job.id] = job
        try:
            client = QwenVLPlusClient()
            for no in pages:
                if not 1 <= no <= meta.page_count:
                    job.status = JobStatus.FAILED
                    break
                image_path = book_dir / "pages" / f"p{no:04d}.png"
                if not image_path.is_file():
                    continue
                result = client.recognize_page(image_path, page_no=no)
                page = markdown_to_blocks(result.markdown, page=no)
                (book_dir / "pages" / f"p{no:04d}.md").write_text(result.markdown, encoding="utf-8")
                (book_dir / "pages" / f"p{no:04d}.blocks.json").write_text(
                    page.model_dump_json(indent=2) + "\n", encoding="utf-8"
                )
                job.progress["parsed"] += 1
            job.status = JobStatus.COMPLETED
        except (VisionError, ValueError) as exc:
            job.status = JobStatus.FAILED
            job.progress["error"] = str(exc)[:200]
        return job

    @app.get("/api/v1/parse-jobs/{job_id}", response_model=ParseJob)
    def get_parse_job(job_id: str) -> ParseJob:
        """任务状态与进度。"""
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
        return job

    return app


app = create_app()
