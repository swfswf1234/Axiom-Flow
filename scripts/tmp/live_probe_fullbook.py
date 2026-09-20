"""L3 取证：整本路径（pages=None → engine.parse_document）打真实 MinerU 5002。

数据根与数据库**都限定在仓库内**（`data/probes/live-bringup-20260920/root`），
不触碰真实 `QED_DATA_ROOT` 与共享 `qed` 库（隔离铁律）。

用法：python scripts/tmp/live_probe_fullbook.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.engine import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import utc_now  # noqa: E402
from axiom_flow.db.models import AfBook, AfPage, AfParseJob, Base  # noqa: E402
from axiom_flow.db.repositories import (  # noqa: E402
    BookRepository,
    BookSyncRow,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.factory import build_engine  # noqa: E402
from axiom_flow.ingest import ingest_book  # noqa: E402
from axiom_flow.orchestrator.pipeline import run_parse_job  # noqa: E402
from axiom_flow.schemas import JobStatus  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "data" / "probes" / "live-bringup-20260920"
ROOT = EVIDENCE / "root"
SAMPLE = REPO / "data" / "probes" / "mineru-3.4.4-20260920" / "samples" / "sb-5page.pdf"
BOOK_ID = "l3full"
JOB_ID = "l3fulljob01"


def main() -> int:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    (ROOT / "raw").mkdir(parents=True)
    shutil.copyfile(SAMPLE, ROOT / "raw" / "probel3.pdf")

    settings = load_settings()
    engine = create_engine(f"sqlite:///{(ROOT / 'l3.db').as_posix()}")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        BookRepository(session).upsert_many([
            BookSyncRow(
                book_id=BOOK_ID,
                domain_id="probe",
                course_id="l3",
                course_name="L3 整本取证",
                knowledge_id="",
                title="L3 full-document probe (5p)",
                part="",
                display_title="L3 full-document probe (5p)",
                authors=[{"name": "probe", "role": "author"}],
                file_path="raw/probel3.pdf",
            )
        ])
        session.commit()

        ing = ingest_book(
            book_id=BOOK_ID,
            source_file="raw/probel3.pdf",
            data_root=ROOT,
            domain_id="probe",
            course_id="l3",
            title="L3 full-document probe (5p)",
            engine="mineru",
        )
        BookRepository(session).update_ingest(BOOK_ID, page_count=ing.page_count, sha256=ing.sha256)
        session.commit()
        print("ingest page_count =", ing.page_count, "sha256 =", ing.sha256[:16])

        ParseJobRepository(session).create(ParseJobRow(
            job_id=JOB_ID, book_id=BOOK_ID, pages=None, engine="mineru",
            status=JobStatus.QUEUED, progress={"parsed": 0, "total": 0, "error": ""},
        ))
        session.commit()

        adapter = build_engine("mineru", settings)
        print("engine =", adapter.name, "model_url =", settings.ocr_model_url,
              "health =", adapter.health())
        outcome = run_parse_job(
            session=session, engine=adapter, job_id=JOB_ID, book_id=BOOK_ID,
            pages=None, data_root=ROOT,
        )
        session.commit()
        close = getattr(adapter, "close", None)
        if callable(close):
            close()

        job = session.get(AfParseJob, JOB_ID)
        rows = list(session.scalars(select(AfPage).where(AfPage.book_id == BOOK_ID).order_by(AfPage.page_no)))
        book = session.get(AfBook, BOOK_ID)
        report = {
            "outcome": {
                "job_id": outcome.job_id, "status": outcome.status,
                "parsed": outcome.parsed, "total": outcome.total,
                "failed_pages": outcome.failed_pages, "error": outcome.error,
            },
            "job": {
                "status": job.status, "pages": job.pages, "engine": job.engine,
                "progress": job.progress, "error": job.error,
                "started_at": str(job.started_at), "finished_at": str(job.finished_at),
            },
            "book": {"parse_status": book.parse_status, "pages_done": book.pages_done,
                     "page_count": book.page_count},
            "count_parsed": PageRepository(session).count_parsed(BOOK_ID),
            "af_pages": [{"page_no": r.page_no, "status": r.status, "source": r.source,
                          "quality": r.quality, "error": (r.error or "")[:120]} for r in rows],
            "generated_at": utc_now().isoformat(),
        }
    book_dir = ROOT / "parsed" / "probe" / "l3" / BOOK_ID
    report["artifacts"] = sorted(p.relative_to(book_dir).as_posix() for p in book_dir.rglob("*") if p.is_file())
    for name in ("chunks.json", "manifest.json"):
        path = book_dir / name
        if path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if name == "chunks.json":
                report["chunks"] = {"params": payload.get("params"), "count": len(payload.get("chunks", [])),
                                    "first": (payload.get("chunks") or [{}])[0]}
            else:
                report["manifest_entries"] = len(payload)
                report["manifest_paths"] = [e.get("path") for e in payload]
    md = book_dir / "pages" / "p0001.md"
    report["p0001_md_head"] = md.read_text(encoding="utf-8")[:400] if md.is_file() else ""
    blocks = book_dir / "pages" / "p0001.blocks.json"
    if blocks.is_file():
        payload = json.loads(blocks.read_text(encoding="utf-8"))
        report["p0001_blocks"] = {
            "image_size": payload.get("image_size"), "source": payload.get("source"),
            "count": len(payload.get("blocks", [])),
            "types": [b.get("type") for b in payload.get("blocks", [])],
            "bboxes": [b.get("bbox") for b in payload.get("blocks", [])],
            "quality": payload.get("quality"),
        }
    (EVIDENCE / "L3_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("outcome:", report["outcome"])
    print("job:", report["job"])
    print("book:", report["book"])
    print("artifacts:", len(report["artifacts"]), "files")
    print("wrote", EVIDENCE / "L3_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
