"""schemas.py 单元测试：统一格式 Pydantic 模型（设计见 docs/design/parsing-pipeline.md）。"""

import pytest
from pydantic import ValidationError

from axiom_flow.schemas import (
    Block,
    BlocksPage,
    BlockType,
    BookMeta,
    BookOut,
    BookSyncItem,
    Chunk,
    ChunkSet,
    EditRecord,
    EditRequest,
    IngestResponse,
    JobStatus,
    ManifestEntry,
    PageData,
    PageState,
    ParseJob,
    ParseJobCreate,
    QualityMetrics,
    Source,
    Verdict,
)


class TestBlockType:
    """块类型枚举（10 种）。"""

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
    """Block 判别联合：类型+必填字段+bbox+index/order 约束。"""

    def test_heading_block(self):
        b = Block(type="heading", level=1, text="1. The Real and Complex Number Systems", bbox=[72, 54, 540, 80], index=0, order=0)
        assert b.type == BlockType.HEADING
        assert b.bbox == (72, 54, 540, 80)
        assert b.index == 0 and b.order == 0

    def test_paragraph_block(self):
        b = Block(type="paragraph", text="We assume...", bbox=[72, 96, 540, 132], index=1, order=1)
        assert b.type == BlockType.PARAGRAPH

    def test_formula_block_with_latex_and_confidence(self):
        b = Block(type="formula", latex="x + y = y + x", display=True, bbox=[72, 150, 540, 175], index=2, order=2, confidence=0.97)
        assert b.latex == "x + y = y + x"
        assert b.display is True
        assert b.confidence == pytest.approx(0.97)

    def test_formula_display_defaults_false(self):
        b = Block(type="formula", latex="x = 1", bbox=[0, 0, 1, 1], index=0, order=0)
        assert b.display is False

    def test_table_block_with_html(self):
        b = Block(type="table", html="<table><tr><td>1</td></tr></table>", bbox=[72, 200, 540, 300], index=0, order=0)
        assert b.type == BlockType.TABLE

    def test_image_block_caption_optional(self):
        b = Block(type="image", path="images/p0001_im1.png", bbox=[0, 0, 1, 1], index=0, order=0)
        assert b.caption is None
        b2 = Block(type="image", path="images/p0001_im1.png", caption="图 1", bbox=[0, 0, 1, 1], index=0, order=0)
        assert b2.caption == "图 1"

    def test_rejects_wrong_type_value(self):
        with pytest.raises(ValidationError):
            Block(type="unknown", text="x", bbox=[0, 0, 1, 1], index=0, order=0)

    def test_heading_requires_level(self):
        with pytest.raises(ValidationError):
            Block(type="heading", text="缺 level", bbox=[0, 0, 1, 1], index=0, order=0)

    def test_heading_level_range(self):
        with pytest.raises(ValidationError):
            Block(type="heading", level=0, text="x", bbox=[0, 0, 1, 1], index=0, order=0)
        with pytest.raises(ValidationError):
            Block(type="heading", level=7, text="x", bbox=[0, 0, 1, 1], index=0, order=0)

    def test_rejects_inverted_bbox(self):
        with pytest.raises(ValidationError):
            Block(type="paragraph", text="x", bbox=[540, 96, 72, 132], index=0, order=0)

    def test_bbox_len_must_be_four(self):
        with pytest.raises(ValidationError):
            Block(type="paragraph", text="x", bbox=[72, 96, 540], index=0, order=0)

    def test_index_and_order_required(self):
        with pytest.raises(ValidationError):
            Block(type="paragraph", text="x", bbox=[0, 0, 1, 1])

    def test_index_non_negative(self):
        with pytest.raises(ValidationError):
            Block(type="paragraph", text="x", bbox=[0, 0, 1, 1], index=-1, order=0)

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            Block(type="formula", latex="x", bbox=[0, 0, 1, 1], index=0, order=0, confidence=1.5)

    def test_list_block_items(self):
        b = Block(type="list", items=["a", "b"], bbox=[0, 0, 1, 1], index=0, order=0)
        assert b.items == ["a", "b"]

    def test_page_number_block(self):
        b = Block(type="page_number", text="3", bbox=[0, 0, 1, 1], index=0, order=0)
        assert b.type == BlockType.PAGE_NUMBER


