"""af_* 表仓储层：幂等 upsert 与状态回写（页状态与块编辑按解析版本作用域）。

设计事实源：`docs/design/parsing-management.md`、`docs/architecture/database-design.md`。
仓储只做数据访问；事务边界由调用方（orchestrator/api）控制，`flush` 后可读回同一 Session。
作用域约束：`af_pages` 与 `af_block_edits` 的键都包含 `job_id`，因此**所有读写必须显式给出解析
版本**——省略作用域会让一个版本的产物污染另一个版本的状态与人工编辑。唯一的例外是
`af_page_regions`（页级、无 `job_id`，ADR 0010 决定 1）：它的读写只按 `(book_id, page_no)` 作用域。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.models import (
    LEGACY_JOB_ID,
    AfBlockEdit,
    AfBook,
    AfPage,
    AfPageRegion,
    AfParseJob,
)


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

    `parser_signature` 缺省 `''` ＝ **未记录代际**（与迁移回填的存量行同一语义），不是「已算过且
    相等」的签名；受理面在签名可用时必须显式传入，否则该版本一出生就落在未知代际里。
    """

    job_id: str
    book_id: str
    engine: str
    parser_signature: str = ""
    pages: list[int] | None = None
    status: str = "queued"
    progress: dict[str, Any] = field(default_factory=dict)
    source_sha256: str = ""
    page_count: int | None = None


@dataclass(frozen=True, slots=True)
class PageStateRow:
    """页清单的轻量投影行：只带清单契约需要的三列。

    整页质量信号与正文仍由单页端点返回；486 页全书一次回包的响应不能拖 `quality` JSON。
    """

    page_no: int
    status: str
    error: str


@dataclass(frozen=True, slots=True)
class RegionIntent:
    """一条页级结构意图（`af_page_regions` 的一行输入）。

    几何由调用方（api 面）在**受理时生效版本**的产物上换算得到，仓储不参与锚定：把换算留在
    仓储会让数据访问层依赖磁盘产物。
    """

    op: str
    bbox: list[int] | None = None
    anchor_bbox: list[int] | None = None
    members: list[list[int]] | None = None
    kind: str = ""
    note: str = ""


def _geometry_key(value: object) -> str:
    """几何的稳定字符串形式：列表/元组与 int/float 的表示差异都不进入身份。"""
    if value is None:
        return "none"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_geometry_key(item) for item in value) + "]"
    return f"{float(value):g}"  # type: ignore[arg-type]


def region_identity(intent: RegionIntent) -> str:
    """意图的身份几何：`add` 看自身框，`delete` 看锚，`merge` 看**成员集合**（顺序不参与）。

    合并成员的数组序是阅读序而非身份——调换顺序仍是同一组合并，换 id 会让人工调整阅读序
    变成「撤销旧意图 + 新建意图」。
    """
    if intent.op == "merge":
        return "|".join(sorted(_geometry_key(member) for member in intent.members or []))
    if intent.op == "delete":
        return _geometry_key(intent.anchor_bbox)
    return _geometry_key(intent.bbox)


