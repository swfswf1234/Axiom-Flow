"""MySQL 级提交边界集成测试（PLAN-003 W3-b / [ADR 0005](../../docs/adr/0005-mysql-integration-test-isolation.md)）。

守护面：`orchestrator/pipeline.py` 的页级提交不变式（条文 3）在**真实事务隔离**下成立——
「未提交不可见 / 每页提交后立即可见 / progress 单调 / 异常不吞已解析页」。单测里的 reader Session
与 writer 共享同一条 SQLite 连接，未提交数据照样可见，这类缺陷在 SQLite 层结构性不可见。

隔离边界（ADR 0005）：只在 `QED_TEST_MYSQL=1` 时连本机 MySQL，且只使用 `qed_test` 库里本仓库自有的
`af_*` 四表（+ `alembic_version`）；数据根指向 `tmp_path`。缺 opt-in 时整体 skip，默认门禁零外部依赖。
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.models import AF_TABLES
from axiom_flow.db.repositories import (
    BookRepository,
    BookSyncRow,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.base import DocumentResult, EngineError, ModelLoad, PageResult
from axiom_flow.ingest import ingest_book
from axiom_flow.orchestrator import pipeline, run_parse_job
from axiom_flow.schemas import BlocksPage

BOOK_ID = "w3b05"
DOMAIN_ID = "math-advanced"
COURSE_ID = "math_analysis"
SOURCE_FILE = f"raw/{DOMAIN_ID}/{COURSE_ID}/book.pdf"
JOB_ID = "job-w3"


def _make_pdf(path: Path, pages: int) -> Path:
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), "w3 persistence probe")
        doc.save(path)
    finally:
        doc.close()
    return path


def _page_result(page_no: int) -> PageResult:
    return PageResult(
        page=page_no,
        blocks=[{"type": "text", "text": f"第 {page_no} 页", "bbox": [72, 96, 540, 132]}],
        page_size=(595.0, 842.0),
    )


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    """合成数据根（ADR 0005 决定 5）：绝不读写真实 `QED_DATA_ROOT`。"""
    root = tmp_path / "data"
    raw_dir = root / "raw" / DOMAIN_ID / COURSE_ID
    raw_dir.mkdir(parents=True)
    _make_pdf(raw_dir / "book.pdf", pages=3)
    return root


@pytest.fixture()
def store(mysql_store: Engine) -> Engine:
    return mysql_store


@pytest.fixture()
def sessions(store: Engine) -> tuple[sessionmaker[Session], sessionmaker[Session]]:
    """写/读两个独立 sessionmaker：同一 engine 的两条连接，才是真隔离。"""
    return sessionmaker(bind=store, future=True), sessionmaker(bind=store, future=True)


def _seed_book(writer: sessionmaker[Session], data_root: Path, *, pages: int = 3) -> None:
    """sync + ingest 一本合成书（含页图与 book.json），供编排走真实落盘路径。"""
    raw_dir = data_root / "raw" / DOMAIN_ID / COURSE_ID
    _make_pdf(raw_dir / "book.pdf", pages=pages)
    with writer() as session:
        BookRepository(session).upsert_many(
            [
                BookSyncRow(
                    book_id=BOOK_ID,
                    domain_id=DOMAIN_ID,
                    course_id=COURSE_ID,
                    title="W3 提交边界样本",
                    file_path=SOURCE_FILE,
                )
            ]
        )
        session.commit()
        result = ingest_book(
            book_id=BOOK_ID,
            source_file=SOURCE_FILE,
            data_root=data_root,
            domain_id=DOMAIN_ID,
            course_id=COURSE_ID,
            title="W3 提交边界样本",
        )
        BookRepository(session).update_ingest(
            BOOK_ID, page_count=result.page_count, sha256=result.sha256
        )
        session.commit()


def _create_job(writer: sessionmaker[Session], *, pages: list[int] | None) -> str:
    with writer() as session:
        ParseJobRepository(session).create(
            ParseJobRow(job_id=JOB_ID, book_id=BOOK_ID, engine="mineru", pages=pages)
        )
        session.commit()
    return JOB_ID


class _ProbeEngine:
    """每次调用引擎前，用**独立读连接**快照已提交的 parsed 页数与 progress。"""

    name = "mineru"

    def __init__(self, reader: sessionmaker[Session], *, fail_pages: set[int] | None = None) -> None:
        self.reader = reader
        self.fail_pages = fail_pages or set()
        self.parsed_snapshots: list[int] = []
        self.progress_snapshots: list[int] = []

    def health(self) -> bool:
        return True

    def model_load(self) -> ModelLoad:
        """恒为空闲：本文件守护提交边界与进度可见性，不承担闸门分支（见编排单测）。"""
        return ModelLoad(queued=0, processing=0)

    def _probe(self) -> None:
        with self.reader() as session:
            self.parsed_snapshots.append(PageRepository(session).count_parsed(BOOK_ID, JOB_ID))
            job = ParseJobRepository(session).get(JOB_ID)
        self.progress_snapshots.append(int((job.progress if job else {}).get("parsed", 0)))

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
        self._probe()
        if page_no in self.fail_pages:
            raise EngineError(f"第 {page_no} 页解析失败")
        return _page_result(page_no)

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        self._probe()
        return DocumentResult(pages=[_page_result(page_no) for page_no in range(1, 4)])


class TestIsolationBoundary:
    """夹具边界本身：库名硬校验 + 只拥有 af_* 四表。"""

    def test_store_targets_the_test_schema_not_the_business_db(self, store: Engine) -> None:
        assert store.url.database == "qed_test"
        with store.connect() as conn:
            assert conn.execute(text("SELECT DATABASE()")).scalar_one() == "qed_test"

    def test_migrations_create_exactly_the_af_tables(self, store: Engine) -> None:
        owned = {table.name for table in AF_TABLES}
        with store.connect() as conn:
            found = {
                row[0]
                for row in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = DATABASE()"
                    )
                )
            }
        assert owned <= found, "af_* 四表必须由 Alembic 建齐"
        assert "alembic_version" in found
        with store.connect() as conn:
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        assert revision == "20260921_0001", "集成面必须跑在当前 head 上"


class TestCommitBoundary:
    """条文 3 的 MySQL 语义：提交边界就是可见性边界。"""

    def test_uncommitted_page_row_is_invisible_until_commit(
        self, sessions: tuple[sessionmaker[Session], sessionmaker[Session]]
    ) -> None:
        writer, reader = sessions
        with writer() as session:
            PageRepository(session).upsert(BOOK_ID, JOB_ID, 1, status="parsed")
            session.flush()
            with reader() as watching:
                assert PageRepository(watching).get(BOOK_ID, JOB_ID, 1) is None, (
                    "未提交即对他连接可见 = 隔离失效，条文 3 的回归网会失真"
                )
            session.commit()
        with reader() as watching:
            assert PageRepository(watching).get(BOOK_ID, JOB_ID, 1) is not None

    def test_each_page_is_committed_before_the_next_one(
        self, sessions: tuple[sessionmaker[Session], sessionmaker[Session]], data_root: Path
    ) -> None:
        """第 n 页调用引擎时，前 n-1 页必须已对独立连接可见（页级提交，不是任务末一次性提交）。"""
        writer, reader = sessions
        _seed_book(writer, data_root)
        _create_job(writer, pages=[1, 2, 3])
        probe = _ProbeEngine(reader)

        with writer() as session:
            outcome = run_parse_job(
                session=session,
                engine=probe,
                job_id=JOB_ID,
                book_id=BOOK_ID,
                pages=[1, 2, 3],
                data_root=data_root,
            )

        assert outcome.status == "completed"
        assert probe.parsed_snapshots == [0, 1, 2]

    def test_deferred_commits_are_genuinely_invisible_mid_run(
        self, sessions: tuple[sessionmaker[Session], sessionmaker[Session]], data_root: Path
    ) -> None:
        """回归网自证：退化开关 `persist_per_page=False` 时独立连接**看不到**任何中间进度。

        没有这条反向判据，上一条正向断言可能恒真（例如读连接意外复用了写连接）。
        """
        writer, reader = sessions
        _seed_book(writer, data_root)
        _create_job(writer, pages=[1, 2])
        probe = _ProbeEngine(reader)

        with writer() as session:
            run_parse_job(
                session=session,
                engine=probe,
                job_id=JOB_ID,
                book_id=BOOK_ID,
                pages=[1, 2],
                data_root=data_root,
                persist_per_page=False,
            )

        assert probe.parsed_snapshots == [0, 0], "两条连接不隔离时这里反而会看到进度"
        with reader() as watching:
            assert PageRepository(watching).count_parsed(BOOK_ID, JOB_ID) == 0, "未由调用方提交前不得落库"


class TestProgressPersistence:
    """`progress.parsed` 在真库上单调可查（L4 的回归网替代）。"""

    def test_progress_samples_are_monotonic_and_advance_mid_run(
        self, sessions: tuple[sessionmaker[Session], sessionmaker[Session]], data_root: Path
    ) -> None:
        writer, reader = sessions
        _seed_book(writer, data_root, pages=12)
        _create_job(writer, pages=list(range(1, 13)))
        probe = _ProbeEngine(reader)

        with writer() as session:
            outcome = run_parse_job(
                session=session,
                engine=probe,
                job_id=JOB_ID,
                book_id=BOOK_ID,
                pages=list(range(1, 13)),
                data_root=data_root,
            )

        assert outcome.parsed == 12
        samples = probe.progress_snapshots
        assert samples == sorted(samples), f"progress 出现回退：{samples}"
        assert any(0 < value < 12 for value in samples), (
            f"运行中从未观察到中间进度（节流写失效或整任务末才提交）：{samples}"
        )
        with reader() as watching:
            job = ParseJobRepository(watching).get(JOB_ID)
        assert job is not None and job.status == "completed"
        assert job.progress["parsed"] == 12


class TestFailurePersistence:
    """失败路径不吞已解析页（A3 回归，独立连接取证）。"""

    def test_unhandled_error_keeps_earlier_pages_visible(
        self, sessions: tuple[sessionmaker[Session], sessionmaker[Session]], data_root: Path
    ) -> None:
        writer, reader = sessions
        _seed_book(writer, data_root)
        _create_job(writer, pages=[1, 2])

        class Exploding(_ProbeEngine):
            """非 `EngineError` 穿透：条文 3 要求前序页行不能被外层回滚吞掉。"""

            def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
                self._probe()
                if page_no == 2:
                    raise ValueError(f"第 {page_no} 页内部异常")
                return _page_result(page_no)

        with writer() as session, pytest.raises(ValueError, match="内部异常"):
            run_parse_job(
                session=session,
                engine=Exploding(reader),
                job_id=JOB_ID,
                book_id=BOOK_ID,
                pages=[1, 2],
                data_root=data_root,
            )

        with reader() as watching:
            assert PageRepository(watching).get(BOOK_ID, JOB_ID, 1).status == "parsed"
            assert PageRepository(watching).get(BOOK_ID, JOB_ID, 2) is None
            assert BookRepository(watching).get(BOOK_ID).active_job_id is None, "异常中断不激活（V3）"

    def test_engine_error_during_whole_book_keeps_parsed_pages_visible(
        self,
        sessions: tuple[sessionmaker[Session], sessionmaker[Session]],
        data_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        writer, reader = sessions
        _seed_book(writer, data_root)
        _create_job(writer, pages=None)
        original = pipeline._write_page_artifacts

        def boom(version_dir: Path, page: BlocksPage) -> None:
            if page.page == 2:
                raise EngineError("落盘失败")
            original(version_dir, page)

        monkeypatch.setattr(pipeline, "_write_page_artifacts", boom)

        with writer() as session:
            outcome = run_parse_job(
                session=session,
                engine=_ProbeEngine(reader),
                job_id=JOB_ID,
                book_id=BOOK_ID,
                pages=None,
                data_root=data_root,
            )

        assert outcome.status == "failed" and "落盘失败" in outcome.error
        with reader() as watching:
            job = ParseJobRepository(watching).get(JOB_ID)
            book = BookRepository(watching).get(BOOK_ID)
            assert PageRepository(watching).get(BOOK_ID, JOB_ID, 1).status == "parsed"
        assert job is not None and job.status == "failed"
        assert job.progress["parsed"] == 1
        assert book is not None and book.pages_done == 1 and book.active_job_id is None
