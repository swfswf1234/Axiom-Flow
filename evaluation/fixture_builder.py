"""
模块职责：从项目自有版式定义重建公开数学 PDF、replay 输入和标准解析金标。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/test_evaluation_regression.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import fitz

from evaluation.replay import materialize_fixture

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
FIXTURE_ID = "math-sample-v1"
RUN_ID = "fixture-replay-v1"


def build_fixture(output_dir: Path) -> Path:
    """重建公开 fixture；目标目录只允许保存本模块生成的版本化样本。"""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = _page_specs()
    source = output_dir / "source.pdf"
    _write_pdf(source, pages)
    source_hash = _sha256(source)

    replay_pages = output_dir / "replay" / "pages"
    replay_pages.mkdir(parents=True, exist_ok=True)
    for stale in replay_pages.glob("page-*.json"):
        stale.unlink()
    for page in pages:
        _write_json(replay_pages / f"page-{page['page_no']:04d}.json", page)

    source_markdown = "\n\n".join(
        f"## 第 {page['page_no']} 页\n\n{page['markdown']}" for page in pages
    )
    (output_dir / "source.md").write_text(
        "# 数学分析公开回归样本源稿\n\n"
        "本文件与 `source.pdf` 由 Axiom-Flow 项目自行创作，用于解析回归，不摘录任何教材。\n\n"
        + source_markdown
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "license.md").write_text(
        "# 样本许可\n\n"
        "`source.pdf`、`source.md`、`replay/` 和 `expected/` 是 Axiom-Flow 项目自制测试资产，"
        "以 CC0 1.0 Universal 贡献给公共领域。第三方可复制、修改和再分发。\n",
        encoding="utf-8",
    )
    fixture = {
        "schema_version": 1,
        "fixture_id": FIXTURE_ID,
        "license": "CC0-1.0",
        "normalization_contract": "nfc-newline-latex-v1",
        "bbox_iou_threshold": 0.75,
        "source": {"path": "source.pdf", "sha256": source_hash, "page_count": len(pages)},
        "replay": {
            "run_id": RUN_ID,
            "provider": {
                "adapter": "FixtureReplayProvider",
                "vision_model": "fixture-replay",
                "contract_version": "fixture-page-v1",
                "model_call_budget": 0,
                "max_tokens": 0,
                "max_attempts_per_page": 1,
                "page_range": {"start": 1, "end": len(pages), "inclusive": True},
            },
        },
        "expected_run": f"documents/{source_hash}/parse-runs/{RUN_ID}",
    }
    _write_json(output_dir / "fixture.json", fixture)

    expected = output_dir / "expected"
    if expected.exists():
        shutil.rmtree(expected)
    materialize_fixture(output_dir, expected)
    return output_dir


def _page_specs() -> list[dict[str, Any]]:
    page1_markdown = r"""# Mathematical Analysis Fixture

数学分析回归样本：本页验证中英文正文、标题层级与行内公式。

For a sequence $(a_n)$, convergence to $L$ means every epsilon admits a tail bound.

$$\lim_{n \to \infty} a_n = L$$"""
    page2_markdown = r"""# Theorem and Proof

## Theorem

If $f$ is differentiable at $x_0$, then $f$ is continuous at $x_0$.

## Proof

Write $f(x)-f(x_0)$ as a difference quotient times $x-x_0$ and take the limit.

1. Isolate the difference quotient.
2. Apply the product limit law.

$$f(x)-f(x_0)=\frac{f(x)-f(x_0)}{x-x_0}(x-x_0)$$"""
    table = """| n | 1 | 2 | 4 | 8 |
| --- | --- | --- | --- | --- |
| 1/n | 1 | 0.5 | 0.25 | 0.125 |"""
    page3_markdown = f"""# Table and Figure

The table records a convergent sequence.

{table}

Figure 1. Secant slopes approaching the tangent line."""
    page4_markdown = """# Two-column Reading Order

## Left column

Definition. A set is open when every point has a contained neighborhood.

