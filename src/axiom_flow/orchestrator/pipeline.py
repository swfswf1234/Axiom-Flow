"""解析任务编排：全本（默认）/逐页 → 引擎 → 归一化 → 分块 → 落盘 → 回写 af_*。

设计事实源：`docs/design/parsing-pipeline.md`。失败语义：
- 模型服务不可用（提交前探活失败）→ 任务 `failed`，不中断其他任务；
- 单页失败（重试 1 次仍失败）→ 该页 `failed`，任务继续，汇总报告列出失败页；
- 整本解析失败（引擎异常）→ 任务 `failed`，已解析页保留（可重跑）。

持久化不变式（PLAN-003 条文 3）：**产物写先于页行提交**，故「有页行必有产物」；每页成功/失败即
提交，使非 `EngineError` 异常穿透后台线程时前序页不被回滚吞掉；`progress` 按「距上次写入 ≥1s
或每 10 页」节流，终态必写一次。`persist_per_page=False` 仅供性能排查，退回调用方统一提交。
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from sqlalchemy.orm import Session

from axiom_flow.db.repositories import BookRepository, PageRepository, ParseJobRepository
from axiom_flow.engines.base import DocumentResult, EngineError, PageResult, ParseEngine
from axiom_flow.ingest import (
    atomic_write_bytes,
    book_dir_for,
    ensure_within,
    png_size,
    read_book_meta,
    resolve_source_pdf,
    write_json,
    write_manifest,
    write_page_markdown,
)
from axiom_flow.normalize.blocks import blocks_to_markdown, normalize_page
from axiom_flow.normalize.chunking import build_chunks, chunk_set_payload
from axiom_flow.schemas import BlocksPage, ChunkParams, ChunkSet, JobStatus, ParseStatus

logger = logging.getLogger(__name__)

DEFAULT_DPI = 150
PAGE_ATTEMPTS = 2  # 首次 + 重试 1 次
PROGRESS_MIN_INTERVAL_SECONDS = 1.0  # progress 写入的最小时间间隔
PROGRESS_PAGE_INTERVAL = 10  # 或每处理这么多页写一次

# 模型服务不可用的统一措辞：提交期 503 与执行期 job failed 共用（两段语义，同一文案）。
MODEL_UNAVAILABLE = "模型服务不可用：请经 8900 /models/{slot} 启停模型后重试"


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


def _store_images(book_dir: Path, images: dict[str, bytes]) -> None:
    """文档内嵌图片落盘到 `pages/<相对路径>`（拒绝目录穿越，与 ingest 同一道数据根闸）。"""
    pages_root = book_dir / "pages"
    for relative, payload in images.items():
        try:
            target = ensure_within(pages_root, pages_root / relative)
        except ValueError:
            logger.warning("跳过越界图片路径：%s", relative)
            continue
        atomic_write_bytes(target, payload)


def _write_page_artifacts(book_dir: Path, page: BlocksPage) -> None:
    write_json(book_dir / "pages" / f"p{page.page:04d}.blocks.json", page.model_dump_json(indent=2))
    write_page_markdown(book_dir, page.page, blocks_to_markdown(page.blocks))


def _parse_page_with_retry(engine: ParseEngine, image_path: Path, page_no: int) -> PageResult:
    last_error: EngineError | None = None
    for _ in range(PAGE_ATTEMPTS):
        try:
            return engine.parse_page(image_path.read_bytes(), page_no)
        except EngineError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def rebuild_chunks(book_dir: Path, book_id: str, params: ChunkParams | None = None) -> ChunkSet:
    """从页产物重建书级分块（派生文件，可重复执行）。"""
    pages: list[BlocksPage] = []
    for path in sorted((book_dir / "pages").glob("p*.blocks.json")):
        try:
            pages.append(BlocksPage.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError:
            logger.warning("跳过损坏的页产物：%s", path)
    chunk_set = build_chunks(pages, book_id=book_id, params=params)
    write_json(book_dir / "chunks.json", chunk_set_payload(chunk_set))
    return chunk_set


def _fail(
    session: Session,
    job_id: str,
    book_id: str,
    message: str,
    pages_done: int = 0,
    progress: dict[str, object] | None = None,
    *,
    persist: bool = True,
) -> ParseOutcome:
    """终态 failed：先提交已累计页行，再写 progress 与终态。

    先提交的意义：终态写入若失败或被外层回滚，也不能带走已解析页的行（条文 3）。
    """
    if persist:
        session.commit()
    jobs = ParseJobRepository(session)
    if progress is not None:
        jobs.update_progress(job_id, progress)
    jobs.mark_finished(job_id, status=JobStatus.FAILED, error=message)
    BookRepository(session).update_parse(book_id, parse_status=ParseStatus.FAILED, pages_done=pages_done)
    if persist:
        session.commit()
    return ParseOutcome(job_id=job_id, status=JobStatus.FAILED, parsed=pages_done, total=0, error=message)


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
) -> ParseOutcome:
    """执行一次解析任务（同步；由 `orchestrator.jobs.JobRunner` 在后台线程调用）。"""
    books = BookRepository(session)
    jobs = ParseJobRepository(session)
    page_repo = PageRepository(session)

    book = books.get(book_id)
    if book is None:
        return _fail(session, job_id, book_id, f"书目不存在：{book_id}", persist=persist_per_page)
    book_dir = book_dir_for(data_root, book.domain_id, book.course_id, book_id)
    try:
        meta = read_book_meta(book_dir)
    except (OSError, ValueError):
        return _fail(
            session,
            job_id,
            book_id,
            f"尚未 ingest 或产物缺失：{book_id}",
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
    geometry = _pdf_geometry(source_pdf)
    target_pages = list(pages) if pages else list(range(1, meta.page_count + 1))
    total = len(target_pages)
    parsed = 0
    failed: list[int] = []
    progress: dict[str, object] = {"parsed": 0, "total": total, "error": ""}
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
        page_repo.upsert(book_id, page_no, status="failed", error=error)
        progress["error"] = f"失败页：{','.join(str(no) for no in failed)}"[:200]
        _commit()
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
        _write_page_artifacts(book_dir, page)  # 产物写先于页行提交：有页行必有产物
        page_repo.upsert(
            book_id,
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
            document: DocumentResult = engine.parse_document(source_pdf.read_bytes(), source_pdf.name)
            _store_images(book_dir, document.images)
            by_page = {result.page: result for result in document.pages}
            for page_no in target_pages:
                result = by_page.get(page_no)
                if result is None:
                    _mark_failed(page_no, "引擎未返回该页")
                    continue
                _handle(page_no, result)
        else:
            for page_no in target_pages:
                image_path = book_dir / "pages" / f"p{page_no:04d}.png"
                if not image_path.is_file():
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
            page_repo.count_parsed(book_id),
            progress,
            persist=persist_per_page,
        )

    rebuild_chunks(book_dir, book_id)
    write_manifest(book_dir)

    pages_done = page_repo.count_parsed(book_id)
    parse_status = ParseStatus.COMPLETED if not failed else ParseStatus.FAILED
    books.update_parse(book_id, parse_status=parse_status, pages_done=pages_done)
    jobs.mark_finished(
        job_id,
        status=JobStatus.COMPLETED if parsed else JobStatus.FAILED,
        error=str(progress["error"]),
        progress=progress,
    )
    _commit()  # 终态与 progress 收尾必写：不依赖调用方提交
    return ParseOutcome(
        job_id=job_id,
        status=JobStatus.COMPLETED if parsed else JobStatus.FAILED,
        parsed=parsed,
        total=total,
        failed_pages=failed,
        error=str(progress["error"]),
    )
