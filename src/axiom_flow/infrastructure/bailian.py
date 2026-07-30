"""
模块职责：封装百炼视觉与文本模型，并隔离供应商响应格式。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/system/test_document_release_flow.py、tests/unit/test_providers.py
"""

import asyncio
import base64
import hashlib
import json
import re
from typing import Any

import httpx
from json_repair import repair_json

from axiom_flow.domain.models import RetryableJobError
from axiom_flow.infrastructure.config import Settings


class ModelBudgetExceeded(RuntimeError):
    """任务级外部模型调用预算已经耗尽。"""


class InvalidModelPage(ValueError):
    """视觉模型没有返回可持久化的完整页面。"""

    def __init__(
        self, message: str, response_content: str = "", metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.response_content = response_content
        self.metadata = metadata or {}


def _json_object(content: str) -> dict[str, Any]:
    """只解析响应起始处的完整顶层对象，拒绝从截断内容中捞取嵌套块。"""
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) < 3:
            raise json.JSONDecodeError("JSON 代码围栏不完整", candidate, 0)
        closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "```"), None)
        if closing is None:
            raise json.JSONDecodeError("JSON 代码围栏不完整", candidate, 0)
        candidate = "\n".join(lines[1:closing]).strip()
    if not candidate.startswith("{"):
        raise json.JSONDecodeError("响应必须以顶层 JSON 对象开始", candidate, 0)
    value, _ = json.JSONDecoder().raw_decode(candidate)
    if not isinstance(value, dict):
        raise ValueError("模型响应不是 JSON 对象")
    return value


def _repair_json_object(content: str) -> tuple[dict[str, Any], bool]:
    """仅修复已完整结束响应的 JSON 语法，调用方仍须执行页面 schema 校验。"""
    try:
        return _json_object(content), False
    except json.JSONDecodeError:
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            closing = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "```"), None)
            if closing is None:
                raise
            candidate = "\n".join(lines[1:closing]).strip()
        if not candidate.startswith("{"):
            raise
        repaired = repair_json(_protect_json_string_backslashes(candidate), return_objects=True)
        if not isinstance(repaired, dict):
            raise InvalidModelPage("修复后的模型响应不是顶层 JSON 对象") from None
        return repaired, True


def _protect_json_string_backslashes(candidate: str) -> str:
    """保护非标准 JSON 字符串中的 LaTeX 反斜杠，避免被解释为控制字符。"""
    protected: list[str] = []
    in_string = False
    index = 0
    while index < len(candidate):
        character = candidate[index]
        if character == '"':
            preceding = 0
            cursor = index - 1
            while cursor >= 0 and candidate[cursor] == "\\":
                preceding += 1
                cursor -= 1
            if preceding % 2 == 0:
                in_string = not in_string
            protected.append(character)
            index += 1
            continue
        if in_string and character == "\\" and index + 1 < len(candidate):
            following = candidate[index + 1]
            if following in {'"', "\\", "/"}:
                protected.extend((character, following))
                index += 2
                continue
            if following == "u" and re.match(r"^[0-9a-fA-F]{4}", candidate[index + 2:index + 6]):
                protected.append(character)
                index += 1
                continue
            protected.extend(("\\", "\\"))
            index += 1
            continue
        protected.append(character)
        index += 1
    return "".join(protected)


class BailianProvider:
    """通过 DashScope OpenAI 兼容接口调用视觉与文本模型。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls = 0

    async def _complete(self, model: str, messages: list[dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        if not self.settings.api_key_value:
            raise RuntimeError("未配置 AXIOM_API_KEY，无法调用百炼模型")
        if self.calls >= self.settings.model_call_budget:
            raise ModelBudgetExceeded("已达到 AXIOM_MODEL_CALL_BUDGET 调用上限")
        self.calls += 1
        try:
            async with httpx.AsyncClient(timeout=self.settings.model_timeout_seconds) as client:
                response = await client.post(
                    f"{self.settings.dashscope_base_url.rstrip('/')}/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.api_key_value}"},
                    json={
                        "model": model, "messages": messages, "temperature": 0,
                        "max_tokens": self.settings.vision_max_tokens,
                    },
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableJobError("百炼网络请求暂时失败") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 or exc.response.status_code >= 500:
                raise RetryableJobError(f"百炼暂时返回 HTTP {exc.response.status_code}") from exc
            raise
        choice = body["choices"][0]
        metadata = {
            "model": str(body.get("model") or model),
            "finish_reason": choice.get("finish_reason"),
            "usage": body.get("usage") if isinstance(body.get("usage"), dict) else {},
        }
        return choice["message"]["content"], metadata

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]:
        """只调用 OCR 模型；可恢复错误在页内重试，不静默切换供应商或模型。"""
        attempts = max(1, self.settings.vision_page_attempts)
        for attempt in range(attempts):
            try:
                return await self._parse_page_with_model(self.settings.vision_model, image_bytes)
            except ModelBudgetExceeded:
                raise
            except Exception as exc:
                if attempt + 1 >= attempts or not self._retryable_page_error(exc):
                    raise
                await asyncio.sleep(self.settings.vision_retry_backoff_seconds * (2 ** attempt))
        raise AssertionError("页级重试循环未返回")

    async def parse_page_primary(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]:
        """兼容评测入口，行为与生产入口相同且只调用主 OCR 模型。"""
        return await self.parse_page(image_bytes, raw_text, page_no)

    async def _parse_page_with_model(self, model: str, image_bytes: bytes) -> dict[str, Any]:
        prompt = """你是扫描数学教材页面解析器。请根据页面图像输出严格 JSON，不要输出 Markdown 围栏。
