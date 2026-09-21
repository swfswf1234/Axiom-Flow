"""ARCH-009 代表书取证（只读）：块类型分布 + 可见丢弃 + 公式 LaTeX 程序化可解析率。

用法：
    python scripts/tmp/live_forensic_representative.py --version-dir <版本目录> --book-id <id> \
        --pages 1,2,3,... --out <json>
    python scripts/tmp/live_forensic_representative.py --selftest    # 反向判据：坏样本必须变红

判据口径（用户 2026-09-22 裁决「程序化可解析 + 人工留档」，不引入 KaTeX 渲染依赖）：
1. **类型分布**：抽样页的 `BlockType` 分布与每页 `dropped_block_count`/`dropped_types` 汇总；
   丢弃必须为 0，否则原始字面值清单即为映射缺口（DEFECT-005 的可见丢弃条款）。
2. **公式可解析率**：对每个 formula 块的 `latex` 与页 markdown 的行内 `$…$` 段做结构校验
   （定界符配对、`\\begin/\\end` 环境配对、花括号/方括号平衡、体内非空），可解析段数 / 总段数
   ≥ 90% 为 PASS。这是「可被 KaTeX 正确渲染」的**保守代理**（结构不闭合必然渲染失败），
   人工抽查另附留档，不声称本判据覆盖渲染语义。
3. `--selftest` 用三类人为缺陷（未闭合环境 / 括号失衡 / 空体）断言检出，防止「恒 PASS 的检查器」。

隔离：只读版本目录，不访问网络、不写库。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from axiom_flow.normalize.blocks import _TYPE_MAP  # noqa: E402

_BEGIN_END = re.compile(r"\\begin\{([^}]*)\}")
_END = re.compile(r"\\end\{([^}]*)\}")
_RENDER_THRESHOLD = 0.90


def _strip_escaped(math: str) -> str:
    """去掉 `\\{` `\\}` `\\%` 等转义符对，避免把 LaTeX 转义记号当结构符号。"""
    return re.sub(r"\\([{}\\%$&_#~^])", "", math)


def check_latex(math: str) -> str | None:
    """返回缺陷标签；None 表示通过。"""
    body = math.strip()
    if not body:
        return "empty"
    inner = _strip_escaped(body)
    if inner.count("$$") % 2:
        return "unbalanced_display"
    stripped = inner.replace("$$", "")
    if stripped.count("$") % 2:
        return "unbalanced_inline"
    begins = _BEGIN_END.findall(stripped)
    ends = _END.findall(stripped)
    if Counter(begins) != Counter(ends):
        return "unmatched_environment"
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        if stripped.count(open_ch) != stripped.count(close_ch):
            return f"unbalanced_{open_ch}"
    if not re.sub(r"\$+|\s", "", stripped):
        return "empty"
    return None


def _inline_math(page_md: str) -> list[str]:
    """页 markdown 的行内/行间公式段，**每页只取一次**（按块迭代会把同一段重复计入）。"""
    segs: list[str] = []
    for match in re.finditer(r"\$\$(.+?)\$\$", page_md, flags=re.S):
        segs.append(match.group(0))
    for match in re.finditer(r"(?<!\$)\$([^$\n]+?)\$(?!\$)", page_md):
        segs.append(match.group(0))
    return segs


def forensic(version_dir: Path, pages: list[int]) -> dict:
    pages_dir = version_dir / "pages"
    type_counts: Counter[str] = Counter()
    dropped_total = 0
    dropped_literals: Counter[str] = Counter()
    bad: list[dict] = []
    segments = 0
    per_page: dict[str, dict] = {}
    missing: list[int] = []
    for page in pages:
        path = pages_dir / f"p{page:04d}.blocks.json"
        if not path.is_file():
            candidates = list(pages_dir.glob(f"p{page}.blocks.json"))
            if not candidates:
                missing.append(page)
                continue
            path = candidates[0]
        payload = json.loads(path.read_text(encoding="utf-8"))
        blocks = payload.get("blocks") or []
        md_path = pages_dir / f"{path.stem.split('.')[0]}.md"
        page_md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
        types = Counter(str(b.get("type")) for b in blocks)
        type_counts.update(types)
        quality = payload.get("quality") or {}
        dropped_total += int(quality.get("dropped_block_count") or 0)
        for literal in quality.get("dropped_types") or []:
            dropped_literals[str(literal)] += 1
        # 来源两段分开：块级 latex 是交付给前端的正本，页 md 行内段是渲染侧视图
        subjects: list[tuple[str, str]] = [
            (f"block:{b.get('index')}", b.get("latex") or b.get("text") or "<empty-formula-block>")
            for b in blocks if b.get("type") == "formula"
        ] + [(f"md:{i}", seg) for i, seg in enumerate(_inline_math(page_md))]
        page_bad = []
        for origin, seg in subjects:
            segments += 1
            reason = check_latex(seg)
            if reason:
                page_bad.append({"origin": origin, "reason": reason, "snippet": seg[:120]})
        if page_bad:
            bad.append({"page": page, "items": page_bad})
        per_page[str(page)] = {
            "blocks": len(blocks),
            "types": dict(types),
            "dropped": int(quality.get("dropped_block_count") or 0),
            "dropped_types": quality.get("dropped_types") or [],
            "md_formula_segments": len(_inline_math(page_md)),
        }
    failed = sum(len(b["items"]) for b in bad)
    rate = 1.0 if segments == 0 else (segments - failed) / segments
    observed = set(type_counts)
    expected = {"paragraph", "heading", "formula", "table", "image", "caption", "header", "footer", "page_number", "list"}
    return {
        "version_dir": str(version_dir),
        "pages_requested": pages,
        "pages_missing_products": missing,
        "block_type_distribution": dict(type_counts),
        "dropped_block_count_total": dropped_total,
        "dropped_raw_literals": dict(dropped_literals),
        "block_types_never_observed": sorted(expected - observed),
        "type_map_keys": sorted(_TYPE_MAP),
        "latex_segments_checked": segments,
        "latex_segments_failed": failed,
        "latex_parseable_rate": round(rate, 4),
        "latex_failures": bad,
        "per_page": per_page,
        "judgement": {
            "zero_drop": dropped_total == 0 and not missing,
            "renderable_rate_ok": rate >= _RENDER_THRESHOLD,
            "rate_threshold": _RENDER_THRESHOLD,
        },
    }


def selftest() -> int:
    """反向判据：三类人为缺陷必须各自变红。"""
    cases = {
        "未闭合环境": r"$$\begin{aligned} x=1 $$",
        "括号失衡": r"$$\frac{1}{2 $$",
        "空体": r"$$  $$",
        "行内未闭合": r"$\alpha + \beta",
    }
    failures = {}
    for label, math in cases.items():
        reason = check_latex(math)
        if reason is None:
            failures[label] = "检查器判为通过（应判红）"
        else:
            print(f"  RED ok：{label} → {reason}")
    ok = check_latex(r"$$\begin{aligned} p \in \alpha + \beta, \end{aligned}$$")
    if ok is not None:
        failures["合法公式"] = f"检查器判为失败（应判绿）：{ok}"
    if failures:
        print("SELFTEST FAIL：" + "; ".join(failures.values()))
        return 1
    print("SELFTEST PASS：4 类缺陷全检出、合法公式判绿")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version-dir", type=Path)
    parser.add_argument("--book-id", default="")
    parser.add_argument("--pages", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if not args.version_dir or not args.pages:
        parser.error("需要 --version-dir 与 --pages（或 --selftest）")
    pages = [int(p) for p in args.pages.split(",") if p.strip()]
    result = forensic(args.version_dir, pages)
    result["book_id"] = args.book_id
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    verdict = result["judgement"]
    print(
        f"{args.book_id or args.version_dir.name}：页 {len(pages) - len(result['pages_missing_products'])}、"
        f"丢弃 {result['dropped_block_count_total']}（原始字面值 {result['dropped_raw_literals']}）、"
        f"公式段 {result['latex_segments_checked']} 可解析率 {result['latex_parseable_rate']:.2%}、"
        f"未出现类型 {result['block_types_never_observed']} → "
        f"{'PASS' if verdict['zero_drop'] and verdict['renderable_rate_ok'] else 'FAIL'}"
    )
    return 0 if (verdict["zero_drop"] and verdict["renderable_rate_ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