def region_id_for(book_id: str, page_no: int, intent: RegionIntent) -> str:
    """区域主键：`rgn_<md5(book_id:page_no:op:身份几何)[:12]>`（确定性派生）。

    确定性而非随机：重复提交同一组意图必须命中同一行，否则「全量声明即幂等」要靠客户端记住
    服务端发过什么 id，两次并发提交还会各造一行。`kind`/`note` 有意不参与身份——改备注不是新意图。
    """
    digest = hashlib.md5(
        f"{book_id}:{page_no}:{intent.op}:{region_identity(intent)}".encode()
    ).hexdigest()
    return f"rgn_{digest[:12]}"


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

    def active_job_index(self) -> dict[str, str]:
        """`book_id -> active_job_id` 全量索引（只含有生效指针的书）。

        跨书作业列表用它判 `active`：逐行查会让一页 50 条变成 50 次 SQL。
        """
        stmt = select(AfBook.book_id, AfBook.active_job_id).where(AfBook.active_job_id.is_not(None))
        return {book_id: job_id for book_id, job_id in self.session.execute(stmt) if job_id}

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
        """ingest 完成回写：页数/哈希/时刻三件套（REQ-097：af_books 是元数据唯一事实源）。

        `ingested_at` 每次 ingest 都刷新——根视图被整体替换，旧时刻对新页图不成立。
        """
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.page_count = page_count
        book.sha256 = sha256
        book.ingest_status = ingest_status
        book.ingested_at = utc_now()
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

    def delete_for_book(self, book_id: str) -> int:
        """管理面清除原语：按书目删 af_books 行并回报行数（DDL 禁用，ADR 0005）。

        与 `delete()` 的分工是语义而非实现：`delete()` 服务 ② 类端点，需要「行不存在 ⇒ 404」
        的布尔判断；本方法服务一键清除，必须报出实删行数且不存在时不算失败。
        """
        deleted = self.session.execute(delete(AfBook).where(AfBook.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        """管理面全项目清除原语：整表 DML 清空（书目可经 `POST /books/sync` 重建）。"""
        deleted = self.session.execute(delete(AfBook)).rowcount
        self.session.flush()
        return int(deleted or 0)


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
            parser_signature=row.parser_signature,
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

    def list_all(
        self,
        *,
        status: Sequence[str] | None = None,
        book_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AfParseJob], int]:
        """全局任务列表（`GET /api/v1/parse-jobs`）：过滤 → 稳定倒序 → 分页，并回过滤后总数。

        倒序以 `created_at DESC, job_id DESC` 收尾：`MySQL DATETIME` 只到秒，同秒任务仅按时间
        排序不可重复，翻页会漏行或重行。`total` 是分页前过滤集的大小，客户端据此判断还有没有
        下一页，因此计数只能与行集共用同一组条件。
        """
        conditions: list[Any] = []
        if status is not None:
            conditions.append(AfParseJob.status.in_(list(status)))
        if book_id is not None:
            conditions.append(AfParseJob.book_id == book_id)

        total = int(
            self.session.scalar(select(func.count()).select_from(AfParseJob).where(*conditions)) or 0
        )
        stmt = (
            select(AfParseJob)
            .where(*conditions)
            .order_by(AfParseJob.created_at.desc(), AfParseJob.job_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt)), total

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

    def count_active(self, book_ids: Sequence[str] | None = None) -> int:
        """在飞任务计数：`queued`/`running` 是 ADR 0007 唯一的「活动」判据。

        `book_ids=None` 表示全项目作用域；`[]` 表示作用域为空，必须返回 0 而不是退化成全表，
        否则按书目清除会被不相干书的在飞任务挡住。
        """
        if book_ids is not None and not book_ids:
            return 0
        stmt = select(func.count()).select_from(AfParseJob).where(
            AfParseJob.status.in_(["queued", "running"])
        )
        if book_ids is not None:
            stmt = stmt.where(AfParseJob.book_id.in_(list(book_ids)))
        return int(self.session.scalar(stmt) or 0)

    def find_in_flight(self, book_id: str, *, signature: str) -> AfParseJob | None:
        """该书**同签名**的在飞任务（`queued`/`running`），按创建序取最早一个。

        受理门用它挡双跑：同签名的在飞任务已会把这本书跑到当前代际，再建一个只是白跑一遍（模型侧
        单占用把两次串行，白跑的成本不会因此消失）。终态行不参与判定——失败后再提交是人的决定。
        同秒创建的多任务以 `job_id` 定序，判据与 `list_for_book` 一致。
        """
        stmt = (
            select(AfParseJob)
            .where(
                AfParseJob.book_id == book_id,
                AfParseJob.parser_signature == signature,
                AfParseJob.status.in_(["queued", "running"]),
            )
            .order_by(AfParseJob.created_at, AfParseJob.job_id)
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    def delete_for_book(self, book_id: str) -> int:
        """删除该书全部任务行（含历史版本）：与 `DELETE /books/{id}` 的「保留任务行」相反，
        只由管理面一键清除调用（PLAN-010 / ARCH-024 例外条文）。"""
        deleted = self.session.execute(delete(AfParseJob).where(AfParseJob.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        deleted = self.session.execute(delete(AfParseJob)).rowcount
        self.session.flush()
        return int(deleted or 0)


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

    def list_states(self, book_id: str, job_id: str) -> list[PageStateRow]:
        """按解析版本列**有页行的**页状态清单（`GET /api/v1/books/{book_id}/pages` 的仓储面）。

        只投影清单契约列；没有页行的页在此缺席，`1..total` 的补全（缺行 = `pending`）由读取面做，
        因为 `total` 的事实源在书目行（`af_books.page_count`），不属页仓储的作用域。
        """
        stmt = (
            select(AfPage.page_no, AfPage.status, AfPage.error)
            .where(AfPage.book_id == book_id, AfPage.job_id == job_id)
            .order_by(AfPage.page_no)
        )
        return [PageStateRow(page_no=no, status=st, error=err or "") for no, st, err in self.session.execute(stmt)]

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

    def delete_all(self) -> int:
        """管理面全项目清除原语：整表清空所有版本的页行（PLAN-010）。"""
        deleted = self.session.execute(delete(AfPage)).rowcount
        self.session.flush()
        return int(deleted or 0)


class RegionRepository:
    """af_page_regions：页级人工结构意图（全量声明，缺席即撤销）。

    作用域是 `(book_id, page_no)` 而**不含 `job_id`**：区域承载人的意图、跨解析版本存活，
    因此本仓储没有「按版本读区域」的入口（ADR 0010 决定 1/2）。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_page(self, book_id: str, page_no: int) -> list[AfPageRegion]:
        stmt = (
            select(AfPageRegion)
            .where(AfPageRegion.book_id == book_id, AfPageRegion.page_no == page_no)
            .order_by(AfPageRegion.region_id)
        )
        return list(self.session.scalars(stmt))

    def list_for_book(self, book_id: str) -> list[AfPageRegion]:
        stmt = (
            select(AfPageRegion)
            .where(AfPageRegion.book_id == book_id)
            .order_by(AfPageRegion.page_no, AfPageRegion.region_id)
        )
        return list(self.session.scalars(stmt))

    def replace_page(
        self, book_id: str, page_no: int, intents: Sequence[RegionIntent]
    ) -> list[AfPageRegion]:
        """把该页区域改写为声明的**全集**：新增/覆盖列出的意图，删除缺席的意图。

        全量声明天然幂等（无需 undo 端点），因此一次调用必须同时做「写声明的」和「删缺席的」；
        只写不删会让人工撤销静默失效，只删不写会把重复提交变成清空。
        """
        now = utc_now()
        by_id: dict[str, RegionIntent] = {}
        for intent in intents:
            by_id[region_id_for(book_id, page_no, intent)] = intent
        stmt = select(AfPageRegion).where(
            AfPageRegion.book_id == book_id, AfPageRegion.page_no == page_no
        )
        for row in self.session.scalars(stmt):
            if row.region_id not in by_id:
                self.session.delete(row)
        for region_id, intent in by_id.items():
            row = self.session.get(AfPageRegion, (book_id, page_no, region_id))
            if row is None:
                row = AfPageRegion(
                    book_id=book_id, page_no=page_no, region_id=region_id, created_at=now
                )
                self.session.add(row)
            row.op = intent.op
            row.bbox = intent.bbox
            row.anchor_bbox = intent.anchor_bbox
            row.members = intent.members
            row.kind = intent.kind
            row.note = intent.note[:1000]
            row.updated_at = now
        self.session.flush()
        return self.list_for_page(book_id, page_no)

    def delete_for_page(self, book_id: str, page_no: int) -> int:
        deleted = self.session.execute(
            delete(AfPageRegion).where(
                AfPageRegion.book_id == book_id, AfPageRegion.page_no == page_no
            )
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_for_book(self, book_id: str) -> int:
        """删书带走全部区域行：区域是页的输入，页不存在时留着会驱动一次指向空页的解析。"""
        deleted = self.session.execute(
            delete(AfPageRegion).where(AfPageRegion.book_id == book_id)
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        """管理面全项目清除原语：整表清空人工区域（PLAN-020 S5 覆盖面的一分子）。"""
        deleted = self.session.execute(delete(AfPageRegion)).rowcount
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

    def count_by_page(self, book_id: str, job_id: str) -> dict[int, int]:
        """按页聚合人工编辑条数（页清单的 `edit_count`）：一次分组查询，不逐页发 SQL。

        作用域必须带 `job_id`——编辑绑定其所属解析版本，跨版本计数会让旧版编辑冒充生效版结论。
        同版本同块是覆盖更新（唯一键），因此重复提交天然只计一次。
        """
        stmt = (
            select(AfBlockEdit.page_no, func.count())
            .where(AfBlockEdit.book_id == book_id, AfBlockEdit.job_id == job_id)
            .group_by(AfBlockEdit.page_no)
        )
        return {int(page_no): int(count) for page_no, count in self.session.execute(stmt)}

    def delete_for_book(self, book_id: str) -> int:
        """删除整本书的编辑行（所有版本）：书目删除不留孤儿覆盖层，页行与文件同一作用域。"""
        deleted = self.session.execute(delete(AfBlockEdit).where(AfBlockEdit.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        """管理面全项目清除原语：整表清空人工编辑（不可回滚，需事前 `af_*` 备份）。"""
        deleted = self.session.execute(delete(AfBlockEdit)).rowcount
        self.session.flush()
        return int(deleted or 0)
