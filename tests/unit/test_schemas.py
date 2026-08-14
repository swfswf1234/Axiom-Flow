"""schemas.py 单元测试：统一格式 Pydantic 模型（spec 第 2 节）。"""

import pytest
from pydantic import ValidationError

from axiom_flow.schemas import (
    Block,
    BlocksPage,
    BlockType,
    BookMeta,
    JobStatus,
    ManifestEntry,
    PageState,
    ParseJob,
    ParseJobCreate,
    QualityMetrics,
    Source,
    Strategy,
)


class TestBlockType:
    """块类型枚举（spec：heading/paragraph/formula/table/image/list/caption/header/footer/page_number）。"""

    def test_enumerate_ten_types(self):
        assert {t.value for t in BlockType} == {
            "heading",
            "paragraph",
            "formula",
            "table",
            "image",
            "list",
            "caption",
            "header",
            "footer",
            "page_number",
        }


class TestBlock:
    """Block 判别联合：类型+必填字段+bbox 约束。"""

    def test_heading_block(self):
        b = Block(type="heading", level=1, text="1. The Real and Complex Number Systems", bbox=[72, 54, 540, 80])
        assert b.type == BlockType.HEADING
        assert b.bbox == (72, 54, 540, 80)

    def test_paragraph_block(self):
        b = Block(type="paragraph", text="We assume...", bbox=[72, 96, 540, 132])
        assert b.type == BlockType.PARAGRAPH

    def test_formula_block_with_latex_and_confidence(self):
        b = Block(type="formula", latex="x + y = y + x", display=True, bbox=[72, 150, 540, 175], confidence=0.97)
        assert b.latex == "x + y = y + x"
        assert b.display is True
        assert b.confidence == pytest.approx(0.97)

    def test_formula_display_defaults_false(self):
        b = Block(type="formula", latex="x = 1", bbox=[0, 0, 1, 1])
        assert b.display is False

    def test_table_block_with_html(self):
        b = Block(type="table", html="<table><tr><td>1</td></tr></table>", bbox=[72, 200, 540, 300])
        assert b.type == BlockType.TABLE

    def test_image_block_caption_optional(self):
        b = Block(type="image", path="images/p0001_im1.png", bbox=[0, 0, 1, 1])
        assert b.caption is None
        b2 = Block(type="image", path="images/p0001_im1.png", caption="图 1", bbox=[0, 0, 1, 1])
        assert b2.caption == "图 1"

    def test_rejects_wrong_type_value(self):
        with pytest.raises(ValidationError):
            Block(type="unknown", text="x", bbox=[0, 0, 1, 1])

    def test_heading_requires_level(self):
        with pytest.raises(ValidationError):
            Block(type="heading", text="缺 level", bbox=[0, 0, 1, 1])

    def test_heading_level_range(self):
        with pytest.raises(ValidationError):
            Block(type="heading", level=0, text="x", bbox=[0, 0, 1, 1])
        with pytest.raises(ValidationError):
            Block(type="heading", level=7, text="x", bbox=[0, 0, 1, 1])

    def test_rejects_inverted_bbox(self):
        with pytest.raises(ValidationError):
            Block(type="paragraph", text="x", bbox=[540, 96, 72, 132])

    def test_bbox_len_must_be_four(self):
        with pytest.raises(ValidationError):
            Block(type="paragraph", text="x", bbox=[72, 96, 540])

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            Block(type="formula", latex="x", bbox=[0, 0, 1, 1], confidence=1.5)

    def test_list_block_items(self):
        b = Block(type="list", items=["a", "b"], bbox=[0, 0, 1, 1])
        assert b.items == ["a", "b"]

    def test_page_number_block(self):
        b = Block(type="page_number", text="3", bbox=[0, 0, 1, 1])
        assert b.type == BlockType.PAGE_NUMBER


class TestBlocksPage:
    """页级三件套的 blocks.json 结构（page/source/blocks + 质量信号）。"""

    def test_page_with_blocks(self):
        page = BlocksPage(
            page=1,
            source="mineru",
            blocks=[
                Block(type="heading", level=1, text="Ch 1", bbox=[0, 0, 1, 1]),
                Block(type="formula", latex="x", bbox=[0, 0, 1, 1]),
            ],
        )
        assert page.page == 1
        assert page.source == Source.MINERU
        assert len(page.blocks) == 2

    def test_page_number_must_be_positive(self):
        with pytest.raises(ValidationError):
            BlocksPage(page=0, source="mineru", blocks=[])

    def test_quality_metrics_optional(self):
        page = BlocksPage(page=2, source="qwen-vl-plus", blocks=[])
        assert page.quality is None

    def test_quality_metrics_attached(self):
        page = BlocksPage(
            page=2,
            source="mineru",
            blocks=[],
            quality=QualityMetrics(empty_block_ratio=0.1, avg_formula_confidence=0.95, table_count=2, text_length_ratio=0.8),
        )
        q = page.quality
        assert q.empty_block_ratio == pytest.approx(0.1)
        assert q.table_count == 2
        assert q.text_length_ratio == pytest.approx(0.8)

    def test_quality_ratio_bounds(self):
        with pytest.raises(ValidationError):
            QualityMetrics(empty_block_ratio=1.5, avg_formula_confidence=0.9, table_count=0, text_length_ratio=0.5)


class TestBookMeta:
    """book.json：书名/作者/页数/SHA-256/解析策略。"""

    def test_full_meta(self):
        m = BookMeta(
            book_id="01-rudin-en-principles-of-math-analysis",
            title="Principles of Mathematical Analysis",
            author="Walter Rudin",
            page_count=342,
            sha256="a" * 64,
            strategy="hybrid",
        )
        assert m.book_id == "01-rudin-en-principles-of-math-analysis"
        assert m.page_count == 342

    def test_sha256_length_validated(self):
        with pytest.raises(ValidationError):
            BookMeta(book_id="b", title="t", author=None, page_count=1, sha256="abc", strategy="local")

    def test_strategy_enum(self):
        assert Strategy.LOCAL == "local"
        assert Strategy.HYBRID == "hybrid"


class TestManifest:
    """manifest.json：产物清单（路径+大小+哈希）。"""

    def test_entry(self):
        e = ManifestEntry(path="pages/p0001.png", size=12345, sha256="b" * 64)
        assert e.path == "pages/p0001.png"
        assert e.size == 12345

    def test_entry_size_non_negative(self):
        with pytest.raises(ValidationError):
            ManifestEntry(path="pages/p0001.png", size=-1, sha256="b" * 64)


class TestParseJob:
    """解析任务：提交（book_id/页范围/策略）与状态。"""

    def test_create_job(self):
        job = ParseJobCreate(book_id="01-rudin-en", pages=[1, 2, 3], strategy="hybrid")
        assert job.pages == [1, 2, 3]

    def test_create_job_pages_optional_means_all(self):
        job = ParseJobCreate(book_id="b", strategy="local")
        assert job.pages is None

    def test_job_status(self):
        job = ParseJob(
            id="job-1",
            book_id="b",
            strategy="hybrid",
            status="running",
            progress={"parsed": 2, "total": 5},
        )
        assert job.status == JobStatus.RUNNING
        assert job.progress == {"parsed": 2, "total": 5}

    def test_job_invalid_status(self):
        with pytest.raises(ValidationError):
            ParseJob(id="job-1", book_id="b", strategy="local", status="unknown")

    def test_page_state_enum(self):
        assert {s.value for s in PageState} == {"pending", "parsed", "fallback", "failed"}

    def test_source_enum(self):
        assert {s.value for s in Source} == {"mineru", "qwen-vl-plus"}
