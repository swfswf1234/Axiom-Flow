"""
模块职责：梳理通路的编排层守护——受理门三形与批次身份（W2）、两阶段执行体与锚点复核（W3）、
收尾与启动期归一（W4）、导出序（W5）、长文／删除通路（W6）与调用账接线（W7）。
设计关联（DesignRef）：docs/design/knowledge-analysis.md（KA-2、KA-4、KA-5、KA-6、KA-7、KA-9、
KA-10、KA-11、KA-13、KA-15、KA-18、KA-19、KA-20、KA-21、KA-22、KA-23、KA-24、KA-25、KA-26、
KA-27、KA-32、KA-33、KA-34、KA-35、KA-36、KA-37、KA-38、KA-39、KA-41、KA-42、KA-43、
KA-54、KA-55）
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
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import axiom_flow.orchestrator.knowledge as knowledge_module
from axiom_flow.db.llm_ledger import LlmCallIdentity, LlmCallLedger
from axiom_flow.db.models import OP_KIND_KP, AfBook, AfKp, AfOperation, Base
from axiom_flow.db.repositories import (
    KP_META_KEYS,
    KP_RESULT_KEYS,
    BookRepository,
    BookSyncRow,
    KpBatchRow,
    KpNodeRow,
    KpRepository,
    OperationRepository,
    PageRepository,
)
from axiom_flow.engines.base import (
    ChapterInput,
    ChapterReply,
    EngineError,
    ExerciseClassifyReply,
    ExerciseDraft,
    ExerciseMatchChoice,
    ExerciseReply,
    ExerciseVerifyReading,
    ExerciseVerifyReply,
    KnowledgeEngine,
    MemberPoints,
    OutlineEntry,
    PageBody,
    ReferenceNote,
    ReferencePage,
    SectionBlock,
    SectionInput,
    SectionPoints,
    TocOutlineRow,
    TocPagePick,
    TocPageReading,
    TocVerifyAnswer,
)
from axiom_flow.ingest import scan_transient_residue
from axiom_flow.normalize.toc import (
    CONTENT_INPUT_BUDGET_CHARS,
    NODE_TYPE_CHAPTER,
    NODE_TYPE_EXERCISE,
    NODE_TYPE_OTHER,
    NODE_TYPE_POINT,
    TOC_PAGE_SAMPLE_MAX_CHARS,
    TOC_PAGE_SCAN_LIMIT,
    node_type_of,
    node_types_of,
)
from axiom_flow.orchestrator.jobs import JobRunner, KpLedgerWiringError
from axiom_flow.orchestrator.knowledge import (
    CONTENT_LEGS_BY_NODE_TYPE,
    EXERCISE_CHUNK_TEXT_BUDGET,
    EXERCISE_SPLIT_MODEL_REF,
    DisplaySpan,
    EvidencePage,
    EvidenceSet,
    KpGateError,
    LedgeredKnowledgeEngine,
    OutlineReading,
    SectionSpan,
    SpanRow,
    _OutlineAssembler,
    _section_block_bodies,
    accept_kp_job,
    anchor_review_flags,
    book_stage_reading,
    book_toc_pages,
    build_evidence_set,
    build_outline,
    chapter_issues,
    collapse_outline_duplicates,
    content_legs_for,
    curation_result_for,
    display_spans_of,
    exercise_pack_status,
    exercise_pack_text,
    exercise_workspace,
    finalize_batch,
    kp_batch_row,
    list_narrative_orphans,
    norm_key_for,
    order_for_export,
    read_exercise_meta,
    regenerate_chapter_narrative,
    regenerate_section_summary,
    remove_node,
    run_knowledge_job,
    section_spans_of,
    write_exercise_indexes,
    write_exercise_meta,
    write_narrative,
)
from axiom_flow.schemas import Block, BlocksPage, BlockType, LastCurationResult
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
    roles: list[str] | None = None,
    title: str = "数学分析原理",
) -> None:
    """一台「已 ingest＋已生效」的书目：受理门只看这两件事，不看磁盘。

    `roles` 缺省＝教材（`KA-51` 的两值闭集里最常见的形状）；传 `[]` 即未声明种类。
    `title` 缺省取教材名——习题集用例按书名同族判据的需要各自换名（`KA-71`）。
    """
    BookRepository(session).upsert_many(
        [
            BookSyncRow(
                book_id=book_id,
                domain_id="math-advanced",
                course_id="math_analysis",
                course_name="数学分析",
                knowledge_id="kt-mathanalysis-3",
                title=title,
                part="教程",
                display_title="Rudin 数学分析原理",
                authors="Walter Rudin",
                file_path="books/b05.pdf",
                roles=["textbook"] if roles is None else list(roles),
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


class TestKindGate:
    """`KA-51`：梳理只对两类书成立——未声明种类当场拒，种类 × 阶段按闭集判，全部零写入。"""

    def test_undeclared_kind_is_422_with_zero_writes(self, session):
        _book(session, roles=[])

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05")

        assert caught.value.status_code == 422
        assert "未声明" in caught.value.detail
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (0, 0)

    @pytest.mark.parametrize("roles", [["slides"], ["notes", "unknown"]])
    def test_roles_outside_the_closed_set_are_undeclared(self, session, roles):
        """闭集外的角色不猜（ADR 0019 决定 1）：派生落空即拒，而不是折成教材。"""
        _book(session, roles=roles)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05")

        assert caught.value.status_code == 422
        assert "未声明" in caught.value.detail

    def test_mixed_roles_are_curated_as_textbook(self, session):
        """`KA-51`：同一本书「正文＋习题」按教材走——混合角色落 `textbook`。"""
        _book(session, roles=["exercises", "textbook"])

        accepted = accept_kp_job(session, book_id="b05")

        assert accepted.stage == "full"
        assert BookRepository(session).get("b05").doc_kind == "textbook"

    @pytest.mark.parametrize("stage", ["full", "content"])
    def test_exercise_book_rejects_the_content_stages(self, session, stage):
        """`KA-51`：习题集开 `outline`／`exercise` 两值，内容腿与全跑腿仍拒。

        `content` 仍先过 `KA-43` 的在册目录门（既有五道门排在种类门之前），故这里先铺一枚在册
        目录行，让组合门成为真正的拒绝理由。
        """
        _book(session, roles=["exercises"])
        _draft(
            session,
            "afkp-o1",
            node_kind="outline",
            gen_status="candidate",
            anchors=[{"page": 3, "block": 0}],
        )

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05", stage=stage)

        assert caught.value.status_code == 422
        assert "组合" in caught.value.detail
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (1, 0)

    def test_exercise_book_opens_the_outline_stage(self, session):
        """C14：习题集的目录腿只产结构行（切题的父行），故与 `exercise` 同开。"""
        _book(session, roles=["exercises"])

        assert accept_kp_job(session, book_id="b05", stage="outline").stage == "outline"

    @pytest.mark.parametrize("roles", [["textbook"], ["textbook", "solutions"]])
    @pytest.mark.parametrize("stage", ["full", "outline"])
    def test_textbook_opens_the_shipped_directory_stages(self, session, roles, stage):
        _book(session, roles=roles)

        assert accept_kp_job(session, book_id="b05", stage=stage).stage == stage

    def test_textbook_opens_content_when_the_outline_is_registered(self, session):
        """`KA-43` 的库侧前置仍先于种类组合判：有在册目录行才谈得上 `content`。"""
        _book(session, roles=["textbook"])
        _draft(
            session,
            "afkp-o1",
            node_kind="outline",
            gen_status="candidate",
            anchors=[{"page": 3, "block": 0}],
        )

        assert accept_kp_job(session, book_id="b05", stage="content").stage == "content"

    @pytest.mark.parametrize("roles", [["textbook"], ["exercises"]])
    def test_the_exercise_stage_ships_for_both_kinds_behind_the_outline_gate(self, session, roles):
        """`KA-53`：两类书的题腿都已放行，前提相同——有在册目录行（教材要习题段行当父，
        习题集要章／节行切区间）。两道拒绝都在受理面、零写入。"""
        _book(session, roles=roles)
        with pytest.raises(KpGateError) as no_outline:
            accept_kp_job(session, book_id="b05", stage="exercise")
        assert no_outline.value.status_code == 422
        assert "先解析目录" in no_outline.value.detail

        _draft(
            session,
            "afkp-o1",
            node_kind="outline",
            gen_status="candidate",
            anchors=[{"page": 3, "block": 0}],
        )
        assert accept_kp_job(session, book_id="b05", stage="exercise").stage == "exercise"
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (1, 0)

    def test_undeclared_gate_runs_after_the_five_existing_gates(self, session):
        """`KA-51` 的两道门排在既有五道之后：无生效版本与在飞批次仍先报，告警不抢先。"""
        _book(session, roles=[], active_job_id=None)
        with pytest.raises(KpGateError) as no_version:
            accept_kp_job(session, book_id="b05")
        assert "生效" in no_version.value.detail

        BookRepository(session).set_active_job("b05", "job-a1")
        _draft(session, "afkp-a1", gen_batch="afkp-ffffffffffff")
        session.commit()
        with pytest.raises(KpGateError) as in_flight:
            accept_kp_job(session, book_id="b05")
        assert in_flight.value.status_code == 409


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


def _write_page_blocks(
    pages_dir: Path, page_no: int, texts: list[str], *, types: list[str] | None = None
) -> None:
    """一页逐块写产物：习题腿要的是「每种块型各成一枚」的真实形状（页脚噪音也得能扔进去）。"""
    kinds = list(types or ["paragraph"] * len(texts))
    blocks = [
        {
            "type": kind,
            "bbox": [0, 40 * index, 800, 40 * index + 36],
            "index": index,
            "order": index,
            "text": text,
        }
        for index, (kind, text) in enumerate(zip(kinds, texts, strict=True))
    ]
    payload = {"page": page_no, "image_size": [1000, 1400], "source": "mineru", "blocks": blocks}
    (pages_dir / f"p{page_no:04d}.blocks.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    (pages_dir / f"p{page_no:04d}.md").write_text("\n".join(texts), encoding="utf-8")


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
    roles: list[str] | None = None,
    title: str = "数学分析原理",
) -> Path:
    """一台「已生效且逐页有产物」的书：页行 + 版本目录页产物一起造，磁盘与 DB 不自洽由参数点名。"""
    _book(session, book_id, active_job_id=job_id, page_count=page_count, roles=roles, title=title)
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
        on_chapter=None,
        model_name: str = "",
        exercise_by_title: dict[str, list[ExerciseDraft]] | None = None,
        failing_exercise: tuple[str, ...] = (),
        on_exercise=None,
        exercise_match_choice: ExerciseMatchChoice | None = None,
        failing_exercise_match: bool = False,
        on_exercise_match=None,
        exercise_classify_reply: ExerciseClassifyReply | None = None,
        failing_exercise_classify: bool = False,
        on_exercise_classify=None,
        exercise_verify_reply: ExerciseVerifyReply | None = None,
        failing_exercise_verify: bool = False,
        on_exercise_verify=None,
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
        self.on_chapter = on_chapter
        self.model_name = model_name
        self.exercise_by_title = dict(exercise_by_title or {})
        self.failing_exercise = set(failing_exercise)
        self.on_exercise = on_exercise
        self.exercise_match_choice = exercise_match_choice
        self.failing_exercise_match = failing_exercise_match
        self.on_exercise_match = on_exercise_match
        self.exercise_classify_reply = exercise_classify_reply
        self.failing_exercise_classify = failing_exercise_classify
        self.on_exercise_classify = on_exercise_classify
        self.exercise_verify_reply = exercise_verify_reply
        self.failing_exercise_verify = failing_exercise_verify
        self.on_exercise_verify = on_exercise_verify
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
        合批调用（`KA-64`）按成员逐点作答：成员按自己的标题取草稿与总结，`failing_sections`
        点到的成员整员缺席——与真引擎「回了但漏掉那一员」同形，其余成员照收。
        """
        self.calls.append(("points", section))
        if section.members:
            if self.on_points is not None:
                produced = self.on_points(section)
                if isinstance(produced, SectionPoints):
                    return produced
            return SectionPoints(
                [],
                "",
                tuple(
                    MemberPoints(
                        node_id=member.node_id,
                        drafts=list(self.points_by_section.get(member.title, [])),
                        summary=self.summaries_by_section.get(member.title, ""),
                    )
                    for member in section.members
                    if member.title not in self.failing_sections
                ),
            )
        if section.title in self.failing_sections:
            raise EngineError(f"模型服务中断：{section.title}")
        produced = self.on_points(section) if self.on_points is not None else self.points_by_section.get(
            section.title, []
        )
        if isinstance(produced, SectionPoints):
            return produced
        return SectionPoints(list(produced), self.summaries_by_section.get(section.title, ""))

    def chapter(self, material) -> ChapterReply:
        """章腿（`KA-45`）：缺省回两段空串——不关心章总结的用例不必为一枚不看的键改写法。

        `on_chapter` 是状态化替身（PLAN-033 W6 的重试链）：按调用次序给不同回复，重试才能被看见。
        """
        self.calls.append(("chapter", material))
        if material.title in self.failing_chapters:
            raise EngineError(f"模型服务中断：{material.title}")
        if self.on_chapter is not None:
            return self.on_chapter(material)
        summary, narrative = self.chapters_by_title.get(material.title, ("", ""))
        return ChapterReply(summary=summary, narrative=narrative)

    def current_model_name(self) -> str:
        return self.model_name

    def exercise(self, section) -> ExerciseReply:
        """习题集一节的桩（`KA-53`）：缺省回空表——不关心题面／解答内容的用例不必逐题给草稿。"""
        self.calls.append(("exercise", section))
        if section.title in self.failing_exercise:
            raise EngineError(f"模型服务中断：{section.title}")
        if self.on_exercise is not None:
            return self.on_exercise(section)
        return ExerciseReply(tuple(self.exercise_by_title.get(section.title, [])))

    def exercise_match(self, request) -> ExerciseMatchChoice:
        """习题集目录匹配的桩（REQ-161 W4）：缺省回「没有教材对得上」。"""
        self.calls.append(("exercise_match", request))
        if self.failing_exercise_match:
            raise EngineError("模型服务中断：exercise_match")
        if self.on_exercise_match is not None:
            return self.on_exercise_match(request)
        return self.exercise_match_choice or ExerciseMatchChoice()

    def exercise_classify(self, request) -> ExerciseClassifyReply | None:
        """逐题读数的桩（REQ-161 W5）：缺省回 `None`＝引擎没有这条腿，旧用例的题行形状不变。"""
        self.calls.append(("exercise_classify", request))
        if self.failing_exercise_classify:
            raise EngineError("模型服务中断：exercise_classify")
        if self.on_exercise_classify is not None:
            return self.on_exercise_classify(request)
        return self.exercise_classify_reply

    def exercise_verify(self, request) -> ExerciseVerifyReply | None:
        """配对校验的桩（REQ-170 第三步）：缺省回 `None`＝引擎没有这条腿，题行不写 `verify_*` 键。"""
        self.calls.append(("exercise_verify", request))
        if self.failing_exercise_verify:
            raise EngineError("模型服务中断：exercise_verify")
        if self.on_exercise_verify is not None:
            return self.on_exercise_verify(request)
        return self.exercise_verify_reply


def _accepted(session: Session, book_id: str = "b05", **kwargs):
    return accept_kp_job(session, book_id=book_id, **kwargs)


def _rows(session: Session, *, node_kind: str | None = None, book_id: str = "b05") -> list[AfKp]:
    return KpRepository(session).list_for_book(book_id, node_kind=node_kind)


def _confirmed_rows(session: Session) -> list[AfKp]:
    return list(session.scalars(select(AfKp).where(AfKp.confirmed == 1)))


def _curation_result(session: Session, book_id: str = "b05") -> str:
    """书目的结局位（`KA-54`）：先过期会话缓存，读到的才是这一轮写下的读数。"""
    session.expire_all()
    book = session.get(AfBook, book_id)
    assert book is not None
    return book.last_curation_result


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
    桩引擎不交判页读数，所以本类走的正是这条回落通路——未定位的那些不成行（目录页不当正文锚，
    `KA-33` 改判）并把条目名送进批次流水点名。产物文件与 `af_pages` 的任何列都不经这条腿
    （读数只补「页与块」两个坐标）。
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
        ], "未定位条目不成行（KA-33 改判）：目录页不当正文锚，名字只在账面点名"
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
        ], "未定位条目不成行（KA-33 改判），名字只进账面点名"
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
        assert len(KP_RESULT_KEYS) == 42, "四十二键（`KA-27`／`KA-53`／PLAN-031／`KA-62`／`KA-63`~`KA-66`）：新键不进闭集就是自由键"

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
        assert len(outline.nodes) == 5, "偏移标定成功的书里查不中的行不成行（KA-33 改判）"
        assert outline.unlocated == ("附录 习题解答",), "丢行要在账面点名，不许静默消失"
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
        assert len(outline.nodes) == 5, "偏移标定成功的书里查不中的行不成行（KA-33 改判）"

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
        assert len(outline.nodes) == 5, "偏移标定成功的书里查不中的行不成行（KA-33 改判）"

    def test_a_third_level_row_is_counted_and_never_becomes_a_node(self, session, tmp_path):
        """三级细目折入（`KA-62`）：`level=3` 的行不成节点，只折进所属二级并把枚数记在账上。"""
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
        assert outline.folded_sub_items == 1
        assert outline.dropped_depth == 0, "三级不是越界：越界的是四级及更深"

    def test_numbered_sub_items_under_a_section_fold_into_its_display_span(self, session, tmp_path):
        """b12 形（`KA-62`）：`§` 节后的编号细目不单独成行，二级节的内容段因此盖住细目页。"""
        pages_dir = _scene(session, tmp_path, page_count=8)
        bodies = {
            1: "第 1 章 极限 …… 1\n§ 1. 极限的定义 …… 2\n1. 数列极限 …… 3\n2. 函数极限 …… 4\n"
            "§ 2. 连续性 …… 5\n1. 连续的定义 …… 6",
            2: "第 1 章 极限\n正文开始。",
            3: "§ 1. 极限的定义\n正文。",
            4: "1. 数列极限\n正文。",
            5: "2. 函数极限\n正文。",
            6: "§ 2. 连续性\n正文。",
            7: "1. 连续的定义\n正文。",
            8: "正文收尾。",
        }
        for page_no, body in bodies.items():
            _write_page_artifacts(pages_dir, page_no, [], body=body)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=1, name="第 1 章 极限"),
                OutlineEntry(level=2, printed_page=2, name="§ 1. 极限的定义"),
                OutlineEntry(level=2, printed_page=3, name="1. 数列极限"),
                OutlineEntry(level=2, printed_page=4, name="2. 函数极限"),
                OutlineEntry(level=2, printed_page=5, name="§ 2. 连续性"),
                OutlineEntry(level=2, printed_page=6, name="1. 连续的定义"),
            ],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [(node.row.name, node.row.depth) for node in outline.nodes] == [
            ("第 1 章 极限", 1),
            ("§ 1. 极限的定义", 2),
            ("§ 2. 连续性", 2),
        ], "三枚编号细目不成行（没有三级概念）"
        assert outline.folded_sub_items == 3
        spans = display_spans_of(
            [
                SpanRow(
                    node_id=node.row.node_id,
                    name=node.row.name,
                    order_hint=node.row.order_hint,
                    page=node.row.anchors[0]["page"],
                    depth=node.row.depth,
                    toc_role=(node.row.meta or {}).get("toc_role", ""),
                    toc_kind=(node.row.meta or {}).get("toc_kind", ""),
                    parent_id=node.row.parent_id,
                )
                for node in outline.nodes
            ],
            last_page=8,
        )
        names = {node.row.node_id: node.row.name for node in outline.nodes}
        assert [(names[span.node_id], span.page_start, span.page_end) for span in spans] == [
            ("第 1 章 极限", 2, 2),
            ("§ 1. 极限的定义", 3, 5),
            ("§ 2. 连续性", 6, 8),
        ], "§ 1 的显示段含 p4／p5 两枚细目页——二级内容显示完全"

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


class TestOutlineRowDedup:
    """目录行去重（D-11／D-12）：同父同名只留书序第一枚；书级附件名全书唯一；习题段不受全书唯一约束。

    去重发生在**成行**这一步，不是批次收尾：行的身份、`order_hint` 与父位都在这里定下，晚一步折叠
    就会在屏上留下一枚已经被算进树序的重影。键只用 `norm_key` 那一个口径——全角、空白、大小写
    折出来的差异不该造出第二行。
    """

    @staticmethod
    def _assembler() -> _OutlineAssembler:
        return _OutlineAssembler(book_id="b05", job_id="job-a1", gen_batch="gb-1")

    @staticmethod
    def _reading(
        page_no: int, level: int, text: str, *, role: str = "knowledge", kind: str = ""
    ) -> OutlineReading:
        return OutlineReading(
            page_no=page_no,
            block_index=0,
            level=level,
            text=text,
            model_ref="llm:toc",
            role=role,
            kind=kind,
        )

    def test_a_repeated_name_under_one_parent_is_registered_once(self):
        """同父同名的第二枚不进行：键走 `norm_key`，空白差异不算另一行。"""
        assembler = self._assembler()
        assembler.add(self._reading(2, 1, "第1章 集合论"))
        kept = assembler.add(self._reading(3, 2, "1.1 极限"))
        assert assembler.add(self._reading(3, 2, "1.1  极限")) is None

        assert kept is not None
        assert [node.row.name for node in assembler.nodes] == ["第1章 集合论", "1.1 极限"]
        assert assembler.dropped_duplicates == 1

    def test_the_same_section_name_under_two_chapters_stays_twice(self):
        """跨章同名保留：每章的「习题课」是各自章的读数，父位不同就不是重影。"""
        assembler = self._assembler()
        for page_no, name in ((2, "第1章 集合论"), (3, "习题课"), (4, "第2章 实数与极限"), (5, "习题课")):
            assembler.add(self._reading(page_no, 1 if "章" in name else 2, name))

        assert [node.row.name for node in assembler.nodes] == [
            "第1章 集合论",
            "习题课",
            "第2章 实数与极限",
            "习题课",
        ]
        assert assembler.dropped_duplicates == 0

    def test_a_book_level_reference_name_is_unique_wherever_it_hangs(self):
        """书级附件的键不带父位：目录行与附件页行同名时只留先到的一枚（D-12）。"""
        assembler = self._assembler()
        assembler.add(self._reading(2, 1, "第1章 集合论"))
        assembler.add(self._reading(3, 2, "前言", role="reference", kind="front_matter"))
        assert assembler.add(self._reading(4, 1, "前言", role="reference", kind="front_matter")) is None

        assert [node.row.name for node in assembler.nodes] == ["第1章 集合论", "前言"]

    def test_exercise_rows_are_not_book_unique(self):
        """习题段是章的子行（D-12 例外）：跨章同名照旧各留一行。"""
        assembler = self._assembler()
        assembler.add(self._reading(2, 1, "第1章 集合论"))
        assembler.add(self._reading(3, 2, "习题", role="reference", kind="exercise"))
        assembler.add(self._reading(4, 1, "第2章 实数与极限"))
        assembler.add(self._reading(5, 2, "习题", role="reference", kind="exercise"))

        assert len(assembler.nodes) == 4
        assert assembler.dropped_duplicates == 0

    def test_an_optional_marker_is_stripped_from_the_name_and_recorded(self):
        """真机 b18：印刷的选学星号被排版器抄成 `\\*`，它不属于书写的名字——成行时卸掉，语义进 meta。"""
        assembler = self._assembler()
        marked = assembler.add(self._reading(42, 2, r"\*2.5 集合的覆盖性质"))
        spaced = assembler.add(self._reading(91, 1, r"\* 第 5 章 作为微分方程解的初等函数"))

        assert marked is not None and marked.row.name == "2.5 集合的覆盖性质"
        assert marked.row.meta is not None and marked.row.meta.get("optional") is True
        assert spaced is not None and spaced.row.name == "第 5 章 作为微分方程解的初等函数"
        assert spaced.row.meta is not None and spaced.row.meta.get("optional") is True

    def test_a_markerless_name_and_a_marked_twin_fold_together(self):
        """卸标记发生在算 `norm_key` 之前：同一节的两份读数不再因为一枚排版星号各留一行。"""
        assembler = self._assembler()
        assembler.add(self._reading(42, 2, r"\*2.5 集合的覆盖性质"))
        assert assembler.add(self._reading(42, 2, "2.5 集合的覆盖性质")) is None

        assert [node.row.name for node in assembler.nodes] == ["2.5 集合的覆盖性质"]

    def test_the_batch_ledger_counts_the_folded_rows(self, session, tmp_path):
        """去重枚数进批次流水（第三十五键）：屏上少一行，账上必须看得见少的是哪一行。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
            ],
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        assert outcome.outline_rows == 3
        assert outcome.duplicate_outline_rows == 1
        assert kp_batch_row(accepted, outcome).result["duplicate_outline_rows"] == 1
        assert "duplicate_outline_rows" in KP_RESULT_KEYS, "新键不进闭集就是自由键（`KA-27`）"
        assert len(KP_RESULT_KEYS) == 42, "四十二键（`KA-27`／PLAN-031／`KA-62`／`KA-63`~`KA-66`）：去重与折入读数也是账面"

    def test_a_landed_row_the_final_pass_folds_away_is_removed(self, session, tmp_path):
        """早落与定稿的折叠面不同一时，定稿不认的那枚必删：批次翻面是无条件的 draft→candidate。

        `KA-33` 改判后，这枚行在定稿的成行处就折掉了（偏移标定成功、查不中者不成行），不再走到
        重影折叠那一步——删行判据不变，账面读数从 `duplicate_outline_rows` 变成 `unlocated`。
        """
        _marked_toc_scene(session, tmp_path)
        accepted = _accepted(session)
        engine = SequentialKnowledgeEngine(
            {
                1: TocPageReading(
                    is_toc=True,
                    entries=(
                        OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                        OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                        OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
                        OutlineEntry(level=1, printed_page=None, name="前言"),
                    ),
                ),
                2: TocPageReading(is_toc=False),
            },
            on_toc_pages=lambda samples: TocPagePick(
                reference_pages=(ReferencePage(page_no=2, name="前言", kind="front_matter"),)
            ),
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        rows = _rows(session, node_kind="outline")
        assert len(rows) == 4, [row.name for row in rows]
        assert [row.name for row in rows].count("前言") == 1, "「前言」只留附件页那一行，早落的目录行必须删掉"
        assert (rows[0].name, rows[0].anchors[0]["page"]) == ("前言", 2)
        assert outcome.duplicate_outline_rows == 0, "未定位行在成行前已折掉，不走重影折叠"
        assert "前言" in outcome.unlocated_outline_entries, "屏上少的行在账上点名"


class TestCollapseOutlineDuplicates:
    """D-13 读面折叠：同键先留书序第一枚，但**作废行不压过未作废行**；被折掉的 id 重挂到留下的那枚。

    真机 b18：存量批次里每章/节都有一对「rejected 旧读数 ＋ candidate 新读数」，旧的排在书序前面。
    只认「书序第一枚」会把保留下来的章换成作废行，而孩子仍指向被折掉的候选行——整棵子树在屏上消失。
    """

    @staticmethod
    def _row(
        node_id: str,
        name: str,
        *,
        parent: str | None = None,
        kind: str = "outline",
        status: str = "candidate",
        depth: int = 1,
        order: int = 1,
    ) -> AfKp:
        now = datetime(2026, 11, 9, tzinfo=UTC)
        meta = {"toc_role": "knowledge"}
        return AfKp(
            node_id=node_id,
            book_id="b18",
            job_id="job-a1",
            node_kind=kind,
            parent_id=parent,
            depth=depth if kind == "outline" else None,
            order_hint=order,
            name=name,
            norm_key=norm_key_for(name),
            role="",
            type="",
            summary="",
            links=None,
            anchors=[{"kind": "page_block", "page": order, "block_indexes": [0]}],
            narrative_ref="",
            gen_status=status,
            confirmed=0,
            gen_batch="gb-1",
            model_ref="llm:test",
            rev=0,
            meta=meta,
            created_at=now,
            updated_at=now,
        )

    def test_the_live_twin_is_kept_over_the_rejected_one_and_the_children_follow(self):
        """b18 第1章：旧读数（rejected）排在书序前面时，留下的必须是 candidate，节行跟着它。"""
        rows = [
            self._row("ch-old", "第1章 分析的工具", status="rejected", order=1),
            self._row("s-old", "1.1 完备性公理", parent="ch-old", status="rejected", depth=2, order=2),
            self._row("ch-new", "第1章 分析的工具", status="candidate", order=3),
            self._row("s-new", "1.1 完备性公理", parent="ch-new", status="candidate", depth=2, order=4),
        ]

        kept = collapse_outline_duplicates(order_for_export(rows))

        assert [row.node_id for row in kept] == ["ch-new", "s-new"]
        assert kept[1].parent_id == "ch-new"

    def test_a_child_of_the_dropped_twin_is_remapped_to_the_kept_one(self):
        """被折掉那一行的孩子不能指向一个已不在屏上的父：父位改指留下的那枚。"""
        rows = [
            self._row("ch-old", "第1章 分析的工具", status="rejected", order=1),
            self._row("orphan", "1.2 整数与有理数", parent="ch-old", status="rejected", depth=2, order=2),
            self._row("ch-new", "第1章 分析的工具", status="candidate", order=3),
        ]

        kept = collapse_outline_duplicates(order_for_export(rows))

        assert {row.node_id: row.parent_id for row in kept} == {"ch-new": None, "orphan": "ch-new"}

    def test_two_live_twins_still_keep_the_first_in_book_order(self):
        """两枚都未作废时仍按书序留第一枚：这一条是原口径，改判只加了「作废行让位」。"""
        rows = [
            self._row("a", "第1章 分析的工具", status="candidate", order=1),
            self._row("b", "第1章 分析的工具", status="candidate", order=2),
            self._row("child", "1.1 完备性公理", parent="b", status="candidate", depth=2, order=3),
        ]

        kept = collapse_outline_duplicates(order_for_export(rows))

        assert [(row.node_id, row.parent_id) for row in kept] == [("a", None), ("child", "a")]

    def test_point_rows_follow_the_surviving_outline_parent(self):
        """点行不参与目录去重，但它的父位同样要改指留下的那枚目录行（内容批次的点在屏上不能悬空）。"""
        rows = [
            self._row("ch-old", "第1章 分析的工具", status="rejected", order=1),
            self._row("ch-new", "第1章 分析的工具", status="candidate", order=2),
            self._row("p1", "极限的定义", parent="ch-old", kind="point", order=3),
        ]

        kept = collapse_outline_duplicates(order_for_export(rows))

        assert [(row.node_id, row.parent_id) for row in kept] == [
            ("ch-new", None),
            ("p1", "ch-new"),
        ]


class StreamingKnowledgeEngine(FakeKnowledgeEngine):
    """整段抄录通路的桩（`KA-57`）：在逐页腿之上再加 `toc_outline` 一条腿。

    `toc_outline` 的存在本身就是通路分岔口（`build_outline` 按 `getattr` 探测），所以这条腿不能挂到
    基桩上——那会让所有既有用例改走新通路。
    """

    def __init__(self, *, corrections=None, failing_outline: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.corrections = list(corrections or [])
        self.failing_outline = failing_outline

    def toc_outline(self, rows):
        self.calls.append(("toc_outline", tuple((row.index, row.name) for row in rows)))
        if self.failing_outline:
            raise EngineError("模型服务中断：toc_outline")
        return list(self.corrections)


class SequentialKnowledgeEngine(FakeKnowledgeEngine):
    """逐页判页通路的桩（P-2／P-3）：在基桩之上再加 `toc_page` 一条腿。

    这条腿的存在本身就是通路分岔口（`build_outline` 按属性探测），所以不能挂到基桩上——
    那会让所有既有用例改走新通路。
    """

    def __init__(self, readings=None, *, failing_pages=(), **kwargs) -> None:
        super().__init__(**kwargs)
        self.readings = dict(readings or {})
        self.failing_pages = set(failing_pages)
        self.page_texts: dict[int, str] = {}

    def toc_page(self, page_no, text):
        self.calls.append(("toc_page", page_no))
        self.page_texts[page_no] = text
        if page_no in self.failing_pages:
            raise EngineError(f"模型服务中断：p{page_no}")
        return self.readings.get(page_no, TocPageReading())


def _marked_toc_scene(
    session: Session, tmp_path: Path, *, book_id: str = "b05", job_id: str = "job-a1"
) -> Path:
    """带「目录」标记的两页目录＋四页正文（P-1~P-3）：绝对页＝印刷页＋2。"""
    _book(session, book_id, active_job_id=job_id, page_count=6)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    bodies = {
        1: "目录\n第1章 集合论 …… 1\n序的关系与集合的势 …… 2",
        2: "目录（续）\n第2章 实数与极限 …… 3",
        3: "第1章 集合论\n正文开始。",
        4: "序的关系与集合的势\n正文继续。",
        5: "第2章 实数与极限\n正文又起。",
        6: "无关正文。",
    }
    repo = PageRepository(session)
    for page_no in range(1, 7):
        _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


class TestSequentialTocPages:
    """P-1~P-5 逐页判页腿：标记页起逐页整页判，判到第一张非目录页为止，条目与判定同一次读数。"""

    @staticmethod
    def _marked() -> tuple[TocPageReading, ...]:
        return (
            TocPageReading(
                is_toc=True,
                entries=(
                    OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                    OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                ),
            ),
            TocPageReading(
                is_toc=True, entries=(OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),)
            ),
        )

    def test_the_range_runs_page_by_page_until_the_first_non_toc_page(self, session, tmp_path):
        _marked_toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        first, second = self._marked()
        engine = SequentialKnowledgeEngine({1: first, 2: second})

        outline = build_outline(accepted, evidence, engine)

        assert [step for step, _ in engine.calls] == ["toc_page", "toc_page", "toc_page", "toc_pages"]
        assert [page for step, page in engine.calls if step == "toc_page"] == [1, 2, 3], "p3 判成非目录页即收住"
        assert outline.toc_pages == (1, 2)
        assert outline.judged_pages == (1, 2, 3)
        assert outline.fallback_pages == ()
        assert [(node.row.name, node.row.depth, node.row.model_ref) for node in outline.nodes] == [
            ("第1章 集合论", 1, "llm:toc"),
            ("序的关系与集合的势", 2, "llm:toc"),
            ("第2章 实数与极限", 1, "llm:toc"),
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [3, 4, 5]
        assert "toc_entries" not in [step for step, _ in engine.calls], "判定与条目同一次读数，不再逐页抄"

    def test_each_judged_page_carries_its_whole_text_with_block_types(self, session, tmp_path):
        """P-2：整页不截断，块类型随行——判页看的是整页形状，不是 400 字样本。"""
        _marked_toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = SequentialKnowledgeEngine({1: self._marked()[0]})

        build_outline(accepted, evidence, engine)

        assert engine.page_texts[1].startswith("[paragraph] ")
        assert "目录" in engine.page_texts[1]
        assert "序的关系与集合的势 …… 2" in engine.page_texts[1]

    def test_the_marker_scan_falls_forward_when_the_first_ten_pages_have_none(self, session, tmp_path):
        """P-1：前 10 页没有「目录」标记时按页序向后递推，p11 的标记才起头。"""
        _book(session, page_count=12)
        pages_dir = _pages_dir(tmp_path)
        bodies = {page_no: "正文一。" for page_no in range(1, 11)}
        bodies[11] = "目录\n第1章 集合论 …… 1"
        bodies[12] = "第1章 集合论\n正文开始。"
        repo = PageRepository(session)
        for page_no in range(1, 13):
            _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
            repo.upsert("b05", "job-a1", page_no, status="parsed", source="mineru", error="")
        session.commit()
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = SequentialKnowledgeEngine(
            {11: TocPageReading(is_toc=True, entries=(OutlineEntry(1, 1, "第1章 集合论"),))}
        )

        outline = build_outline(accepted, evidence, engine)

        assert outline.toc_pages == (11,)
        assert outline.judged_pages == (11, 12)
        assert [node.row.name for node in outline.nodes] == ["第1章 集合论"]

    def test_without_any_marker_the_page_leg_is_never_called(self, session, tmp_path):
        """P-4：全书没有标记 ⇒ 逐页腿零调用，整本条目腿接手，逐页实判面为空。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = SequentialKnowledgeEngine({1: TocPageReading(is_toc=True)})

        outline = build_outline(accepted, evidence, engine)

        assert "toc_page" not in [step for step, _ in engine.calls]
        assert outline.toc_pages == (1,), "整本条目腿从页 1 抄出条目"
        assert outline.judged_pages == ()

    def test_a_single_page_failure_falls_back_for_that_page_and_the_walk_goes_on(self, session, tmp_path):
        """P-5：坏一页只坏那一页——该页退确定性条目腿，下一页照判并抄行。"""
        _marked_toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = SequentialKnowledgeEngine({2: self._marked()[1]}, failing_pages={1})

        outline = build_outline(accepted, evidence, engine)

        assert [page for step, page in engine.calls if step == "toc_page"] == [1, 2, 3]
        assert outline.toc_pages == (1, 2), "失败页仍在目录区间里，不能留在正文面当回声"
        assert outline.fallback_pages == (1,)
        assert ("第2章 实数与极限", "llm:toc") in [
            (node.row.name, node.row.model_ref) for node in outline.nodes
        ]

    def test_two_failures_in_a_row_stop_the_walk_and_the_book_leg_takes_over(self, session, tmp_path):
        """P-5 的边界：连续两页失败＝这条腿不可用，不再把整本正文逐页当目录问。"""
        _marked_toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = SequentialKnowledgeEngine(failing_pages={1, 2})

        outline = build_outline(accepted, evidence, engine)

        assert [page for step, page in engine.calls if step == "toc_page"] == [1, 2]
        assert outline.judged_pages == (1, 2)
        assert len(outline.nodes) >= 1, "整本条目腿接手，目录零行不发生"


