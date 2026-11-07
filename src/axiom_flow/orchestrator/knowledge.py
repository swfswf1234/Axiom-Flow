"""梳理作业通路：受理门与批次身份（`KA-1`/`KA-4`/`KA-10`/`KA-25`）＋两阶段执行体与锚点复核
（`KA-2`/`KA-5`/`KA-6`/`KA-7`/`KA-19`/`KA-26`/`KA-29`/`KA-33`）＋批次收尾与启动期归一（`KA-9`/`KA-13`/`KA-26`）
＋长文两条腿与删除通路（`KA-20`/`KA-21`/`KA-30`）＋批次流水的第十六键点名（`KA-27`/`KA-32`/`KA-33`）
＋书目结局回写（`KA-54`）。

设计事实源：`docs/design/knowledge-analysis.md`——本文件只把 `KA-n` 落成代码，判据正文一处不复制。
编排层不引 HTTP 类型：受理门与执行期复查抛 `KpGateError`，状态码由 API 面按 `status_code` 映射
（与 `EngineSelectionError` → 400 同一条通路）。批次流水的构造（`kp_batch_row`）、导出序与
导出面的复核标记也在本文件；流水的**发出**（`JobRunner` 的 knowledge 腿）与端点面在
`jobs.py`/`api/main.py`。
"""

from __future__ import annotations

import json
import logging
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now
from axiom_flow.db.llm_ledger import KpCallRow, LlmCallIdentity, LlmCallLedger
from axiom_flow.db.models import DOC_KIND_EXERCISE, DOC_KIND_TEXTBOOK, AfBook, AfKp, AfOperation
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
    ExerciseDraft,
    ExerciseInput,
    ExerciseItemInput,
    ExerciseReply,
    HeadingEvidence,
    KnowledgeEngine,
    OutlineEntry,
    PageBody,
    PageSample,
    RevisionContext,
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
from axiom_flow.normalize.exercise import ExerciseItem, exercise_item_key, split_exercise_items
from axiom_flow.normalize.furniture import furniture_hints, furniture_texts, strip_furniture
from axiom_flow.normalize.intents import apply_relabels, relabels_by_block
from axiom_flow.normalize.toc import (
    NODE_TYPE_CHAPTER,
    NODE_TYPE_EXERCISE,
    NODE_TYPE_OTHER,
    NODE_TYPE_POINT,
    TOC_PAGE_SCAN_LIMIT,
    TOC_ROLE_KNOWLEDGE,
    TOC_ROLE_REFERENCE,
    BodyNameIndex,
    BodyReading,
    LocatedEntry,
    TocEntry,
    anchor_block_for_name,
    clean_entry_rows,
    drop_unlocated_toc_pages,
    entry_level,
    exercise_entries,
    exercise_item_entries,
    is_exercise_label,
    locate_entries,
    node_type_of,
    node_types_of,
    page_sample_text,
    pick_in_window,
    reference_role_for_name,
    toc_entries,
)
from axiom_flow.schemas import BlocksPage, BlockType, LastCurationResult

logger = logging.getLogger(__name__)

#: 机器写出的页块锚的 `kind` 值（`anchors` 的形状见 `database-design.md`「af_kp 列清单」；
#: 根侧提升时追加的 `{kind:'af_node'}` 幂等锚由拉取方写，`KA-16`）。
KP_ANCHOR_KIND = "page_block"
#: 目录两层封顶（`KA-6`）：`depth=1` 章、`depth=2` 节，第三层不进 `af_kp`。
OUTLINE_MAX_DEPTH = 2
NODE_KIND_OUTLINE = "outline"
NODE_KIND_POINT = "point"

#: 目录行的产出者标识（`af_kp.model_ref`）：四值闭集＝模型整理出的条目（`KA-35`／`KA-37` 正腿）、
#: 确定性条目腿（同条的回落腿）、heading 块（全书零条目时的末位退路）、章末习题段的结构腿（`KA-46`）。
#: 人工改判不改本列（`KA-12`）。
OUTLINE_MODEL_REF_LLM = "llm:toc"
OUTLINE_MODEL_REF_TOC = "rule:toc"
OUTLINE_MODEL_REF_HEADING = "rule:heading"
OUTLINE_MODEL_REF_EXERCISE = "rule:exercise"
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

#: 种类 × 阶段合法组合（`KA-51`）：教材四值全开（它的习题内容由教材侧成行，此后才轮到习题集合）；
#: 习题集开 `outline`／`exercise` 两值——目录腿只给它结构（切题的父行），内容／全跑腿在零 heading
#: 的书上没有可跑的东西，判在受理面才是零调用。
KP_STAGES_BY_KIND: dict[str, frozenset[str]] = {
    DOC_KIND_TEXTBOOK: frozenset({"full", "outline", "content", "exercise"}),
    DOC_KIND_EXERCISE: frozenset({"outline", "exercise"}),
}
#: 已登记但执行腿未交付的阶段（`KA-53`，按书种类分列）：受理面暂判 422、零写入。放行会掉进执行体
#: 的 else 分支——按目录腿跑一遍并作废区间内旧候选，而目标结构一行也拿不到。两类的 `exercise`
#: 腿都已交付（教材侧 W7 规则分题、习题集侧 W9 题面／解答腿），故两行清零；本表留着是因为它是
#: 「登记与交付分列」的判据位——下一个先登记后交付的阶段仍从它挡在受理面。
UNSHIPPED_KP_STAGES: dict[str, frozenset[str]] = {
    DOC_KIND_TEXTBOOK: frozenset(),
    DOC_KIND_EXERCISE: frozenset(),
}

#: 教材侧题行的产出者标识（`KA-53`）：这一腿零模型调用，`model_ref` 是它与模型点行唯一的区分位——
#: 重跑作废面与「题行是不是这一腿的旧读数」都靠这一枚值判。
EXERCISE_ITEM_MODEL_REF = "rule:exercise_item"
#: 习题集题目腿的产出者标识（`KA-53` 的模型腿）：题行由题号规则切、题面／解答由模型读，故 `model_ref`
#: 记这一枚固定值而不是引擎名——重跑作废面认「这一腿的旧读数」，与用哪台模型无关。
EXERCISE_LEG_MODEL_REF = "llm:exercise"
#: 题包状态闭集（`KA-53`）：键名固定英文，屏上中文文案由根侧唯一常量表给——8902 只出键名。
EXERCISE_STATUS_COMPLETE = "complete"
EXERCISE_STATUS_PROBLEM_ONLY = "problem_only"
EXERCISE_STATUS_SOURCE_MISSING = "source_missing"
#: 解答缺位的题包占位行：题包三段标题恒定（`KA-53`），缺解答时这一段写这一句而不是留空。
EXERCISE_NO_SOLUTION_TEXT = "未配到解答"
#: 一轮题腿调用的正文预算（字符）：真机 b07 有一节 4.5 万字，单轮生成被
#: `finish_reason=length` 截断致整节零行；超过预算的节按题序切成多轮，题号切分仍只做一次。
EXERCISE_CHUNK_TEXT_BUDGET = 12000

#: `content` 批次的分流表（W6／C5）：四类目录行只从这一处领取生成腿——章走章腿、点走节腿，
#: 「其他」与习题段零腿即终态（习题的题级行由规则腿产出，`KA-53`／W7，不是生成腿）。
#: `full` 是 M5 的旧通路，不读本表：它按既有行为逐段发问。
CONTENT_LEGS_BY_NODE_TYPE: dict[str, frozenset[str]] = {
    NODE_TYPE_CHAPTER: frozenset({"chapter"}),
    NODE_TYPE_POINT: frozenset({"points"}),
    NODE_TYPE_EXERCISE: frozenset(),
    NODE_TYPE_OTHER: frozenset(),
}


def content_legs_for(node_type: str) -> frozenset[str]:
    """内容批次对一类目录行允许发起的生成腿；未知类型不猜，按零腿处理。"""
    return CONTENT_LEGS_BY_NODE_TYPE.get(node_type, frozenset())


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
    #: 本批是不是按失败节点重跑（`KA-52`）：名单在受理一刻从上一批流水读出，执行体不再回头看库。
    only_failed: bool = False
    #: 要重问的节点（`KA-52`）：上一批 `failed_section_details` 点名过的行；`only_failed=False` 时为空。
    retry_node_ids: tuple[str, ...] = ()
    #: 配套模式的目标教材（`KA-53`）：有值＝题行与题包写目标书，缺省＝独立模式写本书。
    target_book_id: str | None = None
    #: 习题腿选中重解析的题行（`KA-52`／`KA-53`）：载荷 `retry_node_ids` 的受理面身份。
    #: 与上面 `retry_node_ids`（上一批失败节名单）是两条通路，故各用一名，不共用同一枚字段。
    exercise_retry_node_ids: tuple[str, ...] = ()


# --------------------------------------------------------------------------- 书级三态派生（PLAN-029 W1）


@dataclass(frozen=True, slots=True)
class BookStageReading:
    """整本书的梳理完成读数（PLAN-029 W1）：四枚计数与一枚合取判据，全部读时派生、零写库。

    `done` 是**严口径**：统计域内每一行都有总结、每一行都被确认、且未定位零枚。任一条件不成立
    即 `false`；统计域零行也恒 `false`——没有可确认的行不等于「已经绿色」。
    """

    rows: int = 0
    endorsed_rows: int = 0
    blank_summaries: int = 0
    unlocated_rows: int = 0
    done: bool = False


def book_stage_reading(
    rows: Sequence[AfKp], *, toc_pages: Sequence[int] | None
) -> BookStageReading:
    """在册活行 → 整本完成读数（PLAN-029 W1）：统计域、三条件与未定位都只在这一处判。

    统计域＝`gen_status='candidate'` 的活行里读时派生 `node_type` ∈ {`chapter`, `point`} 的那些：
    `rejected` 是已被取代的旧读数、在飞 `draft` 是半成品，两者都不进统计；`exercise`／`other`
    两类附件行按 `KA-39`／`KA-43` 不产出总结，计入就使任何带章末习题的书永不可能完成。

    未定位的判据与 `KA-33` 同义：锚点非空且**全部**落在本书目录页集合内。锚为空的**人工直填行**
    不在此列——那是「没有条目光凭」，不是「条目没找到页」。`toc_pages=None` 表示目录页集合取不到：
    计数退回零枚但 `done` 恒 `false`（宁可黄、不假绿），调用方不得据此判完成。

    行集由调用方按书给出（可以是全表行，本函数自己按 `candidate` 收窄）：`node_type` 的派生要在
    同一批行上做，父行被滤掉时子行按自身身份位判，这与读面 `node_types_of` 的口径一致。
    """
    live = [row for row in rows if row.gen_status == "candidate"]
    types = node_types_of(live)
    counted = [
        row for row in live if types.get(row.node_id) in (NODE_TYPE_CHAPTER, NODE_TYPE_POINT)
    ]
    toc = None if toc_pages is None else {int(page) for page in toc_pages}
    unlocated = 0
    if toc is not None:
        for row in counted:
            pages = [
                int(anchor.get("page") or 0)
                for anchor in (row.anchors or [])
                if isinstance(anchor, dict)
            ]
            pages = [page for page in pages if page]
            if pages and all(page in toc for page in pages):
                unlocated += 1
    blank = sum(1 for row in counted if not (row.summary or "").strip())
    endorsed = sum(1 for row in counted if row.confirmed)
    return BookStageReading(
        rows=len(counted),
        endorsed_rows=endorsed,
        blank_summaries=blank,
        unlocated_rows=unlocated,
        done=(
            bool(counted)
            and toc is not None
            and endorsed == len(counted)
            and blank == 0
            and unlocated == 0
        ),
    )


def book_toc_pages(session: Session, *, book_id: str, job_id: str | None) -> tuple[int, ...] | None:
    """本书目录页集合（PLAN-029 W1 的未定位判据输入）：取该取证版本最近一批带目录读数的流水。

    目录页集合是某一版页产物的派生读数，不是「当前态行」，所以可以取自批次流水：只认 `job_id`
    与本版一致的那几批，取不到就给 `None`——旧版本的目录页集合拿来判新版本的行只会误判。
    """
    if not job_id:
        return None
    for row in reversed(OperationRepository(session).list_kp_batches(book_id)):
        if row.job_id != job_id:
            continue
        pages = (row.result or {}).get("outline_toc_pages")
        if isinstance(pages, list):
            kept = tuple(sorted({int(page) for page in pages if isinstance(page, int)}))
            if kept:
                return kept
    return None


