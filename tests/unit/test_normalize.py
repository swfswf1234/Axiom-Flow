"""归一化单测：引擎原始块 → 统一 blocks / Markdown / 质量信号（不依赖模型服务）。"""

from __future__ import annotations

import pytest

from axiom_flow.engines.base import PageResult
from axiom_flow.normalize.blocks import (
    blocks_to_markdown,
    compute_quality,
    normalize_page,
    scale_bbox,
)
from axiom_flow.schemas import BlockType

IMAGE_SIZE = (1240, 1754)
PAGE_SIZE = (595.0, 842.0)


def _normalize(raw_blocks, *, markdown="", page=1, page_size=PAGE_SIZE):
    return normalize_page(
        PageResult(page=page, blocks=raw_blocks, markdown=markdown, page_size=page_size),
        source="mineru",
        image_size=IMAGE_SIZE,
    )


class TestTypeMapping:
    """MinerU content_list 类型 → 统一块类型与必需字段。"""

    def test_title_maps_to_heading_with_level(self):
        page = _normalize([{"type": "title", "text": "第 1 章", "text_level": 2, "bbox": [0, 0, 100, 50]}])
        block = page.blocks[0]
        assert block.type is BlockType.HEADING
        assert block.level == 2
        assert block.text == "第 1 章"

    def test_text_maps_to_paragraph(self):
        page = _normalize([{"type": "text", "text": "正文", "bbox": [0, 0, 100, 50]}])
        assert page.blocks[0].type is BlockType.PARAGRAPH

    def test_equation_maps_to_formula(self):
        page = _normalize(
            [
                {"type": "equation", "text": "x^2", "bbox": [0, 0, 100, 50]},
                {"type": "interline_equation", "text": "y = x", "bbox": [0, 60, 100, 90]},
            ]
        )
        assert page.blocks[0].type is BlockType.FORMULA
        assert page.blocks[0].display is False
        assert page.blocks[1].display is True

    def test_table_maps_html_and_caption(self):
        page = _normalize(
            [
                {
                    "type": "table",
                    "table_body": "<table><tr><td>1</td></tr></table>",
                    "table_caption": ["表 1"],
                    "bbox": [0, 0, 100, 50],
                }
            ]
        )
        assert page.blocks[0].type is BlockType.TABLE
        assert page.blocks[0].html.startswith("<table>")
        assert page.blocks[0].caption == "表 1"

    def test_image_path_normalized_and_caption(self):
        page = _normalize([{"type": "image", "img_path": "images/a.png", "img_caption": ["图 1"], "bbox": [0, 0, 100, 50]}])
        assert page.blocks[0].type is BlockType.IMAGE
        assert page.blocks[0].path == "pages/images/a.png"
        assert page.blocks[0].caption == "图 1"

    def test_caption_header_footer_page_number(self):
        page = _normalize(
            [
                {"type": "image_caption", "text": "图注", "bbox": [0, 0, 10, 10]},
                {"type": "page_header", "text": "页眉", "bbox": [0, 0, 10, 10]},
                {"type": "page_footer", "text": "页脚", "bbox": [0, 0, 10, 10]},
                {"type": "page_number", "text": "3", "bbox": [0, 0, 10, 10]},
            ]
        )
        assert [b.type for b in page.blocks] == [
            BlockType.CAPTION,
            BlockType.HEADER,
            BlockType.FOOTER,
            BlockType.PAGE_NUMBER,
        ]

    def test_list_items_from_items_or_text(self):
        page = _normalize(
            [
                {"type": "list", "items": ["a", "b"], "bbox": [0, 0, 10, 10]},
                {"type": "list", "text": "- c\n- d", "bbox": [0, 0, 10, 10]},
            ]
        )
        assert page.blocks[0].items == ["a", "b"]
        assert page.blocks[1].items == ["c", "d"]

    def test_unknown_type_skipped(self):
        page = _normalize([{"type": "mystery", "text": "x", "bbox": [0, 0, 10, 10]}])
        assert page.blocks == []

    def test_formula_without_latex_skipped(self):
        page = _normalize([{"type": "equation", "bbox": [0, 0, 10, 10]}])
        assert page.blocks == []


