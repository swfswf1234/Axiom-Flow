"""引擎原始输出 → 统一 blocks（类型映射、bbox 缩放、阅读序）+ 页级 Markdown。

设计事实源：`docs/design/parsing-pipeline.md`。MinerU `content_list` 条目在适配层消化：
类型映射、坐标缩放（PDF 点 → 页图像素）、图片路径规范为 book 目录相对路径。
"""

from __future__ import annotations

from typing import Any

from axiom_flow.engines.base import PageResult
from axiom_flow.schemas import Block, BlocksPage, BlockType, QualityMetrics

# MinerU content_list 类型 → 统一块类型
_TYPE_MAP: dict[str, BlockType] = {
    "title": BlockType.HEADING,
    "text": BlockType.PARAGRAPH,
    "equation": BlockType.FORMULA,
    "interline_equation": BlockType.FORMULA,
    "table": BlockType.TABLE,
    "image": BlockType.IMAGE,
    "image_caption": BlockType.CAPTION,
    "img_caption": BlockType.CAPTION,
    "table_caption": BlockType.CAPTION,
    "table_footnote": BlockType.CAPTION,
    "page_footnote": BlockType.CAPTION,
    "page_header": BlockType.HEADER,
    "page_footer": BlockType.FOOTER,
    "page_number": BlockType.PAGE_NUMBER,
    "list": BlockType.LIST,
}

_SKIP_IN_MARKDOWN = {BlockType.HEADER, BlockType.FOOTER, BlockType.PAGE_NUMBER}


