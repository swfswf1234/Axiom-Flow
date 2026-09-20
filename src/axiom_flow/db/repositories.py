"""af_* 表仓储层：幂等 upsert 与状态回写。

设计事实源：`docs/design/parsing-management.md`、`docs/architecture/database-design.md`。
仓储只做数据访问；事务边界由调用方（orchestrator/api）控制，`flush` 后可读回同一 Session。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.models import AfBlockEdit, AfBook, AfPage, AfParseJob


@dataclass(frozen=True, slots=True)
class BookSyncRow:
    """一次书目同步的输入行（同源 qt_books 只读快照）。"""

    book_id: str
    domain_id: str = ""
    course_id: str = ""
    course_name: str = ""
    knowledge_id: str = ""
    title: str = ""
    part: str = ""
    display_title: str = ""
    authors: list[dict[str, Any]] = field(default_factory=list)
    file_path: str = ""


@dataclass(frozen=True, slots=True)
class SyncResult:
    """同步结果：新增与更新行数。"""

    synced: int
    updated: int


@dataclass(frozen=True, slots=True)
class ParseJobRow:
    """一次解析任务的输入行。"""

    job_id: str
    book_id: str
    pages: list[int] | None = None
    engine: str = "mineru"
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=dict)


def edit_id_for(book_id: str, page_no: int, block_index: int) -> str:
    """编辑记录主键：`ed_<md5(book_id:page_no:block_index)>`（同块幂等键）。"""
    digest = hashlib.md5(f"{book_id}:{page_no}:{block_index}".encode()).hexdigest()
    return f"ed_{digest}"


class BookRepository:
    """af_books：同步幂等 upsert（快照语义）与进度回写。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_books(self) -> list[AfBook]:
        return list(self.session.scalars(select(AfBook).order_by(AfBook.book_id)))

    def get(self, book_id: str) -> AfBook | None:
        return self.session.get(AfBook, book_id)

    def upsert_many(self, rows: Sequence[BookSyncRow]) -> SyncResult:
        """以 book_id 为幂等键 upsert；重复同步不产生重复行。

        快照语义：只覆盖同步字段（课程归属/标题/作者/路径），**保留本侧进度字段**
        （page_count/sha256/ingest_status/parse_status/pages_done/notes）。
        """
        now = utc_now()
        synced = 0
        updated = 0
        for row in rows:
            book = self.session.get(AfBook, row.book_id)
            if book is None:
                self.session.add(
                    AfBook(
                        book_id=row.book_id,
                        domain_id=row.domain_id,
                        course_id=row.course_id,
                        course_name=row.course_name,
                        knowledge_id=row.knowledge_id,
                        title=row.title,
                        part=row.part,
                        display_title=row.display_title,
                        authors=list(row.authors),
                        file_path=row.file_path,
                        synced_at=now,
                        updated_at=now,
                    )
                )
                synced += 1
            else:
                book.domain_id = row.domain_id
                book.course_id = row.course_id
                book.course_name = row.course_name
                book.knowledge_id = row.knowledge_id
                book.title = row.title
                book.part = row.part
                book.display_title = row.display_title
                book.authors = list(row.authors)
                book.file_path = row.file_path
                book.updated_at = now
                updated += 1
        self.session.flush()
        return SyncResult(synced=synced, updated=updated)

    def update_ingest(
        self, book_id: str, *, page_count: int, sha256: str, ingest_status: str = "ingested"
    ) -> bool:
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.page_count = page_count
        book.sha256 = sha256
        book.ingest_status = ingest_status
        book.updated_at = utc_now()
        self.session.flush()
        return True

    def update_parse(self, book_id: str, *, parse_status: str, pages_done: int) -> bool:
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.parse_status = parse_status
        book.pages_done = pages_done
        book.updated_at = utc_now()
        self.session.flush()
        return True

    def update_notes(self, book_id: str, notes: str | None) -> bool:
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.notes = notes
        book.updated_at = utc_now()
        self.session.flush()
        return True

    def delete(self, book_id: str) -> bool:
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        self.session.delete(book)
        self.session.flush()
        return True


