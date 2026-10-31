"""归一化单测：引擎原始块 → 统一 blocks / Markdown / 质量信号 / 跨页重复线（不依赖模型服务）。"""

from __future__ import annotations

import pytest

from axiom_flow.engines.base import PageResult
from axiom_flow.normalize.blocks import (
    blocks_to_markdown,
    compute_quality,
    normalize_page,
    scale_bbox,
)
from axiom_flow.normalize.furniture import (
    furniture_blocks,
    furniture_hints,
    furniture_texts,
    strip_furniture,
)
from axiom_flow.normalize.toc import (
    TOC_PAGE_SAMPLE_MAX_CHARS,
    TOC_PAGE_SCAN_LIMIT,
    TocEntry,
    anchor_block_for_name,
    clean_entry_rows,
    estimate_print_offset,
    locate_entries,
    page_sample_text,
    reference_role_for_name,
    toc_entries,
)
from axiom_flow.schemas import Block, BlocksPage, BlockType

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

    锚点输入取自真机实验 `tmp/probes/live-bringup-20260920/L3d_coord_space.json`：A4（595x842pt）
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


class TestFormulaDelimiters:
    """ARCH-018：真机 MinerU 3.4.4 的 `equation` 条目 `latex` 自带定界符，归一必须剥出裸源码。

    契约见 `docs/design/parsing-pipeline.md`（`formula.latex` 示例为 `x + y = y + x`，行间/行内
    由 `display` 表达）。定界符留在 `latex` 里会让 `blocks_to_markdown` 与分块再包一层，页级
    Markdown 出现 `$$$…$$$`，KaTeX 无法渲染（2026-09-22 代表书取证：b05 52 段 / b11 63 段全红）。
    """

    def test_display_dollars_stripped_and_display_true(self):
        page = _normalize([{"type": "equation", "latex": "$$\nx + y = z\n$$", "bbox": [0, 0, 10, 10]}])
        block = page.blocks[0]
        assert block.latex == "x + y = z"
        assert block.display is True

    def test_inline_dollars_stripped_and_display_false(self):
        page = _normalize([{"type": "equation", "latex": "$\\alpha$", "bbox": [0, 0, 10, 10]}])
        block = page.blocks[0]
        assert block.latex == "\\alpha"
        assert block.display is False

    def test_bracket_delimiters_map_to_display_flag(self):
        page = _normalize(
            [
                {"type": "equation", "latex": r"\[a=b\]", "bbox": [0, 0, 10, 10]},
                {"type": "equation", "latex": r"\(c=d\)", "bbox": [0, 0, 10, 10]},
            ]
        )
        assert page.blocks[0].latex == "a=b"
        assert page.blocks[0].display is True
        assert page.blocks[1].latex == "c=d"
        assert page.blocks[1].display is False

    def test_bare_latex_untouched(self):
        page = _normalize([{"type": "equation", "latex": "\\frac{1}{2}", "bbox": [0, 0, 10, 10]}])
        assert page.blocks[0].latex == "\\frac{1}{2}"

    def test_inner_delimiter_pair_not_stripped(self):
        """两段公式拼在一个条目里时不剥：剥了会把 `$$` 吞成一段（宁可保留可见异常）。"""
        raw = "$$a$$\n$$b$$"
        page = _normalize([{"type": "equation", "latex": raw, "bbox": [0, 0, 10, 10]}])
        assert page.blocks[0].latex == raw

    def test_markdown_never_emits_triple_dollar(self):
        page = _normalize([{"type": "equation", "latex": "$$\nI = I _ { 0 }\n$$", "bbox": [0, 0, 10, 10]}])
        markdown = blocks_to_markdown(page.blocks)
        assert "$$$" not in markdown
        assert markdown.strip() == "$$\nI = I _ { 0 }\n$$"


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
        assert page.quality.dropped_block_count == 0

    def test_all_items_dropped_never_fall_back_to_paragraph(self):
        """原始条目全部未归一时不得整页回退：回退会把映射缺口伪装成一个正文块（静默降级）。"""
        page = _normalize([{"type": "chart", "text": "图"}], markdown="正文")
        assert page.blocks == []
        assert page.quality.dropped_block_count == 1
        assert page.quality.dropped_types == ["chart"]

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