def _as_text(value: Any) -> str:
    """字段可能是 str 或 list[str]（caption 类），统一为拼接文本。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return " ".join(_as_text(item) for item in value if item).strip()
    return str(value).strip()


def _bbox_tuple(raw: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return (int(round(float(raw[0]))), int(round(float(raw[1]))), int(round(float(raw[2]))), int(round(float(raw[3]))))
    except (TypeError, ValueError):
        return None


def scale_bbox(
    bbox: tuple[int, int, int, int],
    *,
    page_size: tuple[float, float] | None,
    image_size: tuple[int, int],
) -> tuple[int, int, int, int]:
    """PDF 点坐标 → 页图像素坐标（按 page_size/image_size 缩放），并夹取到页内、保底 1px。"""
    if page_size and page_size[0] > 0 and page_size[1] > 0:
        scale_x = image_size[0] / page_size[0]
        scale_y = image_size[1] / page_size[1]
        bbox = (
            int(round(bbox[0] * scale_x)),
            int(round(bbox[1] * scale_y)),
            int(round(bbox[2] * scale_x)),
            int(round(bbox[3] * scale_y)),
        )
    x0 = max(0, min(bbox[0], image_size[0] - 1))
    y0 = max(0, min(bbox[1], image_size[1] - 1))
    x1 = max(x0 + 1, min(bbox[2], image_size[0]))
    y1 = max(y0 + 1, min(bbox[3], image_size[1]))
    return (x0, y0, x1, y1)


def _image_path(raw: dict[str, Any]) -> str:
    """图片路径规范：`img_path`（如 images/abc.jpg）→ book 目录相对 `pages/images/abc.jpg`。"""
    value = _as_text(raw.get("img_path") or raw.get("image_path") or raw.get("path"))
    if not value:
        return ""
    return value if value.startswith("pages/") else f"pages/{value}"


def _list_items(raw: dict[str, Any]) -> list[str]:
    items = raw.get("items")
    if isinstance(items, list):
        return [str(item) for item in items if str(item).strip()]
    text = _as_text(raw.get("text"))
    return [line.strip("-* \t") for line in text.splitlines() if line.strip("-* \t")]


def block_from_raw(
    raw: dict[str, Any],
    *,
    index: int,
    page_size: tuple[float, float] | None,
    image_size: tuple[int, int],
) -> Block | None:
    """单条引擎原始块 → 统一 Block；无法归一的条目返回 None（跳过，不阻断整页）。"""
    block_type = _TYPE_MAP.get(str(raw.get("type", "")).lower())
    if block_type is None:
        return None
    bbox = _bbox_tuple(raw.get("bbox"))
    if bbox is None:
        bbox = (0, 0, image_size[0], image_size[1])
    bbox = scale_bbox(bbox, page_size=page_size, image_size=image_size)

    text = _as_text(raw.get("text"))
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = None

    payload: dict[str, Any] = {
        "type": block_type,
        "bbox": bbox,
        "index": index,
        "order": index,
        "confidence": confidence,
    }
    if block_type is BlockType.HEADING:
        level = raw.get("text_level") or raw.get("level") or 1
        payload["text"] = text or " "
        payload["level"] = int(level) if str(level).isdigit() and 1 <= int(level) <= 6 else 1
    elif block_type in (BlockType.PARAGRAPH, BlockType.CAPTION, BlockType.HEADER, BlockType.FOOTER, BlockType.PAGE_NUMBER):
        payload["text"] = text
    elif block_type is BlockType.FORMULA:
        latex = _as_text(raw.get("latex")) or text
        if not latex:
            return None
        payload["latex"] = latex
        payload["display"] = str(raw.get("type", "")).lower() == "interline_equation" or bool(raw.get("display"))
    elif block_type is BlockType.TABLE:
        html = _as_text(raw.get("table_body")) or _as_text(raw.get("html"))
        if not html:
            return None
        payload["html"] = html
        payload["caption"] = _as_text(raw.get("table_caption")) or None
    elif block_type is BlockType.IMAGE:
        path = _image_path(raw)
        if not path:
            return None
        payload["path"] = path
        payload["caption"] = _as_text(raw.get("img_caption") or raw.get("image_caption")) or None
    elif block_type is BlockType.LIST:
        items = _list_items(raw)
        if not items:
            return None
        payload["items"] = items
    return Block(**payload)


def blocks_to_markdown(blocks: list[Block]) -> str:
    """页级 Markdown 组装（公式 `$$...$$`/`$...$`；页眉/页脚/页码跳过）。"""
    parts: list[str] = []
    for block in blocks:
        if block.type in _SKIP_IN_MARKDOWN:
            continue
        if block.type is BlockType.HEADING:
            parts.append(f"{'#' * (block.level or 1)} {block.text or ''}".rstrip())
        elif block.type in (BlockType.PARAGRAPH, BlockType.CAPTION):
            if block.text:
                parts.append(block.text)
        elif block.type is BlockType.FORMULA:
            latex = block.latex or ""
            parts.append(f"$$\n{latex}\n$$" if block.display else f"${latex}$")
        elif block.type is BlockType.TABLE:
            parts.append(block.html or "")
        elif block.type is BlockType.IMAGE:
            parts.append(f"![{block.caption or ''}]({block.path or ''})")
        elif block.type is BlockType.LIST:
            parts.append("\n".join(f"- {item}" for item in block.items or []))
    return "\n\n".join(part for part in parts if part).strip() + "\n"


def compute_quality(blocks: list[Block], *, native_text_length: int | None = None) -> QualityMetrics:
    """页质量信号：空块率 / 公式平均置信度（无公式取 1.0）/ 表格数 / 文本长度比。"""
    total = len(blocks)
    empty = sum(1 for block in blocks if not (block.text or block.latex or block.html or block.items))
    empty_ratio = (empty / total) if total else 1.0

    confidences = [
        block.confidence
        for block in blocks
        if block.type is BlockType.FORMULA and block.confidence is not None
    ]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 1.0

    text_length = sum(len(block.text or "") + len(block.latex or "") for block in blocks)
    if native_text_length and native_text_length > 0:
        ratio = min(text_length / native_text_length, 10.0)
    else:
        ratio = 1.0

    return QualityMetrics(
        empty_block_ratio=round(empty_ratio, 4),
        avg_formula_confidence=round(avg_confidence, 4),
        table_count=sum(1 for block in blocks if block.type is BlockType.TABLE),
        text_length_ratio=round(ratio, 4),
    )


def normalize_page(
    result: PageResult,
    *,
    source: str,
    image_size: tuple[int, int],
    native_text_length: int | None = None,
) -> BlocksPage:
    """引擎页级结果 → 统一 `BlocksPage`（含 index/order 与质量信号）。

    引擎只回 markdown（无结构化块）时，整页回退为单个 paragraph 块（bbox = 全页）。
    """
    blocks: list[Block] = []
    for raw in result.blocks:
        block = block_from_raw(raw, index=len(blocks), page_size=result.page_size, image_size=image_size)
        if block is not None:
            blocks.append(block)
    if not blocks and result.markdown.strip():
        blocks = [
            Block(
                type=BlockType.PARAGRAPH,
                bbox=(0, 0, image_size[0], image_size[1]),
                index=0,
                order=0,
                text=result.markdown.strip(),
            )
        ]
    return BlocksPage(
        page=result.page,
        image_size=image_size,
        source=source,
        blocks=blocks,
        quality=compute_quality(blocks, native_text_length=native_text_length),
    )
