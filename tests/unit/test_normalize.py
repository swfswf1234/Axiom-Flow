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
    CONTENT_INPUT_BUDGET_CHARS,
    CONTENT_MERGE_FLOOR_CHARS,
    TOC_PAGE_SAMPLE_MAX_CHARS,
    TOC_PAGE_SCAN_LIMIT,
    BodyNameIndex,
    KindFlag,
    KindReviewRow,
    KindSplit,
    MaterialBlock,
    PointSection,
    TocEntry,
    anchor_block_for_name,
    chapter_material_blocks,
    clean_entry_rows,
    entry_level,
    estimate_print_offset,
    exercise_entries,
    exercise_item_entries,
    fold_numbered_sub_items,
    is_exercise_label,
    is_toc_marker_line,
    kind_review,
    locate_entries,
    normalize_entry_levels,
    page_full_text,
    page_sample_text,
    pick_in_window,
    plan_point_calls,
    reference_role_for_name,
    select_material_blocks,
    toc_entries,
    toc_entries_across_pages,
    toc_marker_pages,
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

    def test_bare_page_numbers_and_running_headers_never_join_an_entry(self):
        """碎行先于折行拼回被剔掉（W3）：光秃秃的页码与目录书眉留下就会粘进下一条名字。"""
        assert clean_entry_rows(["12", "目录", "第1章 集合论 …… 1"]) == [("第1章 集合论", 1)]
        assert clean_entry_rows(["目录（续）", "第2章 实数与极限 …… 49"]) == [("第2章 实数与极限", 49)]

    def test_a_leader_run_inside_a_name_folds_to_one_space(self):
        """名字内部的引导点串是排版而不是名字（`第1章……集合论`）：折成单个空格，两腿同一判据。"""
        assert clean_entry_rows(
            ["第1章……集合论 …… 1", "第2章····实数与极限 …… 49", "第3章....单调序与级数 …… 69"]
        ) == [
            ("第1章 集合论", 1),
            ("第2章 实数与极限", 49),
            ("第3章 单调序与级数", 69),
        ]

    def test_single_symbols_inside_a_name_are_left_alone(self):
        """反向判据（W3）：`2.10` 的句点、`ε-δ` 的连字符、`a·b` 的点号都是名字本身，单枚一律不动。"""
        assert clean_entry_rows(
            ["2.10 一致连续 …… 3", "ε-δ 语言 …… 4", "a·b 的运算 …… 5"]
        ) == [
            ("2.10 一致连续", 3),
            ("ε-δ 语言", 4),
            ("a·b 的运算", 5),
        ]


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

    def test_an_inline_formula_in_the_body_heading_still_matches_the_entry(self):
        """正文把目录里的 `n` 排成行内公式（`$n$`）：折过公式壳再比前缀（真机 b12 第 161 条）。

        编号里的点不参与折叠——`2.10` 与 `210` 的边界由折後仍保留的句点守住。
        """
        entries = (
            TocEntry(name="第1章 多元函数", printed_page=1, level=1, block_index=0),
            TocEntry(name="第2章 极限", printed_page=2, level=1, block_index=0),
            TocEntry(name="161. n 维算术空间", printed_page=293, level=2, block_index=0),
        )
        body_pages = (
            _paragraph_page(45, "第1章 多元函数\n正文。"),
            _paragraph_page(46, "第2章 极限\n正文。"),
            _paragraph_page(337, "161. $n$ 维算术空间 转移到 $n$ 个自变量的函数。"),
        )

        located = locate_entries(entries, body_pages).entries

        assert [(item.page_no, item.located) for item in located] == [(45, True), (46, True), (337, True)]

    def test_a_number_the_toc_lost_inside_a_formula_still_lands(self):
        """目录把编号排进公式、OCR 连编号一起吃掉时，正文标题仍带 `150.`：剥正文引导编号再比。

        窗口外的回声照旧不算（第二条正文在偏移对不上的页），假锚比缺锚难发现。
        """
        entries = (
            TocEntry(name="第1章 多元函数", printed_page=1, level=1, block_index=0),
            TocEntry(name="第2章 极限", printed_page=2, level=1, block_index=0),
            TocEntry(name="$\\frac{0}{0}$ 型不定式", printed_page=266, level=2, block_index=0),
        )
        body_pages = (
            _paragraph_page(45, "第1章 多元函数\n正文。"),
            _paragraph_page(46, "第2章 极限\n正文。"),
            _paragraph_page(20, "150. $\\frac{0}{0}$ 型不定式在别处的回声。"),
            _paragraph_page(310, "150. $\\frac{0}{0}$ 型不定式 我们现在应用导数概念来定不定式的值。"),
        )

        located = locate_entries(entries, body_pages).entries

        assert located[2].located
        assert located[2].page_no == 310