JSON 格式：{"markdown":"按阅读顺序保留公式的 Markdown 正文","blocks":[{"kind":"heading|paragraph|formula|table|figure|reference|list","content":"内容","latex":"公式块可选 LaTeX","quote":"可在本页定位的短引文","bbox_1000":[x0,y0,x1,y1],"confidence":0.0}],"page_summary":"中文摘要","page_kind":"content|reference|blank"}。
bbox_1000 使用左上角为原点、页面宽高均归一化到 1000 的坐标。不得编造页面不存在的公式、
表格或文字；不确定时降低 confidence。只有页面确实没有可见内容时才返回 page_kind=blank。"""
        image_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
        messages = [{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_url}}]}]
        try:
            content, metadata = await self._complete(model, messages)
            if metadata["finish_reason"] != "stop":
                raise InvalidModelPage(f"模型响应未完整结束：{metadata['finish_reason']}")
            page, repaired = _repair_json_object(content)
            page, normalization = self._normalize_page_schema(page)
            self._validate_page(page)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, InvalidModelPage):
                if exc.response_content:
                    raise
                raise InvalidModelPage(str(exc), content, metadata) from exc
            raise InvalidModelPage(str(exc), content, metadata) from exc
        metadata["json_repaired"] = repaired
        metadata["schema_normalization"] = normalization
        metadata["contract_version"] = self.settings.vision_contract_version
        metadata["raw_response_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        page["_provider"] = metadata
        page["_raw_response"] = content
        return page

    @staticmethod
    def _normalize_page_schema(page: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """把 OCR 原生 Markdown 响应确定性转换为统一页结构。"""
        if "blocks" in page or "page_kind" in page:
            return page, "native_page"
        markdown = page.get("markdown")
        if not isinstance(markdown, str) or not markdown.strip():
            return page, "invalid"
        blocks = []
        for segment in re.split(r"\n\s*\n", markdown.strip()):
            content = segment.strip()
            if not content:
                continue
            first_line = content.splitlines()[0].strip()
            if len(content) <= 100 and re.match(r"^\d+(?:\.\d+)+\s+\S+", first_line):
                kind = "heading"
            elif (content.startswith("$$") and content.endswith("$$")) or (
                content.startswith("$") and content.endswith("$") and "\n" not in content
            ):
                kind = "formula"
            else:
                kind = "paragraph"
            block = {
                "kind": kind, "content": content, "quote": content[:240], "confidence": 0.7,
            }
            if kind == "formula":
                block["latex"] = content.strip("$").strip()
            blocks.append(block)
        return {**page, "page_kind": "content", "blocks": blocks}, "markdown_only"

    @staticmethod
    def _validate_page(page: dict[str, Any]) -> None:
        page_kind = page.get("page_kind")
        if page_kind not in {"content", "reference", "blank"}:
            raise InvalidModelPage("page_kind 非法或缺失")
        markdown = page.get("markdown")
        blocks = page.get("blocks")
        if not isinstance(markdown, str) or not isinstance(blocks, list):
            raise InvalidModelPage("markdown 或 blocks 类型非法")
        if page_kind != "blank" and (not markdown.strip() or not blocks):
            raise InvalidModelPage("非空页面缺少正文或内容块")
        if any(not isinstance(block, dict) for block in blocks):
            raise InvalidModelPage("blocks 包含非对象成员")

    @staticmethod
    def _retryable_page_error(exc: Exception) -> bool:
        if isinstance(exc, InvalidModelPage | RetryableJobError):
            return True
        return False

    async def extract_knowledge(self, markdown: str) -> dict[str, Any]:
        prompt = """你是计算机论文知识抽取器。基于给定 Markdown 输出严格 JSON，不要输出 Markdown 围栏。
格式：{"nodes":[{"kind":"section|concept|algorithm|formula|result","title":"名称","content":"中文解释","evidence_quote":"原文短引文"}],"edges":[{"source_title":"来源节点名称","target_title":"目标节点名称","relation":"CONTAINS|DEFINES|USES|ILLUSTRATES|RELATED_TO","evidence_quote":"原文短引文"}]}。
只抽取原文有证据的内容，不要从常识补充。\n\n""" + markdown
        content, _ = await self._complete(self.settings.knowledge_model, [{"role": "user", "content": prompt}])
        return _json_object(content)
