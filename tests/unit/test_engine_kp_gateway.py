"""梳理文本引擎适配器（`qed-gateway`）单测：httpx MockTransport，不出公网、不打真实 8900。

守护面（R8 甲＋AF-T3 甲的落地形状）：出站唯一＝8900 `POST /api/v1/llm/text`；归因三键与 `meta`
随请求体上送（账只由 8900 落一行，本仓自写腿在这条通路上停用）；密钥与模型名一律不下发；
模型回复的解析（知识点腿回「总结＋点数组」一枚对象、裸数组照收，含 ``` 代码围栏）；
`success=false`／非 2xx／非 JSON／传输异常四类故障
**全部归一为 `EngineError`**——节失败域（`extract_points` 只捕 `EngineError`）依赖这一点；
未绑定批次身份时拒跑（「每次调用都可指认」不靠调用方自觉）。

隔离：`client=` 注入 MockTransport，测试不构造真实 HTTP 连接，也不读写数据根与运行数据库。
"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from axiom_flow.engines.base import (
    CHAPTER_TEXT_PROMPT_VERSION,
    ChapterInput,
    ChapterTextInput,
    ChapterTextPoint,
    ChapterTextSource,
    EngineError,
    ExerciseClassifyInput,
    ExerciseInput,
    ExerciseItemInput,
    ExerciseMatchCandidate,
    ExerciseMatchInput,
    ExerciseVerifyInput,
    ExerciseVerifyItem,
    PageBody,
    PageSample,
    ReferenceNote,
    ReferencePage,
    RevisionContext,
    SectionBlock,
    SectionInput,
    SectionMember,
    TocOutlineRow,
    TocVerifyItem,
)
from axiom_flow.engines.kp_gateway import (
    KP_SLOT_NAME,
    KpGatewayEngine,
    _repair_json_escapes,
)

GW = "http://127.0.0.1:8900"

_POINTS_REPLY = [
    {
        "name": "数列极限的 ε-N 定义",
        "type": "concept",
        "role": "core",
        "summary": "用 ε-N 语言刻画数列收敛。",
        "anchors": [{"page": 7, "snippet": "设 {x_n} 为数列"}],
    }
]


def _section() -> SectionInput:
    return SectionInput(
        parent_id="afkp-outline-1",
        title="2.1 数列的极限",
        page_start=7,
        page_end=8,
        pages=(PageBody(page_no=7, markdown="设 {x_n} 为数列，正文甲。"), PageBody(page_no=8, markdown="正文乙。")),
    )


def _reply(drafts: list[dict]):
    """200 + 成功信封，`reply` 就是草稿数组的 JSON 文本（老形状，仍要照收）。"""
    payload = json.dumps(drafts, ensure_ascii=False)
    return lambda request: httpx.Response(200, json={"reply": payload, "success": True, "error": ""})


_SECTION_SUMMARY = "这一节把数列收敛写成 ε-N 语言，并给出它的三条推论与判定次序。"


def _reply_object(summary: str, drafts: list[dict]):
    """200 + 成功信封，`reply` 是 `KA-43` 的正形状：`section_summary` ＋ `points` 两半。"""
    payload = json.dumps({"section_summary": summary, "points": drafts}, ensure_ascii=False)
    return lambda request: httpx.Response(200, json={"reply": payload, "success": True, "error": ""})


def _capturing(target: list[dict], reply: str = "[]"):
    def handler(request: httpx.Request) -> httpx.Response:
        target.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"reply": reply, "success": True, "error": ""})

    return handler


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=GW)


def _engine(handler, **kwargs) -> KpGatewayEngine:
    """已绑定批次身份的适配器：真实通路上这一步由执行体在调用前做完。"""
    kwargs.setdefault("timeout", 5.0)
    return KpGatewayEngine(GW, client=_client(handler), **kwargs).bind_batch(
        book_id="mathanalysis-b05", gen_batch="afkp-20261026t000000"
    )


class TestReviewNoteGoesIntoThePrompt:
    """AF-T5 甲・备注通道（PLAN-026 W11）：备注是**这一轮**的上下文，不是新的取材、也不是新的账目维。"""

    def _bound(self, sent: list[dict], note: str) -> KpGatewayEngine:
        return KpGatewayEngine(GW, client=_client(_capturing(sent)), timeout=5.0).bind_batch(
            book_id="mathanalysis-b05", gen_batch="afkp-note", review_note=note
        )

    def test_a_bound_note_appears_in_the_prompt(self):
        sent: list[dict] = []
        engine = self._bound(sent, "第 3 章的节标题把书眉粘进来了，剔掉")
        engine.points(_section())

        assert len(sent) == 1
        body = sent[0]
        assert "人工评审意见" in body["prompt"]
        assert "第 3 章的节标题把书眉粘进来了，剔掉" in body["prompt"]
        # 备注不进归因 meta：它是给模型读的上下文，账目维仍只有身份与取材事实
        assert "review_note" not in body["meta"]
        assert set(body) <= {
            "prompt", "system", "prompt_template", "max_tokens", "service", "task", "step", "meta",
        }

    def test_an_absent_note_leaves_no_empty_heading_behind(self):
        """空备注＝整段不出现。留一枚空标题等于给模型下了一条「没有意见」的指令。"""
        sent: list[dict] = []
        _engine(_capturing(sent)).points(_section())
        assert "人工评审意见" not in sent[0]["prompt"]


class TestRevisionContextGoesIntoThePrompt:
    """`KA-48` 单点重写：人给的修改建议与当前成品只进**这一次调用**的 prompt，不改取材也不进账目维。"""

    def _bound(self, sent: list[dict], revision: RevisionContext) -> KpGatewayEngine:
        reply = json.dumps({"section_summary": "新的总结", "points": []}, ensure_ascii=False)
        return KpGatewayEngine(GW, client=_client(_capturing(sent, reply=reply)), timeout=5.0).bind_batch(
            book_id="mathanalysis-b05", gen_batch="regen-afkp-1", revision=revision
        )

    def test_the_note_and_the_previous_summary_reach_the_points_prompt(self):
        sent: list[dict] = []
        revision = RevisionContext(note="参照已有内容，做简化和通俗化处理", previous="旧的节总结。")

        self._bound(sent, revision).points(_section())

        prompt = sent[0]["prompt"]
        assert "本次修改建议" in prompt
        assert revision.note in prompt
        assert "现有总结" in prompt
        assert revision.previous in prompt
        assert "revision" not in sent[0]["meta"]

    def test_the_note_and_the_previous_chapter_text_reach_the_chapter_prompt(self):
        sent: list[dict] = []
        revision = RevisionContext(note="更简短一些", previous="旧的章导读。", previous_kind="导读")
        engine = KpGatewayEngine(
            GW,
            client=_client(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}')),
            timeout=5.0,
        ).bind_batch(book_id="mathanalysis-b05", gen_batch="regen-afkp-ch1", revision=revision)

        engine.chapter(_material())

        prompt = sent[0]["prompt"]
        assert "本次修改建议" in prompt and revision.note in prompt
        assert "现有导读" in prompt and revision.previous in prompt

    def test_without_a_revision_the_prompt_keeps_its_old_shape(self):
        """内容批次与首次生成不带重写上下文：提示词里不留空标题。"""
        sent: list[dict] = []
        _engine(_capturing(sent, reply='{"section_summary": "甲", "points": []}')).points(_section())

        assert "本次修改建议" not in sent[0]["prompt"]
        assert "现有总结" not in sent[0]["prompt"]


class TestOutboundShape:
    """出站点与归因三键：只出 8900 那一个门，且每一条调用都可指认。"""

    def test_the_points_call_leaves_only_for_the_gateway_text_endpoint(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"reply": "[]", "success": True})

        assert _engine(handler).points(_section()).drafts == []
        assert len(seen) == 1
        assert seen[0].method == "POST"
        assert seen[0].url.path == "/api/v1/llm/text"
        assert (seen[0].url.host, seen[0].url.port) == ("127.0.0.1", 8900)

    def test_the_request_carries_the_attribution_keys_and_no_secret(self):
        sent: list[dict] = []
        _engine(_capturing(sent, reply=json.dumps(_POINTS_REPLY, ensure_ascii=False))).points(_section())

        body = sent[0]
        assert body["service"] == "axiom_flow"
        assert body["task"] == "kp-curation"
        assert body["step"] == "points"
        assert body["meta"]["book_id"] == "mathanalysis-b05"
        assert body["meta"]["gen_batch"] == "afkp-20261026t000000"
        assert body["meta"]["page_count"] == 2
        assert set(body) <= {"prompt", "system", "prompt_template", "max_tokens", "service", "task", "step", "meta"}
        assert not {"api_key", "key", "model"} & set(body)

    def test_the_points_request_names_the_section_in_step_and_meta(self):
        sent: list[dict] = []
        _engine(_capturing(sent)).points(_section())

        body = sent[0]
        assert body["step"] == "points"
        assert body["meta"]["section"] == "2.1 数列的极限"
        assert (body["meta"]["page_start"], body["meta"]["page_end"], body["meta"]["page_count"]) == (7, 8, 2)

    def test_the_prompt_material_is_the_section_and_nothing_else(self):
        """`KA-5` 的取材边界在 prompt 上也成立：点段只带本节标题与本节正文，目录行名不进模型。"""
        sent: list[dict] = []
        _engine(_capturing(sent)).points(_section())

        prompt = sent[0]["prompt"]
        assert "2.1 数列的极限" in prompt
        assert "正文甲" in prompt
        assert "正文乙" in prompt
        assert "第2章 极限" not in prompt

    def test_an_unbound_engine_refuses_the_call_instead_of_running_unattributed(self):
        """未绑定批次身份＝这条调用的账无可指认（R8 甲要的形状），当场拒而不是发出去。"""
        engine = KpGatewayEngine(GW, client=_client(_reply([])), timeout=5.0)
        with pytest.raises(EngineError, match="批次身份"):
            engine.points(_section())

    def test_rebinding_moves_the_batch_identity_forward(self):
        """绑定是「当前批次」一枚读数：换批次再绑就覆盖（同一实例只服务一批，账跟着批次走）。"""
        sent: list[dict] = []
        engine = _engine(_capturing(sent))
        engine.bind_batch(book_id="mathanalysis-b07", gen_batch="afkp-new")
        engine.points(_section())
        assert sent[0]["meta"]["book_id"] == "mathanalysis-b07"
        assert sent[0]["meta"]["gen_batch"] == "afkp-new"

    def test_health_probes_the_gateway_without_a_batch_identity(self):
        """探活不属批次：它没有归因可带，也不该因为没绑定就报错（提交期探活先于绑定）。"""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(200, json={"status": "ok", "service": "qed-engine-config"})

        engine = KpGatewayEngine(GW, client=_client(handler), timeout=5.0)
        assert engine.health() is True
        assert seen == ["/api/v1/health"]


class TestPointsDetailPrompt:
    """`KA-57`：点腿除总结外还要两枚详情读数——「要掌握什么」与关键定义清单。

    提示词给出键名、条数与字数上限（先说要什么），解析侧原样透传这两枚键给编排层净化；
    适配层不在这里做逐条校验，坏条目归 `_point_row` 丢弃。
    """

    def test_the_prompt_asks_for_the_two_detail_reads_with_bounds(self):
        sent: list[dict] = []
        _engine(_capturing(sent)).points(_section())

        prompt = sent[0]["prompt"]
        assert "must_master" in prompt
        assert "key_definitions" in prompt
        assert "2~4" in prompt, "掌握要点的条数上限写在提示词里"
        assert "2~6" in prompt, "关键定义的条数上限写在提示词里"
        assert "term" in prompt and "definition" in prompt


    def test_the_chapter_prompt_names_the_summary_as_learning_content(self):
        """章侧不加新键（`KA-57`）：`chapter_summary` 就是「本章学习内容」，措辞要写明。"""
        sent: list[dict] = []
        _engine(
            _capturing(sent, '{"chapter_summary": "整章一段话。", "chapter_narrative": "导读。"}')
        ).chapter(
            ChapterInput(
                parent_id="afkp-ch-1",
                title="第2章 极限",
                page_start=5,
                page_end=8,
                blocks=(SectionBlock(page=5, block_index=0, text="章前正文。"),),
                point_summaries=(),
            )
        )

        assert "本章学习内容" in sent[0]["prompt"]

    def test_the_two_reads_pass_through_untouched(self):
        draft = {
            **_POINTS_REPLY[0],
            "must_master": ["能写出 ε-N 定义", "会用定义证收敛"],
            "key_definitions": [{"term": "数列极限", "definition": "存在 L 使任意 ε 有 N。"}],
        }
        assert _engine(_reply([draft])).points(_section()).drafts == [draft]


class TestPointSummarySemantics:
    """PLAN-033 W2：`section_summary` 是「本点核心要点与关键定义的梳理」，不是原文压缩，
    也不替读者安排章节位置或后续用途。"""

    @staticmethod
    def _prompt() -> str:
        sent: list[dict] = []
        _engine(_capturing(sent)).points(_section())
        return sent[0]["prompt"]

    def test_the_summary_is_asked_for_core_points_and_key_definitions(self):
        prompt = self._prompt()
        assert "核心要点" in prompt
        assert "关键定义" in prompt
        assert "不是原文压缩" in prompt

    def test_the_summary_forbids_chapter_position_and_followup_use(self):
        prompt = self._prompt()
        assert "不写本节的章节位置" in prompt
        assert "不写后续用在哪里" in prompt

    def test_the_old_chapter_placement_instruction_is_gone(self):
        prompt = self._prompt()
        assert "所在章里的位置" not in prompt
        assert "接下来在哪一节" not in prompt


class TestReplyParsing:
    """模型回复 → 一节的两半（`KA-43`）：对象形状为正、裸数组照收，围栏与前后杂讯兼容，
    坏形状一律归一为 `EngineError`（节失败域只捕这一种）。"""

    def test_a_bare_json_array_becomes_the_draft_list(self):
        assert _engine(_reply(_POINTS_REPLY)).points(_section()).drafts == _POINTS_REPLY

    def test_a_fenced_array_parses_as_the_same_drafts(self):
        fenced = "```json\n" + json.dumps(_POINTS_REPLY, ensure_ascii=False) + "\n```"
        engine = _engine(lambda request: httpx.Response(200, json={"reply": fenced, "success": True}))
        assert engine.points(_section()).drafts == _POINTS_REPLY

    def test_a_reply_wrapped_in_prose_still_yields_the_array(self):
        noise = "好的，以下是知识点：\n" + json.dumps(_POINTS_REPLY, ensure_ascii=False) + "\n以上。"
        engine = _engine(lambda request: httpx.Response(200, json={"reply": noise, "success": True}))
        assert engine.points(_section()).drafts == _POINTS_REPLY

    def test_a_stray_object_in_the_prose_does_not_steal_the_array(self):
        """模型把示例写成 `{name: ...}` 摆在数组前面：那枚假对象不该吃掉整节，回落数组支照收。"""
        noise = (
            "好的，元素形如 {name: ...} 的对象：\n" + json.dumps(_POINTS_REPLY, ensure_ascii=False) + "\n以上。"
        )
        engine = _engine(lambda request: httpx.Response(200, json={"reply": noise, "success": True}))
        assert engine.points(_section()).drafts == _POINTS_REPLY

    def test_an_object_reply_yields_both_halves(self):
        """正形状：草稿数组与节级总结各归一枚读数，总结缺省空串而不是缺键。"""
        engine = _engine(_reply_object(_SECTION_SUMMARY, _POINTS_REPLY))
        produced = engine.points(_section())
        assert produced.drafts == _POINTS_REPLY
        assert produced.summary == _SECTION_SUMMARY

    def test_a_fenced_object_reply_parses_the_same_way(self):
        payload = json.dumps(
            {"section_summary": _SECTION_SUMMARY, "points": _POINTS_REPLY}, ensure_ascii=False
        )
        engine = _engine(
            lambda request: httpx.Response(200, json={"reply": f"```json\n{payload}\n```", "success": True})
        )
        produced = engine.points(_section())
        assert (produced.drafts, produced.summary) == (_POINTS_REPLY, _SECTION_SUMMARY)

    def test_a_bare_array_reply_carries_an_empty_summary(self):
        assert _engine(_reply(_POINTS_REPLY)).points(_section()).summary == ""

    def test_an_object_reply_without_points_is_a_shape_failure(self):
        """对象那一支不可用而回复里又没有数组可回落时判故障，不折算成零产出（`KA-32`）。"""
        engine = _engine(lambda request: httpx.Response(200, json={"reply": '{"name": "只有一个对象"}', "success": True}))
        with pytest.raises(EngineError, match="缺可用的 points 数组"):
            engine.points(_section())

    def test_an_object_whose_points_is_not_a_list_is_a_shape_failure(self):
        engine = _engine(
            lambda request: httpx.Response(
                200, json={"reply": '{"section_summary": "甲", "points": "乙"}', "success": True}
            )
        )
        with pytest.raises(EngineError, match="缺可用的 points 数组"):
            engine.points(_section())

    def test_a_non_json_reply_is_a_shape_failure(self):
        """散文式回复不折算成零产出：那会让「模型没按形状答」在读数里长得像「这书没有知识点」。"""
        engine = _engine(lambda request: httpx.Response(200, json={"reply": "我暂时无法给出知识点", "success": True}))
        with pytest.raises(EngineError, match="不是 JSON"):
            engine.points(_section())

    def test_a_blank_reply_is_a_failure_not_a_silent_zero(self):
        """空回复多半是输出被截断：读成故障才会被节失败域披露，静默 `[]` 就长得像「这节没有知识点」。"""
        engine = _engine(lambda request: httpx.Response(200, json={"reply": "   ", "success": True}))
        with pytest.raises(EngineError, match="空回复"):
            engine.points(_section())

    def test_an_empty_array_reply_is_an_empty_draft_list(self):
        """模型认不出任何条目是合法结局（编排层按零产出披露），不是故障。"""
        engine = _engine(_reply([]))
        assert engine.points(_section()).drafts == []


class TestInvalidJsonEscapeTolerance:
    """DEFECT-026 甲：模型把 LaTeX 的单枚反斜杠原样写进 JSON 字符串（真机原证＝`qed_llm_calls`
    id 130 `status=success`、3617 字符而 `json.loads` 报 `Invalid \\escape: line 75 column 99`），
    一处坏转义不能吞掉整节知识点——数学教材正文里 `\\mu`、`\\,`、`\\rho` 是常态。"""

    def test_a_latex_backslash_reply_keeps_its_drafts_and_the_backslash(self):
        reply = '[{"name": "由 $\\mu$ 与 $\\,$ 定出的界", "page": 7}]'
        engine = _engine(lambda request: httpx.Response(200, json={"reply": reply, "success": True}))
        assert engine.points(_section()).drafts == [{"name": "由 $\\mu$ 与 $\\,$ 定出的界", "page": 7}]

    def test_the_object_shape_goes_through_the_same_repair_leg(self):
        """两半都带 LaTeX 时补计腿照样救得回来：对象支与数组支共用同一枚容错，不是两处各写一遍。"""
        reply = '{"section_summary": "由 $\\mu$ 定出的界", "points": [{"name": "$\\,$ 的读法"}]}'
        engine = _engine(lambda request: httpx.Response(200, json={"reply": reply, "success": True}))
        produced = engine.points(_section())
        assert (produced.summary, produced.drafts) == ("由 $\\mu$ 定出的界", [{"name": "$\\,$ 的读法"}])

    def test_the_repair_leg_leaves_valid_escapes_byte_for_byte(self):
        """过修比不修更坏：把合法转义也加倍会让 `\\n` 变成字面反斜杠加 n，正文从此不可信。"""
        valid = json.dumps(
            [{"name": '含 "引号"、单枚 \\ 反斜杠、\n 换行与 \u00e9', "page": 7}], ensure_ascii=False
        )
        assert _repair_json_escapes(valid) == valid

    def test_the_repair_leg_doubles_only_the_lone_backslash(self):
        assert _repair_json_escapes(r'["a \q b"]') == r'["a \\q b"]'
        assert _repair_json_escapes(r'["\u12"]') == r'["\\u12"]'

    def test_a_reply_that_stays_broken_after_repair_is_still_a_shape_failure(self):
        """容错腿不替模型兜底：修完还是不可解析就照原样判故障，并说出「修过仍不行」。"""
        engine = _engine(
            lambda request: httpx.Response(200, json={"reply": '[{"name": "残缺 \\q 未闭合"', "success": True})
        )
        with pytest.raises(EngineError, match="不是 JSON"):
            engine.points(_section())


class TestFailureNormalization:
    """四类故障全归一为 `EngineError`：节失败域只捕这一种，穿透出去就成了整批回滚。"""

    def test_an_unsuccessful_gateway_reply_carries_its_error_text(self):
        engine = _engine(
            lambda request: httpx.Response(200, json={"reply": "", "success": False, "error": "本地模型未加载"})
        )
        with pytest.raises(EngineError, match="本地模型未加载"):
            engine.points(_section())

    def test_a_non_2xx_gateway_reply_is_normalized(self):
        engine = _engine(lambda request: httpx.Response(503, json={"detail": "文字模型槽位不可用"}))
        with pytest.raises(EngineError, match="503"):
            engine.points(_section())

    def test_a_non_json_error_envelope_is_still_an_engine_error(self):
        engine = _engine(lambda request: httpx.Response(502, text="<html>bad gateway</html>"))
        with pytest.raises(EngineError):
            engine.points(_section())

    def test_a_transport_failure_does_not_escape_as_httpx(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("8900 未在听")

        engine = _engine(handler)
        with pytest.raises(EngineError, match="8900 未在听"):
            engine.points(_section())
        with pytest.raises(EngineError):
            engine.points(_section())

    def test_health_reports_unreachable_as_false_not_as_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("8900 未在听")

        assert KpGatewayEngine(GW, client=_client(handler), timeout=5.0).health() is False


class TestModelNameProbe:
    """PLAN-033 W5：只读探当前文字槽位模型名，只给异常注记；失败回空串，不阻断、不切换。"""

    def test_the_probe_reads_the_default_slot_model(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(
                200,
                json={"default": {"model": "qwen3.5-9b", "provider": "vllm", "configured": True}},
            )

        engine = KpGatewayEngine(GW, client=_client(handler), timeout=5.0)

        assert engine.current_model_name() == "qwen3.5-9b"
        assert seen == ["/api/v1/config/models"]

    @pytest.mark.parametrize(
        "handler",
        [
            lambda request: httpx.Response(503, text="down"),
            lambda request: httpx.Response(200, text="<html>not json</html>"),
            lambda request: httpx.Response(200, json={}),
            lambda request: httpx.Response(200, json={"default": {"model": ""}}),
        ],
    )
    def test_an_unreadable_probe_is_an_empty_name(self, handler):
        assert KpGatewayEngine(GW, client=_client(handler), timeout=5.0).current_model_name() == ""

    def test_an_unreachable_gateway_does_not_raise(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("8900 未在听")

        assert KpGatewayEngine(GW, client=_client(handler), timeout=5.0).current_model_name() == ""


class TestEngineIdentity:
    def test_the_name_is_the_registered_slot(self):
        assert KP_SLOT_NAME == "qed-gateway"
        assert _engine(_reply([])).name == "qed-gateway"

    def test_the_adapter_declares_external_accounting(self):
        """R8 甲的判据开关：本仓自写账腿在这条通路上停用，靠的就是这一记声明。"""
        assert _engine(_reply([])).accounts_externally is True

    def test_an_injected_client_is_not_closed_by_the_adapter(self):
        client = _client(_reply([]))
        KpGatewayEngine(GW, client=client, timeout=5.0).bind_batch(book_id="b", gen_batch="g").close()
        assert client.is_closed is False

    def test_the_adapter_client_never_hands_loopback_to_the_system_proxy(self):
        """`MI-10` 同一条纪律：8900 在回环上，交给系统代理就会出现 502／0 字节的假象。"""
        engine = KpGatewayEngine(GW, timeout=5.0)
        try:
            assert engine._http().trust_env is False
        finally:
            engine.close()


def _samples() -> tuple[PageSample, ...]:
    return (
        PageSample(page_no=1, text="[paragraph] 前言 译者\n[paragraph] ……  iii"),
        PageSample(page_no=2, text="[paragraph] 第 1 章 极限 ……… 1\n[paragraph] 1.1 数列极限 ……… 3"),
        PageSample(page_no=3, text="[paragraph] 正文第一页"),
    )


def _pick(toc_pages, reference_pages=()) -> str:
    return json.dumps(
        {
            "toc_pages": list(toc_pages),
            "reference_pages": [
                {"page": page_no, "name": name, "kind": kind} for page_no, name, kind in reference_pages
            ],
        },
        ensure_ascii=False,
    )


class TestTocPageSelectionAndOrganizeLegs:
    """`KA-37` 目录两条正腿（PLAN-026 W18）：判页一次、整理逐页，**坏形状判故障而不是零产出**。

    判页腿的价值在「样本外的页号整条弃用」：模型报一枚没送给它的页，编排层就会把锚指到没读过的页上。
    整理腿的价值在失败域的划分：一行抄坏只丢一行，整页抄坏要判故障，该页才回落 `KA-33` 条目腿。
    """

    def test_the_pick_makes_one_call_with_the_attributed_body(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=_pick([2], [(1, "前言", "front_matter")])))

        pick = engine.toc_pages(_samples())

        assert len(sent) == 1
        body = sent[0]
        assert body["step"] == "toc_pages"
        assert set(body) <= {"prompt", "system", "prompt_template", "max_tokens", "service", "task", "step", "meta"}
        assert body["meta"]["book_id"] == "mathanalysis-b05"
        assert body["meta"]["gen_batch"] == "afkp-20261026t000000"
        assert body["meta"]["engine"] == KP_SLOT_NAME
        assert body["meta"]["page_count"] == 3
        assert pick.toc_pages == (2,)
        assert pick.reference_pages == (ReferencePage(page_no=1, name="前言", kind="front_matter"),)
        # 样本页要真的送到模型眼前，否则「页号只能取样本里的」这句判据无从成立
        assert "[页 2]" in body["prompt"]

    def test_pages_outside_the_sample_are_dropped_whole(self):
        """域外页号整条弃用（`KA-37`）：目录页与附件页两路都丢，不保留「大概对」的那半。"""
        sent: list[dict] = []
        reply = json.dumps(
            {
                "toc_pages": [2, 99],
                "reference_pages": [{"page": 1, "name": "前言", "kind": "front_matter"},
                                     {"page": 88, "name": "索引", "kind": "index"}],
            },
            ensure_ascii=False,
        )

        pick = _engine(_capturing(sent, reply=reply)).toc_pages(_samples())

        assert pick.toc_pages == (2,)
        assert [ref.page_no for ref in pick.reference_pages] == [1]

    def test_a_page_claimed_as_both_is_not_a_toc_page(self):
        """同一页既报目录又报附件 ⇒ 判附件不判目录：符号表当目录用正是上一轮 49 枚假章的来源。"""
        sent: list[dict] = []
        reply = json.dumps(
            {"toc_pages": [1], "reference_pages": [{"page": 1, "name": "重要符号表", "kind": "symbol_table"}]},
            ensure_ascii=False,
        )

        pick = _engine(_capturing(sent, reply=reply)).toc_pages(_samples())

        assert pick.toc_pages == ()
        assert [ref.kind for ref in pick.reference_pages] == ["symbol_table"]

    def test_an_unknown_kind_degrades_to_no_kind_not_a_dropped_row(self):
        sent: list[dict] = []
        reply = json.dumps(
            {"toc_pages": [], "reference_pages": [{"page": 3, "name": "致谢", "kind": "thanks"}]},
            ensure_ascii=False,
        )

        pick = _engine(_capturing(sent, reply=reply)).toc_pages(_samples())

        assert pick.reference_pages[0].kind == ""

    def test_an_empty_sample_makes_no_call(self):
        sent: list[dict] = []

        pick = _engine(_capturing(sent)).toc_pages(())

        assert pick.toc_pages == () and pick.reference_pages == ()
        assert sent == []

    @pytest.mark.parametrize(
        "reply",
        ["", "抱歉，这一轮我无法判断。", "[]", '{"toc_pages": [2'],
    )
    def test_a_reply_that_is_not_a_json_object_is_a_failure(self, reply):
        """判页失败 ⇒ 抛 `EngineError`，由编排层全书回落条目腿；静默回空集就成了「这本书没有目录」。"""
        sent: list[dict] = []
        with pytest.raises(EngineError):
            _engine(_capturing(sent, reply=reply)).toc_pages(_samples())

    def test_illegal_json_escapes_are_repaired_before_the_pick(self):
        """DEFECT-026 甲同一条补计腿也护目录步：符号表名字里的 `$\\mu$` 不该吃掉整次判页。"""
        sent: list[dict] = []
        reply = r'{"toc_pages": [], "reference_pages": [{"page": 1, "name": "$\mu$ 记号", "kind": "symbol_table"}]}'

        pick = _engine(_capturing(sent, reply=reply)).toc_pages(_samples())

        assert pick.reference_pages[0].name == "$" + chr(92) + "mu$ 记号"

    def test_the_pipe_rows_become_entries(self):
        sent: list[dict] = []
        reply = "1|12|第 1 章 极限\n- 2|15|1.1 数列极限\n2|-|1.2 函数极限\n"

        entries = _engine(_capturing(sent, reply=reply)).toc_entries(4, "第 1 章 极限 ……… 12")

        assert len(entries) == 3
        assert [(e.level, e.printed_page, e.name) for e in entries] == [
            (1, 12, "第 1 章 极限"),
            (2, 15, "1.1 数列极限"),
            (2, None, "1.2 函数极限"),
        ]
        assert sent[0]["step"] == "toc_entries"
        assert sent[0]["meta"]["toc_page"] == 4

    def test_a_non_arabic_page_keeps_the_name_and_has_no_page(self):
        """罗马页码／粘连数字按「这一行没有印刷页码」处理（`KA-37`）：丢行比缺页更难查。"""
        sent: list[dict] = []
        reply = "1|xi|Preface\n2|iii 译名表|译名表\n"

        entries = _engine(_capturing(sent, reply=reply)).toc_entries(2, "Preface ……… xi")

        assert [(e.printed_page, e.name) for e in entries] == [(None, "Preface"), (None, "译名表")]

    def test_the_three_malformed_row_shapes_are_dropped_and_good_rows_survive(self):
        sent: list[dict] = []
        reply = "\n".join(
            [
                "1|12|好的行",
                "3|12|三级细目",
                "4|12|层级越界",
                "1|12|只有两段",
                "1|12|",
                "名字 12",
            ]
        )

        entries = _engine(_capturing(sent, reply=reply)).toc_entries(5, "该页原文")

        assert [e.name for e in entries] == ["好的行", "三级细目", "只有两段"], (
            "三级是合法层级（KA-62 折入二级）；越界的是四段及以上"
        )

    def test_none_is_an_empty_page_not_a_failure(self):
        """`NONE` 是模型自己说「这页没有条目」⇒ 空列表；它和「抄坏了」是两回事。"""
        sent: list[dict] = []

        assert _engine(_capturing(sent, reply="NONE")).toc_entries(6, "该页原文") == []

    @pytest.mark.parametrize("reply", ["", "第 1 章 极限\n1.1 数列的极限\n", "（无）"])
    def test_a_page_whose_rows_are_all_unparsable_is_a_failure(self, reply):
        """整页不可解析 ⇒ 判故障，该页回落条目腿；回 `[]` 会让这一页在目录里静默消失。"""
        sent: list[dict] = []
        with pytest.raises(EngineError):
            _engine(_capturing(sent, reply=reply)).toc_entries(7, "该页原文")

    def test_a_fenced_reply_is_stripped_before_row_parse(self):
        sent: list[dict] = []
        reply = "```text\n1|12|第 1 章 极限\n```"

        entries = _engine(_capturing(sent, reply=reply)).toc_entries(8, "该页原文")

        assert [e.name for e in entries] == ["第 1 章 极限"]

    @staticmethod
    def _outline_rows() -> tuple[TocOutlineRow, ...]:
        return (
            TocOutlineRow(index=0, level=1, name="四章 利用导数研究函数"),
            TocOutlineRow(index=1, level=2, name="1. 前言"),
            TocOutlineRow(index=2, level=2, name="§1. 有理数域"),
        )

    def test_outline_corrections_keep_the_row_identity_and_drop_unknown_rows(self):
        """校订腿只回改动行：序号认得出、层级照旧、名字照收；输入里没有的序号整条弃用。"""
        sent: list[dict] = []
        reply = "0|第四章 利用导数研究函数\n2|1|改名 | 带竖线\n9|不存在的行\n这不是表格\n"

        corrections = _engine(_capturing(sent, reply=reply)).toc_outline(self._outline_rows())

        assert [(row.index, row.level, row.name) for row in corrections] == [
            (0, 1, "第四章 利用导数研究函数"),
            (2, 2, "改名 | 带竖线"),
        ]
        assert sent[0]["step"] == "toc_outline"
        assert sent[0]["meta"]["count"] == 3

    def test_none_or_unparsable_outline_replies_are_no_corrections(self):
        """校订是可选腿：`NONE` 与整段不可解析都只意味着「名字保持原文」，不是故障。"""
        assert _engine(_capturing([], reply="NONE")).toc_outline(self._outline_rows()) == []
        assert _engine(_capturing([], reply="这不是表格")).toc_outline(self._outline_rows()) == []

    def test_an_empty_outline_reply_is_a_failure(self):
        """空回复多半是输出被截断：判故障交编排层记警告，不冒充「没有订正」。"""
        with pytest.raises(EngineError):
            _engine(_capturing([], reply="")).toc_outline(self._outline_rows())

    def test_the_note_reaches_all_three_toc_legs(self):
        """备注跟批次走（AF-T5 甲）：目录四腿都读到人工上一轮的意见，也都不写进归因 meta。"""
        note = "页眉粘进目录了，剔掉"
        legs = (
            ("toc_pages", _pick([2]), lambda engine: engine.toc_pages(_samples())),
            ("toc_page", "NOT_TOC", lambda engine: engine.toc_page(3, "该页原文")),
            ("toc_entries", "1|12|第 1 章 极限", lambda engine: engine.toc_entries(3, "该页原文")),
            ("toc_outline", "NONE", lambda engine: engine.toc_outline(self._outline_rows())),
            (
                "toc_verify",
                "[]",
                lambda engine: engine.toc_verify(
                    (TocVerifyItem(name="1 函数的极限", printed_page=3, candidates=((4, "函数的极限"),)),)
                ),
            ),
        )
        sent: list[dict] = []
        for _step, reply, invoke in legs:
            engine = KpGatewayEngine(
                GW, client=_client(_capturing(sent, reply=reply)), timeout=5.0
            ).bind_batch(book_id="b", gen_batch="g", review_note=note)
            invoke(engine)

        assert [body["step"] for body in sent] == [
            "toc_pages",
            "toc_page",
            "toc_entries",
            "toc_outline",
            "toc_verify",
        ]
        assert all(note in body["prompt"] for body in sent)
        assert all("review_note" not in body["meta"] for body in sent)

    def test_an_unbound_adapter_refuses_every_toc_leg(self):
        """未绑定批次身份就拒跑（R8 甲）：四条目录腿一条都不放过，否则这条调用查不到主人。"""
        engine = KpGatewayEngine(GW, client=_client(_reply([])), timeout=5.0)
        item = TocVerifyItem(name="1 函数的极限", printed_page=None, candidates=())
        with pytest.raises(EngineError, match="toc_pages"):
            engine.toc_pages(_samples())
        with pytest.raises(EngineError, match="toc_page"):
            engine.toc_page(3, "该页原文")
        with pytest.raises(EngineError, match="toc_entries"):
            engine.toc_entries(3, "该页原文")
        with pytest.raises(EngineError, match="toc_outline"):
            engine.toc_outline(self._outline_rows())
        with pytest.raises(EngineError, match="toc_verify"):
            engine.toc_verify((item,))


class TestSequentialPageLeg:
    """P-2／P-3 逐页判页腿：判定与条目同一次读数回来；坏判定行与「行全坏」判该页失败。"""

    def test_a_toc_page_returns_the_verdict_and_its_rows_in_one_reading(self):
        sent: list[dict] = []
        reply = "TOC\n1|1|第 1 章 集合论\n2|2|序的关系与集合的势\n"

        reading = _engine(_capturing(sent, reply=reply)).toc_page(2, "[paragraph] 目录\n…… 2")

        assert reading.is_toc is True
        assert [(entry.level, entry.printed_page, entry.name) for entry in reading.entries] == [
            (1, 1, "第 1 章 集合论"),
            (2, 2, "序的关系与集合的势"),
        ]
        assert len(sent) == 1
        assert sent[0]["step"] == "toc_page"
        assert sent[0]["meta"]["toc_page"] == 2
        assert "本页页号：2" in sent[0]["prompt"]

    def test_the_whole_page_text_reaches_the_prompt_untruncated(self):
        """P-2：判「这一页是不是目录」要看整页，不与判页样本的 400 字封顶共用。"""
        sent: list[dict] = []
        long = "第1章 实数与完备性 …… " + "·" * 900

        _engine(_capturing(sent, reply="NOT_TOC")).toc_page(3, f"[paragraph] {long}")

        assert long in sent[0]["prompt"]

    def test_a_non_toc_verdict_ends_the_range_and_has_no_rows(self):
        reading = _engine(_capturing([], reply="NOT_TOC")).toc_page(4, "[paragraph] 正文")

        assert reading.is_toc is False
        assert reading.entries == ()

    @pytest.mark.parametrize("head", ["not_toc", "NOT TOC", "否", "NOT-TOC"])
    def test_the_verdict_tolerates_spacing_and_case(self, head):
        reading = _engine(_capturing([], reply=head)).toc_page(5, "[paragraph] 正文")

        assert reading.is_toc is False

    def test_a_toc_verdict_with_none_has_no_rows(self):
        reading = _engine(_capturing([], reply="TOC\nNONE")).toc_page(6, "[paragraph] 目录")

        assert reading.is_toc is True
        assert reading.entries == ()

    @pytest.mark.parametrize(
        "reply",
        ["", "抱歉，这一轮我无法判断。", "1|12|第 1 章 极限\n1.1 数列的极限\n"],
    )
    def test_an_unparsable_reply_is_a_page_failure(self, reply):
        """空回复与坏判定行 ⇒ 判该页失败、回落条目腿；静默答 NOT_TOC 会让目录在那一页断掉。"""
        with pytest.raises(EngineError):
            _engine(_capturing([], reply=reply)).toc_page(7, "[paragraph] 目录")

    def test_a_toc_verdict_whose_rows_are_all_broken_is_a_page_failure(self):
        """判成目录页却一行都解不出 ⇒ 故障，该页回落确定性抄录，不冒充「这一页没有条目」。"""
        with pytest.raises(EngineError):
            _engine(_capturing([], reply="TOC\n第 1 章 极限\n1.1 数列的极限\n")).toc_page(
                8, "[paragraph] 目录"
            )


class TestTocVerifyLeg:
    """`KA-38` 核对腿：坏行攒一批一次问，**只答页号、不改名字**。"""

    @staticmethod
    def _items() -> tuple[TocVerifyItem, ...]:
        return (
            TocVerifyItem(name="函数的极限", printed_page=73, candidates=((74, "1.2 函数的极限"),)),
            TocVerifyItem(name="RIEMANN-STIEI. TJES", printed_page=201, candidates=((202, "黎曼-斯蒂尔杰斯积分"),)),
        )

    def test_the_whole_batch_is_asked_in_one_call(self):
        sent: list[dict] = []
        reply = json.dumps(
            [{"name": "函数的极限", "page": 74}, {"name": "RIEMANN-STIEI. TJES", "page": 202}],
            ensure_ascii=False,
        )

        answers = _engine(_capturing(sent, reply=reply)).toc_verify(self._items())

        assert len(sent) == 1
        assert sent[0]["step"] == "toc_verify"
        assert sent[0]["meta"]["count"] == 2
        assert [(a.name, a.page_no) for a in answers] == [("函数的极限", 74), ("RIEMANN-STIEI. TJES", 202)]
        # 候选页的行要送出去，否则模型无从比对异形写法
        assert "黎曼-斯蒂尔杰斯积分" in sent[0]["prompt"]

    def test_a_null_page_answer_survives_as_no_page(self):
        sent: list[dict] = []
        reply = json.dumps([{"name": "函数的极限", "page": None}, {"name": "RIEMANN-STIEI. TJES", "page": 0}])

        answers = _engine(_capturing(sent, reply=reply)).toc_verify(self._items())

        assert [a.page_no for a in answers] == [None, None]

    def test_elements_that_are_not_objects_are_dropped(self):
        sent: list[dict] = []
        reply = json.dumps([{"name": "函数的极限", "page": 74}, "字符串一条", {"page": 75}], ensure_ascii=False)

        answers = _engine(_capturing(sent, reply=reply)).toc_verify(self._items())

        assert [(a.name, a.page_no) for a in answers] == [("函数的极限", 74)]

    def test_an_empty_batch_makes_no_call(self):
        sent: list[dict] = []

        assert _engine(_capturing(sent)).toc_verify(()) == []
        assert sent == []

    @pytest.mark.parametrize("reply", ["", '{"name": "函数的极限", "page": 74}', "不是 JSON"])
    def test_a_reply_that_is_not_an_array_is_a_failure(self, reply):
        """核对失败 ⇒ 抛 `EngineError`，由编排层按未定位披露；把坏回复当「都没命中」就抹掉了故障。"""
        sent: list[dict] = []
        with pytest.raises(EngineError):
            _engine(_capturing(sent, reply=reply)).toc_verify(self._items())



def _material() -> ChapterInput:
    return ChapterInput(
        parent_id="afkp-outline-ch1",
        title="第 2 章 数列",
        page_start=9,
        page_end=28,
        blocks=(
            SectionBlock(page=9, block_index=0, text="# 第 2 章 数列"),
            SectionBlock(page=9, block_index=1, text="章前正文甲。"),
        ),
        point_summaries=(("2.1 数列的极限", "这一节把收敛写成 ε-N 语言。"),),
    )


class TestChapterLegOutbound:
    """`KA-65` 章文件夹的出站形状：一次调用、`step='chapter'`、章前块与节级总结清单都在 prompt 里。"""

    def test_the_call_carries_the_intro_blocks_and_the_summary_list(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}'))

        reply = engine.chapter(_material())

        body = sent[0]
        assert body["step"] == "chapter"
        assert body["meta"]["point_count"] == 1
        assert body["meta"]["material_pages"] == [9]
        assert "p9·b0" in body["prompt"] and "p9·b1" in body["prompt"], "章前块带坐标"
        assert "章前正文甲。" in body["prompt"]
        assert "- 2.1 数列的极限：这一节把收敛写成 ε-N 语言。" in body["prompt"]
        assert (reply.summary, reply.narrative) == ("章总结", "章导读")

    def test_a_chapter_without_intro_blocks_states_so_in_the_prompt(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}'))
        material = ChapterInput(
            parent_id="afkp-outline-ch1",
            title="第 2 章 数列",
            page_start=9,
            page_end=28,
            blocks=(),
            point_summaries=(),
        )

        engine.chapter(material)

        prompt = sent[0]["prompt"]
        assert "没有单独的章前正文" in prompt
        assert "其下各知识点都没有总结" in prompt

    def test_reference_notes_reach_the_chapter_prompt(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}'))
        material = replace(
            _material(),
            reference_notes=(
                ReferenceNote(source="维基百科", title="连续性", text="函数在一点连续指极限等于函数值。"),
            ),
        )

        engine.chapter(material)

        prompt = sent[0]["prompt"]
        assert "检索资料（只用于校准术语与定义；教材结论优先）" in prompt
        assert "[维基百科·连续性] 函数在一点连续指极限等于函数值。" in prompt

    def test_no_reference_block_when_there_are_no_notes(self):
        sent: list[dict] = []
        _engine(
            _capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}')
        ).chapter(_material())

        assert "检索资料（只用于校准术语与定义；教材结论优先）" not in sent[0]["prompt"]

    def test_retry_issues_become_a_modification_request(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}'))
        material = replace(_material(), retry_issues=("出现了页数「共 32 页」",))

        engine.chapter(material)

        prompt = sent[0]["prompt"]
        assert "本次修改要求" in prompt
        assert "出现了页数「共 32 页」" in prompt

    def test_the_prompt_bans_book_level_and_later_chapter_talk(self):
        sent: list[dict] = []
        _engine(
            _capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}')
        ).chapter(_material())

        prompt = sent[0]["prompt"]
        assert "不写页数" in prompt
        assert "不写「本章位于全书…」" in prompt
        assert "不写「为第 X 章…」" in prompt
        assert "整门课里接到哪里" not in prompt
        assert "后面哪一章会直接用到它" not in prompt
        assert "只谈本章" in prompt
        assert "页区间：" not in prompt


class TestCourseChapterOutbound:
    def _material(self) -> ChapterTextInput:
        return ChapterTextInput(
            course_id="math_analysis",
            chapter_kp_id="kp-course-ch3",
            chapter_name="第 3 章 极限",
            chapter_summary="课程级旧总结",
            chapter_narrative="课程级旧导读",
            sources=(
                ChapterTextSource(
                    book_id="mathanalysis-b18",
                    title="数学分析",
                    chapter_name="第三章 极限",
                    summary="b18 章总结",
                    points=(
                        ChapterTextPoint(name="数列极限", summary="ε-N 定义"),
                        ChapterTextPoint(name="函数极限", summary="邻域定义"),
                    ),
                ),
                ChapterTextSource(
                    book_id="mathanalysis-b05",
                    title="数学分析原理",
                    chapter_name="第 3 章 极限",
                    summary="b05 章总结",
                    points=(ChapterTextPoint(name="极限的性质", summary="唯一性与保号性"),),
                ),
            ),
        )

    def test_hash_binds_the_exact_external_accounting_identity(self):
        sent: list[dict] = []
        digest = "0123456789abcdef" * 4
        engine = KpGatewayEngine(
            GW,
            client=_client(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}')),
            timeout=5.0,
        ).bind_chapter_text(
            course_id="math_analysis",
            chapter_kp_id="kp-course-ch3",
            evidence_sha256=digest,
        )

        reply = engine.chapter_text(self._material())

        body = sent[0]
        assert (reply.summary, reply.narrative) == ("章总结", "章导读")
        assert body["service"] == "axiom_flow"
        assert body["task"] == "kp-merge-chapter-0123456789ab"
        assert body["step"] == "chapter"
        assert body["prompt_template"] == f"kp-merge-chapter/{digest}@{CHAPTER_TEXT_PROMPT_VERSION}"
        assert body["meta"] == {
            "course_id": "math_analysis",
            "chapter_kp_id": "kp-course-ch3",
            "evidence_sha256": digest,
            "source_count": 2,
            "point_count": 3,
            "engine": "qed-gateway",
        }
        assert not {"api_key", "key", "model"} & set(body)
        assert engine.accounts_externally is True

    def test_prompt_uses_all_sources_without_page_or_block_provenance(self):
        sent: list[dict] = []
        KpGatewayEngine(
            GW,
            client=_client(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}')),
            timeout=5.0,
        ).bind_chapter_text(
            course_id="math_analysis",
            chapter_kp_id="kp-course-ch3",
            evidence_sha256="a" * 64,
        ).chapter_text(self._material())

        prompt = sent[0]["prompt"]
        assert "课程级旧总结" in prompt and "课程级旧导读" in prompt
        assert "b18 章总结" in prompt and "b05 章总结" in prompt
        assert "数列极限：ε-N 定义" in prompt and "函数极限：邻域定义" in prompt
        assert "极限的性质：唯一性与保号性" in prompt
        assert "章前正文" not in prompt
        assert "p页·b" not in prompt and "[页 " not in prompt

    def test_unbound_course_identity_refuses_the_call(self):
        engine = KpGatewayEngine(GW, client=_client(_capturing([])), timeout=5.0)
        with pytest.raises(EngineError, match="chapter_text"):
            engine.chapter_text(self._material())


class TestCourseChapterLedgerRecovery:
    digest = "0123456789abcdef" * 4
    recovered = '{"chapter_summary": "恢复总结", "chapter_narrative": "恢复导读"}'

    @staticmethod
    def _material() -> ChapterTextInput:
        return ChapterTextInput(
            course_id="math_analysis",
            chapter_kp_id="kp-course-ch3",
            chapter_name="第 3 章 极限",
            sources=(
                ChapterTextSource(
                    book_id="mathanalysis-b18",
                    title="数学分析",
                    chapter_name="第三章 极限",
                    points=(ChapterTextPoint(name="数列极限", summary="ε-N 定义"),),
                ),
            ),
        )

    @staticmethod
    def _row(payload: dict, **changes) -> dict:
        row = {
            "service": payload["service"],
            "task": payload["task"],
            "step": payload["step"],
            "status": "success",
            "prompt_template": payload["prompt_template"],
            "prompt": payload["prompt"],
            "response": TestCourseChapterLedgerRecovery.recovered,
        }
        row.update(changes)
        return row

    def _case(
        self,
        *,
        matching: int = 1,
        distractors=None,
        ledger_status: int = 200,
        ledger_payload=None,
        post_status: int | None = None,
    ):
        posts: list[dict] = []
        queries: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/v1/llm/text":
                body = json.loads(request.content.decode("utf-8"))
                posts.append(body)
                if post_status is not None:
                    return httpx.Response(post_status, text="gateway failed")
                raise httpx.ReadTimeout("read timeout", request=request)
            assert request.url.path == "/api/v1/llm/calls"
            queries.append(dict(request.url.params))
            if ledger_status != 200:
                return httpx.Response(ledger_status, text="ledger unavailable")
            if ledger_payload is not None:
                return httpx.Response(200, json=ledger_payload)
            extra = distractors(posts[0]) if distractors is not None else []
            rows = [*extra, *[self._row(posts[0]) for _ in range(matching)]]
            return httpx.Response(200, json={"items": rows, "total": len(rows), "page": 1, "size": 200})

        engine = KpGatewayEngine(GW, client=_client(handler), timeout=5.0).bind_chapter_text(
            course_id="math_analysis",
            chapter_kp_id="kp-course-ch3",
            evidence_sha256=self.digest,
        )
        return engine, posts, queries

    def test_unique_exact_success_recovers_without_resending(self):
        def distractors(payload):
            return [
                self._row(payload, service="qed_engine"),
                self._row(payload, task="kp-curation"),
                self._row(payload, step="points"),
                self._row(payload, status="error"),
                self._row(payload, prompt_template=payload["prompt_template"] + "-other"),
                self._row(payload, prompt=payload["prompt"] + "不同"),
                self._row(payload, response=None),
            ]

        engine, posts, queries = self._case(distractors=distractors)

        reply = engine.chapter_text(self._material())

        assert (reply.summary, reply.narrative) == ("恢复总结", "恢复导读")
        assert len(posts) == 1
        assert len(queries) == 1
        assert queries[0] == {
            "service": "axiom_flow",
            "task": f"kp-merge-chapter-{self.digest[:12]}",
            "step": "chapter",
            "status": "success",
            "prompt_template": f"kp-merge-chapter/{self.digest}@{CHAPTER_TEXT_PROMPT_VERSION}",
            "size": "200",
        }

    @pytest.mark.parametrize("matching", [0, 2])
    def test_zero_or_multiple_matches_refuse_automatic_resend(self, matching):
        engine, posts, queries = self._case(matching=matching)

        with pytest.raises(EngineError, match="不自动重发"):
            engine.chapter_text(self._material())

        assert len(posts) == 1
        assert len(queries) == 1

    def test_unreachable_ledger_refuses_automatic_resend(self):
        engine, posts, queries = self._case(ledger_status=503)

        with pytest.raises(EngineError, match="不自动重发"):
            engine.chapter_text(self._material())

        assert len(posts) == 1
        assert len(queries) == 1

    @pytest.mark.parametrize(
        "ledger_payload",
        [
            [],
            {"items": "bad", "total": 1},
            {"items": [], "total": 201},
        ],
    )
    def test_unverifiable_ledger_shape_refuses_automatic_resend(self, ledger_payload):
        engine, posts, _ = self._case(ledger_payload=ledger_payload)

        with pytest.raises(EngineError, match="不自动重发"):
            engine.chapter_text(self._material())

        assert len(posts) == 1

    def test_non_200_course_response_also_checks_the_ledger(self):
        engine, posts, queries = self._case(post_status=502)

        reply = engine.chapter_text(self._material())

        assert reply.summary == "恢复总结"
        assert len(posts) == 1
        assert len(queries) == 1

    def test_normal_batch_failure_does_not_gain_a_ledger_query(self):
        calls: list[str] = []

        def handler(request):
            calls.append(request.url.path)
            raise httpx.ReadTimeout("read timeout", request=request)

        engine = KpGatewayEngine(GW, client=_client(handler), timeout=5.0).bind_batch(
            book_id="mathanalysis-b05", gen_batch="afkp-recovery"
        )
        with pytest.raises(EngineError, match="网关不可达"):
            engine.points(_section())
        assert calls == ["/api/v1/llm/text"]


class TestTruncatedReplyTolerance:
    """#129 容错腿：回复被截断时保住已读完的字段，不把整节／整章判失败；
    一点字段都抢不到时仍是故障——坏形状不折算成零产出。
    """

    @staticmethod
    def _reply(text: str):
        return lambda request: httpx.Response(200, json={"reply": text, "success": True, "error": ""})

    def test_a_truncated_points_array_keeps_the_section_summary(self):
        reply = '{"section_summary": "这一节讲数列极限的定义与三条推论。", "points": [{"name": "数列极限", "summary": "用 ε-N'
        engine = _engine(self._reply(reply))

        points = engine.points(_section())

        assert points.drafts == []
        assert points.summary == "这一节讲数列极限的定义与三条推论。"

    def test_a_truncated_reply_without_a_summary_is_still_a_failure(self):
        engine = _engine(self._reply('{"points": [{"name": "数列极限"'))

        with pytest.raises(EngineError):
            engine.points(_section())

    def test_a_truncated_chapter_reply_keeps_the_fields_it_read(self):
        reply = '{"chapter_summary": "这一章给出极限的 ε-δ 理论。", "chapter_narrative": "导读写到一半'
        engine = _engine(self._reply(reply))

        replied = engine.chapter(_material())

        assert replied.summary == "这一章给出极限的 ε-δ 理论。"
        assert replied.narrative == "导读写到一半"

    def test_a_chapter_reply_with_no_readable_field_is_still_a_failure(self):
        engine = _engine(self._reply("不是 JSON"))

        with pytest.raises(EngineError):
            engine.chapter(_material())

    def test_an_empty_chapter_reply_is_a_failure_not_a_silent_blank(self):
        engine = _engine(self._reply(""))

        with pytest.raises(EngineError):
            engine.chapter(_material())


def _exercise_section() -> ExerciseInput:
    return ExerciseInput(
        title="习题 1.1",
        page_start=4,
        page_end=4,
        items=(
            ExerciseItemInput(
                item_no="1",
                label="1.",
                page_start=4,
                page_end=4,
                text="1. 求极限 lim(x→0) sin x / x。\n小问：说明理由。\n解：由洛必达法则得 1。\n所以极限为 1。",
            ),
            ExerciseItemInput(
                item_no="2",
                label="2.",
                page_start=4,
                page_end=4,
                text="2. 求证：任意实数的平方非负。",
            ),
        ),
    )


def _exercise_reply(items: list[dict]):
    payload = json.dumps({"items": items}, ensure_ascii=False)
    return lambda request: httpx.Response(200, json={"reply": payload, "success": True, "error": ""})


class TestExerciseSplitPointReply:
    """`KA-56`：题面／解答不由模型抄录——模型只给解答起始行号，正文由适配层从输入原文切出。"""

    def test_the_prompt_numbers_every_line_and_asks_only_for_the_split_point(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"items": []}, ensure_ascii=False)))

        engine.exercise(_exercise_section())

        prompt = sent[0]["prompt"]
        assert "solution_start" in prompt
        assert "  1| 1. 求极限 lim(x→0) sin x / x。" in prompt
        assert "  3| 解：由洛必达法则得 1。" in prompt
        assert '"problem"' not in prompt, "输出形状不再含题面／解答正文"

    def test_the_two_segments_are_sliced_verbatim_from_the_source(self):
        engine = _engine(
            _exercise_reply(
                [{"item_no": "【1】", "solution_start": 3}, {"item_no": "2", "solution_start": 0}]
            )
        )

        replied = engine.exercise(_exercise_section())

        assert [(draft.item_no, draft.problem, draft.solution) for draft in replied.items] == [
            (
                "1",
                "1. 求极限 lim(x→0) sin x / x。\n小问：说明理由。",
                "解：由洛必达法则得 1。\n所以极限为 1。",
            ),
            ("2", "2. 求证：任意实数的平方非负。", ""),
        ]

    def test_a_boundary_at_the_first_line_makes_the_whole_item_a_solution(self):
        engine = _engine(_exercise_reply([{"item_no": "1", "solution_start": 1}]))

        draft = engine.exercise(_exercise_section()).items[0]

        assert draft.problem == ""
        assert draft.solution == _exercise_section().items[0].text

    def test_an_unsure_or_out_of_range_boundary_is_left_empty_not_guessed(self):
        for boundary in ("3", -1, 99, None):
            engine = _engine(_exercise_reply([{"item_no": "1", "solution_start": boundary}]))
            draft = engine.exercise(_exercise_section()).items[0]
            assert (draft.problem, draft.solution) == ("", ""), boundary

    def test_an_ascii_bracketed_item_number_still_finds_its_text(self):
        """prompt 用 `[N]` 展示题号，模型照抄带方括号回来：同一把键必须命中本节原文。"""
        engine = _engine(_exercise_reply([{"item_no": "[1]", "solution_start": 3}]))

        draft = engine.exercise(_exercise_section()).items[0]

        assert draft.problem == "1. 求极限 lim(x→0) sin x / x。\n小问：说明理由。"
        assert draft.solution.startswith("解：由洛必达法则得 1。")

    def test_an_old_transcription_object_is_not_taken_as_content(self):
        engine = _engine(
            _exercise_reply([{"item_no": "1", "problem": "抄来的题面", "solution": "抄来的解答"}])
        )

        draft = engine.exercise(_exercise_section()).items[0]

        assert (draft.problem, draft.solution) == ("", "")

    def test_the_prompt_also_asks_for_the_solution_end_line(self):
        """REQ-179 R4：解答边界双端化——prompt 同时要 `solution_end`（含），判不出给 -1。"""
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"items": []}, ensure_ascii=False)))

        engine.exercise(_exercise_section())

        prompt = sent[0]["prompt"]
        assert "solution_end" in prompt

    def test_the_solution_end_line_trims_the_tail_of_the_solution(self):
        """`solution_end`（含）截住解答尾部：越界到下一题的长尾被去掉。"""
        engine = _engine(
            _exercise_reply([{"item_no": "【1】", "solution_start": 3, "solution_end": 3}])
        )

        draft = engine.exercise(_exercise_section()).items[0]

        assert draft.problem == "1. 求极限 lim(x→0) sin x / x。\n小问：说明理由。"
        assert draft.solution == "解：由洛必达法则得 1。"

    def test_a_missing_or_invalid_end_falls_back_to_the_section_tail(self):
        """end 缺席 / 非法 / -1 ＝ 判不出，回退「切到本题末尾」（现状行为），不猜。"""
        for end in (None, -1, "3", 0, True, 99):
            engine = _engine(
                _exercise_reply([{"item_no": "【1】", "solution_start": 3, "solution_end": end}])
            )
            draft = engine.exercise(_exercise_section()).items[0]
            assert draft.solution == "解：由洛必达法则得 1。\n所以极限为 1。", end

    def test_an_end_before_the_start_is_not_guessed_into_content(self):
        """end < start 是矛盾读数：解答按空处理（与 start 域外同一「不猜」档），不硬切。"""
        engine = _engine(
            _exercise_reply([{"item_no": "【1】", "solution_start": 3, "solution_end": 2}])
        )

        draft = engine.exercise(_exercise_section()).items[0]

        assert draft.solution == ""


class TestExerciseMatchLeg:
    """REQ-161 W4：匹配腿只做选择与章对应；域外书号按「没有对得上」，空回复不折算成无匹配。"""

    @staticmethod
    def _material() -> ExerciseMatchInput:
        return ExerciseMatchInput(
            title="数学分析习题集",
            chapters=("第 1 章 函数",),
            candidates=(
                ExerciseMatchCandidate(
                    book_id="b05",
                    title="数学分析原理",
                    chapters=("第 1 章 函数与极限", "1.1 映射"),
                ),
            ),
        )

    def test_the_prompt_carries_both_sides_and_the_step_is_its_own(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"book_id": None, "chapters": []})))

        engine.exercise_match(self._material())

        prompt = sent[0]["prompt"]
        assert "第 1 章 函数" in prompt
        assert "[b05] 数学分析原理" in prompt
        assert "第 1 章 函数与极限" in prompt
        assert sent[0]["step"] == "exercise_match"

    def test_a_choice_keeps_the_book_and_its_chapter_pairs(self):
        reply = json.dumps(
            {"book_id": "b05", "chapters": [{"source": "第 1 章 函数", "target": "第 1 章 函数与极限"}]},
            ensure_ascii=False,
        )
        engine = _engine(_capturing([], reply=reply))

        choice = engine.exercise_match(self._material())

        assert choice.book_id == "b05"
        assert choice.chapters == (("第 1 章 函数", "第 1 章 函数与极限"),)

    def test_an_out_of_scope_book_id_is_no_match(self):
        engine = _engine(_capturing([], reply=json.dumps({"book_id": "b99", "chapters": []})))

        choice = engine.exercise_match(self._material())

        assert (choice.book_id, choice.chapters) == (None, ())

    def test_an_empty_reply_is_a_failure_not_a_silent_no_match(self):
        engine = _engine(_capturing([], reply=""))

        with pytest.raises(EngineError) as caught:
            engine.exercise_match(self._material())

        assert "空回复" in str(caught.value)


class TestExerciseClassifyLeg:
    """REQ-161 W5：读数腿只判「对应哪道教材题＋题型」；空回复判故障，不折算成没有读数。"""

    @staticmethod
    def _material() -> ExerciseClassifyInput:
        return ExerciseClassifyInput(
            title="习题 1.1",
            items=(("1", "证明：任意实数的平方非负。"),),
            targets=(("1", "求证：任意实数的平方非负。"),),
            target_title="数学分析原理",
        )

    def test_both_sides_reach_the_prompt_and_the_step_is_its_own(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"items": []})))

        engine.exercise_classify(self._material())

        prompt = sent[0]["prompt"]
        assert "[1] 证明：任意实数的平方非负。" in prompt
        assert "[1] 求证：任意实数的平方非负。" in prompt
        assert "数学分析原理" in prompt
        assert sent[0]["step"] == "exercise_classify"

    def test_the_reply_keeps_item_number_target_and_type(self):
        reply = json.dumps(
            {"items": [{"item_no": "1", "target": "1", "kind": "证明题"}]}, ensure_ascii=False
        )
        engine = _engine(_capturing([], reply=reply))

        reading = engine.exercise_classify(self._material())

        assert [(item.item_no, item.target, item.kind) for item in reading.items] == [
            ("1", "1", "证明题")
        ]

    def test_an_empty_reply_is_a_failure_not_an_empty_reading(self):
        engine = _engine(_capturing([], reply=""))

        with pytest.raises(EngineError) as caught:
            engine.exercise_classify(self._material())

        assert "空回复" in str(caught.value)


class TestExerciseVerifyLeg:
    """REQ-170 第三步：校验腿判「切分对否、题面与解答是否同一题」；空回复判故障，不折算成全部合格。"""

    @staticmethod
    def _material() -> ExerciseVerifyInput:
        return ExerciseVerifyInput(
            title="第 1 章 函数",
            items=(
                ExerciseVerifyItem(
                    item_no="1",
                    problem="1. 求证：任意实数的平方非负。",
                    solution="证明：由 $x^2\\ge 0$ 得证。",
                ),
            ),
        )

    def test_both_segments_reach_the_prompt_and_the_step_is_its_own(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"items": []})))

        engine.exercise_verify(self._material())

        prompt = sent[0]["prompt"]
        assert "第 1 章 函数" in prompt
        assert "[1]" in prompt
        assert "1. 求证：任意实数的平方非负。" in prompt
        assert "证明：由" in prompt
        assert sent[0]["step"] == "exercise_verify"

    def test_the_reply_keeps_the_verdict_and_the_note(self):
        reply = json.dumps(
            {"items": [{"item_no": "1", "verdict": "suspect", "note": "解答像下一题的开头"}]},
            ensure_ascii=False,
        )
        engine = _engine(_capturing([], reply=reply))

        reading = engine.exercise_verify(self._material())

        assert [(item.item_no, item.verdict, item.note) for item in reading.items] == [
            ("1", "suspect", "解答像下一题的开头")
        ]

    def test_an_out_of_scope_verdict_is_read_as_suspect(self):
        reply = json.dumps({"items": [{"item_no": "1", "verdict": "good", "note": ""}]})
        engine = _engine(_capturing([], reply=reply))

        reading = engine.exercise_verify(self._material())

        assert reading.items[0].verdict == "suspect", "没听清的结论不许冒充合格"

    def test_an_empty_reply_is_a_failure_not_all_ok(self):
        engine = _engine(_capturing([], reply=""))

        with pytest.raises(EngineError) as caught:
            engine.exercise_verify(self._material())

        assert "空回复" in str(caught.value)

    def test_reject_is_a_first_class_verdict(self):
        """`KA-70` 三态：否认是合法结论，不因闭集漏项被钳成存疑。"""
        reply = json.dumps(
            {"items": [{"item_no": "1", "verdict": "reject", "note": "解答从第 2 题起"}]},
            ensure_ascii=False,
        )
        engine = _engine(_capturing([], reply=reply))

        reading = engine.exercise_verify(self._material())

        assert reading.items[0].verdict == "reject"

    def test_the_prompt_asks_four_judgments_and_translates_to_chinese_first(self):
        """`KA-70`：四判（切分／同一题／数学结论／补全）＋英文先译后判，结论闭集含 reject。"""
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"items": []})))

        engine.exercise_verify(self._material())

        prompt = sent[0]["prompt"]
        for needle in ("切分", "同一题", "数学结论", "补全", "译成中文", "reject"):
            assert needle in prompt

    def test_the_prompt_shows_a_bracketless_output_sample(self):
        """配对机械失败（REQ-178）：输出样例必须是裸题号，并明说不要带回外层方括号。

        旧口径「照抄输入方括号里的题号」有歧义，b07 有 472 笔把 `[N]` 原样抄回、98 笔抄成
        `（N）`，全部对不上——样例外壳去括号，禁令写在同一行。
        """
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply=json.dumps({"items": []})))

        engine.exercise_verify(self._material())

        prompt = sent[0]["prompt"]
        assert '"item_no": "2.5"' in prompt, "输出样例本身不带括号"
        assert '输入 [2.5] → 输出 "2.5"' in prompt, "给一枚带点的对照样例"
        assert "外层方括号不要带回来" in prompt, "禁令要明确到方括号"


class TestBlockMaterial:
    """`KA-64`／`KA-66`：内容批次的块材料带「p{页}·b{序}」坐标，公式与表格原文进提示词。"""

    @staticmethod
    def _block_section() -> SectionInput:
        return SectionInput(
            parent_id="afkp-s21",
            title="2.1 数列的极限",
            page_start=7,
            page_end=8,
            pages=(),
            blocks=(
                SectionBlock(page=7, block_index=2, text="设 $x_n$ 为数列。"),
                SectionBlock(
                    page=8,
                    block_index=3,
                    text=r"<table><tr><td>$\lim a_n$</td></tr></table>",
                ),
            ),
        )

    def test_each_block_carries_its_page_and_index_prefix(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent))

        engine.points(self._block_section())

        prompt = sent[0]["prompt"]
        assert "p7·b2" in prompt
        assert "p8·b3" in prompt
        assert "设 $x_n$ 为数列。" in prompt, "公式定界符原样透传"
        assert r"<table><tr><td>$\lim a_n$</td></tr></table>" in prompt, "表格 HTML 原文透传"
        assert sent[0]["meta"]["page_count"] == 2, "按块取材时页数由块坐标给出"

    def test_a_merged_reply_is_parsed_by_member_id(self):
        reply = json.dumps(
            {
                "sections": [
                    {"id": "afkp-s11", "section_summary": "甲", "points": []},
                    {
                        "id": "afkp-s12",
                        "section_summary": "乙",
                        "points": [{"name": "点一", "anchors": []}],
                    },
                ]
            },
            ensure_ascii=False,
        )
        engine = _engine(lambda request: httpx.Response(200, json={"reply": reply, "success": True}))
        section = SectionInput(
            parent_id="afkp-ch1",
            title="1.1 数列极限 等 2 节",
            page_start=2,
            page_end=5,
            pages=(),
            members=(
                SectionMember(node_id="afkp-s11", title="1.1 数列极限", blocks=()),
                SectionMember(node_id="afkp-s12", title="1.2 函数极限", blocks=()),
            ),
        )

        produced = engine.points(section)

        assert [(member.node_id, member.summary, len(member.drafts)) for member in produced.members] == [
            ("afkp-s11", "甲", 0),
            ("afkp-s12", "乙", 1),
        ]

    def test_a_merged_reply_without_sections_is_a_shape_failure(self):
        engine = _engine(_reply_object("甲", []))
        section = SectionInput(
            parent_id="afkp-ch1",
            title="合批组",
            page_start=2,
            page_end=5,
            pages=(),
            members=(SectionMember(node_id="afkp-s11", title="1.1 数列极限"),),
        )

        with pytest.raises(EngineError, match="缺可用的 sections"):
            engine.points(section)
