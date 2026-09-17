"""MinerU 引擎适配器：直连 `QED_OCR_MODEL_URL`（容器 5002，生命周期归 8900）。

调用链（与根仓库 8900 已验证客户端同契约）：
1. `POST /file_parse`（multipart `files`）→ `{"task_id": ...}`；
2. `GET /get_task_results/{task_id}` 轮询 → `data.state ∈ done|pending|fail|error`；
3. `done` 时取 `content_list`（块级结构，含 bbox）/ `markdown` / `middle_json`（页尺寸）/
   `images`（base64）。

解析参数（后端/语言/公式开关等）由模型服务侧（QED-Engine）管理，本适配器不暴露调参。
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import replace
from typing import Any

import httpx

from axiom_flow.engines.base import DocumentResult, EngineError, PageResult

_FAIL_STATES = {"fail", "error"}
_MIME_BY_SUFFIX = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _mime_for(filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


def _load_json_field(data: dict[str, Any], key: str) -> Any:
    """字段可能是 JSON 字符串（MinerU 部分版本）或已解析对象，统一为对象。"""
    value = data.get(key)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _page_sizes(data: dict[str, Any]) -> dict[int, tuple[float, float]]:
    """从 middle_json.pdf_info 提取每页 PDF 点尺寸（bbox 缩放基准）。"""
    middle = _load_json_field(data, "middle_json")
    sizes: dict[int, tuple[float, float]] = {}
    if isinstance(middle, dict):
        for index, info in enumerate(middle.get("pdf_info") or []):
            size = info.get("page_size") if isinstance(info, dict) else None
            if isinstance(size, (list, tuple)) and len(size) == 2:
                sizes[index] = (float(size[0]), float(size[1]))
    return sizes


def _images(data: dict[str, Any]) -> dict[str, bytes]:
    """文档内嵌图片：`images` 为 {相对路径: base64}；坏值跳过不阻断。"""
    raw = data.get("images")
    if not isinstance(raw, dict):
        return {}
    images: dict[str, bytes] = {}
    for name, payload in raw.items():
        if not isinstance(payload, str):
            continue
        try:
            images[str(name)] = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            continue
    return images


def _pages_from_result(data: dict[str, Any]) -> list[PageResult]:
    """content_list 按 page_idx（0 基）分组 → 页级结果；缺结构时回退整段 markdown。"""
    content_list = _load_json_field(data, "content_list") or []
    markdown = data.get("markdown") or ""
    sizes = _page_sizes(data)
    grouped: dict[int, list[dict]] = {}
    if isinstance(content_list, list):
        for item in content_list:
            if not isinstance(item, dict):
                continue
            try:
                page_idx = int(item.get("page_idx") or 0)
            except (TypeError, ValueError):
                page_idx = 0
            grouped.setdefault(page_idx, []).append(item)
    if not grouped:
        if not markdown.strip():
            raise EngineError("MinerU 解析完成但无结构化产物")
        return [PageResult(page=1, markdown=markdown)]
    return [
        PageResult(page=index + 1, blocks=grouped[index], page_size=sizes.get(index))
        for index in sorted(grouped)
    ]


class MinerUEngine:
    """MinerU 适配器（HTTP 同步调用 + 轮询）。"""

    name = "mineru"

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 600.0,
        poll_interval: float = 2.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.poll_interval = float(poll_interval)
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def health(self) -> bool:
        """探活：`GET /health`（MinerU 容器健康端点）。"""
        try:
            response = self._http().get(f"{self.base_url}/health", timeout=min(self.timeout, 5.0))
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def _submit_and_wait(self, content: bytes, filename: str) -> dict[str, Any]:
        try:
            response = self._http().post(
                f"{self.base_url}/file_parse",
                files={"files": (filename, content, _mime_for(filename))},
            )
        except httpx.HTTPError as exc:
            raise EngineError(f"MinerU 服务不可达：{exc}") from exc
        if response.status_code != 200:
            raise EngineError(f"MinerU 提交失败：HTTP {response.status_code}")
        try:
            task_id = response.json().get("task_id")
        except ValueError as exc:
            raise EngineError("MinerU 提交失败：响应不是 JSON") from exc
        if not task_id:
            raise EngineError("MinerU 提交失败：响应缺 task_id")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                result = self._http().get(f"{self.base_url}/get_task_results/{task_id}")
            except httpx.HTTPError as exc:
                raise EngineError(f"MinerU 结果查询失败：{exc}") from exc
            if result.status_code != 200:
                raise EngineError(f"MinerU 结果查询失败：HTTP {result.status_code}")
            data = (result.json() or {}).get("data") or {}
            state = data.get("state")
            if state == "done":
                return data
            if state in _FAIL_STATES:
                raise EngineError(f"MinerU 解析失败：{data.get('err_msg') or state}")
            time.sleep(self.poll_interval)
        raise EngineError(f"MinerU 解析超时（>{self.timeout:g}s）")

    def parse_document(self, pdf_bytes: bytes, filename: str) -> DocumentResult:
        """全本解析：整本 PDF 一次提交，返回逐页结果 + 内嵌图片。"""
        data = self._submit_and_wait(pdf_bytes, filename)
        return DocumentResult(pages=_pages_from_result(data), images=_images(data))

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
        """单页解析：页图提交，结果页码重写为请求页码。"""
        data = self._submit_and_wait(image_bytes, f"p{page_no:04d}.png")
        pages = _pages_from_result(data)
        if not pages:
            raise EngineError(f"MinerU 未返回第 {page_no} 页内容")
        return replace(pages[0], page=page_no)
