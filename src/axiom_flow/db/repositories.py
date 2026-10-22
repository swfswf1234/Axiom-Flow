"""af_* 表仓储层：事件流水、人工意图与结果索引的读写（四表族，档 B 重建后形状）。

设计事实源：`docs/architecture/database-design.md`（列与键）、`docs/design/parsing-management.md`（判据）。
仓储只做数据访问；事务边界由调用方（orchestrator/api）控制，`flush` 后可读回同一 Session。

`af_operations` 一张表两用，由 `lifecycle` 分列（OB-1/OB-2）：
- `event` 行＝不可变流水，只答「发生过什么」，**禁作判据**；本轮只有 `op_kind='parse'` 的行有写者，
  `ingest`/`sync`/`purge`/`confirm`/`index`/`kp` 六个值**建值不建写者**（与 `embedding_signature` 同处置）。
- `intent` 行＝人工当前态，唯一合法判据位，定位靠 `intent_key` 唯一键（CW-3 的单语句 upsert）。

作用域约束：`af_pages` 与 edit/confirm 意图行的键都含 `job_id`，读写必须显式给出解析版本；
structure 意图行 `job_id` 恒 `''`（页级、跨版本存活，ADR 0010 决定 1），只按 `(book_id, page_no)` 读写。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.models import (
    LEGACY_JOB_ID,
    LIFECYCLE_EVENT,
    LIFECYCLE_INTENT,
    OP_KIND_EDIT,
    OP_KIND_PARSE,
    OP_KIND_STRUCTURE,
    AfBook,
    AfKp,
    AfOperation,
    AfPage,
)

log = logging.getLogger(__name__)

#: ADR 0007 唯一的活动态判据（在飞计数、受理门、启动期归一三处共用同一元组）。
ACTIVE_JOB_STATUSES = ("queued", "running")
#: CW-2：parse 行的回写只许向「更终」方向；终态之间互不改写。
_FINISHED_FROM = ("queued", "running")

#: 折叠映射：`started_at`/`finished_at` 两列并入 `progress`（代价＝不能按 SQL 聚合算耗时，
#: 故终态写入时顺手算好 `duration_seconds` 落列内）。
PROGRESS_STARTED_AT = "started_at"
PROGRESS_FINISHED_AT = "finished_at"
PROGRESS_DURATION_SECONDS = "duration_seconds"
_TIMING_KEYS = (PROGRESS_STARTED_AT, PROGRESS_FINISHED_AT, PROGRESS_DURATION_SECONDS)

#: edit 与 structure 的 `payload` 闭集键（`note` 按裁决提到实体列，不进载荷）。
EDIT_PAYLOAD_KEYS = ("block_type", "verdict", "corrected_text", "corrected_bbox")
STRUCTURE_PAYLOAD_KEYS = ("op", "bbox", "anchor_bbox", "members", "kind")

#: 意图行 upsert 时**不参与覆盖**的列：身份与首次落库时刻。
_INTENT_INSERT_ONLY = ("op_id", "intent_key", "created_at")


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
    """一次解析任务（= 一个解析版本）的输入行；落表后是 `af_operations` 的 parse 事件行。

    `job_id` 即 `op_id`（守护单测断言 parse 行 `job_id = op_id`）：值形沿用 `uuid4().hex[:12]`，
    产物目录 `versions/<job_id>/` 与生效指针都按它指，换表不换目录名。

    `engine` 无默认值且必填：引擎名由提交期解析（`AXIOM_OCR_ENGINE`/请求体）后显式传入，
    缺名应在建任务时就失败，而不是静默写成一个具体引擎（ADR 0004 无自动兜底）。

    `parser_signature` 缺省 `''` ＝ **未记录代际**，不是「已算过且相等」的签名；受理面在签名
    可用时必须显式传入，否则该版本一出生就落在未知代际里。
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
    """一条页级结构意图（写入 `af_operations` 的 structure 意图行）。

    几何由调用方（api 面）在**受理时生效版本**的产物上换算得到，仓储不参与锚定：把换算留在
    仓储会让数据访问层依赖磁盘产物。
    """

    op: str
    bbox: list[int] | None = None
    anchor_bbox: list[int] | None = None
    members: list[list[int]] | None = None
    kind: str = ""
    note: str = ""


