"""V2-010 质量报告：对 data/books/01-rudin-trial/ 的识别产物做程序化质量统计。

统计项：独立公式块数、行内公式数、LaTeX 程序化可解析率（pylatexenc）、
空块率、失败页、预算明细（调用次数与 tokens）。人工抽查另行进行。

用法（QED_env）：
    & D:/software/anaconda3/envs/QED_env/python.exe scripts/analyze-trial.py [--book-id 01-rudin-trial]
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pymupdf
from pylatexenc.latex2text import LatexNodes2Text

from axiom_flow.ingest import default_data_dir

_INLINE_MATH_RE = re.compile(r"\$[^$\n]+\$")


def latex_parses(src: str) -> bool:
    """程序化可解析校验：pylatexenc 宽松解析（捕获结构性错误，不误伤合法命令）。"""
    if not src.strip():
        return False
    try:
        LatexNodes2Text().latex_to_text(src)
        return True
    except Exception:
        return False


def _inline_math(text: str) -> list[str]:
    return _INLINE_MATH_RE.findall(text)


def _load_blocks(book_dir: Path, page_no: int) -> dict | None:
    path = book_dir / f"p{page_no:04d}.blocks.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="V2-010 识别质量报告")
    parser.add_argument("--book-id", default="01-rudin-trial")
    parser.add_argument(
        "--source-pdf",
        default=r"D:/coding/QED-Engine/dataset/qed-tracker/raw/books/math-qe/01_math_analysis/01-rudin-en_Principles_of_Mathematical_Analysis.pdf",
        help="源 PDF（用于原生文本长度比，扫描件无文本层则跳过）",
    )
    args = parser.parse_args()

    book_dir = default_data_dir() / "books" / args.book_id
    summary_path = book_dir / "trial-summary.json"
    if not summary_path.is_file():
        print(f"[report] 缺少 trial-summary.json：{summary_path}")
        return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    source = pymupdf.open(args.source_pdf) if Path(args.source_pdf).is_file() else None

    formula_total = 0
    formula_ok = 0
    inline_total = 0
    inline_ok = 0
    empty_blocks = 0
    block_total = 0
    per_page: dict[str, dict] = {}

    for no in summary["pages_requested"]:
        page = _load_blocks(book_dir, no)
        if page is None:
            continue
        page_formula = 0
        page_inline = 0
        page_inline_ok = 0
        recognized_text = ""
        for block in page["blocks"]:
            block_total += 1
            recognized_text += block.get("text") or block.get("latex") or ""
            if block["type"] == "formula":
                formula_total += 1
                page_formula += 1
                if latex_parses(block.get("latex", "")):
                    formula_ok += 1
            elif block["type"] == "paragraph":
                for math in _inline_math(block.get("text", "")):
                    inline_total += 1
                    page_inline += 1
                    body = math.strip("$")
                    if latex_parses(body):
                        inline_ok += 1
                        page_inline_ok += 1
            if not (block.get("text") or block.get("latex")):
                empty_blocks += 1
        text_length_ratio = None
        if source is not None:
            native = source[no - 1].get_text().strip()
            if native:
                text_length_ratio = round(len(recognized_text) / len(native), 3)
        per_page[str(no)] = {
            "formula": page_formula,
            "inline_math": page_inline,
            "inline_ok": page_inline_ok,
            "text_length_ratio": text_length_ratio,
        }

    formula_rate = formula_ok / formula_total if formula_total else 1.0
    inline_rate = inline_ok / inline_total if inline_total else 1.0
    empty_rate = empty_blocks / block_total if block_total else 0.0
    total_tokens = sum(
        (p.get("usage") or {}).get("total_tokens", 0) for p in summary["per_page"].values()
    )

    report = {
        "book_id": args.book_id,
        "pages_success": len(summary["per_page"]),
        "pages_failed": summary["failed_pages"],
        "failed_details": summary["failed"],
        "formula_blocks": formula_total,
        "formula_parsable": formula_ok,
        "formula_parsable_rate": round(formula_rate, 4),
        "inline_math": inline_total,
        "inline_parsable": inline_ok,
        "inline_parsable_rate": round(inline_rate, 4),
        "empty_block_rate": round(empty_rate, 4),
        "calls": summary["calls"],
        "total_tokens": total_tokens,
        "text_length_ratio_low_pages": [
            no for no, p in per_page.items() if p["text_length_ratio"] is not None and p["text_length_ratio"] < 0.7
        ],
        "per_page": per_page,
    }
    (book_dir / "quality-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[report] 成功 {report['pages_success']} 页 / 失败 {report['pages_failed']} 页")
    print(f"[report] 独立公式块 {formula_total}（可解析 {formula_ok}，{formula_rate:.1%}）")
    print(f"[report] 行内公式 {inline_total}（可解析 {inline_ok}，{inline_rate:.1%}）")
    print(f"[report] 空块率 {empty_rate:.2%}；调用 {report['calls']} 次，tokens {total_tokens}")
    print(f"[report] 明细：{book_dir / 'quality-report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
