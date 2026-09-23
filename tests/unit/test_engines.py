"""MinerU 适配器单测（httpx MockTransport，不访问公网、不依赖真实容器）。

守护面：三段化提交/轮询/取结果（MinerU 3.4.4 `/tasks` 协议，见 PLAN-003 W0 冻结表）、
状态机（pending→processing→completed / failed）、产物形态开关、信封双兼容、轮询瞬时失败容忍、
超时、非 JSON 归一、图片解码、直连不经系统代理。
"""

from __future__ import annotations

import base64
import json
import re

import httpx
import pytest

from axiom_flow.engines.base import EngineError
from axiom_flow.engines.mineru import MinerUEngine

BASE = "http://127.0.0.1:5002"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=BASE)


def _engine(handler, **kwargs) -> MinerUEngine:
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("timeout", 5.0)
    return MinerUEngine(BASE, client=_client(handler), **kwargs)


def _result_body(stem: str = "book", *, content_list: object = None, md: str = "# ok", middle: object = None) -> dict:
    """按真机留档形状构造 `/tasks/{id}/result` 200 载荷（content_list/middle_json 为 JSON 字符串）。"""
    inner: dict = {"md_content": md}
    if content_list is not None:
        inner["content_list"] = content_list
    if middle is not None:
        inner["middle_json"] = middle
    return {"backend": "hybrid-engine", "version": "3.4.4", "results": {stem: inner}}


class TestHealth:
    def test_health_ok(self):
        engine = _engine(lambda request: httpx.Response(200, json={"status": "ok"}))
        assert engine.health() is True

    def test_health_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        assert _engine(handler).health() is False