def accept_kp_job(
    session: Session,
    *,
    book_id: str,
    page_start: int | None = None,
    page_end: int | None = None,
    review_note: str = "",
    stage: str = "full",
    only_failed: bool = False,
    target_book_id: str | None = None,
    exercise_retry_node_ids: Sequence[str] = (),
) -> KpAcceptance:
    """受理门：所有门槛全判在写入之前，通过才发批次号（`KA-1`/`KA-4`/`KA-10`/`KA-25`/`KA-43`/`KA-51`）。

    顺序固定：书目（404）→ 生效版本（422）→ 页区间（422）→ 在册目录（422，`content` 与教材的
    `exercise`）→ 同书在飞（409）→ 书种类（422，未声明）→ 种类 × 阶段闭集（422）。把在飞判据排在
    种类之前是因为它要读 `af_kp`，而在册目录门也读 `af_kp`、排在页区间之后：页区间先判，读侧就不会
    为一枚越界请求去翻整册行。七道门都不产生写入。

    `content` 的在册目录门（`KA-43`）不是完备性检查而是**语义前提**：这一值不跑目录腿、直接拿在册行
    切区间，零枚在册行时每一枚点都只能进池（`KA-7`），人工拿到的是一份平铺的假产出。判据只认
    `gen_status='candidate'`——`draft` 是在飞读数、`rejected` 是已被取代的旧读数，两者都切不出可信骨架。
    教材的 `exercise`（`KA-53`）同一条前提：题行的父必须是既有习题段行。

    两道种类门（`KA-51`）排在既有五道之后：未声明种类是**书的当前态缺失**而不是并发冲突，本侧
    不照抄根侧「空即教材」的容错——放行等于让每一本未同步的书白跑一轮目录与逐节调用，产出的行
    也没有一条能与习题集合并。种类 × 阶段闭集同样判在这里：习题集跑 `outline`／`content` 会在零
    heading 的书上白付判页与整理，判在受理面才谈得上「零调用、零写入」。

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
    # 习题腿（`KA-53`）的输入是既有目录行：教材侧要习题段行当父，习题集侧要章／节行切区间——
    # 没有在册目录，两边都只能落出一地悬父或一段无父区间。判据只认在册（`candidate`）行。
    if stage == "exercise" and not _registered_outline_rows(session, book_id):
        raise KpGateError(
            422, f"习题梳理不受理：{book_id} 还没有在册目录行，先解析目录（`KA-53`，零写入）"
        )

    in_flight = KpRepository(session).find_draft_batch(book_id)
    if in_flight is not None:
        raise KpGateError(409, f"该书已有在飞梳理批次：{book_id} 的 {in_flight}（同书至多一个）")

    kind = book.doc_kind
    if not kind:
        raise KpGateError(
            422,
            f"梳理不受理：{book_id} 未声明书种类（同步行缺 roles 或落不出两值），"
            f"先同步书目（`KA-51`，零写入）",
        )
    if stage not in KP_STAGES_BY_KIND.get(kind, frozenset()):
        raise KpGateError(
            422,
            f"阶段与书种类组合非法：{book_id} 是 {kind}，不受理 stage={stage}"
            f"（合法值：{'/'.join(sorted(KP_STAGES_BY_KIND[kind]))}，`KA-51`，零写入）",
        )
    if stage in UNSHIPPED_KP_STAGES.get(kind, frozenset()):
        raise KpGateError(
            422, f"梳理阶段尚未交付：{book_id} 的 stage={stage} 暂不受理（零写入）"
        )

    # 按失败节点重跑（`KA-52`）：名单只有 `content` 这一条腿产得出，故组合门先判；名单在受理一刻
    # 从上一批流水读出并随批次身份带走——执行期不再回读，免得下一批的账面把本批的范围改写。
    retry_node_ids: tuple[str, ...] = ()
    if only_failed:
        if stage != "content":
            raise KpGateError(
                422,
                f"按失败节点重跑只与 content 组合：{book_id} 收到 stage={stage}（`KA-52`，零写入）",
            )
        retry_node_ids = _last_failed_section_nodes(session, book_id)
        if not retry_node_ids:
            raise KpGateError(
                422,
                f"按失败节点重跑不受理：{book_id} 的上一批没有失败节（`KA-52`，零写入）",
            )

    # 配套模式（`KA-53`）：目标书由人指定，只许与习题集的习题腿同批——教材带它即「让教材去合并
    # 别人的题」，独立模式带它即自相矛盾，两种都在受理面拒。修补批次可以带目标：配套模式的题行
    # 落在目标教材里，选中题重解析必须知道改哪本书的行。
    selected = tuple(dict.fromkeys(str(node_id) for node_id in exercise_retry_node_ids))
    if target_book_id is not None:
        if kind != DOC_KIND_EXERCISE or stage != "exercise":
            raise KpGateError(
                422,
                f"目标教材只与习题集的习题梳理同批：{book_id} 是 {kind or '未声明'}、"
                f"stage={stage}（`KA-53`，零写入）",
            )
        target = BookRepository(session).get(target_book_id)
        if target is None:
            raise KpGateError(422, f"目标教材不存在：{target_book_id}（`KA-53`，零写入）")
        if target.doc_kind != DOC_KIND_TEXTBOOK:
            raise KpGateError(
                422,
                f"目标必须是有教材身份的书的：{target_book_id} 的 doc_kind={target.doc_kind or '未声明'}"
                "（`KA-53`，零写入）",
            )
        target_in_flight = KpRepository(session).find_draft_batch(target_book_id)
        if target_in_flight is not None:
            raise KpGateError(
                409,
                f"目标教材已有在飞梳理批次：{target_book_id} 的 {target_in_flight}"
                "（同书至多一个，`KA-53`）",
            )
        # 整本绿门（`KA-53`）：习题集的题要挂到教材已审过的结构上，目标还没绿时合并只能产出
        # 一批没人确认过的读数。判据复用 PLAN-029 W1 的纯函数，不在这里另写一份三条件。
        # 修补不是重发首轮（同条）：选中的题行已经在目标书里，重解析只覆盖它们，不新挂章。
        reading = book_stage_reading(
            KpRepository(session).list_for_book(target_book_id),
            toc_pages=book_toc_pages(
                session, book_id=target_book_id, job_id=target.active_job_id
            ),
        )
        if not selected and not reading.done:
            raise KpGateError(
                422,
                f"配套模式不受理：目标教材 {target_book_id} 尚未整本绿"
                f"（在册 {reading.rows} 行／已确认 {reading.endorsed_rows}／"
                f"空总结 {reading.blank_summaries}／未定位 {reading.unlocated_rows}；"
                "`KA-53`，零写入）",
            )

    # 选中题重解析（`KA-52`／`KA-53`）：只与习题腿组合；逐枚校验「行在目标书 ∧ 来源书＝本书 ∧
    # 未确认」排在受理面，任一不成立 422 零写入。目标书缺省时先认本书（独立模式），认不出再认
    # 本书最近一批习题流水的目标（配套模式的既有归属）。
    retry_owner: str | None = None
    if selected:
        if stage != "exercise":
            raise KpGateError(
                422,
                f"选中题重解析只与习题梳理组合：{book_id} 收到 stage={stage}（`KA-52`，零写入）",
            )
        if len(selected) > 200:
            raise KpGateError(
                422, f"选中题重解析一次至多 200 枚：{book_id} 收到 {len(selected)} 枚（零写入）"
            )
        retry_owner = _resolve_exercise_retry_owner(
            session,
            book_id=book_id,
            target_book_id=target_book_id,
            selected=selected,
        )
        if retry_owner != book_id and target_book_id is None:
            # 归属书是推导出来的（本次没带目标）：在飞判据不能因为「人没填」而漏掉目标书。
            target_in_flight = KpRepository(session).find_draft_batch(retry_owner)
            if target_in_flight is not None:
                raise KpGateError(
                    409,
                    f"目标教材已有在飞梳理批次：{retry_owner} 的 {target_in_flight}"
                    "（同书至多一个，`KA-53`）",
                )
        if retry_owner == book_id:
            retry_owner = None

    return KpAcceptance(
        book_id=book_id,
        evidence_job_id=evidence_job_id,
        gen_batch=new_batch_id(),
        page_start=start,
        page_end=end,
        review_note=review_note,
        stage=stage,
        only_failed=only_failed,
        retry_node_ids=retry_node_ids,
        target_book_id=retry_owner or target_book_id,
        exercise_retry_node_ids=selected,
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
    rescued: frozenset[int] = frozenset(),
) -> dict[int, tuple[int, int]]:
    """核对腿（`KA-38`）：偏移定锚查不中的坏行攒一批一次问，只改「落在哪一页」。

    三条零调用：偏移标定不出（那时「全部未定位」是标定失败的读数，问模型等于让模型替机器编页码）、
    没有坏行、引擎不持这条腿。坏行里只取**带印刷页码**的那些——没有页码就没有目标页，候选面只能是全库。
    容错腿（`KA-49`）已救回的行也不交模型：那条腿排在核对之前，两遍都不中的才叫坏行。
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
        if (printed := entry.printed_page) is not None
        and not located_by_index[index].located
        and index not in rescued
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


def _exercise_readings(
    readings: Sequence[OutlineReading],
    pages_by_no: Mapping[int, BlocksPage],
    *,
    toc_pages: set[int] | None = None,
) -> dict[int, list[OutlineReading]]:
    """章末习题段成行（`KA-46`）：每章在**自己那一棵子树**的页区间里找整块习题标签。

    区间口径与点腿同一条树序算式：章管到「下一条同级或更浅行之前」。候选页只认本批已读到正文的页
    （取证集里没有的页不造结构结论，`KA-2` 同轴），目录页与「本页已有一枚习题行」的页整页跳过——
    后者是重跑与模型正腿撞车的去重口，不靠 `norm_key`：同一段的名字可能是 `习题 1.1` 也可能是 `习题`。
    认不出的书出零行；本腿不发一次模型调用、不写 `summary`、不改任何产物。

    返回值按「插入点」寻址：键＝该章子树末行在 `readings` 里的下标，值＝顺序排好的习题行；
    这样合并时插在下一章之前、且与同一章的节保持书序。
    """
    skipped = set(toc_pages or ())
    taken = {
        row.page_no
        for row in readings
        if row.kind == "exercise" or is_exercise_label(row.text)
    }
    found: dict[int, list[OutlineReading]] = {}
    last_page = max(pages_by_no, default=0)
    for index, row in enumerate(readings):
        if row.level > 1 or row.role != TOC_ROLE_KNOWLEDGE or row.page_no in skipped:
            continue
        end_index = len(readings) - 1
        end_page = last_page
        for later in range(index + 1, len(readings)):
            if readings[later].level <= 1:
                end_index = later - 1
                end_page = readings[later].page_no - 1
                break
        produced: list[OutlineReading] = []
        for page_no in range(row.page_no, end_page + 1):
            if page_no in skipped or page_no in taken:
                continue
            page = pages_by_no.get(page_no)
            if page is None:
                continue
            for entry in exercise_entries(page):
                if page_no in taken:
                    break
                taken.add(page_no)
                produced.append(
                    OutlineReading(
                        page_no=page_no,
                        block_index=entry.block_index,
                        level=2,
                        text=entry.name,
                        model_ref=OUTLINE_MODEL_REF_EXERCISE,
                        role=TOC_ROLE_REFERENCE,
                        kind="exercise",
                    )
                )
        if produced:
            found[end_index] = produced
    return found


def _merge_exercise_readings(
    readings: Sequence[OutlineReading], found: Mapping[int, list[OutlineReading]]
) -> list[OutlineReading]:
    """把习题行插到它所属那一章子树的末尾（书序＝节之后、下一章之前）。"""
    if not found:
        return list(readings)
    merged: list[OutlineReading] = []
    for index, row in enumerate(readings):
        merged.append(row)
        merged.extend(found.get(index, ()))
    return merged


def _outline_identity(model_ref: str, name: str) -> tuple[str, str]:
    """目录一行的身份位 `(toc_role, toc_kind)`（`KA-39`／`KA-46`）。

    两趟（逐页早落与定稿重算）必须给出同一份身份位：同批改写面只有锚、序、挂父三列，
    身份一旦两趟不同就会永久留在行里。习题标签先判——正腿与回落腿都可能抄进同一枚「习题」，
    它归附件／习题而不是知识点；其余按来路：条目腿按名字集合判，正腿的名字只判是不是习题。
    """
    if is_exercise_label(name):
        return TOC_ROLE_REFERENCE, "exercise"
    if model_ref == OUTLINE_MODEL_REF_TOC:
        return reference_role_for_name(name)
    return TOC_ROLE_KNOWLEDGE, ""


