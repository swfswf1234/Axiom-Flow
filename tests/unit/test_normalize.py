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


class TestBBoxCoordinateSpace:
    """DEFECT-004 / 根仓库 REQ-081：MinerU 3.4.4 `content_list[].bbox` 是逐轴 0–1000 归一化坐标。

    锚点输入取自真机实验 `data/probes/live-bringup-20260920/L3d_coord_space.json`：A4（595x842pt）
    PDF 内把文本钉在已知点坐标，150 DPI 页图为 1240x1755 像素。
    """

    ANCHOR_IMAGE = (1240, 1755)

    def test_normalized_1000_scales_per_axis_to_pixels(self):
        assert scale_bbox(
            (0, 0, 1000, 1000), page_size=None, image_size=IMAGE_SIZE, space="normalized_1000"
        ) == (0, 0, *IMAGE_SIZE)
        assert scale_bbox(
            (500, 500, 600, 700), page_size=None, image_size=IMAGE_SIZE, space="normalized_1000"
        ) == (620, 877, 744, 1228)

    def test_normalized_1000_ignores_engine_page_size(self):
        """PNG 输入的 `pdf_info.page_size` 是 200 DPI 标称点，不得参与缩放。"""
        mapped = scale_bbox(
            (500, 500, 600, 700),
            page_size=(446.0, 631.0),
            image_size=(1241, 1755),
            space="normalized_1000",
        )
        assert mapped == (620, 878, 745, 1228)

    def test_anchor_lands_on_placed_text(self):
        """真机锚点：放置在 x=420pt、基线 y=780pt 的文本，其块应落回该文本的像素位置。"""
        mapped = scale_bbox(
            (702, 910, 946, 928), page_size=(595.0, 842.0), image_size=self.ANCHOR_IMAGE,
            space="normalized_1000",
        )
        assert abs(mapped[0] - 875) <= 12  # 420pt → 875px
        assert abs(mapped[1] - 1596) <= 12  # 14pt 字面顶部 → 1596px
        assert abs(mapped[3] - 1625) <= 12  # 780pt 基线 → 1625px
        assert mapped[3] < self.ANCHOR_IMAGE[1] - 20, "块贴底边＝旧版点空间过放后被夹取（REQ-081 症状）"

    def test_pixels_space_only_clamps(self):
        assert scale_bbox((10, 20, 30, 40), page_size=PAGE_SIZE, image_size=IMAGE_SIZE, space="pixels") == (
            10, 20, 30, 40)
        assert scale_bbox((-5, -5, 9999, 9999), page_size=PAGE_SIZE, image_size=IMAGE_SIZE, space="pixels")[:2] == (0, 0)

    def test_pdf_points_space_is_default(self):
        """未声明空间的引擎（预留槽位）仍按 PDF 点 → 像素缩放。"""
        assert scale_bbox((0, 0, 595, 842), page_size=PAGE_SIZE, image_size=IMAGE_SIZE) == (0, 0, 1240, 1754)

    def test_normalize_page_uses_result_bbox_space(self):
        """归一化按引擎声明的坐标空间映射；旧版错配会产出贴右缘的 1px 细条块。"""
        page = normalize_page(
            PageResult(
                page=3,
                blocks=[{"type": "text", "text": "正文", "bbox": [16, 40, 984, 62], "page_idx": 2}],
                page_size=(446.0, 631.0),
                bbox_space="normalized_1000",
            ),
            source="mineru",
            image_size=(1241, 1755),
        )
        x0, y0, x1, y1 = page.blocks[0].bbox
        assert (x1 - x0) > 20 and (y1 - y0) > 20
        assert x1 <= 1241 and y1 <= 1755
        assert (x0, y0) != (x1 - 1, y1 - 1)

    def test_missing_bbox_is_full_page_without_rescaling(self):
        page = normalize_page(
            PageResult(page=1, blocks=[{"type": "text", "text": "无坐标"}], bbox_space="normalized_1000"),
            source="mineru",
            image_size=IMAGE_SIZE,
        )
        assert page.blocks[0].bbox == (0, 0, *IMAGE_SIZE)


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
