"""V2-010 实验脚本：Rudin 1-20 页 qwen-vl-plus 识别 → 落盘 data/books/01-rudin-trial/。

用法（QED_env）：
    & D:/software/anaconda3/envs/QED_env/python.exe scripts/run-qwen-trial.py [--pages 1-20] [--limit N]

冻结项（见 docs/plans/2026-08-qwen-vl-plus-trial.md）：样本哈希、预算 ≤30 元（每页最多 3 次尝试）、
采纳门槛（公式 LaTeX 可解析率 ≥90%）。失败页记录在 summary.json，不中断。
"""

import argparse
import json
import os
import sys
from pathlib import Path

import pymupdf  # 读页图尺寸（fitz 已弃用，pymupdf 为官方名）

from axiom_flow.fallback import QwenVLPlusClient, VisionError, markdown_to_blocks
from axiom_flow.ingest import default_data_dir, import_book
from axiom_flow.schemas import Strategy

SOURCE_PDF = Path(
    r"D:\coding\QED-Engine\dataset\qed-tracker\raw\books\math-qe\01_math_analysis"
    r"\01-rudin-en_Principles_of_Mathematical_Analysis.pdf"
)
BOOK_ID = "01-rudin-trial"
EXPECTED_SHA256 = "923b831558ed161ee49a68b1381c1301f53aae2c84745fcad0384f24db2ce01e"


def _load_dotenv(path: Path) -> None:
    """轻量加载仓库根 .env（不覆盖已有环境变量），供凭据与阈值使用。"""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def parse_pages(spec: str) -> list[int]:
    """解析 '1-20' 或 '1,3,5' 格式为页码列表。"""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            pages.extend(range(start, end + 1))
        else:
            pages.append(int(part))
    return sorted(set(pages))


def png_size(png: Path) -> tuple[int, int]:
    """读 PNG 像素尺寸（150 DPI 页图，bbox 占位基准）。"""
    doc = pymupdf.open(png)
    try:
        pix = doc[0]
        return pix.rect.width, pix.rect.height
    finally:
        doc.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="V2-010 qwen-vl-plus 最小闭环实验")
    parser.add_argument("--pages", default="1-20", help="页码范围，如 '1-20' 或 '1,3,5'（默认 1-20）")
    parser.add_argument("--limit", type=int, default=0, help="预算保护：最多调用次数（0 = 不限制）")
    args = parser.parse_args()

    _load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    pages = parse_pages(args.pages)
    print(f"[trial] 样本校验：{SOURCE_PDF.name}")
    meta = import_book(SOURCE_PDF, BOOK_ID, "Principles of Mathematical Analysis", "Walter Rudin", Strategy.LOCAL)
    if meta.sha256 != EXPECTED_SHA256:
        print(f"[trial] 样本哈希不符：{meta.sha256} != {EXPECTED_SHA256}，中止")
        return 1
    print(f"[trial] 导入完成：{meta.page_count} 页，sha256={meta.sha256[:12]}…")

    client = QwenVLPlusClient()
    book_dir = default_data_dir() / "books" / BOOK_ID
    summary: dict = {"book_id": BOOK_ID, "pages_requested": pages, "per_page": {}, "calls": 0, "failed": []}
    total_formula = 0

    for no in pages:
        if args.limit and summary["calls"] >= args.limit:
            print(f"[trial] 达到预算保护上限（{args.limit} 次），提前停止")
            break
        png = book_dir / "pages" / f"p{no:04d}.png"
        if not png.is_file():
            print(f"[trial] 页图缺失：{png}")
            summary["failed"].append({"page": no, "reason": "页图缺失"})
            continue
        width, height = png_size(png)
        try:
            result = client.recognize_page(png, page_no=no)
        except VisionError as exc:
            summary["failed"].append({"page": no, "reason": str(exc)[:200]})
            print(f"[trial] 第 {no} 页失败：{exc}")
            continue

        page = markdown_to_blocks(result.markdown, page=no, page_width=int(width), page_height=int(height))
        formula_count = sum(1 for b in page.blocks if b.type.value == "formula")
        total_formula += formula_count
        pages_dir = book_dir / "pages"
        (pages_dir / f"p{no:04d}.md").write_text(result.markdown, encoding="utf-8")
        (pages_dir / f"p{no:04d}.blocks.json").write_text(page.model_dump_json(indent=2) + "\n", encoding="utf-8")
        summary["per_page"][str(no)] = {
            "blocks": len(page.blocks),
            "formulas": formula_count,
            "calls": 1,
            "usage": result.usage,
        }
        summary["calls"] += 1
        print(f"[trial] 第 {no} 页：{len(page.blocks)} 块（公式 {formula_count}），tokens={result.usage or '?'}")

    summary["formula_blocks_total"] = total_formula
    summary["failed_pages"] = len(summary["failed"])
    (book_dir / "trial-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[trial] 完成：成功 {summary['calls'] - summary['failed_pages']} 页，失败 {summary['failed_pages']} 页，"
          f"公式块 {total_formula}，明细见 {book_dir / 'trial-summary.json'}")
    return 0 if not summary["failed"] else 2


if __name__ == "__main__":
    sys.exit(main())
