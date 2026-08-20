"""模型调用兼容层（V2-014）：统一视觉入口，按 ``api_select`` 路由 local 直连 / qed-engine 网关。

- ``local``（默认）：**direct** —— 用自身 ``API_KEY`` 直连 dashscope qwen-vl（OpenAI 兼容
  ``/chat/completions``，逐页图片），复用 fallback 客户端重试与错误语义，不依赖 8900
  在线（独立性铁律）；成功后写根仓库 ``qed_llm_calls`` 调用记录（``service=axiom_flow``、
  ``mode=api``、``provider=qwen``、``endpoint=vision``），DB 不可达时降级记日志不抛。
- ``qed-engine``：**gateway** —— HTTP 调 8900 ``POST /api/v1/llm/vision``，不接触密钥；
  调用记录由网关统一写，本层不重复写。MinerU（PDF 全文解析）仅经网关可达。

表结构契约以根仓库 ``llm-gateway-and-model-management.md``（§调用记录表）为准；
本层对齐 QED-Tracker REQ-043 三项目模型调用约定。

设计关联（DesignRef）：docs/design/model-mode-config.md
实现状态：Current（V2-014）
关联测试：tests/unit/test_llm_client.py
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import Engine, text

from axiom_flow.config import llm_api_key, load_settings
from axiom_flow.database import utc_now
from axiom_flow.fallback.client import (
    DEFAULT_BASE_URL,
    PROMPT,
    QwenVLPlusClient,
    VisionError,
    VisionResult,
)

logger = logging.getLogger(__name__)

DEFAULT_GATEWAY_URL = "http://127.0.0.1:8900"
_CALL_LOG_COLUMNS = (
    "service, mode, provider, model, endpoint, prompt_template, prompt, response,"
    " duration_ms, status, error, created_at"
)


class VisionClient:
    """统一视觉调用入口；``api_select`` 决定 direct（local）/ gateway（qed-engine）路由。"""

    def __init__(
        self,
        *,
        api_select: str = "local",
        api_key: str = "",
        model: str = "",
        base_url: str = DEFAULT_BASE_URL,
        gateway_url: str = DEFAULT_GATEWAY_URL,
        timeout: float = 180.0,
        max_attempts: int = 3,
        retry_delay: float = 2.0,
        transport: httpx.BaseTransport | None = None,
        engine: Engine | None = None,
    ) -> None:
        self.api_select = api_select
        self.api_key = api_key or llm_api_key()  # 缺省读 config（仅 API_KEY）
        self.model = model or load_settings().vision_model  # 缺省读 AXIOM_VISION_MODEL（默认 qwen-vl-plus）
        self.base_url = base_url
        self.gateway_url = gateway_url.rstrip("/")
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.last_call_id: int | None = None
        self._transport = transport
        self.client = httpx.Client(timeout=timeout, transport=transport)
        self.engine = engine  # direct 模式调用记录写 qed_llm_calls 用（None=不落库）

    @property
    def is_gateway(self) -> bool:
        return self.api_select == "qed-engine"

    # ---------------- 对外接口 ----------------

    def recognize_page(self, image: bytes | Path, page_no: int = 1) -> VisionResult:
        """识别单页：local 直连 / qed-engine 经网关；返回 Markdown 结果。"""
        if self.is_gateway:
            data = image.read_bytes() if isinstance(image, Path) else bytes(image)
            image_base64 = base64.b64encode(data).decode("ascii")
            reply = self._gateway_vision(image_base64=image_base64, prompt=PROMPT)
            return VisionResult(
                markdown=reply,
                contract_version="gateway-vision-v1",
                page_no=page_no,
            )
        started = time.monotonic()
        client = QwenVLPlusClient(
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            max_attempts=self.max_attempts,
            retry_delay=self.retry_delay,
            transport=self._transport,
        )
        result = client.recognize_page(image, page_no)
        self._record_call(PROMPT, result.markdown, started, status="success", error="")
        return result

    def parse_pdf(self, pdf_bytes: bytes, pdf_filename: str = "input.pdf") -> str:
        """PDF 全文解析（返回全文 Markdown）：MinerU 仅经 8900 网关可达。"""
        if not self.is_gateway:
            raise VisionError(
                "local 模式不支持 PDF 直连解析（MinerU 仅经 8900 网关可达），请切换到 qed-engine 模式"
            )
        pdf_base64 = base64.b64encode(bytes(pdf_bytes)).decode("ascii")
        return self._gateway_vision(
            pdf_base64=pdf_base64, pdf_filename=pdf_filename, prompt=PROMPT
        )

    # ---------------- gateway（8900 /api/v1/llm/vision） ----------------

    def _gateway_vision(
        self,
        *,
        image_base64: str | None = None,
        pdf_base64: str | None = None,
        pdf_filename: str = "input.pdf",
        prompt: str = "",
    ) -> str:
        """调用网关 /llm/vision，返回 ``reply``；``success=false`` / 格式错 / 网络错抛 VisionError。"""
        payload: dict[str, Any] = {"prompt": prompt}
        if image_base64 is not None:
            payload["image_base64"] = image_base64
        if pdf_base64 is not None:
            payload["pdf_base64"] = pdf_base64
            payload["pdf_filename"] = pdf_filename
        try:
            response = self.client.post(f"{self.gateway_url}/api/v1/llm/vision", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise VisionError("网关请求超时") from exc
        except httpx.NetworkError as exc:
            raise VisionError("网关请求失败（网络错误）") from exc
        except httpx.HTTPStatusError as exc:
            raise VisionError(f"网关返回 HTTP {exc.response.status_code}") from exc
        except ValueError as exc:
            raise VisionError("网关响应格式无效") from exc
        if not body.get("success"):
            error = str(body.get("error") or "").strip()
            raise VisionError(f"网关失败：{error}" if error else "网关失败")
        reply = body.get("reply")
        if not isinstance(reply, str):
            raise VisionError("网关响应格式无效：reply 非字符串")
        self.last_call_id = body.get("call_id")
        return reply

    # ---------------- qed_llm_calls 调用记录（仅 direct 成功路径） ----------------

    def _record_call(
        self, prompt: str, response_text: str, started: float, *, status: str, error: str
    ) -> None:
        if self.engine is None:
            return
        params = {
            "service": "axiom_flow",
            "mode": "api",
            "provider": "qwen",
            "model": self.model,
            "endpoint": "vision",
            "prompt_template": "",
            "prompt": prompt,
            "response": response_text,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "status": status,
            "error": error,
            "created_at": utc_now(),
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(
                    text(f"INSERT INTO qed_llm_calls ({_CALL_LOG_COLUMNS}) VALUES"
                         " (:service, :mode, :provider, :model, :endpoint, :prompt_template, :prompt,"
                         " :response, :duration_ms, :status, :error, :created_at)"),
                    params,
                )
        except Exception as exc:  # noqa: BLE001 - DB 不可达降级，不阻塞模型调用
            logger.warning("qed_llm_calls 写入降级（数据库不可达）：%s", type(exc).__name__)
