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

import httpx
import pytest

from axiom_flow.engines.base import (
    ChapterInput,
    EngineError,
    PageBody,
    PageSample,
    ReferencePage,
    SectionInput,
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
                "3|12|层级越界",
                "1|12|只有两段",
                "1|12|",
                "名字 12",
            ]
        )

        entries = _engine(_capturing(sent, reply=reply)).toc_entries(5, "该页原文")

        assert [e.name for e in entries] == ["好的行", "只有两段"]

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

    def test_the_note_reaches_all_three_toc_legs(self):
        """备注跟批次走（AF-T5 甲）：目录三条腿都读到人工上一轮的意见，也都不写进归因 meta。"""
        note = "页眉粘进目录了，剔掉"
        legs = (
            ("toc_pages", _pick([2]), lambda engine: engine.toc_pages(_samples())),
            ("toc_entries", "1|12|第 1 章 极限", lambda engine: engine.toc_entries(3, "该页原文")),
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

        assert [body["step"] for body in sent] == ["toc_pages", "toc_entries", "toc_verify"]
        assert all(note in body["prompt"] for body in sent)
        assert all("review_note" not in body["meta"] for body in sent)

    def test_an_unbound_adapter_refuses_every_toc_leg(self):
        """未绑定批次身份就拒跑（R8 甲）：三条目录腿一条都不放过，否则这条调用查不到主人。"""
        engine = KpGatewayEngine(GW, client=_client(_reply([])), timeout=5.0)
        item = TocVerifyItem(name="1 函数的极限", printed_page=None, candidates=())
        with pytest.raises(EngineError, match="toc_pages"):
            engine.toc_pages(_samples())
        with pytest.raises(EngineError, match="toc_entries"):
            engine.toc_entries(3, "该页原文")
        with pytest.raises(EngineError, match="toc_verify"):
            engine.toc_verify((item,))


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
        intro_pages=(PageBody(page_no=9, markdown="章首正文甲。"),),
        title_pages=(),
        point_summaries=(("2.1 数列的极限", "这一节把收敛写成 ε-N 语言。"),),
    )


class TestChapterLegOutbound:
    """`KA-45` 章腿的出站形状：一次调用、`step='chapter'`、章首正文与节级总结清单都在 prompt 里。"""

    def test_the_call_carries_the_intro_pages_and_the_summary_list(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}'))

        reply = engine.chapter(_material())

        body = sent[0]
        assert body["step"] == "chapter"
        assert body["meta"]["point_count"] == 1
        assert body["meta"]["intro_pages"] == [9]
        assert "章首正文甲。" in body["prompt"]
        assert "- 2.1 数列的极限：这一节把收敛写成 ε-N 语言。" in body["prompt"]
        assert (reply.summary, reply.narrative) == ("章总结", "章导读")

    def test_a_chapter_without_intro_pages_falls_back_to_the_title_page(self):
        sent: list[dict] = []
        engine = _engine(_capturing(sent, reply='{"chapter_summary": "章总结", "chapter_narrative": "章导读"}'))
        material = ChapterInput(
            parent_id="afkp-outline-ch1",
            title="第 2 章 数列",
            page_start=9,
            page_end=28,
            intro_pages=(),
            title_pages=(PageBody(page_no=9, markdown="第 2 章 数列 章标题页正文。"),),
            point_summaries=(),
        )

        engine.chapter(material)

        prompt = sent[0]["prompt"]
        assert "章标题页正文。" in prompt
        assert "其下各知识点都没有总结" in prompt


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
