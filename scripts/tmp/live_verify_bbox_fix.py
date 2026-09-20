"""DEFECT-004 修复后的真机端到端锚点验证（REQ-081 回执证据）。

做法：合成 A4 PDF 内把文本钉在**已知 PDF 点坐标**→ 真实 MinerU 5002 + 真实 `run_parse_job`
（整本路径）→ 读产物 `p0001.blocks.json` → 与 PDF 原生文本矩形（点 × dpi/72 折算像素）逐块比对，
输出偏移与 IoU。同时按**旧公式**（将归一化 bbox 误当 PDF 点，按 image/page_size 过放）复算一份
对照，证明旧实现必然贴边夹取。

数据根与库均在仓库内临时目录（`data/probes/live-bbox-fix-20260921/`），不触碰真实
`QED_DATA_ROOT`、不写共享 `qed` 库、不读 `raw/`。

用法：python scripts/tmp/live_verify_bbox_fix.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pymupdf  # noqa: E402
from sqlalchemy.engine import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.models import Base  # noqa: E402
from axiom_flow.db.repositories import (  # noqa: E402
    BookRepository,
    BookSyncRow,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.factory import build_engine  # noqa: E402
from axiom_flow.ingest import ingest_book  # noqa: E402
from axiom_flow.orchestrator.pipeline import DEFAULT_DPI, run_parse_job  # noqa: E402
from axiom_flow.schemas import JobStatus  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "data" / "probes" / "live-bbox-fix-20260921"
ROOT = EVIDENCE / "root"
BOOK_ID = "bboxfix"
JOB_ID = "bboxfixjob01"
PAGE_W_PT, PAGE_H_PT = 595.0, 842.0

LINES = [
    (60.0, 90.0, "ALPHA intro line at top left"),
    (60.0, 300.0, "BRAVO middle line of the page"),
    (60.0, 520.0, "CHARLIE lower middle line here"),
    (60.0, 760.0, "DELTA near bottom of the page"),
    (240.0, 400.0, "ECHO indented centre column"),
]


def _build_pdf(path: Path) -> dict[str, tuple[int, int, int, int]]:
    """生成样本 PDF，并返回每行文本的**像素**期望框（PDF 原生矩形 × dpi/72）。"""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W_PT, height=PAGE_H_PT)
    for x, y, text in LINES:
        page.insert_text((x, y), text, fontsize=14, fontname="helv")
    scale = DEFAULT_DPI / 72.0
    expected: dict[str, tuple[int, int, int, int]] = {}
    for block in page.get_text("blocks"):
        x0, y0, x1, y1, text = block[0], block[1], block[2], block[3], str(block[4])
        key = text.strip().split(" ")[0]
        expected[key] = (round(x0 * scale), round(y0 * scale), round(x1 * scale), round(y1 * scale))
    doc.save(path)
    doc.close()
    return expected


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return round(inter / union, 4) if union > 0 else 0.0


def _legacy_bbox(fixed: tuple[int, int, int, int], *, page_size_pt, image_size) -> tuple[int, int, int, int]:
    """旧实现（DEFECT-004）示意复算：把 0–1000 归一化值当 PDF 点，按 image/page_size 过放后夹取。

    修复后 bbox 已满足 `px = raw / 1000 * image`，故 `raw = px * 1000 / image`，再套旧缩放即得对照值。
    """
    sx, sy = image_size[0] / page_size_pt[0], image_size[1] / page_size_pt[1]
    bbox = tuple(
        round(v * 1000.0 / image_size[i % 2] * s)
        for i, (v, s) in enumerate(zip(fixed, (sx, sy, sx, sy), strict=True))
    )
    x0 = max(0, min(bbox[0], image_size[0] - 1))
    y0 = max(0, min(bbox[1], image_size[1] - 1))
    x1 = max(x0 + 1, min(bbox[2], image_size[0]))
    y1 = max(y0 + 1, min(bbox[3], image_size[1]))
    return (x0, y0, x1, y1)


def main() -> int:
    shutil.rmtree(ROOT, ignore_errors=True)
    (ROOT / "raw").mkdir(parents=True)
    pdf = ROOT / "raw" / f"{BOOK_ID}.pdf"
    expected = _build_pdf(pdf)

    settings = load_settings()
    db_file = EVIDENCE / "bboxfix.db"
    db_file.unlink(missing_ok=True)
    db_engine = create_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(db_engine)

    with Session(db_engine) as session:
        BookRepository(session).upsert_many([BookSyncRow(
            book_id=BOOK_ID, domain_id="probe", course_id="bbox", course_name="坐标验证",
            knowledge_id="", title="bbox anchor verification", part="",
            display_title="bbox anchor verification",
            authors=[{"name": "probe", "role": "author"}], file_path=f"raw/{pdf.name}",
        )])
        session.commit()
        ing = ingest_book(book_id=BOOK_ID, source_file=f"raw/{pdf.name}", data_root=ROOT,
                          domain_id="probe", course_id="bbox", title="bbox anchor verification",
                          engine="mineru")
        BookRepository(session).update_ingest(BOOK_ID, page_count=ing.page_count, sha256=ing.sha256)
        ParseJobRepository(session).create(ParseJobRow(
            job_id=JOB_ID, book_id=BOOK_ID, pages=None, engine="mineru", status=JobStatus.QUEUED,
            progress={"parsed": 0, "total": 0, "error": ""}))
        session.commit()
        adapter = build_engine("mineru", settings)
        outcome = run_parse_job(session=session, engine=adapter, job_id=JOB_ID, book_id=BOOK_ID,
                                pages=None, data_root=ROOT)
        adapter.close()
        print("outcome:", outcome.status, f"{outcome.parsed}/{outcome.total}", outcome.failed_pages)

    book_dir = ROOT / "parsed" / "probe" / "bbox" / BOOK_ID
    payload = json.loads((book_dir / "pages" / "p0001.blocks.json").read_text(encoding="utf-8"))
    image_size = tuple(payload["image_size"])
    raw_doc = pymupdf.open(pdf)
    raw_sizes = (raw_doc[0].rect.width, raw_doc[0].rect.height)
    raw_doc.close()

    rows = []
    for block in payload["blocks"]:
        key = (block.get("text") or "").strip().split(" ")[0]
        want = expected.get(key)
        got = tuple(block["bbox"])
        legacy = _legacy_bbox(got, page_size_pt=raw_sizes, image_size=image_size)
        rows.append({
            "key": key, "type": block["type"], "parsed_px": list(got),
            "expected_px": list(want) if want else None,
            "delta": [got[i] - want[i] for i in range(4)] if want else None,
            "iou_fixed": round(_iou(got, want), 4) if want else None,
            "iou_legacy": round(_iou(legacy, want), 4) if want else None,
            "legacy_px": list(legacy),
            "clipped_by_legacy": any(
                legacy[i] == bound for i, bound in
                ((2, image_size[0]), (3, image_size[1]), (0, 0), (1, 0))
            ),
        })
    report = {
        "dpi": DEFAULT_DPI,
        "image_size": list(image_size),
        "pdf_size_pt": list(raw_sizes),
        "blocks": rows,
        "iou_mean_fixed": round(sum(r["iou_fixed"] or 0 for r in rows) / max(1, len(rows)), 4),
        "iou_mean_legacy": round(sum(r["iou_legacy"] or 0 for r in rows) / max(1, len(rows)), 4),
        "max_abs_delta_px": max(
            (max(abs(d) for d in r["delta"]) for r in rows if r["delta"]), default=None
        ),
    }
    out = EVIDENCE / "E1_anchor_after_fix.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
