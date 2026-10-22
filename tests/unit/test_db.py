"""af_* 表模型与仓储层单测（SQLite 内存，不依赖 MySQL，符合测试隔离铁律）。

守护面：四表结构与设计一致（表名/主键/唯一键）、事件与意图两语义分列（OB-1/OB-2）、并发判据
CW-1~CW-6 的反向用例、同步幂等与快照语义、页/编辑/结构状态回写，以及建值不建写者与域值收窄的守护（裁决⑮⑯⑰）。
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.models import (
    AF_TABLES,
    GEN_STATUSES,
    LEGACY_JOB_ID,
    LIFECYCLE_EVENT,
    LIFECYCLE_INTENT,
    OP_KIND_CONFIRM,
    OP_KIND_EDIT,
    OP_KIND_INDEX,
    OP_KIND_INGEST,
    OP_KIND_KP,
    OP_KIND_PARSE,
    OP_KIND_PURGE,
    OP_KIND_STRUCTURE,
    OP_KIND_SYNC,
    AfBook,
    AfKp,
    AfOperation,
    Base,
)
from axiom_flow.db.repositories import (
    EDIT_PAYLOAD_KEYS,
    PROGRESS_DURATION_SECONDS,
    PROGRESS_FINISHED_AT,
    PROGRESS_STARTED_AT,
    STRUCTURE_PAYLOAD_KEYS,
    BookRepository,
    BookSyncRow,
    KpNodeRow,
    KpRepository,
    OperationRepository,
    PageRepository,
    ParseJobRow,
    RegionIntent,
    intent_key_for,
    job_timing,
    region_id_for,
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


def _seed_book(session: Session, book_id: str = "b05") -> AfBook:
    """结构意图的 CW-4 锁要求书目行在位：测试按真实前置建一行，不让锁空转。"""
    book = AfBook(book_id=book_id, synced_at=utc_now(), updated_at=utc_now())
    session.add(book)
    session.flush()
    return book


def _parse(session: Session, job_id: str, book_id: str = "b05", **overrides) -> AfOperation:
    ops = OperationRepository(session)
    ops.create(ParseJobRow(job_id=job_id, book_id=book_id, engine="mineru", **overrides))
    return ops.get(job_id)


def _raw_intents(session: Session, *, op_kind: str, book_id: str, page_no: int | None = None) -> list[AfOperation]:
    """绕开仓储投影直接读意图行：载荷形状与 `lifecycle` 的守护不能只信 `RegionRow`。"""
    conditions = [
        AfOperation.lifecycle == LIFECYCLE_INTENT,
        AfOperation.op_kind == op_kind,
        AfOperation.book_id == book_id,
    ]
    if page_no is not None:
        # 只在给了页码时加该条件：`= NULL` 在 SQL 里永不为真，缺省必须是「不限页」而不是「页为空」。
        conditions.append(AfOperation.page_no == page_no)
    stmt = (
        select(AfOperation)
        .where(*conditions)
        .execution_options(populate_existing=True)
        .order_by(AfOperation.target_id)
    )
    return list(session.scalars(stmt))


def _count(session: Session, model, *conditions) -> int:
    return int(session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0)


class TestTableStructure:
    """表族结构：表名与设计（database-design.md）一致。"""

    def test_table_family_created(self, engine):
        """建表面：`create_all` 落出的表集合恰为 `AF_TABLES`（表集合不本地硬编码）。"""
        assert set(inspect(engine).get_table_names()) == {table.name for table in AF_TABLES}

    def test_af_pages_composite_primary_key(self, engine):
        pk = inspect(engine).get_pk_constraint("af_pages")
        assert pk["constrained_columns"] == ["book_id", "job_id", "page_no"]

    def test_af_operations_only_intent_key_is_unique(self, engine):
        """部分唯一索引的全部承载＝`intent_key` 一列；坐标列不设唯一键是裁决①的形状约束。

        命名 `UniqueConstraint` 在反射里归 unique 而非 index，故本用例断言 unique 集合恰好一项。
        """
        uniques = inspect(engine).get_unique_constraints("af_operations")
        assert [(u["name"], u["column_names"]) for u in uniques] == [("uq_af_operations_intent", ["intent_key"])]

    def test_af_tables_registry_matches(self):
        """逐名 pin（改名/增表须同批改本行与 `database-design.md` 表清单）：集合形状由上条派生守护。"""
        assert {table.name for table in AF_TABLES} == {
            "af_books",
            "af_operations",
            "af_pages",
            "af_kp",
        }


class TestEventIntentDivision:
    """`lifecycle` 分列（OB-1/OB-2）：一张表两用能被守护的唯一依据。"""

    def test_parse_row_op_id_is_the_version_number(self, session):
        """`op_id = job_id` 是本案最强的值形约束：产物目录与生效指针都按值指它。"""
        row = _parse(session, "ab12cd34ef56")

        assert (row.op_id, row.job_id) == ("ab12cd34ef56", "ab12cd34ef56")
        assert row.op_kind == OP_KIND_PARSE
        assert row.lifecycle == LIFECYCLE_EVENT

    def test_event_rows_never_carry_an_intent_key(self, session):
        """流水行 `intent_key` 恒 NULL 才让唯一索引只约束意图行（MySQL 多行 NULL 允许）。"""
        ops = OperationRepository(session)
        parse_row = _parse(session, "job1")
        edited = ops.upsert_edit("b05", "job1", 3, 2, verdict="ok")

        assert parse_row.intent_key is None
        assert session.get(AfOperation, edited.edit_id).intent_key is not None

    def test_structure_intent_is_version_independent(self, session):
        """ADR 0010 决定 1 的原样平移：结构意图跨版本存活，故 `job_id` 恒 `''`。"""
        _seed_book(session)
        OperationRepository(session).replace_page_intents("b05", 3, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])

        row = _raw_intents(session, op_kind=OP_KIND_STRUCTURE, book_id="b05", page_no=3)[0]
        assert (row.lifecycle, row.job_id, row.op_kind) == (LIFECYCLE_INTENT, "", OP_KIND_STRUCTURE)


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

    def test_sync_rerun_preserves_meta_key_set(self, session):
        """裁决⑮的反向判据：两个写者的列域必须不重叠。

        `upsert_many` 若被改成 `merge` 或整行覆写，本用例即转红——同步重跑吃掉人工刚写的读数，
        在功能面上表现为「读数凭空回退」，事后极难归因到一次同步。
        """
        repo = BookRepository(session)
        repo.upsert_many([_row()])
        repo.update_meta("mathanalysis-b05", {"draft_recall": 0.41, "reviewed_by": "swf"})

        repo.upsert_many([_row(title="数学分析原理（第 3 版）")])

        book = repo.get("mathanalysis-b05")
        assert book.title == "数学分析原理（第 3 版）", "同步字段照覆盖，保护区才只有 meta 一列"
        assert book.meta == {"draft_recall": 0.41, "reviewed_by": "swf"}

    def test_update_meta_is_whole_column_overwrite(self, session):
        """CW-5：人工侧不做「读 JSON→改一键→写回」，整列覆写才是可预测的写法。"""
        repo = BookRepository(session)
        repo.upsert_many([_row()])
        repo.update_meta("mathanalysis-b05", {"a": 1, "b": 2})

        assert repo.update_meta("mathanalysis-b05", {"c": 3}) is True
        assert repo.get("mathanalysis-b05").meta == {"c": 3}

        assert repo.update_meta("nope", {"c": 3}) is False

    def test_update_ingest_stamps_ingested_at(self, session):
        """ingest 完成时刻记入 af_books（REQ-097：根 book.json 退役后 `ingested_at` 的唯一事实源）。"""
        repo = BookRepository(session)
        repo.upsert_many([_row()])
        assert repo.get("mathanalysis-b05").ingested_at is None

        repo.update_ingest("mathanalysis-b05", page_count=320, sha256="a" * 64)

        book = repo.get("mathanalysis-b05")
        assert book.ingested_at is not None
        assert book.ingest_status == "ingested"

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


class TestParseVersionLifecycle:
    """af_operations 的 parse 段：版本状态机与进度（六态原值透传，PM-8）。"""

    def test_job_lifecycle(self, session):
        repo = OperationRepository(session)
        repo.create(
            ParseJobRow(
                job_id="job1", book_id="b05", engine="mineru", pages=None, progress={"parsed": 0, "total": 320}
            )
        )

        assert repo.mark_running("job1") is True
        assert repo.update_progress("job1", {"parsed": 5, "total": 320}) is True
        assert repo.mark_finished("job1", status="completed", progress={"parsed": 320, "total": 320}) is True

        job = repo.get("job1")
        assert job is not None
        assert job.status == "completed"
        assert job.pages is None
        assert (job.progress["parsed"], job.progress["total"]) == (320, 320)
        started_at, finished_at = job_timing(job)
        assert started_at is not None and finished_at is not None

    def test_timing_keys_survive_progress_overwrite(self, session):
        """两列折进 `progress` 的代价由「合键随行」兜住：`started_at` 是版本自证，不被读数刷掉。"""
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        repo.mark_running("job1")

        assert repo.update_progress("job1", {"parsed": 9, "total": 320}) is True
        repo.mark_finished("job1", status="completed", progress={"parsed": 320, "total": 320})

        progress = repo.get("job1").progress
        assert PROGRESS_STARTED_AT in progress and PROGRESS_FINISHED_AT in progress
        assert progress["parsed"] == 320
        assert progress[PROGRESS_DURATION_SECONDS] is not None
        assert progress[PROGRESS_DURATION_SECONDS] >= 0

    def test_unparseable_timing_reads_as_none_not_error(self, session):
        """`progress` 是人机共读的读数列：脏值按未知处理，不让任务列表因一行历史值 500。"""
        job = _parse(session, "job1")
        job.progress = {PROGRESS_STARTED_AT: "not-a-time"}
        session.flush()

        assert job_timing(OperationRepository(session).get("job1")) == (None, None)

    def test_get_rejects_non_parse_rows(self, session):
        """`get()` 只认 parse 行：把 edit 的 `op_id` 递进来必须是 None，不能当成版本读。"""
        ops = OperationRepository(session)
        edited = ops.upsert_edit("b05", "job1", 3, 2, verdict="ok")

        assert ops.get(edited.edit_id) is None

    def test_pages_array_round_trips(self, session):
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job2", book_id="b05", engine="mineru", pages=[1, 3, 5]))

        job = repo.get("job2")
        assert job is not None and job.pages == [1, 3, 5]

    def test_job_row_carries_source_snapshot_for_self_proof(self, session):
        """版本与源 PDF 的对应关系落在行上：事后仍可自证这一版解析的是哪份文件。"""
        repo = OperationRepository(session)
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

    def test_parser_signature_round_trips(self, session):
        """签名与 `source_sha256` 同属版本自证：这一版是哪一代解析参数产的，事后仍可判。"""
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job_sig", book_id="b05", engine="mineru", parser_signature="a" * 64))

        job = repo.get("job_sig")
        assert job is not None and job.parser_signature == "a" * 64

    def test_unrecorded_signature_is_unknown_generation_sentinel(self, session):
        """缺省 `''` ＝ 未知代际（如实表示「本行未记录签名」），不是伪造出来的摘要。"""
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job_blank", book_id="b05", engine="mineru"))

        job = repo.get("job_blank")
        assert job is not None and job.parser_signature == ""

    def test_find_in_flight_returns_earliest_row_with_that_signature(self, session):
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="q_old", book_id="b05", engine="mineru", parser_signature="a" * 64))
        repo.create(ParseJobRow(job_id="r_new", book_id="b05", engine="mineru", parser_signature="a" * 64))
        repo.mark_running("r_new")
        repo.create(ParseJobRow(job_id="q_other", book_id="b05", engine="mineru", parser_signature="b" * 64))
        repo.create(
            ParseJobRow(
                job_id="done", book_id="b05", engine="mineru", status="completed", parser_signature="a" * 64
            )
        )
        repo.create(ParseJobRow(job_id="q_elsewhere", book_id="b11", engine="mineru", parser_signature="a" * 64))

        assert repo.find_in_flight("b05", signature="a" * 64).job_id == "q_old"
        assert repo.find_in_flight("b05", signature="b" * 64).job_id == "q_other"
        assert repo.find_in_flight("b05", signature="c" * 64) is None, "不同代际的在飞任务不挡本次提交"
        assert repo.find_in_flight("b11", signature="a" * 64).job_id == "q_elsewhere"

    def test_list_versions_and_missing_updates(self, session):
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        repo.create(ParseJobRow(job_id="job2", book_id="b11", engine="mineru"))

        assert [job.job_id for job in repo.list_versions("b05")] == ["job1"]
        assert repo.mark_running("nope") is False
        assert repo.mark_finished("nope", status="failed") is False

    def test_mark_finished_stores_structured_failed_pages(self, session):
        """ADR 0007 决定 2：失败页以数组落库，长清单不被 500 字符摘要截断。"""
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        repo.mark_running("job1")

        repo.mark_finished("job1", status="partial", error="失败页：7", failed_pages=[7])

        job = repo.get("job1")
        assert job is not None and job.failed_pages == [7]

    def test_failed_pages_defaults_to_empty_list(self, session):
        job = _parse(session, "job1")

        assert job.failed_pages == []

    def test_count_active_is_the_only_in_flight_criterion(self, session):
        """在飞只由 `queued`/`running` 定义（ADR 0007）：空作用域不得退化成全表计数（purge 闸门）。"""
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        repo.create(ParseJobRow(job_id="job2", book_id="b11", engine="mineru"))

        assert repo.count_active() == 2
        assert repo.count_active(["b05"]) == 1
        assert repo.count_active([]) == 0
        assert repo.count_active(["nope"]) == 0


class TestStartupReconciliation:
    """ADR 0007 决定 4：启动期将遗留活动态归一为 `interrupted`（单实例前提）。"""

    def test_queued_and_running_rows_become_interrupted(self, session):
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="running-1", book_id="b05", engine="mineru"))
        repo.mark_running("running-1")
        repo.create(ParseJobRow(job_id="queued-1", book_id="b11", engine="mineru"))

        affected = repo.reconcile_interrupted()

        assert {(job.job_id, job.book_id) for job in affected} == {("running-1", "b05"), ("queued-1", "b11")}
        assert repo.get("running-1").status == "interrupted"
        assert repo.get("queued-1").status == "interrupted"
        assert "服务重启中断" in repo.get("queued-1").error
        assert job_timing(repo.get("queued-1"))[1] is not None

    def test_terminal_rows_are_untouched(self, session):
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="done-1", book_id="b05", engine="mineru"))
        repo.mark_finished("done-1", status="partial", error="失败页：2", failed_pages=[2])

        assert repo.reconcile_interrupted() == []

        job = repo.get("done-1")
        assert job.status == "partial" and job.error == "失败页：2" and job.failed_pages == [2]

    def test_reconcile_is_idempotent(self, session):
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="running-1", book_id="b05", engine="mineru"))
        repo.mark_running("running-1")

        repo.reconcile_interrupted()

        assert repo.reconcile_interrupted() == []


class TestBookProgressColumns:
    """进度列与结局列分列（ADR 0007 决定 3）：`af_books` 不表达结局细节。"""

    def test_update_parse_keeps_progress_and_result_apart(self, session):
        repo = BookRepository(session)
        repo.upsert_many([_row()])

        repo.update_parse("mathanalysis-b05", parse_status="parsing", pages_done=3)
        assert repo.get("mathanalysis-b05").last_parse_result == "", "运行中不产出结局"

        repo.update_parse("mathanalysis-b05", parse_status="pending", pages_done=3, last_parse_result="partial")
        book = repo.get("mathanalysis-b05")
        assert (book.parse_status, book.last_parse_result, book.pages_done) == ("pending", "partial", 3)

    def test_update_parse_without_result_preserves_previous_result(self, session):
        repo = BookRepository(session)
        repo.upsert_many([_row()])
        repo.update_parse("mathanalysis-b05", parse_status="pending", pages_done=1, last_parse_result="failed")

        repo.update_parse("mathanalysis-b05", parse_status="parsing", pages_done=0)

        assert repo.get("mathanalysis-b05").last_parse_result == "failed"


class TestParseJobListQuery:
    """`GET /api/v1/parse-jobs` 的仓储面（PLAN-008 W2）：过滤、稳定倒序与过滤后总数。

    契约口径见 `docs/architecture/api.md` ③ 类同名节。
    """

    def _seed(self, repo: OperationRepository, job_id: str, *, book_id: str, status: str, day: int) -> None:
        repo.create(ParseJobRow(job_id=job_id, book_id=book_id, engine="mineru"))
        repo.mark_finished(job_id, status=status)
        # 建任务行的 created_at 由仓储取当前时间，倒序判据需要可重复的相对时序
        repo.get(job_id).created_at = datetime(2026, 9, day, 12, 0, tzinfo=UTC)
        repo.session.flush()

    def test_list_all_returns_rows_and_total_of_filtered_set(self, session):
        repo = OperationRepository(session)
        self._seed(repo, "job1", book_id="b05", status="completed", day=20)
        self._seed(repo, "job2", book_id="b05", status="failed", day=21)
        self._seed(repo, "job3", book_id="b11", status="completed", day=22)

        rows, total = repo.list_all()

        assert [job.job_id for job in rows] == ["job3", "job2", "job1"]
        assert total == 3

    def test_list_all_excludes_intent_rows(self, session):
        """一张表两用后列表查询必须自带 `op_kind` 过滤：混入意图行会造出不存在的解析版本。"""
        repo = OperationRepository(session)
        self._seed(repo, "job1", book_id="b05", status="completed", day=20)
        repo.upsert_edit("b05", "job1", 1, 0, verdict="ok")
        repo.upsert_intent(op_kind=OP_KIND_CONFIRM, book_id="b05", job_id="job1", page_no=1)

        rows, total = repo.list_all()

        assert [job.job_id for job in rows] == ["job1"]
        assert total == 1

    def test_status_filter_is_multi_value_or(self, session):
        """任务中心「只列活跃」天然是两个状态值，单值过滤会逼客户端发两次请求。"""
        repo = OperationRepository(session)
        repo.create(ParseJobRow(job_id="q1", book_id="b05", engine="mineru"))
        repo.create(ParseJobRow(job_id="r1", book_id="b05", engine="mineru"))
        repo.mark_running("r1")
        self._seed(repo, "c1", book_id="b05", status="completed", day=20)

        rows, total = repo.list_all(status=["queued", "running"])

        assert {job.job_id for job in rows} == {"q1", "r1"}
        assert total == 2

    def test_status_filter_with_unknown_value_returns_empty(self, session):
        """枚举合法性由 API 层判 422；仓储面对未知值给空集而非全量（不静默放宽成无过滤）。"""
        repo = OperationRepository(session)
        self._seed(repo, "job1", book_id="b05", status="completed", day=20)

        rows, total = repo.list_all(status=["not-a-status"])

        assert rows == [] and total == 0

    def test_book_id_filter_on_missing_book_is_empty_not_error(self, session):
        """已删书与不存在书都走这一条：列表过滤不判资源存在性（API 侧返回 200 空列表）。"""
        repo = OperationRepository(session)
        self._seed(repo, "job1", book_id="b05", status="completed", day=20)

        rows, total = repo.list_all(book_id="nope")

        assert rows == [] and total == 0

    def test_same_instant_orders_by_job_id_desc(self, session):
        """`MySQL DATETIME` 只到秒：同秒任务仅按时间排序不可重复，翻页会漏行或重行。"""
        repo = OperationRepository(session)
        same = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
        for job_id in ("aaa", "bbb", "ccc"):
            repo.create(ParseJobRow(job_id=job_id, book_id="b05", engine="mineru"))
            repo.get(job_id).created_at = same
        session.flush()

        rows, _ = repo.list_all()

        assert [job.job_id for job in rows] == ["ccc", "bbb", "aaa"]

    def test_pagination_slices_rows_but_total_stays_filtered_count(self, session):
        repo = OperationRepository(session)
        for index in range(5):
            self._seed(repo, f"job{index}", book_id="b05", status="completed", day=10 + index)

        rows, total = repo.list_all(limit=2, offset=2)

        assert [job.job_id for job in rows] == ["job2", "job1"]
        assert total == 5, "total 是分页前全集，客户端据此判定还有没有下一页"

    def test_offset_beyond_total_returns_empty_rows_with_total(self, session):
        repo = OperationRepository(session)
        self._seed(repo, "job1", book_id="b05", status="completed", day=20)

        rows, total = repo.list_all(limit=10, offset=50)

        assert rows == [] and total == 1


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

    def test_new_result_index_columns_default_to_unknown(self, session):
        """新增四列的缺省即「未判/未记录」：写者未上线时读侧不能看到伪造读数（OB-5 同源）。"""
        page = PageRepository(session).upsert("b05", "job1", 1, status="parsed", source="mineru")

        assert page.confirm_state == "unconfirmed"
        assert page.section_kind == ""
        assert page.artifacts is None
        assert page.meta is None

    def test_upsert_same_page_overwrites(self, session):
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="failed", error="超时")
        repo.upsert("b05", "job1", 1, status="parsed", source="mineru")

        assert repo.get("b05", "job1", 1).status == "parsed"
        assert len(repo.list_for_book("b05", "job1")) == 1

    def test_same_page_in_two_versions_stays_independent(self, session):
        """同一页在不同版本可有独立状态：旧版结果不被新版覆盖（主键纳入 job_id 的目的）。"""
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


class TestPageStateQuery:
    """`GET /api/v1/books/{book_id}/pages` 的仓储面（PLAN-008 W2）：轻量逐页状态投影。"""

    def test_list_states_projects_only_contract_columns(self, session):
        """清单行不带 quality/整页内容：正文仍走单页端点，486 页全集一次返回必须轻。"""
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="parsed", source="mineru", quality={"empty_block_ratio": 0.0})
        repo.upsert("b05", "job1", 2, status="failed", error="窗口 1-56：超时")

        states = repo.list_states("b05", "job1")

        assert [(s.page_no, s.status, s.error) for s in states] == [
            (1, "parsed", ""),
            (2, "failed", "窗口 1-56：超时"),
        ]

    def test_list_states_is_version_scoped(self, session):
        """作用域按 job_id：跨版本混读会让旧版状态冒充生效版结论（主键含 job_id 的目的）。"""
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 1, status="parsed")
        repo.upsert("b05", "job2", 1, status="failed", error="引擎异常")
        repo.upsert("b11", "job1", 7, status="parsed")

        assert [(s.page_no, s.status) for s in repo.list_states("b05", "job1")] == [(1, "parsed")]
        assert [(s.page_no, s.status) for s in repo.list_states("b05", "job2")] == [(1, "failed")]

    def test_list_states_omits_pages_without_rows(self, session):
        """仓储只给有页行的页；`1..total` 的补全（无行页 = `pending`）由读取面做。"""
        repo = PageRepository(session)
        repo.upsert("b05", "job1", 3, status="parsed")

        assert [s.page_no for s in repo.list_states("b05", "job1")] == [3]


class TestIntentKey:
    """`intent_key` 口径（CW-3 的唯一承载）：坐标集 → 键的纯函数，形状必须被钉住。"""

    def test_key_is_deterministic_and_coordinate_sensitive(self):
        common = {"op_kind": OP_KIND_EDIT, "book_id": "b05", "job_id": "job1", "page_no": 3}
        first = intent_key_for(block_index=2, **common)

        assert first == intent_key_for(block_index=2, **common)
        assert first != intent_key_for(block_index=3, **common)
        # 同一块在不同版本各自成立：块下标语义随版本产物重排，共用键即污染编辑结论
        assert first != intent_key_for(op_kind=OP_KIND_EDIT, book_id="b05", job_id="job2", page_no=3, block_index=2)

    def test_op_kind_participates_in_the_key(self):
        """种类进键：edit 与 confirm 落在同一坐标必须是两行，否则确认覆盖编辑。"""
        common = {"book_id": "b05", "job_id": "job1", "page_no": 3, "block_index": 2}
        assert intent_key_for(op_kind=OP_KIND_EDIT, **common) != intent_key_for(op_kind=OP_KIND_CONFIRM, **common)

    def test_null_coordinates_render_as_empty_segment(self):
        """NULL 落成空段而不是字面 `None`：可空性不得进身份字符串，且缺省与显式 NULL 同键。"""
        canonical = hashlib.md5(b"structure:b05::::rgn_a").hexdigest()[:16]
        kwargs = {"op_kind": OP_KIND_STRUCTURE, "book_id": "b05", "target_id": "rgn_a"}

        assert intent_key_for(**kwargs) == canonical
        assert intent_key_for(page_no=None, block_index=None, **kwargs) == canonical

    def test_missing_coordinate_is_not_coordinate_zero(self):
        no_page = intent_key_for(op_kind=OP_KIND_CONFIRM, book_id="b05", job_id="job1")
        assert no_page != intent_key_for(op_kind=OP_KIND_CONFIRM, book_id="b05", job_id="job1", page_no=0)


class TestEditIntents:
    """edit 意图行：同版本同块覆盖更新（幂等键绑定解析版本）。"""

    def test_upsert_overwrites_same_block(self, session):
        ops = OperationRepository(session)
        ops.upsert_edit("b05", "job1", 3, 2, block_type="formula", verdict="bad", note="公式错")
        ops.upsert_edit(
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

        edits = ops.list_edits_for_page("b05", "job1", 3)
        assert len(edits) == 1
        assert edits[0].verdict == "ok"
        assert edits[0].corrected_text == "x^2 + y^2"
        assert edits[0].corrected_bbox == [1, 2, 3, 4]

    def test_identity_and_first_write_survive_redeclaration(self, session):
        """CW-3 的可观测形式：重复声明不换 `op_id`、不丢首次落库时刻（＝人工判定时刻）。

        身份漂移会让根侧持有的编辑 id 失联；`edited_at` 变成「最后一次提交」就不再能回答
        「这条结论是人什么时候判的」。
        """
        ops = OperationRepository(session)
        first = ops.upsert_edit("b05", "job1", 3, 2, verdict="ok")
        session.expire_all()

        second = ops.upsert_edit("b05", "job1", 3, 2, verdict="bad", note="改判")

        assert second.edit_id == first.edit_id
        assert second.edited_at == first.edited_at
        assert second.updated_at >= first.updated_at
        assert len(ops.list_edits_for_page("b05", "job1", 3)) == 1

    def test_edit_payload_shape_is_closed(self, session):
        """载荷键闭集：自由键会让「什么操作」失去定义，`note` 按裁决提到实体列。"""
        ops = OperationRepository(session)
        edited = ops.upsert_edit("b05", "job1", 3, 2, verdict="ok", note="备注在实体列")

        row = session.get(AfOperation, edited.edit_id)
        assert set(row.payload) == set(EDIT_PAYLOAD_KEYS)
        assert row.note == "备注在实体列"

    def test_same_block_in_two_versions_stays_independent(self, session):
        ops = OperationRepository(session)
        ops.upsert_edit("b05", "job1", 3, 2, verdict="ok", corrected_text="旧版修正")
        ops.upsert_edit("b05", "job2", 3, 2, verdict="bad", note="新版判为不可用")

        assert ops.get_edit("b05", "job1", 3, 2).corrected_text == "旧版修正"
        assert ops.get_edit("b05", "job2", 3, 2).verdict == "bad"
        assert len(ops.list_edits_for_page("b05", "job1", 3)) == 1

    def test_list_for_book_scopes_to_version(self, session):
        ops = OperationRepository(session)
        ops.upsert_edit("b05", "job1", 5, 1, verdict="ok")
        ops.upsert_edit("b05", "job1", 2, 3, verdict="bad")
        ops.upsert_edit("b11", "job1", 1, 0, verdict="ok")

        assert [(e.page_no, e.block_index) for e in ops.list_edits_for_book("b05", "job1")] == [(2, 3), (5, 1)]

    def test_delete_intents_keeps_event_rows(self, session):
        """书目删除带走意图、留下流水：做过什么不可撤回，人工结论不能成为无主行。"""
        ops = OperationRepository(session)
        _parse(session, "job1")
        ops.upsert_edit("b05", "job1", 1, 0, verdict="ok")
        _seed_book(session)
        ops.replace_page_intents("b05", 3, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])
        ops.upsert_edit("b11", "job9", 1, 0, verdict="ok")

        assert ops.delete_intents_for_book("b05") == 2
        assert ops.list_edits_for_book("b05", "job1") == []
        assert ops.list_regions_for_book("b05") == []
        assert ops.get("job1") is not None, "事件行保留为审计线索"
        assert ops.list_edits_for_book("b11", "job9") != []

    def test_delete_for_book_drops_events_too(self, session):
        """管理面一键清除是整族重建的前置：与书目删除相反，不留半套历史。"""
        ops = OperationRepository(session)
        _parse(session, "job1")
        _parse(session, "job2", book_id="b11")
        ops.upsert_edit("b05", "job1", 1, 0, verdict="ok")

        assert ops.delete_for_book("b05") == 2
        assert ops.list_versions("b05") == []
        assert [job.job_id for job in ops.list_versions("b11")] == ["job2"]


class TestEditCountByPage:
    """`edit_count` 的仓储面（PLAN-008 W2 / 承载 ARCH-006「页清单暴露编辑存在性」）。"""

    def test_counts_group_by_page_within_version(self, session):
        ops = OperationRepository(session)
        ops.upsert_edit("b05", "job1", 1, 0, verdict="ok")
        ops.upsert_edit("b05", "job1", 1, 2, verdict="bad")
        ops.upsert_edit("b05", "job1", 3, 1, verdict="ok")
        ops.upsert_edit("b05", "job2", 1, 0, verdict="ok")
        ops.upsert_edit("b11", "job1", 9, 0, verdict="ok")

        assert ops.count_edits_by_page("b05", "job1") == {1: 2, 3: 1}

    def test_repeated_edit_on_same_block_counts_once(self, session):
        """同版本同块覆盖更新（唯一键）：编辑计数不能因重复提交而虚增。"""
        ops = OperationRepository(session)
        ops.upsert_edit("b05", "job1", 1, 0, verdict="ok")
        ops.upsert_edit("b05", "job1", 1, 0, verdict="bad", note="改判")

        assert ops.count_edits_by_page("b05", "job1") == {1: 1}

    def test_book_without_edits_returns_empty_mapping(self, session):
        assert OperationRepository(session).count_edits_by_page("b05", "job1") == {}


class TestStructureIntents:
    """structure 意图行：全量声明即幂等，缺席即撤销（ADR 0010）。"""

    def test_absent_intent_is_withdrawn(self, session):
        _seed_book(session)
        ops = OperationRepository(session)
        keep = RegionIntent(op="add", bbox=[10, 20, 30, 40])
        drop = RegionIntent(op="delete", anchor_bbox=[5, 5, 9, 9])

        ops.replace_page_intents("b05", 3, [keep, drop])
        after = ops.replace_page_intents("b05", 3, [keep])

        assert [r.op for r in after] == ["add"]
        assert len(after) == 1, "撤销必须真实删除缺席行，否则下一次受理仍按旧意图跑"

    def test_repeated_declaration_is_idempotent(self, session):
        _seed_book(session)
        ops = OperationRepository(session)
        intents = [RegionIntent(op="add", bbox=[10, 20, 30, 40], kind="formula")]

        first = ops.replace_page_intents("b05", 3, intents)
        second = ops.replace_page_intents("b05", 3, intents)

        assert [r.region_id for r in first] == [r.region_id for r in second]
        assert len(second) == 1, "重复提交既不能清空也不能翻倍"

    def test_region_id_is_derived_and_unchanged(self, session):
        """派生式与旧 `af_page_regions.region_id` 逐字相同：根侧长期持有的定位符不因换表而失效。"""
        _seed_book(session)
        ops = OperationRepository(session)
        intent = RegionIntent(op="add", bbox=[10, 20, 30, 40])

        rows = ops.replace_page_intents("b05", 3, [intent])

        assert rows[0].region_id == region_id_for("b05", 3, intent)
        assert rows[0].region_id.startswith("rgn_")

    def test_merge_member_order_is_not_identity(self, session):
        """成员数组序是阅读序而非身份：调序必须仍是同一合并，否则撤销静默丢失。"""
        _seed_book(session)
        ops = OperationRepository(session)
        a = RegionIntent(op="merge", members=[[1, 1, 2, 2], [3, 3, 4, 4]])
        b = RegionIntent(op="merge", members=[[3, 3, 4, 4], [1, 1, 2, 2]])

        assert region_id_for("b05", 3, a) == region_id_for("b05", 3, b)
        assert len(ops.replace_page_intents("b05", 3, [a, b])) == 1

    def test_note_and_kind_are_not_identity(self, session):
        """改备注不是新意图：身份只由操作种类与几何决定。"""
        _seed_book(session)
        ops = OperationRepository(session)

        first = ops.replace_page_intents(
            "b05", 3, [RegionIntent(op="add", bbox=[10, 20, 30, 40], kind="figure", note="初判")]
        )
        after = ops.replace_page_intents(
            "b05", 3, [RegionIntent(op="add", bbox=[10, 20, 30, 40], kind="formula", note="改判")]
        )

        assert [r.region_id for r in first] == [r.region_id for r in after]
        assert (after[0].kind, after[0].note) == ("formula", "改判")

    def test_geometry_representation_is_not_identity(self, session):
        """int 与 float 的表示差异不进身份：同一框换个来源类型不能变成两条意图。"""
        as_int = RegionIntent(op="add", bbox=[10, 20, 30, 40])
        as_float = RegionIntent(op="add", bbox=[10.0, 20.0, 30.0, 40.0])

        assert region_id_for("b05", 3, as_int) == region_id_for("b05", 3, as_float)

    def test_intents_survive_a_new_version(self, session):
        """结构意图跨版本存活：新解析版本出现后，页级人工框不得消失。"""
        _seed_book(session)
        ops = OperationRepository(session)
        declared = ops.replace_page_intents("b05", 3, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])
        ops.create(ParseJobRow(job_id="job_new", book_id="b05", engine="mineru"))

        assert [r.region_id for r in ops.list_regions_for_page("b05", 3)] == [r.region_id for r in declared]

    def test_structure_payload_shape_is_closed(self, session):
        _seed_book(session)
        ops = OperationRepository(session)
        ops.replace_page_intents("b05", 3, [RegionIntent(op="add", bbox=[1, 2, 3, 4], note="备注在实体列")])

        row = _raw_intents(session, op_kind=OP_KIND_STRUCTURE, book_id="b05", page_no=3)[0]
        assert set(row.payload) == set(STRUCTURE_PAYLOAD_KEYS)
        assert row.note == "备注在实体列"

    def test_list_regions_for_book_orders_by_page_then_id(self, session):
        _seed_book(session)
        ops = OperationRepository(session)
        ops.replace_page_intents("b05", 7, [RegionIntent(op="add", bbox=[1, 1, 2, 2])])
        ops.replace_page_intents(
            "b05", 3,
            [RegionIntent(op="add", bbox=[3, 3, 4, 4]), RegionIntent(op="delete", anchor_bbox=[1, 1, 2, 2])],
        )
        ops.replace_page_intents("b11", 1, [RegionIntent(op="add", bbox=[1, 1, 2, 2])])

        listed = ops.list_regions_for_book("b05")
        assert [r.page_no for r in listed] == [3, 3, 7]
        assert [r.region_id for r in listed] == sorted(r.region_id for r in listed)

    def test_delete_regions_for_page(self, session):
        _seed_book(session)
        ops = OperationRepository(session)
        ops.replace_page_intents("b05", 3, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])
        ops.replace_page_intents("b05", 4, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])

        assert ops.delete_regions_for_page("b05", 3) == 1
        assert ops.list_regions_for_page("b05", 3) == []
        assert len(ops.list_regions_for_page("b05", 4)) == 1

    def test_structure_rows_carry_no_version_scope(self, session):
        """ADR 0010 决定 1 折叠后的唯一落点：structure 行 `job_id` 恒 `''`。

        一旦写入方给出真实 job_id，意图就退化成版本内覆盖层——换版即丢人工框。
        """
        _seed_book(session)
        ops = OperationRepository(session)
        ops.replace_page_intents("b05", 3, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])
        ops.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))

        rows = _raw_intents(session, op_kind=OP_KIND_STRUCTURE, book_id="b05")

        assert rows and {row.job_id for row in rows} == {""}

    def test_empty_declaration_clears_only_its_page(self, session):
        """空声明＝撤销整页意图，但不跨页外溢：撤销的作用域就是声明的那一页。"""
        _seed_book(session)
        ops = OperationRepository(session)
        ops.replace_page_intents("b05", 1, [RegionIntent(op="add", bbox=[0, 0, 10, 10])])
        ops.replace_page_intents("b05", 2, [RegionIntent(op="add", bbox=[0, 0, 10, 10])])

        assert ops.replace_page_intents("b05", 1, []) == []

        assert ops.list_regions_for_page("b05", 1) == []
        assert len(ops.list_regions_for_page("b05", 2)) == 1

    def test_duplicate_geometry_in_one_declaration_is_one_row(self, session):
        """同一框在一次声明里出现两次 ⇒ 一行：去重由服务端身份承载，不要求客户端自律。"""
        _seed_book(session)
        ops = OperationRepository(session)

        rows = ops.replace_page_intents(
            "b05", 1,
            [RegionIntent(op="add", bbox=[0, 0, 9, 9]), RegionIntent(op="add", bbox=[0, 0, 9, 9], note="重复声明")],
        )

        assert len(rows) == 1 and rows[0].note == "重复声明"

    def test_three_ops_keep_their_own_geometry(self, session):
        """add 看自身框、delete 看锚、merge 看成员集：三种意图各持一个稳定身份，几何原样读回。"""
        _seed_book(session)
        ops = OperationRepository(session)

        rows = ops.replace_page_intents(
            "b05", 3,
            [
                RegionIntent(op="add", bbox=[72, 900, 600, 1010], kind="exercise"),
                RegionIntent(op="delete", anchor_bbox=[600, 96, 1100, 210], note="图注误检"),
                RegionIntent(op="merge", members=[[72, 96, 540, 132], [72, 150, 540, 175]]),
            ],
        )

        by_op = {row.op: row for row in rows}
        assert set(by_op) == {"add", "delete", "merge"}
        assert by_op["add"].bbox == [72, 900, 600, 1010] and by_op["add"].kind == "exercise"
        assert by_op["delete"].anchor_bbox == [600, 96, 1100, 210] and by_op["delete"].note == "图注误检"
        assert by_op["merge"].members == [[72, 96, 540, 132], [72, 150, 540, 175]]
        assert len({row.region_id for row in rows}) == 3

    def test_region_id_shape_and_participants(self):
        """纯函数面：`rgn_<12 hex>`，页码与 op 参与身份，kind/note 不参与（改备注不是新意图）。"""
        intent = RegionIntent(op="add", bbox=[0, 0, 10, 10])

        derived = region_id_for("b05", 1, intent)

        assert re.fullmatch(r"rgn_[0-9a-f]{12}", derived)
        assert region_id_for("b05", 1, RegionIntent(op="add", bbox=[0, 0, 10, 10], note="换备注")) == derived
        assert region_id_for("b05", 2, intent) != derived, "页码参与身份"
        assert region_id_for("b05", 1, RegionIntent(op="delete", anchor_bbox=[0, 0, 10, 10])) != derived


class TestConcurrencyCriteria:
    """CW-1/CW-2 的反向判据：换表后「谁能改哪一行」仍可被证伪。"""

    def test_non_parse_rows_are_never_advanced(self, session):
        """CW-1：状态机回写只作用在 parse 行——把意图行的 id 递进来必须一个字节都不动。"""
        ops = OperationRepository(session)
        edited = ops.upsert_edit("b05", "job1", 3, 2, verdict="ok")
        session.expire_all()

        assert ops.mark_running(edited.edit_id) is False
        assert ops.mark_finished(edited.edit_id, status="failed") is False
        assert ops.update_progress(edited.edit_id, {"parsed": 1}) is False

        row = session.get(AfOperation, edited.edit_id)
        assert (row.lifecycle, row.status, row.progress) == (LIFECYCLE_INTENT, "ok", {})

    def test_late_write_after_terminal_is_dropped(self, session):
        """CW-2：终态之后迟到的进度/结局不写也不报错——worker 被取消时的迟到回写是常态。"""
        ops = OperationRepository(session)
        ops.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        ops.mark_finished("job1", status="failed", error="引擎超时")

        assert ops.update_progress("job1", {"parsed": 3}) is False
        assert ops.mark_finished("job1", status="completed", error="") is False

        job = ops.get("job1")
        assert (job.status, job.error) == ("failed", "引擎超时")

    def test_reconcile_after_reconcile_touches_nothing(self, session):
        ops = OperationRepository(session)
        ops.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        ops.mark_running("job1")
        ops.reconcile_interrupted()

        assert ops.reconcile_interrupted() == []
        assert ops.get("job1").status == "interrupted"

    def test_parse_row_keeps_no_intent_key(self, session):
        """流水行永不获得幂等键：一旦写入，唯一键会把「发生过两次」压成一行，审计即失真。"""
        ops = OperationRepository(session)
        ops.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru"))
        ops.mark_running("job1")
        ops.mark_finished("job1", status="completed")

        assert ops.get("job1").intent_key is None


class TestBuiltWithoutWriter:
    """裁决⑮⑯⑰的四条守护：无写者值与域外值都不得被写出（形状封闭，但梳理与检索语义未落地）。"""

    def _exercise_every_writer(self, session) -> None:
        repo = BookRepository(session)
        repo.upsert_many([_row("b05")])
        ops = OperationRepository(session)
        ops.create(ParseJobRow(job_id="job1", book_id="b05", engine="mineru", parser_signature="a" * 64))
        ops.mark_running("job1")
        ops.update_progress("job1", {"parsed": 1, "total": 2})
        ops.mark_finished("job1", status="completed", failed_pages=[])
        PageRepository(session).upsert("b05", "job1", 1, status="parsed", source="mineru")
        ops.upsert_edit("b05", "job1", 1, 0, verdict="ok")
        ops.replace_page_intents("b05", 1, [RegionIntent(op="add", bbox=[1, 2, 3, 4])])
        kp = KpRepository(session)
        kp.create(KpNodeRow(node_id="afkp-x1", book_id="b05", name="极限", norm_key="极限"))
        kp.update("afkp-x1", {"summary": "一句话"})

    def test_embedding_signature_stays_empty(self, session):
        """检索索引代际与解析代际分列：本轮无写者，非空值即意味着有代码越界写了检索面。"""
        self._exercise_every_writer(session)

        assert _count(session, AfOperation, AfOperation.embedding_signature != "") == 0

    def test_index_op_kind_is_never_written(self, session):
        self._exercise_every_writer(session)

        assert _count(session, AfOperation, AfOperation.op_kind == OP_KIND_INDEX) == 0

    def test_remaining_writerless_op_kinds_are_never_written(self, session):
        """闭集里其余五个无写者值（`confirm`／`ingest`／`sync`／`purge`／`kp`）同样不得被写出。

        权威文档把「守护断言计数恒 0」写给了**全部**无写者值而不只是 `index`，本断言使那句话成立。
        粒度选仓储层：API 面没有独立的插入腿，它只能经这里的生产写入口落库。
        `kp`（梳理批次流水）的行语义已由设计篇定义，但写者属实现批，故本轮同受本条约束。
        """
        self._exercise_every_writer(session)

        for kind in (OP_KIND_CONFIRM, OP_KIND_INGEST, OP_KIND_SYNC, OP_KIND_PURGE, OP_KIND_KP):
            assert _count(session, AfOperation, AfOperation.op_kind == kind) == 0, kind

    def test_adopted_is_no_longer_a_legal_gen_status(self, session):
        """`gen_status` 闭集已删 `adopted`（人工终态只由 `confirmed` 表达）：域外值不得出现。"""
        assert "adopted" not in GEN_STATUSES
        self._exercise_every_writer(session)

        assert _count(session, AfKp, ~AfKp.gen_status.in_(GEN_STATUSES)) == 0


class TestKpRepository:
    """af_kp：机器列与人工列分列（KP-8）+ `rev` 乐观并发（CW-6）。"""

    def _node(self, session: Session, node_id: str = "afkp-a1", **overrides) -> AfKp:
        payload = {"node_id": node_id, "book_id": "b05", "name": "极限的 ε-δ 定义", "norm_key": "极限的ε-δ定义"}
        payload.update(overrides)
        return KpRepository(session).create(KpNodeRow(**payload))

    def test_list_for_book_scopes_and_filters(self, session):
        kp = KpRepository(session)
        self._node(session, "afkp-a1", node_kind="point", order_hint=2)
        self._node(session, "afkp-a2", node_kind="outline", order_hint=1)
        self._node(session, "afkp-b1", book_id="b11", order_hint=3)

        assert [n.node_id for n in kp.list_for_book("b05")] == ["afkp-a2", "afkp-a1"]
        assert [n.node_id for n in kp.list_for_book("b05", node_kind="point")] == ["afkp-a1"]
        assert [n.node_id for n in kp.list_for_book("b05", gen_status="rejected")] == []

    def test_update_increments_rev(self, session):
        kp = KpRepository(session)
        self._node(session)

        result = kp.update("afkp-a1", {"summary": "一句话总结"})

        assert result.ok and not result.rev_conflict
        assert (result.node.rev, result.node.summary) == (1, "一句话总结")

    def test_stale_if_rev_is_a_conflict_not_a_miss(self, session):
        """人已不在该页时提交的旧表单不得静默覆盖新结论：409 与 404 是两个结局。"""
        kp = KpRepository(session)
        self._node(session)
        kp.update("afkp-a1", {"summary": "他人已改"})

        result = kp.update("afkp-a1", {"summary": "旧表单"}, if_rev=0)

        assert (result.ok, result.rev_conflict) == (False, True)
        assert kp.get("afkp-a1").summary == "他人已改"

    def test_missing_node_is_not_reported_as_conflict(self, session):
        result = KpRepository(session).update("afkp-nope", {"summary": "x"})

        assert (result.ok, result.rev_conflict, result.node) == (False, False, None)

    def test_write_whitelist_protects_identity_and_human_columns(self, session):
        """通用写入口拒绝越列：身份不可变、序号只由写入本身递增、确认位另走 `confirm`。"""
        kp = KpRepository(session)
        self._node(session)

        for column in ("node_id", "book_id", "rev", "confirmed", "created_at"):
            with pytest.raises(ValueError, match="af_kp"):
                kp.update("afkp-a1", {column: "x"})

    def test_confirm_is_the_only_human_gate(self, session):
        kp = KpRepository(session)
        self._node(session)

        assert kp.confirm("afkp-a1", confirmed=True).ok is True
        assert (kp.get("afkp-a1").confirmed, kp.get("afkp-a1").rev) == (1, 1)

        assert kp.confirm("afkp-a1", confirmed=False, if_rev=0).rev_conflict is True
        assert kp.get("afkp-a1").confirmed == 1

    def test_gen_status_is_machine_only(self, session):
        """机器写 `gen_status`、人工写 `confirmed`：两列各由一个入口负责，互不覆据。"""
        kp = KpRepository(session)
        self._node(session)

        assert kp.update("afkp-a1", {"gen_status": "rejected"}).ok is True
        assert kp.get("afkp-a1").gen_status == "rejected"
        assert kp.get("afkp-a1").confirmed == 0

    def test_delete_for_book_drops_nodes(self, session):
        """梳理作用域就是一册书：删书带走节点，不留无主结论。"""
        kp = KpRepository(session)
        self._node(session, "afkp-a1")
        self._node(session, "afkp-a2", book_id="b11")

        assert kp.delete_for_book("b05") == 1
        assert kp.list_for_book("b05") == []
        assert len(kp.list_for_book("b11")) == 1

    def test_long_narrative_lives_outside_the_table(self, session):
        """裁决⑧：长文落文件，本表只存根内相对路径（单段名），列内不存正文。"""
        kp = KpRepository(session)
        self._node(session, narrative_ref="afkp-a1.md")

        assert kp.get("afkp-a1").narrative_ref == "afkp-a1.md"
        assert kp.get("afkp-a1").summary == ""
