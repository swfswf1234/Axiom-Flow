"""梳理作业通路：受理门与批次身份（`KA-1`/`KA-4`/`KA-10`/`KA-25`）＋两阶段执行体与锚点复核
（`KA-2`/`KA-5`/`KA-6`/`KA-7`/`KA-19`/`KA-26`/`KA-29`/`KA-33`）＋批次收尾与启动期归一（`KA-9`/`KA-13`/`KA-26`）
＋长文两条腿与删除通路（`KA-20`/`KA-21`/`KA-30`）＋批次流水的第十六键点名（`KA-27`/`KA-32`/`KA-33`）。

设计事实源：`docs/design/knowledge-analysis.md`——本文件只把 `KA-n` 落成代码，判据正文一处不复制。
编排层不引 HTTP 类型：受理门与执行期复查抛 `KpGateError`，状态码由 API 面按 `status_code` 映射
（与 `EngineSelectionError` → 400 同一条通路）。批次流水的构造（`kp_batch_row`）、导出序与
导出面的复核标记也在本文件；流水的**发出**（`JobRunner` 的 knowledge 腿）与端点面在
`jobs.py`/`api/main.py`。
"""

from __future__ import annotations

import logging
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from axiom_flow.db.llm_ledger import KpCallRow, LlmCallIdentity, LlmCallLedger
from axiom_flow.db.models import AfKp
from axiom_flow.db.repositories import (
    PAGE_CONFIRM_GATE_ENABLED,
    BookRepository,
    EditRow,
    KpBatchRow,
    KpNodeRow,
    KpRepository,
    OperationRepository,
    PageRepository,
    new_batch_id,
    new_node_id,
)
from axiom_flow.engines.base import (
    ChapterInput,
    ChapterReply,
    EngineError,
    HeadingEvidence,
    KnowledgeEngine,
    OutlineEntry,
    PageBody,
    PageSample,
    SectionInput,
    SectionPoints,
    TocPagePick,
    TocVerifyAnswer,
    TocVerifyItem,
)
from axiom_flow.ingest import (
    atomic_write_text,
    book_dir_for,
    ensure_safe_segment,
    ensure_within,
    is_transient_name,
    version_dir_for,
)
from axiom_flow.normalize.furniture import furniture_hints, furniture_texts, strip_furniture
from axiom_flow.normalize.intents import apply_relabels, relabels_by_block
from axiom_flow.normalize.toc import (
    NODE_TYPE_CHAPTER,
    NODE_TYPE_POINT,
    TOC_PAGE_SCAN_LIMIT,
    TOC_ROLE_KNOWLEDGE,
    TOC_ROLE_REFERENCE,
    LocatedEntry,
    TocEntry,
    anchor_block_for_name,
    clean_entry_rows,
    drop_unlocated_toc_pages,
    entry_level,
    locate_entries,
    node_type_of,
    node_types_of,
    page_sample_text,
    reference_role_for_name,
    toc_entries,
)
from axiom_flow.schemas import BlocksPage, BlockType

logger = logging.getLogger(__name__)

#: 机器写出的页块锚的 `kind` 值（`anchors` 的形状见 `database-design.md`「af_kp 列清单」；
#: 根侧提升时追加的 `{kind:'af_node'}` 幂等锚由拉取方写，`KA-16`）。
KP_ANCHOR_KIND = "page_block"
#: 目录两层封顶（`KA-6`）：`depth=1` 章、`depth=2` 节，第三层不进 `af_kp`。
OUTLINE_MAX_DEPTH = 2
NODE_KIND_OUTLINE = "outline"
NODE_KIND_POINT = "point"

#: 目录行的产出者标识（`af_kp.model_ref`）：三值闭集＝模型整理出的条目（`KA-35`／`KA-37` 正腿）、
#: 确定性条目腿（同条的回落腿）、heading 块（全书零条目时的末位退路）。人工改判不改本列（`KA-12`）。
OUTLINE_MODEL_REF_LLM = "llm:toc"
OUTLINE_MODEL_REF_TOC = "rule:toc"
OUTLINE_MODEL_REF_HEADING = "rule:heading"
#: 核对腿的候选页半径（`KA-38`）：偏移页 ±2 页里的行才交给模型比对——再宽就成了「全书找一遍」，
#: 那正是先查后核要避免的事；也比 `TOC_LOCATE_SLACK` 宽一档，因为要核的本来就是窗口内没命中的行。
TOC_VERIFY_PAGE_SLACK = 2

#: `links[].kind` 闭集（`database-design.md` 同列行）：闭集外的草稿按「模型输出不合形状」处理——
#: 不写、计数。「先入 `note`」是人工背书时的记法，机器腿不替人做那个折算。
LINK_KINDS = ("prerequisite", "co_requisite", "explains", "exemplifies", "extends")
#: 参与 DAG 校验的两种次序语义（`database-design.md`「DAG 只覆盖次序语义」）：其余三种成环合法。
ORDERING_LINK_KINDS = ("prerequisite", "extends")
#: 对称语义的那一种：写入前按 `min,max` 归一端点序，反向重复声明幂等（同节「对称边归一端点序」）。
SYMMETRIC_LINK_KIND = "co_requisite"
#: `type` 的 point 四分类与 `role` 的取值域（`models.py` 两条列注释）：域外值不写进列，
#: 免得实现批自己造出第六个域值（本案硬边界＝零新域值）。
POINT_TYPES = ("memory", "procedure", "concept", "design")
POINT_ROLES = ("", "core", "explainer", "example")


