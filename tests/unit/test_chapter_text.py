"""课程级章总结与导读（PLAN-034）：无页块溯源的证据模型与生成链路。

隔离：纯模型／映射测试，不访问公网、模型服务或数据库。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from axiom_flow import schemas
from axiom_flow.engines import base
from axiom_flow.orchestrator import knowledge


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
                    {"name": "数列极限", "summary": " ε-N 定义 "},
                    {"name": "函数极限", "summary": "邻域定义"},
                ],
            },
            {
                "book_id": "mathanalysis-b05",
                "title": "数学分析原理",
                "chapter_name": "第 3 章 极限",
                "summary": "b05 章总结",
                "points": [{"name": "极限的性质", "summary": "唯一性与保号性"}],
            },
        ],
    }


def test_chapter_text_evidence_normalizes_without_page_provenance():
    request = schemas.ChapterTextRequest.model_validate(_payload())

    normalized = knowledge.normalize_chapter_text_request(request)
    material = knowledge.chapter_text_material(normalized)

    assert isinstance(material, base.ChapterTextInput)
    assert material.course_id == "math_analysis"
    assert material.chapter_kp_id == "kp-course-ch3"
    assert material.chapter_name == "第 3 章 极限"
    assert [source.book_id for source in material.sources] == ["mathanalysis-b05", "mathanalysis-b18"]
    assert next(point.summary for point in material.sources[1].points if point.name == "数列极限") == "ε-N 定义"
    assert not hasattr(material, "page")
    assert not hasattr(material, "pages")
    assert not hasattr(material, "anchors")


def test_chapter_text_evidence_hash_is_stable_but_content_sensitive():
    first = schemas.ChapterTextRequest.model_validate(_payload())
    reordered = _payload()
    reordered["sources"].reverse()
    for source in reordered["sources"]:
        source["points"].reverse()
    second = schemas.ChapterTextRequest.model_validate(reordered)
    changed = _payload()
    changed["sources"][0]["points"][0]["name"] = "数列收敛"

    digest = knowledge.chapter_text_evidence_sha256(first)

    assert len(digest) == 64
    assert knowledge.chapter_text_evidence_sha256(second) == digest
    assert knowledge.chapter_text_evidence_sha256(schemas.ChapterTextRequest.model_validate(changed)) != digest


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(sources=[]),
        lambda body: body["sources"][0].update(points=[]),
        lambda body: body["sources"][0].pop("chapter_name"),
        lambda body: body["chapter"].update(name="   "),
        lambda body: body.update(unexpected=True),
    ],
)
def test_chapter_text_request_rejects_empty_or_incomplete_evidence(mutate):
    body = _payload()
    mutate(body)

    with pytest.raises(ValidationError):
        schemas.ChapterTextRequest.model_validate(body)


def test_chapter_text_response_has_the_frozen_four_block_shape():
    result = schemas.ChapterTextOut.model_validate(
        {
            "summary": "本章围绕极限展开。",
            "narrative": "总问题\n……\n\n材料推进\n……",
            "check": {"passed": False, "issues": ["缺少出处"], "model": "stub-model"},
            "persistence": {
                "kp_id": "kp-course-ch3",
                "evidence_sha256": "a" * 64,
                "narrative_ref": "parsed/course/math_analysis/kp-course-ch3/aaaaaaaa.md",
                "reused": True,
            },
        }
    )

    assert result.check.passed is False
    assert result.check.issues == ["缺少出处"]
    assert result.persistence.reused is True
    with pytest.raises(ValidationError):
        schemas.ChapterTextOut.model_validate({**result.model_dump(), "unexpected": True})


def test_course_generation_keeps_fail_open_retrieval_and_retries_once():
    class RetryingEngine:
        accounts_externally = True

        def __init__(self):
            self.calls: list[base.ChapterTextInput] = []

        def chapter_text(self, material: base.ChapterTextInput) -> base.ChapterReply:
            self.calls.append(material)
            if len(self.calls) == 1:
                return base.ChapterReply(summary="本章给出极限。", narrative="后续章节会继续讨论。")
            return base.ChapterReply(
                summary="数列极限把无限接近写成 ε-N 条件，函数极限把局部变化写成邻域关系。",
                narrative="先看数列如何逐步逼近一个数，再把这种逼近推广到函数。",
            )

        def current_model_name(self) -> str:
            return "course-stub"

    engine = RetryingEngine()
    retrieved: list[tuple[str, tuple[str, ...]]] = []

    def broken_fetcher(title: str, point_titles):
        retrieved.append((title, tuple(point_titles)))
        raise RuntimeError("reference unavailable")

    replied, issues, model = knowledge.generate_chapter_text(
        engine,
        schemas.ChapterTextRequest.model_validate(_payload()),
        broken_fetcher,
    )

    assert len(engine.calls) == 2
    assert engine.calls[0].retry_issues == ()
    assert engine.calls[1].retry_issues
    assert all(call.reference_notes == () for call in engine.calls)
    assert all(len(call.sources) == 2 for call in engine.calls)
    assert retrieved == [
        ("第 3 章 极限", ("极限的性质", "函数极限", "数列极限"))
    ]
    assert issues == ()
    assert replied.summary.startswith("数列极限")
    assert model == "course-stub"


def test_two_failed_course_validations_keep_text_issues_and_model():
    class AlwaysInvalidEngine:
        def __init__(self):
            self.calls = 0

        def chapter_text(self, material: base.ChapterTextInput) -> base.ChapterReply:
            self.calls += 1
            return base.ChapterReply(summary="本章给出极限。", narrative="后续章节会继续讨论。")

        def current_model_name(self) -> str:
            return "invalid-stub"

    engine = AlwaysInvalidEngine()

    replied, issues, model = knowledge.generate_chapter_text(
        engine, schemas.ChapterTextRequest.model_validate(_payload()), None
    )

    assert engine.calls == 2
    assert replied.summary == "本章给出极限。"
    assert replied.narrative == "后续章节会继续讨论。"
    assert any("章节自称" in issue for issue in issues)
    assert any("后续章节" in issue for issue in issues)
    assert model == "invalid-stub"


def test_course_check_keeps_validation_issues_and_model_name():
    check = knowledge.chapter_text_check(("总结出现章节自称",), "course-stub")

    assert check.passed is False
    assert check.issues == ["总结出现章节自称"]
    assert check.model == "course-stub"
    assert knowledge.chapter_text_check((), "course-stub").passed is True


def test_missing_course_generation_slot_is_an_engine_failure():
    class MissingSlot:
        pass

    with pytest.raises(base.EngineError, match="chapter_text"):
        knowledge.generate_chapter_text(
            MissingSlot(), schemas.ChapterTextRequest.model_validate(_payload()), None
        )
