"""引擎适配器契约：统一输入（PDF/页图字节）与输出（页级原始块 + Markdown + 页尺寸）。

设计事实源：`docs/design/model-integration.md`。适配器不暴露模型调参（归 QED-Engine）；
模型差异在适配层消化，`normalize/` 负责归一为统一 blocks。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

BboxSpace = Literal["pdf_points", "normalized_1000", "pixels"]
"""原始块 `bbox` 的坐标空间：PDF 点 / 逐轴 0–1000 归一化 / 已是页图像素。"""


class EngineError(RuntimeError):
    """引擎调用失败（服务不可达/超时/解析失败）——编排层记为单页失败或任务 `failed`。

    模型服务不可用在 API 层另有两道映射：提交期探活失败 → 503（不落任务行），
    受理后进到执行期 → job `failed`（`orchestrator/pipeline.py`）。
    """


@dataclass(frozen=True, slots=True)
class PageResult:
    """引擎页级原始输出（归一化前）。

    `blocks` 为引擎原始结构化条目（如 MinerU content_list 条目）；`bbox_space` 声明其坐标空间，
    由 `normalize/` 按该空间换算到页图像素；`page_size` 为引擎报告的 `pdf_info.page_size`，
    **仅 `pdf_points` 空间可作缩放基准**（MinerU 在 PNG 输入下回的是按 200 DPI 折算的标称点，
    与归一化 bbox 组不出自洽映射，见 DEFECT-004）。
    """

    page: int
    blocks: list[dict] = field(default_factory=list)
    markdown: str = ""
    page_size: tuple[float, float] | None = None
    bbox_space: BboxSpace = "pdf_points"


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """整本文档解析结果：页级结果 + 文档内嵌图片（相对路径 → 字节）。"""

    pages: list[PageResult] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelLoad:
    """模型服务当前负载（编排层单一占用闸门的判据）。

    GPU 资源不足以并行解析，服务侧的并发许可（MinerU `max_concurrent_requests: 3`）**不能**
    替代调用方自我排他。
    """

    queued: int
    processing: int

    @property
    def busy(self) -> bool:
        """有排队或在飞任务即视为占用。"""
        return (self.queued + self.processing) > 0


@runtime_checkable
class ParseEngine(Protocol):
    """解析引擎协议：健康探活、负载探测、全本解析（默认）、单页解析（重解析）。"""

    name: str

    def health(self) -> bool: ...

    def model_load(self) -> ModelLoad | None:
        """当前负载；引擎不上报、服务未答或载荷不合预期时返回 `None`（无法判定，不是占用）。"""
        ...

    def parse_document(self, pdf_bytes: bytes, filename: str) -> DocumentResult: ...

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult: ...