class TestTolerantBodyMatch:
    """`KA-49`／`KA-50`：第一遍前缀查不中的条目再过一趟容错比对，名字取正文那一行。

    四形命中各取真机在册的一枚脏锚形状（b05 的 `$e$`／`\\Gamma`／`\\mathscr{L}^2`／OCR 断词与 b07 的
    标题折两块），四形不认是它的四条边界＝短名没有鉴别力、窗口外的回声是假锚、家具块不是标题行、
    前缀是段落起头而不是整行同形。名字腿只吃「条目名＋正文块序列」，不读偏移也不读窗口（`KA-50`）。
    """

    def test_a_math_delimiter_folds_to_the_same_name(self):
        """`数 e`（目录）↔ `数 $e$`（正文）：定界符只是排版壳（真机 `afkp-f3bdab5f3d02` 那一形）。"""
        index = BodyNameIndex([_paragraph_page(62, "数 $e$", "正文。")])

        readings = index.find("数 e")

        assert [(item.name, item.page_no, item.block_index) for item in readings] == [("数 $e$", 62, 0)]

    def test_a_greek_macro_name_folds_to_its_letter(self):
        """目录把 `$\\Gamma$ 函数` 抄成 `Γ函数`：宏名折其字符后同串（真机 `afkp-1c8c7a0bed99`）。"""
        index = BodyNameIndex([_paragraph_page(181, "一些正文。", "$\\Gamma$ 函数", "正文。")])

        readings = index.find("Γ函数")

        assert [(item.name, item.page_no, item.block_index) for item in readings] == [
            ("$\\Gamma$ 函数", 181, 1)
        ]

    def test_two_macro_shells_around_the_same_letter_fold_together(self):
        """`\\mathscr{L}^2` 与 `\\mathcal{L}^2` 是同一枚符号的两种写法（真机 `afkp-6e7b1e3f29a0`）。"""
        index = BodyNameIndex([_paragraph_page(305, "$\\mathcal{L}^2$ 类的函数", "正文。")])

        readings = index.find("$\\mathscr{L}^2$ 类的函数")

        assert [item.name for item in readings] == ["$\\mathcal{L}^2$ 类的函数"]

    def test_a_letter_dropped_by_ocr_still_matches_the_body_line(self):
        """断词掉字那一形（真机 `afkp-cee53be7edef`）：`STIEI.` 与 `STIEL` 差一枚字符，近似腿认、前缀腿不认。"""
        index = BodyNameIndex([_paragraph_page(115, "第 6 章 RIEMANN-STIELTJES 积分", "正文。")])

        readings = index.find("第6章 RIEMANN-STIEI. TJES积分")

        assert [item.name for item in readings] == ["第 6 章 RIEMANN-STIELTJES 积分"]

    def test_a_title_split_across_two_blocks_is_one_candidate(self):
        """正文标题折成两块是产物常态（真机 b07 `afkp-3b9b932264e6`）：锚取第一块，名取两块以一枚空格相连。"""
        index = BodyNameIndex(
            [_paragraph_page(93, "§ 2. 反函数的导数. 用参数形式给出的函数的导数.", "隐函数的导数", "正文。")]
        )

        readings = index.find("§ 2. 反函数的导数。用参数形式给出的函数的导数.隐函数的导数")

        assert [(item.name, item.page_no, item.block_index) for item in readings] == [
            ("§ 2. 反函数的导数. 用参数形式给出的函数的导数. 隐函数的导数", 93, 0)
        ]

    def test_candidates_come_back_ordered_by_page_then_block(self):
        """候选序列的序就是「最早一枚」的判据（`KA-50`）：名字按 `(页号, 块序)` 取，不靠插入序。"""
        index = BodyNameIndex(
            [
                _paragraph_page(50, "级数收敛判别", "正文。"),
                _paragraph_page(12, "正文。", "级数收敛判别"),
            ]
        )

        readings = index.find("级数收敛判别")

        assert [(item.page_no, item.block_index) for item in readings] == [(12, 1), (50, 0)]

    def test_a_short_name_is_never_matched_approximately(self):
        """`习题`／`数 e` 这一类短名没有鉴别力：近似腿有长度下限，短名只走整行同形。"""
        index = BodyNameIndex([_paragraph_page(30, "习题选登")])

        assert index.find("习题") == ()

    def test_a_prefix_is_not_a_whole_line_match(self):
        """前缀是正文段落起头的形状：`函数的极限` 不得认 `函数的极限与连续` 那一行（否则题干会被抄成名字）。"""
        index = BodyNameIndex([_paragraph_page(79, "函数的极限与连续")])

        assert index.find("函数的极限") == ()

    def test_an_index_line_is_never_a_title_candidate(self):
        """索引行 `阿基米德公理, 5` 是「名字＋页码」而不是正文标题（真机 b12）：容错腿认了它，
        `KA-50` 就把目录行 `5. 阿基米德公理` 换成索引行原文，行名成了「名字, 页码」。"""
        index = BodyNameIndex([_paragraph_page(531, "阿基米德公理, 5")])

        assert index.find("5. 阿基米德公理") == ()

    def test_a_furniture_block_never_enters_the_candidates(self):
        """判成页眉／页脚／页码的块不是标题行：家具块里撞见的同名行是书眉回声，救回来就是假锚。"""
        page = BlocksPage(
            page=62,
            image_size=[1000, 1400],
            source="mineru",
            blocks=[
                _typed_block(0, "数 $e$", 58, BlockType.HEADER),
                _typed_block(1, "正文。", 300, BlockType.PARAGRAPH),
            ],
        )

        assert BodyNameIndex([page]).find("数 e") == ()

    def test_only_the_first_line_of_a_block_is_a_title_candidate(self):
        """首行才是标题行的形状：块中间那一行是正文句子，不是这一节的题目。"""
        index = BodyNameIndex([_paragraph_page(62, "正文开头。\n数 $e$\n正文结尾。")])

        assert index.find("数 e") == ()

    def test_the_earliest_candidate_inside_the_window_is_the_anchor(self):
        """命中仍只接受 `印刷页＋偏移 ± slack` 窗口内的最早一枚（`KA-49` 的位置腿）：回声页一律不认。"""
        index = BodyNameIndex(
            [
                _paragraph_page(20, "数 $e$"),
                _paragraph_page(62, "一些正文。", "数 $e$", "正文。"),
                _paragraph_page(160, "数 $e$"),
            ]
        )

        readings = index.find("数 e")

        assert [(item.page_no, item.block_index) for item in readings] == [(20, 0), (62, 1), (160, 0)]
        assert pick_in_window(readings, printed_page=54, offset=8) == readings[1]
        assert pick_in_window(readings, printed_page=54, offset=8, slack=0) == readings[1]
        assert pick_in_window(readings, printed_page=152, offset=8) == readings[2]
        assert pick_in_window(readings, printed_page=30, offset=8) is None

    def test_no_offset_and_no_printed_page_give_no_anchor(self):
        """偏移标定不出、或条目行末没有印刷页码时不造锚——与 `KA-33` 的第一遍判据同形。"""
        index = BodyNameIndex([_paragraph_page(62, "数 $e$")])
        readings = index.find("数 e")

        assert pick_in_window(readings, printed_page=54, offset=None) is None
        assert pick_in_window(readings, printed_page=None, offset=8) is None

    def test_the_first_pass_readings_are_not_changed_by_the_tolerant_leg(self):
        """容错腿是第二遍：`locate_entries` 的偏移标定与逐条读数一字不动（页级复核因此也不会被拖走）。"""
        toc_page = _paragraph_page(
            7, "第1章 集合论 …… 1\n第2章 实数 …… 49\n数 e …… 54\nΓ函数 …… 173"
        )
        body_pages = (
            _paragraph_page(45, "第1章 集合论\n正文。", "更多正文。", "正文。"),
            _paragraph_page(93, "第2章 实数\n正文。", "更多正文。", "正文。"),
            _paragraph_page(62, "数 $e$", "正文。", "正文。"),
            _paragraph_page(181, "$\\Gamma$ 函数", "正文。", "正文。"),
        )

        location = locate_entries(toc_entries(toc_page), body_pages)

        assert location.offset == 44, "偏移只由第一遍标定：容错才会命中的 62／181 两页不进众数"
        assert [(item.page_no, item.located) for item in location.entries] == [
            (45, True),
            (93, True),
            (None, False),
            (None, False),
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

    def test_the_toc_marker_is_a_standalone_line_not_a_sentence(self):
        """P-1：只认独立成行的「目录／Contents」（含续页后缀），正文句子里提到的不算。"""
        assert is_toc_marker_line("目录")
        assert is_toc_marker_line("  目 录  ")
        assert is_toc_marker_line("目录（续）")
        assert is_toc_marker_line("Contents")
        assert is_toc_marker_line("TABLE OF CONTENTS")
        assert not is_toc_marker_line("本书目录如下")
        assert not is_toc_marker_line("1. 目录")
        assert not is_toc_marker_line("")

    def test_marker_candidates_prefer_the_first_ten_pages_and_scan_the_book_otherwise(self):
        """P-1：前 10 页有标记就只认这几页；前 10 页没有才按页序扫全书。"""
        pages = [
            _page_of(
                page_no,
                _typed_block(0, "目录" if page_no in {2, 12} else "正文", 100, BlockType.PARAGRAPH),
            )
            for page_no in range(1, 14)
        ]

        assert toc_marker_pages(pages) == (2,)
        pages[1] = _page_of(2, _typed_block(0, "正文", 100, BlockType.PARAGRAPH))
        assert toc_marker_pages(pages) == (12,)

    def test_the_full_page_text_is_not_truncated(self):
        """P-2：判页看整页，不与 `page_sample_text` 的 400 字封顶共用。"""
        long = "第1章 实数与完备性 …… " + "·" * 900
        page = _page_of(5, _typed_block(0, long, 100, BlockType.PARAGRAPH))

        assert page_full_text(page) == f"[paragraph] {long}"
        assert len(page_full_text(page)) > TOC_PAGE_SAMPLE_MAX_CHARS

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
        # 编号起头的行是正文节而不是书前件（b12 `1. 前言`／b13 `§1. 引言` 真机各一枚）。
        assert reference_role_for_name("1. 前言") == ("knowledge", "")
        assert reference_role_for_name("§1. 引言") == ("knowledge", "")

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


class TestExerciseLabels:
    """`KA-46`：章末习题段的确定性识别——整块短标签、编号可选，带解答／答案／提示的另成一类。"""

    def test_the_four_label_shapes_are_recognised_with_an_optional_number(self):
        """中英各两枚；编号认书里真在用的三种写法（`1.1`、`1-3`、`三`），全角数字按 NFKC 折过也算。"""
        for text in (
            "习题",
            "习 题",
            "习题 1.1",
            "习题1-3",
            "习题 三",
            "练习",
            "练习2.3",
            "Exercises",
            "Exercises 1.1",
            "Problems",
            "Problems 3",
            "  习题  ",
            "习题１.１",
        ):
            assert is_exercise_label(text), text

    def test_live_text_and_answer_labels_are_not_exercise_labels(self):
        """整块判：带题干的不是标签；`习题解答`／`习题提示`／`练习答案` 是答案侧，不成习题行。"""
        for text in (
            "习题 1.1 求下列函数的极限。",
            "习题解答",
            "习题提示",
            "练习答案",
            "本章习题",
            "第一章 习题课",
            "",
            "   ",
        ):
            assert not is_exercise_label(text), text

    def test_a_page_yields_its_exercise_blocks_with_their_block_indexes(self):
        """一页里命中的块各成一枚、按块序给出：锚点要指回它自己那一块，不能全落到页首。"""
        page = _paragraph_page(12, "上一节正文的结尾。", "习题 1.1", "1. 求极限 ……", "练习")

        entries = exercise_entries(page)

        assert [(entry.name, entry.block_index) for entry in entries] == [
            ("习题 1.1", 1),
            ("练习", 3),
        ]

    def test_a_page_without_exercise_blocks_yields_nothing(self):
        """认不出就是零枚：本腿不猜「这一段看起来像习题」。"""
        assert exercise_entries(_paragraph_page(12, "第 1 章 极限", "1.1 数列极限")) == ()


class TestExerciseItemEntries:
    """`KA-53`：一行行首的题号闭集与同父单调序——题号是书自己写的，只做空白与全半角折叠。"""

    def test_the_closed_set_forms_keep_the_printed_number(self):
        """六种在册形状各认一枚：题号按书里的写法回给人看，单调序只另记一枚数字键。"""
        entries = exercise_item_entries(
            [
                "【5】设 $a^{[n]} = a(a - h)$",
                "6. 证明伯努利不等式。",
                "7、设 $c$ 为正整数。",
                "(8) 若 $m$ 为偶数。",
                "（9）同理，容易得知。",
                "10-12 证明下列不等式。",
            ]
        )

        assert [(entry.index, entry.item_no, entry.order_key) for entry in entries] == [
            (0, "【5】", 5),
            (1, "6.", 6),
            (2, "7、", 7),
            (3, "(8)", 8),
            (4, "(9)", 9),
            (5, "10-12", 10),
        ]

    def test_a_number_that_does_not_move_forward_is_body_text(self):
        """小问的 (1)(2) 从 1 重新起算、重复题号也回退：只有严格大于上一枚已接受的才开新题。"""
        entries = exercise_item_entries(
            [
                "【5】题面。",
                "(1) 小问重新从 1 起。",
                "(2) 另一枚小问。",
                "【5】重复题号。",
                "【6】下一题。",
            ]
        )

        assert [(entry.index, entry.item_no) for entry in entries] == [(0, "【5】"), (4, "【6】")]

    def test_lines_without_the_closed_shape_yield_nothing(self):
        """章名、年份、小数与空白都不是题号：认不出＝零行，不猜、不回落模型（`KA-53`）。"""
        for line in ("1 分析引论", "2017年，公司与上海华谊公司签署协议。", "3.14 是圆周率", "第 1 章 极限", "", "   "):
            assert exercise_item_entries([line]) == (), line



class TestTocStream:
    """`KA-57` 整段目录抄录：目录页原文按「行内页码标记」切成一条流，页码只由原文给。

    与 `toc_entries` 的分工：那一把按**行末**解析一页、用来兜底认页；这一把吃判页腿已经认下的
    整段目录页，允许一页多块、一行多条目、跨页续行——`微积分学教程` 的目录正是这种「(页码) 连排」形状。
    """

    def test_paren_form_entries_run_together_on_one_line_are_split_at_each_page(self):
        """一串条目挤在一行（`1. 前言 (1) 2. 有理数域的序 (2)`）以每枚 `(页码)` 为界拆开。"""
        page = _paragraph_page(9, "1. 前言 (1) 2. 有理数域的序 (2) 3. 有理数的加法及减法 (2)")

        rows = toc_entries_across_pages([page])

        assert [(page_no, entry.name, entry.printed_page) for page_no, entry in rows] == [
            (9, "1. 前言", 1),
            (9, "2. 有理数域的序", 2),
            (9, "3. 有理数的加法及减法", 2),
        ]

    def test_a_leader_form_and_the_paren_form_interleave_on_the_same_line(self):
        """同一行里两种页码写法并存：引导点连排之后紧跟括号页码，两种都当边界。"""
        page = _paragraph_page(
            10,
            r"§4. 不定式的定值法....266 $\frac{0}{0}$ 型不定式 (266) $\frac{\infty}{\infty}$ 型不定式 (271)",
        )

        rows = toc_entries_across_pages([page])

        assert [(entry.name, entry.printed_page) for _, entry in rows] == [
            ("§4. 不定式的定值法", 266),
            (r"$\frac{0}{0}$ 型不定式", 266),
            (r"$\frac{\infty}{\infty}$ 型不定式", 271),
        ]

    def test_a_cross_page_continuation_joins_the_fragment_from_the_previous_page(self):
        """一条条目被页边界切开（`33. … 定理` ／ `及其应用 (50)`）：前半截与带页码的后半截拼回一条。"""
        page8 = _paragraph_page(8, "33. 斯托尔茨 (O.Stolz) 定理")
        page9 = _paragraph_page(9, "及其应用 (50)\n§3. 单调整序变量.... 53")

        rows = toc_entries_across_pages([page8, page9])

        assert [(page_no, entry.name, entry.printed_page) for page_no, entry in rows] == [
            (9, "33. 斯托尔茨 (O.Stolz) 定理 及其应用", 50),
            (9, "§3. 单调整序变量", 53),
        ]

    def test_a_leading_page_number_closes_the_previous_page_s_last_entry(self):
        """续页以一枚孤立页码起头（`(162) 96. …`）：这枚页码属于上一页最后一条无页码的条目。"""
        page9 = _paragraph_page(9, "94. 反函数的导数 (160)\n95. 导数公式一览表")
        page10 = _paragraph_page(10, "(162) 96. 函数的增量的公式 (162)")

        rows = toc_entries_across_pages([page9, page10])

        assert [(page_no, entry.name, entry.printed_page) for page_no, entry in rows] == [
            (9, "94. 反函数的导数", 160),
            (10, "95. 导数公式一览表", 162),
            (10, "96. 函数的增量的公式", 162),
        ]

    def test_a_page_less_tail_does_not_swallow_the_next_numbered_entry(self):
        """残尾（OCR 把两条页码吃成公式）没有页码、下一行又是编号起头：残尾弃用，不得并进下一枚条目。"""
        page10 = _paragraph_page(
            10,
            r"$\frac{\infty}{\infty}$ 比例法则 (弦线法) $\frac{\infty}{\infty}$ 牛顿法则 (切线法) $\frac{\infty}{\infty}$",
        )
        page11 = _paragraph_page(11, "156. 例题及习题 (281)")

        rows = toc_entries_across_pages([page10, page11])

        assert [(entry.name, entry.printed_page) for _, entry in rows] == [("156. 例题及习题", 281)]

    def test_running_headers_and_bare_page_numbers_are_not_entries(self):
        """目录页的书眉与页脚不是条目：`目录` 与光秃秃的一枚数字整行跳过（`KA-57`）。"""
        page = _paragraph_page(9, "目录\n9\n1. 前言 (1)")

        rows = toc_entries_across_pages([page])

        assert [(entry.name, entry.printed_page) for _, entry in rows] == [("1. 前言", 1)]

    def test_a_continuation_header_is_furniture_too(self):
        """续页书眉（`目录（续）`）与首页标记同一闭集：整行跳过，不并进任何条目。"""
        page = _paragraph_page(9, "目录（续）\n1. 前言 (1)")

        rows = toc_entries_across_pages([page])

        assert [(entry.name, entry.printed_page) for _, entry in rows] == [("1. 前言", 1)]

    def test_an_inner_leader_run_folds_but_single_symbols_survive(self):
        """整段抄录腿与逐页腿同一把名字清洗（W3）：内部引导点串折成空格，单枚句点与连字符不动。"""
        page = _paragraph_page(9, "第1章……集合论 .... 1\n2.10 一致连续 (88)\nε-δ 语言 (90)")

        rows = toc_entries_across_pages([page])

        assert [entry.name for _, entry in rows] == ["第1章 集合论", "2.10 一致连续", "ε-δ 语言"]

    def test_full_width_parens_are_read_as_the_same_marker(self):
        """全角括号与全角数字先经 NFKC：`（１２）` 与 `(12)` 是同一个页码标记。"""
        page = _paragraph_page(9, "（１２）第 3 章 极限（３）")

        rows = toc_entries_across_pages([page])

        assert [(entry.name, entry.printed_page) for _, entry in rows] == [("第 3 章 极限", 3)]

    def test_a_dotted_section_number_is_not_a_page_marker(self):
        """`2.10`／`3.14` 里的单句点不是引导符：切开它们等于把节号劈成页码。"""
        page = _paragraph_page(9, "2.10 一致连续 (88)\n3.14 圆周率 (91)")

        rows = toc_entries_across_pages([page])

        assert [entry.name for _, entry in rows] == ["2.10 一致连续", "3.14 圆周率"]

    def test_explicit_chapters_demote_every_other_row_to_sections(self):
        """本书有明写的章（`第N章`／`四章`／`绪论`）时，`1. 前言` 这类单级编号只是节，不是章。"""
        page = _paragraph_page(
            9,
            "绪论 实数....1\n1. 前言 (1)\n第一章 极限论....28\n22. 变量、整序变量 (28)\n"
            "四章 利用导数研究函数....226\n131. 函数为常数的条件 (226)",
        )

        rows = toc_entries_across_pages([page])

        assert [(entry.name, entry.level) for _, entry in rows] == [
            ("绪论 实数", 1),
            ("1. 前言", 2),
            ("第一章 极限论", 1),
            ("22. 变量、整序变量", 2),
            ("四章 利用导数研究函数", 1),
            ("131. 函数为常数的条件", 2),
        ]

    def test_a_reference_group_in_the_toc_stays_a_top_level_row(self):
        """目录自己列出的书尾件（`附录`／`索引`／`校订后记`）是顶级行，不挂在最后一章底下。"""
        page = _paragraph_page(
            12,
            "第七章 微分学在几何上的应用 ..... 436\n附录 函数扩充的问题 ..... 508\n索引 ..... 519\n校订后记 ..... 525",
        )

        rows = toc_entries_across_pages([page])

        assert [(entry.name, entry.level, reference_role_for_name(entry.name)[0]) for _, entry in rows] == [
            ("第七章 微分学在几何上的应用", 1, "knowledge"),
            ("附录 函数扩充的问题", 1, "reference"),
            ("索引", 1, "reference"),
            ("校订后记", 1, "reference"),
        ]

    def test_a_book_without_explicit_chapters_keeps_the_line_shape(self):
        """没有明写章号的书（`1. 引言` 就是章）：不替它降级，层级仍由行形状给。"""
        page = _paragraph_page(3, "1. 引言 (1)\n1.1 序的关系 (3)\n3 完备性 (9)")

        rows = toc_entries_across_pages([page])

        assert [entry.level for _, entry in rows] == [1, 2, 1]


class TestNormalizeEntryLevels:
    """书级层级归一：明写章号存在时，其余行一律是节；书目附件仍取顶级。"""

    def _entries(self, *names: str) -> list[TocEntry]:
        return [
            TocEntry(name=name, printed_page=index + 1, level=entry_level(name), block_index=0)
            for index, name in enumerate(names)
        ]

    def test_numbered_rows_are_demoted_when_an_explicit_chapter_exists(self):
        rows = normalize_entry_levels(self._entries("第1章 极限论", "1. 前言", "1.1 序"))

        assert [row.level for row in rows] == [1, 2, 2]

    def test_a_section_row_is_not_promoted_by_the_chapter_shape(self):
        """`第一节` 明写的是节，不是章：归一不会把 `第N节` 抬成顶级行。"""
        rows = normalize_entry_levels(self._entries("第1章 极限论", "第一节 集合"))

        assert [row.level for row in rows] == [1, 2]

    def test_a_numbered_section_named_like_front_matter_stays_a_section(self):
        """`§1. 引言` 是章里的第一节（b13 真机）：名字里的「引言」不得把它抬成顶级行。"""
        rows = normalize_entry_levels(self._entries("第十一章 常数项无穷级数", "§1. 引言"))

        assert [row.level for row in rows] == [1, 2]

    def test_without_an_explicit_chapter_the_shape_is_left_alone(self):
        rows = normalize_entry_levels(self._entries("1. 引言", "1.1 序的关系"))

        assert [row.level for row in rows] == [1, 2]


class TestFoldNumberedSubItems:
    """`KA-62`：`§` 节下的纯编号条目是三级细目——二级只有一节时，细目不许把它切成半截。"""

    @staticmethod
    def _entries(*specs: tuple[int, str]) -> list[TocEntry]:
        return [
            TocEntry(name=name, printed_page=index + 1, level=level, block_index=0)
            for index, (level, name) in enumerate(specs)
        ]

    def test_plain_numbered_rows_under_a_section_fold_to_level_three(self):
        rows = fold_numbered_sub_items(
            self._entries(
                (1, "第一章 实数"),
                (2, "§ 1. 极限"),
                (2, "34. 单调整序变量"),
                (2, "35. 区间套"),
                (2, "§ 2. 连续"),
                (2, "1. 连续的定义"),
            )
        )

        assert [row.level for row in rows] == [1, 2, 3, 3, 2, 3]
        assert [row.name for row in rows][2:4] == ["34. 单调整序变量", "35. 区间套"], "只降级不丢行"

    def test_a_plain_numbered_row_without_a_section_stays_a_section(self):
        rows = fold_numbered_sub_items(self._entries((2, "1. 前言"), (2, "2. 记号")))

        assert [row.level for row in rows] == [2, 2], "没有 `§` 节的书一条也不动"

    def test_a_dotted_subsection_number_is_not_a_plain_item_number(self):
        rows = fold_numbered_sub_items(self._entries((2, "§ 1. 极限"), (2, "1.1 数列极限")))

        assert [row.level for row in rows] == [2, 2]

    def test_a_mid_section_non_numbered_row_closes_the_fold(self):
        rows = fold_numbered_sub_items(
            self._entries((2, "§ 1. 极限"), (2, "34. 变量"), (2, "习题"), (2, "35. 区间套"))
        )

        assert [row.level for row in rows] == [2, 3, 2, 2]

    def test_an_explicit_chapter_resets_the_fold(self):
        rows = fold_numbered_sub_items(
            self._entries((1, "第一章"), (2, "§ 1. 极限"), (2, "34. 变量"), (1, "第二章"), (2, "35. 区间套"))
        )

        assert [row.level for row in rows] == [1, 2, 3, 1, 2]

    def test_a_model_reported_level_three_passes_through(self):
        rows = fold_numbered_sub_items(self._entries((2, "§ 1. 极限"), (3, "34. 变量")))

        assert [row.level for row in rows] == [2, 3]


def _body_block(
    index: int,
    text: str = "",
    *,
    kind: BlockType = BlockType.PARAGRAPH,
    level: int | None = None,
) -> Block:
    extra = {"level": level} if kind is BlockType.HEADING else {}
    return Block(
        type=kind,
        bbox=[72, 40 * index, 1168, 40 * index + 36],
        index=index,
        order=index,
        text=text or None,
        **extra,
    )


class TestKindReview:
    """`KA-63`：`content` 开跑前的种类审校——行名是习题标签则点名，段内标签块拆成并列习题行。"""

    @staticmethod
    def _row(
        node_id: str,
        name: str,
        *,
        node_type: str = "point",
        parent_id: str | None = "c1",
        page_start: int = 10,
        page_end: int = 12,
    ) -> KindReviewRow:
        return KindReviewRow(
            node_id=node_id,
            parent_id=parent_id,
            name=name,
            node_type=node_type,
            page_start=page_start,
            page_end=page_end,
        )

    def test_a_label_block_inside_a_point_span_splits_under_its_chapter(self):
        """b18 形：节的行区间里挂着一枚整块「习题 1.1」——拆成章下的并列行，锚回那一块。"""
        rows = [self._row("p1", "1.1 极限", page_start=10, page_end=10)]
        pages = [
            _page_of(
                10,
                _body_block(0, "1.1 极限"),
                _body_block(1, "习题 1.1"),
            )
        ]

        review = kind_review(rows, pages)

        assert review.flags == ()
        assert review.splits == (
            KindSplit(parent_id="c1", name="习题 1.1", page=10, block_index=1),
        )

    def test_a_row_named_like_a_label_is_flagged_and_not_recreated(self):
        """① 只披露不改写：行名是整块习题标签时不发点腿，也不把自己的锚块再拆一行。"""
        rows = [self._row("p1", "习题 1.1", page_start=10, page_end=10)]
        pages = [_page_of(10, _body_block(0, "习题 1.1"))]

        review = kind_review(rows, pages)

        assert review.flags == (KindFlag(node_id="p1", reason="exercise_label"),)
        assert review.splits == ()

    def test_a_label_already_carried_by_another_row_is_not_duplicated(self):
        """已在册的行不再造：章区间扫到的那一枚标签，正是另一枚知识点行的锚点时跳过。"""
        rows = [
            self._row("c1", "第一章 极限", node_type="chapter", parent_id=None, page_start=10, page_end=12),
            self._row("p1", "习题", page_start=10, page_end=10),
        ]
        pages = [_page_of(10, _body_block(0, "习题"))]

        review = kind_review(rows, pages)

        assert [flag.node_id for flag in review.flags] == ["p1"]
        assert review.splits == ()

    def test_chapter_and_point_spans_yield_one_split_per_label(self):
        """章区间与点的区间重叠：同一枚标签只出一行（父＝章、按锚页去重）。"""
        rows = [
            self._row("c1", "第一章 极限", node_type="chapter", parent_id=None, page_start=10, page_end=12),
            self._row("p1", "1.1 极限", page_start=10, page_end=11),
        ]
        pages = [_page_of(11, _body_block(0, "练习"))]

        review = kind_review(rows, pages)

        assert review.splits == (KindSplit(parent_id="c1", name="练习", page=11, block_index=0),)

    def test_a_label_after_the_last_point_still_belongs_to_the_chapter(self):
        """章尾习题不在任何点的区间里，也按章拆出。"""
        rows = [
            self._row("c1", "第一章 极限", node_type="chapter", parent_id=None, page_start=10, page_end=12),
            self._row("p1", "1.1 极限", page_start=10, page_end=10),
        ]
        pages = [_page_of(12, _body_block(0, "习题"))]

        review = kind_review(rows, pages)

        assert review.splits == (KindSplit(parent_id="c1", name="习题", page=12, block_index=0),)

    def test_reference_rows_are_not_audited(self):
        """附录／索引这类行不归种类审校管：区间里的标签不因它们成行，也不点名。"""
        rows = [self._row("o1", "附录", node_type="other", parent_id=None, page_start=10, page_end=12)]
        pages = [_page_of(11, _body_block(0, "习题"))]

        review = kind_review(rows, pages)

        assert review.flags == ()
        assert review.splits == ()

    def test_the_audited_row_count_is_reported_even_when_nothing_happens(self):
        rows = [self._row("p1", "1.1 极限", page_start=10, page_end=10)]
        pages = [_page_of(10, _body_block(0, "1.1 极限"))]

        review = kind_review(rows, pages)

        assert review.rows == 1


class TestSelectMaterialBlocks:
    """`KA-64`：点腿材料＝块选择——页序＋块序、只收正文块、家具零入、按块渲染。"""

    def test_blocks_are_ordered_by_page_then_index_and_furniture_is_dropped(self):
        pages = [
            _page_of(
                12,
                _body_block(1, "第二段"),
                _body_block(0, "第一节"),
            ),
            _page_of(
                11,
                _body_block(0, "页眉", kind=BlockType.HEADER),
                _body_block(1, "第一段"),
                _body_block(2, "1", kind=BlockType.PAGE_NUMBER),
                _body_block(3, "页脚", kind=BlockType.FOOTER),
            ),
        ]

        blocks = select_material_blocks(pages, page_start=11, page_end=12)

        assert [(block.page, block.block_index, block.text) for block in blocks] == [
            (11, 1, "第一段"),
            (12, 0, "第一节"),
            (12, 1, "第二段"),
        ]

    def test_pages_outside_the_window_and_excluded_pages_contribute_nothing(self):
        pages = [
            _page_of(9, _body_block(0, "目录回声")),
            _page_of(10, _body_block(0, "正文")),
            _page_of(11, _body_block(0, "续页")),
        ]

        blocks = select_material_blocks(
            pages, page_start=9, page_end=11, exclude_pages=(9, 11)
        )

        assert [block.text for block in blocks] == ["正文"]

    def test_each_block_renders_through_the_page_renderer(self):
        """块按页级渲染器的同一口径出文：公式带定界符、表原样 HTML、图给路径与题注。"""
        pages = [
            _page_of(
                10,
                _body_block(0, "标题", kind=BlockType.HEADING, level=2),
                Block(
                    type=BlockType.FORMULA,
                    bbox=[72, 40, 1168, 100],
                    index=1,
                    order=1,
                    latex="x^2",
                    display=True,
                ),
                Block(
                    type=BlockType.TABLE,
                    bbox=[72, 110, 1168, 200],
                    index=2,
                    order=2,
                    html="<table><tr><td>1</td></tr></table>",
                ),
                Block(
                    type=BlockType.IMAGE,
                    bbox=[72, 210, 1168, 300],
                    index=3,
                    order=3,
                    path="images/p10-1.png",
                    caption="图 1",
                ),
                Block(
                    type=BlockType.LIST,
                    bbox=[72, 310, 1168, 340],
                    index=4,
                    order=4,
                    items=["甲", "乙"],
                ),
            )
        ]

        blocks = select_material_blocks(pages, page_start=10, page_end=10)

        assert [block.text for block in blocks] == [
            "## 标题",
            "$$\nx^2\n$$",
            "<table><tr><td>1</td></tr></table>",
            "![图 1](images/p10-1.png)",
            "- 甲\n- 乙",
        ]
        assert [block.chars for block in blocks] == [len(block.text) for block in blocks]

    def test_a_block_type_is_reported_for_review(self):
        pages = [_page_of(10, _body_block(0, "正文"))]

        blocks = select_material_blocks(pages, page_start=10, page_end=10)

        assert blocks[0].block_type == "paragraph"

    def test_block_bounds_cut_the_two_ends_of_the_window(self):
        """真机 b05：本节止于下一节标题块之前——首末两页按锚点块号裁，中间页整页收。"""
        pages = [
            _page_of(9, _body_block(0, "上一节尾"), _body_block(1, "导引标题"), _body_block(2, "导引正文")),
            _page_of(10, _body_block(0, "导引续"), _body_block(13, "导引末"), _body_block(14, "有序集标题")),
        ]

        blocks = select_material_blocks(
            pages, page_start=9, page_end=10, start_block=1, end_block=13
        )

        assert [block.text for block in blocks] == ["导引标题", "导引正文", "导引续", "导引末"]

    def test_block_bounds_apply_only_to_the_pages_they_belong_to(self):
        """窗口有多页时起点块号只管起点页、终点块号只管终点页，中间页不因块号被裁。"""
        pages = [
            _page_of(9, _body_block(0, "前"), _body_block(3, "起点")),
            _page_of(10, _body_block(0, "中页首"), _body_block(9, "中页末")),
            _page_of(11, _body_block(0, "终点页首"), _body_block(5, "终点块"), _body_block(6, "下一节")),
        ]

        blocks = select_material_blocks(
            pages, page_start=9, page_end=11, start_block=3, end_block=5
        )

        assert [block.text for block in blocks] == ["起点", "中页首", "中页末", "终点页首", "终点块"]


class TestPlanPointCalls:
    """`KA-64`：预算内的单窗、块边界切窗、单块超预算按句界切、相邻小节合批。"""

    @staticmethod
    def _section(
        node_id: str,
        *texts: str,
        parent_id: str = "c1",
    ) -> PointSection:
        return PointSection(
            node_id=node_id,
            parent_id=parent_id,
            title=node_id,
            blocks=tuple(
                MaterialBlock(page=1, block_index=index, block_type="paragraph", text=text)
                for index, text in enumerate(texts)
            ),
        )

    def test_a_section_within_budget_is_one_call(self):
        section = self._section("p1", "甲" * 20, "乙" * 20)

        calls = plan_point_calls([section], budget=100, floor=10)

        assert len(calls) == 1
        assert calls[0].node_ids == ("p1",)
        assert calls[0].merged is False
        assert [block.text for block in calls[0].blocks] == ["甲" * 20, "乙" * 20]
        assert calls[0].chars <= 100

    def test_an_oversized_section_is_cut_at_block_boundaries(self):
        section = self._section("p1", "甲" * 20, "乙" * 20, "丙" * 20)

        calls = plan_point_calls([section], budget=45, floor=10)

        assert len(calls) == 2
        assert [block.text for block in calls[0].blocks] == ["甲" * 20, "乙" * 20]
        assert [block.text for block in calls[1].blocks] == ["丙" * 20]
        assert all(call.chars <= 45 for call in calls)
        assert all(call.node_ids == ("p1",) for call in calls)

    def test_a_single_oversized_block_is_cut_on_sentence_boundaries(self):
        text = "甲" * 20 + "。" + "乙" * 20 + "。"
        section = self._section("p1", text)

        calls = plan_point_calls([section], budget=25, floor=10)

        assert [block.text for call in calls for block in call.blocks] == [
            "甲" * 20 + "。",
            "乙" * 20 + "。",
        ]
        assert all(call.chars <= 25 for call in calls)
        assert all(block.block_index == 0 for call in calls for block in call.blocks)

    def test_adjacent_small_siblings_share_one_call(self):
        sections = [self._section("p1", "甲" * 8), self._section("p2", "乙" * 8)]

        calls = plan_point_calls(sections, budget=40, floor=10)

        assert len(calls) == 1
        assert calls[0].node_ids == ("p1", "p2")
        assert calls[0].merged is True
        assert [block.text for block in calls[0].blocks] == ["甲" * 8, "乙" * 8]

    def test_small_sections_under_different_parents_are_not_merged(self):
        sections = [
            self._section("p1", "甲" * 8, parent_id="c1"),
            self._section("p2", "乙" * 8, parent_id="c2"),
        ]

        calls = plan_point_calls(sections, budget=40, floor=10)

        assert [call.node_ids for call in calls] == [("p1",), ("p2",)]

    def test_a_merge_that_breaks_the_budget_is_not_taken(self):
        sections = [self._section("p1", "甲" * 12), self._section("p2", "乙" * 12)]

        calls = plan_point_calls(sections, budget=25, floor=15)

        assert [call.node_ids for call in calls] == [("p1",), ("p2",)]

    def test_a_section_above_the_floor_does_not_absorb_a_small_neighbour(self):
        sections = [self._section("p1", "甲" * 12), self._section("p2", "乙" * 40)]

        calls = plan_point_calls(sections, budget=100, floor=15)

        assert [call.node_ids for call in calls] == [("p1",), ("p2",)]

    def test_call_order_follows_the_screen_order(self):
        sections = [
            self._section("p1", "甲" * 20),
            self._section("p2", "乙" * 8),
            self._section("p3", "丙" * 8),
        ]

        calls = plan_point_calls(sections, budget=45, floor=10)

        assert [call.node_ids for call in calls] == [("p1",), ("p2", "p3")]

    def test_the_default_budget_and_floor_are_the_documented_constants(self):
        assert CONTENT_INPUT_BUDGET_CHARS == 10000
        assert CONTENT_MERGE_FLOOR_CHARS == 1500


class TestChapterMaterialBlocks:
    """`KA-65`：章是文件夹——章前材料止于首点锚页之前，同页只取首点标题块之前的块。"""

    def test_the_folder_stops_before_the_first_point_page(self):
        pages = [
            _page_of(1, _body_block(0, "第 1 章 极限"), _body_block(1, "章前导语")),
            _page_of(2, _body_block(0, "续页正文")),
            _page_of(3, _body_block(0, "1.1 数列极限"), _body_block(1, "点正文")),
        ]

        blocks = chapter_material_blocks(pages, page_start=1, first_child_page=3)

        assert [block.text for block in blocks] == ["第 1 章 极限", "章前导语", "续页正文"]

    def test_a_shared_page_keeps_only_the_blocks_before_the_point_title(self):
        pages = [
            _page_of(
                2,
                _body_block(0, "第 1 章 极限"),
                _body_block(1, "章前言"),
                _body_block(2, "1.1 数列极限"),
                _body_block(3, "点正文"),
            )
        ]

        blocks = chapter_material_blocks(
            pages, page_start=2, first_child_page=2, first_child_block=2
        )

        assert [block.text for block in blocks] == ["第 1 章 极限", "章前言"]

    def test_a_shared_page_without_a_block_cutoff_yields_nothing(self):
        """判不出首点从哪个块起读时不切页：宁可章少读一句，也不让章取走点的正文。"""
        pages = [_page_of(2, _body_block(0, "第 1 章 极限"), _body_block(1, "点正文"))]

        assert (
            chapter_material_blocks(pages, page_start=2, first_child_page=2, first_child_block=None)
            == ()
        )

    def test_a_chapter_without_children_has_no_material(self):
        pages = [_page_of(1, _body_block(0, "第 1 章 极限"))]

        assert chapter_material_blocks(pages, page_start=1, first_child_page=None) == ()

    def test_a_child_before_the_chapter_page_is_dirty_and_yields_nothing(self):
        pages = [_page_of(1, _body_block(0, "第 1 章 极限"))]

        assert chapter_material_blocks(pages, page_start=1, first_child_page=0) == ()

    def test_furniture_and_excluded_pages_contribute_nothing(self):
        pages = [
            _page_of(1, _body_block(0, "第 1 章 极限")),
            _page_of(2, _body_block(0, "目录回声")),
        ]

        blocks = chapter_material_blocks(
            pages, page_start=1, first_child_page=3, exclude_pages=(2,)
        )

        assert [block.text for block in blocks] == ["第 1 章 极限"]
