"""8902 API v1 路由：对外协议适配，返回结构与 schemas 一致（契约 docs/architecture/api.md）。

实现状态：Current（af_* 持久化 + 异步 parse-jobs + 分块预览；对照编辑为覆盖层合并）。
只读端点不依赖模型服务与凭据；仅解析任务经引擎适配器直连模型服务（ADR 0004），且提交期
探活（不可用 503，不落任务行）、引擎名按 `AXIOM_OCR_ENGINE`/请求体显式分派（无自动兜底）。
读取面按**生效解析版本**解析目录（`af_books.active_job_id`，PLAN-004 / REQ-080）：指针唯一入口
是 `_read_scope`，URL、响应结构与 `image_url` 字面量对根仓库前端保持不变。
"""

import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow import __version__ as VERSION
from axiom_flow.config import Settings, load_settings
from axiom_flow.db.engine import create_engine_for
from axiom_flow.db.models import AfBlockEdit, AfBook
from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
    BookRepository,
    BookSyncRow,
    EditRepository,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.base import ParseEngine
from axiom_flow.engines.factory import EngineSelectionError, build_engine, resolve_engine_name
from axiom_flow.ingest import book_dir_for, ingest_book, version_dir_for, write_manifest
from axiom_flow.normalize.chunking import build_chunks, read_chunk_set
from axiom_flow.orchestrator import MODEL_UNAVAILABLE, JobRunner, rebuild_chunks
from axiom_flow.schemas import (
    SAFE_SEGMENT_PATTERN,
    Block,
    BlocksPage,
    BookMeta,
    BookOut,
    BookSyncItem,
    BookUpdate,
    ChunkParams,
    ChunkSet,
    EditRecord,
    EditRequest,
    IngestResponse,
    JobStatus,
    ManifestEntry,
    PageData,
    ParseJob,
    ParseJobCreate,
    ParseStatus,
    ParseVersionOut,
    SyncResponse,
)

# 路径参数中的 book_id 必须是安全路径段（防目录穿越，与 schemas 请求体同一约束）。
SafeBookId = Annotated[str, PathParam(pattern=SAFE_SEGMENT_PATTERN)]


def _book_out(book: AfBook) -> BookOut:
    return BookOut(
        book_id=book.book_id,
        domain_id=book.domain_id,
        course_id=book.course_id,
        course_name=book.course_name,
        knowledge_id=book.knowledge_id,
        title=book.title,
        part=book.part,
        display_title=book.display_title,
        authors=book.authors or [],
        file_path=book.file_path,
        page_count=book.page_count,
        sha256=book.sha256,
        ingest_status=book.ingest_status,
        parse_status=book.parse_status,
        pages_done=book.pages_done,
        active_job_id=book.active_job_id,
        notes=book.notes,
    )


def _edit_record(edit: AfBlockEdit) -> EditRecord:
    return EditRecord(
        edit_id=edit.edit_id,
        book_id=edit.book_id,
        job_id=edit.job_id,
        page_no=edit.page_no,
        block_index=edit.block_index,
        block_type=edit.block_type,
        verdict=edit.verdict or "",
        note=edit.note,
        corrected_text=edit.corrected_text,
        corrected_bbox=tuple(edit.corrected_bbox) if edit.corrected_bbox else None,
        edited_at=edit.edited_at,
        updated_at=edit.updated_at,
    )


