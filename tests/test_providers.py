"""
模块职责：验证百炼供应商响应归一化不会因尾随说明拒绝合法 JSON。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
被测代码：backend/app/providers.py
"""

import json

import pytest

from backend.app.config import Settings
from backend.app.providers import _json_object


def test_json_object_accepts_a_valid_object_followed_by_model_explanation():
    content = '{"markdown":"正文","blocks":[]}\n模型补充说明。'

    assert _json_object(content) == {"markdown": "正文", "blocks": []}


def test_json_object_accepts_json_fence_and_trailing_content():
    content = '```json\n{"nodes": []}\n```\n以上是结果。'

    assert _json_object(content) == {"nodes": []}


def test_json_object_rejects_content_without_any_json_object():
    with pytest.raises(json.JSONDecodeError):
        _json_object("无法生成结构化结果")


def test_settings_repr_never_exposes_api_key():
    settings = Settings(api_key="sensitive-value")

    assert "sensitive-value" not in repr(settings)
    assert settings.api_key_value == "sensitive-value"
