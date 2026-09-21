"""
模块职责：对真机生效解析版本的 chunks.json 做只读完整性取证（公式不腰斩、分隔符平衡、
溯源可回跳、非噪声块全覆盖），产出 PASS/FAIL 判据与留档 JSON。
设计关联（DesignRef）：docs/design/parsing-pipeline.md
实现状态：Current
被测代码：src/axiom_flow/normalize/chunking.py（判据与其规则 3/5/7 对齐）
隔离声明：全程只读，不访问网络、不写真实数据根、不写运行数据库；结论写入 docs/。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from axiom_flow.schemas import BlockType  # noqa: E402

_SKIP_TYPES = {BlockType.HEADER, BlockType.FOOTER, BlockType.PAGE_NUMBER, BlockType.IMAGE}


def _load_blocks(pages_dir: Path) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for path in sorted(pages_dir.glob("p*.blocks.json")):
        page = int(path.stem.removeprefix("p").split(".")[0])
        payload = json.loads(path.read_text(encoding="utf-8"))
        out[page] = payload["blocks"]
    return out


def _index_of(raw: dict, position: int) -> int:
    """块的下标以文件内声明的 index 为准（丢弃为 0 时与列表位置一致）。"""
    declared = raw.get("index")
    return position if declared is None else int(declared)


def _render_formula(raw: dict) -> str:
    """公式块的检索文本（与 `chunking.block_text` 同规则：display 用 `$$`，否则 `$`）。"""
    latex = raw.get("latex") or ""
    return f"$$\n{latex}\n$$" if raw.get("display") else f"${latex}$"


def _dollar_balance(text: str) -> tuple[int, int]:
    """返回 (未配对的 $$ 数, 剥掉 $$ 对后未配对的 $ 数)。"""
    display = len(re.findall(r"\$\$", text))
    odd_display = display % 2
    singles = len(re.findall(r"(?<!\$)\$(?!\$)", text))
    return odd_display, singles % 2


def check(version_dir: Path) -> dict:
    chunks = json.loads((version_dir / "chunks.json").read_text(encoding="utf-8"))["chunks"]
    blocks_by_page = _load_blocks(version_dir / "pages")

    # 溯源目标集合与检索文本
    refs_seen: list[dict] = []
    for chunk in chunks:
        for ref in chunk["block_refs"]:
            refs_seen.append({"chunk_id": chunk["chunk_id"], **ref})
    dangling = [
        ref
        for ref in refs_seen
        if ref["page"] not in blocks_by_page
        or ref["index"] >= len(blocks_by_page[ref["page"]])
    ]

    # 公式块整块出现
    formula_total = 0
    formula_missing: list[dict] = []
    joined = {chunk["chunk_id"]: chunk["text"] for chunk in chunks}
    for page, raws in sorted(blocks_by_page.items()):
        for raw in raws:
            if raw.get("type") != "formula":
                continue
            formula_total += 1
            rendered = _render_formula(raw).strip()
            if not any(rendered in text for text in joined.values()):
                formula_missing.append({"page": page, "index": raw.get("index"), "latex": rendered[:120]})

    # 分隔符平衡
    unbalanced = []
    for chunk in chunks:
        odd_display, odd_single = _dollar_balance(chunk["text"])
        if odd_display or odd_single:
            unbalanced.append(
                {"chunk_id": chunk["chunk_id"], "odd_display": odd_display, "odd_single": odd_single}
            )

    # 非噪声块全覆盖
    covered = {(ref["page"], ref["index"]) for ref in refs_seen}
    expected: list[tuple[int, int]] = []
    type_counts: Counter[str] = Counter()
    for page, raws in sorted(blocks_by_page.items()):
        for position, raw in enumerate(raws):
            type_counts[str(raw.get("type"))] += 1
            try:
                block_type = BlockType(str(raw.get("type")))
            except ValueError:
                continue
            if block_type not in _SKIP_TYPES:
                expected.append((page, position))
    uncovered = [
        {"page": page, "index": index} for page, index in expected if (page, index) not in covered
    ]

    return {
        "version_dir": str(version_dir),
        "page_count": len(blocks_by_page),
        "raw_block_count": sum(type_counts.values()),
        "block_type_distribution": dict(sorted(type_counts.items())),
        "chunk_count": len(chunks),
        "formula_block_count": formula_total,
        "formula_blocks_missing_from_chunks": formula_missing,
        "chunks_with_unbalanced_dollar_delimiters": unbalanced,
        "dangling_block_refs": dangling,
        "uncovered_non_noise_blocks": uncovered,
        "passed": not (formula_missing or unbalanced or dangling or uncovered),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-dir", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    result = check(args.version_dir)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
    print("PASS" if result["passed"] else "FAIL")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