class TestStreamedToc:
    """`KA-57`：引擎持 `toc_outline` 腿时走整段抄录——页码只由原文给，逐页整理腿一次不发起。"""

    def test_the_pages_are_copied_verbatim_and_the_per_page_leg_is_not_used(self, session, tmp_path):
        """整段抄录的读数与条目腿逐字一致，但调用面只有判页与校订：`toc_entries` 不在通路上。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = StreamingKnowledgeEngine(on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)))

        outline = build_outline(accepted, evidence, engine)

        assert [(node.row.name, node.row.depth, node.row.model_ref) for node in outline.nodes] == [
            ("第1章 集合论", 1, "rule:toc"),
            ("序的关系与集合的势", 2, "rule:toc"),
            ("第2章 实数与极限", 1, "rule:toc"),
        ], "未定位的附件行不成行（KA-33 改判），名字仍进账面"
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4]
        assert "toc_entries" not in {step for step, _ in engine.calls}
        assert outline.toc_pages == (1,)
        assert outline.unlocated == ("附录 习题解答",)

    def test_the_toc_range_expands_past_the_sampled_window(self, session, tmp_path):
        """判页范围连续扩展（`KA-57`）：采样只认到 p2，p3 由外探一轮补进整段。"""
        pages_dir = _scene(session, tmp_path, page_count=15)
        bodies = {
            1: "第1章 集合论 …… 1\n序的关系与集合的势 …… 2",
            2: "第2章 实数与极限 …… 3\n第3章 连续 …… 4",
            3: "第4章 微分 …… 5",
            4: "第1章 集合论\n正文开始。",
            5: "序的关系与集合的势\n正文继续。",
            6: "第2章 实数与极限\n正文又起。",
            7: "第3章 连续\n正文再续。",
            8: "第4章 微分\n正文收尾。",
        }
        for page_no, body in bodies.items():
            _write_page_artifacts(pages_dir, page_no, [], body=body)
        rounds: list[tuple[int, ...]] = []

        def pick(samples):
            rounds.append(tuple(sample.page_no for sample in samples))
            if len(rounds) == 1:
                return TocPagePick(toc_pages=(1, 2))
            if len(rounds) == 2:
                return TocPagePick(toc_pages=(3,))
            return TocPagePick()

        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted, evidence, StreamingKnowledgeEngine(on_toc_pages=pick)
        )

        assert rounds[1] == (3, 4, 5, 6, 7), "外探只问已知区间两侧各五页"
        assert rounds[2] == (4, 5, 6, 7, 8)
        assert outline.toc_pages == (1, 2, 3)
        assert [node.row.name for node in outline.nodes] == [
            "第1章 集合论",
            "序的关系与集合的势",
            "第2章 实数与极限",
            "第3章 连续",
            "第4章 微分",
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [4, 5, 6, 7, 8]

    def test_an_empty_sample_probes_the_front_window_for_the_toc(self, session, tmp_path):
        """首尾采样一条目录页都没有时，从前段窗口找起头（`KA-57`）——目录可以起在采样之后。"""
        pages_dir = _scene(session, tmp_path, page_count=25)
        _write_page_artifacts(pages_dir, 1, [], body="第1章 集合论\n正文开始。")
        _write_page_artifacts(pages_dir, 2, [], body="第2章 实数与极限\n正文继续。")
        _write_page_artifacts(pages_dir, 12, [], body="第1章 集合论 …… 1\n第2章 实数与极限 …… 2")
        rounds: list[tuple[int, ...]] = []

        def pick(samples):
            rounds.append(tuple(sample.page_no for sample in samples))
            if len(rounds) == 2:
                return TocPagePick(toc_pages=(12,))
            return TocPagePick()

        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted, evidence, StreamingKnowledgeEngine(on_toc_pages=pick)
        )

        assert rounds[1] == (11, 12, 13, 14, 15)
        assert outline.toc_pages == (12,)
        assert [(node.row.name, node.row.anchors[0]["page"]) for node in outline.nodes] == [
            ("第1章 集合论", 1),
            ("第2章 实数与极限", 2),
        ]

    def test_the_review_leg_corrects_names_only(self, session, tmp_path):
        """校订腿（`KA-57`）：只改名字，页码与层级照抄录给的；候选面只收章级行。"""
        pages_dir = _toc_scene(session, tmp_path)
        _write_page_artifacts(pages_dir, 2, [], body="第一章 集合论\n正文开始。")
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = StreamingKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            corrections=[TocOutlineRow(index=0, level=1, name="第一章 集合论")],
        )

        outline = build_outline(accepted, evidence, engine)

        assert [node.row.name for node in outline.nodes] == [
            "第一章 集合论",
            "序的关系与集合的势",
            "第2章 实数与极限",
        ]
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4], "校订不碰页码"
        assert outline.unlocated == ("附录 习题解答",), "偏移标定成功后查不中的行不成行（KA-33 改判）"
        reviews = [payload for step, payload in engine.calls if step == "toc_outline"]
        assert reviews == [
            ((0, "第1章 集合论"), (2, "第2章 实数与极限"), (3, "附录 习题解答"))
        ], "非章级的正文条目不交校订——名以正文为准（`KA-50`）"

    def test_a_failing_review_leg_keeps_the_copied_names(self, session, tmp_path):
        """校订是可选腿：调用失败只让名字保持原文，不让整批目录失败。"""
        _toc_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = StreamingKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)), failing_outline=True
        )

        outline = build_outline(accepted, evidence, engine)

        assert [node.row.name for node in outline.nodes] == [
            "第1章 集合论",
            "序的关系与集合的势",
            "第2章 实数与极限",
        ], "校订失败只让名字保持原文；未定位的附录行照新判据不成行（KA-33 改判）"


    def test_a_reference_group_already_in_the_toc_does_not_get_a_second_row(self, session, tmp_path):
        """附件页与目录成组条目同名时只留目录行（`KA-57`）：索引五页不再变成六枚顶级「索引」。"""
        pages_dir = _scene(session, tmp_path, page_count=6)
        bodies = {
            1: "第1章 集合论 …… 1\n索引 …… 4",
            2: "序\n前言正文。",
            3: "第1章 集合论\n正文开始。",
            4: "正文。",
            5: "正文。",
            6: "索引\n符号与页码清单。",
        }
        for page_no, body in bodies.items():
            _write_page_artifacts(pages_dir, page_no, [], body=body)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = StreamingKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(
                toc_pages=(1,),
                reference_pages=(
                    ReferencePage(page_no=2, name="序", kind="front_matter"),
                    ReferencePage(page_no=6, name="索引", kind="index"),
                ),
            ),
        )

        outline = build_outline(accepted, evidence, engine)

        assert [node.row.name for node in outline.nodes] == ["序", "第1章 集合论", "索引"]
        assert outline.reference_rows == 2


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

        assert [node.row.name for batch in sink.repointed for node in batch] == ["第1章 集合论", "第2章 实数与极限"]
        repointed = sink.repointed[0][0].row
        assert repointed.anchors[0]["page"] == 3, "全书偏移标定出来后，锚从目录页改写成正文页"
        assert (repointed.name, repointed.depth) == ("第1章 集合论", 1), "只改锚，不改名与层级"
        landed_by_name = {node.row.name: node.row for batch in sink.landed for node in batch}
        assert landed_by_name["第1章 集合论"].node_id == repointed.node_id, "改写的是已在册那一行，不是新行"
        assert [[node.row.name for node in batch] for batch in sink.repointed] == [
            ["第1章 集合论", "第2章 实数与极限"]
        ], "第2章只因未定位行折掉而顺延了序位；锚没变的行也属于读数变了那一批"
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [3, 5]
        assert outline.unlocated == ("一致连续的判别法",)
        assert len(outline.nodes) == 2, "定稿不增行：早落三行折掉未定位一行，定稿两行"

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

        # `list_batch` 按 `(created_at, node_id)` 排：同刻落的两行在 Windows 的 16ms 时钟粒度下
        # 谁先谁后没有承诺，故这一窗只判「两行都已可读」，不判库序。
        assert [sorted(batch) for batch in seen] == [
            sorted([("第1章 集合论", "draft", 1), ("一致连续的判别法", "draft", 1)])
        ]
        assert credential == [accepted.gen_batch], "在飞凭据＝早落的那批行，不是第二事实源（KA-40）"
        rows = _rows(session, node_kind="outline")
        assert [(row.name, row.anchors[0]["page"], row.order_hint) for row in rows] == [
            ("第1章 集合论", 3, 1),
            ("第2章 实数与极限", 5, 2),
        ], "定稿改写落在库里且不增行；未定位的早落行随定稿折掉（KA-33 改判）"
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
        assert [node.row.name for node in outline.nodes] == [
            "第1章 集合论",
            "序的关系与集合的势",
            "第2章 实数与极限",
        ], "偏移标定成功后仍未定位的行不成行，只在账面点名（KA-33 改判）"

    def test_an_answer_of_absent_is_disclosed_as_unlocated(self, session, tmp_path):
        """判「正文里没有这一行」⇒ 未定位披露；偏移标定成功时该行不成行（`KA-33` 改判）。"""
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

    def test_a_page_less_row_is_verified_between_its_anchored_neighbours(self, session, tmp_path):
        """Q5 甲：无印刷页码的行同样进核对腿，候选页由上下两枚已定锚行夹出（含两端锚页）。"""
        _book(session, "b05", active_job_id="job-a1", page_count=6)
        pages_dir = _pages_dir(tmp_path, book_id="b05", job_id="job-a1")
        bodies = {
            1: "第1章 集合论 …… 1\n序的关系与集合的势 …… 2\n第2章 实数与极限 …… 3\n"
            "函数的连续性\n第3章 极限的运算 …… 5",
            2: "第1章 集合论\n正文开始。",
            3: "序的关系与集合的势\n正文继续。",
            4: "第2章 实数与极限\n正文又起。",
            5: "函数的连续性\n正文这一段。",
            6: "第3章 极限的运算\n正文收尾。",
        }
        repo = PageRepository(session)
        for page_no in range(1, 7):
            _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
            repo.upsert("b05", "job-a1", page_no, status="parsed", source="mineru", error="")
        session.commit()
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        captured: list = []

        def verify(items):
            captured.append(items)
            return [TocVerifyAnswer(name="函数的连续性", page_no=5)]

        engine = self._engine(
            [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
                OutlineEntry(level=2, printed_page=None, name="函数的连续性"),
                OutlineEntry(level=1, printed_page=5, name="第3章 极限的运算"),
            ],
            verify,
        )

        outline = build_outline(accepted, evidence, engine)

        assert [item.name for item in captured[0]] == ["函数的连续性"]
        assert captured[0][0].printed_page is None, "缺印刷页码的行照样交核对（Q5 甲）"
        assert captured[0][0].candidates == (
            (4, "第2章 实数与极限"),
            (5, "函数的连续性"),
            (6, "第3章 极限的运算"),
        ), "候选面＝上下两枚已定锚行之间的页（含两端）"
        assert outline.verified == ("函数的连续性",)
        assert outline.unlocated == ()
        assert outline.nodes[3].row.anchors[0]["page"] == 5

    def test_a_page_less_tail_row_takes_its_window_from_the_last_anchor(self, session, tmp_path):
        """只有上一枚锚时，候选从那一页起取 `TOC_VERIFY_PAGE_SLACK` 页——不猜更远的地方。"""
        _book(session, "b05", active_job_id="job-a1", page_count=5)
        pages_dir = _pages_dir(tmp_path, book_id="b05", job_id="job-a1")
        bodies = {
            1: "第1章 集合论 …… 1\n序的关系与集合的势 …… 2\n第2章 实数与极限 …… 3\n函数的连续性",
            2: "第1章 集合论\n正文开始。",
            3: "序的关系与集合的势\n正文继续。",
            4: "第2章 实数与极限\n正文又起。",
            5: "函数的连续性\n正文这一段。",
        }
        repo = PageRepository(session)
        for page_no in range(1, 6):
            _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
            repo.upsert("b05", "job-a1", page_no, status="parsed", source="mineru", error="")
        session.commit()
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        captured: list = []

        def verify(items):
            captured.append(items)
            return [TocVerifyAnswer(name="函数的连续性", page_no=5)]

        engine = self._engine(
            [
                OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                OutlineEntry(level=2, printed_page=2, name="序的关系与集合的势"),
                OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
                OutlineEntry(level=2, printed_page=None, name="函数的连续性"),
            ],
            verify,
        )

        outline = build_outline(accepted, evidence, engine)

        assert captured[0][0].candidates == (
            (4, "第2章 实数与极限"),
            (5, "函数的连续性"),
        ), "缺下一枚锚 ⇒ 从上一枚锚页起、止于取证末页（候选页不存在的不编）"
        assert outline.nodes[3].row.anchors[0]["page"] == 5
        assert outline.unlocated == ()


#: `_tolerant_scene` 的页形状：一页若干段落块（块序＝下标），目录页 p1 整页一节一行。
_TOLERANT_BLOCKS: dict[int, list[str]] = {
    1: [
        "第1章 实数 …… 1\n第2章 序 …… 2\n第3章 微分 …… 3\n第4章 积分 …… 4\n第5章 微分法 …… 5\n"
        "第6章 级数 …… 6\n第7章 傅里叶级数 …… 7\n第8章 一致收敛 …… 8\n数 e …… 20\n"
        "§ 2. 反函数的导数 …… 30\n第6章 RIEMANN-STIEI. TJES积分 …… 40\nΓ函数 …… 50\n习题 …… 92"
    ],
    2: ["第1章 实数", "正文。"],
    3: ["第2章 序", "正文。"],
    4: ["第3章 微分", "正文。"],
    5: ["第4章 积分", "正文。"],
    6: ["第 5 章 微分法", "正文。"],
    7: ["第6章 级数", "正文。"],
    8: ["第7章 傅里叶级数", "正文。"],
    9: ["第8章 一致收敛", "正文。"],
    21: ["一些正文。", "数 $e$", "更多正文。"],
    31: ["§ 2. 反函数的导数. 用参数形式给出的函数的导数.", "隐函数的导数", "正文。"],
    41: ["第 6 章 RIEMANN-STIELTJES 积分", "正文。"],
    51: ["一些正文。", "$\\Gamma$ 函数", "正文。"],
}

#: 模型整理腿的读数：八枚前缀命中（用来标定偏移＝1）＋四枚容错形状＋一枚哪一趟都救不回的 `习题`。
#: 名字与页码逐枚取自 REQ-153 壳的「现网取证」表（真机在册脏锚的失配形）。
_TOLERANT_ROWS: list[OutlineEntry] = [
    OutlineEntry(level=1, printed_page=1, name="第1章 实数"),
    OutlineEntry(level=1, printed_page=2, name="第2章 序"),
    OutlineEntry(level=1, printed_page=3, name="第3章 微分"),
    OutlineEntry(level=1, printed_page=4, name="第4章 积分"),
    OutlineEntry(level=1, printed_page=5, name="第5章 微分法"),
    OutlineEntry(level=1, printed_page=6, name="第6章 级数"),
    OutlineEntry(level=1, printed_page=7, name="第7章 傅里叶级数"),
    OutlineEntry(level=1, printed_page=8, name="第8章 一致收敛"),
    OutlineEntry(level=2, printed_page=20, name="数 e"),
    OutlineEntry(
        level=2, printed_page=30, name="§ 2. 反函数的导数。用参数形式给出的函数的导数.隐函数的导数"
    ),
    OutlineEntry(level=1, printed_page=40, name="第6章 RIEMANN-STIEI. TJES积分"),
    OutlineEntry(level=2, printed_page=50, name="Γ函数"),
    OutlineEntry(level=2, printed_page=92, name="习题"),
]


def _tolerant_scene(
    session: Session,
    tmp_path: Path,
    *,
    book_id: str = "b05",
    job_id: str = "job-a1",
    toc_lines: list[str] | None = None,
) -> Path:
    """一本「目录行是脏的、正文行是净的」的书：五形失配各一枚，另有八枚前缀命中用来标定偏移。

    偏移＝1（与 `_toc_scene` 同一取向），所以 `习题` 的目标页 93 根本不在取证面里——它是丙方案
    也救不回的那一形（真机 b05 第 4 章正文没有 `习题` 标题块）。`toc_lines` 换掉目录页那一块的行，
    给确定性条目腿（`KA-33` 的页级复核只在它那条来路上判）用。
    """
    _book(session, book_id, active_job_id=job_id, page_count=51)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    blocks_by_page = dict(_TOLERANT_BLOCKS)
    if toc_lines is not None:
        blocks_by_page[1] = ["\n".join(toc_lines)]
    repo = PageRepository(session)
    for page_no, texts in blocks_by_page.items():
        blocks = [
            {
                "type": "paragraph",
                "bbox": [0, 80 + 90 * index, 900, 170 + 90 * index],
                "index": index,
                "order": index,
                "text": text,
            }
            for index, text in enumerate(texts)
        ]
        (pages_dir / f"p{page_no:04d}.blocks.json").write_text(
            json.dumps(
                {"page": page_no, "image_size": [1000, 1400], "source": "mineru", "blocks": blocks},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (pages_dir / f"p{page_no:04d}.md").write_text("\n\n".join(texts), encoding="utf-8")
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


def _tolerant_engine(
    rows: Sequence[OutlineEntry] | None = None, on_toc_verify=None
) -> FakeKnowledgeEngine:
    return FakeKnowledgeEngine(
        on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
        on_toc_entries=lambda page_no, text: list(rows if rows is not None else _TOLERANT_ROWS),
        on_toc_verify=on_toc_verify,
    )


class TestTolerantRelocate:
    """`KA-49`：第一遍前缀查不中的条目再过一趟容错比对，仍只认窗口内的命中，且排在核对腿之前。

    四形各一枚真机形状（定界符／希腊宏名／宏壳异形／断词近似／跨两块）；`习题` 是哪一趟都不中的
    那一形，照旧未定位披露。容错读数既不进偏移标定也不进页级复核（`KA-33`），否则符号表页的假章
    会成批复活。
    """

    def _build(self, session, tmp_path, rows=None, on_toc_verify=None):
        _tolerant_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        engine = _tolerant_engine(rows, on_toc_verify)
        return build_outline(accepted, evidence, engine), engine

    def test_the_four_tolerant_shapes_land_on_their_body_pages(self, session, tmp_path):
        """四形命中的锚各落正文页与正文块：`数 e`→p21 blk1、跨两块→p31 blk0、断词→p41 blk0、宏名→p51 blk1。"""
        outline, _engine = self._build(session, tmp_path)

        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [
            2, 3, 4, 5, 6, 7, 8, 9, 21, 31, 41, 51,
        ]
        assert [node.row.anchors[0]["block_indexes"] for node in outline.nodes][8:12] == [[1], [0], [0], [1]]
        assert outline.dropped_toc_pages == ()

    def test_the_verify_leg_only_sees_rows_both_passes_missed(self, session, tmp_path):
        """容错趟排在核对之前（`KA-38`「先查后核」的同一条次序）：坏行面只剩真救不回的那一枚。"""
        captured: list = []

        def verify(items):
            captured.append(items)
            return []

        outline, engine = self._build(session, tmp_path, on_toc_verify=verify)

        assert [item.name for item in captured[0]] == ["习题"], "四枚容错行不再交模型（一次核对 168 秒）"
        assert outline.unlocated == ("习题",)
        assert [step for step, _ in engine.calls].count("toc_verify") == 1

    def test_an_entry_no_pass_rescues_is_disclosed_not_rowed(self, session, tmp_path):
        """救不回的不成行、只在账面点名：偏移标定成功时把目录页当正文页就是假锚（`KA-33` 改判）。"""
        outline, _engine = self._build(session, tmp_path)

        assert [node.row.name for node in outline.nodes][-1] == "$\\Gamma$ 函数"
        assert "习题" not in [node.row.name for node in outline.nodes]
        assert outline.unlocated == ("习题",)

    def test_the_tolerant_leg_never_calibrates_the_offset(self, session, tmp_path):
        """偏移只由第一遍标定：容错命中页（21／31／41／51）与印刷页的差是 1 也是「猜」，不进众数。"""
        rows = [row for row in _TOLERANT_ROWS if row.name in {"第1章 实数", "第2章 序", "数 e", "Γ函数"}]
        outline, _engine = self._build(session, tmp_path, rows=rows)

        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 21, 51], (
            "两枚前缀命中支持数够标定（偏移＝1），容错两枚才落得进正文页"
        )

    def test_a_page_the_recheck_drops_is_not_rescued_by_the_tolerant_leg(self, session, tmp_path):
        """页级复核（`KA-33`）只吃第一遍读数：符号表那一页不因容错命中而复活成假章。

        本条走确定性条目腿（`model_ref='rule:toc'`）——页级复核只作用在那条来路上，模型认下的目录页
        不靠命中率反证。
        """
        # 三枚行形状像目录、正文里根本没有同名行（符号表页的原形）。不能拿容错腿救得回的那几形
        # 来凑「过半不中」：`KA-61` 的前缀第二遍能把 `数 $e$`／`$\\Gamma$ 函数` 落回正文页，
        # 那说明这一页的印刷页码与本书偏移真的对得上，它就不是符号表。
        _tolerant_scene(
            session,
            tmp_path,
            toc_lines=[
                "第1章 实数 …… 1",
                "第2章 序 …… 2",
                "符号甲 …… 20",
                "符号乙 …… 50",
                "符号丙 …… 92",
            ],
        )
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(accepted, evidence, FakeKnowledgeEngine())

        assert outline.dropped_toc_pages == (1,), "五枚里三枚第一遍不中 ⇒ 过半 ⇒ 整页不算目录页"
        assert [node.row.name for node in outline.nodes] == []


class TestBodyNameWins:
    """`KA-50`：正文里有折叠同形的标题行时，名字取那一行——脏名在源头换成净名，`norm_key` 随它。"""

    def _build(self, session, tmp_path, *, sink=None, rows=None):
        _tolerant_scene(session, tmp_path)
        accepted = _accepted(session, stage="outline")
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)
        return build_outline(accepted, evidence, _tolerant_engine(rows), sink=sink)

    def test_the_body_spelling_wins_for_every_tolerant_shape(self, session, tmp_path):
        """四形容错行的行名写成正文拼法（不是目录拼法）：跨两块那一枚以一枚空格相连。"""
        outline = self._build(session, tmp_path)

        assert [node.row.name for node in outline.nodes][8:] == [
            "数 $e$",
            "§ 2. 反函数的导数. 用参数形式给出的函数的导数. 隐函数的导数",
            "第 6 章 RIEMANN-STIELTJES 积分",
            "$\\Gamma$ 函数",
        ], "救不回的「习题」不成行，不进这张名字表"

    def test_a_dirty_spacing_in_the_toc_line_is_cleaned_by_the_body_line(self, session, tmp_path):
        """第一遍前缀照样命中的行也改名：`第5章 微分法` 写成正文那一行的 `第 5 章 微分法`（真机在册脏名）。"""
        outline = self._build(session, tmp_path)

        assert [node.row.name for node in outline.nodes][:8] == [
            "第1章 实数",
            "第2章 序",
            "第3章 微分",
            "第4章 积分",
            "第 5 章 微分法",
            "第6章 级数",
            "第7章 傅里叶级数",
            "第8章 一致收敛",
        ]

    def test_a_prefix_hit_does_not_rename_the_entry(self, session, tmp_path):
        """前缀是两条不同的话，不是同一个名字的两种拼法：正文行更长时名字一字不动。"""
        rows = [
            OutlineEntry(level=2, printed_page=20, name="数"),
            OutlineEntry(level=2, printed_page=1, name="第1章 实"),
        ]
        outline = self._build(session, tmp_path, rows=rows)

        assert [node.row.name for node in outline.nodes] == ["数", "第1章 实"]

    def test_norm_key_is_derived_from_the_clean_name(self, session, tmp_path):
        """`norm_key` 随净名：认行判据读的正是这一枚（`KA-13` 的让位与跨批认行都从它走）。"""
        outline = self._build(session, tmp_path)

        assert [node.row.norm_key for node in outline.nodes] == [
            norm_key_for(node.row.name) for node in outline.nodes
        ]
        assert outline.nodes[10].row.name == "第 6 章 RIEMANN-STIELTJES 积分"
        assert norm_key_for("第6章 RIEMANN-STIEI. TJES积分") != outline.nodes[10].row.norm_key

    def test_the_two_passes_give_the_same_names(self, session, tmp_path):
        """两趟名字逐字相同（`KA-40` 的硬不变式）：名字判据不读偏移，早落的行名就是定稿的行名。"""
        sink = _RecordingOutlineSink()
        outline = self._build(session, tmp_path, sink=sink)

        landed_names = {node.row.node_id: node.row.name for batch in sink.landed for node in batch}
        assert landed_names, "逐页落行面要有行可判"
        assert [(landed_names[node.row.node_id], node.row.name) for node in outline.nodes] == [
            (node.row.name, node.row.name) for node in outline.nodes
        ]


def _late_reference_scene(session: Session, tmp_path: Path, *, book_id: str = "b05", job_id: str = "job-a1") -> Path:
    """书尾带习题页与参考书目（b05 形状）：目录页里的 `习题` 行按名字归非知识点。

    绝对页＝印刷页＋1（p1 是目录）；p6 是书尾习题页、p7 是参考书目页。判「正文前后」时若把目录
    条目 `习题 …… 5` 的页也算进知识点页，模型认出的附件页会被判成「正文前」（b05 现网缺陷）。
    """
    _book(session, book_id, active_job_id=job_id, page_count=7)
    pages_dir = _pages_dir(tmp_path, book_id=book_id, job_id=job_id)
    bodies = {
        1: "第1章 集合论 …… 1\n第2章 实数与极限 …… 3\n习题 …… 5",
        2: "第1章 集合论\n正文开始。",
        3: "正文继续。",
        4: "第2章 实数与极限\n正文又起。",
        5: "正文再续。",
        6: "习题\n1. 证明。",
        7: "参考书目\n[1] 某书。",
    }
    repo = PageRepository(session)
    for page_no in range(1, 8):
        _write_page_artifacts(pages_dir, page_no, [], body=bodies[page_no])
        repo.upsert(book_id, job_id, page_no, status="parsed", source="mineru", error="")
    session.commit()
    return pages_dir


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

    def test_a_toc_reference_entry_does_not_pull_back_matter_to_the_front(self, session, tmp_path):
        """判前后只拿**知识点行**的页：目录里自己列着的 `习题 …… 5` 不算尾巴，模型认出的书尾附件页
        因此留在章序之后——b05 第 11 章习题页与参考书目被排到第 1 章之前的回归（2026-11-06）。"""
        _late_reference_scene(session, tmp_path)
        accepted = _accepted(session)
        evidence = build_evidence_set(session, data_root=tmp_path, acceptance=accepted)

        outline = build_outline(
            accepted,
            evidence,
            self._engine(
                (
                    ReferencePage(page_no=6, name="习题", kind="exercise"),
                    ReferencePage(page_no=7, name="参考书目", kind="bibliography"),
                ),
                rows=[
                    OutlineEntry(level=1, printed_page=1, name="第1章 集合论"),
                    OutlineEntry(level=1, printed_page=3, name="第2章 实数与极限"),
                    OutlineEntry(level=1, printed_page=5, name="习题"),
                ],
            ),
        )

        assert [node.row.name for node in outline.nodes[:2]] == ["第1章 集合论", "第2章 实数与极限"], (
            "书尾附件页不得排在章序之前"
        )
        assert [node.row.anchors[0]["page"] for node in outline.nodes[:2]] == [2, 4]
        assert [node.row.name for node in outline.nodes[-2:]] == ["习题", "参考书目"]
        assert [node.row.anchors[0]["page"] for node in outline.nodes[-2:]] == [6, 7], "习题页与参考书目留在章序之后"

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
        """回落腿按名字判（`KA-39`）：条目腿补出的行里，前言／符号表／附录这些名字要带得出身份位。

        附录印在正文最后一页、能回定位——未定位的行自 `KA-33` 改判后不成行，这里必须给它一个
        真页，判的才是身份位而不是「查不中」。
        """
        _book(session, "b05", active_job_id="job-a1", page_count=6)
        pages_dir = _pages_dir(tmp_path, book_id="b05", job_id="job-a1")
        bodies = {
            1: "第1章 集合论 …… 1\n序的关系与集合的势 …… 2\n第2章 实数与极限 …… 3\n附录 习题解答 …… 5",
            2: "第1章 集合论\n正文开始。",
            3: "序的关系与集合的势\n正文继续。",
            4: "第2章 实数与极限\n正文又起。",
            5: "正文。",
            6: "附录 习题解答\n解答正文。",
        }
        repo = PageRepository(session)
        for page_no, body in bodies.items():
            _write_page_artifacts(pages_dir, page_no, [], body=body)
            repo.upsert("b05", "job-a1", page_no, status="parsed", source="mineru", error="")
        session.commit()
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
        assert [node.row.anchors[0]["page"] for node in outline.nodes] == [2, 3, 4, 6]

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

        def __init__(self, node_id, *, depth, meta, parent_id=None, node_kind="outline") -> None:
            self.node_id = node_id
            self.depth = depth
            self.meta = meta
            self.parent_id = parent_id
            self.node_kind = node_kind

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

    def test_a_first_level_row_without_point_children_is_other(self):
        """`KA-68`（用户裁决 2026-11-09）：预备知识、附录这类一级行不是章。

        判据是结构而不是名字：其下没有任何知识点行（只有拆出的习题行，或干脆没有子行）的一级知识行
        记「其他」。按名字认（「第X章」）会把 b12 的「绪论 实数」判错——它有四个知识点子行。
        """
        rows = [
            self._Row("pre", depth=1, meta={"toc_role": "knowledge"}),
            self._Row("appendix", depth=1, meta={"toc_role": "knowledge"}),
            self._Row(
                "appendix-ex",
                depth=2,
                meta={"toc_role": "reference", "toc_kind": "exercise"},
                parent_id="appendix",
            ),
            self._Row("ch1", depth=1, meta={"toc_role": "knowledge"}),
            self._Row("ch1-1", depth=2, meta={"toc_role": "knowledge"}, parent_id="ch1"),
        ]

        types = node_types_of(rows)

        assert types == {
            "pre": "other",
            "appendix": "other",
            "appendix-ex": "exercise",
            "ch1": "chapter",
            "ch1-1": "point",
        }

    def test_a_point_kind_child_keeps_its_first_level_row_a_chapter(self):
        """习题集形的章（子行是 `node_kind='point'` 的题行）仍是章：题行按章分组要靠它。"""
        rows = [
            self._Row("ch1", depth=1, meta={"toc_role": "knowledge"}),
            self._Row(
                "item-1",
                depth=2,
                meta={"toc_kind": "exercise"},
                parent_id="ch1",
                node_kind="point",
            ),
        ]

        assert node_types_of(rows) == {"ch1": "chapter", "item-1": "exercise"}

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
        """`KA-6`／`KA-62`：第三层不进 `af_kp`、只折进所属二级并计数；它的文字不丢——节正文里仍在。"""
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

        assert outcome.folded_sub_items == 1
        assert outcome.dropped_depth == 0, "三级是折入，不是越界"
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


class TestPointDetailReads:
    """`KA-57`：详情两枚读数走 `meta` 闭集键，坏条目丢弃、不丢行。

    点行的成立判据仍是 `name`＋`anchors`＋`type`＋`role`（`KA-32`）；掌握要点与关键定义是增强位，
    形状不合只让这一条消失，不让整个知识点消失。
    """

    def test_the_two_detail_reads_land_in_meta(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [
                        _point_draft(
                            "数列极限的定义",
                            page=2,
                            must_master=["能写出 ε-N 定义", "会用定义证明收敛"],
                            key_definitions=[
                                {"term": "数列极限", "definition": "存在 L 使任意 ε 有 N。"}
                            ],
                        )
                    ]
                },
            ),
            acceptance=_accepted(session),
        )

        row = _rows(session, node_kind="point")[0]
        assert row.meta["must_master"] == ["能写出 ε-N 定义", "会用定义证明收敛"]
        assert row.meta["key_definitions"] == [
            {"term": "数列极限", "definition": "存在 L 使任意 ε 有 N。"}
        ]

    def test_malformed_detail_entries_are_dropped_but_the_row_survives(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [
                        _point_draft(
                            "数列极限的定义",
                            page=2,
                            must_master=["  能复述定义  ", 5, "", "会用定义证收敛"],
                            key_definitions=[
                                {"term": "极限"},
                                {"definition": "只有定义没有术语"},
                                {"term": "  数列  ", "definition": "  按序排列的数  "},
                                "不是对象",
                            ],
                        )
                    ]
                },
            ),
            acceptance=_accepted(session),
        )

        rows = _rows(session, node_kind="point")
        assert [row.name for row in rows] == ["数列极限的定义"]
        assert rows[0].meta["must_master"] == ["能复述定义", "会用定义证收敛"]
        assert rows[0].meta["key_definitions"] == [
            {"term": "数列", "definition": "按序排列的数"}
        ]

    def test_absent_reads_leave_no_meta_and_long_entries_are_capped(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [
                        _point_draft("数列极限的定义", page=2),
                        _point_draft(
                            "函数极限的定义",
                            page=5,
                            must_master=["长" * 300],
                            key_definitions=[{"term": "t" * 300, "definition": "d" * 900}],
                        ),
                    ]
                },
            ),
            acceptance=_accepted(session),
        )

        by_name = {row.name: row for row in _rows(session, node_kind="point")}
        assert by_name["数列极限的定义"].meta is None
        capped = by_name["函数极限的定义"].meta
        assert len(capped["must_master"][0]) == 120
        assert len(capped["key_definitions"][0]["term"]) == 120
        assert len(capped["key_definitions"][0]["definition"]) == 500

    def test_point_meta_never_carries_keys_outside_the_registered_set(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={
                    "1.1 数列极限": [
                        _point_draft(
                            "数列极限的定义",
                            page=2,
                            must_master=["能写出定义"],
                            key_definitions=[{"term": "极限", "definition": "接近某值。"}],
                        )
                    ]
                },
            ),
            acceptance=_accepted(session),
        )

        row = _rows(session, node_kind="point")[0]
        assert set(row.meta) <= set(KP_META_KEYS)


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


class TestCurationResult:
    """`KA-54`／`KA-55`：结局位三个写者，三段读数各答一件事。

    `last_curation_result` 只由收尾、异常腿与启动归一写；「开始梳理」不入列，由 `draft` 行的
    存在性在读时派生（`curating_book_ids` 的一条分组查询）。
    """

    def test_a_clean_batch_reads_completed(self, session, tmp_path):
        """四类缺口一个都没有 ⇒ `completed`：收尾翻 `candidate` 与结局写在同一提交里。"""
        _scene(session, tmp_path, page_count=6)

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )
        session.commit()

        assert _curation_result(session) == LastCurationResult.COMPLETED

    def test_a_failed_section_reads_partial(self, session, tmp_path):
        """一节坏了不等于整本坏了（批内继续），但上一批没跑全 ⇒ `partial`。"""
        _scene(session, tmp_path, page_count=6)

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(failing_sections=("1.1 数列极限",)),
            acceptance=_accepted(session),
        )
        session.commit()

        assert _curation_result(session) == LastCurationResult.PARTIAL

    def test_a_missing_artifact_page_reads_partial(self, session, tmp_path):
        """缺产物与未解析页不是失败节，但那是「要读的正文没读到」——不能读成 `completed`。"""
        _scene(session, tmp_path, page_count=6, missing_artifacts=(3,))

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )
        session.commit()

        assert _curation_result(session) == LastCurationResult.PARTIAL

    def test_an_unparsed_page_reads_partial(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6, unparsed=(3,))

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )
        session.commit()

        assert _curation_result(session) == LastCurationResult.PARTIAL

    def test_the_judgement_reads_each_gap_independently(self):
        """纯判据面：四类缺口任一成立即 `partial`，只看「有没有」，不看「几处」。"""
        gaps = {
            "failed_sections": 1,
            "failed_chapters": 1,
            "missing_pages": (3,),
            "unparsed_pages": (3,),
        }

        assert (
            curation_result_for(
                failed_sections=0, failed_chapters=0, missing_pages=(), unparsed_pages=()
            )
            == LastCurationResult.COMPLETED
        )
        for key, value in gaps.items():
            kwargs = dict(failed_sections=0, failed_chapters=0, missing_pages=(), unparsed_pages=())
            kwargs[key] = value
            assert curation_result_for(**kwargs) == LastCurationResult.PARTIAL, key

    def test_the_exception_leg_writes_failed(self, tmp_path):
        """执行体抛异常 ⇒ 失败流水与结局同一提交写；异常批次不再走收尾，`failed` 是唯一读数。"""
        _, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)

        class _BindBroken(FakeKnowledgeEngine):
            accounts_externally = True

            def bind_batch(self, **kwargs):
                raise RuntimeError("适配器绑定失败")

        runner = _kp_runner(factory, tmp_path, _BindBroken())
        runner.submit_knowledge(acceptance=acceptance, engine_name="qed-gateway")

        with factory() as session:
            assert _curation_result(session) == LastCurationResult.FAILED
            ledger = OperationRepository(session).find_kp_batch(
                acceptance.book_id, acceptance.gen_batch
            )
            assert ledger is not None and ledger.status == "failed"

    def test_startup_reconcile_writes_interrupted(self, session):
        """进程被杀留下的 `draft` 没有流水行 ⇒ 归一时记 `interrupted`。"""
        _book(session)
        _draft(session, "afkp-a1", gen_batch="afkp-111111111111")
        session.commit()

        KpRepository(session).reconcile_draft_on_startup()

        assert _curation_result(session) == LastCurationResult.INTERRUPTED

    def test_a_recorded_failure_survives_the_restart(self, session):
        """异常腿已写下 `failed` 的批次，重启时残留 `draft` 照清，但结局不被改写成 `interrupted`。"""
        _book(session)
        _draft(session, "afkp-a1", gen_batch="afkp-111111111111")
        BookRepository(session).mark_curation_result("b05", result=LastCurationResult.FAILED)
        OperationRepository(session).create_kp_batch(
            KpBatchRow(
                gen_batch="afkp-111111111111",
                book_id="b05",
                job_id="job-a1",
                status="failed",
                error="内部错误（详见服务日志）",
            )
        )
        session.commit()

        disposed = KpRepository(session).reconcile_draft_on_startup()

        assert disposed == [("b05", "afkp-111111111111", 1)]
        assert _curation_result(session) == LastCurationResult.FAILED

    def test_the_in_flight_set_reads_draft_rows_only(self, session):
        """`KA-55`：派生读数的判据与 `find_draft_batch` 同一把，作用域放大到全表。"""
        _draft(session, "afkp-a1", book_id="b05", gen_status="draft")
        _draft(session, "afkp-a2", book_id="b06", gen_status="candidate")
        _draft(session, "afkp-a3", book_id="b07", gen_status="rejected")
        _draft(session, "afkp-a4", book_id="b08", gen_status="draft")
        session.commit()

        assert KpRepository(session).curating_book_ids() == {"b05", "b08"}


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

    def test_the_streamed_review_leg_is_recorded_and_stays_optional(self, tmp_path):
        """`toc_outline`（`KA-57`）也是模型调用：有这条腿的引擎透传一枚记账过的，没有的照旧没有。

        能力探测靠 `getattr(engine, "toc_outline", None)` 分通路，包装器不能替任何引擎凭空长出一条腿。
        """
        store, factory = _ledger_env(tmp_path)
        with factory() as session:
            acceptance = _accepted(session)

        wrapped = _accounted(FakeKnowledgeEngine(), factory, acceptance)
        assert getattr(wrapped, "toc_outline", None) is None

        streamed = _accounted(StreamingKnowledgeEngine(), factory, acceptance)
        streamed.toc_outline([TocOutlineRow(index=0, level=1, name="第1章 集合论")])

        rows = read_ledger_rows(store)
        assert [row["step"] for row in rows] == ["toc_outline"]

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
    def _tree(*specs: tuple) -> list[SpanRow]:
        """`(node_id, depth, page, toc_role[, toc_kind])` → 树序输入（`order_hint` 按给定次序）。"""
        rows: list[SpanRow] = []
        for index, (node_id, depth, page, role, *rest) in enumerate(specs, start=1):
            rows.append(
                SpanRow(
                    node_id=node_id,
                    name=node_id,
                    order_hint=index,
                    page=page,
                    depth=depth,
                    toc_role=role,
                    toc_kind=rest[0] if rest else "",
                )
            )
        return rows

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

    def test_an_exercise_row_gets_the_tree_order_span_not_a_single_page(self):
        """`KA-46`：习题段的正文从标题页起延伸到本章末——它是附件身份位，但不按附件行的单页退化发区间。

        单页退化是按「符号表／索引的物理正文不在条目页」写的；习题段的正文就在标题页之后，
        照发一页会让练习面只抽到标题那一页。
        """
        spans = section_spans_of(
            self._tree(
                ("ch1", 1, 9, "knowledge", ""),
                ("ex", 2, 20, "reference", "exercise"),
                ("ch2", 1, 29, "knowledge", ""),
            ),
            first_page=9,
            last_page=317,
        )

        assert [(span.parent_id, span.page_start, span.page_end) for span in spans] == [
            ("ch1", 9, 28),
            ("ex", 20, 28),
            ("ch2", 29, 317),
        ]

    def test_a_section_reaches_the_page_where_the_next_section_starts_up_to_its_heading(self):
        """真机 b05：导引的正文延续到「有序集」起始页——终点页取那一页，止于有序集标题块之前。

        页级算式把终点压到「下一行起始页 − 1」，上一节的尾段落在下一节那页开头时整段丢掉；
        带块号的锚点能把它精确切回来：下一页（含）从页首到标题块前一块。
        """
        spans = section_spans_of(
            [
                SpanRow(node_id="daoyin", name="导引", order_hint=1, page=9, depth=2, block=1),
                SpanRow(node_id="order", name="有序集", order_hint=2, page=10, depth=2, block=14),
                SpanRow(node_id="ch2", name="第2章", order_hint=3, page=29, depth=1),
            ],
            first_page=9,
            last_page=317,
        )

        assert [
            (span.parent_id, span.page_start, span.page_end, span.start_block, span.end_block)
            for span in spans
        ] == [
            ("daoyin", 9, 10, 1, 13),
            ("order", 10, 28, 14, None),
            ("ch2", 29, 317, None, None),
        ]

    def test_a_next_heading_at_the_top_of_its_page_ends_the_previous_section_a_page_earlier(self):
        """下一节标题就是那一页的首块时没有可归属的尾段：上一节仍止于前一页（不造空页）。"""
        spans = section_spans_of(
            [
                SpanRow(node_id="a", name="甲", order_hint=1, page=9, depth=2, block=1),
                SpanRow(node_id="b", name="乙", order_hint=2, page=10, depth=2, block=0),
            ],
            first_page=9,
            last_page=20,
        )

        assert [
            (span.parent_id, span.page_start, span.page_end, span.start_block, span.end_block)
            for span in spans
        ] == [
            ("a", 9, 9, 1, None),
            ("b", 10, 20, 0, None),
        ]


class TestDisplaySpansOf:
    """D-1~D-9：显示段与取证区间分离——`section_spans_of` 继续给点腿取材，`display_spans_of` 给
    `page_start`／`page_end`：DFS 展示序、父先于子、段与段不重合且按页序，同页由先到者占页。
    """

    @staticmethod
    def _row(
        node_id: str,
        page: int,
        *,
        parent: str | None = None,
        depth: int = 2,
        role: str = "knowledge",
        kind: str = "",
        order: int | None = None,
        block: int | None = None,
    ) -> SpanRow:
        return SpanRow(
            node_id=node_id,
            name=node_id,
            order_hint=order,
            page=page,
            depth=depth,
            toc_role=role,
            toc_kind=kind,
            parent_id=parent,
            block=block,
        )

    @staticmethod
    def _read(spans: list[DisplaySpan]) -> list[tuple]:
        return [(span.node_id, span.page_start, span.page_end) for span in spans]

    def test_a_point_sharing_its_last_page_with_the_next_heading_keeps_that_page(self):
        """真机 b05：正文延续到下一枚知识点起始页时，显示段也要带到那页，止于下一标题块之前。"""
        spans = display_spans_of(
            [
                self._row("a", 9, block=1),
                self._row("b", 10, block=14),
                self._row("c", 29, block=0),
            ],
            last_page=317,
        )

        assert [(span.node_id, span.page_start, span.page_end, span.end_block) for span in spans] == [
            ("a", 9, 10, 13),
            ("b", 10, 28, None),
            ("c", 29, 317, None),
        ]

    def test_a_chapter_hands_the_pages_after_its_first_child_to_that_child(self):
        """D-4／D-5：章止于首子之前，知识点之间各管到下一枚锚页之前。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("p15", 15, parent="ch1"),
                self._row("p20", 20, parent="ch1"),
                self._row("ch2", 29, depth=1),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 14),
            ("p15", 15, 19),
            ("p20", 20, 28),
            ("ch2", 29, 317),
        ]

    def test_a_chapter_sharing_a_page_with_its_first_child_shows_only_that_page(self):
        """D-4／D-6：章与首点同页，先到者占页——章 [9,9]，首点顺延到 p10 起算。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("p9", 9, parent="ch1"),
                self._row("p20", 20, parent="ch1"),
                self._row("ch2", 29, depth=1),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 9),
            ("p9", 10, 19),
            ("p20", 20, 28),
            ("ch2", 29, 317),
        ]

    def test_a_chapter_without_children_runs_to_before_the_next_chapter(self):
        """D-4：无子行时终点＝下一章锚页 − 1，末章到书末。"""
        spans = display_spans_of(
            [self._row("ch1", 9, depth=1), self._row("ch2", 29, depth=1)],
            last_page=317,
        )

        assert self._read(spans) == [("ch1", 9, 28), ("ch2", 29, 317)]

    def test_points_anchored_on_one_page_take_one_page_each_in_book_order(self):
        """D-6：同页多枚知识点——先到者占该页，后到者从下一页起算，封顶章末。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("a", 20, parent="ch1"),
                self._row("b", 20, parent="ch1"),
                self._row("ch2", 41, depth=1),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 19),
            ("a", 20, 20),
            ("b", 21, 40),
            ("ch2", 41, 317),
        ]

    def test_a_point_shifted_past_the_chapter_end_is_disclosed_as_empty(self):
        """D-6：顺延越过本章末时该行显示段为空并披露，不编造页。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("a", 20, parent="ch1"),
                self._row("b", 20, parent="ch1"),
                self._row("ch2", 21, depth=1),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 19),
            ("a", 20, 20),
            ("b", None, None),
            ("ch2", 21, 317),
        ]

    def test_front_matter_rows_before_the_first_knowledge_row_take_one_page_each(self):
        """D-7：书前件（锚页早于第一枚知识点行）单页，不把整段前置页吞掉。"""
        spans = display_spans_of(
            [
                self._row("front", 4, depth=1, role="reference", kind="front_matter"),
                self._row("sym", 5, depth=1, role="reference", kind="symbol_table"),
                self._row("ch1", 9, depth=1),
                self._row("ch2", 29, depth=1),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("front", 4, 4),
            ("sym", 5, 5),
            ("ch1", 9, 28),
            ("ch2", 29, 317),
        ]

    def test_back_matter_rows_after_the_last_knowledge_row_run_on_to_the_book_end(self):
        """D-7：书尾附件区连续，末章止于第一枚附件行之前。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("ch2", 29, depth=1),
                self._row("bib", 313, depth=1, role="reference", kind="bibliography"),
                self._row("index", 315, depth=1, role="reference", kind="index"),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 28),
            ("ch2", 29, 312),
            ("bib", 313, 314),
            ("index", 315, 317),
        ]

    def test_an_exercise_row_runs_to_the_end_of_its_chapter(self):
        """D-7：习题段是附件身份位但不退回单页——正文从标题页延伸到本章末（`KA-46`）。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("p10", 10, parent="ch1"),
                self._row("ex", 20, parent="ch1", role="reference", kind="exercise"),
                self._row("ch2", 29, depth=1),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 9),
            ("p10", 10, 19),
            ("ex", 20, 28),
            ("ch2", 29, 317),
        ]

    def test_an_exercise_row_before_a_later_heading_on_the_same_page_takes_only_its_own_blocks(self):
        """真机 b18 §8.4／§8.5：习题行在目录里排在章末（order_hint 大），锚点却落在同页第 4 块、
        下一节标题在同页第 17 块。同级展示序只按 `(锚页, order_hint)` 会把 §8.5 的段算成
        「第 17 块起、第 3 块止」的倒挂段（屏上整页回落）。同页必须按块号排：习题段止于第 16 块。
        """
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("p1", 161, parent="ch1", order=1, block=17),
                self._row("ex", 161, parent="ch1", order=9, role="reference", kind="exercise", block=4),
                self._row("ch2", 200, depth=1),
            ],
            last_page=317,
        )

        assert [(span.node_id, span.page_start, span.page_end, span.end_block) for span in spans] == [
            ("ch1", 9, 160, None),
            ("ex", 161, 161, 16),
            ("p1", 161, 199, None),
            ("ch2", 200, 317, None),
        ]

    def test_a_row_with_children_ends_before_its_first_child_at_any_depth(self):
        """D-8：有子的行止于首子之前，叶子行到下一同级行之前——三层时同一算式自然生效。"""
        spans = display_spans_of(
            [
                self._row("ch1", 9, depth=1),
                self._row("p15", 15, parent="ch1"),
                self._row("q18", 18, parent="p15", depth=3),
                self._row("p20", 20, parent="ch1"),
            ],
            last_page=317,
        )

        assert self._read(spans) == [
            ("ch1", 9, 14),
            ("p15", 15, 17),
            ("q18", 18, 19),
            ("p20", 20, 317),
        ]

    def test_the_parent_comes_first_whatever_the_input_order(self):
        """D-3：展示序由锚页与树位给，输入按库里的行序喂进来也不改父先于子。"""
        rows = [
            self._row("ch2", 29, depth=1, order=2),
            self._row("p20", 20, parent="ch1", order=3),
            self._row("ch1", 9, depth=1, order=1),
            self._row("p15", 15, parent="ch1", order=2),
        ]

        assert [span.node_id for span in display_spans_of(rows, last_page=317)] == [
            "ch1",
            "p15",
            "p20",
            "ch2",
        ]

    def test_a_missing_page_count_leaves_the_tail_end_open(self):
        """D-9：书末页数缺时不给尾行终点，不猜书末。"""
        spans = display_spans_of(
            [self._row("ch1", 9, depth=1), self._row("ch2", 29, depth=1)],
            last_page=None,
        )

        assert self._read(spans) == [("ch1", 9, 28), ("ch2", 29, None)]

    def test_a_row_past_the_book_end_gets_no_segment(self):
        """起点页超出书末的行夹不出区间，两键为空而不是造一个假终点。"""
        spans = display_spans_of(
            [self._row("ch1", 9, depth=1), self._row("ch2", 29, depth=1)],
            last_page=20,
        )

        assert self._read(spans) == [("ch1", 9, 20), ("ch2", None, None)]

    def test_an_empty_outline_has_no_display_segments(self):
        """目录零行时没有可显示的段：显示面只发行自身的段，不编造无主区间。"""
        assert display_spans_of([], last_page=317) == []


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
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：从数列到极限。")},
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        steps = [step for step, _ in engine.calls]
        assert not [step for step in steps if step.startswith("toc_")], "目录三条腿零发起"
        assert steps == ["points", "chapter"], "先一节合批、后一章（`KA-45` 的两遍制）"
        assert len(_rows(session, node_kind="outline")) == 3, "在册目录行不增不减"
        assert (outcome.outline_rows, outcome.point_rows) == (0, 0)
        assert kp_batch_row(accepted, outcome).stage == "content"

    def test_the_sections_are_cut_from_the_registered_rows(self, session, tmp_path):
        """区间由在册行的锚点页与 `order_hint` 切（`KA-42` 同一条算式），不是另起一份目录。"""
        rows, accepted = self._registered(session, tmp_path)
        engine = FakeKnowledgeEngine()

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        (call,) = self._sections(engine)
        assert call.title == "1.1 数列极限 等 2 节", "两节都不足下限 ⇒ 合成一笔（`KA-64`）"
        assert call.parent_id == rows[0].node_id, "合批以共同父行为组"
        assert [member.node_id for member in call.members] == [row.node_id for row in rows[1:]]
        assert [
            (member.title, member.blocks[0].page, member.blocks[-1].page)
            for member in call.members
        ] == [("1.1 数列极限", 2, 3), ("1.2 函数极限", 4, 6)]

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

    # ------------------------------------------------------------------ 四链路分流（W6）

    def test_the_four_node_types_have_one_dispatch_table(self):
        """W6：四类行只有一张分流表——章走章腿、点走节腿，其他与习题零腿即终态（C5）。"""
        assert CONTENT_LEGS_BY_NODE_TYPE == {
            NODE_TYPE_CHAPTER: frozenset({"chapter"}),
            NODE_TYPE_POINT: frozenset({"points"}),
            NODE_TYPE_EXERCISE: frozenset(),
            NODE_TYPE_OTHER: frozenset(),
        }
        assert content_legs_for("unknown") == frozenset(), "未知类型不猜，按零腿处理"

    def test_other_and_exercise_rows_get_no_generated_legs(self, session, tmp_path):
        """W6／C5：内容批次只对章与点发问；「其他」与习题段零腿，行数与结构都不动。"""
        _scene(session, tmp_path, page_count=8)
        _register_tree(
            session,
            [
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
                ("afkp-ex1", "习题 1.1", 4, 2, "afkp-ch1"),
                ("afkp-sym", "重要符号表", 6, 1, None),
            ],
            metas={
                "afkp-ch1": {"toc_role": "knowledge"},
                "afkp-s11": {"toc_role": "knowledge"},
                "afkp-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
                "afkp-sym": {"toc_role": "reference", "toc_kind": "symbol_table"},
            },
        )
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        assert [step for step, _ in engine.calls] == ["points", "chapter"], "习题与其他零腿"
        assert [section.title for section in self._sections(engine)] == ["1.1 数列极限"]
        assert [material.title for step, material in engine.calls if step == "chapter"] == ["第 1 章 极限"]
        assert len(_rows(session, node_kind="outline")) == 4, "在册行不增不减"
        assert _rows(session, node_kind="point") == []
        assert outcome.point_rows == 0

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




class TestKindReviewPass:
    """`KA-63`：`content` 点腿前的一趟种类审校——零模型调用；行名是标签的行点名，
    段内标签块按（父, 归一名字, 锚页）拆成章下的并列习题行，重跑只作废旧拆分行。"""

    @staticmethod
    def _registered_scene(session: Session, tmp_path: Path) -> Path:
        """一章两节，第一节的区间里挂一枚整块「习题 1.1」（p4·b1）。"""
        pages_dir = _scene(session, tmp_path, page_count=8, layout={})
        _write_page_blocks(pages_dir, 2, ["1.1 数列极限", "正文一"])
        _write_page_blocks(pages_dir, 4, ["正文二", "习题 1.1"])
        _write_page_blocks(pages_dir, 7, ["正文三"])
        _register_tree(
            session,
            [
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
                ("afkp-s12", "1.2 函数极限", 5, 2, "afkp-ch1"),
            ],
        )
        return pages_dir

    @staticmethod
    def _splits(session: Session) -> list[AfKp]:
        return [
            row
            for row in _rows(session, node_kind="outline")
            if row.model_ref == EXERCISE_SPLIT_MODEL_REF
        ]

    def test_a_label_block_inside_a_point_span_lands_under_its_chapter(self, session, tmp_path):
        self._registered_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        splits = self._splits(session)
        assert len(splits) == 1
        split = splits[0]
        assert (split.parent_id, split.depth, split.name, split.summary) == (
            "afkp-ch1",
            2,
            "习题 1.1",
            "",
        )
        assert split.gen_status == "candidate"
        assert split.meta == {"toc_kind": "exercise", "toc_role": "reference"}
        assert [
            (anchor["kind"], anchor["page"], anchor["block_indexes"])
            for anchor in split.anchors or []
        ] == [("page_block", 4, [1])]
        assert [step for step, _ in engine.calls] == ["points", "chapter"], (
            "拆分行零模型调用，点腿调用数不变（两节合批仍是一笔）"
        )
        assert (outcome.type_review_rows, outcome.split_exercise_rows) == (3, 1)
        result = kp_batch_row(accepted, outcome).result
        assert (result["type_review_rows"], result["type_review_flags"], result["split_exercise_rows"]) == (
            3,
            [],
            1,
        )

    def test_the_registered_rows_keep_every_structural_column(self, session, tmp_path):
        self._registered_scene(session, tmp_path)
        before = {
            row.node_id: _columns(row)[3:9] for row in _rows(session, node_kind="outline")
        }
        accepted = accept_kp_job(session, book_id="b05", stage="content")

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
            ),
            acceptance=accepted,
        )
        session.expire_all()

        after = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert {
            node_id: _columns(row)[3:9] for node_id, row in after.items() if node_id in before
        } == before, "结构列（名、去重键、挂父、次序、边、锚点）零被触碰"

    def test_a_row_named_like_a_label_is_named_and_gets_no_point_leg(self, session, tmp_path):
        pages_dir = _scene(session, tmp_path, page_count=8, layout={})
        _write_page_blocks(pages_dir, 4, ["正文二", "习题 1.1"])
        _register_tree(
            session,
            [
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-ex", "习题 1.1", 4, 2, "afkp-ch1"),
            ],
        )
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine()

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert outcome.type_review_flags == ("习题 1.1",)
        assert (outcome.type_review_rows, outcome.split_exercise_rows) == (2, 0)
        assert [step for step, _ in engine.calls if step == "points"] == []
        assert self._splits(session) == [], "已在册的那一行不被拆出第二枚"

    def test_a_rerun_supersedes_the_previous_split_rows(self, session, tmp_path):
        self._registered_scene(session, tmp_path)
        first = accept_kp_job(session, book_id="b05", stage="content")
        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=first
        )
        session.expire_all()
        stale = self._splits(session)[0]

        second = accept_kp_job(session, book_id="b05", stage="content")
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=second
        )
        session.expire_all()

        assert session.get(AfKp, stale.node_id).gen_status == "rejected"
        fresh = [row for row in self._splits(session) if row.gen_status == "candidate"]
        assert len(fresh) == 1
        assert fresh[0].node_id != stale.node_id
        assert (outcome.split_exercise_rows, outcome.superseded_rows) == (1, 1)

    def test_the_point_material_stops_before_the_exercise_segment(self, session, tmp_path):
        """`KA-68`（用户裁决 2026-11-09）：点腿材料不需要习题段——取材止于习题标签块之前。

        切点取本批拆出的习题行（与屏上切尾同一条边界）：第一节的取块表里没有「习题 1.1」，
        标签块之前的正文照收。
        """
        self._registered_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine(summaries_by_section={"1.1 数列极限": "第一节总结"})

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert len(self._splits(session)) == 1, "切分照旧发生：切点不是「认不出」的副产物"
        texts = [
            text
            for step, section in engine.calls
            if step == "points"
            for text in (block.text for block in section.blocks)
        ]
        assert "习题 1.1" not in texts
        assert "正文二" in texts, "标签块之前的正文仍归本节"
        row = session.get(AfKp, "afkp-s11")
        ranges = (row.meta or {}).get("material_ranges") or []
        assert {"page": 4, "block_indexes": [1]} not in ranges
        assert {"page": 4, "block_indexes": [0]} in ranges


class _BudgetEngine(FakeKnowledgeEngine):
    """内容批次的点腿桩（`KA-64`）：合批按成员逐点作答，单节按窗口给出带序号的总结。"""

    def __init__(self, *, fail_window: tuple[str, int] | None = None) -> None:
        super().__init__(
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
        )
        self.fail_window = fail_window
        self.windows_seen: dict[str, int] = {}

    def points(self, section) -> SectionPoints:
        self.calls.append(("points", section))
        if section.members:
            return SectionPoints(
                [],
                "",
                tuple(
                    MemberPoints(
                        node_id=member.node_id,
                        drafts=[],
                        summary=f"总结：{member.title}",
                    )
                    for member in section.members
                ),
            )
        ordinal = self.windows_seen.get(section.title, 0) + 1
        self.windows_seen[section.title] = ordinal
        if self.fail_window == (section.title, ordinal):
            raise EngineError("模型服务中断")
        return SectionPoints([], f"总结：{section.title}#{ordinal}")


class TestPointBudget:
    """`KA-64`：内容批次的点腿按块取材、10,000 字符预算切窗、相邻小节点合批。

    一章三节：前两节各不足下限（合成一笔），第三节一枚块自身超预算（按句界切成两窗）。
    """

    _LONG = "长" * (CONTENT_INPUT_BUDGET_CHARS + 2000)

    @staticmethod
    def _registered_scene(session: Session, tmp_path: Path) -> Path:
        pages_dir = _scene(session, tmp_path, page_count=8, layout={})
        _write_page_blocks(
            pages_dir, 2, ["页眉噪音", "正文一甲。"], types=["header", "paragraph"]
        )
        _write_page_blocks(pages_dir, 3, ["正文一乙。"])
        _write_page_blocks(pages_dir, 4, ["正文二甲。"])
        _write_page_blocks(pages_dir, 5, ["正文二乙。"])
        _write_page_blocks(pages_dir, 6, [TestPointBudget._LONG])
        _write_page_blocks(pages_dir, 7, ["正文三乙。"])
        _write_page_blocks(pages_dir, 8, ["页脚噪音"], types=["footer"])
        _register_tree(
            session,
            [
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
                ("afkp-s12", "1.2 函数极限", 4, 2, "afkp-ch1"),
                ("afkp-s13", "1.3 极限的运算", 6, 2, "afkp-ch1"),
            ],
        )
        return pages_dir

    def test_blocks_feed_the_point_leg_and_small_sections_share_one_call(self, session, tmp_path):
        self._registered_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = _BudgetEngine()

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        calls = [payload for step, payload in engine.calls if step == "points"]
        merged = [call for call in calls if call.members]
        assert len(merged) == 1, "两枚小节点合成一笔调用"
        assert [member.node_id for member in merged[0].members] == ["afkp-s11", "afkp-s12"]
        assert [[block.text for block in member.blocks] for member in merged[0].members] == [
            ["正文一甲。", "正文一乙。"],
            ["正文二甲。", "正文二乙。"],
        ], "块取材：页眉页脚零入，按页序与块序"
        singles = [call for call in calls if not call.members]
        assert [(call.page_start, call.page_end) for call in singles] == [(6, 6), (6, 7)], (
            "超长块按句界切成两窗，第二窗接住余量与下一页"
        )
        assert all(
            sum(len(block.text) for block in call.blocks) + 2 * (len(call.blocks) - 1)
            <= CONTENT_INPUT_BUDGET_CHARS
            for call in calls
        ), "每笔材料正文都不超预算"
        assert (outcome.point_windows, outcome.point_merges) == (1, 1)

        rows = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert rows["afkp-s11"].summary == "总结：1.1 数列极限"
        assert rows["afkp-s12"].summary == "总结：1.2 函数极限"
        assert rows["afkp-s13"].summary == "总结：1.3 极限的运算#1\n\n总结：1.3 极限的运算#2", (
            "多窗口的节总结按窗口序拼合"
        )
        result = kp_batch_row(accepted, outcome).result
        assert (result["point_windows"], result["point_merges"]) == (1, 1)
        assert result["outline_summary_rows"] == 3

    def test_a_failed_window_voids_the_whole_section_and_only_failed_retries_it(self, session, tmp_path):
        self._registered_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=_BudgetEngine(fail_window=("1.3 极限的运算", 2)),
            acceptance=accepted,
        )
        OperationRepository(session).create_kp_batch(kp_batch_row(accepted, outcome))
        session.commit()
        session.expire_all()

        assert outcome.failed_sections == 1
        detail = outcome.failed_section_details[0]
        assert (detail["node_id"], detail["page_start"], detail["page_end"]) == (
            "afkp-s13",
            6,
            8,
        )
        rows = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert rows["afkp-s11"].summary == "总结：1.1 数列极限"
        assert rows["afkp-s12"].summary == "总结：1.2 函数极限"
        assert rows["afkp-s13"].summary == "", "任一窗口失败＝整节零行，已收回的半份总结也丢掉"

        retry = accept_kp_job(session, book_id="b05", stage="content", only_failed=True)
        retry_engine = _BudgetEngine()
        run_knowledge_job(
            session, data_root=tmp_path, engine=retry_engine, acceptance=retry
        )
        retry_calls = [payload for step, payload in retry_engine.calls if step == "points"]
        assert [(call.title, call.members) for call in retry_calls] == [
            ("1.3 极限的运算", ()),
            ("1.3 极限的运算", ()),
        ], "重跑只问名单内的节点，不重跑合批组"


class TestMaterialRanges:
    """`KA-66`：内容批次把本次实际送入的块区间写进在册点行的 `meta.material_ranges`。

    区间是复核增强位：与调用块集合逐块相等、失败节零枚、章行与 `full`／`exercise` 通路不写。
    """

    def test_ranges_are_the_blocks_sent_and_counted(self, session, tmp_path):
        """合批按成员各算各的区间，超预算节把两窗的块并成一张表；章行不写。"""
        TestPointBudget._registered_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b05", stage="content")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=_BudgetEngine(), acceptance=accepted
        )
        session.expire_all()

        rows = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert rows["afkp-s11"].meta["material_ranges"] == [
            {"page": 2, "block_indexes": [1]},
            {"page": 3, "block_indexes": [0]},
        ], "页眉零入；合批组的区间不串到邻点"
        assert rows["afkp-s12"].meta["material_ranges"] == [
            {"page": 4, "block_indexes": [0]},
            {"page": 5, "block_indexes": [0]},
        ]
        assert rows["afkp-s13"].meta["material_ranges"] == [
            {"page": 6, "block_indexes": [0]},
            {"page": 7, "block_indexes": [0]},
        ], "两窗送的是同一块的两段：并表后同页同块只记一次"
        assert rows["afkp-ch1"].meta == {"toc_role": "knowledge"}, "章腿不写取材区间"
        assert rows["afkp-s11"].meta["toc_role"] == "knowledge", "既有 meta 键逐键保留"
        assert outcome.material_ranges_rows == 3
        assert kp_batch_row(accepted, outcome).result["material_ranges_rows"] == 3

    def test_a_failed_section_writes_no_ranges(self, session, tmp_path):
        """任一窗口失败＝整节零行：已送出的半份区间也一并丢掉。"""
        TestPointBudget._registered_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b05", stage="content")

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=_BudgetEngine(fail_window=("1.3 极限的运算", 2)),
            acceptance=accepted,
        )
        session.expire_all()

        rows = {row.node_id: row for row in _rows(session, node_kind="outline")}
        assert "material_ranges" not in rows["afkp-s13"].meta
        assert outcome.material_ranges_rows == 2

    def test_the_full_leg_writes_no_ranges(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(
                points_by_section={"1.1 数列极限": [_point_draft("数列极限的定义", page=2)]}
            ),
            acceptance=_accepted(session),
        )

        assert outcome.material_ranges_rows == 0
        assert all("material_ranges" not in (row.meta or {}) for row in _rows(session))

    def test_the_exercise_leg_writes_no_ranges(self, session, tmp_path):
        _scene(session, tmp_path, page_count=8)
        _register_tree(
            session,
            [
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-ex1", "习题 1.1", 5, 2, "afkp-ch1"),
            ],
            metas={
                "afkp-ch1": {"toc_role": "knowledge"},
                "afkp-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            },
        )
        accepted = accept_kp_job(session, book_id="b05", stage="exercise")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )

        assert outcome.material_ranges_rows == 0
        assert all("material_ranges" not in (row.meta or {}) for row in _rows(session))


def _register_tree(
    session: Session,
    rows,
    *,
    narrative_refs=None,
    metas=None,
    book_id: str = "b05",
    job_id: str = "job-a1",
) -> None:
    """按（节点, 名字, 锚页, 层, 父）直接在册一棵双层目录树：章腿与重生成腿共用这台前提。

    走库里已有的目录行，不经目录腿：这一块守的是「拿到在册行之后」的行为，不是成行本身。
    """
    repo = KpRepository(session)
    for order, (node_id, name, page, depth, parent) in enumerate(rows, start=1):
        repo.create(
            KpNodeRow(
                node_id=node_id,
                book_id=book_id,
                name=name,
                norm_key=norm_key_for(name),
                node_kind="outline",
                job_id=job_id,
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


class TestExerciseStage:
    """`KA-53` 教材侧：既有习题段行下按行首题号逐题成点层行——规则分题、零模型调用、零文件。

    这一层守的是：闭集与单调序的边界只由规则给、题行与习题段同父、重跑只作废旧题行（不碰模型点），
    以及受理面「教材放行、习题集照旧拒」。
    """

    _ROWS = [
        ("afkp-ch1", "第 1 章 极限", 1, 1, None),
        ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
        ("afkp-ex1", "习题 1.1", 5, 2, "afkp-ch1"),
    ]
    _METAS = {
        "afkp-ch1": {"toc_role": "knowledge"},
        "afkp-s11": {"toc_role": "knowledge"},
        "afkp-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
    }

    def _accept(self, session: Session, tmp_path: Path, *, rows=None, metas=None) -> object:
        _scene(session, tmp_path, page_count=8)
        _register_tree(session, rows or self._ROWS, metas=metas or self._METAS)
        return accept_kp_job(session, book_id="b05", stage="exercise")

    def test_items_land_as_point_rows_under_the_exercise_segment(self, session, tmp_path):
        """题行父＝习题段行、`name`＝书自己写的题号、`meta.toc_kind='exercise'`、零模型调用与零文件。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(
            _pages_dir(tmp_path),
            5,
            [
                "【1】设 $a_n = 1/n$，求证 $\\lim a_n = 0$。",
                "（1）先证明有界。",
                "【2】证明伯努利不等式。",
                "【4】证明不等式。",
                "【3】这一枚题号回退，视为正文。",
                "1. 2017年，公司与上海华谊公司签署协议。",
            ],
            types=["paragraph", "paragraph", "paragraph", "paragraph", "paragraph", "footer"],
        )
        engine = FakeKnowledgeEngine()

        outcome = run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        items = _rows(session, node_kind="point")
        assert [row.name for row in items] == ["【1】", "【2】", "【4】"], "小问回退、页脚题号都不算新题"
        assert [row.order_hint for row in items] == [1, 2, 3]
        assert {row.parent_id for row in items} == {"afkp-ex1"}
        assert {row.node_kind for row in items} == {"point"}
        assert {row.model_ref for row in items} == {"rule:exercise_item"}
        assert {row.summary for row in items} == {""}
        assert {row.gen_status for row in items} == {"candidate"}
        assert [row.meta for row in items] == [{"toc_kind": "exercise"}] * 3
        assert all(row.job_id == "job-a1" for row in items)
        assert [row.anchors[0]["page"] for row in items] == [5, 5, 5]
        assert [row.anchors[0]["block_indexes"] for row in items] == [[0], [2], [3]]

        assert engine.calls == [], "教材侧分题零模型调用"
        assert (outcome.exercise_item_rows, outcome.point_rows, outcome.outline_rows) == (3, 0, 0)
        batch = kp_batch_row(accepted, outcome)
        assert batch.result["exercise_item_rows"] == 3
        assert set(batch.result) <= set(KP_RESULT_KEYS)
        assert not _kp_dir(tmp_path).exists(), "题行腿零文件"

    def test_numbering_restarts_per_exercise_segment(self, session, tmp_path):
        """跨段重置：两章的习题段各自从 1 起，单调序只在自己的父行下计。"""
        rows = [
            ("afkp-ch1", "第 1 章 极限", 1, 1, None),
            ("afkp-ex1", "习题 1.1", 2, 2, "afkp-ch1"),
            ("afkp-ch2", "第 2 章 微分", 5, 1, None),
            ("afkp-ex2", "习题 2.1", 6, 2, "afkp-ch2"),
        ]
        metas = {
            "afkp-ch1": {"toc_role": "knowledge"},
            "afkp-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            "afkp-ch2": {"toc_role": "knowledge"},
            "afkp-ex2": {"toc_role": "reference", "toc_kind": "exercise"},
        }
        accepted = self._accept(session, tmp_path, rows=rows, metas=metas)
        _write_page_blocks(_pages_dir(tmp_path), 2, ["【1】题一。", "【2】题二。"])
        _write_page_blocks(_pages_dir(tmp_path), 6, ["【1】新的一章重新起算。", "【2】题二。"])

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )
        session.expire_all()

        items = _rows(session, node_kind="point")
        assert outcome.exercise_item_rows == 4
        assert sorted((row.parent_id, row.name) for row in items) == [
            ("afkp-ex1", "【1】"),
            ("afkp-ex1", "【2】"),
            ("afkp-ex2", "【1】"),
            ("afkp-ex2", "【2】"),
        ]

    def test_a_segment_without_recognisable_numbers_lands_nothing(self, session, tmp_path):
        """识别不出即零行：披露不是失败，本段仍只有习题段那一行，批次结局照常按缺口判。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(_pages_dir(tmp_path), 5, ["求下列函数的极限。", "本题没有题号。"])

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )
        session.expire_all()

        assert _rows(session, node_kind="point") == []
        assert outcome.exercise_item_rows == 0
        assert _curation_result(session) == "completed"

    def test_a_rerun_supersedes_previous_item_rows_but_not_model_points(self, session, tmp_path):
        """重跑作废面只覆盖旧题行：另一批次的模型点行不是这一腿的过期读数（`KA-36` 的窄面）。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(_pages_dir(tmp_path), 5, ["【1】题一。", "【2】题二。"])
        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )
        stale = _draft(
            session,
            "afkp-model1",
            gen_status="candidate",
            node_kind="point",
            parent_id="afkp-s11",
            name="模型抽出的点",
            norm_key="模型抽出的点",
            anchors=[{"page": 2, "block_indexes": [0], "snippet": "回指正文"}],
        )
        session.commit()

        second = accept_kp_job(session, book_id="b05", stage="exercise")
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=second
        )
        session.expire_all()

        first_batch = [row for row in _rows(session, node_kind="point") if row.gen_batch == accepted.gen_batch]
        assert len(first_batch) == 2
        assert {row.gen_status for row in first_batch} == {"rejected"}
        assert session.get(AfKp, stale.node_id).gen_status == "candidate", "模型点行不在作废面里"
        assert outcome.superseded_rows == 2
        assert outcome.exercise_item_rows == 2

    def test_a_confirmed_item_row_is_never_overwritten(self, session, tmp_path):
        """`KA-13`：人已背书的题行挡机器新行；新行让位并计数，而不是覆盖已确认的结论。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(_pages_dir(tmp_path), 5, ["【1】题一。", "【2】题二。"])
        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )
        session.expire_all()
        endorsed = next(row for row in _rows(session, node_kind="point") if row.name == "【1】")
        KpRepository(session).set_confirmed(endorsed.node_id, confirmed=True)
        session.commit()

        second = accept_kp_job(session, book_id="b05", stage="exercise")
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=second
        )
        session.expire_all()

        kept = session.get(AfKp, endorsed.node_id)
        assert (kept.gen_status, kept.confirmed) == ("candidate", 1)
        assert outcome.let_away_rows == 1
        assert outcome.superseded_rows == 1, "未背书的那一枚旧题行照旧作废"


    def test_a_segment_anchored_on_a_toc_page_is_skipped_and_named(self, session, tmp_path):
        """`KA-43` 的同一条判据用在分题腿：锚还在目录页的习题段整段跳过并点名。

        这一批不跑目录腿，坏锚救不回正文页；照发会按「目录页起、到本章末」的派生区间，把前几章
        的题行重复挂到本段父行下。目录页的认法与内容腿同源（`toc_entries`），零模型调用。
        """
        rows = [
            ("afkp-ch1", "第 1 章 极限", 5, 1, None),
            ("afkp-ex1", "习题", 2, 2, "afkp-ch1"),
        ]
        metas = {
            "afkp-ch1": {"toc_role": "knowledge"},
            "afkp-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
        }
        accepted = self._accept(session, tmp_path, rows=rows, metas=metas)
        pages = _pages_dir(tmp_path)
        _write_page_artifacts(
            pages, 2, [], body="第 1 章 极限 …… 5\n1.1 数列极限 …… 6\n1.2 函数极限 …… 7"
        )
        # 派生区间里确有可成行的题号：跳过是判据，不是「本来就没有题」。
        _write_page_blocks(pages, 6, ["【1】题一。", "【2】题二。"])

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )
        session.expire_all()

        assert _rows(session, node_kind="point") == []
        assert outcome.exercise_item_rows == 0
        assert outcome.unlocated_outline_entries == ("习题",)
        assert kp_batch_row(accepted, outcome).result["unlocated_outline_entries"] == ["习题"]


class TestExerciseBookStage:
    """`KA-53` 习题集侧（独立模式）：规则定界、模型只读题面／解答、逐题一行一包。

    这一层守的是：题号只由切分器给（模型回的题号不得增删）、题行挂本书章／节行、题包三段恒定落
    `kp/<node_id>.md`、三态在编排层派生（不落库）、未匹配解答只披露不落行、失败域按段隔离，
    以及重跑只作废本腿（`llm:exercise`）的旧读数。章行区间覆盖其下各节的正文，故只对叶子行切段。
    """

    _ROWS = [
        ("afkp-b7ch1", "第 1 章 函数", 1, 1, None),
        ("afkp-b7s11", "1.1 函数与极限", 2, 2, "afkp-b7ch1"),
        ("afkp-b7ex1", "习题 1.1", 5, 2, "afkp-b7ch1"),
    ]
    _METAS = {
        "afkp-b7ch1": {"toc_role": "knowledge"},
        "afkp-b7s11": {"toc_role": "knowledge"},
        "afkp-b7ex1": {"toc_role": "reference", "toc_kind": "exercise"},
    }

    def _accept(self, session, tmp_path, *, rows=None, metas=None, page_count=8):
        _scene(
            session,
            tmp_path,
            book_id="b07",
            job_id="job-b1",
            page_count=page_count,
            roles=["exercises"],
        )
        _register_tree(
            session,
            rows or self._ROWS,
            metas=metas or self._METAS,
            book_id="b07",
            job_id="job-b1",
        )
        return accept_kp_job(session, book_id="b07", stage="exercise")

    def _pages(self, tmp_path: Path) -> Path:
        return _pages_dir(tmp_path, book_id="b07", job_id="job-b1")

    @staticmethod
    def _exercise_calls(engine: FakeKnowledgeEngine):
        return [section for step, section in engine.calls if step == "exercise"]

    def test_one_row_and_one_pack_per_split_item(self, session, tmp_path):
        """题号由规则定界：模型只填两段内容；行挂本节父行，题包三段恒定，三态随题落读数。"""
        accepted = self._accept(session, tmp_path)
        pages = self._pages(tmp_path)
        _write_page_blocks(pages, 5, ["1. 求极限。", "2. 证明伯努利不等式。"])
        # 页 5 追加一枚 image 块（REQ-179 R1：题包里真实出现的图片引用形态）：
        # blocks.json 带 `path`（image 块必需字段），页 md 由 blocks_to_markdown 同形生成。
        blocks_payload = json.loads((pages / "p0005.blocks.json").read_text(encoding="utf-8"))
        blocks_payload["blocks"].append(
            {
                "type": "image",
                "bbox": [10, 90, 700, 200],
                "index": 2,
                "order": 2,
                "path": "images/abc123.jpg",
                "caption": "图1",
            }
        )
        (pages / "p0005.blocks.json").write_text(
            json.dumps(blocks_payload, ensure_ascii=False), encoding="utf-8"
        )
        (pages / "p0005.md").write_text(
            "1. 求极限。\n\n![图1](pages/images/abc123.jpg)\n\n2. 证明伯努利不等式。",
            encoding="utf-8",
        )
        _write_page_blocks(pages, 6, ["3. 讨论连续性。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    # 题面按真实引擎（kp_gateway）的程序切片形态回原文（含图片引用行）；
                    # REQ-179 R1 断言的就是这份原文在题包写入时被改写成 8902 读腿 URL。
                    ExerciseDraft(
                        "1",
                        problem="1. 求极限。\n\n![图1](pages/images/abc123.jpg)",
                        solution="夹逼得 0。",
                    ),
                    ExerciseDraft("2", problem="证明伯努利不等式。", solution=""),
                ]
            }
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        rows = _rows(session, node_kind="point", book_id="b07")
        assert [row.name for row in rows] == ["1.", "2.", "3."], "name＝书自己写的题号"
        assert [row.norm_key for row in rows] == ["1", "2", "3"]
        assert [row.order_hint for row in rows] == [1, 2, 3]
        assert {row.parent_id for row in rows} == {"afkp-b7ex1"}
        assert {row.model_ref for row in rows} == {"llm:exercise"}
        assert {row.summary for row in rows} == {""}
        assert {row.gen_status for row in rows} == {"candidate"}
        assert [row.meta for row in rows] == [
            {"toc_kind": "exercise", "source_book_id": "b07", "source_pages": [5]},
            {"toc_kind": "exercise", "source_book_id": "b07", "source_pages": [5]},
            # 末题没有下一枚题号收尾：它正文一路到本节末页，页区间如实跟着走。
            {"toc_kind": "exercise", "source_book_id": "b07", "source_pages": [6, 8]},
        ]
        assert [row.anchors[0]["page"] for row in rows] == [5, 5, 6]

        calls = self._exercise_calls(engine)
        assert [section.title for section in calls] == ["习题 1.1"], "一节一次调用"
        assert [item.item_no for item in calls[0].items] == ["1", "2", "3"]

        packs = [_kp_dir(tmp_path, "b07") / f"{row.node_id}.md" for row in rows]
        assert all(path.exists() for path in packs)
        first = packs[0].read_text(encoding="utf-8")
        assert first.startswith("# 1.\n\n## 题目\n\n")
        assert "\n\n## 解答\n\n夹逼得 0。\n" in first
        assert first.rstrip().endswith("## 来源\n\n《数学分析原理》p.5")
        # REQ-179 R2：两种缺解答要可区分——题 2 有草稿但解答空＝真没配到（占位句保留）；
        # 题 3 模型未回草稿＝判定失败（写「解答不可用」标记，不再把占位句固化成正文）。
        assert "## 解答\n\n未配到解答" in packs[1].read_text(encoding="utf-8")
        assert "## 解答\n\n解答不可用（模型判定失败）" in packs[2].read_text(encoding="utf-8")
        assert "未配到解答" not in packs[2].read_text(encoding="utf-8")
        # REQ-179 R1：题包里的图片引用在**写入面**改写成 8902 可代理取的 URL（8900 字节转发，
        # 前端按 API_BASE 前缀取），不再是 8903 下取不到的产物相对路径。
        rewritten = packs[0].read_text(encoding="utf-8")
        assert "![图1](/api/v1/books/b07/images/abc123.jpg)" in rewritten
        assert "pages/images/abc123.jpg" not in rewritten
        assert [row.narrative_ref for row in rows] == [path.name for path in packs]

        assert (outcome.exercise_item_rows, outcome.exercise_pack_rows) == (3, 3)
        batch = kp_batch_row(accepted, outcome)
        assert batch.result["exercise_pack_rows"] == 3
        assert batch.result["merged_exercise_rows"] == 0
        assert batch.result["unmatched_solutions"] == 0
        assert set(batch.result) <= set(KP_RESULT_KEYS)

    def test_answers_from_the_running_header_section_fill_blank_solutions(
        self, session, tmp_path
    ):
        """答案段回配（PLAN-036 W11）：书尾「答案」连续页按题号补空白解答，只填不猜。"""
        rows = [
            *self._ROWS,
            ("afkp-b7ans", "答案", 8, 1, None),
        ]
        metas = {
            **self._METAS,
            "afkp-b7ans": {"toc_role": "reference", "toc_kind": "back_matter"},
        }
        accepted = self._accept(session, tmp_path, rows=rows, metas=metas)
        pages = self._pages(tmp_path)
        _write_page_blocks(pages, 5, ["1. 题一。", "2. 题二。"])
        _write_page_blocks(pages, 8, ["答案", "1. 解一。 2. 解二。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题一", solution=""),
                    ExerciseDraft("2", problem="题二", solution=""),
                ]
            }
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        item_rows = _rows(session, node_kind="point", book_id="b07")
        assert [row.name for row in item_rows] == ["1.", "2."]
        packs = [
            (_kp_dir(tmp_path, "b07") / f"{row.node_id}.md").read_text(encoding="utf-8")
            for row in item_rows
        ]
        assert "## 解答\n\n解一。" in packs[0]
        assert "## 解答\n\n解二。" in packs[1]
        assert outcome.exercise_pack_rows == 2
        reading = exercise_workspace(
            session, data_root=tmp_path, book=BookRepository(session).get("b07")
        )
        assert reading is not None
        assert [item["status"] for item in reading.items] == ["complete", "complete"]

    def test_independent_mode_verifies_after_answer_backfill(self, session, tmp_path):
        """`KA-70` 独立模式：答案回填之后按顶层章成一问，回填出的解答进校验材料。

        三态随题行落 `meta`：`reject` 照写不钳、note 原样、无 note 的行不造空键。
        """
        rows = [
            *self._ROWS,
            ("afkp-b7ans", "答案", 8, 1, None),
        ]
        metas = {
            **self._METAS,
            "afkp-b7ans": {"toc_role": "reference", "toc_kind": "back_matter"},
        }
        accepted = self._accept(session, tmp_path, rows=rows, metas=metas)
        pages = self._pages(tmp_path)
        _write_page_blocks(pages, 5, ["1. 题一。", "2. 题二。"])
        _write_page_blocks(pages, 8, ["答案", "1. 解一。 2. 解二。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题一", solution=""),
                    ExerciseDraft("2", problem="题二", solution=""),
                ]
            },
            exercise_verify_reply=ExerciseVerifyReply(
                items=(
                    ExerciseVerifyReading(item_no="1", verdict="reject", note="解答从第 2 题起"),
                    ExerciseVerifyReading(item_no="2", verdict="ok"),
                )
            ),
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        verify_calls = [
            request for step, request in engine.calls if step == "exercise_verify"
        ]
        assert [request.title for request in verify_calls] == ["第 1 章 函数"], "按顶层章交问"
        material = {item.item_no: (item.problem, item.solution) for item in verify_calls[0].items}
        assert material == {"1": ("题一", "解一。"), "2": ("题二", "解二。")}, "回填后的解答进校验"

        item_rows = _rows(session, node_kind="point", book_id="b07")
        assert [row.meta.get("verify_state") for row in item_rows] == ["done", "done"]
        assert item_rows[0].meta["verify_verdict"] == "reject"
        assert item_rows[0].meta["verify_note"] == "解答从第 2 题起"
        assert item_rows[1].meta["verify_verdict"] == "ok"
        assert "verify_note" not in item_rows[1].meta

    def test_a_verification_failure_only_marks_the_row_failed(self, session, tmp_path):
        """校验失败是披露（`KA-70`）：行照出、配对不动，只记 `verify_state='failed'`。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["1. 求极限。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="求 $\\lim 1/n$。", solution="夹逼得 0。")]
            },
            failing_exercise_verify=True,
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        (row,) = _rows(session, node_kind="point", book_id="b07")
        assert row.gen_status == "candidate"
        assert row.meta["verify_state"] == "failed"
        assert "verify_verdict" not in row.meta and "verify_note" not in row.meta
        assert outcome.exercise_item_rows == 1, "校验失败不计进失败节"
        verify_calls = [
            request for step, request in engine.calls if step == "exercise_verify"
        ]
        assert len(verify_calls) == 2, "坏轮只在同轮重试一次，不无限重试"

    def test_duplicate_item_numbers_within_a_chapter_are_judged_once(
        self, session, tmp_path
    ):
        """本书各节各自起号：同章撞号只把首个交问，后一枚不猜、不给读数。"""
        rows = [
            ("afkp-b7ch1", "第 1 章 函数", 1, 1, None),
            ("afkp-b7ex1", "习题 1.1", 4, 2, "afkp-b7ch1"),
            ("afkp-b7ex2", "习题 1.2", 6, 2, "afkp-b7ch1"),
        ]
        metas = {
            "afkp-b7ch1": {"toc_role": "knowledge"},
            "afkp-b7ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            "afkp-b7ex2": {"toc_role": "reference", "toc_kind": "exercise"},
        }
        accepted = self._accept(session, tmp_path, rows=rows, metas=metas)
        pages = self._pages(tmp_path)
        _write_page_blocks(pages, 4, ["1. 题一。"])
        _write_page_blocks(pages, 6, ["1. 题二甲。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="题一", solution="解一。")],
                "习题 1.2": [ExerciseDraft("1", problem="题二甲", solution="解二甲。")],
            },
            exercise_verify_reply=ExerciseVerifyReply(
                items=(ExerciseVerifyReading(item_no="1", verdict="suspect", note="两处撞号"),)
            ),
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        verify_calls = [
            request for step, request in engine.calls if step == "exercise_verify"
        ]
        assert [item.item_no for item in verify_calls[0].items] == ["1"], "撞号只交问首个"
        item_rows = _rows(session, node_kind="point", book_id="b07")
        states = {row.meta["source_pages"][0]: row.meta.get("verify_state") for row in item_rows}
        assert states == {4: "done", 6: None}, "后一枚撞号不给读数"
        judged = next(row for row in item_rows if row.meta.get("verify_state") == "done")
        assert judged.meta["verify_verdict"] == "suspect"

    def test_bracketed_reply_item_numbers_are_folded_before_matching(self, session, tmp_path):
        """配对机械失败（REQ-178）：模型把 `[N]`／`（N）` 照回时先折键再匹配，读数照常落行。

        b07 的 662 笔审核失败里 472 笔是 `[N]`、98 笔是 `（N）`——书名一侧的键是裸的，
        两侧不归一就全部对不上。
        """
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["1. 题一。", "2. 题二。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题一", solution="解一。"),
                    ExerciseDraft("2", problem="题二", solution="解二。"),
                ]
            },
            exercise_verify_reply=ExerciseVerifyReply(
                items=(
                    ExerciseVerifyReading(item_no="[1]", verdict="ok"),
                    ExerciseVerifyReading(item_no="（2）", verdict="suspect", note="解答半截"),
                )
            ),
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        item_rows = _rows(session, node_kind="point", book_id="b07")
        assert [row.meta.get("verify_state") for row in item_rows] == ["done", "done"]
        assert [row.meta.get("verify_verdict") for row in item_rows] == ["ok", "suspect"]
        assert item_rows[1].meta["verify_note"] == "解答半截"

    def test_a_broken_verify_round_is_retried_once(self, session, tmp_path):
        """坏轮同轮重试一次（REQ-178）：92 笔坏 JSON／空回复不再整块记 failed。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["1. 题一。"])
        attempts: list[str] = []

        def flaky(request):
            attempts.append(request.title)
            if len(attempts) == 1:
                raise EngineError("模型回了坏 JSON")
            return ExerciseVerifyReply(
                items=(ExerciseVerifyReading(item_no="1", verdict="ok"),)
            )

        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="题一", solution="解一。")]
            },
            on_exercise_verify=flaky,
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert len(attempts) == 2, "坏轮只重试一次"
        (row,) = _rows(session, node_kind="point", book_id="b07")
        assert row.meta["verify_state"] == "done"
        assert row.meta["verify_verdict"] == "ok"

    def test_a_zero_reading_round_is_retried_once(self, session, tmp_path):
        """回了但一枚域内读数都没有＝坏轮（REQ-178）：同轮重试，第二次正常即算 done。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["1. 题一。"])
        attempts: list[int] = []

        def blank_first(request):
            attempts.append(1)
            if len(attempts) == 1:
                return ExerciseVerifyReply(items=())
            return ExerciseVerifyReply(
                items=(ExerciseVerifyReading(item_no="1", verdict="ok"),)
            )

        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="题一", solution="解一。")]
            },
            on_exercise_verify=blank_first,
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert len(attempts) == 2
        (row,) = _rows(session, node_kind="point", book_id="b07")
        assert row.meta["verify_state"] == "done"
        assert row.meta["verify_verdict"] == "ok"

    def test_a_model_draft_outside_the_split_is_disclosed_not_stored(self, session, tmp_path):
        """未匹配只披露（`KA-53`）：模型多回的题号不落行、不写题包，缺答的题照切分补行。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["1. 题一。", "2. 题二。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题一", solution="解一"),
                    ExerciseDraft("9", problem="模型自造的一题", solution="自造的解答"),
                ]
            }
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        rows = _rows(session, node_kind="point", book_id="b07")
        assert [row.name for row in rows] == ["1.", "2."], "9 号不落行"
        assert outcome.unmatched_solutions == 1
        detail = outcome.unmatched_solution_details[0]
        assert (detail["item_no"], detail["reason"]) == ("9", "题号不在本节切分内")
        batch = kp_batch_row(accepted, outcome)
        assert batch.result["unmatched_solutions"] == 1
        assert batch.result["unmatched_solution_details"] == [detail]

    def test_a_segment_without_recognisable_numbers_costs_no_call(self, session, tmp_path):
        """一段题号也认不出⇒零调用零行：两段（正文节与习题段）各自只计披露，结局照常 completed。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["求下列函数的极限。", "本题没有题号。"])
        engine = FakeKnowledgeEngine()

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert [step for step, _ in engine.calls] == [], "没有可切的题就不发问"
        assert [row for row in _rows(session, node_kind="point", book_id="b07")] == []
        assert (outcome.exercise_item_rows, outcome.exercise_pack_rows) == (0, 0)
        assert _curation_result(session, "b07") == "completed"

    def test_one_failed_segment_leaves_the_other_segment_intact(self, session, tmp_path):
        """失败域按段：一段模型失败只让这段零行、明细点名，另一段照出；结局按缺口判 partial。"""
        rows = [
            ("afkp-b7ch1", "第 1 章 函数", 1, 1, None),
            ("afkp-b7ex1", "习题 1.1", 5, 2, "afkp-b7ch1"),
            ("afkp-b7ex2", "习题 1.2", 7, 2, "afkp-b7ch1"),
        ]
        metas = {
            "afkp-b7ch1": {"toc_role": "knowledge"},
            "afkp-b7ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            "afkp-b7ex2": {"toc_role": "reference", "toc_kind": "exercise"},
        }
        accepted = self._accept(session, tmp_path, rows=rows, metas=metas)
        pages = self._pages(tmp_path)
        _write_page_blocks(pages, 5, ["1. 题一。"])
        _write_page_blocks(pages, 7, ["1. 题二。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={"习题 1.2": [ExerciseDraft("1", problem="题二", solution="解二")]},
            failing_exercise=("习题 1.1",),
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert [row.parent_id for row in _rows(session, node_kind="point", book_id="b07")] == [
            "afkp-b7ex2"
        ]
        assert outcome.failed_sections == 1
        assert outcome.failed_section_details[0]["node_id"] == "afkp-b7ex1"
        assert _curation_result(session, "b07") == "partial"

    def test_a_long_section_is_split_into_rounds_and_merged(self, session, tmp_path):
        """超长节按体量分轮（每轮正文 ≤ 预算）：切分只做一次，行与题包按原题号不重不漏。

        真机证据（2026-11-07，b07 整本）：一节 prompt 4.5 万字，单轮生成被
        `finish_reason=length` 截断，整节零行——分轮把「节太大」从失败改成按轮落地。
        """
        accepted = self._accept(session, tmp_path)
        body = "长" * 1200
        _write_page_blocks(
            self._pages(tmp_path), 5, [f"{index}. {body}" for index in range(1, 13)]
        )
        rounds: list[list[str]] = []

        def on_exercise(section):
            rounds.append([item.item_no for item in section.items])
            return ExerciseReply(
                tuple(
                    ExerciseDraft(item.item_no, problem=f"题面{item.item_no}", solution="解")
                    for item in section.items
                )
            )

        engine = FakeKnowledgeEngine(on_exercise=on_exercise)
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert len(rounds) > 1, "超预算必然分轮"
        assert [no for chunk in rounds for no in chunk] == [
            str(index) for index in range(1, 13)
        ], "分轮不重不漏、题号仍是同一次切分的产物"
        body_chars = [
            sum(len(item.text) for item in section.items)
            for step, section in engine.calls
            if step == "exercise"
        ]
        assert max(body_chars) <= EXERCISE_CHUNK_TEXT_BUDGET

        rows = _rows(session, node_kind="point", book_id="b07")
        assert [row.norm_key for row in rows] == [str(index) for index in range(1, 13)]
        assert all(row.narrative_ref for row in rows), "每一轮回的题都落了题包"
        assert outcome.exercise_item_rows == 12
        assert outcome.unmatched_solutions == 0
        assert outcome.failed_sections == 0

    def test_a_failed_round_is_disclosed_while_the_other_rounds_still_land(
        self, session, tmp_path
    ):
        """一轮失败只丢这一轮：其余轮的题照落行，失败轮的点以 `source_missing` 进待重解析。

        `KA-53` 的失败域是节而不是轮——同一节里成功的轮不能被失败轮连坐成零行；但丢的题必须
        如实挡完成（题包写指针句 ⇒ `source_missing` ⇒ `done=false`），不许静默算齐。
        """
        accepted = self._accept(session, tmp_path)
        body = "长" * 1200
        _write_page_blocks(
            self._pages(tmp_path), 5, [f"{index}. {body}" for index in range(1, 13)]
        )

        def on_exercise(section):
            if any(item.item_no == "1" for item in section.items):
                raise EngineError("模型响应不可用：响应被 max_tokens 截断（finish_reason=length）")
            return ExerciseReply(
                tuple(
                    ExerciseDraft(item.item_no, problem=f"题面{item.item_no}", solution="解")
                    for item in section.items
                )
            )

        engine = FakeKnowledgeEngine(on_exercise=on_exercise)
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        rows = _rows(session, node_kind="point", book_id="b07")
        assert len(rows) == 12, "成功轮的题不因同节另一轮失败而消失"
        assert all(row.narrative_ref for row in rows)
        assert outcome.failed_sections == 1
        assert outcome.failed_section_details[0]["node_id"] == "afkp-b7ex1"
        assert "max_tokens" in outcome.failed_section_details[0]["reason"]

        reading = exercise_workspace(
            session, data_root=tmp_path, book=BookRepository(session).get("b07")
        )
        assert reading is not None
        assert reading.pending_reparse >= 1, "失败轮的题是缺项，必须挡完成"
        assert reading.done is False

    def test_a_rerun_supersedes_only_this_legs_previous_rows(self, session, tmp_path):
        """重跑作废面只覆 `llm:exercise`：另一腿抽出的模型点行与教材侧的规则题行都不是本腿旧读数。"""
        accepted = self._accept(session, tmp_path)
        _write_page_blocks(self._pages(tmp_path), 5, ["1. 题一。", "2. 题二。"])
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题一", solution="解一"),
                    ExerciseDraft("2", problem="题二", solution="解二"),
                ]
            }
        )
        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()
        stale = _draft(
            session,
            "afkp-b7model1",
            book_id="b07",
            gen_status="candidate",
            gen_batch="afkp-oldmodel0",
            node_kind="point",
            parent_id="afkp-b7s11",
            name="模型抽出的点",
            norm_key="模型抽出的点",
            anchors=[{"page": 5, "block_indexes": [0], "snippet": "回指正文"}],
        )
        session.commit()

        second = accept_kp_job(session, book_id="b07", stage="exercise")
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=second
        )
        session.expire_all()

        first_batch = [
            row
            for row in _rows(session, node_kind="point", book_id="b07")
            if row.gen_batch == accepted.gen_batch
        ]
        assert len(first_batch) == 2
        assert {row.gen_status for row in first_batch} == {"rejected"}
        assert session.get(AfKp, stale.node_id).gen_status == "candidate", "模型点行不在作废面里"
        assert outcome.superseded_rows == 2
        assert outcome.exercise_item_rows == 2

    def test_an_outline_rerun_only_replaces_the_chapter_skeleton(self, session, tmp_path):
        """`KA-36` 收窄（W6）：目录重跑只作废目录行——题行是另一条腿的旧读数，不因目录重跑被判死。"""
        _scene(session, tmp_path, book_id="b07", job_id="job-b1", page_count=8, roles=["exercises"])
        # 独立型要有解答证据（`KA-71` B 门）：没有证据的习题集在预处理就红灯、零目录行。
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"), 8, ["证 由定义直接可得。"]
        )
        _register_tree(session, self._ROWS, metas=self._METAS, book_id="b07", job_id="job-b1")
        stale_item = _draft(
            session,
            "afkp-b7oldq1",
            book_id="b07",
            job_id="job-b1",
            gen_status="candidate",
            gen_batch="afkp-olditems0",
            node_kind="point",
            parent_id="afkp-b7ex1",
            depth=2,
            name="1.",
            norm_key="1",
            model_ref="llm:exercise",
            anchors=[
                {"kind": "page_block", "ref_id": "p0005", "page": 5, "block_indexes": [0], "snippet": "1. 题一。"}
            ],
        )
        session.commit()
        stale_item_rev = stale_item.rev

        accepted = accept_kp_job(session, book_id="b07", stage="outline")
        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=accepted
        )
        session.expire_all()

        assert session.get(AfKp, "afkp-b7ch1").gen_status == "rejected", "上一轮目录行是过期读数"
        item = session.get(AfKp, "afkp-b7oldq1")
        assert item.gen_status == "candidate", "题行与目录无关，不因目录重跑被判死"
        assert item.rev == stale_item_rev