class TestModelLoad:
    """负载探测：读 /health 的 queued_tasks/processing_tasks；无法判定时返回 None（不臆造阻塞）。"""

    def test_reads_queued_and_processing(self):
        engine = _engine(
            lambda request: httpx.Response(
                200, json={"status": "ok", "queued_tasks": 2, "processing_tasks": 1}
            )
        )
        load = engine.model_load()
        assert (load.queued, load.processing) == (2, 1)

    def test_idle_reports_zero(self):
        engine = _engine(lambda request: httpx.Response(200, json={"queued_tasks": 0, "processing_tasks": 0}))
        assert engine.model_load().busy is False

    def test_non_200_is_unknown(self):
        engine = _engine(lambda request: httpx.Response(503, json={"queued_tasks": 9, "processing_tasks": 9}))
        assert engine.model_load() is None

    def test_missing_fields_is_unknown(self):
        engine = _engine(lambda request: httpx.Response(200, json={"status": "ok", "processing_tasks": 1}))
        assert engine.model_load() is None

    def test_non_json_is_unknown(self):
        engine = _engine(lambda request: httpx.Response(200, text="<html>not json</html>"))
        assert engine.model_load() is None

    def test_transport_error_is_unknown(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        assert _engine(handler).model_load() is None


class TestSubmitContract:
    """提交段：POST /tasks，202 受理，只送产物形态开关。"""

    def test_submits_to_tasks_with_output_flags_only(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t1", "status": "pending"})
            if request.url.path == "/tasks/t1":
                return httpx.Response(200, json={"task_id": "t1", "status": "completed"})
            return httpx.Response(
                200,
                json=_result_body("book", content_list=json.dumps([
                    {"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}
                ])),
            )

        _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        submit = seen[0]
        assert submit.method == "POST" and submit.url.path == "/tasks"
        body = submit.read().decode("utf-8", "replace")
        for flag in ("return_md", "return_content_list", "return_middle_json"):
            assert flag in body
        # 解析调参归模型服务侧（ADR 0004）：适配器不得送调参字段
        for tuned in ("backend", "effort", "parse_method", "lang_list", "formula_enable", "table_enable"):
            assert f'name="{tuned}"' not in body

    def test_submit_http_error_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(500, text="boom")
            return httpx.Response(404)

        with pytest.raises(EngineError, match="提交失败"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_missing_task_id_raises(self):
        engine = _engine(lambda request: httpx.Response(202, json={"detail": "x"}))
        with pytest.raises(EngineError, match="task_id"):
            engine.parse_document(b"%PDF-1.4", "book.pdf")

    def test_submit_non_json_raises_engine_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, text="<html>gateway</html>")
            return httpx.Response(404)

        with pytest.raises(EngineError, match="不是 JSON"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_unreachable_raises(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(EngineError, match="不可达"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")


class TestPollingStateMachine:
    """轮询段：GET /tasks/{id} 顶层 status；瞬时失败连续 3 次才判失败。"""

    def test_polls_until_completed(self):
        states = iter(["pending", "processing", "completed"])
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t2", "status": "pending"})
            if request.url.path == "/tasks/t2":
                return httpx.Response(200, json={"task_id": "t2", "status": next(states)})
            return httpx.Response(
                200,
                json=_result_body("book", content_list=json.dumps([
                    {"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}
                ])),
            )

        _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert paths.count("/tasks/t2") == 3

    def test_transient_failure_below_threshold_keeps_polling(self):
        responses = iter([
            httpx.Response(500),
            httpx.Response(200, json={"status": "processing"}),
            httpx.Response(200, json={"status": "completed"}),
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t3"})
            if request.url.path == "/tasks/t3":
                return next(responses)
            return httpx.Response(
                200,
                json=_result_body("book", content_list=json.dumps([
                    {"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}
                ])),
            )

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.pages[0].blocks[0]["text"] == "x"

    def test_three_consecutive_transient_failures_raise(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t4"})
            if request.url.path == "/tasks/t4":
                return httpx.Response(502, text="bad gateway")
            return httpx.Response(404)

        with pytest.raises(EngineError, match="轮询失败"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_status_fail_state_raises_with_error_text(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t5"})
            if request.url.path == "/tasks/t5":
                return httpx.Response(200, json={"status": "failed", "error": "显存不足"})
            return httpx.Response(404)

        with pytest.raises(EngineError, match="显存不足"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_timeout_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t6"})
            return httpx.Response(200, json={"status": "pending"})

        with pytest.raises(EngineError, match="超时"):
            _engine(handler, timeout=0.05, poll_interval=0.02).parse_document(b"%PDF-1.4", "book.pdf")


class TestResultEnvelope:
    """结果段：202＝未就绪继续等，200＝取 results[<stem>]；字段双兼容。"""

    def test_result_202_is_not_an_error(self):
        result_calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t7"})
            if request.url.path == "/tasks/t7":
                return httpx.Response(200, json={"status": "completed"})
            if request.url.path == "/tasks/t7/result":
                result_calls["n"] += 1
                if result_calls["n"] == 1:
                    return httpx.Response(202, json={"status": "completed", "message": "Task result is not ready yet"})
                return httpx.Response(
                    200,
                    json=_result_body("book", content_list=json.dumps([
                        {"type": "text", "text": "正文", "bbox": [0, 0, 1, 1], "page_idx": 0}
                    ])),
                )
            return httpx.Response(404)

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result_calls["n"] == 2
        assert result.pages[0].blocks[0]["text"] == "正文"

    def test_groups_content_list_by_page_with_sizes(self):
        content_list = [
            {"type": "title", "text": "Ch 1", "text_level": 1, "bbox": [72, 54, 540, 80], "page_idx": 0},
            {"type": "text", "text": "正文", "bbox": [72, 96, 540, 132], "page_idx": 0},
            {"type": "interline_equation", "text": "x^2", "bbox": [72, 150, 540, 175], "page_idx": 1},
        ]
        middle = {"pdf_info": [{"page_size": [595.0, 842.0]}, {"page_size": [612.0, 792.0]}]}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t8"})
            if request.url.path == "/tasks/t8":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(
                200,
                json=_result_body(
                    "book",
                    content_list=json.dumps(content_list),
                    middle=json.dumps(middle),
                    md="# Ch 1",
                ),
            )

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert [page.page for page in result.pages] == [1, 2]
        assert len(result.pages[0].blocks) == 2
        assert result.pages[1].blocks[0]["type"] == "interline_equation"
        assert result.pages[0].page_size == (595.0, 842.0)
        assert result.pages[1].page_size == (612.0, 792.0)

    def test_accepts_parsed_objects_and_legacy_state(self):
        """旧式载体：content_list 为已解析对象、状态在 `data.state`。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t9"})
            if request.url.path == "/tasks/t9":
                return httpx.Response(200, json={"data": {"state": "done"}})
            return httpx.Response(
                200,
                json={
                    "results": {
                        "book": {
                            "content_list": [{"type": "text", "text": "直对象", "bbox": [0, 0, 1, 1], "page_idx": 0}],
                        }
                    }
                },
            )

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.pages[0].blocks[0]["text"] == "直对象"

    def test_legacy_markdown_key_falls_back(self):
        """无块结构时正文回退：接受旧式 `markdown` 键（新载体为 `md_content`）。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t9b"})
            if request.url.path == "/tasks/t9b":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(200, json={"results": {"book": {"markdown": "# legacy"}}})

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.pages[0].markdown == "# legacy"

    def test_falls_back_to_markdown_when_no_blocks(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t10"})
            if request.url.path == "/tasks/t10":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(200, json=_result_body("book", content_list=None, md="# only"))

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.pages[0].markdown == "# only"
        assert result.pages[0].blocks == []

    def test_empty_result_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t11"})
            if request.url.path == "/tasks/t11":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(200, json={"backend": "hybrid-engine", "results": {}})

        with pytest.raises(EngineError, match="无结构化产物"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_non_json_result_raises_engine_error(self):
        """A3 回归：done 响应体非 JSON 必须归一为 EngineError，不得穿透 JSONDecodeError。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t12"})
            if request.url.path == "/tasks/t12":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(200, text="<html>502</html>")

        with pytest.raises(EngineError, match="不是 JSON"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_decodes_images(self):
        payload_bytes = b"\x89PNG\r\n"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "t13"})
            if request.url.path == "/tasks/t13":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(
                200,
                json={
                    "backend": "hybrid-engine",
                    "results": {
                        "book": {
                            "md_content": "x",
                            "content_list": json.dumps([
                                {"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}
                            ]),
                            "images": {
                                "images/a.png": base64.b64encode(payload_bytes).decode(),
                                "images/bad.png": "not-base64!",
                            },
                        }
                    },
                },
            )

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.images == {"images/a.png": payload_bytes}


class TestParsePage:
    """单页解析：页图提交，结果按请求页码重写。"""

    def test_rewrites_page_number(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                assert b"p0007.png" in request.read()
                return httpx.Response(202, json={"task_id": "p1"})
            if request.url.path == "/tasks/p1":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(
                200,
                json=_result_body(
                    "p0007",
                    content_list=json.dumps([
                        {"type": "text", "text": "页内容", "bbox": [0, 0, 10, 10], "page_idx": 0}
                    ]),
                ),
            )

        page = _engine(handler).parse_page(b"png-bytes", 7)
        assert page.page == 7
        assert page.blocks[0]["text"] == "页内容"

    def test_result_grouped_by_other_key_still_read(self):
        """单页提交但 results 键与文件名不同：按唯一键回落。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "p2"})
            if request.url.path == "/tasks/p2":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(
                200,
                json=_result_body(
                    "unknown-name",
                    content_list=json.dumps([
                        {"type": "text", "text": "回退取键", "bbox": [0, 0, 1, 1], "page_idx": 0}
                    ]),
                ),
            )

        page = _engine(handler).parse_page(b"png-bytes", 9)
        assert page.page == 9
        assert page.blocks[0]["text"] == "回退取键"


class TestWindowSubmission:
    """PLAN-007 W1：窗口协议（W0 真机冻结）。

    冻结事实（`data/probes/window-req086-20260922/`，合成标记 PDF 两窗留档）：

    - `start_page_id`/`end_page_id` 为 **0 基且含两端**（`start=2,end=4` → 真机返回 1 基第 3/4/5 页，
      `middle_json.pdf_info` 长度 3）；
    - 返回的 `content_list[].page_idx` 与 `pdf_info[].page_idx` 都是**窗口内相对**（自 0 起），
      不是全书索引 ⇒ 适配器必须按窗口起点还原为全书绝对页号（现实现按全书 0 基解释，是缺陷）。
    """

    @staticmethod
    def _form_field(body: str, name: str) -> str | None:
        match = re.search(rf'name="{name}"\r\n\r\n([^\r]*)', body)
        return match.group(1) if match else None

    @staticmethod
    def _handler(seen: list[httpx.Request], *, content_list: object, middle: object, md: str = "# ok"):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                seen.append(request)
                return httpx.Response(202, json={"task_id": "win"})
            if request.url.path == "/tasks/win":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(
                200,
                json=_result_body(
                    "book",
                    content_list=json.dumps(content_list) if content_list is not None else None,
                    middle=json.dumps(middle) if middle is not None else None,
                    md=md,
                ),
            )

        return handler

    def test_window_sends_zero_based_inclusive_page_ids(self):
        seen: list[httpx.Request] = []
        handler = self._handler(
            seen,
            content_list=[{"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}],
            middle={"pdf_info": [{"page_size": [595.0, 842.0]}] * 4},
        )
        _engine(handler).parse_document(b"%PDF-1.4", "book.pdf", start_page=11, end_page=14)
        body = seen[0].read().decode("utf-8", "replace")
        assert self._form_field(body, "start_page_id") == "10"  # 1 基 11 → 0 基 10
        assert self._form_field(body, "end_page_id") == "13"  # 含两端：1 基 14 → 0 基 13

    def test_no_window_sends_no_page_ids(self):
        """不带窗口时不发送页段字段：整本仍按容器默认（0/99999），不臆造参数。"""
        seen: list[httpx.Request] = []
        handler = self._handler(
            seen,
            content_list=[{"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}],
            middle={"pdf_info": [{"page_size": [595.0, 842.0]}]},
        )
        _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        body = seen[0].read().decode("utf-8", "replace")
        assert self._form_field(body, "start_page_id") is None
        assert self._form_field(body, "end_page_id") is None

    def test_relative_page_idx_restored_to_absolute_book_pages(self):
        """窗口内相对索引 → 全书绝对页号；中间无条目的页仍须出现（真书空白页形态）。"""
        seen: list[httpx.Request] = []
        handler = self._handler(
            seen,
            content_list=[
                {"type": "text", "text": "first", "bbox": [0, 0, 1, 1], "page_idx": 0},
                {"type": "text", "text": "third", "bbox": [0, 0, 1, 1], "page_idx": 2},
            ],
            middle={"pdf_info": [{"page_size": [595.0, 842.0]}] * 3},
        )
        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf", start_page=47, end_page=49)
        assert [page.page for page in result.pages] == [47, 48, 49]
        assert [block["text"] for block in result.pages[0].blocks] == ["first"]
        assert result.pages[1].blocks == []
        assert [block["text"] for block in result.pages[2].blocks] == ["third"]
        assert result.pages[1].page_size == (595.0, 842.0)

    def test_window_without_middle_json_still_offsets_pages(self):
        """无 `middle_json` 时按条目相对索引偏移，不退回「全书索引」旧口径。"""
        seen: list[httpx.Request] = []
        handler = self._handler(
            seen,
            content_list=[
                {"type": "text", "text": "a", "bbox": [0, 0, 1, 1], "page_idx": 0},
                {"type": "text", "text": "b", "bbox": [0, 0, 1, 1], "page_idx": 1},
            ],
            middle=None,
        )
        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf", start_page=100, end_page=101)
        assert [page.page for page in result.pages] == [100, 101]

    def test_whole_book_default_starts_at_page_one(self):
        seen: list[httpx.Request] = []
        handler = self._handler(
            seen,
            content_list=[{"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}],
            middle={"pdf_info": [{"page_size": [595.0, 842.0]}] * 2},
        )
        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert [page.page for page in result.pages] == [1, 2]

    def test_blank_page_window_is_a_legal_empty_page(self):
        """W2 / REQ-087：真机空白页形状（`real-blank-only` 留档）= completed + `pdf_info` 1 页 +
        `content_list` 与 `md_content` 皆空。这必须是**合法的空页结果**，不得抛 `EngineError`
        ——否则空白页被判 `failed`，含空白页的书永不满足激活门槛。"""
        seen: list[httpx.Request] = []
        handler = self._handler(
            seen,
            content_list=[],
            middle={"pdf_info": [{"page_size": [815.0, 1151.0]}]},
            md="",
        )
        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf", start_page=48, end_page=48)
        assert [page.page for page in result.pages] == [48]
        assert result.pages[0].blocks == []
        assert result.pages[0].markdown == ""

    def test_middle_json_absent_and_truly_empty_still_raises(self):
        """反向判据：无任何页证据（无条目、无 md、无 `pdf_info`）时仍须抛错——
        「空」与「什么都没回」不能混为一谈，否则缺陷会被静默成合法空白页。"""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/tasks":
                return httpx.Response(202, json={"task_id": "win0"})
            if request.url.path == "/tasks/win0":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(200, json=_result_body("book", content_list=[], md=""))

        with pytest.raises(EngineError, match="无结构化产物"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf", start_page=1, end_page=3)


class TestBBoxSpaceDeclaration:
    """DEFECT-004：3.4.4 `content_list[].bbox` 为逐轴 0–1000 归一化，适配器必须如实声明坐标空间。"""

    @staticmethod
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/tasks":
            return httpx.Response(202, json={"task_id": "sp"})
        if request.url.path == "/tasks/sp":
            return httpx.Response(200, json={"status": "completed"})
        return httpx.Response(
            200,
            json=_result_body(
                "book",
                content_list=json.dumps([
                    {"type": "text", "text": "正文", "bbox": [16, 40, 984, 62], "page_idx": 0}
                ]),
                middle=json.dumps({"pdf_info": [{"page_size": [446.0, 631.0]}]}),
            ),
        )

    def test_document_pages_declare_normalized_1000(self):
        result = _engine(self._handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.pages[0].bbox_space == "normalized_1000"

    def test_single_page_declares_normalized_1000(self):
        page = _engine(self._handler).parse_page(b"png-bytes", 3)
        assert page.bbox_space == "normalized_1000"
        assert page.page_size == (446.0, 631.0)  # PNG 输入下是标称点，仅留档不作缩放基准


class TestDirectConnection:
    """`MI-10`：适配器客户端恒直连，不经系统代理。"""

    def test_client_trust_env_disabled(self):
        engine = MinerUEngine(BASE)
        try:
            assert engine._http().trust_env is False
        finally:
            engine.close()
