"""MinerU 引擎适配器：直连 `QED_OCR_MODEL_URL`（容器 5002，生命周期归 8900）。

调用链三段化（MinerU 3.4.4 实测契约，PLAN-003 W0 冻结表与条文 1/1b/2/11）：

1. `POST /tasks`（multipart 字段名 `files` + 产物形态开关）→ **202** + 顶层 `task_id`；
2. `GET /tasks/{task_id}` 轮询 → 顶层 `status`：`pending|processing` 继续，`completed` 进入取结果，
   `failed|error` 抛错（取顶层 `error` 文本）；
3. `GET /tasks/{task_id}/result` → **202＝未就绪继续等**，200 → `{backend, version,
   results:{"<file_stem>":{md_content, content_list, middle_json}}}`。

`content_list`/`middle_json` 在真机为 JSON 字符串，解析面兼容两种载体。解析参数（后端/语言/公式
开关等）由模型服务侧（QED-Engine）管理，本适配器只设产物形态开关、不设解析调参。
HTTP 客户端恒以 `trust_env=False` 构造：Windows 系统代理对回环地址不做豁免，交给代理会出现
502/0 字节假象（条文 11）。
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import replace
from typing import Any

import httpx

from axiom_flow.engines.base import BboxSpace, DocumentResult, EngineError, ModelLoad, PageResult

_DONE_STATES = {"done", "completed", "finished", "success"}
_FAIL_STATES = {"fail", "failed", "error"}
# content_list[].bbox 真机锚点测定为「相对页框、逐轴 0–1000 归一化」（PDF 点与像素都不是），
# 且整本与逐页两条路径同一空间；据此归一化侧按 image_size/1000 映射（DEFECT-004）。
_BBOX_SPACE: BboxSpace = "normalized_1000"
# 轮询瞬时失败（传输层/非 2xx）容忍次数；达到阈值才判任务失败（A2）。
_TRANSIENT_FAILURE_LIMIT = 3
# 只设产物形态开关：openapi 中 return_content_list/return_middle_json 默认 false，不显式开启则
# blocks 与页尺寸恒缺失（A5）。
_OUTPUT_FLAGS = {"return_md": "true", "return_content_list": "true", "return_middle_json": "true"}
_MIME_BY_SUFFIX = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _count(value: Any) -> int | None:
    """负载计数字段：非负整数才采信（`bool` 不算），其余（缺失/字符串/负数）返回 None。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _mime_for(filename: str) -> str:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


def _stem(filename: str) -> str:
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _json_object(response: httpx.Response, context: str) -> dict[str, Any]:
    """响应体必须是 JSON 对象；非 JSON 归一为 EngineError，绝不穿透到编排层（A3）。"""
    try:
        payload = response.json()
    except ValueError as exc:
        raise EngineError(f"{context}：响应不是 JSON（HTTP {response.status_code}）") from exc
    if not isinstance(payload, dict):
        raise EngineError(f"{context}：响应不是 JSON 对象")
    return payload


def _load_json_field(data: dict[str, Any], key: str) -> Any:
    """字段可能是 JSON 字符串（MinerU 3.4.4 真机形态）或已解析对象，统一为对象。"""
    value = data.get(key)
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _status_of(payload: dict[str, Any]) -> str:
    """状态载体兼容：真机为顶层 `status`，旧式响应为 `data.state`。"""
    status = payload.get("status")
    if isinstance(status, str) and status:
        return status.lower()
    nested = payload.get("data")
    if isinstance(nested, dict):
        legacy = nested.get("state")
        if isinstance(legacy, str) and legacy:
            return legacy.lower()
    return ""