class TestBookStageReading:
    """PLAN-029 W1 的判据纯函数：统计域、三条件与未定位只在这一处判，读时派生、零写库。"""

    @staticmethod
    def _row(
        node_id,
        *,
        depth=1,
        parent=None,
        meta=None,
        status="candidate",
        summary="总结",
        confirmed=1,
        anchors=None,
    ):
        return SimpleNamespace(
            node_id=node_id,
            parent_id=parent,
            depth=depth,
            meta={} if meta is None else meta,
            gen_status=status,
            summary=summary,
            confirmed=confirmed,
            anchors=[{"page": 5}] if anchors is None else anchors,
        )

    def test_all_counted_rows_endorsed_with_summaries_and_located_is_green(self):
        rows = [self._row("a1"), self._row("a2", depth=2, parent="a1")]
        reading = book_stage_reading(rows, toc_pages=(1, 2, 3))

        assert (
            reading.rows,
            reading.endorsed_rows,
            reading.blank_summaries,
            reading.unlocated_rows,
            reading.done,
        ) == (2, 2, 0, 0, True)

    def test_zero_counted_rows_is_never_green(self):
        assert book_stage_reading([], toc_pages=(1, 2)).done is False

    def test_light_green_ready_without_endorsement(self):
        """浅绿放行（REQ-170 追加请求 A）：机器完成即 `ready`，人工背书进度不挡配套链路。"""
        rows = [self._row("a1", confirmed=0), self._row("a2", depth=2, parent="a1", confirmed=0)]
        reading = book_stage_reading(rows, toc_pages=(1, 2, 3))

        assert (reading.endorsed_rows, reading.ready, reading.done) == (0, True, False)

    def test_ready_keeps_the_remaining_three_conditions(self):
        """浅绿只放宽「人工背书」一项：零行、目录页取不到、空总结、未定位照样不成浅绿。"""
        blank = [self._row("a1", summary="", confirmed=0)]
        unlocated = [self._row("a1", anchors=[{"page": 2}], confirmed=0)]

        assert book_stage_reading([], toc_pages=(1, 2)).ready is False
        assert book_stage_reading(blank, toc_pages=(1, 2, 3)).ready is False
        assert book_stage_reading(unlocated, toc_pages=(1, 2, 3)).ready is False
        assert (
            book_stage_reading([self._row("a1", confirmed=0)], toc_pages=None).ready is False
        )

    def test_an_unavailable_toc_page_set_is_never_green(self):
        """目录页集合取不到时宁可黄、不假绿：计数照出，合取判据恒假。"""
        rows = [self._row("a1"), self._row("a1-1", depth=2, parent="a1")]
        reading = book_stage_reading(rows, toc_pages=None)

        assert (reading.rows, reading.unlocated_rows, reading.done) == (2, 0, False)

    def test_a_first_level_row_without_point_children_stays_out_of_the_count(self):
        """`KA-68`：预备知识／附录这类行不再是章，也不进统计域——它们本来就不产总结。"""
        rows = [self._row("pre", summary="", confirmed=0)]

        assert book_stage_reading(rows, toc_pages=(1, 2, 3)).rows == 0

    def test_rejected_and_in_flight_rows_stay_out_of_the_count(self):
        rows = [
            self._row("a1"),
            self._row("a1-1", depth=2, parent="a1"),
            self._row("a2", status="draft"),
            self._row("a3", status="rejected"),
        ]

        assert book_stage_reading(rows, toc_pages=(1, 2, 3)).rows == 2

    def test_exercise_and_reference_rows_stay_out_of_the_count(self):
        """`KA-39`／`KA-43`：不产出总结的两类附件行进统计域会让带章末习题的书永不可能完成。"""
        rows = [
            self._row("a1"),
            self._row("a1-1", depth=2, parent="a1"),
            self._row("ex", meta={"toc_role": "reference", "toc_kind": "exercise"}),
            self._row("ref", meta={"toc_role": "reference"}),
        ]

        assert book_stage_reading(rows, toc_pages=(1, 2, 3)).rows == 2

    def test_an_anchor_inside_the_toc_page_set_is_unlocated(self):
        """「未定位」＝锚落在目录页集合内（条目从没落到正文页），与 `KA-33` 同义。"""
        reading = book_stage_reading(
            [
                self._row("a1", anchors=[{"page": 2}]),
                self._row("a1-1", depth=2, parent="a1", anchors=[{"page": 9}]),
            ],
            toc_pages=(1, 2, 3),
        )

        assert (reading.unlocated_rows, reading.done) == (1, False)

    def test_an_anchor_outside_the_toc_page_set_is_located(self):
        reading = book_stage_reading(
            [
                self._row("a1", anchors=[{"page": 9}]),
                self._row("a1-1", depth=2, parent="a1", anchors=[{"page": 9}]),
            ],
            toc_pages=(1, 2, 3),
        )

        assert (reading.unlocated_rows, reading.done) == (0, True)

    def test_rows_without_anchors_are_not_unlocated(self):
        """锚为空的**人工直填行**是「没有条目光凭」，不是「条目没找到页」。"""
        reading = book_stage_reading(
            [
                self._row("a1", anchors=[]),
                self._row("a1-1", depth=2, parent="a1", anchors=[]),
            ],
            toc_pages=(1, 2, 3),
        )

        assert (reading.unlocated_rows, reading.done) == (0, True)

    def test_the_toc_page_set_comes_only_from_the_matching_evidence_version(self, session):
        for batch, job, pages in (("afkp-b1", "job-a1", [1, 2]), ("afkp-b2", "job-b2", [7, 8])):
            OperationRepository(session).create_kp_batch(
                KpBatchRow(
                    gen_batch=batch,
                    book_id="b05",
                    job_id=job,
                    status="ok",
                    result={"outline_toc_pages": pages},
                )
            )
        session.commit()

        assert book_toc_pages(session, book_id="b05", job_id="job-a1") == (1, 2)
        assert book_toc_pages(session, book_id="b05", job_id="job-zz") is None


