"""解析任务编排：全本（默认按页窗口分块）/逐页 → 引擎 → 归一化 → 分块 → 落盘 → 回写 af_*。

设计事实源：`docs/design/parsing-pipeline.md`、[ADR 0007](../../../docs/adr/0007-parse-status-semantics.md)。
全本提交按 `window_size` 页切成**串行窗口**（PLAN-007 / REQ-086）：适配器一次调用只有一个 deadline，
整本单提交使长书必然超时；分块后单块 `EngineError` 只把该块目标页记 `failed` 并继续下一块，块间
探活失败即停止提交剩余块。失败语义（终态判据与激活门槛**同源**，不存在「`completed` 但未激活」）：
- 模型服务不可用（提交前探活失败）→ 任务 `failed`，不中断其他任务；
- 目标页全部 `parsed` 且产物在位 → `completed`，同时激活本版本；
- 至少一页成功但未齐备（含单页失败，重试 1 次仍失败）→ `partial`，**不激活**，失败页结构化写
  `af_operations` parse 行的 `failed_pages`；
- 零页成功，或整本解析异常中止 → `failed`，已解析页保留（可重跑）。
`interrupted` 不在本管线产出，只由服务启动期归一写入（ADR 0007 决定 4）。

区域驱动页（PP-21 / [ADR 0010](../../../docs/adr/0010-page-regions-are-parse-inputs.md)）：有 `op=add`
人工框的页**逐框裁剪提交**——每框一次 `parse_page`、独立重试并先过单一占用闸门；框内块按声明坐标
空间以裁剪图映射后平移回原页坐标系（归一化侧不知裁剪），一框至多一块、页内按阅读序 `(y0, x0)` 编排。
单框重试后仍 `EngineError` 则**整页**记 `failed`（错误点名框序与 `region_id`，半页产物不落盘）。
无框页回落默认路径：逐页任务单图提交；全本任务带框页剔出窗口，窗口切分只对剩余页。

版本布局（PLAN-004 / REQ-080）：一次任务 = 一个解析版本，产物只写 `<book_dir>/versions/<job_id>/`，
**不回写根视图**（根视图的页图仍由 ingest 拥有，版本目录以硬链接接入）。页状态行按
`(book_id, job_id, page_no)` 作用域，故同一 job 重跑是**续跑**：已 `parsed` 且产物在位的页跳过引擎。
生效指针只在目标页齐备且清单重算成功后置位（`af_books.active_job_id`），失败版本文件保留不删；
激活成功则删除**被替换**的旧版本目录（REQ-097 / PLAN-017 W4）——磁盘上的解析正本只留生效版本，
删除失败（读取持有句柄）只告警保留，不翻转任务终态。

持久化不变式（PP-13）：**产物写先于页行提交**，故「有页行必有产物」；每页成功/失败即
提交，使非 `EngineError` 异常穿透后台线程时前序页不被回滚吞掉；`progress` 按「距上次写入 ≥1s
或每 10 页」节流，终态必写一次。`persist_per_page=False` 仅供性能排查，退回调用方统一提交。
"""

from __future__ import annotations

import logging
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from sqlalchemy.orm import Session

from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
    BookRepository,
    OperationRepository,
    PageRepository,
)
from axiom_flow.engines.base import DocumentResult, EngineError, PageResult, ParseEngine
from axiom_flow.ingest import (
    atomic_write_bytes,
    book_dir_for,
    ensure_within,
    link_pages_into,
    png_size,
    resolve_source_pdf,
    seed_version_from,
    sha256_file,
    version_dir_for,
    write_book_meta,
    write_json,
    write_manifest,
    write_page_markdown,
)
from axiom_flow.normalize.blocks import block_from_raw, blocks_to_markdown, compute_quality, normalize_page
from axiom_flow.normalize.chunking import build_chunks, chunk_set_payload
from axiom_flow.schemas import (
    Block,
    BlocksPage,
    BlockType,
    BookMeta,
    ChunkParams,
    ChunkSet,
    JobStatus,
    LastParseResult,
    ParseStatus,
)

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150
PAGE_ATTEMPTS = 2  # 首次 + 重试 1 次
PROGRESS_MIN_INTERVAL_SECONDS = 1.0  # progress 写入的最小时间间隔
PROGRESS_PAGE_INTERVAL = 10  # 或每处理这么多页写一次
# 全本分块提交的默认窗口页数（PLAN-007 / REQ-086）：真机 W0 实测 b05 前 56 页 245s
# （约 4.4s/页），且不超过 MinerU 容器 `processing_window_size=64`；实际取值读配置
# `AXIOM_PARSE_WINDOW_SIZE`，本常量只作为未接线时的兜底。
DEFAULT_WINDOW_SIZE = 56

