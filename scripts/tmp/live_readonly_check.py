"""真机取证只读检查：af_parse_jobs 与 af_pages 落库现状（不写入任何数据）。

用法：python scripts/tmp/live_readonly_check.py <job_id> <book_id> <page_from> <page_to>
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.db.models import AfPage, AfParseJob  # noqa: E402


def main() -> int:
    job_id, book_id = sys.argv[1], sys.argv[2]
    lo, hi = int(sys.argv[3]), int(sys.argv[4])
    settings = load_settings()
    engine = create_engine_for(settings, mysql_url(settings))
    session = Session(engine)
    print("db_name =", settings.db_name)
    job = session.scalars(select(AfParseJob).where(AfParseJob.job_id == job_id)).one_or_none()
    if job is None:
        print("job", job_id, "MISSING")
    else:
        print("job", job_id, "status =", job.status, "progress =", job.progress, "error =", job.error[:120])
    rows = list(session.scalars(select(AfPage).where(AfPage.book_id == book_id).order_by(AfPage.page_no)))
    print("af_pages rows total =", len(rows))
    for row in rows:
        print("  page", row.page_no, row.status, row.source, (row.error or "")[:40], row.parsed_at)
    parsed = {row.page_no for row in rows if row.status == "parsed"}
    target = set(range(lo, hi + 1))
    print("target", f"{lo}-{hi}", "n =", len(target))
    print("resume set (无行 ∪ failed) =", sorted(target - parsed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
