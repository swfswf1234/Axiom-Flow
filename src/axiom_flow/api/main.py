"""8902 API v1 路由：对外协议适配，返回结构与 schemas 一致（契约 docs/architecture/api.md）。

实现状态：Current（af_* 持久化 + 异步 parse-jobs + 分块预览；对照编辑为覆盖层合并）。
只读端点不依赖模型服务与凭据；仅解析任务经引擎适配器直连模型服务（ADR 0004），且提交期
探活（不可用 503，不落任务行）、引擎名按 `AXIOM_OCR_ENGINE`/请求体显式分派（无自动兜底）。
读取面按**已解析页最多的可见版本**解析目录（PLAN-030；未激活的 `partial`／`failed` 版本里已落盘的页
同样可读）：作用域唯一入口是 `_read_scope`，URL、响应结构与 `image_url` 字面量对根仓库前端保持不变。
"""

import logging
import shutil
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow import __version__ as VERSION
from axiom_flow.api.admin import mount_admin
from axiom_flow.config import Settings, load_settings
from axiom_flow.db.engine import create_engine_for
from axiom_flow.db.llm_ledger import LlmCallIdentity, LlmCallLedger
from axiom_flow.db.models import DOC_KIND_EXERCISE, AfBook, AfKp, AfOperation
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
    sanitize_material_ranges,
)
from axiom_flow.engines.base import EngineError, KnowledgeEngine, ParseEngine
from axiom_flow.engines.factory import (
    EngineSelectionError,
    TextSlotError,
    build_engine,
    build_text_engine,
    resolve_engine_name,
)
from axiom_flow.ingest import (
    book_dir_for,
    ensure_within,
    ingest_book,
    version_dir_for,
    warn_transient_residue,
    write_manifest,
)
from axiom_flow.normalize.chunking import build_chunks, read_chunk_set
from axiom_flow.normalize.furniture import furniture_blocks, furniture_hints
from axiom_flow.normalize.intents import apply_relabels, relabels_by_block
from axiom_flow.normalize.toc import TOC_ROLE_KNOWLEDGE, node_type_of, node_types_of
from axiom_flow.orchestrator import (
    MODEL_UNAVAILABLE,
    JobRunner,
    rebuild_chunks,
    resolve_visible_version,
)
from axiom_flow.orchestrator.chapter_text import ChapterTextError, merge_chapter_text
from axiom_flow.orchestrator.jobs import KpLedgerWiringError
from axiom_flow.orchestrator.knowledge import (
    ExerciseReading,
    KpGateError,
    ReferenceFetcher,
    SpanRow,
    accept_kp_job,
    anchor_review_flags,
    book_stage_reading,
    book_toc_pages,
    collapse_outline_duplicates,
    display_spans_of,
    exercise_workspace,
    order_for_export,
    read_narrative,
    regenerate_chapter_narrative,
    regenerate_section_summary,
    split_optional_marker,
    unconfirmed_anchor_flags,
    validate_endorsement_links,
    write_narrative,
)
from axiom_flow.orchestrator.reparse import (
    SCOPE_SELECTED,
    build_reparse_plan,
    resolve_scope,
)
from axiom_flow.reference import collect_reference_notes
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
    ChapterTextOut,
    ChapterTextRequest,
    ChunkParams,
    ChunkSet,
    EditRecord,
    EditRequest,
    IngestResponse,
    IngestStatus,
    JobStatus,
    KpConfirmIn,
    KpConfirmOut,
    KpExerciseItemOut,
    KpExerciseOut,
    KpExportOut,
    KpJobAccepted,
    KpJobCreate,
    KpJobProgressOut,
    KpKeyDefinition,
    KpLedgerOut,
    KpNarrativeGenerateIn,
    KpNarrativeOut,
    KpNarrativeWriteIn,
    KpNodeOut,
    KpNodePatch,
    KpOutlineDeclareIn,
    KpOutlineDeclareOut,
    KpStageOut,
    KpSummaryRegenIn,
    KpWorkspaceOut,
    ManifestEntry,
    PageBatchOut,
    PageBatchRequest,
    PageBatchResult,
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
    ReparsePageAdvice,
    ReparsePlanOut,
    StructureRequest,
    SyncResponse,
)

logger = logging.getLogger(__name__)

# 路径参数中的 book_id 必须是安全路径段（防目录穿越，与 schemas 请求体同一约束）。
SafeBookId = Annotated[str, PathParam(pattern=SAFE_SEGMENT_PATTERN)]


def _book_out(book: AfBook, *, curation_in_flight: bool = False) -> BookOut:
    """书目读面：`curation_in_flight` 是读时派生的第三段（`KA-55`），由调用方按库内 `draft` 行给出。"""
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
        doc_kind=book.doc_kind,
        last_curation_result=book.last_curation_result,
        curation_in_flight=curation_in_flight,
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


def _anchor_block(row: AfKp, page: int) -> int | None:
    """一行在给定页上最早那块（与编排层 `_first_anchor_block` 同一判据）：块序缺失回 `None`。"""
    indexes = [
        int(index)
        for anchor in (row.anchors or [])
        if isinstance(anchor, dict) and int(anchor.get("page") or 0) == page
        for index in (anchor.get("block_indexes") or [])
        if isinstance(index, int) and index >= 0
    ]
    return min(indexes) if indexes else None


PRACTICE_SYNC_TIMEOUT_SECONDS = 30.0