# 模型服务不可用的统一措辞：提交期 503 与执行期 job failed 共用（两段语义，同一文案）。
MODEL_UNAVAILABLE = "模型服务不可用：请经 8900 /models/{slot} 启停模型后重试"
# 单一占用闸门（ARCH-017）：本机 GPU 只够一个解析在飞，服务侧不会替调用方排队，串行由编排层负责。
MODEL_GATE_INTERVAL_SECONDS = 5.0
MODEL_GATE_DEADLINE_SECONDS = 900.0
MODEL_BUSY = "模型服务被其他解析任务占用：等待 {wait:g} 秒后排队/在飞仍未清零，请稍后重试"


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """任务执行结果（供 API/日志使用）。"""

    job_id: str
    status: str
    parsed: int
    total: int
    failed_pages: list[int] = field(default_factory=list)
    error: str = ""


def _pdf_geometry(pdf_path: Path) -> dict[int, tuple[tuple[float, float] | None, int | None]]:
    """每页几何：PDF 点尺寸（bbox 缩放基准）与原生文本长度（质量信号基准）。"""
    geometry: dict[int, tuple[tuple[float, float] | None, int | None]] = {}
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:  # noqa: BLE001 - 源 PDF 不可读时降级（质量信号跳过）
        return geometry
    with doc:
        for index, page in enumerate(doc):
            geometry[index + 1] = ((page.rect.width, page.rect.height), len(page.get_text() or ""))
    return geometry


def _image_size(book_dir: Path, page_no: int, geometry, dpi: int) -> tuple[int, int]:
    """页图像素尺寸：优先读 PNG 事实，缺图时按 PDF 点尺寸与 DPI 估算。"""
    png = book_dir / "pages" / f"p{page_no:04d}.png"
    if png.is_file():
        try:
            return png_size(png)
        except (OSError, ValueError):
            pass
    page_size = geometry.get(page_no, (None, None))[0]
    if page_size:
        return (max(1, math.ceil(page_size[0] * dpi / 72)), max(1, math.ceil(page_size[1] * dpi / 72)))
    return (1, 1)


def _store_images(version_dir: Path, images: dict[str, bytes]) -> None:
    """文档内嵌图片落盘到版本目录的 `pages/<相对路径>`（拒绝目录穿越，与 ingest 同一道数据根闸）。"""
    pages_root = version_dir / "pages"
    for relative, payload in images.items():
        try:
            target = ensure_within(pages_root, pages_root / relative)
        except ValueError:
            logger.warning("跳过越界图片路径：%s", relative)
            continue
        atomic_write_bytes(target, payload)


def _write_page_artifacts(version_dir: Path, page: BlocksPage) -> None:
    write_json(version_dir / "pages" / f"p{page.page:04d}.blocks.json", page.model_dump_json(indent=2))
    write_page_markdown(version_dir, page.page, blocks_to_markdown(page.blocks))


def _parse_payload_with_retry(engine: ParseEngine, payload: bytes, page_no: int) -> PageResult:
    last_error: EngineError | None = None
    for _ in range(PAGE_ATTEMPTS):
        try:
            return engine.parse_page(payload, page_no)
        except EngineError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _parse_page_with_retry(engine: ParseEngine, image_path: Path, page_no: int) -> PageResult:
    return _parse_payload_with_retry(engine, image_path.read_bytes(), page_no)


