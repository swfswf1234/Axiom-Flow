"""课程级章腿的共享表锁、幂等回读与文件／三列同事务通路（PLAN-034 W4）。

隔离：临时 SQLite 文件库＋临时数据根＋假引擎；不访问 MySQL、公网、模型服务或真实数据根。
设计关联：docs/history/plans/2026-11/2026-11-10-merge-chapter-text-generation.md（W0／W4）
被测代码：src/axiom_flow/db/shared_knowledge.py、src/axiom_flow/orchestrator/chapter_text.py
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow import schemas
from axiom_flow.db.shared_knowledge import SharedKnowledgeRepository
from axiom_flow.engines.base import ChapterReply, EngineError
from axiom_flow.orchestrator.chapter_text import ChapterTextError, merge_chapter_text
from axiom_flow.orchestrator.knowledge import chapter_text_evidence_sha256
from tests.support.shared_knowledge_schema import seed_shared_chapter

VALID_SUMMARY = "数列极限与函数极限用不同方式描述逼近关系。"
VALID_NARRATIVE = "先看数列如何逐步逼近一个数，再把这种逼近推广到函数。"


def _payload() -> dict:
    return {
        "course_id": "math_analysis",
        "chapter_kp_id": "kp-course-ch3",
        "chapter": {"name": "第 3 章 极限", "summary": "课程级旧总结"},
        "sources": [
            {
                "book_id": "mathanalysis-b18",
                "title": "数学分析",
                "chapter_name": "第三章 极限",
                "summary": "b18 章总结",
                "points": [
                    {"name": "数列极限", "summary": "ε-N 定义"},
                    {"name": "函数极限", "summary": "邻域定义"},
                ],
            }
        ],
    }


def _request() -> schemas.ChapterTextRequest:
    return schemas.ChapterTextRequest.model_validate(_payload())


class StubEngine:
    accounts_externally = True

    def __init__(self, *, reply=None, on_call=None, error=None, retry_error=None):
        self.reply = reply or ChapterReply(summary=VALID_SUMMARY, narrative=VALID_NARRATIVE)
        self.on_call = on_call
        self.error = error
        self.retry_error = retry_error
        self.bindings: list[dict] = []
        self.calls: list = []

    def bind_chapter_text(self, *, course_id, chapter_kp_id, evidence_sha256):
        self.bindings.append(
            {
                "course_id": course_id,
                "chapter_kp_id": chapter_kp_id,
                "evidence_sha256": evidence_sha256,
            }
        )
        return self

    def chapter_text(self, material):
        self.calls.append(material)
        if self.on_call is not None:
            self.on_call()
        if len(self.calls) > 1 and self.retry_error is not None:
            raise self.retry_error
        if self.error is not None:
            raise self.error
        return self.reply

    def current_model_name(self):
        return "shared-stub"


@pytest.fixture()
def shared_db(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'shared.db'}")
    seed_shared_chapter(engine)
    factory = sessionmaker(bind=engine, future=True)
    yield factory, tmp_path
    engine.dispose()


def _row(factory) -> dict:
    with factory() as session:
        row = session.execute(
            text(
                "SELECT kp_id, course_id, plane, node_kind, confirm_state, summary, "
                "narrative_ref, meta, updated_at FROM qed_knowledge_point WHERE kp_id='kp-course-ch3'"
            )
        ).mappings().one()
        result = dict(row)
        result["meta"] = json.loads(result["meta"]) if isinstance(result["meta"], str) else result["meta"]
        return result


def _merge(factory, data_root: Path, engine, request=None):
    with factory() as session:
        result = merge_chapter_text(
            session,
            request or _request(),
            engine,
            data_root=data_root,
        )
        return result


def test_success_persists_file_and_three_columns_then_reuses_without_model(shared_db):
    factory, data_root = shared_db
    engine = StubEngine()

    first = _merge(factory, data_root, engine)
    row = _row(factory)

    assert first.persistence.reused is False
    assert first.summary == VALID_SUMMARY
    assert first.narrative == VALID_NARRATIVE
    assert first.check.passed is True
    assert first.check.model == "shared-stub"
    assert first.persistence.narrative_ref == (
        f"parsed/course/math_analysis/kp-course-ch3/{first.persistence.evidence_sha256}.md"
    )
    assert (data_root / first.persistence.narrative_ref).read_text(encoding="utf-8") == VALID_NARRATIVE
    assert row["summary"] == VALID_SUMMARY
    assert row["narrative_ref"] == first.persistence.narrative_ref
    assert row["meta"]["keep"] is True
    record = row["meta"]["chapter_text"]
    assert record["evidence_sha256"] == first.persistence.evidence_sha256
    assert record["prompt_version"] == "kp-merge-chapter-v1"
    assert len(record["summary_sha256"]) == len(record["narrative_sha256"]) == 64
    assert record["check"] == first.check.model_dump(mode="json")
    assert '"narrative":' not in json.dumps(record, ensure_ascii=False)
    assert engine.bindings == [
        {
            "course_id": "math_analysis",
            "chapter_kp_id": "kp-course-ch3",
            "evidence_sha256": first.persistence.evidence_sha256,
        }
    ]

    second = _merge(factory, data_root, engine)

    assert len(engine.calls) == 1
    assert second.persistence.reused is True
    assert second.summary == first.summary
    assert second.narrative == first.narrative
    assert second.check == first.check
    assert second.persistence.kp_id == first.persistence.kp_id
    assert second.persistence.evidence_sha256 == first.persistence.evidence_sha256
    assert second.persistence.narrative_ref == first.persistence.narrative_ref


def test_multiline_narrative_is_written_and_reused_as_exact_utf8_bytes(shared_db):
    factory, data_root = shared_db
    narrative = f"{VALID_NARRATIVE}\n\n第二段继续推进。\n"
    engine = StubEngine(reply=ChapterReply(summary=VALID_SUMMARY, narrative=narrative))

    first = _merge(factory, data_root, engine)
    path = data_root / first.persistence.narrative_ref

    assert path.read_bytes() == narrative.encode("utf-8")
    second = _merge(factory, data_root, engine)
    assert second.persistence.reused is True
    assert second.narrative == narrative
    assert len(engine.calls) == 1


def test_changed_evidence_creates_a_new_content_addressed_result(shared_db):
    factory, data_root = shared_db
    engine = StubEngine()
    first = _merge(factory, data_root, engine)
    changed = _payload()
    changed["sources"][0]["points"][0]["summary"] = "ε-N 语言的另一表述"

    second = _merge(factory, data_root, engine, schemas.ChapterTextRequest.model_validate(changed))

    assert len(engine.calls) == 2
    assert second.persistence.reused is False
    assert second.persistence.evidence_sha256 != first.persistence.evidence_sha256
    assert second.persistence.narrative_ref != first.persistence.narrative_ref
    assert (data_root / first.persistence.narrative_ref).is_file()
    assert (data_root / second.persistence.narrative_ref).is_file()


def test_returning_to_an_evidence_hash_never_overwrites_a_different_historical_narrative(shared_db):
    factory, data_root = shared_db
    engine = StubEngine()
    first = _merge(factory, data_root, engine)
    first_path = data_root / first.persistence.narrative_ref
    original_bytes = first_path.read_bytes()
    changed = _payload()
    changed["sources"][0]["points"][0]["summary"] = "ε-N 语言的另一表述"
    changed_request = schemas.ChapterTextRequest.model_validate(changed)
    engine.reply = ChapterReply(
        summary=VALID_SUMMARY,
        narrative="从确界出发理解极限，再比较不同教材的等价表述。",
    )
    second = _merge(factory, data_root, engine, changed_request)
    before_third = _row(factory)
    engine.reply = ChapterReply(
        summary=VALID_SUMMARY,
        narrative="第三版导读改变了历史内容，但证据哈希仍指向原文件。",
    )

    with pytest.raises(ChapterTextError) as caught:
        _merge(factory, data_root, engine)

    assert caught.value.status_code == 409
    assert first_path.read_bytes() == original_bytes
    assert _row(factory) == before_third
    assert (data_root / second.persistence.narrative_ref).is_file()


def test_returning_to_an_evidence_hash_accepts_identical_historical_bytes(shared_db):
    factory, data_root = shared_db
    engine = StubEngine()
    first = _merge(factory, data_root, engine)
    changed = _payload()
    changed["sources"][0]["points"][0]["summary"] = "ε-N 语言的另一表述"
    _merge(factory, data_root, engine, schemas.ChapterTextRequest.model_validate(changed))

    third = _merge(factory, data_root, engine)

    assert third.persistence.evidence_sha256 == first.persistence.evidence_sha256
    assert third.persistence.reused is False
    assert (data_root / third.persistence.narrative_ref).read_bytes() == VALID_NARRATIVE.encode("utf-8")
    assert _row(factory)["narrative_ref"] == third.persistence.narrative_ref


@pytest.mark.parametrize(
    ("kp_id", "field", "value", "status_code"),
    [
        ("missing", None, None, 404),
        ("kp-course-ch3", "course_id", "other_course", 422),
        ("kp-course-ch3", "plane", "book", 422),
        ("kp-course-ch3", "node_kind", "point", 422),
        ("kp-course-ch3", "confirm_state", "confirmed", 409),
        ("kp-course-ch3", "confirm_state", "rejected", 409),
    ],
)
def test_target_precheck_rejects_bad_identity_without_model(shared_db, kp_id, field, value, status_code):
    factory, data_root = shared_db
    if field is not None:
        with factory() as session:
            session.execute(
                text(f"UPDATE qed_knowledge_point SET {field}=:value WHERE kp_id='kp-course-ch3'"),
                {"value": value},
            )
            session.commit()
    payload = _payload()
    payload["chapter_kp_id"] = kp_id
    engine = StubEngine()

    with pytest.raises(ChapterTextError) as caught:
        _merge(factory, data_root, engine, schemas.ChapterTextRequest.model_validate(payload))

    assert caught.value.status_code == status_code
    assert engine.calls == []


def test_confirmation_during_generation_conflicts_before_file_write(shared_db):
    factory, data_root = shared_db

    def confirm_during_generation():
        with factory() as session:
            session.execute(
                text(
                    "UPDATE qed_knowledge_point SET confirm_state='confirmed' "
                    "WHERE kp_id='kp-course-ch3'"
                )
            )
            session.commit()

    engine = StubEngine(on_call=confirm_during_generation)

    with pytest.raises(ChapterTextError) as caught:
        _merge(factory, data_root, engine)

    assert caught.value.status_code == 409
    assert _row(factory)["summary"] == "旧总结"
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_concurrent_row_change_is_rejected_before_file_write(shared_db):
    factory, data_root = shared_db
    changed_at = datetime.fromisoformat(_row(factory)["updated_at"]) + timedelta(seconds=1)

    def change_summary_during_generation():
        with factory() as session:
            session.execute(
                text(
                    "UPDATE qed_knowledge_point SET summary='并发总结', updated_at=:updated_at "
                    "WHERE kp_id='kp-course-ch3'"
                ),
                {"updated_at": changed_at},
            )
            session.commit()

    engine = StubEngine(on_call=change_summary_during_generation)

    with pytest.raises(ChapterTextError) as caught:
        _merge(factory, data_root, engine)

    assert caught.value.status_code == 409
    row = _row(factory)
    assert row["summary"] == "并发总结"
    assert row["narrative_ref"] == ""
    assert row["meta"] == {"keep": True}
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_concurrent_change_with_same_timestamp_is_still_rejected(shared_db):
    factory, data_root = shared_db
    original_updated_at = datetime.fromisoformat(_row(factory)["updated_at"])

    def change_summary_during_generation():
        with factory() as session:
            session.execute(
                text(
                    "UPDATE qed_knowledge_point SET summary='同秒并发总结', updated_at=:updated_at "
                    "WHERE kp_id='kp-course-ch3'"
                ),
                {"updated_at": original_updated_at},
            )
            session.commit()

    engine = StubEngine(on_call=change_summary_during_generation)

    with pytest.raises(ChapterTextError) as caught:
        _merge(factory, data_root, engine)

    assert caught.value.status_code == 409
    assert _row(factory)["summary"] == "同秒并发总结"
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_corrupted_completion_is_not_reused_or_regenerated(shared_db):
    factory, data_root = shared_db
    engine = StubEngine()
    first = _merge(factory, data_root, engine)
    (data_root / first.persistence.narrative_ref).write_text("被改写", encoding="utf-8")

    with pytest.raises(ChapterTextError) as caught:
        _merge(factory, data_root, engine)

    assert caught.value.status_code == 409
    assert len(engine.calls) == 1
    assert (data_root / first.persistence.narrative_ref).read_text(encoding="utf-8") == "被改写"


def test_failed_validation_is_persisted_and_reused_with_its_check(shared_db):
    factory, data_root = shared_db
    engine = StubEngine(
        reply=ChapterReply(summary="本章给出极限。", narrative="后续章节会继续讨论。")
    )

    first = _merge(factory, data_root, engine)
    second = _merge(factory, data_root, engine)

    assert len(engine.calls) == 2
    assert first.check.passed is False
    assert first.check.issues
    assert second.persistence.reused is True
    assert second.check == first.check


def test_engine_failure_preserves_old_row_and_writes_no_file(shared_db):
    factory, data_root = shared_db
    engine = StubEngine(error=EngineError("文字槽位不可用"))

    with pytest.raises(EngineError, match="文字槽位不可用"):
        _merge(factory, data_root, engine)

    assert _row(factory)["summary"] == "旧总结"
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_uncertain_course_retry_is_not_replaced_by_the_invalid_first_reply(shared_db):
    factory, data_root = shared_db
    before = _row(factory)
    engine = StubEngine(
        reply=ChapterReply(summary="本章给出极限。", narrative="后续章节会继续讨论。"),
        retry_error=EngineError("重试超时；课程级出站结果不确定：账本不可达（不自动重发）"),
    )

    with pytest.raises(EngineError, match="不自动重发"):
        _merge(factory, data_root, engine)

    assert len(engine.calls) == 2
    assert _row(factory) == before
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_non_external_accounting_engine_is_refused(shared_db):
    factory, data_root = shared_db
    engine = StubEngine()
    engine.accounts_externally = False

    with pytest.raises(EngineError, match="accounts_externally"):
        _merge(factory, data_root, engine)

    assert engine.calls == []
    assert _row(factory)["summary"] == "旧总结"


def test_oversized_model_output_is_refused_before_persistence(shared_db):
    factory, data_root = shared_db
    engine = StubEngine(reply=ChapterReply(summary="长" * 1001, narrative=VALID_NARRATIVE))

    with pytest.raises(EngineError, match="上限"):
        _merge(factory, data_root, engine)

    assert _row(factory)["summary"] == "旧总结"
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_database_update_failure_rolls_back_and_removes_the_new_file(shared_db, monkeypatch):
    factory, data_root = shared_db

    def fail_update(self, target, *, summary, narrative_ref, meta):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(SharedKnowledgeRepository, "update_text", fail_update)
    engine = StubEngine()

    with pytest.raises(RuntimeError, match="database unavailable"):
        _merge(factory, data_root, engine)

    assert _row(factory)["summary"] == "旧总结"
    assert _row(factory)["narrative_ref"] == ""
    assert _row(factory)["meta"] == {"keep": True}
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_database_commit_failure_rolls_back_and_removes_the_new_file(shared_db, monkeypatch):
    factory, data_root = shared_db
    original_commit = Session.commit
    commits = 0

    def fail_second_commit(session):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise RuntimeError("commit failed")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", fail_second_commit)

    with pytest.raises(RuntimeError, match="commit failed"):
        _merge(factory, data_root, StubEngine())

    assert commits == 2
    assert _row(factory)["summary"] == "旧总结"
    assert _row(factory)["narrative_ref"] == ""
    assert _row(factory)["meta"] == {"keep": True}
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_ambiguous_commit_failure_preserves_the_file_referenced_by_the_committed_row(
    shared_db, monkeypatch
):
    factory, data_root = shared_db
    original_commit = Session.commit
    commits = 0

    def commit_then_disconnect(session):
        nonlocal commits
        commits += 1
        if commits == 2:
            original_commit(session)
            raise RuntimeError("connection lost after commit")
        return original_commit(session)

    monkeypatch.setattr(Session, "commit", commit_then_disconnect)

    with pytest.raises(RuntimeError, match="connection lost after commit"):
        _merge(factory, data_root, StubEngine())

    row = _row(factory)
    assert commits == 2
    assert row["summary"] == VALID_SUMMARY
    assert row["narrative_ref"]
    assert (data_root / row["narrative_ref"]).read_text(encoding="utf-8") == VALID_NARRATIVE


@pytest.mark.parametrize("failure_stage", ["update", "commit"])
def test_database_failure_never_removes_a_preexisting_identical_file(
    shared_db, monkeypatch, failure_stage
):
    factory, data_root = shared_db
    digest = chapter_text_evidence_sha256(_request())
    path = data_root / "parsed" / "course" / "math_analysis" / "kp-course-ch3" / f"{digest}.md"
    path.parent.mkdir(parents=True)
    original_bytes = VALID_NARRATIVE.encode("utf-8")
    path.write_bytes(original_bytes)

    if failure_stage == "update":
        def fail_update(self, target, *, summary, narrative_ref, meta):
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(SharedKnowledgeRepository, "update_text", fail_update)
    else:
        original_commit = Session.commit
        commits = 0

        def fail_second_commit(session):
            nonlocal commits
            commits += 1
            if commits == 2:
                raise RuntimeError("commit failed")
            return original_commit(session)

        monkeypatch.setattr(Session, "commit", fail_second_commit)

    with pytest.raises(RuntimeError, match="database unavailable|commit failed"):
        _merge(factory, data_root, StubEngine())

    assert path.read_bytes() == original_bytes
    assert _row(factory)["summary"] == "旧总结"
    assert _row(factory)["narrative_ref"] == ""


def test_retry_after_database_failure_can_replace_the_uncommitted_narrative(shared_db, monkeypatch):
    factory, data_root = shared_db

    def fail_update(self, target, *, summary, narrative_ref, meta):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(SharedKnowledgeRepository, "update_text", fail_update)
    engine = StubEngine()
    with pytest.raises(RuntimeError, match="database unavailable"):
        _merge(factory, data_root, engine)

    monkeypatch.undo()
    replacement = "失败未提交的导读已回收，同一证据可以按新版正文重跑。"
    engine.reply = ChapterReply(summary=VALID_SUMMARY, narrative=replacement)

    result = _merge(factory, data_root, engine)

    assert result.persistence.reused is False
    assert result.narrative == replacement
    assert (data_root / result.persistence.narrative_ref).read_text(encoding="utf-8") == replacement
    assert _row(factory)["summary"] == VALID_SUMMARY
    assert len(list((data_root / "parsed").rglob("*.md"))) == 1