#: 内嵌图片读腿的媒体类型白名单（REQ-179 R1）：题包图片由 MinerU 落盘为常见图像扩展名；
#: 未登记的扩展名按字节流给，浏览器自行判定。
_PACK_IMAGE_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _course_id_of_book(session: Session, book_id: str) -> str:
    """书的课程坐标（`af_books.course_id`）：同步腿按课程寻址，取不到就不同步（不猜一个空课程）。"""
    book = BookRepository(session).get(book_id)
    return str(getattr(book, "course_id", "") or "") if book is not None else ""


def _post_practice_sync(gateway_url: str) -> Callable[[str, str], None]:
    """构造「确认即同步」出站回调（REQ-168 W8）：确认落库后把书搬进 8900 的课后练习题库。

    出站纪律与梳理网关（`MI-10`）同形：**`trust_env=False`** —— 8900 在回环上，交给 Windows
    系统代理会得到 502／0 字节的假象。超时取 30s：这一步只做幂等 upsert，撞上大批（895 题）
    也不该把 confirm 的响应拖住；超了按失败处理，由用户在梳理屏重发一次确认来补。

    **失败只记警告、绝不外抛**：确认位已经落库、不可回退，把回调异常透成 5xx 会让用户以为
    「确认失败」而反复重试，反而把已做成的事搅乱；真失败的后果只是「题还没进练习面」，
    下一次确认（或直接调同步端点）即收敛。
    """

    def _sync(course_id: str, book_id: str) -> None:
        url = f"{gateway_url}/api/v1/learn/courses/{course_id}/exercises/sync"
        try:
            with httpx.Client(trust_env=False, timeout=PRACTICE_SYNC_TIMEOUT_SECONDS) as client:
                response = client.post(url, params={"book": book_id})
            if response.status_code >= 400:
                logger.warning(
                    "确认后同步课后练习失败（书 %s）：%s %s", book_id, response.status_code,
                    response.text[:200],
                )
        except httpx.HTTPError as exc:  # 连不上／超时／协议错：只记，不外抛
            logger.warning("确认后同步课后练习失败（书 %s）：%s", book_id, type(exc).__name__)

    return _sync


def _kp_page_spans(
    rows: Sequence[AfKp], last_page: int | None
) -> dict[str, tuple[int | None, int | None, int | None, int | None]]:
    """在册行 → 每行的显示段与按块切点（D-1~D-9）：算式住在编排层，读面不自推。

    参与切分的是 `gen_status='candidate'` 的目录行与点行（`draft` 是在飞读数、`rejected` 是被本批取代
    的旧读数，拿它们切段会把中间那一段凭空挖掉）。两者同进一棵树：目录段的终点止于首子之前，点段
    管到下一枚点之前——层数不再由读面另立一套。`af_books.page_count` 缺时尾行只发起点，两键退化是
    披露，不是整面报错的理由；锚点缺页的行不参与，读面因此发空四键。

    四键＝`(page_start, page_end, start_block, end_block)`：后两枚是同页分行必要的块序——
    `start_block` 是起点页上属于本行的第一块、`end_block` 是终点页上属于本行的最后一块（`None`＝整页）。
    起点被游标抬到别的页时本行不在那页按块起读，`start_block` 照发 `None`。
    """
    items: list[SpanRow] = []
    for row in rows:
        if row.node_kind not in ("outline", "point") or row.gen_status != "candidate":
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
                toc_kind=str((row.meta or {}).get("toc_kind") or ""),
                parent_id=row.parent_id,
                block=_anchor_block(row, pages[0]),
            )
        )
    if not items:
        return {}
    by_id = {item.node_id: item for item in items}
    result: dict[str, tuple[int | None, int | None, int | None, int | None]] = {}
    for span in display_spans_of(items, last_page=last_page):
        if not span.node_id:
            continue
        item = by_id[span.node_id]
        start_block = item.block if span.page_start is not None and item.page == span.page_start else None
        result[str(span.node_id)] = (span.page_start, span.page_end, start_block, span.end_block)
    return result


def _display_range_of(
    node: AfKp, spans: Mapping[str, tuple[int | None, int | None, int | None, int | None]] | None
) -> tuple[int | None, int | None, int | None, int | None]:
    """一行的四枚显示键：一律读派生器结果，不按 `node_kind` 分两套算式（D-1）。

    派生器只收参与行（在册候选、有锚点）；不在表里＝这一行不展示，四键为空是披露。
    """
    return (spans or {}).get(node.node_id, (None, None, None, None))