class TestBBox:
    """坐标缩放：PDF 点 → 页图像素，夹取页内且保底 1px。"""

    def test_scale_full_page(self):
        assert scale_bbox((0, 0, 595, 842), page_size=PAGE_SIZE, image_size=IMAGE_SIZE) == (0, 0, 1240, 1754)

    def test_clamps_out_of_bounds(self):
        x0, y0, x1, y1 = scale_bbox((-10, -10, 9999, 9999), page_size=PAGE_SIZE, image_size=IMAGE_SIZE)
        assert (x0, y0) == (0, 0)
        assert (x1, y1) == IMAGE_SIZE

    def test_without_page_size_keeps_pixels(self):
        assert scale_bbox((10, 20, 30, 40), page_size=None, image_size=IMAGE_SIZE) == (10, 20, 30, 40)

    def test_blocks_get_scaled_bbox(self):
        page = _normalize([{"type": "text", "text": "x", "bbox": [0, 0, 595, 842]}])
        assert page.blocks[0].bbox == (0, 0, 1240, 1754)


class TestMarkdown:
    """页级 Markdown 组装：公式与结构保留，页眉/页脚/页码跳过。"""

    def test_assembles_blocks(self):
        page = _normalize(
            [
                {"type": "title", "text": "Ch 1", "text_level": 1, "bbox": [0, 0, 10, 10]},
                {"type": "text", "text": "正文", "bbox": [0, 0, 10, 10]},
                {"type": "interline_equation", "text": "x^2", "bbox": [0, 0, 10, 10]},
                {"type": "equation", "text": "y", "bbox": [0, 0, 10, 10]},
                {"type": "table", "table_body": "<table></table>", "bbox": [0, 0, 10, 10]},
                {"type": "list", "items": ["a"], "bbox": [0, 0, 10, 10]},
                {"type": "page_number", "text": "1", "bbox": [0, 0, 10, 10]},
            ]
        )
        markdown = blocks_to_markdown(page.blocks)
        assert "# Ch 1" in markdown
        assert "正文" in markdown
        assert "$$\nx^2\n$$" in markdown
        assert "$y$" in markdown
        assert "<table></table>" in markdown
        assert "- a" in markdown
        assert "\n1\n" not in markdown  # 页码不进 markdown


class TestNormalizePage:
    """页级归一化：index/order、质量信号与 markdown-only 回退。"""

    def test_index_and_order_are_sequential(self):
        page = _normalize(
            [
                {"type": "title", "text": "A", "bbox": [0, 0, 10, 10]},
                {"type": "text", "text": "B", "bbox": [0, 0, 10, 10]},
            ]
        )
        assert [b.index for b in page.blocks] == [0, 1]
        assert [b.order for b in page.blocks] == [0, 1]

    def test_markdown_only_falls_back_to_paragraph(self):
        page = _normalize([], markdown="# 只有 markdown\n\n正文", page=4)
        assert len(page.blocks) == 1
        assert page.blocks[0].type is BlockType.PARAGRAPH
        assert page.blocks[0].bbox == (0, 0, *IMAGE_SIZE)
        assert page.page == 4

    def test_quality_attached(self):
        page = _normalize([{"type": "text", "text": "x", "bbox": [0, 0, 10, 10]}])
        assert page.quality is not None
        assert page.quality.table_count == 0


class TestQuality:
    """质量信号：空块率 / 公式置信度 / 表格数 / 文本长度比。"""

    def test_metrics(self):
        page = _normalize(
            [
                {"type": "text", "text": "abcdef", "bbox": [0, 0, 10, 10]},
                {"type": "equation", "text": "x", "confidence": 0.8, "bbox": [0, 0, 10, 10]},
                {"type": "equation", "text": "y", "confidence": 0.6, "bbox": [0, 0, 10, 10]},
                {"type": "table", "table_body": "<table></table>", "bbox": [0, 0, 10, 10]},
            ]
        )
        quality = compute_quality(page.blocks, native_text_length=10)
        assert quality.table_count == 1
        assert quality.avg_formula_confidence == pytest.approx(0.7)
        assert quality.empty_block_ratio == 0.0
        assert quality.text_length_ratio == pytest.approx(0.8)

    def test_empty_page_ratio_is_one(self):
        quality = compute_quality([])
        assert quality.empty_block_ratio == 1.0
        assert quality.avg_formula_confidence == 1.0
        assert quality.text_length_ratio == 1.0
