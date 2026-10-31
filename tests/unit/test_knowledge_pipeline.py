"""
模块职责：梳理通路的编排层守护——受理门三形与批次身份（W2）、两阶段执行体与锚点复核（W3）、
收尾与启动期归一（W4）、导出序（W5）、长文／删除通路（W6）与调用账接线（W7）。
设计关联（DesignRef）：docs/design/knowledge-analysis.md（KA-2、KA-4、KA-5、KA-6、KA-7、KA-9、
KA-10、KA-11、KA-13、KA-15、KA-18、KA-19、KA-20、KA-21、KA-22、KA-23、KA-24、KA-25、KA-26、
KA-27、KA-32、KA-33、KA-34、KA-35、KA-36、KA-37、KA-38、KA-39、KA-41、KA-42、KA-43）
设计关联（DesignRef）：docs/architecture/database-design.md（`af_kp` 的 `gen_status` 三态、批次分组键、
`links`/`role`/`type` 闭集与 `norm_key` 去重口径、「服务端不变式」的边端点存在与次序边 DAG）
设计关联（DesignRef）：docs/design/parsing-management.md（PM-13 的单 worker 串行前提）
实现状态：Current
被测代码：src/axiom_flow/orchestrator/knowledge.py、src/axiom_flow/orchestrator/jobs.py、
src/axiom_flow/db/llm_ledger.py、src/axiom_flow/db/repositories.py:KpRepository、
src/axiom_flow/engines/base.py（`KnowledgeEngine` 两阶段契约）
守护面：知识点梳理的受理门、批次身份、取证集构造、目录／知识点两阶段执行体与批次收尾归一，
以及每次模型调用在共享表 `qed_llm_calls` 上的一行追加账与「接线不全即拒跑」
失效后果：无生效版本被梳理、同书并发两批、重跑覆盖人已背书的结论、或中断批次残留成在飞时，
三态判据与「机器不与人工争同一身份」这条主线同时失真，结论无法回指
"""

from __future__ import annotations

import copy
import json
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.llm_ledger import LlmCallIdentity, LlmCallLedger
from axiom_flow.db.models import OP_KIND_KP, AfKp, AfOperation, Base
from axiom_flow.db.repositories import (
    KP_RESULT_KEYS,
    BookRepository,
    BookSyncRow,
    KpNodeRow,
    KpRepository,
    OperationRepository,
    PageRepository,
)
from axiom_flow.engines.base import (
    ChapterReply,
    EngineError,
    KnowledgeEngine,
    OutlineEntry,
    PageBody,
    ReferencePage,
    SectionInput,
    SectionPoints,
    TocPagePick,
    TocVerifyAnswer,
)
from axiom_flow.ingest import scan_transient_residue
from axiom_flow.normalize.toc import (
    TOC_PAGE_SAMPLE_MAX_CHARS,
    TOC_PAGE_SCAN_LIMIT,
    node_type_of,
    node_types_of,
)
from axiom_flow.orchestrator.jobs import JobRunner, KpLedgerWiringError
from axiom_flow.orchestrator.knowledge import (
    KpGateError,
    LedgeredKnowledgeEngine,
    SpanRow,
    accept_kp_job,
    anchor_review_flags,
    build_evidence_set,
    build_outline,
    finalize_batch,
    kp_batch_row,
    list_narrative_orphans,
    norm_key_for,
    order_for_export,
    regenerate_section_summary,
    remove_node,
    run_knowledge_job,
    section_spans_of,
    write_narrative,
)
from tests.support.llm_ledger_schema import create_ledger_table, read_ledger_rows


@pytest.fixture()
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def session(engine):
    with Session(engine) as session:
        yield session


def _book(
    session: Session,
    book_id: str = "b05",
    *,
    active_job_id: str | None = "job-a1",
    page_count: int = 20,
) -> None:
    """一台「已 ingest＋已生效」的书目：受理门只看这两件事，不看磁盘。"""
    BookRepository(session).upsert_many(
        [
            BookSyncRow(
                book_id=book_id,
                domain_id="math-advanced",
                course_id="math_analysis",
                course_name="数学分析",
                knowledge_id="kt-mathanalysis-3",
                title="数学分析原理",
                part="教程",
                display_title="Rudin 数学分析原理",
                authors="Walter Rudin",
                file_path="books/b05.pdf",
            )
        ]
    )
    BookRepository(session).update_ingest(book_id, page_count=page_count, sha256="a" * 64)
    BookRepository(session).set_active_job(book_id, active_job_id)
    session.commit()


def _draft(session: Session, node_id: str = "afkp-a1", **overrides) -> AfKp:
    payload = {
        "node_id": node_id,
        "book_id": "b05",
        "job_id": "job-a1",
        "name": "极限的 ε-δ 定义",
        "norm_key": "极限的ε-δ定义",
        "gen_status": "draft",
        "gen_batch": "afkp-0123456789ab",
        "model_ref": "qwen-vl",
    }
    payload.update(overrides)
    return KpRepository(session).create(KpNodeRow(**payload))


def _kp_rows(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(AfKp)) or 0)


def _kp_ledger_rows(session: Session) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(AfOperation).where(AfOperation.op_kind == OP_KIND_KP)
        )
        or 0
    )


class TestAcceptanceGate:
    """`KA-1`/`KA-4`/`KA-10`/`KA-25`：受理门的三个结局与批次身份，全部判在写入之前。"""

    def test_missing_book_is_404(self, session):
        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="nope")

        assert caught.value.status_code == 404
        assert _kp_rows(session) == 0

    def test_no_active_version_is_422_with_zero_writes(self, session):
        """`KA-1`：无生效版本判 422 且零写入——锚点将无处解释，不给「对着空气建批次」留路径。"""
        _book(session, active_job_id=None)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05")

        assert caught.value.status_code == 422
        assert "生效" in caught.value.detail
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (0, 0)

    def test_in_flight_batch_is_409_and_names_the_batch(self, session):
        """`KA-4`：在飞判据＝该书存在 `draft` 行，命中即 409 并点名既有批次。"""
        _book(session)
        _draft(session, "afkp-a1", gen_batch="afkp-ffffffffffff")

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05")

        assert caught.value.status_code == 409
        assert "afkp-ffffffffffff" in caught.value.detail
        assert _kp_rows(session) == 1

    def test_acceptance_returns_batch_id_and_evidence_version(self, session):
        """`KA-10`＋`KA-1`：批次号 `afkp-<hex12>` 由服务端发放，取证版本＝受理时的生效指针。"""
        _book(session, active_job_id="job-a1")

        accepted = accept_kp_job(session, book_id="b05")

        assert re.fullmatch(r"afkp-[0-9a-f]{12}", accepted.gen_batch)
        assert accepted.evidence_job_id == "job-a1"
        assert accepted.book_id == "b05"
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (0, 0)

    def test_two_acceptances_issue_different_batch_ids(self, session):
        """批次号是随机发放而非内容摘要：重跑换批，不要求批次名可排序（`KA-10`）。"""
        _book(session)

        first = accept_kp_job(session, book_id="b05")
        second = accept_kp_job(session, book_id="b05")

        assert first.gen_batch != second.gen_batch

    def test_default_scope_is_the_whole_book(self, session):
        """`KA-25`：默认整本书，取证区间＝`1..page_count`。"""
        _book(session, page_count=37)

        accepted = accept_kp_job(session, book_id="b05")

        assert (accepted.page_start, accepted.page_end) == (1, 37)

    def test_single_chapter_range_is_carried_into_the_acceptance(self, session):
        """`KA-25`：单章批次仍只有一个 `gen_batch`，取证集按页区间构造、裁剪仍按书计数。"""
        _book(session, page_count=37)

        accepted = accept_kp_job(session, book_id="b05", page_start=10, page_end=14)

        assert (accepted.page_start, accepted.page_end) == (10, 14)
        assert re.fullmatch(r"afkp-[0-9a-f]{12}", accepted.gen_batch)

    @pytest.mark.parametrize(
        ("page_start", "page_end"),
        [(0, 5), (1, 21), (14, 10), (10, None), (None, 10)],
    )
    def test_illegal_page_range_is_422_before_any_write(self, session, page_start, page_end):
        """`KA-25`：页区间要么完整要么没有，越界一律 422 且零写入。"""
        _book(session, page_count=20)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05", page_start=page_start, page_end=page_end)

        assert caught.value.status_code == 422
        assert _kp_rows(session) == 0


class TestInFlightJudge:
    """`KA-4`/`KA-9`：在飞判据只读 `gen_status`，不读流水行。"""

    def test_returns_the_draft_batch_of_that_book(self, session):
        _draft(session, "afkp-a1", gen_batch="afkp-111111111111")

        assert KpRepository(session).find_draft_batch("b05") == "afkp-111111111111"

    def test_finished_batches_are_not_in_flight(self, session):
        """`candidate`/`rejected` 都不算在飞：收尾与归一之后同书可再发一批。"""
        _draft(session, "afkp-a1", gen_status="candidate")
        _draft(session, "afkp-a2", gen_status="rejected")

        assert KpRepository(session).find_draft_batch("b05") is None

    def test_in_flight_uniqueness_is_per_book_not_per_chapter(self, session):
        """`KA-25` 的边界：单章批次同样按书算——别的书的批次不挡这本书。"""
        _draft(session, "afkp-a1", gen_batch="afkp-111111111111")
        _draft(session, "afkp-b1", book_id="b11", gen_batch="afkp-222222222222")

        assert KpRepository(session).find_draft_batch("b05") == "afkp-111111111111"
        assert KpRepository(session).find_draft_batch("b11") == "afkp-222222222222"
        assert KpRepository(session).find_draft_batch("b99") is None


class TestNoAutoScheduling:
    """`KA-24`：发起面唯一＝人工提交，版本激活不排批。"""

    def test_activation_creates_nothing(self, session):
        """一次版本激活后 `af_kp` 行数与 `op_kind='kp'` 流水行数逐本不变。"""
        _book(session, active_job_id=None)
        _draft(session, "afkp-a1", gen_batch="afkp-111111111111")
        before = (_kp_rows(session), _kp_ledger_rows(session))

        assert BookRepository(session).set_active_job("b05", "job-b2")
        session.commit()

        assert (_kp_rows(session), _kp_ledger_rows(session)) == before


# --------------------------------------------------------------------------- W3 两阶段执行体


#: 目录场景：1 章 2 节 + 一个第三层标题（`KA-6` 的被拒对象）；页 3/5/6 无标题。
_HEADING_LAYOUT: dict[int, list[tuple[int, str]]] = {
    1: [(1, "第 1 章 极限")],
    2: [(2, "1.1 数列极限")],
    4: [(2, "1.2 函数极限"), (3, "1.2.1 无穷小")],
}


def _pages_dir(
    tmp_path: Path,
    *,
    book_id: str = "b05",
    job_id: str = "job-a1",
    domain: str = "math-advanced",
    course: str = "math_analysis",
) -> Path:
    """版本目录里的页产物目录：数据根一律由 `tmp_path` 造，绝不指向真实数据根。"""
    pages = tmp_path / "parsed" / domain / course / book_id / "versions" / job_id / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    return pages


def _write_page_artifacts(
    pages_dir: Path, page_no: int, headings: list[tuple[int, str]], *, body: str | None = None
) -> None:
    """写一页的 `blocks.json` + `.md`：块序＝数组下标（`KA-18` 说的就是这块空间）。"""
    text = body or f"第 {page_no} 页 正文"
    blocks: list[dict] = []
    for level, title in headings:
        blocks.append(
            {
                "type": "heading",
                "bbox": [0, 0, 800, 60],
                "index": len(blocks),
                "order": len(blocks),
                "text": title,
                "level": level,
            }
        )
    blocks.append(
        {
            "type": "paragraph",
            "bbox": [0, 80, 800, 900],
            "index": len(blocks),
            "order": len(blocks),
            "text": text,
        }
    )
    payload = {"page": page_no, "image_size": [1000, 1400], "source": "mineru", "blocks": blocks}
    (pages_dir / f"p{page_no:04d}.blocks.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    markdown = "\n".join([f"{'#' * level} {title}" for level, title in headings] + [text])
    (pages_dir / f"p{page_no:04d}.md").write_text(markdown, encoding="utf-8")


def _scene(
    session: Session,
    tmp_path: Path,
    *,
    book_id: str = "b05",
    job_id: str = "job-a1",
    page_count: int = 6,
    layout: dict[int, list[tuple[int, str]]] | None = None,
    missing_artifacts: tuple[int, ...] = (),
    unparsed: tuple[int, ...] = (),
    section_kinds: dict[int, str] | None = None,
) -> Path:
    """一台「已生效且逐页有产物」的书：页行 + 版本目录页产物一起造，磁盘与 DB 不自洽由参数点名。"""
    _book(session, book_id, active_job_id=job_id, page_count=page_count)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    layout = _HEADING_LAYOUT if layout is None else layout
    repo = PageRepository(session)
    for page_no in range(1, page_count + 1):
        headings = layout.get(page_no, [])
        if page_no not in missing_artifacts:
            _write_page_artifacts(pages_dir, page_no, headings)
        repo.upsert(
            book_id,
            job_id,
            page_no,
            status="failed" if page_no in unparsed else "parsed",
            source="mineru",
            error="" if page_no not in unparsed else "引擎失败",
        )
        kind = (section_kinds or {}).get(page_no, "")
        if kind:
            # `section_kind` 本轮无写者（database-design.md「本轮只建列」），测试直填喂 `KA-5` 的读数。
            row = repo.get(book_id, job_id, page_no)
            assert row is not None
            row.section_kind = kind
    session.commit()
    return pages_dir


def _point_draft(name: str, *, page: int, blocks=(0,), **overrides) -> dict:
    payload = {
        "name": name,
        "type": "concept",
        "summary": f"{name} 的一句话总结",
        "anchors": [{"page": page, "block_indexes": list(blocks), "snippet": "回指正文"}],
        "links": None,
    }
    payload.update(overrides)
    return payload


class FakeKnowledgeEngine:
    """梳理引擎的桩：记录每次调用的入参与次序，草稿由用例给定（目录三条腿＋知识点一条腿）。

    目录三条腿的缺省读数＝「这本书里没有可认的目录页」（判页回空集 ⇒ 全书回落 `KA-33` 条目腿，
    零条目 ⇒ 退 `heading` 块）。这是最省事的一档形状：既让不关心目录取材的用例照旧走确定性通路，
    又不会把「模型没答」冒充成「模型答了但没有」。要走模型正腿的用例各自传入 callable。
    """

    name = "fake-text"

    def __init__(
        self,
        *,
        points_by_section: dict[str, list[dict]] | None = None,
        summaries_by_section: dict[str, str] | None = None,
        on_points=None,
        failing_sections: tuple[str, ...] = (),
        on_toc_pages=None,
        on_toc_entries=None,
        on_toc_verify=None,
        chapters_by_title: dict[str, tuple[str, str]] | None = None,
        failing_chapters: tuple[str, ...] = (),
    ) -> None:
        self.points_by_section = dict(points_by_section or {})
        self.summaries_by_section = dict(summaries_by_section or {})
        self.on_points = on_points
        self.failing_sections = set(failing_sections)
        self.on_toc_pages = on_toc_pages
        self.on_toc_entries = on_toc_entries
        self.on_toc_verify = on_toc_verify
        self.chapters_by_title = dict(chapters_by_title or {})
        self.failing_chapters = set(failing_chapters)
        self.calls: list[tuple[str, object]] = []

    def health(self) -> bool:
        return True

    def toc_pages(self, samples):
        self.calls.append(("toc_pages", samples))
        if self.on_toc_pages is not None:
            return self.on_toc_pages(samples)
        return TocPagePick()

    def toc_entries(self, page_no, text):
        self.calls.append(("toc_entries", page_no))
        if self.on_toc_entries is not None:
            return self.on_toc_entries(page_no, text)
        return []

    def toc_verify(self, items):
        self.calls.append(("toc_verify", tuple(item.name for item in items)))
        if self.on_toc_verify is not None:
            return self.on_toc_verify(items)
        return []

    def points(self, section) -> SectionPoints:
        """一节的两半回复（`KA-43`）：草稿数组＋节级总结。

        `on_points` 的老形状（返回列表）照旧接受——总结缺省空串，用例不必为一枚不关心的键改写法。
        """
        self.calls.append(("points", section))
        if section.title in self.failing_sections:
            raise EngineError(f"模型服务中断：{section.title}")
        produced = self.on_points(section) if self.on_points is not None else self.points_by_section.get(
            section.title, []
        )
        if isinstance(produced, SectionPoints):
            return produced
        return SectionPoints(list(produced), self.summaries_by_section.get(section.title, ""))

    def chapter(self, material) -> ChapterReply:
        """章腿（`KA-45`）：缺省回两段空串——不关心章总结的用例不必为一枚不看的键改写法。"""
        self.calls.append(("chapter", material))
        if material.title in self.failing_chapters:
            raise EngineError(f"模型服务中断：{material.title}")
        summary, narrative = self.chapters_by_title.get(material.title, ("", ""))
        return ChapterReply(summary=summary, narrative=narrative)


def _accepted(session: Session, book_id: str = "b05", **kwargs):
    return accept_kp_job(session, book_id=book_id, **kwargs)


def _rows(session: Session, *, node_kind: str | None = None) -> list[AfKp]:
    return KpRepository(session).list_for_book("b05", node_kind=node_kind)


def _confirmed_rows(session: Session) -> list[AfKp]:
    return list(session.scalars(select(AfKp).where(AfKp.confirmed == 1)))


class TestEvidenceSet:
    """`KA-2`：取证页集＝该版本 `parsed` 页 ∩ 请求区间 ∩ 产物在位，且不读 `chunks.json`。"""

    def test_only_parsed_pages_within_the_requested_range_enter_the_set(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)

        evidence = build_evidence_set(
            session, data_root=tmp_path, acceptance=_accepted(session, page_start=2, page_end=5)
        )

        assert [page.page_no for page in evidence.pages] == [2, 3, 4, 5]
        assert (evidence.missing_pages, evidence.unparsed_pages) == ((), ())
        assert evidence.job_id == "job-a1"

    def test_page_whose_artifact_is_gone_is_dropped_and_visible_in_the_count(self, session, tmp_path):
        """`KA-2`：缺文件的页剔出取证集且**计数可见**——缺页不建半批，也不静默缩水。"""
        _scene(session, tmp_path, page_count=6, missing_artifacts=(4,))

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        assert [page.page_no for page in evidence.pages] == [1, 2, 3, 5, 6]
        assert evidence.missing_pages == (4,)

    def test_unparsed_page_rows_are_not_evidence(self, session, tmp_path):
        """[PP-13] 的 `parsed` 门槛：未解析成功的页不进取证集，逐页缺口照样披露。"""
        _scene(session, tmp_path, page_count=6, unparsed=(5, 6))

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        assert [page.page_no for page in evidence.pages] == [1, 2, 3, 4]
        assert evidence.unparsed_pages == (5, 6)

    def test_headings_and_section_kind_come_from_the_page_row_and_blocks(self, session, tmp_path):
        """`KA-5` 的输入面：标题序列（层级＋块序）来自 `blocks.json`，页域角色来自 `af_pages`。"""
        _scene(
            session,
            tmp_path,
            page_count=4,
            layout={4: [(2, "1.2 函数极限"), (3, "1.2.1 无穷小")]},
            section_kinds={4: "chapter"},
        )

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        page4 = next(page for page in evidence.pages if page.page_no == 4)
        assert page4.section_kind == "chapter"
        assert [h.text for h in page4.headings] == ["1.2 函数极限", "1.2.1 无穷小"]
        assert [h.level for h in page4.headings] == [2, 3]
        assert [h.block_index for h in page4.headings] == [0, 1]
        assert page4.markdown.startswith("## 1.2 函数极限")

    def test_a_page_with_only_one_of_the_two_artifacts_is_not_half_a_batch(self, session, tmp_path):
        """`KA-2`：`.md` 与 `.blocks.json` 只有一件＝半批——剔出取证集并计入 `missing_pages`。"""
        pages_dir = _scene(session, tmp_path, page_count=3, layout={1: [(1, "第 1 章 极限")]})
        (pages_dir / "p0002.md").unlink()

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        assert [page.page_no for page in evidence.pages] == [1, 3]
        assert evidence.missing_pages == (2,)

    def test_derived_chunk_file_is_never_part_of_the_evidence(self, session, tmp_path):
        """`KA-2` 的边界：分块文件是检索单元，知识块与知识点只共享坐标空间——本通路不读它。"""
        pages_dir = _scene(session, tmp_path, page_count=2)
        chunks = pages_dir.parent / "chunks.json"
        chunks.write_text("{ 这不是合法的分块文件", encoding="utf-8")

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        assert [page.page_no for page in evidence.pages] == [1, 2]
        assert (evidence.missing_pages, evidence.unparsed_pages) == ((), ())


def _relabel(
    session: Session,
    *,
    page_no: int,
    block_index: int,
    corrected_type: str | None,
    book_id: str = "b05",
    job_id: str = "job-a1",
) -> None:
    """落一枚人工改判意图行（回放腿的输入侧）：走在册 `upsert_edit`，不手拼载荷。"""
    OperationRepository(session).upsert_edit(
        book_id,
        job_id,
        page_no,
        block_index,
        block_type="heading",
        verdict="ok",
        corrected_type=corrected_type,
    )
    session.commit()


class TestHumanRelabelIsReplayed:
    """取证面读的是**改判后**的类型（PLAN-026 W3／`KA-2` 的人工侧）：读时回放、不写产物。

    四条判据各有取向：前两条形＝改判生效（去掉页眉、补上漏判的标题）；后两条形是反向断言——
    改判行绑在**别的解析版本**上不套用，只记 verdict 没记 `corrected_type` 的行不动任何读数。
    """

    def test_a_relabelled_heading_stops_being_outline_input(self, session, tmp_path):
        """页眉判成 `header` 后目录阶段收不到它——这就是「梳理展示去掉页眉页脚」的取材侧。"""
        _scene(session, tmp_path, page_count=3, layout={1: [(1, "第 1 章 极限")], 2: [(2, "2.1 数列极限")]})
        _relabel(session, page_no=1, block_index=0, corrected_type="header")

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        page1 = next(page for page in evidence.pages if page.page_no == 1)
        assert page1.headings == ()
        page2 = next(page for page in evidence.pages if page.page_no == 2)
        assert [h.text for h in page2.headings] == ["2.1 数列极限"], "未改判的页照原样取证"

    def test_a_relabelled_paragraph_enters_the_heading_track(self, session, tmp_path):
        """反方向也通：正文判成 `heading` 就进目录阶段，产物无 `level` 时按 1 级（在册 `level or 1`）。"""
        _scene(session, tmp_path, page_count=2, layout={})
        _relabel(session, page_no=1, block_index=0, corrected_type="heading")

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        page1 = next(page for page in evidence.pages if page.page_no == 1)
        assert [h.text for h in page1.headings] == ["第 1 页 正文"]
        assert [h.level for h in page1.headings] == [1]
        assert [h.block_index for h in page1.headings] == [0]

    def test_a_relabel_bound_to_another_version_is_not_replayed(self, session, tmp_path):
        """块下标只在解析版本内有意义：旧版本的改判不得套到生效版（与 edit 绑 `job_id` 的在册语义一致）。"""
        _scene(session, tmp_path, page_count=2, layout={1: [(1, "第 1 章 极限")]})
        _relabel(session, job_id="job-an-older-version", page_no=1, block_index=0, corrected_type="header")

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        page1 = next(page for page in evidence.pages if page.page_no == 1)
        assert [h.text for h in page1.headings] == ["第 1 章 极限"]

    def test_an_edit_without_a_relabel_moves_nothing(self, session, tmp_path):
        """「没判」与「判成某种」分得开：只记 verdict／corrected_text 的行不覆盖类型。"""
        _scene(session, tmp_path, page_count=2, layout={1: [(1, "第 1 章 极限")]})
        _relabel(session, page_no=1, block_index=0, corrected_type=None)

        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=_accepted(session))

        page1 = next(page for page in evidence.pages if page.page_no == 1)
        assert [h.text for h in page1.headings] == ["第 1 章 极限"]


