"""ARCH-009 抽查口径的选页（只读）：首/中/尾 + 公式密集页，输出每本 20 页的页码清单。

用法：python scripts/tmp/live_pick_sample_pages.py <book_id> <pdf 绝对路径> <总页数>

「公式密集」有两种近似，只用于挑页、不参与任何质量判据：
1. **文本层模式**：PDF 原生文本的数学记号密度（有文本层时优先）；
2. **墨度模式**：代表书是扫描件（文本层 0 字符，2026-09-22 实测 b05/b11 皆如此），此时按低分
   辨率栅格化的深色像素占比排序——题解类扫描书的公式密集页同时是墨迹最重的页。
选页结果连同所用模式写进窗口留档，保证复跑用同一批页。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

_MATH = re.compile(
    r"\\frac|\\sum|\\int|\\lim|\\partial|\\infty|\\alpha|\\beta|\\gamma|\\theta|"
    r"\^\(|\^\{|_\{|\\left|\\right|\\to|\\subseteq|\\cup|\\cap|\\sqrt|\\,|\|\|"
)

#: 墨度模式的栅格缩放（0.3 ≈ 20 DPI）：只用于排序，够分辨「重页 / 轻页」即可。
_INK_ZOOM = 0.3
_INK_DARK_THRESHOLD = 160


def _text_density(doc: pymupdf.Document) -> list[tuple[int, int]]:
    return [(len(_MATH.findall(doc[i].get_text())), i + 1) for i in range(doc.page_count)]


def _ink_density(doc: pymupdf.Document) -> list[tuple[int, int]]:
    """每页深色像素占比（放大 1000 倍取整，便于与文本层模式共用排序）。"""
    matrix = pymupdf.Matrix(_INK_ZOOM, _INK_ZOOM)
    scores: list[tuple[int, int]] = []
    for page in doc:
        pix = page.get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY)
        samples = pix.samples
        dark = sum(1 for b in samples if b < _INK_DARK_THRESHOLD)
        scores.append((int(dark * 1000 / len(samples)), page.number + 1))
    return scores


def pick(pdf: Path, total: int) -> dict[str, list[int]]:
    doc = pymupdf.open(pdf)
    try:
        density = _text_density(doc)
        mode = "text-layer"
        if max(s for s, _ in density) == 0:
            # 无文本层（扫描件）：文本层密度全为 0 会让「公式密集」退化成随机页
            density = _ink_density(doc)
            mode = "ink-coverage"
    finally:
        doc.close()
    head = list(range(1, 4))
    middle = [total // 2 - 3 + i for i in range(7)]
    tail = [total - 5 + 1 + i for i in range(5)]
    positional = set(head + middle + tail)
    dense_pages: list[int] = []
    # 密集页只在首/中/尾之外挑，否则去重后凑不满「每本 20 页」的抽查口径
    for score, page in sorted(density, reverse=True):
        if score < 3 or len(dense_pages) >= 5:
            break
        if page in positional or any(abs(page - got) < 15 for got in dense_pages):
            continue
        dense_pages.append(page)
    pages = sorted(positional | set(dense_pages))
    return {
        "density_mode": mode,
        "head": head,
        "middle": middle,
        "tail": tail,
        "formula_dense": sorted(dense_pages),
        "pages": pages,
        "top_density": [
            {"page": p, "score": s} for s, p in sorted(density, reverse=True)[:8]
        ],
    }


def main() -> int:
    book_id, pdf, total = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3])
    out = pick(pdf, total)
    out["book_id"] = book_id
    out["pdf"] = str(pdf)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
