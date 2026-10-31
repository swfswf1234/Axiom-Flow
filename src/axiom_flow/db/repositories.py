"""af_* 表仓储层：事件流水、人工意图与结果索引的读写（四表族，档 B 重建后形状）。

设计事实源：`docs/architecture/database-design.md`（列与键）、`docs/design/parsing-management.md`（判据）。
仓储只做数据访问；事务边界由调用方（orchestrator/api）控制，`flush` 后可读回同一 Session。

`af_operations` 一张表两用，由 `lifecycle` 分列（OB-1/OB-2）：
- `event` 行＝不可变流水，只答「发生过什么」，**禁作判据**；有写者的两族是 `op_kind='parse'`（解析版本，
  逐版本一行）与 `op_kind='kp'`（梳理批次流水，整批一行，`KA-27`）；
  `ingest`/`sync`/`purge`/`confirm`/`index` 五个值**建值不建写者**（与 `embedding_signature` 同处置）。
- `intent` 行＝人工当前态，唯一合法判据位，定位靠 `intent_key` 唯一键（CW-3 的单语句 upsert）。

作用域约束：`af_pages` 与 edit/confirm 意图行的键都含 `job_id`，读写必须显式给出解析版本；
structure 意图行 `job_id` 恒 `''`（页级、跨版本存活，ADR 0010 决定 1），只按 `(book_id, page_no)` 读写。
"""

from __future__ import annotations

import hashlib
import logging
import re
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
    OP_KIND_KP,
    OP_KIND_PARSE,
    OP_KIND_STRUCTURE,
    AfBook,
    AfKp,
    AfOperation,
    AfPage,
)
from axiom_flow.schemas import SAFE_SEGMENT_PATTERN

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
#: `block_type`＝产物原类型快照（只读回声），`corrected_type`＝人工改判（REQ-142 标注腿；
#: `None` 表示从未改判，回放腿只覆盖非 `None` 的那一枚）。
EDIT_PAYLOAD_KEYS = ("block_type", "verdict", "corrected_text", "corrected_bbox", "corrected_type")
STRUCTURE_PAYLOAD_KEYS = ("op", "bbox", "anchor_bbox", "members", "kind")
#: 梳理批次流水（`op_kind='kp'`）的两组闭集键（`KA-27`）：`payload`＝这次批的入参，
#: `result`＝执行体一次跑完的账面计数。禁自由键——流水是审计线索，读数形状必须可预期。
#: `review_note` 是入参（AF-T5 甲的人工评审意见），与页区间同列；缺省空串，不为「有没有备注」维持两种形状。
#: `stage`（`KA-34`）同样是入参：这一批跑了哪条腿只有请求方知道，流水是它唯一的落账处。
KP_PAYLOAD_KEYS = ("gen_batch", "page_start", "page_end", "review_note", "stage")
#: outline 行 `meta` 的两枚键（`KA-39`）：知识点身份位与其类别。**零 ALTER** 的落点就在这里——
#: 两枚值只进 JSON 扩展槽、不建列、不进 WHERE，读面（`_kp_node_out`）逐字取出，屏上据此标「非知识点」。
#: 导出面（`KA-15`）照带这两枚键交拉取方自判，本组件不因它删行、也不因它拒确认。
KP_META_KEYS = ("toc_role", "toc_kind")
#: `outline_summary_rows`（第二十三键，`KA-43`）＝`content` 批次里被写上节级总结的在册目录行数。
KP_RESULT_KEYS = (
    "outline_rows",
    "point_rows",
    "outline_summary_rows",
    "chapter_rows",
    "chapter_summary_rows",
    "chapter_narrative_rows",
    "failed_chapters",
    "failed_chapter_details",
    "candidate_rows",
    "let_away_rows",
    "unarchored",
    "dropped_depth",
    "dropped_nodes",
    "failed_sections",
    "failed_section_details",
    "symmetric_duplicates",
    "detached_to_pool",
    "dropped_edges",
    "foreign_cycles",
    "missing_page_nos",
    "unparsed_page_nos",
    "unlocated_outline_entries",
    "dropped_toc_pages",
    "outline_toc_pages",
    "outline_fallback_pages",
    "verified_outline_entries",
    "reference_rows",
    "superseded_rows",
)

#: 页确认门是否已启用（`KA-3` 的第二个析取支）。`af_pages.confirm_state` 的写者属另一条线
#: （PLAN-018 W8 的页确认端点），写者到齐前这道门不存在——把未确认的页当成「确认不了」会把
#: 整条梳理通路当场锁死。因此判据是「门未启用」这一显式事实，不从数据反推（反向判据见单测）。
PAGE_CONFIRM_GATE_ENABLED = False

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
class KpBatchRow:
    """一次梳理批次的流水输入行（`KA-27`）：落表后是 `af_operations` 的 `op_kind='kp'` 事件行。

    **整批一行**而非逐节点一行：`result` 承载的是逐节计数，失败节的编号根本没有节点可挂
    （行还没建就抛错了），逐节点形态装不下它——这是形状决定的，不是省写入。
    `job_id` = 取证版本（`KA-2` 的那一版页产物），`target_id` = 批次号：批次号与节点号同形
    （都是 `afkp-<hex12>`），读侧按 `(op_kind='kp', target_id=gen_batch)` 定位，不解析值形。
    事件行一经写入不改写（`updated_at` 恒等于 `created_at`），当前态判据一律读 `af_kp` 自身。
    """

    gen_batch: str
    book_id: str
    job_id: str
    status: str
    page_start: int | None = None
    page_end: int | None = None
    result: dict[str, Any] | None = None
    error: str = ""
    note: str = ""
    #: 这一批的入参之一（AF-T5 甲）：落 `payload`，与 `note`（实体列，人工意图行的批注位）分列。
    review_note: str = ""
    #: 这一批跑了哪条腿（`KA-34`）：同样落 `payload`；缺省 `full` 与早期批次的实际形态一致。
    stage: str = "full"


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
    corrected_type: str | None
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
            corrected_type=payload.get("corrected_type"),
            edited_at=row.created_at,
            updated_at=row.updated_at,
        )


