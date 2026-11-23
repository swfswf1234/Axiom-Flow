"""解析版本读面作用域的解析规则（PLAN-030）：**已解析页最多的可见版本**。

读面与解析基线共用本模块，保证「屏幕上读到的版本」与「补页任务继承的版本」是同一版。

可见＝该版本的目录存在、任务已离开 `queued`/`running`、且 `af_pages` 里有 `parsed` 行。
候选之间按已解析页数取最大；并列时生效指针优先，再按创建序取最新（`MySQL DATETIME` 只到秒，
同秒以 `job_id` 定序，保证判定可重复）。

**重 ingest 之后的老版本不算可见**（任务创建时刻早于 `af_books.ingested_at`）：重 ingest 换的是
源文件，旧版本产物可能来自另一份 PDF，把它顶上读面就是拿陈旧内容冒充当前源。重 ingest 同时
清空生效指针，读面因此回落到新的根视图。

没有带内容的版本时退回「当前生效指针（目录在位）」或根视图（legacy），与旧行为一致：
空版本不参与比较，避免一张 0 页的失败版本把有内容的 legacy 根视图挤掉。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from axiom_flow.db.models import AfBook
from axiom_flow.db.repositories import LEGACY_JOB_ID, OperationRepository, PageRepository
from axiom_flow.ingest import version_dir_for
from axiom_flow.schemas import JobStatus

_IN_FLIGHT = {JobStatus.QUEUED.value, JobStatus.RUNNING.value}


def resolve_visible_version(
    session: Session, *, data_root: Path, book: AfBook
) -> tuple[Path | None, str]:
    """返回 `(版本目录, job_id)`；无可见版本时返回 `(None, LEGACY_JOB_ID)`。"""
    pages = PageRepository(session)
    best: tuple[tuple[int, bool, float, str], Path, str] | None = None
    for job in OperationRepository(session).list_versions(book.book_id):
        if job.status in _IN_FLIGHT:
            continue
        if book.ingested_at is not None and job.created_at is not None:
            if job.created_at < book.ingested_at:
                continue
        version_dir = version_dir_for(
            data_root, book.domain_id, book.course_id, book.book_id, job.job_id
        )
        if not version_dir.is_dir():
            continue
        parsed = pages.count_parsed(book.book_id, job.job_id)
        if parsed <= 0:
            continue
        key = (
            parsed,
            job.job_id == book.active_job_id,
            job.created_at.timestamp() if job.created_at else 0.0,
            job.job_id,
        )
        if best is None or key > best[0]:
            best = (key, version_dir, job.job_id)
    if best is not None:
        return best[1], best[2]

    if book.active_job_id:
        active_dir = version_dir_for(
            data_root, book.domain_id, book.course_id, book.book_id, book.active_job_id
        )
        if active_dir.is_dir():
            return active_dir, book.active_job_id
    return None, LEGACY_JOB_ID