class ParseJobRepository:
    """af_parse_jobs：任务状态与进度。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, row: ParseJobRow) -> AfParseJob:
        job = AfParseJob(
            job_id=row.job_id,
            book_id=row.book_id,
            pages=row.pages,
            engine=row.engine,
            status=row.status,
            progress=dict(row.progress),
            created_at=utc_now(),
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: str) -> AfParseJob | None:
        return self.session.get(AfParseJob, job_id)

    def list_for_book(self, book_id: str) -> list[AfParseJob]:
        stmt = select(AfParseJob).where(AfParseJob.book_id == book_id).order_by(AfParseJob.created_at)
        return list(self.session.scalars(stmt))

    def mark_running(self, job_id: str) -> bool:
        job = self.session.get(AfParseJob, job_id)
        if job is None:
            return False
        job.status = "running"
        job.started_at = utc_now()
        self.session.flush()
        return True

    def update_progress(self, job_id: str, progress: dict[str, Any]) -> bool:
        job = self.session.get(AfParseJob, job_id)
        if job is None:
            return False
        job.progress = dict(progress)
        self.session.flush()
        return True

    def mark_finished(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
        progress: dict[str, Any] | None = None,
    ) -> bool:
        job = self.session.get(AfParseJob, job_id)
        if job is None:
            return False
        job.status = status
        job.error = error[:500]
        if progress is not None:
            job.progress = dict(progress)
        job.finished_at = utc_now()
        self.session.flush()
        return True


class PageRepository:
    """af_pages：页状态与质量信号（book_id + page_no 幂等 upsert）。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        book_id: str,
        page_no: int,
        *,
        status: str,
        source: str = "",
        quality: dict[str, Any] | None = None,
        error: str = "",
    ) -> AfPage:
        page = self.session.get(AfPage, (book_id, page_no))
        if page is None:
            page = AfPage(book_id=book_id, page_no=page_no)
            self.session.add(page)
        page.status = status
        page.source = source
        page.quality = quality
        page.error = error[:500]
        page.parsed_at = utc_now() if status == "parsed" else None
        self.session.flush()
        return page

    def get(self, book_id: str, page_no: int) -> AfPage | None:
        return self.session.get(AfPage, (book_id, page_no))

    def list_for_book(self, book_id: str) -> list[AfPage]:
        stmt = select(AfPage).where(AfPage.book_id == book_id).order_by(AfPage.page_no)
        return list(self.session.scalars(stmt))

    def count_parsed(self, book_id: str) -> int:
        stmt = select(func.count()).select_from(AfPage).where(
            AfPage.book_id == book_id, AfPage.status == "parsed"
        )
        return int(self.session.scalar(stmt) or 0)

    def reset_for_book(self, book_id: str) -> int:
        """清空整本书的页行（重新 ingest 后产物已作废，页状态需回到无行 = pending）。"""
        deleted = self.session.execute(delete(AfPage).where(AfPage.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)


class EditRepository:
    """af_block_edits：块级人工编辑覆盖层（同块重复编辑覆盖更新）。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        book_id: str,
        page_no: int,
        block_index: int,
        *,
        block_type: str = "",
        verdict: str = "",
        note: str = "",
        corrected_text: str | None = None,
        corrected_bbox: list[int] | None = None,
    ) -> AfBlockEdit:
        edit_id = edit_id_for(book_id, page_no, block_index)
        edit = self.session.get(AfBlockEdit, edit_id)
        now = utc_now()
        if edit is None:
            edit = AfBlockEdit(
                edit_id=edit_id,
                book_id=book_id,
                page_no=page_no,
                block_index=block_index,
                edited_at=now,
                updated_at=now,
            )
            self.session.add(edit)
        edit.block_type = block_type
        edit.verdict = verdict
        edit.note = note[:1000]
        edit.corrected_text = corrected_text
        edit.corrected_bbox = corrected_bbox
        edit.updated_at = now
        self.session.flush()
        return edit

    def get(self, book_id: str, page_no: int, block_index: int) -> AfBlockEdit | None:
        return self.session.get(AfBlockEdit, edit_id_for(book_id, page_no, block_index))

    def list_for_page(self, book_id: str, page_no: int) -> list[AfBlockEdit]:
        stmt = (
            select(AfBlockEdit)
            .where(AfBlockEdit.book_id == book_id, AfBlockEdit.page_no == page_no)
            .order_by(AfBlockEdit.block_index)
        )
        return list(self.session.scalars(stmt))

    def list_for_book(self, book_id: str) -> list[AfBlockEdit]:
        stmt = (
            select(AfBlockEdit)
            .where(AfBlockEdit.book_id == book_id)
            .order_by(AfBlockEdit.page_no, AfBlockEdit.block_index)
        )
        return list(self.session.scalars(stmt))