class TestBlocksPage:
    """页级 blocks.json 结构（page/image_size/source/blocks + 质量信号）。"""

    def test_page_with_blocks(self):
        page = BlocksPage(
            page=1,
            image_size=(1240, 1754),
            source="mineru",
            blocks=[
                Block(type="heading", level=1, text="Ch 1", bbox=[0, 0, 1, 1], index=0, order=0),
                Block(type="formula", latex="x", bbox=[0, 0, 1, 1], index=1, order=1),
            ],
        )
        assert page.page == 1
        assert page.image_size == (1240, 1754)
        assert page.source == Source.MINERU
        assert len(page.blocks) == 2

    def test_image_size_required(self):
        with pytest.raises(ValidationError):
            BlocksPage(page=1, source="mineru", blocks=[])

    def test_page_number_must_be_positive(self):
        with pytest.raises(ValidationError):
            BlocksPage(page=0, image_size=(1, 1), source="mineru", blocks=[])

    def test_quality_metrics_optional(self):
        page = BlocksPage(page=2, image_size=(1, 1), source="paddleocr-vl", blocks=[])
        assert page.quality is None

    def test_quality_metrics_attached(self):
        page = BlocksPage(
            page=2,
            image_size=(1, 1),
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

    def test_source_enum_values(self):
        assert {s.value for s in Source} == {"mineru", "paddleocr-vl", "qwen-vl"}


class TestBookMeta:
    """book.json：book_id/标题/作者/页数/SHA-256/源文件/引擎。"""

    def test_full_meta(self):
        m = BookMeta(
            book_id="mathanalysis-b05",
            title="数学分析原理",
            authors=[{"name": "Walter Rudin", "role": "author"}],
            page_count=342,
            sha256="a" * 64,
            source_file="raw/math-advanced/math_analysis/rudin_ab12cd34.pdf",
            engine="mineru",
        )
        assert m.book_id == "mathanalysis-b05"
        assert m.page_count == 342
        assert m.authors[0].name == "Walter Rudin"
        assert m.source_file.endswith(".pdf")

    def test_sha256_length_validated(self):
        with pytest.raises(ValidationError):
            BookMeta(book_id="b", title="t", page_count=1, sha256="abc")


class TestManifest:
    """manifest.json：产物清单（路径+大小+哈希）。"""

    def test_entry(self):
        e = ManifestEntry(path="pages/p0001.png", size=12345, sha256="b" * 64)
        assert e.path == "pages/p0001.png"
        assert e.size == 12345

    def test_entry_size_non_negative(self):
        with pytest.raises(ValidationError):
            ManifestEntry(path="pages/p0001.png", size=-1, sha256="b" * 64)


class TestChunk:
    """分块模型：chunk 溯源与参数。"""

    def test_chunk_set_round_trip(self):
        chunk = Chunk(
            chunk_id="c0001",
            order=0,
            type="mixed",
            text="正文 $x^2$ 与公式 $$y=x$$",
            page_start=32,
            page_end=33,
            block_refs=[{"page": 32, "index": 3}, {"page": 33, "index": 0}],
            bbox=[72, 150, 540, 420],
            char_count=20,
        )
        chunk_set = ChunkSet(book_id="mathanalysis-b05", chunks=[chunk])
        assert chunk_set.params.max_chars == 1000
        assert chunk_set.params.overlap_blocks == 1
        assert chunk_set.chunks[0].block_refs[1].page == 33

    def test_chunk_requires_provenance(self):
        with pytest.raises(ValidationError):
            Chunk(chunk_id="c1", order=0, type="paragraph", text="x", page_start=1, page_end=1, bbox=[0, 0, 1, 1], char_count=1)

    def test_params_bounds(self):
        with pytest.raises(ValidationError):
            ChunkSet(book_id="b", params={"max_chars": 0})


class TestApiModels:
    """API 契约模型：书目同步/ingest/对照/编辑/任务。"""

    def test_book_sync_item_defaults(self):
        item = BookSyncItem(book_id="b05", file_path="raw/x.pdf")
        assert item.authors == []
        assert item.domain_id == ""

    def test_book_out_status_defaults(self):
        book = BookOut(book_id="b05")
        assert book.ingest_status == "none"
        assert book.parse_status == "pending"

    def test_ingest_response(self):
        resp = IngestResponse(book_id="b05", page_count=3, sha256="a" * 64)
        assert resp.ingest_status == "ingested"

    def test_edit_request_verdict(self):
        assert EditRequest().verdict == Verdict.NONE
        assert EditRequest(verdict="bad", note="公式错").verdict == Verdict.BAD
        with pytest.raises(ValidationError):
            EditRequest(verdict="maybe")

    def test_edit_record(self):
        record = EditRecord(edit_id="ed_x", book_id="b05", page_no=3, block_index=2, verdict="ok")
        assert record.verdict == Verdict.OK
        assert record.corrected_bbox is None

    def test_page_data(self):
        page = BlocksPage(page=1, image_size=(10, 10), source="mineru", blocks=[])
        data = PageData(blocks=page, image_url="/api/v1/books/b05/pages/1/image")
        assert data.markdown == ""
        assert data.edits == []


class TestParseJob:
    """解析任务：提交（book_id/页范围/引擎）与状态。"""

    def test_create_job(self):
        job = ParseJobCreate(book_id="b05", pages=[1, 2, 3])
        assert job.pages == [1, 2, 3]
        assert job.engine is None  # 缺省 = 回落 AXIOM_OCR_ENGINE，不在 schema 层写死引擎

    def test_create_job_rejects_unregistered_engine(self):
        with pytest.raises(ValidationError, match="paddleocr|Input should"):
            ParseJobCreate(book_id="b05", engine="gpt-4o")

    def test_create_job_pages_optional_means_all(self):
        job = ParseJobCreate(book_id="b05")
        assert job.pages is None

    def test_job_status(self):
        job = ParseJob(
            id="job-1",
            book_id="b05",
            status="running",
            progress={"parsed": 2, "total": 5},
        )
        assert job.status == JobStatus.RUNNING
        assert job.progress == {"parsed": 2, "total": 5}

    def test_job_invalid_status(self):
        with pytest.raises(ValidationError):
            ParseJob(id="job-1", book_id="b05", status="unknown")

    def test_page_state_enum(self):
        assert {s.value for s in PageState} == {"pending", "parsed", "failed"}
