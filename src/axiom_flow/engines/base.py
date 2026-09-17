"""引擎适配器契约：统一输入（PDF/页图字节）与输出（页级原始块 + Markdown + 页尺寸）。

设计事实源：`docs/design/model-integration.md`。适配器不暴露模型调参（归 QED-Engine）；
模型差异在适配层消化，`normalize/` 负责归一为统一 blocks。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


class EngineError(RuntimeError):
    """引擎调用失败（服务不可达/超时/解析失败）——API 层映射为 503 或任务失败。"""


@dataclass(frozen=True, slots=True)
class PageResult:
    """引擎页级原始输出（归一化前）。

    `blocks` 为引擎原始结构化条目（如 MinerU content_list 条目）；`page_size` 为 PDF 点坐标
    下的页面尺寸（bbox 缩放基准），缺省 None 表示 bbox 已按图像像素给出。
    """

    page: int
    blocks: list[dict] = field(default_factory=list)
    markdown: str = ""
    page_size: tuple[float, float] | None = None


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """整本文档解析结果：页级结果 + 文档内嵌图片（相对路径 → 字节）。"""

    pages: list[PageResult] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)


@runtime_checkable
class ParseEngine(Protocol):
    """解析引擎协议：健康探活、全本解析（默认）、单页解析（重解析）。"""

    name: str

    def health(self) -> bool: ...

    def parse_document(self, pdf_bytes: bytes, filename: str) -> DocumentResult: ...

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult: ...
