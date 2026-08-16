"""fallback 单元测试：qwen-vl-plus 客户端与 Markdown→blocks 归一化（设计文档 §组件职责）。

覆盖：客户端（成功/截断/限流/超时/重试上限/编码/凭据）与归一化
（heading/paragraph/formula、LaTeX 反斜杠保护、bbox 占位）。
"""

import base64
import json

import httpx
import pytest

from axiom_flow.fallback.client import QwenVLPlusClient, VisionError, VisionResult
from axiom_flow.fallback.normalize import markdown_to_blocks
from axiom_flow.schemas import BlockType, Source

# ---------- normalize ----------


class TestMarkdownToBlocks:
    """Markdown 确定性转换：heading/paragraph/formula、反斜杠保护、bbox 占位。"""

    def test_headings_paragraphs_and_display_formula(self):
        md = "# 1. The Real and Complex Number Systems\n\nWe assume that the reader is familiar with\n\n$$x + y = y + x$$\n\nThis is a theorem."
        page = markdown_to_blocks(md, page=1)

        assert page.page == 1
        assert page.source == Source.QWEN_VL_PLUS
        assert [b.type for b in page.blocks] == [
            BlockType.HEADING,
            BlockType.PARAGRAPH,
            BlockType.FORMULA,
            BlockType.PARAGRAPH,
        ]
        assert page.blocks[0].text == "1. The Real and Complex Number Systems"
        assert page.blocks[0].level == 1
        assert page.blocks[2].latex == "x + y = y + x"
        assert page.blocks[2].display is True

    def test_heading_levels(self):
        md = "### Level three\n\n###### Level six"
        page = markdown_to_blocks(md, page=1)

        assert [b.level for b in page.blocks] == [3, 6]

    def test_inline_latex_stays_in_paragraph(self):
        md = "For $x \\in \\mathbb{R}$, the norm is finite."
        page = markdown_to_blocks(md, page=1)

        assert len(page.blocks) == 1
        assert page.blocks[0].type == BlockType.PARAGRAPH
        assert "$x \\in \\mathbb{R}$" in page.blocks[0].text

    def test_backslash_latex_is_preserved(self):
        """LaTeX 单反斜杠（\\bar、\\begin）在序列化后不被解释或丢失（v1 qwen-ocr-markdown-v2 教训）。"""
        md = "$$\\bar{x} = \\begin{aligned} a \\\\ b \\end{aligned}$$"
        page = markdown_to_blocks(md, page=1)

        raw = page.model_dump_json()
        assert "\\bar{x}" in raw
        assert "\\begin{aligned}" in raw
        assert page.blocks[0].latex == "\\bar{x} = \\begin{aligned} a \\\\ b \\end{aligned}"

    def test_bbox_placeholder_stacks_vertically(self):
        md = "First\n\nSecond\n\nThird"
        page = markdown_to_blocks(md, page=1, page_width=100, page_height=300)

        assert [b.bbox for b in page.blocks] == [
            (0, 0, 100, 100),
            (0, 100, 100, 200),
            (0, 200, 100, 300),
        ]

    def test_empty_markdown_yields_no_blocks(self):
        page = markdown_to_blocks("", page=3)

        assert page.page == 3
        assert page.blocks == []

    def test_fenced_formula_block(self):
        md = "```math\n\\int_0^1 x^2 dx\n```"
        page = markdown_to_blocks(md, page=1)

        assert len(page.blocks) == 1
        assert page.blocks[0].type == BlockType.FORMULA
        assert page.blocks[0].latex == "\\int_0^1 x^2 dx"

    def test_list_lines_merge_into_paragraph(self):
        md = "- item one\n- item two"
        page = markdown_to_blocks(md, page=1)

        assert len(page.blocks) == 1
        assert page.blocks[0].type == BlockType.PARAGRAPH
        assert page.blocks[0].text == "- item one\n- item two"


# ---------- client ----------


def _ok_response(content: str) -> httpx.Response:
    payload = {"choices": [{"message": {"content": content}}]}
    return httpx.Response(200, json=payload)


def _make_client(handler) -> QwenVLPlusClient:
    transport = httpx.MockTransport(handler)
    return QwenVLPlusClient(api_key="test-key", transport=transport, retry_delay=0)


class TestQwenVLPlusClient:
    """客户端：请求构造、编码、重试语义与错误处理。"""

    def test_recognize_page_sends_base64_image_and_prompt(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return _ok_response("## Section\n\nSome text")

        client = _make_client(handler)
        png = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes"

        result = client.recognize_page(png, page_no=7)

        body = captured["body"]
        assert body["model"] == "qwen-vl-plus"
        content = body["messages"][0]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert base64.b64decode(content[0]["image_url"]["url"].split(",", 1)[1]) == png
        assert "Markdown" in content[1]["text"]
        assert result.markdown == "## Section\n\nSome text"
        assert result.page_no == 7
        assert result.contract_version == QwenVLPlusClient.CONTRACT_VERSION

    def test_retries_on_429_and_5xx_up_to_max_attempts(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) < 3:
                return httpx.Response(429, json={"error": "rate limited"})
            return _ok_response("recovered")

        client = _make_client(handler)

        result = client.recognize_page(b"png", page_no=1)

        assert len(calls) == 3
        assert result.markdown == "recovered"

    def test_gives_up_after_max_attempts(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(500, json={"error": "boom"})

        client = QwenVLPlusClient(api_key="k", transport=httpx.MockTransport(handler), max_attempts=3, retry_delay=0)

        with pytest.raises(VisionError):
            client.recognize_page(b"png", page_no=1)
        assert len(calls) == 3

    def test_retries_on_timeout(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ReadTimeout("slow")
            return _ok_response("fast enough")

        client = _make_client(handler)

        result = client.recognize_page(b"png", page_no=1)

        assert len(calls) == 2
        assert result.markdown == "fast enough"

    def test_non_retryable_4xx_raises_immediately(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            return httpx.Response(400, json={"error": "bad request"})

        client = _make_client(handler)

        with pytest.raises(VisionError):
            client.recognize_page(b"png", page_no=1)
        assert len(calls) == 1

    def test_empty_content_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})

        client = _make_client(handler)

        with pytest.raises(VisionError):
            client.recognize_page(b"png", page_no=1)

    def test_api_key_from_environment(self, monkeypatch):
        monkeypatch.setenv("AXIOM_API_KEY", "env-key")

        client = QwenVLPlusClient(transport=httpx.MockTransport(lambda r: _ok_response("ok")))

        assert client.api_key == "env-key"

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("AXIOM_API_KEY", raising=False)

        with pytest.raises(VisionError, match="AXIOM_API_KEY"):
            QwenVLPlusClient(api_key=None)

    def test_vision_result_carries_contract_version(self):
        assert VisionResult("md", "qwen-vl-markdown-v1", 1).contract_version == "qwen-vl-markdown-v1"
