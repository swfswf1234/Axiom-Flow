"""
模块职责：验证百炼供应商响应归一化不会因尾随说明拒绝合法 JSON。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
被测代码：src/axiom_flow/infrastructure/bailian.py
"""

import asyncio
import json

import pytest

from axiom_flow.infrastructure.bailian import BailianProvider, InvalidModelPage, _json_object, _repair_json_object
from axiom_flow.infrastructure.config import Settings


def test_json_object_accepts_a_valid_object_followed_by_model_explanation():
    content = '{"markdown":"正文","blocks":[]}\n模型补充说明。'

    assert _json_object(content) == {"markdown": "正文", "blocks": []}


def test_json_object_accepts_json_fence_and_trailing_content():
    content = '```json\n{"nodes": []}\n```\n以上是结果。'

    assert _json_object(content) == {"nodes": []}


def test_json_object_rejects_content_without_any_json_object():
    with pytest.raises(json.JSONDecodeError):
        _json_object("无法生成结构化结果")


def test_json_object_rejects_nested_block_from_a_truncated_page():
    content = '模型输出中断 {"kind":"paragraph","content":"局部块"}'

    with pytest.raises(json.JSONDecodeError, match="顶层 JSON"):
        _json_object(content)


def test_json_repair_only_repairs_a_root_object():
    content = '{"markdown":"第一行\n$\\bar{x}=\\begin{aligned}x\\end{aligned}$","blocks":[],"page_kind":"blank"}'

    repaired, changed = _repair_json_object(content)

    assert changed is True
    assert repaired["markdown"] == "第一行\n$\\bar{x}=\\begin{aligned}x\\end{aligned}$"


def test_json_repair_does_not_search_for_a_nested_object():
    with pytest.raises(json.JSONDecodeError, match="顶层 JSON"):
        _repair_json_object('prefix {"markdown":"局部"}')


class SequenceProvider(BailianProvider):
    """返回冻结响应序列，验证页级完整性重试与元数据保留。"""

    def __init__(self, settings: Settings, responses: list[tuple[str, dict]]):
        super().__init__(settings)
        self.responses = responses

    async def _complete(self, model: str, messages: list[dict]) -> tuple[str, dict]:
        self.calls += 1
        return self.responses.pop(0)


def test_ocr_markdown_contract_is_normalized_without_inventing_bbox():
    provider = SequenceProvider(
        Settings(vision_retry_backoff_seconds=0),
        [(json.dumps({"markdown": "1.28 定理\n\n$x^2=1$\n\n证明正文"}, ensure_ascii=False), {
            "model": "qwen-vl-ocr", "finish_reason": "stop", "usage": {},
        })],
    )

    result = asyncio.run(provider.parse_page(b"image", "", 20))

    assert result["page_kind"] == "content"
    assert [block["kind"] for block in result["blocks"]] == ["heading", "formula", "paragraph"]
    assert all("bbox_1000" not in block for block in result["blocks"])
    assert result["_provider"]["schema_normalization"] == "markdown_only"
    assert result["_provider"]["contract_version"] == "qwen-ocr-markdown-v2"
    assert result["_raw_response"].startswith("{")


def test_ocr_page_retries_truncation_and_records_completion_metadata():
    valid = {
        "markdown": "正文 $x^2$", "page_kind": "content",
        "blocks": [{"kind": "paragraph", "content": "正文 $x^2$"}],
    }
    provider = SequenceProvider(
        Settings(vision_retry_backoff_seconds=0),
        [
            ('{"markdown":"截断', {"model": "qwen-vl-ocr", "finish_reason": "length", "usage": {}}),
            (json.dumps(valid, ensure_ascii=False), {
                "model": "qwen-vl-ocr", "finish_reason": "stop", "usage": {"total_tokens": 321},
            }),
        ],
    )

    result = asyncio.run(provider.parse_page(b"image", "", 20))

    assert provider.calls == 2
    assert result["markdown"] == "正文 $x^2$"
    assert result["_provider"]["finish_reason"] == "stop"
    assert result["_provider"]["usage"]["total_tokens"] == 321


def test_ocr_page_rejects_invalid_top_level_schema_after_three_attempts():
    response = ('{"kind":"paragraph","content":"局部块"}', {
        "model": "qwen-vl-ocr", "finish_reason": "stop", "usage": {},
    })
    provider = SequenceProvider(Settings(vision_retry_backoff_seconds=0), [response, response, response])

    with pytest.raises(InvalidModelPage, match="page_kind") as captured:
        asyncio.run(provider.parse_page(b"image", "", 20))

    assert provider.calls == 3
    assert captured.value.response_content == response[0]
    assert captured.value.metadata["finish_reason"] == "stop"


def test_settings_repr_never_exposes_api_key():
    settings = Settings(api_key="sensitive-value")

    assert "sensitive-value" not in repr(settings)
    assert settings.api_key_value == "sensitive-value"
