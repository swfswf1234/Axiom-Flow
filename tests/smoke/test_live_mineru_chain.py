"""真机冒烟：MinerU 3.4.4 两条提交形状（逐页 PNG + 整本 PDF）可重复验证。

守护面：PLAN-003 W8 8.1——把一次性取证脚本（`tmp/live_probe_*.py`）固化为可重跑用例；
逐页路径覆盖 `parse_page` 的 PNG 提交形状与归一化产出，整本路径覆盖 `run_parse_job` 的
`pages=None` 分支、页级持久化与**版本目录落盘 + 终态激活**（PLAN-004：产物只写
`versions/<job_id>/`，根视图不得被解析写回）。

隔离：合成 PDF 与数据根/数据库全部位于 pytest `tmp_path`，不读写真实 `QED_DATA_ROOT`、
不写共享 `qed` 库；只连本机回环上的模型服务。默认门禁跳过，需 `QED_SMOKE_LIVE=1` 显式开启
（与 W3 的 `QED_TEST_MYSQL=1` 同精神）；开启后模型不可达即**失败**，不静默跳过。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select
from sqlalchemy.engine import create_engine
from sqlalchemy.orm import Session

from axiom_flow.config import load_settings
from axiom_flow.db.models import AfBook, AfPage, AfParseJob, Base
from axiom_flow.db.repositories import (
    BookRepository,
    BookSyncRow,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.factory import build_engine
from axiom_flow.ingest import ingest_book
from axiom_flow.ingest.artifacts import book_dir_for, png_size, version_dir_for
from axiom_flow.normalize.blocks import normalize_page
from axiom_flow.orchestrator.pipeline import run_parse_job
from axiom_flow.schemas import JobStatus, LastParseResult, ParseStatus

pytestmark = pytest.mark.skipif(
    os.getenv("QED_SMOKE_LIVE") != "1",
    reason="真机冒烟需显式开启（QED_SMOKE_LIVE=1）：默认门禁不调用外部模型服务",
)

DOMAIN_ID = "smoke"
COURSE_ID = "live"
HEADING_MARK = "HEADING"
ALIGN_MIN_IOU = 0.5


def _pdf_text_rect(
    pdf_path: Path, *, page_no: int, image_size: tuple[int, int], needle: str
) -> tuple[int, int, int, int] | None:
    """PDF 原生文本矩形折算到页图像素，作为「块是否贴住原文字」的判据基准。

    基准只依赖 PDF 点坐标与页图像素尺寸，不依赖引擎输出，因此能独立检出坐标空间错配（DEFECT-004）。
    """
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_no - 1]
        scale_x = image_size[0] / page.rect.width
        scale_y = image_size[1] / page.rect.height
        for block in page.get_text("blocks"):
            if needle in str(block[4]):
                return (
                    round(block[0] * scale_x),
                    round(block[1] * scale_y),
                    round(block[2] * scale_x),
                    round(block[3] * scale_y),
                )
    finally:
        doc.close()
    return None


def _iou(a: tuple[int, int, int, int], b: tuple[float, float, float, float]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def assert_heading_aligned(
    blocks: list[tuple[str | None, tuple[float, float, float, float]]],
    *,
    pdf_path: Path,
    page_no: int,
    image_size: tuple[int, int],
) -> None:
    """页内标题块必须贴住 PDF 原生文字位置：坐标空间错配时 IoU 会跌到近 0（x 轴过放 1.68 倍）。

    `blocks` 为 `(文本, bbox)` 序列，便于同时覆盖归一化对象与页产物 `blocks.json` 两条读法。
    """
    expected = _pdf_text_rect(pdf_path, page_no=page_no, image_size=image_size, needle=HEADING_MARK)
    assert expected is not None, f"样本 PDF 第 {page_no} 页缺少标题行，判据失效"
    matched = [bbox for text, bbox in blocks if HEADING_MARK in (text or "").upper()]
    assert matched, f"第 {page_no} 页未解析出标题块，无法判定坐标对齐"
    best = max(_iou(bbox, expected) for bbox in matched)
    assert best >= ALIGN_MIN_IOU, (
        f"第 {page_no} 页标题块与原文字不对齐（IoU={best:.3f}）："
        f"parsed={list(matched[0])} expected={list(expected)}"
    )



def _build_pdf(path: Path, *, pages: int) -> None:
    """生成含已知文本行的合成 PDF（不依赖仓库外样本，样本目录在 .gitignore 内）。"""
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 80), f"SMOKE PAGE {index + 1} HEADING", fontsize=18, fontname="helv")
        for row in range(6):
            page.insert_text(
                (60, 140 + row * 45),
                f"Smoke sample line {row + 1} on page {index + 1} for parsing verification.",
                fontsize=12,
                fontname="helv",
            )
    doc.save(path)
    doc.close()


@pytest.fixture(scope="module")
def live_engine():
    """真机引擎适配器：开启冒烟即要求模型服务在线，否则显式失败。"""
    settings = load_settings()
    adapter = build_engine("mineru", settings)
    try:
        if not adapter.health():
            pytest.fail(f"真机冒烟前置失败：MinerU 不可达（{settings.ocr_model_url} /health）")
        yield adapter
    finally:
        adapter.close()


@pytest.fixture
def smoke_session(tmp_path):
    """仓库外临时 SQLite 文件库（隔离铁律：不触碰共享 MySQL）。"""
    db_engine = create_engine(f"sqlite:///{(tmp_path / 'smoke.db').as_posix()}")
    Base.metadata.create_all(db_engine)
    with Session(db_engine) as session:
        yield session
    db_engine.dispose()


def _prepare_book(*, session: Session, data_root: Path, book_id: str, pages: int) -> int:
    raw_dir = data_root / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _build_pdf(raw_dir / f"{book_id}.pdf", pages=pages)
    BookRepository(session).upsert_many([
        BookSyncRow(
            book_id=book_id,
            domain_id=DOMAIN_ID,
            course_id=COURSE_ID,
            course_name="真机冒烟",
            knowledge_id="",
            title=f"Smoke {book_id} ({pages}p)",
            part="",
            display_title=f"Smoke {book_id} ({pages}p)",
            authors=[{"name": "smoke", "role": "author"}],
            file_path=f"raw/{book_id}.pdf",
        )
    ])
    session.commit()
    result = ingest_book(
        book_id=book_id,
        source_file=f"raw/{book_id}.pdf",
        data_root=data_root,
        domain_id=DOMAIN_ID,
        course_id=COURSE_ID,
    )
    BookRepository(session).update_ingest(book_id, page_count=result.page_count, sha256=result.sha256)
    session.commit()
    return result.page_count


def test_smoke_single_page_png_shape(tmp_path, smoke_session, live_engine) -> None:
    """逐页路径：页图 PNG 提交形状成立（DEFECT-002 的 legacy 404 根因回归对照）。"""
    book_id = "smoke-single"
    assert _prepare_book(session=smoke_session, data_root=tmp_path, book_id=book_id, pages=1) == 1

    page_png = book_dir_for(tmp_path, DOMAIN_ID, COURSE_ID, book_id) / "pages" / "p0001.png"
    image_size = png_size(page_png)
    result = live_engine.parse_page(page_png.read_bytes(), 1)

    assert result.page == 1
    assert result.blocks, "引擎未返回 content_list 块（return_content_list 开关失效？）"
    page = normalize_page(result, source="mineru", image_size=image_size)
    assert page.image_size == image_size
    assert page.blocks, "归一化后无块：引擎原始块全部被丢弃"
    # 归一化对账不变式（DEFECT-005）：原始条目数 = 存活块数 + 可见丢弃数。
    assert len(result.blocks) == len(page.blocks) + page.quality.dropped_block_count, (
        f"原始 {len(result.blocks)} 项 ≠ 存活 {len(page.blocks)} 块 + 丢弃 "
        f"{page.quality.dropped_block_count} 块：归一化对账不成立"
    )
    assert page.quality.dropped_block_count == 0, (
        f"真机字面值未被映射表覆盖，需人工判定映射缺口：{page.quality.dropped_types}"
    )
    assert any("SMOKE" in (block.text or "").upper() for block in page.blocks)
    for block in page.blocks:
        x0, y0, x1, y1 = block.bbox
        assert 0 <= x0 < x1 <= image_size[0]
        assert 0 <= y0 < y1 <= image_size[1]
    assert_heading_aligned(
        [(b.text, b.bbox) for b in page.blocks],
        pdf_path=tmp_path / "raw" / f"{book_id}.pdf",
        page_no=1,
        image_size=image_size,
    )


def test_smoke_full_document_five_pages(tmp_path, smoke_session, live_engine) -> None:
    """整本路径：`pages=None` 一次上传 → 5/5 页行 parsed + 产物齐套。"""
    book_id = "smoke-full"
    job_id = "smokefulljob01"
    assert _prepare_book(session=smoke_session, data_root=tmp_path, book_id=book_id, pages=5) == 5

    ParseJobRepository(smoke_session).create(ParseJobRow(
        job_id=job_id,
        book_id=book_id,
        pages=None,
        engine="mineru",
        status=JobStatus.QUEUED,
        progress={"parsed": 0, "total": 0, "error": ""},
    ))
    smoke_session.commit()

    outcome = run_parse_job(
        session=smoke_session,
        engine=live_engine,
        job_id=job_id,
        book_id=book_id,
        pages=None,
        data_root=tmp_path,
    )
    smoke_session.commit()

    assert outcome.status == JobStatus.COMPLETED
    assert (outcome.parsed, outcome.total) == (5, 5)
    assert outcome.failed_pages == []

    job = smoke_session.get(AfParseJob, job_id)
    assert job.status == JobStatus.COMPLETED.value
    assert job.engine == "mineru"
    assert job.progress == {"parsed": 5, "total": 5, "failed": 0, "error": ""}

    rows = list(smoke_session.scalars(select(AfPage).where(AfPage.book_id == book_id).order_by(AfPage.page_no)))
    assert [row.status for row in rows] == ["parsed"] * 5
    assert all(row.job_id == job_id for row in rows), "页行必须落在本 job 的版本作用域内（PLAN-004 V7）"
    assert PageRepository(smoke_session).count_parsed(book_id, job_id) == 5
    book = smoke_session.get(AfBook, book_id)
    assert book.parse_status == ParseStatus.PENDING.value, "进度字段不得兼任结局（ADR 0007 决定 3 / `PM-10`）"
    assert book.last_parse_result == LastParseResult.COMPLETED.value
    assert book.active_job_id == job_id, (
        "completed 且目标页齐备必须激活生效指针（PLAN-004 V3）"
    )

    # 产物只写版本目录（PLAN-004 V1）；根目录只留 ingest 级页图正本，不写 blocks/md/chunks
    version_dir = version_dir_for(tmp_path, DOMAIN_ID, COURSE_ID, book_id, job_id)
    book_dir = book_dir_for(tmp_path, DOMAIN_ID, COURSE_ID, book_id)
    assert not (book_dir / "chunks.json").exists(), "解析产物不得写回根视图"
    for page_no in range(1, 6):
        assert (version_dir / "pages" / f"p{page_no:04d}.md").is_file()
        blocks_file = version_dir / "pages" / f"p{page_no:04d}.blocks.json"
        assert blocks_file.is_file()
        payload = json.loads(blocks_file.read_text(encoding="utf-8"))
        assert_heading_aligned(
            [(b.get("text"), b["bbox"]) for b in payload["blocks"]],
            pdf_path=tmp_path / "raw" / f"{book_id}.pdf",
            page_no=page_no,
            image_size=tuple(payload["image_size"]),
        )
    assert (version_dir / "chunks.json").is_file()
    manifest = version_dir / "manifest.json"
    assert manifest.is_file()

    manifest_entries = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(manifest_entries) >= 5 * 3 + 2
    assert not (tmp_path / "tmp").exists()
