"""L3b 取证：整本路径下 MinerU 原始 content_list 与 middle_json 页尺寸对照（bbox 坐标系判定）。

只读引擎响应，不落任何业务产物；数据根仍限仓库内。
用法：python scripts/tmp/live_probe_bbox.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.engines.factory import build_engine  # noqa: E402
from axiom_flow.engines.mineru import _load_json_field, _page_sizes  # noqa: E402
from axiom_flow.ingest import png_size  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "data" / "probes" / "live-bringup-20260920"
ROOT = EVIDENCE / "root"
BOOK_DIR = ROOT / "parsed" / "probe" / "l3" / "l3full"
PDF = ROOT / "raw" / "probel3.pdf"


def main() -> int:
    settings = load_settings()
    engine = build_engine("mineru", settings)
    data = engine._run(PDF.read_bytes(), PDF.name)  # noqa: SLF001 - 取证需原始载荷
    close = getattr(engine, "close", None)
    if callable(close):
        close()

    content_list = _load_json_field(data, "content_list") or []
    sizes = _page_sizes(data)
    report: dict[str, object] = {
        "page_sizes_pts": {str(k): list(v) for k, v in sorted(sizes.items())},
        "image_sizes_px": {},
        "items": [],
    }
    for page_no in range(1, 6):
        png = BOOK_DIR / "pages" / f"p{page_no:04d}.png"
        if png.is_file():
            report["image_sizes_px"][str(page_no)] = list(png_size(png))
    for item in content_list:
        if not isinstance(item, dict):
            continue
        report["items"].append({
            "type": item.get("type"),
            "page_idx": item.get("page_idx"),
            "bbox": item.get("bbox"),
            "text": str(item.get("text", ""))[:40],
        })
    out = EVIDENCE / "L3b_raw_bbox.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("page_sizes_pts:", report["page_sizes_pts"])
    print("image_sizes_px:", report["image_sizes_px"])
    for row in report["items"][:8] if isinstance(report["items"], list) else []:
        print("  ", row)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