def _anchor_from_body(
    index: BodyNameIndex,
    entries: Sequence[TocEntry],
    located: Sequence[LocatedEntry],
    offset: int | None,
) -> tuple[dict[int, BodyReading], dict[int, str]]:
    """容错回定位与「名以正文为准」一处给齐（`KA-49`／`KA-50`，两趟成行共用）。

    两把读数同进同出不是省代码，是因为它们读同一份候选行：位置腿只在第一遍查不中的条目上给锚，
    且照旧受窗口约束；名字腿**不读偏移也不读窗口**，逐页趟与定稿趟因此必然给同一份名字
    （`KA-40` 的改写面只有锚、序、挂父三列，名字跟着趟变就会永久留在行里）。名字腿对第一遍已命中的
    行同样生效——真机那枚 `第5章 微分法` 正是前缀命中却带 CJK 间空格的脏名，正文那一行才是书自己
    写的拼法。
    """
    rescued: dict[int, BodyReading] = {}
    names: dict[int, str] = {}
    for position, entry in enumerate(entries):
        readings = index.find(entry.name)
        if not readings:
            continue
        names[position] = readings[0].name
        if located[position].located:
            continue
        reading = pick_in_window(readings, printed_page=entry.printed_page, offset=offset)
        if reading is not None:
            rescued[position] = reading
    return rescued, names


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
    cached_index: BodyNameIndex | None = None

    def body_index() -> BodyNameIndex:
        """正文候选行的索引（`KA-49` 的输入面）：取证面定了才建，整本扫一遍、逐条查询走字典。

        懒建不会读错面：逐页落行回调在 `body_pages` 剔掉目录页**之后**才触发，而定稿重算沿用同一绑定；
        整理零行走整本回落时回调从未触发，索引到那一刻仍没建，那一趟按新的正文面建第一次。
        """
        nonlocal cached_index
        if cached_index is None:
            cached_index = BodyNameIndex(body_pages)
        return cached_index

    def land_page(current: list[tuple[int, TocEntry, str]], start: int) -> None:
        if sink is None or len(current) == start:
            return
        rows = [entry for _, entry, _ in current]
        location = locate_entries(rows, body_pages)
        rescued, body_names = _anchor_from_body(body_index(), rows, location.entries, location.offset)
        batch: list[OutlineNode] = []
        for index in range(start, len(current)):
            toc_page, entry, model_ref = current[index]
            item = location.entries[index]
            reading = rescued.get(index)
            if item.located and item.page_no is not None:
                page_no, block_index = item.page_no, item.block_index
            elif reading is not None:
                page_no, block_index = reading.page_no, reading.block_index
            else:
                page_no, block_index = toc_page, item.block_index
            text = body_names.get(index, entry.name)
            role, kind = _outline_identity(model_ref, text)
            node = early.add(
                OutlineReading(
                    page_no=page_no,
                    block_index=block_index,
                    level=entry.level,
                    text=text,
                    model_ref=model_ref,
                    role=role,
                    kind=kind,
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
    # 容错腿（`KA-49`／`KA-50`）只补两样：救回的行给锚，命中正文行的行换名。
    # 它既不进偏移标定也不进页级复核——上面那段复核用的仍是第一遍的读数。
    rescued, body_names = _anchor_from_body(
        body_index(), [entry for _, entry, _ in entries], location.entries, location.offset
    )
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
    anchors = _verify_outline_entries(
        engine, entries, located_by_index, location.offset, body_pages, frozenset(rescued)
    )
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
        elif (saved := rescued.get(index)) is not None:
            page_no, block_index = saved.page_no, saved.block_index
        else:
            page_no, block_index = toc_page, item.block_index
            unlocated.append(entry.name)
        # 身份位的来源分两路（KA-39）：正腿的行由模型判页给（附件页另成行），回落腿只能按名字集合判。
        # 习题段是第三路（KA-46）：正腿的行只要名字是整块习题标签就归附件／习题——模型只判页，不判类别。
        # 名字以正文为准（KA-50）：脏名在源头换成净名，`norm_key` 与身份位都从净名走。
        text = body_names.get(index, entry.name)
        role, kind = _outline_identity(model_ref, text)
        readings.append(
            OutlineReading(
                page_no=page_no,
                block_index=block_index,
                level=entry.level,
                text=text,
                model_ref=model_ref,
                role=role,
                kind=kind,
                entry_index=index,
            )
        )

    reference_readings = _reference_readings(pick, pages_by_no)
    if readings or reference_readings:
        # 排在所有知识点行之后的才算正文后附件页，其余一律进章序之前——符号表在正文前但在目录页之后。
        # 尾巴只按 `knowledge` 行算：目录里自己就列着书尾的「参考书目／习题」，把它们算进来会
        # 把正文后的附件页整批判成「正文前」——b05 第 11 章习题页与参考书目因此排到了第 1 章之前。
        knowledge_pages = [
            reading.page_no for reading in readings if reading.role == TOC_ROLE_KNOWLEDGE
        ]
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

    # 章末习题段成行（`KA-46`）：确定性规则、零模型调用，插在各自章的子树末尾。
    readings = _merge_exercise_readings(
        readings, _exercise_readings(readings, pages_by_no, toc_pages=set(toc_pages))
    )

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
    """切区间的一枚输入行：排序键 `(order_hint, page)` ＋ 身份 `(node_id, name)` ＋ 树位 `(depth, toc_role, toc_kind)`。

    点腿给的是本批定形的目录行，读面给的是库里的在册目录行——两处形状不同，算式必须同一处
    （`KA-42`）：屏上圈的区间要与模型实际读过的那一段逐字对得上。树位只参与终点判（DEFECT-028），
    缺省 `depth=1`／`toc_role=knowledge`／`toc_kind=''` 即「平铺目录」，与改判前的读数逐字相同；
    `toc_kind` 只用来把习题段从附件行的单页退化里摘出来（`KA-46`）。
    """

    node_id: str | None
    name: str
    order_hint: int | None
    page: int
    depth: int = 1
    toc_role: str = "knowledge"
    toc_kind: str = ""


def section_spans_of(
    ordered_pages: Sequence[SpanRow], *, first_page: int, last_page: int
) -> list[SectionSpan]:
    """按目录行的树序切区间：每行管「它自己那页起、到下一条同级或更浅行之前」（DEFECT-028）。

    终点取**树序**而不是「下一条行」：章与其首节同页，按后者算章只拿到一页（b05 第1章 p9 与「导引」
    同页 ⇒ `[9,9]`），而需求方要的是 p9–28（第2章在 p29）。同级或更浅的下一行才是这一棵子树的边界。
    两条目录行挤在同一页时后一条的区间为空，仍按它自己那页取——宁可两节共读一页，也不让正文没有
    归属（那会往 `KA-7` 的池子里混进本该挂父的点）。`toc_role='reference'` 的行（符号表、索引、书目）
    只发起点＝终点：它们的物理正文不在目录条目所在页，按树序拼出去只会造出跨章巨区间（「重要符号表」
    曾读成 `5-310`），不精确处如实披露不猜。**习题段是这条退化唯一的例外**（`KA-46`）：它的正文就从
    标题页起向下延伸到本章末，照单页发会让练习面只抽到标题那一页，故按知识行的树序算式给真区间。
    目录零产出（引擎认不出任何标题）时整段正文自成一段：
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
        if row.toc_role == TOC_ROLE_REFERENCE and row.toc_kind != "exercise":
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


def _last_failed_section_nodes(session: Session, book_id: str) -> tuple[str, ...]:
    """上一批 `content` 流水点名过的失败节点（`KA-52`）：只认最近一批，不回退到更早的批次。

    节点身份只在 `failed_section_details[].node_id` 里：标题会重名、页区间会跨节点，两者都不能当
    名单。最近一批没有 `result`（异常腿）或明细里零节点时回空表——「上一批」就是最近那一批，
    跳过它去翻旧账等于拿一批已经过时的失败名单重跑。
    """
    for row in reversed(OperationRepository(session).list_kp_batches(book_id)):
        payload = row.payload or {}
        if payload.get("stage") != "content":
            continue
        details = (row.result or {}).get("failed_section_details")
        if not isinstance(details, list):
            return ()
        return tuple(
            str(item.get("node_id"))
            for item in details
            if isinstance(item, dict) and item.get("node_id")
        )
    return ()


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
                toc_kind=str((node.row.meta or {}).get("toc_kind") or ""),
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


DETAIL_MASTERY_LIMIT = 6
DETAIL_MASTERY_CHARS = 120
DETAIL_DEFINITION_ROWS = 8
DETAIL_TERM_CHARS = 120
DETAIL_DEFINITION_CHARS = 500


def _detail_reads(draft: Mapping[str, Any]) -> dict[str, Any]:
    """详情两枚读数的净化（`KA-57`）：坏条目丢弃，不牵连行本身。

    这里的上限是挡离谱值的闸，不是质量判据——提示词要得更紧（掌握要点每条 60 字、术语 30 字、
    定义 120 字），越界但语义完整的内容照样保留，只截到上限。
    """
    raw_master = draft.get("must_master")
    mastery: list[str] = []
    if isinstance(raw_master, list):
        for item in raw_master:
            if isinstance(item, str) and item.strip():
                mastery.append(item.strip()[:DETAIL_MASTERY_CHARS])
                if len(mastery) >= DETAIL_MASTERY_LIMIT:
                    break
    raw_definitions = draft.get("key_definitions")
    definitions: list[dict[str, str]] = []
    if isinstance(raw_definitions, list):
        for item in raw_definitions:
            if not isinstance(item, dict):
                continue
            term = item.get("term")
            definition = item.get("definition")
            if not isinstance(term, str) or not isinstance(definition, str):
                continue
            if not term.strip() or not definition.strip():
                continue
            definitions.append(
                {
                    "term": term.strip()[:DETAIL_TERM_CHARS],
                    "definition": definition.strip()[:DETAIL_DEFINITION_CHARS],
                }
            )
            if len(definitions) >= DETAIL_DEFINITION_ROWS:
                break
    reads: dict[str, Any] = {}
    if mastery:
        reads["must_master"] = mastery
    if definitions:
        reads["key_definitions"] = definitions
    return reads


def _point_row(
    draft: dict[str, Any], acceptance: KpAcceptance, engine_name: str, parent_id: str | None
) -> KpNodeRow | None:
    """形状校验先于写库：缺 `name`／`anchors` 为空／闭集外值 ⇒ 返回 `None`，由调用方计数（不写）。

    详情两枚读数（`KA-57`）不参与这道闸：坏条目在 `_detail_reads` 里逐条丢弃，点行照写。
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
        meta=_detail_reads(draft) or None,
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
    *,
    retry_node_ids: Sequence[str] | None = None,
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

    `retry_node_ids`（`KA-52`）是按失败节点重跑的发起面：给了名单就只对名单内的节点发问，名单外的
    节（含未归属区间）一条不发——它们是上一批已经成功的读数。取材切片仍按完整目录树算，名单里的
    节点拿到的还是它自己那一段完整正文，不是缩到锚点页。
    """
    repo = KpRepository(session)
    retry_nodes = set(retry_node_ids) if retry_node_ids is not None else None
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
        if retry_nodes is not None and span.parent_id not in retry_nodes:
            continue
        if span.parent_id in non_knowledge:
            continue
        if content_only and "points" not in content_legs_for(node_types.get(span.parent_id or "", "")):
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
                    # 节点身份（`KA-52`）：按失败节点重跑的唯一名单来源；未归属区间的节没有父行，落空串。
                    "node_id": span.parent_id or "",
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


# --------------------------------------------------------------------------- 教材侧习题分题（KA-53）


@dataclass(frozen=True, slots=True)
class ExerciseItemBuild:
    """分题腿的读数（`KA-53`）：成出的点层行数；无题号的习题段只计数（披露，不是失败）。"""

    item_rows: int = 0
    empty_segments: int = 0


def _exercise_item_lines(
    pages: Sequence[BlocksPage], span: SectionSpan
) -> list[tuple[int, int, str]]:
    """一段习题的候选行：只吃正文块（段落／列表），逐行带出页号与块下标（`KA-18` 的锚点空间）。

    页眉页脚页码与标题块不进候选：b07 每一页的页脚都以 `1.` 开头，章标题与正文题行又在同一份块
    序列里，靠块型维先剔掉才谈得上「行首题号」。段落块按换行拆行、列表块按 `items` 逐项拆行——
    两处都落回同一个块下标：锚点指向块，不编行偏移。
    """
    lines: list[tuple[int, int, str]] = []
    for page in sorted(pages, key=lambda item: item.page):
        if not span.page_start <= page.page <= span.page_end:
            continue
        for block in sorted(page.blocks, key=lambda item: (item.order, item.index)):
            if block.type is BlockType.PARAGRAPH:
                lines.extend(
                    (page.page, block.index, line) for line in (block.text or "").splitlines()
                )
            elif block.type is BlockType.LIST:
                lines.extend((page.page, block.index, line) for line in (block.items or []))
    return lines


def extract_exercise_items(
    session: Session,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
) -> ExerciseItemBuild:
    """教材侧习题分题（`KA-53`）：按既有习题段行逐段扫正文、按行首题号成点层行。

    零模型调用、零文件：题号闭集与同父单调序都在 `normalize.toc.exercise_item_entries`，本函数只把
    命中行变成 `af_kp` 的行——父＝该习题段行（`KA-46` 的真区间，不按附件行退化成单页）、`name`＝
    书自己写的题号、`summary` 空串、`meta.toc_kind='exercise'`（读面据此派生 `node_type='exercise'`）、
    `model_ref` 固定为本腿的产出者标识。单调序按段重置：两章各自的「1.」都是各自的第一题，跨段比较
    会把后一章整段判成回退。

    一段里一枚题号也认不出时该段零行：这是披露而不是失败——拼一枚假题号比空着更难复核。`entry.index`
    是本段候选行序列的下标，故行与页／块的对偶始终成立。
    """
    repo = KpRepository(session)
    exercises = {
        node.row.node_id
        for node in outline_nodes
        if str((node.row.meta or {}).get("toc_kind") or "") == "exercise"
    }
    if not exercises:
        return ExerciseItemBuild()
    item_rows = empty_segments = 0
    for span in _section_spans(outline_nodes, evidence):
        if span.parent_id not in exercises:
            continue
        lines = _exercise_item_lines(evidence.blocks, span)
        entries = exercise_item_entries([line for _, _, line in lines])
        if not entries:
            empty_segments += 1
            continue
        for ordinal, entry in enumerate(entries, start=1):
            page_no, block_index, line = lines[entry.index]
            repo.create(
                KpNodeRow(
                    node_id=new_node_id(),
                    book_id=acceptance.book_id,
                    job_id=acceptance.evidence_job_id,
                    node_kind=NODE_KIND_POINT,
                    parent_id=span.parent_id,
                    order_hint=ordinal,
                    name=entry.item_no,
                    norm_key=norm_key_for(entry.item_no),
                    summary="",
                    anchors=[_page_anchor(page_no, block_index, line)],
                    gen_status="draft",
                    gen_batch=acceptance.gen_batch,
                    model_ref=EXERCISE_ITEM_MODEL_REF,
                    meta={"toc_kind": "exercise"},
                )
            )
            item_rows += 1
        session.commit()
    return ExerciseItemBuild(item_rows=item_rows, empty_segments=empty_segments)


# --------------------------------------------------------------------------- 习题集题目腿（KA-53）


@dataclass(frozen=True, slots=True)
class ExerciseBookBuild:
    """习题集题目腿的读数（`KA-53`）：题行、题包、未匹配解答与失败节。

    `statuses` 是 `(node_id, status)` 的对偶清单，只为章级索引的派生视图服务（状态不落库、不进
    `summary`——题面与解答正文都在题包文件里）。`empty_segments`／`orphan_segments` 是披露位：
    前者＝一段里题号一枚也没认出，后者＝那段正文不在目录树上、没有可挂的父行。
    """

    item_rows: int = 0
    pack_rows: int = 0
    empty_segments: int = 0
    orphan_segments: int = 0
    failed_sections: int = 0
    failed_section_details: tuple[dict[str, Any], ...] = ()
    unmatched_solutions: int = 0
    unmatched_solution_details: tuple[dict[str, Any], ...] = ()
    statuses: tuple[tuple[str, str], ...] = ()
    #: 配套模式下与教材既有题行命中的枚数（`KA-53`）：命中的行只补空位，不算「新增」。
    merged_rows: int = 0


def exercise_pack_text(
    *,
    label: str,
    problem: str,
    solution: str,
    source_lines: Sequence[str],
) -> str:
    """题包正文（`KA-53`）：固定三段标题，题号作一级标题。

    三段恒定是界面契约：屏上题框按这两枚标题切「题目」与「解答」两栏，缺哪一段也不能整段消失——
    缺解答写占位句、缺题面写指针句，读侧永远拿得到同一副骨架。`source_lines` 由两模式各自给：
    独立模式只有本书一册，配套模式是教材与习题集两行。
    """
    return "\n".join(
        [
            f"# {label}".rstrip(),
            "",
            "## 题目",
            "",
            problem.strip(),
            "",
            "## 解答",
            "",
            solution.strip() or EXERCISE_NO_SOLUTION_TEXT,
            "",
            "## 来源",
            "",
            *[line for line in source_lines if line.strip()],
            "",
        ]
    )


def _exercise_status(problem: str, solution: str) -> str:
    """题包状态（`KA-53`）：题面缺＝`source_missing`，只缺解答＝`problem_only`，两者齐＝`complete`。"""
    if not problem.strip():
        return EXERCISE_STATUS_SOURCE_MISSING
    if not solution.strip():
        return EXERCISE_STATUS_PROBLEM_ONLY
    return EXERCISE_STATUS_COMPLETE


def _exercise_page_span(pages: Sequence[int]) -> str:
    """页区间的印刷形：单页 `p.7`，跨页 `p.7–9`。"""
    kept = sorted({int(page) for page in pages if page})
    if not kept:
        return ""
    if len(kept) == 1:
        return f"p.{kept[0]}"
    return f"p.{kept[0]}–{kept[-1]}"


def _exercise_anchors(item: ExerciseItem) -> list[dict[str, Any]]:
    """题行锚点：题号页带原文片段，跨页再补一枚末页锚（同书同版，`KA-18`）。

    锚点只指题目的页——解答页在独立模式下与本行同书，故可作第二枚；配套模式的解答页属另一册，
    只进 `meta.source_pages`，不是锚点。
    """
    anchors = [_page_anchor(item.page_start, None, item.text[:200])]
    if item.page_end != item.page_start:
        anchors.append(_page_anchor(item.page_end, None, ""))
    return anchors


def _exercise_rounds(items: Sequence[ExerciseItem]) -> list[list[ExerciseItem]]:
    """按题序把一节切成调用轮：累计正文超过 `EXERCISE_CHUNK_TEXT_BUDGET` 即开下一轮。

    单题自身超预算时独占一轮——切轮只为不把大节整节压进一次生成，不为改写题界。
    """
    rounds: list[list[ExerciseItem]] = []
    current: list[ExerciseItem] = []
    size = 0
    for item in items:
        if current and size + len(item.text) > EXERCISE_CHUNK_TEXT_BUDGET:
            rounds.append(current)
            current, size = [], 0
        current.append(item)
        size += len(item.text)
    if current:
        rounds.append(current)
    return rounds


def _exercise_section_reply(
    engine: KnowledgeEngine, span: SectionSpan, pages: Sequence[PageBody]
) -> tuple[tuple[ExerciseItem, ...], ExerciseReply, tuple[dict[str, Any], ...]]:
    """一节的分题与模型回复（`KA-53`）：规则先定界，界内零题时一次调用也不发。

    分题权只在 `normalize.exercise`；模型拿到的只是「这一节有这几道题、题号是这些」。
    一节正文超预算时按题序分轮调用，失败域仍是节：某一轮失败只让这一轮的点缺项、进披露，
    其余轮的草稿照常返回；全部轮都失败才把整节判失败（与单轮失败同形，零行）。
    """
    items = split_exercise_items([(page.page_no, page.markdown) for page in pages])
    if not items:
        return (), ExerciseReply(), ()
    rounds = _exercise_rounds(items)
    drafts: list[ExerciseDraft] = []
    failures: list[dict[str, Any]] = []
    for turn, chunk in enumerate(rounds, start=1):
        try:
            reply = engine.exercise(
                ExerciseInput(
                    title=span.title,
                    page_start=chunk[0].page_start,
                    page_end=chunk[-1].page_end,
                    items=tuple(
                        ExerciseItemInput(
                            item_no=item.item_no,
                            label=item.label,
                            page_start=item.page_start,
                            page_end=item.page_end,
                            text=item.text,
                        )
                        for item in chunk
                    ),
                )
            )
        except EngineError as exc:
            if len(rounds) == 1:
                raise
            failures.append(
                {
                    "node_id": span.parent_id,
                    "title": span.title or "目录未覆盖区间",
                    "page_start": chunk[0].page_start,
                    "page_end": chunk[-1].page_end,
                    "reason": f"本节第 {turn}/{len(rounds)} 轮：{str(exc)[:200]}",
                }
            )
            continue
        drafts.extend(reply.items)
    if not drafts and failures:
        raise EngineError(failures[0]["reason"])
    return items, ExerciseReply(tuple(drafts)), tuple(failures)


def _group_exercise_drafts(
    items: Sequence[ExerciseItem], reply: ExerciseReply, span: SectionSpan
) -> tuple[dict[str, ExerciseDraft], list[dict[str, Any]]]:
    """模型回的草稿按题号折键（`KA-53`）：多回、重复、界外的题号只进披露，不落行不写包。"""
    split_keys = {item.item_no for item in items}
    by_key: dict[str, ExerciseDraft] = {}
    unmatched: list[dict[str, Any]] = []
    for draft in reply.items:
        key = exercise_item_key(draft.item_no)
        if key is None:
            reason = "模型回的题号认不出"
        elif key in by_key:
            reason = "题号重复"
        elif key not in split_keys:
            reason = "题号不在本节切分内"
        else:
            by_key[key] = draft
            continue
        if draft.problem.strip() or draft.solution.strip():
            unmatched.append(
                {
                    "item_no": draft.item_no,
                    "source_pages": [span.page_start, span.page_end],
                    "reason": reason,
                }
            )
    return by_key, unmatched


def _exercise_failure_detail(span: SectionSpan, exc: EngineError) -> dict[str, Any]:
    """一段调用失败的披露项（与知识点逐节同形）：点名父行、区间与原因。"""
    return {
        "node_id": span.parent_id,
        "title": span.title or "目录未覆盖区间",
        "page_start": span.page_start,
        "page_end": span.page_end,
        "reason": str(exc)[:200],
    }


def _exercise_problem_text(problem: str, *, book_title: str, pages: Sequence[int]) -> str:
    """题面段的正文（`KA-53`）：读不到正文时写一行指针，不留整段空白。

    屏上的题框按两枚标题切两栏；题面整段空白会被读成「这一题没有题面」，看不出「正文取不到」。
    """
    text = (problem or "").strip()
    if text:
        return text
    span = _exercise_page_span(pages)
    return f"见《{book_title}》{span}" if span else f"见《{book_title}》"


def _write_exercise_pack(
    session: Session,
    *,
    data_root: Path | str,
    node_id: str,
    label: str,
    problem: str,
    solution: str,
    source_lines: Sequence[str],
) -> bool:
    """题包落盘＋指针回写（`KA-20`／`KA-30`）：先写文件，成功才回写 `narrative_ref`。"""
    try:
        name = write_narrative(
            session,
            data_root=data_root,
            node_id=node_id,
            content=exercise_pack_text(
                label=label,
                problem=problem,
                solution=solution,
                source_lines=source_lines,
            ),
        )
    except (KpGateError, OSError) as exc:
        logger.warning("题包落盘失败：%s（%s）", node_id, exc)
        return False
    return KpRepository(session).set_narrative_ref(node_id, name).ok


# --------------------------------------------------------------- 题包索引与习题读面（KA-53／W16）

#: 题包索引目录与来源书清单的文件名：都在 `kp/` 下，逐本删除天然覆盖（不另建清理腿）。
EXERCISE_INDEX_DIRNAME = "exercises"
EXERCISE_INDEX_META_NAME = "meta.json"


def exercise_pack_status(text: str) -> str:
    """题包正文 → 三值状态（`KA-53`）：从「题目／解答」两段的实际内容反推，不另存状态列。

    指针句（`见《书》p.X`）与空段都算题面没取到；解答段是占位句（`未配到解答`）或是空则只算
    「仅题面」。三段标题由 `exercise_pack_text` 恒定写出，这里按 `## ` 标题切段。
    """
    sections: dict[str, str] = {}
    current = ""
    body: list[str] = []
    for line in (text or "").splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(body).strip()
            current = line[3:].strip()
            body = []
            continue
        if current:
            body.append(line)
    if current:
        sections[current] = "\n".join(body).strip()
    problem = sections.get("题目", "")
    solution = sections.get("解答", "")
    if not problem or problem.startswith("见《"):
        return EXERCISE_STATUS_SOURCE_MISSING
    if not solution or solution == EXERCISE_NO_SOLUTION_TEXT:
        return EXERCISE_STATUS_PROBLEM_ONLY
    return EXERCISE_STATUS_COMPLETE


def exercise_item_pages(row: AfKp) -> list[int]:
    """题行的锚点页（去重定序）：独立模式下解答页可作第二枚锚点，配套模式只有题面锚。"""
    return sorted(
        {
            int(anchor.get("page") or 0)
            for anchor in (row.anchors or [])
            if isinstance(anchor, dict) and anchor.get("page")
        }
    )


def exercise_item_ref(row: AfKp) -> dict[str, Any]:
    """题行的索引条目（`KA-53` 章级索引的 `items[]` 项）：只存指针与身份，不复制题包正文。"""
    meta = row.meta or {}
    pages = exercise_item_pages(row)
    return {
        "node_id": row.node_id,
        "item_no": exercise_item_key(row.name or "") or (row.name or ""),
        "parent_id": row.parent_id,
        "page_start": pages[0] if pages else None,
        "page_end": pages[-1] if pages else None,
        "source_book_id": str(meta.get("source_book_id") or ""),
        "source_pages": sorted(
            {int(page) for page in (meta.get("source_pages") or []) if isinstance(page, int)}
        ),
    }


def _exercise_index_dir(data_root: Path | str, book: AfBook) -> Path:
    """索引目录＝`<book_dir>/kp/exercises/`（与 `KA-20` 同一派生式，章的组织只由文件名给）。"""
    return ensure_within(
        data_root,
        book_dir_for(data_root, book.domain_id, book.course_id, book.book_id)
        / NARRATIVE_DIRNAME
        / EXERCISE_INDEX_DIRNAME,
    )


def _exercise_item_clusters(rows: Sequence[AfKp]) -> dict[str, list[AfKp]]:
    """一本在册行 → 「章行 → 其下题行」：题行的章按祖先链上最近的派生 `chapter` 认。

    只有 `candidate` 活行参与（`rejected` 的旧读数不进派生视图）；题行的判据与读面同源
    （`node_kind='point'` ∧ 派生 `node_type='exercise'`），不按 `model_ref` 猜产出者。
    """
    live = [row for row in rows if row.gen_status != "rejected"]
    types = node_types_of(live)
    by_id = {row.node_id: row for row in live}
    clusters: dict[str, list[AfKp]] = {}
    for row in live:
        if row.node_kind != NODE_KIND_POINT or types.get(row.node_id) != NODE_TYPE_EXERCISE:
            continue
        chapter = _target_chapter_of(row.parent_id, by_id, types)
        if chapter is None:
            continue
        clusters.setdefault(chapter.node_id, []).append(row)
    return clusters


def write_exercise_indexes(
    session: Session, *, data_root: Path | str, book_id: str, gen_batch: str
) -> int:
    """批次收尾整份重写章级索引（`KA-53`）：一章一文件、派生视图、不追加、不复制正文。

    每章一道文件 `<chapter_id>.json`，本次没有题行的旧章索引一并清掉——「整份重写」意味着磁盘上
    的索引集合等于当前活行的章集合，不是历史追加。写盘失败只告警、不升级为批次失败：索引是派生
    视图，读面读不到时会退到行与题包指针（`exercise_workspace` 的兜底）。
    """
    book = BookRepository(session).get(book_id)
    if book is None:
        return 0
    rows = KpRepository(session).list_for_book(book_id)
    clusters = _exercise_item_clusters(rows)
    by_id = {row.node_id: row for row in rows}
    directory = _exercise_index_dir(data_root, book)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("习题索引目录建不出来：%s（%s）", directory, exc)
        return 0
    written = 0
    for chapter_id, items in clusters.items():
        chapter = by_id[chapter_id]
        payload = {
            "chapter": {
                "node_id": chapter.node_id,
                "name": chapter.name or "",
                "order_hint": chapter.order_hint,
            },
            "items": [
                {
                    **exercise_item_ref(row),
                    "status": _exercise_pack_status_of(session, data_root=data_root, row=row),
                }
                for row in items
            ],
            "updated_batch": gen_batch,
        }
        try:
            atomic_write_text(
                directory / f"{chapter_id}.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
        except OSError as exc:
            logger.warning("习题索引写盘失败：%s（%s）", chapter_id, exc)
            continue
        written += 1
    try:
        stale = [
            path
            for path in directory.glob("*.json")
            if path.name != EXERCISE_INDEX_META_NAME and path.stem not in clusters
        ]
    except OSError:
        stale = []
    for path in stale:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("过期习题索引删不掉：%s（%s）", path, exc)
    return written


def write_exercise_meta(
    session: Session,
    *,
    data_root: Path | str,
    book_id: str,
    kind: str,
    target_book_id: str | None,
    gen_batch: str,
    unmatched: Sequence[Mapping[str, Any]],
) -> bool:
    """来源书清单（`KA-53`）：模式、目标书、最近批次与未匹配解答，只服务「这本书的提示」。"""
    book = BookRepository(session).get(book_id)
    if book is None:
        return False
    directory = _exercise_index_dir(data_root, book)
    payload = {
        "kind": kind,
        "target_book_id": target_book_id,
        "updated_batch": gen_batch,
        "unmatched": [dict(item) for item in unmatched],
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            directory / EXERCISE_INDEX_META_NAME,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
    except OSError as exc:
        logger.warning("习题来源书清单写盘失败：%s（%s）", book_id, exc)
        return False
    return True


def read_exercise_meta(data_root: Path | str, book: AfBook) -> dict[str, Any]:
    """读来源书清单：不在位或坏 JSON 一律当空表（读面退到批次流水），读腿不造文件。"""
    try:
        payload = json.loads(
            (_exercise_index_dir(data_root, book) / EXERCISE_INDEX_META_NAME).read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _exercise_index_statuses(data_root: Path | str, book: AfBook) -> dict[str, str]:
    """章级索引里的「题行 → 状态」：坏文件跳过；索引缺席的题行由读面的兜底判。"""
    directory = _exercise_index_dir(data_root, book)
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return {}
    statuses: dict[str, str] = {}
    for path in paths:
        if path.name == EXERCISE_INDEX_META_NAME:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("node_id") and item.get("status"):
                statuses[str(item["node_id"])] = str(item["status"])
    return statuses


def _exercise_pack_status_of(
    session: Session, *, data_root: Path | str, row: AfKp
) -> str:
    """题行 → 状态：按题包文件的实际内容反推（状态不落库，题包是唯一事实源）。"""
    if not row.narrative_ref:
        return EXERCISE_STATUS_SOURCE_MISSING
    try:
        text = _narrative_path(session, data_root=data_root, node=row).read_text(encoding="utf-8")
    except (KpGateError, OSError):
        return EXERCISE_STATUS_SOURCE_MISSING
    return exercise_pack_status(text)


def _latest_exercise_batch(session: Session, book_id: str) -> AfOperation | None:
    """最近一批 `stage='exercise'` 的流水行：模式与目标的兜底来源（清单不在位时）。"""
    for row in reversed(OperationRepository(session).list_kp_batches(book_id)):
        if (row.payload or {}).get("stage") == "exercise":
            return row
    return None


@dataclass(frozen=True, slots=True)
class ExerciseReading:
    """习题读面（`KA-53`／W16）：模式、目标、题框、题包数、待重解析与未匹配，全部读时派生。

    `done` 是习题书唯一的完成读数：题行 > 0 ∧ 题包数 == 题行数 ∧ 待重解析零枚。它**不**复用
    `stage.done`（统计域只有章／点，在习题书上恒零行）。未匹配解答是披露、不是缺项，不挡完成；
    「仅题面」也不算缺项（有些习题集本来就没有解答），只有题面没取到的行才计入待重解析。
    """

    kind: str = "standalone"
    target_book_id: str | None = None
    items: tuple[dict[str, Any], ...] = ()
    packs: int = 0
    pending_reparse: int = 0
    unmatched: tuple[dict[str, Any], ...] = ()
    done: bool = False


def exercise_workspace(
    session: Session, *, data_root: Path | str, book: AfBook
) -> ExerciseReading | None:
    """习题书的工作区读面（`KA-53`，零写库）：教材恒 `None`——共享主表不长第二套读数。

    模式与目标先读来源书清单（最近一次习题批次写下的），清单不在位时退到批次流水。题行的归属：
    独立模式读本书；配套模式读目标教材里 `meta.source_book_id` 指向本书的行——屏上只显示本书
    贡献的题，教材自己的题（教材内部习题段）不算进来。状态优先取章级索引（写盘时的读数），
    索引缺席的行按题包指针兜底。
    """
    if book.doc_kind != DOC_KIND_EXERCISE:
        return None
    meta = read_exercise_meta(data_root, book)
    batch = _latest_exercise_batch(session, book.book_id)
    payload = (batch.payload if batch is not None else None) or {}
    result = (batch.result if batch is not None else None) or {}
    kind = str(meta.get("kind") or ("companion" if payload.get("target_book_id") else "standalone"))
    target_book_id = meta.get("target_book_id") or payload.get("target_book_id") or None
    unmatched_raw = meta.get("unmatched")
    if not isinstance(unmatched_raw, list):
        unmatched_raw = result.get("unmatched_solution_details")
    unmatched = tuple(
        dict(item) for item in (unmatched_raw or []) if isinstance(item, dict)
    )

    if kind == "companion":
        if not target_book_id:
            return ExerciseReading(
                kind="companion", target_book_id=None, unmatched=unmatched
            )
        owner = BookRepository(session).get(str(target_book_id))
        if owner is None:
            return ExerciseReading(
                kind="companion", target_book_id=str(target_book_id), unmatched=unmatched
            )
    else:
        kind = "standalone"
        target_book_id = None
        owner = book

    rows = KpRepository(session).list_for_book(owner.book_id)
    live = [row for row in rows if row.gen_status != "rejected"]
    types = node_types_of(live)
    mine = (
        [
            row
            for row in live
            if str((row.meta or {}).get("source_book_id") or "") == book.book_id
        ]
        if kind == "companion"
        else live
    )
    item_rows = [
        row
        for row in mine
        if row.node_kind == NODE_KIND_POINT and types.get(row.node_id) == NODE_TYPE_EXERCISE
    ]
    statuses = _exercise_index_statuses(data_root, owner)
    items: list[dict[str, Any]] = []
    for row in item_rows:
        status = statuses.get(row.node_id) or _exercise_pack_status_of(
            session, data_root=data_root, row=row
        )
        items.append(
            {
                **exercise_item_ref(row),
                "status": status,
                "pack": bool(row.narrative_ref),
                "confirmed": int(row.confirmed or 0),
            }
        )
    packs = sum(1 for item in items if item["pack"])
    pending = sum(1 for item in items if item["status"] == EXERCISE_STATUS_SOURCE_MISSING)
    return ExerciseReading(
        kind=kind,
        target_book_id=target_book_id,
        items=tuple(items),
        packs=packs,
        pending_reparse=pending,
        unmatched=unmatched,
        done=bool(items) and packs == len(items) and pending == 0,
    )


def extract_exercise_book_items(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    data_root: Path | str,
) -> ExerciseBookBuild:
    """习题集独立模式（`KA-53`）：按在册目录切段，规则定题界、模型读题面与解答、逐题成行写题包。

    一段一次调用：边界先由 `split_exercise_items` 给出（题号闭集＋单调序），模型只填这一批题的
    「题面／解答」两段内容，不得增删题号；模型多回的题号与本节切分对不上即进未匹配披露——不落行、
    不写题包（`KA-53` 的「未匹配只披露」）。题号一枚也没认出的段零调用、零行，只计数。

    题行的父＝该段所属的章／节行（独立模式的题属于本书目录树），`meta` 记来源书与本题页区间；
    `summary` 恒空——题面与解答都在题包文件里（`KA-20` 的长文通路），库里只留身份、锚点与指针。
    失败域按段：一段调用失败只让这一段零行，明细点名之后本批照跑（与知识点逐节同形）。

    只有叶子目录行切段：章行的区间按树序延伸到下一章之前，天然覆盖其下各节的正文——章与节都
    发问等于把同一段题抄两遍（同一个父下两套题号）。有子行的结构行整行跳过，正文归属它下面
    真正的叶子；章下没有子行时章自己就是叶子，照常切。
    """
    repo = KpRepository(session)
    book = BookRepository(session).get(acceptance.book_id)
    if book is None:
        raise KpGateError(404, f"书目不存在：{acceptance.book_id}")
    book_title = str(getattr(book, "title", "") or acceptance.book_id)
    node_meta = {node.row.node_id: (node.row.meta or {}) for node in outline_nodes}
    structural = {node.row.parent_id for node in outline_nodes if node.row.parent_id}
    item_rows = pack_rows = empty_segments = orphan_segments = failed = 0
    failed_details: list[dict[str, Any]] = []
    unmatched_details: list[dict[str, Any]] = []
    statuses: list[tuple[str, str]] = []

    for span in _section_spans(outline_nodes, evidence):
        meta = node_meta.get(span.parent_id or "") or {}
        role = str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE)
        kind = str(meta.get("toc_kind") or "")
        if role == TOC_ROLE_REFERENCE and kind != "exercise":
            continue
        if span.parent_id is None:
            orphan_segments += 1
            continue
        if span.parent_id in structural:
            continue
        pages = _page_bodies(evidence, span)
        try:
            items, reply, round_failures = _exercise_section_reply(engine, span, pages)
        except EngineError as exc:
            failed += 1
            failed_details.append(_exercise_failure_detail(span, exc))
            continue
        if round_failures:
            failed += 1
            failed_details.extend(round_failures)
        if not items:
            empty_segments += 1
            continue
        by_key, unmatched = _group_exercise_drafts(items, reply, span)
        unmatched_details.extend(unmatched)

        for ordinal, item in enumerate(items, start=1):
            draft = by_key.get(item.item_no)
            problem = draft.problem if draft is not None else ""
            solution = draft.solution if draft is not None else ""
            status = _exercise_status(problem, solution)
            row = repo.create(
                KpNodeRow(
                    node_id=new_node_id(),
                    book_id=acceptance.book_id,
                    job_id=acceptance.evidence_job_id,
                    node_kind=NODE_KIND_POINT,
                    parent_id=span.parent_id,
                    order_hint=ordinal,
                    name=item.label[:500],
                    norm_key=norm_key_for(item.item_no)[:200],
                    summary="",
                    anchors=_exercise_anchors(item),
                    gen_status="draft",
                    gen_batch=acceptance.gen_batch,
                    model_ref=EXERCISE_LEG_MODEL_REF,
                    meta={
                        "toc_kind": "exercise",
                        "source_book_id": acceptance.book_id,
                        "source_pages": sorted({item.page_start, item.page_end}),
                    },
                )
            )
            item_rows += 1
            statuses.append((row.node_id, status))
            source_line = f"《{book_title}》{_exercise_page_span([item.page_start, item.page_end])}"
            if _write_exercise_pack(
                session,
                data_root=data_root,
                node_id=row.node_id,
                label=item.label,
                problem=_exercise_problem_text(
                    problem,
                    book_title=book_title,
                    pages=[item.page_start, item.page_end],
                ),
                solution=solution,
                source_lines=[source_line],
            ):
                pack_rows += 1
        session.commit()

    return ExerciseBookBuild(
        item_rows=item_rows,
        pack_rows=pack_rows,
        empty_segments=empty_segments,
        orphan_segments=orphan_segments,
        failed_sections=failed,
        failed_section_details=tuple(failed_details),
        unmatched_solutions=len(unmatched_details),
        unmatched_solution_details=tuple(unmatched_details),
        statuses=tuple(statuses),
    )


def _exercise_book_chapter_norm(
    node_id: str | None, by_id: Mapping[str, OutlineNode]
) -> str | None:
    """习题集目录行 → 它所属的章（走到顶的那一行）的 `norm_key`；断链时用走到的最浅行。"""
    seen: set[str] = set()
    current = by_id.get(node_id or "")
    top: OutlineNode | None = None
    while current is not None and current.row.node_id not in seen:
        seen.add(current.row.node_id)
        top = current
        current = by_id.get(current.row.parent_id or "")
    return top.row.norm_key if top is not None else None


def _target_chapter_of(
    node_id: str | None, by_id: Mapping[str, AfKp], types: Mapping[str, str]
) -> AfKp | None:
    """目标教材的一行 → 它所属的章行（读时派生的 `node_type='chapter'`）：题行沿父链上溯找。"""
    seen: set[str] = set()
    current = by_id.get(node_id or "")
    while current is not None and current.node_id not in seen:
        seen.add(current.node_id)
        if types.get(current.node_id) == NODE_TYPE_CHAPTER:
            return current
        current = by_id.get(current.parent_id or "")
    return None


def _target_segment_of(node_id: str | None, by_id: Mapping[str, AfKp]) -> AfKp | None:
    """目标教材的题行 → 它所属的习题段行（`outline` ∧ `toc_kind='exercise'`）。"""
    seen: set[str] = set()
    current = by_id.get(node_id or "")
    while current is not None and current.node_id not in seen:
        seen.add(current.node_id)
        meta = current.meta or {}
        if current.node_kind == NODE_KIND_OUTLINE and str(meta.get("toc_kind") or "") == "exercise":
            return current
        current = by_id.get(current.parent_id or "")
    return None


def _target_exercise_spans(target_rows: Sequence[AfKp], target: AfBook) -> list[SectionSpan]:
    """目标教材的目录区间（`KA-42` 同一算式）：只为把习题段的页区间取回来。"""
    span_rows: list[SpanRow] = []
    for row in target_rows:
        if row.node_kind != NODE_KIND_OUTLINE:
            continue
        page = _first_anchor_page(row)
        if page is None:
            continue
        meta = row.meta or {}
        span_rows.append(
            SpanRow(
                node_id=row.node_id,
                name=row.name or "",
                order_hint=row.order_hint,
                page=page,
                depth=row.depth or 1,
                toc_role=str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE),
                toc_kind=str(meta.get("toc_kind") or ""),
            )
        )
    last_page = int(target.page_count or 0) or max(
        (row.page for row in span_rows), default=1
    )
    return section_spans_of(span_rows, first_page=1, last_page=last_page)


def _target_segment_texts(
    *,
    data_root: Path | str,
    target: AfBook,
    segment: AfKp,
    spans: Sequence[SectionSpan],
) -> dict[str, str] | None:
    """教材习题段的「题号 → 题面正文」：按同一把分题规则重跑该段**自身取证版本**的页产物。

    锚点与正文必须同书同版（`KA-18`）。页产物缺位或区间夹不出来时给 `None`，调用方退到锚点
    片段、再退到指针句——不拿别的版本的正文化替。
    """
    span = next((item for item in spans if item.parent_id == segment.node_id), None)
    if span is None:
        return None
    pages_dir = (
        version_dir_for(
            data_root,
            target.domain_id,
            target.course_id,
            target.book_id,
            segment.job_id or "",
        )
        / "pages"
    )
    pages: list[tuple[int, str]] = []
    for page_no in range(span.page_start, span.page_end + 1):
        try:
            pages.append(
                (page_no, (pages_dir / f"p{page_no:04d}.md").read_text(encoding="utf-8"))
            )
        except OSError:
            return None
    return {item.item_no: item.text for item in split_exercise_items(pages)}


def merge_exercise_book_items(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    data_root: Path | str,
) -> ExerciseBookBuild:
    """习题集配套模式（`KA-53`）：题面与解答按（章＋题号）合并进目标教材的题行。

    匹配域＝目标教材的在册行（`candidate`）：章行按读时派生的 `node_type='chapter'` 认，题行按
    `node_type='exercise'` ∧ `node_kind='point'` 认；两书的题号都折到 `normalize.exercise` 的
    同一把键上，与行的派生 `node_type` 共用同一份读。命中只补空位——`confirmed=1` 的行一枚不碰
    （`KA-13`）、已有题包不覆盖、来源两键只在原值空着时写；未命中在对应章下新增一行，父＝章行、
    锚点为空（跨书的页号不能当本书的锚点，`KA-18`）。

    题面正文取自目标教材锚点页的文本（同一把分题规则在该段自己的页产物上重跑），取不到时退到
    锚点片段、再退到指针句；解答由本批模型读出。来源书与来源页只进 `meta` 与批次流水，题面不进
    `summary`。未匹配（章无法定位／题号在目标章下不唯一／模型回的题号认不出）只披露：不落行、
    不写题包——习题集的完成读数不要求它为零（披露不是缺项）。

    同一章下两个习题段都从 `1.` 起时，`(章＋题号)` 命中的会是两枚：若其中恰好一枚的所在段名与
    本节标题同名（`norm_key` 相等）则取它，否则如实披露「题号在目标章下不唯一」——不猜。
    题包落点是**目标书的书目录**（`KA-20` 的派生式按行所属书现算），失败域与独立模式同形。
    """
    repo = KpRepository(session)
    book = BookRepository(session).get(acceptance.book_id)
    if book is None:
        raise KpGateError(404, f"书目不存在：{acceptance.book_id}")
    target_book_id = acceptance.target_book_id or ""
    target = BookRepository(session).get(target_book_id)
    if target is None:
        raise KpGateError(404, f"目标教材不存在：{target_book_id}")
    book_title = str(getattr(book, "title", "") or acceptance.book_id)
    target_title = str(getattr(target, "title", "") or target_book_id)
    node_meta = {node.row.node_id: (node.row.meta or {}) for node in outline_nodes}
    structural = {node.row.parent_id for node in outline_nodes if node.row.parent_id}
    outline_by_id = {node.row.node_id: node for node in outline_nodes}

    target_rows = repo.list_for_book(target_book_id, gen_status="candidate")
    target_by_id = {row.node_id: row for row in target_rows}
    target_types = node_types_of(target_rows)
    chapters: dict[str, AfKp] = {}
    for row in target_rows:
        if target_types.get(row.node_id) != NODE_TYPE_CHAPTER:
            continue
        key = row.norm_key or norm_key_for(row.name or "")
        if key:
            chapters.setdefault(key, row)
    exercise_index: dict[tuple[str, str], list[AfKp]] = {}
    order_cursor: dict[str, int] = {}
    for row in target_rows:
        if row.node_kind != NODE_KIND_POINT or target_types.get(row.node_id) != NODE_TYPE_EXERCISE:
            continue
        key = exercise_item_key(row.name or "")
        chapter = _target_chapter_of(row.parent_id, target_by_id, target_types)
        if key is None or chapter is None:
            continue
        exercise_index.setdefault((chapter.node_id, key), []).append(row)
        order_cursor[chapter.node_id] = max(
            order_cursor.get(chapter.node_id, 0), int(row.order_hint or 0)
        )

    target_spans: list[SectionSpan] | None = None
    segment_texts: dict[str, dict[str, str] | None] = {}

    def segment_text_of(segment: AfKp) -> dict[str, str] | None:
        nonlocal target_spans
        if segment.node_id not in segment_texts:
            if target_spans is None:
                target_spans = _target_exercise_spans(target_rows, target)
            segment_texts[segment.node_id] = _target_segment_texts(
                data_root=data_root, target=target, segment=segment, spans=target_spans
            )
        return segment_texts[segment.node_id]

    item_rows = pack_rows = empty_segments = orphan_segments = failed = merged = 0
    failed_details: list[dict[str, Any]] = []
    unmatched_details: list[dict[str, Any]] = []
    statuses: list[tuple[str, str]] = []

    for span in _section_spans(outline_nodes, evidence):
        meta = node_meta.get(span.parent_id or "") or {}
        role = str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE)
        kind = str(meta.get("toc_kind") or "")
        if role == TOC_ROLE_REFERENCE and kind != "exercise":
            continue
        if span.parent_id is None:
            orphan_segments += 1
            continue
        if span.parent_id in structural:
            continue
        pages = _page_bodies(evidence, span)
        try:
            items, reply, round_failures = _exercise_section_reply(engine, span, pages)
        except EngineError as exc:
            failed += 1
            failed_details.append(_exercise_failure_detail(span, exc))
            continue
        if round_failures:
            failed += 1
            failed_details.extend(round_failures)
        if not items:
            empty_segments += 1
            continue
        by_key, unmatched = _group_exercise_drafts(items, reply, span)
        unmatched_details.extend(unmatched)

        chapter_norm = _exercise_book_chapter_norm(span.parent_id, outline_by_id)
        chapter = chapters.get(chapter_norm or "")
        if chapter is None:
            for item in items:
                draft = by_key.get(item.item_no)
                if draft is not None and (draft.problem.strip() or draft.solution.strip()):
                    unmatched_details.append(
                        {
                            "item_no": item.item_no,
                            "source_pages": [item.page_start, item.page_end],
                            "reason": "目标教材里认不出本章",
                        }
                    )
            continue

        for item in items:
            draft = by_key.get(item.item_no)
            problem_read = draft.problem if draft is not None else ""
            solution = draft.solution if draft is not None else ""
            candidates = exercise_index.get((chapter.node_id, item.item_no), [])
            if len(candidates) > 1:
                preferred = []
                for row in candidates:
                    segment = _target_segment_of(row.parent_id, target_by_id)
                    if segment is not None and segment.norm_key == norm_key_for(span.title):
                        preferred.append(row)
                if len(preferred) == 1:
                    candidates = preferred
            if len(candidates) > 1:
                if problem_read.strip() or solution.strip():
                    unmatched_details.append(
                        {
                            "item_no": item.item_no,
                            "source_pages": [item.page_start, item.page_end],
                            "reason": "题号在目标章下不唯一",
                        }
                    )
                continue

            if candidates:
                row = candidates[0]
                merged += 1
                if row.confirmed or row.narrative_ref:
                    # 命中只补空位：被背书或已有题包的行不覆盖，差别由 merged 与 pack_rows 披露。
                    continue
                segment = _target_segment_of(row.parent_id, target_by_id)
                texts = segment_text_of(segment) if segment is not None else None
                problem = (texts or {}).get(item.item_no, "")
                if not problem.strip():
                    problem = str((row.anchors or [{}])[0].get("snippet") or "")
                anchor_pages = sorted(
                    {
                        int(anchor.get("page") or 0)
                        for anchor in (row.anchors or [])
                        if isinstance(anchor, dict) and anchor.get("page")
                    }
                )
                source_lines = []
                if anchor_pages:
                    source_lines.append(
                        f"《{target_title}》{_exercise_page_span(anchor_pages)}"
                    )
                source_lines.append(
                    f"《{book_title}》"
                    f"{_exercise_page_span([item.page_start, item.page_end])}（解答）"
                )
                if _write_exercise_pack(
                    session,
                    data_root=data_root,
                    node_id=row.node_id,
                    label=row.name or item.label,
                    problem=_exercise_problem_text(
                        problem, book_title=target_title, pages=anchor_pages
                    ),
                    solution=solution,
                    source_lines=source_lines,
                ):
                    pack_rows += 1
                    statuses.append((row.node_id, _exercise_status(problem, solution)))
                repo.set_exercise_source(
                    row.node_id,
                    source_book_id=acceptance.book_id,
                    source_pages=[item.page_start, item.page_end],
                )
                continue

            order_cursor[chapter.node_id] = order_cursor.get(chapter.node_id, 0) + 1
            row = repo.create(
                KpNodeRow(
                    node_id=new_node_id(),
                    book_id=target_book_id,
                    job_id=target.active_job_id or "",
                    node_kind=NODE_KIND_POINT,
                    parent_id=chapter.node_id,
                    order_hint=order_cursor[chapter.node_id],
                    name=item.label[:500],
                    norm_key=norm_key_for(item.item_no)[:200],
                    summary="",
                    anchors=[],
                    gen_status="draft",
                    gen_batch=acceptance.gen_batch,
                    model_ref=EXERCISE_LEG_MODEL_REF,
                    meta={
                        "toc_kind": "exercise",
                        "source_book_id": acceptance.book_id,
                        "source_pages": sorted({item.page_start, item.page_end}),
                    },
                )
            )
            item_rows += 1
            statuses.append((row.node_id, _exercise_status(problem_read, solution)))
            exercise_index.setdefault((chapter.node_id, item.item_no), []).append(row)
            source_line = (
                f"《{book_title}》{_exercise_page_span([item.page_start, item.page_end])}"
                "（题面与解答）"
            )
            if _write_exercise_pack(
                session,
                data_root=data_root,
                node_id=row.node_id,
                label=item.label,
                problem=_exercise_problem_text(
                    problem_read,
                    book_title=book_title,
                    pages=[item.page_start, item.page_end],
                ),
                solution=solution,
                source_lines=[source_line],
            ):
                pack_rows += 1
        session.commit()

    return ExerciseBookBuild(
        item_rows=item_rows,
        pack_rows=pack_rows,
        empty_segments=empty_segments,
        orphan_segments=orphan_segments,
        failed_sections=failed,
        failed_section_details=tuple(failed_details),
        unmatched_solutions=len(unmatched_details),
        unmatched_solution_details=tuple(unmatched_details),
        statuses=tuple(statuses),
        merged_rows=merged,
    )


# --------------------------------------------------------------- 选中题重解析（KA-53／W17）


def _exercise_retry_rows(
    session: Session, *, book_id: str, owner_id: str, selected: Sequence[str]
) -> list[AfKp]:
    """修补批次的行集（`KA-53`）：逐枚校验「行在目标书 ∧ 来源书＝本书 ∧ 未确认」。

    任一不成立整批拒（零写入）——半批修补会把屏上的「选中重解析」读成「选中里成功了几枚」的
    不确定读数。确认位这里只拒不退出：机器不与人工结论争同一身份（`KA-13`）。
    """
    rows = {row.node_id: row for row in KpRepository(session).list_for_book(owner_id)}
    kept: list[AfKp] = []
    for node_id in selected:
        row = rows.get(node_id)
        if row is None:
            raise KpGateError(
                422,
                f"选中题重解析不受理：{node_id} 不在目标书 {owner_id} 的行里（`KA-53`，零写入）",
            )
        if str((row.meta or {}).get("source_book_id") or "") != book_id:
            raise KpGateError(
                422,
                f"选中题重解析不受理：{node_id} 的来源书不是本书 {book_id}（`KA-53`，零写入）",
            )
        if row.confirmed:
            raise KpGateError(
                422,
                f"选中题重解析不受理：{node_id} 已确认，机器行不覆盖人工结论"
                "（`KA-13`，零写入）",
            )
        kept.append(row)
    return kept


def _resolve_exercise_retry_owner(
    session: Session,
    *,
    book_id: str,
    target_book_id: str | None,
    selected: Sequence[str],
) -> str:
    """修补批次的行归属书：显式目标 > 本书 > 本书最近一批习题流水的目标教材。

    独立模式的题行挂在本书；配套模式的题行挂在目标教材。调用方不必记住上一次的模式——
    清单不在位时按最近一批的载荷认，两条模式先后在同一本书上跑过也不猜错行集。
    """
    if target_book_id:
        _exercise_retry_rows(session, book_id=book_id, owner_id=target_book_id, selected=selected)
        return target_book_id
    try:
        _exercise_retry_rows(session, book_id=book_id, owner_id=book_id, selected=selected)
        return book_id
    except KpGateError as own_error:
        previous = _latest_exercise_batch(session, book_id)
        previous_target = (
            str((previous.payload or {}).get("target_book_id") or "") if previous else ""
        )
        if not previous_target or previous_target == book_id:
            raise own_error
        _exercise_retry_rows(
            session, book_id=book_id, owner_id=previous_target, selected=selected
        )
        return previous_target


def retry_exercise_book_items(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    data_root: Path | str,
) -> ExerciseBookBuild:
    """选中题重解析（`KA-53`／W17）：只对选中题行重新取证与配对，覆盖未确认的行与题包。

    分题边界仍由规则给（与首轮同一支 `split_exercise_items`）：模型只重读选中题号的「题面／
    解答」两段，题号不在本次切分内的只披露、不改行。独立模式题面取本书模型读数、锚点随切分
    刷新；配套模式题面仍取目标教材**同一版**页产物、解答取本书模型读数，行锚点不动（跨书页号
    不能当本书的锚点，`KA-18`），来源两键只刷新页区间。确认位在受理面排掉、执行期再复查一次
    ——受理与执行之间有人背书时同样不覆盖（`KA-13`）。不新成行、不作废旧行：修补批次只改写
    选中的那几枚。
    """
    repo = KpRepository(session)
    book = BookRepository(session).get(acceptance.book_id)
    if book is None:
        raise KpGateError(404, f"书目不存在：{acceptance.book_id}")
    owner_id = acceptance.target_book_id or acceptance.book_id
    owner = BookRepository(session).get(owner_id)
    if owner is None:
        raise KpGateError(404, f"目标书不存在：{owner_id}")
    book_title = str(getattr(book, "title", "") or acceptance.book_id)
    target_title = str(getattr(owner, "title", "") or owner_id)
    selected = list(dict.fromkeys(str(node_id) for node_id in acceptance.exercise_retry_node_ids))
    targets = _exercise_retry_rows(
        session, book_id=acceptance.book_id, owner_id=owner_id, selected=selected
    )

    spans = _section_spans(outline_nodes, evidence)
    span_by_segment = {span.parent_id: span for span in spans if span.parent_id}
    outline_by_id = {node.row.node_id: node for node in outline_nodes}
    owner_rows = repo.list_for_book(owner_id)
    owner_by_id = {row.node_id: row for row in owner_rows}
    owner_types = node_types_of([row for row in owner_rows if row.gen_status != "rejected"])
    split_cache: dict[str, tuple[ExerciseItem, ...]] = {}

    def items_of(span: SectionSpan) -> tuple[ExerciseItem, ...]:
        key = span.parent_id or f"{span.page_start}-{span.page_end}"
        if key not in split_cache:
            split_cache[key] = split_exercise_items(
                [(page.page_no, page.markdown) for page in _page_bodies(evidence, span)]
            )
        return split_cache[key]

    # 选中行 → 它在本书的那一段：独立模式按父行认；配套模式按目标章名＋题号认（同一章下
    # 两个习题段都从 `1.` 起时，再用目标段名把本书的段对齐，仍不唯一就不猜）。
    pending: dict[str, list[tuple[AfKp, ExerciseItem]]] = {}
    unmatched_details: list[dict[str, Any]] = []
    for row in targets:
        key = exercise_item_key(row.name or "")
        row_pages = exercise_item_pages(row)
        if key is None:
            unmatched_details.append(
                {"item_no": row.name or "", "source_pages": row_pages, "reason": "题行的题号认不出"}
            )
            continue
        if owner_id == acceptance.book_id:
            span = span_by_segment.get(row.parent_id or "")
        else:
            chapter = _target_chapter_of(row.parent_id, owner_by_id, owner_types)
            chapter_norm = (
                (chapter.norm_key or norm_key_for(chapter.name or ""))
                if chapter is not None
                else ""
            )
            candidates = [
                item
                for item in spans
                if item.parent_id
                and _exercise_book_chapter_norm(item.parent_id, outline_by_id) == chapter_norm
            ]
            hits = [item for item in candidates if key in {i.item_no for i in items_of(item)}]
            if len(hits) > 1:
                segment = _target_segment_of(row.parent_id, owner_by_id)
                segment_norm = norm_key_for(segment.name or "") if segment is not None else ""
                narrowed = [
                    item
                    for item in hits
                    if norm_key_for(span_by_segment[item.parent_id].title) == segment_norm
                ]
                hits = narrowed or hits
            span = hits[0] if len(hits) == 1 else None
        item = (
            next((entry for entry in items_of(span) if entry.item_no == key), None)
            if span is not None
            else None
        )
        if span is None or item is None:
            unmatched_details.append(
                {
                    "item_no": key,
                    "source_pages": [span.page_start, span.page_end] if span is not None else row_pages,
                    "reason": "选中题号在本书目录里认不出所在的节"
                    if span is None
                    else "选中题号在本节正文里认不出",
                }
            )
            continue
        pending.setdefault(span.parent_id or "", []).append((row, item))

    pack_rows = failed = 0
    failed_details: list[dict[str, Any]] = []
    statuses: list[tuple[str, str]] = []
    target_spans: list[SectionSpan] | None = None
    segment_texts: dict[str, dict[str, str] | None] = {}

    for segment_id, jobs in pending.items():
        span = span_by_segment[segment_id]
        pages = _page_bodies(evidence, span)
        try:
            items, reply, round_failures = _exercise_section_reply(engine, span, pages)
        except EngineError as exc:
            failed += 1
            failed_details.append(_exercise_failure_detail(span, exc))
            continue
        if round_failures:
            failed += 1
            failed_details.extend(round_failures)
        if not items:
            for _row, item in jobs:
                unmatched_details.append(
                    {
                        "item_no": item.item_no,
                        "source_pages": [item.page_start, item.page_end],
                        "reason": "本节正文当次没切出题",
                    }
                )
            continue
        by_key, unmatched = _group_exercise_drafts(items, reply, span)
        unmatched_details.extend(unmatched)
        ordinals = {entry.item_no: index for index, entry in enumerate(items, start=1)}
        for row, item in jobs:
            draft = by_key.get(item.item_no)
            problem_read = draft.problem if draft is not None else ""
            solution = draft.solution if draft is not None else ""
            source_pages = sorted({item.page_start, item.page_end})
            if owner_id == acceptance.book_id:
                problem_raw = problem_read
                problem = _exercise_problem_text(
                    problem_read, book_title=book_title, pages=[item.page_start, item.page_end]
                )
                source_lines = [
                    f"《{book_title}》{_exercise_page_span([item.page_start, item.page_end])}"
                ]
            else:
                problem_raw = ""
                segment = _target_segment_of(row.parent_id, owner_by_id)
                if segment is not None:
                    if target_spans is None:
                        target_spans = _target_exercise_spans(
                            repo.list_for_book(owner_id, gen_status="candidate"), owner
                        )
                    if segment.node_id not in segment_texts:
                        segment_texts[segment.node_id] = _target_segment_texts(
                            data_root=data_root,
                            target=owner,
                            segment=segment,
                            spans=target_spans,
                        )
                    problem_raw = (segment_texts[segment.node_id] or {}).get(item.item_no, "")
                if not problem_raw.strip():
                    problem_raw = problem_read
                if not problem_raw.strip():
                    problem_raw = str((row.anchors or [{}])[0].get("snippet") or "")
                problem = _exercise_problem_text(
                    problem_raw, book_title=target_title, pages=row_pages
                )
                source_lines = []
                if row_pages:
                    source_lines.append(f"《{target_title}》{_exercise_page_span(row_pages)}")
                source_lines.append(
                    f"《{book_title}》"
                    f"{_exercise_page_span([item.page_start, item.page_end])}（解答）"
                )
            if not _write_exercise_pack(
                session,
                data_root=data_root,
                node_id=row.node_id,
                label=item.label or row.name or "",
                problem=problem,
                solution=solution,
                source_lines=source_lines,
            ):
                continue
            pack_rows += 1
            statuses.append((row.node_id, _exercise_status(problem_raw, solution)))
            if owner_id == acceptance.book_id:
                # 独立模式：锚点与序随本次切分刷新（正文可能因重解析而换页）。
                row.anchors = _exercise_anchors(item)
                row.order_hint = ordinals.get(item.item_no, row.order_hint or 0)
                row.rev += 1
                row.updated_at = utc_now()
            repo.set_exercise_source(
                row.node_id,
                source_book_id=acceptance.book_id,
                source_pages=source_pages,
            )
        session.commit()

    return ExerciseBookBuild(
        pack_rows=pack_rows,
        failed_sections=failed,
        failed_section_details=tuple(failed_details),
        unmatched_solutions=len(unmatched_details),
        unmatched_solution_details=tuple(unmatched_details),
        statuses=tuple(statuses),
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
        (row for row in rows if "chapter" in content_legs_for(types.get(row.node_id, ""))),
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
    """该行自己的树序区间（与点腿同一条 `section_spans_of` 算式），锚点只有一页也不缩水。

    单点重生成与章导读生成共用：章与节的终点都按树序判（章含其下全部子行）。

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
                toc_kind=str((row.meta or {}).get("toc_kind") or ""),
            )
            for row in rows
        ],
        first_page=min(pages),
        last_page=last_page,
    )
    return next((span for span in spans if span.parent_id == node.node_id), None)


