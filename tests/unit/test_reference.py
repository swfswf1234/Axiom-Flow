"""章级检索资料源（PLAN-033 W3）：确定性取词、维基优先的源回退、摘要清洗限长与 fail-open。

隔离：`client=` 注入 `httpx.MockTransport`，默认门禁不出站；缓存每例清空。
"""

from __future__ import annotations

import httpx
import pytest

from axiom_flow.reference import (
    ReferenceNote,
    clear_reference_cache,
    collect_reference_notes,
    reference_query_terms,
)

ZH = "zh.wikipedia.org"
EN = "en.wikipedia.org"
EOM = "encyclopediaofmath.org"


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_reference_cache()
    yield
    clear_reference_cache()


def _payload(extracts: dict[str, str], missing: tuple[str, ...] = ()) -> dict:
    pages = [{"title": title, "extract": text} for title, text in extracts.items()]
    pages.extend({"title": title, "missing": True} for title in missing)
    return {"query": {"pages": pages, "normalized": [], "redirects": []}}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _source_client(replies: dict[str, dict], *, sent: list[httpx.Request] | None = None):
    """按域名回各自的 MediaWiki 载荷；未登记的域名一律 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.append(request)
        body = replies.get(request.url.host)
        if body is None:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json=body)

    return _client(handler)


class TestQueryTerms:
    def test_chapter_and_section_numbers_are_stripped(self):
        terms = reference_query_terms(
            "第 3 章 连续性", ["3.1 连续性的定义", "3.2 连续函数的性质"]
        )

        assert terms == ("连续性", "连续性的定义", "连续函数的性质")

    def test_terms_are_deduplicated_and_capped(self):
        points = [f"1.{index} 术语{index}" for index in range(1, 13)] + ["1.1 术语1"]
        terms = reference_query_terms("第 1 章 总纲", points)

        assert len(terms) == 8
        assert len(set(terms)) == len(terms)

    def test_blank_titles_are_dropped(self):
        assert reference_query_terms("", ["  ", "1.1 数列"]) == ("数列",)


class TestSourceFallback:
    def test_a_chinese_wikipedia_hit_needs_no_further_source(self):
        sent: list[httpx.Request] = []
        client = _source_client(
            {
                ZH: _payload({"连续性": "连续性是拓扑学的基本概念。函数在一点连续指极限等于函数值。"}),
                EN: _payload({"连续性": "Continuity is a basic concept."}),
            },
            sent=sent,
        )

        notes = collect_reference_notes("第 3 章 连续性", [], client=client)

        assert [note.source for note in notes] == ["维基百科"]
        assert {request.url.host for request in sent} == {ZH}

    def test_a_missing_chinese_entry_falls_back_to_english(self):
        client = _source_client(
            {
                ZH: _payload({}, missing=("连续性的定义",)),
                EN: _payload({"连续性的定义": "A function is continuous at a point when its limit equals its value."}),
            }
        )

        notes = collect_reference_notes("第 3 章 连续性", ["3.1 连续性的定义"], client=client)

        assert [note.source for note in notes] == ["Wikipedia"]
        assert "continuous" in notes[0].text

    def test_both_wikis_missing_falls_back_to_the_math_source(self):
        client = _source_client(
            {
                ZH: _payload({}, missing=("一致连续",)),
                EN: _payload({}, missing=("一致连续",)),
                EOM: _payload({"一致连续": "Uniform continuity: for every epsilon there is one delta for all points."}),
            }
        )

        notes = collect_reference_notes("第 3 章 连续性", ["3.3 一致连续"], client=client)

        assert [note.source for note in notes] == ["Encyclopedia of Mathematics"]

    def test_requests_carry_a_user_agent_and_the_mediawiki_api(self):
        sent: list[httpx.Request] = []
        client = _source_client({ZH: _payload({})}, sent=sent)

        collect_reference_notes("第 3 章 连续性", [], client=client)

        assert sent
        assert sent[0].url.path == "/w/api.php"
        assert "AxiomFlow" in sent[0].headers["user-agent"]
        assert sent[0].headers["accept-encoding"] == "gzip, deflate"
        assert "titles=" in str(sent[0].url)


class TestCleaningAndBudget:
    def test_headings_and_whitespace_are_cleaned(self):
        client = _source_client(
            {ZH: _payload({"连续性": "== 定义 ==\n\n函数  在一点连续\r\n指极限等于函数值；这一点称为连续点。"})}
        )

        notes = collect_reference_notes("第 3 章 连续性", [], client=client)

        assert "\n" not in notes[0].text
        assert "==" not in notes[0].text
        assert notes[0].text == "函数 在一点连续 指极限等于函数值；这一点称为连续点。"

    def test_each_note_is_capped_and_the_total_budget_is_respected(self):
        long_text = "连续性" * 1000 + "。"
        client = _source_client(
            {ZH: _payload({f"术语{i}": long_text for i in range(8)})}
        )
        terms = [f"1.{i} 术语{i}" for i in range(1, 9)]

        notes = collect_reference_notes("第 1 章 连续性", terms, client=client)

        assert all(len(note.text) <= 400 for note in notes)
        assert sum(len(note.text) for note in notes) <= 3000

    def test_tiny_extracts_are_not_given_as_reference(self):
        client = _source_client({ZH: _payload({"连续性": "短。"})})

        assert collect_reference_notes("第 3 章 连续性", [], client=client) == ()


class TestFailOpenAndCache:
    def test_a_network_failure_yields_no_notes(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        notes = collect_reference_notes(
            "第 3 章 连续性", [], client=_client(handler)
        )

        assert notes == ()

    def test_a_non_json_reply_yields_no_notes(self):
        client = _client(lambda request: httpx.Response(200, text="<html>nope</html>"))

        assert collect_reference_notes("第 3 章 连续性", [], client=client) == ()

    def test_a_second_call_serves_from_the_process_cache(self):
        sent: list[httpx.Request] = []
        client = _source_client(
            {ZH: _payload({"连续性": "连续性是函数在一点附近没有断裂的性质，直观上函数图像在此处可以一笔画出。"})}, sent=sent
        )

        first = collect_reference_notes("第 3 章 连续性", [], client=client)
        second = collect_reference_notes("第 3 章 连续性", [], client=client)

        assert first == second
        assert len(sent) == 1

    def test_no_terms_makes_no_request(self):
        sent: list[httpx.Request] = []
        client = _source_client({ZH: _payload({})}, sent=sent)

        assert collect_reference_notes("", [], client=client) == ()
        assert sent == []


def test_note_shape_is_a_plain_value():
    note = ReferenceNote(source="维基百科", title="连续性", text="正文")

    assert note == ReferenceNote(source="维基百科", title="连续性", text="正文")