#: 整本每页都重复的那一条章名眉：引擎判成 `heading`，所以它既进目录抽取又留在页正文里——W5 要治的就是它。
_FURNITURE_HEAD = "PRINCIPLES OF MATHEMATICAL ANALYSIS"


def _write_furniture_page(
    pages_dir: Path, page_no: int, *, titles: list[tuple[int, str]] | None = None
) -> None:
    """一页的「有家具」产物：跨页重复的章名眉（第 0 块）＋本页标题＋本页正文＋本页页码。

    `.md` 按在册 `blocks_to_markdown` 的形状组装（heading 带 `#` 前缀、块间空行、结尾一个换行）：
    剔除判据吃的是「产物里那一行」，形状写错用例就白跑。页码判成 `paragraph`（引擎漏判的常见形状），
    各页文本不同 ⇒ 线维判不出它，只能由人工改判那条腿接走。
    """
    blocks: list[dict] = [
        {"type": "heading", "bbox": [72, 58, 1168, 98], "index": 0, "order": 0,
         "text": _FURNITURE_HEAD, "level": 1}
    ]
    lines = [f"# {_FURNITURE_HEAD}"]
    for level, title in titles or []:
        blocks.append(
            {
                "type": "heading",
                "bbox": [72, 200 + len(blocks) * 120, 1168, 240 + len(blocks) * 120],
                "index": len(blocks),
                "order": len(blocks),
                "text": title,
                "level": level,
            }
        )
        lines.append(f"{'#' * level} {title}")
    for text in (f"第 {page_no} 页 正文", f"第 {page_no} 页"):
        blocks.append(
            {
                "type": "paragraph",
                "bbox": [72, 300 + len(blocks) * 200, 1168, 340 + len(blocks) * 200],
                "index": len(blocks),
                "order": len(blocks),
                "text": text,
            }
        )
        lines.append(text)
    payload = {"page": page_no, "image_size": [1240, 1754], "source": "mineru", "blocks": blocks}
    (pages_dir / f"p{page_no:04d}.blocks.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (pages_dir / f"p{page_no:04d}.md").write_text("\n\n".join(lines) + "\n", encoding="utf-8")


def _furniture_scene(
    session: Session,
    tmp_path: Path,
    *,
    page_count: int = 4,
    job_id: str = "job-a1",
    titles: dict[int, list[tuple[int, str]]] | None = None,
) -> Path:
    """一条章名眉贯全书的书：逐页 parsed 页行＋成对产物，返回 `pages` 目录。"""
    _book(session, active_job_id=job_id, page_count=page_count)
    pages_dir = _pages_dir(tmp_path, job_id=job_id)
    repo = PageRepository(session)
    for page_no in range(1, page_count + 1):
        _write_furniture_page(pages_dir, page_no, titles=(titles or {}).get(page_no))
        repo.upsert("b05", job_id, page_no, status="parsed", source="mineru")
    session.commit()
    return pages_dir


def _captured_bodies(session: Session, tmp_path: Path, accepted) -> dict[int, str]:
    """跑完整批，回收「送进模型的每页正文副本」：知识点阶段的入参就是这条通路的剔除面。"""
    seen: dict[int, str] = {}

    def capture(section):
        for page in section.pages:
            seen[page.page_no] = page.markdown
        return []

    run_knowledge_job(
        session,
        data_root=tmp_path,
        engine=FakeKnowledgeEngine(on_points=capture),
        acceptance=accepted,
    )
    return seen


class TestCurationStripsFurniture:
    """梳理取材默认剔除页眉页脚（PLAN-026 W5 改判／第二令 R10）：读时投影，产物一字不改。

    改判后的形状＝不造 `section_kind` 写手，两阶段各自的入参各治一件事：目录阶段收不到落在家具线上的
    标题，知识点阶段收不到家具行。两条来源腿分得开——线维来自 `furniture_hints`（跨页重复），类型维来自
    人工改判的回放（各页不同的页码只有这一条腿能接走）。
    """

    def test_a_running_head_does_not_reach_the_outline_readings(self, session, tmp_path):
        """线维进目录读数的剔除面：每页都有的章名眉不再是目录行，同页的真标题照旧在。"""
        _furniture_scene(session, tmp_path, titles={2: [(2, "1.1 实数与有序对")]})
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        readings = {page.page_no: [h.text for h in page.headings] for page in evidence.pages}
        assert sorted(readings) == [1, 2, 3, 4]
        assert all(_FURNITURE_HEAD not in texts for texts in readings.values())
        assert readings[2] == ["1.1 实数与有序对"], "真标题不受影响"

        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())
        assert [node.row.name for node in outline.nodes] == ["1.1 实数与有序对"]

    def test_the_repeated_head_line_leaves_the_body_copy_sent_to_the_engine(self, session, tmp_path):
        """线维进正文面：目录阶段零产出时整段正文自成一节，副本里不该再出现那条眉。"""
        _furniture_scene(session, tmp_path)

        seen = _captured_bodies(session, tmp_path, _accepted(session))

        assert all(_FURNITURE_HEAD not in markdown for markdown in seen.values())
        assert seen[1].strip() == "第 1 页 正文\n\n第 1 页", "正文与本页页码（未判家具）留下"

    def test_a_relabelled_folio_line_leaves_only_the_page_it_was_judged_on(self, session, tmp_path):
        """类型维按页生效：判掉第 1 页的页码，别页的页码行照原样在副本里——剔除不跨页传染。"""
        _furniture_scene(session, tmp_path)
        _relabel(session, page_no=1, block_index=2, corrected_type="page_number")  # 第 2 块＝本页页码行

        seen = _captured_bodies(session, tmp_path, _accepted(session))

        assert seen[1].strip() == "第 1 页 正文", "整行等于页码才剔：正文里那串同前缀的字句留下"
        assert seen[2].strip().splitlines()[-1] == "第 2 页"

    def test_the_page_artifact_and_its_evidence_markdown_stay_verbatim(self, session, tmp_path):
        """剔除只在送模型的副本上：磁盘 `pXXXX.md` 与取证面的 `markdown` 都还是产物原文，家具读数另行给出。"""
        pages_dir = _furniture_scene(session, tmp_path)
        before = (pages_dir / "p0001.md").read_text(encoding="utf-8")
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )

        assert (pages_dir / "p0001.md").read_text(encoding="utf-8") == before
        page1 = next(page for page in evidence.pages if page.page_no == 1)
        assert _FURNITURE_HEAD in page1.markdown
        assert page1.furniture == (_FURNITURE_HEAD.casefold(),)

    def test_a_book_without_furniture_is_read_verbatim(self, session, tmp_path):
        """反向判据：无重复线也无改判 ⇒ 每页家具读数为空，取证面逐字等于产物（默认剔≠默认删行）。"""
        pages_dir = _scene(session, tmp_path, page_count=3)

        evidence = build_evidence_set(
            session, data_root=tmp_path, acceptance=_accepted(session, page_start=1, page_end=3)
        )

        assert all(page.furniture == () for page in evidence.pages)
        for page in evidence.pages:
            assert page.markdown == (pages_dir / f"p{page.page_no:04d}.md").read_text(encoding="utf-8")


#: 目录页的一页条目（现网整本零 `heading` 的教材就是这个形状：章名与节名一律落 `paragraph`）。
#: 绝对页＝印刷页＋1（前言占一页），故偏移 1 支持三条、末条「附录 习题解答 …… 201」落在取证面之外。
_TOC_LINES = "第1章 集合论 …… 1\n序的关系与集合的势 …… 2\n第2章 实数与极限 …… 3\n附录 习题解答 …… 201"


def _toc_scene(session: Session, tmp_path: Path, *, book_id: str = "b05", job_id: str = "job-a1") -> Path:
    """一本「零 `heading` 块＋有自己目录页」的书：正文页各以一条条目名起头，供回定位命中。"""
    _book(session, book_id, active_job_id=job_id, page_count=4)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    bodies = {
        1: _TOC_LINES,
        2: "第1章 集合论\n正文开始。",
        3: "序的关系与集合的势\n正文继续。",
        4: "第2章 实数与极限\n正文又起。",
    }
    repo = PageRepository(session)
    for page_no in range(1, 5):
        _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


def _toc_scene_with_symbol_page(
    session: Session, tmp_path: Path, *, locatable: int = 3, book_id: str = "b05", job_id: str = "job-a1"
) -> Path:
    """p1 真目录页（绝对页＝印刷页＋2）、p2 符号表页（行形状与目录页相同，名字在正文里查不到）。

    `locatable` 决定正文里放几枚条目名：3 枚 ⇒ 偏移标定成功；1 枚 ⇒ 支持数不足、标定不出。
    """
    _book(session, book_id, active_job_id=job_id, page_count=5)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    bodies = {
        1: "第1章 集合论 …… 1\n第2章 实数与极限 …… 2\n第3章 数列与级数 …… 3",
        2: "∈ 属于 …… 10\n⊂ 包含符号 …… 11\nsup 最小上界 …… 12",
        3: "第1章 集合论\n正文开始。",
        4: "第2章 实数与极限\n正文又起。" if locatable >= 3 else "无关正文。",
        5: "第3章 数列与级数\n正文收尾。" if locatable >= 3 else "无关正文。",
    }
    repo = PageRepository(session)
    for page_no in range(1, 6):
        _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


class TestTocEntryLeg:
    """`KA-33` 回落腿进管线：整本零 `heading` 的书照样出目录行，锚点只认窗口内命中。

    本条腿现在是**回落腿**（`KA-35` 改判）：判页腿拿不到可用地、或模型整理全书零条目时才发起。
    桩引擎不交判页读数，所以本类走的正是这条回落通路——未定位的那些仍成目录行（读数落回目录页）
    并把条目名送进批次流水点名。产物文件与 `af_pages` 的任何列都不经这条腿（读数只补「页与块」两个坐标）。
    """

    def test_a_book_with_zero_heading_blocks_still_feeds_the_outline_stage(self, session, tmp_path):
        """现网形状（整本零 `heading`）：条目腿给出两层读数，章行的读数落在正文页而不是目录页。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())

        readings = [
            (node.row.anchors[0]["page"], node.row.name, node.row.depth, node.row.anchors[0]["block_indexes"])
            for node in outline.nodes
        ]
        assert readings == [
            (2, "第1章 集合论", 1, [0]),
            (3, "序的关系与集合的势", 2, [0]),
            (4, "第2章 实数与极限", 1, [0]),
            (1, "附录 习题解答", 2, [0]),  # 未定位条目落回目录页自身，仍成一行
        ]
        assert all(page.headings == () for page in evidence.pages), "条目腿不再合成 heading 读数"
        assert outline.unlocated == ("附录 习题解答",)
        assert {node.row.model_ref for node in outline.nodes} == {"rule:toc"}

    def test_the_chapter_rows_are_anchored_inside_the_body_pages(self, session, tmp_path):
        """判据正文那句「出两层目录且章行锚点在正文页内」：条目腿给什么坐标，目录行就用什么坐标。

        这里不手写页号——断言真能落到「条目腿给了什么坐标」上；换掉那条腿，这几行锚点立刻跟着变。
        """
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )

        assert [(row.name, row.depth, row.anchors[0]["page"]) for row in _rows(session, node_kind="outline")] == [
            ("第1章 集合论", 1, 2),
            ("序的关系与集合的势", 2, 3),
            ("第2章 实数与极限", 1, 4),
            ("附录 习题解答", 2, 1),  # 未定位条目落回目录页，仍按条目序排在末位
        ]

    def test_the_unlocated_entries_are_named_in_the_batch_ledger(self, session, tmp_path):
        """账面点名不依赖模型产出：判页腿没认出目录页时，未定位条目名照样进 `result` 的第十六键。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )

        assert kp_batch_row(accepted, outcome).result["unlocated_outline_entries"] == ["附录 习题解答"]

    def test_a_clean_batch_emits_an_empty_array_not_a_missing_key(self, session, tmp_path):
        """零未定位＝空数组不出缺键（`KA-32` 同一条口径），且新键必须已进闭集（`KA-27`）。"""
        _scene(session, tmp_path, page_count=4)
        accepted = _accepted(session)

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )

        assert "unlocated_outline_entries" in KP_RESULT_KEYS, "新键不进闭集就是自由键（`KA-27`）"
        assert kp_batch_row(accepted, outcome).result["unlocated_outline_entries"] == []

    def test_a_book_without_a_toc_page_is_read_exactly_as_before(self, session, tmp_path):
        """反向判据：无目录页 ⇒ 本腿零输出，取证面的标题读数只来自真 `heading` 块，成行走 heading 位。"""
        _scene(session, tmp_path, page_count=4, layout={1: [(1, "第 1 章 极限")], 2: [(2, "1.1 数列极限")]})
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        assert {
            page.page_no: [h.text for h in page.headings]
            for page in evidence.pages
            if page.headings
        } == {1: ["第 1 章 极限"], 2: ["1.1 数列极限"]}
        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())
        assert [(node.row.name, node.row.model_ref) for node in outline.nodes] == [
            ("第 1 章 极限", "rule:heading"),
            ("1.1 数列极限", "rule:heading"),
        ]
        assert (outline.unlocated, outline.dropped_toc_pages) == ((), ())

    def test_the_entry_leg_writes_nothing_to_disk_or_the_pages_table(self, session, tmp_path):
        """合成读数只在读取投影里：页产物两件逐字不动，`af_pages` 的列一个不碰。"""
        pages_dir = _toc_scene(session, tmp_path)
        before = {
            name: (pages_dir / name).read_text(encoding="utf-8")
            for name in ("p0001.md", "p0001.blocks.json", "p0002.md", "p0002.blocks.json")
        }
        pages_before = [
            (row.page_no, row.status, row.section_kind)
            for row in PageRepository(session).list_for_book("b05", "job-a1")
        ]

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=_accepted(session),
        )

        assert {
            name: (pages_dir / name).read_text(encoding="utf-8") for name in before
        } == before
        assert [
            (row.page_no, row.status, row.section_kind)
            for row in PageRepository(session).list_for_book("b05", "job-a1")
        ] == pages_before

    def test_a_symbol_table_page_is_dropped_whole_when_its_entries_never_locate(self, session, tmp_path):
        """页级复核（`KA-33`）：符号表／索引页与目录页行形状相同，命中率过半不中 ⇒ 整页剔除并披露页号。"""
        _toc_scene_with_symbol_page(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())

        assert [(node.row.anchors[0]["page"], node.row.name) for node in outline.nodes] == [
            (3, "第1章 集合论"),
            (4, "第2章 实数与极限"),
            (5, "第3章 数列与级数"),
        ]
        assert outline.dropped_toc_pages == (2,)
        assert outline.unlocated == ()

    def test_no_page_is_dropped_when_the_offset_cannot_be_calibrated(self, session, tmp_path):
        """偏移标定不出时不做页级复核：宁可多行，不可丢目录（符号表页同样留着）。"""
        _toc_scene_with_symbol_page(session, tmp_path, locatable=1)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())

        assert outline.dropped_toc_pages == ()
        assert len(outline.nodes) == 6


def _two_toc_page_scene(
    session: Session, tmp_path: Path, *, book_id: str = "b05", job_id: str = "job-a1"
) -> Path:
    """两枚目录页（p1／p2）＋五枚正文页：绝对页＝印刷页＋2（扉页与前言占两页）。

    「单页整理失败只坏那一页」这条判据（`KA-37`）在一枚目录页的书上判不出来——只有一枚页时，
    回落与全书回落读起来是同一份读数。
    """
    _book(session, book_id, active_job_id=job_id, page_count=7)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    bodies = {
        1: "第1章 集合论 …… 1\n序的关系与集合的势 …… 2\n第2章 实数与极限 …… 3",
        2: "第3章 连续 …… 4\n第4章 微分 …… 5\n附录 习题解答 …… 201",
        3: "第1章 集合论\n正文开始。",
        4: "序的关系与集合的势\n正文继续。",
        5: "第2章 实数与极限\n正文又起。",
        6: "第3章 连续\n正文再续。",
        7: "第4章 微分\n正文收尾。",
    }
    repo = PageRepository(session)
    for page_no in range(1, 8):
        _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


class TestModelLedOutline:
    """`KA-35`／`KA-37`：模型判页＋逐页整理是目录正腿，条目腿只在正腿拿不到行时补位。

    正本优先级三段在这类里分别判：模型整理出的条目为正本 → 单页整理失败只有那一页回落 → 判页不成
    （空集或抛错）整本回落条目腿。`KA-37` 的调用上界与「任一失败形态都不产生目录零行」同轴。
    """

    def test_the_model_organized_entries_are_the_trunk(self, session, tmp_path):
        """模型条目为正本：行序＝模型在目录页里的出现次序，层级与页码照模型给的用，机器不重排。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [(node.row.name, node.row.depth, node.row.model_ref) for node in outline.nodes] == [
            ("第1章 集合论", 1, "llm:toc"),
            ("序的关系与集合的势", 2, "llm:toc"),
            ("第2章 实数与极限", 1, "llm:toc"),
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4], "页码定锚走 KA-33 的偏移"
        by_id = {node.row.node_id: node.row.name for node in outline.nodes}
        assert [node.row.parent_id and by_id[node.row.parent_id] for node in outline.nodes] == [
            None,
            "第1章 集合论",
            None,
        ]
        assert all(node.row.summary == "" for node in outline.nodes), "结构性事实机器给，描述文字交人工"
        assert (outline.toc_pages, outline.fallback_pages, outline.unlocated) == ((1,), (), ())
        assert [step for step, _ in engine.calls] == ["toc_pages", "toc_entries"]

    def test_the_organize_legs_dirty_names_are_cleaned_before_they_are_registered(self, session, tmp_path):
        """`KA-41`：整理腿的名字入册前过同一把清洗——脏名让前缀命中永不成立，锚就落回目录页自己。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论 ……"),
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势 ……"),
                OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限 ……"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [node.row.name for node in outline.nodes] == [
            "第1章 集合论",
            "序的关系与集合的势",
            "第2章 实数与极限",
        ], "屏上看到的就是入册的那份名字"
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4], "锚指正文页而不是目录页 p1"
        assert outline.unlocated == ()

    def test_a_page_the_model_left_inside_the_name_is_recovered(self, session, tmp_path):
        """`KA-41`：模型把页码留在名字里时以回收值为准——行末数字填 `printed_page` 的缺位。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=None, name="第1章 集合论 …… 1"),
                OutlineEntry(level=2, printed_page=None, name="序的关系与集合的势 …… 2"),
                OutlineEntry(level=1, printed_page=None, name="第2章 实数与极限 …… 3"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [node.row.name for node in outline.nodes] == [
            "第1章 集合论",
            "序的关系与集合的势",
            "第2章 实数与极限",
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4], "回收出的页码同样能标定偏移"
        assert outline.unlocated == ()

    def test_a_glued_model_row_splits_and_the_second_entry_takes_its_own_shape_level(self, session, tmp_path):
        """`KA-41`：黏行拆分对模型行同样生效，拆出的第二段层级由它自己的行形状给，不继承模型那一枚。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论 …… 1序的关系与集合的势 …… 2"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [(node.row.name, node.row.depth) for node in outline.nodes] == [
            ("第1章 集合论", 1),
            ("序的关系与集合的势", 2),
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3]

    def test_a_failing_page_falls_back_only_for_that_page(self, session, tmp_path):
        """单页失败域（`KA-37`）：坏的那一页由条目腿补行、逐条目 `model_ref` 两值并存，其它页照出模型行。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        def organize(page_no, text):
            if page_no == 1:
                raise EngineError("整理腿超时：p1")
            return [
                OutlineEntry(level=1, printed_page=4, name="第3章 连续"),
                OutlineEntry(level=1, printed_page=5, name="第4章 微分"),
                OutlineEntry(level=2, printed_page=201, name="附录 习题解答"),
            ]

        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1, 2)), on_toc_entries=organize
        )

        outline = build_outline(accepted, evidence, engine)

        assert [(node.row.name, node.row.model_ref) for node in outline.nodes] == [
            ("第1章 集合论", "rule:toc"),
            ("序的关系与集合的势", "rule:toc"),
            ("第2章 实数与极限", "rule:toc"),
            ("第3章 连续", "llm:toc"),
            ("第4章 微分", "llm:toc"),
            ("附录 习题解答", "llm:toc"),
        ]
        assert outline.fallback_pages == (1,)
        assert outline.toc_pages == (1, 2)
        assert outline.unlocated == ("附录 习题解答",)
        # 正腿的行不靠名字判身份（`KA-39`：来源＝判页与整理两步由模型给），只有回落腿才按名字集合判。
        assert outline.reference_rows == 0

    def test_the_fallback_page_rows_land_in_the_batch_ledger(self, session, tmp_path):
        """并进账面（`KA-37`）：判出的目录页与回落页都要在流水 `result` 里点得出名，且新键已进闭集。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")

        def organize(page_no, text):
            if page_no == 1:
                raise EngineError("整理腿超时：p1")
            return [OutlineEntry(level=1, printed_page=4, name="第3章 连续")]

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_toc_pages=lambda samples: TocPagePick(toc_pages=(1, 2)), on_toc_entries=organize
            ),
            acceptance=accepted,
        )

        result = kp_batch_row(accepted, outcome).result
        assert result["outline_toc_pages"] == [1, 2]
        assert result["outline_fallback_pages"] == [1]
        assert result["dropped_toc_pages"] == []
        assert outcome.outline_rows == 4, "回落保证有目录可看，而不是什么都没有（KA-35）"
        assert {"outline_toc_pages", "outline_fallback_pages", "verified_outline_entries", "reference_rows"} <= set(
            KP_RESULT_KEYS
        )
        assert len(KP_RESULT_KEYS) == 28, "二十八键（`KA-27`）：新键不进闭集就是自由键"

    def test_an_empty_page_pick_falls_back_to_the_entry_leg_for_the_whole_book(self, session, tmp_path):
        """判页空集 ⇒ 整本退条目腿，整理腿一次都不发起（`KA-35` 第二段优先级）。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(),
            on_toc_entries=lambda page_no, text: pytest.fail("判页空集时不该发起整理腿"),
        )

        outline = build_outline(accepted, evidence, engine)

        assert {node.row.model_ref for node in outline.nodes} == {"rule:toc"}
        assert len(outline.nodes) == 6
        assert outline.toc_pages == (1, 2), "全书回落时账面点名的目录页由条目腿自己扫出来"
        assert [step for step, _ in engine.calls] == ["toc_pages", "toc_verify"]

    def test_a_page_pick_error_falls_back_without_calling_the_organize_leg(self, session, tmp_path):
        """判页抛错与判页空集折成同一形态（`KA-37` 的「三条 `None` 来路」）：整本回落、整理腿零调用。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        def pick_fails(samples):
            raise EngineError("判页腿网关超时")

        engine = FakeKnowledgeEngine(
            on_toc_pages=pick_fails,
            on_toc_entries=lambda page_no, text: pytest.fail("判页失败时不该发起整理腿"),
        )

        outline = build_outline(accepted, evidence, engine)

        assert {node.row.model_ref for node in outline.nodes} == {"rule:toc"}
        assert len(outline.nodes) == 6

    def test_every_organize_page_failing_still_yields_an_outline(self, session, tmp_path):
        """整理腿全坏 ⇒ 逐页回落，目录照样出行（`KA-37`「任一失败形态都不产生目录零行」）。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        def organize_fails(page_no, text):
            raise EngineError(f"整理腿超时：p{page_no}")

        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1, 2)), on_toc_entries=organize_fails
        )

        outline = build_outline(accepted, evidence, engine)

        assert outline.fallback_pages == (1, 2)
        assert {node.row.model_ref for node in outline.nodes} == {"rule:toc"}
        assert len(outline.nodes) == 6

    def test_a_third_level_row_is_counted_and_never_becomes_a_node(self, session, tmp_path):
        """层级越界计数（`KA-35`／`KA-6`）：模型给 `level=3` 的行不进节点，但要数得出丢了几行。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=3, printed_page=2, name="1.1.1 子条目"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [node.row.name for node in outline.nodes] == ["第1章 集合论"]
        assert outline.dropped_depth == 1

    def test_a_section_before_any_chapter_gets_no_parent(self, session, tmp_path):
        """无前置章不造父（`KA-26` 同轴）：正腿第一条就是节时父为空，其后的节挂前面最近那条章。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=2, printed_page=3, name="第2章 实数与极限"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        by_id = {node.row.node_id: node.row.name for node in outline.nodes}
        assert [
            (node.row.name, node.row.depth, node.row.parent_id and by_id[node.row.parent_id])
            for node in outline.nodes
        ] == [
            ("序的关系与集合的势", 2, None),
            ("第1章 集合论", 1, None),
            ("第2章 实数与极限", 2, "第1章 集合论"),
        ]

    def test_the_stage_call_count_is_bounded_by_one_plus_pages_plus_one(self, session, tmp_path):
        """调用次数上界（`KA-37`）：判页一次＋逐页整理每页一次＋坏行核对一次，不随正文页数膨胀。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        rows_by_page = {
            1: [OutlineEntry(level=1, printed_page=1, name="第1章 集合论")],
            2: [
                OutlineEntry(level=1, printed_page=4, name="第3章 连续"),
                OutlineEntry(level=2, printed_page=3, name="一致连续的判别法"),
            ],
        }
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1, 2)),
            on_toc_entries=lambda page_no, text: list(rows_by_page[page_no]),
        )

        outline = build_outline(accepted, evidence, engine)

        steps = [step for step, _ in engine.calls]
        assert steps == ["toc_pages", "toc_entries", "toc_entries", "toc_verify"]
        assert len(steps) <= 1 + len(outline.toc_pages) + 1

    def test_the_page_pick_samples_are_the_first_and_last_ten_pages(self, session, tmp_path):
        """取材封顶（`KA-37`）：判页只送前 10 页与末 10 页，且每页样本带块类型、不超 400 字符。"""
        _scene(session, tmp_path, page_count=25)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine()

        build_outline(accepted, evidence, engine)

        picks = [payload for step, payload in engine.calls if step == "toc_pages"]
        assert len(picks) == 1, "判页整批一次调用（KA-37）"
        samples = picks[0]
        assert [sample.page_no for sample in samples] == [
            *range(1, TOC_PAGE_SCAN_LIMIT + 1),
            *range(25 - TOC_PAGE_SCAN_LIMIT + 1, 26),
        ], "目录是局部事实：首尾各十页（枚数由 `KA-37` 的常量给）"
        assert samples[0].text == "[heading] 第 1 章 极限\n[paragraph] 第 1 页 正文"
        assert all(len(sample.text) <= TOC_PAGE_SAMPLE_MAX_CHARS for sample in samples)

    def test_a_short_book_sends_every_page_exactly_once(self, session, tmp_path):
        """首尾窗口重叠时按页去重：短书不能把同一页送两遍，那等于替模型造第二枚候选页。"""
        _scene(session, tmp_path, page_count=6)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine()

        build_outline(accepted, evidence, engine)

        samples = [payload for step, payload in engine.calls if step == "toc_pages"][0]
        assert [sample.page_no for sample in samples] == [1, 2, 3, 4, 5, 6]


