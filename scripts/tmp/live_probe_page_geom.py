"""L3c 取证：单页（PNG）路径的坐标系判定——middle_json.page_size vs 页图实际像素。

页图经 8902 只读端点取回（不读真实数据根），引擎原始载荷落在仓库内留档。
用法：python scripts/tmp/live_probe_page_geom.py <book_id> <page_no>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.engines.factory import build_engine  # noqa: E402
from axiom_flow.ingest import png_size  # noqa: E402
from axiom_flow.normalize.blocks import normalize_page  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "data" / "probes" / "live-bringup-20260920"


def main() -> int:
    book_id, page_no = sys.argv[1], int(sys.argv[2])
    settings = load_settings()
    api = f"http://127.0.0.1:{settings.port}/api/v1"
    with httpx.Client(trust_env=False, timeout=30.0) as client:
        resp = client.get(f"{api}/books/{book_id}/pages/{page_no}/image")
        resp.raise_for_status()
        image_bytes = resp.content
    png = EVIDENCE / f"L3c_p{page_no:04d}.png"
    png.write_bytes(image_bytes)
    print("page image bytes =", len(image_bytes), "png_size =", png_size(png))

    engine = build_engine("mineru", settings)
    result = engine.parse_page(image_bytes, page_no)
    close = getattr(engine, "close", None)
    if callable(close):
        close()

    raw = [
        {
            "type": item.get("type"),
            "bbox": item.get("bbox"),
            "text": str(item.get("text", ""))[:30],
        }
        for item in (result.blocks or [])
    ]
    report = {
        "book_id": book_id,
        "page_no": page_no,
        "image_size_px": list(png_size(png)),
        "engine_page_size": list(result.page_size) if result.page_size else None,
        "raw_block_count": len(raw),
        "raw_blocks": raw,
    }
    page = normalize_page(result, source=engine.name, image_size=png_size(png))
    report["normalized"] = [
        {"type": b.type.value, "bbox": list(b.bbox)} for b in page.blocks
    ]
    out = EVIDENCE / f"L3c_page{page_no:04d}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("engine page_size (pts?) =", report["engine_page_size"])
    for row in raw[:12]:
        print("   raw", row["type"], row["bbox"], repr(row["text"][:20]))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
