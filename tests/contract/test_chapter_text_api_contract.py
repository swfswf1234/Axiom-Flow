"""课程级章总结端点契约（PLAN-034 W4）。

隔离：临时 SQLite 文件库＋临时数据根＋假文字引擎；不访问 MySQL、公网或模型服务。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from axiom_flow.api.main import create_app
from axiom_flow.config import load_settings
from axiom_flow.db.models import Base
from axiom_flow.engines.base import ChapterReply, EngineError
from axiom_flow.engines.kp_gateway import KP_SLOT_NAME
from tests.support.shared_knowledge_schema import seed_shared_chapter

URL = "/api/v1/kp/merge/chapter-text"
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
                "points": [{"name": "数列极限", "summary": "ε-N 定义"}],
            }
        ],
    }


class _CourseEngine:
    name = "fake-course-text"
    accounts_externally = True

    def __init__(self, *, reply=None, error=None, on_call=None, healthy=True, retry_error=None):
        self.reply = reply or ChapterReply(summary=VALID_SUMMARY, narrative=VALID_NARRATIVE)
        self.error = error
        self.retry_error = retry_error
        self.on_call = on_call
        self.healthy = healthy
        self.calls = []
        self.closed = 0

    def health(self):
        return self.healthy

    def close(self):
        self.closed += 1

    def bind_chapter_text(self, *, course_id, chapter_kp_id, evidence_sha256):
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
        return "fake-course-model"


@pytest.fixture()
def course_api(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'contract.db'}")
    Base.metadata.create_all(db_engine)
    seed_shared_chapter(db_engine)
    factory = sessionmaker(bind=db_engine, future=True)
    engine = _CourseEngine()
    settings = load_settings(
        data_dir=data_root,
        kp_engine=KP_SLOT_NAME,
        admin_enabled=False,
        admin_token="",
    )
    app = create_app(
        settings,
        session_factory=factory,
        knowledge_engine_factory=lambda name: engine,
        reference_fetcher=lambda *args, **kwargs: (),
        background_jobs=False,
        practice_sync=lambda *args: None,
    )
    with TestClient(app) as client:
        yield client, factory, data_root, engine
    db_engine.dispose()


def _row(factory) -> dict:
    with factory() as session:
        return dict(
            session.execute(
                text(
                    "SELECT summary, narrative_ref, meta, confirm_state, updated_at "
                    "FROM qed_knowledge_point WHERE kp_id='kp-course-ch3'"
                )
            ).mappings().one()
        )


def test_success_persists_and_reuses_without_second_model_call(course_api):
    client, factory, data_root, engine = course_api

    first = client.post(URL, json=_payload())
    second = client.post(URL, json=_payload())

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    body = first.json()
    assert body["summary"] == VALID_SUMMARY
    assert body["narrative"] == VALID_NARRATIVE
    assert body["check"] == {
        "passed": True,
        "issues": [],
        "model": "fake-course-model",
    }
    assert body["persistence"]["reused"] is False
    assert second.json()["persistence"] == {**body["persistence"], "reused": True}
    assert len(engine.calls) == 1
    row = _row(factory)
    assert row["summary"] == VALID_SUMMARY
    assert row["narrative_ref"] == body["persistence"]["narrative_ref"]
    assert (data_root / row["narrative_ref"]).read_text(encoding="utf-8") == VALID_NARRATIVE
    assert engine.closed == 2


@pytest.mark.parametrize(
    ("payload_change", "status_code"),
    [
        ({"chapter_kp_id": "missing"}, 404),
        ({"course_id": "other_course"}, 422),
        ({"chapter_kp_id": "../escape"}, 422),
    ],
)
def test_target_and_request_rejections_do_not_call_model(course_api, payload_change, status_code):
    client, _, _, engine = course_api
    payload = _payload()
    payload.update(payload_change)

    response = client.post(URL, json=payload)

    assert response.status_code == status_code, response.text
    assert engine.calls == []


@pytest.mark.parametrize("state", ["confirmed", "rejected"])
def test_protected_target_returns_409_without_model(course_api, state):
    client, factory, _, engine = course_api
    with factory() as session:
        session.execute(
            text("UPDATE qed_knowledge_point SET confirm_state=:state WHERE kp_id='kp-course-ch3'"),
            {"state": state},
        )
        session.commit()

    response = client.post(URL, json=_payload())

    assert response.status_code == 409, response.text
    assert engine.calls == []


def test_generation_time_confirmation_returns_409_before_file_write(course_api):
    client, factory, data_root, engine = course_api

    def confirm():
        with factory() as session:
            session.execute(
                text(
                    "UPDATE qed_knowledge_point SET confirm_state='confirmed' "
                    "WHERE kp_id='kp-course-ch3'"
                )
            )
            session.commit()

    engine.on_call = confirm
    response = client.post(URL, json=_payload())

    assert response.status_code == 409, response.text
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_engine_failure_returns_503_and_preserves_old_row(course_api):
    client, factory, data_root, engine = course_api
    engine.error = EngineError("文字槽位不可用")

    response = client.post(URL, json=_payload())

    assert response.status_code == 503, response.text
    assert _row(factory)["summary"] == "旧总结"
    assert list((data_root / "parsed").rglob("*.md")) == []
    assert engine.closed == 1


def test_uncertain_retry_returns_503_without_persisting_the_invalid_first_reply(course_api):
    client, factory, data_root, engine = course_api
    before = _row(factory)
    engine.reply = ChapterReply(summary="本章给出极限。", narrative="后续章节会继续讨论。")
    engine.retry_error = EngineError("重试超时；课程级出站结果不确定：账本不可达（不自动重发）")

    response = client.post(URL, json=_payload())

    assert response.status_code == 503, response.text
    assert "不自动重发" in response.json()["detail"]
    assert len(engine.calls) == 2
    assert _row(factory) == before
    assert list((data_root / "parsed").rglob("*.md")) == []


def test_failed_validation_is_written_and_returned_as_200(course_api):
    client, factory, _, engine = course_api
    engine.reply = ChapterReply(summary="本章给出极限。", narrative="后续章节会继续讨论。")

    response = client.post(URL, json=_payload())

    assert response.status_code == 200, response.text
    assert response.json()["check"]["passed"] is False
    assert response.json()["check"]["issues"]
    assert _row(factory)["summary"] == "本章给出极限。"


def test_missing_text_slot_returns_503(tmp_path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db_engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'slot.db'}")
    Base.metadata.create_all(db_engine)
    seed_shared_chapter(db_engine)
    factory = sessionmaker(bind=db_engine, future=True)
    app = create_app(
        load_settings(data_dir=data_root, kp_engine="", admin_enabled=False, admin_token=""),
        session_factory=factory,
        knowledge_engine_factory=lambda name: pytest.fail("空槽位不得构造引擎"),
        background_jobs=False,
        practice_sync=lambda *args: None,
    )
    try:
        with TestClient(app) as client:
            response = client.post(URL, json=_payload())
        assert response.status_code == 503, response.text
        assert _row(factory)["summary"] == "旧总结"
    finally:
        db_engine.dispose()


def test_unknown_request_field_is_rejected(course_api):
    client, _, _, engine = course_api
    payload = _payload()
    payload["model"] = "forbidden"

    response = client.post(URL, json=payload)

    assert response.status_code == 422, response.text
    assert engine.calls == []


def test_openapi_exposes_strict_chapter_text_contract(course_api):
    client, _, _, _ = course_api

    schema = client.get("/openapi.json").json()

    assert URL in schema["paths"]
    operation = schema["paths"][URL]["post"]
    request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert request_ref.endswith("/ChapterTextRequest")
    request_schema = schema["components"]["schemas"]["ChapterTextRequest"]
    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {"course_id", "chapter_kp_id", "chapter", "sources"}
