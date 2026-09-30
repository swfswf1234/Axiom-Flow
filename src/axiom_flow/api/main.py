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
from axiom_flow.api.admin import mount_admin
from axiom_flow.config import Settings, load_settings
from axiom_flow.db.engine import create_engine_for
from axiom_flow.db.models import AfBlockEdit, AfBook, AfPageRegion
from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
    BookRepository,
    BookSyncRow,
    EditRepository,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
    RegionIntent,
    RegionRepository,
)
from axiom_flow.engines.base import ParseEngine
from axiom_flow.engines.factory import EngineSelectionError, build_engine, resolve_engine_name
from axiom_flow.ingest import book_dir_for, ingest_book, version_dir_for, warn_transient_residue, write_manifest
from axiom_flow.normalize.chunking import build_chunks, read_chunk_set
from axiom_flow.orchestrator import MODEL_UNAVAILABLE, JobRunner, rebuild_chunks
from axiom_flow.schemas import (
    SAFE_SEGMENT_PATTERN,
    BBox,
    Block,
    BlockLocator,
    BlocksPage,
    BookOut,
    BookSyncItem,
    BookUpdate,
    ChunkParams,
    ChunkSet,
    EditRecord,
    EditRequest,
    IngestResponse,
    IngestStatus,
    JobStatus,
    ManifestEntry,
    PageData,
    PageListOut,
    PageState,
    PageStateOut,
    ParseJob,
    ParseJobCreate,
    ParseJobListItem,
    ParseJobListOut,
    ParseStatus,
    ParseVersionOut,
    RegionRecord,
    StructureRequest,
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
        last_parse_result=book.last_parse_result,
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


def _checked_bbox(bbox: BBox, label: str) -> list[int]:
    """几何形状合法性（非负且右下严格大于左上）：非法框无法裁剪，落库前先拒。

    页像素上界不在此校验——`adds` 可以在产物之前声明（画框先于解析），那时没有页尺寸可比；
    越界框由解析侧按图裁剪自然收敛，不在此处造第二套「尺寸事实源」。
    """
    x0, y0, x1, y1 = bbox
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise HTTPException(
            status_code=400, detail=f"{label} 形状非法：[{x0}, {y0}, {x1}, {y1}]（需非负且 x1>x0、y1>y0）"
        )
    return [x0, y0, x1, y1]


def _region_record(row: AfPageRegion) -> RegionRecord:
    """`af_page_regions` 行 → 响应记录（几何以数组回给根侧，与 `Block.bbox` 同一形状）。"""
    return RegionRecord(
        region_id=row.region_id,
        op=row.op,
        bbox=tuple(row.bbox) if row.bbox else None,
        anchor_bbox=tuple(row.anchor_bbox) if row.anchor_bbox else None,
        members=[tuple(member) for member in row.members] if row.members else None,
        kind=row.kind,
        note=row.note,
        created_at=row.created_at,
        updated_at=row.updated_at,
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
        window_size=settings.parse_window_size,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 受理任何请求之前先归一遗留活动态，避免读取端看到假 running/queued（ADR 0007 决定 4）
        runner.reconcile_on_startup()
        # 崩溃残留只计数告警不清理（存储规范对齐 W5 / Q3 裁决；重清归版本就绪判据）
        warn_transient_residue(data_root, context="启动期瞬态残留核对")
        yield
        runner.shutdown()
        if db_engine is not None:
            db_engine.dispose()

    app = FastAPI(title="Axiom-Flow", version=VERSION, lifespan=lifespan)
    mount_admin(app, settings=settings, session_factory=session_factory)

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
        """产物目录解析唯一入口（REQ-097）：只认 af_books 行，路径按行内段名构造。

        文件系统回落随 PLAN-017 W1 删除：磁盘产物不再算书（它证明不了页数与哈希已回写库）；
        两层/三层的深度无关由 `book_dir_for` 的空段折叠天然承载。目录不存在返回 None，
        读取端按「产物缺失」404。
        """
        book = BookRepository(session).get(book_id)
        if book is None:
            return None
        candidate = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
        return candidate if candidate.is_dir() else None

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

    @app.get("/api/v1/health")
    def health() -> dict:
        """服务健康探针：8900 控制中心与生命周期脚本（--wait/status）的就绪判据。"""
        return {"status": "ok"}

    # ---------- ② 数据查询（af_*） ----------

    @app.get("/api/v1/books", response_model=list[BookOut])
    def list_books(session: SessionDep) -> list[BookOut]:
        """书目列表：只读 af_books（REQ-097：文件系统不再是书目来源）。"""
        rows = BookRepository(session).list_books()
        return [_book_out(row) for row in rows]

    @app.get("/api/v1/books/{book_id}", response_model=BookOut)
    def get_book(book_id: SafeBookId, session: SessionDep) -> BookOut:
        """单书目元数据（af_books 行；不在库即 404，不查文件系统）。"""
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        return _book_out(book)

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
        """删除 af_books 行与该书**所有解析版本**的 af_pages / af_block_edits 行，以及该书全部
        人工结构意图（af_page_regions）；`?purge=true` 同时删除整本产物目录（含 `versions/` 历史版本）。

        af_parse_jobs 历史行保留为审计线索（不随书目删除；`architecture/database-design.md`「删除保留
        审计」，其管理面 purge 例外见 `PM-17`）。人工框**不保留**：它是这本书某一页的解析输入，书行没了
        就是无主行，留着只会在重建同 id 书时凭空复活旧框。
        """
        repo = BookRepository(session)
        book = repo.get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        book_dir = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
        PageRepository(session).delete_for_book(book_id)
        EditRepository(session).delete_for_book(book_id)
        RegionRepository(session).delete_for_book(book_id)
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
        """渲染页图（不调模型）并把页数/哈希/时刻回写 af_books（REQ-097：唯一事实源）。

        根视图只替换 `pages/p*.png`——`book.json`/`manifest.json` 不再落盘（产物正本只在
        `versions/`）；历史解析版本目录完整保留，生效指针回到 `NULL`（根视图），`af_pages`
        只清 legacy 行、版本作用域行不动（`PM-16` + `PP-14`）。源文件变了之后旧版本仍可从
        `versions/` 直读，但不再对生效端点可见，需重新解析。
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

    def _page_total(session: Session, book_id: str) -> int:
        """页清单的枚举域：**只取 `af_books.page_count`**（REQ-097 删除 `book.json` 回落）。

        NULL 即 404——「枚举域未知」不能伪装成空清单，否则客户端会判定全书齐备；磁盘上
        残留的旧布局 book.json 不再是第二事实源。
        """
        book = BookRepository(session).get(book_id)
        if book is not None and book.page_count:
            return int(book.page_count)
        raise HTTPException(status_code=404, detail=f"书目不存在或产物缺失：{book_id}")

    @app.get("/api/v1/books/{book_id}/pages", response_model=PageListOut)
    def list_page_states(book_id: SafeBookId, session: SessionDep) -> PageListOut:
        """逐页状态清单（只读，**不分页**）：一次给全集，取代 1..page_count 的逐页试错。

        `job_id`/`scope`/`has_artifact` 全部取自 `_read_scope` 的同一作用域；没有页行的页出行
        `pending`。`status="parsed"` 且 `has_artifact=false` **如实暴露不改判**：该组合只可能来自
        产物被外部删除，静默改判会让磁盘事故在台账上消失。
        """
        artifact_dir, job_id = _read_scope(session, book_id)
        total = _page_total(session, book_id)
        states = {state.page_no: state for state in PageRepository(session).list_states(book_id, job_id)}
        edit_counts = EditRepository(session).count_by_page(book_id, job_id)
        pages = [
            PageStateOut(
                page_no=page_no,
                status=states[page_no].status if page_no in states else PageState.PENDING,
                has_artifact=(artifact_dir / "pages" / f"p{page_no:04d}.blocks.json").is_file(),
                error=states[page_no].error if page_no in states else "",
                edit_count=edit_counts.get(page_no, 0),
            )
            for page_no in range(1, total + 1)
        ]
        return PageListOut(
            book_id=book_id,
            job_id=job_id,
            scope="legacy" if job_id == LEGACY_JOB_ID else "active",
            total=total,
            pages=pages,
        )

    @app.get("/api/v1/books/{book_id}/manifest", response_model=list[ManifestEntry])
    def get_manifest(book_id: SafeBookId, session: SessionDep) -> list[ManifestEntry]:
        """生效版本的产物清单：文件路径 + 大小 + SHA-256（防篡改/增量），路径相对版本目录。

        manifest 是**版本**的自证（REQ-097）：legacy 根视图没有清单可给 → 404。端点只重算
        并写回版本目录内的 `manifest.json`，不再往根视图制造产物文件。
        """
        artifact_dir, job_id = _read_scope(session, book_id)
        if job_id == LEGACY_JOB_ID:
            raise HTTPException(status_code=404, detail=f"尚无生效版本：{book_id}")
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

    @app.put(
        "/api/v1/books/{book_id}/pages/{page_no}/structure",
        response_model=list[RegionRecord],
    )
    def put_structure(
        book_id: SafeBookId, page_no: int, payload: StructureRequest, session: SessionDep
    ) -> list[RegionRecord]:
        """页结构意图**全量声明**（落 `af_page_regions`，页级、不绑生效版本，ADR 0010 决定 1）。

        缺席即撤销：本次未列出的人工意图被删除，所以重复提交同一组意图结果不变（幂等，无需
        undo 端点）。定位三选一在受理时统一换算成**几何锚**再落库——块下标只在生效版本内有
        意义，换版后重锚定用的就是这份快照（Q4 定判：下标↔`region_id` 的翻译归本仓库）。
        本端点不调模型服务：画框先于解析，模型不可用时也必须能声明意图。
        """
        if page_no < 1:
            raise HTTPException(status_code=404, detail=f"页不存在：第 {page_no} 页（页码 1 基）")
        artifact_dir, job_id = _read_scope(session, book_id)
        if job_id == LEGACY_JOB_ID:
            raise HTTPException(
                status_code=404,
                detail=f"无生效解析版本，拒绝结构操作：{book_id}（legacy 根视图无从锚定块，先激活版本）",
            )
        page_count = BookRepository(session).get(book_id).page_count
        if page_count is not None and page_no > page_count:
            raise HTTPException(
                status_code=404,
                detail=f"页不存在：{book_id} 共 {page_count} 页，请求第 {page_no} 页",
            )
        total = len(payload.adds) + len(payload.deletes) + len(payload.merges)
        if total > settings.max_regions_per_page:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"第 {page_no} 页结构意图 {total} 条，超出每页上限 "
                    f"{settings.max_regions_per_page}（每框一次单页提交，成本必须可见）"
                ),
            )
        merge_targets = [locator for group in payload.merges for locator in group.targets]
        for group in payload.merges:
            if len(group.targets) < 2:
                raise HTTPException(
                    status_code=400, detail=f"合并至少需要 2 个成员块，实际 {len(group.targets)}"
                )
        blocks = (
            _load_page(artifact_dir, page_no).blocks
            if any(loc.block_index is not None for loc in [*payload.deletes, *merge_targets])
            else []
        )
        existing = {
            row.region_id: row
            for row in RegionRepository(session).list_for_page(book_id, page_no)
        }

        def anchor_of(locator: BlockLocator) -> list[int]:
            """三选一定位 → 几何锚；给多或给少都判 400（猜一个等于替人做结构决定）。"""
            given = [
                locator.block_index is not None,
                bool(locator.region_id),
                locator.bbox is not None,
            ]
            if sum(given) != 1:
                raise HTTPException(
                    status_code=400, detail="定位必须恰好给出 block_index / region_id / bbox 之一"
                )
            if locator.block_index is not None:
                if not 0 <= locator.block_index < len(blocks):
                    raise HTTPException(status_code=400, detail=f"块下标越界：{locator.block_index}")
                return list(blocks[locator.block_index].bbox)
            if locator.region_id:
                row = existing.get(locator.region_id)
                if row is None:
                    raise HTTPException(
                        status_code=400, detail=f"region_id 不存在于本页：{locator.region_id}"
                    )
                geometry = row.anchor_bbox or row.bbox or (row.members or [[]])[0]
                return list(geometry)
            assert locator.bbox is not None
            return _checked_bbox(locator.bbox, "定位框")

        intents: list[RegionIntent] = [
            RegionIntent(op="add", bbox=_checked_bbox(bbox, "人工框")) for bbox in payload.adds
        ]
        intents += [
            RegionIntent(op="delete", anchor_bbox=anchor_of(locator))
            for locator in payload.deletes
        ]
        intents += [
            RegionIntent(op="merge", members=[anchor_of(locator) for locator in group.targets])
            for group in payload.merges
        ]
        rows = RegionRepository(session).replace_page(book_id, page_no, intents)
        return [_region_record(row) for row in rows]

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

        受理前三道门槛（`api.md` 错误码表 + `MI-2` + DEFECT-006）：页码越界 422（不探活、不落任务行，否则会在
        `af_pages` 写出指向不存在页图的状态行）、引擎名解析（未实现 400、未知 422，不回落
        MinerU）、模型服务探活（不可用 503）；三者都不创建任务行。受理后的服务故障才记为 job `failed`。

        「已 ingest」的门槛只看 af_books（`ingest_status`，REQ-097）：根视图 book.json 已退役，
        磁盘文件证明不了页数与哈希已回写库。
        """
        book = BookRepository(session).get(payload.book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{payload.book_id}")
        if book.ingest_status != IngestStatus.INGESTED:
            raise HTTPException(status_code=404, detail=f"尚未 ingest：{payload.book_id}")
        out_of_range = [p for p in payload.pages or () if p < 1 or p > (book.page_count or 0)]
        if out_of_range:
            raise HTTPException(
                status_code=422,
                detail=f"页码越界：{payload.book_id} 共 {book.page_count} 页，请求 {out_of_range}",
            )
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
                progress={"parsed": 0, "total": total, "failed": 0},
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
            failed_pages=list(job.failed_pages or []),
        )

    @app.get("/api/v1/parse-jobs", response_model=ParseJobListOut)
    def list_parse_jobs(
        session: SessionDep,
        book_id: Annotated[str | None, Query(pattern=SAFE_SEGMENT_PATTERN)] = None,
        status_filter: Annotated[list[JobStatus] | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> ParseJobListOut:
        """解析作业列表（只读）：过滤组合 + 稳定倒序 + 过滤后总数。

        本端点**不经** `_read_scope` 也不摸磁盘：作业事实全在 `af_parse_jobs`，跨书列表没有单一
        per-book 产物作用域，硬套会造出第二套作用域语义（`api.md` 强制规则的同一条）。
        已删书的审计行不排除、其 `active` 恒为 `False`。
        """
        jobs, total = ParseJobRepository(session).list_all(
            status=[job_status.value for job_status in status_filter] if status_filter else None,
            book_id=book_id,
            limit=limit,
            offset=offset,
        )
        active_by_book = BookRepository(session).active_job_index()
        return ParseJobListOut(
            jobs=[
                ParseJobListItem(
                    id=job.job_id,
                    book_id=job.book_id,
                    pages=job.pages,
                    engine=job.engine,
                    status=job.status,
                    progress=job.progress,
                    failed_pages=list(job.failed_pages or []),
                    error=job.error,
                    created_at=job.created_at,
                    started_at=job.started_at,
                    finished_at=job.finished_at,
                    active=active_by_book.get(job.book_id) == job.job_id,
                )
                for job in jobs
            ],
            total=total,
            limit=limit,
            offset=offset,
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
            failed_pages=list(job.failed_pages or []),
            error=job.error,
        )

    return app


app = create_app()
