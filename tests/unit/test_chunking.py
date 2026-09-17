"""分块规则单测：heading 起块、公式/表格不拆、段落累积、跳过噪声、溯源与重叠。"""

from __future__ import annotations

from axiom_flow.normalize.chunking import build_chunks
from axiom_flow.schemas import Block, BlocksPage, ChunkParams


def _block(index: int, type_: str, **kwargs) -> Block:
    payload = {"type": type_, "bbox": kwargs.pop("bbox", [0, 0, 100, 50]), "index": index, "order": index}
    payload.update(kwargs)
    return Block(**payload)


def _page(page_no: int, blocks: list[Block]) -> BlocksPage:
    return BlocksPage(page=page_no, image_size=(1240, 1755), source="mineru", blocks=blocks)


class TestChunkRules:
    """切分规则（数学文档感知）。"""

    def test_heading_starts_new_chunk(self):
        pages = [
            _page(
                1,
                [
                    _block(0, "paragraph", text="前言段落"),
                    _block(1, "heading", level=1, text="第 1 章"),
                    _block(2, "paragraph", text="章内正文"),
                ],
            )
        ]

        chunk_set = build_chunks(pages, book_id="b05")

        assert len(chunk_set.chunks) == 2
        assert chunk_set.chunks[0].text == "前言段落"
        assert chunk_set.chunks[1].text.startswith("第 1 章")
        assert "章内正文" in chunk_set.chunks[1].text

    def test_formula_and_table_never_split(self):
        long_latex = "x" * 50
        pages = [
            _page(
                1,
                [
                    _block(0, "formula", latex=long_latex, display=True),
                    _block(1, "table", html="<table></table>"),
                ],
            )
        ]

        chunk_set = build_chunks(pages, book_id="b05", params=ChunkParams(max_chars=10, overlap_blocks=0))

        assert len(chunk_set.chunks) == 2
        assert chunk_set.chunks[0].type == "formula"
        assert long_latex in chunk_set.chunks[0].text
        assert chunk_set.chunks[1].type == "table"
        assert chunk_set.chunks[1].text == "<table></table>"

    def test_paragraphs_accumulate_to_budget(self):
        pages = [_page(1, [_block(i, "paragraph", text="字" * 30) for i in range(5)])]

        chunk_set = build_chunks(pages, book_id="b05", params=ChunkParams(max_chars=70, overlap_blocks=0))

        assert len(chunk_set.chunks) == 3  # 30+30 → 收尾；30+30 → 收尾；30
        assert [len(chunk.block_refs) for chunk in chunk_set.chunks] == [2, 2, 1]

    def test_noise_blocks_skipped(self):
        pages = [
            _page(
                1,
                [
                    _block(0, "header", text="页眉"),
                    _block(1, "paragraph", text="正文"),
                    _block(2, "footer", text="页脚"),
                    _block(3, "page_number", text="3"),
                    _block(4, "image", path="pages/images/a.png"),
                ],
            )
        ]

        chunk_set = build_chunks(pages, book_id="b05")

        assert len(chunk_set.chunks) == 1
        assert chunk_set.chunks[0].text == "正文"

    def test_overlap_carries_previous_paragraph(self):
        pages = [_page(1, [_block(i, "paragraph", text=f"段{i}" + "字" * 40) for i in range(3)])]

        chunk_set = build_chunks(pages, book_id="b05", params=ChunkParams(max_chars=50, overlap_blocks=1))

        assert len(chunk_set.chunks) >= 2
        assert chunk_set.chunks[1].text.startswith(chunk_set.chunks[0].text.split("\n\n")[-1])

    def test_cross_page_merge_and_provenance(self):
        pages = [
            _page(1, [_block(0, "paragraph", text="上页正文")]),
            _page(2, [_block(0, "paragraph", text="下页正文")]),
        ]

        chunk_set = build_chunks(pages, book_id="b05")

        assert len(chunk_set.chunks) == 1
        chunk = chunk_set.chunks[0]
        assert (chunk.page_start, chunk.page_end) == (1, 2)
        assert [(ref.page, ref.index) for ref in chunk.block_refs] == [(1, 0), (2, 0)]
        assert chunk.bbox == (0, 0, 100, 50)
        assert chunk.order == 0
        assert chunk.chunk_id == "c0001"

    def test_empty_pages_yield_empty_set(self):
        chunk_set = build_chunks([_page(1, [])], book_id="b05")

        assert chunk_set.book_id == "b05"
        assert chunk_set.chunks == []
        assert chunk_set.params.max_chars == 1000

    def test_type_label_for_single_type_chunk(self):
        pages = [_page(1, [_block(0, "list", items=["a", "b"])])]

        chunk_set = build_chunks(pages, book_id="b05")

        assert chunk_set.chunks[0].type == "list"
        assert chunk_set.chunks[0].text == "- a\n- b"
