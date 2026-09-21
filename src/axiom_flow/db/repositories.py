"""af_* 表仓储层：幂等 upsert 与状态回写（页状态与块编辑按解析版本作用域）。

设计事实源：`docs/design/parsing-management.md`、`docs/architecture/database-design.md`。
仓储只做数据访问；事务边界由调用方（orchestrator/api）控制，`flush` 后可读回同一 Session。
作用域约束：`af_pages` 与 `af_block_edits` 的键都包含 `job_id`，因此**所有读写必须显式给出解析
版本**——省略作用域会让一个版本的产物污染另一个版本的状态与人工编辑。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.models import LEGACY_JOB_ID, AfBlockEdit, AfBook, AfPage, AfParseJob


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
    """一次解析任务（= 一个解析版本）的输入行。

    `engine` 无默认值且必填：引擎名由提交期解析（`AXIOM_OCR_ENGINE`/请求体）后显式传入，
    缺名应在建任务时就失败，而不是静默写成一个具体引擎（ADR 0004 无自动兜底）。
    """

    job_id: str
    book_id: str
    engine: str
    pages: list[int] | None = None
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=dict)
    source_sha256: str = ""
    page_count: int | None = None


def edit_id_for(book_id: str, job_id: str, page_no: int, block_index: int) -> str:
    """编辑记录主键：`ed_<md5(book_id:job_id:page_no:block_index)>`（同版本同块幂等键）。

    主键只是幂等标识；定位已存在的编辑一律走唯一键（见 `EditRepository.upsert`），因为
    迁移前的存量行保留旧摘要（不含 `job_id`），重算主键会让这些人工编辑变成孤儿。
    """
    digest = hashlib.md5(f"{book_id}:{job_id}:{page_no}:{block_index}".encode()).hexdigest()
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

    def update_parse(
        self, book_id: str, *, parse_status: str, pages_done: int, last_parse_result: str | None = None
    ) -> bool:
        """回写书目进度；`last_parse_result` 只在终态传入（进度与结局分列，ADR 0007 决定 3）。"""
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.parse_status = parse_status
        book.pages_done = pages_done
        if last_parse_result is not None:
            book.last_parse_result = last_parse_result
        book.updated_at = utc_now()
        self.session.flush()
        return True

    def mark_parse_idle(self, book_id: str, *, last_parse_result: str | None = None) -> bool:
        """启动期归一回写进度：`parse_status` 回 `pending`，**不动 `pages_done`**（进度口径不变）。

        `last_parse_result` 只在该书最新一次任务被归一时传入，否则保留上一次的真实结局
        （ADR 0007 决定 4：结局跟随最新任务，不能被更早的中断行覆盖）。
        """
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.parse_status = "pending"
        if last_parse_result is not None:
            book.last_parse_result = last_parse_result
        book.updated_at = utc_now()
        self.session.flush()
        return True

    def set_active_job(self, book_id: str, job_id: str | None) -> bool:
        """激活解析版本（唯一入口，单列 UPDATE）；`None` 表示回到根视图。

        只在版本目录校验通过后调用：生效指针一旦切换，读取端立即按新版本目录出数据。
        """
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.active_job_id = job_id
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
            source_sha256=row.source_sha256,
            page_count=row.page_count,
            created_at=utc_now(),
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: str) -> AfParseJob | None:
        return self.session.get(AfParseJob, job_id)

    def list_for_book(self, book_id: str) -> list[AfParseJob]:
        """按创建时间列该书的解析版本（末位 = 最新一次任务）。

        `MySQL DATETIME` 只到秒，同秒建的多任务以 `job_id` 定序，保证「最新任务」判定可重复。
        """
        stmt = (
            select(AfParseJob)
            .where(AfParseJob.book_id == book_id)
            .order_by(AfParseJob.created_at, AfParseJob.job_id)
        )
        return list(self.session.scalars(stmt))

    def mark_running(self, job_id: str) -> bool:
        job = self.session.get(AfParseJob, job_id)
        if job is None:
            return False
        job.status = "running"
        job.started_at = utc_now()
        self.session.flush()
        return True

    def record_source_snapshot(self, job_id: str, *, source_sha256: str, page_count: int) -> bool:
        """回写本版本解析的源文件指纹（版本 ↔ 源 PDF 对应关系的自证）。"""
        job = self.session.get(AfParseJob, job_id)
        if job is None:
            return False
        job.source_sha256 = source_sha256
        job.page_count = page_count
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
        failed_pages: list[int] | None = None,
    ) -> bool:
        job = self.session.get(AfParseJob, job_id)
        if job is None:
            return False
        job.status = status
        job.error = error[:500]
        if progress is not None:
            job.progress = dict(progress)
        if failed_pages is not None:
            job.failed_pages = list(failed_pages)
        job.finished_at = utc_now()
        self.session.flush()
        return True

    def reconcile_interrupted(self) -> list[AfParseJob]:
        """启动期归一（ADR 0007 决定 4）：把遗留 `queued`/`running` 改写为 `interrupted`。

        只在服务启动、开始受理请求之前调用一次。**前提是本服务单实例部署**：本表没有跨进程
        任务所有权标记，多实例下会把其他实例的活任务误判为中断（多实例前须改为心跳 + 回收）。
        """
        stmt = select(AfParseJob).where(AfParseJob.status.in_(["queued", "running"]))
        rows = list(self.session.scalars(stmt))
        now = utc_now()
        for job in rows:
            job.status = "interrupted"
            job.error = "服务重启中断，可重跑"
            job.finished_at = now
        self.session.flush()
        return rows


class PageRepository:
    """af_pages：一页在**一个解析版本内**的状态与质量信号。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        book_id: str,
        job_id: str,
        page_no: int,
        *,
        status: str,
        source: str = "",
        quality: dict[str, Any] | None = None,
        error: str = "",
    ) -> AfPage:
        page = self.session.get(AfPage, (book_id, job_id, page_no))
        if page is None:
            page = AfPage(book_id=book_id, job_id=job_id, page_no=page_no)
            self.session.add(page)
        page.status = status
        page.source = source
        page.quality = quality
        page.error = error[:500]
        page.parsed_at = utc_now() if status == "parsed" else None
        self.session.flush()
        return page

    def get(self, book_id: str, job_id: str, page_no: int) -> AfPage | None:
        return self.session.get(AfPage, (book_id, job_id, page_no))

    def list_for_book(self, book_id: str, job_id: str) -> list[AfPage]:
        stmt = (
            select(AfPage)
            .where(AfPage.book_id == book_id, AfPage.job_id == job_id)
            .order_by(AfPage.page_no)
        )
        return list(self.session.scalars(stmt))

    def count_parsed(self, book_id: str, job_id: str) -> int:
        stmt = select(func.count()).select_from(AfPage).where(
            AfPage.book_id == book_id, AfPage.job_id == job_id, AfPage.status == "parsed"
        )
        return int(self.session.scalar(stmt) or 0)

    def reset_legacy(self, book_id: str) -> int:
        """只清根视图（`job_id = 'legacy'`）页行：重新 ingest 替换的是根视图产物。

        真实版本作用域的行**必须保留**——它们对应磁盘上仍然存在的历史版本目录，
        按全书删除会让历史版本的状态与人工编辑失去依据（V8）。
        """
        deleted = self.session.execute(
            delete(AfPage).where(AfPage.book_id == book_id, AfPage.job_id == LEGACY_JOB_ID)
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_for_job(self, book_id: str, job_id: str) -> int:
        """删除指定解析版本的页行（该版本目录被清理时与其文件同步发生）。"""
        deleted = self.session.execute(
            delete(AfPage).where(AfPage.book_id == book_id, AfPage.job_id == job_id)
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_for_book(self, book_id: str) -> int:
        """删除整本书的页行（legacy 根视图 + 所有真实版本）：书目级删除没有「保留哪个版本」的语义。"""
        deleted = self.session.execute(delete(AfPage).where(AfPage.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)


class EditRepository:
    """af_block_edits：块级人工编辑覆盖层（编辑绑定其所属解析版本）。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _find(self, book_id: str, job_id: str, page_no: int, block_index: int) -> AfBlockEdit | None:
        stmt = select(AfBlockEdit).where(
            AfBlockEdit.book_id == book_id,
            AfBlockEdit.job_id == job_id,
            AfBlockEdit.page_no == page_no,
            AfBlockEdit.block_index == block_index,
        )
        return self.session.scalars(stmt).first()

    def upsert(
        self,
        book_id: str,
        job_id: str,
        page_no: int,
        block_index: int,
        *,
        block_type: str = "",
        verdict: str = "",
        note: str = "",
        corrected_text: str | None = None,
        corrected_bbox: list[int] | None = None,
    ) -> AfBlockEdit:
        edit = self._find(book_id, job_id, page_no, block_index)
        now = utc_now()
        if edit is None:
            edit = AfBlockEdit(
                edit_id=edit_id_for(book_id, job_id, page_no, block_index),
                book_id=book_id,
                job_id=job_id,
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

    def get(self, book_id: str, job_id: str, page_no: int, block_index: int) -> AfBlockEdit | None:
        return self._find(book_id, job_id, page_no, block_index)

    def list_for_page(self, book_id: str, job_id: str, page_no: int) -> list[AfBlockEdit]:
        stmt = (
            select(AfBlockEdit)
            .where(
                AfBlockEdit.book_id == book_id,
                AfBlockEdit.job_id == job_id,
                AfBlockEdit.page_no == page_no,
            )
            .order_by(AfBlockEdit.block_index)
        )
        return list(self.session.scalars(stmt))

    def list_for_book(self, book_id: str, job_id: str) -> list[AfBlockEdit]:
        stmt = (
            select(AfBlockEdit)
            .where(AfBlockEdit.book_id == book_id, AfBlockEdit.job_id == job_id)
            .order_by(AfBlockEdit.page_no, AfBlockEdit.block_index)
        )
        return list(self.session.scalars(stmt))

    def delete_for_book(self, book_id: str) -> int:
        """删除整本书的编辑行（所有版本）：书目删除不留孤儿覆盖层，页行与文件同一作用域。"""
        deleted = self.session.execute(delete(AfBlockEdit).where(AfBlockEdit.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)