def _kp_node_out(
    node: AfKp,
    spans: Mapping[str, tuple[int | None, int | None, int | None, int | None]] | None = None,
    node_types: Mapping[str, str] | None = None,
) -> KpNodeOut:
    """`af_kp` 一行的响应投影（工作区／背书回显／导出面同一份）。

    不发 `norm_key` 与整槽 `meta`：前者是去重用的派生值（冲突由 422 的点名承载），后者是禁进 WHERE
    的扩展槽（`database-design.md` 的 `meta` 列契约）。两枚身份位 `toc_role`／`toc_kind` 是例外，
    从槽里逐字取出带出（`KA-39`）——屏上要标「非知识点」，靠的就是它们。点行详情的两枚读数
    `must_master`／`key_definitions`（`KA-57`）走同一条「逐键取出」的口径，槽里的坏值归空表；
    第三枚 `material_ranges`（`KA-66`）走同一份净化，坏项丢弃、坏根值归空表。
    `page_start`／`page_end` 是读时派生的**显示段**（D-1~D-10，与取证区间分家）：调用方手里有整册行时
    传 `spans`，没有则两键为空——单行回显（背书腿）不为了两枚派生键去多读一整册。
    `start_block`／`end_block` 是同一条派生腿上的按块切点：同页两点各占各的块段，屏上按这两枚切正文
    （`KA-67`）；空＝那一端整页起／整页止，不猜块序。
    `node_type` 是第三枚派生键（`KA-44`）：四值类型要先看祖先的 `toc_kind` 才判得出习题的子行，
    所以同样由调用方按整册行算好递进来；缺省时按本行自己的身份位判（不猜祖先）。
    """
    meta = node.meta or {}
    page_start, page_end, start_block, end_block = _display_range_of(node, spans)
    toc_role = str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE)
    toc_kind = str(meta.get("toc_kind") or "")
    raw_mastery = meta.get("must_master")
    must_master = (
        [item for item in raw_mastery if isinstance(item, str) and item.strip()]
        if isinstance(raw_mastery, list)
        else []
    )
    raw_definitions = meta.get("key_definitions")
    key_definitions = (
        [
            KpKeyDefinition(
                term=str(item.get("term") or ""), definition=str(item.get("definition") or "")
            )
            for item in raw_definitions
            if isinstance(item, dict) and item.get("term") and item.get("definition")
        ]
        if isinstance(raw_definitions, list)
        else []
    )
    node_type = (node_types or {}).get(node.node_id) or node_type_of(
        depth=node.depth, toc_role=toc_role, toc_kind=toc_kind
    )
    material_ranges = sanitize_material_ranges(meta.get("material_ranges"))
    # 存量行名里还带着排版器的选学星号（`\*`）：成行处已卸，读面对旧行同口径卸（零数据修补）。
    display_name, _ = split_optional_marker(node.name or "")
    return KpNodeOut(
        node_id=node.node_id,
        book_id=node.book_id,
        job_id=node.job_id,
        node_kind=node.node_kind,
        node_type=node_type,
        parent_id=node.parent_id,
        depth=node.depth,
        order_hint=node.order_hint,
        name=display_name,
        role=node.role or "",
        type=node.type or "",
        summary=node.summary or "",
        must_master=must_master,
        key_definitions=key_definitions,
        links=list(node.links or []),
        anchors=list(node.anchors or []),
        page_start=page_start,
        page_end=page_end,
        start_block=start_block,
        end_block=end_block,
        narrative_ref=node.narrative_ref or "",
        gen_status=node.gen_status,
        gen_batch=node.gen_batch or "",
        model_ref=node.model_ref or "",
        toc_role=toc_role,
        toc_kind=toc_kind,
        source_book_id=str(meta.get("source_book_id") or ""),
        source_pages=[
            int(page) for page in (meta.get("source_pages") or []) if isinstance(page, int)
        ],
        material_ranges=material_ranges,
        confirmed=node.confirmed,
        rev=node.rev,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _kp_exercise_out(reading: ExerciseReading | None) -> KpExerciseOut | None:
    """习题读面的响应装配（`KA-53`）：只搬服务端派生好的读数，屏侧不重算完成与状态。

    教材的共享主表永不含习题行，服务端对它给 `None`——这里原样透传，不拿空形状冒充「本书无题」。
    """
    if reading is None:
        return None
    return KpExerciseOut(
        kind=reading.kind,  # type: ignore[arg-type]
        target_book_id=reading.target_book_id,
        items=[KpExerciseItemOut(**item) for item in reading.items],
        packs=reading.packs,
        pending_reparse=reading.pending_reparse,
        unmatched=[dict(item) for item in reading.unmatched],
        done=reading.done,
    )


def _is_exercise_book(book: AfBook | None) -> bool:
    """习题集的类型派生走 S4 豁免（`exercise_book=True`）：章按书种类认，不靠子行认。

    目录批先于题行落库，按教材口径「无子行即其他」会把整本目录降成附件、习题受理门永远放不开。
    """
    return book is not None and book.doc_kind == DOC_KIND_EXERCISE


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
        node_types_of(rows, exercise_book=_is_exercise_book(book)),
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
    reference_fetcher: ReferenceFetcher | None = None,
    practice_sync: Callable[[str, str], None] | None = None,
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
    `practice_sync(course_id, book_id)` 是「确认即同步」的出站回调（REQ-168 W8）：确认落库后由本仓
    回调 8900 的 `POST /learn/courses/{course_id}/exercises/sync?book=`，把该书已确认的题投影进课后
    练习。缺省走 `_post_practice_sync`（httpx，`trust_env=False`，与梳理网关同一出站纪律）；注入只
    留给测试。**该回调绝不改变 confirm 的返回码**——确认已落库不可回退，回调失败只记警告。
    """
    settings = settings or load_settings()
    data_root = Path(settings.data_dir)

    db_engine = None
    if session_factory is None:
        db_engine = create_engine_for(settings)
        session_factory = sessionmaker(bind=db_engine, future=True)
    make_engine = engine_factory or (lambda name: build_engine(name, settings))
    make_knowledge_engine = knowledge_engine_factory or (lambda name: build_text_engine(name, settings))
    fetch_references = reference_fetcher or collect_reference_notes
    sync_practice = practice_sync or _post_practice_sync(settings.engine_gateway_url)
    runner = JobRunner(
        session_factory=session_factory,
        engine_factory=make_engine,
        data_root=data_root,
        background=background_jobs,
        window_size=settings.parse_window_size,
        knowledge_engine_factory=make_knowledge_engine,
        kp_call_ledger=LlmCallLedger(session_factory),
        kp_call_identity=kp_call_identity,
        reference_fetcher=fetch_references,
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
        """读面作用域解析的唯一入口（PLAN-030）：返回 `(产物目录, job_id 作用域)`。

        作用域＝**已解析页最多的可见版本**（`orchestrator.versions.resolve_visible_version`）：
        未激活的 `partial`／`failed` 版本里已落盘的页同样可读，`active_job_id` 只在页数并列时优先。
        生效指针非空而目录缺失时仍显式 404 而不回落：静默回落会让读者把旧产物当成新版本核对。
        没有带内容的版本时回落书目录根（legacy 根视图，迁移前存量产物与重 ingest 后的目录）。
        """
        book = BookRepository(session).get(book_id)
        book_dir = _require_book_dir(session, book_id)
        if book is None:
            return book_dir, LEGACY_JOB_ID
        if book.active_job_id:
            active_dir = version_dir_for(
                data_root, book.domain_id, book.course_id, book_id, book.active_job_id
            )
            if not active_dir.is_dir():
                raise HTTPException(
                    status_code=404, detail=f"生效版本产物缺失：{book_id} @ {book.active_job_id}"
                )
        version_dir, job_id = resolve_visible_version(
            session, data_root=data_root, book=book
        )
        if version_dir is None:
            return book_dir, LEGACY_JOB_ID
        return version_dir, job_id

    def _load_page(artifact_dir: Path, page_no: int) -> BlocksPage:
        path = artifact_dir / "pages" / f"p{page_no:04d}.blocks.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"页产物缺失：第 {page_no} 页")
        return BlocksPage.model_validate_json(path.read_text(encoding="utf-8"))

    def _load_pages(
        artifact_dir: Path,
        *,
        skip_io_errors: bool = False,
        io_errors: list[OSError] | None = None,
    ) -> list[BlocksPage]:
        pages: list[BlocksPage] = []
        for path in sorted((artifact_dir / "pages").glob("p*.blocks.json")):
            try:
                pages.append(BlocksPage.model_validate_json(path.read_text(encoding="utf-8")))
            except OSError as exc:
                if io_errors is not None:
                    io_errors.append(exc)
                if not skip_io_errors:
                    raise
                continue
            except ValueError:
                continue
        return pages

    #: 家具标记的进程内缓存（AF-T6 乙半边／REQ-143 W20）。判一条重复线的分母是**整本页集**，本仓无
    #: 跨请求读缓存，逐请求即逐请求整本读盘（现网最大 439 页）。失效条件只有两条，与写入面一一对应：
    #: ① 换版＝换键——`job_id` 进键，旧版本目录随激活删除，旧键不会再被读到；② 同版本内唯一能改标记
    #: 的写是块编辑的 `corrected_type`（线维吃产物文本、结构意图不动块序列），编辑端点写后作废本书本版键。
    furniture_marks: dict[tuple[str, str], dict[int, tuple[int, ...]]] = {}

    def _furniture_marks_for(
        session: Session,
        book_id: str,
        job_id: str,
        artifact_dir: Path,
        *,
        skip_io_errors: bool = False,
    ) -> dict[int, tuple[int, ...]]:
        """整本生效版本的家具块标记（类型维∪线维），按 `(book_id, job_id)` 缓存。

        常量级读路径上没有「产物在版本内被改写」这条通路（parse job 只写自己的版本目录），所以缓存
        只需守住上面两条失效条件；批量取页允许整本扫描跳过 I/O 失败页且不缓存降级结果，其它读端点
        保持既有容错面——标记少一页好过整页读不出来，也不能让后续单页读到缺页标记。
        """
        cached = furniture_marks.get((book_id, job_id))
        if cached is not None:
            return cached
        edits_by_page: dict[int, list[EditRow]] = defaultdict(list)
        for edit in OperationRepository(session).list_edits_for_book(book_id, job_id):
            edits_by_page[edit.page_no].append(edit)
        io_errors: list[OSError] = []
        replayed = [
            page.model_copy(
                update={
                    "blocks": apply_relabels(
                        page.blocks, relabels_by_block(edits_by_page.get(page.page, []))
                    )
                }
            )
            for page in _load_pages(
                artifact_dir, skip_io_errors=skip_io_errors, io_errors=io_errors
            )
        ]
        marks = furniture_blocks(replayed, hints=furniture_hints(replayed))
        if not io_errors:
            furniture_marks[(book_id, job_id)] = marks
        return marks

    @app.get("/api/v1/health")
    def health() -> dict:
        """服务健康探针：8900 控制中心与生命周期脚本（--wait/status）的就绪判据。"""
        return {"status": "ok"}

    # ---------- ② 数据查询（af_*） ----------

    @app.get("/api/v1/books", response_model=list[BookOut])
    def list_books(session: SessionDep) -> list[BookOut]:
        """书目列表：只读 af_books（REQ-097：文件系统不再是书目来源）。

        「开始梳理」由一条分组查询一次取全（`KA-55`），不为每本书各查一次 `af_kp`。
        """
        rows = BookRepository(session).list_books()
        curating = KpRepository(session).curating_book_ids()
        return [_book_out(row, curation_in_flight=row.book_id in curating) for row in rows]

    @app.get("/api/v1/books/{book_id}", response_model=BookOut)
    def get_book(book_id: SafeBookId, session: SessionDep) -> BookOut:
        """单书目元数据（af_books 行；不在库即 404，不查文件系统）。"""
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        return _book_out(book, curation_in_flight=KpRepository(session).find_draft_batch(book_id) is not None)

    @app.patch("/api/v1/books/{book_id}", response_model=BookOut)
    def update_book(book_id: SafeBookId, payload: BookUpdate, session: SessionDep) -> BookOut:
        """修改书目（Axiom-Flow 自有字段，如 notes）。"""
        repo = BookRepository(session)
        if not repo.update_notes(book_id, payload.notes):
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        book = repo.get(book_id)
        assert book is not None
        return _book_out(book, curation_in_flight=KpRepository(session).find_draft_batch(book_id) is not None)

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
                roles=list(item.roles),
            )
            for item in items
        ]
        repo = BookRepository(session)
        result = repo.upsert_many(rows)
        books = [repo.get(item.book_id) for item in items]
        curating = KpRepository(session).curating_book_ids()
        return SyncResponse(
            synced=result.synced,
            updated=result.updated,
            books=[
                _book_out(book, curation_in_flight=book.book_id in curating)
                for book in books
                if book is not None
            ],
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

    @app.get("/api/v1/books/{book_id}/reparse-plan", response_model=ReparsePlanOut)
    def get_reparse_plan(book_id: SafeBookId, session: SessionDep) -> ReparsePlanOut:
        """只读重跑建议（PLAN-019）：逐页给出 DB 状态 + 文件核验 + 建议理由；**不替人决定、不触发重跑**。

        `status` 来自 `af_pages`（DB 状态），`has_artifact` 来自版本目录（文件真实性）；
        `status=parsed` 但 `has_artifact=false` 的页 suggested=true、reason=artifact_missing。
        """
        artifact_dir, job_id = _read_scope(session, book_id)
        total = _page_total(session, book_id)
        rows = PageRepository(session).list_for_book(book_id, job_id)
        version_dir = None if job_id == LEGACY_JOB_ID else artifact_dir
        advice = build_reparse_plan(rows, total_pages=total, version_dir=version_dir)
        return ReparsePlanOut(book_id=book_id, job_id=job_id, total=total, pages=[
            ReparsePageAdvice(
                page_no=a.page_no, status=a.status, has_artifact=a.has_artifact,
                suggested=a.suggested, reason=a.reason,
            )
            for a in advice
        ])

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

    def _page_data(
        book_id: str,
        page_no: int,
        blocks: BlocksPage,
        markdown: str,
        edit_rows: Sequence[EditRow],
        furniture: Sequence[int],
    ) -> PageData:
        return PageData(
            blocks=blocks.model_copy(
                update={"blocks": apply_relabels(blocks.blocks, relabels_by_block(edit_rows))}
            ),
            markdown=markdown,
            image_url=f"/api/v1/books/{book_id}/pages/{page_no}/image",
            edits=[_edit_record(edit) for edit in edit_rows],
            furniture_blocks=list(furniture),
        )

    @app.post("/api/v1/books/{book_id}/pages/batch", response_model=PageBatchOut)
    def get_pages_batch(
        book_id: SafeBookId, payload: PageBatchRequest, session: SessionDep
    ) -> PageBatchOut:
        """同书多页只读批量入口：一次选版，逐页按 DB 状态与产物真实性归因。"""
        artifact_dir, job_id = _read_scope(session, book_id)
        total = _page_total(session, book_id)
        out_of_range = [page_no for page_no in payload.page_nos if page_no > total]
        if out_of_range:
            raise HTTPException(
                status_code=422,
                detail=f"页码越界：共 {total} 页，请求包含 {out_of_range}",
            )

        states = {row.page_no: row for row in PageRepository(session).list_states(book_id, job_id)}
        edits_by_page: dict[int, list[EditRow]] = defaultdict(list)
        for edit in OperationRepository(session).list_edits_for_book(book_id, job_id):
            edits_by_page[edit.page_no].append(edit)

        results: list[PageBatchResult] = []
        marks: dict[int, tuple[int, ...]] | None = None
        for page_no in payload.page_nos:
            state = states.get(page_no)
            parse_status = state.status if state is not None else PageState.PENDING
            if state is None:
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="missing",
                        parse_status=parse_status,
                        error=f"该版本没有第 {page_no} 页的页行",
                    )
                )
                continue
            if parse_status != PageState.PARSED:
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="not_parsed",
                        parse_status=parse_status,
                        error=state.error or f"第 {page_no} 页尚未解析",
                    )
                )
                continue

            pages_dir = artifact_dir / "pages"
            blocks_path = pages_dir / f"p{page_no:04d}.blocks.json"
            markdown_path = pages_dir / f"p{page_no:04d}.md"
            if not blocks_path.is_file():
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="artifact_missing",
                        parse_status=parse_status,
                        error=f"页块产物缺失：第 {page_no} 页",
                    )
                )
                continue
            if not markdown_path.is_file():
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="artifact_missing",
                        parse_status=parse_status,
                        error=f"页 Markdown 产物缺失：第 {page_no} 页",
                    )
                )
                continue
            try:
                blocks = _load_page(artifact_dir, page_no)
            except HTTPException:
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="artifact_missing",
                        parse_status=parse_status,
                        error=f"页块产物缺失：第 {page_no} 页",
                    )
                )
                continue
            except (OSError, ValueError):
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="invalid_artifact",
                        parse_status=parse_status,
                        error=f"页块产物无法解析：第 {page_no} 页",
                    )
                )
                continue
            try:
                markdown = markdown_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="artifact_missing",
                        parse_status=parse_status,
                        error=f"页 Markdown 产物缺失：第 {page_no} 页",
                    )
                )
                continue
            except (OSError, ValueError):
                results.append(
                    PageBatchResult(
                        page_no=page_no,
                        status="invalid_artifact",
                        parse_status=parse_status,
                        error=f"页 Markdown 产物无法读取：第 {page_no} 页",
                    )
                )
                continue

            if marks is None:
                marks = _furniture_marks_for(
                    session, book_id, job_id, artifact_dir, skip_io_errors=True
                )
            results.append(
                PageBatchResult(
                    page_no=page_no,
                    status="ok",
                    parse_status=parse_status,
                    data=_page_data(
                        book_id,
                        page_no,
                        blocks,
                        markdown,
                        edits_by_page.get(page_no, ()),
                        marks.get(page_no, ()),
                    ),
                )
            )

        ok = sum(result.status == "ok" for result in results)
        return PageBatchOut(
            book_id=book_id,
            job_id=job_id,
            scope="legacy" if job_id == LEGACY_JOB_ID else "active",
            requested=len(payload.page_nos),
            ok=ok,
            failed=len(results) - ok,
            pages=results,
        )

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
        markdown_path = artifact_dir / "pages" / f"p{page_no:04d}.md"
        markdown = markdown_path.read_text(encoding="utf-8") if markdown_path.is_file() else ""
        return _page_data(
            book_id,
            page_no,
            blocks,
            markdown,
            edit_rows,
            _furniture_marks_for(session, book_id, job_id, artifact_dir).get(page_no, ()),
        )

    @app.get("/api/v1/books/{book_id}/pages/{page_no}/image")
    def get_page_image(book_id: SafeBookId, page_no: int, session: SessionDep) -> FileResponse:
        """原页高清图（对照左栏）。"""
        artifact_dir, _ = _read_scope(session, book_id)
        image_path = artifact_dir / "pages" / f"p{page_no:04d}.png"
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail=f"页图缺失：第 {page_no} 页")
        return FileResponse(image_path, media_type="image/png")

    @app.get("/api/v1/books/{book_id}/images/{filename}")
    def get_pack_image(book_id: SafeBookId, filename: str, session: SessionDep) -> FileResponse:
        """生效版本目录的内嵌图片（REQ-179 R1）：题包写入面改写出的读腿 URL 的取图口。

        只按单段文件名取 `pages/images/<filename>`（题包引用 `pages/images/<sha>.jpg` 的
        尾段），路径分隔符一律 404——文件名是唯一入参，不接受任何目录形状；媒体类型按
        扩展名白名单映射，未登记的扩展名按字节流给（浏览器可自行判定）。作用域与页图
        读腿同一 `_read_scope`：无生效版本同口径 404。
        """
        artifact_dir, _ = _read_scope(session, book_id)
        if "/" in filename or "\\" in filename or filename in (".", ".."):
            raise HTTPException(status_code=404, detail="图片文件名非法（不接受路径分隔）")
        image_path = ensure_within(artifact_dir, artifact_dir / "pages" / "images" / filename)
        if not image_path.is_file():
            raise HTTPException(status_code=404, detail=f"图片缺失：{filename}")
        media_type = _PACK_IMAGE_MEDIA.get(image_path.suffix.lower(), "application/octet-stream")
        return FileResponse(image_path, media_type=media_type)

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
        total_pages = book.page_count or 0
        # PLAN-019 范围表达：scope 显式时按 scope 解析目标页；scope 缺省则完全保持旧行为（向后兼容）。
        if payload.scope == SCOPE_SELECTED and not payload.pages:
            raise HTTPException(status_code=422, detail="scope=selected 必须提供 pages")
        if payload.scope is not None:
            artifact_dir, visible_job = _read_scope(session, payload.book_id)
            version_dir = None if visible_job == LEGACY_JOB_ID else artifact_dir
            advice = build_reparse_plan(
                PageRepository(session).list_for_book(payload.book_id, visible_job),
                total_pages=total_pages,
                version_dir=version_dir,
            )
            target_list, reasons = resolve_scope(
                scope=payload.scope,
                pages=payload.pages,
                advice=advice,
                failed_pages=[],
                total_pages=total_pages,
            )
            if not target_list:
                raise HTTPException(status_code=422, detail="范围解析结果为空：没有需要重跑的页")
        else:
            target_list = payload.pages or list(range(1, total_pages + 1))
            reasons = {}
        # 区域驱动受理门：只查目标页、只计 add（delete/merge 不产生模型提交）；全本提交逐页全查。
        target_pages = set(target_list)
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
        if payload.scope is not None:
            # 用户裁决：受理时把当前可见版本的目标页状态先写 failed 并写明理由，再补跑；
            # 只有所有门槛通过、确实要建任务时才写，失败路径不留半成品。
            with session.no_autoflush:
                page_repo = PageRepository(session)
                for page_no in target_list:
                    current = page_repo.get(payload.book_id, visible_job, page_no)
                    reason = reasons.get(page_no, payload.scope)
                    page_repo.upsert(
                        payload.book_id, visible_job, page_no,
                        status="failed",
                        source=current.source if current else "",
                        quality=current.quality if current else None,
                        error=f"rerun requested: {reason}",
                    )
                session.flush()
        total = len(target_list)
        job_id = uuid.uuid4().hex[:12]
        stored_pages = target_list if payload.scope is not None else payload.pages
        job = OperationRepository(session).create(
            ParseJobRow(
                job_id=job_id,
                book_id=payload.book_id,
                pages=stored_pages,
                engine=engine_name,
                status=JobStatus.QUEUED,
                progress={"parsed": 0, "total": total, "failed": 0},
            )
        )
        session.commit()  # 先落库，后台线程才能读到任务行
        runner.submit(job_id=job_id, book_id=payload.book_id, pages=stored_pages, engine=engine_name)
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
        """文本引擎槽位（`KA-22`）：三键显式空、闭集外或未接入均为 503，且不构造引擎。

        与 `resolve_engine_name` 的解析面分开：梳理请求体没有 `engine` 字段（`KA-22` 的成本归因
        要有落点），所以名字只可能来自 `AXIOM_KP_ENGINE`。出站地址与超时同样只读配置；
        三键任一显式空值都在受理期停用梳理槽位，零写入。闭集收在 `engines/factory.py`
        （`TEXT_ENGINE_NAMES`），端点不另写一份名字表；`TextSlotError` 在这里只换成 503。
        """
        name = settings.kp_engine
        if not name.strip():
            raise HTTPException(
                status_code=503,
                detail=(
                    "梳理引擎槽位未接入：AXIOM_KP_ENGINE 槽位名为空"
                    "（不回落、不缺省成任何引擎，零写入）"
                ),
            )
        if not settings.engine_gateway_url.strip():
            raise HTTPException(
                status_code=503,
                detail=(
                    "梳理引擎槽位未接入：QED_ENGINE_GATEWAY_URL 槽位地址为空"
                    "（不回落、不构造引擎，零写入）"
                ),
            )
        if settings.kp_timeout is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "梳理引擎槽位未接入：AXIOM_KP_TIMEOUT 等待预算为空"
                    "（不回落、不构造引擎，零写入）"
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
                only_failed=payload.only_failed,
                target_book_id=payload.target_book_id,
                exercise_retry_node_ids=tuple(payload.retry_node_ids),
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
        排序与导出面共用 `order_for_export`，两屏看到的是同一个结构。`stage` 是整本完成读数（`KA-58`）：
        行集复用上面这一次读，未定位所需的目录页集合只从本版批次流水取一次，不逐行查询、不写库。
        """
        book = BookRepository(session).get(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail=f"书目不存在：{book_id}")
        repo = KpRepository(session)
        rows = repo.list_for_book(book_id)
        # 存量重影行读时折叠（D-13）：先排书序再折，留下的才是「书序第一枚」。
        nodes = collapse_outline_duplicates(order_for_export(rows))
        flags = [
            *anchor_review_flags(session, data_root=data_root, nodes=nodes),
            *unconfirmed_anchor_flags(session, nodes),
        ]
        spans = _kp_page_spans(nodes, book.page_count)
        types = node_types_of(nodes, exercise_book=_is_exercise_book(book))
        stage = book_stage_reading(
            rows,
            toc_pages=book_toc_pages(session, book_id=book_id, job_id=book.active_job_id),
            exercise_book=_is_exercise_book(book),
        )
        return KpWorkspaceOut(
            book_id=book_id,
            in_flight_batch=repo.find_draft_batch(book_id),
            nodes=[_kp_node_out(node, spans, types) for node in nodes],
            review_flags=[
                AnchorReviewOut(node_id=flag.node_id, page=flag.page, reason=flag.reason)
                for flag in flags
            ],
            exercise=_kp_exercise_out(
                exercise_workspace(session, data_root=data_root, book=book)
            ),
            stage=KpStageOut(
                rows=stage.rows,
                endorsed_rows=stage.endorsed_rows,
                blank_summaries=stage.blank_summaries,
                unlocated_rows=stage.unlocated_rows,
                done=stage.done,
            ),
        )

    @app.post("/api/v1/kp/merge/chapter-text", response_model=ChapterTextOut)
    def merge_course_chapter_text(
        payload: ChapterTextRequest, session: SessionDep
    ) -> ChapterTextOut:
        """课程级章总结与导读（PLAN-034）：显式目标、生成外锁、共享表三列同事务落库。"""
        _, engine = _kp_text_engine()
        try:
            return merge_chapter_text(
                session,
                payload,
                engine,
                data_root=data_root,
                reference_fetcher=fetch_references,
            )
        except ChapterTextError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except EngineError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

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
        outline_fields = {"depth", "toc_role", "toc_kind"}
        if given & outline_fields:
            if node.node_kind != "outline":
                raise HTTPException(status_code=422, detail=f"目录改判字段只适用于目录行：{node_id}")
            if node.confirmed:
                raise HTTPException(status_code=422, detail=f"已确认目录行不可改判：{node_id}")
        effective_depth = payload.depth if "depth" in given else node.depth
        effective_parent_id = payload.parent_id if "parent_id" in given else node.parent_id
        if (
            node.node_kind == "outline"
            and given & {"depth", "parent_id"}
            and effective_depth == 2
        ):
            parent = repo.get(effective_parent_id) if effective_parent_id else None
            if (
                parent is None
                or parent.book_id != node.book_id
                or parent.node_kind != "outline"
                or parent.depth != 1
            ):
                raise HTTPException(status_code=422, detail=f"二级目录须挂在同书一级目录下：{node_id}")
        if "parent_id" in given and node.node_kind == "outline" and payload.parent_id:
            cursor = repo.get(payload.parent_id)
            seen = {node_id}
            while cursor is not None and cursor.node_id not in seen:
                seen.add(cursor.node_id)
                cursor = repo.get(cursor.parent_id) if cursor.parent_id else None
            if cursor is not None:
                raise HTTPException(status_code=422, detail=f"目录父级不得成环：{node_id}")
        if given & {"toc_role", "toc_kind"}:
            old_meta = node.meta or {}
            effective_role = (
                payload.toc_role or TOC_ROLE_KNOWLEDGE
                if "toc_role" in given
                else str(old_meta.get("toc_role") or TOC_ROLE_KNOWLEDGE)
            )
            effective_kind = (
                payload.toc_kind or ""
                if "toc_kind" in given
                else str(old_meta.get("toc_kind") or "")
            )
            if effective_role == TOC_ROLE_KNOWLEDGE and effective_kind:
                raise HTTPException(status_code=422, detail=f"知识点目录的 toc_kind 必须为空：{node_id}")
        fields: dict[str, Any] = {
            key: getattr(payload, key)
            for key in given
            if key not in {"links", "anchors", "toc_role", "toc_kind"}
        }
        if given & {"toc_role", "toc_kind"}:
            meta = dict(node.meta or {})
            if "toc_role" in given:
                meta["toc_role"] = payload.toc_role
            if "toc_kind" in given:
                if payload.toc_kind:
                    meta["toc_kind"] = payload.toc_kind
                else:
                    meta.pop("toc_kind", None)
            fields["meta"] = meta
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
        written = (
            repo.rejudge_outline(node_id, fields, if_rev=payload.if_rev)
            if given & outline_fields
            else repo.update(node_id, fields, if_rev=payload.if_rev)
        )
        if not written.ok:
            if written.rev_conflict:
                assert written.node is not None
                raise HTTPException(
                    status_code=409,
                    detail=f"af_kp 乐观并发冲突：{node_id} 当前 rev={written.node.rev}，"
                    f"请求 if_rev={payload.if_rev}",
                )
            if written.node is not None:
                raise HTTPException(
                    status_code=422,
                    detail=f"目录行当前不可改判（只允许未确认的 outline 行）：{node_id}",
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
        # 确认即同步（REQ-168 W8，用户 2026-11-22 裁决）：确认落库后把该书已确认的题搬进课后练习。
        # **只在真置位时发**：撤回不搬运（取消背书不该把题送进练习，也不该删已在册的行）；
        # 失败只记警告、不改变本次 200 与 confirmed=1 —— 确认不可回退，重试会把已做成的事搅乱。
        #
        # **必须先 commit 再回调**：`db_session` 的提交发生在端点返回之后，若在此刻就看 8900 的
        # 读面，上游读不到刚置位的那一行（它按 `confirmed≠0` 供给门筛），同步会白跑一趟。
        # 先提交不影响本端点的原子性判据：走到这里时 404／409／422 三种拒绝都已排除、写入已成立。
        if payload.confirmed and result.node.confirmed:
            course_id = _course_id_of_book(session, result.node.book_id)
            if course_id:
                session.commit()
                try:
                    sync_practice(course_id, result.node.book_id)
                except Exception as exc:  # noqa: BLE001 - 详见 docstring：任何失败都不得改本次响应
                    logger.warning(
                        "确认后同步课后练习失败（书 %s）：%s", result.node.book_id, type(exc).__name__
                    )
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

    @app.post("/api/v1/kp/nodes/{node_id}/narrative/generate", response_model=KpNarrativeOut)
    def generate_kp_narrative(
        node_id: str, payload: KpNarrativeGenerateIn, session: SessionDep
    ) -> KpNarrativeOut:
        """章导读单点生成（`KA-47`）：梳理屏「一键生成／重新生成」的导读腿，同步等一次章级模型调用。

        取材与提示词复用内容批次章腿（`KA-65` 的章前块＋子点总结），写面只碰导读两处（正文落盘＋指针列），
        **覆盖**已有导读——与内容批次「已有就不覆盖」的分界是发起人：这里是人对这一章显式要求重写。
        判定序、在飞批次闸与长超时与单点重生成同族；本层只把拒绝原样搬给调用方，不重判。
        """
        _, engine = _kp_text_engine()
        _probe_model_service(engine)
        try:
            node, content = regenerate_chapter_narrative(
                session,
                data_root=data_root,
                engine=engine,
                node_id=node_id,
                if_rev=payload.if_rev,
                revision_note=payload.revision_note,
                reference_fetcher=fetch_references,
            )
        except KpGateError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return _kp_narrative_out(
            node, pointer=node.narrative_ref, content=content, exists=True
        )

    @app.post("/api/v1/kp/nodes/{node_id}/summary", response_model=KpNodeOut)
    def regenerate_kp_summary(
        node_id: str, payload: KpSummaryRegenIn, session: SessionDep
    ) -> KpNodeOut:
        """单点重生成（`KA-45`）：梳理屏「一键生成／重新生成」的总结腿，覆盖该行的 `summary`。

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
                revision_note=payload.revision_note,
                reference_fetcher=fetch_references,
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
        # 显示段按整册在册行切（含点行）：只拿回显的那几行切，章的终点会吞掉它点子行的页。
        # 存量重影先折（D-13），否则重影行会在切段时占走一页。类型也要在整册行上判（`KA-68`）：
        # 声明回显只有目录行，缺子行时每一章都会被判成「其他」。
        current = collapse_outline_duplicates(order_for_export(repo.list_for_book(book_id)))
        spans = _kp_page_spans(current, book.page_count)
        types = node_types_of(current, exercise_book=_is_exercise_book(book))
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
        rows = KpRepository(session).list_for_book(book_id)
        # 折叠先于确认过滤：未确认的第一枚若被过滤掉，第二枚就会顶上来当「第一枚」，读面又出重影。
        ordered = collapse_outline_duplicates(order_for_export(rows))
        nodes = [node for node in ordered if node.confirmed == 1]
        # 显示段在整册行上切、只投影已确认的行：拿过滤后的子集切，未确认的父不在树里，点段会跑到书尾。
        spans = _kp_page_spans(ordered, book.page_count)
        # 类型同样在整册行上判（`KA-68`）：子点没确认不代表父章不是章。
        types = node_types_of(ordered, exercise_book=_is_exercise_book(book))
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