class TestVisibleDrop:
    """DEFECT-005：真机类型字面值映射 + 未覆盖/必需字段缺失必须**可见丢弃**（计入质量信号）。"""

    def test_real_machine_header_and_footer_literals_map(self):
        """真机 `content_list` 回的是 `header`/`footer`（非 `page_header`/`page_footer`）→ 必须成块。"""
        page = _normalize([
            {"type": "header", "text": "吉米多维奇数学分析习题集", "bbox": [0, 0, 100, 10]},
            {"type": "text", "text": "正文", "bbox": [0, 50, 100, 80]},
            {"type": "footer", "text": "42", "bbox": [0, 900, 100, 930]},
        ])
        assert [b.type for b in page.blocks] == [
            BlockType.HEADER,
            BlockType.PARAGRAPH,
            BlockType.FOOTER,
        ]
        assert [b.text for b in page.blocks] == ["吉米多维奇数学分析习题集", "正文", "42"]

    def test_legacy_page_header_literals_still_map(self):
        """旧字面值不回归：`page_header`/`page_footer` 与 `header`/`footer` 同义。"""
        page = _normalize([
            {"type": "page_header", "text": "旧页眉", "bbox": [0, 0, 10, 10]},
            {"type": "page_footer", "text": "旧页脚", "bbox": [0, 0, 10, 10]},
        ])
        assert [b.type for b in page.blocks] == [BlockType.HEADER, BlockType.FOOTER]

    def test_unmapped_literal_is_dropped_visibly(self):
        """映射表未覆盖的字面值：丢块但计入质量信号，不静默消失。"""
        page = _normalize([
            {"type": "text", "text": "正文", "bbox": [0, 0, 10, 10]},
            {"type": "aside_text", "text": "边注", "bbox": [0, 0, 10, 10]},
            {"type": "chart", "text": "图", "bbox": [0, 0, 10, 10]},
        ])
        assert [b.type for b in page.blocks] == [BlockType.PARAGRAPH]
        assert page.quality is not None
        assert page.quality.dropped_block_count == 2
        assert page.quality.dropped_types == ["aside_text", "chart"]

    def test_missing_required_field_drop_is_visible_too(self):
        """必需字段缺失（表格无 html、公式无 latex）同样计入，不静默丢块。"""
        page = _normalize([
            {"type": "table", "bbox": [0, 0, 10, 10]},
            {"type": "equation", "bbox": [0, 0, 10, 10]},
            {"type": "image", "bbox": [0, 0, 10, 10]},
            {"type": "text", "text": "正文", "bbox": [0, 0, 10, 10]},
        ])
        assert [b.type for b in page.blocks] == [BlockType.PARAGRAPH]
        assert page.quality is not None
        assert page.quality.dropped_block_count == 3
        assert page.quality.dropped_types == ["equation", "image", "table"]

    def test_index_stays_contiguous_after_drop(self):
        """丢弃不得让 `index` 跳号：块编辑以 `index` 为定位键（af_operations 的 edit 意图行）。"""
        page = _normalize([
            {"type": "aside_text", "text": "边注", "bbox": [0, 0, 10, 10]},
            {"type": "text", "text": "第一", "bbox": [0, 0, 10, 10]},
            {"type": "chart", "text": "图", "bbox": [0, 0, 10, 10]},
            {"type": "text", "text": "第二", "bbox": [0, 0, 10, 10]},
        ])
        assert [b.index for b in page.blocks] == [0, 1]
        assert [b.order for b in page.blocks] == [0, 1]

    def test_no_drop_keeps_signal_zero(self):
        page = _normalize([{"type": "text", "text": "正文", "bbox": [0, 0, 10, 10]}])
        assert page.quality is not None
        assert page.quality.dropped_block_count == 0
        assert page.quality.dropped_types == []

    def test_header_footer_excluded_from_markdown(self):
        """页眉/页脚成块后仍不进页级 Markdown（否则正文混入页码）。"""
        page = _normalize([
            {"type": "header", "text": "页眉", "bbox": [0, 0, 10, 10]},
            {"type": "text", "text": "正文", "bbox": [0, 50, 10, 80]},
            {"type": "footer", "text": "42", "bbox": [0, 900, 10, 930]},
        ])
        markdown = blocks_to_markdown(page.blocks)
        assert markdown.strip() == "正文"

    def test_compute_quality_signature_back_compatible(self):
        """旧调用点（只传 blocks）不传丢弃信息时默认为 0。"""
        quality = compute_quality([])
        assert quality.dropped_block_count == 0

    def test_accounting_invariant_on_mixed_page(self):
        """对账不变式（默认门禁覆盖，不依赖真机）：原始条目数 = 存活块数 + 可见丢弃数。"""
        raw = [
            {"type": "text", "text": "正文", "bbox": [0, 0, 10, 10]},
            {"type": "chart", "text": "图", "bbox": [0, 0, 10, 10]},
            {"type": "table", "bbox": [0, 0, 10, 10]},
            {"type": "header", "text": "页眉", "bbox": [0, 20, 10, 30]},
        ]
        page = _normalize(raw, markdown="正文\n\n页眉")
        assert len(raw) == len(page.blocks) + page.quality.dropped_block_count

class TestBlankPageSignal:
    """PLAN-007 W2 / REQ-087：空白页是合法产物，必须与「解析失败」「映射缺口」可区分（判据 S5）。

    真机形状（`tmp/probes/window-req086-20260922` 的 `real-blank-only`）：任务 `completed`、
    `pdf_info` 有该页、`content_list` 与 `md_content` 皆空 ⇒ 归一化为零块页并带空白标记。
    """

    def test_page_with_nothing_from_engine_is_blank(self):
        page = _normalize([], markdown="", page=48)
        assert page.blocks == []
        assert page.quality is not None
        assert page.quality.blank_page is True

    def test_markdown_only_page_is_not_blank(self):
        page = _normalize([], markdown="正文", page=2)
        assert page.quality.blank_page is False

    def test_all_dropped_page_is_not_blank(self):
        """反向判据：条目全未映射的页**不是**空白页——那是映射缺口，不得伪装成白页。"""
        page = _normalize([{"type": "chart", "text": "图"}], markdown="")
        assert page.quality is not None
        assert page.quality.blank_page is False
        assert page.quality.dropped_block_count == 1