def _error_text(payload: dict[str, Any], status: str) -> str:
    for key in ("error", "err_msg", "detail", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = payload.get("data")
    if isinstance(nested, dict):
        for key in ("error", "err_msg"):
            value = nested.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return status


def _result_payload(data: dict[str, Any], stem: str) -> dict[str, Any]:
    """解包 `results[<file_stem>]`；单文件提交且键不匹配时按唯一键回落（真机按去扩展名分组）。"""
    results = data.get("results")
    if isinstance(results, dict) and results:
        grouped = results.get(stem)
        if not isinstance(grouped, dict) and len(results) == 1:
            grouped = next(iter(results.values()))
        return grouped if isinstance(grouped, dict) else {}
    return data


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
    """文档内嵌图片：`images` 为 {相对路径: base64}；坏值跳过不阻断。

    本仓库不请求 `return_images`（页图由 ingest 自产），此处仅兼容引擎可选返回。
    """
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
    markdown = _markdown_of(data)
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
        return [PageResult(page=1, markdown=markdown, bbox_space=_BBOX_SPACE)]
    return [
        PageResult(page=index + 1, blocks=grouped[index], page_size=sizes.get(index), bbox_space=_BBOX_SPACE)
        for index in sorted(grouped)
    ]


def _markdown_of(data: dict[str, Any]) -> str:
    """正文字段兼容：真机为 `md_content`，旧式版本为 `markdown`。"""
    for key in ("md_content", "markdown"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


class MinerUEngine:
    """MinerU 适配器（HTTP 同步调用 + 三段轮询）。"""

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
            # trust_env=False：不让系统/环境变量代理接管回环流量（条文 11）。
            self._client = httpx.Client(timeout=self.timeout, trust_env=False)
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

    def model_load(self) -> ModelLoad | None:
        """负载探测：`GET /health` 的 `queued_tasks` / `processing_tasks`（3.4.4 实测字段）。

        非 200、传输异常、非 JSON 对象或字段缺失/非计数一律返回 `None`＝**无法判定**：编排层按
        可用放行，不因为探测不灵就凭空造出一个阻塞；真正的不可达仍由 `health()` 判定。
        """
        try:
            response = self._http().get(f"{self.base_url}/health", timeout=min(self.timeout, 5.0))
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        queued = _count(payload.get("queued_tasks"))
        processing = _count(payload.get("processing_tasks"))
        if queued is None or processing is None:
            return None
        return ModelLoad(queued=queued, processing=processing)

    def _submit(self, content: bytes, filename: str) -> str:
        """提交任务：`POST /tasks`，202 受理并返回顶层 `task_id`。"""
        try:
            response = self._http().post(
                f"{self.base_url}/tasks",
                files={"files": (filename, content, _mime_for(filename))},
                data=_OUTPUT_FLAGS,
            )
        except httpx.HTTPError as exc:
            raise EngineError(f"MinerU 服务不可达：{exc}") from exc
        if response.status_code not in (200, 202):
            raise EngineError(f"MinerU 提交失败：HTTP {response.status_code}")
        payload = _json_object(response, "MinerU 提交")
        task_id = payload.get("task_id")
        if not task_id:
            raise EngineError("MinerU 提交失败：响应缺 task_id")
        return str(task_id)

    def _wait_done(self, task_id: str, deadline: float) -> None:
        """轮询终态：瞬时失败连续 3 次才判失败，deadline 前不中断（A2/A3）。"""
        transient_failures = 0
        while True:
            if time.monotonic() >= deadline:
                raise EngineError(f"MinerU 解析超时（>{self.timeout:g}s）")
            try:
                response = self._http().get(f"{self.base_url}/tasks/{task_id}")
            except httpx.HTTPError as exc:
                transient_failures += 1
                if transient_failures >= _TRANSIENT_FAILURE_LIMIT:
                    raise EngineError(
                        f"MinerU 轮询失败（连续 {transient_failures} 次）：{exc}"
                    ) from exc
                time.sleep(self.poll_interval)
                continue
            if response.status_code != 200:
                transient_failures += 1
                if transient_failures >= _TRANSIENT_FAILURE_LIMIT:
                    raise EngineError(
                        f"MinerU 轮询失败（连续 {transient_failures} 次）：HTTP {response.status_code}"
                    )
                time.sleep(self.poll_interval)
                continue
            transient_failures = 0
            payload = _json_object(response, "MinerU 状态查询")
            status = _status_of(payload)
            if status in _DONE_STATES:
                return
            if status in _FAIL_STATES:
                raise EngineError(f"MinerU 解析失败：{_error_text(payload, status)}")
            time.sleep(self.poll_interval)

    def _fetch_result(self, task_id: str, deadline: float) -> dict[str, Any]:
        """取结果：202＝未就绪继续等；200＝返回载荷；其余非 2xx 即失败。"""
        while True:
            if time.monotonic() >= deadline:
                raise EngineError(f"MinerU 结果获取超时（>{self.timeout:g}s）")
            try:
                response = self._http().get(f"{self.base_url}/tasks/{task_id}/result")
            except httpx.HTTPError as exc:
                raise EngineError(f"MinerU 结果查询失败：{exc}") from exc
            if response.status_code == 202:
                time.sleep(self.poll_interval)
                continue
            if response.status_code != 200:
                raise EngineError(f"MinerU 结果查询失败：HTTP {response.status_code}")
            return _json_object(response, "MinerU 结果查询")

    def _run(self, content: bytes, filename: str) -> dict[str, Any]:
        """三段编排：提交 → 轮询终态 → 取结果，全程受同一 deadline 约束。"""
        task_id = self._submit(content, filename)
        deadline = time.monotonic() + self.timeout
        self._wait_done(task_id, deadline)
        return _result_payload(self._fetch_result(task_id, deadline), _stem(filename))

    def parse_document(self, pdf_bytes: bytes, filename: str) -> DocumentResult:
        """全本解析：整本 PDF 一次提交，返回逐页结果 + 内嵌图片。"""
        data = self._run(pdf_bytes, filename)
        return DocumentResult(pages=_pages_from_result(data), images=_images(data))

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
        """单页解析：页图提交，结果页码重写为请求页码。"""
        data = self._run(image_bytes, f"p{page_no:04d}.png")
        pages = _pages_from_result(data)
        if not pages:
            raise EngineError(f"MinerU 未返回第 {page_no} 页内容")
        return replace(pages[0], page=page_no)
