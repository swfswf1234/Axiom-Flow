"""llm_client 单元测试：VisionClient 双模式（local 直连 / qed-engine 网关）与调用记录（V2-014）。

覆盖：local 直连请求构造与重试、direct 调用记录落库与 DB 降级、gateway
请求构造（image_base64 / pdf_base64）、网关失败语义、is_gateway 与 local parse_pdf 限制。
"""

import base64
import json

import httpx
import pytest

from axiom_flow.fallback.client import VisionError, VisionResult
from axiom_flow.llm_client import VisionClient


def _dashscope_ok(content: str) -> httpx.Response:
    payload = {"choices": [{"message": {"content": content}}]}
    return httpx.Response(200, json=payload)


def _gateway_ok(reply: str, call_id: int | None = 7) -> httpx.Response:
    return httpx.Response(
        200,
        json={"reply": reply, "call_id": call_id, "success": True, "error": ""},
    )


class _RecordingConnection:
    """记录每次 execute 的参数，供断言 INSERT 内容。"""

    def __init__(self):
        self.executions = []

    def execute(self, statement, params):
        self.executions.append((str(statement), params))


class _EngineCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, *exc):
        return False


class _RecordingEngine:
    """begin() 返回可记录 execute 的上下文连接。"""

    def __init__(self):
        self.conn = _RecordingConnection()
        self.begin_called = 0

    def begin(self):
        self.begin_called += 1
        return _EngineCtx(self.conn)


class _FailingEngine:
    """begin() 即抛（模拟 DB 不可达）。"""

    def begin(self):
        raise RuntimeError("db unreachable")


class TestLocalDirect:
    """local 模式：直连 dashscope，请求构造与重试语义。"""

    def test_recognize_page_sends_base64_image_and_model(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            captured["auth"] = request.headers.get("Authorization")
            return _dashscope_ok("## Section\n\nSome text")

        client = VisionClient(
            api_key="test-key",
            model="qwen-vl-plus",
            transport=httpx.MockTransport(handler),
        )
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

        result = client.recognize_page(png, page_no=7)

        assert isinstance(result, VisionResult)
        assert result.markdown == "## Section\n\nSome text"
        assert result.page_no == 7
        body = captured["body"]
        assert body["model"] == "qwen-vl-plus"
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert base64.b64decode(content[0]["image_url"]["url"].split(",", 1)[1]) == png
        assert captured["auth"] == "Bearer test-key"

    def test_retries_on_429_then_succeeds(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) < 3:
                return httpx.Response(429, json={"error": "rate limited"})
            return _dashscope_ok("recovered")

        client = VisionClient(
            api_key="k",
            transport=httpx.MockTransport(handler),
            max_attempts=3,
            retry_delay=0,
        )

        result = client.recognize_page(b"png", page_no=1)

        assert len(calls) == 3
        assert result.markdown == "recovered"

    def test_4xx_raises_immediately(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"error": "bad request"})

        client = VisionClient(
            api_key="k",
            transport=httpx.MockTransport(handler),
            retry_delay=0,
        )

        with pytest.raises(VisionError):
            client.recognize_page(b"png", page_no=1)
        assert len(calls) == 1

    def test_local_parse_pdf_raises(self):
        client = VisionClient(api_key="k")

        with pytest.raises(VisionError, match="qed-engine"):
            client.parse_pdf(b"%PDF-1.4 fake")


class TestDirectCallRecord:
    """direct 调用记录：固定标识、engine=None 跳过、DB 不可达降级。"""

    def test_success_records_insert_with_fixed_identity(self):
        engine = _RecordingEngine()

        def handler(request: httpx.Request) -> httpx.Response:
            return _dashscope_ok("## Section")

        client = VisionClient(
            api_key="k",
            model="qwen-vl-plus",
            transport=httpx.MockTransport(handler),
            engine=engine,
        )

        client.recognize_page(b"png", page_no=1)

        assert engine.begin_called == 1
        assert len(engine.conn.executions) == 1
        stmt, params = engine.conn.executions[0]
        assert "INSERT INTO qed_llm_calls" in stmt
        assert params["service"] == "axiom_flow"
        assert params["mode"] == "api"
        assert params["provider"] == "qwen"
        assert params["model"] == "qwen-vl-plus"
        assert params["endpoint"] == "vision"
        assert params["prompt_template"] == ""
        assert params["status"] == "success"
        assert params["error"] == ""
        assert params["response"] == "## Section"
        assert params["prompt"]
        assert params["duration_ms"] >= 0
        assert params["created_at"] is not None

    def test_engine_none_skips_recording(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _dashscope_ok("## Section")

        client = VisionClient(api_key="k", transport=httpx.MockTransport(handler), engine=None)

        result = client.recognize_page(b"png", page_no=1)

        assert result.markdown == "## Section"

    def test_db_failure_degrades_and_call_still_succeeds(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _dashscope_ok("## Section")

        client = VisionClient(
            api_key="k",
            transport=httpx.MockTransport(handler),
            engine=_FailingEngine(),
        )

        result = client.recognize_page(b"png", page_no=1)

        assert result.markdown == "## Section"


class TestGateway:
    """qed-engine 模式：经 8900 /llm/vision 网关，不接触密钥。"""

    def test_parse_pdf_sends_pdf_base64_and_filename(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return _gateway_ok("# Full doc\n\nmarkdown")

        client = VisionClient(api_select="qed-engine", transport=httpx.MockTransport(handler))

        reply = client.parse_pdf(b"%PDF-1.4 fake", pdf_filename="book.pdf")

        assert reply == "# Full doc\n\nmarkdown"
        assert captured["url"] == "http://127.0.0.1:8900/api/v1/llm/vision"
        body = captured["body"]
        assert body["pdf_base64"] == base64.b64encode(b"%PDF-1.4 fake").decode("ascii")
        assert body["pdf_filename"] == "book.pdf"
        assert client.last_call_id == 7

    def test_recognize_page_sends_bare_image_base64(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _gateway_ok("## Page markdown", call_id=9)

        client = VisionClient(api_select="qed-engine", transport=httpx.MockTransport(handler))

        result = client.recognize_page(b"png", page_no=3)

        assert isinstance(result, VisionResult)
        assert result.markdown == "## Page markdown"
        assert result.page_no == 3
        assert result.contract_version == "gateway-vision-v1"
        body = captured["body"]
        assert body["image_base64"] == base64.b64encode(b"png").decode("ascii")
        assert not body["image_base64"].startswith("data:")
        assert client.last_call_id == 9

    def test_success_false_raises_with_gateway_reason(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"reply": "", "call_id": None, "success": False, "error": "MinerU down"},
            )

        client = VisionClient(api_select="qed-engine", transport=httpx.MockTransport(handler))

        with pytest.raises(VisionError, match="MinerU down"):
            client.parse_pdf(b"pdf")

    def test_non_string_reply_raises_format_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"reply": {"nested": 1}, "call_id": 1, "success": True, "error": ""},
            )

        client = VisionClient(api_select="qed-engine", transport=httpx.MockTransport(handler))

        with pytest.raises(VisionError):
            client.parse_pdf(b"pdf")

    def test_is_gateway_reflects_api_select(self):
        assert VisionClient(api_key="k").is_gateway is False
        assert VisionClient(api_select="qed-engine").is_gateway is True