def _regenerate_chapter_material(
    session: Session,
    *,
    data_root: Path | str,
    engine: KnowledgeEngine,
    node: AfKp,
    revision: RevisionContext | None = None,
) -> tuple[ChapterInput, tuple[int, int], EvidenceSet]:
    """单点章腿共用的取材前序（`KA-45`／`KA-47`）：在飞闸 → 版本 → 页区间 → 取证集 → 章材料三块。

    两条同步腿只差写面，前序一字不差：挡在飞是因为本机模型服务多为单并发，两路并跑只会让两边都
    超时；材料不够时宁可 422，也不付一次「没读到正文」的百秒级调用。`revision` 是这一次重写的
    上下文（`KA-48`），随同一次批次绑定交给引擎。
    """
    in_flight = KpRepository(session).find_draft_batch(node.book_id)
    if in_flight is not None:
        raise KpGateError(
            409, f"梳理批次在飞（{node.book_id} 的 {in_flight}）：单点生成等这一批跑完再发"
        )
    book = BookRepository(session).get(node.book_id)
    version = (node.job_id or "").strip() or (book.active_job_id if book is not None else "") or ""
    if not version:
        raise KpGateError(
            422, f"这一行没有取证版本可回指：{node.node_id}（发一次解析或重新挂锚后再试）"
        )
    span = _section_span_for(session, node)
    pages = (span.page_start, span.page_end) if span is not None else _anchor_page_range(node)
    if pages is None:
        raise KpGateError(422, f"这一行没有可用锚点页：{node.node_id}（重挂锚点后再试）")
    acceptance = KpAcceptance(
        book_id=node.book_id,
        evidence_job_id=version,
        gen_batch=f"regen-{node.node_id}-{int(time.time())}",
        page_start=pages[0],
        page_end=pages[1],
        stage="content",
    )
    bind = getattr(engine, "bind_batch", None)
    if callable(bind):
        bind(book_id=node.book_id, gen_batch=acceptance.gen_batch, revision=revision)
    evidence = build_evidence_set(session, data_root=data_root, acceptance=acceptance)
    rows = _registered_outline_rows(session, node.book_id)
    types = node_types_of(rows)
    summaries = {row.node_id: (row.summary or "").strip() for row in rows}
    ends = {
        span_row.parent_id: span_row.page_end
        for span_row in _section_spans(_outline_nodes_from_book(rows), evidence)
    }
    material = _chapter_material(
        node, rows, types, evidence, summaries, page_end=ends.get(node.node_id)
    )
    if material is None:
        raise KpGateError(422, f"这一行没有可用锚点页：{node.node_id}（重挂锚点后再试）")
    if not material.intro_pages and not material.title_pages:
        raise KpGateError(
            422,
            f"第 {pages[0]}..{pages[1]} 页没有可用的页产物：{node.node_id}"
            f"（缺页 {list(evidence.missing_pages)}，未解析 {list(evidence.unparsed_pages)}）",
        )
    return material, pages, evidence


