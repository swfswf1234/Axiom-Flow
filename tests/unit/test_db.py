"""af_* 表模型与仓储层单测（SQLite 内存，不依赖 MySQL，符合测试隔离铁律）。

守护面：四表结构与设计一致（表名/主键/唯一键）、同步幂等与快照语义、任务/页/编辑状态回写。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.models import AF_TABLES, AfBlockEdit, Base
from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
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
        assert pk["constrained_columns"] == ["book_id", "job_id", "page_no"]

    def test_af_block_edits_unique_position(self, engine):
        uniques = inspect(engine).get_unique_constraints("af_block_edits")
        assert any(u["column_names"] == ["book_id", "job_id", "page_no", "block_index"] for u in uniques)

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

    def test_set_active_job_is_the_only_effective_pointer(self, session):
        """生效版本是 af_books 单列事实源：切换即覆盖，不引入第二处布尔（V2）。"""
        repo = BookRepository(session)
        repo.upsert_many([_row()])
        assert repo.get("mathanalysis-b05").active_job_id is None, "新建书默认根视图，不猜生效版本"

        assert repo.set_active_job("mathanalysis-b05", "job2") is True
        assert repo.get("mathanalysis-b05").active_job_id == "job2"

        assert repo.set_active_job("mathanalysis-b05", "job3") is True
        assert repo.get("mathanalysis-b05").active_job_id == "job3"

        assert repo.set_active_job("mathanalysis-b05", None) is True
        assert repo.get("mathanalysis-b05").active_job_id is None

    def test_set_active_job_missing_book_returns_false(self, session):
        assert BookRepository(session).set_active_job("nope", "job1") is False


class TestParseJobRepository:
    """af_parse_jobs：任务状态机与进度。"""

    def test_job_lifecycle(self, session):
        repo = ParseJobRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru", pages=None, progress={"parsed": 0, "total": 320}))

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
        repo.create(ParseJobRow(job_id="job2", book_id="b05", engine="mineru", pages=[1, 3, 5]))

        job = repo.get("job2")
        assert job is not None and job.pages == [1, 3, 5]

    def test_job_row_carries_source_snapshot_for_self_proof(self, session):
        """版本与源 PDF 的对应关系落在 job 行：事后仍可自证这一版解析的是哪份文件。"""
        repo = ParseJobRepository(session)
        repo.create(
            ParseJobRow(
                job_id="job3",
                book_id="b05",
                engine="mineru",
                pages=None,
                source_sha256="b" * 64,
                page_count=486,
            )
        )

        job = repo.get("job3")
        assert job is not None
        assert job.source_sha256 == "b" * 64
        assert job.page_count == 486

    def test_job_row_requires_explicit_engine(self):
        """死默认值销账：`engine` 必填，缺引擎名在建任务行时就失败，不静默写成 MinerU。"""
        with pytest.raises(TypeError, match="engine"):
            ParseJobRow(job_id="job1", book_id="b05")

    def test_list_for_book_and_missing_updates(self, session):
        repo = ParseJobRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        repo.create(ParseJobRow(job_id="job2", book_id="b11", engine="mineru"))

        assert [job.job_id for job in repo.list_for_book("b05")] == ["job1"]
        assert repo.mark_running("nope") is False
        assert repo.mark_finished("nope", status="failed") is False


class TestPageRepository:
    """af_pages：页状态 upsert 与质量信号（按解析版本 job_id 作用域）。"""

    def test_upsert_page_and_count_parsed(self, session):
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="pending")
        repo.upsert("b05", "job1", 2, status="failed", error="超时")
        repo.upsert("b05", "job1", 3, status="parsed", source="mineru", quality={"empty_block_ratio": 0.0})

        page = repo.get("b05", "job1", 3)
        assert page is not None
        assert page.source == "mineru"
        assert page.quality == {"empty_block_ratio": 0.0}
        assert page.parsed_at is not None
        assert repo.count_parsed("b05", "job1") == 1
        assert [p.page_no for p in repo.list_for_book("b05", "job1")] == [1, 2, 3]

    def test_upsert_same_page_overwrites(self, session):
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="failed", error="超时")
        repo.upsert("b05", "job1", 1, status="parsed", source="mineru")

        assert repo.get("b05", "job1", 1).status == "parsed"
        assert len(repo.list_for_book("b05", "job1")) == 1

    def test_same_page_in_two_versions_stays_independent(self, session):
        """同一页在不同版本可有独立状态：旧版结果不被新版覆盖（V2 主键纳入 job_id 的目的）。"""
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="parsed", source="mineru")
        repo.upsert("b05", "job2", 1, status="failed", error="引擎超时")

        assert repo.get("b05", "job1", 1).status == "parsed"
        assert repo.get("b05", "job2", 1).status == "failed"
        assert [p.job_id for p in repo.list_for_book("b05", "job1")] == ["job1"]
        assert repo.count_parsed("b05", "job2") == 0

    def test_reset_legacy_only_drops_root_view_rows(self, session):
        """重新 ingest 只作废根视图（legacy）页行；历史版本状态必须保留（V8）。"""
        repo = PageRepository(session)
        repo.upsert("b05", LEGACY_JOB_ID, 1, status="parsed")
        repo.upsert("b05", "job1", 1, status="parsed")

        assert repo.reset_legacy("b05") == 1
        assert repo.get("b05", LEGACY_JOB_ID, 1) is None
        assert repo.get("b05", "job1", 1) is not None

    def test_delete_for_job_drops_only_that_version(self, session):
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="parsed")
        repo.upsert("b05", "job2", 1, status="parsed")
        repo.upsert("b11", "job1", 1, status="parsed")

        assert repo.delete_for_job("b05", "job1") == 1
        assert repo.get("b05", "job1", 1) is None
        assert repo.get("b05", "job2", 1) is not None
        assert repo.get("b11", "job1", 1) is not None

    def test_delete_for_book_drops_every_version(self, session):
        """书目删除是全书作用域：legacy 与所有真实版本的页行一起消失。"""
        repo = PageRepository(session)
        repo.upsert("b05", LEGACY_JOB_ID, 1, status="parsed")
        repo.upsert("b05", "job1", 1, status="parsed")
        repo.upsert("b05", "job2", 2, status="failed")
        repo.upsert("b11", "job1", 1, status="parsed")

        assert repo.delete_for_book("b05") == 3
        assert repo.list_for_book("b05", "job1") == []
        assert repo.list_for_book("b05", "job2") == []
        assert repo.get("b05", LEGACY_JOB_ID, 1) is None
        assert repo.get("b11", "job1", 1) is not None


class TestEditRepository:
    """af_block_edits：同块重复编辑覆盖更新（幂等键绑定解析版本）。"""

    def test_edit_id_is_deterministic(self):
        assert edit_id_for("b05", "job1", 3, 2) == edit_id_for("b05", "job1", 3, 2)
        assert edit_id_for("b05", "job1", 3, 2) != edit_id_for("b05", "job1", 3, 3)
        # 同一块在不同版本各自成立：块下标语义随映射表版本变化，共用键即污染编辑
        assert edit_id_for("b05", "job1", 3, 2) != edit_id_for("b05", "job2", 3, 2)

    def test_upsert_overwrites_same_block(self, session):
        repo = EditRepository(session)
        repo.upsert("b05", "job1", 3, 2, block_type="formula", verdict="bad", note="公式错")
        repo.upsert(
            "b05",
            "job1",
            3,
            2,
            block_type="formula",
            verdict="ok",
            note="",
            corrected_text="x^2 + y^2",
            corrected_bbox=[1, 2, 3, 4],
        )

        edits = repo.list_for_page("b05", "job1", 3)
        assert len(edits) == 1
        assert edits[0].verdict == "ok"
        assert edits[0].corrected_text == "x^2 + y^2"
        assert edits[0].corrected_bbox == [1, 2, 3, 4]

    def test_list_for_book_scopes_to_version(self, session):
        repo = EditRepository(session)
        repo.upsert("b05", "job1", 5, 1, verdict="ok")
        repo.upsert("b05", "job1", 2, 3, verdict="bad")
        repo.upsert("b11", "job1", 1, 0, verdict="ok")

        assert [(e.page_no, e.block_index) for e in repo.list_for_book("b05", "job1")] == [(2, 3), (5, 1)]

    def test_same_block_in_two_versions_stays_independent(self, session):
        repo = EditRepository(session)
        repo.upsert("b05", "job1", 3, 2, verdict="ok", corrected_text="旧版修正")
        repo.upsert("b05", "job2", 3, 2, verdict="bad", note="新版判为不可用")

        assert repo.get("b05", "job1", 3, 2).corrected_text == "旧版修正"
        assert repo.get("b05", "job2", 3, 2).verdict == "bad"
        assert len(repo.list_for_page("b05", "job1", 3)) == 1

    def test_legacy_row_with_old_digest_edit_id_stays_reachable(self, session):
        """迁移保留存量 edit_id（旧摘要不含 job_id）：upsert 按唯一键定位，不产生第二条编辑。"""
        session.add(
            AfBlockEdit(
                edit_id="ed_pre_version_digest",
                book_id="b05",
                job_id=LEGACY_JOB_ID,
                page_no=3,
                block_index=2,
                verdict="bad",
                note="迁移前人工备注",
                edited_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.flush()
        repo = EditRepository(session)

        edited = repo.upsert("b05", LEGACY_JOB_ID, 3, 2, verdict="ok", note="复核通过")

        assert edited.edit_id == "ed_pre_version_digest", "存量主键不得重算，否则旧编辑行成为孤儿"
        assert len(repo.list_for_page("b05", LEGACY_JOB_ID, 3)) == 1

    def test_delete_for_book_drops_every_version(self, session):
        """V8：书目删除带走该书所有版本的人工编辑（页行同理），不留孤儿覆盖层。"""
        repo = EditRepository(session)
        repo.upsert("b05", LEGACY_JOB_ID, 1, 0, verdict="ok")
        repo.upsert("b05", "job1", 1, 0, verdict="bad")
        repo.upsert("b11", "job1", 1, 0, verdict="ok")

        assert repo.delete_for_book("b05") == 2
        assert repo.list_for_book("b05", "job1") == []
        assert repo.list_for_book("b11", "job1") != []