class TestRefTextMapping:
    """ARCH-019（2026-09-22 用户裁决取 (A)）：`ref_text` 参考文献条目映射为 `PARAGRAPH`，丢弃归零。

    (B) `LIST` 聚合会改变块下标 `index`（块编辑定位键），属渲染面需求，根侧提出后再议。
    """

    def test_ref_text_maps_to_paragraph(self):
        page = _normalize([{"type": "ref_text", "text": "[1] W. Rudin, Principles", "bbox": [0, 0, 10, 10]}])
        assert page.blocks[0].type is BlockType.PARAGRAPH
        assert page.blocks[0].text == "[1] W. Rudin, Principles"

    def test_ref_text_leaves_no_drop(self):
        page = _normalize([
            {"type": "text", "text": "正文", "bbox": [0, 0, 10, 10]},
            {"type": "ref_text", "text": "[2] 参考文献", "bbox": [0, 0, 10, 10]},
        ])
        assert page.quality is not None
        assert page.quality.dropped_block_count == 0
        assert page.quality.dropped_types == []

    def test_adjacent_unmapped_literal_still_visible(self):
        """反向判据：补 `ref_text` 不得顺手放宽其它字面值。"""
        page = _normalize([
            {"type": "ref_text", "text": "[1] x", "bbox": [0, 0, 10, 10]},
            {"type": "aside_text", "text": "边注", "bbox": [0, 0, 10, 10]},
        ])
        assert len(page.blocks) == 1
        assert page.quality.dropped_types == ["aside_text"]


def _line_block(index: int, text: str, y: int) -> Block:
    """只关心「文本＋纵向位置」的块：类型一律 paragraph——判据不看类型，判错的正是被测对象。"""
    return Block(
        type=BlockType.PARAGRAPH, bbox=[72, y, 1168, y + 40], index=index, order=index, text=text
    )


def _page_of(page_no: int, *blocks: Block) -> BlocksPage:
    return BlocksPage(page=page_no, image_size=(1240, 1754), source="mineru", blocks=list(blocks))


class TestFurnitureHints:
    """跨页重复线的页域判据（PLAN-026 W4）：读时派生、零写库、零重解析。

    判据只有一条＝**同一文本在多数页的同一纵向位置出现**。另两条件（页边带几何、短文本）留给根侧
    渲染腿合取（REQ-140 R3 的分层）：AF 只供给这一维，不替渲染面判「像不像页眉」。
    """

    def test_a_line_repeated_at_one_height_on_most_pages_is_flagged(self):
        pages = [
            _page_of(
                no,
                _line_block(0, "PRINCIPLES OF MATHEMATICAL ANALYSIS", 58),
                _line_block(1, f"body {no}", 300 + no * 90),
            )
            for no in range(1, 9)
        ]

        hints = furniture_hints(pages)

        assert [hint.text for hint in hints] == ["principles of mathematical analysis"]
        assert hints[0].members == tuple((no, 0) for no in range(1, 9))
        assert hints[0].y_center == 58

    def test_a_definition_sentence_repeated_in_the_body_is_not_flagged(self):
        """反例（本壳点名的那条）：正文里反复出现的定义句纵向位置各不相同 ⇒ 不成线。"""
        pages = [_page_of(no, _line_block(0, "lim x_n = a", 320 + no * 150)) for no in range(1, 9)]

        assert furniture_hints(pages) == ()

    def test_a_line_on_only_a_minority_of_pages_is_not_flagged(self):
        """分母是整本页数：8 页里只有 3 页有这一行（前 3 页的章名眉）⇒ 不到「多数页」，不判。"""
        pages = [
            _page_of(no, _line_block(0, "CHAPTER 1", 58))
            if no <= 3
            else _page_of(no, _line_block(0, f"body {no}", 300))
            for no in range(1, 9)
        ]

        assert furniture_hints(pages) == ()

    def test_the_gate_counts_pages_not_block_occurrences(self):
        """重复块不充数：同一页上两处同文本同行，跨页数仍是 1。"""
        pages = [
            _page_of(no, _line_block(0, "x", 58), _line_block(1, "x", 58)) for no in (1, 2)
        ]

        assert furniture_hints(pages) == ()

    def test_width_and_spacing_variants_fold_into_one_line(self):
        """全角与空白差异不拆线：折叠口径 NFKC＋折空白＋小写，与「同一行」的直觉一致。"""
        pages = [
            _page_of(1, _line_block(0, "CHAPTER 1", 60)),
            _page_of(2, _line_block(0, "ＣＨＡＰＴＥＲ 1", 62)),
            _page_of(3, _line_block(0, "chapter  1", 59)),
            _page_of(4, _line_block(0, "body text", 400)),
        ]

        hints = furniture_hints(pages)

        assert [(hint.text, hint.members) for hint in hints] == [
            ("chapter 1", ((1, 0), (2, 0), (3, 0)))
        ]

    def test_blocks_without_text_do_not_join_a_line(self):
        """无正文的块（公式／表／图）没有「同一文本」可言：一律不参与，不当成空串行。"""
        pages = [
            _page_of(
                no,
                Block(
                    type=BlockType.FORMULA,
                    bbox=[72, 58, 1168, 98],
                    index=0,
                    order=0,
                    latex="x^2",
                    display=True,
                ),
            )
            for no in range(1, 9)
        ]

        assert furniture_hints(pages) == ()


