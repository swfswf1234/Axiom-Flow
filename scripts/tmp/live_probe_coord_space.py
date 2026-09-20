"""L3d 判定性实验：MinerU content_list bbox 的坐标空间（PDF 点 vs 归一化 0–1000）。

在仓库内构造一张 A4（595x842 pt）PDF，把文本钉在**已知的右下角位置**，整本提交后读原始
bbox：若返回值 ≈ 放置坐标（点）即点空间；若 ≈ 放置坐标 / 页尺寸 * 1000 即归一化空间。
不读写真实数据根，不写共享 qed 库。
用法：python scripts/tmp/live_probe_coord_space.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pymupdf  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.engines.factory import build_engine  # noqa: E402
from axiom_flow.engines.mineru import _load_json_field, _page_sizes  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "data" / "probes" / "live-bringup-20260920"
PDF = EVIDENCE / "L3d_anchor.pdf"

# 已知放置位置（PDF 点，左上原点）：右下角一行 + 右边缘一列 + 左上参照行
ANCHORS = [
    ("BOTTOMRIGHT", (420.0, 780.0), "BOTTOMRIGHT-ZZZZ"),
    ("RIGHTEDGE", (500.0, 400.0), "RIGHTEDGE-YYYY"),
    ("TOPLEFT", (60.0, 60.0), "TOPLEFT-WWWW"),
]


def build_pdf() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    for _, (x, y), text in ANCHORS:
        page.insert_text((x, y), text, fontsize=14, fontname="helv")
    PDF.write_bytes(doc.tobytes())
    doc.close()


def main() -> int:
    build_pdf()
    settings = load_settings()
    engine = build_engine("mineru", settings)
    data = engine._run(PDF.read_bytes(), PDF.name)  # noqa: SLF001 - 取证需原始载荷
    close = getattr(engine, "close", None)
    if callable(close):
        close()

    content_list = _load_json_field(data, "content_list") or []
    sizes = _page_sizes(data)
    rows = []
    for item in content_list:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", ""))
        bbox = item.get("bbox")
        anchor = next((name for name, _, t in ANCHORS if t[:9] in text.replace(" ", "")), None)
        rows.append({"anchor": anchor, "type": item.get("type"), "bbox": bbox, "text": text[:30]})
    report = {"page_size_pts": {str(k): list(v) for k, v in sorted(sizes.items())}, "items": rows}
    out = EVIDENCE / "L3d_coord_space.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("expected placement (pts) and normalized(/1000) equivalents:")
    for name, (_x, y), _ in ANCHORS:
        print(f"  {name}: pts y={y} -> normalized y={y / 842 * 1000:.0f}")
    for row in rows:
        print("  ", row)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