def _progressive_scene(session: Session, tmp_path: Path, *, book_id: str = "b05", job_id: str = "job-a1") -> Path:
    """两枚目录页（p1／p2）＋三枚正文页：绝对页＝印刷页＋2，且 p1 单独到齐时标定不出偏移。

    逐页落行（`KA-40`）要判的三种读数都在这一形里：p1 只有第 1 章能在正文查到（支持数 1 ＜
    `TOC_MIN_OFFSET_SUPPORT`）⇒ 早落的行先锚在目录页自己；p2 到齐后支持数变 2 ⇒ 定稿把那枚命中的行
    改写到正文页；正文里查不到的那条两次都锚在目录页 ⇒ 用来判「定稿写手不多改一行」。
    """
    _book(session, book_id, active_job_id=job_id, page_count=5)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    bodies = {
        1: "第1章 集合论 …… 1\n一致连续的判别法 …… 2",
        2: "第2章 实数与极限 …… 3",
        3: "第1章 集合论\n正文开始。",
        4: "与本书无关的一页。",
        5: "第2章 实数与极限\n正文又起。",
    }
    repo = PageRepository(session)
    for page_no in range(1, 6):
        _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


#: `_progressive_scene` 的模型整理读数：p1 两条（一条查得中、一条查不到），p2 一条（查得中）。
_PROGRESSIVE_ROWS = {
    1: [
        OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
        OutlineEntry(level=2, printed_page=2, name="一致连续的判别法"),
    ],
    2: [OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限")],
}


def _progressive_engine(pages=(1, 2), rows_by_page=None) -> FakeKnowledgeEngine:
    return FakeKnowledgeEngine(
        on_toc_pages=lambda samples: TocPagePick(toc_pages=tuple(pages)),
        on_toc_entries=lambda page_no, text: list((rows_by_page or _PROGRESSIVE_ROWS)[page_no]),
    )


class _RecordingOutlineSink:
    """`KA-40` 的观测面：逐批记下 `land` 与 `repoint` 交来的行，不碰库、不判形状。

    落库腿（`run_knowledge_job` 那个 sink）另有两条纪律——每批一提交、只写机器腿——由
    `test_landed_rows_are_readable_...` 那条走真库判，这里只判「谁在哪一批里」。
    """

    def __init__(self) -> None:
        self.landed: list[list] = []
        self.repointed: list[list] = []

    def land(self, nodes) -> None:
        self.landed.append(list(nodes))

    def repoint(self, nodes) -> None:
        self.repointed.append(list(nodes))


class TestProgressiveOutlineLanding:
    """`KA-40`：目录行边整理边落库，定稿时同批改写一次——落库面与在飞凭据都提前到「第一页整理完」。

    四条判据各取一形：逐页成批（一页一批、批内是那一页的行）；回落页不进逐页面（`KA-33` 的页级复核
    要全书偏移，先落再删等于造出一批没人认领的行）；定稿改写只落在读数真的变了的行上且不增行；
    写手对「非本批／非 `draft`／已确认」三种行逐列零变化。`sink=None` 的形状由本文件其余用例逐字守着
    （它们都不传 sink），改判前后的成行读数必须一模一样。
    """

    def test_each_organized_page_lands_its_rows_as_its_own_batch(self, session, tmp_path):
        """逐页成批（`KA-40`）：p1 整理完即交 p1 的行，p2 整理完只交 p2 的行——一批一页，不攒到最后。"""
        _progressive_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        sink = _RecordingOutlineSink()

        build_outline(accepted, evidence, _progressive_engine(), sink=sink)

        assert [[node.row.name for node in batch] for batch in sink.landed] == [
            ["第1章 集合论", "一致连续的判别法"],
            ["第2章 实数与极限"],
        ]
        landed = [node.row for batch in sink.landed for node in batch]
        assert [row.gen_status for row in landed] == ["draft"] * 3, "早落行是 draft：定稿仍只在收尾（KA-9）"
        assert {row.model_ref for row in landed} == {"llm:toc"}, "逐页落行面只收模型整理成功的行"
        assert [row.anchors[0]["page"] for row in landed] == [1, 1, 5], (
            "前缀标定不出偏移时锚先落目录页自己，p2 到齐后那一页才标定得出来"
        )
        assert [row.order_hint for row in landed] == [1, 2, 3], "落库序就是发出序（屏上序由定稿步统一给）"

    def test_a_page_that_yields_no_entry_lands_no_batch(self, session, tmp_path):
        """零条目不成批：一页交空表就是白提交一次，逐页落行的批次数只等于真交出条目的页数。"""
        _progressive_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        sink = _RecordingOutlineSink()
        engine = _progressive_engine(rows_by_page={1: _PROGRESSIVE_ROWS[1], 2: []})

        build_outline(accepted, evidence, engine, sink=sink)

        assert [len(batch) for batch in sink.landed] == [2], "空批不发（一批一提交，提交要给真行）"

    def test_a_fallback_page_is_never_in_the_progressive_landing(self, session, tmp_path):
        """回落页留在末批（`KA-40` 边界）：p1 整理失败 ⇒ 逐页面只有 p2 的行，p1 的 `rule:toc` 行是新行。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        sink = _RecordingOutlineSink()

        def organize(page_no, text):
            if page_no == 1:
                raise EngineError("整理腿超时：p1")
            return [
                OutlineEntry(level=1, printed_page=4, name="第3章 连续"),
                OutlineEntry(level=1, printed_page=5, name="第4章 微分"),
                OutlineEntry(level=2, printed_page=201, name="附录 习题解答"),
            ]

        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1, 2)), on_toc_entries=organize
        )

        outline = build_outline(accepted, evidence, engine, sink=sink)

        assert [[node.row.name for node in batch] for batch in sink.landed] == [
            ["第3章 连续", "第4章 微分", "附录 习题解答"]
        ]
        landed_ids = {node.row.node_id for batch in sink.landed for node in batch}
        fresh = [node.row for node in outline.nodes if node.row.node_id not in landed_ids]
        assert [row.name for row in fresh] == ["第1章 集合论", "序的关系与集合的势", "第2章 实数与极限"]
        assert {row.model_ref for row in fresh} == {"rule:toc"}, "回落行整体后移到末批，不进逐页落行面"

    def test_the_finalizing_pass_repoints_only_the_row_whose_reading_changed(self, session, tmp_path):
        """定稿改写一次（`KA-40`）：前缀给的锚不是终值——读数变了的行沿用早落 `node_id` 交写手，其余零触碰。"""
        _progressive_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        sink = _RecordingOutlineSink()

        outline = build_outline(accepted, evidence, _progressive_engine(), sink=sink)

        assert [node.row.name for batch in sink.repointed for node in batch] == ["第1章 集合论"]
        repointed = sink.repointed[0][0].row
        assert repointed.anchors[0]["page"] == 3, "全书偏移标定出来后，锚从目录页改写成正文页"
        assert (repointed.name, repointed.depth) == ("第1章 集合论", 1), "只改锚，不改名与层级"
        landed_by_name = {node.row.name: node.row for batch in sink.landed for node in batch}
        assert landed_by_name["第1章 集合论"].node_id == repointed.node_id, "改写的是已在册那一行，不是新行"
        assert [[node.row.name for node in batch] for batch in sink.repointed] == [["第1章 集合论"]], (
            "锚没变的行（含未定位那条）不在改写面内"
        )
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [3, 1, 5]
        assert len(outline.nodes) == 3, "定稿不增行：早落三行就是本批三行"

    def test_landed_rows_are_readable_from_the_database_while_the_next_page_is_organized(
        self, session, tmp_path
    ):
        """落库即可读＋凭据提前：p2 还在整理时，p1 的行已在库里且 `KA-4` 的在飞判据已经认得本批。"""
        _progressive_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        seen: list[list[tuple[str, str, int]]] = []
        credential: list[str | None] = []

        def organize(page_no, text):
            if page_no == 2:
                rows = KpRepository(session).list_batch(accepted.book_id, accepted.gen_batch)
                seen.append([(row.name, row.gen_status, row.anchors[0]["page"]) for row in rows])
                credential.append(KpRepository(session).find_draft_batch(accepted.book_id))
            return list(_PROGRESSIVE_ROWS[page_no])

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_toc_pages=lambda samples: TocPagePick(toc_pages=(1, 2)), on_toc_entries=organize
            ),
            acceptance=accepted,
        )

        assert seen == [[("第1章 集合论", "draft", 1), ("一致连续的判别法", "draft", 1)]]
        assert credential == [accepted.gen_batch], "在飞凭据＝早落的那批行，不是第二事实源（KA-40）"
        rows = _rows(session, node_kind="outline")
        assert [(row.name, row.anchors[0]["page"], row.order_hint) for row in rows] == [
            ("第1章 集合论", 3, 1),
            ("一致连续的判别法", 1, 2),
            ("第2章 实数与极限", 5, 3),
        ], "定稿改写落在库里，且改写不增行"
        assert {row.gen_status for row in rows} == {"candidate"}, "收尾仍是唯一的一次三态迁移（KA-9）"

    def test_the_same_batch_writer_touches_nothing_outside_its_own_unconfirmed_draft(self, session):
        """写手的四道 `WHERE`（`KA-40`）：别的批、已定稿的行、已背书的行、不存在的 `node_id`——逐列零变化。"""
        _book(session)
        repo = KpRepository(session)
        mine = _draft(
            session,
            "afkp-mine",
            node_kind="outline",
            depth=1,
            anchors=[{"kind": "page_block", "ref_id": "p0001", "page": 1, "block_indexes": [0], "snippet": "行"}],
            order_hint=1,
        )
        confirmed = _draft(
            session, "afkp-confirmed", node_kind="outline", depth=1, anchors=[{"page": 1, "block_indexes": [0]}]
        )
        finalized = _draft(
            session,
            "afkp-candidate",
            node_kind="outline",
            depth=1,
            gen_status="candidate",
            anchors=[{"page": 1, "block_indexes": [0]}],
        )
        other_batch = _draft(
            session,
            "afkp-elsewhere",
            node_kind="outline",
            depth=1,
            gen_batch="afkp-other-batch",
            anchors=[{"page": 1, "block_indexes": [0]}],
        )
        repo.set_confirmed("afkp-confirmed", confirmed=True)
        session.commit()
        before = {
            row.node_id: (row.anchors, row.order_hint, row.parent_id, row.name, row.depth, row.model_ref, row.rev)
            for row in (mine, confirmed, finalized, other_batch)
        }

        patches = [
            {
                "node_id": node_id,
                "anchors": [{"page": 9, "block_indexes": [0]}],
                "order_hint": 9,
                "parent_id": None,
            }
            for node_id in ("afkp-confirmed", "afkp-candidate", "afkp-elsewhere", "afkp-never-created")
        ]
        patches.insert(
            0,
            {"node_id": "afkp-mine", "anchors": [{"page": 3, "block_indexes": [0]}], "order_hint": 7, "parent_id": None},
        )

        written = repo.repoint_draft_outline(book_id="b05", gen_batch="afkp-0123456789ab", patches=patches)
        session.commit()

        assert written == 1
        for row in (confirmed, finalized, other_batch):
            node = repo.get(row.node_id)
            assert (
                node.anchors,
                node.order_hint,
                node.parent_id,
                node.name,
                node.depth,
                node.model_ref,
                node.rev,
            ) == before[row.node_id]
        node = repo.get("afkp-mine")
        assert (node.anchors[0]["page"], node.order_hint) == (3, 7)
        assert (node.name, node.depth, node.model_ref, node.gen_status, node.confirmed, node.rev) == (
            "极限的 ε-δ 定义",
            1,
            "qwen-vl",
            "draft",
            0,
            before["afkp-mine"][6],
        ), "只改三列：名字／层级／来源／三态／确认位与 rev 一律不动"

    def test_the_runner_holds_a_credential_from_submit_until_the_batch_ends(self, tmp_path):
        """在途登记（`KA-40` 的凭据腿）：受理到落行之间没有行也没有流水，进度面靠它才不判 404。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            accepted = accept_kp_job(session, book_id="b05", stage="outline")
        observed: list[bool] = []

        def pick(samples):
            observed.append(runner.kp_in_flight(accepted.book_id, accepted.gen_batch))
            return TocPagePick()

        inner = FakeKnowledgeEngine(on_toc_pages=pick)
        runner = _kp_runner(
            factory,
            tmp_path,
            inner,
            kp_call_ledger=LlmCallLedger(factory),
            kp_call_identity=_LEDGER_IDENTITY,
        )

        runner.submit_knowledge(acceptance=accepted, engine_name="fake-text")

        assert observed == [True], "判页腿还在跑（一行未落）时就得认得这一批"
        assert runner.kp_in_flight(accepted.book_id, accepted.gen_batch) is False
        assert runner.kp_in_flight(accepted.book_id, "afkp-never") is False

    def test_a_batch_that_never_starts_gives_its_credential_back(self, tmp_path):
        """登记不泄漏：执行体在落行之前自止（在飞复查 409）也要撤下凭据，否则进度面把空批次读成永久在飞。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            accepted = accept_kp_job(session, book_id="b05", stage="outline")
            KpRepository(session).create(
                KpNodeRow(
                    node_id="afkp-blocker",
                    book_id="b05",
                    name="别人家的在飞行",
                    norm_key=norm_key_for("别人家的在飞行"),
                    gen_status="draft",
                    gen_batch="afkp-blocker-batch",
                    model_ref="llm:toc",
                )
            )
            session.commit()
        runner = _kp_runner(
            factory,
            tmp_path,
            FakeKnowledgeEngine(),
            kp_call_ledger=LlmCallLedger(factory),
            kp_call_identity=_LEDGER_IDENTITY,
        )

        runner.submit_knowledge(acceptance=accepted, engine_name="fake-text")

        assert runner.kp_in_flight(accepted.book_id, accepted.gen_batch) is False
        assert runner.kp_in_flight("b05", "afkp-blocker-batch") is False, "只登记自己提交的那一批"


class TestPrintPageAnchorAndVerify:
    """`KA-38`：印刷页码先直查，查不中的坏行才攒一批交模型核对一次。"""

    @staticmethod
    def _trunk_rows():
        """前三条都能被偏移直查命中，第四条是「目录里有、正文里查不中」的坏行。"""
        return [
            OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
            OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
            OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
            OutlineEntry(level=2, printed_page=3, name="函数的连续性"),
        ]

    def _engine(self, rows, on_toc_verify=None):
        return FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: list(rows),
            on_toc_verify=on_toc_verify,
        )

    def test_a_located_entry_is_anchored_without_a_verify_call(self, session, tmp_path):
        """查得中即定锚、零调用（`KA-38`）：偏移稳定成书时绝大多数条目一次就查得中，不花模型的钱。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = self._engine(self._trunk_rows()[:3])

        outline = build_outline(accepted, evidence, engine)

        assert [step for step, _ in engine.calls] == ["toc_pages", "toc_entries"]
        assert (outline.verified, outline.unlocated) == ((), ())
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4]

    def test_a_punctuation_variant_written_by_the_model_still_hits_without_a_verify_call(
        self, session, tmp_path
    ):
        """`KA-41` 的比对口径：原文写 ASCII 句点、模型转写成中文句号时窗口内照样命中、零核对调用。"""
        _book(session, "b05", active_job_id="job-a1", page_count=4)
        pages_dir = _pages_dir(tmp_path, book_id="b05", job_id="job-a1")
        bodies = {
            1: "第1章 集合论 …… 1\n§ 9. 矩的计算法. 质心的坐标 …… 2\n第2章 实数与极限 …… 3",
            2: "第1章 集合论\n正文开始。",
            3: "§ 9. 矩的计算法. 质心的坐标\n正文继续。",
            4: "第2章 实数与极限\n正文又起。",
        }
        repo = PageRepository(session)
        for page_no in range(1, 5):
            _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
            repo.upsert("b05", "job-a1", page_no, status="parsed", source="mineru", error="")
        session.commit()
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = self._engine(
            [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=2, printed_page=2, name="§ 9. 矩的计算法。质心的坐标"),
                OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
            ]
        )

        outline = build_outline(accepted, evidence, engine)

        assert [step for step, _ in engine.calls] == ["toc_pages", "toc_entries"], "命中即零调用（KA-38）"
        assert (outline.verified, outline.unlocated) == ((), ())
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4]

    def test_the_bad_rows_are_verified_in_one_batch(self, session, tmp_path):
        """坏行攒批一次核：一次调用问完，命中行把锚落那一页，且只改页不改名与层级。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        captured: list = []

        def verify(items):
            captured.append(items)
            return [TocVerifyAnswer(name="函数的连续性", page_no=4)]

        outline = build_outline(accepted, evidence, self._engine(self._trunk_rows(), verify))

        assert len(captured) == 1, "坏行通常个位数，一次请求就能全核完（KA-38）"
        assert [item.name for item in captured[0]] == ["函数的连续性"]
        assert captured[0][0].printed_page == 3
        assert captured[0][0].candidates == (
            (2, "第1章 集合论"),
            (3, "序的关系与集合的势"),
            (4, "第2章 实数与极限"),
        ), "候选面＝偏移页 ±2 的每块首行（KA-38 的输入面）"
        last = outline.nodes[3]
        assert (last.row.name, last.row.depth) == ("函数的连续性", 2), "核对不许改名与改层级（KA-38）"
        assert (last.row.anchors[0]["page"], last.row.anchors[0]["block_indexes"]) == (4, [0])
        assert last.row.model_ref == "llm:toc"
        assert outline.verified == ("函数的连续性",)
        assert outline.unlocated == ()
        by_id = {node.row.node_id: node.row.name for node in outline.nodes}
        assert by_id[last.row.parent_id] == "第2章 实数与极限"

    def test_a_verified_page_outside_the_candidates_is_not_accepted(self, session, tmp_path):
        """模型报的页号不在候选页里 ⇒ 整条不认：锚指到没读过正文的页上是假锚（`KA-38`）。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted,
            evidence,
            self._engine(self._trunk_rows(), lambda items: [TocVerifyAnswer(name="函数的连续性", page_no=99)]),
        )

        assert outline.verified == ()
        assert outline.unlocated == ("函数的连续性",)
        assert outline.nodes[3].row.anchors[0]["page"] == 1, "不认 ⇒ 锚落回目录页那一行自己"

    def test_an_answer_of_absent_is_disclosed_as_unlocated(self, session, tmp_path):
        """判「正文里没有这一行」⇒ 未定位披露：锚照样可用，只是指向目录页（`KA-38`）。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted,
            evidence,
            self._engine(self._trunk_rows(), lambda items: [TocVerifyAnswer(name="函数的连续性", page_no=None)]),
        )

        assert [(node.row.name, node.row.anchors[0]["page"]) for node in outline.nodes] == [
            ("第1章 集合论", 2),
            ("序的关系与集合的势", 3),
            ("第2章 实数与极限", 4),
            ("函数的连续性", 1),
        ]
        assert (outline.verified, outline.unlocated) == ((), ("函数的连续性",))

    def test_the_verify_leg_is_skipped_when_the_offset_cannot_be_calibrated(self, session, tmp_path):
        """标定不出 ⇒ 核对腿零调用：那时「全部未定位」是标定失败的读数，问模型等于让机器替它编页码。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = self._engine(
            [
                OutlineEntry(level=1, printed_page=7, name="第9章 正文里没有"),
                OutlineEntry(level=2, printed_page=8, name="9.1 也没有这一行"),
            ],
            lambda items: pytest.fail("偏移标定不出时不该发起核对腿"),
        )

        outline = build_outline(accepted, evidence, engine)

        assert [step for step, _ in engine.calls] == ["toc_pages", "toc_entries"]
        assert outline.unlocated == ("第9章 正文里没有", "9.1 也没有这一行")
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [1, 1]

    def test_the_verified_names_reach_the_batch_ledger(self, session, tmp_path):
        """账面点名（`KA-27` 第二十一键）：核对救回来的条目名进 `verified_outline_entries`，不再进未定位。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=self._engine(
                self._trunk_rows(), lambda items: [TocVerifyAnswer(name="函数的连续性", page_no=4)]
            ),
            acceptance=accepted,
        )

        result = kp_batch_row(accepted, outcome).result
        assert result["verified_outline_entries"] == ["函数的连续性"]
        assert result["unlocated_outline_entries"] == []
        assert len(_rows(session, node_kind="outline")) == 4


class TestFrontMatterRows:
    """`KA-39`：正文前后的附件页照常成目录行，但带一枚「非知识点」身份位；两枚键只进 `meta`。"""

    def _engine(self, reference_pages, rows=None):
        return FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,), reference_pages=reference_pages),
            on_toc_entries=lambda page_no, text: list(
                rows
                if rows is not None
                else [
                    OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                    OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                    OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
                ]
            ),
        )

    def test_reference_pages_become_top_level_rows_marked_non_knowledge(self, session, tmp_path):
        """前言／符号表各成一枚顶级行且标 `reference`；正文页在后的排在章序之后，在前的排在章序之前。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted,
            evidence,
            self._engine(
                (
                    ReferencePage(page_no=2, name="重要符号表", kind="symbol_table"),
                    ReferencePage(page_no=7, name="附录 习题解答", kind="appendix"),
                )
            ),
        )

        by_id = {node.row.node_id: node.row.name for node in outline.nodes}
        assert [
            (node.row.name, node.row.depth, node.row.parent_id and by_id[node.row.parent_id], node.row.meta)
            for node in outline.nodes
        ] == [
            ("重要符号表", 1, None, {"toc_role": "reference", "toc_kind": "symbol_table"}),
            ("第1章 集合论", 1, None, {"toc_role": "knowledge"}),
            ("序的关系与集合的势", 2, "第1章 集合论", {"toc_role": "knowledge"}),
            ("第2章 实数与极限", 1, None, {"toc_role": "knowledge"}),
            ("附录 习题解答", 1, None, {"toc_role": "reference", "toc_kind": "appendix"}),
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4, 5, 7]
        assert {node.row.model_ref for node in outline.nodes} == {"llm:toc"}
        assert outline.reference_rows == 2

    def test_a_blank_kind_is_folded_to_the_one_the_name_gives(self, session, tmp_path):
        """域外／空缺的 `kind` 折成按名字判出来的那一类，模型给了域内值就照用——机器不替模型造第八个域值。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted,
            evidence,
            self._engine(
                (
                    ReferencePage(page_no=2, name="前言", kind=""),
                    ReferencePage(page_no=7, name="参考文献汇总", kind="index"),
                )
            ),
        )

        assert [node.row.meta for node in outline.nodes if node.row.name in ("前言", "参考文献汇总")] == [
            {"toc_role": "reference", "toc_kind": "front_matter"},
            {"toc_role": "reference", "toc_kind": "index"},
        ]
        assert outline.reference_rows == 2

    def test_a_reference_page_outside_the_evidence_set_makes_no_row(self, session, tmp_path):
        """模型报了取证集外的页 ⇒ 不成行：机器不为读不到的页造结构结论（`KA-2` 同一纪律）。"""
        _two_toc_page_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(accepted, evidence, self._engine((ReferencePage(page_no=9, name="索引", kind="index"),)))

        assert outline.reference_rows == 0
        assert len(outline.nodes) == 3
        assert all(node.row.meta == {"toc_role": "knowledge"} for node in outline.nodes)

    def test_the_fallback_leg_tags_rows_by_their_names(self, session, tmp_path):
        """回落腿按名字判（`KA-39`）：条目腿补出的行里，前言／符号表／附录这些名字要带得出身份位。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())

        assert [(node.row.name, node.row.meta) for node in outline.nodes] == [
            ("第1章 集合论", {"toc_role": "knowledge"}),
            ("序的关系与集合的势", {"toc_role": "knowledge"}),
            ("第2章 实数与极限", {"toc_role": "knowledge"}),
            # 「附录 习题解答」归 appendix 而不是 exercise——判序里附录先命中（normalize/toc.py 的判序注释）。
            ("附录 习题解答", {"toc_role": "reference", "toc_kind": "appendix"}),
        ]
        assert outline.reference_rows == 1

    def test_the_identity_bits_add_no_column(self, session, tmp_path):
        """零 ALTER（`KA-39`）：两枚身份位只在 `meta` JSON 里，`af_kp` 表上一个列都不新加。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        build_outline(accepted, evidence, FakeKnowledgeEngine())

        columns = {column.name for column in AfKp.__table__.columns}
        assert "meta" in columns
        assert {"toc_role", "toc_kind"}.isdisjoint(columns), "建列就要开 ALTER 窗口（KA-39 明确不做）"


class TestNodeTypeOf:
    """`KA-44`：四值类型只由结构＋两枚身份位派生，判序固定，不落列、不开人工写键。"""

    class _Row:
        """认三枚属性就够（`AfKp` 与点腿的 `KpNodeRow` 都长这样）。"""

        def __init__(self, node_id, *, depth, meta, parent_id=None) -> None:
            self.node_id = node_id
            self.depth = depth
            self.meta = meta
            self.parent_id = parent_id

    def test_knowledge_rows_split_into_chapter_and_point_by_depth(self):
        assert node_type_of(depth=1, toc_role="knowledge", toc_kind="") == "chapter"
        assert node_type_of(depth=2, toc_role="knowledge", toc_kind="") == "point"

    def test_an_exercise_row_is_exercise_before_it_is_other(self):
        """习题行在 `KA-39` 里同时带 `toc_role='reference'`：先判 role 会把它标成「其他」。"""
        assert node_type_of(depth=1, toc_role="reference", toc_kind="exercise") == "exercise"

    def test_a_reference_row_of_another_kind_is_other(self):
        for kind in ("symbol_table", "bibliography", "appendix", "front_matter", "back_matter", "index"):
            assert node_type_of(depth=1, toc_role="reference", toc_kind=kind) == "other"

    def test_a_child_of_an_exercise_row_is_exercise_too(self):
        """子行自己的 `toc_kind` 可能为空，靠祖先那一枚才认得出来。"""
        rows = [
            self._Row("ex", depth=1, meta={"toc_role": "reference", "toc_kind": "exercise"}),
            self._Row("ex-1", depth=2, meta={"toc_role": "knowledge"}, parent_id="ex"),
        ]

        assert node_types_of(rows) == {"ex": "exercise", "ex-1": "exercise"}

    def test_a_missing_parent_does_not_invent_an_ancestor(self):
        rows = [self._Row("s", depth=2, meta={"toc_role": "knowledge"}, parent_id="gone")]

        assert node_types_of(rows) == {"s": "point"}

    def test_the_whole_book_maps_to_the_four_values(self, session, tmp_path):
        """一台真场景：章／节／习题／符号表各得其一，点行（旧批次遗留）按自己的身份位判。"""
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        rows = _rows(session, node_kind="outline")
        types = node_types_of(rows)

        assert sorted(types.values()) == ["chapter", "point", "point"]


class TestOutlineStage:
    """`KA-5`/`KA-6`：目录阶段只读结构化读数，两层封顶。"""

    def test_the_outline_stage_calls_only_the_toc_legs(self, session, tmp_path):
        """`KA-35`／`KA-37`：目录阶段的模型面＝判页一次，且它排在任何知识点调用之前——
        两阶段的次序在调用面上就看得见；判页认不出目录页时行照样出（回落腿），不出现目录零行。"""
        _scene(session, tmp_path, page_count=4, layout={1: [(1, "第 1 章 极限")]})
        engine = FakeKnowledgeEngine()

        run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=_accepted(session)
        )

        steps = [step for step, _ in engine.calls]
        assert len(_rows(session, node_kind="outline")) == 1
        assert steps[0] == "toc_pages"
        assert set(steps) == {"toc_pages", "points"}

    def test_third_level_title_never_becomes_a_node_but_still_reaches_the_point_stage(
        self, session, tmp_path
    ):
        """`KA-6`：第三层不进 `af_kp`、并计数；它的文字不丢——节正文里仍在，由知识点承载。"""
        _scene(session, tmp_path, page_count=6)
        accepted = _accepted(session)
        seen: dict[str, str] = {}

        def capture(section):
            seen[section.title] = "\n".join(page.markdown for page in section.pages)
            return []

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(on_points=capture, points_by_section={}),
            acceptance=accepted,
        )

        assert outcome.dropped_depth == 1
        assert all(row.depth in (1, 2) for row in _rows(session, node_kind="outline"))
        assert "1.2.1 无穷小" in seen["1.2 函数极限"]

    def test_outline_rows_carry_two_levels_parent_order_and_source_domain(self, session, tmp_path):
        """目录行的来源域：两层封顶、`order_hint` 前序、`type` 恒 `''`、同批同取证版本同产出者。"""
        _scene(session, tmp_path, page_count=6)
        accepted = _accepted(session)

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=accepted,
        )

        outline = _rows(session, node_kind="outline")
        assert [(row.depth, row.order_hint) for row in outline] == [(1, 1), (2, 2), (2, 3)]
        assert outline[1].parent_id == outline[0].node_id
        assert outline[0].parent_id is None
        assert all(row.gen_status == "candidate" for row in outline)  # 收尾后恒 candidate（中途态见 W4）
        assert {row.gen_batch for row in outline} == {accepted.gen_batch}
        assert {row.job_id for row in outline} == {"job-a1"}
        assert {row.model_ref for row in outline} == {"rule:heading"}
        assert {row.summary for row in outline} == {""}
        assert all(row.type == "" and row.node_kind == "outline" for row in outline)
        assert outline[0].anchors[0]["page"] == 1
        assert outline[0].anchors[0]["block_indexes"] == [0]


class TestDeterministicOutline:
    """`KA-33`／`KA-35` 的回落面：判页腿认不出目录页时条目腿为正本，零条目才退到 heading 块。

    桩引擎不交判页读数，所以本类整条走的都是回落通路；模型正腿的判据面在 `TestModelLedOutline`。
    """

    _TOC_BODY = "第 1 章 极限 ……… 1\n1.1 数列极限 ……… 3\n1.2 函数极限 ……… 5"

    def _toc_scene(self, session, tmp_path) -> None:
        """目录页在 p1；p2／p4／p6 各有一条能被回定位的标题；p3 另有一条不该成行的标题。"""
        pages_dir = _scene(session, tmp_path, page_count=6, layout={3: [(1, "附录 记号表")]})
        _write_page_artifacts(pages_dir, 1, [], body=self._TOC_BODY)
        _write_page_artifacts(pages_dir, 2, [(1, "第 1 章 极限")])
        _write_page_artifacts(pages_dir, 4, [(2, "1.1 数列极限")])
        _write_page_artifacts(pages_dir, 6, [(2, "1.2 函数极限")])

    def test_toc_entries_are_the_outline_source_and_headings_are_not_used(self, session, tmp_path):
        """条目腿有输出 ⇒ 条目为正本：次序是书自己的章次、锚在回定位到的正文页、heading 整条不用。"""
        self._toc_scene(session, tmp_path)

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        assert [row.name for row in outline] == ["第 1 章 极限", "1.1 数列极限", "1.2 函数极限"]
        assert [row.depth for row in outline] == [1, 2, 2]
        assert [row.anchors[0]["page"] for row in outline] == [2, 4, 6]
        assert [row.anchors[0]["block_indexes"] for row in outline] == [[0], [0], [0]]
        assert {row.model_ref for row in outline} == {"rule:toc"}
        assert "附录 记号表" not in {row.name for row in outline}

    def test_a_section_before_any_chapter_gets_no_parent(self, session, tmp_path):
        """`KA-26` 同轴：前面没有章就不造章——第一个读数就是节时父为空。"""
        _scene(
            session,
            tmp_path,
            page_count=3,
            layout={1: [(2, "1.1 前置节")], 2: [(1, "第 2 章 主体")]},
        )

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        assert [(row.name, row.depth, row.parent_id) for row in outline] == [
            ("1.1 前置节", 2, None),
            ("第 2 章 主体", 1, None),
        ]

    def test_a_book_with_no_readings_yields_zero_rows(self, session, tmp_path):
        """零读数＝零行：机器不为了让目录非空而编造条目。"""
        _scene(session, tmp_path, page_count=3, layout={})

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        assert outcome.outline_rows == 0
        assert outcome.dropped_depth == 0
        assert _rows(session, node_kind="outline") == []


class TestTwoStageOrdering:
    """`KA-5`：一个批次内固定两阶段，目录先于点写入。"""

    def test_the_two_stage_dependency_is_the_adapter_protocol(self):
        """`KA-22`/`KA-35`：协议承载目录三条腿＋知识点一条腿，且没有「一次调用出整批目录」那种旧名字。"""
        assert isinstance(FakeKnowledgeEngine(), KnowledgeEngine)
        assert not hasattr(KnowledgeEngine, "outline")

    def test_outline_rows_are_persisted_before_the_point_stage_runs(self, session, tmp_path):
        """次序断言：点阶段第一眼就该看见本批目录行——父不存在时只能产出成堆未归档行。"""
        _scene(session, tmp_path, page_count=6)
        seen: dict[str, int] = {}

        def probe(section):
            seen["outline"] = len(_rows(session, node_kind="outline"))
            seen["point"] = len(_rows(session, node_kind="point"))
            seen["sections"] = seen.get("sections", 0) + 1
            return []

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(on_points=probe),
            acceptance=_accepted(session),
        )

        assert seen == {"outline": 3, "point": 0, "sections": 3}
        assert _kp_ledger_rows(session) == 0  # 执行体不写流水：批次账面由 JobRunner 在跑完后单独会话落账

    def test_point_rows_share_the_batch_and_evidence_version_of_the_outline_rows(
        self, session, tmp_path
    ):
        """`KA-1`/`KA-10`：一次批次的每一行同 `gen_batch`、同取证版本——批次身份一经写入不可变。"""
        _scene(session, tmp_path, page_count=6)
        accepted = _accepted(session)
        engine = FakeKnowledgeEngine(
            points_by_section={
                "1.1 数列极限": [_point_draft("数列极限的定义", page=2, blocks=(1,))],
                "1.2 函数极限": [_point_draft("函数极限的定义", page=5, blocks=(0,))],
            },
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        rows = _rows(session)
        assert {row.gen_batch for row in rows} == {accepted.gen_batch}
        assert {row.job_id for row in rows} == {"job-a1"}
        assert len(_rows(session, node_kind="outline")) == 3
        assert len(_rows(session, node_kind="point")) == 2


class TestOutlineOnlyStage:
    """`KA-34`：`stage=outline` 只出目录行——知识点阶段整条不发起，收尾与流水照跑。

    自 `KA-35` 起本阶段**不再是零调用**（判页＋逐页整理＋坏行核对三条目录腿照发），「零点次调用」
    这条判据因此改判成「知识点腿零发起」——它才是 `KA-34` 真正要守的那件事。
    """

    def test_the_outline_only_stage_never_calls_the_points_leg(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        engine = FakeKnowledgeEngine(
            points_by_section={"1.1 数列极限": [_point_draft("数列极限的定义", page=2)]},
        )
        accepted = _accepted(session, stage="outline")

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=engine,
            acceptance=accepted,
        )

        assert [step for step, _ in engine.calls] == ["toc_pages"], "只目录批次只有目录腿，知识点腿零发起"
        assert _rows(session, node_kind="point") == []
        assert len(_rows(session, node_kind="outline")) == 3
        assert (outcome.point_rows, outcome.failed_sections) == (0, 0)
        assert outcome.candidate_rows == 3, "收尾照跑：只目录批次同样翻到 candidate"
        # 流水行带 stage（`KA-27` 五键入参）：读侧只认账面这一枚，不看 `af_kp` 有没有点行
        assert kp_batch_row(accepted, outcome).stage == "outline"

    def test_the_default_stage_still_runs_the_points_leg(self, session, tmp_path):
        """缺省＝`full`：目录行先落库，随后逐节发起知识点调用。"""
        _scene(session, tmp_path, page_count=6)
        engine = FakeKnowledgeEngine(
            points_by_section={"1.1 数列极限": [_point_draft("数列极限的定义", page=2)]},
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=_accepted(session))

        assert [step for step, _ in engine.calls] == ["toc_pages", "points", "points", "points"]


class TestUnarchoredPool:
    """`KA-7`/`KA-26`：未归档池是一等公民，机器不猜父。"""

    def test_points_from_pages_outside_the_outline_tree_enter_the_pool(self, session, tmp_path):
        """目录没覆盖到的正文照样抽点，父为 `NULL`＝入池——硬挂父会造出假结构。"""
        _scene(session, tmp_path, page_count=4, layout={3: [(1, "第 1 章 极限")]})
        accepted = _accepted(session)

        def by_parent(section):
            if section.parent_id is None:
                return [_point_draft("前置无主正文", page=1, blocks=(0,))]
            return [_point_draft("章内知识点", page=3, blocks=(1,))]

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=by_parent,
            ),
            acceptance=accepted,
        )

        pool = [row for row in _rows(session, node_kind="point") if row.parent_id is None]
        assert [row.name for row in pool] == ["前置无主正文"]
        assert outcome.unarchored == 1
        assert len(_rows(session, node_kind="point")) == 2

    def test_section_points_take_the_fed_section_as_parent_not_a_guessed_one(self, session, tmp_path):
        """`KA-26` 的合法来源：父＝本批目录阶段给出、且被逐节喂给模型的那一行。"""
        _scene(session, tmp_path, page_count=6)

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [_point_draft("数列极限的定义", page=2, blocks=(1,))]
                },
            ),
            acceptance=_accepted(session),
        )

        outline = {row.name: row.node_id for row in _rows(session, node_kind="outline")}
        point = next(row for row in _rows(session, node_kind="point"))
        assert point.parent_id == outline["1.1 数列极限"]

    def test_parent_reference_outside_the_batch_is_not_resolved(self, session, tmp_path):
        """`KA-26`：回指不到同批目录行的父 → 入池并计数，机器不替他造一个父。"""
        _scene(session, tmp_path, page_count=6)

        def draft(section):
            if section.title != "1.1 数列极限":
                return []
            return [
                _point_draft("点到不存在的父", page=2, blocks=(1,), parent_ref="afkp-ffffffffffff")
            ]

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=draft, points_by_section={}
            ),
            acceptance=_accepted(session),
        )

        point = _rows(session, node_kind="point")[0]
        assert point.parent_id is None
        assert outcome.unarchored == 1

    def test_parent_reference_to_a_batch_sibling_is_resolved(self, session, tmp_path):
        """同批目录行可按名回指——这是 `KA-26` 允许的第一种来源。"""
        _scene(session, tmp_path, page_count=6)

        def draft(section):
            if section.title != "1.1 数列极限":
                return []
            return [_point_draft("回指同批节", page=2, blocks=(1,), parent_ref="1.2 函数极限")]

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=draft, points_by_section={}
            ),
            acceptance=_accepted(session),
        )

        outline = {row.name: row.node_id for row in _rows(session, node_kind="outline")}
        assert _rows(session, node_kind="point")[0].parent_id == outline["1.2 函数极限"]


class TestFailureDomain:
    """「失败与质量语义」：单节失败不拖整批；不合形状的输出不写且计数披露。"""

    def test_one_broken_section_leaves_the_batch_running(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [_point_draft("数列极限的定义", page=2, blocks=(1,))],
                    "1.2 函数极限": [_point_draft("函数极限的定义", page=4, blocks=(1,))],
                },
                failing_sections=("1.1 数列极限",),
            ),
            acceptance=_accepted(session),
        )

        assert outcome.failed_sections == 1
        assert [(d["title"], d["reason"]) for d in outcome.failed_section_details] == [
            ("1.1 数列极限", "模型服务中断：1.1 数列极限")
        ]
        assert [row.name for row in _rows(session, node_kind="point")] == ["函数极限的定义"]
        assert len(_rows(session, node_kind="outline")) == 3

    def test_a_failed_section_is_identifiable_on_the_batch_ledger(self, session, tmp_path):
        """DEFECT-026 甲的披露半边：`failed_sections` 不能只剩一枚计数——账面要答「哪一节、哪几页、为什么」。

        真机表征＝批次 `afkp-215187d641e2` 收口只读得出 `failed_sections=1`，哪一节被吃掉要靠翻服务日志。
        """
        _scene(session, tmp_path, page_count=6)
        acceptance = _accepted(session)

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.2 函数极限": [_point_draft("函数极限的定义", page=4, blocks=(1,))]
                },
                failing_sections=("1.1 数列极限",),
            ),
            acceptance=acceptance,
        )

        result = kp_batch_row(acceptance, outcome).result or {}
        assert "failed_section_details" in KP_RESULT_KEYS, "新键不进闭集就是自由键（`KA-27`）"
        assert result["failed_sections"] == outcome.failed_sections == 1
        assert len(result["failed_section_details"]) == result["failed_sections"]
        detail = result["failed_section_details"][0]
        assert detail["title"] == "1.1 数列极限"
        assert detail["page_start"] <= detail["page_end"]
        assert detail["reason"] == "模型服务中断：1.1 数列极限"

    def test_a_clean_batch_carries_an_empty_failure_list_not_a_missing_key(self, session, tmp_path):
        """跑通的批次也要能读出「零失败节」：缺键与空数组是两种形状，前者让读侧每次都得防 KeyError。"""
        _scene(session, tmp_path, page_count=6)
        acceptance = _accepted(session)

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(points_by_section={}),
            acceptance=acceptance,
        )

        result = kp_batch_row(acceptance, outcome).result or {}
        assert result["failed_sections"] == 0
        assert result["failed_section_details"] == []

    @pytest.mark.parametrize(
        ("draft", "reason"),
        [
            ({"name": "", "anchors": [{"page": 2, "block_indexes": [1]}]}, "缺 name"),
            ({"name": "无锚的点", "anchors": []}, "anchors 为空"),
            (
                {
                    "name": "边指向不存在的点",
                    "anchors": [{"page": 2, "block_indexes": [1]}],
                    "links": [{"kind": "prerequisite", "to": "并不存在的点", "note": ""}],
                },
                "links 回指不到",
            ),
            (
                {
                    "name": "锚页在取证集外",
                    "anchors": [{"page": 99, "block_indexes": [1]}],
                },
                "锚页越界（`KA-19` 保留并标记，不静默删）",
            ),
        ],
    )
    def test_out_of_shape_drafts_are_written_or_dropped_as_the_design_says(
        self, session, tmp_path, draft, reason
    ):
        """缺 name／空锚／回指不到 ⇒ 不写且计数；只有锚页越界属于 `KA-19` 的「可见保留」。"""
        _scene(session, tmp_path, page_count=6)
        payload = {"type": "concept", "summary": "", "links": None, **draft}
        name = draft["name"] or "无名"

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={"1.1 数列极限": [payload]},
            ),
            acceptance=_accepted(session),
        )

        written = [row.name for row in _rows(session, node_kind="point")]
        if "越界" in reason:
            assert written == [name]
            assert outcome.dropped_nodes == 0
        else:
            assert written == []
            assert outcome.dropped_nodes == 1

    def test_domain_values_outside_the_documented_closures_are_not_written(self, session, tmp_path):
        """`type`/`role`/`links[].kind` 的闭集外草稿＝不合形状：不写、不折算、计数披露。"""
        _scene(session, tmp_path, page_count=6)
        drafts = [
            _point_draft("域外 type", page=2, blocks=(1,), type="quiz"),
            _point_draft("域外 role", page=2, blocks=(1,), role="secondary"),
            _point_draft(
                "域外 link kind",
                page=2,
                blocks=(1,),
                links=[{"kind": "relates_to", "to": "数列极限的定义", "note": ""}],
            ),
        ]

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={"1.1 数列极限": drafts}
            ),
            acceptance=_accepted(session),
        )

        assert (_rows(session, node_kind="point"), outcome.dropped_nodes) == ([], 3)

    def test_a_full_batch_run_never_touches_the_human_slot(self, session, tmp_path):
        """验收判据 3／`KA-11`：自动批次全跑完后 `confirmed` 计数逐本不变。"""
        _scene(session, tmp_path, page_count=6)
        human = _draft(session, "afkp-h1", gen_status="candidate", gen_batch="")
        KpRepository(session).set_confirmed(human.node_id, confirmed=True)
        session.commit()
        before = copy.deepcopy(human.anchors or [])

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [_point_draft("数列极限的定义", page=2, blocks=(1,))]
                },
            ),
            acceptance=_accepted(session),
        )

        assert [row.node_id for row in _confirmed_rows(session)] == [human.node_id]
        assert (session.get(AfKp, human.node_id).anchors or []) == before
        assert outcome.point_rows == 1


class TestExecutionTimeRecheck:
    """`PM-13`／W2 承诺的后半：受理到首行落库之间的窗口由执行期第一眼复查守住。"""

    def test_another_batches_draft_row_stops_the_run_with_409(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        accepted = _accepted(session)
        _draft(session, "afkp-z9", gen_batch="afkp-eeeeeeeeeeee")
        session.commit()

        with pytest.raises(KpGateError) as caught:
            run_knowledge_job(
                session,
                data_root=tmp_path,
                engine=FakeKnowledgeEngine(),
                acceptance=accepted,
            )

        assert caught.value.status_code == 409
        assert "afkp-eeeeeeeeeeee" in caught.value.detail
        assert len(_rows(session)) == 1

    def test_own_batch_rows_do_not_stop_a_retried_run(self, session, tmp_path):
        """同批的 `draft` 行不是冲突：在飞唯一性按书算，批次身份才是自我豁免的键。"""
        _scene(session, tmp_path, page_count=6, layout={1: [(1, "第 1 章 极限")]})
        accepted = _accepted(session)
        _draft(session, "afkp-own", gen_batch=accepted.gen_batch)
        session.commit()

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=accepted,
        )

        assert outcome.outline_rows == 1
        assert len(_rows(session)) == 2


class TestAnchorReviewFlags:
    """`KA-19`：换版或重新 ingest 后锚点越界＝可见保留，不静默改写、不静默删。"""

    def test_out_of_range_anchor_is_flagged_without_rewriting_the_row(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        node = _draft(
            session,
            "afkp-a1",
            anchors=[{"kind": "page_block", "ref_id": "p0099", "page": 99, "block_indexes": [0]}],
        )
        session.commit()
        before = copy.deepcopy(node.anchors)

        flags = anchor_review_flags(session, data_root=tmp_path, nodes=_rows(session))

        assert [(flag.node_id, flag.page) for flag in flags] == [(node.node_id, 99)]
        assert (session.get(AfKp, node.node_id).anchors or []) == before
        assert len(_rows(session)) == 1

    def test_block_index_beyond_the_evidence_version_is_flagged(self, session, tmp_path):
        """`KA-18` 的块序空间：锚点按行自身取证版本的 `blocks.json` 解释，越界只标记。"""
        pages_dir = _scene(session, tmp_path, page_count=2, layout={2: [(1, "标题")]})
        node = _draft(
            session,
            "afkp-a1",
            anchors=[{"kind": "page_block", "page": 2, "block_indexes": [7]}],
        )
        other = _draft(
            session,
            "afkp-a2",
            anchors=[{"kind": "page_block", "page": 2, "block_indexes": [1]}],
        )
        session.commit()
        assert json.loads((pages_dir / "p0002.blocks.json").read_text(encoding="utf-8"))["blocks"]

        flags = anchor_review_flags(session, data_root=tmp_path, nodes=_rows(session))

        assert [flag.node_id for flag in flags] == [node.node_id]
        assert (session.get(AfKp, other.node_id).anchors or [{}])[0]["block_indexes"] == [1]

    def test_flags_are_read_per_rows_own_evidence_version(self, session, tmp_path):
        """同一页号在两个版本里可指性不同：判读用行上的 `job_id`，不是生效指针。"""
        _scene(session, tmp_path, page_count=6, job_id="job-a1")
        _book(session, "b05", active_job_id="job-a1", page_count=6)
        PageRepository(session).upsert("b05", "job-b2", 1, status="parsed", source="mineru")
        PageRepository(session).upsert("b05", "job-b2", 2, status="parsed", source="mineru")
        session.commit()
        old_version = _draft(
            session, "afkp-old", job_id="job-b2", anchors=[{"page": 5, "block_indexes": [0]}]
        )
        live_version = _draft(
            session, "afkp-live", job_id="job-a1", anchors=[{"page": 5, "block_indexes": [0]}]
        )
        session.commit()

        flags = anchor_review_flags(session, data_root=tmp_path, nodes=_rows(session))

        assert [flag.node_id for flag in flags] == [old_version.node_id]
        assert live_version.job_id == "job-a1"

    def test_manual_rows_have_no_version_to_read_the_anchor_against(self, session, tmp_path):
        """`KA-18` 的例外：`job_id=''` 的人工直填行无锚可解释，标记而不是判死。"""
        _scene(session, tmp_path, page_count=2)
        node = _draft(session, "afkp-manual", job_id="", anchors=[{"page": 1, "block_indexes": [0]}])
        session.commit()

        flags = anchor_review_flags(session, data_root=tmp_path, nodes=_rows(session))

        assert [flag.node_id for flag in flags] == [node.node_id]
        assert "job_id" in flags[0].reason or "取证" in flags[0].reason


class TestNormKey:
    """`norm_key` 的去重口径（database-design.md 列行）：全角转半角＋空白折叠＋小写。"""

    def test_width_space_and_case_are_folded(self):
        assert norm_key_for("ＨＴＴＰ   教程") == "http 教程"
        assert norm_key_for("  Limit  of a Sequence ") == "limit of a sequence"

    def test_norm_key_is_what_the_batch_writes(self, session, tmp_path):
        """写进去的 `norm_key` 必须是这个口径——`ix_af_kp_norm` 查重（W4）按它比，`name` 保持原样。"""
        _scene(session, tmp_path, page_count=6, layout={1: [(1, "第 1 章 极限")]})

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "第 1 章 极限": [_point_draft("极限的　 ε-δ  定义", page=1, blocks=(1,))]
                },
            ),
            acceptance=_accepted(session),
        )

        point = _rows(session, node_kind="point")[0]
        assert point.norm_key == "极限的 ε-δ 定义"
        assert point.name == "极限的　 ε-δ  定义"
        assert {row.norm_key for row in _rows(session)} == {"第 1 章 极限", "极限的 ε-δ 定义"}


# --------------------------------------------------------------------------- W4 收尾与启动期归一


def _columns(row: AfKp) -> tuple:
    """非三态列的快照：收尾/归一「零被触碰」逐列判，而不是只看一个状态位。"""
    return (
        row.confirmed,
        row.rev,
        row.updated_at,
        row.name,
        row.norm_key,
        row.parent_id,
        row.order_hint,
        copy.deepcopy(row.links),
        copy.deepcopy(row.anchors),
        row.summary,
    )


class TestFinalizeFlip:
    """`KA-9`：`draft→candidate` 只在收尾这一步发生，改写方向唯一，非本批行零被触碰。"""

    def test_the_flip_is_the_last_step_of_the_job(self, session, tmp_path):
        """作业中途本批恒 `draft`（那正是在飞判据），跑完只剩 `candidate`——翻转点只有收尾一处。"""
        _scene(session, tmp_path, page_count=6)
        seen: list[set[str]] = []

        def capture(section):
            seen.append({row.gen_status for row in _rows(session)})
            return []

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=capture, points_by_section={}
            ),
            acceptance=_accepted(session),
        )

        assert seen and all(statuses == {"draft"} for statuses in seen)
        assert {row.gen_status for row in _rows(session)} == {"candidate"}
        assert outcome.candidate_rows == len(_rows(session))

    def test_a_finished_batch_is_no_longer_in_flight(self, session, tmp_path):
        """收尾翻面即解除在飞：`KA-4` 的判据与三态同源，同书随后可再发一批。"""
        _scene(session, tmp_path, page_count=6)
        first = _accepted(session)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=first,
        )

        assert KpRepository(session).find_draft_batch("b05") is None
        assert accept_kp_job(session, book_id="b05").gen_batch != first.gen_batch

    def test_rows_of_other_batches_are_never_touched(self, session, tmp_path):
        """验收判据 2：`candidate` 及以上行零被触碰——非本批行逐列不变。"""
        _scene(session, tmp_path, page_count=6)
        outsider = _draft(
            session,
            "afkp-out",
            gen_batch="afkp-999999999999",
            gen_status="candidate",
            links=[{"kind": "prerequisite", "to": "afkp-none", "note": "旧边"}],
        )
        KpRepository(session).set_confirmed(outsider.node_id, confirmed=True)
        session.commit()
        before = (outsider.gen_status, _columns(outsider))

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=_accepted(session),
        )
        session.expire_all()
        after = session.get(AfKp, outsider.node_id)

        assert (after.gen_status, _columns(after)) == before

    def test_the_repository_admits_only_the_forward_directions(self, session):
        """`KA-9` 的「改写方向唯一」做成构造性：仓储只认前进边，反向与终态回改在语句发出前拒。"""
        _draft(session, "afkp-a1", gen_status="candidate")
        session.commit()
        repo = KpRepository(session)

        with pytest.raises(ValueError):
            repo.flip_batch_status("b05", "afkp-0123456789ab", source="candidate", target="draft")
        with pytest.raises(ValueError):
            repo.flip_batch_status("b05", "afkp-0123456789ab", source="rejected", target="candidate")
        with pytest.raises(ValueError):
            repo.flip_batch_status("b05", "afkp-0123456789ab", source="rejected", target="draft")


class TestRerunSupersede:
    """`KA-36`：同一页区间重跑 ⇒ 其它批次的未背书旧候选作废，屏上不叠加。

    作废只认四元合取（同书、非本批、`candidate`、`confirmed=0`、任一锚点页 ∈ 区间）：人已背书的行与
    区间外的行一行不碰——重跑说的是「这一段的旧读数过期了」，不是「这本书的旧结论一律作废」。
    """

    _OLD_BATCH = "afkp-999999999999"
    _NEW_BATCH = "afkp-0123456789ab"

    def _old(self, session, node_id: str, *, page: int, name: str) -> AfKp:
        return _draft(
            session,
            node_id,
            gen_batch=self._OLD_BATCH,
            gen_status="candidate",
            name=name,
            norm_key=norm_key_for(name),
            anchors=[{"kind": "page_block", "ref_id": f"p{page:04d}", "page": page, "block_indexes": [0]}],
        )

    def _current_draft(self, session) -> AfKp:
        """收尾要跑得起来，本批至少得有一行 `draft`——零 draft 的批次在收尾入口就返回了。"""
        return _draft(session, "afkp-new", gen_batch=self._NEW_BATCH, name="这一段的新读数", norm_key="这一段的新读数")

    def test_a_rerun_supersedes_candidates_from_other_batches_inside_the_range(self, session):
        _book(session)
        old = self._old(session, "afkp-old", page=3, name="旧读数")
        self._current_draft(session)
        session.commit()

        result = finalize_batch(
            session, book_id="b05", gen_batch=self._NEW_BATCH, page_start=1, page_end=6
        )
        session.expire_all()

        assert result.superseded_rows == 1
        assert session.get(AfKp, old.node_id).gen_status == "rejected"
        assert session.get(AfKp, "afkp-new").gen_status == "candidate"

    def test_confirmed_rows_and_rows_outside_the_range_are_untouched(self, session):
        _book(session)
        endorsed = self._old(session, "afkp-endorsed", page=3, name="人已背书的那一行")
        outside = self._old(session, "afkp-outside", page=50, name="区间外的旧读数")
        inside = self._old(session, "afkp-inside", page=4, name="区间内的旧读数")
        KpRepository(session).set_confirmed(endorsed.node_id, confirmed=True)
        self._current_draft(session)
        session.commit()

        result = finalize_batch(
            session, book_id="b05", gen_batch=self._NEW_BATCH, page_start=1, page_end=6
        )
        session.expire_all()

        assert result.superseded_rows == 1
        assert (session.get(AfKp, endorsed.node_id).gen_status, session.get(AfKp, endorsed.node_id).confirmed) == (
            "candidate",
            1,
        )
        assert session.get(AfKp, outside.node_id).gen_status == "candidate"
        assert session.get(AfKp, inside.node_id).gen_status == "rejected"

    def test_a_batch_with_no_drafts_supersedes_nothing(self, session):
        """零 `draft` 的批次在收尾入口就返回：空批次不拥有「作废谁」的资格。"""
        _book(session)
        old = self._old(session, "afkp-old", page=3, name="旧读数")
        session.commit()

        result = finalize_batch(
            session, book_id="b05", gen_batch=self._NEW_BATCH, page_start=1, page_end=6
        )
        session.expire_all()

        assert result.superseded_rows == 0
        assert session.get(AfKp, old.node_id).gen_status == "candidate"

    def test_two_submissions_do_not_stack_when_the_workspace_is_read(self, session, tmp_path):
        """端到端：同一区间跑两轮后，读面上的活行只剩第二批——旧候选已作废，屏侧不叠加。"""
        _scene(session, tmp_path, page_count=6)
        first_acceptance = _accepted(session)
        first = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=first_acceptance
        )
        second_acceptance = _accepted(session)
        second = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=second_acceptance
        )

        live = [row for row in _rows(session) if row.gen_status != "rejected"]
        assert {row.gen_batch for row in live} == {second.gen_batch}
        assert second.superseded_rows == first.candidate_rows
        row = kp_batch_row(second_acceptance, second)
        assert set(row.result) <= set(KP_RESULT_KEYS), "新键不进闭集就是自由键（`KA-27`）"
        assert row.result["superseded_rows"] == first.candidate_rows
        assert row.result["dropped_toc_pages"] == []


class TestLetAway:
    """`KA-13`：机器永远不与人工结论争同一身份——撞上一次人已背书的身份就把**新行**判 `rejected`。"""

    def test_a_pool_duplicate_of_an_endorsed_point_yields(self, session, tmp_path):
        """同父（都落在未归档池）同 `norm_key` ⇒ 新机器行 rejected；已确认行列值不变、行数只加自己那条。"""
        _scene(session, tmp_path, page_count=6, layout={})
        human = _draft(
            session,
            "afkp-h1",
            gen_status="candidate",
            gen_batch="",
            parent_id=None,
            name="数列极限的定义",
            norm_key=norm_key_for("数列极限的定义"),
        )
        KpRepository(session).set_confirmed(human.node_id, confirmed=True)
        session.commit()
        endorsed_before = _columns(human)
        rows_before = _kp_rows(session)

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=lambda section: [_point_draft("数列极限的定义", page=2, blocks=(1,))],
                points_by_section={},
            ),
            acceptance=_accepted(session),
        )
        session.expire_all()

        assert (outcome.let_away_rows, outcome.candidate_rows) == (1, 0)
        machine = [row for row in _rows(session) if row.node_id != human.node_id]
        assert [(row.gen_status, row.confirmed) for row in machine] == [("rejected", 0)]
        assert _columns(session.get(AfKp, human.node_id)) == endorsed_before
        assert _kp_rows(session) == rows_before + 1

    def test_a_confirmed_chapter_title_makes_the_new_outline_row_yield(self, session, tmp_path):
        """让位同样管目录行：`node_kind` 与父都在身份里，人已背书的章名不被新一批顶掉。"""
        _scene(session, tmp_path, page_count=6, layout={1: [(1, "第 1 章 极限")]})
        human = _draft(
            session,
            "afkp-h2",
            node_kind="outline",
            depth=1,
            gen_status="candidate",
            gen_batch="",
            parent_id=None,
            name="第 1 章 极限",
            norm_key=norm_key_for("第 1 章 极限"),
        )
        KpRepository(session).set_confirmed(human.node_id, confirmed=True)
        session.commit()

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=_accepted(session),
        )

        assert outcome.let_away_rows == 1
        new_rows = [
            row
            for row in _rows(session, node_kind="outline")
            if row.gen_batch == outcome.gen_batch
        ]
        assert [row.gen_status for row in new_rows] == ["rejected"]

    def test_rows_of_two_kinds_never_share_an_identity(self, session, tmp_path):
        """`ix_af_kp_norm` 的命名空间含 `node_kind`：同名的节目录行不让位于同名的点行。"""
        _scene(session, tmp_path, page_count=6, layout={})
        human = _draft(
            session,
            "afkp-h3",
            node_kind="outline",
            depth=2,
            gen_status="candidate",
            gen_batch="",
            parent_id=None,
            name="数列极限的定义",
            norm_key=norm_key_for("数列极限的定义"),
        )
        KpRepository(session).set_confirmed(human.node_id, confirmed=True)
        session.commit()

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=lambda section: [_point_draft("数列极限的定义", page=2, blocks=(1,))],
                points_by_section={},
            ),
            acceptance=_accepted(session),
        )

        assert (outcome.let_away_rows, outcome.candidate_rows) == (0, 1)

    def test_an_unendorsed_twin_does_not_yield(self, session, tmp_path):
        """让位的对手只有 `confirmed=1`：机器行之间不互相否决（判据位是人的背书，不是新旧）。"""
        _scene(session, tmp_path, page_count=6, layout={})
        _draft(
            session,
            "afkp-p1",
            gen_status="candidate",
            gen_batch="afkp-888888888888",
            parent_id=None,
            name="数列极限的定义",
            norm_key=norm_key_for("数列极限的定义"),
        )
        session.commit()

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=lambda section: [_point_draft("数列极限的定义", page=2, blocks=(1,))],
                points_by_section={},
            ),
            acceptance=_accepted(session),
        )

        assert (outcome.let_away_rows, outcome.candidate_rows) == (0, 1)

    def test_a_different_parent_is_not_the_same_identity(self, session, tmp_path):
        """字面读法把「同父」算进身份：人已确认行挂在别的父下时不让位——这一格是 R4 的形状缺口。"""
        _scene(session, tmp_path, page_count=6, layout={})
        human = _draft(
            session,
            "afkp-h4",
            gen_status="candidate",
            gen_batch="",
            parent_id="afkp-旧批目录行",
            name="数列极限的定义",
            norm_key=norm_key_for("数列极限的定义"),
        )
        KpRepository(session).set_confirmed(human.node_id, confirmed=True)
        session.commit()

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=lambda section: [_point_draft("数列极限的定义", page=2, blocks=(1,))],
                points_by_section={},
            ),
            acceptance=_accepted(session),
        )

        assert (outcome.let_away_rows, outcome.candidate_rows) == (0, 1)


class TestOrderingCycleGuard:
    """收尾的次序边 DAG：`prerequisite`/`extends` 成环即拒边并回指环路径，`co_requisite` 不参与。"""

    def test_only_this_batches_edge_is_dropped_when_the_cycle_crosses_batches(self, session):
        """成环时拒的是**本批 draft 行**那一条边；跨批那条长在 `candidate` 行上，按 `KA-11` 不可写。"""
        _book(session, page_count=6)
        _draft(
            session,
            "afkp-aaaa",
            gen_status="candidate",
            gen_batch="afkp-aaaaaaa00001",
            links=[{"kind": "prerequisite", "to": "afkp-dddd", "note": ""}],
        )
        _draft(
            session,
            "afkp-dddd",
            gen_batch="afkp-bbbbbbb00002",
            links=[
                {"kind": "prerequisite", "to": "afkp-aaaa", "note": "本批新边"},
                {"kind": "explains", "to": "afkp-aaaa", "note": "保留"},
            ],
        )
        session.commit()

        result = finalize_batch(session, book_id="b05", gen_batch="afkp-bbbbbbb00002")
        session.expire_all()

        assert [(e.node_id, e.kind, e.to) for e in result.dropped_edges] == [
            ("afkp-dddd", "prerequisite", "afkp-aaaa")
        ]
        assert result.dropped_edges[0].path == ("afkp-aaaa", "afkp-dddd", "afkp-aaaa")
        assert [entry["kind"] for entry in session.get(AfKp, "afkp-dddd").links] == ["explains"]
        assert session.get(AfKp, "afkp-aaaa").links == [
            {"kind": "prerequisite", "to": "afkp-dddd", "note": ""}
        ]
        assert result.candidate_rows == 1

    def test_a_cycle_among_existing_rows_is_reported_without_being_touched(self, session, tmp_path):
        """环长在非本批行上：只点环路径、不改一行——`candidate` 及以上行零被触碰优先于修环。"""
        _scene(session, tmp_path, page_count=6)
        first = _draft(
            session,
            "afkp-aaaa",
            gen_status="candidate",
            gen_batch="afkp-aaaaaaa00001",
            links=[{"kind": "prerequisite", "to": "afkp-bbbb", "note": ""}],
        )
        second = _draft(
            session,
            "afkp-bbbb",
            gen_status="candidate",
            gen_batch="afkp-aaaaaaa00001",
            links=[{"kind": "extends", "to": "afkp-aaaa", "note": ""}],
        )
        session.commit()
        before = (_columns(first), _columns(second))

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=_accepted(session),
        )
        session.expire_all()

        assert (outcome.dropped_edges, len(outcome.foreign_cycles)) == ((), 1)
        assert set(outcome.foreign_cycles[0]) == {first.node_id, second.node_id}
        assert (_columns(session.get(AfKp, first.node_id)), _columns(session.get(AfKp, second.node_id))) == before

    def test_mutual_co_requisite_collapses_onto_the_lower_endpoint(self, session):
        """`co_requisite` 不是次序语义：互指不成环，但按 `min,max` 归一端点序——多写的那条静默去掉。"""
        _book(session, page_count=6)
        lower = _draft(
            session,
            "afkp-aaaa",
            gen_batch="afkp-bbbbbbb00002",
            links=[{"kind": "co_requisite", "to": "afkp-bbbb", "note": "低"}],
        )
        upper = _draft(
            session,
            "afkp-bbbb",
            gen_batch="afkp-bbbbbbb00002",
            links=[{"kind": "co_requisite", "to": "afkp-aaaa", "note": "高"}],
        )
        session.commit()

        result = finalize_batch(session, book_id="b05", gen_batch="afkp-bbbbbbb00002")
        session.expire_all()

        assert (result.dropped_edges, result.foreign_cycles, result.symmetric_duplicates) == ((), (), 1)
        assert session.get(AfKp, lower.node_id).links == [
            {"kind": "co_requisite", "to": "afkp-bbbb", "note": "低"}
        ]
        assert session.get(AfKp, upper.node_id).links is None

    def test_a_reverse_duplicate_growing_on_someone_elses_row_is_left_alone(self, session):
        """归一只清本批行：反向重复长在 `candidate` 行上时不去替人归一——读侧的并集本来就认它。"""
        _book(session, page_count=6)
        ours = _draft(
            session,
            "afkp-aaaa",
            gen_batch="afkp-bbbbbbb00002",
            links=[{"kind": "co_requisite", "to": "afkp-zzzz", "note": "本批正向"}],
        )
        theirs = _draft(
            session,
            "afkp-zzzz",
            gen_status="candidate",
            gen_batch="afkp-aaaaaaa00001",
            links=[{"kind": "co_requisite", "to": "afkp-aaaa", "note": "别人的反向"}],
        )
        session.commit()
        before = _columns(theirs)

        result = finalize_batch(session, book_id="b05", gen_batch="afkp-bbbbbbb00002")
        session.expire_all()

        assert (result.symmetric_duplicates, result.dropped_edges) == (0, ())
        assert _columns(session.get(AfKp, theirs.node_id)) == before
        assert session.get(AfKp, ours.node_id).links == [
            {"kind": "co_requisite", "to": "afkp-zzzz", "note": "本批正向"}
        ]

    def test_a_reference_to_a_rejected_row_is_not_part_of_the_live_graph(self, session):
        """`rejected` 行退出结构：它身上的边不参与成环判定，本批指向它的边因此照落。"""
        _book(session, page_count=6)
        dead = _draft(
            session,
            "afkp-aaaa",
            gen_status="rejected",
            gen_batch="afkp-aaaaaaa00001",
            links=[{"kind": "prerequisite", "to": "afkp-dddd", "note": ""}],
        )
        mine = _draft(
            session,
            "afkp-dddd",
            gen_batch="afkp-bbbbbbb00002",
            links=[{"kind": "prerequisite", "to": "afkp-aaaa", "note": "指向已废行"}],
        )
        session.commit()

        result = finalize_batch(session, book_id="b05", gen_batch="afkp-bbbbbbb00002")
        session.expire_all()

        assert (result.dropped_edges, result.foreign_cycles) == ((), ())
        assert session.get(AfKp, mine.node_id).links == [
            {"kind": "prerequisite", "to": dead.node_id, "note": "指向已废行"}
        ]
        assert result.candidate_rows == 1


class TestLinkTargetsAreEndpoints:
    """`database-design.md`「边的端点存在」：草稿用名称回指，落库存的要就是 `node_id`。

    否则 `links[].to` 读出来是一个字符串线索而非端点身份，8903 与根侧都定位不到那一条行。
    """

    def test_a_title_reference_is_rewritten_to_the_existing_node_id(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        target = _draft(
            session,
            "afkp-t1",
            gen_status="candidate",
            gen_batch="",
            parent_id=None,
            name="函数极限的定义",
            norm_key=norm_key_for("函数极限的定义"),
        )
        session.commit()

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [
                        _point_draft(
                            "数列极限的定义",
                            page=2,
                            blocks=(1,),
                            links=[{"kind": "prerequisite", "to": "函数极限的定义", "note": ""}],
                        )
                    ]
                },
            ),
            acceptance=_accepted(session),
        )

        point = next(
            row for row in _rows(session, node_kind="point") if row.name == "数列极限的定义"
        )
        assert point.links == [{"kind": "prerequisite", "to": target.node_id, "note": ""}]
        assert outcome.dropped_nodes == 0

    def test_the_endorsed_row_wins_when_a_name_matches_several(self, session, tmp_path):
        """同名多行时端点取人已背书那一行：`KA-13` 的「不与人工争同一身份」在边上也成立。"""
        _scene(session, tmp_path, page_count=6)
        _draft(
            session,
            "afkp-0plain",
            gen_status="candidate",
            gen_batch="afkp-777777777777",
            parent_id=None,
            name="函数极限的定义",
            norm_key=norm_key_for("函数极限的定义"),
        )
        blessed = _draft(
            session,
            "afkp-blessed",
            gen_status="candidate",
            gen_batch="afkp-666666666666",
            parent_id=None,
            name="函数极限的定义",
            norm_key=norm_key_for("函数极限的定义"),
        )
        KpRepository(session).set_confirmed(blessed.node_id, confirmed=True)
        session.commit()

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [
                        _point_draft(
                            "数列极限的定义",
                            page=2,
                            blocks=(1,),
                            links=[{"kind": "prerequisite", "to": "函数极限的定义", "note": ""}],
                        )
                    ]
                },
            ),
            acceptance=_accepted(session),
        )

        point = next(
            row for row in _rows(session, node_kind="point") if row.name == "数列极限的定义"
        )
        assert point.links[0]["to"] == blessed.node_id


class TestStartupReconcile:
    """`KA-9`／「进程中途死」：启动期把残留 `draft` 按 `(book_id, gen_batch)` 整批判 `rejected`。"""

    def test_residue_drafts_are_rejected_batch_by_batch(self, session):
        _draft(session, "afkp-a1", gen_batch="afkp-111111111111")
        _draft(session, "afkp-a2", gen_batch="afkp-111111111111")
        _draft(session, "afkp-b1", gen_batch="afkp-222222222222")
        _draft(session, "afkp-x1", book_id="b11", gen_batch="afkp-555555555555")
        keeper = _draft(session, "afkp-c1", gen_status="candidate", gen_batch="afkp-333333333333")
        done = _draft(session, "afkp-c2", gen_status="rejected", gen_batch="afkp-444444444444")
        session.commit()
        before = (_columns(keeper), _columns(done))

        disposed = KpRepository(session).reconcile_draft_on_startup()
        session.expire_all()

        assert sorted(disposed) == [
            ("b05", "afkp-111111111111", 2),
            ("b05", "afkp-222222222222", 1),
            ("b11", "afkp-555555555555", 1),
        ]
        assert [session.get(AfKp, i).gen_status for i in ("afkp-a1", "afkp-a2", "afkp-b1")] == [
            "rejected"
        ] * 3
        assert (_columns(session.get(AfKp, keeper.node_id)), _columns(session.get(AfKp, done.node_id))) == before
        assert KpRepository(session).find_draft_batch("b05") is None
        assert KpRepository(session).reconcile_draft_on_startup() == []

    def test_the_reconciliation_never_writes_the_endorsed_slot(self, session):
        """系统腿同样越不过人工位：归一只改 `gen_status`，`confirmed` 逐字不动。"""
        row = _draft(session, "afkp-a1", gen_batch="afkp-111111111111")
        KpRepository(session).set_confirmed(row.node_id, confirmed=True)
        session.commit()
        rev_before = row.rev
        links_before = copy.deepcopy(row.links)
        name_before = row.name

        KpRepository(session).reconcile_draft_on_startup()
        session.expire_all()
        after = session.get(AfKp, row.node_id)

        assert (after.gen_status, after.confirmed) == ("rejected", 1)
        assert after.rev == rev_before + 1
        assert (after.links, after.name) == (links_before, name_before)


class TestNormConflictJudge:
    """设计篇「同父重名」的判据位＝`ix_af_kp_norm` 查重后点名既有 `node_id`，全库只有这一处。

    HTTP 的 422 映射由梳理端点面承接（契约用例在 `tests/contract/test_api_v1_contract.py`）；
    机器腿不吞这条判据（让位另按 `KA-13` 判）。
    """

    def test_same_parent_and_norm_key_names_the_existing_row(self, session):
        _draft(
            session,
            "afkp-a1",
            gen_status="candidate",
            parent_id="afkp-parent",
            name="极限",
            norm_key="极限",
        )
        session.commit()

        assert KpRepository(session).find_norm_conflict(
            book_id="b05", node_kind="point", parent_id="afkp-parent", norm_key="极限"
        ) == "afkp-a1"

    def test_the_pool_and_a_mounted_parent_are_different_scopes(self, session):
        _draft(
            session,
            "afkp-a1",
            gen_status="candidate",
            parent_id="afkp-parent",
            name="极限",
            norm_key="极限",
        )
        session.commit()

        assert KpRepository(session).find_norm_conflict(
            book_id="b05", node_kind="point", parent_id=None, norm_key="极限"
        ) is None

    def test_outline_and_point_rows_do_not_share_a_namespace(self, session):
        _draft(
            session,
            "afkp-a1",
            node_kind="outline",
            depth=1,
            gen_status="candidate",
            parent_id=None,
            name="第 1 章 极限",
            norm_key="第 1 章 极限",
        )
        session.commit()

        assert KpRepository(session).find_norm_conflict(
            book_id="b05", node_kind="point", parent_id=None, norm_key="第 1 章 极限"
        ) is None

    def test_a_row_is_not_its_own_conflict(self, session):
        _draft(session, "afkp-a1", gen_status="candidate", parent_id=None, name="极限", norm_key="极限")
        session.commit()

        assert KpRepository(session).find_norm_conflict(
            book_id="b05", node_kind="point", parent_id=None, norm_key="极限", exclude_node_id="afkp-a1"
        ) is None


class TestParentSource:
    """`KA-26`：机器不替人补结构结论——重跑不改人已挂的父，新行的父只可能是同批目录行或 `NULL`。"""

    def test_a_rerun_leaves_human_mounted_parents_untouched(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        mounted = _draft(
            session,
            "afkp-m1",
            gen_status="candidate",
            gen_batch="",
            parent_id="afkp-旧批目录行",
            name="人挂的点",
            norm_key="人挂的点",
        )
        session.commit()
        before = _columns(mounted)

        second = _accepted(session)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [_point_draft("数列极限的定义", page=2, blocks=(1,))]
                },
            ),
            acceptance=second,
        )
        session.expire_all()

        assert _columns(session.get(AfKp, mounted.node_id)) == before
        batch_points = [
            row for row in _rows(session, node_kind="point") if row.gen_batch == second.gen_batch
        ]
        batch_outline_ids = {
            row.node_id
            for row in _rows(session, node_kind="outline")
            if row.gen_batch == second.gen_batch
        }
        assert {row.parent_id for row in batch_points} <= batch_outline_ids | {None}

    def test_a_batch_row_mounted_outside_the_batch_is_detached_into_the_pool(self, session):
        """收尾的挂父来源校验（`KA-26` 的实现落点）：违法父不靠执行体自觉，退 `NULL` 入池、不改挂别处。"""
        _book(session, page_count=6)
        stray = _draft(session, "afkp-a1", parent_id="afkp-旧批目录行")
        keeper = _draft(
            session,
            "afkp-a2",
            gen_status="candidate",
            parent_id="afkp-旧批目录行",
            name="已定稿的行",
            norm_key="已定稿的行",
        )
        session.commit()
        before = _columns(keeper)

        result = finalize_batch(session, book_id="b05", gen_batch="afkp-0123456789ab")
        session.expire_all()

        assert (result.detached_to_pool, result.let_away_rows, result.candidate_rows) == (1, 0, 1)
        assert session.get(AfKp, stray.node_id).parent_id is None
        assert _columns(session.get(AfKp, keeper.node_id)) == before

    def test_an_empty_outline_stage_still_leaves_the_whole_range_as_one_pool_span(self, session, tmp_path):
        """目录阶段零产出（认不出标题的书）不塌区间：整段正文仍喂点阶段，抽出的点全入池。"""
        _scene(session, tmp_path, page_count=6, layout={})

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                on_points=lambda section: [_point_draft("无主正文点", page=3, blocks=(1,))],
                points_by_section={},
            ),
            acceptance=_accepted(session),
        )

        assert (outcome.outline_rows, outcome.point_rows, outcome.unarchored) == (0, 1, 1)
        point = _rows(session, node_kind="point")[0]
        assert (point.parent_id, point.gen_status) == (None, "candidate")


class TestExportOrdering:
    """`KA-15` 的导出序（工作区与导出面共用）：目录前序先出、点随其父、池与悬父尾置、环不丢行。

    输入序一律给**反的**：判据是「读侧拿到的顺序自带父先于子」，不是「入库顺序碰巧对」。
    """

    def _ids(self, rows: list[AfKp]) -> list[str]:
        return [row.node_id for row in order_for_export(rows)]

    def test_a_point_older_than_its_parent_is_still_emitted_after_it(self, session):
        """拉取方按序插入时父必须已在表里：点行先入库也不改变导出序。"""
        chapter = _draft(session, "afkp-ch", node_kind="outline", order_hint=1)
        point = _draft(session, "afkp-pt", node_kind="point", parent_id="afkp-ch")

        assert self._ids([point, chapter]) == ["afkp-ch", "afkp-pt"]

    def test_outline_siblings_follow_order_hint_not_insertion(self, session):
        """章按 `order_hint` 出，节跟在自己的章后面：声明过的序就是外发的序。"""
        chapter_one = _draft(session, "afkp-c1", node_kind="outline", order_hint=1)
        chapter_two = _draft(session, "afkp-c2", node_kind="outline", order_hint=2)
        section = _draft(session, "afkp-s1", node_kind="outline", parent_id="afkp-c2", order_hint=1)

        assert self._ids([section, chapter_two, chapter_one]) == ["afkp-c1", "afkp-c2", "afkp-s1"]

    def test_points_of_one_parent_also_follow_order_hint(self, session):
        """同父的点之间也有序：`order_hint` 大的后出，没声明过的不插队。"""
        chapter = _draft(session, "afkp-ch", node_kind="outline", order_hint=1)
        first = _draft(session, "afkp-p1", parent_id="afkp-ch", order_hint=1)
        second = _draft(session, "afkp-p2", parent_id="afkp-ch", order_hint=2)

        assert self._ids([second, first, chapter]) == ["afkp-ch", "afkp-p1", "afkp-p2"]

    def test_the_pool_and_a_dangling_parent_both_go_to_the_tail(self, session):
        """未归档池与「父没进导出集」同尾置：把子排在一个还没外发的父后面＝拉取方当场撞空父。"""
        chapter = _draft(session, "afkp-ch", node_kind="outline", order_hint=1)
        mounted = _draft(session, "afkp-p1", parent_id="afkp-ch")
        pool = _draft(session, "afkp-p2")
        dangling = _draft(session, "afkp-p3", parent_id="afkp-gone")

        ids = self._ids([dangling, pool, mounted, chapter])

        assert ids[:2] == ["afkp-ch", "afkp-p1"]
        assert ids[2:] == ["afkp-p2", "afkp-p3"]

    def test_a_cycle_among_outline_rows_still_emits_every_row_once(self, session):
        """人工改挂能把目录挂成环：环保护只停止重访，不静默丢行。"""
        lower = _draft(session, "afkp-o1", node_kind="outline", parent_id="afkp-o2", order_hint=1)
        upper = _draft(session, "afkp-o2", node_kind="outline", parent_id="afkp-o1", order_hint=2)

        assert self._ids([lower, upper]) == ["afkp-o1", "afkp-o2"]


def _kp_dir(tmp_path: Path, book_id: str = "b05") -> Path:
    """长文目录＝`<book_dir>/kp/`：测试自己按 `KA-20` 的派生式算一遍，不沿用实现里的私设基准。"""
    return tmp_path / "parsed" / "math-advanced" / "math_analysis" / book_id / "kp"


class TestNarrativePlacement:
    """`KA-20`：长文只经派生路径落书目录、走原子替换；文件腿不碰 `narrative_ref` 列。"""

    def test_the_file_lands_on_the_derived_path_and_the_name_is_one_segment(self, session, tmp_path):
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()

        name = write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="# ε-δ\n\n正文")

        assert name == "afkp-a1.md"
        assert "/" not in name and "\\" not in name
        assert _kp_dir(tmp_path).joinpath(name).read_text(encoding="utf-8") == "# ε-δ\n\n正文"

    def test_rewriting_replaces_in_place_and_leaves_no_transient_residue(self, session, tmp_path):
        """`PP-4`：临时文件 → 校验 → 原子替换，正常路径不留 `.tmp-`，目录里始终只有那一个文件。"""
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="第一版")

        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="第二版")

        assert [path.name for path in scan_transient_residue(_kp_dir(tmp_path))] == []
        assert sorted(path.name for path in _kp_dir(tmp_path).iterdir()) == ["afkp-a1.md"]
        assert _kp_dir(tmp_path).joinpath("afkp-a1.md").read_text(encoding="utf-8") == "第二版"

    def test_the_narrative_path_does_not_write_the_narrative_ref_column(self, session, tmp_path):
        """文件腿的越域判据：写文件不顺手改列——列的写者是第四条系统腿，不在本函数里。

        `KA-30` 落地后这一条仍成立，只是不再等于「全表无人写」：端点面按「先落盘再落列」把两件事
        分给两处（`write_narrative` 与 `KpRepository.set_narrative_ref`），本用例守的是文件腿单边。
        写列一侧的守护见 `tests/unit/test_db_knowledge.py::TestNarrativeRefSystemLeg`。
        """
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        before = KpRepository(session).get("afkp-a1")
        assert before is not None
        snapshot = (before.narrative_ref, before.rev, before.updated_at)

        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="正文")

        session.expire_all()
        after = KpRepository(session).get("afkp-a1")
        assert after is not None
        assert (after.narrative_ref, after.rev, after.updated_at) == snapshot == ("", 0, before.created_at)
        assert int(session.scalar(select(func.count()).select_from(AfKp).where(AfKp.narrative_ref != "")) or 0) == 0

    def test_a_row_that_was_never_committed_has_no_path_to_write_into(self, session, tmp_path):
        """「写在行提交之后」的可守形状：库里没有这一行就写不出文件，先文件后行＝正文无主。"""
        _book(session)
        _draft(session, "afkp-a1")
        session.rollback()

        with pytest.raises(KpGateError) as excinfo:
            write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="正文")

        assert excinfo.value.status_code == 404
        assert not _kp_dir(tmp_path).exists()

    def test_a_node_id_that_is_not_a_single_segment_never_reaches_the_disk(self, session, tmp_path):
        """`PP-5` 的纵深防御：`node_id` 就是文件名的一部分，越根的名字在拼路径前拒。

        这一条不靠 `ensure_within` 兜——`b05/kp/afkp-../x.md` 归一化后仍在数据根内，只有单段名闸拦得住。
        """
        _book(session)
        _draft(session, "afkp-../outside")
        session.commit()

        with pytest.raises(ValueError, match="node_id"):
            write_narrative(session, data_root=tmp_path, node_id="afkp-../outside", content="正文")

        assert not (tmp_path / "parsed" / "math-advanced" / "math_analysis" / "b05" / "outside.md").exists()


class TestNodeDeletionPath:
    """`KA-21` 的编排一半：删行与清边先提交，文件只在提交之后清，清不掉只告警。"""

    def test_removing_a_node_takes_the_row_and_its_narrative_file_together(self, session, tmp_path):
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="正文")

        assert remove_node(session, data_root=tmp_path, node_id="afkp-a1") is True

        assert KpRepository(session).get("afkp-a1") is None
        assert not _kp_dir(tmp_path).joinpath("afkp-a1.md").exists()

    def test_the_file_goes_only_after_the_transaction_is_committed(self, session, tmp_path, monkeypatch):
        """为什么不在事务里删文件：提交失败时行会回来，先删的正文回不来（`KA-21` 的代价那一栏）。"""
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="正文")

        def _fail(self: Session) -> None:
            raise RuntimeError("提交失败")

        monkeypatch.setattr(Session, "commit", _fail)
        with pytest.raises(RuntimeError):
            remove_node(session, data_root=tmp_path, node_id="afkp-a1")
        monkeypatch.undo()

        assert _kp_dir(tmp_path).joinpath("afkp-a1.md").is_file()

    def test_a_cleanup_failure_only_warns_and_never_undoes_the_delete(self, session, tmp_path, monkeypatch, caplog):
        """清理失败只告警：行已提交删除，不回滚、不抛错——残留交给「只读清单先行」的裁剪流程收口。"""

        def _refuse(self: Path, *args, **kwargs) -> None:
            raise OSError("磁盘占用")

        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="正文")
        monkeypatch.setattr(Path, "unlink", _refuse)

        with caplog.at_level(logging.WARNING):
            assert remove_node(session, data_root=tmp_path, node_id="afkp-a1") is True

        assert KpRepository(session).get("afkp-a1") is None
        assert "afkp-a1.md" in caplog.text

    def test_removing_a_node_without_a_narrative_file_is_not_a_failure(self, session, tmp_path):
        """没有正文的行照样删得掉：文件缺席不是错误，也不该为它造一条告警。"""
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()

        assert remove_node(session, data_root=tmp_path, node_id="afkp-a1") is True

        assert KpRepository(session).get("afkp-a1") is None

    def test_removing_a_missing_node_reports_false_and_touches_no_disk(self, session, tmp_path):
        assert remove_node(session, data_root=tmp_path, node_id="afkp-nope") is False
        assert not _kp_dir(tmp_path).exists()


class TestNarrativeOrphanList:
    """孤儿只读清单（`KA-21`／database-design.md「保留与裁剪」的只读清单先行）：点名，不删。"""

    def test_a_file_whose_node_is_gone_gets_named_and_stays_on_disk(self, session, tmp_path):
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="活行的正文")
        _kp_dir(tmp_path).joinpath("afkp-ghost.md").write_text("孤儿", encoding="utf-8")

        assert list_narrative_orphans(session, data_root=tmp_path, book_id="b05") == ["afkp-ghost.md"]
        assert _kp_dir(tmp_path).joinpath("afkp-ghost.md").is_file()

    def test_transient_residue_is_left_to_the_pp_six_watcher(self, session, tmp_path):
        """`.tmp-`/`.old-` 属 PP-6 的识别式与告警面，不混进孤儿清单：两者的处置流程不是同一条。"""
        _book(session)
        _draft(session, "afkp-a1")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="正文")
        _kp_dir(tmp_path).joinpath(".afkp-a1.md.tmp-00000000").write_text("半成品", encoding="utf-8")

        assert list_narrative_orphans(session, data_root=tmp_path, book_id="b05") == []

    def test_a_rejected_row_is_not_an_orphan(self, session, tmp_path):
        """`rejected` 行仍在表里，它的正文要留着读批次账面（`KA-9`／`KA-13` 的「行留在表里」）。"""
        _book(session)
        _draft(session, "afkp-a1", gen_status="rejected")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="作废批次的遗留")

        assert list_narrative_orphans(session, data_root=tmp_path, book_id="b05") == []

    def test_an_absent_book_directory_is_an_empty_list(self, session, tmp_path):
        """没写过长文的书不该报错：只读清单要能被裁剪流程逐书空跑一遍。"""
        _book(session)

        assert list_narrative_orphans(session, data_root=tmp_path, book_id="b05") == []

    def test_another_books_directory_is_out_of_scope(self, session, tmp_path):
        """清单按书算：`b06` 目录里的文件不由 `b05` 的清单点名（逐书执行是裁剪的判据）。"""
        _book(session, "b05")
        _book(session, "b06")
        _draft(session, "afkp-a1", book_id="b06")
        session.commit()
        write_narrative(session, data_root=tmp_path, node_id="afkp-a1", content="b06 的正文")

        assert list_narrative_orphans(session, data_root=tmp_path, book_id="b06") == []
        assert list_narrative_orphans(session, data_root=tmp_path, book_id="b05") == []


#: 三枚 NOT NULL 身份列的取值：R6 甲案下只能由装配点注入，测试给的就是「接线的人」。
_LEDGER_IDENTITY = LlmCallIdentity(mode="local", provider="vllm", model="qwen-plus")


def _ledger_env(tmp_path: Path):
    """四表与 `qed_llm_calls` 同库、但各走自己连接的环境。

    用文件库而不是内存库：账必须能在批次会话回滚之后还在，单连接的内存库看不见这种隔离。
    """
    store = create_engine(f"sqlite+pysqlite:///{tmp_path / 'kp_ledger.db'}", connect_args={"timeout": 5})
    Base.metadata.create_all(store)
    create_ledger_table(store)
    factory = sessionmaker(bind=store, class_=Session, future=True)
    with factory() as session:
        _scene(session, tmp_path, page_count=6)
    return store, factory


def _ledger_points() -> dict[str, list[dict]]:
    return {
        "1.1 数列极限": [_point_draft("数列极限的定义", page=2, blocks=(1,))],
        "1.2 函数极限": [_point_draft("函数极限的定义", page=4, blocks=(0,))],
    }


def _accounted(inner: FakeKnowledgeEngine, factory, acceptance) -> LedgeredKnowledgeEngine:
    return LedgeredKnowledgeEngine(
        inner,
        LlmCallLedger(factory),
        _LEDGER_IDENTITY,
        book_id=acceptance.book_id,
        gen_batch=acceptance.gen_batch,
    )


class _GatewayShapedEngine(FakeKnowledgeEngine):
    """R8 甲里 8900 网关适配器的形状桩：声明「账在外面」，并接受执行体交来的批次身份。

    只加两样本仓之外的能力（`accounts_externally` 与 `bind_batch`），知识点腿本体沿用桩，
    免得用例误以为「换成真适配器才有这行为」。
    """

    name = "qed-gateway"
    accounts_externally = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bound: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def bind_batch(self, *, book_id: str, gen_batch: str, review_note: str = "") -> _GatewayShapedEngine:
        self.bound.append((book_id, gen_batch))
        self.notes.append(review_note)
        self.calls.append(("bind", (book_id, gen_batch)))
        return self


def _kp_runner(factory, data_root: Path, inner: FakeKnowledgeEngine, **kwargs) -> JobRunner:
    """一台只接了梳理槽位的执行器：解析工厂在这里永不触发，只为满足构造签名。"""
    return JobRunner(
        session_factory=factory,
        engine_factory=lambda name: inner,
        data_root=data_root,
        background=False,
        knowledge_engine_factory=lambda name: inner,
        **kwargs,
    )


class TestCallLedger:
    """`KA-23` 的账面与 `KA-22` 的接线面：一调用一行、失败也留账、回滚不销账、接线不全当场拒跑。"""

    def test_the_wrapper_is_still_a_knowledge_engine(self, tmp_path):
        """执行体只认协议：包装器替引擎出面时，`name`／`health`／点腿一个都不能少。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)

        wrapped = _accounted(FakeKnowledgeEngine(), factory, acceptance)

        assert isinstance(wrapped, KnowledgeEngine)
        assert wrapped.name == "fake-text"
        assert wrapped.health() is True

    def test_the_wrapper_forwards_an_out_of_protocol_capability(self, tmp_path):
        """执行体的 `finally` 靠 `getattr(engine, "close", None)` 收尾：包装器不转发就漏掉适配器的资源。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)

        closed: list[str] = []

        class _Closable(FakeKnowledgeEngine):
            def close(self) -> None:
                closed.append(self.name)

        wrapped = _accounted(_Closable(), factory, acceptance)
        wrapped.close()

        assert closed == ["fake-text"]

    def test_the_wrapper_does_not_rename_the_engine(self, tmp_path):
        """`model_ref` 写的是 `engine.name`：包装器改名等于把账面模型名写成别的名字（目录行走规则位，不经引擎）。"""
        store, factory = _ledger_env(tmp_path)
        inner = FakeKnowledgeEngine(points_by_section=_ledger_points())
        with factory() as session:
            acceptance = _accepted(session)
            run_knowledge_job(
                session, data_root=tmp_path, engine=_accounted(inner, factory, acceptance), acceptance=acceptance
            )

            assert {(row.node_kind, row.model_ref) for row in _rows(session)} == {
                ("outline", "rule:heading"),
                ("point", "fake-text"),
            }

    def test_one_model_call_appends_exactly_one_ledger_row(self, tmp_path):
        """一调用一行：账的行序与引擎的调用序逐位相等——既没漏记一次调用，也没把一次调用记两行。"""
        store, factory = _ledger_env(tmp_path)
        inner = FakeKnowledgeEngine(points_by_section=_ledger_points())
        with factory() as session:
            acceptance = _accepted(session)
            run_knowledge_job(
                session, data_root=tmp_path, engine=_accounted(inner, factory, acceptance), acceptance=acceptance
            )

        rows = read_ledger_rows(store)
        assert [row["step"] for row in rows] == [name for name, _ in inner.calls]
        assert [row["status"] for row in rows] == ["success"] * len(rows)
        assert {row["service"] for row in rows} == {"axiom_flow"}
        assert {row["endpoint"] for row in rows} == {"text"}
        assert {row["task"] for row in rows} == {"kp-curation"}
        assert {row["model"] for row in rows} == {"qwen-plus"}

    def test_the_row_carries_the_measured_duration_and_the_batch_attribution(self, tmp_path):
        """归因走 `meta` 扩展槽：哪本书、哪一批、哪个引擎、喂进去多少——身份列不拿来装批次号。

        点腿与判页腿各查一行：两枚 `meta` 的键集都由包装器给定，不在这里替适配器兜底。
        """
        store, factory = _ledger_env(tmp_path)
        inner = FakeKnowledgeEngine(points_by_section=_ledger_points())
        with factory() as session:
            acceptance = _accepted(session)
            run_knowledge_job(
                session, data_root=tmp_path, engine=_accounted(inner, factory, acceptance), acceptance=acceptance
            )

        rows = read_ledger_rows(store)
        point_meta = json.loads(next(row for row in rows if row["step"] == "points")["meta"])
        assert set(point_meta) == {
            "book_id",
            "gen_batch",
            "engine",
            "section",
            "page_start",
            "page_end",
            "page_count",
        }
        assert point_meta["book_id"] == "b05"
        assert point_meta["gen_batch"] == acceptance.gen_batch
        assert point_meta["engine"] == "fake-text"
        first_section = next(payload for step, payload in inner.calls if step == "points")
        assert point_meta["page_count"] == len(first_section.pages)
        assert point_meta["page_start"] <= point_meta["page_end"]

        toc_meta = json.loads(next(row for row in rows if row["step"] == "toc_pages")["meta"])
        assert set(toc_meta) == {"book_id", "gen_batch", "engine", "page_count", "page_nos"}, "判页腿的输入面＝样本页集"
        assert toc_meta["page_count"] == len(inner.calls[0][1])

        assert all(row["duration_ms"] is not None and row["duration_ms"] >= 0 for row in rows)
        assert all(row["created_at"] for row in rows)

    def test_the_duration_is_measured_across_the_call(self, tmp_path, monkeypatch):
        """`duration_ms` 是包装器两侧的时钟差：写死一个数就等于把「模型耗时」这一列变成假的。"""
        store, factory = _ledger_env(tmp_path)
        state = {"t": 100.0}

        def clock() -> float:
            state["t"] += 0.25
            return state["t"]

        monkeypatch.setattr("axiom_flow.orchestrator.knowledge.time.perf_counter", clock)
        with factory() as session:
            acceptance = _accepted(session)
            inner = FakeKnowledgeEngine()
            run_knowledge_job(
                session, data_root=tmp_path, engine=_accounted(inner, factory, acceptance), acceptance=acceptance
            )

        rows = read_ledger_rows(store)
        assert len(rows) == len(inner.calls) > 0
        assert [row["duration_ms"] for row in rows] == [250] * len(rows)

    def test_a_failed_section_leaves_its_error_row_and_the_batch_continues(self, tmp_path):
        """失败域延长到节（`PP-11` 的同一条）：账上得看得见「这一节坏过」，而不是只剩成功的节。"""
        store, factory = _ledger_env(tmp_path)
        inner = FakeKnowledgeEngine(
            points_by_section=_ledger_points(),
            failing_sections=("1.1 数列极限",),
        )
        with factory() as session:
            acceptance = _accepted(session)
            run_knowledge_job(
                session, data_root=tmp_path, engine=_accounted(inner, factory, acceptance), acceptance=acceptance
            )

            survivors = _rows(session, node_kind="point")

        rows = read_ledger_rows(store)
        assert [row["step"] for row in rows] == [name for name, _ in inner.calls]
        failed = [row for row in rows if row["status"] == "error"]
        assert len(failed) == 1
        assert "模型服务中断" in failed[0]["error"]
        assert len(survivors) == 1

    def test_the_ledger_outlives_the_batch_it_accounts_for(self, tmp_path):
        """调用已发生、批次随后回滚 ⇒ 那一行账仍在：账走自己的会话，不跟着批次回滚。"""
        store, factory = _ledger_env(tmp_path)

        class _PointsBroken(FakeKnowledgeEngine):
            def points(self, section):
                self.calls.append(("points", section))
                raise EngineError("模型服务中断：点阶段")

        section = SectionInput(
            parent_id=None,
            title="1.1 数列极限",
            page_start=1,
            page_end=1,
            pages=(PageBody(1, "正文。"),),
        )
        with factory() as session:
            acceptance = _accepted(session)
            wrapped = _accounted(_PointsBroken(), factory, acceptance)
            with pytest.raises(EngineError):
                wrapped.points(section)
            session.rollback()

            assert _kp_rows(session) == 0

        assert [(row["step"], row["status"]) for row in read_ledger_rows(store)] == [("points", "error")]

    def test_a_wired_slot_without_an_identity_refuses_the_batch(self, tmp_path):
        """甲案的拒跑面（R6）：引擎接上了却没给身份 ⇒ 受理期就抛，不静默跑一批无账的调用。"""
        store, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)
        inner = FakeKnowledgeEngine()
        runner = _kp_runner(factory, tmp_path, inner, kp_call_ledger=LlmCallLedger(factory))

        with pytest.raises(KpLedgerWiringError, match="身份"):
            runner.submit_knowledge(acceptance=acceptance, engine_name="fake-text")

        assert inner.calls == []
        assert read_ledger_rows(store) == []
        with factory() as session:
            assert _kp_rows(session) == 0
            assert _kp_ledger_rows(session) == 0

    def test_a_wired_slot_without_a_ledger_refuses_the_batch(self, tmp_path):
        """拒跑是对称的：只给身份不给账同样不行——否则「有身份」就会被当成「已记账」。"""
        store, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)
        inner = FakeKnowledgeEngine()
        runner = _kp_runner(factory, tmp_path, inner, kp_call_identity=_LEDGER_IDENTITY)

        with pytest.raises(KpLedgerWiringError, match="账"):
            runner.submit_knowledge(acceptance=acceptance, engine_name="fake-text")

        assert inner.calls == []
        with factory() as session:
            assert _kp_ledger_rows(session) == 0

    def test_an_externally_accounted_engine_runs_without_the_local_ledger(self, tmp_path):
        """R8 甲的接线面：出站经 8900 的适配器自报「账在外面」⇒ 同批不接本仓账也照跑，且不落本仓行。"""
        store, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)
        inner = _GatewayShapedEngine(points_by_section=_ledger_points())
        runner = _kp_runner(factory, tmp_path, inner)

        runner.submit_knowledge(acceptance=acceptance, engine_name="qed-gateway")

        with factory() as session:
            assert len(_rows(session, node_kind="point")) == 2
            assert _kp_ledger_rows(session) == 1  # 批次流水照旧一行（KA-27 与调用账是两回事）
        assert read_ledger_rows(store) == []
        assert inner.bound == [(acceptance.book_id, acceptance.gen_batch)]

    def test_the_execution_body_hands_the_batch_identity_to_the_engine_first(self, tmp_path):
        """归因不靠适配器猜：执行体第一眼把批次身份交出去，其后每一次调用都带着它走。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)
        inner = _GatewayShapedEngine()

        with factory() as session:
            run_knowledge_job(session, data_root=tmp_path, engine=inner, acceptance=acceptance)
            session.commit()

        assert [name for name, _ in inner.calls][0] == "bind"
        assert inner.bound == [(acceptance.book_id, acceptance.gen_batch)]

    def test_the_review_note_reaches_the_binding_and_the_ledger_row(self, tmp_path):
        """AF-T5 甲的两半同批：备注既交给适配器（进 prompt），也随批次流水存档（可指认）。

        执行体不解释备注的内容——它只做两件事：绑定时递过去、跑完后进 `payload`。
        """
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session, review_note="第 3 章的节标题把书眉粘进来了")
        inner = _GatewayShapedEngine()
        runner = _kp_runner(factory, tmp_path, inner)

        runner.submit_knowledge(acceptance=acceptance, engine_name="qed-gateway")

        with factory() as session:
            ledger = OperationRepository(session).find_kp_batch(acceptance.book_id, acceptance.gen_batch)

        assert inner.notes == ["第 3 章的节标题把书眉粘进来了"]
        assert ledger.payload["review_note"] == "第 3 章的节标题把书眉粘进来了"

    def test_an_engine_that_declares_nothing_still_gets_no_batch_identity(self, tmp_path):
        """没声明 `bind_batch` 的引擎（协议内实现）不该被执行体硬塞参数：协议之外一条不假设。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)
        inner = FakeKnowledgeEngine()

        with factory() as session:
            run_knowledge_job(session, data_root=tmp_path, engine=inner, acceptance=acceptance)
            session.commit()

        steps = [name for name, _ in inner.calls]
        assert steps[0] == "toc_pages", "没声明 `bind_batch` 也照常跑完整批：判页腿是第一枚调用"
        assert "bind" not in steps

    def test_the_runner_records_every_call_of_the_batch(self, tmp_path):
        """判据 10 的生产端：包装器挂在执行体唯一入口上，批次经 `JobRunner` 跑完就必有条条对应的账。"""
        store, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)
        inner = FakeKnowledgeEngine(points_by_section=_ledger_points())
        runner = _kp_runner(
            factory, tmp_path, inner, kp_call_ledger=LlmCallLedger(factory), kp_call_identity=_LEDGER_IDENTITY
        )

        runner.submit_knowledge(acceptance=acceptance, engine_name="fake-text")

        rows = read_ledger_rows(store)
        assert [row["step"] for row in rows] == [name for name, _ in inner.calls]
        assert len(rows) == len(inner.calls) > 1
        with factory() as session:
            assert _kp_ledger_rows(session) == 1


class TestSectionSpansOf:
    """`KA-42`：切区间那条算式是纯函数，点腿取材与读面派生两键各调一次同一处。

    抽出来之前它只在 `_section_spans` 里，读面要发「从 X 页到 Y 页」就得整份复制排序键与边角判据
    ——那是第二处事实源，屏上圈的范围与模型实际读过的正文会分叉。
    """

    @staticmethod
    def _rows(*pages: int) -> list[SpanRow]:
        return [
            SpanRow(node_id=f"n{index}", name=f"第{index + 1}节", order_hint=index, page=page)
            for index, page in enumerate(pages, start=1)
        ]

    def test_the_last_section_runs_to_the_end_of_the_book(self):
        """末行到全书末页：末节正文一直延伸到书尾，用最大锚点页会把它的尾巴切掉。"""
        spans = section_spans_of(self._rows(2, 5), first_page=1, last_page=9)

        assert [(span.parent_id, span.page_start, span.page_end) for span in spans] == [
            (None, 1, 1),
            ("n1", 2, 4),
            ("n2", 5, 9),
        ]

    def test_the_end_is_the_next_rows_start_minus_one(self):
        """需求方给的就是这条：终点＝下一条目录行的起点页 − 1，不是本行自己的锚点页。"""
        spans = section_spans_of(self._rows(45, 93, 117), first_page=45, last_page=320)

        assert [(span.page_start, span.page_end) for span in spans] == [(45, 92), (93, 116), (117, 320)]

    def test_two_rows_on_one_page_still_each_get_that_page(self):
        """两行挤同一页时后一条的区间为空，仍按它自己那页取：宁可两节共读一页，也不让正文没有归属。"""
        spans = section_spans_of(self._rows(3, 3), first_page=1, last_page=9)

        assert [(span.parent_id, span.page_start, span.page_end) for span in spans] == [
            (None, 1, 2),
            ("n1", 3, 3),
            ("n2", 3, 9),
        ]

    def test_a_row_past_the_last_page_is_not_given_a_span(self):
        """起点页超出全书末页的行不参与切分：夹不出区间，读面那两键因此为空而不是造一个假终点。"""
        spans = section_spans_of(self._rows(2, 40), first_page=1, last_page=9)

        assert [(span.parent_id, span.page_start, span.page_end) for span in spans] == [
            (None, 1, 1),
            ("n1", 2, 9),
        ]

    def test_an_empty_outline_makes_the_whole_body_one_section(self):
        """目录零产出时整段正文自成一段：正文不因为目录空了就消失，抽出的点全入池（`KA-7`）。"""
        spans = section_spans_of([], first_page=1, last_page=9)

        assert [(span.parent_id, span.title, span.page_start, span.page_end) for span in spans] == [
            (None, "", 1, 9)
        ]

    def test_the_input_order_does_not_decide_the_spans(self):
        """排序键 `(order_hint, 起点页)` 住在算式里而不是调用方：乱序喂进去读数不变。"""
        rows = self._rows(2, 5)

        assert section_spans_of(list(reversed(rows)), first_page=1, last_page=9) == section_spans_of(
            rows, first_page=1, last_page=9
        )

    @staticmethod
    def _tree(*specs: tuple[str, int, int, str]) -> list[SpanRow]:
        """`(node_id, depth, page, toc_role)` → 树序输入（`order_hint` 按给定次序）。"""
        return [
            SpanRow(
                node_id=node_id,
                name=node_id,
                order_hint=index,
                page=page,
                depth=depth,
                toc_role=role,
            )
            for index, (node_id, depth, page, role) in enumerate(specs, start=1)
        ]

    def test_a_chapter_runs_to_before_the_next_chapter_even_when_its_first_section_shares_the_page(self):
        """DEFECT-028：章与其首节同页，按「下一条行」算章只剩一页；树序判给回 p9–28。"""
        spans = section_spans_of(
            self._tree(
                ("ch1", 1, 9, "knowledge"),
                ("s11", 2, 9, "knowledge"),
                ("s12", 2, 20, "knowledge"),
                ("ch2", 1, 29, "knowledge"),
            ),
            first_page=9,
            last_page=317,
        )

        assert [(span.parent_id, span.page_start, span.page_end) for span in spans] == [
            ("ch1", 9, 28),
            ("s11", 9, 19),
            ("s12", 20, 28),
            ("ch2", 29, 317),
        ]

    def test_a_reference_row_spans_only_its_own_page(self):
        """DEFECT-028：符号表／书目这类行的物理正文不在目录条目页，按树序拼出去会造出跨章巨区间。"""
        spans = section_spans_of(
            self._tree(
                ("sym", 1, 5, "reference"),
                ("ch1", 1, 9, "knowledge"),
                ("bib", 1, 313, "reference"),
            ),
            first_page=4,
            last_page=317,
        )

        assert [(span.parent_id, span.page_start, span.page_end) for span in spans] == [
            (None, 4, 4),
            ("sym", 5, 5),
            ("ch1", 9, 312),
            ("bib", 313, 313),
        ]


class TestContentOnlyStage:
    """`KA-43`：`stage='content'`＝拿在册目录做知识点——目录腿零发起、在册行重构成区间，
    同批为每枚被处理的目录行补一枚节级总结（第五条系统腿），在册目录行的结构列一行不动。

    这一枚入参存在的原因＝真机目录步约十分钟而知识点腿每节 137~205 秒：目录是人工刚审过的结构，
    重跑一轮就是把模型时间花在没人要求重做的事上。
    """

    #: 在册目录行的结构读数：内容批次对这些列的影响必须是零（`summary` 与 `updated_at` 不在里面）。
    _STRUCTURAL = ("name", "norm_key", "node_kind", "gen_status", "rev", "gen_batch", "confirmed",
                   "parent_id", "order_hint", "depth")

    def _structure(self, row: AfKp) -> tuple:
        return tuple(getattr(row, name) for name in self._STRUCTURAL) + (
            copy.deepcopy(row.anchors),
        )

    @staticmethod
    def _registered(session: Session, tmp_path: Path) -> tuple[list[AfKp], object]:
        """先跑一轮只目录批次把三枚目录行留在册（章 p1／1.1 p2／1.2 p4），再发内容批次。"""
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=_accepted(session, stage="outline"),
        )
        session.expire_all()
        rows = _rows(session, node_kind="outline")
        assert len(rows) == 3, "场景前提：三枚在册目录行"
        return rows, _accepted(session, stage="content")

    def _sections(self, engine: FakeKnowledgeEngine) -> list[SectionInput]:
        return [payload for step, payload in engine.calls if step == "points"]

    # ------------------------------------------------------------------ 目录腿与取材

    def test_the_content_stage_never_calls_the_outline_legs(self, session, tmp_path):
        _, accepted = self._registered(session, tmp_path)
        engine = FakeKnowledgeEngine(
            points_by_section={"1.1 数列极限": [_point_draft("数列极限的定义", page=2)]},
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        steps = [step for step, _ in engine.calls]
        assert not [step for step in steps if step.startswith("toc_")], "目录三条腿零发起"
        assert steps == ["points", "points", "chapter"], "先两节、后一章（`KA-45` 的两遍制）"
        assert len(_rows(session, node_kind="outline")) == 3, "在册目录行不增不减"
        assert (outcome.outline_rows, outcome.point_rows) == (0, 0)
        assert kp_batch_row(accepted, outcome).stage == "content"

    def test_the_sections_are_cut_from_the_registered_rows(self, session, tmp_path):
        """区间由在册行的锚点页与 `order_hint` 切（`KA-42` 同一条算式），不是另起一份目录。"""
        rows, accepted = self._registered(session, tmp_path)
        engine = FakeKnowledgeEngine()

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        assert [(s.title, s.page_start, s.page_end) for s in self._sections(engine)] == [
            ("1.1 数列极限", 2, 3),
            ("1.2 函数极限", 4, 6),
        ]
        assert [s.parent_id for s in self._sections(engine)] == [row.node_id for row in rows[1:]]

    def test_a_narrow_range_only_asks_about_the_sections_that_cross_it(self, session, tmp_path):
        """区间外的节整节不发起：一节一次百秒级调用，为零页的节付费等于把批次拖长一倍。"""
        self._registered(session, tmp_path)
        narrow = accept_kp_job(session, book_id="b05", stage="content", page_start=4, page_end=6)
        engine = FakeKnowledgeEngine()

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=narrow)

        assert [(s.title, s.page_start, s.page_end) for s in self._sections(engine)] == [
            ("1.2 函数极限", 4, 6)
        ]

    def test_the_content_stage_lands_no_third_layer_rows(self, session, tmp_path):
        """`KA-44`：一章节／一点的双层结构下，内容批次只写总结——草稿不再落行（第三层已不存在）。"""
        rows, accepted = self._registered(session, tmp_path)
        engine = FakeKnowledgeEngine(
            points_by_section={"1.1 数列极限": [_point_draft("数列极限的定义", page=2)]},
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert _rows(session, node_kind="point") == []
        assert (outcome.point_rows, outcome.unarchored, outcome.detached_to_pool) == (0, 0, 0)
        assert len(_rows(session, node_kind="outline")) == len(rows)

    # ------------------------------------------------------------------ 节级总结

    def test_the_section_summary_lands_on_the_outline_row_and_nothing_else_moves(self, session, tmp_path):
        rows, accepted = self._registered(session, tmp_path)
        before = {row.node_id: self._structure(row) for row in rows}
        engine = FakeKnowledgeEngine(
            points_by_section={"1.1 数列极限": [_point_draft("数列极限的定义", page=2)]},
            summaries_by_section={"1.1 数列极限": "这一节讲数列极限的定义与它的三条推论。"},
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        after = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert {node_id: self._structure(row) for node_id, row in after.items()} == before
        assert after[rows[1].node_id].summary == "这一节讲数列极限的定义与它的三条推论。"
        assert outcome.outline_summary_rows == 1
        assert kp_batch_row(accepted, outcome).result["outline_summary_rows"] == 1

    def test_a_confirmed_or_already_summarized_row_is_never_written(self, session, tmp_path):
        """写手的 `WHERE` 四条件：人已背书的行与已有总结的行零触碰——机器不与人工争同一列。"""
        rows, accepted = self._registered(session, tmp_path)
        endorsed = next(row for row in rows if row.name == "第 1 章 极限")
        KpRepository(session).set_confirmed(endorsed.node_id, confirmed=True)
        written = next(row for row in rows if row.name == "1.2 函数极限")
        written.summary = "人工已经写过的节级总结"
        session.commit()
        before = {row.node_id: _columns(row) for row in _rows(session, node_kind="outline")}

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                summaries_by_section={
                    "第 1 章 极限": "机器想给章补一份",
                    "1.1 数列极限": "这一节的总结",
                    "1.2 函数极限": "机器想覆盖人已写过的那份",
                }
            ),
            acceptance=accepted,
        )
        session.expire_all()

        after = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert _columns(after[endorsed.node_id]) == before[endorsed.node_id]
        assert _columns(after[written.node_id]) == before[written.node_id]
        assert after[rows[1].node_id].summary == "这一节的总结"
        assert outcome.outline_summary_rows == 1

    # ------------------------------------------------------------------ 作废面

    def test_the_supersede_face_only_covers_point_rows(self, session, tmp_path):
        """`KA-36` 按 `KA-43` 改判：内容批次作废的是同区间的旧**点**行，在册目录行不是过期读数。"""
        self._registered(session, tmp_path)
        stale = _draft(
            session,
            "afkp-old1",
            gen_status="candidate",
            node_kind="point",
            name="旧批的点",
            norm_key="旧批的点",
            anchors=[{"page": 2, "block_indexes": [0], "snippet": "回指正文"}],
        )
        session.commit()
        accepted = accept_kp_job(session, book_id="b05", stage="content")

        engine = FakeKnowledgeEngine(
            points_by_section={"1.1 数列极限": [_point_draft("新批的点", page=2)]},
        )
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert {row.gen_status for row in _rows(session, node_kind="outline")} == {"candidate"}
        assert session.get(AfKp, stale.node_id).gen_status == "rejected"
        assert outcome.superseded_rows == 1

    # ------------------------------------------------------------------ 受理门

    def test_a_content_batch_without_registered_rows_is_refused(self, session, tmp_path):
        """「有目录」是库侧前置：零枚在册目录行时点全进池（`KA-7`），人工拿到的是一份平铺的假产出。"""
        _scene(session, tmp_path, page_count=6)

        with pytest.raises(KpGateError) as caught:
            _accepted(session, stage="content")

        assert caught.value.status_code == 422
        assert "先解析目录" in caught.value.detail
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (0, 0)
        # 同一台书、同一枚零目录状态：另两值照发（`KA-43` 边界＝`full`/`outline` 逐字不动）
        assert _accepted(session, stage="outline").stage == "outline"
        assert _accepted(session).stage == "full"

    def test_rejected_outline_rows_do_not_satisfy_the_gate(self, session, tmp_path):
        """在册＝`candidate` 及以上：整批被作废过的目录行不算「有目录」。"""
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(),
            acceptance=_accepted(session, stage="outline"),
        )
        for row in _rows(session, node_kind="outline"):
            row.gen_status = "rejected"
        session.commit()

        with pytest.raises(KpGateError) as caught:
            _accepted(session, stage="content")

        assert caught.value.status_code == 422
        assert "先解析目录" in caught.value.detail

    # ------------------------------------------------------------------ 锚在目录页的行

    def _register(self, session: Session, rows: list[tuple[str, int, int]]) -> None:
        """按 `(名字, 锚页, 层级)` 直接在册若干行：走库里已有的目录行，不经目录腿。"""
        repo = KpRepository(session)
        for order, (name, page, depth) in enumerate(rows, start=1):
            repo.create(
                KpNodeRow(
                    node_id=f"afkp-reg{order}",
                    book_id="b05",
                    name=name,
                    norm_key=norm_key_for(name),
                    node_kind="outline",
                    job_id="job-a1",
                    depth=depth,
                    order_hint=order,
                    anchors=[
                        {
                            "kind": "page_block",
                            "ref_id": f"p{page:04d}",
                            "page": page,
                            "block_indexes": [0],
                            "snippet": name,
                        }
                    ],
                    gen_status="candidate",
                    gen_batch="afkp-registered00",
                    meta={"toc_role": "knowledge"},
                )
            )
        session.commit()

    def test_a_row_anchored_on_a_toc_page_is_skipped_and_named(self, session, tmp_path):
        """`KA-43`：锚仍在目录页的行整节跳过并点名——这一批没有核对腿，照发只会把目录行抄成知识点。

        目录页的认法用确定性条目腿（`toc_entries`）：一次模型调用都不发，`calls` 里只有真正读正文的节。
        """
        pages_dir = _scene(session, tmp_path, page_count=6)
        _write_page_artifacts(
            pages_dir, 1, [], body="第 1 章 极限 …… 1\n1.1 数列极限 …… 2\n1.2 函数极限 …… 4"
        )
        self._register(
            session,
            [("第 1 章 极限", 1, 1), ("1.1 数列极限", 1, 2), ("1.2 函数极限", 4, 2)],
        )
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine()

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        assert [section.title for section in self._sections(engine)] == ["1.2 函数极限"]
        assert outcome.unlocated_outline_entries == ("1.1 数列极限",)
        assert kp_batch_row(accepted, outcome).result["unlocated_outline_entries"] == ["1.1 数列极限"]




def _register_tree(session: Session, rows, *, narrative_refs=None, metas=None) -> None:
    """按（节点, 名字, 锚页, 层, 父）直接在册一棵双层目录树：章腿与重生成腿共用这台前提。

    走库里已有的目录行，不经目录腿：这一块守的是「拿到在册行之后」的行为，不是成行本身。
    """
    repo = KpRepository(session)
    for order, (node_id, name, page, depth, parent) in enumerate(rows, start=1):
        repo.create(
            KpNodeRow(
                node_id=node_id,
                book_id="b05",
                name=name,
                norm_key=norm_key_for(name),
                node_kind="outline",
                job_id="job-a1",
                parent_id=parent,
                depth=depth,
                order_hint=order,
                anchors=[
                    {
                        "kind": "page_block",
                        "ref_id": f"p{page:04d}",
                        "page": page,
                        "block_indexes": [0],
                        "snippet": name,
                    }
                ],
                narrative_ref=(narrative_refs or {}).get(node_id, ""),
                gen_status="candidate",
                gen_batch="afkp-registered00",
                meta=(metas or {}).get(node_id, {"toc_role": "knowledge"}),
            )
        )
    session.commit()


class TestChapterLeg:
    """`KA-45` 章腿：内容批次在节腿之后逐章一次调用，材料＝章首正文＋其下各知识点的节级总结。

    这一层守的事：章总结读得到节腿刚写下的那份总结（不是批次开头的空读数）、章与其首节同页时
    改用章标题页兜底、已有导读的章不被覆盖、失败章计数并点名而不拖累同批其他章、只目录批次
    一次章调用都不发起。
    """

    _LAYOUT = {
        1: [(1, "第 1 章 极限")],
        2: [(2, "1.1 数列极限")],
        5: [(2, "1.2 函数极限")],
    }
    _ROWS = [
        ("afkp-ch1", "第 1 章 极限", 1, 1, None),
        ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
        ("afkp-s12", "1.2 函数极限", 5, 2, "afkp-ch1"),
    ]

    def _scene(self, session, tmp_path, *, layout=None, page_count=8, rows=None, **kwargs):
        _scene(session, tmp_path, page_count=page_count, layout=layout or self._LAYOUT)
        _register_tree(session, rows or self._ROWS, **kwargs)
        return accept_kp_job(session, book_id="b05", stage="content")

    def _chapter_calls(self, engine):
        return [material for step, material in engine.calls if step == "chapter"]

    def test_the_chapter_material_is_the_intro_pages_and_the_child_summaries(self, session, tmp_path):
        accepted = self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            summaries_by_section={"1.1 数列极限": "节一总结", "1.2 函数极限": "节二总结"},
            chapters_by_title={"第 1 章 极限": ("第 1 章的章级总结", "第 1 章的导读正文")},
        )

        outcome = run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        (material,) = self._chapter_calls(engine)
        assert (material.title, material.page_start, material.page_end) == ("第 1 章 极限", 1, 8)
        assert [page.page_no for page in material.intro_pages] == [1], "章首正文＝首个子行起点页之前那一段"
        assert material.title_pages == (), "有章首正文就不再重复送章标题页"
        assert material.point_summaries == (
            ("1.1 数列极限", "节一总结"),
            ("1.2 函数极限", "节二总结"),
        ), "清单是节腿刚写下的那一份，不是批次开头的空读数"

        chapter = next(row for row in _rows(session, node_kind="outline") if row.node_id == "afkp-ch1")
        assert chapter.summary == "第 1 章的章级总结"
        assert chapter.narrative_ref == "afkp-ch1.md"
        assert _kp_dir(tmp_path).joinpath("afkp-ch1.md").read_text(encoding="utf-8") == "第 1 章的导读正文"
        assert (outcome.chapter_rows, outcome.chapter_summary_rows, outcome.chapter_narrative_rows) == (1, 1, 1)
        result = kp_batch_row(accepted, outcome).result
        assert (result["chapter_rows"], result["chapter_summary_rows"], result["chapter_narrative_rows"]) == (1, 1, 1)

    def test_a_chapter_that_starts_on_its_first_section_page_gets_the_title_page(self, session, tmp_path):
        layout = {2: [(1, "第 1 章 极限"), (2, "1.1 数列极限")], 5: [(2, "1.2 函数极限")]}
        accepted = self._scene(
            session,
            tmp_path,
            layout=layout,
            rows=[
                ("afkp-ch1", "第 1 章 极限", 2, 1, None),
                ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
                ("afkp-s12", "1.2 函数极限", 5, 2, "afkp-ch1"),
            ],
        )
        engine = FakeKnowledgeEngine()

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        (material,) = self._chapter_calls(engine)
        assert material.intro_pages == (), "章与其首节同页 ⇒ 没有单独的章首正文"
        assert [page.page_no for page in material.title_pages] == [2]

    def test_a_chapter_whose_children_have_no_summary_gets_an_empty_list(self, session, tmp_path):
        accepted = self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine()

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        (material,) = self._chapter_calls(engine)
        assert material.point_summaries == ()

    def test_a_failed_chapter_is_counted_and_named_and_the_others_proceed(self, session, tmp_path):
        layout = {
            1: [(1, "第 1 章 极限")],
            2: [(2, "1.1 数列极限")],
            4: [(2, "1.2 函数极限")],
            6: [(1, "第 2 章 导数")],
            7: [(2, "2.1 导数定义")],
        }
        accepted = self._scene(
            session,
            tmp_path,
            layout=layout,
            rows=[
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
                ("afkp-s12", "1.2 函数极限", 4, 2, "afkp-ch1"),
                ("afkp-ch2", "第 2 章 导数", 6, 1, None),
                ("afkp-s21", "2.1 导数定义", 7, 2, "afkp-ch2"),
            ],
        )
        engine = FakeKnowledgeEngine(
            summaries_by_section={"1.1 数列极限": "一节", "1.2 函数极限": "二节", "2.1 导数定义": "三节"},
            chapters_by_title={"第 1 章 极限": ("章一总结", "章一导读")},
            failing_chapters=("第 2 章 导数",),
        )

        outcome = run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert [material.title for material in self._chapter_calls(engine)] == ["第 1 章 极限", "第 2 章 导数"]
        assert (outcome.chapter_rows, outcome.chapter_summary_rows, outcome.chapter_narrative_rows) == (1, 1, 1)
        assert outcome.failed_chapters == 1
        detail = outcome.failed_chapter_details[0]
        assert detail["title"] == "第 2 章 导数" and detail["reason"]
        assert (detail["page_start"], detail["page_end"]) == (6, 8)
        result = kp_batch_row(accepted, outcome).result
        assert result["failed_chapters"] == 1 and result["failed_chapter_details"][0]["title"] == "第 2 章 导数"
        chapter2 = next(row for row in _rows(session, node_kind="outline") if row.node_id == "afkp-ch2")
        assert chapter2.summary == "" and chapter2.narrative_ref == ""

    def test_a_chapter_that_already_has_a_narrative_keeps_it(self, session, tmp_path):
        accepted = self._scene(session, tmp_path, narrative_refs={"afkp-ch1": "afkp-ch1.md"})
        _kp_dir(tmp_path).mkdir(parents=True, exist_ok=True)
        _kp_dir(tmp_path).joinpath("afkp-ch1.md").write_text("人写过的导读", encoding="utf-8")
        engine = FakeKnowledgeEngine(chapters_by_title={"第 1 章 极限": ("机器的新总结", "机器的新导读")})

        outcome = run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        chapter = next(row for row in _rows(session, node_kind="outline") if row.node_id == "afkp-ch1")
        assert chapter.narrative_ref == "afkp-ch1.md"
        assert _kp_dir(tmp_path).joinpath("afkp-ch1.md").read_text(encoding="utf-8") == "人写过的导读"
        assert chapter.summary == "机器的新总结", "导读不动不牵连总结：两张写手各守自己的列"
        assert outcome.chapter_narrative_rows == 0

    def test_the_outline_stage_never_calls_the_chapter_leg(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        engine = FakeKnowledgeEngine()

        run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=_accepted(session, stage="outline")
        )

        assert "chapter" not in [step for step, _ in engine.calls]


class TestRegeneratePointSummary:
    """`KA-45` 单点重生成：覆盖知识点行自己的 `summary`、`rev` 递增，取材走该节自己的树序区间。"""

    _LAYOUT = TestChapterLeg._LAYOUT
    _ROWS = TestChapterLeg._ROWS

    def _scene(self, session, tmp_path, **kwargs):
        _scene(session, tmp_path, page_count=8, layout=self._LAYOUT)
        _register_tree(session, self._ROWS, **kwargs)

    def test_the_regenerated_summary_overwrites_the_row_and_bumps_its_rev(self, session, tmp_path):
        self._scene(session, tmp_path)
        before = KpRepository(session).get("afkp-s11")
        assert before is not None
        rev0 = before.rev
        engine = FakeKnowledgeEngine(summaries_by_section={"1.1 数列极限": "重生成后的总结"})

        updated = regenerate_section_summary(session, data_root=tmp_path, engine=engine, node_id="afkp-s11")
        session.expire_all()

        assert updated.summary == "重生成后的总结"
        assert updated.rev == rev0 + 1, "屏上那一份确实变了：人工下次保存的 if_rev 按新序号走"
        (section,) = [payload for step, payload in engine.calls if step == "points"]
        assert (section.title, section.page_start, section.page_end) == ("1.1 数列极限", 2, 4), (
            "读的是这一节自己的树序区间（下一行开始页之前），不是锚点那一页"
        )
        assert [page.page_no for page in section.pages] == [2, 3, 4]

    def test_only_a_point_node_can_regenerate(self, session, tmp_path):
        metas = {"afkp-ex": {"toc_role": "reference", "toc_kind": "exercise"}}
        _scene(session, tmp_path, page_count=8, layout=self._LAYOUT)
        _register_tree(
            session, [*self._ROWS, ("afkp-ex", "习题一", 8, 2, "afkp-ch1")], metas=metas
        )
        engine = FakeKnowledgeEngine()

        for node_id in ("afkp-ch1", "afkp-ex"):
            with pytest.raises(KpGateError) as caught:
                regenerate_section_summary(session, data_root=tmp_path, engine=engine, node_id=node_id)
            assert caught.value.status_code == 422
            assert "只有知识点可以重生成总结" in caught.value.detail
        assert engine.calls == [], "类型不合在取材之前就拒：不为一份写不进去的总结付一次调用"

    def test_an_in_flight_batch_blocks_the_regenerate(self, session, tmp_path):
        self._scene(session, tmp_path)
        _draft(session, "afkp-inflight", node_kind="outline", depth=2, order_hint=99)
        session.commit()

        with pytest.raises(KpGateError) as caught:
            regenerate_section_summary(
                session, data_root=tmp_path, engine=FakeKnowledgeEngine(), node_id="afkp-s11"
            )

        assert caught.value.status_code == 409
        assert "在飞" in caught.value.detail

    def test_a_stale_if_rev_is_a_409_and_the_row_is_untouched(self, session, tmp_path):
        self._scene(session, tmp_path)
        row = KpRepository(session).get("afkp-s11")
        assert row is not None
        engine = FakeKnowledgeEngine(summaries_by_section={"1.1 数列极限": "不该落库的总结"})

        with pytest.raises(KpGateError) as caught:
            regenerate_section_summary(
                session, data_root=tmp_path, engine=engine, node_id="afkp-s11", if_rev=row.rev + 3
            )

        assert caught.value.status_code == 409
        assert engine.calls == [], "序号不对在取材之前就拒"
        session.expire_all()
        assert KpRepository(session).get("afkp-s11").summary == ""

    def test_a_model_failure_is_a_502_and_the_old_summary_stays(self, session, tmp_path):
        self._scene(session, tmp_path)
        row = KpRepository(session).get("afkp-s11")
        assert row is not None
        row.summary = "人写过的旧总结"
        session.commit()
        engine = FakeKnowledgeEngine(failing_sections=("1.1 数列极限",))

        with pytest.raises(KpGateError) as caught:
            regenerate_section_summary(session, data_root=tmp_path, engine=engine, node_id="afkp-s11")

        assert caught.value.status_code == 502
        session.expire_all()
        assert KpRepository(session).get("afkp-s11").summary == "人写过的旧总结"

    def test_an_empty_model_summary_is_refused_and_nothing_is_written(self, session, tmp_path):
        self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine()

        with pytest.raises(KpGateError) as caught:
            regenerate_section_summary(session, data_root=tmp_path, engine=engine, node_id="afkp-s11")

        assert caught.value.status_code == 422
        assert "没有给出总结" in caught.value.detail
        session.expire_all()
        assert KpRepository(session).get("afkp-s11").summary == ""

    def test_an_unknown_node_is_a_404(self, session, tmp_path):
        self._scene(session, tmp_path)

        with pytest.raises(KpGateError) as caught:
            regenerate_section_summary(
                session, data_root=tmp_path, engine=FakeKnowledgeEngine(), node_id="afkp-missing"
            )

        assert caught.value.status_code == 404