class TestExerciseBookAcceptance:
    """`KA-53` 受理面：配套模式与选中题重解析的组合门——执行体交付前整扇关住、零写入。"""

    def _scene(self, session, tmp_path):
        _scene(
            session,
            tmp_path,
            book_id="b07",
            job_id="job-b1",
            page_count=8,
            roles=["exercises"],
        )
        _register_tree(
            session,
            [
                ("afkp-b7ch1", "第 1 章 函数", 1, 1, None),
                ("afkp-b7ex1", "习题 1.1", 5, 2, "afkp-b7ch1"),
            ],
            metas={
                "afkp-b7ch1": {"toc_role": "knowledge"},
                "afkp-b7ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            },
            book_id="b07",
            job_id="job-b1",
        )

    def _textbook(self, session):
        _book(session, "b05")

    def _rows_before(self, session):
        return len(_rows(session, book_id="b07"))

    def _green_target(
        self, session, tmp_path, *, chapter="第 1 章 函数", summary="本章总结", endorse=True
    ):
        """整本绿的教材 b05：章行已确认且有总结、锚点落在目录页集合之外；目录页集合取自同版本流水。

        章行下挂一枚知识点子行：`KA-68` 起没有可装东西的一级知识行记「其他」，章身份要由子行撑起来。
        `endorse=False` 给的是浅绿读数：行与总结齐、只差人工背书。
        """
        _scene(session, tmp_path, book_id="b05", job_id="job-a1", page_count=8)
        _register_tree(
            session,
            [
                ("afkp-b05-ch1", chapter, 4, 1, None),
                ("afkp-b05-s11", "1.1 函数与映射", 4, 2, "afkp-b05-ch1"),
                ("afkp-b05-ex1", "习题 1.1", 5, 2, "afkp-b05-ch1"),
            ],
            metas={
                "afkp-b05-ch1": {"toc_role": "knowledge"},
                "afkp-b05-s11": {"toc_role": "knowledge"},
                "afkp-b05-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            },
            book_id="b05",
            job_id="job-a1",
        )
        repo = KpRepository(session)
        if summary:
            assert repo.set_outline_summary(
                book_id="b05", node_id="afkp-b05-ch1", summary=summary
            )
        if endorse:
            repo.set_confirmed("afkp-b05-ch1", confirmed=True)
        assert repo.set_outline_summary(
            book_id="b05", node_id="afkp-b05-s11", summary="定义域与值域。"
        )
        if endorse:
            repo.set_confirmed("afkp-b05-s11", confirmed=True)
        OperationRepository(session).create_kp_batch(
            KpBatchRow(
                gen_batch="afkp-green000001",
                book_id="b05",
                job_id="job-a1",
                status="ok",
                stage="content",
                result={"outline_toc_pages": [1, 2, 3]},
            )
        )
        session.commit()

    def test_the_gate_stays_closed_while_the_target_is_not_green(self, session, tmp_path):
        """整本绿门（`KA-53`）：目标零行即不绿，422 点名四项读数，且零写入。"""
        self._scene(session, tmp_path)
        self._textbook(session)
        before = self._rows_before(session)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        assert caught.value.status_code == 422
        assert "尚未达到浅绿" in caught.value.detail
        assert "在册 0 行" in caught.value.detail
        assert self._rows_before(session) == before

    def test_a_blank_summary_keeps_the_gate_closed(self, session, tmp_path):
        """严口径（PLAN-029 W1）：已确认但总结为空的书不算绿——空总结那一枚也要点名。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path, summary="")

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        assert caught.value.status_code == 422
        assert "空总结 1" in caught.value.detail

    def test_a_green_target_opens_the_gate(self, session, tmp_path):
        """整本绿的目标不再触发 422：受理结果把目标书带下去，执行体据此走跨书挂章。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path)

        accepted = accept_kp_job(
            session, book_id="b07", stage="exercise", target_book_id="b05"
        )

        assert accepted.target_book_id == "b05"
        assert accepted.exercise_retry_node_ids == ()

    def test_a_light_green_target_opens_the_gate(self, session, tmp_path):
        """浅绿放行（REQ-170 追加请求 A）：机器完成、人工未背书的目标同样进得了配套模式。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path, endorse=False)

        accepted = accept_kp_job(
            session, book_id="b07", stage="exercise", target_book_id="b05"
        )

        assert accepted.target_book_id == "b05"

    def test_the_gate_is_named_light_green_after_relaxation(self, session, tmp_path):
        """门名与口径同改：拒单措辞点名浅绿，四项读数照旧一并给出。"""
        self._scene(session, tmp_path)
        self._textbook(session)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        assert "尚未达到浅绿" in caught.value.detail
        assert "在册 0 行" in caught.value.detail

    def _companion_scene(
        self, session, tmp_path, *, target_chapter="第 1 章 函数", own_problem="1. 题一。"
    ):
        """配套提取的公共场景：本书一页一题、教材同一章一页一题（两侧页产物都要在位）。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path, chapter=target_chapter)
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b05", job_id="job-a1"), 5, ["1. 教材题一。"]
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"), 5, [own_problem]
        )

    def _added_rows(self, session, accepted):
        return [
            row
            for row in _rows(session, book_id="b07")
            if row.gen_batch == accepted.gen_batch and row.node_kind == "point"
        ]

    def _pack_of(self, tmp_path, row, book_id="b07"):
        return (
            _kp_dir(tmp_path, book_id)
            .joinpath(row.narrative_ref or "")
            .read_text(encoding="utf-8")
        )

    def _two_pair_scene(self, session, tmp_path, *, solution="解"):
        """两题两对：教材同页两题、本书同页两题，两侧页产物都在位。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path)
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b05", job_id="job-a1"),
            5,
            ["1. 教材题一。", "2. 教材题二。"],
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"),
            5,
            ["1. 题一。", "2. 题二。"],
        )
        return FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题面一", solution=f"{solution}一"),
                    ExerciseDraft("2", problem="题面二", solution=f"{solution}二"),
                ]
            }
        )

    def test_companion_extraction_writes_paired_rows_into_the_exercise_book(
        self, session, tmp_path
    ):
        """REQ-170：题面取教材、解答取本书，配对行落在**本书**自己的章下，教材一行也不得。"""
        self._companion_scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="习题集题面", solution="解一")]
            }
        )
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        added = self._added_rows(session, accepted)
        assert len(added) == 1
        row = added[0]
        assert (row.book_id, row.parent_id, row.gen_status) == ("b07", "afkp-b7ch1", "candidate")
        meta = row.meta or {}
        assert (meta["pair_state"], meta["target_item"], meta["target_book_id"]) == (
            "matched",
            "1",
            "b05",
        )
        assert meta["source_book_id"] == "b07"
        assert meta["source_pages"] == [5, 8], "解答页区间跟着本书切分走"
        assert row.anchors and row.anchors[0]["page"] == 5, "锚点只指本书本版"
        assert row.anchors[0]["block_indexes"] == [0]
        pack = self._pack_of(tmp_path, row)
        assert "1. 教材题一。" in pack, "题面取教材自身版本的正文"
        assert "习题集题面" not in pack, "模型读出的题面不冒充教材题面"
        assert "解一" in pack
        assert "（题面）" in pack and "（解答）" in pack, "来源两行分别点名题面与解答"
        assert [
            other for other in _rows(session, book_id="b05") if other.gen_batch == accepted.gen_batch
        ] == [], "教材不因配套提取新增任何行"
        assert (outcome.exercise_item_rows, outcome.exercise_pack_rows) == (1, 1)
        assert outcome.merged_exercise_rows == 0
        assert [step for step, _ in engine.calls if step == "exercise_classify"] == [], (
            "配套提取不再发分类读数腿"
        )

    def test_a_textbook_problem_without_a_book_solution_gets_an_unpaired_row(
        self, session, tmp_path
    ):
        """教材有题、本书没解答：行照样成（题面完整、解答留占位），状态点名「未收录」。"""
        self._companion_scene(session, tmp_path, own_problem="这页正文里没有题号。")
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        added = self._added_rows(session, accepted)
        assert len(added) == 1
        row = added[0]
        meta = row.meta or {}
        assert (meta["pair_state"], meta["pair_reason"]) == ("unpaired", "习题集未收录本题")
        assert row.anchors == [], "本书没有这一题的页，不编锚点"
        pack = self._pack_of(tmp_path, row)
        assert "1. 教材题一。" in pack and "未配到解答" in pack

    def test_an_extra_book_solution_becomes_an_unpaired_tail_row(self, session, tmp_path):
        """本书多出来的解答另占一行、按题序排在配对行之后：那是「下列可选解答」的一类。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path)
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b05", job_id="job-a1"), 5, ["1. 教材题一。"]
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"), 5, ["1. 题一。", "2. 题二。"]
        )
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [
                    ExerciseDraft("1", problem="题面一", solution="解一"),
                    ExerciseDraft("2", problem="题面二", solution="解二"),
                ]
            }
        )
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        added = sorted(self._added_rows(session, accepted), key=lambda row: row.order_hint or 0)
        assert [row.name for row in added] == ["1.", "2."]
        first, second = (row.meta or {} for row in added)
        assert (first["pair_state"], second["pair_state"]) == ("matched", "unpaired")
        assert second["pair_reason"] == "教材本章未收录本题"
        assert added[1].anchors[0]["block_indexes"] == [1], "第二题锚在它自己的题号块上"

    def test_a_failed_read_leaves_textbook_rows_with_a_named_reason(self, session, tmp_path):
        """解答腿失败不挡题行：教材题面照样成行，原因点名失败，结局如实 partial。"""
        self._companion_scene(session, tmp_path)
        engine = FakeKnowledgeEngine(failing_exercise=("习题 1.1",))
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        added = self._added_rows(session, accepted)
        assert len(added) == 1
        meta = added[0].meta or {}
        assert (meta["pair_state"], meta["pair_reason"]) == ("unpaired", "本章解答读取失败")
        assert outcome.failed_sections == 1
        assert _curation_result(session, "b07") == "partial"
        # REQ-179 R2：解答腿失败＝判定不可用 → 题包写显式标记，不再把占位句固化成正文
        pack = self._pack_of(tmp_path, added[0])
        assert "解答不可用（模型判定失败）" in pack
        assert "未配到解答" not in pack

    def test_a_target_chapter_missing_from_the_book_is_disclosed(self, session, tmp_path):
        """教材章在本书目录里没有位置：只披露，本书自己的题按未配对行留下。"""
        self._companion_scene(session, tmp_path, target_chapter="第 2 章 别的")
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="题面", solution="解一")]
            }
        )
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        reasons = [str(detail.get("reason")) for detail in outcome.unmatched_solution_details]
        assert any("第 2 章" in reason for reason in reasons)
        added = self._added_rows(session, accepted)
        assert len(added) == 1
        meta = added[0].meta or {}
        assert (meta["pair_state"], meta["pair_reason"]) == ("unpaired", "教材本章习题段取不到")

    def test_the_verifier_judges_each_packed_pair_and_records_the_reading(
        self, session, tmp_path
    ):
        """REQ-170 第三步：LLM 逐对判「切分是否正确、题面与解答是否对应」，读数进题行 meta。

        判的是**落行的那两段**：题面取教材原文、解答取本书切分，读数不改变配对与成行。
        """
        engine = self._two_pair_scene(session, tmp_path)
        engine.exercise_verify_reply = ExerciseVerifyReply(
            items=(
                ExerciseVerifyReading(item_no="1", verdict="ok"),
                ExerciseVerifyReading(
                    item_no="2", verdict="suspect", note="解答像是下一题的开头"
                ),
            )
        )
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        requests = [payload for step, payload in engine.calls if step == "exercise_verify"]
        assert len(requests) == 1, "同一章的对一次判完"
        request = requests[0]
        assert request.title == "第 1 章 函数"
        assert [item.item_no for item in request.items] == ["1", "2"]
        assert request.items[0].problem.startswith("1. 教材题一。"), "判的是教材题面"
        assert request.items[0].solution.startswith("解一")
        rows = sorted(self._added_rows(session, accepted), key=lambda row: row.order_hint or 0)
        first, second = (row.meta or {} for row in rows)
        assert (first["verify_state"], first.get("verify_verdict")) == ("done", "ok")
        assert (second["verify_state"], second.get("verify_verdict")) == ("done", "suspect")
        assert second["verify_note"] == "解答像是下一题的开头"
        assert (first["pair_state"], second["pair_state"]) == ("matched", "matched"), (
            "读数只披露，不否决成行"
        )

    def test_a_failed_verification_leaves_the_rows_and_names_the_failure(
        self, session, tmp_path
    ):
        """校验腿失败不否决成行：配对行照出，行上点名失败，结局不被它拉成 partial。"""
        engine = self._two_pair_scene(session, tmp_path)
        engine.failing_exercise_verify = True
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        rows = self._added_rows(session, accepted)
        assert len(rows) == 2
        assert all((row.meta or {})["verify_state"] == "failed" for row in rows)
        assert all((row.meta or {})["pair_state"] == "matched" for row in rows)
        assert outcome.failed_sections == 0

    def test_an_engine_without_the_verifier_writes_no_verify_keys(self, session, tmp_path):
        """引擎没有校验腿：题行不长出 `verify_*` 空键——旧引擎的行形状不因新腿出现而变。"""
        engine = self._two_pair_scene(session, tmp_path)
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        rows = self._added_rows(session, accepted)
        assert len(rows) == 2
        assert all(
            not any(key.startswith("verify_") for key in (row.meta or {})) for row in rows
        )

    def test_a_long_chapter_is_verified_in_rounds(self, session, tmp_path):
        """一章几十题：按正文预算分轮交模型，每轮只带本轮的对（不压进一次生成）。"""
        engine = self._two_pair_scene(session, tmp_path, solution="解" * 7000)
        engine.on_exercise_verify = lambda request: ExerciseVerifyReply(
            items=tuple(
                ExerciseVerifyReading(item_no=item.item_no, verdict="ok")
                for item in request.items
            )
        )
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        requests = [payload for step, payload in engine.calls if step == "exercise_verify"]
        assert [len(request.items) for request in requests] == [1, 1]
        assert [request.items[0].item_no for request in requests] == ["1", "2"]

    def test_the_outline_batch_remembers_the_target_for_the_exercise_batch(
        self, session, tmp_path
    ):
        """目录腿命中的教材目标落进来源书清单，随后的习题腿不再要求人重带目标。"""
        self._scene(session, tmp_path)
        self._green_target(session, tmp_path)
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b05", job_id="job-a1"), 5, ["1. 教材题一。"]
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"), 5, ["1. 题一。", "解 答案一。"]
        )
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "第 1 章 函数": [ExerciseDraft("1", problem="题面", solution="解一")]
            }
        )
        outline = accept_kp_job(session, book_id="b07", stage="outline")
        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=outline)
        accepted = accept_kp_job(session, book_id="b07", stage="exercise")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        added = self._added_rows(session, accepted)
        assert len(added) == 1, "目录批记得目标，习题批按配套提取落行"
        assert (added[0].meta or {})["target_book_id"] == "b05"
        assert (added[0].meta or {})["pair_state"] == "matched"

    def test_the_companion_target_must_exist_and_be_a_textbook(self, session, tmp_path):
        self._scene(session, tmp_path)

        with pytest.raises(KpGateError) as missing:
            accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b99")
        assert (missing.value.status_code, "不存在" in missing.value.detail) == (422, True)

        _scene(
            session,
            tmp_path,
            book_id="b08",
            job_id="job-b8",
            page_count=4,
            roles=["exercises"],
        )
        with pytest.raises(KpGateError) as wrong_kind:
            accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b08")
        assert wrong_kind.value.status_code == 422
        assert "教材身份" in wrong_kind.value.detail

    def test_the_companion_target_in_flight_is_a_conflict(self, session, tmp_path):
        self._scene(session, tmp_path)
        self._textbook(session)
        _draft(session, "afkp-b05-draft", gen_status="draft")
        session.commit()

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        assert caught.value.status_code == 409
        assert "在飞" in caught.value.detail

    def test_the_companion_target_is_rejected_on_any_other_stage(self, session, tmp_path):
        """教材带目标即「让教材合并别人的题」、习题集的目录腿带目标即自相矛盾，均 422。"""
        self._scene(session, tmp_path)
        self._textbook(session)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b07", stage="outline", target_book_id="b05")
        assert caught.value.status_code == 422
        assert "只与习题集的习题梳理同批" in caught.value.detail

        _register_tree(session, [("afkp-b05-ch1", "第 1 章 极限", 1, 1, None)])
        with pytest.raises(KpGateError) as textbook_with_target:
            accept_kp_job(session, book_id="b05", stage="exercise", target_book_id="b05")
        assert textbook_with_target.value.status_code == 422

    def _retry_item(
        self,
        session,
        tmp_path,
        node_id,
        *,
        book_id="b07",
        parent="afkp-b7ch1",
        name="1. 题一",
        problem="旧题面",
        solution="旧解一",
        source_book_id="b07",
        page=2,
        confirmed=False,
    ):
        """待修补的一枚未确认机器行：来源书＝本书、题包已写（内容待重解析）。"""
        row = _draft(
            session,
            node_id,
            book_id=book_id,
            job_id="job-b1",
            node_kind="point",
            parent_id=parent,
            name=name,
            norm_key=norm_key_for(name),
            anchors=[
                {"kind": "page_block", "page": page, "block_indexes": [0], "snippet": problem}
            ],
            meta={
                "toc_kind": "exercise",
                "source_book_id": source_book_id,
                "source_pages": [page],
            },
            gen_status="candidate",
        )
        content = exercise_pack_text(
            label=name, problem=problem, solution=solution, source_lines=["《数学分析原理》p.2"]
        )
        write_narrative(session, data_root=tmp_path, node_id=node_id, content=content)
        session.commit()
        return row

    def test_selected_retry_rejects_rows_outside_the_owned_row_set(self, session, tmp_path):
        """逐枚校验：行不在归属书、来源书不是本书、已确认——任一不成立 422 零写入。"""
        self._scene(session, tmp_path)
        before = self._rows_before(session)

        with pytest.raises(KpGateError) as missing:
            accept_kp_job(
                session, book_id="b07", stage="exercise", exercise_retry_node_ids=("afkp-nope",)
            )
        assert missing.value.status_code == 422
        assert "不在归属书" in missing.value.detail

        with pytest.raises(KpGateError) as foreign:
            accept_kp_job(
                session, book_id="b07", stage="exercise", exercise_retry_node_ids=("afkp-b7ex1",)
            )
        assert "来源书不是本书" in foreign.value.detail
        assert self._rows_before(session) == before

        _draft(
            session,
            "afkp-b7q1",
            book_id="b07",
            job_id="job-b1",
            node_kind="point",
            parent_id="afkp-b7ch1",
            name="1. 题一",
            norm_key=norm_key_for("1. 题一"),
            meta={"toc_kind": "exercise", "source_book_id": "b07"},
            gen_status="candidate",
        )
        KpRepository(session).set_confirmed("afkp-b7q1", confirmed=True)
        session.commit()
        with pytest.raises(KpGateError) as confirmed:
            accept_kp_job(
                session, book_id="b07", stage="exercise", exercise_retry_node_ids=("afkp-b7q1",)
            )
        assert "已确认" in confirmed.value.detail
        assert self._rows_before(session) == before + 1, "拒行不改行数"

    def test_a_selected_retry_rewrites_only_the_selected_pack(self, session, tmp_path):
        """修补只改写选中的那几枚：题号照旧、未选中的题包包内容不变、不新成行。"""
        self._scene(session, tmp_path)
        self._retry_item(session, tmp_path, "afkp-b7q1", name="1. 题一", solution="旧解一")
        self._retry_item(session, tmp_path, "afkp-b7q2", name="2. 题二", solution="旧解二", page=3)
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"),
            2,
            ["1. 题一题面", "2. 题二题面"],
        )
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "第 1 章 函数": [
                    ExerciseDraft("1", problem="新题面", solution="新解一"),
                    ExerciseDraft("2", problem="题二题面", solution="解二"),
                ]
            }
        )
        accepted = accept_kp_job(
            session, book_id="b07", stage="exercise", exercise_retry_node_ids=("afkp-b7q1",)
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        directory = _kp_dir(tmp_path, "b07")
        first = (directory / "afkp-b7q1.md").read_text(encoding="utf-8")
        second = (directory / "afkp-b7q2.md").read_text(encoding="utf-8")
        assert "新解一" in first and "旧解一" not in first
        assert "旧解二" in second, "未选中的题包不因别人修补而被重写"
        assert (outcome.exercise_item_rows, outcome.exercise_pack_rows) == (0, 1)
        assert KpRepository(session).find_draft_batch("b07") is None

    def test_a_companion_retry_skips_the_green_gate_and_keeps_the_target_anchor(
        self, session, tmp_path
    ):
        """修补不是重发首轮：目标未绿也受理；题面仍取目标教材同一版正文、行锚点不动。"""
        self._scene(session, tmp_path)
        _scene(session, tmp_path, book_id="b05", job_id="job-a1", page_count=8)
        _register_tree(
            session,
            [
                ("afkp-b05-ch1", "第 1 章 函数", 1, 1, None),
                ("afkp-b05-ex1", "习题 1.1", 5, 2, "afkp-b05-ch1"),
            ],
            metas={
                "afkp-b05-ch1": {"toc_role": "knowledge"},
                "afkp-b05-ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            },
        )
        self._retry_item(
            session,
            tmp_path,
            "afkp-b05-q1",
            book_id="b05",
            parent="afkp-b05-ex1",
            name="1. 题一",
            source_book_id="b07",
            page=5,
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b05", job_id="job-a1"), 5, ["1. 教材题一。"]
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"), 2, ["1. 题一题面"]
        )
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "第 1 章 函数": [ExerciseDraft("1", problem="习题集题面", solution="新解一")]
            }
        )
        accepted = accept_kp_job(
            session,
            book_id="b07",
            stage="exercise",
            target_book_id="b05",
            exercise_retry_node_ids=("afkp-b05-q1",),
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        row = session.get(AfKp, "afkp-b05-q1")
        assert row is not None
        pack = _kp_dir(tmp_path, "b05").joinpath(row.narrative_ref).read_text(encoding="utf-8")
        assert "1. 教材题一。" in pack and "新解一" in pack
        assert row.anchors == [
            {"kind": "page_block", "page": 5, "block_indexes": [0], "snippet": "旧题面"}
        ], "跨书的解答页不当锚点"
        assert row.meta["source_pages"] == [2, 8], "题正文延续到本节末页"
        assert (outcome.exercise_item_rows, outcome.exercise_pack_rows) == (0, 1)

    def test_the_retry_owner_falls_back_to_the_last_companion_target(self, session, tmp_path):
        """没带目标书的修补按上一批流水认归属：配套模式的行在目标教材里，不在本书。"""
        self._scene(session, tmp_path)
        self._textbook(session)
        self._retry_item(session, tmp_path, "afkp-b05-q1", book_id="b05", source_book_id="b07", page=5)
        OperationRepository(session).create_kp_batch(
            KpBatchRow(
                gen_batch="afkp-previous01",
                book_id="b07",
                job_id="job-b1",
                status="ok",
                stage="exercise",
                target_book_id="b05",
                result={},
            )
        )
        session.commit()

        accepted = accept_kp_job(
            session, book_id="b07", stage="exercise", exercise_retry_node_ids=("afkp-b05-q1",)
        )

        assert accepted.target_book_id == "b05"
        assert accepted.exercise_retry_node_ids == ("afkp-b05-q1",)

    def test_selected_retry_only_combines_with_the_exercise_stage(self, session, tmp_path):
        self._textbook(session)
        _register_tree(session, [("afkp-b05-ch1", "第 1 章 极限", 1, 1, None)])

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(
                session,
                book_id="b05",
                stage="content",
                exercise_retry_node_ids=("afkp-b05-ch1",),
            )

        assert caught.value.status_code == 422
        assert "只与习题梳理组合" in caught.value.detail

    def test_selected_retry_is_capped_at_two_hundred(self, session, tmp_path):
        self._scene(session, tmp_path)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(
                session,
                book_id="b07",
                stage="exercise",
                exercise_retry_node_ids=tuple(f"afkp-x{index}" for index in range(201)),
            )

        assert caught.value.status_code == 422
        assert "200" in caught.value.detail


class TestExerciseOutlineMatch:
    """REQ-161 W4：习题集目录腿＝先读自身目录，再与同课程整本绿的教材匹配。

    命中即定目标（来源书清单写 `kind=companion`／`target_book_id`，读面直接读到）；未命中、
    调用失败或没有可匹配的教材都退回独立目录（本书自己的章行），不挡本批收尾。匹配时给出的
    章对应关系落进来源书清单，配套合并按它把习题集的章名认到教材章上。
    """

    def _exercise_scene(self, session, tmp_path, *, title="数学分析原理", layout=None):
        _scene(
            session,
            tmp_path,
            book_id="b07",
            job_id="job-b1",
            page_count=8,
            roles=["exercises"],
            title=title,
            layout=layout,
        )

    def _green_textbook(
        self,
        session,
        tmp_path,
        *,
        chapter="第 1 章 极限",
        book_id="b05",
        job_id="job-a1",
        chapters: list[str] | None = None,
        title="数学分析原理",
    ):
        """同课程整本绿的教材：章与子点都已背书、总结非空，目录页集合取自同版流水。"""
        chapter_names = list(chapters) if chapters else [chapter]
        chapter_ids = [f"afkp-{book_id}-ch{index}" for index in range(1, len(chapter_names) + 1)]
        section_id = f"afkp-{book_id}-s11"
        _scene(session, tmp_path, book_id=book_id, job_id=job_id, page_count=8, title=title)
        tree: list[tuple[str, str, int, int, str | None]] = [
            (chapter_id, name, 4, 1, None)
            for chapter_id, name in zip(chapter_ids, chapter_names, strict=True)
        ]
        tree.append((section_id, "1.1 函数与映射", 4, 2, chapter_ids[0]))
        # `KA-68`：没有知识点子行的一级行记「其他」，进不了教材候选面——每章各挂一枚子行。
        extra_sections = [
            (f"{chapter_id}-s1", f"{index}.1 示例", chapter_id)
            for index, chapter_id in enumerate(chapter_ids[1:], start=2)
        ]
        tree.extend((node_id, name, 4, 2, parent) for node_id, name, parent in extra_sections)
        _register_tree(session, tree, book_id=book_id, job_id=job_id)
        repo = KpRepository(session)
        for chapter_id in chapter_ids:
            assert repo.set_outline_summary(book_id=book_id, node_id=chapter_id, summary="本章总结")
            repo.set_confirmed(chapter_id, confirmed=True)
        assert repo.set_outline_summary(
            book_id=book_id, node_id=section_id, summary="定义域与值域。"
        )
        repo.set_confirmed(section_id, confirmed=True)
        for node_id, _name, _parent in extra_sections:
            assert repo.set_outline_summary(book_id=book_id, node_id=node_id, summary="示例。")
            repo.set_confirmed(node_id, confirmed=True)
        OperationRepository(session).create_kp_batch(
            KpBatchRow(
                gen_batch=f"afkp-green{book_id[-3:]}0001",
                book_id=book_id,
                job_id=job_id,
                status="ok",
                stage="content",
                result={"outline_toc_pages": [1, 2, 3]},
            )
        )
        session.commit()

    def _meta(self, tmp_path, book_id="b07"):
        path = _kp_dir(tmp_path, book_id) / "exercises" / "meta.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def _match_calls(self, engine: FakeKnowledgeEngine):
        return [request for step, request in engine.calls if step == "exercise_match"]

    def _write_solution_evidence(self, tmp_path, *, page=8):
        """在本书末页写一行中文详解：B 门证据（`KA-71`）成立，独立型不判红。"""
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"),
            page,
            ["8. 证明该等式。", "证 两边按定义展开即得。"],
        )

    def _two_chapter_layout(self):
        return {
            1: [(1, "第一章 实数")],
            4: [(1, "第二章 数列")],
        }

    def _five_chapter_layout(self):
        return {
            1: [(1, "第 1 章 极限")],
            2: [(1, "第 2 章 连续")],
            3: [(1, "第 3 章 导数")],
            4: [(1, "第 4 章 积分")],
            5: [(1, "第 5 章 级数")],
        }

    def test_a_single_candidate_is_hit_without_a_model_call(self, session, tmp_path):
        """S1：同套只剩一本教材时确定性命中——目录章名直接复用教材章名，零模型调用。

        全局证据门（2026-11-22 用户裁决）生效后，配套命中同样要过「习题＋解答」证据门，
        所以本用例须先落一行解答证据再跑。
        """
        self._exercise_scene(session, tmp_path)
        self._green_textbook(session, tmp_path, chapter="第 1 章 函数与极限")
        self._write_solution_evidence(tmp_path)
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert outcome.outline_rows > 0, "先读自身目录再匹配，目录行照常落"
        assert self._match_calls(engine) == [], "单候选不发匹配调用"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("companion", "b05")
        assert meta["match"]["state"] == "hit"
        assert meta["match"]["candidates"] == ["b05"]
        assert meta["match"]["chapters"] == [
            {"source": "第 1 章 极限", "target": "第 1 章 函数与极限"}
        ]
        assert [row.name for row in _rows(session, book_id="b07") if row.depth == 1] == [
            "第 1 章 函数与极限"
        ], "习题集章行复用教材章名"
        reading = exercise_workspace(
            session, data_root=tmp_path, book=BookRepository(session).get("b07")
        )
        assert reading is not None
        assert (reading.kind, reading.target_book_id) == ("companion", "b05")

    def test_a_multi_candidate_set_asks_the_model_and_a_miss_stays_standalone(
        self, session, tmp_path
    ):
        """S1／S2：章覆盖低于 80% 才发一次模型匹配；模型说没有对得上的，本书章行照常可用。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._five_chapter_layout()
        )
        self._write_solution_evidence(tmp_path)
        self._green_textbook(session, tmp_path, chapters=["第 1 章 极限", "第 2 章 连续"])
        self._green_textbook(
            session,
            tmp_path,
            chapter="第 1 章 另一本",
            book_id="b06",
            job_id="job-c1",
            title="另一本教材",
        )
        engine = FakeKnowledgeEngine(exercise_match_choice=ExerciseMatchChoice(book_id=None))
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        requests = self._match_calls(engine)
        assert len(requests) == 1
        assert [candidate.book_id for candidate in requests[0].candidates] == ["b05", "b06"]
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("standalone", None)
        assert meta["match"]["state"] == "miss"
        reading = exercise_workspace(
            session, data_root=tmp_path, book=BookRepository(session).get("b07")
        )
        assert reading is not None and reading.kind == "standalone"
        assert [row.name for row in _rows(session, book_id="b07") if row.depth == 1] == [
            "第 1 章 极限",
            "第 2 章 连续",
            "第 3 章 导数",
            "第 4 章 积分",
            "第 5 章 级数",
        ], "未命中即留在本书自己的章名上"

    def test_a_failed_match_call_does_not_fail_the_batch(self, session, tmp_path):
        """匹配腿是一枚可失败的旁路：调用失败退回独立模式，目录行与收尾读数不受牵连。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._five_chapter_layout()
        )
        self._write_solution_evidence(tmp_path)
        self._green_textbook(session, tmp_path, chapters=["第 1 章 极限", "第 2 章 连续"])
        self._green_textbook(
            session,
            tmp_path,
            chapter="第 1 章 另一本",
            book_id="b06",
            job_id="job-c1",
            title="另一本教材",
        )
        engine = FakeKnowledgeEngine(failing_exercise_match=True)
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert outcome.outline_rows > 0
        assert _curation_result(session, "b07") == "completed", "旁路失败不进缺口"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("standalone", None)
        assert meta["match"]["state"] == "failed"

    def test_a_non_green_textbook_is_not_a_candidate_and_costs_no_call(self, session, tmp_path):
        """候选面＝同课程浅绿及以上的教材：连机器读数都不齐的教材不进材料，也就不发匹配调用。"""
        self._exercise_scene(session, tmp_path, title="数学分析习题集")
        self._write_solution_evidence(tmp_path)
        _scene(session, tmp_path, book_id="b05", job_id="job-a1", page_count=8)
        _register_tree(session, [("afkp-b05-ch1", "第 1 章 极限", 4, 1, None)])
        engine = FakeKnowledgeEngine(
            exercise_match_choice=ExerciseMatchChoice(book_id="b05")
        )
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert self._match_calls(engine) == []
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("standalone", None)
        assert meta["match"]["state"] == "skipped"

    def test_a_light_green_textbook_is_a_candidate(self, session, tmp_path):
        """浅绿放行（REQ-170 追加请求 A）：总结齐、锚点不在目录页，只差人工背书也进候选面。"""
        self._exercise_scene(session, tmp_path)
        _scene(session, tmp_path, book_id="b05", job_id="job-a1", page_count=8)
        _register_tree(
            session,
            [
                ("afkp-b05-ch1", "第 1 章 极限", 4, 1, None),
                ("afkp-b05-s11", "1.1 极限的定义", 4, 2, "afkp-b05-ch1"),
            ],
        )
        repo = KpRepository(session)
        assert repo.set_outline_summary(
            book_id="b05", node_id="afkp-b05-ch1", summary="本章总结"
        )
        assert repo.set_outline_summary(
            book_id="b05", node_id="afkp-b05-s11", summary="定义与例子。"
        )
        OperationRepository(session).create_kp_batch(
            KpBatchRow(
                gen_batch="afkp-lightgreen01",
                book_id="b05",
                job_id="job-a1",
                status="ok",
                stage="content",
                result={"outline_toc_pages": [1, 2, 3]},
            )
        )
        session.commit()
        self._write_solution_evidence(tmp_path)
        engine = FakeKnowledgeEngine(
            exercise_match_choice=ExerciseMatchChoice(book_id="b05")
        )
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("companion", "b05"), (
            "浅绿教材是候选且单候选确定性命中"
        )
        assert self._match_calls(engine) == []

    def test_title_family_makes_a_single_candidate_companion(self, session, tmp_path):
        """R1：书名同族（习题书名含教材书名）即配——章名对不上也不挡，零模型调用。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析原理习题集", layout=self._two_chapter_layout()
        )
        self._green_textbook(session, tmp_path, chapter="第 1 章 函数与极限")
        self._write_solution_evidence(tmp_path)
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert self._match_calls(engine) == [], "同族判定是确定性的"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("companion", "b05")
        assert meta["match"]["state"] == "hit"
        assert meta["match"]["chapters"] == [
            {"source": "第一章 实数", "target": "第 1 章 函数与极限"}
        ], "同号章按章号对应，未对上的章不猜"

    def test_eighty_percent_chapter_coverage_is_companion(self, session, tmp_path):
        """R1：章覆盖恰 80% 即判配套（解析噪声允许两成缺失），零模型调用。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._five_chapter_layout()
        )
        self._green_textbook(
            session,
            tmp_path,
            chapters=["第 1 章 极限", "第 2 章 连续", "第 3 章 导数", "第 4 章 积分"],
        )
        self._write_solution_evidence(tmp_path)
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert self._match_calls(engine) == [], "80% 覆盖是确定性放行"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("companion", "b05")
        assert meta["match"]["state"] == "hit"
        assert len(meta["match"]["chapters"]) == 4

    def test_a_thin_chapter_coverage_asks_the_model(self, session, tmp_path):
        """R1：覆盖低于 80% 才交模型按范围语义判；模型选中的书与章对应照常落清单。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._five_chapter_layout()
        )
        self._green_textbook(
            session,
            tmp_path,
            chapters=["第 1 章 极限", "第 2 章 连续", "第 3 章 导数"],
            title="数学分析原理",
        )
        self._write_solution_evidence(tmp_path)
        engine = FakeKnowledgeEngine(
            exercise_match_choice=ExerciseMatchChoice(
                book_id="b05",
                chapters=(("第 4 章 积分", "第 1 章 极限"), ("第 5 章 级数", "第 3 章 导数")),
            )
        )
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert len(self._match_calls(engine)) == 1
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("companion", "b05")
        assert meta["match"]["state"] == "hit"
        assert meta["match"]["chapters"] == [
            {"source": "第 4 章 积分", "target": "第 1 章 极限"},
            {"source": "第 5 章 级数", "target": "第 3 章 导数"},
        ], "模型给的对应须两边都认得出才采信"

    def test_a_single_candidate_without_title_family_or_coverage_stays_standalone(
        self, session, tmp_path
    ):
        """R1：0% 覆盖＋明确章名＋无书名同族＝确定性命定独立，单候选不得自动命中。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._two_chapter_layout()
        )
        self._write_solution_evidence(tmp_path)
        self._green_textbook(session, tmp_path, chapter="第 1 章 函数与极限")
        engine = FakeKnowledgeEngine(
            exercise_match_choice=ExerciseMatchChoice(book_id="b05")
        )
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        assert self._match_calls(engine) == [], "零覆盖不交模型（防止把独立习题集改写成教材章名）"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("standalone", None)
        assert meta["match"]["state"] == "miss"
        assert [row.name for row in _rows(session, book_id="b07") if row.depth == 1] == [
            "第一章 实数",
            "第二章 数列",
        ], "目录留在本书自己的章名上"

    def test_a_book_without_solution_evidence_is_red_with_zero_rows(self, session, tmp_path):
        """R2：既非配套型、又拿不出题目＋解答证据＝红灯，零目录行，不回退教材目录腿。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._two_chapter_layout()
        )
        self._green_textbook(session, tmp_path, chapter="第 1 章 函数与极限")
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert outcome.outline_rows == 0, "红灯零目录行"
        assert _rows(session, book_id="b07") == [], "本书不留任何目录行"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("red", None)
        assert meta["match"]["state"] == "red"
        assert not any(step == "toc_pages" for step, _ in engine.calls), (
            "红灯不得回退教材目录腿"
        )

    def test_a_companion_hit_without_solution_evidence_is_also_red(self, session, tmp_path):
        """全局证据门（2026-11-22 用户裁决）：配套命中同样过「习题＋解答」证据门。

        匹配到教材只解决「章从哪来」；配不出题+解的书不配长出一套练不了的目录。
        章覆盖 ≥80% 命中配套，但本书零解答证据 ⇒ `red`、零目录行、且不得回退教材腿。
        """
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._five_chapter_layout()
        )
        self._green_textbook(
            session,
            tmp_path,
            chapters=["第 1 章 极限", "第 2 章 连续", "第 3 章 导数", "第 4 章 积分"],
        )
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert self._match_calls(engine) == [], "证据门在配套命中处、不影响零调用判定"
        assert outcome.outline_rows == 0, "配套命中缺证据＝红灯零目录行"
        assert _rows(session, book_id="b07") == [], "本书不留任何目录行"
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("red", None)
        assert meta["match"]["state"] == "red"
        assert not any(step == "toc_pages" for step, _ in engine.calls), (
            "红灯不得回退教材目录腿"
        )

    def test_a_solution_book_in_the_same_set_counts_as_evidence(self, session, tmp_path):
        """R2：同套存在题解本即算解答证据——独立型照常落目录，不判红。"""
        self._exercise_scene(
            session, tmp_path, title="数学分析习题集", layout=self._two_chapter_layout()
        )
        _scene(
            session,
            tmp_path,
            book_id="b11",
            job_id="job-d1",
            page_count=8,
            roles=["solutions"],
            title="数学分析习题集题解",
        )
        self._green_textbook(session, tmp_path, chapter="第 1 章 函数与极限")
        engine = FakeKnowledgeEngine()
        accepted = accept_kp_job(session, book_id="b07", stage="outline")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert outcome.outline_rows > 0
        meta = self._meta(tmp_path)
        assert (meta["kind"], meta["target_book_id"]) == ("standalone", None)
        assert meta["match"]["state"] == "miss"

    def test_the_recorded_mapping_places_items_under_the_books_own_chapter(self, session, tmp_path):
        """章对应关系的消费者：教材章名与本书不同，按章号配对后题仍落在本书自己的章下。"""
        _scene(session, tmp_path, book_id="b07", job_id="job-b1", page_count=8, roles=["exercises"])
        _register_tree(
            session,
            [
                ("afkp-b7ch1", "第 1 章 函数", 1, 1, None),
                ("afkp-b7ex1", "习题 1.1", 5, 2, "afkp-b7ch1"),
            ],
            metas={
                "afkp-b7ch1": {"toc_role": "knowledge"},
                "afkp-b7ex1": {"toc_role": "reference", "toc_kind": "exercise"},
            },
            book_id="b07",
            job_id="job-b1",
        )
        self._green_textbook(session, tmp_path, chapter="第 1 章 函数与极限")
        _register_tree(
            session,
            [("afkp-b05-ex1", "习题 1.1", 5, 2, "afkp-b05-ch1")],
            metas={"afkp-b05-ex1": {"toc_role": "reference", "toc_kind": "exercise"}},
        )
        assert write_exercise_meta(
            session,
            data_root=tmp_path,
            book_id="b07",
            kind="companion",
            target_book_id="b05",
            gen_batch="afkp-outline0001",
            unmatched=[],
            match={
                "state": "hit",
                "chapters": [{"source": "第 1 章 函数", "target": "第 1 章 函数与极限"}],
            },
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b05", job_id="job-a1"), 5, ["1. 教材题一。"]
        )
        _write_page_blocks(
            _pages_dir(tmp_path, book_id="b07", job_id="job-b1"), 5, ["1. 题一。"]
        )
        engine = FakeKnowledgeEngine(
            exercise_by_title={
                "习题 1.1": [ExerciseDraft("1", problem="题面", solution="解一")]
            }
        )
        accepted = accept_kp_job(session, book_id="b07", stage="exercise", target_book_id="b05")

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        assert outcome.unmatched_solutions == 0, "章名不同不再判「认不出本章」"
        assert (outcome.exercise_item_rows, outcome.exercise_pack_rows) == (1, 1)
        added = [
            row for row in _rows(session, book_id="b07") if row.gen_batch == accepted.gen_batch
        ]
        assert [row.parent_id for row in added] == ["afkp-b7ch1"], "行落本书自己的章，不搬家到教材"
        pack = (
            _kp_dir(tmp_path, "b07")
            .joinpath(added[0].narrative_ref or "")
            .read_text(encoding="utf-8")
        )
        assert "1. 教材题一。" in pack, "题面仍取教材同一题的正文"
        assert self._meta(tmp_path)["match"]["state"] == "hit", "习题批不抹掉匹配读数"


class TestExerciseIndexAndReadFace:
    """`KA-53`／W16：题包索引与习题读面——状态从题包正文反推、索引整份重写、完成只认三条件。"""

    def _scene(self, session, tmp_path, *, book_id="b07"):
        _scene(
            session,
            tmp_path,
            book_id=book_id,
            job_id="job-b1",
            page_count=8,
            roles=["exercises"] if book_id == "b07" else None,
        )
        _register_tree(
            session,
            [
                ("afkp-b7ch1", "第 1 章 函数", 1, 1, None),
                ("afkp-b7ch2", "第 2 章 极限", 6, 1, None),
            ]
            if book_id == "b07"
            else [("afkp-b05-ch1", "第 1 章 函数", 1, 1, None)],
            metas={"afkp-b7ch1": {"toc_role": "knowledge"}, "afkp-b7ch2": {"toc_role": "knowledge"}}
            if book_id == "b07"
            else {"afkp-b05-ch1": {"toc_role": "knowledge"}},
            book_id=book_id,
            job_id="job-b1",
        )

    def _item(
        self,
        session,
        tmp_path,
        node_id,
        *,
        book_id="b07",
        parent="afkp-b7ch1",
        name="1. 题一",
        problem="题面正文",
        solution="解一",
        source_book_id="b07",
        page=2,
        status="candidate",
        with_pack=True,
        extra_meta=None,
    ):
        row = _draft(
            session,
            node_id,
            book_id=book_id,
            job_id="job-b1",
            node_kind="point",
            parent_id=parent,
            name=name,
            norm_key=norm_key_for(name),
            anchors=[
                {"kind": "page_block", "page": page, "block_indexes": [0], "snippet": problem}
            ],
            meta={
                "toc_kind": "exercise",
                "source_book_id": source_book_id,
                "source_pages": [page],
                **(extra_meta or {}),
            },
            gen_status=status,
        )
        if with_pack:
            content = exercise_pack_text(
                label=name,
                problem=problem,
                solution=solution,
                source_lines=["《数学分析原理》p.2"],
            )
            write_narrative(session, data_root=tmp_path, node_id=node_id, content=content)
            assert KpRepository(session).set_narrative_ref(node_id, f"{node_id}.md").ok
        session.commit()
        return row

    def test_the_pack_status_comes_from_the_two_body_sections(self):
        """三值不落列：题面指针句＝没取到、缺解答＝仅题面、两段齐＝题解齐。"""
        assert (
            exercise_pack_status(
                exercise_pack_text(label="1", problem="题面", solution="解", source_lines=[])
            ),
            exercise_pack_status(
                exercise_pack_text(label="1", problem="见《书》p.3", solution="解", source_lines=[])
            ),
            exercise_pack_status(
                exercise_pack_text(label="1", problem="题面", solution="", source_lines=[])
            ),
        ) == ("complete", "source_missing", "problem_only")

    def test_the_unavailable_marker_is_not_mistaken_for_a_body(self):
        """REQ-179 R2：显式不可用标记不冒充解答正文，也不冒充「有解答」状态。

        判定失败路径写标记（不可用）而非占位句（未配到解答）；标记本身可从解答段反查，
        读面与屏侧据此把该题按「解答不可用」呈现，而不是把提示语当正文。
        """
        assert knowledge_module.EXERCISE_UNAVAILABLE_TEXT == "解答不可用（模型判定失败）"
        pack = exercise_pack_text(
            label="1", problem="题面", solution=knowledge_module.EXERCISE_UNAVAILABLE_TEXT,
            source_lines=[],
        )
        # 标记不算解答：状态仍是 problem_only（不是 complete）
        assert exercise_pack_status(pack) == "problem_only"
        # 占位句仍按旧行为识别为 problem_only（存量 12 份在重跑前的读法不变）
        legacy = exercise_pack_text(
            label="1", problem="题面", solution=knowledge_module.EXERCISE_NO_SOLUTION_TEXT,
            source_lines=[],
        )
        assert exercise_pack_status(legacy) == "problem_only"

    def test_the_index_is_one_file_per_chapter_and_a_rewrite_drops_stale_chapters(
        self, session, tmp_path
    ):
        """一份章一个文件、条目只存指针、`updated_batch` 记本批；重写后旧章文件不残留。"""
        self._scene(session, tmp_path)
        self._item(session, tmp_path, "afkp-b7q1", name="1. 题一")
        self._item(
            session, tmp_path, "afkp-b7q2", parent="afkp-b7ch2", name="2. 题二", page=7
        )
        directory = _kp_dir(tmp_path, "b07") / "exercises"

        assert write_exercise_indexes(
            session, data_root=tmp_path, book_id="b07", gen_batch="afkp-batch001"
        ) == 2
        first = json.loads((directory / "afkp-b7ch1.json").read_text(encoding="utf-8"))
        assert first["chapter"] == {
            "node_id": "afkp-b7ch1",
            "name": "第 1 章 函数",
            "order_hint": 1,
        }
        assert first["updated_batch"] == "afkp-batch001"
        assert first["items"][0]["node_id"] == "afkp-b7q1"
        assert (first["items"][0]["item_no"], first["items"][0]["status"]) == ("1", "complete")
        assert first["items"][0]["source_book_id"] == "b07"
        assert first["items"][0]["page_start"] == 2

        rejected = session.get(AfKp, "afkp-b7q2")
        assert rejected is not None
        rejected.gen_status = "rejected"
        session.commit()
        assert write_exercise_indexes(
            session, data_root=tmp_path, book_id="b07", gen_batch="afkp-batch002"
        ) == 1
        assert not (directory / "afkp-b7ch2.json").exists(), "整份重写＝索引集合等于当前章集合"

    def test_the_meta_file_records_mode_target_and_unmatched(self, session, tmp_path):
        self._scene(session, tmp_path)
        book = BookRepository(session).get("b07")
        assert book is not None

        assert write_exercise_meta(
            session,
            data_root=tmp_path,
            book_id="b07",
            kind="companion",
            target_book_id="b05",
            gen_batch="afkp-batch001",
            unmatched=[{"item_no": "9", "source_pages": [3, 4], "reason": "题号在目标章下不唯一"}],
        )
        payload = read_exercise_meta(tmp_path, book)
        assert (payload["kind"], payload["target_book_id"]) == ("companion", "b05")
        assert payload["unmatched"][0]["reason"] == "题号在目标章下不唯一"

        path = _kp_dir(tmp_path, "b07") / "exercises" / "meta.json"
        path.write_text("{坏掉的 JSON", encoding="utf-8")
        assert read_exercise_meta(tmp_path, book) == {}, "坏文件当空表，读腿不造文件也不抛"

    def test_a_standalone_book_reads_its_own_items_and_done(self, session, tmp_path):
        """完成＝题行>0 ∧ 题包齐 ∧ 待重解析零；状态优先取索引，索引缺席按题包指针兜底。"""
        self._scene(session, tmp_path)
        self._item(session, tmp_path, "afkp-b7q1", name="1. 题一")
        self._item(session, tmp_path, "afkp-b7q2", name="2. 题二", solution="", page=3)
        assert write_exercise_indexes(
            session, data_root=tmp_path, book_id="b07", gen_batch="afkp-batch001"
        ) == 1
        book = BookRepository(session).get("b07")
        assert book is not None

        reading = exercise_workspace(session, data_root=tmp_path, book=book)

        assert reading is not None
        assert (reading.kind, reading.target_book_id) == ("standalone", None)
        assert [item["node_id"] for item in reading.items] == ["afkp-b7q1", "afkp-b7q2"]
        assert [item["status"] for item in reading.items] == ["complete", "problem_only"]
        assert reading.items[0]["item_no"] == "1"
        assert reading.items[0]["source_pages"] == [2]
        assert [item["pack"] for item in reading.items] == [True, True]
        assert (reading.packs, reading.pending_reparse, reading.done) == (2, 0, True)

    def test_the_read_face_carries_the_pairing_and_verification_readings(self, session, tmp_path):
        """`pair_state`／`pair_reason` 与 `verify_*` 逐键透传：旧批次缺键读空串，屏侧只读不重算。"""
        self._scene(session, tmp_path)
        self._item(
            session,
            tmp_path,
            "afkp-b7q1",
            name="1. 题一",
            extra_meta={
                "pair_state": "unpaired",
                "pair_reason": "习题集未收录本题",
                "verify_state": "done",
                "verify_verdict": "suspect",
                "verify_note": "解答像下一题的开头",
            },
        )
        self._item(session, tmp_path, "afkp-b7q2", name="2. 题二", page=3)
        book = BookRepository(session).get("b07")
        assert book is not None

        reading = exercise_workspace(session, data_root=tmp_path, book=book)

        assert reading is not None
        first, second = reading.items
        assert (
            first["pair_state"],
            first["pair_reason"],
            first["verify_state"],
            first["verify_verdict"],
            first["verify_note"],
        ) == ("unpaired", "习题集未收录本题", "done", "suspect", "解答像下一题的开头")
        assert (
            second["pair_state"],
            second["pair_reason"],
            second["verify_state"],
            second["verify_verdict"],
            second["verify_note"],
        ) == ("", "", "", "", "")

    def test_missing_problem_blocks_done_and_counts_pending(self, session, tmp_path):
        """题面没取到才是缺项：题包缺席、状态 `source_missing`、待重解析一枚、完成恒假。"""
        self._scene(session, tmp_path)
        self._item(session, tmp_path, "afkp-b7q1", name="1. 题一", with_pack=False)
        book = BookRepository(session).get("b07")
        assert book is not None

        reading = exercise_workspace(session, data_root=tmp_path, book=book)

        assert reading is not None
        assert [(item["status"], item["pack"]) for item in reading.items] == [
            ("source_missing", False)
        ]
        assert (reading.packs, reading.pending_reparse, reading.done) == (0, 1, False)

    def test_without_an_index_the_status_still_comes_from_the_pack(self, session, tmp_path):
        """在飞批（章索引只在收尾写）读面兜底也读题包正文：缺解答仍是仅题面，不冒充题解齐。"""
        self._scene(session, tmp_path)
        self._item(session, tmp_path, "afkp-b7q1", name="1. 题一", solution="")
        book = BookRepository(session).get("b07")
        assert book is not None
        assert not (_kp_dir(tmp_path, "b07") / "exercises" / "afkp-b7ch1.json").exists()

        reading = exercise_workspace(session, data_root=tmp_path, book=book)

        assert reading is not None
        assert [(item["status"], item["pack"]) for item in reading.items] == [
            ("problem_only", True)
        ]
        assert (reading.packs, reading.pending_reparse, reading.done) == (1, 0, True)

    def test_a_textbook_has_no_exercise_face(self, session, tmp_path):
        """共享主表永不含习题行：教材这一位恒 `None`，不制造第二套读数。"""
        self._scene(session, tmp_path, book_id="b05")
        book = BookRepository(session).get("b05")
        assert book is not None

        assert exercise_workspace(session, data_root=tmp_path, book=book) is None

    def test_a_companion_book_reads_its_own_rows(self, session, tmp_path):
        """配套模式的题行落在本书自己名下（REQ-170）：读面读本书行，教材自己的题不算进来。"""
        self._scene(session, tmp_path)
        self._scene(session, tmp_path, book_id="b05")
        self._item(session, tmp_path, "afkp-b7q1", name="1. 题一")
        self._item(
            session,
            tmp_path,
            "afkp-b05-q2",
            book_id="b05",
            parent="afkp-b05-ch1",
            name="2. 题二",
            source_book_id="b05",
            page=4,
        )
        assert write_exercise_meta(
            session,
            data_root=tmp_path,
            book_id="b07",
            kind="companion",
            target_book_id="b05",
            gen_batch="afkp-batch001",
            unmatched=[],
        )
        book = BookRepository(session).get("b07")
        assert book is not None

        reading = exercise_workspace(session, data_root=tmp_path, book=book)

        assert reading is not None
        assert (reading.kind, reading.target_book_id) == ("companion", "b05")
        assert [item["node_id"] for item in reading.items] == ["afkp-b7q1"]
        assert reading.items[0]["source_book_id"] == "b07"

    def test_a_companion_read_face_reparents_items_to_the_target_chapter(
        self, session, tmp_path
    ):
        """读面的题行挂目标教材同号章：屏上列的是目标书那套章，行仍留在本书自己的章下。

        目标书缺同号章时退回本书章号；两本书的库行都零改动（映射只在读时派生）。
        """
        self._scene(session, tmp_path)
        self._scene(session, tmp_path, book_id="b05")
        _draft(
            session,
            "afkp-b05-k1",
            job_id="job-b1",
            node_kind="point",
            parent_id="afkp-b05-ch1",
            depth=2,
            name="函数的概念",
            norm_key=norm_key_for("函数的概念"),
            gen_status="candidate",
        )
        self._item(session, tmp_path, "afkp-b7q1", name="1. 题一")
        self._item(session, tmp_path, "afkp-b7q2", parent="afkp-b7ch2", name="2. 题二", page=7)
        assert write_exercise_meta(
            session,
            data_root=tmp_path,
            book_id="b07",
            kind="companion",
            target_book_id="b05",
            gen_batch="afkp-batch001",
            unmatched=[],
        )
        book = BookRepository(session).get("b07")
        assert book is not None

        reading = exercise_workspace(session, data_root=tmp_path, book=book)

        assert reading is not None
        assert [item["parent_id"] for item in reading.items] == [
            "afkp-b05-ch1",
            "afkp-b7ch2",
        ]
        session.expire_all()
        assert session.get(AfKp, "afkp-b7q1").parent_id == "afkp-b7ch1", "库行不动"
        assert len(KpRepository(session).list_for_book("b05")) == 2, "目标书零写入"


class TestFailedNodeRetry:
    """`KA-52`：按失败节点重跑只开在 `content` 上——名单来自上一批流水，取证集仍按原页区间读全。

    这一层守的是：失败明细带节点身份、名单外的节零调用、收尾不把名单外的旧点行判死，以及
    「与其它阶段组合」「上一批零失败节」两条 422 都是零写入。
    """

    _ROWS = [
        ("afkp-ch1", "第 1 章 极限", 1, 1, None),
        ("afkp-s11", "1.1 数列极限", 2, 2, "afkp-ch1"),
        ("afkp-s12", "1.2 函数极限", 5, 2, "afkp-ch1"),
    ]

    def _first_batch(self, session, tmp_path, *, failing: tuple[str, ...]):
        """先跑一轮 content 并把流水行落库：下一轮的 `only_failed` 只认账面点名。"""
        _scene(session, tmp_path, page_count=8)
        _register_tree(session, self._ROWS)
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=FakeKnowledgeEngine(failing_sections=failing),
            acceptance=accepted,
        )
        OperationRepository(session).create_kp_batch(kp_batch_row(accepted, outcome))
        session.commit()
        return accepted, outcome

    @staticmethod
    def _sections(engine: FakeKnowledgeEngine) -> list[SectionInput]:
        return [payload for step, payload in engine.calls if step == "points"]

    def test_only_the_named_node_is_reasked_with_its_full_span(self, session, tmp_path):
        """名单只收失败节，切片仍按原树序取到下一行之前——不是按锚点页缩成一页。"""
        first_accepted, first = self._first_batch(session, tmp_path, failing=("1.1 数列极限",))
        detail = kp_batch_row(first_accepted, first).result["failed_section_details"]
        assert [(item["node_id"], item["title"]) for item in detail] == [
            ("afkp-s11", "1.1 数列极限")
        ]

        retry = accept_kp_job(session, book_id="b05", stage="content", only_failed=True)
        engine = FakeKnowledgeEngine()
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=retry
        )

        assert [
            (section.title, section.page_start, section.page_end)
            for section in self._sections(engine)
        ] == [("1.1 数列极限", 2, 4)], "只问失败的节，区间仍是它自己的完整切片"
        assert outcome.failed_sections == 0

    def test_a_retry_never_supersedes_point_rows_outside_the_named_nodes(self, session, tmp_path):
        """`KA-36` 的作废面按名单收窄：名单外的旧点行不是这一批的过期读数。"""
        self._first_batch(session, tmp_path, failing=("1.1 数列极限",))
        stale = _draft(
            session,
            "afkp-p12",
            gen_status="candidate",
            node_kind="point",
            parent_id="afkp-s12",
            gen_batch="afkp-old00",
            anchors=[{"page": 6, "block_indexes": [0], "snippet": "上一批成功节的读数"}],
        )
        session.commit()

        retry = accept_kp_job(session, book_id="b05", stage="content", only_failed=True)
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=retry
        )
        session.expire_all()

        assert session.get(AfKp, stale.node_id).gen_status == "candidate"
        assert outcome.superseded_rows == 0
        assert outcome.candidate_rows == 0

    def test_only_failed_without_a_failed_section_is_rejected_with_zero_writes(self, session, tmp_path):
        """上一批没有失败节 ⇒ 422 点名；不落批次号、不写行、不写流水。"""
        self._first_batch(session, tmp_path, failing=())
        rows, ledger = _kp_rows(session), _kp_ledger_rows(session)

        with pytest.raises(KpGateError) as caught:
            accept_kp_job(session, book_id="b05", stage="content", only_failed=True)

        assert caught.value.status_code == 422
        assert "没有失败节" in caught.value.detail
        assert (_kp_rows(session), _kp_ledger_rows(session)) == (rows, ledger)

    def test_only_failed_only_combines_with_content(self, session, tmp_path):
        """`KA-52`：目录与教材侧习题都不产生「失败节」，组合即 422 且零写入。"""
        _scene(session, tmp_path, page_count=8)
        _register_tree(session, self._ROWS)
        rows, ledger = _kp_rows(session), _kp_ledger_rows(session)

        for stage in ("full", "outline", "exercise"):
            with pytest.raises(KpGateError) as caught:
                accept_kp_job(session, book_id="b05", stage=stage, only_failed=True)
            assert caught.value.status_code == 422, stage
            assert "只与 content" in caught.value.detail

        assert (_kp_rows(session), _kp_ledger_rows(session)) == (rows, ledger)


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

    def test_the_chapter_material_is_the_folder_head_and_the_child_summaries(self, session, tmp_path):
        accepted = self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            summaries_by_section={"1.1 数列极限": "节一总结", "1.2 函数极限": "节二总结"},
            chapters_by_title={"第 1 章 极限": ("第 1 章的章级总结", "第 1 章的导读正文")},
        )

        outcome = run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)
        session.expire_all()

        (material,) = self._chapter_calls(engine)
        assert (material.title, material.page_start, material.page_end) == ("第 1 章 极限", 1, 8)
        assert {block.page for block in material.blocks} == {1}, "章前材料止于首点子行锚页之前"
        assert [step for step, _ in engine.calls] == ["points", "chapter"], "章腿在点腿之后（`KA-65`）"
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

    def test_a_chapter_that_starts_on_its_first_section_page_keeps_only_the_title_block(
        self, session, tmp_path
    ):
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
        child = KpRepository(session).get("afkp-s11")
        child.anchors = [
            {"kind": "page_block", "ref_id": "p0002", "page": 2, "block_indexes": [1], "snippet": "1.1 数列极限"}
        ]
        session.commit()
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        (material,) = self._chapter_calls(engine)
        assert [(block.page, block.block_index) for block in material.blocks] == [
            (2, 0)
        ], "章与其首节同页 ⇒ 只取首点标题块之前的块"

    def test_a_chapter_whose_children_have_no_summary_gets_an_empty_list(self, session, tmp_path):
        accepted = self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
        )

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        (material,) = self._chapter_calls(engine)
        assert material.point_summaries == ()

    def test_the_chapter_leg_carries_the_reference_notes_from_the_fetcher(self, session, tmp_path):
        """`KA-67` 章级检索：查询词＝章名＋其下点标题，命中资料随章材料进提示词；点腿不受牵连。"""
        accepted = self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("极限是分析数列与函数逼近行为的工具。", "本章导读：极限。")}
        )
        seen: list[tuple[str, tuple[str, ...]]] = []
        note = ReferenceNote(source="维基百科", title="极限（数学）", text="极限是数学分析的基础概念。")

        def fetcher(title, titles):
            seen.append((title, tuple(titles)))
            return (note,)

        run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=engine,
            acceptance=accepted,
            reference_fetcher=fetcher,
        )

        (material,) = self._chapter_calls(engine)
        assert material.reference_notes == (note,)
        assert seen == [("第 1 章 极限", ("1.1 数列极限", "1.2 函数极限"))]

    def test_a_failing_reference_fetcher_still_produces_the_chapter(self, session, tmp_path):
        """外部源故障 fail-open：按无检索资料继续，章腿照发不误。"""
        accepted = self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(chapters_by_title={"第 1 章 极限": ("章总结", "章导读")})

        def fetcher(_title, _titles):
            raise RuntimeError("网络不通")

        outcome = run_knowledge_job(
            session,
            data_root=tmp_path,
            engine=engine,
            acceptance=accepted,
            reference_fetcher=fetcher,
        )
        session.expire_all()

        (material,) = self._chapter_calls(engine)
        assert material.reference_notes == ()
        assert outcome.chapter_rows == 1
        assert outcome.failed_chapters == 0

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


class TestChapterValidation:
    """PLAN-033 C-6：章级正文的确定性校验——禁页数、禁全书结构、禁后续章节、带名著述须有出处。"""

    _CLEAN = "极限刻画数列与函数的逼近行为：先给极限的定义，再谈它的性质。"
    _CLEAN_NARRATIVE = "本章从极限的定义出发，说明逼近行为怎样用距离与不等式度量。"

    def _material(self, *, summary_text: str = "数列极限的定义与性质。"):
        return ChapterInput(
            parent_id="afkp-ch1",
            title="第 1 章 极限",
            page_start=1,
            page_end=8,
            blocks=(SectionBlock(page=1, block_index=0, text="正文讨论数列极限与函数极限。"),),
            point_summaries=(("1.1 数列极限", summary_text),),
        )

    def _issues(self, text: str, **kwargs) -> tuple[str, ...]:
        return chapter_issues(
            self._material(),
            ChapterReply(summary=text, narrative=self._CLEAN_NARRATIVE),
            **kwargs,
        )

    def test_a_clean_summary_and_narrative_pass(self):
        assert self._issues(self._CLEAN) == ()

    @pytest.mark.parametrize("text", ["本章讨论极限。", "这一章先给极限的定义。", "整章围绕极限展开。"])
    def test_the_summary_does_not_talk_about_itself_as_a_chapter(self, text):
        """总结是纯知识概述：不以「本章／这一章」自称（用户口径 2026-11-09）。"""
        assert any("章节自称" in issue for issue in self._issues(text))

    def test_the_narrative_may_still_say_this_chapter(self):
        """导读是章级走读，「本章开头」这类引导语不算自称。"""
        replied = ChapterReply(summary=self._CLEAN, narrative=self._CLEAN_NARRATIVE)
        assert chapter_issues(self._material(), replied) == ()

    def test_a_positioning_use_statement_is_caught(self):
        issues = self._issues("极限刻画逼近行为，为处理复变函数等复杂对象提供理论支撑。")
        assert any("用途陈述" in issue for issue in issues)

    def test_a_knowledge_grounding_sentence_is_not_a_use_statement(self):
        assert self._issues("极限的唯一性由距离不等式推出。") == ()

    def test_the_narrative_must_not_end_with_a_study_completion_cliche(self):
        replied = ChapterReply(
            summary=self._CLEAN,
            narrative="我们从极限的定义出发，把逼近行为写成距离不等式。学完这一章，你们就掌握了逼近的工具。",
        )
        issues = chapter_issues(self._material(), replied)
        assert any("总结陈词" in issue for issue in issues)

    @pytest.mark.parametrize(
        "text",
        [
            "本章共 32 页，讲极限。",
            "本章覆盖 p49–80 的内容。",
            "第 49–80 页讨论极限。",
            "本章含 14 个知识点。",
        ],
    )
    def test_page_counts_and_ranges_are_caught(self, text):
        issues = self._issues(text)
        assert issues and "页数或页区间" in issues[0]

    def test_whole_book_words_are_caught(self):
        issues = self._issues("本章位于全书前段，承接基础准备。")
        assert any("全书结构表述" in issue for issue in issues)

    @pytest.mark.parametrize(
        "text",
        [
            "本章为第 4 章的连续性研究提供分析支撑。",
            "这些结论在后续章节会继续使用。",
            "学完本章可以进入下一章。",
        ],
    )
    def test_later_chapter_sentences_are_caught(self, text):
        assert any("后续章节承接句" in issue for issue in self._issues(text))

    def test_an_ungrounded_named_result_is_caught(self):
        issues = self._issues("由柯西准则可以判断收敛。")
        assert any("柯西准则" in issue and "没有出处" in issue for issue in issues)

    def test_a_named_result_with_a_source_in_the_material_passes(self):
        assert self._issues("数列极限的定义给出收敛的判据。") == ()

    def test_a_named_result_grounded_only_in_the_reference_notes_passes(self):
        material = replace(
            self._material(),
            reference_notes=(ReferenceNote(source="维基百科", title="柯西列", text="柯西准则刻画完备性。"),),
        )
        assert chapter_issues(material, ChapterReply(summary="由柯西准则可以判断收敛。", narrative=self._CLEAN)) == ()

    def test_empty_segments_are_issues_but_only_for_the_checked_side(self):
        replied = ChapterReply(summary="", narrative=self._CLEAN)
        assert chapter_issues(self._material(), replied) == ("章总结为空",)
        assert chapter_issues(self._material(), replied, check_narrative=False) == ("章总结为空",)
        assert chapter_issues(self._material(), replied, check_summary=False) == ()

    def test_coverage_is_not_a_failure_condition(self):
        """少讲一个子点不判失败：覆盖是尽力目标，不逼模型为凑全改句义。"""
        material = replace(
            self._material(),
            point_summaries=(
                ("1.1 数列极限", "数列极限的定义。"),
                ("1.2 函数极限", "函数极限的定义。"),
            ),
        )
        assert chapter_issues(material, ChapterReply(summary=self._CLEAN, narrative=self._CLEAN)) == ()


class TestChapterCheckRetry:
    """PLAN-033 C-5／C-7：同模型一次重试；重试后仍不过则照写，并在章行 `meta.chapter_check` 落注记。"""

    _LAYOUT = TestChapterLeg._LAYOUT
    _ROWS = TestChapterLeg._ROWS
    _BAD_SUMMARY = "本章共 32 页，讲数列极限。"

    def _scene(self, session, tmp_path):
        _scene(session, tmp_path, page_count=8, layout=self._LAYOUT)
        _register_tree(session, self._ROWS)

    def _run(self, session, tmp_path, engine):
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()
        chapter = next(row for row in _rows(session, node_kind="outline") if row.node_id == "afkp-ch1")
        return outcome, chapter

    def test_a_clean_reply_is_asked_once_and_gets_no_note(self, session, tmp_path):
        self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("极限是数列逼近行为的刻画。", "本章导读：从数列到极限。")},
            model_name="qwen3.5-9b",
        )

        outcome, chapter = self._run(session, tmp_path, engine)

        assert len([step for step, _ in engine.calls if step == "chapter"]) == 1
        assert "chapter_check" not in chapter.meta
        assert outcome.chapter_summary_rows == 1

    def test_a_bad_reply_is_retried_with_the_issues_in_the_prompt_then_passes(self, session, tmp_path):
        self._scene(session, tmp_path)
        replies = iter(
            [
                ChapterReply(summary=self._BAD_SUMMARY, narrative="本章导读：极限。"),
                ChapterReply(summary="极限是数列逼近行为的刻画。", narrative="本章导读：极限。"),
            ]
        )
        engine = FakeKnowledgeEngine(on_chapter=lambda _material: next(replies))

        outcome, chapter = self._run(session, tmp_path, engine)

        chapter_materials = [payload for step, payload in engine.calls if step == "chapter"]
        assert len(chapter_materials) == 2
        assert chapter_materials[1].retry_issues and "页数或页区间" in chapter_materials[1].retry_issues[0]
        assert chapter.summary == "极限是数列逼近行为的刻画。"
        assert "chapter_check" not in chapter.meta
        assert outcome.chapter_summary_rows == 1

    def test_a_reply_that_still_fails_is_written_and_noted(self, session, tmp_path):
        self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            on_chapter=lambda _material: ChapterReply(
                summary=self._BAD_SUMMARY, narrative="本章导读：极限。"
            ),
            model_name="qwen3.5-9b",
        )

        outcome, chapter = self._run(session, tmp_path, engine)

        assert len([step for step, _ in engine.calls if step == "chapter"]) == 2
        assert chapter.summary == self._BAD_SUMMARY, "两次不过也照写：注明不等于作废"
        check = chapter.meta["chapter_check"]
        assert check["passed"] is False
        assert check["model"] == "qwen3.5-9b"
        assert any("页数或页区间" in issue for issue in check["issues"])
        assert outcome.chapter_summary_rows == 1, "照写的那一份照常计数"

    def test_a_failed_retry_keeps_the_first_reply_and_notes(self, session, tmp_path):
        self._scene(session, tmp_path)
        calls = {"n": 0}

        def chapter(_material):
            calls["n"] += 1
            if calls["n"] == 2:
                raise EngineError("模型服务中断")
            return ChapterReply(summary=self._BAD_SUMMARY, narrative="本章导读：极限。")

        engine = FakeKnowledgeEngine(on_chapter=chapter)

        outcome, chapter_row = self._run(session, tmp_path, engine)

        assert chapter_row.summary == self._BAD_SUMMARY, "重试失败不丢首版"
        assert chapter_row.meta["chapter_check"]["passed"] is False
        assert outcome.failed_chapters == 0, "首版已拿到，不算整章失败"

    def test_the_summary_leg_only_checks_the_summary(self, session, tmp_path):
        """单点总结腿不看导读：导读为空不触发重试，也不落注记。"""
        self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            on_chapter=lambda _material: ChapterReply(
                summary="极限是数列逼近行为的刻画。",
                narrative="",
            )
        )

        regenerate_section_summary(session, data_root=tmp_path, engine=engine, node_id="afkp-ch1")

        assert len([step for step, _ in engine.calls if step == "chapter"]) == 1
        session.expire_all()
        assert "chapter_check" not in KpRepository(session).get("afkp-ch1").meta

    def test_the_narrative_leg_only_checks_the_narrative(self, session, tmp_path):
        """单点导读腿不看总结：总结为空不触发重试。"""
        self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(
            on_chapter=lambda _material: ChapterReply(
                summary="",
                narrative="本章导读：从数列极限到函数极限。",
            )
        )

        _node, content = regenerate_chapter_narrative(
            session, data_root=tmp_path, engine=engine, node_id="afkp-ch1"
        )

        assert content == "本章导读：从数列极限到函数极限。"
        assert len([step for step, _ in engine.calls if step == "chapter"]) == 1
        session.expire_all()
        assert "chapter_check" not in KpRepository(session).get("afkp-ch1").meta


class TestChapterFolder:
    """`KA-65` 的四条边界：无子点零调用、区间外子点不带章、子点失败照发、已确认行不被覆盖。"""

    def test_a_chapter_without_children_is_never_asked(self, session, tmp_path):
        _scene(session, tmp_path, page_count=6)
        _register_tree(session, [("afkp-ch1", "第 1 章 极限", 1, 1, None)])
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine()

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )

        assert [step for step, _ in engine.calls] == [], "无子点章零调用"
        assert outcome.chapter_rows == 0

    def test_a_chapter_whose_children_are_outside_the_range_is_not_asked(self, session, tmp_path):
        layout = {1: [(1, "第 1 章 极限")], 6: [(2, "1.2 函数极限")]}
        _scene(session, tmp_path, page_count=8, layout=layout)
        _register_tree(
            session,
            [
                ("afkp-ch1", "第 1 章 极限", 1, 1, None),
                ("afkp-s12", "1.2 函数极限", 6, 2, "afkp-ch1"),
            ],
        )
        accepted = accept_kp_job(session, book_id="b05", stage="content", page_start=1, page_end=3)
        engine = FakeKnowledgeEngine()

        run_knowledge_job(session, data_root=tmp_path, engine=engine, acceptance=accepted)

        assert [step for step, _ in engine.calls] == [], "区间内零子点 ⇒ 章零调用（`KA-65`）"

    def test_a_chapter_is_still_asked_when_a_point_leg_failed(self, session, tmp_path):
        _scene(session, tmp_path, page_count=8, layout=TestChapterLeg._LAYOUT)
        _register_tree(session, TestChapterLeg._ROWS)
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine(
            failing_sections=("1.1 数列极限",),
            chapters_by_title={"第 1 章 极限": ("章总结", "章导读")},
        )

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        (material,) = [payload for step, payload in engine.calls if step == "chapter"]
        assert material.point_summaries == (), "失败节的空总结不入清单"
        chapter = next(row for row in _rows(session, node_kind="outline") if row.node_id == "afkp-ch1")
        assert chapter.summary == "章总结", "子点失败不等于章不可写"
        assert outcome.chapter_rows == 1

    def test_a_confirmed_chapter_keeps_its_empty_summary(self, session, tmp_path):
        _scene(session, tmp_path, page_count=8, layout=TestChapterLeg._LAYOUT)
        _register_tree(session, TestChapterLeg._ROWS)
        KpRepository(session).set_confirmed("afkp-ch1", confirmed=True)
        session.commit()
        accepted = accept_kp_job(session, book_id="b05", stage="content")
        engine = FakeKnowledgeEngine(chapters_by_title={"第 1 章 极限": ("机器想覆盖", "")})

        outcome = run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=accepted
        )
        session.expire_all()

        chapter = next(row for row in _rows(session, node_kind="outline") if row.node_id == "afkp-ch1")
        assert chapter.summary == "", "人已背书的行机器零触碰"
        assert outcome.chapter_summary_rows == 0


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

    def test_a_chapter_regenerates_only_its_summary_from_the_chapter_material(self, session, tmp_path):
        """2026-11-05 扩章：章走 `_chapter_material` 的章前块与子点总结，回复里的导读丢弃、不写 `narrative_ref`。"""
        self._scene(session, tmp_path)
        before = KpRepository(session).get("afkp-ch1")
        assert before is not None
        rev0 = before.rev
        engine = FakeKnowledgeEngine(
            chapters_by_title={"第 1 章 极限": ("章级重写总结", "这份导读不许落盘")}
        )

        updated = regenerate_section_summary(
            session, data_root=tmp_path, engine=engine, node_id="afkp-ch1"
        )
        session.expire_all()

        assert updated.summary == "章级重写总结"
        assert updated.rev == rev0 + 1
        assert updated.narrative_ref in (None, ""), "章总结腿不写导读指针"
        (material,) = [payload for step, payload in engine.calls if step == "chapter"]
        assert (material.title, material.page_start, material.page_end) == ("第 1 章 极限", 1, 8), (
            "章材料的区间是整章（到本节树序的终点页），不是锚点那一页"
        )
        assert {block.page for block in material.blocks} == {1}, "章前材料只取首点之前的块"
        assert not _kp_dir(tmp_path).joinpath("afkp-ch1.md").exists(), "回复里的导读丢弃，不落盘"

    def test_the_regenerated_chapter_gets_the_reference_notes(self, session, tmp_path):
        """`KA-67` 单点腿同一条检索：取词用章名＋全部子点标题（总结为空的点也在取词里）。"""
        self._scene(session, tmp_path)
        engine = FakeKnowledgeEngine(chapters_by_title={"第 1 章 极限": ("章级重写总结", "")})
        seen: list[tuple[str, tuple[str, ...]]] = []
        note = ReferenceNote(source="维基百科", title="函数极限", text="函数极限的定义。")

        def fetcher(title, titles):
            seen.append((title, tuple(titles)))
            return (note,)

        regenerate_section_summary(
            session,
            data_root=tmp_path,
            engine=engine,
            node_id="afkp-ch1",
            reference_fetcher=fetcher,
        )

        (material,) = [payload for step, payload in engine.calls if step == "chapter"]
        assert material.reference_notes == (note,)
        assert seen == [("第 1 章 极限", ("1.1 数列极限", "1.2 函数极限"))]

    def test_exercise_and_other_nodes_cannot_regenerate(self, session, tmp_path):
        metas = {"afkp-ex": {"toc_role": "reference", "toc_kind": "exercise"}}
        _scene(session, tmp_path, page_count=8, layout=self._LAYOUT)
        _register_tree(
            session, [*self._ROWS, ("afkp-ex", "习题一", 8, 2, "afkp-ch1")], metas=metas
        )
        engine = FakeKnowledgeEngine()

        with pytest.raises(KpGateError) as caught:
            regenerate_section_summary(session, data_root=tmp_path, engine=engine, node_id="afkp-ex")
        assert caught.value.status_code == 422
        assert "只有章节或知识点可以重生成总结" in caught.value.detail
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


class TestExerciseRows:
    """`KA-46`：章末习题段由确定性规则成行——父＝所属章、附件身份位、零 summary、零模型调用。

    这一腿补的是「概念与标签都在册、却只有模型偶发抄行时才落行」的缺口（b05 两行、b07 零行），
    所以用例面盯的是三件事：认得出、挂得对、不重复。
    """

    def _scene(self, session, tmp_path, *, layout=None, page_count: int = 6):
        return _scene(
            session,
            tmp_path,
            page_count=page_count,
            layout={1: [(1, "第 1 章 极限")], 2: [(2, "1.1 数列极限")]}
            if layout is None
            else layout,
        )

    def test_an_exercise_section_becomes_a_child_row_of_its_own_chapter(self, session, tmp_path):
        """父＝它所在那一章（不是后一章），身份位＝附件＋习题，`summary` 恒空串、产出者记 `rule:exercise`。"""
        pages_dir = self._scene(
            session, tmp_path, layout={1: [(1, "第 1 章 极限")], 4: [(1, "第 2 章 导数")]}
        )
        _write_page_artifacts(pages_dir, 3, [], body="习题 1.1")

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        chapters = [row for row in outline if row.depth == 1]
        exercises = [row for row in outline if (row.meta or {}).get("toc_kind") == "exercise"]
        assert [row.name for row in chapters] == ["第 1 章 极限", "第 2 章 导数"]
        assert [(row.name, row.depth, row.model_ref, row.summary) for row in exercises] == [
            ("习题 1.1", 2, "rule:exercise", "")
        ]
        assert exercises[0].parent_id == chapters[0].node_id
        assert exercises[0].meta["toc_role"] == "reference"
        assert (exercises[0].anchors[0]["page"], exercises[0].anchors[0]["block_indexes"]) == (3, [0])

    def test_exercise_rows_come_after_their_chapters_own_rows(self, session, tmp_path):
        """行序＝书序：习题段在它自己那一章的节之后、下一章之前，`order_hint` 因此是前序读数。"""
        pages_dir = self._scene(
            session,
            tmp_path,
            layout={1: [(1, "第 1 章 极限")], 2: [(2, "1.1 数列极限")], 6: [(1, "第 2 章 导数")]},
        )
        _write_page_artifacts(pages_dir, 4, [], body="习题 1.1")

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        assert [(row.name, row.order_hint) for row in _rows(session, node_kind="outline")] == [
            ("第 1 章 极限", 1),
            ("1.1 数列极限", 2),
            ("习题 1.1", 3),
            ("第 2 章 导数", 4),
        ]

    def test_a_book_without_exercise_labels_keeps_its_row_set(self, session, tmp_path):
        """认不出即零行：本腿不为了「凑一枚习题行」去猜段落（零读数的书行集合与改判前逐字相同）。"""
        self._scene(session, tmp_path)

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        assert [row.name for row in outline] == ["第 1 章 极限", "1.1 数列极限"]
        assert all((row.meta or {}).get("toc_kind") != "exercise" for row in outline)

    def test_a_page_that_already_has_an_exercise_row_is_not_duplicated(self, session, tmp_path):
        """同页去重：模型正腿已把这枚习题段抄成条目时，结构腿不再造第二行（身份位也按习题归一）。"""
        pages_dir = self._scene(
            session, tmp_path, layout={2: [(1, "第 1 章 极限")], 3: [(2, "1.1 数列极限")]}
        )
        _write_page_artifacts(pages_dir, 5, [], body="习题 1.1")
        engine = FakeKnowledgeEngine(
            on_toc_pages=lambda samples: TocPagePick(toc_pages=(1,)),
            on_toc_entries=lambda page_no, text: [
                OutlineEntry(level=1, printed_page=2, name="第 1 章 极限"),
                OutlineEntry(level=2, printed_page=3, name="1.1 数列极限"),
                # 目录行形状是「习题 …… 5」：清洗后名字只剩标签，印刷页码进定锚窗口。
                OutlineEntry(level=2, printed_page=5, name="习题"),
            ],
        )

        run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        exercises = [row for row in outline if (row.meta or {}).get("toc_kind") == "exercise"]
        assert [(row.name, row.model_ref, row.anchors[0]["page"]) for row in exercises] == [
            ("习题", "llm:toc", 5)
        ]
        assert len([row for row in outline if row.name.startswith("习题")]) == 1

    def test_per_section_exercise_labels_produce_no_chapter_tail_row(self, session, tmp_path):
        """b18 形（`KA-46` 章尾判据）：每节后面都挂习题 ⇒ 本腿零行。

        真机 b18 的目录里没有习题行，旧规则取章区间第一枚标签，于是 1.1 的习题被挂到第 1 章末尾、
        锚却停在章中——屏上多出一枚目录里没有的行，位置还错。多枚标签即判「按节布置」。
        """
        pages_dir = self._scene(
            session,
            tmp_path,
            layout={
                1: [(1, "第 1 章 极限")],
                2: [(2, "1.1 数列极限")],
                4: [(2, "1.2 函数极限")],
                6: [(1, "第 2 章 导数")],
            },
        )
        _write_page_artifacts(pages_dir, 3, [], body="习题 1.1")
        _write_page_artifacts(pages_dir, 5, [], body="习题 1.2")

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        assert [row.name for row in outline] == ["第 1 章 极限", "1.1 数列极限", "1.2 函数极限", "第 2 章 导数"]
        assert all((row.meta or {}).get("toc_kind") != "exercise" for row in outline), (
            "目录里没有的习题行不许从章中冒出来"
        )

    def test_a_label_before_the_last_section_is_not_a_chapter_tail_row(self, session, tmp_path):
        """唯一一枚标签落在最后一节的锚页之前 ⇒ 它属于那一节而不是章尾：不成行（`KA-46` 章尾判据）。"""
        pages_dir = self._scene(
            session,
            tmp_path,
            layout={
                1: [(1, "第 1 章 极限")],
                2: [(2, "1.1 数列极限")],
                4: [(2, "1.2 函数极限")],
                6: [(1, "第 2 章 导数")],
            },
        )
        _write_page_artifacts(pages_dir, 3, [], body="习题 1.1")

        run_knowledge_job(
            session, data_root=tmp_path, engine=FakeKnowledgeEngine(), acceptance=_accepted(session)
        )

        outline = _rows(session, node_kind="outline")
        assert [row.name for row in outline] == ["第 1 章 极限", "1.1 数列极限", "1.2 函数极限", "第 2 章 导数"]

    def test_the_exercise_leg_adds_no_model_call_and_no_point(self, session, tmp_path):
        """零模型调用、不进点腿：习题行是附件身份位，内容阶段不为它发问（`KA-46` 的硬边界）。"""
        pages_dir = self._scene(session, tmp_path)
        _write_page_artifacts(pages_dir, 4, [], body="习题")
        engine = FakeKnowledgeEngine()

        run_knowledge_job(
            session, data_root=tmp_path, engine=engine, acceptance=_accepted(session)
        )

        steps = [step for step, _ in engine.calls]
        assert set(steps) <= {"toc_pages", "points", "chapter"}
        assert steps.count("toc_pages") == 1
        assert not [
            call
            for call in engine.calls
            if call[0] == "points" and getattr(call[1], "title", "") == "习题"
        ]


class TestSectionBlockBodies:
    """习题腿的取材副本按块界切（b17 真机）：章界页属于两节，页级取法会把邻章首页整页读两遍。

    真机形状＝`section_spans_of` 给上一节 `end_block=k-1`、下一节 `start_block=k`（同一页），
    Homework 1 的区间止于 p7 块 1、Homework 2 从 p7 块 2 起；整页喂给两节时 `Problem 2.2` …
    被 Homework 1 再切一遍、`Problem 5.1` 落进 Homework 4——同题两行、章归属错。
    """

    @staticmethod
    def _blocks_page(page_no: int, texts: list[str]) -> BlocksPage:
        return BlocksPage(
            page=page_no,
            image_size=(1240, 1754),
            source="mineru",
            blocks=[
                Block(
                    type=BlockType.PARAGRAPH,
                    bbox=[72, 60 + index * 40, 1168, 100 + index * 40],
                    index=index,
                    order=index,
                    text=text,
                )
                for index, text in enumerate(texts)
            ],
        )

    def _evidence(self) -> EvidenceSet:
        pages = (
            EvidencePage(1, "", (), "Problem 1.1: a\nProblem 1.2: b"),
            EvidencePage(2, "", (), "Problem 1.3: c\nProblem 2.1: d"),
        )
        blocks = (
            self._blocks_page(1, ["Problem 1.1: a", "Problem 1.2: b"]),
            self._blocks_page(2, ["Problem 1.3: c", "Problem 2.1: d"]),
        )
        return EvidenceSet("b", "j", 1, 2, pages, (), (), blocks)

    def test_the_end_page_stops_before_the_next_chapter_block(self) -> None:
        evidence = self._evidence()
        span = SectionSpan("ch1", "Homework 1", 1, 2, start_block=0, end_block=0)

        bodies = _section_block_bodies(evidence, span)

        assert [body.page_no for body in bodies] == [1, 2]
        assert "Problem 1.3" in bodies[1].markdown
        assert "Problem 2.1" not in bodies[1].markdown

    def test_the_start_page_drops_the_previous_chapters_tail(self) -> None:
        evidence = self._evidence()
        span = SectionSpan("ch2", "Homework 2", 2, 2, start_block=1)

        bodies = _section_block_bodies(evidence, span)

        assert "Problem 1.3" not in bodies[0].markdown
        assert "Problem 2.1" in bodies[0].markdown

    def test_a_page_without_blocks_falls_back_to_the_markdown(self) -> None:
        """块产物缺页时退回原文整页：取材不因缺一块产物就整节消失。"""
        pages = (EvidencePage(3, "", (), "Problem 9.1: e"),)
        evidence = EvidenceSet("b", "j", 3, 3, pages, (), ())

        bodies = _section_block_bodies(evidence, SectionSpan("ch", "Homework 9", 3, 3))

        assert bodies[0].markdown.strip() == "Problem 9.1: e"
