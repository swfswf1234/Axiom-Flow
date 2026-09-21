"""真机窗口只读预检：库版本、活动 job 遗留、产物目录与源 PDF 在位情况。

用法：python scripts/tmp/live_window_preflight.py [book_id ...]

只读：SHOW/SELECT + 目录列举，不写库、不落任何产物。**刻意只用裸 SQL**——迁移未 apply 时
ORM 声明的新列（`failed_pages`/`last_parse_result`）会让模型查询直接报错，而预检正是要在
apply **之前**跑一次，故按 `SHOW COLUMNS` 实际在位的列拼查询。用于窗口开工前固化起点与收工后对照。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sqlalchemy import select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.db.models import AfPage  # noqa: E402

_JOB_COLS = [
    "job_id",
    "book_id",
    "status",
    "progress",
    "error",
    "created_at",
    "started_at",
    "finished_at",
    "failed_pages",
]
_BOOK_COLS = [
    "book_id",
    "title",
    "page_count",
    "ingest_status",
    "parse_status",
    "last_parse_result",
    "active_job_id",
    "pages_done",
    "file_path",
]


def _columns(session: Session, table: str) -> list[str]:
    return [r[0] for r in session.execute(text(f"SHOW COLUMNS FROM `{table}`")).all()]


def _rows(session: Session, table: str, wanted: list[str], where: str = "") -> list[dict]:
    have = set(_columns(session, table))
    cols = [c for c in wanted if c in have]
    sql = f"SELECT {', '.join(f'`{c}`' for c in cols)} FROM `{table}`"  # noqa: S608
    if where:
        sql += f" WHERE {where}"
    return [dict(zip(cols, r, strict=True)) for r in session.execute(text(sql)).all()]


def _ser(obj):
    if isinstance(obj, (datetime,)):
        return obj.isoformat() + ("Z" if obj.tzinfo is None else "")
    raise TypeError(type(obj))


def main() -> int:
    wanted = sys.argv[1:]
    settings = load_settings()
    engine = create_engine_for(settings, mysql_url(settings))
    out: dict[str, object] = {
        "db_name": settings.db_name,
        "data_dir": str(settings.data_dir),
        "utc_now": datetime.now(UTC).isoformat(),
    }
    with Session(engine) as s:
        out["alembic_version"] = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
        out["af_books_columns"] = _columns(s, "af_books")
        out["af_parse_jobs_columns"] = _columns(s, "af_parse_jobs")
        now = datetime.now(UTC).replace(tzinfo=None)
        jobs = _rows(s, "af_parse_jobs", _JOB_COLS, "status IN ('queued','running')")
        for j in jobs:
            anchor = j.get("started_at") or j.get("created_at")
            j["idle_seconds"] = round((now - anchor).total_seconds()) if anchor else None
        out["active_jobs"] = jobs
        out["jobs_total"] = s.execute(text("SELECT COUNT(*) FROM af_parse_jobs")).scalar()
        books = _rows(s, "af_books", _BOOK_COLS)
        if wanted:
            books = [b for b in books if b["book_id"] in wanted]
        out["books"] = books
        out["ghost_rows_b01"] = [
            {"page_no": r.page_no, "job_id": r.job_id, "status": r.status}
            for r in s.scalars(
                select(AfPage).where(AfPage.book_id == "linearalgebra-b01", AfPage.page_no > 400)
            )
        ]
        page_rows = {}
        for b in books:
            rows = list(
                s.scalars(select(AfPage).where(AfPage.book_id == b["book_id"]))
            )
            page_rows[b["book_id"]] = {
                "total": len(rows),
                "by_job": _count(lambda r: r.job_id, rows),
                "by_status": _count(lambda r: r.status, rows),
            }
        out["page_rows"] = page_rows
    root = Path(settings.data_dir) / "parsed"
    for b in books:
        file_path = b.get("file_path") or ""
        pdf = Path(settings.data_dir) / file_path if file_path else None
        b["pdf_exists"] = bool(pdf and pdf.is_file())
        b["pdf_bytes"] = pdf.stat().st_size if b["pdf_exists"] else 0
        b["root_view"] = {}
        if root.is_dir():
            for d in (p for p in root.rglob(b["book_id"]) if p.is_dir()):
                pages = d / "pages"
                ver = d / "versions"
                b["root_view"][str(d.relative_to(root))] = {
                    "book_json": (d / "book.json").is_file(),
                    "page_png": len(list(pages.glob("p*.png"))) if pages.is_dir() else 0,
                    "versions": sorted(p.name for p in ver.iterdir() if p.is_dir())
                    if ver.is_dir()
                    else [],
                }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=_ser))
    return 0


def _count(key, rows) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(key(r))
        out[k] = out.get(k, 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