def _typed_block(index: int, text: str, y: int, block_type: BlockType) -> Block:
    """带类型的行块：类型维看的就是这一枚 `type`，其余字段与 `_line_block` 同形。"""
    return Block(
        type=block_type, bbox=[72, y, 1168, y + 40], index=index, order=index, text=text
    )


class TestFurnitureTexts:
    """家具文本的两条来源腿（PLAN-026 W5 改判／第二令 R10）＝人工判定的类型维 ∪ 跨页重复线的线维。

    输出按页**稀疏**给出（没有家具的页不占键），文本一律走归一口径——消费方拿到的就是可以逐行
    比对的形态。类型维吃的是回放后的 `type`（人工判成什么），不是产物原判。
    """

    def test_blocks_typed_as_furniture_give_their_own_page_only(self):
        """页码各页不同文本 ⇒ 线维判不出，全靠类型维承接：这正是「人工改判页码」那条腿的形状。"""
        pages = [
            _page_of(
                no, _line_block(0, f"正文 {no}", 300), _typed_block(1, f"第 {no} 页", 1700, BlockType.PAGE_NUMBER)
            )
            for no in (1, 2)
        ]

        assert furniture_texts(pages) == {1: ("第 1 页",), 2: ("第 2 页",)}

    def test_the_three_furniture_types_are_one_leg(self):
        """`header`／`footer`／`page_number` 同一条腿：三枚都算家具，取材面不分视任一。"""
        pages = [
            _page_of(
                1,
                _typed_block(0, "页眉", 58, BlockType.HEADER),
                _line_block(1, "正文", 300),
                _typed_block(2, "页脚", 1660, BlockType.FOOTER),
                _typed_block(3, "3", 1740, BlockType.PAGE_NUMBER),
            )
        ]

        assert furniture_texts(pages) == {1: ("3", "页眉", "页脚")}

    def test_a_furniture_block_without_text_adds_nothing(self):
        """判成页眉但产物没有正文的块不贡献文本：空串会吃掉整页的空行分隔。"""
        pages = [_page_of(1, _typed_block(0, "   ", 58, BlockType.HEADER), _line_block(1, "正文", 300))]

        assert furniture_texts(pages) == {}

    def test_a_repeated_line_is_added_only_to_the_pages_carrying_it(self):
        """线维按成员落页：4 页里 3 页有这条眉就只给那 3 页——第 4 页不该被别人的线扫到。"""
        pages = [
            _page_of(no, _line_block(0, "CHAPTER ONE", 58), _line_block(1, f"正文 {no}", 300))
            if no <= 3
            else _page_of(4, _line_block(0, "正文 4", 300))
            for no in range(1, 5)
        ]
        hints = furniture_hints(pages)

        assert [hint.text for hint in hints] == ["chapter one"]
        assert furniture_texts(pages, hints=hints) == {
            1: ("chapter one",),
            2: ("chapter one",),
            3: ("chapter one",),
        }


class TestFurnitureBlocks:
    """页数据读投影的家具标记（PLAN-026 W12 ③）：同一判据的**块下标**投影，供渲染腿逐块隐藏。

    文本腿（`furniture_texts`）供取材剔除逐行比对，块腿供屏侧按块隐藏——两枚投影必须同源，
    否则「送模型的」与「屏上隐藏的」会各判一套。类型维看回放后的 `type`，线维看 `hints` 成员；
    输出稀疏，块坐标就是 `Block.index`（块编辑与锚点用的那一套）。
    """

    def test_a_replayed_furniture_type_marks_its_own_block(self):
        pages = [
            _page_of(1, _typed_block(0, "页眉", 58, BlockType.HEADER), _line_block(1, "正文", 300))
        ]

        assert furniture_blocks(pages) == {1: (0,)}

    def test_a_furniture_block_without_text_is_still_marked(self):
        """文本腿要靠文本比对，块腿不用：判成页眉的空块也是家具，屏上照样该隐藏。"""
        pages = [
            _page_of(1, _typed_block(0, "   ", 58, BlockType.HEADER), _line_block(1, "正文", 300))
        ]

        assert furniture_blocks(pages) == {1: (0,)}

    def test_a_repeated_line_marks_the_carrying_block_on_every_page(self):
        """线维落块：4 页同一高度同一串字 ⇒ 每页第 0 块被标，正文块不动。"""
        pages = [
            _page_of(no, _line_block(0, "PRINCIPLES", 58), _line_block(1, f"正文 {no}", 300))
            for no in range(1, 5)
        ]

        assert furniture_blocks(pages, hints=furniture_hints(pages)) == {
            1: (0,),
            2: (0,),
            3: (0,),
            4: (0,),
        }

    def test_pages_outside_the_line_are_not_marked(self):
        """反向断言：不参与这条线的页拿不到标记——同一串字在别页的正文里是内容。"""
        pages = [
            _page_of(no, _line_block(0, "CHAPTER ONE", 58), _line_block(1, f"正文 {no}", 300))
            if no <= 3
            else _page_of(4, _line_block(0, "正文 4", 300))
            for no in range(1, 5)
        ]
        marks = furniture_blocks(pages, hints=furniture_hints(pages))

        assert marks == {1: (0,), 2: (0,), 3: (0,)}

    def test_both_legs_on_one_block_yield_one_entry(self):
        """两腿合取不是两条记录：同一块既判成页眉又落在线维上，下标只出现一次。"""
        pages = [
            _page_of(
                no,
                _typed_block(0, "PRINCIPLES", 58, BlockType.HEADER),
                _line_block(1, f"正文 {no}", 300),
            )
            for no in range(1, 5)
        ]

        assert furniture_blocks(pages, hints=furniture_hints(pages)) == {
            1: (0,),
            2: (0,),
            3: (0,),
            4: (0,),
        }

    def test_a_book_without_furniture_is_sparse(self):
        pages = [_page_of(no, _line_block(0, f"正文 {no}", 300 + no * 97)) for no in range(1, 5)]

        assert furniture_blocks(pages, hints=furniture_hints(pages)) == {}