@dataclass(frozen=True, slots=True)
class KpNodeRow:
    """一个知识点节点（`af_kp` 一行）的写入输入。

    `norm_key` 由调用方按去重口径算好传入（全角转半角＋空白折叠＋小写）：口径属分析模块的
    语义，不属数据访问层。`confirmed` 不在输入面里——机器腿恒写 0，确认位只由 `set_confirmed`
    置（`KA-11`/`KA-17`）。
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


@dataclass(frozen=True, slots=True)
class KpOutlineDecl:
    """目录整批声明的一项（`KA-8`）：只有节点身份与挂载两列，**没有** `depth`/`name`。

    层数与名是分层的产物或既有行自身的列，不属于声明面；把它们放进声明就等于让一次重排
    顺手改名，绕过 `KA-11` 的列域分列。
    """

    node_id: str
    parent_id: str | None = None
    order_hint: int = 0


@dataclass(frozen=True, slots=True)
class KpConfirmResult:
    """确认动作的结果（`KA-14`）：把 404／409／422 三种结局分列，缺一列就判不出该回哪个状态码。

    `missing` 是**逐条人读文案**，API 面整表回给 422——「可回指」的拒收必须点名缺了哪个锚点页、
    哪条边的端点，否则人只能反复试。撤回路径不跑校验，故 `missing` 恒空。
    """

    ok: bool
    rev_conflict: bool
    missing: tuple[str, ...] = ()
    node: AfKp | None = None


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


def lock_book(session: Session, book_id: str) -> None:
    """CW-4 的锁：对 `af_books` 行加悲观排他锁（MySQL `SELECT … FOR UPDATE`）。

    一把锁覆盖「一册书的整批声明」这个作用域：结构意图（逐页）与目录声明（逐批）都走它，
    两处共用同一把锁才不会出现「A 持页锁读、B 持书锁写」这类互不相干的锁序。
    SQLite 方言不渲染 `FOR UPDATE`，单写者天然串行，故同一份代码在两库都成立。
    锁内不得发起模型调用——把网络等待算进锁持有时间会让同页声明互相阻塞。
    """
    session.execute(select(AfBook.book_id).where(AfBook.book_id == book_id).with_for_update())


def new_node_id() -> str:
    """知识点节点身份 `afkp-<hex12>`：随机发放而非内容摘要，改名与重挂父级不产生新节点。"""
    return f"afkp-{uuid.uuid4().hex[:12]}"


def new_batch_id() -> str:
    """梳理批次身份 `afkp-<hex12>`（`KA-10`）：服务端随机发放，一经写入同批同值不可变。

    与 `new_node_id` 同形而不同 namespace——批次名单看不出先后，读侧的「最新」按行内 `created_at`
    分组取，不要求批次名可排序。
    """
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
    """af_operations：事件流水（parse 版本 + kp 批次）与人工意图（edit/structure）的唯一写入面。

    方法按 `lifecycle` 分三段，段与段之间没有共用写入口——这是 CW-1 能被守护的结构原因：
    事件段里只有 parse 行被条件回写（kp 行只插不改，`updated_at` 恒等于 `created_at`），
    意图段只允许唯一键 upsert，删除段只有整作用域清理。
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

    # ---------- 事件段：kp 批次流水 ----------

    def create_kp_batch(self, row: KpBatchRow) -> AfOperation:
        """梳理批次流水的插入腿（`KA-27`）：整批一行、只插不改（`updated_at` 恒等于 `created_at`）。

        列取值的三处不是随手定的：`actor='system'`（这一行是执行体跑完后的账面，人提交的事实由
        `af_kp` 行的 `gen_batch` 表达，`actor` 列的用途是确认门与自动归一可分列）；`page_no` 留 NULL
        （批次落在页区间上、不落在某一页，区间进 `payload`）；`engine` 留空（该列的文档语义是
        parse 行的显式引擎名，梳理引擎名留在 `af_kp.model_ref` 逐行上）。
        事件行**永不参与当前态判据**（`KA-4`/`KA-9`）：在飞只读 `af_kp` 的 `draft` 行。
        """
        now = utc_now()
        batch = AfOperation(
            op_id=new_op_id(),
            job_id=row.job_id,
            book_id=row.book_id,
            op_kind=OP_KIND_KP,
            lifecycle=LIFECYCLE_EVENT,
            actor="system",
            target_id=row.gen_batch,
            status=row.status,
            error=row.error[:500],
            payload={
                "gen_batch": row.gen_batch,
                "page_start": row.page_start,
                "page_end": row.page_end,
                "review_note": row.review_note,
                "stage": row.stage,
            },
            result=dict(row.result) if row.result is not None else None,
            note=row.note[:1000],
            created_at=now,
            updated_at=now,
        )
        self.session.add(batch)
        self.session.flush()
        return batch

    def list_kp_batches(self, book_id: str) -> list[AfOperation]:
        """该书的全部批次流水（进度端点的 `ledger` 读法）：按创建时间正序，同秒以 `op_id` 收尾。"""
        stmt = (
            select(AfOperation)
            .where(AfOperation.book_id == book_id, AfOperation.op_kind == OP_KIND_KP)
            .order_by(AfOperation.created_at, AfOperation.op_id)
        )
        return list(self.session.scalars(stmt))

    def find_kp_batch(self, book_id: str, gen_batch: str) -> AfOperation | None:
        """按批次号取流水行：定位靠 `(op_kind, book_id, target_id)`，不把 JSON 塞进 WHERE。

        批次号与节点号同形（都是 `afkp-<hex12>`），因此这一列承载的是「哪个批次」而不是「哪个节点」
        ——逐节点一行的形态装不下失败节的计数（`KA-27`），这里按批读回整行 `result`。
        """
        stmt = select(AfOperation).where(
            AfOperation.book_id == book_id,
            AfOperation.op_kind == OP_KIND_KP,
            AfOperation.target_id == gen_batch,
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
        corrected_type: str | None = None,
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
                "corrected_type": corrected_type,
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
        """CW-4 的锁（委托模块级 `lock_book`）：让目录声明与逐页意图声明共用同一把锁。"""
        lock_book(self.session, book_id)

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


#: `narrative_ref` 的单段名闸（`KA-20`／database-design.md 的「双闸沿用产物约定，拒绝越根」）：
#: 段规则与产物路径共用 `schemas.SAFE_SEGMENT_PATTERN`，两处不各写一份正则。
_SEGMENT_RE = re.compile(SAFE_SEGMENT_PATTERN)

#: 人工背书域（`KA-11`/`KA-12`）：`KpRepository.update` 唯一可写的列集——名称、结构挂载两列
#: （`KA-7` 的重排只改这两列）、`links`、`anchors`、`role`/`type`、`summary`。
#: 来源三列（`gen_batch`/`model_ref`/`gen_status`）与身份、确认位、`rev`、时刻列一律拒绝：
#: 机器写来源、人工写背书，两域不重叠；确认位只由 `set_confirmed` 负责。
_KP_ENDORSEMENT_WRITABLE = (    "name",
    "parent_id",
    "order_hint",
    "links",
    "anchors",
    "role",
    "type",
    "summary",
)

#: 批次三态的合法改写方向（`KA-9`）：`draft` 只能流向 `candidate`（收尾）或 `rejected`（让位与启动期
#: 归一），`candidate` 只能流向 `rejected`（人判不可用）。反向与终态回改在语句发出前就拒——
#: 「只向更终方向回写」与 parse 行同构，判据不靠调用方自觉。
GEN_FORWARD_TRANSITIONS = frozenset(
    {("draft", "candidate"), ("draft", "rejected"), ("candidate", "rejected")}
)


class KpRepository:
    """af_kp：知识点节点的读写面，四个写者各自的列域不重叠（`KA-11`）。

    机器腿＝`create`（来源域整行插入，`confirmed` 不在它的输入面里，故恒为 0）；人工腿＝`update`
    （只写背书域）＋`set_confirmed`（确认位的唯一入口）。系统腿＝归一（`flip_batch_status`、
    `rewrite_draft_links`、`detach_draft_parent`、`reject_draft_nodes`）、裁剪（`reconcile_draft_on_startup`
    走归一那条单语句路径，`delete_node` 按 `KA-21` 删行并清引用边）与**长文指针一条**
    （`set_narrative_ref`，UPDATE 通路上这一列的唯一写者，`KA-30`）：归一各条都带 `WHERE` 把
    「只碰 `draft`」钉在语句里，四条归一入口不共用 `update`。

    作用域是 `book_id` 而**不含 `job_id`**：知识点是跨版本存活的人工资产，`job_id` 只作取证溯源，
    锚点一律按行上那一版解释（`KA-18`，读面见 `list_by_evidence_version`）。
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

    def find_draft_batch(self, book_id: str) -> str | None:
        """在飞判据（`KA-4`）：该书是否存在 `gen_status='draft'` 的行，命中即返回它的批次号。

        判据只读 `af_kp` 自身的列，**不读** `op_kind='kp'` 流水行：事件行只答「发生过什么」、禁作
        当前态判据（`KA-9`）。单章批次同样按书算（`KA-25`），故这里不按页过滤。
        """
        return self.session.scalar(
            select(AfKp.gen_batch)
            .where(AfKp.book_id == book_id, AfKp.gen_status == "draft")
            .limit(1)
        )

    def list_by_evidence_version(self, book_id: str) -> dict[str, list[AfKp]]:
        """按行的取证版本分桶（`KA-18` 的读面唯一形态）。

        锚点的 `block_indexes` 属于「该行 `job_id` 那一版」的块序空间，生效指针换了也不改写锚点：
        分组键取行自身的 `job_id`，**不是** `af_books.active_job_id`。`''` 桶＝人工直填或根视图的行，
        无锚可解释，只可靠 `summary`。
        """
        grouped: dict[str, list[AfKp]] = {}
        for node in self.list_for_book(book_id):
            grouped.setdefault(node.job_id, []).append(node)
        return grouped

    def create(self, row: KpNodeRow) -> AfKp:
        """机器腿：来源域整行插入（身份、结构、锚点、`summary`、来源三列）。

        `confirmed` 不在 `KpNodeRow` 的输入面里，故机器路径写不出非零确认位（`KA-11` 的构造性保证）。
        `narrative_ref` 在语句发出前过单段名闸：这一列的 UPDATE 写者唯一（`set_narrative_ref`，`KA-30`），
        插入时给值只属机器腿的输入面形状——梳理通路恒给空串，两条通路都得守 `KA-20` 的「只存单段文件名」。
        """
        if row.narrative_ref and not _SEGMENT_RE.match(row.narrative_ref):
            raise ValueError(f"非法 narrative_ref：{row.narrative_ref!r}（`KA-20`：只存单段文件名）")
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
        """人工腿的背书域写入（`KA-11`/`KA-12`）＋ CW-6 的乐观并发落点。

        只许写 `_KP_ENDORSEMENT_WRITABLE`：来源三列（`gen_batch`/`model_ref`/`gen_status`）、身份、
        确认位与时刻列一律在语句发出前拒绝——人工修订不改来源，`rev+1` 是唯一递增的序号。

        `if_rev=None` 表示不比对序号（幂等整节点声明走 CW-3 式覆盖）；带 `if_rev` 而行被他人写过
        则 rowcount=0 ⇒ `rev_conflict=True`，API 面据此判 409——人已不在该页时提交的旧表单
        不得静默覆盖新结论。
        """
        unknown = sorted(set(fields) - set(_KP_ENDORSEMENT_WRITABLE))
        if unknown:
            raise ValueError(f"af_kp 人工背书域之外的列：{unknown}")
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

    def set_confirmed(
        self, node_id: str, *, confirmed: bool, if_rev: int | None = None
    ) -> KpWriteResult:
        """确认位的唯一入口（`KA-11`/`KA-17`）：机器路径写不进这一列，人工的置与撤都只走这里。

        本方法**不做前置校验**：「可回指」的判据在 `confirm`（随端点面交付，`KA-14`）。撤回
        （1→0）只影响本侧导出面，不发删除、不发消息、不回收任何行（`KA-17`）。
        """
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

    def confirm(
        self, node_id: str, *, confirmed: bool, if_rev: int | None = None
    ) -> KpConfirmResult:
        """确认／撤回的唯一带校验入口（`KA-14` 的可回指前置校验 + `KA-3` 的确认门析取支）。

        判定序本身就是契约：行不存在（404）→ `rev` 不匹配（409，**先于**形状校验，旧表单的冲突
        不得被读成「行不合形状」的 422）→ 撤回直接落 `set_confirmed`（`KA-17` 的「永远可撤」：
        锚坏了的行不能卡在已确认态）→ 只有置位跑前置校验，缺失项逐条点名回 422。
        本方法不碰 `confirmed` 列，写库仍只经 `set_confirmed` 一处（`KA-11` 的确认位唯一入口）。
        """
        node = self.get(node_id)
        if node is None:
            return KpConfirmResult(False, False, (), None)
        if if_rev is not None and node.rev != if_rev:
            return KpConfirmResult(False, True, (), node)
        if not confirmed:
            written = self.set_confirmed(node_id, confirmed=False, if_rev=if_rev)
            return KpConfirmResult(written.ok, written.rev_conflict, (), written.node)
        missing = self._endorsement_missing(node)
        if missing:
            return KpConfirmResult(False, False, tuple(missing), node)
        written = self.set_confirmed(node_id, confirmed=True, if_rev=if_rev)
        return KpConfirmResult(written.ok, written.rev_conflict, (), written.node)

    def _endorsement_missing(self, node: AfKp) -> list[str]:
        """「可回指」的判据清单（`KA-14`）：空表即可确认；每条文案都点名缺的是什么。

        锚点页按**行自身** `job_id` 那一版解释（`KA-18`）：生效指针换了不改写锚点读数，页集也不能
        换成新版——否则同一行会随别人的重解析忽而可确认、忽而不可确认。`job_id=''` 的人工直填行
        没有取证版本，锚点无处落地，因此只报这一条、不再逐页判（逐页报只会淹没真正的原因）。
        `role`/`type`/`summary` 允许空：它们是描述不是证据。
        """
        missing: list[str] = []
        anchors = [anchor for anchor in (node.anchors or []) if isinstance(anchor, dict)]
        if not anchors:
            missing.append("anchors 为空：无锚的点一旦进主表就无人能复核")
        pages = sorted({int(anchor["page"]) for anchor in anchors if anchor.get("page") is not None})
        if anchors and not pages:
            missing.append("anchors 缺 page：锚点必须指到具体页")
        elif pages and not node.job_id:
            missing.append("job_id 为空：该行没有取证版本，锚点页无法解释")
        elif pages:
            states = self._page_states(node.book_id, node.job_id, pages)
            for page in pages:
                entry = states.get(page)
                if entry is None:
                    missing.append(f"锚点页 {page} 不在取证版本 {node.job_id} 的页集内")
                elif entry[0] != "parsed":
                    missing.append(f"锚点页 {page} 在取证版本内不是 parsed（当前 {entry[0]}）")
                elif PAGE_CONFIRM_GATE_ENABLED and entry[1] != "confirmed":
                    missing.append(f"锚点页 {page} 尚未人工确认（页确认门已启用）")
        for link in node.links or []:
            if not isinstance(link, dict):
                continue
            kind = str(link.get("kind") or "")
            to = str(link.get("to") or "")
            if to == node.node_id:
                missing.append(f"关系边自环：{kind} 指向自己")
            elif not to:
                missing.append(f"关系边缺端点：{kind} 的 to 为空")
        endpoints = dict.fromkeys(
            str(link["to"])
            for link in (node.links or [])
            if isinstance(link, dict) and link.get("to") and link["to"] != node.node_id
        )
        if endpoints:
            known = self._known_nodes(node.book_id, list(endpoints))
            for to in endpoints:
                if to not in known:
                    missing.append(f"关系边的端点不存在：{to}")
        return missing

    def _page_states(self, book_id: str, job_id: str, pages: Sequence[int]) -> dict[int, tuple[str, str]]:
        """取证版本内这些页的 `(status, confirm_state)`：缺行的页不出现在结果里（＝不在页集内）。"""
        stmt = (
            select(AfPage.page_no, AfPage.status, AfPage.confirm_state)
            .where(
                AfPage.book_id == book_id,
                AfPage.job_id == job_id,
                AfPage.page_no.in_(list(pages)),
            )
        )
        return {int(no): (status, state or "") for no, status, state in self.session.execute(stmt)}

    def _known_nodes(self, book_id: str, node_ids: Sequence[str]) -> set[str]:
        stmt = select(AfKp.node_id).where(AfKp.book_id == book_id, AfKp.node_id.in_(list(node_ids)))
        return {node_id for (node_id,) in self.session.execute(stmt)}

    def replace_outline(
        self, book_id: str, declarations: Sequence[KpOutlineDecl]
    ) -> list[AfKp]:
        """目录整批声明（`KA-8`）：锁内「读现有→算差异→写全部」，缺席的 outline 不下线、只让它点子点退池。

        只写 `parent_id`/`order_hint` 两列且写库仍走人工腿的 `update`（`KA-7` 的「重排不删不重建」；
        背书域之外的列在这条通路上同样改不动）。**校验全部先于写入**：半批落库的目录比一次拒收难收拾。
        与逐页结构意图共用 `lock_book` 那一把 `af_books` 行锁（`KA-8` 的同一事务要求）。
        返回声明序的行集，供端点面原样回显当前目录。
        """
        lock_book(self.session, book_id)
        existing = {
            node.node_id: node
            for node in self.list_for_book(book_id, node_kind="outline")
        }
        declared: dict[str, KpOutlineDecl] = {}
        for decl in declarations:
            node = self.get(decl.node_id)
            if node is None:
                raise ValueError(f"目录声明的节点不存在：{decl.node_id}")
            if node.book_id != book_id:
                raise ValueError(f"目录声明的节点不属于本书 {book_id}：{decl.node_id}")
            if node.node_kind != "outline":
                raise ValueError(f"目录声明只覆盖 node_kind='outline' 的行：{decl.node_id}")
            declared[decl.node_id] = node, decl
        self._validate_outline_shape(existing, declared)
        for node_id, (_node, decl) in declared.items():
            self.update(node_id, {"parent_id": decl.parent_id, "order_hint": decl.order_hint})
        detached = [node_id for node_id in existing if node_id not in declared]
        if detached:
            stmt = select(AfKp.node_id).where(
                AfKp.book_id == book_id,
                AfKp.node_kind == "point",
                AfKp.parent_id.in_(detached),
            )
            for node_id in [node_id for (node_id,) in self.session.execute(stmt)]:
                self.update(node_id, {"parent_id": None})
        self.session.flush()
        return [self.get(decl.node_id) for decl in declarations]  # type: ignore[misc]

    def _validate_outline_shape(
        self,
        existing: dict[str, AfKp],
        declared: dict[str, tuple[AfKp, KpOutlineDecl]],
    ) -> None:
        """声明面自身的四条形状：两层闭集、`depth=1` 无父、`depth=2` 的父是本批 `depth=1` 行、同父不重名。

        父的合法性只看**本批声明**（缺席的行不在当前目录里，挂上去等于挂进一个没人声明的位置）。
        重名按声明后的最终父分组比 `norm_key`——口径仍只有 `norm_key_for` 一处（`KA-8` 的整批形态与
        `ix_af_kp_norm` 的单行形态共用同一份存储值），点名的是「既有的那一行」，按创建序取最先。
        """
        depths: dict[str, int | None] = {}
        for node_id, (node, _decl) in declared.items():
            depths[node_id] = node.depth
        violations: list[str] = []
        for node_id, (node, decl) in declared.items():
            if node.depth not in (1, 2):
                violations.append(f"目录行的 depth 不在两层闭集内：{node_id}（depth={node.depth}）")
                continue
            if node.depth == 1 and decl.parent_id is not None:
                violations.append(f"depth=1 的目录行不可有父：{node_id}")
            elif node.depth == 2:
                parent_depth = depths.get(decl.parent_id or "")
                if decl.parent_id is None or decl.parent_id == node_id or parent_depth != 1:
                    violations.append(
                        f"depth=2 的目录行必须挂在本批声明的 depth=1 行下：{node_id}"
                        f"（父={decl.parent_id}，该父的本批 depth={parent_depth}）"
                    )
        if violations:
            raise ValueError("；".join(violations))
        final_parent = {
            node_id: declared[node_id][1].parent_id if node_id in declared else node.parent_id
            for node_id, node in existing.items()
        }
        grouped: dict[tuple[str | None, str], list[AfKp]] = {}
        for node_id, node in existing.items():
            grouped.setdefault((final_parent[node_id], node.norm_key), []).append(node)
        conflicts = []
        for (_parent, norm_key), nodes in grouped.items():
            if len(nodes) > 1:
                first = min(nodes, key=lambda item: (item.created_at, item.node_id))
                others = [item.node_id for item in nodes if item.node_id != first.node_id]
                conflicts.append(
                    f"同一父下重名（norm_key={norm_key}）：{first.node_id} 已占用，"
                    f"{others} 不能挂同一父"
                )
        if conflicts:
            raise ValueError("；".join(conflicts))

    def list_batch(
        self, book_id: str, gen_batch: str, *, gen_status: str | None = None
    ) -> list[AfKp]:
        """按批次读整批行（`KA-25` 的分组键 `book_id + gen_batch`）：收尾与裁剪的唯一整批读法。

        定序用 `(created_at, node_id)`——批次号是随机发放的、本身不可排序（`KA-10`），「最新」只能按
        行内时间读。
        """
        conditions: list[Any] = [AfKp.book_id == book_id, AfKp.gen_batch == gen_batch]
        if gen_status is not None:
            conditions.append(AfKp.gen_status == gen_status)
        stmt = select(AfKp).where(*conditions).order_by(AfKp.created_at, AfKp.node_id)
        return list(self.session.scalars(stmt))

    def confirmed_identity_keys(self, book_id: str) -> set[tuple[str, str | None, str]]:
        """让位判据的对手集合（`KA-13`）：身份＝`(node_kind, parent_id, norm_key)` 三元组。

        只收 `confirmed=1` 的行——让位的判据位是**人的背书**，不是行的新旧：机器批次之间不互相否决，
        否则 `rejected` 就成了机器自相残杀的位。`node_kind` 进身份是因为 `ix_af_kp_norm` 的命名空间
        含它：同名的目录行与点行是两个结论，不撞车。
        """
        rows = self.session.execute(
            select(AfKp.node_kind, AfKp.parent_id, AfKp.norm_key).where(
                AfKp.book_id == book_id, AfKp.confirmed == 1
            )
        )
        return {(node_kind, parent_id, norm_key) for node_kind, parent_id, norm_key in rows}

    def find_norm_conflict(
        self,
        *,
        book_id: str,
        node_kind: str,
        parent_id: str | None,
        norm_key: str,
        exclude_node_id: str | None = None,
    ) -> str | None:
        """「同父重名」判据位的唯一一处：设计篇把处置写成「按 `ix_af_kp_norm` 查重后 422 并点名既有 `node_id`」。

        不建唯一键的代价就是把这条校验钉在服务端：判据只此一处，HTTP 的 422 映射与人工声明端点都
        调它，机器腿的让位另按 `KA-13`（只看 `confirmed=1`）判，两件事不共用一次查询。
        `parent_id` 为空走 `IS NULL`——未归档池与「挂在某个父下」是两个作用域，不能互相撞名。
        """
        conditions: list[Any] = [
            AfKp.book_id == book_id,
            AfKp.node_kind == node_kind,
            AfKp.norm_key == norm_key,
            AfKp.parent_id == parent_id if parent_id is not None else AfKp.parent_id.is_(None),
        ]
        if exclude_node_id is not None:
            conditions.append(AfKp.node_id != exclude_node_id)
        return self.session.scalar(
            select(AfKp.node_id)
            .where(*conditions)
            .order_by(AfKp.created_at, AfKp.node_id)
            .limit(1)
        )

    def flip_batch_status(
        self, book_id: str, gen_batch: str, *, source: str, target: str
    ) -> int:
        """系统腿的归一（`KA-9`）：整批一条条件 UPDATE，改写方向不在前进表里就在语句发出前拒。

        `rev` 与 `updated_at` 随每次成功写入自增（列契约），包括这种批次级翻面；导出面按 `rev` 增量
        拉取时，一批中断批次的作废会表现为一次整体前移，这是「三态只由 `gen_status` 表达」的代价。
        """
        if (source, target) not in GEN_FORWARD_TRANSITIONS:
            raise ValueError(f"af_kp 批次三态的非法改写方向：{source} → {target}")
        result = self.session.execute(
            update(AfKp)
            .where(
                AfKp.book_id == book_id,
                AfKp.gen_batch == gen_batch,
                AfKp.gen_status == source,
            )
            .values(gen_status=target, rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        return int(result.rowcount or 0)

    def reject_superseded_candidates(
        self, book_id: str, gen_batch: str, *, page_start: int, page_end: int, node_kind: str | None = None
    ) -> int:
        """重跑取代（`KA-36`）：同书其它批次、落在本批页区间内的**未背书候选行**翻 `rejected`。

        判据是四元合取，缺一条都不作废：同书（别的书的旧行与这次重跑无关）、非本批（本批此刻还是
        `draft`，写成条件是为了让语句自身可读）、`candidate`（`draft` 属在飞批次、`rejected` 已是终态）、
        `confirmed=0`（人已背书的行是「这本书的结论」，重跑只否「这一段的旧读数」）。页区间的判据是
        **任一锚点页**入区间：跨在边界上的行，它的读数已经与这一段的取证版本对齐过。

        `node_kind`（`KA-43`）把作废面收到一种行上：`content` 批次不重做目录，在册目录行是它**正在
        使用**的结构而不是过期读数，整面作废会把这一批点行的父一起抽掉。缺省 `None`＝两种行都在面里
        （`full` 批次的既有口径逐字不动）。

        锚点是 JSON 列，区间判定放在 Python 里做：候选行的规模是「一本书的行数」，一次取回再过滤比把
        JSON 下推成 SQL 更可读，也免得两库的 JSON 语法分叉。回写的 `WHERE` 仍带 `candidate` 与
        `confirmed=0` 两道条件——读与写之间的竞态由语句自身兜住。
        """
        conditions: list[Any] = [
            AfKp.book_id == book_id,
            AfKp.gen_batch != gen_batch,
            AfKp.gen_status == "candidate",
            AfKp.confirmed == 0,
        ]
        if node_kind is not None:
            conditions.append(AfKp.node_kind == node_kind)
        rows = self.session.execute(select(AfKp.node_id, AfKp.anchors).where(*conditions))
        ids = [
            node_id
            for node_id, anchors in rows
            if any(page_start <= int(anchor.get("page") or 0) <= page_end for anchor in anchors or [])
        ]
        if not ids:
            return 0
        result = self.session.execute(
            update(AfKp)
            .where(
                AfKp.node_id.in_(ids),
                AfKp.gen_status == "candidate",
                AfKp.confirmed == 0,
            )
            .values(gen_status="rejected", rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        return int(result.rowcount or 0)

    def reject_draft_nodes(self, node_ids: Sequence[str]) -> int:
        """让位的单语句路径（`KA-13`）：`WHERE gen_status='draft'` 让「只作废未定稿行」不靠调用方自觉。

        已让位的行留在表里而不删：`KA-9` 的三态就是批次账面，删了就读不出「这一批想过它」。
        """
        ids = list(node_ids)
        if not ids:
            return 0
        result = self.session.execute(
            update(AfKp)
            .where(AfKp.node_id.in_(ids), AfKp.gen_status == "draft")
            .values(gen_status="rejected", rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        return int(result.rowcount or 0)

    def rewrite_draft_links(self, node_id: str, links: list[dict[str, Any]] | None) -> bool:
        """收尾归一（对称边幂等）与裁剪（拒环边）唯一的写入口：只改 `draft` 行的 `links`。

        `candidate` 及以上行零被触碰（`KA-11`）由 `WHERE` 守住；环路的另一端若是已定稿行，本方法
        写不进去，调用方只能把那条环报成 `foreign_cycles` 而不修它。空表写 `NULL` 而非 `[]`：与
        「无关系」的列语义一致，读侧统一 `links or []`。
        """
        result = self.session.execute(
            update(AfKp)
            .where(AfKp.node_id == node_id, AfKp.gen_status == "draft")
            .values(links=links or None, rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        return bool(result.rowcount)

    def detach_draft_parent(self, node_id: str) -> bool:
        """挂父来源违法时退回未归档池（`KA-26`/`KA-7`）：只改 `draft` 行的 `parent_id`，不改到别的父上。

        机器「猜父」的禁止也包括「猜到一个违法父上」；退 `NULL` 是 `KA-26` 承认的另一种合法来源，
        入池后由人重挂（`KA-26`）。
        """
        result = self.session.execute(
            update(AfKp)
            .where(AfKp.node_id == node_id, AfKp.gen_status == "draft")
            .values(parent_id=None, rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        return bool(result.rowcount)

    def repoint_draft_outline(
        self, *, book_id: str, gen_batch: str, patches: Sequence[dict[str, Any]]
    ) -> int:
        """同批定稿改写（`KA-40`）：把早落的目录行改写到终值锚点／屏上序，只写 `anchors`/`order_hint`/`parent_id`。

        逐页落行时偏移还没标定全，锚与序都是临时读数；定稿这一次是同一批内唯一的改写，不是新一轮
        背书——所以 `rev` **不递增**（早落行与定稿行是同一批的两次读数，序号加上去，人工的 `if_rev`
        就会把「机器自己把锚修准了」读成「有人改过这行」而平白撞一次 409）。改名与改层级走不到这里：
        `name`/`depth` 不在可写列里，形状在语句发出前就拒。

        `WHERE` 四条件是判据位，不是优化：`book_id` ∧ `gen_batch` 把范围钉在「这一批自己的行」上（别人家的
        批次、上一批的旧候选都碰不到）、`gen_status='draft'` 只碰未定稿行（`KA-9`：收尾之后这一批归 `candidate`，
        改写窗口就此关闭）、`confirmed=0` 护住人的背书（`KA-11`——`set_confirmed` 不做前置校验，`draft ∧
        confirmed=1` 是可达状态，光靠 `draft` 挡不住已确认的行）；四条之外逐条再钉 `node_id` 认行。
        """
        for patch in patches:
            unknown = sorted(set(patch) - {"node_id", "anchors", "order_hint", "parent_id"})
            if unknown:
                raise ValueError(f"af_kp 同批改写面之外的键：{unknown}（`KA-40`：只改锚、序、挂父）")
        written = 0
        now = utc_now()
        for patch in patches:
            values = {key: patch[key] for key in ("anchors", "order_hint", "parent_id") if key in patch}
            if not values:
                continue
            result = self.session.execute(
                update(AfKp)
                .where(
                    AfKp.node_id == str(patch["node_id"]),
                    AfKp.book_id == book_id,
                    AfKp.gen_batch == gen_batch,
                    AfKp.gen_status == "draft",
                    AfKp.confirmed == 0,
                )
                .values(**values, updated_at=now)
            )
            written += int(result.rowcount or 0)
        self.session.flush()
        return written

    def set_outline_summary(self, *, book_id: str, node_id: str, summary: str) -> bool:
        """节级总结的唯一系统腿（第五条，`KA-43`）：只写 `summary` 一列，且只在原值为空串时写。

        `summary` 是人工也写的列（`_KP_ENDORSEMENT_WRITABLE` 含它），所以「原值为空」是判据位而不是
        优化：覆盖人已写过的那份就违反 `KA-11` 的「机器不与人工争同一身份」。`rev` **不递增**，理由与
        `KA-40` 的同批定稿写手同一条——机器自己补一份读数不该让人工的 `if_rev` 平白撞一次 409。

        `WHERE` 四条件＝`book_id` ∧ `node_kind='outline'` ∧ `confirmed=0` ∧ 原 `summary` 为空串，
        逐条再钉 `node_id` 认行；`node_kind` 那一道不是冗余：点行的 `summary` 是点级导读，写手不认它。
        返回是否真的写进去了一行，调用方据此计数（`result.outline_summary_rows`）。
        """
        text = (summary or "").strip()
        if not text:
            return False
        result = self.session.execute(
            update(AfKp)
            .where(
                AfKp.node_id == node_id,
                AfKp.book_id == book_id,
                AfKp.node_kind == "outline",
                AfKp.confirmed == 0,
                func.coalesce(AfKp.summary, "") == "",
            )
            .values(summary=text[:1000], updated_at=utc_now())
        )
        self.session.flush()
        return bool(result.rowcount)

    def regenerate_outline_summary(
        self, node_id: str, *, summary: str, if_rev: int | None = None
    ) -> KpWriteResult:
        """单点重生成（`KA-45`）：人工在知识点详情面按「重新生成总结」，**覆盖**该行的 `summary`。

        与 `set_outline_summary` 的唯一分别就是覆盖：那是机器在内容批次里补空白，碰不到人写过的值
        （`KA-11`）；这是人主动要新的一份，旧值被替换是预期内的。`rev` 递增，因为屏上读到的那一份
        确实变了——下一次人工保存的 `if_rev` 该按新序号走。
        `WHERE` 仍钉 `node_kind='outline'`：点行（旧第三层）的 `summary` 不是节级总结，重生成不写它。
        """
        text = (summary or "").strip()
        if not text:
            return KpWriteResult(False, False, self.get(node_id))
        conditions = [AfKp.node_id == node_id, AfKp.node_kind == "outline"]
        if if_rev is not None:
            conditions.append(AfKp.rev == if_rev)
        result = self.session.execute(
            update(AfKp)
            .where(*conditions)
            .values(summary=text[:1000], rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        if result.rowcount:
            return KpWriteResult(True, False, self.get(node_id))
        node = self.get(node_id)
        return KpWriteResult(False, node is not None, node)

    def set_narrative_ref(self, node_id: str, narrative_ref: str, *, if_rev: int | None = None) -> KpWriteResult:
        """长文指针的唯一系统腿（`KA-30`）：只写 `narrative_ref` 一列，值必须先过单段名闸。

        这一列的其余三条腿都碰不到它——机器 `create` 只在插入时给值、人工 `update` 的白名单不含它
        （`KA-11`）、确认位与归一各条不途经。`rev` 与 `updated_at` 随成功写入自增，与归一各条同形：
        序号不是「只有人改过」的计数器，判据位是 `confirmed`。

        `if_rev` 钉在 `WHERE` 里守竞态；端点面在**落盘之前**还会先读一次 `rev` 判 409（`KA-30` 的判定序：
        磁盘写入不可回滚，旧表单不得先把文件改了再被序号拒）。空串不是合法指针——「没有长文」由
        `GET` 的 `exists=false` 披露，不靠把列写空来表达。
        """
        if not narrative_ref or not _SEGMENT_RE.match(narrative_ref):
            raise ValueError(f"非法 narrative_ref：{narrative_ref!r}（`KA-20`：只存单段文件名）")
        conditions = [AfKp.node_id == node_id]
        if if_rev is not None:
            conditions.append(AfKp.rev == if_rev)
        result = self.session.execute(
            update(AfKp)
            .where(*conditions)
            .values(narrative_ref=narrative_ref, rev=AfKp.rev + 1, updated_at=utc_now())
        )
        self.session.flush()
        if result.rowcount:
            return KpWriteResult(True, False, self.get(node_id))
        node = self.get(node_id)
        return KpWriteResult(False, node is not None, node)

    def reconcile_draft_on_startup(self) -> list[tuple[str, str, int]]:
        """启动期归一（`KA-9`／设计篇「进程中途死」）：残留 `draft` 按 `(book_id, gen_batch)` 整批判 `rejected` 并告警。

        没跑到收尾的批次会让 `KA-4` 的在飞判据永久为真——不处置就再没人能对这本书发起梳理。
        处置只做一件事：翻 `gen_status`。不删数据、不碰 `confirmed`、不动 `candidate` 及以上任何行
        （`KA-11` 的「归一与裁剪两条单语句路径」里，本方法属归一）。返回逐组处置清单供日志与测试读；
        没有残留就返回空表，重复调用第二次必然为空。
        """
        groups = self.session.execute(
            select(AfKp.book_id, AfKp.gen_batch, func.count())
            .where(AfKp.gen_status == "draft")
            .group_by(AfKp.book_id, AfKp.gen_batch)
            .order_by(AfKp.book_id, AfKp.gen_batch)
        )
        disposed: list[tuple[str, str, int]] = []
        for book_id, gen_batch, count in groups:
            flipped = self.flip_batch_status(
                book_id, gen_batch, source="draft", target="rejected"
            )
            if not flipped:
                continue
            log.warning(
                "启动期归一：%s 的批次 %s 残留 %s 行 draft，整批判 rejected（中断批次，不进导出面）",
                book_id,
                gen_batch,
                int(flipped),
            )
            disposed.append((book_id, gen_batch, int(count)))
        return disposed

    def delete_node(self, node_id: str) -> bool:
        """显式删除一个节点（`KA-21`）：删行与清引用边在同一事务里完成，锁内整列覆写。

        「删除必须清引用边」（database-design.md「服务端不变式」）的处置面点名 `links`：对本册所有
        以 `links` 指向它的行整列覆写，只摘那几条、其余边逐条原样（读侧并集靠它们，`note` 与键序都不动）。
        悬边会让那些行在 `KA-14` 的确认门前判 422——一次删除就变成一堆确认不了的行。
        `parent_id` 不在清除范围：`KA-21` 只说边。失父的行由导出面尾置承接（`order_for_export`）、
        由人重挂或退池（`KA-7`/`KA-8`），删除不替人改结构。
        本方法属系统的「裁剪」一路，故不复用人工腿的 `update`（域白名单只放背书域），
        也不写 `op_kind='purge'` 流水（该值仍「建值不建写者」）。长文文件在本事务之外清，
        形状见 `orchestrator/knowledge.py:remove_node`。

        按书逐行扫、在 Python 里判空，而不是用 SQL 判 `links IS NOT NULL`：JSON 列把 Python `None`
        存成序列化的 `null`，SQL 的空判据筛不掉它，读回来仍是 `None`。
        """
        node = self.get(node_id)
        if node is None:
            return False
        lock_book(self.session, node.book_id)
        self.session.delete(node)
        self.session.flush()
        now = utc_now()
        for holder in self.session.scalars(select(AfKp).where(AfKp.book_id == node.book_id)):
            links = list(holder.links or [])
            kept = [
                link
                for link in links
                if not isinstance(link, dict) or str(link.get("to") or "") != node_id
            ]
            if len(kept) == len(links):
                continue
            self.session.execute(
                update(AfKp)
                .where(AfKp.node_id == holder.node_id)
                .values(links=kept or None, rev=AfKp.rev + 1, updated_at=now)
            )
        self.session.flush()
        return True

    def delete_for_book(self, book_id: str) -> int:
        """删书带走全部节点：梳理作用域就是一册书，跨书 `links` 不在本表的允许域内。"""
        deleted = self.session.execute(delete(AfKp).where(AfKp.book_id == book_id)).rowcount
        self.session.flush()
        return int(deleted or 0)

    def delete_all(self) -> int:
        deleted = self.session.execute(delete(AfKp)).rowcount
        self.session.flush()
        return int(deleted or 0)
