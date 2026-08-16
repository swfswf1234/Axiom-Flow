"""qwen-vl-plus 客户端：页图 → 阿里百炼 OpenAI 兼容端点 → Markdown（设计文档 §组件职责）。

设计关联（DesignRef）：docs/design/pdf-parsing-and-rendering.md、docs/design/8902-integration-contract.md
实现状态：Current（V2-010 实验落地，V2-006 继承）
关联测试：tests/unit/test_fallback.py
"""

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEFAULT_MODEL = "qwen-vl-plus"

PROMPT = (
    "这是一页数学教材（英文版《Principles of Mathematical Analysis》）的页面图像。"
    "请完整识别该页面的全部内容并输出 Markdown：\n"
    "1. 章节标题用 #/##/### 标记；\n"
    "2. 数学公式用 $$...$$（独立公式）或 $...$（行内公式）包裹，保留完整 LaTeX；\n"
    "3. 保持阅读顺序，不要遗漏任何段落、公式、表格或页眉页脚；\n"
    "4. 只输出内容本身，不要任何解释或额外文字。"
)


class VisionError(Exception):
    """图像识别失败（网络/限流/服务端错误/空响应）。"""


@dataclass(frozen=True)
class VisionResult:
    """单页识别结果：Markdown 正文 + 契约版本 + 页码 + token 用量。"""

    markdown: str
    contract_version: str
    page_no: int
    usage: dict | None = None


class QwenVLPlusClient:
    """qwen-vl-plus 视觉识别客户端（OpenAI 兼容协议，不依赖 dashscope SDK）。

    页级重试语义：限流（429）、5xx 与网络/超时错误按指数退避重试，最多
    ``max_attempts`` 次；其余 4xx 立即失败。凭据取 ``AXIOM_API_KEY``。
    """

    CONTRACT_VERSION = "qwen-vl-markdown-v1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = 8192,
        timeout: float = 180.0,
        max_attempts: int = 3,
        retry_delay: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        api_key = api_key if api_key is not None else os.environ.get("AXIOM_API_KEY")
        if not api_key:
            raise VisionError("缺少凭据：请配置 AXIOM_API_KEY（阿里百炼）")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self._transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, transport=self._transport)

    def recognize_page(self, image: bytes | Path, page_no: int = 1) -> VisionResult:
        """识别单页：image 为 PNG 字节或路径；返回 Markdown 结果。"""
        data = image.read_bytes() if isinstance(image, Path) else bytes(image)
        image_url = "data:image/png;base64," + base64.b64encode(data).decode("ascii")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": PROMPT},
                    ],
                }
            ],
            "max_tokens": self.max_tokens,
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self._client() as client:
                    response = client.post(
                        self.base_url,
                        json=payload,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                if response.status_code in (429,) or response.status_code >= 500:
                    raise _RetryableError(f"服务端状态 {response.status_code}")
                if response.status_code >= 400:
                    raise VisionError(f"请求失败（{response.status_code}）：{response.text[:200]}")
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content")
                if not content:
                    raise VisionError("响应无内容（截断或空响应）")
                return VisionResult(
                    markdown=content,
                    contract_version=self.CONTRACT_VERSION,
                    page_no=page_no,
                    usage=response.json().get("usage"),
                )
            except _RetryableError as exc:
                last_error = exc
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            if attempt < self.max_attempts:
                import time

                time.sleep(self.retry_delay * attempt)
        raise VisionError(f"识别失败（已重试 {self.max_attempts} 次）：{last_error}") from last_error


class _RetryableError(Exception):
    """可重试的服务端错误（429/5xx）。"""
