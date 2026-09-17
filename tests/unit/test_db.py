"""af_* 表模型与仓储层单测（SQLite 内存，不依赖 MySQL，符合测试隔离铁律）。

守护面：四表结构与设计一致（表名/主键/唯一键）、同步幂等与快照语义、任务/页/编辑状态回写。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from axiom_flow.db.models import AF_TABLES, Base
from axiom_flow.db.repositories import (
    BookRepository,
    BookSyncRow,
    EditRepository,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
    edit_id_for,
)


@pytest.fixture()
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def session(engine):
    with Session(engine) as session:
        yield session


def _row(book_id: str = "mathanalysis-b05", **overrides) -> BookSyncRow:
    payload = {
        "book_id": book_id,
        "domain_id": "math-advanced",
        "course_id": "math_analysis",
        "course_name": "数学分析",
        "knowledge_id": "kt-mathanalysis-3",
        "title": "数学分析原理",
        "part": "教程",
        "display_title": "Rudin 数学分析原理",
        "authors": [{"name": "Walter Rudin", "role": "author"}],
        "file_path": "raw/math-advanced/math_analysis/rudin_ab12cd34.pdf",
    }
    payload.update(overrides)
    return BookSyncRow(**payload)


class TestTableStructure:
    """四表结构：表名与设计（database-design.md）一致。"""

    def test_four_tables_created(self, engine):
        assert set(inspect(engine).get_table_names()) == {
            "af_books",
            "af_parse_jobs",
            "af_pages",
            "af_block_edits",
        }

    def test_af_pages_composite_primary_key(self, engine):
        pk = inspect(engine).get_pk_constraint("af_pages")
        assert pk["constrained_columns"] == ["book_id", "page_no"]

    def test_af_block_edits_unique_position(self, engine):
        uniques = inspect(engine).get_unique_constraints("af_block_edits")
        assert any(u["column_names"] == ["book_id", "page_no", "block_index"] for u in uniques)

    def test_af_tables_registry_matches(self):
        assert {table.name for table in AF_TABLES} == {
            "af_books",
            "af_parse_jobs",
            "af_pages",
            "af_block_edits",
        }


class TestBookRepository:
    """af_books：同步幂等、快照语义与进度回写。"""

    def test_sync_inserts_then_idempotent(self, session):
        repo = BookRepository(session)

        first = repo.upsert_many([_row("b05"), _row("b11")])
        second = repo.upsert_many([_row("b05"), _row("b11")])

        assert (first.synced, first.updated) == (2, 0)
        assert (second.synced, second.updated) == (0, 2)
        assert len(repo.list_books()) == 2

    def test_sync_updates_metadata_but_preserves_progress(self, session):
        repo = BookRepository(session)
        repo.upsert_many([_row()])
        repo.update_ingest("mathanalysis-b05", page_count=320, sha256="a" * 64)
        repo.update_parse("mathanalysis-b05", parse_status="parsing", pages_done=12)
        repo.update_notes("mathanalysis-b05", "重点核对第 3 章")

        repo.upsert_many([_row(title="数学分析原理（第 3 版）", display_title="Rudin 数学分析原理")])

        book = repo.get("mathanalysis-b05")
        assert book is not None
        assert book.title == "数学分析原理（第 3 版）"
        assert book.page_count == 320
        assert book.sha256 == "a" * 64
        assert book.parse_status == "parsing"
        assert book.pages_done == 12
        assert book.notes == "重点核对第 3 章"

    def test_update_missing_book_returns_false(self, session):
        repo = BookRepository(session)

        assert repo.update_ingest("nope", page_count=1, sha256="0" * 64) is False
        assert repo.update_parse("nope", parse_status="failed", pages_done=0) is False
        assert repo.update_notes("nope", "x") is False
        assert repo.delete("nope") is False

    def test_delete_removes_row(self, session):
        repo = BookRepository(session)
        repo.upsert_many([_row()])

        assert repo.delete("mathanalysis-b05") is True
        assert repo.get("mathanalysis-b05") is None


class TestParseJobRepository:
    """af_parse_jobs：任务状态机与进度。"""

    def test_job_lifecycle(self, session):
        repo = ParseJobRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", pages=None, progress={"parsed": 0, "total": 320}))

        assert repo.mark_running("job1") is True
        assert repo.update_progress("job1", {"parsed": 5, "total": 320}) is True
        assert repo.mark_finished("job1", status="completed", progress={"parsed": 320, "total": 320}) is True

        job = repo.get("job1")
        assert job is not None
        assert job.status == "completed"
        assert job.pages is None
        assert job.progress == {"parsed": 320, "total": 320}
        assert job.started_at is not None and job.finished_at is not None

    def test_pages_array_round_trips(self, session):
        repo = ParseJobRepository(session)
        repo.create(ParseJobRow(job_id="job2", book_id="b05", pages=[1, 3, 5]))

        job = repo.get("job2")
        assert job is not None and job.pages == [1, 3, 5]

    def test_list_for_book_and_missing_updates(self, session):
        repo = ParseJobRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05"))
        repo.create(ParseJobRow(job_id="job2", book_id="b11"))

        assert [job.job_id for job in repo.list_for_book("b05")] == ["job1"]
        assert repo.mark_running("nope") is False
        assert repo.mark_finished("nope", status="failed") is False


class TestPageRepository:
    """af_pages：页状态 upsert 与质量信号。"""

    def test_upsert_page_and_count_parsed(self, session):
        repo = PageRepository(session)
        repo.upsert("b05", 1, status="pending")
        repo.upsert("b05", 2, status="failed", error="超时")
        repo.upsert("b05", 3, status="parsed", source="mineru", quality={"empty_block_ratio": 0.0})

        page = repo.get("b05", 3)
        assert page is not None
        assert page.source == "mineru"
        assert page.quality == {"empty_block_ratio": 0.0}
        assert page.parsed_at is not None
        assert repo.count_parsed("b05") == 1
        assert [p.page_no for p in repo.list_for_book("b05")] == [1, 2, 3]

    def test_upsert_same_page_overwrites(self, session):
        repo = PageRepository(session)
        repo.upsert("b05", 1, status="failed", error="超时")
        repo.upsert("b05", 1, status="parsed", source="mineru")

        assert repo.get("b05", 1).status == "parsed"
        assert len(repo.list_for_book("b05")) == 1


class TestEditRepository:
    """af_block_edits：同块重复编辑覆盖更新（幂等键）。"""

    def test_edit_id_is_deterministic(self):
        assert edit_id_for("b05", 3, 2) == edit_id_for("b05", 3, 2)
        assert edit_id_for("b05", 3, 2) != edit_id_for("b05", 3, 3)

    def test_upsert_overwrites_same_block(self, session):
        repo = EditRepository(session)
        repo.upsert("b05", 3, 2, block_type="formula", verdict="bad", note="公式错")
        repo.upsert(
            "b05",
            3,
            2,
            block_type="formula",
            verdict="ok",
            note="",
            corrected_text="x^2 + y^2",
            corrected_bbox=[1, 2, 3, 4],
        )

        edits = repo.list_for_page("b05", 3)
        assert len(edits) == 1
        assert edits[0].verdict == "ok"
        assert edits[0].corrected_text == "x^2 + y^2"
        assert edits[0].corrected_bbox == [1, 2, 3, 4]

    def test_list_for_book_orders_by_page_and_block(self, session):
        repo = EditRepository(session)
        repo.upsert("b05", 5, 1, verdict="ok")
        repo.upsert("b05", 2, 3, verdict="bad")
        repo.upsert("b11", 1, 0, verdict="ok")

        assert [(e.page_no, e.block_index) for e in repo.list_for_book("b05")] == [(2, 3), (5, 1)]
