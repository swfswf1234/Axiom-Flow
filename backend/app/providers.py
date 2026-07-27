"""
模块职责：封装百炼视觉与文本模型，并隔离供应商响应格式。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py
"""

import base64
import json
import re
from typing import Any, Protocol

import httpx

from backend.app.config import Settings


class VisionProvider(Protocol):
    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]: ...


class KnowledgeProvider(Protocol):
    async def extract_knowledge(self, markdown: str) -> dict[str, Any]: ...


def _json_object(content: str) -> dict[str, Any]:
    """兼容代码围栏和尾随说明，但只将首个完整 JSON 对象交给领域层。"""
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", candidate):
            try:
                value, _ = decoder.raw_decode(candidate, match.start())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise original_error
    if not isinstance(value, dict):
        raise ValueError("模型响应不是 JSON 对象")
    return value


class BailianProvider:
    """通过 DashScope OpenAI 兼容接口调用视觉与文本模型。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls = 0

    async def _complete(self, model: str, messages: list[dict[str, Any]]) -> str:
        if not self.settings.api_key:
            raise RuntimeError("未配置 AXIOM_API_KEY，无法调用百炼模型")
        if self.calls >= self.settings.model_call_budget:
            raise RuntimeError("已达到 AXIOM_MODEL_CALL_BUDGET 调用上限")
        self.calls += 1
        async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
            response = await client.post(
                f"{self.settings.dashscope_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json={"model": model, "messages": messages, "temperature": 0},
            )
            response.raise_for_status()
            body = response.json()
        return body["choices"][0]["message"]["content"]

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]:
        """主链默认先尝试主视觉模型，再在失败时使用已配置的回退模型。"""
        try:
            return await self.parse_page_primary(image_bytes, raw_text, page_no)
        except Exception:
            return await self.parse_page_fallback(image_bytes, raw_text, page_no)

    async def parse_page_primary(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]:
        """只调用主视觉模型，供不允许静默回退的评测预检使用。"""
        return await self._parse_page_with_model(self.settings.vision_model, image_bytes)

    async def parse_page_fallback(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]:
        """只调用回退视觉模型，供预检明确记录备用链路结果。"""
        if self.settings.vision_fallback_model == self.settings.vision_model:
            raise RuntimeError("未配置不同于主模型的视觉回退模型")
        return await self._parse_page_with_model(self.settings.vision_fallback_model, image_bytes)

    async def _parse_page_with_model(self, model: str, image_bytes: bytes) -> dict[str, Any]:
        prompt = """你是学术论文页面解析器。请根据页面图像输出严格 JSON，不要输出 Markdown 围栏。
JSON 格式：{"markdown":"保留公式的 Markdown 正文","blocks":[{"kind":"heading|paragraph|formula|table|figure|reference","content":"内容","quote":"可在原文中定位的短引文","confidence":0.0}],"page_summary":"中文摘要"}。
不得编造页面不存在的公式、表格或文字；不确定时在 blocks 中降低 confidence。"""
        image_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_url}}]}]
        return _json_object(await self._complete(model, messages))

    async def extract_knowledge(self, markdown: str) -> dict[str, Any]:
        prompt = """你是计算机论文知识抽取器。基于给定 Markdown 输出严格 JSON，不要输出 Markdown 围栏。
格式：{"nodes":[{"kind":"section|concept|algorithm|formula|result","title":"名称","content":"中文解释","evidence_quote":"原文短引文"}],"edges":[{"source_title":"来源节点名称","target_title":"目标节点名称","relation":"CONTAINS|DEFINES|USES|ILLUSTRATES|RELATED_TO","evidence_quote":"原文短引文"}]}。
只抽取原文有证据的内容，不要从常识补充。\n\n""" + markdown
        return _json_object(await self._complete(self.settings.knowledge_model, [{"role": "user", "content": prompt}]))