class TestStripFurniture:
    """取材剔除只做「整行折叠相等」：送进模型的副本删掉家具行，其余每一行原样（零写库、零改产物）。"""

    def test_a_line_that_is_exactly_a_furniture_text_goes(self):
        markdown = "PRINCIPLES\n\n正文第一段\n"

        assert strip_furniture(markdown, ("principles",)) == "\n正文第一段\n"

    def test_an_empty_furniture_set_is_the_identity(self):
        """没有家具读数的页一律逐字返回：判据不成立时取材面与产物面同形。"""
        markdown = "PRINCIPLES\n\n正文\n"

        assert strip_furniture(markdown, ()) == markdown

    def test_a_sentence_that_merely_contains_the_furniture_text_stays(self):
        """「包含」不剔：正文里引用页眉那串书名是内容，不是家具行。"""
        markdown = "见 PRINCIPLES OF MATHEMATICAL ANALYSIS 前言\n\n正文\n"

        assert strip_furniture(markdown, ("principles of mathematical analysis",)) == markdown

    def test_full_width_and_spacing_variants_hit_the_same_line(self):
        """比对与 `furniture_texts` 同一条归一口径：产物里的全角章名照样对上折叠后的家具文本。"""
        markdown = "ＣＨＡＰＴＥＲ  1\n\n正文\n"

        assert strip_furniture(markdown, ("chapter 1",)) == "\n正文\n"

    def test_an_atx_heading_line_carrying_the_furniture_text_goes(self):
        """标题行的 `#` 是渲染装饰：页眉被引擎判成标题时，整行仍属那一条线（b05 章名眉的实际形状）。"""
        markdown = "# PRINCIPLES\n\n正文\n"

        assert strip_furniture(markdown, ("principles",)) == "\n正文\n"

    def test_everything_else_round_trips_line_for_line(self):
        """只删行、不改行：公式、缩进、表格竖线都不动，删完留下的空行也不合并。"""
        markdown = "x^2\n\n  - 列表项\n|a|b|\n\nPRINCIPLES\n末行"

        assert strip_furniture(markdown, ("principles",)) == "x^2\n\n  - 列表项\n|a|b|\n\n末行"


def _paragraph_page(page_no: int, *texts: str) -> BlocksPage:
    """一页只有段落块的产物形状（现网零 `heading` 的教材就是这个形状）。"""
    return BlocksPage(
        page=page_no,
        image_size=[1000, 1400],
        source="mineru",
        blocks=[
            Block(
                type=BlockType.PARAGRAPH,
                bbox=[0, 80 + 40 * index, 900, 120 + 40 * index],
                index=index,
                order=index,
                text=text,
            )
            for index, text in enumerate(texts)
        ],
    )


