"""Markdown → blocks 确定性归一化：qwen-vl-plus 输出转统一块结构（设计文档 §组件职责）。

设计关联（DesignRef）：docs/design/parsing-pipeline.md
实现状态：Current（V2-010 实验落地，V2-006 继承）
关联测试：tests/unit/test_fallback.py
"""

import re
from typing import cast

from axiom_flow.schemas import Block, BlocksPage, BlockType, Source

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_DISPLAY_MATH_RE = re.compile(r"^\$\$(?P<body>.+?)\$\$$", re.DOTALL)
_FENCED_MATH_RE = re.compile(r"^```math\s*\n(?P<body>.*?)```$", re.DOTALL)


def markdown_to_blocks(
    markdown: str,
    page: int,
    page_width: int = 1275,
    page_height: int = 1650,
) -> BlocksPage:
    """按空行切分段落，确定性生成 heading/paragraph/formula 块（v1 qwen-ocr-markdown-v2 先例）。

    - 整段 `$$...$$` 或 ```math``` → formula（display=True）
    - `#~###### ` 开头 → heading
    - 其余（含列表、行内 `$...$`）→ paragraph，保留原始 Markdown 文本
    - bbox 为实验阶段占位（垂直均分页高），后续深化阶段由真实版面坐标替换
    """
    blocks: list[Block] = []
    width = max(page_width, 1)
    height = max(page_height, 1)
    segments = [s.strip("\n") for s in re.split(r"\n\s*\n", markdown.strip()) if s.strip()]

    for index, segment in enumerate(segments):
        y0 = height * index // len(segments)
        y1 = height * (index + 1) // len(segments)
        bbox = (0, y0, width, y1)

        heading = _HEADING_RE.match(segment)
        if heading:
            blocks.append(
                Block(
                    type=BlockType.HEADING,
                    level=len(heading.group(1)),
                    text=heading.group(2).strip(),
                    bbox=bbox,
                )
            )
            continue

        display = _DISPLAY_MATH_RE.match(segment) or _FENCED_MATH_RE.match(segment)
        if display:
            blocks.append(
                Block(
                    type=BlockType.FORMULA,
                    latex=display.group("body").strip(),
                    display=True,
                    bbox=bbox,
                )
            )
            continue

        blocks.append(Block(type=BlockType.PARAGRAPH, text=segment, bbox=bbox))

    return BlocksPage(page=page, source=Source.QWEN_VL_PLUS, blocks=cast(list[Block], blocks))
