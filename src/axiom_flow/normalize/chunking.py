"""分块（blocks → chunks）：书级有序块序列切分为知识单元（派生、可重建）。

设计事实源：`docs/design/parsing-pipeline.md` §分块。规则：
1. 输入为书级有序块序列（页序 + `order`），跨页合并；
2. `heading` 结束当前 chunk 并作为新 chunk 开头；
3. `formula` / `table` 整块不可拆（单块超预算允许超出）；
4. 段落类累积至 `max_chars`；
5. 页眉/页脚/页码/图片默认跳过（无检索文本或噪声）；
6. `overlap_blocks` 控制相邻 chunk 上下文重叠；
7. 每块记录页/块溯源与 bbox 并集。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from axiom_flow.schemas import BBox, Block, BlockRef, BlocksPage, BlockType, Chunk, ChunkParams, ChunkSet

_SKIP_TYPES = {BlockType.HEADER, BlockType.FOOTER, BlockType.PAGE_NUMBER, BlockType.IMAGE}
_ATOMIC_TYPES = {BlockType.FORMULA, BlockType.TABLE}
_OVERLAP_TYPES = {BlockType.PARAGRAPH, BlockType.LIST, BlockType.CAPTION}
_TYPE_LABELS = {
    BlockType.PARAGRAPH: "paragraph",
    BlockType.FORMULA: "formula",
    BlockType.TABLE: "table",
    BlockType.LIST: "list",
    BlockType.CAPTION: "caption",
}


def block_text(block: Block) -> str:
    """块 → 检索文本（公式用 LaTeX，表格用 HTML，列表逐项）。"""
    if block.type is BlockType.FORMULA:
        latex = block.latex or ""
        return f"$$\n{latex}\n$$" if block.display else f"${latex}$"
    if block.type is BlockType.TABLE:
        return block.html or ""
    if block.type is BlockType.LIST:
        return "\n".join(f"- {item}" for item in block.items or [])
    return block.text or ""


def _union_bbox(bboxes: Sequence[BBox]) -> BBox:
    x0 = min(bbox[0] for bbox in bboxes)
    y0 = min(bbox[1] for bbox in bboxes)
    x1 = max(bbox[2] for bbox in bboxes)
    y1 = max(bbox[3] for bbox in bboxes)
    return (x0, y0, max(x1, x0 + 1), max(y1, y0 + 1))


class _ChunkBuilder:
    """累积块 → chunk（heading 起新块、原子块不拆、段落类累积）。"""

    def __init__(self, max_chars: int, overlap_blocks: int) -> None:
        self.max_chars = max_chars
        self.overlap_blocks = overlap_blocks
        self.chunks: list[Chunk] = []
        self._parts: list[str] = []
        self._chars = 0
        self._blocks: list[tuple[int, Block]] = []
        self._last_context: list[tuple[int, Block]] = []

    def add(self, page_no: int, block: Block) -> None:
        text = block_text(block)
        if block.type is BlockType.HEADING:
            self.flush()
            self._parts, self._chars, self._blocks = [text], len(text), [(page_no, block)]
            return
        if block.type in _ATOMIC_TYPES:
            if self._parts and self._chars + len(text) > self.max_chars:
                self.flush()
            if not self._parts:
                self._start_with_overlap()
            self._parts.append(text)
            self._chars += len(text)
            self._blocks.append((page_no, block))
            return
        # 段落类：超预算先收尾再起新块（新块带上一块上下文重叠）
        if self._parts and self._chars + len(text) > self.max_chars:
            self.flush()
            self._start_with_overlap()
        self._parts.append(text)
        self._chars += len(text)
        self._blocks.append((page_no, block))

    def _start_with_overlap(self) -> None:
        for page_no, block in self._last_context[: self.overlap_blocks]:
            text = block_text(block)
            self._parts.append(text)
            self._chars += len(text)
            self._blocks.append((page_no, block))

    def flush(self) -> None:
        if not self._parts:
            return
        text = "\n\n".join(part for part in self._parts if part)
        refs = [BlockRef(page=page_no, index=block.index) for page_no, block in self._blocks]
        types = {block.type for _, block in self._blocks}
        chunk_type = _TYPE_LABELS.get(next(iter(types)), "mixed") if len(types) == 1 else "mixed"
        self.chunks.append(
            Chunk(
                chunk_id=f"c{len(self.chunks) + 1:04d}",
                order=len(self.chunks),
                type=chunk_type,
                text=text,
                page_start=min(ref.page for ref in refs),
                page_end=max(ref.page for ref in refs),
                block_refs=refs,
                bbox=_union_bbox([block.bbox for _, block in self._blocks]),
                char_count=len(text),
            )
        )
        self._last_context = [pair for pair in self._blocks if pair[1].type in _OVERLAP_TYPES][-self.overlap_blocks :] if self.overlap_blocks else []
        self._parts, self._chars, self._blocks = [], 0, []


def build_chunks(pages: Sequence[BlocksPage], *, book_id: str, params: ChunkParams | None = None) -> ChunkSet:
    """书级分块：页序列 → `ChunkSet`（跨页合并，公式/表格不拆）。"""
    params = params or ChunkParams()
    builder = _ChunkBuilder(max_chars=params.max_chars, overlap_blocks=params.overlap_blocks)
    for page in pages:
        for block in page.blocks:
            if block.type in _SKIP_TYPES:
                continue
            builder.add(page.page, block)
    builder.flush()
    return ChunkSet(book_id=book_id, params=params, chunks=builder.chunks)


def chunk_set_payload(chunk_set: ChunkSet) -> str:
    """chunks.json 文本（缩进 2，保持中文不转义）。"""
    return chunk_set.model_dump_json(indent=2)


def read_chunk_set(path: Path | str) -> ChunkSet:
    """读取 chunks.json。"""
    return ChunkSet.model_validate_json(Path(path).read_text(encoding="utf-8"))