class KpGateError(Exception):
    """受理门与执行期复查的拒绝：`status_code` 只是给 API 面的映射位（404／422／409），判据本身在 `KA-n`。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class KpAcceptance:
    """受理成功给出的批次身份与取证范围：执行体据此写本批每一行，不再回头读 `af_books`。

    `evidence_job_id` 是受理一刻的 `af_books.active_job_id`（`KA-1`），它将被写进本批每行的
    `job_id`；执行期指针若被换掉，已受理的批次仍按受理版本取证。
    """

    book_id: str
    evidence_job_id: str
    gen_batch: str
    page_start: int
    page_end: int
    #: 人工评审意见（AF-T5 甲）：执行体把它递给适配器进 prompt，并随批次流水存档。
    review_note: str = ""
    #: 本批跑哪几条腿（`KA-34`）：`outline` 时知识点阶段整条不发起。闭集由 schema 层守住。
    stage: str = "full"


def accept_kp_job(
    session: Session,
    *,
    book_id: str,
    page_start: int | None = None,
    page_end: int | None = None,
    review_note: str = "",
    stage: str = "full",
) -> KpAcceptance:
    """受理门：四道门槛全判在写入之前，通过才发批次号（`KA-1`/`KA-4`/`KA-10`/`KA-25`/`KA-43`）。

    顺序固定：书目（404）→ 生效版本（422）→ 页区间（422）→ 在册目录（422，仅 `content`）→
    同书在飞（409）。把在飞判据排在最后是因为它要读 `af_kp`，而在册目录门也读 `af_kp`、排在页区间
    之后：页区间先判，读侧就不会为一枚越界请求去翻整册行。五者都不产生写入。

    `content` 的在册目录门（`KA-43`）不是完备性检查而是**语义前提**：这一值不跑目录腿、直接拿在册行
    切区间，零枚在册行时每一枚点都只能进池（`KA-7`），人工拿到的是一份平铺的假产出。判据只认
    `gen_status='candidate'`——`draft` 是在飞读数、`rejected` 是已被取代的旧读数，两者都切不出可信骨架。

    「受理到首行落库」之间窗口（模型还没产出任何 `draft` 行）不由本门守住，而由单 worker 串行队列
    与执行期的复查守住（`PM-13`，见 `run_knowledge_job`）：后发的批次在执行期第一眼就撞见前一批的
    `draft` 行而自止。
    """
    book = BookRepository(session).get(book_id)
    if book is None:
        raise KpGateError(404, f"书目不存在：{book_id}")
    evidence_job_id = book.active_job_id
    if not evidence_job_id:
        raise KpGateError(422, f"梳理不受理：{book_id} 无生效解析版本，锚点将无处解释（零写入）")

    page_count = int(book.page_count or 0)
    if (page_start is None) != (page_end is None):
        raise KpGateError(
            422,
            f"单章范围须同时给出起止页：{book_id} 收到 page_start={page_start} page_end={page_end}",
        )
    start = 1 if page_start is None else int(page_start)
    end = page_count if page_end is None else int(page_end)
    if start < 1 or end > page_count or start > end:
        raise KpGateError(422, f"页区间越界：{book_id} 共 {page_count} 页，请求 {start}..{end}")

    if stage == "content" and not _registered_outline_rows(session, book_id):
        raise KpGateError(
            422, f"内容解析不受理：{book_id} 还没有在册目录行，先解析目录（`KA-43`，零写入）"
        )

    in_flight = KpRepository(session).find_draft_batch(book_id)
    if in_flight is not None:
        raise KpGateError(409, f"该书已有在飞梳理批次：{book_id} 的 {in_flight}（同书至多一个）")

    return KpAcceptance(
        book_id=book_id,
        evidence_job_id=evidence_job_id,
        gen_batch=new_batch_id(),
        page_start=start,
        page_end=end,
        review_note=review_note,
        stage=stage,
    )


def norm_key_for(name: str) -> str:
    """`norm_key` 的去重口径（`database-design.md` 的 `norm_key` 列行）：全角转半角＋空白折叠＋小写。

    口径属分析模块语义，故在这里算好交给数据访问层（`KpNodeRow` 收的是成品值）。`ix_af_kp_norm`
    的查重（W4）与同父重名判定都只认这一个算法。
    """
    folded = unicodedata.normalize("NFKC", name or "")
    return " ".join(folded.split()).casefold()


# --------------------------------------------------------------------------- 取证集（KA-2）


@dataclass(frozen=True, slots=True)
class EvidencePage:
    """一页的取证读数：标题序列（目录阶段）＋页正文（知识点阶段）＋页域角色＋本页的家具读数。

    `markdown` 是**产物原文**（与磁盘逐字同形，对照视图与 `KA-19` 的复核都按它判）；`furniture` 是
    本页该剔的家具文本（归一口径）。剔除只在送模型的副本上发生，取证面保留原文就是为了两者可对偶。
    """

    page_no: int
    section_kind: str
    headings: tuple[HeadingEvidence, ...]
    markdown: str
    furniture: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OutlineReading:
    """目录阶段的一条读数（`KA-35`）：三条通路共用的形状，只够成行、不带正文。

    `page_no` 对条目是回定位到的正文页（未定位时落回目录页自身），`block_index` 是那一页
    `blocks.json` 的数组下标（`KA-18`）；`level` 由模型整理行给出（回落腿取条目行形状，heading 腿
    取 `Block.level`）。`model_ref` 与 `role`／`kind` 都是**逐行**事实：一页整理失败回落时只有那一页
    的行改记 `rule:toc`，附件行也只有自己那几行带 `reference`——整批一个值就分不出「哪几行是回落」。

    `entry_index` 是这条读数在 `entries` 序列里的下标（`KA-40` 的身份通道）：逐页早落的行与定稿重算
    的行靠它对上号，才谈得上「改写已在册那一行」而不是「再造一行」。附件行与 heading 行不属于条目
    序列，因此留 `None`——它们本来就只出现在末批，没有要复用的身份。
    """

    page_no: int
    block_index: int
    level: int
    text: str
    model_ref: str = OUTLINE_MODEL_REF_TOC
    role: str = TOC_ROLE_KNOWLEDGE
    kind: str = ""
    entry_index: int | None = None


@dataclass(frozen=True, slots=True)
class EvidenceSet:
    """一次批次的取证面：可用的页读数与两类**计数可见**的缺口（`KA-2`「缺页不建半批」）。

    `blocks` ＝回放完人工改判的页块序列（`KA-14`/`KA-29`），目录阶段的三条通路都在它上面算：
    `KA-37` 的判页样本、`KA-33` 的回落条目腿、`KA-38` 的定锚与候选行。取证阶段不预跑条目腿——
    先跑就是给每条通路都付一遍整本扫描的钱，而模型判页成功那一轮根本不会读那份读数。

    条目腿的两枚披露（未定位条目名、页级复核剔掉的页号）随这条腿一起挪进目录阶段：它们是
    「这一轮目录取材的读数」而不是取证事实，留在本结构上就是拿回落读数冒充取证读数。
    """

    book_id: str
    job_id: str
    page_start: int
    page_end: int
    pages: tuple[EvidencePage, ...]
    missing_pages: tuple[int, ...]
    unparsed_pages: tuple[int, ...]
    blocks: tuple[BlocksPage, ...] = ()


def _heading_evidence(
    blocks_page: BlocksPage, furniture: Sequence[str]
) -> tuple[HeadingEvidence, ...]:
    """只取 heading 块：层级来自 `Block.level`，块序来自该版 `blocks.json` 的数组下标（`KA-18`）。

    进来的页必须已经回放完人工改判（`intents.apply_relabels`，在 `build_evidence_set` 里做）：
    「把页眉判成 `header` 就不该再成为目录行」由类型维判掉；引擎把章名眉判成 `title` 的那一类
    （b05 的实际形状）由线维判掉——`furniture` 就是两条腿合取后的本页读数。比对走 `norm_key_for`，
    与 `normalize.furniture._fold` 同一条算法。

    只回放判别维，`markdown` 仍是产物原文，取证面不改写内容。
    """
    flagged = set(furniture)
    return tuple(
        HeadingEvidence(
            level=int(block.level or 1),
            text=(block.text or "").strip(),
            block_index=block.index,
        )
        for block in blocks_page.blocks
        if block.type is BlockType.HEADING and norm_key_for(block.text or "") not in flagged
    )


def _toc_pages(pages: Sequence[BlocksPage]) -> tuple[tuple[int, TocEntry], ...]:
    """整本页块序列 → `(目录页号, 条目)` 平铺读数（`KA-33` 条目腿在编排侧的唯一入口）。

    拆分与目录页判据都在 `normalize.toc`，这里只逐页扫一遍并把页号带上——偏移必须按**整本**标定，
    逐页各定位一次就把每条重复的条目名看成孤例（与家具判线同一个理由）。扫描与家具腿同趟进行，
    页块序列在第一趟已经装载完，本腿不额外读盘。
    """
    return tuple((page.page, entry) for page in pages for entry in toc_entries(page))


def build_evidence_set(
    session: Session, *, data_root: Path | str, acceptance: KpAcceptance
) -> EvidenceSet:
    """取证页集（`KA-2`）＝该取证版本内 `status='parsed'` 的页 ∩ 请求页区间 ∩ 页产物成对在位。

    三道门槛各守一件事：`parsed` 是 DB 侧就绪唯一源（ADR 0015／`PP-13`）；`.md` 与
    `.blocks.json` **成对**在位才可用——只有一件就是「半批」，目录阶段与知识点阶段会读到组不起来
    的证据；页区间把单章批次关在人工划定的范围内（`KA-25`）。

    产物缺位说明磁盘被外部清退（重建窗口或人工清理），本函数按缺页处理且**如实计数**，
    **不改判 DB 状态**。`chunks.json` 不在通路上：它是检索单元（`PP-19`），与知识点只共享坐标空间。

    家具读数（页眉页脚页码）按整本派生：线维的第二个条件是「页数」，逐页各判一次会把每条重复线
    看成孤例，所以装载与派生分两趟——第一趟只读盘＋回放，第二趟一次性判线再逐页出读数。

    第三趟撤掉了（`KA-35` 改判）：页块序列整本带出（`blocks`），目录取材的三条腿都在 `build_outline`
    里按那一轮的实际通路跑。取证面因此与目录读数的来源脱钩——判页成功的那轮不会留下任何回落读数。
    """
    book = BookRepository(session).get(acceptance.book_id)
    if book is None:
        raise KpGateError(404, f"书目不存在：{acceptance.book_id}")
    pages_dir = (
        version_dir_for(
            data_root,
            book.domain_id,
            book.course_id,
            acceptance.book_id,
            acceptance.evidence_job_id,
        )
        / "pages"
    )
    rows = {
        row.page_no: row
        for row in PageRepository(session).list_for_book(
            acceptance.book_id, acceptance.evidence_job_id
        )
    }
    # 改判行整本一次取回（逐页一查＝N 次 SQL）：取证集要的是「人工判过什么」，与页产物同批读。
    edits_by_page: dict[int, list[EditRow]] = {}
    for edit in OperationRepository(session).list_edits_for_book(
        acceptance.book_id, acceptance.evidence_job_id
    ):
        edits_by_page.setdefault(edit.page_no, []).append(edit)
    relabels_by_page = {
        page_no: relabels_by_block(page_edits) for page_no, page_edits in edits_by_page.items()
    }

    usable: list[EvidencePage] = []
    missing: list[int] = []
    unparsed: list[int] = []
    loaded: list[tuple[BlocksPage, str, str]] = []
    for page_no in range(acceptance.page_start, acceptance.page_end + 1):
        row = rows.get(page_no)
        if row is None or row.status != "parsed":
            unparsed.append(page_no)
            continue
        blocks_path = pages_dir / f"p{page_no:04d}.blocks.json"
        markdown_path = pages_dir / f"p{page_no:04d}.md"
        if not (blocks_path.is_file() and markdown_path.is_file()):
            missing.append(page_no)
            continue
        try:
            blocks_page = BlocksPage.model_validate_json(blocks_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            logger.warning("页产物不可读，按缺页处理：%s", blocks_path)
            missing.append(page_no)
            continue
        # 页号以文件名为准（锚点与改判都按它解释），块序列按本页的改判行回放。
        loaded.append(
            (
                blocks_page.model_copy(
                    update={
                        "page": page_no,
                        "blocks": apply_relabels(
                            blocks_page.blocks, relabels_by_page.get(page_no, {})
                        ),
                    }
                ),
                row.section_kind or "",
                markdown,
            )
        )

    replayed = [blocks_page for blocks_page, _, _ in loaded]
    furniture_by_page = furniture_texts(replayed, hints=furniture_hints(replayed))

    for blocks_page, section_kind, markdown in loaded:
        furniture = furniture_by_page.get(blocks_page.page, ())
        usable.append(
            EvidencePage(
                page_no=blocks_page.page,
                section_kind=section_kind,
                headings=_heading_evidence(blocks_page, furniture),
                markdown=markdown,
                furniture=furniture,
            )
        )

    return EvidenceSet(
        book_id=acceptance.book_id,
        job_id=acceptance.evidence_job_id,
        page_start=acceptance.page_start,
        page_end=acceptance.page_end,
        pages=tuple(usable),
        missing_pages=tuple(missing),
        unparsed_pages=tuple(unparsed),
        blocks=tuple(replayed),
    )


# --------------------------------------------------------------------------- 目录阶段（KA-5/KA-6）


@dataclass(frozen=True, slots=True)
class OutlineNode:
    """一条已定形的目录行输入：`row` 交给机器腿插入，`page` 供知识点阶段切页区间。"""

    row: KpNodeRow
    page: int


@dataclass(frozen=True, slots=True)
class OutlineBuild:
    """目录阶段的产出与全部披露面（`KA-35`／`KA-37`／`KA-38`／`KA-39`）。

    前三枚是既有账面：节点行、第三层丢弃、不合形状丢弃。后面六枚都是「这一轮目录取材走成了什么样」
    的读数——没有它们，回落与核对就只存在于服务日志里，人工既补不了页也圈不出重跑范围：
    `unlocated`（未定位条目名）、`dropped_toc_pages`（页级复核剔掉的页）、`toc_pages`（判出的目录页）、
    `fallback_pages`（整理失败而回落条目腿的页）、`verified`（模型核对救回来的条目名）、
    `reference_rows`（附件行枚数，`KA-39`）。
    """

    nodes: tuple[OutlineNode, ...]
    dropped_depth: int
    dropped_malformed: int
    unlocated: tuple[str, ...] = ()
    dropped_toc_pages: tuple[int, ...] = ()
    toc_pages: tuple[int, ...] = ()
    fallback_pages: tuple[int, ...] = ()
    verified: tuple[str, ...] = ()
    reference_rows: int = 0


@runtime_checkable
class OutlineSink(Protocol):
    """目录阶段的落库通道（`KA-40`）：把「边整理边落行」与「定稿改写一次」两刀交给调用方执行。

    只有两枚方法，形状本身不规定怎么落——`build_outline` 只管「谁该在哪一批里」，事务边界、
    列域与 `WHERE` 都在实现方（`_KpOutlineSink` 走仓储的两条机器腿）。传 `None` 就是既有形状：
    目录阶段照旧只在末尾产出，本文件其余用例逐字守着那条通路。
    """

    def land(self, nodes: Sequence[OutlineNode]) -> None:
        """一页整理完就交一批：批内是那一页新成的行（空批不会交来）。"""
        ...

    def repoint(self, nodes: Sequence[OutlineNode]) -> None:
        """定稿交一次：只包含读数真的变了的、已在册的那批行。"""
        ...


@dataclass(slots=True)
class _OutlineAssembler:
    """读数序列 → `af_kp` 行的那一步，两条通路共用（`KA-40`）。

    共用不是为了省代码，是为了**不许两条通路各定一次行**：逐页早落与定稿重算必须给出一模一样的
    名字、层级、`norm_key`、`model_ref` 与 `meta`——同批改写面只有锚、序、挂父三列（`KA-40`），
    任何别的不一致都会永久留在行里。`reuse` 是身份通道：`entry_index` 对上的行沿用已在册的
    `node_id`，对不上就发新号（`KA-11` 的「机器不与人工争身份」在这里只剩「自己这两批不互相造行」）。
    """

    book_id: str
    job_id: str
    gen_batch: str
    reuse: dict[int, str] = field(default_factory=dict)
    nodes: list[OutlineNode] = field(default_factory=list)
    chapter_id: str | None = None
    dropped_depth: int = 0
    dropped_malformed: int = 0

    def add(self, reading: OutlineReading) -> OutlineNode | None:
        """成一行；不合形状的行只计数不进节点（`KA-6`）。越界与空名在两趟重算里同判，故身份不漂。"""
        name = reading.text.strip()
        depth = reading.level
        if not name:
            self.dropped_malformed += 1
            return None
        if depth > OUTLINE_MAX_DEPTH:
            self.dropped_depth += 1
            return None
        if reading.entry_index is None:
            node_id = new_node_id()
        else:
            node_id = self.reuse.get(reading.entry_index) or new_node_id()
            self.reuse[reading.entry_index] = node_id
        if depth == 1:
            self.chapter_id = node_id
            parent_id: str | None = None
        else:
            parent_id = self.chapter_id
        meta: dict[str, Any] = {"toc_role": reading.role}
        if reading.kind:
            meta["toc_kind"] = reading.kind
        node = OutlineNode(
            page=reading.page_no,
            row=KpNodeRow(
                node_id=node_id,
                book_id=self.book_id,
                job_id=self.job_id,
                node_kind=NODE_KIND_OUTLINE,
                parent_id=parent_id,
                depth=depth,
                order_hint=len(self.nodes) + 1,
                name=name[:500],
                norm_key=norm_key_for(name)[:200],
                role="",
                type="",
                summary="",
                links=None,
                anchors=[_page_anchor(reading.page_no, reading.block_index, name)],
                gen_status="draft",
                gen_batch=self.gen_batch,
                model_ref=reading.model_ref,
                meta=meta,
            ),
        )
        self.nodes.append(node)
        return node


def _pick_toc_pages(engine: KnowledgeEngine, blocks: Sequence[BlocksPage]) -> TocPagePick | None:
    """判页腿（`KA-37`）：样本＝前 `TOC_PAGE_SCAN_LIMIT` 页与末同数页；拿不到读数就返回 `None`。

    `None` 是「本腿没给出可用地」而不是「这本书没有目录」——三条来路都折成它：引擎没有这条腿
    （协议内的其他实现、单测里的替身）、调用抛 `EngineError`、返回的目录页集为空。三种都由调用方
    回落 `KA-33` 条目腿，目录零行才是真的不可能发生。
    """
    invoke = getattr(engine, "toc_pages", None)
    if not callable(invoke) or not blocks:
        return None
    tail = TOC_PAGE_SCAN_LIMIT
    picked = list(blocks[:tail])
    if len(blocks) > tail:
        picked.extend(blocks[-tail:])
    samples = tuple(
        PageSample(page_no=page.page, text=page_sample_text(page))
        for page in {page.page: page for page in picked}.values()
    )
    try:
        return invoke(samples)
    except EngineError as exc:
        logger.warning("目录判页腿失败，全书回落条目腿（KA-37）：%s", exc)
        return None


def _toc_page_text(page: BlocksPage) -> str:
    """整理腿的输入＝该页段落块原文（`KA-37`）：逐块成行、不截断——截断会把一条条目名切成两半。"""
    return "\n".join(
        (block.text or "").strip()
        for block in page.blocks
        if block.type is BlockType.PARAGRAPH and (block.text or "").strip()
    )


def _candidate_lines(pages_by_no: dict[int, BlocksPage], target_page: int) -> tuple[tuple[int, str], ...]:
    """核对腿的候选行（`KA-38`）：偏移页 ±`TOC_VERIFY_PAGE_SLACK` 的页里，每块取首个非空行。

    只取首行是把「比对的面积」交还给判据：一条条目在正文里就是那一行的起头，整页正文喂进去既超
    本机吞吐，也让模型在无关段落里挑花眼。
    """
    lines: list[tuple[int, str]] = []
    for page_no in range(target_page - TOC_VERIFY_PAGE_SLACK, target_page + TOC_VERIFY_PAGE_SLACK + 1):
        page = pages_by_no.get(page_no)
        if page is None:
            continue
        for block in page.blocks:
            for line in (block.text or "").splitlines():
                if line.strip():
                    lines.append((page_no, line.strip()[:120]))
                    break
    return tuple(lines)


def _entries_from_pages(
    engine: KnowledgeEngine,
    pages: Sequence[BlocksPage],
    *,
    on_page: Callable[[list[tuple[int, TocEntry, str]], int], None] | None = None,
) -> tuple[list[tuple[int, TocEntry, str]], list[int]]:
    """逐页整理（`KA-37`）：一页一次调用，坏一页只回落那一页。

    返回的 `model_ref` 是**逐条目**的：模型出的行记 `llm:toc`，该页整理失败后由条目腿补出的行记
    `rule:toc`，两枚值在同一次批次里并存是设计内读数（账面要分得出哪几行不是模型抄的）。

    `on_page(entries, start)` 只在**模型整理成功**的那一页、其条目已经接进 `entries` 之后发出，
    `entries[start:]` 恰是这一页新到的条目（`KA-40` 的逐页落行时刻）。回落页不发：`KA-33` 的页级复核
    要靠全书偏移才能剔掉符号表页，回落行先落再删就等于造出一批没人认领的行。零条目也不发：
    一批一提交，提交得给真行。
    """
    entries: list[tuple[int, TocEntry, str]] = []
    fallback: list[int] = []
    for page in pages:
        invoke = getattr(engine, "toc_entries", None)
        rows: list[Any] | None = None
        if callable(invoke):
            try:
                rows = invoke(page.page, _toc_page_text(page))
            except EngineError as exc:
                logger.warning("目录整理腿失败，该页回落条目腿（KA-37）：p%s %s", page.page, exc)
                rows = None
        if rows is None:
            fallback.append(page.page)
            entries.extend((page.page, entry, OUTLINE_MODEL_REF_TOC) for entry in toc_entries(page))
            continue
        start = len(entries)
        for row in rows:
            entries.extend(_cleaned_model_entries(page, row))
        if on_page is not None and len(entries) > start:
            on_page(entries, start)
    return entries, fallback


def _cleaned_model_entries(page: BlocksPage, row: Any) -> list[tuple[int, TocEntry, str]]:
    """整理腿的一行 → 若干条目：名字过 `KA-41` 的那一把清洗，清洗后才派生 `norm_key`、才是屏上那份。

    模型交来的行有两种脏法，都让 `locate_entries` 的前缀命中永不成立（真机 b07 的 31 行因此全锚在
    书自身目录页上）＝页码留在名字里（`…… 150`）、或页码进了自己那格而引导点留在名字末尾（`……`）。
    一枚黏行还会拆成两条：拆出的第二段不是模型报的那一行，层级只能由它自己的形状给（`entry_level`），
    印刷页码也只能取它自己行末回收出的那一枚——模型给的那一格是第一段的。
    """
    cleaned = clean_entry_rows([str(row.name)])
    readings: list[tuple[int, TocEntry, str]] = []
    for index, (name, recovered) in enumerate(cleaned):
        if not name:
            continue
        printed = row.printed_page if index == 0 and row.printed_page is not None else recovered
        readings.append(
            (
                page.page,
                TocEntry(
                    name=name,
                    printed_page=printed,
                    level=int(row.level) if index == 0 else entry_level(name),
                    block_index=anchor_block_for_name(page, name),
                ),
                OUTLINE_MODEL_REF_LLM,
            )
        )
    return readings


def _whole_book_entries(blocks: Sequence[BlocksPage]) -> list[tuple[int, TocEntry, str]]:
    """全书回落条目腿（`KA-33`）：整本扫一遍目录页条目，页号带上是因为正文要排除它们。"""
    return [(page_no, entry, OUTLINE_MODEL_REF_TOC) for page_no, entry in _toc_pages(blocks)]


def _reference_readings(pick: TocPagePick | None, pages_by_no: dict[int, BlocksPage]) -> list[OutlineReading]:
    """附件页成行（`KA-39`）：判页腿认出的正文前后附件页各成一枚顶级行，身份位标 `reference`。

    页不在取证集里（未解析或产物缺位）就不成行——机器不为读不到的页造结构结论（`KA-2` 同一纪律）。
    `kind` 取模型给的类别，域外值折成按名字判出来的那一类；两条都给不出时留空串（有身份位没类别，
    屏上仍标得出「非知识点」）。
    """
    if pick is None:
        return []
    readings: list[OutlineReading] = []
    for reference in pick.reference_pages:
        page = pages_by_no.get(reference.page_no)
        if page is None:
            continue
        kind = reference.kind or reference_role_for_name(reference.name)[1]
        readings.append(
            OutlineReading(
                page_no=reference.page_no,
                block_index=anchor_block_for_name(page, reference.name),
                level=1,
                text=reference.name,
                model_ref=OUTLINE_MODEL_REF_LLM,
                role=TOC_ROLE_REFERENCE,
                kind=kind,
            )
        )
    return readings


def _verify_outline_entries(
    engine: KnowledgeEngine,
    entries: Sequence[tuple[int, TocEntry, str]],
    located_by_index: dict[int, LocatedEntry],
    offset: int | None,
    body_pages: Sequence[BlocksPage],
) -> dict[int, tuple[int, int]]:
    """核对腿（`KA-38`）：偏移定锚查不中的坏行攒一批一次问，只改「落在哪一页」。

    三条零调用：偏移标定不出（那时「全部未定位」是标定失败的读数，问模型等于让模型替机器编页码）、
    没有坏行、引擎不持这条腿。坏行里只取**带印刷页码**的那些——没有页码就没有目标页，候选面只能是全库。
    核对判出的页号必须落在本条的候选页里，域外页号整条不认：下游会把锚指到没读过正文的页上，
    那是假锚。命中行的块序由 `anchor_block_for_name` 在那一页里指认；名字与层级一律照旧。
    """
    invoke = getattr(engine, "toc_verify", None)
    if offset is None or not callable(invoke):
        return {}
    pages_by_no = {page.page: page for page in body_pages}
    pending = [
        (index, entry, printed)
        for index, (_, entry, _) in enumerate(entries)
        if (printed := entry.printed_page) is not None and not located_by_index[index].located
    ]
    if not pending:
        return {}
    items = [
        TocVerifyItem(
            name=entry.name,
            printed_page=printed,
            candidates=_candidate_lines(pages_by_no, printed + offset),
        )
        for _, entry, printed in pending
    ]
    try:
        answers = invoke(items)
    except EngineError as exc:
        logger.warning("目录核对腿失败，坏行按未定位披露（KA-38）：%s", exc)
        return {}

    pools: dict[str, list[int | None]] = {}
    for answer in answers:
        pools.setdefault(str(answer.name), []).append(answer.page_no)

    anchors: dict[int, tuple[int, int]] = {}
    for (index, _, _), item in zip(pending, items, strict=True):
        pool = pools.get(item.name)
        if not pool:
            continue
        page_no = pool.pop(0)
        if page_no is None:
            continue
        if page_no not in {candidate_page for candidate_page, _ in item.candidates}:
            logger.warning("目录核对给出的页号不在候选页里，整条不认（KA-38）：%s → p%s", item.name, page_no)
            continue
        anchors[index] = (page_no, anchor_block_for_name(pages_by_no[page_no], item.name))
    return anchors


def build_outline(
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    engine: KnowledgeEngine,
    *,
    sink: OutlineSink | None = None,
) -> OutlineBuild:
    """目录阶段（`KA-5`/`KA-35`）：判页 → 逐页整理 → 印刷页码定锚 → 坏行攒批核对 → 成行。

    正本优先级三条，都保证「目录零行」不会发生：模型整理出的条目为正本；单页整理失败只有那一页
    回落 `KA-33` 条目腿；判页不成（或全书零条目）时整本退条目腿，仍零条目才退 `heading` 块。
    层级＝整理行给的层级（回落腿取条目行形状，heading 取 `Block.level`），越界计入 `dropped_depth`
    不进节点（`KA-6`）；父只取排在它前面最近的一条 `depth=1`，前面没有章就不造章（`KA-26` 同轴）；
    `summary` 恒空串：结构性事实机器给、描述性文字交人工「一键总结」。

    定锚只认「偏移标定后的窗口内命中」（`KA-38`，判据正本在 `normalize.toc.locate_entries`）：印刷页码
    永不进锚点。查不中的行先交核对腿救一次，只改「落在哪一页」，不改名字与层级；核对也救不回来的
    按未定位披露——锚照样可用，只是指向目录页那一行自己。身份位同样两路（`KA-39`）：正腿的行由判页
    读数给（附件页另成顶级行），回落腿的行只能按名字集合判。

    `sink` 给的是「边整理边落库」那一条通路（`KA-40`）：每页整理完就按**此刻已收到的条目**标定偏移、
    定锚、成行并交 `land`，落库序就是发出序；页级复核与核对腿都在全书读数上发生，所以定稿还要按既有
    通路整本重算一遍，两次读数不同的行交 `repoint` 改写（同批改写只有锚、序、挂父三列，名字与层级
    在两趟里由同一把装配器给出）。回落页、附件行、heading 行不进逐页落行面——它们的位置判据要全书
    读数才定得下来，先落再删就是造出一批没人认领的行。`sink=None` 时本段一行不发，产出与判据与既有
    通路逐字相同。
    """
    pages_by_no = {page.page: page for page in evidence.blocks}
    pick = _pick_toc_pages(engine, evidence.blocks)
    toc_pages: tuple[int, ...] = ()
    fallback_pages: list[int] = []
    entries: list[tuple[int, TocEntry, str]] = []
    body_pages: list[BlocksPage] = list(evidence.blocks)
    early = _OutlineAssembler(
        book_id=acceptance.book_id, job_id=evidence.job_id, gen_batch=acceptance.gen_batch
    )

    def land_page(current: list[tuple[int, TocEntry, str]], start: int) -> None:
        if sink is None or len(current) == start:
            return
        location = locate_entries([entry for _, entry, _ in current], body_pages)
        batch: list[OutlineNode] = []
        for index in range(start, len(current)):
            toc_page, entry, model_ref = current[index]
            item = location.entries[index]
            located = item.located and item.page_no is not None
            node = early.add(
                OutlineReading(
                    page_no=item.page_no if located else toc_page,
                    block_index=item.block_index,
                    level=entry.level,
                    text=entry.name,
                    model_ref=model_ref,
                    entry_index=index,
                )
            )
            if node is not None:
                batch.append(node)
        if batch:
            sink.land(batch)

    if pick is not None and pick.toc_pages:
        toc_pages = tuple(pick.toc_pages)
        body_pages = [page for page in evidence.blocks if page.page not in set(toc_pages)]
        entries, fallback_pages = _entries_from_pages(
            engine,
            [pages_by_no[page_no] for page_no in pick.toc_pages if page_no in pages_by_no],
            on_page=None if sink is None else land_page,
        )
        fallback_pages.extend(page_no for page_no in pick.toc_pages if page_no not in pages_by_no)
    if not entries:
        entries = _whole_book_entries(evidence.blocks)
        toc_pages = tuple(sorted({page_no for page_no, _, _ in entries}))
        body_pages = [page for page in evidence.blocks if page.page not in set(toc_pages)]

    location = locate_entries([entry for _, entry, _ in entries], body_pages)
    # 页级复核（`KA-33`）只作用于条目腿产出的那些页：模型认下的目录页不靠「命中率」反证，
    # 而条目腿的符号表页正是靠这一条整页剔掉的。
    # 复核后剩下的条目这里用不上：整页剔掉的页按页号丢行，剩下的行仍走同一条定锚通路。
    _, dropped_toc_pages = drop_unlocated_toc_pages(
        [
            (page_no, item)
            for (page_no, _, model_ref), item in zip(entries, location.entries, strict=True)
            if model_ref == OUTLINE_MODEL_REF_TOC
        ],
        location.offset,
    )
    dropped = set(dropped_toc_pages)

    located_by_index = {index: item for index, item in enumerate(location.entries)}
    anchors = _verify_outline_entries(engine, entries, located_by_index, location.offset, body_pages)
    readings: list[OutlineReading] = []
    unlocated: list[str] = []
    verified: list[str] = []
    for index, (toc_page, entry, model_ref) in enumerate(entries):
        if toc_page in dropped:
            continue
        item = located_by_index[index]
        anchor = anchors.get(index)
        if anchor is not None:
            page_no, block_index = anchor
            verified.append(entry.name)
        elif item.located and item.page_no is not None:
            page_no, block_index = item.page_no, item.block_index
        else:
            page_no, block_index = toc_page, item.block_index
            unlocated.append(entry.name)
        # 身份位的来源分两路（KA-39）：正腿的行由模型判页给（附件页另成行），回落腿只能按名字集合判。
        if model_ref == OUTLINE_MODEL_REF_TOC:
            role, kind = reference_role_for_name(entry.name)
        else:
            role, kind = TOC_ROLE_KNOWLEDGE, ""
        readings.append(
            OutlineReading(
                page_no=page_no,
                block_index=block_index,
                level=entry.level,
                text=entry.name,
                model_ref=model_ref,
                role=role,
                kind=kind,
                entry_index=index,
            )
        )

    reference_readings = _reference_readings(pick, pages_by_no)
    if readings or reference_readings:
        # 排在所有知识点行之后的才算正文后附件页，其余一律进章序之前——符号表在正文前但在目录页之后。
        knowledge_pages = [reading.page_no for reading in readings]
        tail_from = max(knowledge_pages) if knowledge_pages else 0
        front = [reading for reading in reference_readings if reading.page_no <= tail_from]
        back = [reading for reading in reference_readings if reading.page_no > tail_from]
        readings = [*front, *readings, *back]
    else:
        readings = [
            OutlineReading(
                page_no=page.page_no,
                block_index=heading.block_index,
                level=heading.level,
                text=heading.text,
                model_ref=OUTLINE_MODEL_REF_HEADING,
            )
            for page in evidence.pages
            for heading in page.headings
        ]

    # 定稿那把装配器读的是逐页那把的身份表（`KA-40`）：对得上号的行沿用已在册的 `node_id`，
    # 对不上的（回落行、附件行、heading 行）发新号——「改写已在册那一行」与「再造一行」的分别是这一句给的。
    final = _OutlineAssembler(
        book_id=acceptance.book_id,
        job_id=evidence.job_id,
        gen_batch=acceptance.gen_batch,
        reuse=early.reuse,
    )
    for reading in readings:
        final.add(reading)
    nodes = final.nodes

    if sink is not None and early.nodes:
        landed_by_id = {node.row.node_id: node.row for node in early.nodes}
        changed = [
            node
            for node in nodes
            if (previous := landed_by_id.get(node.row.node_id)) is not None
            and (node.row.anchors, node.row.order_hint, node.row.parent_id)
            != (previous.anchors, previous.order_hint, previous.parent_id)
        ]
        if changed:
            sink.repoint(changed)

    return OutlineBuild(
        nodes=tuple(nodes),
        dropped_depth=final.dropped_depth,
        dropped_malformed=final.dropped_malformed,
        unlocated=tuple(unlocated),
        dropped_toc_pages=dropped_toc_pages,
        toc_pages=toc_pages,
        fallback_pages=tuple(fallback_pages),
        verified=tuple(verified),
        reference_rows=sum(1 for node in nodes if (node.row.meta or {}).get("toc_role") == TOC_ROLE_REFERENCE),
    )


def _page_anchor(page_no: int, block_index: int | None, snippet: str) -> dict[str, Any]:
    """机器锚点的唯一构造式：页序＝PDF 页序，块序＝取证版本 `blocks.json` 的下标（`KA-18`）。"""
    return {
        "kind": KP_ANCHOR_KIND,
        "ref_id": f"p{page_no:04d}",
        "page": int(page_no),
        "block_indexes": [] if block_index is None else [int(block_index)],
        "snippet": (snippet or "")[:200],
    }


# --------------------------------------------------------------------------- 知识点阶段（KA-5/KA-7/KA-26）


@dataclass(frozen=True, slots=True)
class PointBuild:
    """点阶段的产出计数：`unarchored` 是未归档池的入账（`KA-7`），不是缺陷计数。

    `failed_section_details` 与 `failed_sections` 同批长出来（DEFECT-026 甲）：只有计数时，读侧要
    回答「哪一节被吃掉」只能翻服务日志，而日志是会滚走的——账面才是能指认的那一处。
    """

    point_rows: int
    unarchored: int
    dropped_nodes: int
    failed_sections: int
    failed_section_details: tuple[dict[str, Any], ...] = ()
    #: 写上节级总结的在册目录行数（`KA-43`）：只数真的写成的那几行，人工已写过或已背书的不算。
    outline_summary_rows: int = 0
    #: 因锚仍在目录页而整节跳过的目录行名（`KA-43`）：内容批次不重跑目录，没有核对腿可救，
    #: 拿目录页当正文抽点只会把目录行抄成知识点——跳过并在账面点名，人工据此重挂锚或重跑目录。
    skipped_unlocated: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """一个节的页区间：`parent_id` 为空表示这段正文不在目录树上，抽出的点直接入池。"""

    parent_id: str | None
    title: str
    page_start: int
    page_end: int


@dataclass(frozen=True, slots=True)
class SpanRow:
    """切区间的一枚输入行：排序键 `(order_hint, page)` ＋ 身份 `(node_id, name)` ＋ 树位 `(depth, toc_role)`。

    点腿给的是本批定形的目录行，读面给的是库里的在册目录行——两处形状不同，算式必须同一处
    （`KA-42`）：屏上圈的区间要与模型实际读过的那一段逐字对得上。树位只参与终点判（DEFECT-028），
    缺省 `depth=1`／`toc_role=knowledge` 即「平铺目录」，与改判前的读数逐字相同。
    """

    node_id: str | None
    name: str
    order_hint: int | None
    page: int
    depth: int = 1
    toc_role: str = "knowledge"


def section_spans_of(
    ordered_pages: Sequence[SpanRow], *, first_page: int, last_page: int
) -> list[SectionSpan]:
    """按目录行的树序切区间：每行管「它自己那页起、到下一条同级或更浅行之前」（DEFECT-028）。

    终点取**树序**而不是「下一条行」：章与其首节同页，按后者算章只拿到一页（b05 第1章 p9 与「导引」
    同页 ⇒ `[9,9]`），而需求方要的是 p9–28（第2章在 p29）。同级或更浅的下一行才是这一棵子树的边界。
    两条目录行挤在同一页时后一条的区间为空，仍按它自己那页取——宁可两节共读一页，也不让正文没有
    归属（那会往 `KA-7` 的池子里混进本该挂父的点）。`toc_role='reference'` 的行（符号表、索引、书目）
    只发起点＝终点：它们的物理正文不在目录条目所在页，按树序拼出去只会造出跨章巨区间（「重要符号表」
    曾读成 `5-310`），不精确处如实披露不猜。目录零产出（引擎认不出任何标题）时整段正文自成一段：
    正文不因为目录空了就消失，抽出的点全入池。起点页超出 `last_page` 的行不参与切分（夹不出区间），
    读面因此发空键而不是编一个假终点。
    """
    ordered = sorted(ordered_pages, key=lambda row: (row.order_hint or 0, row.page))
    if not ordered:
        return [SectionSpan(None, "", first_page, last_page)]
    spans: list[SectionSpan] = []
    if ordered[0].page > first_page:
        spans.append(SectionSpan(None, "", first_page, ordered[0].page - 1))
    tail_is_reference = False
    for index, row in enumerate(ordered):
        if row.page > last_page:
            continue
        if row.toc_role == TOC_ROLE_REFERENCE:
            spans.append(SectionSpan(row.node_id, row.name, row.page, min(row.page, last_page)))
            tail_is_reference = True
            continue
        end_page = last_page + 1
        for later in ordered[index + 1 :]:
            if later.depth <= row.depth:
                end_page = later.page
                break
        end = max(row.page, min(end_page - 1, last_page))
        spans.append(SectionSpan(row.node_id, row.name, row.page, end))
        tail_is_reference = False
    covered_end = max((span.page_end for span in spans), default=0)
    # 末行是附件行时，它后面的页子就是附件自己的正文（书目、版图、跋）：不为读不到的书尾再发一次问。
    if covered_end < last_page and not tail_is_reference:
        spans.append(SectionSpan(None, "", covered_end + 1, last_page))
    return spans


def _first_anchor_page(row: AfKp) -> int | None:
    """一行锚点里最小的有效页号；没有可解释的页即 `None`（与读面 `_anchor_pages` 同一判据）。"""
    pages = [
        page
        for page in (
            int(anchor.get("page") or 0) for anchor in (row.anchors or []) if isinstance(anchor, dict)
        )
        if page > 0
    ]
    return min(pages) if pages else None


def _registered_outline_rows(
    session: Session, book_id: str, *, page_start: int = 1, page_end: int | None = None
) -> list[AfKp]:
    """在册目录行（`KA-43`）：`gen_status='candidate'` 且锚页落在给区间内的那些行，按库序返回。

    「在册」不含 `draft`（在飞读数）与 `rejected`（已被取代的旧读数）：两者都是某一批的半成品，
    拿它们切区间会把中间那一段凭空挖掉。页区间过滤的判据是**本行起点页落在区间内**——区间是一段
    重写范围，不跨边界去替上一节重读它的后半段；被滤掉的节由它自己的下次批次负责。
    """
    rows = KpRepository(session).list_for_book(book_id, node_kind=NODE_KIND_OUTLINE)
    kept: list[AfKp] = []
    for row in rows:
        if row.gen_status != "candidate":
            continue
        page = _first_anchor_page(row)
        if page is None or page < page_start:
            continue
        if page_end is not None and page > page_end:
            continue
        kept.append(row)
    return kept


def _outline_nodes_from_book(rows: Sequence[AfKp]) -> list[OutlineNode]:
    """在册行 → 点腿认的目录节点形状（`KA-43`）：只搬取材与挂父要用的列，`page` 取本行起点页。

    走 `KpNodeRow` 而不是直接递 `AfKp`：`OutlineNode` 的契约是「已定形的目录行输入」，把 ORM 活对象
    递进去等于让点腿顺手读得到任何一列，也把会话生命周期漏进纯函数。
    """
    nodes: list[OutlineNode] = []
    for row in rows:
        page = _first_anchor_page(row)
        if page is None:
            continue
        nodes.append(
            OutlineNode(
                page=page,
                row=KpNodeRow(
                    node_id=row.node_id,
                    book_id=row.book_id,
                    name=row.name or "",
                    norm_key=row.norm_key or "",
                    node_kind=NODE_KIND_OUTLINE,
                    job_id=row.job_id or "",
                    parent_id=row.parent_id,
                    depth=row.depth,
                    order_hint=row.order_hint,
                    anchors=row.anchors,
                    summary=row.summary or "",
                    gen_status=row.gen_status,
                    gen_batch=row.gen_batch or "",
                    model_ref=row.model_ref or "",
                    meta=row.meta,
                ),
            )
        )
    return nodes


def _section_spans(
    outline_nodes: Sequence[OutlineNode], evidence: EvidenceSet, *, first_page: int | None = None
) -> list[SectionSpan]:
    """点腿的取材切分（`KA-42` 的取材侧）：区间端点取取证集首末页，算式与读面共用 `section_spans_of`。

    `first_page` 只由 `content` 批次显式给（＝最小的在册行起点页）：这一批不重跑目录，目录之前的
    那几页没有行认领，凭空造一段无父区间会把前言正文抽进未归档池。
    """
    if not evidence.pages:
        return []
    return section_spans_of(
        [
            SpanRow(
                node_id=node.row.node_id,
                name=node.row.name,
                order_hint=node.row.order_hint,
                page=node.page,
                depth=node.row.depth or 1,
                toc_role=str((node.row.meta or {}).get("toc_role") or TOC_ROLE_KNOWLEDGE),
            )
            for node in outline_nodes
        ],
        first_page=evidence.pages[0].page_no if first_page is None else first_page,
        last_page=evidence.pages[-1].page_no,
    )


def _page_bodies(evidence: EvidenceSet, span: SectionSpan) -> tuple[PageBody, ...]:
    """节正文＝送进模型的副本：本页的家具行整行剔掉（第二令 R10 的「默认剔除」）。

    剔的是副本，取证面的 `markdown` 与磁盘产物仍是原文（`KA-2`）；判据只有「整行折叠相等」一条，
    正文里引用同一串书名的句子不受影响（`normalize.furniture.strip_furniture`）。
    """
    return tuple(
        PageBody(page.page_no, strip_furniture(page.markdown, page.furniture))
        for page in evidence.pages
        if span.page_start <= page.page_no <= span.page_end
    )


def _resolve_parent(
    draft: dict[str, Any], span: SectionSpan, outline_nodes: Sequence[OutlineNode]
) -> str | None:
    """挂父的合法来源只有两种（`KA-26`）：喂给模型的那一节，或草稿点名且能在**同批目录行**回指到的节。

    草稿点了名却回指不到时不猜、不补、也不退回本节——那等于机器替人确认一个不存在的前提；
    落 `NULL` 进未归档池（`KA-7`）。
    """
    reference = draft.get("parent_ref")
    if reference is None or str(reference).strip() == "":
        return span.parent_id
    wanted = str(reference).strip()
    for node in outline_nodes:
        if node.row.node_id == wanted or node.row.norm_key == norm_key_for(wanted):
            return node.row.node_id
    return None


def _point_row(
    draft: dict[str, Any], acceptance: KpAcceptance, engine_name: str, parent_id: str | None
) -> KpNodeRow | None:
    """形状校验先于写库：缺 `name`／`anchors` 为空／闭集外值 ⇒ 返回 `None`，由调用方计数（不写）。

    锚点页**越界不属于这里**——那是 `KA-19` 的「可见保留」，行照写、标记由 `anchor_review_flags` 给。
    """
    name = str(draft.get("name") or "").strip()
    if not name:
        return None
    raw_anchors = draft.get("anchors") or []
    if not isinstance(raw_anchors, list) or not raw_anchors:
        return None
    anchors: list[dict[str, Any]] = []
    for raw in raw_anchors:
        if not isinstance(raw, dict):
            return None
        page_no = raw.get("page")
        if not isinstance(page_no, int) or page_no < 1:
            return None
        blocks = raw.get("block_indexes")
        indexes = [int(i) for i in blocks if isinstance(i, int)] if isinstance(blocks, list) else []
        anchors.append(
            {
                "kind": KP_ANCHOR_KIND,
                "ref_id": f"p{page_no:04d}",
                "page": page_no,
                "block_indexes": indexes,
                "snippet": str(raw.get("snippet") or "")[:200],
            }
        )
    node_type = str(draft.get("type") or "")
    if node_type not in POINT_TYPES:
        return None
    role = str(draft.get("role") or "")
    if role not in POINT_ROLES:
        return None
    raw_links = draft.get("links") or []
    if not isinstance(raw_links, list):
        return None
    links: list[dict[str, Any]] = []
    for raw in raw_links:
        if not isinstance(raw, dict) or raw.get("kind") not in LINK_KINDS:
            return None
        links.append({"kind": raw["kind"], "to": str(raw.get("to") or ""), "note": str(raw.get("note") or "")})
    return KpNodeRow(
        node_id=new_node_id(),
        book_id=acceptance.book_id,
        job_id=acceptance.evidence_job_id,
        node_kind=NODE_KIND_POINT,
        parent_id=parent_id,
        depth=None,
        order_hint=None,
        name=name[:500],
        norm_key=norm_key_for(name)[:200],
        role=role,
        type=node_type,
        summary=str(draft.get("summary") or ""),
        links=links or None,
        anchors=anchors,
        gen_status="draft",
        gen_batch=acceptance.gen_batch,
        model_ref=engine_name,
    )


def _endpoint_id(session: Session, book_id: str, reference: str, known_ids: set[str]) -> str | None:
    """把草稿里的回指（标题或 `node_id`）换成真实端点身份，回指不到返回 `None`。

    `links[].to` 落库必须是 `node_id` 而不是一个字符串线索：「边的端点存在」（`database-design.md`
    「服务端不变式」）要求读侧拿到的就是身份，8903 与根侧才定位得到那一条行。同名多行时取人已背书的
    那一行——`KA-13` 的「机器不与人工争同一身份」在边上也成立。
    """
    target = reference.strip()
    if not target:
        return None
    if target in known_ids:
        return target
    return session.scalar(
        select(AfKp.node_id)
        .where(
            AfKp.book_id == book_id,
            or_(AfKp.node_id == target, AfKp.norm_key == norm_key_for(target)),
        )
        .order_by(AfKp.confirmed.desc(), AfKp.created_at, AfKp.node_id)
        .limit(1)
    )


def _resolve_links(
    session: Session, book_id: str, row: KpNodeRow, known_ids: set[str]
) -> KpNodeRow | None:
    """整节点的边端点解析：任一条回指不到 ⇒ 返回 `None`，由调用方按「不合形状」计数（不写）。

    回指集合＝本批已落库的行（目录行＋先写出的点行）＋该书既有行；指向尚未落库的前向引用同样判
    「不存在的点」——宁可少产出，也不产出无法复核的行。
    """
    resolved: list[dict[str, Any]] = []
    for link in row.links or []:
        endpoint = _endpoint_id(session, book_id, str(link.get("to") or ""), known_ids)
        if endpoint is None:
            return None
        resolved.append({**link, "to": endpoint})
    return replace(row, links=resolved or None)


def extract_points(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
) -> PointBuild:
    """知识点阶段（`KA-5`）：按节的页区间逐节读正文、逐节落行。

    逐节而不是一次全本：单节抽取失败只让该节不落行、批内继续（设计篇「失败与质量语义」把
    `PP-11` 的窗口失败域延长到节），每节写完即提交——已成功的行不随后一节的失败回滚。

    附件行（`KA-39` 的 `toc_role=reference`）那一段整段不发起：符号表与索引页抽不出知识点，而一次
    节调用的真机读数是百秒级——为读不到的东西付一次调用，是把「成行」误当成「该抽点」。
    页区间照旧由它切（正文前后的附件页因此不会并给相邻章），只是不发问。

    `content` 批次另有两件事（`KA-43`）：每节的回复带一枚节级总结，写回**在册目录行**的 `summary`
    （只写原值为空的、未背书的行，`set_outline_summary` 的五条 `WHERE`）；锚仍停在目录页的行整节跳过
    并点名——这一批不跑目录腿，没有核对腿把坏锚救回正文页，照发只会把目录行抄成知识点。
    目录页的认法用确定性条目腿（`toc_entries`），一次模型调用都不发。

    基本版本（`KA-44`）把这一腿收窄到**知识点行**：章与其下的节各占一行，节就是知识点、章不再是
    「一个能抽点的正文段」，所以内容批次只对 `node_type='point'` 的行发问（章级总结另走
    `extract_chapters` 的章腿），目录之前的未归属页也不再发问。回合同批的草稿不再落行——第三层
    在 `KA-6` 就不该存在，落出来的点行是屏上「本章知识点」那块错误干扰的来源。
    """
    repo = KpRepository(session)
    known_ids = {node.row.node_id for node in outline_nodes}
    non_knowledge = {
        node.row.node_id
        for node in outline_nodes
        if (node.row.meta or {}).get("toc_role") == TOC_ROLE_REFERENCE
    }
    node_types = node_types_of([node.row for node in outline_nodes])
    content_only = acceptance.stage == "content"
    toc_pages = {page.page for page in evidence.blocks if toc_entries(page)} if content_only else set()
    point_rows = unarchored = dropped_nodes = failed_sections = 0
    outline_summary_rows = 0
    skipped_unlocated: list[str] = []
    failed_details: list[dict[str, Any]] = []

    first_page = min((node.page for node in outline_nodes), default=None) if content_only else None
    for span in _section_spans(outline_nodes, evidence, first_page=first_page):
        if span.parent_id in non_knowledge:
            continue
        if content_only and node_types.get(span.parent_id or "") != NODE_TYPE_POINT:
            continue
        if content_only and span.page_start in toc_pages:
            skipped_unlocated.append(span.title or f"第 {span.page_start} 页")
            logger.warning(
                "内容批次跳过锚在目录页的行（`KA-43`）：%s 的 %s（p%s）",
                acceptance.book_id,
                span.title or "无标题行",
                span.page_start,
            )
            continue
        section = SectionInput(
            parent_id=span.parent_id,
            title=span.title,
            page_start=span.page_start,
            page_end=span.page_end,
            pages=_page_bodies(evidence, span),
        )
        try:
            replied = engine.points(section)
        except EngineError as exc:
            failed_sections += 1
            failed_details.append(
                {
                    "title": span.title or "目录未覆盖区间",
                    "page_start": span.page_start,
                    "page_end": span.page_end,
                    "reason": str(exc)[:200],
                }
            )
            logger.warning(
                "节抽取失败，跳过该节：%s 的第 %s..%s 页（%s）",
                acceptance.book_id,
                span.page_start,
                span.page_end,
                span.title or "目录未覆盖区间",
            )
            continue
        if content_only and span.parent_id and replied.summary:
            wrote = repo.set_outline_summary(
                book_id=acceptance.book_id, node_id=span.parent_id, summary=replied.summary
            )
            outline_summary_rows += 1 if wrote else 0
        if content_only:
            continue
        for draft in replied.drafts:
            if not isinstance(draft, dict):
                dropped_nodes += 1
                continue
            row = _point_row(
                draft, acceptance, engine.name, _resolve_parent(draft, span, outline_nodes)
            )
            if row is not None:
                row = _resolve_links(session, acceptance.book_id, row, known_ids)
            if row is None:
                dropped_nodes += 1
                continue
            repo.create(row)
            known_ids.add(row.node_id)
            point_rows += 1
            if row.parent_id is None:
                unarchored += 1
        session.commit()

    return PointBuild(
        point_rows=point_rows,
        unarchored=unarchored,
        dropped_nodes=dropped_nodes,
        failed_sections=failed_sections,
        failed_section_details=tuple(failed_details),
        outline_summary_rows=outline_summary_rows,
        skipped_unlocated=tuple(skipped_unlocated),
    )


# --------------------------------------------------------------------------- 章级总结与导读（KA-45）


@dataclass(frozen=True, slots=True)
class ChapterBuild:
    """章腿的读数：问到的章数、写进 `summary` 的章数、落盘的导读篇数、失败章与点名清单。"""

    chapter_rows: int = 0
    summary_rows: int = 0
    narrative_rows: int = 0
    failed_chapters: int = 0
    failed_chapter_details: tuple[dict[str, Any], ...] = ()


def _chapter_material(
    row: AfKp,
    rows: Sequence[AfKp],
    types: Mapping[str, str],
    evidence: EvidenceSet,
    summaries: Mapping[str, str],
    *,
    page_end: int | None = None,
) -> ChapterInput | None:
    """一章的材料三块（`KA-45`）：章首正文、章标题页（兜底）、其下各知识点的总结清单。

    章首正文＝章起点页起到首个子行起点页之前；章与其首节同页（b05 第1章 p9 与「导引」p9）时该段
    为空，改送章标题页那一页作兜底——两块不重复送同一页。子行按 `order_hint` 与起点页排成屏上序，
    空总结的行不进清单（空字符串在 prompt 里只会教模型编一段出来）。
    章没有可解释的起点页时回 `None`：没有页就连「章标题页」也读不到，发一次只有标题的调用等于付费
    让模型按名字编。
    """
    start = _first_anchor_page(row)
    if start is None:
        return None
    page_of = {other.node_id: _first_anchor_page(other) for other in rows}
    children = [
        other
        for other in rows
        if types.get(other.node_id) == NODE_TYPE_POINT and _has_ancestor(other, row.node_id, rows)
    ]
    children.sort(
        key=lambda other: (
            other.order_hint or 0,
            page_of.get(other.node_id) or 0,
        )
    )
    first_child = next((page_of[child.node_id] for child in children if page_of[child.node_id]), None)
    intro_end = start - 1 if first_child is None else first_child - 1
    intro_pages = (
        _page_bodies(evidence, SectionSpan(row.node_id, row.name, start, intro_end))
        if intro_end >= start
        else ()
    )
    title_pages = (
        ()
        if intro_pages
        else _page_bodies(evidence, SectionSpan(row.node_id, row.name, start, start))
    )
    listed = tuple(
        (child.name or child.node_id, summaries[child.node_id])
        for child in children
        if summaries.get(child.node_id)
    )
    return ChapterInput(
        parent_id=row.node_id,
        title=row.name or "",
        page_start=start,
        page_end=(
            page_end
            if page_end is not None
            else max((page_of[child.node_id] or start for child in children), default=start)
        ),
        intro_pages=intro_pages,
        title_pages=title_pages,
        point_summaries=listed,
    )


def _has_ancestor(row: AfKp, ancestor_id: str, rows: Sequence[AfKp]) -> bool:
    """沿挂父链找祖先：环上立即停（脏数据不该把章腿拖进死循环），找不到回 `False`。"""
    parent_of = {other.node_id: other.parent_id for other in rows}
    seen: set[str] = set()
    current = row.parent_id
    while current and current not in seen:
        if current == ancestor_id:
            return True
        seen.add(current)
        current = parent_of.get(current)
    return False


def extract_chapters(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    data_root: Path | str,
) -> ChapterBuild:
    """章级腿（`KA-45`）：先逐节、后逐章的两遍制——章材料依赖其下各节的节级总结。

    每章一次调用，产出**总结＋导读两段**：总结走 `set_outline_summary`（与节级总结同一条系统腿、
    同一条「原值为空才写」的判据），导读走 `write_narrative`＋`set_narrative_ref`（`KA-30` 的
    正文写腿与指针写腿）。导读已有指针或已有文件时**不覆盖**：同一份导读重跑一次内容批不该把
    人改过的那篇冲掉，而对齐正文的办法是显式重发，不是静默改写。

    只由 `content` 批次发起：基本版本的正式通路是「只目录 → 内容」，`full` 是 M5 的旧通路，
    它连目录都是这一批现成的、节总结也才刚写，追加章调用只会让旧批次多付二十次钱。
    """
    repo = KpRepository(session)
    rows = [node.row for node in outline_nodes]
    types = node_types_of(rows)
    # 节腿刚写下的总结与导读指针在库里，不在调用方递来的行快照上——`content` 批次的行快照是节腿
    # 开跑之前拼的（`KA-45` 两遍制的全部意义就在这里）。按列现读一次，别让章材料看空清单。
    fresh = (
        {
            node_id: (summary or "", narrative_ref or "")
            for node_id, summary, narrative_ref in session.execute(
                select(AfKp.node_id, AfKp.summary, AfKp.narrative_ref).where(
                    AfKp.node_id.in_([row.node_id for row in rows])
                )
            )
        }
        if rows
        else {}
    )
    summaries = {row.node_id: fresh.get(row.node_id, ("", ""))[0].strip() for row in rows}
    ends = {span.parent_id: span.page_end for span in _section_spans(outline_nodes, evidence)}
    chapters = sorted(
        (row for row in rows if types.get(row.node_id) == NODE_TYPE_CHAPTER),
        key=lambda row: (row.order_hint or 0, _first_anchor_page(row) or 0),
    )
    asked = summary_rows = narrative_rows = failed = 0
    details: list[dict[str, Any]] = []
    for row in chapters:
        material = _chapter_material(
            row, rows, types, evidence, summaries, page_end=ends.get(row.node_id)
        )
        if material is None:
            continue
        if not material.intro_pages and not material.title_pages:
            continue
        try:
            replied: ChapterReply = engine.chapter(material)
        except EngineError as exc:
            failed += 1
            details.append(
                {
                    "title": row.name or row.node_id,
                    "page_start": material.page_start,
                    "page_end": material.page_end,
                    "reason": str(exc)[:200],
                }
            )
            logger.warning(
                "章总结失败，跳过该章：%s 的 %s（p%s..p%s）",
                acceptance.book_id,
                row.name or row.node_id,
                material.page_start,
                material.page_end,
            )
            continue
        asked += 1
        if replied.summary and repo.set_outline_summary(
            book_id=acceptance.book_id, node_id=row.node_id, summary=replied.summary
        ):
            summary_rows += 1
        if replied.narrative and not fresh.get(row.node_id, ("", ""))[1].strip():
            try:
                name = write_narrative(
                    session, data_root=data_root, node_id=row.node_id, content=replied.narrative
                )
            except KpGateError as exc:
                logger.warning("章导读落盘失败：%s（%s）", row.node_id, exc.detail)
            else:
                if repo.set_narrative_ref(row.node_id, name).ok:
                    narrative_rows += 1
        session.commit()
    return ChapterBuild(
        chapter_rows=asked,
        summary_rows=summary_rows,
        narrative_rows=narrative_rows,
        failed_chapters=failed,
        failed_chapter_details=tuple(details),
    )





# --------------------------------------------------------------------------- 单点重生成（KA-45）


def _anchor_page_range(row: AfKp) -> tuple[int, int] | None:
    """一行锚点的页区间（min／max）；没有可解释的页号即 `None`（与读面 `_anchor_pages` 同一判据）。"""
    pages = [
        int(anchor.get("page") or 0)
        for anchor in (row.anchors or [])
        if isinstance(anchor, dict) and int(anchor.get("page") or 0) > 0
    ]
    return (min(pages), max(pages)) if pages else None


def _section_span_for(session: Session, node: AfKp) -> SectionSpan | None:
    """该知识点自己的树序区间（与点腿同一条 `section_spans_of` 算式），锚点只有一页也不缩水。

    在册行集合取全书：单点重生成不是「这一批的范围」，终点由下一条同级或更浅的行决定
    （DEFECT-028）；只看锚点那一页会把多页的一节总结截在起点上。行不在在册集合里（例如仍是
    `draft`）时回 `None`，调用方退回锚点页区间。
    """
    start = _first_anchor_page(node)
    if start is None:
        return None
    rows = [
        row for row in _registered_outline_rows(session, node.book_id) if _first_anchor_page(row)
    ]
    if not any(row.node_id == node.node_id for row in rows):
        return None
    pages = [_first_anchor_page(row) or 0 for row in rows]
    book = BookRepository(session).get(node.book_id)
    last_page = max([*pages, int(getattr(book, "page_count", 0) or 0), start])
    spans = section_spans_of(
        [
            SpanRow(
                node_id=row.node_id,
                name=row.name or "",
                order_hint=row.order_hint,
                page=_first_anchor_page(row) or 0,
                depth=row.depth or 1,
                toc_role=str((row.meta or {}).get("toc_role") or TOC_ROLE_KNOWLEDGE),
            )
            for row in rows
        ],
        first_page=min(pages),
        last_page=last_page,
    )
    return next((span for span in spans if span.parent_id == node.node_id), None)


def regenerate_section_summary(
    session: Session,
    *,
    data_root: Path | str,
    engine: KnowledgeEngine,
    node_id: str,
    if_rev: int | None = None,
) -> AfKp:
    """知识点单点重生成（`KA-45`）：详情面「重新生成总结」的落点，复用单节取材路径。

    判定序与写面同族：行不存在 404 → `if_rev` 不匹配 409 → 类型不合 422 → 同书在飞 409 → 模型失败 502
    → 模型没给总结 422。只有「知识点」这一型可重生成：章节的总结与导读由内容批次的章腿产出（同一份
    材料是整章，单点重生成拿不到），习题与其他的令文口径是「什么都不需要」。

    这一条腿**不走** `KA-4` 的单 worker 队列（它是同步请求），所以显式挡住同书在飞的批次：本机模型
    服务多为单并发，两路并跑只会让两边都超时。区间与页面都走节腿同一条算式（`section_spans_of`
    ＋`_page_bodies`），剔除眉脚也与节腿一致；材料不够时宁可 422，也不发改一次「没读到正文」的调用。
    """
    repo = KpRepository(session)
    node = repo.get(node_id)
    if node is None:
        raise KpGateError(404, f"知识点节点不存在：{node_id}")
    if if_rev is not None and node.rev != if_rev:
        raise KpGateError(
            409, f"af_kp 乐观并发冲突：{node_id} 当前 rev={node.rev}，请求 if_rev={if_rev}"
        )
    meta = node.meta or {}
    node_type = node_type_of(
        depth=node.depth,
        toc_role=str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE),
        toc_kind=str(meta.get("toc_kind") or ""),
    )
    if node.node_kind != "outline" or node_type != NODE_TYPE_POINT:
        raise KpGateError(
            422,
            f"只有知识点可以重生成总结：{node_id} 的类型是 {node_type}"
            "（章节走内容解析的章腿，习题与其他不生成总结）",
        )
    in_flight = repo.find_draft_batch(node.book_id)
    if in_flight is not None:
        raise KpGateError(
            409, f"梳理批次在飞（{node.book_id} 的 {in_flight}）：单点重生成等这一批跑完再发"
        )
    book = BookRepository(session).get(node.book_id)
    version = (node.job_id or "").strip() or (book.active_job_id if book is not None else "") or ""
    if not version:
        raise KpGateError(422, f"这一行没有取证版本可回指：{node_id}（发一次解析或重新挂锚后再试）")
    span = _section_span_for(session, node)
    pages = (span.page_start, span.page_end) if span is not None else _anchor_page_range(node)
    if pages is None:
        raise KpGateError(422, f"这一行没有可用锚点页：{node_id}（重挂锚点后再试）")
    acceptance = KpAcceptance(
        book_id=node.book_id,
        evidence_job_id=version,
        gen_batch=f"regen-{node_id}-{int(time.time())}",
        page_start=pages[0],
        page_end=pages[1],
        stage="content",
    )
    bind = getattr(engine, "bind_batch", None)
    if callable(bind):
        bind(book_id=node.book_id, gen_batch=acceptance.gen_batch)
    evidence = build_evidence_set(session, data_root=data_root, acceptance=acceptance)
    span = SectionSpan(node.node_id, node.name or "", pages[0], pages[1])
    bodies = _page_bodies(evidence, span)
    if not bodies:
        raise KpGateError(
            422,
            f"第 {pages[0]}..{pages[1]} 页没有可用的页产物：{node_id}"
            f"（缺页 {list(evidence.missing_pages)}，未解析 {list(evidence.unparsed_pages)}）",
        )
    section = SectionInput(
        parent_id=node.node_id,
        title=node.name or "",
        page_start=pages[0],
        page_end=pages[1],
        pages=bodies,
    )
    try:
        replied = engine.points(section)
    except EngineError as exc:
        raise KpGateError(502, f"模型调用失败（重生成总结）：{exc}") from exc
    summary = (replied.summary or "").strip()
    if not summary:
        raise KpGateError(422, f"模型没有给出总结，未落库：{node_id}（原文照旧，可重发一次）")
    written = repo.regenerate_outline_summary(node_id, summary=summary, if_rev=if_rev)
    if not written.ok or written.node is None:
        raise KpGateError(409, f"总结未落库：{node_id}（并发改写，请重读后重试）")
    session.commit()
    return written.node


# --------------------------------------------------------------------------- 批次收尾（KA-9/KA-13/KA-26）


@dataclass(frozen=True, slots=True)
class DroppedEdge:
    """一条被收尾拒掉的次序边：点名哪个节点的哪种边，并回指整条环路径（`KA-9`）。"""

    node_id: str
    kind: str
    to: str
    path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    """收尾五件事的读数：翻面行数、让位行数、拒边与外来环、对称归一与退池的条数、重跑作废的旧候选数。"""

    candidate_rows: int
    let_away_rows: int
    dropped_edges: tuple[DroppedEdge, ...] = ()
    foreign_cycles: tuple[tuple[str, ...], ...] = ()
    symmetric_duplicates: int = 0
    detached_to_pool: int = 0
    #: 重跑取代（`KA-36`）：同书其它批次落在本批页区间内的未背书候选行被翻成 `rejected` 的条数。
    superseded_rows: int = 0


def _live_graph(session: Session, book_id: str) -> dict[str, list[dict[str, Any]]]:
    """成环判定的图：本书**非 `rejected`** 行的 `links`（深拷贝，校验期间的裁剪只改这份内存图）。

    作废行退出结构——它不进导出面、也不占「最新 2 批」名额（设计篇「进程中途死」），它的边永不被读，
    因此不该拦下本批的边。图上少了它，指向它的边就只剩「端点是否存在」那一问，归梳理端点面的
    `validate_endorsement_links` 判 422。
    """
    graph: dict[str, list[dict[str, Any]]] = {}
    stmt = (
        select(AfKp)
        .where(AfKp.book_id == book_id, AfKp.gen_status != "rejected")
        .order_by(AfKp.node_id)
    )
    for row in session.scalars(stmt):
        graph[row.node_id] = [dict(link) for link in row.links or [] if isinstance(link, dict)]
    return graph


def _ordering_edges(links: Sequence[dict[str, Any]]) -> list[tuple[str, str]]:
    """一行的次序边，按 `(kind, to)` 定序：DFS 的走向必须与行的写入顺序无关，拒边结果才可复现。"""
    return sorted(
        (str(link.get("kind") or ""), str(link.get("to") or ""))
        for link in links
        if link.get("kind") in ORDERING_LINK_KINDS
    )


def _collapse_symmetric_edges(
    graph: dict[str, list[dict[str, Any]]], writable: set[str]
) -> tuple[set[str], int]:
    """`co_requisite` 的反向重复声明归一（「对称边归一端点序」＝静默归一、不报错）。

    正向记录已在图上（含别人写的）时，反向那条就是同一关系的第二次声明，去掉它幂等；反向只在可写行上
    时不迁到对端——「写侧不得为『整齐』去改对端行」是同一节的另一条不变式，读侧的并集本来就认它。
    返回被改动的 `node_id` 与去掉的重复边数。
    """
    declared = {
        (node_id, str(link.get("to") or ""))
        for node_id, links in graph.items()
        for link in links
        if link.get("kind") == SYMMETRIC_LINK_KIND
    }
    changed: set[str] = set()
    duplicates = 0
    for node_id, links in graph.items():
        if node_id not in writable:
            continue
        kept = []
        for link in links:
            target = str(link.get("to") or "")
            reverse_duplicate = (
                link.get("kind") == SYMMETRIC_LINK_KIND
                and node_id > target
                and (target, node_id) in declared
            )
            if reverse_duplicate:
                duplicates += 1
                continue
            kept.append(link)
        if len(kept) != len(links):
            graph[node_id] = kept
            changed.add(node_id)
    return changed, duplicates


def _reject_ordering_cycles(
    graph: dict[str, list[dict[str, Any]]], writable: set[str]
) -> tuple[list[DroppedEdge], list[tuple[str, ...]], set[str]]:
    """整批一次 DFS 的次序边校验（`KA-9`／「DAG 只覆盖次序语义」）。

    只走 `prerequisite` 与 `extends`：`co_requisite`/`explains`/`exemplifies` 不是次序语义，成环合法。
    环里断的是**本批 `draft` 行**那条边——「`candidate` 及以上行零被触碰」优先于修环，整环都长在别人
    行上时只回指路径、一行不改。断掉的边当场从内存图摘掉，同一条环因此不会被重复上报。
    """
    dropped: list[DroppedEdge] = []
    foreign: list[tuple[str, ...]] = []
    changed: set[str] = set()
    unseen, on_stack, finished = 0, 1, 2
    state = {node_id: unseen for node_id in graph}

    for root in sorted(graph):
        if state[root] != unseen:
            continue
        path: list[str] = [root]
        enters: list[str] = [""]  # 与 path 平行：path[i] 由哪一种次序边进来
        frames: list[list[tuple[str, str]]] = [_ordering_edges(graph[root])]
        state[root] = on_stack
        while frames:
            if not frames[-1]:
                frames.pop()
                enters.pop()
                state[path.pop()] = finished
                continue
            kind, target = frames[-1].pop(0)
            if target not in graph:
                continue  # 端点不在活图里（不存在或已作废）：不构成环
            if state[target] == unseen:
                state[target] = on_stack
                path.append(target)
                enters.append(kind)
                frames.append(_ordering_edges(graph[target]))
                continue
            if state[target] != on_stack:
                continue  # 子图已走完：这条边不闭环
            start = path.index(target)
            edges = [(path[i], enters[i + 1], path[i + 1]) for i in range(start, len(path) - 1)]
            edges.append((path[-1], kind, target))
            culprit = next((edge for edge in edges if edge[0] in writable), None)
            if culprit is None:
                foreign.append((*path[start:], target))
                continue
            owner, edge_kind, edge_to = culprit
            if not any(
                str(link.get("kind")) == edge_kind and str(link.get("to")) == edge_to
                for link in graph[owner]
            ):
                continue  # 同一对端点的重复声明：上一条已断，环不再存在
            graph[owner] = [
                link
                for link in graph[owner]
                if not (str(link.get("kind")) == edge_kind and str(link.get("to")) == edge_to)
            ]
            changed.add(owner)
            dropped.append(DroppedEdge(owner, edge_kind, edge_to, (*path[start:], target)))
    return dropped, foreign, changed


def finalize_batch(
    session: Session,
    *,
    book_id: str,
    gen_batch: str,
    page_start: int | None = None,
    page_end: int | None = None,
    stage: str = "full",
) -> FinalizeResult:
    """批次收尾（`KA-9`/`KA-13`/`KA-26`/`KA-36`）：让位 → 挂父校验 → 边的归一与裁剪 → 旧候选作废 → 翻面。

    次序是判据的一部分。先按 `KA-13` 把撞上人已背书身份的新行作废，它们就不再进图、不再占边；再对
    本批剩下的行做对称归一与整批一次 DAG 校验；然后按 `KA-36` 把**同一页区间**里其它批次的未背书
    候选行作废（重跑说的是「这一段的旧读数过期了」）；最后**一条条件 UPDATE** 把 `draft` 翻成
    `candidate`，`KA-4` 的在飞判据随之解除。翻面放在收尾末尾，正是「进程中途死 ⇒ 残留 `draft` ⇒
    启动期归一」这条通路成立的前提。

    全程只碰本批的 `draft` 行：`WHERE` 收在仓储的每条系统腿语句里，`candidate` 及以上行零被触碰不是
    这里的自觉，是语句写不出来。唯一例外是 `KA-36` 的旧候选作废——它按判据点名到别的批次，但仍只
    翻 `candidate` 且只翻 `confirmed=0` 的那一类，人已背书的行一行不碰。

    `page_start`／`page_end` 缺任一枚 ⇒ 不做旧候选作废（没有区间就没有「这一段」的边界，猜一个
    等于替调用方划定重跑范围）。

    `stage='content'` 改判两处（`KA-43`），其余逐字不动：合法父集＝**在册目录行**（本批只落点行，
    挂到哪一章由上一批的目录决定，`KA-26` 的「同批目录行」在只补内容的批次里没有行可指）、
    作废面只覆**点行**（在册目录行不是这一批的过期读数，把它们判死等于重跑一次点就把人工刚审过的
    目录清空）。
    """
    repo = KpRepository(session)
    drafts = repo.list_batch(book_id, gen_batch, gen_status="draft")
    if not drafts and stage != "content":
        return FinalizeResult(0, 0)

    endorsed = repo.confirmed_identity_keys(book_id)
    yielding = [
        row.node_id
        for row in drafts
        if (row.node_kind, row.parent_id, row.norm_key) in endorsed
    ]
    let_away = repo.reject_draft_nodes(yielding)
    writable = {row.node_id for row in drafts} - set(yielding)

    batch_outline_ids = {row.node_id for row in drafts if row.node_kind == NODE_KIND_OUTLINE}
    if stage == "content":
        batch_outline_ids |= {
            row.node_id
            for row in repo.list_for_book(book_id, node_kind=NODE_KIND_OUTLINE)
            if row.gen_status == "candidate"
        }
    detached_to_pool = 0
    for row in drafts:
        if (
            row.node_id in writable
            and row.parent_id is not None
            and row.parent_id not in batch_outline_ids
        ):
            if repo.detach_draft_parent(row.node_id):
                detached_to_pool += 1

    graph = _live_graph(session, book_id)
    changed, symmetric_duplicates = _collapse_symmetric_edges(graph, writable)
    dropped_edges, foreign_cycles, cut = _reject_ordering_cycles(graph, writable)
    for node_id in sorted(changed | cut):
        repo.rewrite_draft_links(node_id, graph[node_id] or None)

    # `KA-36`：本批要留下的读数覆盖了这一段，其它批次的未背书候选行就是过期读数。放在翻面之前——
    # 本批自己的 draft 行不在作废面里（判据是 `candidate`），次序只影响「同一事务里谁先落地」。
    superseded = 0
    if page_start is not None and page_end is not None:
        superseded = repo.reject_superseded_candidates(
            book_id,
            gen_batch,
            page_start=page_start,
            page_end=page_end,
            node_kind=NODE_KIND_POINT if stage == "content" else None,
        )

    candidates = repo.flip_batch_status(book_id, gen_batch, source="draft", target="candidate")
    return FinalizeResult(
        candidates,
        let_away,
        tuple(dropped_edges),
        tuple(foreign_cycles),
        symmetric_duplicates,
        detached_to_pool,
        superseded,
    )


# --------------------------------------------------------------------------- 执行体


@dataclass(frozen=True, slots=True)
class KnowledgeOutcome:
    """一次批次跑完的账面读数：产出行数、入池行数与各缺口，全部计数可见、无一条静默。

    后六个字段来自收尾（`finalize_batch`）：`candidate_rows` 才是「这一批留下了多少行」，
    `point_rows`/`outline_rows` 只是落库中途的读数（那时全为 `draft`）。
    """

    book_id: str
    gen_batch: str
    evidence_job_id: str
    engine: str
    outline_rows: int
    point_rows: int
    unarchored: int
    missing_pages: tuple[int, ...]
    unparsed_pages: tuple[int, ...]
    dropped_depth: int
    dropped_nodes: int
    failed_sections: int
    candidate_rows: int = 0
    let_away_rows: int = 0
    dropped_edges: tuple[DroppedEdge, ...] = ()
    foreign_cycles: tuple[tuple[str, ...], ...] = ()
    symmetric_duplicates: int = 0
    detached_to_pool: int = 0
    #: 失败节的点名清单（DEFECT-026 甲）：与 `failed_sections` 同序，随批次流水的 `result` 外发。
    failed_section_details: tuple[dict[str, Any], ...] = ()
    #: 未定位的目录条目名（`KA-33`／`KA-38`）：定锚与核对都救不回来的行，随批次流水外发点名。
    unlocated_outline_entries: tuple[str, ...] = ()
    #: 页级复核剔掉的目录页页号（`KA-33`）：符号表／索引页与目录页行形状相同，命中率过半不中即整页剔除。
    dropped_toc_pages: tuple[int, ...] = ()
    #: 重跑作废的旧候选行数（`KA-36`）：同书其它批次落在本批页区间内的未背书候选。
    superseded_rows: int = 0
    #: 判页腿认下的目录页（`KA-37`）：本批目录取材的地面，屏上「这一轮的目录是从哪几页抄来的」指认得出。
    outline_toc_pages: tuple[int, ...] = ()
    #: 整理失败而回落条目腿的页（`KA-37`）：这几页的目录行不是模型抄的，是形状判据兜的。
    outline_fallback_pages: tuple[int, ...] = ()
    #: 核对腿救回来的条目名（`KA-38`）：只有「落在哪一页」被模型改过，名字与层级照旧。
    verified_outline_entries: tuple[str, ...] = ()
    #: 附件行枚数（`KA-39`）：正文前后的引言／符号表等成行但标 `reference`，不计入知识点。
    reference_rows: int = 0
    #: 写上节级总结的在册目录行数（`KA-43`）：`content` 批次的第五系统腿只在这一批里发生。
    outline_summary_rows: int = 0
    #: 章腿问到的章数（`KA-45`）：只数真正发出调用并回来的章，材料读不到的章不进这一列。
    chapter_rows: int = 0
    #: 写上章级总结的章数（`KA-45`）：与 `outline_summary_rows` 同一条「原值为空才写」的写手。
    chapter_summary_rows: int = 0
    #: 落盘的章级导读篇数（`KA-45`）：已有导读的章不覆盖，故这一列可能小于 `chapter_rows`。
    chapter_narrative_rows: int = 0
    failed_chapters: int = 0
    #: 失败章的点名清单（`KA-45`）：与 `failed_sections` 同形，随批次流水外发。
    failed_chapter_details: tuple[dict[str, Any], ...] = ()


class LedgeredKnowledgeEngine:
    """梳理引擎的记账外壳（`KA-23`）：每一次模型调用向 `qed_llm_calls` 追加一行。

    四条腿都在册：知识点逐节（`points`）与目录三条（判页 `toc_pages`／逐页整理 `toc_entries`／
    坏行核对 `toc_verify`，`KA-35`／`KA-37`／`KA-38`）。挂在执行体唯一入口之外，管线本身仍只认
    `KnowledgeEngine` 协议，判据一条不改。三条纪律：
    一调用一行（成功与失败各一行，失败带原因后**原样抛出**——目录腿的失败域靠回落、知识点腿的失败域
    靠 `extract_points`，两边靠的都是同一个异常，包装器不能把它吞成「这页没条目」或「这节没产物」）；
    账走 `LlmCallLedger` 自己的会话，批次回滚带不走已经发生过的调用（`KA-23`／R6）；
    `name` 与协议之外的能力（如适配器的 `close`、`bind_batch`）一律透传。
    """

    def __init__(
        self,
        engine: KnowledgeEngine,
        ledger: LlmCallLedger,
        identity: LlmCallIdentity,
        *,
        book_id: str,
        gen_batch: str,
    ) -> None:
        self._engine = engine
        self._ledger = ledger
        self._identity = identity
        self._book_id = book_id
        self._gen_batch = gen_batch

    @property
    def name(self) -> str:
        return self._engine.name

    def __getattr__(self, item: str) -> Any:
        """协议之外的能力透传：包装器不能顺手关掉适配器自己的资源。"""
        return getattr(self._engine, item)

    def health(self) -> bool:
        return self._engine.health()

    def toc_pages(self, samples: Sequence[PageSample]) -> TocPagePick:
        return self._record(
            "toc_pages",
            lambda: self._engine.toc_pages(samples),
            {"page_count": len(samples), "page_nos": [sample.page_no for sample in samples][:20]},
        )

    def toc_entries(self, page_no: int, text: str) -> list[OutlineEntry]:
        return self._record(
            "toc_entries",
            lambda: self._engine.toc_entries(page_no, text),
            {"page": page_no, "char_count": len(text)},
        )

    def toc_verify(self, items: Sequence[TocVerifyItem]) -> list[TocVerifyAnswer]:
        return self._record(
            "toc_verify",
            lambda: self._engine.toc_verify(items),
            {"count": len(items), "names": [item.name[:60] for item in items][:20]},
        )

    def points(self, section: SectionInput) -> SectionPoints:
        return self._record(
            "points",
            lambda: self._engine.points(section),
            {
                "section": section.title,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "page_count": len(section.pages),
            },
        )

    def chapter(self, material: ChapterInput) -> ChapterReply:
        return self._record(
            "chapter",
            lambda: self._engine.chapter(material),
            {
                "chapter": material.title,
                "page_start": material.page_start,
                "page_end": material.page_end,
                "point_count": len(material.point_summaries),
            },
        )

    def _record(self, step: str, invoke: Callable[[], Any], facts: dict[str, Any]) -> Any:
        """耗时由包装器实测（`duration_ms` 是「调用耗时」，不是适配器自报的数字），失败原因取异常文本。"""
        started = time.perf_counter()
        meta = {"book_id": self._book_id, "gen_batch": self._gen_batch, "engine": self.name, **facts}
        try:
            result = invoke()
        except Exception as exc:  # noqa: BLE001 - 失败也要留账，异常本身照原样交给调用方的失败域
            elapsed = _elapsed_ms(started)
            self._ledger.record(
                KpCallRow(
                    step=step, status="error", identity=self._identity, duration_ms=elapsed, meta=meta, error=str(exc)
                )
            )
            raise
        self._ledger.record(
            KpCallRow(step=step, status="success", identity=self._identity, duration_ms=_elapsed_ms(started), meta=meta)
        )
        return result


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


@dataclass(slots=True)
class _KpOutlineSink:
    """`KA-40` 的落库腿：逐页一批一提交，定稿改写走同批那条三列写手。

    两枚方法各自只「发一条仓储的机器腿 ＋ 提交一次」——事务边界在这里而不是仓储里，因为逐页落行的
    全部意义就是「下一页还在整理时，别人已经读得到这一批」，不提交等于没落。`landed_ids` 是交给
    `run_knowledge_job` 的跳行凭据：末批只插早落没覆盖到的那些行，同一批不该出现两次同一个 `node_id`。
    """

    repo: KpRepository
    session: Session
    book_id: str
    gen_batch: str
    landed_ids: set[str] = field(default_factory=set)

    def land(self, nodes: Sequence[OutlineNode]) -> None:
        for node in nodes:
            self.repo.create(node.row)
            self.landed_ids.add(node.row.node_id)
        self.session.commit()

    def repoint(self, nodes: Sequence[OutlineNode]) -> None:
        self.repo.repoint_draft_outline(
            book_id=self.book_id,
            gen_batch=self.gen_batch,
            patches=[
                {
                    "node_id": node.row.node_id,
                    "anchors": node.row.anchors,
                    "order_hint": node.row.order_hint,
                    "parent_id": node.row.parent_id,
                }
                for node in nodes
            ],
        )
        self.session.commit()


def run_knowledge_job(
    session: Session,
    *,
    data_root: Path | str,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
) -> KnowledgeOutcome:
    """两阶段执行体：取证集 → 目录（逐页落行 → 定稿改写）→ 逐节抽点落行 → 收尾（`KA-5`/`KA-9`/`KA-40`）。

    知识点阶段按 `acceptance.stage` 条件发起（`KA-34`）：只目录批次零点次、零点行，收尾与流水照跑
    ——目录行的 `candidate` 身份不因为「这一批没抽点」而悬着。
    跑完本函数时本批已是 `candidate`：中途的 `draft` 态只服务 `KA-4` 的在飞判据与 `PM-13` 单 worker
    队列的排他凭据，收尾（`finalize_batch`）是这条通路的最后一步。本函数不写流水行——
    账面由调用方（`JobRunner` 的 knowledge 腿）按成功／异常两条路各发一行 `op_kind='kp'`（`KA-27`）。

    执行期第一眼的在飞复查承接受理门的窗口：受理时还没有 `draft` 行、轮到本批执行时前一批已落行的
    话，本批自止并抛 409。同批自己的行不算冲突——重跑同一份受理结果不该自我否决。

    目录行边整理边落（`KA-40`）：每页整理完就落一批 `draft`，所以「受理到落行」之间不再只有凭据没有
    行，`KA-4` 的在飞判据从第一页起就认得这一批。末批只补早落没覆盖到的行（回落行、附件行、heading 行），
    已落的那批行由目录阶段的定稿步同批改写一次，不在这里二次插入。
    """
    repo = KpRepository(session)
    in_flight = repo.find_draft_batch(acceptance.book_id)
    if in_flight is not None and in_flight != acceptance.gen_batch:
        raise KpGateError(
            409,
            f"梳理执行期复查：{acceptance.book_id} 已有在飞批次 {in_flight}"
            f"（本批 {acceptance.gen_batch}）",
        )

    # 批次身份交给适配器（R8 甲）：协议不携带受理结果，而网关那条通路的账只认请求体里的归因。
    # 声明之外的引擎（协议内实现）一条都不假设——没有 `bind_batch` 就照常跑。
    # 备注跟着批次身份一起递过去（AF-T5 甲）：声明了 `bind_batch` 就必须认这一枚关键字参数。
    bind = getattr(engine, "bind_batch", None)
    if callable(bind):
        bind(book_id=acceptance.book_id, gen_batch=acceptance.gen_batch, review_note=acceptance.review_note)

    evidence = build_evidence_set(session, data_root=data_root, acceptance=acceptance)
    if acceptance.stage == "content":
        # 只补内容（`KA-43`）：目录腿一条不发起，骨架取在册行；本批自己的行只有点行。
        outline = OutlineBuild(nodes=(), dropped_depth=0, dropped_malformed=0)
        section_nodes = _outline_nodes_from_book(
            _registered_outline_rows(
                session,
                acceptance.book_id,
                page_start=acceptance.page_start,
                page_end=acceptance.page_end,
            )
        )
    else:
        outline_sink = _KpOutlineSink(
            repo=repo, session=session, book_id=acceptance.book_id, gen_batch=acceptance.gen_batch
        )
        outline = build_outline(acceptance, evidence, engine, sink=outline_sink)
        for node in outline.nodes:
            if node.row.node_id in outline_sink.landed_ids:
                continue
            repo.create(node.row)
        session.commit()
        section_nodes = list(outline.nodes)

    points = (
        extract_points(session, engine, acceptance, evidence, section_nodes)
        if acceptance.stage in ("full", "content")
        else PointBuild(0, 0, 0, 0)
    )
    chapters = (
        extract_chapters(
            session, engine, acceptance, evidence, section_nodes, data_root=data_root
        )
        if acceptance.stage == "content"
        else ChapterBuild()
    )
    closing = finalize_batch(
        session,
        book_id=acceptance.book_id,
        gen_batch=acceptance.gen_batch,
        page_start=acceptance.page_start,
        page_end=acceptance.page_end,
        stage=acceptance.stage,
    )
    session.commit()
    return KnowledgeOutcome(
        book_id=acceptance.book_id,
        gen_batch=acceptance.gen_batch,
        evidence_job_id=acceptance.evidence_job_id,
        engine=engine.name,
        outline_rows=len(outline.nodes),
        point_rows=points.point_rows,
        unarchored=points.unarchored,
        missing_pages=evidence.missing_pages,
        unparsed_pages=evidence.unparsed_pages,
        dropped_depth=outline.dropped_depth,
        dropped_nodes=points.dropped_nodes,
        failed_sections=points.failed_sections,
        failed_section_details=points.failed_section_details,
        outline_summary_rows=points.outline_summary_rows,
        chapter_rows=chapters.chapter_rows,
        chapter_summary_rows=chapters.summary_rows,
        chapter_narrative_rows=chapters.narrative_rows,
        failed_chapters=chapters.failed_chapters,
        failed_chapter_details=chapters.failed_chapter_details,
        unlocated_outline_entries=(*outline.unlocated, *points.skipped_unlocated),
        dropped_toc_pages=outline.dropped_toc_pages,
        outline_toc_pages=outline.toc_pages,
        outline_fallback_pages=outline.fallback_pages,
        verified_outline_entries=outline.verified,
        reference_rows=outline.reference_rows,
        superseded_rows=closing.superseded_rows,
        candidate_rows=closing.candidate_rows,
        let_away_rows=closing.let_away_rows,
        dropped_edges=closing.dropped_edges,
        foreign_cycles=closing.foreign_cycles,
        symmetric_duplicates=closing.symmetric_duplicates,
        detached_to_pool=closing.detached_to_pool,
    )


# --------------------------------------------------------------------------- 锚点复核（KA-19）


@dataclass(frozen=True, slots=True)
class AnchorReview:
    """一条待复核标记：点名是哪个节点的哪个锚页、为什么读不下去。"""

    node_id: str
    page: int
    reason: str


def anchor_review_flags(
    session: Session, *, data_root: Path | str, nodes: Sequence[AfKp]
) -> list[AnchorReview]:
    """锚点判读（`KA-18`/`KA-19`）：按**行自身取证版本**解释，越界一律可见保留——不改写、不删行。

    与结构意图行的越界判据同形（[ADR 0010](../../docs/adr/0010-page-regions-are-parse-inputs.md)
    决定 1 的先例），族内只有一种「失效怎么办」。人工直填行（`job_id=''`）无取证版本可回指，
    只可靠 `summary`，同样只标记不判死。处置动作（重挂锚点或撤确认）交人工。
    """
    pages_by_version: dict[tuple[str, str], set[int]] = {}
    blocks_by_page: dict[tuple[str, str, int], int | None] = {}
    flags: list[AnchorReview] = []

    for node in nodes:
        book = BookRepository(session).get(node.book_id)
        if not node.job_id:
            for anchor in node.anchors or []:
                flags.append(
                    AnchorReview(
                        node.node_id,
                        int(anchor.get("page") or 0),
                        "人工直填行（job_id=''）无取证版本可回指",
                    )
                )
            continue
        if book is None:
            flags.append(AnchorReview(node.node_id, 0, f"书目缺失：{node.book_id} 的锚点无处解释"))
            continue
        key = (node.book_id, node.job_id)
        if key not in pages_by_version:
            pages_by_version[key] = {
                row.page_no
                for row in PageRepository(session).list_for_book(node.book_id, node.job_id)
                if row.status == "parsed"
            }
        parsed_pages = pages_by_version[key]
        for anchor in node.anchors or []:
            page_no = int(anchor.get("page") or 0)
            if page_no not in parsed_pages:
                flags.append(AnchorReview(node.node_id, page_no, "锚点页不在取证版本的页集内"))
                continue
            block_key = (node.book_id, node.job_id, page_no)
            if block_key not in blocks_by_page:
                blocks_by_page[block_key] = _block_count(
                    data_root,
                    book.domain_id,
                    book.course_id,
                    node.book_id,
                    node.job_id,
                    page_no,
                )
            count = blocks_by_page[block_key]
            if count is None:
                flags.append(AnchorReview(node.node_id, page_no, "取证版本缺该页产物"))
                continue
            if any(not 0 <= int(index) < count for index in anchor.get("block_indexes") or []):
                flags.append(AnchorReview(node.node_id, page_no, "块序越出该版 blocks.json 空间"))

    return flags


def _block_count(
    data_root: Path | str, domain_id: str, course_id: str, book_id: str, job_id: str, page_no: int
) -> int | None:
    """该版该页的块数；产物不可读时返回 `None`（缺产物，不是零块）。"""
    path = (
        version_dir_for(data_root, domain_id, course_id, book_id, job_id)
        / "pages"
        / f"p{page_no:04d}.blocks.json"
    )
    if not path.is_file():
        return None
    try:
        return len(BlocksPage.model_validate_json(path.read_text(encoding="utf-8")).blocks)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- 批次流水与导出面


def kp_batch_row(acceptance: KpAcceptance, outcome: KnowledgeOutcome) -> KpBatchRow:
    """批次流水的构造（`KA-27`）：整批一行，`result` 只放执行体账面里已有的读数。

    页码数组不截断（ADR 0007 决定 2 的同一纪律延长到批次的缺页与未解析页）；收尾的边处置是明细，
    在流水里折算成计数——流水答「这一批做了什么」，逐条边仍在 `af_kp` 行的 `links` 与日志里。
    两条例外都是「没有行可回读」的那一类：`failed_section_details` 点名被吃掉的节（DEFECT-026 甲），
    `unlocated_outline_entries` 点名回定位没落进正文页的目录条目（`KA-33`）——账面不留名就只剩
    服务日志里一条会滚走的 warning，人工既补不了页也圈不出重跑范围。
    """
    return KpBatchRow(
        gen_batch=outcome.gen_batch,
        book_id=outcome.book_id,
        job_id=outcome.evidence_job_id,
        status="ok",
        page_start=acceptance.page_start,
        page_end=acceptance.page_end,
        review_note=acceptance.review_note,
        stage=acceptance.stage,
        result={
            "outline_rows": outcome.outline_rows,
            "point_rows": outcome.point_rows,
            "candidate_rows": outcome.candidate_rows,
            "let_away_rows": outcome.let_away_rows,
            "unarchored": outcome.unarchored,
            "dropped_depth": outcome.dropped_depth,
            "dropped_nodes": outcome.dropped_nodes,
            "failed_sections": outcome.failed_sections,
            "failed_section_details": list(outcome.failed_section_details),
            "symmetric_duplicates": outcome.symmetric_duplicates,
            "detached_to_pool": outcome.detached_to_pool,
            "dropped_edges": len(outcome.dropped_edges),
            "foreign_cycles": len(outcome.foreign_cycles),
            "missing_page_nos": list(outcome.missing_pages),
            "unparsed_page_nos": list(outcome.unparsed_pages),
            "unlocated_outline_entries": list(outcome.unlocated_outline_entries),
            "dropped_toc_pages": list(outcome.dropped_toc_pages),
            "outline_toc_pages": list(outcome.outline_toc_pages),
            "outline_fallback_pages": list(outcome.outline_fallback_pages),
            "verified_outline_entries": list(outcome.verified_outline_entries),
            "reference_rows": outcome.reference_rows,
            "superseded_rows": outcome.superseded_rows,
            "outline_summary_rows": outcome.outline_summary_rows,
            "chapter_rows": outcome.chapter_rows,
            "chapter_summary_rows": outcome.chapter_summary_rows,
            "chapter_narrative_rows": outcome.chapter_narrative_rows,
            "failed_chapters": outcome.failed_chapters,
            "failed_chapter_details": list(outcome.failed_chapter_details),
        },
    )


def unconfirmed_anchor_flags(session: Session, nodes: Sequence[AfKp]) -> list[AnchorReview]:
    """导出面的「锚点页未确认」提示（`KA-3` 的第二半：确认门是**外发**门槛，不是梳理门槛）。

    只在页确认门已启用时产出：门未启用时「页没确认」是现状而非缺陷，逢行就标会把真信号埋掉。
    只标记、不拦截——拦在导出面等于把确认门塞进 `af_kp` 之外再造一处。
    """
    if not PAGE_CONFIRM_GATE_ENABLED:
        return []
    flags: list[AnchorReview] = []
    confirmed_by_version: dict[tuple[str, str], set[int]] = {}
    for node in nodes:
        if not node.job_id:
            continue
        key = (node.book_id, node.job_id)
        if key not in confirmed_by_version:
            confirmed_by_version[key] = {
                row.page_no
                for row in PageRepository(session).list_for_book(node.book_id, node.job_id)
                if row.confirm_state == "confirmed"
            }
        confirmed_pages = confirmed_by_version[key]
        for anchor in node.anchors or []:
            page_no = int(anchor.get("page") or 0)
            if page_no and page_no not in confirmed_pages:
                flags.append(
                    AnchorReview(node.node_id, page_no, "锚点页尚未人工确认（页确认门已启用）")
                )
    return flags


#: `order_hint` 为空（未归档池）时的排序位：尾置，但不能溢出成负数。
_UNORDERED = 1 << 62


def _export_key(row: AfKp) -> tuple[int, str]:
    """行内序：`order_hint` 为空的排到最后（没声明过位置的不插队），再以 `node_id` 收尾保证可重复。"""
    return (row.order_hint if row.order_hint is not None else _UNORDERED, row.node_id)


def order_for_export(rows: Sequence[AfKp]) -> list[AfKp]:
    """导出序（`KA-15`）：目录前序（章→其节，按 `order_hint`）先出，点随其父的先后，池与悬父尾置。

    父先于子是拉取方的硬前提（建表时父必须已在表里）。目录里读不到的父（未确认、故不在导出集里）
    与未归档池同样尾置——把子排在一个还没外发的父后面，等于让拉取方按序插入时当场撞空父。
    """
    outlines = [row for row in rows if row.node_kind == NODE_KIND_OUTLINE]
    children: dict[str | None, list[AfKp]] = {}
    for row in outlines:
        children.setdefault(row.parent_id, []).append(row)
    for group in children.values():
        group.sort(key=_export_key)
    ordered: list[AfKp] = []
    emitted: set[str] = set()

    def emit(row: AfKp) -> None:
        """前序下行，`emitted` 兼作环保护：目录行成环（人工改挂造成）时仍每行出一次，不静默丢。"""
        if row.node_id in emitted:
            return
        emitted.add(row.node_id)
        ordered.append(row)
        for child in children.get(row.node_id, []):
            emit(child)

    for root in children.get(None, []):
        emit(root)
    for row in sorted(outlines, key=_export_key):
        emit(row)

    position = {row.node_id: index for index, row in enumerate(ordered)}
    tail = len(ordered) + 1
    points = [row for row in rows if row.node_kind != NODE_KIND_OUTLINE]
    points.sort(key=lambda row: (position.get(row.parent_id or "", tail), *_export_key(row)))
    return [*ordered, *points]


# --------------------------------------------------------------------------- 人工背书的边校验（服务端不变式）


def validate_endorsement_links(
    session: Session, *, node: AfKp, links: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """人工腿 `links` 的四条不变式（端点存在／无自环／次序边不成环／对称边归一），违规抛 422 点名。

    复用收尾的**同一张活图与同一批函数**（`_live_graph`/`_collapse_symmetric_edges`/
    `_reject_ordering_cycles`）：人工腿不另造第二种读法。与机器腿两处不同——端点必须是同册**非作废行**
    的 `node_id`（人工界面持有身份，不做名称回指解析；作废行退出结构，指向它的边永不被读，因此同样
    算缺失）；本行新写的边一旦闭环就整条拒收，而不是像收尾那样断边放行——机器批的断边有
    `dropped_edges` 计数披露，把人工声明悄悄摘掉一条等于替人改结论。
    """
    declared = [dict(link) for link in links]
    loops = sum(1 for link in declared if str(link.get("to") or "") == node.node_id)
    if loops:
        raise KpGateError(422, f"自环：{node.node_id} 不能给自己挂边（{loops} 条）")
    known = {
        row_id
        for row_id in session.scalars(
            select(AfKp.node_id).where(AfKp.book_id == node.book_id, AfKp.gen_status != "rejected")
        )
    }
    missing = sorted({str(link.get("to") or "") for link in declared} - known)
    if missing:
        raise KpGateError(422, f"边端点不存在于本册：{'、'.join(missing)}")

    graph = _live_graph(session, node.book_id)
    graph[node.node_id] = declared
    _collapse_symmetric_edges(graph, {node.node_id})
    dropped, _, _ = _reject_ordering_cycles(graph, {node.node_id})
    if dropped:
        edge = dropped[0]
        raise KpGateError(
            422,
            f"次序边成环：{edge.kind} → {edge.to} 落在环 {' → '.join(edge.path)}"
            "（`prerequisite`/`extends` 必须成 DAG；人工声明不代断边，请自己摘掉一条）",
        )
    return graph[node.node_id]


# --------------------------------------------------------------------------- 长文与删除通路（KA-20／KA-21／KA-30）


#: 长文目录名（`KA-20` 的派生式）：`<book_dir>/kp/<node_id>.md`——落书目录内，逐本删除与管理面
#: 清除天然覆盖它，不留孤儿。
NARRATIVE_DIRNAME = "kp"


def narrative_file_name(node_id: str) -> str:
    """长文的单段文件名＝`<node_id>.md`（`KA-20`：文件名即节点身份，`node_id` 本来就不可改名换身份）。"""
    ensure_safe_segment(node_id, "node_id")
    return f"{node_id}.md"


def _narrative_path(session: Session, *, data_root: Path | str, node: AfKp) -> Path:
    """派生长文完整路径的唯一算式：按行所属书的 `domain_id/course_id` **现算**，不读 `narrative_ref`。

    为什么不存路径而要每次现算：书目录含 `domain_id/course_id`，重新同步可能带回新值，存全路径会让
    长文一夜之间指向不存在（`KA-20`）。`ensure_within` 是写盘前的最后一道根内闸（`PP-5`）。
    """
    book = BookRepository(session).get(node.book_id)
    if book is None:
        raise KpGateError(404, f"长文的宿主书目不存在：{node.book_id}")
    path = (
        book_dir_for(data_root, book.domain_id, book.course_id, node.book_id)
        / NARRATIVE_DIRNAME
        / narrative_file_name(node.node_id)
    )
    return ensure_within(data_root, path)


def write_narrative(session: Session, *, data_root: Path | str, node_id: str, content: str) -> str:
    """长文落盘（`KA-20`）：派生路径 + `PP-4` 原子替换，返回单段文件名，**不碰 `narrative_ref` 列**。

    「写在行提交之后」在这里是可守的形状：库里没有这一行就拼不出路径（404，磁盘上什么都没多）。
    这一列的写者是第四条系统腿 `KpRepository.set_narrative_ref`（`KA-30`），不在本函数里——
    文件腿与列腿分两处，端点面才能按「先落盘再落列」排判定序，读侧也仍按派生路径走（`KA-20`）。
    """
    node = KpRepository(session).get(node_id)
    if node is None:
        raise KpGateError(404, f"知识点节点不存在：{node_id}")
    path = _narrative_path(session, data_root=data_root, node=node)
    atomic_write_text(path, content)
    return path.name


def read_narrative(
    session: Session, *, data_root: Path | str, node_id: str
) -> tuple[str, str | None]:
    """长文读回（`KA-30` 的读腿）：按同一条派生式现算路径读那一篇，返回 `(单段文件名, 正文或 None)`。

    `None`＝文件不在位，端点面据此出 `exists=false` 而**不是** 404：行在而正文缺是需要被显示出来的
    矛盾，判 404 会谎报行本身。本函数既不修列也不删文件——两处都不是读腿的职权。
    读的是**文件**而不是列：列只存单段文件名，路径永远现算（`KA-20`），列值与磁盘不同步时以磁盘为准。
    """
    node = KpRepository(session).get(node_id)
    if node is None:
        raise KpGateError(404, f"知识点节点不存在：{node_id}")
    path = _narrative_path(session, data_root=data_root, node=node)
    try:
        return path.name, path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return path.name, None


def remove_node(session: Session, *, data_root: Path | str, node_id: str) -> bool:
    """删除通路（`KA-21`）：删行与清引用边在同一事务里提交，长文文件只在提交之后清。

    False＝库里没有这一行，表与磁盘都不动。清理失败只告警、不回滚也不抛：行此刻已经提交，把
    「文件删不掉」升级成「删除失败」只会让人对着一个仍在表里的正文反复重试；留下的残件正是
    `list_narrative_orphans` 该点名的东西，由「只读清单先行」的裁剪流程收口。
    """
    node = KpRepository(session).get(node_id)
    if node is None:
        return False
    path = _narrative_path(session, data_root=data_root, node=node)
    if not KpRepository(session).delete_node(node_id):
        return False
    session.commit()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("长文文件清理失败，留给只读清单点名：%s", path)
    return True


def list_narrative_orphans(session: Session, *, data_root: Path | str, book_id: str) -> list[str]:
    """孤儿只读清单（`KA-21`／database-design.md「保留与裁剪」的只读清单先行）：只点名，一个都不删。

    回指判据用 `KA-20` 的派生式而不是 `narrative_ref` 列：文件名去掉后缀就是节点身份，**行在**就不算
    孤儿——任何 `gen_status` 都算，作废的行也要留在表里读批次账面（`KA-9`/`KA-13`）。
    瞬态残留（`.tmp-`/`.old-`）不混进来：那是 `PP-6` 的识别式与告警面，处置流程与裁剪不同。
    书目不在库里、`kp/` 目录不存在都按空表算——裁剪流程要能逐书空跑一遍再拿清单去要批准。
    """
    book = BookRepository(session).get(book_id)
    if book is None:
        return []
    directory = book_dir_for(data_root, book.domain_id, book.course_id, book_id) / NARRATIVE_DIRNAME
    if not directory.is_dir():
        return []
    live = {
        node_id for (node_id,) in session.execute(select(AfKp.node_id).where(AfKp.book_id == book_id))
    }
    return [
        path.name
        for path in sorted(directory.iterdir())
        if path.is_file() and not is_transient_name(path.name) and path.stem not in live
    ]