@dataclass(frozen=True, slots=True)
class RegionRow:
    """structure 意图行的读回投影：把 `payload` 摊平回响应需要的形状。

    投影而非 ORM 行是必需的：几何在 `payload` 里，`_region_record` 与 ADR 0010 决定 4 的受理门
    都按属性读；摊平留在仓储，API 面不必知道载荷形状。
    """

    region_id: str
    book_id: str
    page_no: int
    op: str
    bbox: list[int] | None
    anchor_bbox: list[int] | None
    members: list[list[int]] | None
    kind: str
    note: str
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_row(cls, row: AfOperation) -> RegionRow:
        payload = row.payload or {}
        return cls(
            region_id=row.target_id,
            book_id=row.book_id,
            page_no=int(row.page_no or 0),
            op=str(payload.get("op", "")),
            bbox=payload.get("bbox"),
            anchor_bbox=payload.get("anchor_bbox"),
            members=payload.get("members"),
            kind=str(payload.get("kind", "")),
            note=row.note,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True, slots=True)
class EditRow:
    """edit 意图行的读回投影（`op_id` 顶替旧 `edit_id`，值形 `afop-<hex12>`）。"""

    edit_id: str
    book_id: str
    job_id: str
    page_no: int
    block_index: int
    block_type: str
    verdict: str
    note: str
    corrected_text: str | None
    corrected_bbox: list[int] | None
    edited_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_row(cls, row: AfOperation) -> EditRow:
        payload = row.payload or {}
        return cls(
            edit_id=row.op_id,
            book_id=row.book_id,
            job_id=row.job_id,
            page_no=int(row.page_no or 0),
            block_index=int(row.block_index or 0),
            block_type=str(payload.get("block_type", "")),
            verdict=str(payload.get("verdict", "")),
            note=row.note,
            corrected_text=payload.get("corrected_text"),
            corrected_bbox=payload.get("corrected_bbox"),
            edited_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True, slots=True)
class KpNodeRow:
    """一个知识点节点（`af_kp` 一行）的写入输入。

    `norm_key` 由调用方按去重口径算好传入（全角转半角＋空白折叠＋小写）：口径属分析模块的
    语义，不属数据访问层。`confirmed` 不接受在此写入（KP-8：只有人工可置，走 `confirm`）。
    """

    node_id: str
    book_id: str
    name: str
    norm_key: str
    node_kind: str = "point"
    job_id: str = ""
    parent_id: str | None = None
    depth: int | None = None
    order_hint: int | None = None
    role: str = ""
    type: str = ""
    summary: str = ""
    links: list[dict[str, Any]] | None = None
    anchors: list[dict[str, Any]] | None = None
    narrative_ref: str = ""
    gen_status: str = "candidate"
    gen_batch: str = ""
    model_ref: str = "manual"
    meta: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class KpWriteResult:
    """`KpRepository.update` 的结果：把「行不存在」与 `rev` 不匹配分开报（404 与 409 是两个结局）。"""

    ok: bool
    rev_conflict: bool
    node: AfKp | None


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
    """被操作对象身份：`rgn_<md5(book_id:page_no:op:身份几何)[:12]>`（确定性派生，落 `target_id`）。

    确定性而非随机：重复提交同一组意图必须命中同一行，否则「全量声明即幂等」要靠客户端记住
    服务端发过什么 id，两次并发提交还会各造一行。`kind`/`note` 有意不参与身份——改备注不是新意图。
    派生式与旧 `af_page_regions.region_id` 逐字相同：根侧长期持有的定位符不因换表而失效。
    """
    digest = hashlib.md5(
        f"{book_id}:{page_no}:{intent.op}:{region_identity(intent)}".encode()
    ).hexdigest()
    return f"rgn_{digest[:12]}"


def intent_key_for(
    *,
    op_kind: str,
    book_id: str,
    job_id: str = "",
    page_no: int | None = None,
    block_index: int | None = None,
    target_id: str = "",
) -> str:
    """意图幂等键：`md5(op_kind:book_id:job_id:page_no:block_index:target_id)` 前 16 位（CW-3 的承载）。

    NULL 坐标渲染成空段而不是字面 `None`：`(page=3, block=NULL)` 与 `(page=NULL, block=3)` 必须
    不同键。`event` 行不调用本函数——它恒 `intent_key=NULL`，唯一索引因此只约束意图行。
    """
    parts = (
        op_kind,
        book_id,
        job_id,
        "" if page_no is None else str(page_no),
        "" if block_index is None else str(block_index),
        target_id,
    )
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]


def new_op_id() -> str:
    """非 parse 行的事件身份值形 `afop-<hex12>`；parse 行不用它（版本号保持裸 hex12）。"""
    return f"afop-{uuid.uuid4().hex[:12]}"