class TestTocEntries:
    """`KA-33` 的目录页条目腿：只剥行末、折行拼回、偏移众数标定与窗口内命中。"""

    def test_a_toc_block_splits_into_entries_with_printed_pages(self):
        page = _paragraph_page(
            7, "第1章 集合论 …… 1\n第2章 实数与极限 …… 49\n第3章 单调序与级数 …… 73"
        )

        entries = toc_entries(page)

        assert [(e.name, e.printed_page) for e in entries] == [
            ("第1章 集合论", 1),
            ("第2章 实数与极限", 49),
            ("第3章 单调序与级数", 73),
        ]
        assert all(e.block_index == 0 for e in entries)

    def test_only_the_line_end_is_stripped_while_digits_inside_names_survive(self):
        """名字中间的数字一概不碰：行首的节号与行内的「2.10」都是名字的一部分。"""
        page = _paragraph_page(
            7, "2.10 一致连续 …… 88\n3 完备性 …… 91\n第4章 连续性 107\n第5章 导数 123"
        )

        entries = toc_entries(page)

        assert [e.name for e in entries] == ["2.10 一致连续", "3 完备性", "第4章 连续性", "第5章 导数"]
        assert [e.printed_page for e in entries] == [88, 91, 107, 123]

    def test_a_line_without_a_trailing_page_joins_the_numbered_entry_it_belonged_to(self):
        """折行拼回：一条条目被产物切成两行时，前半截不得丢弃（丢了就吃掉整个章号）。"""
        page = _paragraph_page(
            7, "第6章 RIEMANN-STIEI.\nTJES积分 …… 115\n第7章 级数 …… 137\n第8章 等度连续 …… 155"
        )

        entries = toc_entries(page)

        assert [(e.name, e.printed_page) for e in entries] == [
            ("第6章 RIEMANN-STIEI. TJES积分", 115),
            ("第7章 级数", 137),
            ("第8章 等度连续", 155),
        ]

    def test_the_chapter_line_shape_gives_the_level(self):
        """层级只由行形状给：`第N章` 与**单级**编号起头＝章，`N.M` 多级编号与其余＝节（`KA-35` 直接消费它）。"""
        page = _paragraph_page(
            7, "第1章 集合论 …… 1\n1.1 序的关系 …… 3\n关于实数的域公理 …… 9\n第2章 实数 …… 49"
        )

        assert [e.level for e in toc_entries(page)] == [1, 2, 2, 1]

    def test_single_level_numbering_is_a_chapter_and_dotted_sections_are_not(self):
        """`1.`／`3 ` 是章号（书里确有「1. 引言」这种章），`1.1`／`2.10` 是节号——判别只在分隔符后有没有数字。"""
        page = _paragraph_page(
            7, "1. 引言 …… 1\n2.10 一致连续 …… 3\n3 完备性 …… 9\n第4章 连续性 …… 49"
        )

        assert [e.level for e in toc_entries(page)] == [1, 2, 1, 1]

    def test_a_block_with_fewer_lines_than_the_threshold_is_not_a_toc_page(self):
        """两行的段落块不算目录页：少于 `TOC_MIN_ENTRIES` 行的重复是巧合。"""
        page = _paragraph_page(3, "第1章 集合论 …… 1\n第2章 实数 …… 49")

        assert toc_entries(page) == ()

    def test_a_block_where_numbered_lines_are_the_minority_is_not_a_toc_page(self):
        """正文里偶尔夹两行带页码的引用不算目录页：多数行要以数字结尾。"""
        page = _paragraph_page(
            3,
            "正文的第一段，讲极限的定义。\n正文的第二段，讲连续性。\n见定理 3.14 于 88\n"
            "见推论 2.7 于 91\n正文的第三段，收尾。",
        )

        assert toc_entries(page) == ()

    def test_a_heading_block_is_not_read_as_a_toc_page(self):
        """条目腿只吃段落块：有真标题的书走原路，本条腿零输出（`KA-33` 的边界）。"""
        page = BlocksPage(
            page=7,
            image_size=[1000, 1400],
            source="mineru",
            blocks=[
                Block(
                    type=BlockType.HEADING,
                    bbox=[0, 80, 900, 120],
                    index=0,
                    order=0,
                    text="第1章 集合论 …… 1\n第2章 实数 …… 49\n第3章 单调序 …… 73",
                    level=1,
                )
            ],
        )

        assert toc_entries(page) == ()

    def test_two_entries_glued_into_one_line_split_at_the_inner_page_number(self):
        """产物把两条条目黏成一行是常态：行内第二处「引导点＋页码」以那处为界拆开，只切分不改字。"""
        page = _paragraph_page(
            8, "第3章 单调序与级数 …… 69\n第4章 连续性 …… 73函数的极限 …… 79\n第5章 微分法 …… 99"
        )

        entries = toc_entries(page)

        assert [(e.name, e.printed_page, e.level) for e in entries] == [
            ("第3章 单调序与级数", 69, 1),
            ("第4章 连续性", 73, 1),
            ("函数的极限", 79, 2),
            ("第5章 微分法", 99, 1),
        ]

    def test_a_section_number_is_not_an_inner_boundary(self):
        """`2.10` 里那枚单句点不是页码边界：行内边界至少要两枚引导符（或带空白），否则名字当场被劈开。"""
        page = _paragraph_page(7, "2.10 一致连续 …… 3\n2.11 一致收敛 …… 5\n2.12 紧性 …… 9")

        assert [e.name for e in toc_entries(page)] == ["2.10 一致连续", "2.11 一致收敛", "2.12 紧性"]

    def test_a_pageless_line_still_loses_its_trailing_leaders(self):
        """行末引导点一律剔离，即使这一行没有页码（`KA-41`：清洗与「有没有回收出页码」无关）。"""
        page = _paragraph_page(7, "第1章 集合论 …… 1\n第2章 实数与极限 …… 49\n第3章 单调序与级数 ……")

        assert [(e.name, e.printed_page) for e in toc_entries(page)] == [
            ("第1章 集合论", 1),
            ("第2章 实数与极限", 49),
            ("第3章 单调序与级数", None),
        ]

    def test_the_organize_leg_gets_the_same_single_pass_of_cleaning(self):
        """`KA-41`：整理腿交来的行与条目腿共用同一把清洗——真机 b07 那 31 行的原形。"""
        assert clean_entry_rows(["§ 2. 有理函数的积分法 …… 150"]) == [("§ 2. 有理函数的积分法", 150)]

    def test_leaders_the_model_left_behind_are_stripped_without_a_page_to_recover(self):
        """模型把页码放进自己那一格、把引导点留在名字里：名字照样要洗干净，否则前缀命中永不成立。"""
        assert clean_entry_rows(["§ 2. 有理函数的积分法 ……"]) == [("§ 2. 有理函数的积分法", None)]

    def test_a_glued_model_row_splits_at_its_inner_page_number(self):
        """黏行拆分对模型行同样生效：只切分不改字，两段各带自己的印刷页码。"""
        assert clean_entry_rows(["第4章 连续性 …… 73函数的极限 …… 79"]) == [
            ("第4章 连续性", 73),
            ("函数的极限", 79),
        ]

    def test_a_pageless_fragment_joins_the_numbered_entry_it_belonged_to(self):
        """折行拼回是同一把清洗的第三步，模型行与产物行同一判据。"""
        assert clean_entry_rows(["第6章 RIEMANN-STIEI.", "TJES积分 …… 115"]) == [
            ("第6章 RIEMANN-STIEI. TJES积分", 115)
        ]

    def test_a_lone_trailing_period_is_part_of_the_name(self):
        """单枚句点不是引导符：`RIEMANN-STIEI.` 的名字末尾一概不碰（清洗只认引导点串）。"""
        assert clean_entry_rows(["第6章 RIEMANN-STIEI."]) == [("第6章 RIEMANN-STIEI.", None)]