def _probe_model_service(engine: ParseEngine) -> None:
    """提交期探活：模型服务不可用 → 503 且不创建任务行（受理后故障才记 job `failed`）。"""
    try:
        healthy = engine.health()
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            close()
    if not healthy:
        raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE)


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    engine_factory: Callable[[str], ParseEngine] | None = None,
    background_jobs: bool = True,
) -> FastAPI:
    """应用工厂：数据根/数据库/引擎在创建时装配；测试可注入 sqlite 会话与假引擎。

    `engine_factory(name)` 以**已解析的引擎名**构造适配器（`engines/factory.build_engine`），
    名字解析发生在提交期：未实现或未知引擎直接 400/422，不回落 MinerU（ADR 0004）。
    """
    settings = settings or load_settings()
    data_root = Path(settings.data_dir)

    db_engine = None
    if session_factory is None:
        db_engine = create_engine_for(settings)
        session_factory = sessionmaker(bind=db_engine, future=True)
    make_engine = engine_factory or (lambda name: build_engine(name, settings))
    runner = JobRunner(
        session_factory=session_factory,
        engine_factory=make_engine,
        data_root=data_root,
        background=background_jobs,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        runner.shutdown()
        if db_engine is not None:
            db_engine.dispose()

    app = FastAPI(title="Axiom-Flow", version=VERSION, lifespan=lifespan)

    def db_session() -> Iterator[Session]:
        with session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    SessionDep = Annotated[Session, Depends(db_session)]

    def _find_book_dir(session: Session, book_id: str) -> Path | None:
        book = BookRepository(session).get(book_id)
        if book is not None:
            candidate = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
            if (candidate / "book.json").is_file():
                return candidate
        for candidate in sorted(data_root.glob(f"parsed/*/*/{book_id}")):
            if (candidate / "book.json").is_file():
                return candidate
        return None

    def _require_book_dir(session: Session, book_id: str) -> Path:
        book_dir = _find_book_dir(session, book_id)
        if book_dir is None:
            raise HTTPException(status_code=404, detail=f"书目不存在或产物缺失：{book_id}")
        return book_dir

    def _read_scope(session: Session, book_id: str) -> tuple[Path, str]:
        """生效解析版本解析的唯一入口（V6）：返回 `(产物目录, job_id 作用域)`。

        `active_job_id` 非空 → 该版本目录；为空 → 书目录根（legacy 根视图，迁移前存量产物与
        重 ingest 后的目录都走这里，故根仓库前端不需迁移即可继续读取）。生效目录缺失时显式
        404 而不回落根视图：静默回落会让读者把旧产物当成新版本核对。
        """
        book = BookRepository(session).get(book_id)
        book_dir = _require_book_dir(session, book_id)
        if book is None or not book.active_job_id:
            return book_dir, LEGACY_JOB_ID
        version_dir = version_dir_for(
            data_root, book.domain_id, book.course_id, book_id, book.active_job_id
        )
        if not version_dir.is_dir():
            raise HTTPException(
                status_code=404, detail=f"生效版本产物缺失：{book_id} @ {book.active_job_id}"
            )
        return version_dir, book.active_job_id

    def _load_page(artifact_dir: Path, page_no: int) -> BlocksPage:
        path = artifact_dir / "pages" / f"p{page_no:04d}.blocks.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"页产物缺失：第 {page_no} 页")
        return BlocksPage.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_pages(artifact_dir: Path) -> list[BlocksPage]:
        pages: list[BlocksPage] = []
        for path in sorted((artifact_dir / "pages").glob("p*.blocks.json")):
            try:
                pages.append(BlocksPage.model_validate_json(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return pages

    def _books_from_filesystem() -> list[BookOut]:
        books: list[BookOut] = []
        for book_json in sorted(data_root.glob("parsed/*/*/*/book.json")):
            try:
                book_meta = BookMeta.model_validate_json(book_json.read_text(encoding="utf-8"))
            except ValueError:
                continue
            book_dir = book_json.parent
            books.append(
                BookOut(
                    book_id=book_meta.book_id,
                    title=book_meta.title,
                    authors=book_meta.authors,
                    file_path=book_meta.source_file,
                    page_count=book_meta.page_count,
                    sha256=book_meta.sha256,
                    ingest_status="ingested",
                    parse_status="pending",
                    pages_done=len(list((book_dir / "pages").glob("p*.blocks.json"))),
                )
            )
        return books

    @app.get("/api/v1/health")
    def health() -> dict:
        """服务健康探针：8900 控制中心与生命周期脚本（--wait/status）的就绪判据。"""
        return {"status": "ok"}

    # ---------- ② 数据查询（af_*） ----------

    @app.get("/api/v1/books", response_model=list[BookOut])
    def list_books(session: SessionDep) -> list[BookOut]:
        """书目列表：af_books；空表回退文件系统产物（独立性降级）。"""
        rows = BookRepository(session).list_books()
        return [_book_out(row) for row in rows] if rows else _books_from_filesystem()

    @app.get("/api/v1/books/{book_id}", response_model=BookOut)
    def get_book(book_id: SafeBookId, session: SessionDep) -> BookOut:
        """单书目元数据。"""
        book = BookRepository(session).get(book_id)
        if book is not None:
            return _book_out(book)
        for candidate in _books_from_filesystem():
            if candidate.book_id == book_id:
                return candidate
        raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")

    @app.patch("/api/v1/books/{book_id}", response_model=BookOut)
    def update_book(book_id: SafeBookId, payload: BookUpdate, session: SessionDep) -> BookOut:
        """修改书目（Axiom-Flow 自有字段，如 notes）。"""
        repo = BookRepository(session)
        if not repo.update_notes(book_id, payload.notes):
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        book = repo.get(book_id)
        assert book is not None
        return _book_out(book)

    @app.delete("/api/v1/books/{book_id}")
    def delete_book(book_id: SafeBookId, session: SessionDep, purge: bool = False) -> dict:
        """删除 af_books 行与该书**所有解析版本**的 af_pages / af_block_edits 行；
        `?purge=true` 同时删除整本产物目录（含 `versions/` 历史版本）。

        af_parse_jobs 历史行保留为审计线索（不随书目删除，条文 9）。
        """
        repo = BookRepository(session)
        book = repo.get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        book_dir = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
        PageRepository(session).delete_for_book(book_id)
        EditRepository(session).delete_for_book(book_id)
        repo.delete(book_id)
        if purge and book_dir.is_dir():
            shutil.rmtree(book_dir)
        return {"book_id": book_id, "purged": bool(purge)}

    @app.post("/api/v1/books/sync", response_model=SyncResponse)
    def sync_books(items: list[BookSyncItem], session: SessionDep) -> SyncResponse:
        """批量同步已验证书目（幂等 upsert af_books，book_id 同源 qt_books）。"""
        rows = [
            BookSyncRow(
                book_id=item.book_id,
                domain_id=item.domain_id,
                course_id=item.course_id,
                course_name=item.course_name,
                knowledge_id=item.knowledge_id,
                title=item.title,
                part=item.part,
                display_title=item.display_title,
                authors=[author.model_dump() for author in item.authors],
                file_path=item.file_path,
            )
            for item in items
        ]
        repo = BookRepository(session)
        result = repo.upsert_many(rows)
        books = [repo.get(item.book_id) for item in items]
        return SyncResponse(
            synced=result.synced,
            updated=result.updated,
            books=[_book_out(book) for book in books if book is not None],
        )

    @app.post("/api/v1/books/{book_id}/ingest", response_model=IngestResponse)
    def ingest(book_id: SafeBookId, session: SessionDep) -> IngestResponse:
        """渲染页图 + book.json / manifest.json（不调模型），回写 ingest 进度。

        重复 ingest 替换的只是**根视图**（`book.json` + `pages/`）：解析版本目录完整保留，
        生效指针回到 `NULL`（根视图），`af_pages` 只清 legacy 行、版本作用域行不动（条文 8 +
        V8）。源文件变了之后旧版本仍可从 `versions/` 直读，但不再对生效端点可见，需重新解析。
        """
        repo = BookRepository(session)
        book = repo.get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        try:
            result = ingest_book(
                book_id=book_id,
                source_file=book.file_path,
                data_root=data_root,
                domain_id=book.domain_id,
                course_id=book.course_id,
                title=book.title,
                authors=book.authors or [],
                engine=settings.ocr_engine,
            )
        except ValueError as exc:
            code = 404 if "源 PDF 不存在" in str(exc) else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        repo.update_ingest(book_id, page_count=result.page_count, sha256=result.sha256)
        repo.set_active_job(book_id, None)
        repo.update_parse(book_id, parse_status=ParseStatus.PENDING, pages_done=0)
        PageRepository(session).reset_legacy(book_id)
        return IngestResponse(book_id=book_id, page_count=result.page_count, sha256=result.sha256)

    # ---------- ③ 解析结果与 PDF 对照 ----------

    @app.get("/api/v1/books/{book_id}/manifest", response_model=list[ManifestEntry])
    def get_manifest(book_id: SafeBookId, session: SessionDep) -> list[ManifestEntry]:
        """生效版本的产物清单：文件路径 + 大小 + SHA-256（防篡改/增量），路径相对版本目录。"""
        artifact_dir, _ = _read_scope(session, book_id)
        return write_manifest(artifact_dir)

    @app.get("/api/v1/books/{book_id}/pages/{page_no}", response_model=PageData)
    def get_page(book_id: SafeBookId, page_no: int, session: SessionDep) -> PageData:
        """单页完整数据：结构化 blocks + 页级 markdown + 原页图 URL + 已合并人工编辑。"""
        if page_no < 1:
            raise HTTPException(status_code=400, detail=f"页码非法：{page_no}")
        artifact_dir, job_id = _read_scope(session, book_id)
        blocks = _load_page(artifact_dir, page_no)
        markdown_path = artifact_dir / "pages" / f"p{page_no:04d}.md"
        edits = [
            _edit_record(edit)
            for edit in EditRepository(session).list_for_page(book_id, job_id, page_no)
        ]
        return PageData(
            blocks=blocks,
            markdown=markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else "",
            image_url=f"/api/v1/books/{book_id}/pages/{page_no}/image",
            edits=edits,
        )

    @app.get("/api/v1/books/{book_id}/pages/{page_no}/image")
    def get_page_image(book_id: SafeBookId, page_no: int, session: SessionDep) -> FileResponse:
        """原页高清图（对照左栏）。"""
        artifact_dir, _ = _read_scope(session, book_id)
        image_path = artifact_dir / "pages" / f"p{page_no:04d}.png"
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail=f"页图缺失：第 {page_no} 页")
        return FileResponse(image_path, media_type="image/png")

    @app.put(
        "/api/v1/books/{book_id}/pages/{page_no}/blocks/{index}/edit",
        response_model=EditRecord,
    )
    def edit_block(
        book_id: SafeBookId, page_no: int, index: int, payload: EditRequest, session: SessionDep
    ) -> EditRecord:
        """块编辑 upsert（落 af_block_edits，绑定**生效版本**的 `job_id`，同版本同块覆盖更新）。

        块下标只在解析版本内有意义（映射表/归一化规则一变语义就变），故编辑必须记在它核对
        的那个版本上；换版本后旧编辑不自动套用。
        """
        artifact_dir, job_id = _read_scope(session, book_id)
        page = _load_page(artifact_dir, page_no)
        if not 0 <= index < len(page.blocks):
            raise HTTPException(status_code=400, detail=f"块下标越界：{index}")
        block: Block = page.blocks[index]
        edit = EditRepository(session).upsert(
            book_id,
            job_id,
            page_no,
            index,
            block_type=block.type.value,
            verdict=payload.verdict.value,
            note=payload.note,
            corrected_text=payload.corrected_text,
            corrected_bbox=list(payload.corrected_bbox) if payload.corrected_bbox else None,
        )
        return _edit_record(edit)

    @app.get("/api/v1/books/{book_id}/pages/{page_no}/edits", response_model=list[EditRecord])
    def list_edits(book_id: SafeBookId, page_no: int, session: SessionDep) -> list[EditRecord]:
        """页内编辑列表（对照视图回显，作用域 = 生效版本）。"""
        _, job_id = _read_scope(session, book_id)
        return [
            _edit_record(edit)
            for edit in EditRepository(session).list_for_page(book_id, job_id, page_no)
        ]

    @app.get("/api/v1/books/{book_id}/versions", response_model=list[ParseVersionOut])
    def list_versions(book_id: SafeBookId, session: SessionDep) -> list[ParseVersionOut]:
        """解析版本列举（只读，不改生效指针）：按任务创建时间给出每版自证信息。"""
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        pages = PageRepository(session)
        return [
            ParseVersionOut(
                job_id=job.job_id,
                engine=job.engine,
                status=job.status,
                page_count=job.page_count,
                parsed=pages.count_parsed(book_id, job.job_id),
                source_sha256=job.source_sha256,
                created_at=job.created_at,
                active=job.job_id == book.active_job_id,
            )
            for job in ParseJobRepository(session).list_for_book(book_id)
        ]

    @app.get("/api/v1/books/{book_id}/chunks", response_model=ChunkSet)
    def get_chunks(
        book_id: SafeBookId,
        session: SessionDep,
        max_chars: Annotated[int | None, Query(ge=1)] = None,
        overlap_blocks: Annotated[int | None, Query(ge=0)] = None,
    ) -> ChunkSet:
        """分块预览：读生效版本的 chunks.json；带参数时基于该版本页产物实时重算（不落盘）。"""
        artifact_dir, _ = _read_scope(session, book_id)
        if max_chars is not None or overlap_blocks is not None:
            pages = _load_pages(artifact_dir)
            if not pages:
                raise HTTPException(status_code=404, detail=f"分块未生成：{book_id}")
            params = ChunkParams(
                max_chars=max_chars if max_chars is not None else ChunkParams().max_chars,
                overlap_blocks=overlap_blocks if overlap_blocks is not None else ChunkParams().overlap_blocks,
            )
            return build_chunks(pages, book_id=book_id, params=params)
        chunk_path = artifact_dir / "chunks.json"
        if chunk_path.is_file():
            return read_chunk_set(chunk_path)
        chunk_set = rebuild_chunks(artifact_dir, book_id)
        if not chunk_set.chunks and not _load_pages(artifact_dir):
            raise HTTPException(status_code=404, detail=f"分块未生成：{book_id}")
        return chunk_set

    @app.post("/api/v1/parse-jobs", response_model=ParseJob, status_code=status.HTTP_202_ACCEPTED)
    def create_parse_job(payload: ParseJobCreate, session: SessionDep) -> ParseJob:
        """提交解析任务：pages 缺省 = 全本解析；指定页 = 逐页重解析。202 受理后后台执行。

        受理前两道门槛（条文 5/6）：引擎名解析（未实现 400、未知 422，不回落 MinerU）与
        模型服务探活（不可用 503）；两者都不创建任务行。受理后的服务故障才记为 job `failed`。
        """
        book = BookRepository(session).get(payload.book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{payload.book_id}")
        book_dir = book_dir_for(data_root, book.domain_id, book.course_id, payload.book_id)
        if not (book_dir / "book.json").is_file():
            raise HTTPException(status_code=404, detail=f"尚未 ingest：{payload.book_id}")
        try:
            engine_name = resolve_engine_name(payload.engine, settings.ocr_engine)
        except EngineSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _probe_model_service(make_engine(engine_name))
        total = len(payload.pages) if payload.pages else (book.page_count or 0)
        job_id = uuid.uuid4().hex[:12]
        job = ParseJobRepository(session).create(
            ParseJobRow(
                job_id=job_id,
                book_id=payload.book_id,
                pages=payload.pages,
                engine=engine_name,
                status=JobStatus.QUEUED,
                progress={"parsed": 0, "total": total},
            )
        )
        session.commit()  # 先落库，后台线程才能读到任务行
        runner.submit(job_id=job_id, book_id=payload.book_id, pages=payload.pages, engine=engine_name)
        return ParseJob(
            id=job.job_id,
            book_id=job.book_id,
            pages=job.pages,
            engine=job.engine,
            status=job.status,
            progress=job.progress,
        )

    @app.get("/api/v1/parse-jobs/{job_id}", response_model=ParseJob)
    def get_parse_job(job_id: str, session: SessionDep) -> ParseJob:
        """任务状态与进度。"""
        job = ParseJobRepository(session).get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
        return ParseJob(
            id=job.job_id,
            book_id=job.book_id,
            pages=job.pages,
            engine=job.engine,
            status=job.status,
            progress=job.progress,
            error=job.error,
        )

    return app


app = create_app()