Example. Every open interval $(a,b)$ is open in the real line.

## Right column

Definition. A set is closed when it contains all of its limit points.

Observation. Complements exchange open and closed sets."""

    return [
        _page(1, page1_markdown, [
            _block(0, "heading", "Mathematical Analysis Fixture", [50, 48, 545, 86]),
            _block(1, "paragraph", "数学分析回归样本：本页验证中英文正文、标题层级与行内公式。", [50, 112, 545, 150]),
            _block(2, "paragraph", "For a sequence $(a_n)$, convergence to $L$ means every epsilon admits a tail bound.", [50, 184, 545, 235]),
            _block(3, "formula", "$$\\lim_{n \\to \\infty} a_n = L$$", [130, 280, 465, 330], latex="\\lim_{n \\to \\infty} a_n = L"),
        ]),
        _page(2, page2_markdown, [
            _block(0, "heading", "Theorem and Proof", [50, 48, 545, 86]),
            _block(1, "heading", "Theorem", [50, 110, 545, 145]),
            _block(2, "paragraph", "If $f$ is differentiable at $x_0$, then $f$ is continuous at $x_0$.", [50, 155, 545, 200]),
            _block(3, "heading", "Proof", [50, 225, 545, 260]),
            _block(4, "paragraph", "Write $f(x)-f(x_0)$ as a difference quotient times $x-x_0$ and take the limit.", [50, 270, 545, 320]),
            _block(5, "list", "1. Isolate the difference quotient.\n2. Apply the product limit law.", [70, 345, 520, 405]),
            _block(6, "formula", "$$f(x)-f(x_0)=\\frac{f(x)-f(x_0)}{x-x_0}(x-x_0)$$", [70, 445, 525, 500], latex="f(x)-f(x_0)=\\frac{f(x)-f(x_0)}{x-x_0}(x-x_0)"),
        ]),
        _page(3, page3_markdown, [
            _block(0, "heading", "Table and Figure", [50, 48, 545, 86]),
            _block(1, "paragraph", "The table records a convergent sequence.", [50, 105, 545, 140]),
            _block(2, "table", table, [60, 160, 535, 285]),
            _block(3, "figure", "Figure 1. Secant slopes approaching the tangent line.", [90, 350, 505, 610]),
        ]),
        _page(4, page4_markdown, [
            _block(0, "heading", "Two-column Reading Order", [50, 48, 545, 86]),
            _block(1, "heading", "Left column", [45, 115, 275, 150]),
            _block(2, "paragraph", "Definition. A set is open when every point has a contained neighborhood.", [45, 160, 275, 245]),
            _block(3, "paragraph", "Example. Every open interval $(a,b)$ is open in the real line.", [45, 275, 275, 355]),
            _block(4, "heading", "Right column", [320, 115, 550, 150]),
            _block(5, "paragraph", "Definition. A set is closed when it contains all of its limit points.", [320, 160, 550, 245]),
            _block(6, "paragraph", "Observation. Complements exchange open and closed sets.", [320, 275, 550, 355]),
        ]),
    ]


def _page(page_no: int, markdown: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    first = blocks[0]
    return {
        "page_no": page_no,
        "page_kind": "content",
        "markdown": markdown,
        "blocks": blocks,
        "evidence": [{
            "kind": "text_quote",
            "page_no": page_no,
            "quote": first["content"],
            "bbox": first["bbox"],
        }],
        "quality": {
            "status": "accepted_fixture",
            "issues": [],
            "page_kind": "content",
            "native_text_chars": len(markdown),
            "ink_ratio": 0.1,
            "provider": {"contract_version": "fixture-page-v1", "replayed": True},
        },
    }


def _block(
    order_no: int, kind: str, content: str, bbox: list[float], *, latex: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "content": content,
        "latex": latex,
        "quote": content[:240],
        "order_no": order_no,
        "bbox": bbox,
        "confidence": 1.0,
        "source": "fixture-gold",
    }


def _write_pdf(path: Path, pages: list[dict[str, Any]]) -> None:
    document = fitz.open()
    document.set_metadata({
        "title": "Axiom-Flow Mathematical Analysis Fixture",
        "author": "Axiom-Flow",
        "subject": "Public deterministic PDF parsing fixture",
        "keywords": "mathematics, parsing, regression",
        "creator": "Axiom-Flow fixture_builder",
        "producer": "PyMuPDF",
        "creationDate": "D:20260729000000+08'00'",
        "modDate": "D:20260729000000+08'00'",
    })
    for spec in pages:
        page = document.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.draw_rect(page.rect, color=(0.2, 0.2, 0.2), width=0.7)
        for block in spec["blocks"]:
            rect = fitz.Rect(block["bbox"])
            kind = block["kind"]
            if kind == "table":
                _draw_table(page, rect)
            elif kind == "figure":
                _draw_figure(page, rect)
            else:
                text = _display_text(block["content"])
                font_name = "china-s" if any(ord(char) > 127 for char in text) else "helv"
                font_size = 18 if kind == "heading" and block["order_no"] == 0 else 11
                if kind == "formula":
                    font_name = "cour"
                    font_size = 11
                page.insert_textbox(rect, text, fontname=font_name, fontsize=font_size, lineheight=1.25)
        page.insert_text((PAGE_WIDTH - 70, PAGE_HEIGHT - 28), str(spec["page_no"]), fontname="helv", fontsize=9)
    document.save(path, garbage=4, deflate=True, no_new_id=True)
    document.close()


def _draw_table(page: fitz.Page, rect: fitz.Rect) -> None:
    rows, columns = 2, 5
    row_height = rect.height / rows
    column_width = rect.width / columns
    for row in range(rows + 1):
        y = rect.y0 + row * row_height
        page.draw_line((rect.x0, y), (rect.x1, y), color=(0, 0, 0), width=0.8)
    for column in range(columns + 1):
        x = rect.x0 + column * column_width
        page.draw_line((x, rect.y0), (x, rect.y1), color=(0, 0, 0), width=0.8)
    values = [["n", "1", "2", "4", "8"], ["1/n", "1", "0.5", "0.25", "0.125"]]
    for row, values_row in enumerate(values):
        for column, value in enumerate(values_row):
            cell = fitz.Rect(
                rect.x0 + column * column_width + 5,
                rect.y0 + row * row_height + 5,
                rect.x0 + (column + 1) * column_width - 5,
                rect.y0 + (row + 1) * row_height - 5,
            )
            page.insert_textbox(cell, value, fontname="helv", fontsize=10, align=fitz.TEXT_ALIGN_CENTER)


def _draw_figure(page: fitz.Page, rect: fitz.Rect) -> None:
    origin = fitz.Point(rect.x0 + 35, rect.y1 - 45)
    page.draw_line(origin, (rect.x1 - 25, origin.y), color=(0, 0, 0), width=1)
    page.draw_line(origin, (origin.x, rect.y0 + 25), color=(0, 0, 0), width=1)
    curve = [
        fitz.Point(origin.x + offset, origin.y - (offset * offset) / 520)
        for offset in range(0, int(rect.width - 80), 8)
    ]
    for left, right in zip(curve, curve[1:], strict=False):
        page.draw_line(left, right, color=(0.1, 0.35, 0.65), width=1.4)
    page.draw_line(curve[5], curve[-4], color=(0.75, 0.2, 0.15), width=1)
    caption = fitz.Rect(rect.x0 + 10, rect.y1 - 30, rect.x1 - 10, rect.y1 - 5)
    page.insert_textbox(caption, "Figure 1. Secant slopes approaching the tangent line.", fontname="helv", fontsize=9, align=fitz.TEXT_ALIGN_CENTER)


def _display_text(content: str) -> str:
    return content.replace("$$", "").replace("$", "").replace("\\lim", "lim").replace("\\to", "->").replace("\\infty", "infinity").replace("\\frac", "frac")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(build_fixture(args.output))


if __name__ == "__main__":
    main()
