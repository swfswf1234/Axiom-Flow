"""引擎原始输出 → 统一 blocks（类型映射、bbox 坐标空间换算、阅读序）+ 页级 Markdown。

设计事实源：`docs/design/parsing-pipeline.md`。MinerU `content_list` 条目在适配层消化：
类型映射、坐标换算（引擎声明的 `bbox_space` → 页图像素）、图片路径规范为 book 目录相对路径。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from axiom_flow.engines.base import BboxSpace, PageResult
from axiom_flow.schemas import Block, BlocksPage, BlockType, QualityMetrics

# MinerU content_list 类型 → 统一块类型。真机 3.4.4 的页眉/页脚字面值是 `header`/`footer`
# （`page_header`/`page_footer` 为旧版/其它版本字面值，两者并存以免回归），见 DEFECT-005。
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
    "header": BlockType.HEADER,
    "footer": BlockType.FOOTER,
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
    space: BboxSpace = "pdf_points",
) -> tuple[int, int, int, int]:
    """引擎原始坐标 → 页图像素坐标，并夹取到页内、保底 1px。

    `space` 由引擎适配器声明（真机测定见 `docs/design/parsing-pipeline.md`）：
    `normalized_1000` 逐轴按 `image_size/1000` 映射（MinerU 3.4.4 两条提交路径共用，此时
    `page_size` 不参与缩放）；`pdf_points` 按 `image_size/page_size` 映射；`pixels` 只夹取。
    """
    if space == "normalized_1000":
        bbox = _multiply(bbox, image_size[0] / 1000.0, image_size[1] / 1000.0)
    elif space == "pdf_points" and page_size and page_size[0] > 0 and page_size[1] > 0:
        bbox = _multiply(bbox, image_size[0] / page_size[0], image_size[1] / page_size[1])
    x0 = max(0, min(bbox[0], image_size[0] - 1))
    y0 = max(0, min(bbox[1], image_size[1] - 1))
    x1 = max(x0 + 1, min(bbox[2], image_size[0]))
    y1 = max(y0 + 1, min(bbox[3], image_size[1]))
    return (x0, y0, x1, y1)


def _multiply(
    bbox: tuple[int, int, int, int], scale_x: float, scale_y: float
) -> tuple[int, int, int, int]:
    return (
        int(round(bbox[0] * scale_x)),
        int(round(bbox[1] * scale_y)),
        int(round(bbox[2] * scale_x)),
        int(round(bbox[3] * scale_y)),
    )


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


#: 数学定界符 → 是否行间式（长定界符在前，`$$` 必须先于 `$` 判定）
_MATH_DELIMITERS: tuple[tuple[str, str, bool], ...] = (
    ("$$", "$$", True),
    (r"\[", r"\]", True),
    ("$", "$", False),
    (r"\(", r"\)", False),
)


def split_math_delimiters(value: str) -> tuple[str, bool | None]:
    """剥掉引擎自带的数学定界符，返回 `(裸 LaTeX, display 判定或 None)`。

    MinerU 3.4.4 的 `equation` 条目把 `$$…$$` 连定界符一起塞进 `latex`，而契约（parsing-pipeline
    的 `formula.latex` 示例）与两个 Markdown 写出方都假定**裸源码**：不剥就会二次包裹成
    `$$$…$$$`，KaTeX 无法渲染（ARCH-018）。定界符成对且内部不再含同种定界符才剥——两段公式拼在
    一个条目里时剥掉会把它们吞成一段，宁可保留可见异常等人工判定。
    """
    body = value.strip()
    for open_token, close_token, display in _MATH_DELIMITERS:
        if not body.startswith(open_token) or not body.endswith(close_token):
            continue
        inner = body[len(open_token): len(body) - len(close_token)].strip()
        if inner and open_token not in inner and close_token not in inner:
            return inner, display
    return body, None


def block_from_raw(
    raw: dict[str, Any],
    *,
    index: int,
    page_size: tuple[float, float] | None,
    image_size: tuple[int, int],
    space: BboxSpace = "pdf_points",
) -> Block | None:
    """单条引擎原始块 → 统一 Block；无法归一的条目返回 None（跳过，不阻断整页）。"""
    block_type = _TYPE_MAP.get(str(raw.get("type", "")).lower())
    if block_type is None:
        return None
    bbox = _bbox_tuple(raw.get("bbox"))
    if bbox is None:
        # 引擎未给坐标：整页占位已是像素，不再走缩放（避免二次映射）
        bbox = (0, 0, image_size[0], image_size[1])
    else:
        bbox = scale_bbox(bbox, page_size=page_size, image_size=image_size, space=space)

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
        # 定界符自带 display 语义时以它为准：真机 3.4.4 的行间式走 `equation` 字面值，类型判不出行间
        latex, display_from_delim = split_math_delimiters(latex)
        payload["latex"] = latex
        payload["display"] = (
            str(raw.get("type", "")).lower() == "interline_equation" or bool(raw.get("display"))
        ) if display_from_delim is None else display_from_delim
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


def compute_quality(
    blocks: list[Block],
    *,
    native_text_length: int | None = None,
    dropped_literals: Sequence[str] = (),
) -> QualityMetrics:
    """页质量信号：空块率 / 公式平均置信度（无公式取 1.0）/ 表格数 / 文本长度比 + 可见丢弃。

    `dropped_literals` 为引擎返回但未能归一的条目原始 `type` 字面值：丢弃必须**可见**
    （计入信号），否则同页块数与引擎条目数不一致却无从判定（DEFECT-005 契约）。
    """
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
        dropped_block_count=len(dropped_literals),
        dropped_types=sorted({str(literal) for literal in dropped_literals}),
    )


def normalize_page(
    result: PageResult,
    *,
    source: str,
    image_size: tuple[int, int],
    native_text_length: int | None = None,
) -> BlocksPage:
    """引擎页级结果 → 统一 `BlocksPage`（含 index/order 与质量信号）。

    引擎**未回结构化条目**只回 markdown 时，整页回退为单个 paragraph 块（bbox = 全页）；回了条目
    但全部未能归一时不回退——那是映射缺口，回退会把缺陷伪装成一个正文块（违反可见丢弃契约）。
    """
    blocks: list[Block] = []
    dropped: list[str] = []
    for raw in result.blocks:
        block = block_from_raw(
            raw,
            index=len(blocks),
            page_size=result.page_size,
            image_size=image_size,
            space=result.bbox_space,
        )
        if block is None:
            dropped.append(str(raw.get("type", "")))
        else:
            blocks.append(block)
    if not result.blocks and result.markdown.strip():
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
        quality=compute_quality(blocks, native_text_length=native_text_length, dropped_literals=dropped),
    )
