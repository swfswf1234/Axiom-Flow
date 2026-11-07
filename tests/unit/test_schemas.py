"""schemas.py 单元测试：统一格式 Pydantic 模型（设计见 docs/design/parsing-pipeline.md）。"""

from typing import get_args

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
    KpExerciseOut,
    KpJobCreate,
    KpNodeOut,
    KpStage,
    KpWorkspaceOut,
    LastParseResult,
    ManifestEntry,
    PageData,
    PageState,
    ParseJob,
    ParseJobCreate,
    ParseStatus,
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

    def test_drop_signals_default_and_bound(self):
        """可见丢弃信号（DEFECT-005）：默认零，计数不得为负。"""
        quality = QualityMetrics(
            empty_block_ratio=0.0, avg_formula_confidence=1.0, table_count=0, text_length_ratio=1.0
        )
        assert quality.dropped_block_count == 0
        assert quality.dropped_types == []
        with pytest.raises(ValidationError):
            QualityMetrics(
                empty_block_ratio=0.0, avg_formula_confidence=1.0, table_count=0,
                text_length_ratio=1.0, dropped_block_count=-1,
            )

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
        assert item.roles == []

    def test_book_sync_item_carries_roles(self):
        """种类派生的输入只走同步行（ADR 0019）：`roles` 是透传字段，本侧不改写取值。"""
        item = BookSyncItem(book_id="b05", roles=["textbook", "exercises"])
        assert item.roles == ["textbook", "exercises"]

    def test_book_sync_item_accepts_fullwidth_punctuation(self):
        """REQ-083：中文书名的全角标点不是遍历风险，不得让整批 sync 422。"""
        item = BookSyncItem(
            book_id="mathanalysis-b17",
            file_path="raw/math-advanced/数学分析（第2版）：讲义【上】.pdf",
        )

        assert item.file_path.endswith(".pdf")

    @pytest.mark.parametrize(
        "path",
        [
            "raw/../etc/passwd",
            "../outside.pdf",
            "raw\\book.pdf",
            "D:/raw/book.pdf",
            "/absolute/book.pdf",
            "raw/book.pdf.",
            "raw//double.pdf",
        ],
    )
    def test_book_sync_item_still_rejects_unsafe_path(self, path):
        with pytest.raises(ValidationError):
            BookSyncItem(book_id="b05", file_path=path)

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


class TestParseStatusSemantics:
    """ADR 0007：任务结局三态 + 书级进度/结局分列（`parse_status` 不再表达结局）。"""

    def test_parse_status_only_expresses_progress(self):
        assert {s.value for s in ParseStatus} == {"pending", "parsing"}

    def test_job_status_terminal_set(self):
        assert {s.value for s in JobStatus} == {
            "queued",
            "running",
            "completed",
            "partial",
            "failed",
            "interrupted",
        }

    def test_last_parse_result_set(self):
        """枚举覆盖 `last_parse_result` 的全部取值域：`''` = 该书从未有任务。"""
        assert {s.value for s in LastParseResult} == {"", "completed", "partial", "failed", "interrupted"}

    def test_last_parse_result_none_is_empty_string(self):
        assert LastParseResult.NONE == ""

    def test_book_out_last_parse_result_defaults_empty(self):
        assert BookOut(book_id="b05").last_parse_result == ""

    def test_book_out_carries_kind_and_curation_result(self):
        """`KA-51`／ADR 0019：两枚闭集列都从书目读面透出，缺省空串＝未声明／从未梳理。"""
        book = BookOut(book_id="b05")
        assert (book.doc_kind, book.last_curation_result) == ("", "")

    def test_book_out_carries_the_derived_in_flight_reading(self):
        """`KA-55`：第三段读数是读时派生的布尔，缺省 `false`——它不是库里的一列。"""
        assert BookOut(book_id="b05").curation_in_flight is False

    def test_parse_job_failed_pages_is_structured(self):
        job = ParseJob(id="job-1", book_id="b05", status="partial", failed_pages=[3, 1])
        assert job.status == JobStatus.PARTIAL
        assert job.failed_pages == [3, 1]

    def test_parse_job_failed_pages_defaults_empty(self):
        assert ParseJob(id="job-1", book_id="b05", status="completed").failed_pages == []

    def test_parse_job_rejects_non_numeric_failed_page(self):
        with pytest.raises(ValidationError):
            ParseJob(id="job-1", book_id="b05", status="partial", failed_pages=["abc"])


class TestKpJobBody:
    """`KA-51`／`KA-52`：阶段四值闭集与「目录阶段无页子集重跑」的请求体形状。"""

    def test_the_stage_closed_set_carries_the_exercise_value(self):
        """第四值先登记在型上：域外值仍由 pydantic 判 422，先于受理门（`KA-51`）。"""
        assert get_args(KpStage) == ("full", "outline", "content", "exercise")
        assert KpJobCreate(book_id="b05", stage="exercise").stage == "exercise"

    def test_the_body_has_no_page_subset_or_resume_input(self):
        """`KA-52`：重跑目录＝同范围重发新批次——请求体没有逐页／续跑键。

        `only_failed` 不是续跑：它只收窄「这一次对哪些节点发问」，批次号照旧新发、取证集照旧是整个
        页区间。习题腿的两键也不是续跑（`KA-53`）：`target_book_id` 是配套模式的目标书、
        `retry_node_ids` 只对 `stage='exercise'` 合法且同样是新批次；目录与内容两腿仍无页子集入参。
        """
        assert set(KpJobCreate.model_fields) == {
            "book_id",
            "page_start",
            "page_end",
            "review_note",
            "stage",
            "only_failed",
            "target_book_id",
            "retry_node_ids",
        }

    def test_exercise_keys_default_off(self):
        """两键缺省＝独立模式、首发批次；载荷闭集扩键不改旧请求的读数（`KA-53`）。"""
        body = KpJobCreate(book_id="b07", stage="exercise")
        assert body.target_book_id is None
        assert body.retry_node_ids == []

    def test_target_book_id_rejects_unsafe_segment(self):
        with pytest.raises(ValidationError):
            KpJobCreate(book_id="b07", stage="exercise", target_book_id="../b05")


class TestKpExerciseFace:
    """`KA-53`：题行的来源位与习题读面的缺省形状。"""

    def test_node_out_carries_empty_source_keys_by_default(self):
        """非习题行的两枚来源键恒空串／空表：meta 整槽不外发，只逐键带这两枚。"""
        row = KpNodeOut(
            node_id="n1", book_id="b05", job_id="", node_kind="point", name="3.2", gen_status="candidate"
        )
        assert row.source_book_id == ""
        assert row.source_pages == []

    def test_workspace_exercise_is_optional(self):
        """教材的共享主表永不含习题行：这一位对它缺省 `None`，不制造第二套读数。"""
        assert KpWorkspaceOut(book_id="b05").exercise is None

    def test_exercise_defaults_are_the_empty_standalone_shape(self):
        face = KpExerciseOut()
        assert (face.kind, face.target_book_id, face.done) == ("standalone", None, False)
        assert (face.items, face.packs, face.pending_reparse, face.unmatched) == ([], 0, 0, [])

    def test_exercise_done_is_a_plain_boolean(self):
        """完成读数只由服务端派生（题行>0 ∧ 题包齐 ∧ 待重解析零枚），根侧不另算。"""
        assert KpExerciseOut(items=[], packs=0, pending_reparse=0, done=False).done is False
