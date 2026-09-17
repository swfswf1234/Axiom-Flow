"""解析编排单测：全本/逐页、失败语义、af_* 回写与分块重建（SQLite + 假引擎，不调模型）。

守护面（docs/design/parsing-pipeline.md）：默认全本、指定页逐页、单页失败不中断、
模型不可用任务失败、产物三件套 + chunks.json + manifest.json、进度回写。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from axiom_flow.db.models import Base
from axiom_flow.db.repositories import BookRepository, BookSyncRow, PageRepository, ParseJobRepository, ParseJobRow
from axiom_flow.engines.base import DocumentResult, EngineError, PageResult
from axiom_flow.ingest import ingest_book
from axiom_flow.orchestrator import run_parse_job
from axiom_flow.schemas import BlocksPage


def _make_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), "orchestrator test")
        doc.save(path)
    finally:
        doc.close()
    return path


class FakeEngine:
    """假引擎：全本两页；单页按页码返回。"""

    name = "mineru"

    def __init__(self, *, healthy: bool = True, fail_pages: set[int] | None = None, fail_document: bool = False):
        self.healthy = healthy
        self.fail_pages = fail_pages or set()
        self.fail_document = fail_document

    def health(self) -> bool:
        return self.healthy

    def parse_document(self, pdf_bytes: bytes, filename: str) -> DocumentResult:
        if self.fail_document:
            raise EngineError("MinerU 服务不可达：connection refused")
        return DocumentResult(
            pages=[
                PageResult(
                    page=1,
                    blocks=[{"type": "title", "text": "第 1 章", "text_level": 1, "bbox": [72, 54, 540, 80]}],
                    page_size=(595.0, 842.0),
                ),
                PageResult(
                    page=2,
                    blocks=[{"type": "text", "text": "正文二", "bbox": [72, 96, 540, 132]}],
                    page_size=(595.0, 842.0),
                ),
            ]
        )

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
        if page_no in self.fail_pages:
            raise EngineError(f"第 {page_no} 页解析失败")
        return PageResult(
            page=page_no,
            blocks=[{"type": "text", "text": f"第 {page_no} 页", "bbox": [72, 96, 540, 132]}],
            page_size=(595.0, 842.0),
        )


@pytest.fixture()
def env(tmp_path):
    """临时数据根 + SQLite 内存库 + 已 ingest 的书。"""
    data_root = tmp_path / "data"
    raw_dir = data_root / "raw" / "math-advanced" / "math_analysis"
    raw_dir.mkdir(parents=True)
    _make_pdf(raw_dir / "book.pdf", pages=2)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        books = BookRepository(session)
        books.upsert_many(
            [
                BookSyncRow(
                    book_id="b05",
                    domain_id="math-advanced",
                    course_id="math_analysis",
                    title="数学分析原理",
                    file_path="raw/math-advanced/math_analysis/book.pdf",
                )
            ]
        )
        session.commit()
        yield session, data_root


def _create_job(session: Session, *, pages: list[int] | None = None) -> str:
    job_id = "job-1"
    ParseJobRepository(session).create(ParseJobRow(job_id=job_id, book_id="b05", pages=pages))
    session.commit()
    return job_id


def _ingest(session: Session, data_root: Path) -> None:
    result = ingest_book(
        book_id="b05",
        source_file="raw/math-advanced/math_analysis/book.pdf",
        data_root=data_root,
        domain_id="math-advanced",
        course_id="math_analysis",
        title="数学分析原理",
    )
    BookRepository(session).update_ingest("b05", page_count=result.page_count, sha256=result.sha256)
    session.commit()


def _book_dir(data_root: Path) -> Path:
    return data_root / "parsed" / "math-advanced" / "math_analysis" / "b05"


class TestWholeBookJob:
    """默认全本：一次整本解析 → 逐页产物 + 分块 + manifest + 回写。"""

    def test_completes_and_writes_artifacts(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "completed"
        assert (outcome.parsed, outcome.total, outcome.failed_pages) == (2, 2, [])
        book_dir = _book_dir(data_root)
        assert (book_dir / "pages" / "p0001.blocks.json").is_file()
        assert (book_dir / "pages" / "p0001.md").is_file()
        assert (book_dir / "chunks.json").is_file()
        assert (book_dir / "manifest.json").is_file()
        chunk_set = json.loads((book_dir / "chunks.json").read_text(encoding="utf-8"))
        assert chunk_set["chunks"][0]["block_refs"][0]["page"] == 1
        page = BlocksPage.model_validate_json((book_dir / "pages" / "p0001.blocks.json").read_text(encoding="utf-8"))
        assert page.source == "mineru"
        assert page.blocks[0].type.value == "heading"

        book = BookRepository(session).get("b05")
        assert book is not None and book.parse_status == "completed" and book.pages_done == 2
        job = ParseJobRepository(session).get(job_id)
        assert job is not None and job.status == "completed"
        assert PageRepository(session).count_parsed("b05") == 2

    def test_engine_error_fails_job(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(fail_document=True),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "failed"
        assert "不可达" in outcome.error
        book = BookRepository(session).get("b05")
        assert book is not None and book.parse_status == "failed"

    def test_unhealthy_engine_fails_job_before_calling(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(healthy=False),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "failed"
        assert "模型服务不可用" in outcome.error

    def test_missing_ingest_fails_job(self, env):
        session, data_root = env
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "failed"
        assert "尚未 ingest" in outcome.error

    def test_missing_book_fails_job(self, env):
        session, data_root = env
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="nope",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "failed"
        assert "书目不存在" in outcome.error


class TestPerPageJob:
    """指定页逐页：失败页不中断，汇总报告列出失败页。"""

    def test_single_page_reparse(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[2])

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=[2],
            data_root=data_root,
        )

        assert outcome.status == "completed"
        assert outcome.parsed == 1
        page = BlocksPage.model_validate_json(
            (_book_dir(data_root) / "pages" / "p0002.blocks.json").read_text(encoding="utf-8")
        )
        assert page.blocks[0].text == "第 2 页"

    def test_failed_page_does_not_abort_job(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1, 2])

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(fail_pages={2}),
            job_id=job_id,
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
        )

        assert outcome.status == "completed"
        assert outcome.failed_pages == [2]
        assert "失败页：2" in outcome.error
        assert PageRepository(session).get("b05", 2).status == "failed"
        book = BookRepository(session).get("b05")
        assert book is not None and book.parse_status == "failed" and book.pages_done == 1

    def test_missing_page_image_marks_failed(self, env):
        session, data_root = env
        _ingest(session, data_root)
        (_book_dir(data_root) / "pages" / "p0002.png").unlink()
        job_id = _create_job(session, pages=[2])

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=[2],
            data_root=data_root,
        )

        assert outcome.failed_pages == [2]
        assert PageRepository(session).get("b05", 2).error == "页图缺失"
