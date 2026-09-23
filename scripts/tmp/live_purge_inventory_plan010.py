"""业务侧清除前只读盘点：af_* 行数、生效版本、人工块编辑明细、在飞任务、数据根规模。

只读，不写任何库与磁盘（除本脚本自身）。用法：
  PYTHONIOENCODING=utf-8 <QED_env python> scripts/tmp/live_purge_inventory_plan010.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402


def main() -> int:
    s = load_settings(db_name="qed")
    print(f"db={s.db_name} data_dir={s.data_dir}")
    engine = create_engine_for(s, mysql_url(s))
    with sessionmaker(engine)() as sess:
        for table in ("af_books", "af_parse_jobs", "af_pages", "af_block_edits"):
            n = sess.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()  # noqa: S608
            print(f"{table}: {n}")
        print("--- 在飞（queued/running）---")
        rows = sess.execute(
            text(
                "SELECT job_id, book_id, status FROM af_parse_jobs "
                "WHERE status IN ('queued','running') ORDER BY created_at"
            )
        ).all()
        for r in rows:
            print(dict(r._mapping))
        if not rows:
            print("(无)")
        print("--- 生效版本（af_books.active_job_id 非空）---")
        rows = sess.execute(
            text(
                "SELECT b.book_id, b.domain_id, b.course_id, b.active_job_id, "
                "j.status, j.page_count, j.finished_at "
                "FROM af_books b LEFT JOIN af_parse_jobs j ON j.job_id = b.active_job_id "
                "ORDER BY b.book_id"
            )
        ).all()
        for r in rows:
            print(dict(r._mapping))
        print("--- 人工块编辑明细 ---")
        rows = sess.execute(
            text(
                "SELECT e.edit_id, e.book_id, e.job_id, e.page_no, e.block_index, "
                "e.block_type, e.verdict, CHAR_LENGTH(e.corrected_text) AS text_len, "
                "e.edited_at, e.updated_at "
                "FROM af_block_edits e ORDER BY e.edited_at"
            )
        ).all()
        for r in rows:
            print(dict(r._mapping))
        print("--- 按书目行数分布 ---")
        rows = sess.execute(
            text(
                "SELECT b.book_id, b.active_job_id IS NOT NULL AS active, "
                "(SELECT COUNT(*) FROM af_parse_jobs j WHERE j.book_id = b.book_id) AS jobs, "
                "(SELECT COUNT(*) FROM af_pages p WHERE p.book_id = b.book_id) AS pages "
                "FROM af_books b ORDER BY b.book_id"
            )
        ).all()
        for r in rows:
            print(dict(r._mapping))
    parsed = Path(s.data_dir) / "parsed"
    total_files = total_bytes = 0
    print(f"--- 数据根 {parsed} ---")
    if parsed.exists():
        for child in sorted(p for p in parsed.iterdir() if p.is_dir()):
            for domain in sorted(p for p in child.iterdir() if p.is_dir()):
                for course in sorted(p for p in domain.iterdir() if p.is_dir()):
                    for book in sorted(p for p in course.iterdir() if p.is_dir()):
                        files = [f for f in book.rglob("*") if f.is_file()]
                        n = len(files)
                        b = sum(f.stat().st_size for f in files)
                        total_files += n
                        total_bytes += b
                        print(f"{child.name}/{domain.name}/{course.name}/{book.name}: {n} 文件 {b/1e6:.1f} MB")
    print(f"合计 {total_files} 文件 {total_bytes/1e9:.2f} GB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