def new_node_id() -> str:
    """知识点节点身份 `afkp-<hex12>`：随机发放而非内容摘要，改名与重挂父级不产生新节点。"""
    return f"afkp-{uuid.uuid4().hex[:12]}"


def job_timing(row: AfOperation) -> tuple[datetime | None, datetime | None]:
    """从 `progress` 取回 `(started_at, finished_at)`——两列合并进 JSON 后的唯一读法。

    解析失败按 `None` 处理而不是抛错：`progress` 是人机共读的读数列，历史值可能来自旧写入方。
    """
    progress = row.progress or {}
    out: list[datetime | None] = []
    for key in (PROGRESS_STARTED_AT, PROGRESS_FINISHED_AT):
        raw = progress.get(key)
        try:
            out.append(datetime.fromisoformat(str(raw)) if raw else None)
        except ValueError:
            out.append(None)
    return out[0], out[1]


def public_progress(row: AfOperation) -> dict[str, Any]:
    """进度读数 → 对外读数：剥掉折叠进来的三个时刻键。

    折叠的代价由读取面承担：API 契约的 `progress` 只有业务读数，两个时刻仍是**独立字段**
    （经 `job_timing` 取回）。读取端不剥键就是把内部折叠泄漏成契约。
    """
    return {key: value for key, value in (row.progress or {}).items() if key not in _TIMING_KEYS}


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

        快照语义：只覆盖同步字段（课程归属/标题/作者/路径），**保留本侧字段**
        （page_count/sha256/ingest_status/parse_status/pages_done/notes/**meta**）。
        `meta` 在这份清单里不是新增纪律而是裁决⑮的判据：改成 `merge`/整行覆写后，
        「同步重跑 ⇒ 先前写入的 `meta` 键集合不变」的用例即转红（双写者列域不重叠）。
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
        """ingest 完成回写：页数/哈希/时刻三件套（af_books 是元数据唯一事实源）。

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

    def update_meta(self, book_id: str, meta: dict[str, Any] | None) -> bool:
        """人工侧 `meta` 写入：**整列覆写**（CW-5），不做读 JSON→改一键→写回。

        与 `upsert_many` 的列域互不重叠是裁决⑮的全部代价，故本方法只碰 `meta`/`updated_at`。
        """
        book = self.session.get(AfBook, book_id)
        if book is None:
            return False
        book.meta = meta
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


class OperationRepository:
    """af_operations：事件流水（parse 版本）与人工意图（edit/structure）的唯一写入面。

    方法按 `lifecycle` 分三段，段与段之间没有共用写入口——这是 CW-1 能被守护的结构原因：
    事件段只允许 parse 行被条件回写，意图段只允许唯一键 upsert，删除段只有整作用域清理。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    # ---------- 事件段：parse 版本 ----------

    def create(self, row: ParseJobRow) -> AfOperation:
        """建解析版本行：`op_id = job_id`（守护判据），`lifecycle='event'`。"""
        now = utc_now()
        job = AfOperation(
            op_id=row.job_id,
            job_id=row.job_id,
            book_id=row.book_id,
            op_kind=OP_KIND_PARSE,
            lifecycle=LIFECYCLE_EVENT,
            pages=row.pages,
            engine=row.engine,
            parser_signature=row.parser_signature,
            status=row.status,
            progress=dict(row.progress),
            source_sha256=row.source_sha256,
            page_count=row.page_count,
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get(self, job_id: str) -> AfOperation | None:
        """按版本取 parse 行（`op_id`）。"""
        row = self.session.get(AfOperation, job_id)
        return row if row is not None and row.op_kind == OP_KIND_PARSE else None

    def list_versions(self, book_id: str) -> list[AfOperation]:
        """按创建时间列该书的解析版本（末位 = 最新一次任务）。

        `MySQL DATETIME` 只到秒，同秒建的多任务以 `job_id` 定序，保证「最新任务」判定可重复。
        """
        stmt = (
            select(AfOperation)
            .where(AfOperation.book_id == book_id, AfOperation.op_kind == OP_KIND_PARSE)
            .order_by(AfOperation.created_at, AfOperation.job_id)
        )
        return list(self.session.scalars(stmt))

    def list_all(
        self,
        *,
        status: Sequence[str] | None = None,
        book_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AfOperation], int]:
        """全局任务列表（`GET /api/v1/parse-jobs`）：过滤 → 稳定倒序 → 分页，并回过滤后总数。

        倒序以 `created_at DESC, job_id DESC` 收尾：`MySQL DATETIME` 只到秒，同秒任务仅按时间
        排序不可重复，翻页会漏行或重行。`total` 是分页前过滤集的大小，客户端据此判断还有没有
        下一页，因此计数只能与行集共用同一组条件。
        """
        conditions: list[Any] = [AfOperation.op_kind == OP_KIND_PARSE]
        if status is not None:
            conditions.append(AfOperation.status.in_(list(status)))
        if book_id is not None:
            conditions.append(AfOperation.book_id == book_id)

        total = int(
            self.session.scalar(
                select(func.count()).select_from(AfOperation).where(*conditions)
            )
            or 0
        )
        stmt = (
            select(AfOperation)
            .where(*conditions)
            .order_by(AfOperation.created_at.desc(), AfOperation.job_id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.scalars(stmt)), total

    def _advance(self, job_id: str, *, allow: Sequence[str], **values: Any) -> bool:
        """CW-2 的唯一落点：条件 UPDATE，`status` 不在前序态集合内就不写并记日志。

        `op_kind='parse'` 写进 WHERE 而不是信任调用方：这既让「非 parse 的 event 行被 UPDATE」
        计数天然为 0（CW-1 的反向判据），也让将来误把 edit/structure 行递进来变成静默失败而非改数据。
        """
        stmt = (
            update(AfOperation)
            .where(
                AfOperation.op_id == job_id,
                AfOperation.op_kind == OP_KIND_PARSE,
                AfOperation.status.in_(list(allow)),
            )
            .values(updated_at=utc_now(), **values)
        )
        result = self.session.execute(stmt)
        self.session.flush()
        if not result.rowcount:
            log.info("af_operations 条件回写未命中，忽略：job_id=%s 允许前序态=%s", job_id, list(allow))
            return False
        return True

    def mark_running(self, job_id: str) -> bool:
        row = self.get(job_id)
        if row is None:
            return False
        progress = dict(row.progress or {})
        progress[PROGRESS_STARTED_AT] = utc_now().isoformat(timespec="seconds")
        return self._advance(job_id, allow=("queued",), status="running", progress=progress)

    def record_source_snapshot(self, job_id: str, *, source_sha256: str, page_count: int) -> bool:
        """回写本版本解析的源文件指纹（版本 ↔ 源 PDF 对应关系的自证）。"""
        return self._advance(
            job_id,
            allow=ACTIVE_JOB_STATUSES,
            source_sha256=source_sha256,
            page_count=page_count,
        )

    def update_progress(self, job_id: str, progress: dict[str, Any]) -> bool:
        """进度整列覆写，但**时刻键随行保留**：`started_at` 是版本自证，不该被读数刷掉。

        读旧列合键不违 CW-5：parse 行由单 worker 拥有，且回写方向受 CW-2 约束（终态后本方法
        直接不写），不存在两个写者各自丢键的窗口。
        """
        row = self.get(job_id)
        if row is None:
            return False
        merged = dict(progress)
        current = row.progress or {}
        for key in _TIMING_KEYS:
            if key in current:
                merged[key] = current[key]
        return self._advance(job_id, allow=ACTIVE_JOB_STATUSES, progress=merged)

    def mark_finished(
        self,
        job_id: str,
        *,
        status: str,
        error: str = "",
        progress: dict[str, Any] | None = None,
        failed_pages: list[int] | None = None,
    ) -> bool:
        row = self.get(job_id)
        if row is None:
            return False
        now = utc_now()
        merged = dict(progress) if progress is not None else dict(row.progress or {})
        for key in _TIMING_KEYS:
            if key in (row.progress or {}) and key not in merged:
                merged[key] = row.progress[key]
        merged[PROGRESS_FINISHED_AT] = now.isoformat(timespec="seconds")
        started = merged.get(PROGRESS_STARTED_AT)
        if started:
            try:
                merged[PROGRESS_DURATION_SECONDS] = round(
                    (now - datetime.fromisoformat(str(started))).total_seconds(), 3
                )
            except ValueError:
                merged[PROGRESS_DURATION_SECONDS] = None
        values: dict[str, Any] = {"status": status, "error": error[:500], "progress": merged}
        if failed_pages is not None:
            values["failed_pages"] = list(failed_pages)
        return self._advance(job_id, allow=_FINISHED_FROM, **values)

    def reconcile_interrupted(self) -> list[AfOperation]:
        """启动期归一（ADR 0007 决定 4）：把遗留 `queued`/`running` 改写为 `interrupted`。

        只在服务启动、开始受理请求之前调用一次。**前提是本服务单实例部署**：本表没有跨进程
        任务所有权标记，多实例下会把其他实例的活任务误判为中断（多实例前须改为心跳 + 回收）。
        逐行走 CW-2 的条件回写，因此返回的行集恰为「本次真的被归一的那些」。
        """
        stmt = select(AfOperation.op_id).where(
            AfOperation.op_kind == OP_KIND_PARSE,
            AfOperation.status.in_(list(ACTIVE_JOB_STATUSES)),
        )
        ids = [job_id for (job_id,) in self.session.execute(stmt)]
        reconciled: list[AfOperation] = []
        for job_id in ids:
            if self.mark_finished(job_id, status="interrupted", error="服务重启中断，可重跑"):
                row = self.get(job_id)
                if row is not None:
                    reconciled.append(row)
        return reconciled

    def count_active(self, book_ids: Sequence[str] | None = None) -> int:
        """在飞任务计数：`queued`/`running` 是 ADR 0007 唯一的「活动」判据。

        `book_ids=None` 表示全项目作用域；`[]` 表示作用域为空，必须返回 0 而不是退化成全表，
        否则按书目清除会被不相干书的在飞任务挡住。
        """
        if book_ids is not None and not book_ids:
            return 0
        stmt = (
            select(func.count())
            .select_from(AfOperation)
            .where(
                AfOperation.op_kind == OP_KIND_PARSE,
                AfOperation.status.in_(list(ACTIVE_JOB_STATUSES)),
            )
        )
        if book_ids is not None:
            stmt = stmt.where(AfOperation.book_id.in_(list(book_ids)))
        return int(self.session.scalar(stmt) or 0)

    def find_in_flight(self, book_id: str, *, signature: str) -> AfOperation | None:
        """该书**同签名**的在飞任务（`queued`/`running`），按创建序取最早一个。

        受理门用它挡双跑：同签名的在飞任务已会把这本书跑到当前代际，再建一个只是白跑一遍（模型侧
        单占用把两次串行，白跑的成本不会因此消失）。终态行不参与判定——失败后再提交是人的决定。
        同秒创建的多任务以 `job_id` 定序，判据与 `list_versions` 一致。
        """
        stmt = (
            select(AfOperation)
            .where(
                AfOperation.book_id == book_id,
                AfOperation.op_kind == OP_KIND_PARSE,
                AfOperation.parser_signature == signature,
                AfOperation.status.in_(list(ACTIVE_JOB_STATUSES)),
            )
            .order_by(AfOperation.created_at, AfOperation.job_id)
            .limit(1)
        )
        return self.session.scalars(stmt).first()

    # ---------- 意图段：edit / structure ----------

    def _upsert_intent(self, values: dict[str, Any]) -> AfOperation:
        """CW-3 的唯一落点：**单语句** upsert（MySQL `ON DUPLICATE KEY UPDATE` / SQLite `DO UPDATE`）。

        「先查有没有、再决定插入或更新」在两个会话并发声明同一坐标时必然各造一行；唯一键
        `uq_af_operations_intent` 命中后后到者覆盖前者，坐标列本身不设唯一键（唯一性只由本列保证）。
        `op_id`/`intent_key`/`created_at` 不参与覆盖：身份与首次落库时刻一经写入不再变。
        """
        if self.session.get_bind().dialect.name == "mysql":
            stmt = mysql_insert(AfOperation).values(**values)
            stmt = stmt.on_duplicate_key_update(
                **{key: stmt.inserted[key] for key in values if key not in _INTENT_INSERT_ONLY}
            )
        else:
            stmt = sqlite_insert(AfOperation).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[AfOperation.intent_key],
                set_={key: stmt.excluded[key] for key in values if key not in _INTENT_INSERT_ONLY},
            )
        self.session.execute(stmt)
        self.session.flush()
        return self.session.scalars(
            select(AfOperation)
            .where(AfOperation.intent_key == values["intent_key"])
            .execution_options(populate_existing=True)
        ).one()

    def upsert_intent(
        self,
        *,
        op_kind: str,
        book_id: str,
        job_id: str = "",
        page_no: int | None = None,
        block_index: int | None = None,
        target_id: str = "",
        actor: str = "human",
        status: str = "ok",
        payload: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        note: str = "",
        meta: dict[str, Any] | None = None,
    ) -> AfOperation:
        now = utc_now()
        key = intent_key_for(
            op_kind=op_kind,
            book_id=book_id,
            job_id=job_id,
            page_no=page_no,
            block_index=block_index,
            target_id=target_id,
        )
        return self._upsert_intent(
            {
                "op_id": new_op_id(),
                "book_id": book_id,
                "job_id": job_id,
                "op_kind": op_kind,
                "lifecycle": LIFECYCLE_INTENT,
                "actor": actor,
                "page_no": page_no,
                "block_index": block_index,
                "target_id": target_id,
                "intent_key": key,
                "status": status,
                "payload": dict(payload or {}),
                "result": result,
                "note": note[:1000],
                "meta": meta,
                "created_at": now,
                "updated_at": now,
            }
        )

    def upsert_edit(
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
    ) -> EditRow:
        """同版本同块的编辑覆盖（意图行，绑定 `job_id`：块下标只在版本内有意义，PP-9）。"""
        row = self.upsert_intent(
            op_kind=OP_KIND_EDIT,
            book_id=book_id,
            job_id=job_id,
            page_no=page_no,
            block_index=block_index,
            status="ok",
            payload={
                "block_type": block_type,
                "verdict": verdict,
                "corrected_text": corrected_text,
                "corrected_bbox": corrected_bbox,
            },
            note=note,
        )
        return EditRow.from_row(row)

    def _intents(self, *conditions: Any) -> list[AfOperation]:
        stmt = (
            select(AfOperation)
            .where(AfOperation.lifecycle == LIFECYCLE_INTENT, *conditions)
            .execution_options(populate_existing=True)
        )
        return list(self.session.scalars(stmt))

    def get_edit(self, book_id: str, job_id: str, page_no: int, block_index: int) -> EditRow | None:
        rows = self._intents(
            AfOperation.op_kind == OP_KIND_EDIT,
            AfOperation.book_id == book_id,
            AfOperation.job_id == job_id,
            AfOperation.page_no == page_no,
            AfOperation.block_index == block_index,
        )
        return EditRow.from_row(rows[0]) if rows else None

    def list_edits_for_page(self, book_id: str, job_id: str, page_no: int) -> list[EditRow]:
        stmt = (
            select(AfOperation)
            .where(
                AfOperation.lifecycle == LIFECYCLE_INTENT,
                AfOperation.op_kind == OP_KIND_EDIT,
                AfOperation.book_id == book_id,
                AfOperation.job_id == job_id,
                AfOperation.page_no == page_no,
            )
            .order_by(AfOperation.block_index)
            .execution_options(populate_existing=True)
        )
        return [EditRow.from_row(row) for row in self.session.scalars(stmt)]

    def list_edits_for_book(self, book_id: str, job_id: str) -> list[EditRow]:
        stmt = (
            select(AfOperation)
            .where(
                AfOperation.lifecycle == LIFECYCLE_INTENT,
                AfOperation.op_kind == OP_KIND_EDIT,
                AfOperation.book_id == book_id,
                AfOperation.job_id == job_id,
            )
            .order_by(AfOperation.page_no, AfOperation.block_index)
            .execution_options(populate_existing=True)
        )
        return [EditRow.from_row(row) for row in self.session.scalars(stmt)]

    def count_edits_by_page(self, book_id: str, job_id: str) -> dict[int, int]:
        """按页聚合人工编辑条数（页清单的 `edit_count`）：一次分组查询，不逐页发 SQL。

        作用域必须带 `job_id`——编辑绑定其所属解析版本，跨版本计数会让旧版编辑冒充生效版结论。
        同版本同块是覆盖更新（`intent_key` 唯一），因此重复提交天然只计一次。
        """
        stmt = (
            select(AfOperation.page_no, func.count())
            .where(
                AfOperation.lifecycle == LIFECYCLE_INTENT,
                AfOperation.op_kind == OP_KIND_EDIT,
                AfOperation.book_id == book_id,
                AfOperation.job_id == job_id,
            )
            .group_by(AfOperation.page_no)
        )
        return {int(page_no): int(count) for page_no, count in self.session.execute(stmt) if page_no}

    def list_regions_for_page(self, book_id: str, page_no: int) -> list[RegionRow]:
        """该页结构意图全集（`job_id` 不参与作用域：意图跨版本存活，ADR 0010 决定 1）。"""
        return sorted(
            (RegionRow.from_row(row) for row in self._structure_rows(book_id, page_no)),
            key=lambda item: item.region_id,
        )

    def list_regions_for_book(self, book_id: str) -> list[RegionRow]:
        rows = self._intents(
            AfOperation.op_kind == OP_KIND_STRUCTURE, AfOperation.book_id == book_id
        )
        return sorted(
            (RegionRow.from_row(row) for row in rows),
            key=lambda item: (item.page_no, item.region_id),
        )

    def _structure_rows(self, book_id: str, page_no: int) -> list[AfOperation]:
        return self._intents(
            AfOperation.op_kind == OP_KIND_STRUCTURE,
            AfOperation.book_id == book_id,
            AfOperation.page_no == page_no,
        )

    def _lock_book(self, book_id: str) -> None:
        """CW-4 的锁：对 `af_books` 行加悲观排他锁（MySQL `SELECT … FOR UPDATE`）。

        SQLite 方言不渲染 `FOR UPDATE`，单写者天然串行，故同一份代码在两库都成立。
        锁内不得发起模型调用——把网络等待算进锁持有时间会让同页声明互相阻塞。
        """
        self.session.execute(
            select(AfBook.book_id).where(AfBook.book_id == book_id).with_for_update()
        )

    def replace_page_intents(
        self, book_id: str, page_no: int, intents: Sequence[RegionIntent]
    ) -> list[RegionRow]:
        """把该页结构意图改写为声明的**全集**：新增/覆盖列出的意图，删除缺席的意图。

        全量声明天然幂等（无需 undo 端点），因此一次调用必须同时做「写声明的」和「删缺席的」；
        只写不删会让人工撤销静默失效，只删不写会把重复提交变成清空。读现有行→算差异→写全部发生在
        `_lock_book` 这一把锁内（CW-4），否则两个并发声明会各自把对方判成缺席，撤销静默丢失。
        """
        self._lock_book(book_id)
        existing = {row.target_id: row for row in self._structure_rows(book_id, page_no)}
        declared: dict[str, RegionIntent] = {}
        for intent in intents:
            declared[region_id_for(book_id, page_no, intent)] = intent
        stale = [row.op_id for target_id, row in existing.items() if target_id not in declared]
        if stale:
            self.session.execute(delete(AfOperation).where(AfOperation.op_id.in_(stale)))
        for target_id, intent in declared.items():
            self.upsert_intent(
                op_kind=OP_KIND_STRUCTURE,
                book_id=book_id,
                page_no=page_no,
                target_id=target_id,
                status="ok",
                payload={
                    "op": intent.op,
                    "bbox": intent.bbox,
                    "anchor_bbox": intent.anchor_bbox,
                    "members": intent.members,
                    "kind": intent.kind,
                },
                note=intent.note,
            )
        self.session.flush()
        return self.list_regions_for_page(book_id, page_no)

    def delete_regions_for_page(self, book_id: str, page_no: int) -> int:
        deleted = self.session.execute(
            delete(AfOperation).where(
                AfOperation.lifecycle == LIFECYCLE_INTENT,
                AfOperation.op_kind == OP_KIND_STRUCTURE,
                AfOperation.book_id == book_id,
                AfOperation.page_no == page_no,
            )
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    # ---------- 删除段：书目删除与管理面清除 ----------

    def delete_intents_for_book(self, book_id: str) -> int:
        """只删人工意图行（edit + structure）：`DELETE /books/{id}` 用。

        事件行**保留为审计线索**（做过什么不可撤回），意图行必须随之消失：它是这本书某一页的解析
        输入，书行没了就是无主行，留着只会在重建同 id 书时凭空复活旧框与旧结论。
        """
        deleted = self.session.execute(
            delete(AfOperation).where(
                AfOperation.book_id == book_id, AfOperation.lifecycle == LIFECYCLE_INTENT
            )
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_for_book(self, book_id: str) -> int:
        """删除该书全部行（含历史版本事件行）：与书目删除的「保留审计」相反，
        只由管理面一键清除调用（清除是整族重建的前置，不留半套历史）。"""
        deleted = self.session.execute(
            delete(AfOperation).where(AfOperation.book_id == book_id)
        ).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        deleted = self.session.execute(delete(AfOperation)).rowcount
        self.session.flush()
        return int(deleted or 0)


class PageRepository:
    """af_pages：一页在**一个解析版本内**的状态、质量与产物索引（全族唯一结果索引表）。"""

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
        return [
            PageStateRow(page_no=no, status=st, error=err or "")
            for no, st, err in self.session.execute(stmt)
        ]

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
        """管理面全项目清除原语：整表清空所有版本的页行。"""
        deleted = self.session.execute(delete(AfPage)).rowcount
        self.session.flush()
        return int(deleted or 0)


#: `KpRepository.update` 可写字段白名单：`node_id`/`book_id`/`rev`/`confirmed` 与时刻列不在其中，
#: 身份不可变（`node_id` 一经发放就不能改）、序号只由写入本身递增、确认位另走 `confirm`。
_KP_WRITABLE = (
    "job_id",
    "node_kind",
    "parent_id",
    "depth",
    "order_hint",
    "name",
    "norm_key",
    "role",
    "type",
    "summary",
    "links",
    "anchors",
    "narrative_ref",
    "gen_status",
    "gen_batch",
    "model_ref",
    "meta",
)


class KpRepository:
    """af_kp：知识点节点的单表读写（分析模块的存储面，本轮只到形状，无消费端点）。

    作用域是 `book_id` 而**不含 `job_id`**：知识点是跨版本存活的人工资产，`job_id` 只作取证溯源。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, node_id: str) -> AfKp | None:
        return self.session.get(AfKp, node_id)

    def list_for_book(
        self, book_id: str, *, node_kind: str | None = None, gen_status: str | None = None
    ) -> list[AfKp]:
        """目录/工作区主屏读法：走 `ix_af_kp_book`，按 `(order_hint, node_id)` 定序。

        `order_hint` 可空（未归档池）；两库都把 NULL 排在同一侧，再以 `node_id` 收尾保证翻页可重复。
        """
        conditions: list[Any] = [AfKp.book_id == book_id]
        if node_kind is not None:
            conditions.append(AfKp.node_kind == node_kind)
        if gen_status is not None:
            conditions.append(AfKp.gen_status == gen_status)
        stmt = select(AfKp).where(*conditions).order_by(AfKp.order_hint, AfKp.node_id)
        return list(self.session.scalars(stmt))

    def create(self, row: KpNodeRow) -> AfKp:
        now = utc_now()
        node = AfKp(
            node_id=row.node_id,
            book_id=row.book_id,
            job_id=row.job_id,
            node_kind=row.node_kind,
            parent_id=row.parent_id,
            depth=row.depth,
            order_hint=row.order_hint,
            name=row.name,
            norm_key=row.norm_key,
            role=row.role,
            type=row.type,
            summary=row.summary[:1000],
            links=row.links,
            anchors=row.anchors,
            narrative_ref=row.narrative_ref,
            gen_status=row.gen_status,
            gen_batch=row.gen_batch,
            model_ref=row.model_ref,
            meta=row.meta,
            created_at=now,
            updated_at=now,
        )
        self.session.add(node)
        self.session.flush()
        return node

    def update(
        self, node_id: str, fields: dict[str, Any], *, if_rev: int | None = None
    ) -> KpWriteResult:
        """CW-6 的落点：条件 UPDATE（`rev` 匹配）+ 成功即自增，一条语句完成，不读改写。

        `if_rev=None` 表示不比对序号（幂等整节点声明走 CW-3 式覆盖）；带 `if_rev` 而行被他人写过
        则 rowcount=0 ⇒ `rev_conflict=True`，API 面据此判 409——人已不在该页时提交的旧表单
        不得静默覆盖新结论。
        """
        unknown = sorted(set(fields) - set(_KP_WRITABLE))
        if unknown:
            raise ValueError(f"af_kp 不可由本入口写入的列：{unknown}")
        now = utc_now()
        conditions = [AfKp.node_id == node_id]
        if if_rev is not None:
            conditions.append(AfKp.rev == if_rev)
        stmt = (
            update(AfKp)
            .where(*conditions)
            .values(**fields, rev=AfKp.rev + 1, updated_at=now)
        )
        result = self.session.execute(stmt)
        self.session.flush()
        if result.rowcount:
            return KpWriteResult(True, False, self.get(node_id))
        node = self.get(node_id)
        return KpWriteResult(False, node is not None, node)

    def confirm(self, node_id: str, *, confirmed: bool, if_rev: int | None = None) -> KpWriteResult:
        """人工确认位（KP-8）：只有本入口能置 `confirmed`，机器路径写不进这一列。"""
        now = utc_now()
        conditions = [AfKp.node_id == node_id]
        if if_rev is not None:
            conditions.append(AfKp.rev == if_rev)
        stmt = (
            update(AfKp)
            .where(*conditions)
            .values(confirmed=1 if confirmed else 0, rev=AfKp.rev + 1, updated_at=now)
        )
        result = self.session.execute(stmt)
        self.session.flush()
        if result.rowcount:
            return KpWriteResult(True, False, self.get(node_id))
        node = self.get(node_id)
        return KpWriteResult(False, node is not None, node)

    def delete_for_book(self, book_id: str) -> int:
        """删书带走全部节点：梳理作用域就是一册书，跨书 `links` 不在本表的允许域内。"""
        deleted = self.session.execute(delete(AfKp).where(AfKp.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        deleted = self.session.execute(delete(AfKp)).rowcount
        self.session.flush()
        return int(deleted or 0)