class TestPrintOffsetAndLocating:
    """印刷页码永不进锚点：先用众数标定本书偏移，再逐条只认窗口内的命中。"""

    def _entries(self, *pairs: tuple[str, int | None]) -> tuple[TocEntry, ...]:
        return tuple(
            TocEntry(name=name, printed_page=printed, level=1, block_index=0)
            for name, printed in pairs
        )

    def test_the_offset_is_the_mode_of_hit_page_minus_printed_page(self):
        """并列时取较小偏移（确定读数），但正解靠众数取胜：44 出现三次、噪声各一次。"""
        candidates = [(1, 45), (49, 93), (73, 117), (5, 6), (9, 12)]

        assert estimate_print_offset(candidates) == 44

    def test_no_candidates_gives_no_offset(self):
        assert estimate_print_offset(()) is None

    def test_a_hit_inside_the_calibrated_window_becomes_the_anchor(self):
        toc_page = _paragraph_page(
            7, "第1章 集合论 …… 1\n第2章 实数与极限 …… 49\n第3章 单调序与级数 …… 73"
        )
        body_pages = (
            _paragraph_page(45, "第1章 集合论\n正文开始。", "一些正文。", "更多正文。"),
            _paragraph_page(93, "第2章 实数与极限\n正文开始。", "一些正文。", "更多正文。"),
            _paragraph_page(117, "第3章 单调序与级数\n正文开始。", "一些正文。", "更多正文。"),
        )

        located = locate_entries(toc_entries(toc_page), body_pages).entries

        assert [(item.page_no, item.located) for item in located] == [(45, True), (93, True), (117, True)]
        assert located[1].block_index == 0
        assert located[1].entry.name == "第2章 实数与极限"

    def test_an_earlier_echo_outside_the_window_is_not_accepted(self):
        """假锚比缺锚难发现：条目名在别处（偏移对不上的页）回声一次，不算命中。"""
        toc_page = _paragraph_page(
            7, "第1章 集合论 …… 1\n第2章 实数与极限 …… 49\n第3章 单调序与级数 …… 73"
        )
        body_pages = (
            # 偏移众数＝44；第 20 页那句「第2章 实数与极限」是前言里的回声（20−49≠44）。
            _paragraph_page(20, "第2章 实数与极限在前文已提及。", "一些正文。", "更多正文。"),
            _paragraph_page(45, "第1章 集合论\n正文开始。", "一些正文。", "更多正文。"),
            _paragraph_page(93, "第2章 实数与极限\n正文开始。", "一些正文。", "更多正文。"),
            _paragraph_page(117, "第3章 单调序与级数\n正文开始。", "一些正文。", "更多正文。"),
        )

        located = locate_entries(toc_entries(toc_page), body_pages).entries

        assert [item.page_no for item in located] == [45, 93, 117]
        assert all(item.located for item in located)

    def test_a_single_candidate_does_not_calibrate_an_offset(self):
        """一条候选定不出本书偏移：支持数不足时全部按未定位披露，不拿回声造假锚。"""
        assert estimate_print_offset([(1, 45)]) is None

    def test_a_hit_outside_every_window_is_disclosed_rather_than_invented(self):
        """只有回声、真页不在取证面里时：不造锚（`page_no=None`），由取证面落回目录页并进账面点名。"""
        toc_page = _paragraph_page(
            7, "第1章 集合论 …… 1\n第2章 实数与极限 …… 49\n第3章 单调序与级数 …… 73"
        )
        body_pages = (_paragraph_page(20, "第1章 集合论的回声。", "一些正文。", "更多正文。"),)

        located = locate_entries(toc_entries(toc_page), body_pages).entries

        assert [item.page_no for item in located] == [None, None, None]
        assert not any(item.located for item in located)

    def test_an_entry_without_a_printed_page_cannot_be_located(self):
        toc_page = _paragraph_page(
            7, "第1章 集合论 …… 1\n附录\n第2章 实数与极限 …… 49\n第3章 单调序 …… 73"
        )
        body_pages = (
            _paragraph_page(45, "第1章 集合论\n正文。", "一些正文。", "更多正文。"),
            _paragraph_page(93, "第2章 实数与极限\n正文。", "一些正文。", "更多正文。"),
            _paragraph_page(117, "第3章 单调序\n正文。", "一些正文。", "更多正文。"),
        )

        located = locate_entries(toc_entries(toc_page), body_pages).entries

        assert [(item.entry.name, item.located) for item in located] == [
            ("第1章 集合论", True),
            ("附录", False),
            ("第2章 实数与极限", True),
            ("第3章 单调序", True),
        ]

    def test_a_punctuation_variant_written_by_the_model_is_the_same_name(self):
        """`KA-41` 的比对口径：原文写 ASCII 句点、模型转写成中文句号时仍算同一个名字（真机 b07 第二层口径差）。"""
        entries = (
            TocEntry(name="第1章 集合论", printed_page=1, level=1, block_index=0),
            TocEntry(name="§ 9. 矩的计算法。质心的坐标", printed_page=2, level=2, block_index=0),
        )
        body_pages = (
            _paragraph_page(45, "第1章 集合论\n正文。"),
            _paragraph_page(46, "§ 9. 矩的计算法. 质心的坐标\n正文。"),
        )

        location = locate_entries(entries, body_pages)

        assert location.offset == 44
        assert [(item.page_no, item.located) for item in location.entries] == [(45, True), (46, True)]

    def test_numbering_is_never_folded_into_another_entry(self):
        """折标点只折闭集那两枚：`2.10` 与 `210` 必须是两个名字（编号是教材目录的身份）。"""
        entries = (
            TocEntry(name="2.10 一致连续", printed_page=1, level=2, block_index=0),
            TocEntry(name="2.11 一致收敛", printed_page=2, level=2, block_index=0),
            TocEntry(name="3 完备性", printed_page=3, level=1, block_index=0),
        )
        body_pages = (
            _paragraph_page(3, "210 一致连续\n正文。"),
            _paragraph_page(4, "2.11 一致收敛\n正文。"),
            _paragraph_page(5, "3 完备性\n正文。"),
        )

        located = locate_entries(entries, body_pages).entries

        assert [(item.entry.name, item.located) for item in located] == [
            ("2.10 一致连续", False),
            ("2.11 一致收敛", True),
            ("3 完备性", True),
        ]


