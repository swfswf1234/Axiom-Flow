"""8902 API v1 路由：对外协议适配，返回结构与 schemas 一致（契约 docs/architecture/api.md）。

实现状态：Current（af_* 持久化 + 异步 parse-jobs + 分块预览；对照编辑为覆盖层合并）。
只读端点不依赖模型服务与凭据；仅解析任务经引擎适配器直连模型服务（ADR 0004），且提交期
探活（不可用 503，不落任务行）、引擎名按 `AXIOM_OCR_ENGINE`/请求体显式分派（无自动兜底）。
读取面按**生效解析版本**解析目录（`af_books.active_job_id`，PLAN-004 / REQ-080）：指针唯一入口
是 `_read_scope`，URL、响应结构与 `image_url` 字面量对根仓库前端保持不变。
"""

import shutil
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow import __version__ as VERSION
from axiom_flow.api.admin import mount_admin
from axiom_flow.config import Settings, load_settings
from axiom_flow.db.engine import create_engine_for
from axiom_flow.db.llm_ledger import LlmCallIdentity, LlmCallLedger
from axiom_flow.db.models import AfBook, AfKp, AfOperation
from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
    BookRepository,
    BookSyncRow,
    EditRow,
    KpOutlineDecl,
    KpRepository,
    OperationRepository,
    PageRepository,
    ParseJobRow,
    RegionIntent,
    RegionRow,
    job_timing,
    public_progress,
)
from axiom_flow.engines.base import KnowledgeEngine, ParseEngine
from axiom_flow.engines.factory import (
    EngineSelectionError,
    TextSlotError,
    build_engine,
    build_text_engine,
    resolve_engine_name,
)
from axiom_flow.ingest import book_dir_for, ingest_book, version_dir_for, warn_transient_residue, write_manifest
from axiom_flow.normalize.chunking import build_chunks, read_chunk_set
from axiom_flow.normalize.furniture import furniture_blocks, furniture_hints
from axiom_flow.normalize.intents import apply_relabels, relabels_by_block
from axiom_flow.normalize.toc import TOC_ROLE_KNOWLEDGE, node_type_of, node_types_of
from axiom_flow.orchestrator import MODEL_UNAVAILABLE, JobRunner, rebuild_chunks
from axiom_flow.orchestrator.jobs import KpLedgerWiringError
from axiom_flow.orchestrator.knowledge import (
    KpGateError,
    SpanRow,
    accept_kp_job,
    anchor_review_flags,
    order_for_export,
    read_narrative,
    regenerate_section_summary,
    section_spans_of,
    unconfirmed_anchor_flags,
    validate_endorsement_links,
    write_narrative,
)
from axiom_flow.schemas import (
    SAFE_SEGMENT_PATTERN,
    AnchorReviewOut,
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
    KpConfirmIn,
    KpConfirmOut,
    KpExportOut,
    KpJobAccepted,
    KpJobCreate,
    KpJobProgressOut,
    KpLedgerOut,
    KpNarrativeOut,
    KpNarrativeWriteIn,
    KpNodeOut,
    KpNodePatch,
    KpOutlineDeclareIn,
    KpOutlineDeclareOut,
    KpSummaryRegenIn,
    KpWorkspaceOut,
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


def _edit_record(edit: EditRow) -> EditRecord:
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
        corrected_type=edit.corrected_type,
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


def _region_record(row: RegionRow) -> RegionRecord:
    """structure 意图行投影 → 响应记录（几何以数组回给根侧，与 `Block.bbox` 同一形状）。"""
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


def _anchor_pages(node: AfKp) -> list[int]:
    """一行锚点里的页号（升序、去零）：`anchors` 是 JSON 列，脏形状按「没有这一页」处理。"""
    return sorted(
        page
        for page in (
            int(anchor.get("page") or 0) for anchor in (node.anchors or []) if isinstance(anchor, dict)
        )
        if page > 0
    )


def _kp_page_spans(rows: Sequence[AfKp], last_page: int | None) -> dict[str, tuple[int, int]]:
    """在册目录行 → 每行的正文区间（`KA-42`）：算式与点腿取材同一处，读面不自推。

    参与切分的只有 `gen_status='candidate'` 的目录行（`draft` 是在飞读数、`rejected` 是被本批取代的
    旧读数，拿它们切区间会把中间那一段凭空挖掉）。`af_books.page_count` 缺时折回本行起点页——两键
    退化是披露，不是整面报错的理由。
    """
    items: list[SpanRow] = []
    for row in rows:
        if row.node_kind != "outline" or row.gen_status != "candidate":
            continue
        pages = _anchor_pages(row)
        if not pages:
            continue
        items.append(
            SpanRow(
                node_id=row.node_id,
                name=row.name or "",
                order_hint=row.order_hint,
                page=pages[0],
                depth=row.depth or 1,
                toc_role=str((row.meta or {}).get("toc_role") or TOC_ROLE_KNOWLEDGE),
            )
        )
    if not items:
        return {}
    if last_page is None:
        return {str(item.node_id): (item.page, item.page) for item in items if item.node_id}
    first_page = min(item.page for item in items)
    return {
        str(span.parent_id): (span.page_start, span.page_end)
        for span in section_spans_of(items, first_page=first_page, last_page=int(last_page))
        if span.parent_id
    }


def _page_range_of(node: AfKp, spans: Mapping[str, tuple[int, int]] | None) -> tuple[int | None, int | None]:
    """一行的两枚派生键：目录行读切分结果，点行读自己锚点的两端（屏侧只留一枚消费口）。"""
    pages = _anchor_pages(node)
    if node.node_kind == "outline":
        return (spans or {}).get(node.node_id, (None, None))
    return (pages[0], pages[-1]) if pages else (None, None)


def _kp_node_out(
    node: AfKp,
    spans: Mapping[str, tuple[int, int]] | None = None,
    node_types: Mapping[str, str] | None = None,
) -> KpNodeOut:
    """`af_kp` 一行的响应投影（工作区／背书回显／导出面同一份）。

    不发 `norm_key` 与整槽 `meta`：前者是去重用的派生值（冲突由 422 的点名承载），后者是禁进 WHERE
    的扩展槽（`database-design.md` 的 `meta` 列契约）。两枚身份位 `toc_role`／`toc_kind` 是例外，
    从槽里逐字取出带出（`KA-39`）——屏上要标「非知识点」，靠的就是它们。
    `page_start`／`page_end` 同样是读时派生（`KA-42`）：调用方手里有整册行时传 `spans`，没有则目录行
    两键为空——单行回显（背书腿）不为了两枚派生键去多读一整册。
    `node_type` 是第三枚派生键（`KA-44`）：四值类型要先看祖先的 `toc_kind` 才判得出习题的子行，
    所以同样由调用方按整册行算好递进来；缺省时按本行自己的身份位判（不猜祖先）。
    """
    meta = node.meta or {}
    page_start, page_end = _page_range_of(node, spans)
    toc_role = str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE)
    toc_kind = str(meta.get("toc_kind") or "")
    node_type = (node_types or {}).get(node.node_id) or node_type_of(
        depth=node.depth, toc_role=toc_role, toc_kind=toc_kind
    )
    return KpNodeOut(
        node_id=node.node_id,
        book_id=node.book_id,
        job_id=node.job_id,
        node_kind=node.node_kind,
        node_type=node_type,
        parent_id=node.parent_id,
        depth=node.depth,
        order_hint=node.order_hint,
        name=node.name,
        role=node.role or "",
        type=node.type or "",
        summary=node.summary or "",
        links=list(node.links or []),
        anchors=list(node.anchors or []),
        page_start=page_start,
        page_end=page_end,
        narrative_ref=node.narrative_ref or "",
        gen_status=node.gen_status,
        gen_batch=node.gen_batch or "",
        model_ref=node.model_ref or "",
        toc_role=toc_role,
        toc_kind=toc_kind,
        confirmed=node.confirmed,
        rev=node.rev,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _kp_node_echo(node: AfKp, session: Session) -> KpNodeOut:
    """单行回显（背书腿）也带派生两键：屏上已读到的区间不该被一次人工写入抹成 `null`（`KA-42`）。

    代价是每次 PATCH 多读一整册行与一行书目——同一会话内两处都已取过或极小，换来的是「屏侧只有一枚
    消费口」这条不被写腿打破。
    """
    book = BookRepository(session).get(node.book_id)
    rows = KpRepository(session).list_for_book(node.book_id)
    return _kp_node_out(
        node,
        _kp_page_spans(rows, book.page_count if book is not None else None),
        node_types_of(rows),
    )


def _kp_narrative_out(node: AfKp, *, pointer: str, content: str, exists: bool) -> KpNarrativeOut:
    """长文两条腿共用的响应装配（`KA-30`）：`bytes` 按 UTF-8 计，与 `content` 的字符数是两个口径。

    `pointer` 由调用方给：文件在位时回**派生名**（走端点通路写的行与列必然同值），缺文件时回**列原值**
    ——「列有值而文件不在位」这个矛盾正是披露的对象，读腿不替它圆场。
    """
    return KpNarrativeOut(
        node_id=node.node_id,
        narrative_ref=pointer,
        exists=exists,
        content=content,
        bytes=len(content.encode("utf-8")),
        rev=node.rev,
        updated_at=node.updated_at,
    )


def _kp_ledger_out(row: AfOperation) -> KpLedgerOut:
    """批次流水行的投影（`KA-27`）：页区间从 `payload` 取，批次身份从 `target_id` 取。"""
    payload = row.payload or {}
    return KpLedgerOut(
        gen_batch=str(payload.get("gen_batch") or row.target_id or ""),
        book_id=row.book_id,
        job_id=row.job_id or "",
        status=row.status,  # type: ignore[arg-type]
        page_start=payload.get("page_start"),
        page_end=payload.get("page_end"),
        review_note=payload.get("review_note") or "",
        stage=payload.get("stage") or "full",
        result=row.result,
        error=row.error or "",
        created_at=row.created_at,
    )


def _probe_model_service(engine: ParseEngine | KnowledgeEngine) -> None:
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
    knowledge_engine_factory: Callable[[str], KnowledgeEngine] | None = None,
    kp_call_identity: LlmCallIdentity | None = None,
    background_jobs: bool = True,
) -> FastAPI:
    """应用工厂：数据根/数据库/引擎在创建时装配；测试可注入 sqlite 会话与假引擎。

    `engine_factory(name)` 以**已解析的引擎名**构造适配器（`engines/factory.build_engine`），
    名字解析发生在提交期：未实现或未知引擎直接 400/422，不回落 MinerU（ADR 0004）。
    `knowledge_engine_factory(name)` 同理服务梳理通路（`KA-22`）：**缺省即登记好的那一个槽位工厂**
    （`engines/factory.build_text_engine`），注入只留给测试与「另接一台文本引擎」的场合。槽位本身
    没接线（名为空或闭集外）时提交端点判 503，不回落成任何具体文本引擎。
    `kp_call_identity` 是 `qed_llm_calls` 三枚 `NOT NULL` 身份列的取值（`KA-23`）：只有适配器知道
    自己连的是哪家，所以由接线的人给。调用账本身由本工厂用同一个 `session_factory` 建，不需要外部注入。
    经 8900 网关的适配器自报 `accounts_externally`（R8 甲）：账由 8900 落，本仓既不需要身份、
    也不再包一层自写账。
    """
    settings = settings or load_settings()
    data_root = Path(settings.data_dir)

    db_engine = None
    if session_factory is None:
        db_engine = create_engine_for(settings)
        session_factory = sessionmaker(bind=db_engine, future=True)
    make_engine = engine_factory or (lambda name: build_engine(name, settings))
    make_knowledge_engine = knowledge_engine_factory or (lambda name: build_text_engine(name, settings))
    runner = JobRunner(
        session_factory=session_factory,
        engine_factory=make_engine,
        data_root=data_root,
        background=background_jobs,
        window_size=settings.parse_window_size,
        knowledge_engine_factory=make_knowledge_engine,
        kp_call_ledger=LlmCallLedger(session_factory),
        kp_call_identity=kp_call_identity,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # 受理任何请求之前先归一遗留活动态，避免读取端看到假 running/queued（ADR 0007 决定 4）
        runner.reconcile_on_startup()
        # 梳理批次同一条通路：残留 `draft` 会让 `KA-4` 的在飞判据永久为真，启动期整批判 `rejected`（`KA-9`）
        with session_factory() as kp_session:
            KpRepository(kp_session).reconcile_draft_on_startup()
            kp_session.commit()
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

    #: 家具标记的进程内缓存（AF-T6 乙半边／REQ-143 W20）。判一条重复线的分母是**整本页集**，本仓无
    #: 跨请求读缓存，逐请求即逐请求整本读盘（现网最大 439 页）。失效条件只有两条，与写入面一一对应：
    #: ① 换版＝换键——`job_id` 进键，旧版本目录随激活删除，旧键不会再被读到；② 同版本内唯一能改标记
    #: 的写是块编辑的 `corrected_type`（线维吃产物文本、结构意图不动块序列），编辑端点写后作废本书本版键。
    furniture_marks: dict[tuple[str, str], dict[int, tuple[int, ...]]] = {}

    def _furniture_marks_for(
        session: Session, book_id: str, job_id: str, artifact_dir: Path
    ) -> dict[int, tuple[int, ...]]:
        """整本生效版本的家具块标记（类型维∪线维），按 `(book_id, job_id)` 缓存。

        常量级读路径上没有「产物在版本内被改写」这条通路（parse job 只写自己的版本目录），所以缓存
        只需守住上面两条失效条件；整本读盘失败按缺页跳过（与三个读端点同一容错）——标记少一页好过
        整页读不出来。
        """
        cached = furniture_marks.get((book_id, job_id))
        if cached is not None:
            return cached
        edits_by_page: dict[int, list[EditRow]] = defaultdict(list)
        for edit in OperationRepository(session).list_edits_for_book(book_id, job_id):
            edits_by_page[edit.page_no].append(edit)
        replayed = [
            page.model_copy(
                update={
                    "blocks": apply_relabels(
                        page.blocks, relabels_by_block(edits_by_page.get(page.page, []))
                    )
                }
            )
            for page in _load_pages(artifact_dir)
        ]
        marks = furniture_blocks(replayed, hints=furniture_hints(replayed))
        furniture_marks[(book_id, job_id)] = marks
        return marks

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
        """删除 af_books 行与该书**所有解析版本**的 af_pages 行，以及该书全部人工意图行
        （`af_operations` 的 edit 与 structure 两类）；`?purge=true` 同时删除整本产物目录（含
        `versions/` 历史版本）。

        parse 事件行保留为审计线索（不随书目删除；`architecture/database-design.md`「删除保留
        审计」，其管理面 purge 例外见 `PM-17`）。人工框**不保留**：它是这本书某一页的解析输入，书行没了
        就是无主行，留着只会在重建同 id 书时凭空复活旧框。
        """
        repo = BookRepository(session)
        book = repo.get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        book_dir = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
        PageRepository(session).delete_for_book(book_id)
        OperationRepository(session).delete_intents_for_book(book_id)
        repo.delete(book_id)
        for key in [key for key in furniture_marks if key[0] == book_id]:
            furniture_marks.pop(key, None)
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
        edit_counts = OperationRepository(session).count_edits_by_page(book_id, job_id)
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
        """单页完整数据：结构化 blocks + 页级 markdown + 原页图 URL + 已合并人工编辑。

        块序列读的是**改判后**的类型（回放见 `normalize/intents.py`）：`blocks[i].type` 被同版本
        同块的 `corrected_type` 覆盖，其余字段与产物逐字一致；`edits[]` 仍同时给出 `block_type`
        原类型快照与 `corrected_type`，所以「产物怎么判的」在改判后依然可指认。
        `furniture_blocks` 是同一回放的**家具块下标**（类型维∪整本跨页重复线）：它只活在读投影里，
        产物文件与 `markdown` 一字不改（`KA-19` 的「对照视图按原文判」不破）。
        """
        if page_no < 1:
            raise HTTPException(status_code=400, detail=f"页码非法：{page_no}")
        artifact_dir, job_id = _read_scope(session, book_id)
        blocks = _load_page(artifact_dir, page_no)
        edit_rows = OperationRepository(session).list_edits_for_page(book_id, job_id, page_no)
        blocks = blocks.model_copy(
            update={"blocks": apply_relabels(blocks.blocks, relabels_by_block(edit_rows))}
        )
        markdown_path = artifact_dir / "pages" / f"p{page_no:04d}.md"
        edits = [_edit_record(edit) for edit in edit_rows]
        return PageData(
            blocks=blocks,
            markdown=markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else "",
            image_url=f"/api/v1/books/{book_id}/pages/{page_no}/image",
            edits=edits,
            furniture_blocks=list(
                _furniture_marks_for(session, book_id, job_id, artifact_dir).get(page_no, ())
            ),
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
        """块编辑 upsert（落 `af_operations` 的 edit 意图行，绑定**生效版本**的 `job_id`，同版本同块覆盖更新）。

        块下标只在解析版本内有意义（映射表/归一化规则一变语义就变），故编辑必须记在它核对
        的那个版本上；换版本后旧编辑不自动套用。

        `corrected_type`＝人工改判的类型（闭集外取值与闭集外键都判 422），它**不写回产物文件**，
        只落意图行，由读侧的两处回放（页数据与梳理取材）消费；`block_type` 仍是产物的原类型快照。
        """
        artifact_dir, job_id = _read_scope(session, book_id)
        page = _load_page(artifact_dir, page_no)
        if not 0 <= index < len(page.blocks):
            raise HTTPException(status_code=400, detail=f"块下标越界：{index}")
        block: Block = page.blocks[index]
        edit = OperationRepository(session).upsert_edit(
            book_id,
            job_id,
            page_no,
            index,
            block_type=block.type.value,
            verdict=payload.verdict.value,
            note=payload.note,
            corrected_text=payload.corrected_text,
            corrected_bbox=list(payload.corrected_bbox) if payload.corrected_bbox else None,
            corrected_type=payload.corrected_type.value if payload.corrected_type else None,
        )
        # 改判是唯一能在同版本内改家具标记的写：作废本书本版缓存（线维吃产物文本、结构意图不动序列）
        furniture_marks.pop((book_id, job_id), None)
        return _edit_record(edit)

    @app.put(
        "/api/v1/books/{book_id}/pages/{page_no}/structure",
        response_model=list[RegionRecord],
    )
    def put_structure(
        book_id: SafeBookId, page_no: int, payload: StructureRequest, session: SessionDep
    ) -> list[RegionRecord]:
        """页结构意图**全量声明**（落 `af_operations` 的 structure 意图行，页级、不绑生效版本，ADR 0010 决定 1）。

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
            for row in OperationRepository(session).list_regions_for_page(book_id, page_no)
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
        rows = OperationRepository(session).replace_page_intents(book_id, page_no, intents)
        return [_region_record(row) for row in rows]

    @app.get("/api/v1/books/{book_id}/pages/{page_no}/edits", response_model=list[EditRecord])
    def list_edits(book_id: SafeBookId, page_no: int, session: SessionDep) -> list[EditRecord]:
        """页内编辑列表（对照视图回显，作用域 = 生效版本）。"""
        _, job_id = _read_scope(session, book_id)
        return [
            _edit_record(edit)
            for edit in OperationRepository(session).list_edits_for_page(book_id, job_id, page_no)
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
            for job in OperationRepository(session).list_versions(book_id)
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

        受理前四道门槛（`api.md` 错误码表 + `MI-2` + DEFECT-006）：页码越界 422（不探活、不落任务行，否则会在
        `af_pages` 写出指向不存在页图的状态行）、区域驱动框数越限 422（逐目标页计 `add` 框数对
        `AXIOM_MAX_REGIONS_PER_PAGE`，判在探活前——每框一次单页提交，非法请求不打模型，ADR 0010 决定 4）、
        引擎名解析（未实现 400、未知 422，不回落
        MinerU）、模型服务探活（不可用 503）；四者都不创建任务行。受理后的服务故障才记为 job `failed`。

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
        # 区域驱动受理门：只查目标页、只计 add（delete/merge 不产生模型提交）；全本提交逐页全查。
        target_pages = (
            set(payload.pages) if payload.pages else set(range(1, (book.page_count or 0) + 1))
        )
        add_counts: dict[int, int] = {}
        for row in OperationRepository(session).list_regions_for_book(payload.book_id):
            if row.op == "add":
                add_counts[row.page_no] = add_counts.get(row.page_no, 0) + 1
        over_limit = sorted(p for p in target_pages if add_counts.get(p, 0) > settings.max_regions_per_page)
        if over_limit:
            readings = "；".join(
                f"第 {p} 页人工框 {add_counts[p]} 超每页上限 {settings.max_regions_per_page}"
                for p in over_limit
            )
            raise HTTPException(
                status_code=422,
                detail=f"区域驱动受理拒绝，不创建任务行：{readings}（每框一次单页提交，非法请求不打模型）",
            )
        try:
            engine_name = resolve_engine_name(payload.engine, settings.ocr_engine)
        except EngineSelectionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _probe_model_service(make_engine(engine_name))
        total = len(payload.pages) if payload.pages else (book.page_count or 0)
        job_id = uuid.uuid4().hex[:12]
        job = OperationRepository(session).create(
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
            progress=public_progress(job),
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

        本端点**不经** `_read_scope` 也不摸磁盘：作业事实全在 `af_operations` 的 parse 事件行，跨书列表没有单一
        per-book 产物作用域，硬套会造出第二套作用域语义（`api.md` 强制规则的同一条）。
        已删书的审计行不排除、其 `active` 恒为 `False`。
        """
        jobs, total = OperationRepository(session).list_all(
            status=[job_status.value for job_status in status_filter] if status_filter else None,
            book_id=book_id,
            limit=limit,
            offset=offset,
        )
        active_by_book = BookRepository(session).active_job_index()
        items: list[ParseJobListItem] = []
        for job in jobs:
            started_at, finished_at = job_timing(job)
            items.append(
                ParseJobListItem(
                    id=job.job_id,
                    book_id=job.book_id,
                    pages=job.pages,
                    engine=job.engine,
                    status=job.status,
                    progress=public_progress(job),
                    failed_pages=list(job.failed_pages or []),
                    error=job.error,
                    created_at=job.created_at,
                    started_at=started_at,
                    finished_at=finished_at,
                    active=active_by_book.get(job.book_id) == job.job_id,
                )
            )
        return ParseJobListOut(jobs=items, total=total, limit=limit, offset=offset)

    @app.get("/api/v1/parse-jobs/{job_id}", response_model=ParseJob)
    def get_parse_job(job_id: str, session: SessionDep) -> ParseJob:
        """任务状态与进度。"""
        job = OperationRepository(session).get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"任务不存在：{job_id}")
        return ParseJob(
            id=job.job_id,
            book_id=job.book_id,
            pages=job.pages,
            engine=job.engine,
            status=job.status,
            progress=public_progress(job),
            failed_pages=list(job.failed_pages or []),
            error=job.error,
        )

    # ---------- ④ 知识点梳理（af_kp） ----------

    def _kp_text_engine() -> tuple[str, KnowledgeEngine]:
        """文本引擎槽位（`KA-22`）：名字空或闭集外都是 503，**不回落**成任何具体引擎。

        与 `resolve_engine_name` 的解析面分开：梳理请求体没有 `engine` 字段（`KA-22` 的成本归因
        要有落点），所以这里的名字只可能来自 `AXIOM_KP_ENGINE` 一处。槽位默认值就是内置实名
        （AF-T1 甲），装配即接通、`.env` 零改动——因此 503 只可能来自「把默认值显式清空」这一
        种服务侧误配，文案不再教人「请配置」。闭集本身收在 `engines/factory.py`（`TEXT_ENGINE_NAMES`），
        端点不另写一份名字表——那样等于给「哪个名字算梳理引擎」开出第二处事实源；工厂报的
        `TextSlotError` 在这里只换成 503，不新增 HTTP 码（误配的是服务侧环境，不是调用方传错请求体，
        读成 400 会把责任放错边）。
        """
        name = settings.kp_engine
        if not name:
            raise HTTPException(
                status_code=503,
                detail=(
                    "梳理引擎槽位未接入：槽位名为空（默认值被显式清空；"
                    "不回落、不缺省成任何引擎，零写入）"
                ),
            )
        try:
            return name, make_knowledge_engine(name)
        except TextSlotError as exc:
            raise HTTPException(status_code=503, detail=f"梳理引擎槽位未接入：{exc}") from exc

    @app.post("/api/v1/kp-jobs", response_model=KpJobAccepted, status_code=status.HTTP_202_ACCEPTED)
    def create_kp_job(payload: KpJobCreate, session: SessionDep) -> KpJobAccepted:
        """提交一次梳理批次：受理门全判在写入之前，通过后 202 发批次号、后台执行（`KA-1`/`KA-24`）。

        门槛次序就是契约（`api.md` ④ 的表）：书目 404 → 无生效版本 422 → 页区间 422 → 在册目录 422
        （仅 `stage='content'`，`KA-43`）→ 同书在飞 409 → 槽位 503 → 探活 503。把非法请求排在槽位与
        探活之前，与解析任务「非法请求不打模型」同构；七道全零写入。批次与解析共用单 worker（`PM-13`），
        执行期第一眼还会复查在飞。
        """
        try:
            acceptance = accept_kp_job(
                session,
                book_id=payload.book_id,
                page_start=payload.page_start,
                page_end=payload.page_end,
                review_note=payload.review_note,
                stage=payload.stage,
            )
        except KpGateError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        engine_name, engine = _kp_text_engine()
        _probe_model_service(engine)
        session.commit()  # 释放读事务，执行体才能在同一连接上写库（与 create_parse_job 同一条纪律）
        try:
            runner.submit_knowledge(acceptance=acceptance, engine_name=engine_name)
        except KpLedgerWiringError as exc:
            # 接线不全（有引擎无账／有账无身份）与槽位未接入同判 503：错在服务侧配置，不在请求体
            raise HTTPException(status_code=503, detail=f"{exc}（零写入）") from exc
        return KpJobAccepted(
            book_id=acceptance.book_id,
            gen_batch=acceptance.gen_batch,
            evidence_job_id=acceptance.evidence_job_id,
            page_start=acceptance.page_start,
            page_end=acceptance.page_end,
        )

    @app.get("/api/v1/books/{book_id}/kp-jobs/{gen_batch}", response_model=KpJobProgressOut)
    def get_kp_job(book_id: SafeBookId, gen_batch: str, session: SessionDep) -> KpJobProgressOut:
        """单批次进度（`KpJobProgressOut`）：三态计数读 `af_kp` 本批行，账面读 `op_kind='kp'` 流水行。

        `in_flight` **不看流水行的 `status`**（事件行禁作当前态判据，`KA-4`/`KA-9`）：判据仍是「该书
        存在 `draft` 行**且那一批就是本批**」，与受理门共用同一处 `find_draft_batch`；再加一条进程内
        的在途登记（`KA-40`），它补的是「受理到第一页目录行落库」那一窗——那时行还没落、流水要批末
        才发（`KA-27` 的整批一行），批次明明是活的。登记只在**本批次自己的批次号**上为真，不参与
        受理门，也不落盘：进程重启后只剩库里的 `draft` 行，而那批残留由启动期归一处置（`KA-9`）。
        `ledger` 为 `null` ＝流水还没写出（批次仍在跑），此时 `in_flight` 恒真。
        """
        if BookRepository(session).get(book_id) is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        repo = KpRepository(session)
        rows = repo.list_batch(book_id, gen_batch)
        ledger = OperationRepository(session).find_kp_batch(book_id, gen_batch)
        in_flight = repo.find_draft_batch(book_id) == gen_batch or runner.kp_in_flight(book_id, gen_batch)
        if not rows and ledger is None and not in_flight:
            raise HTTPException(status_code=404, detail=f"梳理批次不存在：{book_id} 的 {gen_batch}")
        counts = {"draft": 0, "candidate": 0, "rejected": 0, "outline": 0, "point": 0}
        for row in rows:
            counts[row.gen_status] = counts.get(row.gen_status, 0) + 1
            counts[row.node_kind] = counts.get(row.node_kind, 0) + 1
        return KpJobProgressOut(
            book_id=book_id,
            gen_batch=gen_batch,
            in_flight=in_flight,
            counts=counts,
            ledger=_kp_ledger_out(ledger) if ledger is not None else None,
        )

    @app.get("/api/v1/books/{book_id}/kp", response_model=KpWorkspaceOut)
    def get_kp_workspace(book_id: SafeBookId, session: SessionDep) -> KpWorkspaceOut:
        """整册工作区（`KA-7`/`KA-18`/`KA-19`）：目录＋点＋未归档池一次给全，**不分页、不走生效指针**。

        本端点**不调** `_read_scope`：知识点是跨版本存活的人工资产，锚点按行自身 `job_id` 那一版解释，
        生效版本产物目录被删也不 404——读不到的落进 `review_flags`，一行都不改写、不删除（`KA-19`）。
        排序与导出面共用 `order_for_export`，两屏看到的是同一个结构。
        """
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        repo = KpRepository(session)
        nodes = order_for_export(repo.list_for_book(book_id))
        flags = [
            *anchor_review_flags(session, data_root=data_root, nodes=nodes),
            *unconfirmed_anchor_flags(session, nodes),
        ]
        spans = _kp_page_spans(nodes, book.page_count)
        types = node_types_of(nodes)
        return KpWorkspaceOut(
            book_id=book_id,
            in_flight_batch=repo.find_draft_batch(book_id),
            nodes=[_kp_node_out(node, spans, types) for node in nodes],
            review_flags=[
                AnchorReviewOut(node_id=flag.node_id, page=flag.page, reason=flag.reason)
                for flag in flags
            ],
        )

    @app.patch("/api/v1/kp/nodes/{node_id}", response_model=KpNodeOut)
    def patch_kp_node(node_id: str, payload: KpNodePatch, session: SessionDep) -> KpNodeOut:
        """人工背书域写入（`KA-11`/`KA-12`）：只有请求体里出现过的键参与写入，缺席与显式 `null` 不同。

        判定序与 confirm 同形：404 → `if_rev` 冲突 409 → 形状 422 → 写入。旧表单的冲突不得被读成
        「行不合形状」。来源三列与确认位不在请求模型里，改不动是形状保证（`extra="forbid"`）。
        `links` 的四条服务端不变式在此生效并逐条点名，校验复用收尾的同一张活图（`KA-26`）。
        """
        repo = KpRepository(session)
        node = repo.get(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"知识点节点不存在：{node_id}")
        if payload.if_rev is not None and node.rev != payload.if_rev:
            raise HTTPException(
                status_code=409,
                detail=f"af_kp 乐观并发冲突：{node_id} 当前 rev={node.rev}，请求 if_rev={payload.if_rev}",
            )
        given = payload.model_fields_set - {"if_rev"}
        fields: dict[str, Any] = {key: getattr(payload, key) for key in given if key != "links" and key != "anchors"}
        if "type" in given and node.node_kind == "outline" and payload.type:
            raise HTTPException(
                status_code=422,
                detail=f"目录行的 type 恒为空串：{node_id}（node_kind='outline'，两层目录的分类型属点）",
            )
        if "anchors" in given:
            fields["anchors"] = [anchor.model_dump() for anchor in payload.anchors or []]
        if "links" in given:
            try:
                fields["links"] = validate_endorsement_links(
                    session, node=node, links=[link.model_dump() for link in payload.links or []]
                )
            except KpGateError as exc:
                raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        if not fields:
            return _kp_node_echo(node, session)
        written = repo.update(node_id, fields, if_rev=payload.if_rev)
        if not written.ok:
            if written.rev_conflict:
                raise HTTPException(
                    status_code=409, detail=f"af_kp 乐观并发冲突：{node_id} 已被他人改写"
                )
            raise HTTPException(status_code=404, detail=f"知识点节点不存在：{node_id}")
        assert written.node is not None
        return _kp_node_echo(written.node, session)

    @app.post("/api/v1/kp/nodes/{node_id}/confirm", response_model=KpConfirmOut)
    def confirm_kp_node(
        node_id: str, payload: KpConfirmIn, session: SessionDep
    ) -> KpConfirmOut | JSONResponse:
        """确认或撤回（`KA-14`/`KA-17`）：置位跑「可回指」前置校验并把缺失项逐条点名回 422。

        判定序本身就是契约（404 → 409 → 422 → 200）：撤回不跑校验，锚坏了的行也退得出来。
        422 的正文与 200 同形（`KpConfirmOut`，`missing` 非空）而不套 `detail`：`missing` 是要逐条
        显示的清单，两种状态码两套形状会把界面逼成先判码再判体。404／409 仍是全族统一的 `detail` 串。
        """
        result = KpRepository(session).confirm(node_id, confirmed=payload.confirmed, if_rev=payload.if_rev)
        if result.node is None:
            raise HTTPException(status_code=404, detail=f"知识点节点不存在：{node_id}")
        if not result.ok:
            if result.rev_conflict:
                raise HTTPException(
                    status_code=409,
                    detail=f"af_kp 乐观并发冲突：{node_id} 当前 rev={result.node.rev}，"
                    f"请求 if_rev={payload.if_rev}",
                )
            if result.missing:
                return JSONResponse(
                    status_code=422,
                    content=KpConfirmOut(
                        node_id=node_id,
                        confirmed=result.node.confirmed,
                        rev=result.node.rev,
                        missing=list(result.missing),
                    ).model_dump(),
                )
            raise HTTPException(status_code=409, detail=f"确认动作未落库：{node_id}（并发改写，请重读后重试）")
        return KpConfirmOut(
            node_id=node_id,
            confirmed=result.node.confirmed,
            rev=result.node.rev,
            missing=[],
        )

    @app.put("/api/v1/kp/nodes/{node_id}/narrative", response_model=KpNarrativeOut)
    def put_kp_narrative(
        node_id: str, payload: KpNarrativeWriteIn, session: SessionDep
    ) -> KpNarrativeOut:
        """长文写腿（`KA-30`）：一篇**已成文**的详解落到 `KA-20` 的派生路径，指针由第四条系统腿写。

        判定序与 `PATCH`／`confirm` 同族，两处不同是刻意的：**409 排在任何磁盘写入之前**（文件写了
        撤不回，旧表单先把正文改了再被序号拒就留下一份没人认领的正文），形状闸在请求模型里故 422
        **先于查行**（空正文与超限都拒，不截断——与 `summary[:1000]` 故意相反）。
        先落盘再落列：反过来会留一个指不到文件的列值。落盘成功而指针没落库时整个响应以 409 结束、
        事务回滚，磁盘上多出的那份按 `KA-21` 不算孤儿（行仍在），重发一次即收敛——文件名由 `node_id`
        派生、同名覆盖，本端点天然幂等。
        """
        repo = KpRepository(session)
        node = repo.get(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"知识点节点不存在：{node_id}")
        if payload.if_rev is not None and node.rev != payload.if_rev:
            raise HTTPException(
                status_code=409,
                detail=f"af_kp 乐观并发冲突：{node_id} 当前 rev={node.rev}，请求 if_rev={payload.if_rev}",
            )
        try:
            name = write_narrative(session, data_root=data_root, node_id=node_id, content=payload.content)
        except KpGateError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        written = repo.set_narrative_ref(node_id, name, if_rev=payload.if_rev)
        if not written.ok or written.node is None:
            raise HTTPException(
                status_code=409,
                detail=f"长文指针未落库：{node_id}（并发改写；正文已落盘，重读 rev 后重发即收敛）",
            )
        return _kp_narrative_out(
            written.node, pointer=written.node.narrative_ref, content=payload.content, exists=True
        )

    @app.get("/api/v1/kp/nodes/{node_id}/narrative", response_model=KpNarrativeOut)
    def get_kp_narrative(node_id: str, session: SessionDep) -> KpNarrativeOut:
        """长文读回（`KA-30`）：按同一条派生式现算路径读那一篇——读的是**文件**，不是列。

        缺文件回 **200 带 `exists=false`** 而不判 404：行在而正文缺是需要被显示出来的矛盾，判 404
        会把「节点存在」谎报成「节点不存在」。本端点既不修列也不删文件（两处都不是读腿的职权），
        也不自增 `rev`。与写腿共用同一条路径派生与同一道 jail，两条腿读到的必然是同一个文件。
        """
        node = KpRepository(session).get(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"知识点节点不存在：{node_id}")
        try:
            name, content = read_narrative(session, data_root=data_root, node_id=node_id)
        except KpGateError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _kp_narrative_out(
            node,
            pointer=name if content is not None else node.narrative_ref,
            content=content or "",
            exists=content is not None,
        )

    @app.post("/api/v1/kp/nodes/{node_id}/summary", response_model=KpNodeOut)
    def regenerate_kp_summary(
        node_id: str, payload: KpSummaryRegenIn, session: SessionDep
    ) -> KpNodeOut:
        """单点重生成（`KA-45`）：知识点详情面「重新生成总结」的落点，覆盖该行的 `summary`。

        这是一条**同步**腿（一次百秒级的模型调用），不进 `KA-4` 的单 worker 队列：编排层因此在发问
        之前挡同书在飞的批次（409），免得两路并跑把本机模型服务顶到超时。人工发起的写不遵守
        「原值为空才写」（那是机器的判据），但 `if_rev` 照旧钉——屏上读到的那份变了就先撞 409。
        新序号随响应体回来，屏侧下一次保存按它走。
        """
        _, engine = _kp_text_engine()
        _probe_model_service(engine)
        try:
            node = regenerate_section_summary(
                session,
                data_root=data_root,
                engine=engine,
                node_id=node_id,
                if_rev=payload.if_rev,
            )
        except KpGateError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _kp_node_echo(node, session)

    @app.put("/api/v1/books/{book_id}/kp/outline", response_model=KpOutlineDeclareOut)
    def declare_kp_outline(
        book_id: SafeBookId, payload: KpOutlineDeclareIn, session: SessionDep
    ) -> KpOutlineDeclareOut:
        """目录**整批**声明（`KA-8`）：在 `af_books` 行锁内「读现有 → 算差异 → 写全部」，缺席只降级。

        与逐页结构意图（`PUT …/structure`）共用同一把锁与同一条幂等纪律：重复提交同一份声明结果
        不变，不需要 undo 端点。声明项只有身份与挂载两列——层数与名不是声明面能改的东西。
        `detached_points` 在写入**之前**算：它是「本次退了几行」，写完再数就变成「现在池里有几行」。
        """
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        repo = KpRepository(session)
        existing = repo.list_for_book(book_id)
        declared_ids = {decl.node_id for decl in payload.declarations}
        absent_outlines = {
            node.node_id for node in existing if node.node_kind == "outline" and node.node_id not in declared_ids
        }
        detached_points = sum(
            1
            for node in existing
            if node.node_kind == "point" and node.parent_id in absent_outlines
        )
        try:
            rows = repo.replace_outline(
                book_id,
                [
                    KpOutlineDecl(node_id=decl.node_id, parent_id=decl.parent_id, order_hint=decl.order_hint)
                    for decl in payload.declarations
                ],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        spans = _kp_page_spans(rows, book.page_count)
        types = node_types_of(rows)
        return KpOutlineDeclareOut(
            book_id=book_id,
            outline=[_kp_node_out(node, spans, types) for node in rows],
            detached_points=detached_points,
        )

    @app.get("/api/v1/books/{book_id}/kp/export", response_model=KpExportOut)
    def export_kp_book(book_id: SafeBookId, session: SessionDep) -> KpExportOut:
        """只读导出面＝根侧拉取面（`KA-15`）：只给 `confirmed=1` 的行，一册全给，目录先于点。

        逐行带 `rev` 与 `updated_at`，增量由拉取方按它们判，服务端不记「谁拉过什么」。空集是 200 不是
        404——「这本书还没有已确认结论」是合法现状。`review_flags` 只承载 `KA-3` 的外发提示（页确认门
        启用时才产出），只提示、不拦截。
        """
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        nodes = order_for_export(
            [node for node in KpRepository(session).list_for_book(book_id) if node.confirmed == 1]
        )
        spans = _kp_page_spans(nodes, book.page_count)
        types = node_types_of(nodes)
        return KpExportOut(
            book_id=book_id,
            nodes=[_kp_node_out(node, spans, types) for node in nodes],
            review_flags=[
                AnchorReviewOut(node_id=flag.node_id, page=flag.page, reason=flag.reason)
                for flag in unconfirmed_anchor_flags(session, nodes)
            ],
            total=len(nodes),
        )

    return app


app = create_app()