def regenerate_section_summary(
    session: Session,
    *,
    data_root: Path | str,
    engine: KnowledgeEngine,
    node_id: str,
    if_rev: int | None = None,
    revision_note: str = "",
) -> AfKp:
    """单点总结重生成（`KA-45`）：梳理屏详情面「一键生成／重新生成」的总结腿。

    判定序与写面同族：行不存在 404 → `if_rev` 不匹配 409 → 类型不合 422 → 同书在飞 409 → 模型失败 502
    → 模型没给总结 422。**章节与知识点**可重生成：点走单节取材（`section_spans_of` 区间＋眉脚剔除），
    章走 `_chapter_material` 三块并一次 `engine.chapter` 调用（回复里的导读丢弃）；两条都只覆盖该行
    `summary`、`rev+1`，不碰 `narrative_ref`。习题与其他的令文口径是「什么都不需要」，422。

    `revision_note` 是屏上确认框里那句修改建议（`KA-48`）：与这一行现有总结一起交给引擎，只调写法
    与详略；空串＝按正文重写，提示词里不留重写上下文段。

    这一条腿**不走** `KA-4` 的单 worker 队列（它是同步请求），所以显式挡住同书在飞的批次：本机模型
    服务多为单并发，两路并跑只会让两边都超时；材料不够时宁可 422，也不发改一次「没读到正文」的调用。
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
    if node.node_kind != "outline" or node_type not in (NODE_TYPE_CHAPTER, NODE_TYPE_POINT):
        raise KpGateError(
            422,
            f"只有章节或知识点可以重生成总结：{node_id} 的类型是 {node_type}"
            "（习题与其他不生成总结）",
        )
    if node_type == NODE_TYPE_CHAPTER:
        revision = RevisionContext(
            note=revision_note, previous=(node.summary or "").strip(), previous_kind="总结"
        )
        material, _pages, _evidence = _regenerate_chapter_material(
            session, data_root=data_root, engine=engine, node=node, revision=revision
        )
        try:
            replied = engine.chapter(material)
        except EngineError as exc:
            raise KpGateError(502, f"模型调用失败（重生成总结）：{exc}") from exc
        summary = (replied.summary or "").strip()
        if not summary:
            raise KpGateError(
                422, f"模型没有给出总结，未落库：{node_id}（原文照旧，可重发一次）"
            )
        written = repo.regenerate_outline_summary(node_id, summary=summary, if_rev=if_rev)
        if not written.ok or written.node is None:
            raise KpGateError(409, f"总结未落库：{node_id}（并发改写，请重读后重试）")
        session.commit()
        return written.node
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
    revision = RevisionContext(
        note=revision_note, previous=(node.summary or "").strip(), previous_kind="总结"
    )
    bind = getattr(engine, "bind_batch", None)
    if callable(bind):
        bind(book_id=node.book_id, gen_batch=acceptance.gen_batch, revision=revision)
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


def regenerate_chapter_narrative(
    session: Session,
    *,
    data_root: Path | str,
    engine: KnowledgeEngine,
    node_id: str,
    if_rev: int | None = None,
    revision_note: str = "",
) -> tuple[AfKp, str]:
    """章导读单点生成（`KA-47`）：知识点梳理屏详情面「一键生成／重新生成」的导读腿，复用内容批次的章材料与章提示词。

    与内容批次章腿同一条取材与同一次调用（`_chapter_material` 三块＋`engine.chapter`），差别只在
    写面：这里**只写导读**（`write_narrative`＋`set_narrative_ref`），不碰章行 `summary`，并且
    **覆盖**已有导读——内容批次不覆盖是为了不冲掉人改过的那篇，而本端点就是人显式要求重写的那一次。
    判定序与单点重生成同族：404 → 409（`if_rev`）→ 422（非章节）→ 409（同书在飞）→ 422（无取证版本／
    无可用锚点页／区间内无可读页产物）→ 502（模型失败）→ 422（模型没给导读）→ 409（指针未落库）。
    同步腿一律先挡在飞批次：本机模型服务多为单并发，两路并跑只会让两边都超时。

    `revision_note` 与盘上现存的这一篇（`read_narrative` 现读）组成重写上下文（`KA-48`）交给引擎：
    「参照已有内容」因此有底稿可依；空串且无旧篇＝按正文新写。
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
    if node.node_kind != "outline" or node_type != NODE_TYPE_CHAPTER:
        raise KpGateError(
            422,
            f"只有章节可以生成导读：{node_id} 的类型是 {node_type}"
            "（导读的章材料是整章，单行不在这条腿上）",
        )
    _name, previous = read_narrative(session, data_root=data_root, node_id=node_id)
    revision = RevisionContext(
        note=revision_note, previous=(previous or "").strip(), previous_kind="导读"
    )
    material, _pages, _evidence = _regenerate_chapter_material(
        session, data_root=data_root, engine=engine, node=node, revision=revision
    )
    try:
        replied = engine.chapter(material)
    except EngineError as exc:
        raise KpGateError(502, f"模型调用失败（生成导读）：{exc}") from exc
    narrative = (replied.narrative or "").strip()
    if not narrative:
        raise KpGateError(422, f"模型没有给出导读，未落库：{node_id}（原文照旧，可重发一次）")
    name = write_narrative(session, data_root=data_root, node_id=node_id, content=narrative)
    written = repo.set_narrative_ref(node_id, name, if_rev=if_rev)
    if not written.ok or written.node is None:
        raise KpGateError(
            409, f"长文指针未落库：{node_id}（并发改写；正文已落盘，重读 rev 后重发即收敛）"
        )
    session.commit()
    return written.node, narrative


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
    only_failed: bool = False,
    retry_node_ids: Sequence[str] = (),
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

    `stage='exercise'`（`KA-53`）与前者的父集口径相同（题行的父是既有习题段行），作废面再窄一档：
    只覆 `model_ref='rule:exercise_item'` 的点行——模型抽出的知识点不是分题腿的旧读数，一次重跑
    不能顺手把它们判死。

    `only_failed=True`（`KA-52`）把 `content` 的作废面再收到 `retry_node_ids` 的父上：按节点重跑只
    重写那几节的读数，上一批成功的其它节不是这一批的过期读数。区间判据照旧（`KA-36`），收窄的是
    「谁在这个区间里」，不是把区间换成名单。
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
    if stage in ("content", "exercise"):
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
            node_kind=NODE_KIND_POINT if stage in ("content", "exercise") else None,
            model_ref=EXERCISE_ITEM_MODEL_REF if stage == "exercise" else None,
            parent_ids=list(retry_node_ids) if only_failed else None,
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
    #: 题行枚数（`KA-53`）：教材侧规则分题与习题集侧题目腿成出的点层行合计（第二十九键）。
    exercise_item_rows: int = 0
    #: 落盘的题包篇数（第三十键）：独立模式逐题一份；小于题行数即有待重解析的题。
    exercise_pack_rows: int = 0
    #: 配套模式合并命中并写出的题行数（第三十一键）：未命中的在对应章下新增，不算「合并」。
    merged_exercise_rows: int = 0
    #: 未匹配解答的枚数（第三十二键）：读出却对不上题号的解答，只披露、不落行不写题包。
    unmatched_solutions: int = 0
    #: 未匹配解答的逐条明细（第三十三键）：`item_no`／`source_pages`／`reason` 三项。
    unmatched_solution_details: tuple[dict[str, Any], ...] = ()


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

    def exercise(self, section: ExerciseInput) -> ExerciseReply:
        """题腿（`KA-53`）也要一调用一行：题面／解答是模型读的，这一腿的成本归因不能漏。"""
        return self._record(
            "exercise",
            lambda: self._engine.exercise(section),
            {
                "section": section.title,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "item_count": len(section.items),
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


def curation_result_for(
    *,
    failed_sections: int,
    failed_chapters: int,
    missing_pages: Sequence[int],
    unparsed_pages: Sequence[int],
) -> str:
    """批次结局（`KA-54`）：四类缺口一个都没有才算 `completed`，其余跑完的批次一律 `partial`。

    缺页与未解析页算进 `partial` 是刻意的：它们不是失败节，但那一批要读的正文有一段从未被读到，
    读成 `completed` 会让屏上亮出「梳理完成」。异常与中断两条腿不走这里（`KA-54` 的②③）。
    """
    if failed_sections or failed_chapters or missing_pages or unparsed_pages:
        return LastCurationResult.PARTIAL
    return LastCurationResult.COMPLETED


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
    if acceptance.stage in ("content", "exercise"):
        # 只补内容／教材侧分题（`KA-43`／`KA-53`）：目录腿一条不发起，骨架取在册行。
        # `content` 本批自己的行是点行，`exercise` 是题行——两者都不重造目录。
        outline = OutlineBuild(nodes=(), dropped_depth=0, dropped_malformed=0)
        registered_rows = _registered_outline_rows(
            session,
            acceptance.book_id,
            page_start=acceptance.page_start,
            page_end=acceptance.page_end,
        )
        outline_nodes = _outline_nodes_from_book(registered_rows)
        section_nodes = outline_nodes if acceptance.stage == "content" else []
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
        outline_nodes = list(outline.nodes)
        section_nodes = outline_nodes

    # 习题腿按书种类分两条（`KA-53`）：教材侧规则分题（题行父＝习题段行、零模型调用），习题集侧
    # 规则定界＋模型读内容（题行父＝题目所在的章／节行、逐题写题包）。两条腿的行集互不相交。
    book = BookRepository(session).get(acceptance.book_id)
    if (
        acceptance.stage == "exercise"
        and book is not None
        and book.doc_kind == DOC_KIND_EXERCISE
    ):
        # 配套模式（`KA-53`）按目标书有无分流：有目标＝跨书挂章，缺省＝本书自成一册；
        # 带选中题行的是修补批次（W17）——只改写那几枚，不走首轮的成行面。
        if acceptance.exercise_retry_node_ids:
            exercise_book = retry_exercise_book_items(
                session, engine, acceptance, evidence, outline_nodes, data_root=data_root
            )
        else:
            exercise_book = (
                merge_exercise_book_items
                if acceptance.target_book_id
                else extract_exercise_book_items
            )(session, engine, acceptance, evidence, outline_nodes, data_root=data_root)
    else:
        exercise_book = ExerciseBookBuild()
    exercise = (
        extract_exercise_items(session, acceptance, evidence, outline_nodes)
        if acceptance.stage == "exercise" and book is not None and book.doc_kind != DOC_KIND_EXERCISE
        else ExerciseItemBuild()
    )
    points = (
        extract_points(
            session,
            engine,
            acceptance,
            evidence,
            section_nodes,
            retry_node_ids=acceptance.retry_node_ids if acceptance.only_failed else None,
        )
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
        only_failed=acceptance.only_failed,
        retry_node_ids=acceptance.retry_node_ids,
    )
    # 习题集题腿的旧读数不在收尾的 `KA-36` 作废面里：收尾那一档按教材侧题行的产出者标识收窄，
    # 本腿的题行是模型腿（`llm:exercise`）。补在同一次提交里、翻面之后——作废的是**别的批次**的
    # 未确认候选行，本批刚翻成 `candidate` 的行按 `gen_batch` 排除在外。
    # 独立模式才有本书的旧题行要作废；配套模式的行落在目标书，本书的独立行集不是这一批的过期读数
    # （两模式可先后在本书上跑，行集互不覆盖）。修补批次一律不作废：它只改写选中的几行，其余
    # 未确认机器行仍是上一轮的有效读数。
    if (
        not acceptance.target_book_id
        and not acceptance.exercise_retry_node_ids
        and (exercise_book.item_rows or exercise_book.statuses)
    ):
        superseded = repo.reject_superseded_candidates(
            acceptance.book_id,
            acceptance.gen_batch,
            page_start=acceptance.page_start,
            page_end=acceptance.page_end,
            node_kind=NODE_KIND_POINT,
            model_ref=EXERCISE_LEG_MODEL_REF,
        )
        closing = replace(closing, superseded_rows=closing.superseded_rows + superseded)
    # 配套模式新成的行挂在目标书：收尾只翻本书的 `draft`，目标书那几行须在同一处一并翻面，
    # 否则目标书会一直显示「有在飞批次」且永远读不出整本绿。
    if acceptance.target_book_id and exercise_book.item_rows:
        flipped = repo.flip_batch_status(
            acceptance.target_book_id, acceptance.gen_batch, source="draft", target="candidate"
        )
        closing = replace(closing, candidate_rows=closing.candidate_rows + flipped)
    # 题包索引（`KA-53`／W16）：收尾后整份重写章级索引与来源书清单。题行落在哪本书，索引就写哪本
    # 书的 `kp/exercises/`；来源书清单恒写本书（它记的是「这本书的题去了哪里」）。派生视图写失败
    # 只告警，不把一批已经落好的行改判为失败。
    if (
        acceptance.stage == "exercise"
        and book is not None
        and book.doc_kind == DOC_KIND_EXERCISE
    ):
        write_exercise_indexes(
            session,
            data_root=data_root,
            book_id=acceptance.target_book_id or acceptance.book_id,
            gen_batch=acceptance.gen_batch,
        )
        write_exercise_meta(
            session,
            data_root=data_root,
            book_id=acceptance.book_id,
            kind="companion" if acceptance.target_book_id else "standalone",
            target_book_id=acceptance.target_book_id,
            gen_batch=acceptance.gen_batch,
            unmatched=exercise_book.unmatched_solution_details,
        )
    # `KA-54` ①：结局与收尾同一次提交——翻 `candidate` 成功而结局没落下，读面会把「刚跑完」
    # 读成上一批的读数。失败节／失败章与缺页／未解析页四类缺口任一成立即 `partial`。
    BookRepository(session).mark_curation_result(
        acceptance.book_id,
        result=curation_result_for(
            failed_sections=points.failed_sections + exercise_book.failed_sections,
            failed_chapters=chapters.failed_chapters,
            missing_pages=evidence.missing_pages,
            unparsed_pages=evidence.unparsed_pages,
        ),
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
        failed_sections=points.failed_sections + exercise_book.failed_sections,
        failed_section_details=(
            *points.failed_section_details,
            *exercise_book.failed_section_details,
        ),
        outline_summary_rows=points.outline_summary_rows,
        chapter_rows=chapters.chapter_rows,
        chapter_summary_rows=chapters.summary_rows,
        chapter_narrative_rows=chapters.narrative_rows,
        failed_chapters=chapters.failed_chapters,
        failed_chapter_details=chapters.failed_chapter_details,
        exercise_item_rows=exercise.item_rows + exercise_book.item_rows,
        exercise_pack_rows=exercise_book.pack_rows,
        merged_exercise_rows=exercise_book.merged_rows,
        unmatched_solutions=exercise_book.unmatched_solutions,
        unmatched_solution_details=exercise_book.unmatched_solution_details,
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
        only_failed=acceptance.only_failed,
        target_book_id=acceptance.target_book_id,
        retry_node_ids=tuple(acceptance.exercise_retry_node_ids),
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
            "exercise_item_rows": outcome.exercise_item_rows,
            "exercise_pack_rows": outcome.exercise_pack_rows,
            "merged_exercise_rows": outcome.merged_exercise_rows,
            "unmatched_solutions": outcome.unmatched_solutions,
            "unmatched_solution_details": list(outcome.unmatched_solution_details),
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
