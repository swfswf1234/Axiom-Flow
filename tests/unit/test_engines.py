"""MinerU 适配器单测（httpx MockTransport，不访问公网、不依赖真实容器）。

守护面：提交/轮询契约、状态机（pending→done / fail）、超时、结构化输出分组、图片解码。
"""

from __future__ import annotations

import base64
import json

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


class TestHealth:
    def test_health_ok(self):
        engine = _engine(lambda request: httpx.Response(200, json={"status": "ok"}))
        assert engine.health() is True

    def test_health_unreachable(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        assert _engine(handler).health() is False


class TestParseDocument:
    """全本解析：提交 → 轮询 → content_list 按页分组 + 页尺寸 + 图片。"""

    def test_groups_content_list_by_page(self):
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path)
            if request.url.path == "/file_parse":
                assert request.method == "POST"
                return httpx.Response(200, json={"task_id": "t1"})
            if request.url.path == "/get_task_results/t1":
                payload = {
                    "data": {
                        "state": "done",
                        "content_list": [
                            {"type": "title", "text": "Ch 1", "text_level": 1, "bbox": [72, 54, 540, 80], "page_idx": 0},
                            {"type": "text", "text": "正文", "bbox": [72, 96, 540, 132], "page_idx": 0},
                            {"type": "interline_equation", "text": "x^2", "bbox": [72, 150, 540, 175], "page_idx": 1},
                        ],
                        "middle_json": json.dumps({"pdf_info": [{"page_size": [595.0, 842.0]}, {"page_size": [595.0, 842.0]}]}),
                    }
                }
                return httpx.Response(200, json=payload)
            return httpx.Response(404)

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

        assert calls == ["/file_parse", "/get_task_results/t1"]
        assert [page.page for page in result.pages] == [1, 2]
        assert len(result.pages[0].blocks) == 2
        assert result.pages[1].blocks[0]["type"] == "interline_equation"
        assert result.pages[0].page_size == (595.0, 842.0)

    def test_polls_until_done(self):
        states = iter(["pending", "running", "done"])

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/file_parse":
                return httpx.Response(200, json={"task_id": "t2"})
            return httpx.Response(200, json={"data": {"state": next(states), "markdown": "# ok"}})

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.pages[0].markdown == "# ok"

    def test_decodes_images(self):
        payload_bytes = b"\x89PNG\r\n"
        encoded = base64.b64encode(payload_bytes).decode()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/file_parse":
                return httpx.Response(200, json={"task_id": "t3"})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "content_list": [{"type": "text", "text": "x", "bbox": [0, 0, 1, 1], "page_idx": 0}],
                        "images": {"images/a.png": encoded, "images/bad.png": "not-base64!"},
                    }
                },
            )

        result = _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")
        assert result.images == {"images/a.png": payload_bytes}

    def test_failure_state_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/file_parse":
                return httpx.Response(200, json={"task_id": "t4"})
            return httpx.Response(200, json={"data": {"state": "fail", "err_msg": "显存不足"}})

        with pytest.raises(EngineError, match="显存不足"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_timeout_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/file_parse":
                return httpx.Response(200, json={"task_id": "t5"})
            return httpx.Response(200, json={"data": {"state": "pending"}})

        with pytest.raises(EngineError, match="超时"):
            _engine(handler, timeout=0.05, poll_interval=0.02).parse_document(b"%PDF-1.4", "book.pdf")

    def test_submit_http_error_raises(self):
        engine = _engine(lambda request: httpx.Response(500))
        with pytest.raises(EngineError, match="提交失败"):
            engine.parse_document(b"%PDF-1.4", "book.pdf")

    def test_missing_task_id_raises(self):
        engine = _engine(lambda request: httpx.Response(200, json={"detail": "x"}))
        with pytest.raises(EngineError, match="task_id"):
            engine.parse_document(b"%PDF-1.4", "book.pdf")

    def test_unreachable_raises(self):
        def handler(request):
            raise httpx.ConnectError("refused")

        with pytest.raises(EngineError, match="不可达"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")

    def test_empty_result_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/file_parse":
                return httpx.Response(200, json={"task_id": "t6"})
            return httpx.Response(200, json={"data": {"state": "done", "content_list": [], "markdown": ""}})

        with pytest.raises(EngineError, match="无结构化产物"):
            _engine(handler).parse_document(b"%PDF-1.4", "book.pdf")


class TestParsePage:
    """单页解析：页图提交，页码重写为请求页。"""

    def test_rewrites_page_number(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/file_parse":
                body = request.read()
                assert b"p0007.png" in body
                return httpx.Response(200, json={"task_id": "p1"})
            return httpx.Response(
                200,
                json={
                    "data": {
                        "state": "done",
                        "content_list": [{"type": "text", "text": "页内容", "bbox": [0, 0, 10, 10], "page_idx": 0}],
                    }
                },
            )

        page = _engine(handler).parse_page(b"png-bytes", 7)
        assert page.page == 7
        assert page.blocks[0]["text"] == "页内容"
