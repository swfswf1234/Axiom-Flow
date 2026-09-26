"""解析编排单测：全本/逐页、失败语义、af_* 回写与分块重建（SQLite + 假引擎，不调模型）。

守护面（docs/design/parsing-pipeline.md）：默认全本、指定页逐页、单页失败不中断、
模型不可用任务失败、产物三件套 + chunks.json + manifest.json、进度回写；
以及页级持久化不变式（PP-13）：产物写先于页行提交、整本/异常失败保留已解析页、
progress 节流写入。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.models import AfParseJob, Base
from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
    BookRepository,
    BookSyncRow,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.base import DocumentResult, EngineError, ModelLoad, PageResult
from axiom_flow.ingest import ingest_book
from axiom_flow.orchestrator import jobs as jobs_module
from axiom_flow.orchestrator import pipeline, run_parse_job
from axiom_flow.orchestrator.jobs import JobRunner
from axiom_flow.schemas import BlocksPage, BookMeta


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
    """假引擎：全本两页；单页按页码返回。`loads` 逐次给出负载（耗尽后重复末值）。"""

    name = "mineru"

    def __init__(
        self,
        *,
        healthy: bool = True,
        fail_pages: set[int] | None = None,
        fail_document: bool = False,
        loads: list[ModelLoad | None] | None = None,
    ):
        self.healthy = healthy
        self.fail_pages = fail_pages or set()
        self.fail_document = fail_document
        self.loads = loads
        self.load_calls = 0
        self.parse_calls = 0

    def health(self) -> bool:
        return self.healthy

    def model_load(self) -> ModelLoad | None:
        if self.loads is None:
            return None
        index = min(self.load_calls, len(self.loads) - 1)
        self.load_calls += 1
        return self.loads[index]

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        self.parse_calls += 1
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
        self.parse_calls += 1
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


def _create_job(session: Session, *, pages: list[int] | None = None, job_id: str = "job-1") -> str:
    ParseJobRepository(session).create(ParseJobRow(job_id=job_id, book_id="b05", engine="mineru", pages=pages))
    session.commit()
    return job_id


def _ingest(session: Session, data_root: Path) -> None:
    result = ingest_book(
        book_id="b05",
        source_file="raw/math-advanced/math_analysis/book.pdf",
        data_root=data_root,
        domain_id="math-advanced",
        course_id="math_analysis",
    )
    BookRepository(session).update_ingest("b05", page_count=result.page_count, sha256=result.sha256)
    session.commit()


def _book_dir(data_root: Path) -> Path:
    return data_root / "parsed" / "math-advanced" / "math_analysis" / "b05"


def _version_dir(data_root: Path, job_id: str) -> Path:
    return _book_dir(data_root) / "versions" / job_id


class TestMetadataSourceIsAfBooks:
    """REQ-097 / PLAN-017 W1+W3：书目元数据唯一事实源是 af_books，版本 `book.json` 由任务生成。

    旧实现从根视图 `book.json` 读 meta（ingest 落盘的第二正本），根视图退役后该文件不存在；
    版本目录的 book.json 也不得从基线继承——否则引擎名与时刻都是旧版的。
    """

    def test_parse_succeeds_without_root_book_json_and_writes_version_book_json(self, env):
        session, data_root = env
        _ingest(session, data_root)
        assert not (_book_dir(data_root) / "book.json").exists(), "根视图不再落 book.json（W2）"
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "completed", outcome.error
        meta = BookMeta.model_validate_json(
            (_version_dir(data_root, job_id) / "book.json").read_text(encoding="utf-8")
        )
        assert meta.book_id == "b05"
        assert meta.title == "数学分析原理"
        assert meta.page_count == 2
        assert meta.source_file == "raw/math-advanced/math_analysis/book.pdf"
        assert meta.engine == "mineru"
        book = BookRepository(session).get("b05")
        assert meta.sha256 == book.sha256
        assert meta.ingested_at == book.ingested_at

    def test_stale_root_book_json_is_not_used(self, env):
        """基线/根视图里的旧 book.json 是第二正本：解析读 af_books、版本 book.json 不回抄旧值。"""
        session, data_root = env
        _ingest(session, data_root)
        (_book_dir(data_root) / "book.json").write_text(
            BookMeta(
                book_id="b05",
                title="陈旧副本",
                page_count=9,
                sha256="f" * 64,
                source_file="raw/elsewhere.pdf",
                engine="paddleocr-vl",
            ).model_dump_json(),
            encoding="utf-8",
        )
        job_id = _create_job(session)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "completed", outcome.error
        assert outcome.total == 2, "页范围取 af_books.page_count，不取旧 book.json 的 9"
        meta = BookMeta.model_validate_json(
            (_version_dir(data_root, job_id) / "book.json").read_text(encoding="utf-8")
        )
        assert meta.title == "数学分析原理"
        assert meta.engine == "mineru"


class TestActivationRetiresReplacedVersion:
    """REQ-097 / PLAN-017 W4：激活成功的下一刻删除被替换的旧版本目录，日志留痕。

    删除只发生在指针切换并提交**之后**：删除失败最多留残磁盘；若发生在切换前，指针会指向
    已删目录。`partial`/`failed` 不激活也不删；同 job 续跑不删自己；legacy 无目录可删。
    """

    def _run(self, session, data_root, job_id, *, engine=None, pages=None):
        return run_parse_job(
            session=session,
            engine=engine or FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=pages,
            data_root=data_root,
        )

    def test_replaced_version_dir_deleted_on_activation(self, env, caplog):
        session, data_root = env
        _ingest(session, data_root)
        assert self._run(session, data_root, _create_job(session, job_id="job-1")).status == "completed"
        assert _version_dir(data_root, "job-1").is_dir()

        with caplog.at_level(logging.INFO, logger="axiom_flow.orchestrator.pipeline"):
            outcome = self._run(session, data_root, _create_job(session, job_id="job-2"))

        assert outcome.status == "completed", outcome.error
        assert not _version_dir(data_root, "job-1").exists(), "激活后旧正本目录必须删除，磁盘上只留生效版本"
        assert _version_dir(data_root, "job-2").is_dir()
        assert any("job-1" in record.getMessage() for record in caplog.records), "删除动作必须日志留痕"

    def test_partial_does_not_delete_replaced_version(self, env):
        session, data_root = env
        _ingest(session, data_root)
        assert self._run(session, data_root, _create_job(session, job_id="job-1")).status == "completed"

        outcome = self._run(
            session,
            data_root,
            _create_job(session, job_id="job-2"),
            engine=FakeEngine(fail_pages={1}),
            pages=[1, 2],
        )

        assert outcome.status == "partial", outcome.status
        assert _version_dir(data_root, "job-1").is_dir(), "未激活就不删：旧版本仍是唯一可读正本"
        assert BookRepository(session).get("b05").active_job_id == "job-1"

    def test_rerun_of_active_job_keeps_its_own_dir(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, job_id="job-1")
        assert self._run(session, data_root, job_id).status == "completed"

        assert self._run(session, data_root, job_id).status == "completed"

        assert _version_dir(data_root, "job-1").is_dir(), "被替换版本 == 本版本：不得自删"

    def test_deletion_failure_only_warns_and_keeps_job_completed(self, env, monkeypatch, caplog):
        session, data_root = env
        _ingest(session, data_root)
        assert self._run(session, data_root, _create_job(session, job_id="job-1")).status == "completed"

        def _raise(_path, *args, **kwargs):
            raise OSError("被读取中的句柄占用")

        monkeypatch.setattr(pipeline.shutil, "rmtree", _raise)
        with caplog.at_level(logging.WARNING, logger="axiom_flow.orchestrator.pipeline"):
            outcome = self._run(session, data_root, _create_job(session, job_id="job-2"))

        assert outcome.status == "completed", "删除失败不翻转任务终态：指针已切换，残留只是磁盘面"
        assert _version_dir(data_root, "job-1").is_dir()
        assert any("job-1" in record.getMessage() for record in caplog.records), "删除失败必须告警"


class TestWholeBookJob:
    """默认全本：一次整本解析 → 逐页产物 + 分块 + manifest + 回写（产物落版本目录）。"""

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
        version_dir = _version_dir(data_root, job_id)
        assert (version_dir / "pages" / "p0001.blocks.json").is_file()
        assert (version_dir / "pages" / "p0001.md").is_file()
        assert (version_dir / "chunks.json").is_file()
        assert (version_dir / "manifest.json").is_file()
        assert not (book_dir / "pages" / "p0001.blocks.json").exists(), "版本产物不得写根视图（V1）"
        assert (book_dir / "pages" / "p0001.png").is_file(), "页图仍由 ingest 拥有，版本目录只接入"
        chunk_set = json.loads((version_dir / "chunks.json").read_text(encoding="utf-8"))
        assert chunk_set["chunks"][0]["block_refs"][0]["page"] == 1
        page = BlocksPage.model_validate_json(
            (version_dir / "pages" / "p0001.blocks.json").read_text(encoding="utf-8")
        )
        assert page.source == "mineru"
        assert page.blocks[0].type.value == "heading"

        book = BookRepository(session).get("b05")
        assert book is not None and book.pages_done == 2
        assert book.parse_status == "pending", "进度列不表达结局（ADR 0007 决定 3）"
        assert book.last_parse_result == "completed"
        job = ParseJobRepository(session).get(job_id)
        assert job is not None and job.status == "completed"
        assert PageRepository(session).count_parsed("b05", job_id) == 2

    def test_completion_activates_the_version(self, env):
        """齐备且落盘成功 → 生效指针指向本版本（V3 的唯一激活时点）。"""
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert BookRepository(session).get("b05").active_job_id == job_id

    def test_job_row_self_proves_its_source_snapshot(self, env):
        """版本 ↔ 源文件对应关系落在任务行（V7）。"""
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        job = ParseJobRepository(session).get(job_id)
        book = BookRepository(session).get("b05")
        assert job is not None
        assert job.source_sha256 == book.sha256 and job.source_sha256 != ""
        assert job.page_count == 2

    def test_no_intermediates_written_into_data_root(self, env):
        """V10：编排不在数据根留临时目录（历史踩坑：tmp 污染产物树）。"""
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert not (data_root / "tmp").exists()
        assert sorted(p.name for p in data_root.iterdir()) == ["parsed", "raw"]

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
        assert book is not None and book.parse_status == "pending"
        assert book.last_parse_result == "failed"
        assert book.active_job_id is None, "失败版本不得激活（V3）"

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


class TestSingleOccupancyGate:
    """单一占用闸门（ARCH-017 / R1 窗口实证）：他人占用时不提交，等待期保持 queued，超时文案分列。"""

    def test_waits_for_idle_then_parses(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1])
        engine = FakeEngine(
            loads=[ModelLoad(queued=1, processing=0), ModelLoad(queued=0, processing=1), ModelLoad(queued=0, processing=0)]
        )

        outcome = run_parse_job(
            session=session,
            engine=engine,
            job_id=job_id,
            book_id="b05",
            pages=[1],
            data_root=data_root,
            model_gate_interval=0.0,
            model_gate_deadline=5.0,
        )

        assert outcome.status == "completed"
        assert engine.load_calls == 3, "空闲前不得提交，每轮等待后复探一次"
        assert engine.parse_calls == 1

    def test_busy_until_deadline_fails_with_occupancy_wording(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1])
        engine = FakeEngine(loads=[ModelLoad(queued=0, processing=1)])

        outcome = run_parse_job(
            session=session,
            engine=engine,
            job_id=job_id,
            book_id="b05",
            pages=[1],
            data_root=data_root,
            model_gate_interval=0.0,
            model_gate_deadline=0.05,
        )

        assert outcome.status == "failed"
        assert "占用" in outcome.error
        assert "不可用" not in outcome.error, "占用与不可用是两种语义，不互相冒充"
        assert engine.parse_calls == 0, "占用期间不得向模型服务提交任何内容"
        job = ParseJobRepository(session).get(job_id)
        assert job is not None and job.started_at is None, "等待期保持 queued（started_at 为空）"
        assert PageRepository(session).list_for_book("b05", job_id) == []
        book = BookRepository(session).get("b05")
        assert book is not None and book.active_job_id is None

    def test_engine_without_load_reporting_passes_through(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1])
        engine = FakeEngine(loads=[None])

        outcome = run_parse_job(
            session=session,
            engine=engine,
            job_id=job_id,
            book_id="b05",
            pages=[1],
            data_root=data_root,
            model_gate_interval=0.0,
            model_gate_deadline=5.0,
        )

        assert outcome.status == "completed", "引擎不上报负载时按可用放行，不臆造阻塞"
        assert engine.load_calls == 1

    def test_gate_contract_numbers(self):
        assert (pipeline.MODEL_GATE_INTERVAL_SECONDS, pipeline.MODEL_GATE_DEADLINE_SECONDS) == (5.0, 900.0)


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
            (_version_dir(data_root, job_id) / "pages" / "p0002.blocks.json").read_text(encoding="utf-8")
        )
        assert page.blocks[0].text == "第 2 页"
        assert PageRepository(session).count_parsed("b05", job_id) == 1

    def test_two_versions_write_isolated_until_activation_retires_predecessor(self, env):
        """各版本写各的目录、互不覆盖；后者激活成功即删除被替换版本（REQ-097 W4）。

        旧契约（PLAN-004 / REQ-080）允许历史版本目录长期共存；W4 后磁盘只留生效版本，
        历史可解释性由 af_pages/af_parse_jobs 行承载，不再依赖残留文件。
        """
        session, data_root = env
        _ingest(session, data_root)
        first = _create_job(session, pages=[1], job_id="job-a")
        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=first,
            book_id="b05",
            pages=[1],
            data_root=data_root,
        )
        assert (_version_dir(data_root, first) / "pages" / "p0001.blocks.json").is_file()
        second = _create_job(session, pages=[2], job_id="job-b")
        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=second,
            book_id="b05",
            pages=[2],
            data_root=data_root,
        )

        assert not _version_dir(data_root, first).exists(), "job-b 已激活：被替换的 job-a 目录应被删除"
        assert (_version_dir(data_root, second) / "pages" / "p0001.blocks.json").is_file(), (
            "生效版本自带继承页产物，不依赖旧版本目录存活"
        )
        assert (_version_dir(data_root, second) / "pages" / "p0002.blocks.json").is_file()
        assert PageRepository(session).get("b05", first, 2) is None
        assert PageRepository(session).get("b05", second, 2).status == "parsed"
        assert BookRepository(session).get("b05").active_job_id == second

    def test_failed_page_marks_job_partial(self, env):
        """ADR 0007：有失败页即 `partial`，不再由 `completed` 掩盖（DEFECT-006）。"""
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

        assert outcome.status == "partial"
        assert outcome.failed_pages == [2]
        assert "失败页：2" in outcome.error
        assert PageRepository(session).get("b05", job_id, 2).status == "failed"
        job = ParseJobRepository(session).get(job_id)
        assert job is not None and job.status == "partial"
        assert job.failed_pages == [2], "失败页必须结构化落库，不只活在 error 字符串里"
        assert job.progress["failed"] == 1
        book = BookRepository(session).get("b05")
        assert book is not None and book.parse_status == "pending" and book.pages_done == 1
        assert book.last_parse_result == "partial"
        assert book.active_job_id is None, "目标页未齐备 → 不激活（V3）"
        assert (_version_dir(data_root, job_id) / "pages" / "p0001.blocks.json").is_file(), (
            "不激活也不删产物：失败版本留在磁盘上供排查"
        )

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
        assert PageRepository(session).get("b05", job_id, 2).error == "页图缺失"

    def test_per_page_version_is_a_complete_snapshot(self, env):
        """逐页重解析的版本是**完整快照**：未碰的页从上一生效版本继承产物与页行。"""
        session, data_root = env
        _ingest(session, data_root)
        first = _create_job(session, job_id="job-a")
        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=first,
            book_id="b05",
            pages=None,
            data_root=data_root,
        )
        second = _create_job(session, pages=[2], job_id="job-b")

        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=second,
            book_id="b05",
            pages=[2],
            data_root=data_root,
        )

        version_dir = _version_dir(data_root, second)
        inherited = BlocksPage.model_validate_json(
            (version_dir / "pages" / "p0001.blocks.json").read_text(encoding="utf-8")
        )
        refreshed = BlocksPage.model_validate_json(
            (version_dir / "pages" / "p0002.blocks.json").read_text(encoding="utf-8")
        )
        assert inherited.blocks[0].type.value == "heading", "第 1 页沿用上一版内容"
        assert refreshed.blocks[0].text == "第 2 页"
        assert PageRepository(session).get("b05", second, 1).status == "parsed", "页行随版本走"
        assert PageRepository(session).count_parsed("b05", second) == 2
        assert BookRepository(session).get("b05").pages_done == 2, "pages_done 描述生效版本，不是本次跑了几页"

    def test_legacy_root_view_pages_are_inherited_into_the_first_version(self, env):
        """迁移后的真机形态：根视图（legacy）页行 + 根目录产物 → 首个新版本按页继承。"""
        session, data_root = env
        _ingest(session, data_root)
        _book_dir(data_root).joinpath("pages", "p0001.blocks.json").write_text(
            BlocksPage(page=1, source="mineru", image_size=(595, 842), blocks=[]).model_dump_json(),
            encoding="utf-8",
        )
        PageRepository(session).upsert("b05", LEGACY_JOB_ID, 1, status="parsed", source="mineru")
        job_id = _create_job(session, pages=[2])

        run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=[2],
            data_root=data_root,
        )

        assert PageRepository(session).get("b05", job_id, 1).status == "parsed"
        assert PageRepository(session).get("b05", LEGACY_JOB_ID, 1).status == "parsed", "旧作用域不被改写"
        assert (_version_dir(data_root, job_id) / "pages" / "p0001.blocks.json").is_file()

    def test_rerunning_same_job_resumes_parsed_pages(self, env, monkeypatch):
        """同 job 重跑 = 续跑：已 `parsed` 且产物在位的页不再送引擎（真机 kill 后重启的路径）。"""
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1, 2])
        called: list[int] = []
        original = pipeline._parse_page_with_retry

        def spy(engine, image_path, page_no):
            called.append(page_no)
            return original(engine, image_path, page_no)

        monkeypatch.setattr(pipeline, "_parse_page_with_retry", spy)
        PageRepository(session).upsert("b05", job_id, 1, status="parsed", source="mineru")
        version_dir = _version_dir(data_root, job_id)
        (version_dir / "pages").mkdir(parents=True)
        (version_dir / "pages" / "p0001.blocks.json").write_text(
            BlocksPage(page=1, source="mineru", image_size=(595, 842), blocks=[]).model_dump_json(),
            encoding="utf-8",
        )

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=job_id,
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
        )

        assert called == [2], "续跑页不得重复送引擎"
        assert outcome.failed_pages == []
        assert (outcome.parsed, outcome.total) == (2, 2), "续跑页计入 parsed，否则 progress 低估"
        assert (version_dir / "pages" / "p0002.blocks.json").is_file()
        assert BookRepository(session).get("b05").active_job_id == job_id


def _file_env(tmp_path: Path, pages: int = 2):
    """文件库 + 多连接：读 Session 只看得见已提交行，复现 MySQL 的连接隔离语义。

    内存 SQLite 单连接会同时看见未提交数据，无法守护页级持久化不变式。
    """
    data_root = tmp_path / "data"
    raw_dir = data_root / "raw" / "math-advanced" / "math_analysis"
    raw_dir.mkdir(parents=True)
    _make_pdf(raw_dir / "book.pdf", pages=pages)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'af.db'}", connect_args={"timeout": 5})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        BookRepository(session).upsert_many(
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
        ingest_book(
            book_id="b05",
            source_file="raw/math-advanced/math_analysis/book.pdf",
            data_root=data_root,
            domain_id="math-advanced",
            course_id="math_analysis",
        )
        BookRepository(session).update_ingest("b05", page_count=pages, sha256="a" * 64)
        ParseJobRepository(session).create(ParseJobRow(job_id="job-1", book_id="b05", engine="mineru"))
        session.commit()
        return engine, session, data_root


class _WindowEngine(FakeEngine):
    """按窗口回**全书绝对页号**的假引擎：记录每次提交的 1 基含两端区间，可按窗失败或块间转不健康。"""

    def __init__(
        self,
        *,
        pages: int,
        fail_spans: set[tuple[int, int]] | None = None,
        healthy_windows: int | None = None,
        reader=None,
    ) -> None:
        super().__init__()
        self.pages = pages
        self.fail_spans = fail_spans or set()
        self.healthy_windows = healthy_windows
        self._reader = reader
        self.spans: list[tuple[int, int]] = []
        self.snapshots: list[int] = []

    def health(self) -> bool:
        if self.healthy_windows is None:
            return True
        return len(self.spans) < self.healthy_windows

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        if self._reader is not None:
            with Session(self._reader) as reader:
                self.snapshots.append(PageRepository(reader).count_parsed("b05", "job-1"))
        span = (start_page or 1, min(end_page or self.pages, self.pages))
        self.spans.append(span)
        if span in self.fail_spans:
            raise EngineError("MinerU 服务不可达：connection refused")
        return DocumentResult(
            pages=[
                PageResult(
                    page=page_no,
                    blocks=[{"type": "text", "text": f"第 {page_no} 页", "bbox": [72, 96, 540, 132]}],
                    page_size=(595.0, 842.0),
                )
                for page_no in range(span[0], span[1] + 1)
            ]
        )


class TestWholeBookWindows:
    """PLAN-007 W3 / REQ-086：全本按页窗口**串行**提交，块级 deadline 与块级失败隔离。

    为什么必须分块：MinerU 适配器一次调用只有一个 deadline（`ocr_timeout`，默认 600s），
    真机 W0 实测 b05 前 56 页耗时 245s ⇒ 317 页整本单提交必然超时（progress 恒 0）。
    """

    def test_book_is_submitted_in_serial_page_windows(self, tmp_path):
        engine, session, data_root = _file_env(tmp_path, pages=12)
        fake = _WindowEngine(pages=12)

        outcome = run_parse_job(
            session=session,
            engine=fake,
            job_id="job-1",
            book_id="b05",
            pages=None,
            data_root=data_root,
            window_size=5,
        )

        assert fake.spans == [(1, 5), (6, 10), (11, 12)], "按 window_size 切连续区间、串行提交"
        assert outcome.status == "completed" and (outcome.parsed, outcome.total) == (12, 12)
        assert PageRepository(session).count_parsed("b05", "job-1") == 12
        version_dir = _version_dir(data_root, "job-1")
        assert (version_dir / "pages" / "p0012.blocks.json").is_file(), "跨窗页号必须是全书绝对页号"

    def test_window_failure_only_fails_its_own_pages(self, tmp_path):
        """S1：单块失败只损失该块（该块页行 `failed`），后续块继续，job 不整体 `failed`。"""
        engine, session, data_root = _file_env(tmp_path, pages=12)
        fake = _WindowEngine(pages=12, fail_spans={(6, 10)})

        outcome = run_parse_job(
            session=session,
            engine=fake,
            job_id="job-1",
            book_id="b05",
            pages=None,
            data_root=data_root,
            window_size=5,
        )

        assert fake.spans == [(1, 5), (6, 10), (11, 12)], "块失败不得中断后续块"
        assert outcome.status == "partial"
        assert outcome.failed_pages == [6, 7, 8, 9, 10]
        assert outcome.parsed == 7
        assert "不可达" in outcome.error, "块错误文本必须进终态，不能只剩页号清单"
        with Session(engine) as reader:
            pages = PageRepository(reader)
            assert pages.get("b05", "job-1", 5).status == "parsed"
            assert pages.get("b05", "job-1", 6).status == "failed"
            assert pages.get("b05", "job-1", 12).status == "parsed"
            job = ParseJobRepository(reader).get("job-1")
        assert job is not None and job.failed_pages == [6, 7, 8, 9, 10]

    def test_progress_is_visible_per_window_from_another_connection(self, tmp_path):
        """S2：进度按块推进且可在真机观测到非零中间值（独立连接读，复现 MySQL 连接隔离）。"""
        engine, session, data_root = _file_env(tmp_path, pages=12)
        fake = _WindowEngine(pages=12, reader=engine)

        run_parse_job(
            session=session,
            engine=fake,
            job_id="job-1",
            book_id="b05",
            pages=None,
            data_root=data_root,
            window_size=5,
        )

        assert fake.snapshots == [0, 5, 10], "每块开始前，前序块已对独立连接可见"

    def test_model_gone_between_windows_stops_submitting(self, tmp_path):
        """块间探活失败即止：不拿剩余窗口去撞超时（最坏挂钟 = 块数 × 单块超时）。"""
        engine, session, data_root = _file_env(tmp_path, pages=12)
        fake = _WindowEngine(pages=12, healthy_windows=1)

        outcome = run_parse_job(
            session=session,
            engine=fake,
            job_id="job-1",
            book_id="b05",
            pages=None,
            data_root=data_root,
            window_size=5,
        )

        assert fake.spans == [(1, 5)], "服务已不可用就不再提交后续块"
        assert outcome.status == "partial" and outcome.parsed == 5
        assert "模型服务不可用" in outcome.error

    def test_partial_resumed_window_still_skips_parsed_pages(self, tmp_path, monkeypatch):
        """同 job 续跑：已 parsed 且产物在位的页不重复回写，但其所在窗口仍须提交（引擎按窗口解析）。"""
        engine, session, data_root = _file_env(tmp_path, pages=12)
        fake = _WindowEngine(pages=12)
        for page_no in (1, 2):
            PageRepository(session).upsert("b05", "job-1", page_no, status="parsed", source="mineru")
        pages_dir = _version_dir(data_root, "job-1") / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for page_no in (1, 2):
            (pages_dir / f"p{page_no:04d}.blocks.json").write_text(
                BlocksPage(page=page_no, image_size=(1, 1), source="mineru", blocks=[]).model_dump_json(),
                encoding="utf-8",
            )
        written: list[int] = []
        original = pipeline._write_page_artifacts

        def spy(version_dir: Path, page: BlocksPage) -> None:
            written.append(page.page)
            original(version_dir, page)

        monkeypatch.setattr(pipeline, "_write_page_artifacts", spy)

        outcome = run_parse_job(
            session=session,
            engine=fake,
            job_id="job-1",
            book_id="b05",
            pages=None,
            data_root=data_root,
            window_size=5,
        )

        assert outcome.parsed == 12 and outcome.status == "completed"
        assert written == list(range(3, 13)), "续跑页只继承不重写；首窗从第 3 页起补写"
        assert fake.spans == [(1, 5), (6, 10), (11, 12)]

    def test_runner_forwards_window_size(self, tmp_path, monkeypatch):
        """接线守护：`AXIOM_PARSE_WINDOW_SIZE` 必须真的到达 `run_parse_job`，否则配置静默失效。"""
        engine, session, data_root = _file_env(tmp_path)
        session.close()
        captured: dict[str, object] = {}

        def fake_run(**kwargs) -> pipeline.ParseOutcome:
            captured.update(kwargs)
            return pipeline.ParseOutcome(job_id=kwargs["job_id"], status="completed", parsed=0, total=0)

        monkeypatch.setattr(jobs_module, "run_parse_job", fake_run)
        runner = JobRunner(
            session_factory=sessionmaker(bind=engine, future=True),
            engine_factory=lambda name: FakeEngine(),
            data_root=data_root,
            background=False,
            window_size=7,
        )

        runner.submit(job_id="job-1", book_id="b05", pages=None, engine="mineru")

        assert captured["window_size"] == 7


class _ProbingEngine(FakeEngine):
    """在每次调用引擎前用独立读连接快照本版本已提交的 parsed 页数。"""

    def __init__(self, engine, *, job_id: str = "job-1", explode_page: int | None = None, explode: type[Exception] = ValueError):
        super().__init__()
        self._engine = engine
        self._job_id = job_id
        self.explode_page = explode_page
        self.explode = explode
        self.snapshots: list[int] = []

    def _probe(self) -> None:
        with Session(self._engine) as reader:
            self.snapshots.append(PageRepository(reader).count_parsed("b05", self._job_id))

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
        self._probe()
        if self.explode_page is not None and page_no == self.explode_page:
            raise self.explode(f"第 {page_no} 页内部异常")
        return super().parse_page(image_bytes, page_no)

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        self._probe()
        return super().parse_document(pdf_bytes, filename, start_page=start_page, end_page=end_page)


class TestPerPagePersistence:
    """`PP-13`：页级提交与进度落库——运行中可查、异常不吞已解析页。"""

    def test_pages_visible_to_other_connection_during_run(self, tmp_path):
        """页级提交：第 n 页调用引擎时，前 n-1 页已对独立连接可见。"""
        engine, session, data_root = _file_env(tmp_path)
        probe = _ProbingEngine(engine)

        run_parse_job(
            session=session,
            engine=probe,
            job_id="job-1",
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
        )

        assert probe.snapshots == [0, 1]
        with Session(engine) as reader:
            assert PageRepository(reader).count_parsed("b05", "job-1") == 2

    def test_progress_written_every_ten_pages(self, tmp_path):
        engine, session, data_root = _file_env(tmp_path, pages=12)
        writes: list[int] = []
        original = ParseJobRepository.update_progress

        def spy(self, job_id, progress):  # noqa: ANN001
            writes.append(progress["parsed"])
            return original(self, job_id, progress)

        monkey = pytest.MonkeyPatch()
        monkey.setattr(ParseJobRepository, "update_progress", spy)
        try:
            run_parse_job(
                session=session,
                engine=FakeEngine(),
                job_id="job-1",
                book_id="b05",
                pages=list(range(1, 13)),
                data_root=data_root,
            )
        finally:
            monkey.undo()

        assert writes == [10], "12 页快跑（未到 1s 间隔）只在每 10 页节点写一次"
        with Session(engine) as reader:
            job = ParseJobRepository(reader).get("job-1")
        assert job is not None and job.status == "completed"
        assert job.progress["parsed"] == 12

    def test_non_engine_error_keeps_earlier_pages(self, tmp_path):
        """A3 回归：引擎抛非 EngineError 时，前序页行与产物必须已落库（不得被回滚吞掉）。"""
        engine, session, data_root = _file_env(tmp_path)
        probe = _ProbingEngine(engine, explode_page=2)

        with pytest.raises(ValueError, match="内部异常"):
            run_parse_job(
                session=session,
                engine=probe,
                job_id="job-1",
                book_id="b05",
                pages=[1, 2],
                data_root=data_root,
            )

        with Session(engine) as reader:
            assert PageRepository(reader).get("b05", "job-1", 1).status == "parsed"
            assert PageRepository(reader).get("b05", "job-1", 2) is None
            assert BookRepository(reader).get("b05").active_job_id is None, "异常中断不激活（V3）"
        assert (_version_dir(data_root, "job-1") / "pages" / "p0001.blocks.json").is_file()

    def test_whole_book_failure_keeps_parsed_pages(self, tmp_path, monkeypatch):
        """整本失败（EngineError 中途穿透）时已解析页保留，且 progress 记录已完成页。"""
        engine, session, data_root = _file_env(tmp_path)
        original = pipeline._write_page_artifacts

        def boom(book_dir: Path, page: BlocksPage) -> None:
            if page.page == 2:
                raise EngineError("落盘失败")
            original(book_dir, page)

        monkeypatch.setattr(pipeline, "_write_page_artifacts", boom)

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id="job-1",
            book_id="b05",
            pages=None,
            data_root=data_root,
        )

        assert outcome.status == "failed" and "落盘失败" in outcome.error
        with Session(engine) as reader:
            assert PageRepository(reader).get("b05", "job-1", 1).status == "parsed"
            assert BookRepository(reader).get("b05").active_job_id is None
            job = ParseJobRepository(reader).get("job-1")
            book = BookRepository(reader).get("b05")
        assert job is not None and job.status == "failed"
        assert job.progress["parsed"] == 1
        assert book is not None and book.pages_done == 1

    def test_persist_per_page_off_defers_to_caller(self, tmp_path):
        """退化开关：persist_per_page=False 时运行中不可见，由调用方统一提交。"""
        engine, session, data_root = _file_env(tmp_path)
        probe = _ProbingEngine(engine)

        run_parse_job(
            session=session,
            engine=probe,
            job_id="job-1",
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
            persist_per_page=False,
        )

        assert probe.snapshots == [0, 0]
        session.commit()
        with Session(engine) as reader:
            assert PageRepository(reader).count_parsed("b05", "job-1") == 2


class TestStatusSemanticsAdr0007:
    """ADR 0007：`completed` 与激活门槛同源、结局三态、书级进度/结局分列。"""
    def test_completed_and_activation_are_the_same_criterion(self, env):
        """齐备 → 同时 `completed` 与被激活；未齐备 → 同时非 `completed` 与未激活。"""
        session, data_root = env
        _ingest(session, data_root)

        ok_job = _create_job(session, job_id="job-ok")
        assert run_parse_job(
            session=session,
            engine=FakeEngine(),
            job_id=ok_job,
            book_id="b05",
            pages=None,
            data_root=data_root,
        ).status == "completed"
        book = BookRepository(session).get("b05")
        assert book.active_job_id == ok_job

        partial_job = _create_job(session, pages=[1, 2], job_id="job-partial")
        assert run_parse_job(
            session=session,
            engine=FakeEngine(fail_pages={1}),
            job_id=partial_job,
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
        ).status == "partial"
        # 「completed 但未激活」不可能出现：未齐备的任务既不判 completed 也不夺走生效指针
        assert BookRepository(session).get("b05").active_job_id == ok_job

    def test_zero_success_is_failed_not_partial(self, env):
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1, 2])

        outcome = run_parse_job(
            session=session,
            engine=FakeEngine(fail_pages={1, 2}),
            job_id=job_id,
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
        )

        assert outcome.status == "failed", "零页成功不是「部分成功」"
        assert outcome.failed_pages == [1, 2]
        job = ParseJobRepository(session).get(job_id)
        assert job is not None and job.status == "failed" and job.failed_pages == [1, 2]
        book = BookRepository(session).get("b05")
        assert book is not None and book.last_parse_result == "failed" and book.parse_status == "pending"

    def test_running_job_keeps_progress_only(self, env):
        """受理即 `parsing`（进度），结局列在终态前保持空值。"""
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session)

        ParseJobRepository(session).mark_running(job_id)
        BookRepository(session).update_parse("b05", parse_status="parsing", pages_done=0)
        session.commit()

        book = BookRepository(session).get("b05")
        assert book.parse_status == "parsing"
        assert book.last_parse_result == "", "结局只在终态写"

    def test_pipeline_never_writes_interrupted(self, env):
        """`interrupted` 只有启动期归一会写：管线自身四种结局都不产出它。"""
        session, data_root = env
        _ingest(session, data_root)
        job_id = _create_job(session, pages=[1, 2])

        run_parse_job(
            session=session,
            engine=FakeEngine(fail_pages={2}),
            job_id=job_id,
            book_id="b05",
            pages=[1, 2],
            data_root=data_root,
        )

        job = ParseJobRepository(session).get(job_id)
        assert job is not None and job.status != "interrupted"


class TestStartupReconciliationRunner:
    """ADR 0007 决定 4 的执行侧：启动期归一同时收敛书目进度，且只改最新任务书的结局。"""

    def _runner(self, engine, data_root: Path) -> JobRunner:
        return JobRunner(
            session_factory=sessionmaker(bind=engine, future=True),
            engine_factory=lambda name: FakeEngine(),
            data_root=data_root,
            background=False,
        )

    def test_interrupted_latest_job_sets_book_result(self, tmp_path):
        engine, session, data_root = _file_env(tmp_path)
        ParseJobRepository(session).mark_running("job-1")
        BookRepository(session).update_parse("b05", parse_status="parsing", pages_done=1)
        session.commit()
        session.close()

        runner = self._runner(engine, data_root)
        normalized = runner.reconcile_on_startup()

        assert normalized == [("job-1", "b05")]
        with Session(engine) as reader:
            job = ParseJobRepository(reader).get("job-1")
            book = BookRepository(reader).get("b05")
            assert job is not None and job.status == "interrupted"
            assert book is not None and book.parse_status == "pending"
            assert book.last_parse_result == "interrupted"

    def test_terminal_latest_job_keeps_its_result(self, tmp_path):
        """旧任务被归一时不得改写该书最近一次任务的结局（结局跟随最新任务）。"""
        engine, session, data_root = _file_env(tmp_path)
        jobs = ParseJobRepository(session)
        jobs.mark_running("job-1")  # 更早的任务：上次进程被 kill 的遗留
        jobs.create(ParseJobRow(job_id="job-2", book_id="b05", engine="mineru"))
        jobs.mark_finished("job-2", status="partial", error="失败页：2", failed_pages=[2])
        BookRepository(session).update_parse(
            "b05", parse_status="parsing", pages_done=1, last_parse_result="partial"
        )
        session.commit()
        session.close()

        runner = self._runner(engine, data_root)
        normalized = runner.reconcile_on_startup()

        assert normalized == [("job-1", "b05")], "只归一活动行，终态行不动"
        with Session(engine) as reader:
            book = BookRepository(reader).get("b05")
            assert book.parse_status == "pending", "进度收回 pending"
            assert book.last_parse_result == "partial", "结局跟随最新任务（job-2），不被更早的中断行覆盖"
            assert reader.get(AfParseJob, "job-2").status == "partial"

    def test_startup_reconciliation_is_idempotent(self, tmp_path):
        engine, session, data_root = _file_env(tmp_path)
        ParseJobRepository(session).mark_running("job-1")
        session.commit()
        session.close()
        runner = self._runner(engine, data_root)

        assert runner.reconcile_on_startup() != []
        assert runner.reconcile_on_startup() == []