def _region_frames(regions: OperationRepository, book_id: str) -> dict[int, list[tuple[str, tuple[int, int, int, int]]]]:
    """人工待识别框按页分组（只消费 `op=add`）：`(region_id, 页图像素框)` 按阅读序 `(y0, x0)`。

    `delete`/`merge` 不改解析写面——重解析后的 IoU 重锚定属读投影（ADR 0010 决定 5）。
    """
    frames: dict[int, list[tuple[str, tuple[int, int, int, int]]]] = {}
    for row in regions.list_regions_for_book(book_id):
        if row.op != "add" or not isinstance(row.bbox, (list, tuple)) or len(row.bbox) != 4:
            continue
        box = (int(row.bbox[0]), int(row.bbox[1]), int(row.bbox[2]), int(row.bbox[3]))
        frames.setdefault(row.page_no, []).append((row.region_id, box))
    for page_frames in frames.values():
        page_frames.sort(key=lambda item: (item[1][1], item[1][0]))
    return frames


def _clamp_frame(bbox: tuple[int, int, int, int], image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """框夹取到页图内并保底 1px（与 `scale_bbox` 同一套边界纪律）。"""
    x0 = max(0, min(bbox[0], image_size[0] - 1))
    y0 = max(0, min(bbox[1], image_size[1] - 1))
    x1 = max(x0 + 1, min(bbox[2], image_size[0]))
    y1 = max(y0 + 1, min(bbox[3], image_size[1]))
    return (x0, y0, x1, y1)


def _crop_page_image(image_bytes: bytes, frame: tuple[int, int, int, int]) -> tuple[bytes, tuple[int, int]]:
    """按框裁剪原页图 → `(裁剪 PNG 字节, 裁剪像素尺寸)`；页图非 PNG 由 pymupdf 抛 ValueError。

    框坐标是页图像素，而「图片当文档开」的页尺寸按内嵌 DPI 折算成点（同一 PDF 以 96/150 DPI
    渲染时两个页图会不一致），故先按整幅像素宽折出点/像素比再缩放裁剪，保证逐像素对齐。
    """
    full = pymupdf.Pixmap(image_bytes)
    doc = pymupdf.open("png", image_bytes)
    with doc:
        page = doc[0]
        scale = page.rect.width / full.width
        clip = pymupdf.Rect(frame[0] * scale, frame[1] * scale, frame[2] * scale, frame[3] * scale)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1 / scale, 1 / scale), clip=clip, alpha=False)
    return pix.tobytes("png"), (pix.width, pix.height)


def _normalize_frame(
    result: PageResult, *, frame: tuple[int, int, int, int], crop_size: tuple[int, int]
) -> tuple[list[Block], list[str]]:
    """框内归一：原始条目按声明坐标空间以**裁剪图尺寸**映射（PP-8），再平移回原页坐标系。

    平移在编排侧——归一化侧不知裁剪，`normalize/` 的坐标契约不为此扩参（PLAN-018 W1 裁决 1）。
    框内无结构化条目只回 markdown 时整幅回退单 paragraph，与 `normalize_page` 的整页判据同构。
    """
    blocks: list[Block] = []
    dropped: list[str] = []
    for raw in result.blocks:
        block = block_from_raw(
            raw,
            index=len(blocks),
            page_size=result.page_size,
            image_size=crop_size,
            space=result.bbox_space,
        )
        if block is None:
            dropped.append(str(raw.get("type", "")))
        else:
            blocks.append(block)
    if not result.blocks and result.markdown.strip():
        blocks = [
            Block(
                type=BlockType.PARAGRAPH,
                bbox=(0, 0, crop_size[0], crop_size[1]),
                index=0,
                order=0,
                text=result.markdown.strip(),
            )
        ]
    shifted: list[Block] = []
    for block in blocks:
        b0, b1, b2, b3 = block.bbox
        shifted.append(
            block.model_copy(update={"bbox": (b0 + frame[0], b1 + frame[1], b2 + frame[0], b3 + frame[1])})
        )
    return shifted, dropped