class TestPageSampleAndIdentityBits:
    """`KA-37`／`KA-38`／`KA-39` 的三枚纯函数腿：判页样本的封顶、锚块指认、附件名判类。"""

    def test_the_sample_tags_block_types_and_skips_empty_blocks(self):
        """带类型标注是给模型「这页清一色段落还是带标题」的形状线索；空块不进样本。"""
        page = _page_of(
            3,
            _typed_block(0, "第1章 集合论 …… 1", 100, BlockType.PARAGRAPH),
            _typed_block(1, "   ", 140, BlockType.PARAGRAPH),
            Block(
                type=BlockType.HEADING,
                bbox=[72, 180, 1168, 220],
                index=2,
                order=2,
                text="1.1 数列极限",
                level=2,
            ),
        )

        assert page_sample_text(page) == "[paragraph] 第1章 集合论 …… 1\n[heading] 1.1 数列极限"

    def test_the_sample_clips_inside_the_budget_instead_of_dropping_the_page(self):
        """真机目录页一枚块就 500 字符级：整块丢掉会让样本空成串，判页腿等于什么都没看见。"""
        page = _page_of(
            7,
            *[
                _typed_block(
                    index, f"第{index + 1}章 实数与完备性 …… {index * 49 + 1}{'·' * 520}", 100 + 40 * index, BlockType.PARAGRAPH
                )
                for index in range(3)
            ],
        )

        sample = page_sample_text(page)

        assert sample, "空样本＝把这本书误判成「没有目录页」，整本被推回确定性条目腿"
        assert len(sample) <= TOC_PAGE_SAMPLE_MAX_CHARS
        assert sample.startswith("[paragraph] 第1章 实数与完备性")

    def test_the_two_input_caps_are_the_numbers_the_clause_names(self):
        """封顶值本身就是判据（`KA-37`）：改这两枚数就是改「目录是局部事实」这条前提，得动文档才动码。"""
        assert (TOC_PAGE_SCAN_LIMIT, TOC_PAGE_SAMPLE_MAX_CHARS) == (10, 400)

    def test_a_reference_name_maps_to_its_kind_and_the_order_decides_ties(self):
        """七类各认一枚，并列时按判序取更具体的一类（`附录 习题解答` 归附录）；认不出＝知识点行。"""
        assert reference_role_for_name("前言") == ("reference", "front_matter")
        assert reference_role_for_name("∈ 属于 —— 重要符号表") == ("reference", "symbol_table")
        assert reference_role_for_name("附录 习题解答") == ("reference", "appendix")
        assert reference_role_for_name("参考文献") == ("reference", "bibliography")
        assert reference_role_for_name("索引") == ("reference", "index")
        assert reference_role_for_name("后记") == ("reference", "back_matter")
        assert reference_role_for_name("习题") == ("reference", "exercise")
        assert reference_role_for_name("第3章 单调序与级数") == ("knowledge", "")
        assert reference_role_for_name("   ") == ("knowledge", "")

    def test_the_anchor_block_is_the_one_containing_the_name(self):
        """目录页里条目名在段落块内部，用包含而不是前缀：这里要的是「锚指到哪一块读得出来」。"""
        page = _page_of(
            7,
            _typed_block(0, "无关的一段。", 100, BlockType.PARAGRAPH),
            _typed_block(1, "序言与引言 …… 1\n第1章 集合论 …… 3", 140, BlockType.PARAGRAPH),
        )

        assert anchor_block_for_name(page, "第1章 集合论") == 1
        assert anchor_block_for_name(page, "正文里查不到的名字") == 0
        assert anchor_block_for_name(_page_of(8), "任意名字") == 0