def _merge_frame_blocks(blocks: list[Block], *, frame: tuple[int, int, int, int]) -> Block:
    """一框至多一块：单存活块保持原类型；多块按 markdown 渲染拼接为 `paragraph`，bbox 取并集夹在框内。"""
    if len(blocks) == 1:
        return blocks[0]
    text = blocks_to_markdown(blocks).strip()
    union = (
        max(frame[0], min(b.bbox[0] for b in blocks)),
        max(frame[1], min(b.bbox[1] for b in blocks)),
        min(frame[2], max(b.bbox[2] for b in blocks)),
        min(frame[3], max(b.bbox[3] for b in blocks)),
    )
    return Block(type=BlockType.PARAGRAPH, bbox=union, index=0, order=0, text=text)


def rebuild_chunks(artifact_dir: Path, book_id: str, params: ChunkParams | None = None) -> ChunkSet:
    """从页产物重建书级分块（派生文件，可重复执行）。

    `artifact_dir` 为**一个产物作用域**的目录：解析写入时是版本目录，读取端在生效版本缺失时
    可传根视图目录。两者结构相同（`pages/p*.blocks.json` + `chunks.json`）。
    """
    pages: list[BlocksPage] = []
    for path in sorted((artifact_dir / "pages").glob("p*.blocks.json")):
        try:
            pages.append(BlocksPage.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError:
            logger.warning("跳过损坏的页产物：%s", path)
    chunk_set = build_chunks(pages, book_id=book_id, params=params)
    write_json(artifact_dir / "chunks.json", chunk_set_payload(chunk_set))
    return chunk_set


def _resumed_pages(
    page_repo: PageRepository, book_id: str, job_id: str, version_dir: Path, requested: list[int]
) -> set[int]:
    """本版本内可续跑的页：状态 `parsed` **且**产物在位。

    只认页行不够——`parsed` 行必然有产物是 `PP-13` 的不变式，但历史库（迁移前的 legacy 行）
    与人工清理过的目录都可能破坏它，产物缺失时宁可重解析也不给出空版本。
    """
    parsed = {row.page_no for row in page_repo.list_for_book(book_id, job_id) if row.status == "parsed"}
    return {
        page_no
        for page_no in requested
        if page_no in parsed and (version_dir / "pages" / f"p{page_no:04d}.blocks.json").is_file()
    }


def _page_windows(requested: list[int], size: int) -> list[tuple[int, int]]:
    """把全书请求页切成**串行提交**的连续窗口（1 基含两端）。

    按请求页而非待处理页切分：续跑已解析的页仍留在其所属窗口内，窗口边界与页号只由
    `requested + size` 决定，重跑同一任务时序列可复现。
    """
    if size <= 0:
        raise ValueError(f"窗口页数必须为正整数，当前为 {size}")
    return [(requested[i], requested[min(i + size, len(requested)) - 1]) for i in range(0, len(requested), size)]


def _retire_replaced_version(
    *,
    data_root: Path | str,
    domain_id: str,
    course_id: str,
    book_id: str,
    replaced_job_id: str | None,
    new_job_id: str,
) -> None:
    """激活成功后删除被替换的旧版本目录（REQ-097 / PLAN-017 W4）：磁盘上只留生效版本。

    只在指针切换并提交之后调用：删除失败最多留残磁盘，切换前删除会让指针指向已删目录。
    `legacy`（根视图，无版本目录）与被替换版本恰为本版本（同 job 续跑）都不删。
    读取中的调用方持有页文件时（Windows 共享违例）删除失败 → 只告警保留目录，
    任务终态不翻转：旧版本已非生效视图，残留无误读风险，可事后清理。
    """
    if not replaced_job_id or replaced_job_id in (new_job_id, LEGACY_JOB_ID):
        return
    target = version_dir_for(data_root, domain_id, course_id, book_id, replaced_job_id)
    if not target.is_dir():
        return
    try:
        shutil.rmtree(target)
    except OSError as exc:
        logger.warning("旧版本目录删除失败（可能正被读取持有），保留待清理：%s：%s", target, exc)
        return
    logger.info("版本 %s 激活，已删除被替换的旧版本目录：%s", new_job_id, target)


def _version_is_complete(
    page_repo: PageRepository, book_id: str, job_id: str, version_dir: Path, requested: list[int]
) -> bool:
    """激活门槛的「页数齐备」判据：本次任务目标页全部 `parsed` 且产物在位。"""
    return len(_resumed_pages(page_repo, book_id, job_id, version_dir, requested)) == len(requested)


def _fail(
    session: Session,
    job_id: str,
    book_id: str,
    message: str,
    pages_done: int = 0,
    progress: dict[str, object] | None = None,
    *,
    persist: bool = True,
    failed_pages: list[int] | None = None,
) -> ParseOutcome:
    """终态 failed：先提交已累计页行，再写 progress 与终态。

    先提交的意义：终态写入若失败或被外层回滚，也不能带走已解析页的行（`PP-13`）。
    """
    if persist:
        session.commit()
    jobs = OperationRepository(session)
    if progress is not None:
        jobs.update_progress(job_id, progress)
    jobs.mark_finished(job_id, status=JobStatus.FAILED, error=message, failed_pages=failed_pages)
    BookRepository(session).update_parse(
        book_id,
        parse_status=ParseStatus.PENDING,
        pages_done=pages_done,
        last_parse_result=LastParseResult.FAILED,
    )
    if persist:
        session.commit()
    return ParseOutcome(job_id=job_id, status=JobStatus.FAILED, parsed=pages_done, total=0, error=message)


def _inherit_page_rows(
    page_repo: PageRepository,
    book_id: str,
    baseline_job_id: str,
    job_id: str,
    version_dir: Path,
    requested: list[int],
) -> int:
    """把基线作用域里产物已被继承的页行复制进本版本，返回复制条数。

    页行必须与版本产物同构：否则逐页重解析出的版本「文件齐、状态缺」，`pages_done` 与
    续跑判据都会低估真实内容。只复制产物确实在位的页，保持「有页行必有产物」不变式。
    """
    copied = 0
    skipped = set(requested)
    for row in page_repo.list_for_book(book_id, baseline_job_id):
        if row.status != "parsed" or row.page_no in skipped:
            continue
        if not (version_dir / "pages" / f"p{row.page_no:04d}.blocks.json").is_file():
            continue
        if page_repo.get(book_id, job_id, row.page_no) is not None:
            continue
        page_repo.upsert(
            book_id, job_id, row.page_no, status=row.status, source=row.source, quality=row.quality
        )
        copied += 1
    return copied


def _await_model_idle(
    engine: ParseEngine,
    *,
    interval: float = MODEL_GATE_INTERVAL_SECONDS,
    deadline: float = MODEL_GATE_DEADLINE_SECONDS,
) -> bool:
    """单一占用闸门：等待模型服务空闲后放行；等待超时返回 `False`。

    引擎不上报负载（`model_load()` 返回 `None`）即放行——探测不灵不等于占用，**不臆造阻塞**；
    真正的不可达由 `health()` 判定，两种语义不互相冒充（ARCH-017 / R1 窗口实证）。
    """
    load = engine.model_load()
    started = time.monotonic()
    while load is not None and load.busy:
        remaining = deadline - (time.monotonic() - started)
        if remaining <= 0:
            return False
        time.sleep(min(interval, remaining))
        load = engine.model_load()
    return True


def run_parse_job(
    *,
    session: Session,
    engine: ParseEngine,
    job_id: str,
    book_id: str,
    pages: list[int] | None,
    data_root: Path | str,
    dpi: int = DEFAULT_DPI,
    persist_per_page: bool = True,
    model_gate_interval: float = MODEL_GATE_INTERVAL_SECONDS,
    model_gate_deadline: float = MODEL_GATE_DEADLINE_SECONDS,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> ParseOutcome:
    """执行一次解析任务（同步；由 `orchestrator.jobs.JobRunner` 在后台线程调用）。

    全本（`pages is None`）按 `window_size` 页**串行分块**提交：适配器每次调用有独立 deadline，
    单块失败只损失该块页，进度按块可见。指定页时逐页提交，不做窗口切分。
    """
    books = BookRepository(session)
    jobs = OperationRepository(session)
    page_repo = PageRepository(session)

    book = books.get(book_id)
    if book is None:
        return _fail(session, job_id, book_id, f"书目不存在：{book_id}", persist=persist_per_page)
    book_dir = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
    version_dir = version_dir_for(data_root, book.domain_id, book.course_id, book_id, job_id)
    # 元数据事实源是 af_books（REQ-097 / PLAN-017 W1）：根视图 book.json 已退役，磁盘上
    # 任何旧副本都不再作数。页数/哈希/源路径不完整 = 未 ingest，按任务失败如实呈现。
    if not book.file_path:
        return _fail(
            session,
            job_id,
            book_id,
            f"尚未 ingest 或缺源文件路径：{book_id}",
            book.pages_done,
            persist=persist_per_page,
        )
    try:
        meta = BookMeta(
            book_id=book_id,
            title=book.title or "",
            authors=list(book.authors or []),
            page_count=book.page_count or 0,
            sha256=book.sha256,
            source_file=book.file_path,
            engine=engine.name,
            ingested_at=book.ingested_at,
        )
    except ValueError:
        return _fail(
            session,
            job_id,
            book_id,
            f"尚未 ingest 或 af_books 元数据不完整：{book_id}",
            book.pages_done,
            persist=persist_per_page,
        )

    if not _await_model_idle(
        engine, interval=model_gate_interval, deadline=model_gate_deadline
    ):
        return _fail(
            session,
            job_id,
            book_id,
            MODEL_BUSY.format(wait=model_gate_deadline),
            book.pages_done,
            persist=persist_per_page,
        )

    if not engine.health():
        return _fail(
            session,
            job_id,
            book_id,
            MODEL_UNAVAILABLE,
            book.pages_done,
            persist=persist_per_page,
        )

    jobs.mark_running(job_id)
    books.update_parse(book_id, parse_status=ParseStatus.PARSING, pages_done=book.pages_done)
    if persist_per_page:
        session.commit()

    source_pdf = resolve_source_pdf(data_root, book.file_path or meta.source_file)
    jobs.record_source_snapshot(job_id, source_sha256=sha256_file(source_pdf), page_count=meta.page_count)
    # 版本基线：新版本从当前生效作用域继承产物（active 为空 = 根视图/legacy 作用域）
    baseline_dir = book_dir
    baseline_job_id = LEGACY_JOB_ID
    # 被替换版本在指针被本任务改写之前就取好：激活后要按它删除旧版本目录（W4）
    replaced_job_id = book.active_job_id
    if book.active_job_id and book.active_job_id != job_id:
        candidate = version_dir_for(data_root, book.domain_id, book.course_id, book_id, book.active_job_id)
        if candidate.is_dir():
            baseline_dir = candidate
            baseline_job_id = book.active_job_id
    seed_version_from(baseline_dir, version_dir)
    # 版本 book.json 由本次任务从 af_books 生成（PP-15 / REQ-097 W3）：不继承基线旧副本——
    # 引擎名与 ingest 时刻必须与本版本自证一致，继承会带回上一版的陈旧值。
    write_book_meta(version_dir, meta)

    geometry = _pdf_geometry(source_pdf)
    frames_by_page = _region_frames(OperationRepository(session), book_id)
    requested_pages = list(pages) if pages else list(range(1, meta.page_count + 1))
    _inherit_page_rows(page_repo, book_id, baseline_job_id, job_id, version_dir, requested_pages)
    resumed = _resumed_pages(page_repo, book_id, job_id, version_dir, requested_pages)
    target_pages = [page_no for page_no in requested_pages if page_no not in resumed]
    if resumed:
        logger.info("parse job %s 续跑：跳过 %d 个已解析页", job_id, len(resumed))
    total = len(requested_pages)
    parsed = len(resumed)
    failed: list[int] = []
    progress: dict[str, object] = {"parsed": parsed, "total": total, "failed": 0, "error": ""}
    last_progress_at = time.monotonic()

    def _commit() -> None:
        if persist_per_page:
            session.commit()

    def _flush_progress(*, force: bool = False) -> None:
        """progress 节流：≥1s 或每 10 页覆写一次；force 用于需要立即可见的失败节点。"""
        nonlocal last_progress_at
        if not force:
            within_interval = time.monotonic() - last_progress_at < PROGRESS_MIN_INTERVAL_SECONDS
            if within_interval and parsed % PROGRESS_PAGE_INTERVAL:
                return
        jobs.update_progress(job_id, progress)
        last_progress_at = time.monotonic()
        _commit()

    def _mark_failed(page_no: int, error: str) -> None:
        failed.append(page_no)
        page_repo.upsert(book_id, job_id, page_no, status="failed", error=error)
        progress["failed"] = len(failed)
        progress["error"] = f"失败页：{','.join(str(no) for no in failed)}"[:200]
        _commit()
        _flush_progress(force=True)

    def _mark_window_failed(window_pages: list[int], error: str) -> None:
        """块级失败隔离：本块目标页逐页记 `failed`，终态错误文本保留块原因。

        不重试也不中断后续块：最坏挂钟 = 块数 × 单块超时，块级重试会把它翻倍；同 job 重跑即续跑。
        """
        for page_no in window_pages:
            _mark_failed(page_no, error)
        progress["error"] = error[:200]
        _flush_progress(force=True)

    def _handle(page_no: int, result: PageResult) -> None:
        nonlocal parsed
        image_size = _image_size(book_dir, page_no, geometry, dpi)
        native_text_length = geometry.get(page_no, (None, None))[1]
        page = normalize_page(
            result,
            source=engine.name,
            image_size=image_size,
            native_text_length=native_text_length,
        )
        # 页图由版本基线继承（见 `seed_version_from`），此处只需写归一化产物
        _write_page_artifacts(version_dir, page)  # 产物写先于页行提交：有页行必有产物
        page_repo.upsert(
            book_id,
            job_id,
            page_no,
            status="parsed",
            source=engine.name,
            quality=page.quality.model_dump() if page.quality else None,
        )
        parsed += 1
        progress["parsed"] = parsed
        _commit()
        _flush_progress()

    def _handle_region_page(page_no: int, page_frames: list[tuple[str, tuple[int, int, int, int]]]) -> None:
        """区域驱动页（PP-21）：逐框裁剪提交、一框至多一块；单框重试后仍失败**整页**判 `failed`。

        缺框的页不满足一框一块——半页产物不落盘（PP-13 的「有页行必有产物」由此保持）。
        """
        nonlocal parsed
        image_path = link_pages_into(book_dir / "pages", version_dir / "pages", page_no)
        if image_path is None:
            _mark_failed(page_no, "页图缺失")
            return
        image_bytes = image_path.read_bytes()
        image_size = _image_size(book_dir, page_no, geometry, dpi)
        blocks: list[Block] = []
        dropped: list[str] = []
        empty_frames = 0
        total_frames = len(page_frames)
        for ordinal, (region_id, raw_frame) in enumerate(page_frames, start=1):
            where = f"区域驱动第 {ordinal}/{total_frames} 框（{region_id}）"
            if not _await_model_idle(engine, interval=model_gate_interval, deadline=model_gate_deadline):
                _mark_failed(page_no, f"{where}：{MODEL_BUSY.format(wait=model_gate_deadline)}"[:500])
                return
            frame = _clamp_frame(raw_frame, image_size)
            try:
                crop_bytes, crop_size = _crop_page_image(image_bytes, frame)
                result = _parse_payload_with_retry(engine, crop_bytes, page_no)
            except (EngineError, ValueError) as exc:
                _mark_failed(page_no, f"{where}：{exc}"[:500])
                return
            frame_blocks, frame_dropped = _normalize_frame(result, frame=frame, crop_size=crop_size)
            dropped.extend(frame_dropped)
            if not frame_blocks:
                empty_frames += 1  # 空框不出块，但按可见计数暴露，不静默丢框
                continue
            blocks.append(_merge_frame_blocks(frame_blocks, frame=frame))
        final = [
            block.model_copy(update={"index": i, "order": i})
            for i, block in enumerate(sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0])))
        ]
        page = BlocksPage(
            page=page_no,
            image_size=image_size,
            source=engine.name,
            blocks=final,
            quality=compute_quality(
                final,
                native_text_length=geometry.get(page_no, (None, None))[1],
                dropped_literals=dropped,
            ).model_copy(update={"empty_region_count": empty_frames}),
        )
        _write_page_artifacts(version_dir, page)  # 产物写先于页行提交（PP-13）
        page_repo.upsert(
            book_id,
            job_id,
            page_no,
            status="parsed",
            source=engine.name,
            quality=page.quality.model_dump() if page.quality else None,
        )
        parsed += 1
        progress["parsed"] = parsed
        _commit()
        _flush_progress()

    try:
        if pages is None:
            pdf_bytes = source_pdf.read_bytes()
            # 带框页剔出窗口：窗口只对剩余页切分（PLAN-018 W1 裁决 4），带框页随后逐页区域驱动
            plain_targets = [no for no in requested_pages if no not in frames_by_page]
            for start, end in _page_windows(plain_targets, window_size):
                if not engine.health():
                    # 块间探活失败即止：拿剩余窗口继续提交只会把挂钟按块数翻倍（僵尸任务）
                    _mark_window_failed(
                        [no for no in target_pages if no >= start and no not in frames_by_page],
                        MODEL_UNAVAILABLE,
                    )
                    break
                window_pages = [
                    no for no in target_pages if start <= no <= end and no not in frames_by_page
                ]
                if not window_pages:
                    continue  # 整窗已在本次版本解析并落盘，无需重复提交
                try:
                    document: DocumentResult = engine.parse_document(
                        pdf_bytes, source_pdf.name, start_page=start, end_page=end
                    )
                except EngineError as exc:
                    _mark_window_failed(window_pages, f"窗口 {start}-{end}：{exc}"[:500])
                    continue
                _store_images(version_dir, document.images)
                by_page = {result.page: result for result in document.pages}
                for page_no in window_pages:
                    result = by_page.get(page_no)
                    if result is None:
                        _mark_failed(page_no, "引擎未返回该页")
                        continue
                    _handle(page_no, result)
            for page_no in target_pages:
                page_frames = frames_by_page.get(page_no)
                if page_frames is not None:
                    _handle_region_page(page_no, page_frames)
        else:
            for page_no in target_pages:
                page_frames = frames_by_page.get(page_no)
                if page_frames is not None:
                    _handle_region_page(page_no, page_frames)
                    continue
                image_path = link_pages_into(book_dir / "pages", version_dir / "pages", page_no)
                if image_path is None:
                    _mark_failed(page_no, "页图缺失")
                    continue
                try:
                    result = _parse_page_with_retry(engine, image_path, page_no)
                except EngineError as exc:
                    _mark_failed(page_no, str(exc))
                    continue
                _handle(page_no, result)
    except EngineError as exc:
        return _fail(
            session,
            job_id,
            book_id,
            str(exc)[:500],
            page_repo.count_parsed(book_id, job_id),
            progress,
            persist=persist_per_page,
            failed_pages=failed,
        )

    rebuild_chunks(version_dir, book_id)
    write_manifest(version_dir)

    pages_done = page_repo.count_parsed(book_id, job_id)
    # ADR 0007 决定 1：`completed` 与激活门槛是同一判据，二者不可能分叉
    complete = _version_is_complete(page_repo, book_id, job_id, version_dir, requested_pages)
    if complete:
        books.set_active_job(book_id, job_id)
    else:
        logger.warning("parse job %s 未激活：目标页未齐备（失败页 %s）", job_id, failed or "-")
    status = JobStatus.COMPLETED if complete else (JobStatus.PARTIAL if parsed else JobStatus.FAILED)
    progress["failed"] = len(failed)
    books.update_parse(
        book_id,
        parse_status=ParseStatus.PENDING,
        pages_done=pages_done,
        last_parse_result=LastParseResult(status.value),
    )
    jobs.mark_finished(
        job_id,
        status=status,
        error=str(progress["error"]),
        progress=progress,
        failed_pages=failed,
    )
    _commit()  # 终态与 progress 收尾必写：不依赖调用方提交
    if complete:
        # W4：指针已提交，旧版本从此刻起不再是生效视图——删除它才安全（顺序不可颠倒）。
        _retire_replaced_version(
            data_root=data_root,
            domain_id=book.domain_id,
            course_id=book.course_id,
            book_id=book_id,
            replaced_job_id=replaced_job_id,
            new_job_id=job_id,
        )
    return ParseOutcome(
        job_id=job_id,
        status=status,
        parsed=parsed,
        total=total,
        failed_pages=failed,
        error=str(progress["error"]),
    )
