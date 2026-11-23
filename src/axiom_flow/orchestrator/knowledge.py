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
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

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
    sanitize_material_ranges,
)
from axiom_flow.engines.base import (
    ChapterInput,
    ChapterReply,
    EngineError,
    ExerciseClassifyInput,
    ExerciseClassifyItem,
    ExerciseClassifyReply,
    ExerciseDraft,
    ExerciseInput,
    ExerciseItemInput,
    ExerciseMatchCandidate,
    ExerciseMatchChoice,
    ExerciseMatchInput,
    ExerciseReply,
    ExerciseVerifyInput,
    ExerciseVerifyItem,
    ExerciseVerifyReading,
    ExerciseVerifyReply,
    HeadingEvidence,
    KnowledgeEngine,
    OutlineEntry,
    PageBody,
    PageSample,
    ReferenceNote,
    RevisionContext,
    SectionBlock,
    SectionInput,
    SectionMember,
    SectionPoints,
    TocOutlineRow,
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
from axiom_flow.normalize.blocks import blocks_to_markdown
from axiom_flow.normalize.exercise import (
    ExerciseItem,
    exercise_item_key,
    homework_chapters,
    split_exercise_items,
    textbook_answer_key,
)
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
    TOP_LEVEL_REFERENCE_KINDS,
    BodyNameIndex,
    BodyReading,
    ExerciseOutlineHint,
    KindReviewRow,
    LocatedEntry,
    PointCall,
    PointSection,
    TocEntry,
    anchor_block_for_name,
    answer_section_window,
    chapter_material_blocks,
    chapter_name_hint,
    chapter_number,
    clean_entry_rows,
    drop_unlocated_toc_pages,
    entry_level,
    exercise_chapter_hints,
    exercise_entries,
    exercise_item_entries,
    fold_numbered_sub_items,
    is_exercise_label,
    kind_review,
    locate_entries,
    node_type_of,
    node_types_of,
    page_full_text,
    page_sample_text,
    pick_in_window,
    plan_point_calls,
    reference_role_for_name,
    select_material_blocks,
    toc_entries,
    toc_entries_across_pages,
    toc_marker_pages,
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

#: 目录页范围的连续扩展（`KA-57`）：首尾采样只看得见书头和书尾各 `TOC_PAGE_SCAN_LIMIT` 页，
#: 目录常常横跨采样边界。每轮向已知区间两侧各探 `TOC_PAGE_EXPAND_STEP` 页，最多 `…MAX_ROUNDS` 轮；
#: 首尾采样一条目录页都没认出时（目录起在采样之后），改为从前段窗口里逐轮找起头——
#: 范围封在 `TOC_PAGE_FRONT_PROBE_END` 页以内，再往后就不像「书前部的目录」了。
TOC_PAGE_EXPAND_STEP = 5
TOC_PAGE_EXPAND_MAX_ROUNDS = 3
TOC_PAGE_FRONT_PROBE_END = 30
#: 层级校订腿的单次输入封顶（`KA-57`）：候选只取章级与带节号的行走这一条腿，超出的行保持原文名字。
#: 封顶是吞吐判据不是质量判据——9B 一条 60 行的短回复已接近分钟级，再长就得拆批了。
TOC_OUTLINE_MAX_ROWS = 60

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
#: 种类审校拆出的习题行产出者标识（`KA-63`）：规则腿、零模型调用；重跑作废面靠这一枚值认清
#: 「这一腿的旧读数」，与人工行、模型目录行区分开。
EXERCISE_SPLIT_MODEL_REF = "rule:exercise_split"
#: 题包状态闭集（`KA-53`）：键名固定英文，屏上中文文案由根侧唯一常量表给——8902 只出键名。
EXERCISE_STATUS_COMPLETE = "complete"
EXERCISE_STATUS_PROBLEM_ONLY = "problem_only"
EXERCISE_STATUS_SOURCE_MISSING = "source_missing"
#: 解答缺位的题包占位行：题包三段标题恒定（`KA-53`），缺解答时这一段写这一句而不是留空。
EXERCISE_NO_SOLUTION_TEXT = "未配到解答"
#: 解答**不可用**的显式标记（REQ-179 R2）：与「未配到解答」（真没配到）区分——判定失败路径
#: 不再把失败说明固化成解答正文，改写这一句；`exercise_pack_status` 认它为「仅题面」，
#: 根侧屏面据此按「解答不可用」呈现，不冒充「有解答」。旧占位句的识别保留（存量读法不变）。
EXERCISE_UNAVAILABLE_TEXT = "解答不可用（模型判定失败）"
#: 一轮题腿调用的正文预算（字符）：真机 b07 有一节 4.5 万字，单轮生成被
#: `finish_reason=length` 截断致整节零行；超过预算的节按题序切成多轮，题号切分仍只做一次。
EXERCISE_CHUNK_TEXT_BUDGET = 12000
#: 配对校验腿的轮次预算（REQ-170 第三步）：一章的对按题面＋解答累计字符分轮，长章不压成一次。
EXERCISE_VERIFY_TEXT_BUDGET = 12000
#: 校验结论闭集（`KA-70`）：`ok` 四问都过；`suspect` 拿不准、交人看；`reject` 明确错了。
#: 三态都只披露、不否决成行（不删行、不改配对、不自动确认）。
EXERCISE_VERIFY_VERDICTS = ("ok", "suspect", "reject")

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
    """整本书的梳理完成读数（PLAN-029 W1）：四枚计数与两枚合取判据，全部读时派生、零写库。

    `done` 是**严口径**：统计域内每一行都有总结、每一行都被确认、且未定位零枚。任一条件不成立
    即 `false`；统计域零行也恒 `false`——没有可确认的行不等于「已经绿色」。
    `ready` 是**浅绿口径**（REQ-170 追加请求 A）：机器完成即行，人工背书进度不参与。配套受理门与
    匹配候选面只认这一条——17 本里没有一本被人工背书过，严口径让核心链路一次也跑不起来；空白总结
    与未定位锚点两道闸不动（它们保证锚点可解释），读面报绿仍走 `done`。
    """

    rows: int = 0
    endorsed_rows: int = 0
    blank_summaries: int = 0
    unlocated_rows: int = 0
    done: bool = False
    ready: bool = False


def book_stage_reading(
    rows: Sequence[AfKp],
    *,
    toc_pages: Sequence[int] | None,
    exercise_book: bool = False,
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
    `exercise_book=True` 透传第二趟豁免（PLAN-036 S4）：习题集的章按书种类认，不按子行认。
    """
    live = [row for row in rows if row.gen_status == "candidate"]
    types = node_types_of(live, exercise_book=exercise_book)
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
    ready = (
        bool(counted)
        and toc is not None
        and blank == 0
        and unlocated == 0
    )
    return BookStageReading(
        rows=len(counted),
        endorsed_rows=endorsed,
        blank_summaries=blank,
        unlocated_rows=unlocated,
        done=ready and endorsed == len(counted),
        ready=ready,
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
        # 浅绿门（`KA-53`，REQ-170 追加请求 A）：习题集的题要挂到教材已机器完成的结构上，目标连
        # 总结与锚点都不齐时合并只能产出一批没人确认过的读数。判据复用 PLAN-029 W1 的纯函数，
        # 不在这里另写一份三条件；人工背书进度不参与（浅绿及以上即放行）。
        # 修补不是重发首轮（同条）：选中的题行已经在目标书里，重解析只覆盖它们，不新挂章。
        reading = book_stage_reading(
            KpRepository(session).list_for_book(target_book_id),
            toc_pages=book_toc_pages(
                session, book_id=target_book_id, job_id=target.active_job_id
            ),
        )
        if not selected and not reading.ready:
            raise KpGateError(
                422,
                f"配套模式不受理：目标教材 {target_book_id} 尚未达到浅绿"
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
    `reference_rows`（附件行枚数，`KA-39`）、`dropped_duplicates`（成行处折叠掉的重影行数，D-11／D-12）。
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
    #: 成行处按 D-11／D-12 折叠掉的重影行数：屏上少的那几行在这里留一枚计数。
    dropped_duplicates: int = 0
    #: 三级细目折入二级的枚数（KA-62）：它们不单独成行，只把所属二级的显示段撑到下一节之前。
    folded_sub_items: int = 0
    #: 逐页判页腿实判过的页（P-1~P-5）：空集＝这一轮没走逐页判定（无标记或引擎无此腿），
    #: 有集＝这些页的判定就是目录区间的来源；整本条目腿接手时这一列仍是实判面，不作结构结论。
    judged_pages: tuple[int, ...] = ()


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


#: 印刷的选学标记（真机 b18 的 `\*2.5 …`／`\* 第 5 章 …`）：排版器把星号转成 LaTeX 的 `\*` 抄进名字。
#: 它属于排版而不属于书写的名字，成行与读面都按同一口径卸掉；只认行首，名字中间的星号一概不碰。
_OPTIONAL_MARKER = re.compile(r"^(?:\\?\*)+[\s\u3000]*")


def split_optional_marker(name: str) -> tuple[str, bool]:
    """名字 → `(卸掉行首选学标记的名字, 是否选学)`；整名只有标记时原样保留，不造空名。"""
    stripped = _OPTIONAL_MARKER.sub("", name or "", count=1).strip()
    if not stripped:
        return name or "", False
    return stripped, stripped != (name or "")


def _outline_row_key(
    parent_id: str | None, norm_key: str, role: str, kind: str
) -> tuple[str, str]:
    """目录行的去重键（D-11／D-12）：成行处与读面共用这一把，不造第二份口径。

    常规键是「同父同名」——跨章同名的节各自保留。六类书级附件改按全书唯一判：它们的身份就是
    「这本书的前言／索引」，挂在目录行还是附件页行是同一条事实的两种读数（真机「一个前言两行」
    正是两个来源各出一枚）。习题段不在其中：章末习题是章的子行，每章各有一枚。
    """
    if role == TOC_ROLE_REFERENCE and kind in TOP_LEVEL_REFERENCE_KINDS:
        return ("", norm_key)
    return (parent_id or "", norm_key)


@dataclass(slots=True)
class _OutlineAssembler:
    """读数序列 → `af_kp` 行的那一步，两条通路共用（`KA-40`）。

    共用不是为了省代码，是为了**不许两条通路各定一次行**：逐页早落与定稿重算必须给出一模一样的
    名字、层级、`norm_key`、`model_ref` 与 `meta`——同批改写面只有锚、序、挂父三列（`KA-40`），
    任何别的不一致都会永久留在行里。`reuse` 是身份通道：`entry_index` 对上的行沿用已在册的
    `node_id`，对不上就发新号（`KA-11` 的「机器不与人工争身份」在这里只剩「自己这两批不互相造行」）。

    去重也在这同一步（D-11／D-12）：键在发号之前算，重影行连身份都不占——早落面与定稿面因此
    对「留哪一枚」给出同一个答案（书序第一枚），落库面只差定稿追加的附件行。读数序就是书序：
    成行处的调用方按书前件 → 目录行 → 书尾件排好再喂进来。
    """

    book_id: str
    job_id: str
    gen_batch: str
    reuse: dict[int, str] = field(default_factory=dict)
    nodes: list[OutlineNode] = field(default_factory=list)
    chapter_id: str | None = None
    seen: set[tuple[str, str]] = field(default_factory=set)
    dropped_depth: int = 0
    dropped_malformed: int = 0
    dropped_duplicates: int = 0
    folded_sub_items: int = 0

    def add(self, reading: OutlineReading) -> OutlineNode | None:
        """成一行；不合形状的行只计数不进节点（`KA-6`），同键的第二枚只计数（D-11／D-12）。

        越界与空名在两趟重算里同判，故身份不漂；去重键也只在名字、父位与身份位定下之后才判，
        两趟判的是同一个键。三级细目不发行（KA-62）：它是二级节的内部结构，发行就会把二级的
        显示段从第一个细目处切断——真机 b12 的「§3 下面 34./35. 各占一行」正是这条。折掉的枚数
        单独计数，与「层级越界丢行」分开披露。
        """
        name, optional = split_optional_marker(reading.text.strip())
        depth = reading.level
        if not name:
            self.dropped_malformed += 1
            return None
        if depth == OUTLINE_MAX_DEPTH + 1:
            self.folded_sub_items += 1
            return None
        if depth > OUTLINE_MAX_DEPTH:
            self.dropped_depth += 1
            return None
        norm_key = norm_key_for(name)[:200]
        parent_id: str | None = None if depth == 1 else self.chapter_id
        key = _outline_row_key(parent_id, norm_key, reading.role, reading.kind)
        if key in self.seen:
            self.dropped_duplicates += 1
            return None
        self.seen.add(key)
        if reading.entry_index is None:
            node_id = new_node_id()
        else:
            node_id = self.reuse.get(reading.entry_index) or new_node_id()
            self.reuse[reading.entry_index] = node_id
        if depth == 1:
            self.chapter_id = node_id
        meta: dict[str, Any] = {"toc_role": reading.role}
        if reading.kind:
            meta["toc_kind"] = reading.kind
        if optional:
            meta["optional"] = True
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
                norm_key=norm_key,
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


def _toc_samples(pages: Sequence[BlocksPage]) -> tuple[PageSample, ...]:
    """判页腿的一批输入：同页号只留一页（首尾采样在薄书上会重叠），顺序按传入。"""
    return tuple(
        PageSample(page_no=page.page, text=page_sample_text(page))
        for page in {page.page: page for page in pages}.values()
    )


def _expand_toc_pages(
    invoke: Callable[[Sequence[PageSample]], TocPagePick],
    pick: TocPagePick,
    blocks: Sequence[BlocksPage],
) -> TocPagePick:
    """判页读数 → 连续整段（`KA-57`）：向已知区间两侧逐轮外探，直到探不到新页或轮数用尽。

    首尾采样之外的两段目录页靠这一腿补齐；它只接受本轮**探过的那几页**里被判成目录页的页号——
    模型在探页回复里顺手报回的远处页号不受理，否则「一次判页」就会重新变成「全书找一遍」。
    初始读数里一条目录页都没有时，改从前段窗口（采样之后到 `TOC_PAGE_FRONT_PROBE_END`）逐轮找起头，
    这时一轮没认出也继续往后找，找不到才停。附件页仍然只认初始那份：它们在首尾采样里已经看全了。
    """
    available = {page.page: page for page in blocks}
    known = set(pick.toc_pages)
    front_probe: list[int] = []
    if not known:
        front_probe = [
            page_no
            for page_no in range(TOC_PAGE_SCAN_LIMIT + 1, TOC_PAGE_FRONT_PROBE_END + 1)
            if page_no in available
        ]
    for _ in range(TOC_PAGE_EXPAND_MAX_ROUNDS):
        if not known:
            wave = front_probe[:TOC_PAGE_EXPAND_STEP]
            front_probe = front_probe[TOC_PAGE_EXPAND_STEP:]
        else:
            low, high = min(known), max(known)
            wave = [
                page_no
                for page_no in range(max(1, low - TOC_PAGE_EXPAND_STEP), low)
                if page_no in available
            ]
            wave.extend(
                page_no
                for page_no in range(high + 1, high + 1 + TOC_PAGE_EXPAND_STEP)
                if page_no in available
            )
        wave = [page_no for page_no in wave if page_no not in known]
        if not wave:
            break
        try:
            reply = invoke(_toc_samples([available[page_no] for page_no in wave]))
        except EngineError as exc:
            logger.warning("目录判页扩展腿失败，按已知范围继续（KA-57）：%s", exc)
            break
        probed = set(wave)
        found = {page_no for page_no in reply.toc_pages if page_no in probed}
        if not found and known:
            break
        if not found and not front_probe:
            break
        known |= found
    return replace(pick, toc_pages=tuple(sorted(known)))


def _pick_toc_pages(
    engine: KnowledgeEngine, blocks: Sequence[BlocksPage], *, expand: bool = False
) -> TocPagePick | None:
    """判页腿（`KA-37`）：样本＝前 `TOC_PAGE_SCAN_LIMIT` 页与末同数页；拿不到读数就返回 `None`。

    `None` 是「本腿没给出可用地」而不是「这本书没有目录」——三条来路都折成它：引擎没有这条腿
    （协议内的其他实现、单测里的替身）、调用抛 `EngineError`、返回的目录页集为空。三种都由调用方
    回落 `KA-33` 条目腿，目录零行才是真的不可能发生。

    `expand=True` 是整段抄录通路（`KA-57`）要的读法：首尾采样认出的区间再向两侧连续扩展，直到
    目录页成一段。既有逐页整理通路仍用采样读数本身，`expand` 不改那条通路的输入。
    """
    invoke = getattr(engine, "toc_pages", None)
    if not callable(invoke) or not blocks:
        return None
    tail = TOC_PAGE_SCAN_LIMIT
    picked = list(blocks[:tail])
    if len(blocks) > tail:
        picked.extend(blocks[-tail:])
    try:
        pick = invoke(_toc_samples(picked))
    except EngineError as exc:
        logger.warning("目录判页腿失败，全书回落条目腿（KA-37）：%s", exc)
        return None
    if not expand:
        return pick
    return _expand_toc_pages(invoke, pick, blocks)


def _toc_page_text(page: BlocksPage) -> str:
    """整理腿的输入＝该页段落块原文（`KA-37`）：逐块成行、不截断——截断会把一条条目名切成两半。"""
    return "\n".join(
        (block.text or "").strip()
        for block in page.blocks
        if block.type is BlockType.PARAGRAPH and (block.text or "").strip()
    )


@dataclass(frozen=True, slots=True)
class _SequentialTocRead:
    """逐页判页腿的一次读数（P-1~P-5）：目录页、条目、失败页与实判页都是同一趟里的事实。"""

    toc_pages: tuple[int, ...] = ()
    entries: tuple[tuple[int, TocEntry, str], ...] = ()
    fallback_pages: tuple[int, ...] = ()
    judged_pages: tuple[int, ...] = ()


def _sequential_toc_readings(
    engine: KnowledgeEngine,
    blocks: Sequence[BlocksPage],
    *,
    on_page: Callable[[list[tuple[int, TocEntry, str]], int], None] | None = None,
) -> _SequentialTocRead | None:
    """逐页判页腿（P-1~P-5）：标记页起逐页整页判定，判定为目录页时同一次读数带回本页条目。

    区间右边界只由**判定**给：从候选页起向后逐页问「这一页是不是目录页」，第一张非目录页即停，
    该页一条也不接。候选页＝前 10 页里带「目录／Contents」独立行的那几页；前 10 页没有标记时按
    页序扫全书（`toc_marker_pages`，零模型调用）。候选一个都没判成目录页时返回空读数，由调用方
    走整本条目腿（P-4）——「没有目录页」与「模型没答」在这里是同一档披露，不冒充结构结论。

    单页失败（`EngineError`）只坏那一页：该页退确定性条目腿并计数，后续页照判（P-5）；失败页落
    在判出的目录区间里，仍计入目录页集——它的行是目录行，不能留在正文面里当回声。连续两页失败按
    「这条腿不可用」收手，免得引擎整体不可用时把整本正文逐页当目录问一遍。引擎没有这条腿时返回
    `None`，旧读数（`toc_pages`＋`toc_entries`）原样接管。
    """
    invoke = getattr(engine, "toc_page", None)
    if not callable(invoke) or not blocks:
        return None
    marked = toc_marker_pages(blocks)
    if not marked:
        return _SequentialTocRead()
    positions = {page.page: index for index, page in enumerate(blocks)}
    entries: list[tuple[int, TocEntry, str]] = []
    found: list[int] = []
    fallback: list[int] = []
    judged: list[int] = []
    consumed: set[int] = set()
    for candidate in marked:
        if candidate in consumed:
            continue
        failures = 0
        for page in blocks[positions[candidate] :]:
            if page.page in consumed:
                break
            consumed.add(page.page)
            judged.append(page.page)
            try:
                reading = invoke(page.page, page_full_text(page))
            except EngineError as exc:
                failures += 1
                logger.warning("逐页判页腿失败，该页回落条目腿（P-5）：p%s %s", page.page, exc)
                fallback.append(page.page)
                entries.extend(
                    (page.page, entry, OUTLINE_MODEL_REF_TOC) for entry in toc_entries(page)
                )
                if failures >= 2:
                    break
                continue
            failures = 0
            if not reading.is_toc:
                break
            found.append(page.page)
            start = len(entries)
            for row in reading.entries:
                entries.extend(_cleaned_model_entries(page, row))
            if on_page is not None and len(entries) > start:
                on_page(entries, start)
    if not found:
        return _SequentialTocRead(fallback_pages=tuple(sorted(set(fallback))), judged_pages=tuple(judged))
    if not entries:
        # 判成了目录页却一条行都没回来：整页退确定性抄录，比把整本推回条目腿更贴书。
        pages = [page for page in blocks if page.page in set(found)]
        entries = [
            (page_no, entry, OUTLINE_MODEL_REF_TOC)
            for page_no, entry in toc_entries_across_pages(pages)
        ]
        fallback.extend(found)
    return _SequentialTocRead(
        toc_pages=tuple(sorted({*found, *fallback})),
        entries=tuple(entries),
        fallback_pages=tuple(sorted(set(fallback))),
        judged_pages=tuple(judged),
    )


def _candidate_lines(
    pages_by_no: Mapping[int, BlocksPage], page_nos: Iterable[int]
) -> tuple[tuple[int, str], ...]:
    """核对腿的候选行（`KA-38`）：给定页里，每块取首个非空行。

    只取首行是把「比对的面积」交还给判据：一条条目在正文里就是那一行的起头，整页正文喂进去既超
    本机吞吐，也让模型在无关段落里挑花眼。页集由调用方给：有印刷页码的行取偏移页 ±slack，没有
    印刷页码的行由上下已定锚行夹出（Q5 甲）。
    """
    lines: list[tuple[int, str]] = []
    for page_no in page_nos:
        page = pages_by_no.get(page_no)
        if page is None:
            continue
        for block in page.blocks:
            for line in (block.text or "").splitlines():
                if line.strip():
                    lines.append((page_no, line.strip()[:120]))
                    break
    return tuple(lines)


def _resolved_pages(
    count: int,
    located_by_index: Mapping[int, LocatedEntry],
    rescued: Mapping[int, BodyReading],
) -> list[int | None]:
    """逐行「此刻已经定得下来的锚页」（`KA-38`／Q5 甲）：前缀命中与容错腿救回的都算。

    核对腿只能拿已定锚的邻居给无页码行夹候选面；未定位行自己没有页，不能给下一行当边界。
    """
    pages: list[int | None] = []
    for index in range(count):
        item = located_by_index.get(index)
        if item is not None and item.located and item.page_no is not None:
            pages.append(item.page_no)
        elif (saved := rescued.get(index)) is not None:
            pages.append(saved.page_no)
        else:
            pages.append(None)
    return pages


def _clamped_candidate_pages(prev_page: int | None, next_page: int | None) -> tuple[int, ...]:
    """无印刷页码行的候选页（Q5 甲）：由上下两枚已定锚行夹出，缺一侧时退回该侧邻页。

    两侧都有锚时取两端之间的页**含两枚锚页**——两条目录行挤在同一页是常态，严格开区间会让这种
    行永远核不中。区间宽过 `2 * TOC_VERIFY_PAGE_SLACK + 1` 页时只取两端各 `slack + 1` 页的窄条：
    中间整段没有判据可依，全量喂给模型既超本机吞吐，也等于让它在一大段正文里猜。只有一侧有锚时
    以那一页为界取 `slack + 1` 页；两侧都没有 ⇒ 空集，这一行不交核对。
    """
    if prev_page is None:
        if next_page is None:
            return ()
        return tuple(range(next_page - TOC_VERIFY_PAGE_SLACK, next_page + 1))
    if next_page is None:
        return tuple(range(prev_page, prev_page + TOC_VERIFY_PAGE_SLACK + 1))
    low, high = min(prev_page, next_page), max(prev_page, next_page)
    if high - low <= 2 * TOC_VERIFY_PAGE_SLACK:
        return tuple(range(low, high + 1))
    left = range(low, low + TOC_VERIFY_PAGE_SLACK + 1)
    right = range(high - TOC_VERIFY_PAGE_SLACK, high + 1)
    return tuple(sorted({*left, *right}))


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


def _outline_candidates(entries: Sequence[tuple[int, TocEntry]]) -> list[TocOutlineRow]:
    """层级校订腿的输入（`KA-57`）：只交章级行与带节号的行，封顶 `TOC_OUTLINE_MAX_ROWS`。

    候选面收窄的理由是这一腿只补「章号／节号被 OCR 吃掉」这一类伤：正文条目（无编号的节标题）交给
    它改，模型就会开始改写说法，而那正是 `KA-50`「名以正文为准」要避免的第二只手。
    """
    candidates: list[TocOutlineRow] = []
    for index, (_, entry) in enumerate(entries):
        if entry.level == 1 or entry.name.startswith("§"):
            candidates.append(TocOutlineRow(index=index, level=entry.level, name=entry.name))
        if len(candidates) >= TOC_OUTLINE_MAX_ROWS:
            break
    return candidates


def _review_stream_entries(
    engine: KnowledgeEngine, entries: list[tuple[int, TocEntry]]
) -> list[tuple[int, TocEntry]]:
    """整段抄录的名字过一遍校订腿（`KA-57`）：只改名字，页码与层级一律照原文。

    校订腿是可选腿：引擎没有这条腿、调用失败、或没有候选行都不算失败，条目按抄录原文继续。
    回包里的序号必须落在本轮候选里；同一序号只认第一枚——模型给出的多余行不得替书造条目。
    """
    invoke = getattr(engine, "toc_outline", None)
    if not callable(invoke) or not entries:
        return list(entries)
    candidates = _outline_candidates(entries)
    if not candidates:
        return list(entries)
    try:
        corrections = invoke(candidates)
    except EngineError as exc:
        logger.warning("目录校订腿失败，条目保持原文名字（KA-57）：%s", exc)
        return list(entries)
    allowed = {row.index for row in candidates}
    names: dict[int, str] = {}
    for row in corrections:
        if row.index in allowed and row.index not in names:
            names[row.index] = row.name
    if not names:
        return list(entries)
    return [
        (page_no, entry if (name := names.get(index)) is None else replace(entry, name=name))
        for index, (page_no, entry) in enumerate(entries)
    ]


def _stream_entries(
    engine: KnowledgeEngine,
    pages_by_no: Mapping[int, BlocksPage],
    toc_pages: Sequence[int],
) -> tuple[list[tuple[int, TocEntry, str]], list[int]]:
    """整段目录页 → 行读数（`KA-57`）：确定性抄录一次，只把章级名字交模型校订。

    页码只由原文给；跨页续行、一页多块、一行多条在 `toc_entries_across_pages` 里处理完。取证集里
    没有的目录页按页号记进缺失面，不拿别的页顶替（`KA-2` 同一纪律）。`model_ref` 用 `rule:toc`——
    这些行是规则抄出来的，模型只参与了名字校订，账面上要分得出这一点。
    """
    readable = [pages_by_no[page_no] for page_no in toc_pages if page_no in pages_by_no]
    rows = _review_stream_entries(engine, list(toc_entries_across_pages(readable)))
    entries = [(page_no, entry, OUTLINE_MODEL_REF_TOC) for page_no, entry in rows]
    missing = [page_no for page_no in toc_pages if page_no not in pages_by_no]
    return entries, missing


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
    rescued: Mapping[int, BodyReading] | None = None,
) -> dict[int, tuple[int, int]]:
    """核对腿（`KA-38`）：偏移定锚查不中的坏行攒一批一次问，只改「落在哪一页」。

    三条零调用：偏移标定不出（那时「全部未定位」是标定失败的读数，问模型等于让模型替机器编页码）、
    没有坏行、引擎不持这条腿。坏行面＝第一遍与容错腿（`KA-49`）都没救回的行：带印刷页码的行以
    「印刷页＋偏移 ±slack」为候选面；**没有印刷页码的行同样交核对（Q5 甲）**，候选页由上下两枚
    已定锚行夹出（`_clamped_candidate_pages`），两侧都没有已定锚邻居时这一行没有候选，不发起——
    没有正文面可比。前缀命中即定锚（Q6 乙）：已定位的行不进坏行面，也不做第二道逐行复核。
    核对判出的页号必须落在本条的候选页里，域外页号整条不认：下游会把锚指到没读过正文的页上，
    那是假锚。命中行的块序由 `anchor_block_for_name` 在那一页里指认；名字与层级一律照旧。
    """
    invoke = getattr(engine, "toc_verify", None)
    if offset is None or not callable(invoke):
        return {}
    rescued = rescued or {}
    pages_by_no = {page.page: page for page in body_pages}
    resolved = _resolved_pages(len(entries), located_by_index, rescued)
    pending: list[tuple[int, TocEntry, tuple[tuple[int, str], ...]]] = []
    for index, (_, entry, _) in enumerate(entries):
        if entry.level > OUTLINE_MAX_DEPTH:
            # 三级细目不进核对（KA-62）：它不会成行，问一次就是白花一次模型调用。
            continue
        item = located_by_index[index]
        if item.located or index in rescued:
            continue
        if entry.printed_page is not None:
            target = entry.printed_page + offset
            candidates = _candidate_lines(
                pages_by_no, range(target - TOC_VERIFY_PAGE_SLACK, target + TOC_VERIFY_PAGE_SLACK + 1)
            )
        else:
            prev_page = next((page for page in reversed(resolved[:index]) if page is not None), None)
            next_page = next((page for page in resolved[index + 1 :] if page is not None), None)
            page_nos = _clamped_candidate_pages(prev_page, next_page)
            if not page_nos:
                continue
            candidates = _candidate_lines(pages_by_no, page_nos)
        pending.append((index, entry, candidates))
    if not pending:
        return {}
    items = [
        TocVerifyItem(name=entry.name, printed_page=entry.printed_page, candidates=candidates)
        for _, entry, candidates in pending
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
    """章末习题段成行（`KA-46`）：每章在**自己那一棵子树**的页区间里找唯一的章尾整块习题标签。

    区间口径与点腿同一条树序算式：章管到「下一条同级或更浅行之前」。候选页只认本批已读到正文的页
    （取证集里没有的页不造结构结论，`KA-2` 同轴），目录页与「本页已有一枚习题行」的页整页跳过——
    后者是重跑与模型正腿撞车的去重口，不靠 `norm_key`：同一段的名字可能是 `习题 1.1` 也可能是 `习题`。

    章尾判据（真机 b18 改判）：一本书**每节后面都挂习题**时，这些标签不在目录里，按章区间找第一枚
    就会把 1.1 的习题行挂到第 1 章末尾、锚却停在章中——屏上多出一枚目录里没有的「习题」，位置还错。
    因此本章标签页多于一枚即判「按节布置习题」，本腿零行；唯一一枚也必须在章内最后一枚 `depth=2`
    行之后才算章尾。认不出的书出零行；本腿不发一次模型调用、不写 `summary`、不改任何产物。

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
        label_pages = [
            page_no
            for page_no in range(row.page_no, end_page + 1)
            if page_no not in skipped
            and page_no in pages_by_no
            and exercise_entries(pages_by_no[page_no])
        ]
        if len(label_pages) != 1:
            continue
        last_section_page = max(
            (entry.page_no for entry in readings[index + 1 : end_index + 1] if entry.level == 2),
            default=row.page_no,
        )
        page_no = label_pages[0]
        if page_no < last_section_page or page_no in taken:
            continue
        page = pages_by_no[page_no]
        entry = exercise_entries(page)[0]
        taken.add(page_no)
        found[end_index] = [
            OutlineReading(
                page_no=page_no,
                block_index=entry.block_index,
                level=2,
                text=entry.name,
                model_ref=OUTLINE_MODEL_REF_EXERCISE,
                role=TOC_ROLE_REFERENCE,
                kind="exercise",
            )
        ]
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
    按未定位披露并**不成行**（`KA-33` 改判）——把目录页当正文锚只会把书自己的目录抄进目录，
    名字进 `unlocated_outline_entries` 供人工重挂。身份位同样两路（`KA-39`）：正腿的行由判页
    读数给（附件页另成顶级行），回落腿的行只能按名字集合判。

    取材有两条腿（`KA-57`）：引擎持 `toc_outline` 腿时走整段抄录——判页范围先连续扩展成一段，
    整段目录页由确定性抄录一次（页码只由原文给、跨页续行拼回），章级名字再交一次有界校订；这条
    通路不做逐页早落。其余引擎照旧逐页整理（`KA-37`），下面 `sink` 一段只描述那条通路。

    `sink` 给的是「边整理边落库」那一条通路（`KA-40`）：每页整理完就按**此刻已收到的条目**标定偏移、
    定锚、成行并交 `land`，落库序就是发出序；页级复核与核对腿都在全书读数上发生，所以定稿还要按既有
    通路整本重算一遍，两次读数不同的行交 `repoint` 改写（同批改写只有锚、序、挂父三列，名字与层级
    在两趟里由同一把装配器给出）。回落页、附件行、heading 行不进逐页落行面——它们的位置判据要全书
    读数才定得下来，先落再删就是造出一批没人认领的行。`sink=None` 时本段一行不发，产出与判据与既有
    通路逐字相同。
    """
    pages_by_no = {page.page: page for page in evidence.blocks}
    # 整段抄录通路（`KA-57`）：引擎持 `toc_outline` 腿时，目录范围连续扩展、整段确定性抄录一次、
    # 章级名字交一次校订；逐页整理通路原样留给协议内的其他实现（`getattr` 探测是那条分岔口）。
    stream = callable(getattr(engine, "toc_outline", None))
    sequential = callable(getattr(engine, "toc_page", None))
    pick: TocPagePick | None = None
    toc_pages: tuple[int, ...] = ()
    fallback_pages: list[int] = []
    judged_pages: tuple[int, ...] = ()
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

    if sequential:
        # 逐页判页腿（P-1~P-5）：目录区间由整页判定给，条目与判定同一次读数回来。
        read = _sequential_toc_readings(
            engine, evidence.blocks, on_page=None if sink is None else land_page
        )
        judged_pages = read.judged_pages if read is not None else ()
        # 附件页仍走采样判页：它认的是书首／书尾那几页的附件，与目录区间是两件事。
        pick = _pick_toc_pages(engine, evidence.blocks, expand=False)
        if read is not None and read.toc_pages:
            toc_pages = read.toc_pages
            entries = list(read.entries)
            fallback_pages = list(read.fallback_pages)
            body_pages = [page for page in evidence.blocks if page.page not in set(toc_pages)]
    else:
        pick = _pick_toc_pages(engine, evidence.blocks, expand=stream)
        if pick is not None and pick.toc_pages:
            toc_pages = tuple(pick.toc_pages)
            body_pages = [page for page in evidence.blocks if page.page not in set(toc_pages)]
            if stream:
                # 整段抄录要读到整段页码才能拼跨页续行，逐页早落在这里无从谈起；行在定稿一遍里落。
                entries, _ = _stream_entries(engine, pages_by_no, pick.toc_pages)
            else:
                entries, fallback_pages = _entries_from_pages(
                    engine,
                    [pages_by_no[page_no] for page_no in pick.toc_pages if page_no in pages_by_no],
                    on_page=None if sink is None else land_page,
                )
                fallback_pages.extend(
                    page_no for page_no in pick.toc_pages if page_no not in pages_by_no
                )
    if not entries:
        entries = _whole_book_entries(evidence.blocks)
        toc_pages = tuple(sorted({page_no for page_no, _, _ in entries}))
        body_pages = [page for page in evidence.blocks if page.page not in set(toc_pages)]

    # 三级细目识别（`KA-62`）：`§` 节下的纯编号条目降为三级，成行处不发行、只把二级的显示段撑开。
    # 下标按位置对齐——逐页早落用的 `entry_index` 与定稿这一趟读的是同一串位置。
    folded_levels = fold_numbered_sub_items([entry for _, entry, _ in entries])
    entries = [
        (toc_page, folded, model_ref)
        for (toc_page, _, model_ref), folded in zip(entries, folded_levels, strict=True)
    ]

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
        engine, entries, located_by_index, location.offset, body_pages, rescued
    )
    readings: list[OutlineReading] = []
    unlocated: list[str] = []
    verified: list[str] = []
    folded_sub_items = 0
    for index, (toc_page, entry, model_ref) in enumerate(entries):
        if entry.level > OUTLINE_MAX_DEPTH:
            # 三级细目不定位、不核对、不成行（KA-62）：它在屏上的位置由所属二级的显示段承担。
            folded_sub_items += 1
            continue
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
            unlocated.append(entry.name)
            # 偏移标定成功的书里，查不中的是例外条目（b12 的「及其应用」碎片）：把目录页当成它的
            # 正文页，等于把书自己的目录抄进目录，故不成行、只在账面点名（KA-33 改判）。偏移整本
            # 标定不出时条条都查不中——这时丢行就是整本零目录，仍按旧读数落回目录页自保。
            if location.offset is not None:
                continue
            page_no, block_index = toc_page, item.block_index
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
    if stream and reference_readings:
        # 整段抄录已经把附录／索引／校订后记这类成组条目录成了行，判页腿再按页报一遍同一组名字就是重影：
        # 索引五页会变成六枚顶级「索引」（真机 b12 的读数）。同名的附件页行让给目录行——目录行给了
        # 这组条目的起点页与书序位置，页级细目留给人翻原书，不在这里各占一行。
        toc_reference_names = {
            norm_key_for(reading.text)
            for reading in readings
            if reading.role == TOC_ROLE_REFERENCE
        }
        reference_readings = [
            reading
            for reading in reference_readings
            if norm_key_for(reading.text) not in toc_reference_names
        ]
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
        dropped_duplicates=final.dropped_duplicates,
        # 两条来路各数一段：条目腿的三级在定稿循环里跳过（不到装配器），heading 回落的三级直接进
        # 装配器。两段不会重叠——一行只走其中一条。
        folded_sub_items=folded_sub_items + final.folded_sub_items,
        judged_pages=judged_pages,
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
    #: 同一节点第二枚起的追加窗口数合计（`KA-64`，第四十键）：超预算的节分几次发问。
    point_windows: int = 0
    #: 合并调用组数（`KA-64`，第四十一键）：相邻小节点并成一笔的发问次数。
    point_merges: int = 0
    #: 写上 `meta.material_ranges` 的在册点行数（`KA-66`，第四十二键）：只数真正落笔的行。
    material_ranges_rows: int = 0


@dataclass(frozen=True, slots=True)
class SectionSpan:
    """一个节的取材区间：`parent_id` 为空表示这段正文不在目录树上，抽出的点直接入池。

    `start_block`／`end_block` 是两端的块界（含端点，`None` 表示该端取整页）：只在起点页／终点页
    生效。页界不够用是真机 b05 的「导引」——它的正文延续到下一枚知识点起始页，页级算式把终点压到
    前一页就整段丢掉；带块号的锚点能把「下一页页首到下一标题块之前」精确切回来。
    """

    parent_id: str | None
    title: str
    page_start: int
    page_end: int
    start_block: int | None = None
    end_block: int | None = None


@dataclass(frozen=True, slots=True)
class SpanRow:
    """切区间的一枚输入行：排序键 `(order_hint, page)` ＋ 身份 `(node_id, name)` ＋ 树位 `(depth, toc_role, toc_kind)`。

    点腿给的是本批定形的目录行，读面给的是库里的在册目录行——两处形状不同，算式必须同一处
    （`KA-42`）：屏上圈的区间要与模型实际读过的那一段逐字对得上。树位只参与终点判（DEFECT-028），
    缺省 `depth=1`／`toc_role=knowledge`／`toc_kind=''` 即「平铺目录」，与改判前的读数逐字相同；
    `toc_kind` 只用来把习题段从附件行的单页退化里摘出来（`KA-46`）。`block` 是锚点页上最早那块：
    下一行的块号决定上一节的正文在那一页停在哪里（真机 b05 的跨页点）。`parent_id` 只服务显示段
    派生（D-3／D-4 的展示序与「章止于首子」），`section_spans_of` 不读它。
    """

    node_id: str | None
    name: str
    order_hint: int | None
    page: int
    depth: int = 1
    toc_role: str = "knowledge"
    toc_kind: str = ""
    parent_id: str | None = None
    block: int | None = None


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

    跨页尾页按**块界**切（真机 b05「导引」p9–10、「有序集」p10–12）：下一同级行的锚点落在起始页
    第 `k>0` 块时，本行终点延到那一页、`end_block=k-1`——上一节的正文常常续写在下一节标题所在的
    那一页上半部，按页取会整页丢掉。`k=0`（标题在页首）仍退一页；取材与显示共用这一组块界。
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
            spans.append(
                SectionSpan(
                    row.node_id,
                    row.name,
                    row.page,
                    min(row.page, last_page),
                    start_block=row.block,
                )
            )
            tail_is_reference = True
            continue
        boundary = next(
            (later for later in ordered[index + 1 :] if later.depth <= row.depth), None
        )
        end = last_page
        end_block: int | None = None
        if boundary is not None and boundary.page <= last_page:
            if (
                boundary.block is not None
                and boundary.block > 0
                and boundary.page >= row.page
            ):
                # 本节的正文可延续到下一行起始页，止于它的标题块之前（真机 b05「导引」跨 p9–10）。
                end = max(row.page, boundary.page)
                end_block = boundary.block - 1
            else:
                end = max(row.page, boundary.page - 1)
        spans.append(
            SectionSpan(
                row.node_id,
                row.name,
                row.page,
                end,
                start_block=row.block,
                end_block=end_block,
            )
        )
        tail_is_reference = False
    covered_end = max((span.page_end for span in spans), default=0)
    # 末行是附件行时，它后面的页子就是附件自己的正文（书目、版图、跋）：不为读不到的书尾再发一次问。
    if covered_end < last_page and not tail_is_reference:
        spans.append(SectionSpan(None, "", covered_end + 1, last_page))
    return spans


@dataclass(frozen=True, slots=True)
class DisplaySpan:
    """一枚显示段：`page_start`／`page_end` 是屏上切片的范围，两端空＝这一行不展示。

    两键可空有两种形状：整段为空（两端皆 `None`，D-6 的「顺延越过章末」）与书末未知（有起点无
    终点，D-9）。读面不需要区分形状，两者都如实发 `None`。`end_block` 是终点页上属于本行的最后
    一块（`None`＝整页）：正文延续到下一行起始页时，那一页由两行按块分读，屏上据此切尾。
    """

    node_id: str | None
    name: str
    page_start: int | None
    page_end: int | None
    end_block: int | None = None


def display_spans_of(rows: Sequence[SpanRow], *, last_page: int | None) -> list[DisplaySpan]:
    """按树序派生显示段（D-3~D-9）：父先于子、段与段不重合、按页序，同页由先到者占页。

    与 `section_spans_of` 是两条算式、两个用途：那条给点腿取材（章含自己的知识点页），这条给屏上
    切片（章止于首子之前）。展示序＝DFS，同级按 `(锚页, 锚块, order_hint)`；父由 `parent_id` 指认，指向
    不在册的父按顶级行处理——显示面不吞行。同页按块号排而不是只按 `order_hint`（`KA-70`）：习题行
    在目录里排在章末，锚点却可能落在同页更前的块，只看 `order_hint` 会把下一节切成倒挂段。

    一段的终点先取自然终点：有子的行止于首子之前（D-4／D-8），书前件（附件行且锚页早于第一枚
    知识点行）退回单页（D-7），其余行到作用域末（下一条同级或更浅行之前；末行到书末）。再用全局
    游标把起点抬到「上一段末页 ＋ 1」：同页时先到者占页、后到者顺延（D-6），顺延越过作用域末时
    整段为空并披露。书前件与书尾附件走同一游标，所以前置页与附件区各自连续、不与章行重叠。

    `last_page` 为 `None`（书未记页数）时尾行只发起点、不发终点，不猜书末（D-9）；起点页超出
    `last_page` 的行夹不出区间，两键为空。

    `end_block` 让同页分读落到实处：下一行在那一页的标题块 `k>0` 时，本行终点延到那页、止于
    `k-1`（例：b05「导引」9–10 与「有序集」10–12）；首子与父同页 `k>0` 时父止于 `k-1`。游标
    只在整页独占时前移一页——按块分读的页仍归下一行从它自己的块起重读。
    """
    if not rows:
        return []
    known_ids = {row.node_id for row in rows if row.node_id is not None}
    child_positions: dict[str, list[int]] = {}
    root_positions: list[int] = []
    for position, row in enumerate(rows):
        parent = row.parent_id
        if parent is None or parent not in known_ids or parent == row.node_id:
            root_positions.append(position)
        else:
            child_positions.setdefault(parent, []).append(position)

    def order_key(position: int) -> tuple[int, int, int, int]:
        row = rows[position]
        # 同页按块号排（`KA-70`）：习题行在目录里按章末排 `order_hint`，锚点却可能落在同页更前的块；
        # 只按 `order_hint` 排会把下一节切成「起点块 > 终点块」的倒挂段（真机 b18 §8.5）。无块号的
        # 行退回原序，不猜它在该页的位置。
        return (row.page, row.block if row.block is not None else row.order_hint or 0, row.order_hint or 0, position)

    ordered_positions: list[int] = []
    placed: set[int] = set()

    def visit(position: int) -> None:
        if position in placed:
            return
        placed.add(position)
        ordered_positions.append(position)
        node_id = rows[position].node_id
        for child in sorted(child_positions.get(node_id or "", ()), key=order_key):
            visit(child)

    for position in sorted(root_positions, key=order_key):
        visit(position)
    # 环或悬空父引用：树走不到的行按页序补在末尾，显示面不吞行。
    for position in sorted(range(len(rows)), key=order_key):
        visit(position)

    ordered = [rows[position] for position in ordered_positions]
    knowledge_pages = [row.page for row in ordered if row.toc_role == TOC_ROLE_KNOWLEDGE]
    first_knowledge = min(knowledge_pages) if knowledge_pages else None

    spans: list[DisplaySpan] = []
    cursor = 0
    for index, row in enumerate(ordered):
        if last_page is not None and row.page > last_page:
            spans.append(DisplaySpan(row.node_id, row.name, None, None))
            continue
        boundary_row = next(
            (later for later in ordered[index + 1 :] if later.depth <= row.depth), None
        )
        boundary = None if boundary_row is None else boundary_row.page
        scope_end = None if boundary is None else boundary - 1
        end_block: int | None = None
        if (
            boundary_row is not None
            and boundary_row.block is not None
            and boundary_row.block > 0
            and boundary is not None
            and boundary >= row.page
        ):
            # 下一行在那页有标题块：本行正文可延续到那页，止于标题块之前（与取材同一算式）。
            # 那一页由两行按块分读，故它不算本行独占——游标停在前一页，下一行仍从这页起。
            scope_end = boundary
            end_block = boundary_row.block - 1
        if last_page is not None:
            scope_end = last_page if scope_end is None else min(scope_end, last_page)
        start = max(row.page, cursor + 1)
        # 同页的下一行把本行作用域压到「本页前一页」，本行仍按自己那页展示（D-6 先到者占页）；
        # 只有被游标挤到作用域之外的行才发空段。
        cap = scope_end if scope_end is None or scope_end >= row.page else row.page
        if cap is not None and start > cap:
            spans.append(DisplaySpan(row.node_id, row.name, None, None))
            continue
        child_positions_of_row = sorted(
            child_positions.get(row.node_id or "", ()), key=order_key
        )
        end: int | None
        if child_positions_of_row:
            first_child = rows[child_positions_of_row[0]]
            end = first_child.page - 1
            if (
                first_child.page == row.page
                and first_child.block is not None
                and first_child.block > 0
            ):
                # 章与首点同页：章止于首点标题块之前，首点从自己的块起重读（两行按块分读同一页）。
                end = row.page
                end_block = first_child.block - 1
            else:
                end_block = None
        elif (
            row.toc_role == TOC_ROLE_REFERENCE
            and row.toc_kind != "exercise"
            and (first_knowledge is None or row.page < first_knowledge)
        ):
            end = start
            end_block = None
        else:
            end = scope_end
        if end is None:
            spans.append(DisplaySpan(row.node_id, row.name, start, None, end_block))
            continue
        end = max(end, start)
        if last_page is not None:
            end = min(end, last_page)
        spans.append(DisplaySpan(row.node_id, row.name, start, end, end_block))
        cursor = end - 1 if end_block is not None else end
    return spans


def _first_anchor_page(row: AfKp | KpNodeRow) -> int | None:
    """一行锚点里最小的有效页号；没有可解释的页即 `None`（与读面 `_anchor_pages` 同一判据）。"""
    pages = [
        page
        for page in (
            int(anchor.get("page") or 0) for anchor in (row.anchors or []) if isinstance(anchor, dict)
        )
        if page > 0
    ]
    return min(pages) if pages else None


def _first_anchor_block(row: AfKp | KpNodeRow, page: int | None) -> int | None:
    """一行在指定页上最早那块（`KA-65`）：同页切块只比块序，取不到块序回 `None`。"""
    if page is None:
        return None
    indexes = [
        int(index)
        for anchor in (row.anchors or [])
        if isinstance(anchor, dict) and int(anchor.get("page") or 0) == page
        for index in (anchor.get("block_indexes") or [])
        if isinstance(index, int) and index >= 0
    ]
    return min(indexes) if indexes else None


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
                block=_first_anchor_block(node.row, node.page),
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


def _section_block_bodies(evidence: EvidenceSet, span: SectionSpan) -> tuple[PageBody, ...]:
    """按 span 的块界切页正文的副本：起点页／终点页只留界内块，其余页整页（`KA-42` 的取材侧）。

    页级取法会让相邻两节共读边界页——一章的正文常续写在下一章标题页的上半部，而章界页按
    「下一章首块」分给了两节（b17：Homework 1 止于 p7 块 1、Homework 2 从 p7 块 2 起），整页喂
    两边就把下一章的题按上一章的节再切一遍。块产物缺该页时退回整页原文，取材不因缺块消失；
    剔家具与取证面的原文仍按 `KA-2` 保留。
    """
    by_page = {page.page: page for page in evidence.blocks}
    bodies: list[PageBody] = []
    for page in evidence.pages:
        if not span.page_start <= page.page_no <= span.page_end:
            continue
        blocks_page = by_page.get(page.page_no)
        if blocks_page is None:
            bodies.append(PageBody(page.page_no, strip_furniture(page.markdown, page.furniture)))
            continue
        kept = [
            block
            for block in sorted(blocks_page.blocks, key=lambda item: (item.order, item.index))
            if not (
                page.page_no == span.page_start
                and span.start_block is not None
                and block.index < span.start_block
            )
            and not (
                page.page_no == span.page_end
                and span.end_block is not None
                and block.index > span.end_block
            )
        ]
        bodies.append(
            PageBody(
                page.page_no,
                strip_furniture(blocks_to_markdown(kept), page.furniture),
            )
        )
    return tuple(bodies)


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


@dataclass(frozen=True, slots=True)
class KindReviewBuild:
    """P0 种类审校的账面（`KA-63`）：审过的行数、点名行名、拆出并新增的习题行数。

    `flag_node_ids` 与 `flags` 同枚不同用：账面外发的是行名（人能一眼认出是哪一行），点腿跳过
    要用的是身份——名字在屏上可重名，身份不会。`nodes` 是拆出的行（`KA-68`）：点腿要拿它们的
    锚块当取材的止点，传回给调用方而不是让腿自己再读一次库。
    """

    rows: int = 0
    flags: tuple[str, ...] = ()
    splits: int = 0
    flag_node_ids: tuple[str, ...] = ()
    nodes: tuple[OutlineNode, ...] = ()


def kind_review_pass(
    session: Session,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
) -> KindReviewBuild:
    """`KA-63`：点腿之前的一趟种类审校——零模型调用、只写拆出的习题行。

    行的取证区间复用点腿的同一把算式（`_section_spans`），因此「章尾习题不属于任何点」也盖得住；
    行名是整块习题标签的行只点名、不发点腿，也不再把自己的锚块拆一行（`kind_review` 的已在册判据）。
    上一批的拆分行不进审校输入：它们的身份由 `EXERCISE_SPLIT_MODEL_REF` 与人工/模型目录行分开，
    本批按当前证据重算一遍，旧行由收尾作废（`KA-36`）——两批拆分行不会在屏上并存。
    拆分行的 `order_hint` 排在所属章现有子行之后（不改既有序号），屏上它就是章末那一段。
    """
    node_types = node_types_of([node.row for node in outline_nodes])
    first_page = min((node.page for node in outline_nodes), default=None)
    span_of = {
        span.parent_id: span
        for span in _section_spans(outline_nodes, evidence, first_page=first_page)
        if span.parent_id is not None
    }
    audited = [
        KindReviewRow(
            node_id=node.row.node_id,
            parent_id=node.row.parent_id,
            name=node.row.name,
            node_type=node_types.get(node.row.node_id, ""),
            page_start=span_of[node.row.node_id].page_start,
            page_end=span_of[node.row.node_id].page_end,
        )
        for node in outline_nodes
        if node.row.model_ref != EXERCISE_SPLIT_MODEL_REF and node.row.node_id in span_of
    ]
    review = kind_review(audited, evidence.blocks)
    names = {row.node_id: row.name for row in audited}
    child_hints: dict[str, list[int]] = {}
    own_hints: dict[str, int] = {}
    for node in outline_nodes:
        own_hints[node.row.node_id] = node.row.order_hint or 0
        if node.row.parent_id:
            child_hints.setdefault(node.row.parent_id, []).append(node.row.order_hint or 0)
    repo = KpRepository(session)
    cursor: dict[str, int] = {}
    created = 0
    created_nodes: list[OutlineNode] = []
    for split in review.splits:
        if split.parent_id in cursor:
            cursor[split.parent_id] += 1
        else:
            sibling_hints = child_hints.get(split.parent_id)
            base = max(sibling_hints) if sibling_hints else own_hints.get(split.parent_id, 0)
            cursor[split.parent_id] = base + 1
        row = KpNodeRow(
            node_id=new_node_id(),
            book_id=acceptance.book_id,
            name=split.name[:500],
            norm_key=norm_key_for(split.name)[:200],
            node_kind=NODE_KIND_OUTLINE,
            job_id=acceptance.evidence_job_id,
            parent_id=split.parent_id,
            depth=2,
            order_hint=cursor[split.parent_id],
            anchors=[
                {
                    "kind": KP_ANCHOR_KIND,
                    "ref_id": f"p{split.page:04d}",
                    "page": split.page,
                    "block_indexes": [split.block_index],
                    "snippet": split.name[:200],
                }
            ],
            summary="",
            gen_status="draft",
            gen_batch=acceptance.gen_batch,
            model_ref=EXERCISE_SPLIT_MODEL_REF,
            meta={"toc_kind": "exercise", "toc_role": TOC_ROLE_REFERENCE},
        )
        repo.create(row)
        created_nodes.append(OutlineNode(page=split.page, row=row))
        created += 1
    return KindReviewBuild(
        rows=review.rows,
        flags=tuple(names.get(flag.node_id, flag.node_id) for flag in review.flags),
        splits=created,
        flag_node_ids=tuple(flag.node_id for flag in review.flags),
        nodes=tuple(created_nodes),
    )


def extract_points(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    retry_node_ids: Sequence[str] | None = None,
    skip_node_ids: Sequence[str] = (),
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

    `skip_node_ids`（`KA-63` ①）：行名本身就是整块习题标签的行不发点腿——它们的身份已在种类审校
    里被点名（`type_review_flags`），再读一遍正文只会产出一节「知识化的习题」。
    """
    repo = KpRepository(session)
    retry_nodes = set(retry_node_ids) if retry_node_ids is not None else None
    skipped_nodes = set(skip_node_ids)
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
    spans = _section_spans(outline_nodes, evidence, first_page=first_page)
    if content_only:
        return _extract_content_points(
            session,
            repo,
            engine,
            acceptance,
            evidence,
            spans,
            outline_nodes=outline_nodes,
            node_types=node_types,
            non_knowledge=non_knowledge,
            skipped_nodes=skipped_nodes,
            retry_nodes=retry_nodes,
            toc_pages=toc_pages,
        )
    for span in spans:
        if retry_nodes is not None and span.parent_id not in retry_nodes:
            continue
        if span.parent_id in skipped_nodes:
            continue
        if span.parent_id in non_knowledge:
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


def _exercise_cut_anchors(outline_nodes: Sequence[OutlineNode]) -> list[tuple[int, int]]:
    """习题段的切点（`KA-68`）：每枚习题行最早的那枚锚（页，块序），按页块序排好。

    认行身份位（`node_kind='outline'` ∧ `toc_kind='exercise'`），不按 `model_ref` 认产出者——
    本批刚拆出的行与既有目录里的习题段是同一种东西。没有块序的锚不切：没有块界就只能整页取舍，
    那是替人猜一个别处已明确的边界。
    """
    cuts: set[tuple[int, int]] = set()
    for node in outline_nodes:
        row = node.row
        if row.node_kind != NODE_KIND_OUTLINE or row.depth != 2:
            continue
        if str((row.meta or {}).get("toc_kind") or "") != "exercise":
            continue
        page = _first_anchor_page(row)
        block = _first_anchor_block(row, page)
        if page is None or block is None:
            continue
        cuts.add((page, block))
    return sorted(cuts)


def _clamp_span_at_exercise(
    span: SectionSpan, cuts: Sequence[tuple[int, int]]
) -> SectionSpan:
    """点的取材止于本段第一枚习题标签之前（`KA-68`）：与屏上切尾同一条边界。

    切点只在「本行起点之后、本行终点之内」时生效；标签在页首（块序 0）时本行止于前一页。
    点的正文与习题段在同一页上相接是常态（真机 b18：每节正文后紧跟一段习题），页级取舍会把
    整段习题读进总结材料，块级切点才切得开。
    """
    start = (span.page_start, span.start_block or 0)
    for page, block in cuts:
        if (page, block) <= start:
            continue
        if page > span.page_end:
            break
        if page == span.page_end and span.end_block is not None and block > span.end_block:
            break
        if block == 0:
            return replace(span, page_end=page - 1, end_block=None)
        return replace(span, page_end=page, end_block=block - 1)
    return span


def _extract_content_points(
    session: Session,
    repo: KpRepository,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    spans: Iterable[SectionSpan],
    *,
    outline_nodes: Sequence[OutlineNode],
    node_types: dict[str, str],
    non_knowledge: set[str],
    skipped_nodes: set[str],
    retry_nodes: set[str] | None,
    toc_pages: set[int],
) -> PointBuild:
    """内容批次的点腿（`KA-64`／`KA-66`）：块取材 → 预算计划 → 逐笔发问 → 节级总结与取材区间落库。

    与 `full` 的按页正文不同，材料只取正文块（家具与目录页零入）并按「p{页}·b{序}」带坐标；
    超预算的节点按块边界切窗，相邻小节点合成一笔。窗口与合批都只改「怎么发问」，不改写库面：
    这一批仍不建点行，只往在册目录行写节级 `summary` 与详情取材区间 `meta.material_ranges`。
    """
    sections: list[PointSection] = []
    span_by_node: dict[str, SectionSpan] = {}
    owners = {node.row.node_id: node.row.parent_id for node in outline_nodes}
    exercise_cuts = _exercise_cut_anchors(outline_nodes)
    skipped_unlocated: list[str] = []
    for span in spans:
        if retry_nodes is not None and span.parent_id not in retry_nodes:
            continue
        if span.parent_id in skipped_nodes or span.parent_id in non_knowledge:
            continue
        if "points" not in content_legs_for(node_types.get(span.parent_id or "", "")):
            continue
        if span.page_start in toc_pages:
            skipped_unlocated.append(span.title or f"第 {span.page_start} 页")
            logger.warning(
                "内容批次跳过锚在目录页的行（`KA-43`）：%s 的 %s（p%s）",
                acceptance.book_id,
                span.title or "无标题行",
                span.page_start,
            )
            continue
        span = _clamp_span_at_exercise(span, exercise_cuts)
        blocks = select_material_blocks(
            evidence.blocks,
            page_start=span.page_start,
            page_end=span.page_end,
            exclude_pages=toc_pages,
            start_block=span.start_block,
            end_block=span.end_block,
        )
        if not blocks:
            logger.warning(
                "内容批次跳过零取材块的行（`KA-64`）：%s 的 %s（第 %s..%s 页）",
                acceptance.book_id,
                span.title or "无标题行",
                span.page_start,
                span.page_end,
            )
            continue
        node_id = span.parent_id or ""
        span_by_node[node_id] = span
        sections.append(
            PointSection(
                node_id=node_id,
                parent_id=owners.get(node_id),
                title=span.title or "",
                blocks=blocks,
            )
        )

    summaries: dict[str, list[str]] = {}
    failures: dict[str, str] = {}
    windows: dict[str, int] = {}
    sent: dict[str, list[tuple[int, int]]] = {}
    merges = 0
    for call in plan_point_calls(sections):
        for node_id in call.node_ids:
            windows[node_id] = windows.get(node_id, 0) + 1
        if call.merged:
            merges += 1
        if not call.blocks:
            continue
        try:
            replied = engine.points(_content_section_input(call))
        except EngineError as exc:
            for node_id in call.node_ids:
                failures.setdefault(node_id, str(exc)[:200])
            logger.warning(
                "内容批次点调用失败（`KA-64`）：%s 的第 %s..%s 页（%s）",
                acceptance.book_id,
                call.blocks[0].page,
                call.blocks[-1].page,
                "、".join(section.title for section in call.sections),
            )
            continue
        cursor = 0
        for section in call.sections:
            width = len(section.blocks)
            member_blocks = call.blocks[cursor : cursor + width]
            cursor += width
            sent.setdefault(section.node_id, []).extend(
                (block.page, block.block_index) for block in member_blocks
            )
        if call.merged:
            by_id = {member.node_id: member for member in replied.members}
            for node_id in call.node_ids:
                member = by_id.get(node_id)
                if member is None:
                    failures.setdefault(node_id, "合批回复缺少该点（KA-64）")
                elif member.summary:
                    summaries.setdefault(node_id, []).append(member.summary)
        else:
            if replied.summary:
                summaries.setdefault(call.node_ids[0], []).append(replied.summary)

    failed_sections = 0
    failed_details: list[dict[str, Any]] = []
    for node_id in windows:
        if node_id not in failures:
            continue
        # 任一窗口失败＝整节零行（`KA-64`）：已收回的半份总结一并丢掉，账面点名而不是留半节。
        summaries.pop(node_id, None)
        sent.pop(node_id, None)
        failed_sections += 1
        span = span_by_node.get(node_id)
        failed_details.append(
            {
                "node_id": node_id,
                "title": (span.title if span else "") or "目录未覆盖区间",
                "page_start": span.page_start if span else 0,
                "page_end": span.page_end if span else 0,
                "reason": failures[node_id],
            }
        )
        logger.warning("内容批次的节失败（`KA-64`）：%s 的 %s", acceptance.book_id, node_id)

    outline_summary_rows = 0
    summarized: list[str] = []
    for node_id, parts in summaries.items():
        wrote = repo.set_outline_summary(
            book_id=acceptance.book_id, node_id=node_id, summary="\n\n".join(parts)
        )
        if wrote:
            summarized.append(node_id)
            outline_summary_rows += 1
    material_ranges_rows = 0
    for node_id in summarized:
        # 取材区间跟着「这一批写成的那份详情」走：人已写或已背书的行没有机器详情，不挂机器读数。
        ranges = sanitize_material_ranges(
            [
                {"page": page, "block_indexes": [index]}
                for page, index in sent.get(node_id, [])
            ]
        )
        wrote = repo.set_material_ranges(
            book_id=acceptance.book_id, node_id=node_id, ranges=ranges
        )
        material_ranges_rows += 1 if wrote else 0
    session.commit()

    return PointBuild(
        point_rows=0,
        unarchored=0,
        dropped_nodes=0,
        failed_sections=failed_sections,
        failed_section_details=tuple(failed_details),
        outline_summary_rows=outline_summary_rows,
        skipped_unlocated=tuple(skipped_unlocated),
        point_windows=sum(max(0, count - 1) for count in windows.values()),
        point_merges=merges,
        material_ranges_rows=material_ranges_rows,
    )


def _content_section_input(call: PointCall) -> SectionInput:
    """计划里的一笔调用 → 引擎输入（`KA-64`）：合批时按各点自己的块数切回分段。"""
    blocks = tuple(
        SectionBlock(page=block.page, block_index=block.block_index, text=block.text)
        for block in call.blocks
    )
    if not blocks:
        return SectionInput(
            parent_id=call.sections[0].node_id,
            title=call.sections[0].title,
            page_start=0,
            page_end=0,
            pages=(),
        )
    page_start = min(block.page for block in blocks)
    page_end = max(block.page for block in blocks)
    if not call.merged:
        section = call.sections[0]
        return SectionInput(
            parent_id=section.node_id,
            title=section.title,
            page_start=page_start,
            page_end=page_end,
            pages=(),
            blocks=blocks,
        )
    members: list[SectionMember] = []
    cursor = 0
    for section in call.sections:
        width = len(section.blocks)
        members.append(
            SectionMember(
                node_id=section.node_id,
                title=section.title,
                blocks=blocks[cursor : cursor + width],
            )
        )
        cursor += width
    first = call.sections[0]
    return SectionInput(
        parent_id=first.parent_id,
        title=f"{first.title} 等 {len(call.sections)} 节",
        page_start=page_start,
        page_end=page_end,
        pages=(),
        blocks=blocks,
        members=tuple(members),
    )


# --------------------------------------------------------------------------- 教材侧习题分题（KA-53）


@dataclass(frozen=True, slots=True)
class ExerciseItemBuild:
    """分题腿的读数（`KA-53`）：成出的点层行数；无题号的习题段只计数（披露，不是失败）。

    `skipped_unlocated` 是锚仍在目录页的习题段（`KA-43` 判据在分题腿上的同一份点名）：整段不发，
    进批次的 `unlocated_outline_entries`。
    """

    item_rows: int = 0
    empty_segments: int = 0
    skipped_unlocated: tuple[str, ...] = ()


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
            if page.page == span.page_start and span.start_block is not None:
                if block.index < span.start_block:
                    continue
            if page.page == span.page_end and span.end_block is not None:
                if block.index > span.end_block:
                    continue
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

    锚仍在目录页的习题段整段不发（`KA-43` 的同一条判据）：这一批不跑目录腿，坏锚救不回正文页，而
    `KA-46` 给习题段的是「目录页起、到本章末」的真区间——照发会把前几章的题行重复挂到本段父行下。
    跳过的段点名进 `unlocated_outline_entries`，与内容腿的披露合流。
    """
    repo = KpRepository(session)
    exercises = {
        node.row.node_id
        for node in outline_nodes
        if str((node.row.meta or {}).get("toc_kind") or "") == "exercise"
    }
    if not exercises:
        return ExerciseItemBuild()
    toc_pages = {page.page for page in evidence.blocks if toc_entries(page)}
    item_rows = empty_segments = 0
    skipped_unlocated: list[str] = []
    for span in _section_spans(outline_nodes, evidence):
        if span.parent_id not in exercises:
            continue
        if span.page_start in toc_pages:
            skipped_unlocated.append(span.title or f"第 {span.page_start} 页")
            logger.warning(
                "习题批次跳过锚在目录页的行（`KA-43`）：%s 的 %s（p%s）",
                acceptance.book_id,
                span.title or "无标题行",
                span.page_start,
            )
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
    return ExerciseItemBuild(
        item_rows=item_rows,
        empty_segments=empty_segments,
        skipped_unlocated=tuple(skipped_unlocated),
    )


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
    #: 答案段回配填上的枚数（PLAN-036 W11）：只计入「解答原为空、按题号唯一命中答案段」的那些。
    answer_pairs: int = 0


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


#: 产物内嵌图片引用（REQ-179 R1）：页 markdown 由 `blocks_to_markdown` 写出
#: `![caption](pages/images/<sha>.jpg)`；该相对路径只在数据根内有意义，8903 下取不到。
_PACK_IMAGE_REF = re.compile(r"(!\[[^\]]*\]\()pages/images/([^)]+)\)")


def rewrite_pack_image_urls(*segments: str, book_id: str) -> tuple[str, ...]:
    """题包两段的图片引用改写（REQ-179 R1，写入面）：产物相对路径 → 8902 读腿 URL。

    `pages/images/<sha>.jpg` 实存于生效版本目录（`_store_images` 落盘），读腿
    `GET /books/{book_id}/images/<filename>` 按同一路径取字节；URL 不带主机名，前端按
    `API_BASE`（8900 → 8902 字节转发）取。只改写 `pages/images/` 前缀的引用，其余路径不动；
    非该前缀的写法不猜。文件名段不允许路径分隔（防穿越），命中不了就原样保留——披露优于改错。
    """
    rewritten: list[str] = []
    for segment in segments:
        text = segment or ""

        def _sub(match: re.Match[str]) -> str:
            filename = match.group(2)
            if "/" in filename or "\\" in filename or not filename.strip():
                return match.group(0)
            return f"{match.group(1)}/api/v1/books/{book_id}/images/{filename})"

        rewritten.append(_PACK_IMAGE_REF.sub(_sub, text))
    return tuple(rewritten)


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
    if not solution or solution in (EXERCISE_NO_SOLUTION_TEXT, EXERCISE_UNAVAILABLE_TEXT):
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
        # 逐题机器读数（REQ-161 W5）：空串＝未判读／未对应，键恒在，屏侧只读不重算。
        "match_state": str(meta.get("match_state") or ""),
        "target_item": str(meta.get("target_item") or ""),
        "exercise_type": str(meta.get("exercise_type") or ""),
        # 配对读数（REQ-170 第二步）：`pair_state` 空串＝旧批次或独立模式；`unpaired` 时 reason 点名。
        "pair_state": str(meta.get("pair_state") or ""),
        "pair_reason": str(meta.get("pair_reason") or ""),
        # 配对校验读数（REQ-170 第三步）：`verify_state` 空串＝没校验腿；`done` 时 verdict 才是结论。
        "verify_state": str(meta.get("verify_state") or ""),
        "verify_verdict": str(meta.get("verify_verdict") or ""),
        "verify_note": str(meta.get("verify_note") or ""),
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
    match: Mapping[str, Any] | None = None,
) -> bool:
    """来源书清单（`KA-53`）：模式、目标书、最近批次与未匹配解答，只服务「这本书的提示」。

    `match`（REQ-161 W4）是目录匹配的读数：状态、候选集与章对应。它是可选键——目录腿命中／
    未命中／失败都写，习题腿重写清单时原样带着走，免得一次切题把「对的是哪本教材」抹掉。
    """
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
    if match is not None:
        payload["match"] = dict(match)
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


@dataclass(frozen=True, slots=True)
class ExerciseOutlineMatch:
    """习题集目录匹配的读数（REQ-161 W4／REQ-178 R1）：状态、选中的教材与章对应。

    `state` 五值：`hit`（命中）、`miss`（没有教材对得上：书名同族与章覆盖都没过、模型也说不配）、
    `failed`（匹配调用失败，人工指定是备用链路）、`skipped`（没有候选教材或引擎没有这条腿，
    一次调用都没发）、`red`（拿不出「习题＋解答」证据——独立型或配套型命中后证据门不过都归此态，
    本批零目录行，`KA-71`）。
    """

    state: str = "skipped"
    target_book_id: str | None = None
    candidates: tuple[str, ...] = ()
    chapters: tuple[tuple[str, str], ...] = ()


def _exercise_match_candidates(
    session: Session, *, book: AfBook
) -> list[tuple[AfBook, tuple[str, ...]]]:
    """同套浅绿及以上的教材候选（PLAN-036 S1）：书＋它的章／节名清单，按书号定序。

    候选面先按 `knowledge_id`（套）收窄：同一套里可以有多本教材、多本习题集，跨套的教材不该
    进材料（b07 曾与 b01/b12/b13/b14/b18 六本混选）。`knowledge_id` 有一边空着时退回 `course_id`
    粗筛——那是旧口径，仍在回执的 `candidates` 里如实体现在本套候选之外。

    浅绿与配套受理门同源（`book_stage_reading.ready`，REQ-170 追加请求 A）——机器完成即能承载
    习题；连总结与锚点都不齐的书不进材料，匹配调用一次都不发。人工背书进度不挡候选。节名一并交出去：
    习题集的章名常按「章.节」对齐，只看一级章名会让模型缺一份判据。
    """
    same_set = bool(book.knowledge_id)
    if not book.course_id and not same_set:
        return []
    repo = KpRepository(session)
    candidates: list[tuple[AfBook, tuple[str, ...]]] = []
    for other in BookRepository(session).list_books():
        if other.book_id == book.book_id:
            continue
        if other.doc_kind != DOC_KIND_TEXTBOOK:
            continue
        if same_set:
            if other.knowledge_id != book.knowledge_id:
                continue
        elif other.course_id != book.course_id:
            continue
        rows = repo.list_for_book(other.book_id)
        reading = book_stage_reading(
            rows,
            toc_pages=book_toc_pages(
                session, book_id=other.book_id, job_id=other.active_job_id
            ),
        )
        if not reading.ready:
            continue
        live = [row for row in rows if row.gen_status == "candidate"]
        types = node_types_of(live)
        chapters = tuple(
            row.name
            for row in live
            if types.get(row.node_id) in (NODE_TYPE_CHAPTER, NODE_TYPE_POINT)
            and (row.name or "").strip()
        )
        if chapters:
            candidates.append((other, chapters))
    return candidates


def _validated_chapter_pairs(
    pairs: Sequence[tuple[str, str]],
    source_names: Sequence[str],
    target_names: Sequence[str],
) -> tuple[tuple[str, str], ...]:
    """章对应只采信两边都认得出的行（REQ-161 W4）：猜错的对应比没有对应更难查。"""
    sources = {norm_key_for(name): name for name in source_names if name.strip()}
    targets = {norm_key_for(name): name for name in target_names if name.strip()}
    kept: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source, target in pairs:
        source_key = norm_key_for(source)
        target_key = norm_key_for(target)
        if source_key not in sources or target_key not in targets or source_key in seen:
            continue
        seen.add(source_key)
        kept.append((sources[source_key], targets[target_key]))
    return tuple(kept)


_EXERCISE_PROBLEM_REF = "rule:exercise_outline"

_EXERCISE_ANSWER_NAME = "答案"


def _problem_block_index(page: BlocksPage, chapter_no: int) -> int:
    pattern = re.compile(
        rf"(?m)^[ \t]*Problem\s+{chapter_no}\s*[.．]\s*\d{{1,4}}\s*[:：（(]"
    )
    for block in page.blocks:
        if pattern.search(block.text or ""):
            return block.index
    return 0


_CHAPTER_TRAILING = re.compile(r"[.…·\s]*\d{1,4}\s*$")


def _clean_chapter_name(name: str) -> str:
    """教材章行名去行末引导点与目录印刷页码（`…… 1`）：屏上章名只留标题本身。"""
    return _CHAPTER_TRAILING.sub("", (name or "").strip()).strip()


def _target_chapter_names(session: Session, target_book_id: str | None) -> dict[int, str]:
    """目标教材的「章号 → 章名」：只取顶层知识行里名字带 `第N章` 形状的那些。

    习题集的章行直接复用教材的章名（用户 2026-11-16 定：目录取教材的章节段，只筛出章），
    因此这里的名字要洗掉目录行末的印刷页码；前言、符号表、出版商页这类顶层行没有章号，不进表。
    """
    if not target_book_id:
        return {}
    rows = KpRepository(session).list_for_book(target_book_id, gen_status="candidate")
    types = node_types_of(rows)
    names: dict[int, str] = {}
    for row in rows:
        if row.node_kind != NODE_KIND_OUTLINE:
            continue
        if (row.depth or 1) != 1 and types.get(row.node_id) != NODE_TYPE_CHAPTER:
            continue
        number = chapter_number(row.name or "")
        if number is None:
            continue
        names.setdefault(number, _clean_chapter_name(row.name or ""))
    return names


def exercise_outline_hints(*, evidence: EvidenceSet) -> tuple[ExerciseOutlineHint, ...]:
    """习题集目录的确定性读数（PLAN-036 W9）：章行取目录、无目录由题号派生、再退 heading 块。

    三条取材都零模型调用，判据在 `normalize` 侧：`exercise_chapter_hints` 只取章（分部行不进）、
    未定位丢行；`homework_chapters` 以 `Problem N.M` 的首段当章号、首次出现页当锚；都没有时退
    heading 块（与教材目录腿同一条回落），只认章级名字、按书序首枚去重。匹配腿与成行腿共用这一份
    读数——两处各算一次的话，改名的章与匹配的章会是两份名单。
    """
    pages = list(evidence.blocks)
    hints: tuple[ExerciseOutlineHint, ...] = exercise_chapter_hints(pages)
    if not hints:
        derived = homework_chapters(
            [
                (page.page, "\n".join(block.text or "" for block in page.blocks))
                for page in pages
            ]
        )
        by_page = {page.page: page for page in pages}
        hints = tuple(
            ExerciseOutlineHint(
                name=f"Homework {chapter_no}",
                page_no=page_no,
                block_index=_problem_block_index(by_page[page_no], chapter_no),
            )
            for chapter_no, page_no in derived
        )
    if not hints:
        seen: set[str] = set()
        headings: list[ExerciseOutlineHint] = []
        for page in pages:
            for block in page.blocks:
                name = (block.text or "").strip()
                if block.type is not BlockType.HEADING or not chapter_name_hint(name):
                    continue
                key = norm_key_for(name)
                if key in seen:
                    continue
                seen.add(key)
                headings.append(
                    ExerciseOutlineHint(name=name, page_no=page.page, block_index=block.index)
                )
        hints = tuple(headings)
    return hints


def build_exercise_outline(
    *,
    book: AfBook,
    evidence: EvidenceSet,
    gen_batch: str,
    hints: Sequence[ExerciseOutlineHint] | None = None,
    chapter_names: Mapping[int, str] | None = None,
) -> OutlineBuild | None:
    """习题集目录成行（PLAN-036 W9／S3）：章行取目录或题号派生，答案段补一枚附件行。

    章名在配套命中时直接取目标教材的章名（`chapter_names`：章号 → 教材章名，用户 2026-11-16 定），
    锚点始终是本书自己的第一枚题号页——跨书的页号不能当本书锚点（`KA-18`）。答案段按「运行页眉
    连续窗」补一行 `reference/back_matter`，它的作用是给末章切段上界，不产总结。

    三条取材都认不出时返回 `None`，由调用方退回教材目录腿：种类标成习题集、实际却带完整章目录的书
    （`roles` 里混着 `exercises` 的教材）不该因此被拒；退回后仍由同一条腿的守卫兜底。
    """
    pages = list(evidence.blocks)
    assembler = _OutlineAssembler(
        book_id=book.book_id, job_id=evidence.job_id, gen_batch=gen_batch
    )
    if hints is None:
        hints = exercise_outline_hints(evidence=evidence)
    if not hints:
        return None
    for hint in hints:
        name = hint.name
        if chapter_names:
            number = chapter_number(hint.name)
            if number is not None:
                name = chapter_names.get(number, name)
        assembler.add(
            OutlineReading(
                page_no=hint.page_no,
                block_index=hint.block_index,
                level=1,
                text=name,
                model_ref=_EXERCISE_PROBLEM_REF,
                role=hint.role,
                kind=hint.kind,
            )
        )
    window = answer_section_window(pages)
    if window is not None:
        start, _end, block_index = window
        assembler.add(
            OutlineReading(
                page_no=start,
                block_index=block_index,
                level=1,
                text=_EXERCISE_ANSWER_NAME,
                model_ref=_EXERCISE_PROBLEM_REF,
                role=TOC_ROLE_REFERENCE,
                kind="back_matter",
            )
        )
    if not assembler.nodes:
        return None
    return OutlineBuild(
        nodes=tuple(assembler.nodes),
        dropped_depth=assembler.dropped_depth,
        dropped_malformed=assembler.dropped_malformed,
        toc_pages=tuple(sorted({page.page for page in pages if toc_entries(page)})),
        dropped_duplicates=assembler.dropped_duplicates,
    )


def _numbered_chapter_pairs(
    source_names: Sequence[str], target_names: Mapping[int, str]
) -> tuple[tuple[str, str], ...]:
    """按章号把两边的章配上（`第N章` ↔ `Homework N`）：配套目录复用教材章名的那一步。

    只配两边都认得出章号的那些；认不出（前言一类）不猜，也不挡其余章。
    """
    pairs: list[tuple[str, str]] = []
    for name in source_names:
        number = chapter_number(name)
        if number is None or number not in target_names:
            continue
        pairs.append((name, target_names[number]))
    return tuple(pairs)


#: 配套型的章覆盖下限（`KA-71`）：解析噪声允许两成章对不出，不因个别章读不出而判死。
EXERCISE_COMPANION_COVERAGE = 0.8
#: 书名同族比对的最短教材书名（规范后）：短名（「数学」一类）没有鉴别力。
EXERCISE_TITLE_MIN_CHARS = 4
#: 题型后缀按长到短剥（`KA-71` 书名同族）：剥到不再命中为止，两本书名归一后再比。
_EXERCISE_TITLE_SUFFIXES = ("习题解答", "习题集", "题解", "解答", "答案", "详解", "辅导", "习题")

_CHAPTER_PREFIX = re.compile(r"^第\s*[0-9０-９一二三四五六七八九十百零〇两]+\s*[章篇卷部]")
_CHAPTER_NAME_NOISE = re.compile(r"[\s·•・:：,，.。()（）\[\]【】\-—_/]+")


def _title_core(title: str) -> str:
    """书名归一（`KA-71`）：NFKC、去空白与标点、小写，再逐层剥掉题型后缀。"""
    text = _CHAPTER_NAME_NOISE.sub(
        "", unicodedata.normalize("NFKC", title or "")
    ).casefold()
    changed = True
    while changed:
        changed = False
        for suffix in _EXERCISE_TITLE_SUFFIXES:
            if text.endswith(suffix) and len(text) > len(suffix):
                text = text[: -len(suffix)]
                changed = True
    return text


def _exercise_title_family(source_title: str, target_title: str) -> bool:
    """书名同族（`KA-71` 配套第一条）：习题书名含教材书名（教材名 ≥4 字），或剥缀后归一相等。"""
    target = _title_core(target_title)
    if len(target) < EXERCISE_TITLE_MIN_CHARS:
        return False
    return target in _title_core(source_title)


def _chapter_title_core(name: str) -> str:
    """章名归一：去「第N章」前缀与空白标点——两边写法不同（「第一章 实数」/「第 1 章 实数」）也算同章。"""
    text = unicodedata.normalize("NFKC", name or "").strip()
    text = _CHAPTER_PREFIX.sub("", text).strip()
    return _CHAPTER_NAME_NOISE.sub("", text).casefold()


def _exercise_chapter_coverage(
    source_names: Sequence[str], target_names: Sequence[str]
) -> float | None:
    """章覆盖（`KA-71` 配套第二条）：有章号的本书章在同号教材章上对出同名或包含关系的比例。

    分母只算**认得出章号**的本书章（前言一类没有章号，不参与也不拉低比例）；本书章一个章号
    都认不出时返回 `None`（无法判定，不当作 0%）。
    """
    numbered = [
        (number, name)
        for name in source_names
        if (name or "").strip() and (number := chapter_number(name)) is not None
    ]
    if not numbered:
        return None
    target_by_number: dict[int, str] = {}
    for name in target_names:
        number = chapter_number(name)
        if number is not None:
            target_by_number.setdefault(number, name)
    covered = 0
    for number, name in numbered:
        target = target_by_number.get(number)
        if target is None:
            continue
        source_core = _chapter_title_core(name)
        target_core = _chapter_title_core(target)
        if not source_core or not target_core:
            continue
        if (
            source_core == target_core
            or source_core in target_core
            or target_core in source_core
        ):
            covered += 1
    return covered / len(numbered)


#: 解答起首的行首形状（`KA-71` B 门中文详解／英文详解）：行首标记后接空白或标点。
_SOLUTION_START = re.compile(
    r"(?m)^[ \t]*(?:解(?:答|析)?|证(?:明)?|详解|"
    r"Solution(?:\s*\([^)\n]*\))?|Proof|Alternate\s+solution)"
    r"\s*(?:[：:]|[，,。.、]|\s)"
)

#: 同套题解本的书名形状（`KA-71` B 门第三条）：只认书名这条读面事实，不逐页扫他书。
_SOLUTION_BOOK_TITLE = re.compile(r"题解|解答|答案|详解|solution", re.IGNORECASE)


def _solution_book_in_set(session: Session, *, book: AfBook) -> bool:
    """同套里是否已有题解／答案本：书名带题解形状的同套习题书即算一条证据。"""
    if not book.knowledge_id:
        return False
    for other in BookRepository(session).list_books():
        if other.book_id == book.book_id or other.knowledge_id != book.knowledge_id:
            continue
        if other.doc_kind != DOC_KIND_EXERCISE:
            continue
        if _SOLUTION_BOOK_TITLE.search(str(other.title or "")):
            return True
    return False


def _exercise_solution_evidence(
    session: Session, *, book: AfBook, evidence: EvidenceSet
) -> bool:
    """解答证据（`KA-71` B 门）：本书行首详解、书末答案段、同套题解本三者任一成立即可。"""
    pages = list(evidence.blocks)
    if answer_section_window(pages) is not None:
        return True
    if any(
        _SOLUTION_START.search(block.text or "")
        for page in pages
        for block in page.blocks
    ):
        return True
    return _solution_book_in_set(session, book=book)


def _companion_outline_match(
    session: Session,
    *,
    book: AfBook,
    target: AfBook,
    source_names: Sequence[str],
    candidates: tuple[str, ...],
    evidence: EvidenceSet | None = None,
) -> ExerciseOutlineMatch:
    """配套命中：选中的教材＋按章号对应的章名（教材侧只认带章号的顶层行）。

    **全局解答证据门（`KA-71` B 门扩展）**：配套型也必须有「题+解」证据才出目录——匹配到教材
    只解决「章从哪来」，不解决「这道题有没有解答」。缺证据时转 `red`（本批零目录行），与独立型
    同一条红灯纪律。`evidence` 缺席时维持旧行为（拿到取证面的批次才判红，避免无取证面误杀）。
    """
    if evidence is not None and not _exercise_solution_evidence(session, book=book, evidence=evidence):
        return ExerciseOutlineMatch(state="red", candidates=candidates)
    return ExerciseOutlineMatch(
        state="hit",
        target_book_id=target.book_id,
        candidates=candidates,
        chapters=_numbered_chapter_pairs(
            source_names, _target_chapter_names(session, target.book_id)
        ),
    )


def _standalone_or_red(
    session: Session,
    *,
    book: AfBook,
    evidence: EvidenceSet | None,
    candidates: tuple[str, ...],
    state: str,
) -> ExerciseOutlineMatch:
    """独立型判定（`KA-71` 第二步）：配得出题目＋解答才算独立型，否则红灯。

    `evidence` 缺席（调用方没有取齐页读数）时不判红：红灯只由拿到了取证面的批次下。
    """
    if evidence is None or _exercise_solution_evidence(
        session, book=book, evidence=evidence
    ):
        return ExerciseOutlineMatch(state=state, candidates=candidates)
    return ExerciseOutlineMatch(state="red", candidates=candidates)


def match_exercise_outline(
    session: Session,
    engine: KnowledgeEngine,
    *,
    book: AfBook,
    chapters: Sequence[str],
    evidence: EvidenceSet | None = None,
) -> ExerciseOutlineMatch:
    """习题集书型判定与教材匹配（PLAN-036 S1／S2，REQ-178 R1／R2）：先配套、后独立、皆无则红。

    顺序固定（2026-11-17 用户令；2026-11-22 用户裁决红灯改全局）：① 书名同族 → 配套（零调用）；
    ② 章覆盖 ≥80% → 配套（零调用）；
    ③ 0% 覆盖＋认得出章号＋无书名同族 → 确定性命定独立（不交模型，防止把独立习题集改写成
    教材章名）；④ 其余交一次模型按范围语义判，模型说不配或失败转独立；⑤ **全局解答证据门**——
    任何命中（配套或独立）在 `evidence` 非空时都必须拿得出「习题＋解答」证据（`KA-71` B 门），
    否则 `red`：本批零目录行，不回退教材目录腿；`evidence` 缺席时不判红（无取证面的批次不误杀）。
    单候选教材**不自动命中**：b05 与 b07／b11 同套但章名不同，必须过覆盖门或模型判。
    """
    candidates = _exercise_match_candidates(session, book=book)
    ids = tuple(other.book_id for other, _ in candidates)
    source_names = tuple(name for name in chapters if (name or "").strip())
    scored = [
        (other, _exercise_chapter_coverage(source_names, names))
        for other, names in candidates
    ]
    if source_names:
        family = [
            other
            for other, _ in scored
            if _exercise_title_family(str(book.title or ""), str(other.title or ""))
        ]
        if family:
            return _companion_outline_match(
                session, book=book, target=family[0], source_names=source_names,
                candidates=ids, evidence=evidence,
            )
        covered = [
            (other, ratio)
            for other, ratio in scored
            if ratio is not None and ratio >= EXERCISE_COMPANION_COVERAGE
        ]
        if covered:
            target = max(covered, key=lambda item: item[1] or 0.0)[0]
            return _companion_outline_match(
                session, book=book, target=target, source_names=source_names,
                candidates=ids, evidence=evidence,
            )
        if scored and all(ratio == 0.0 for _, ratio in scored):
            return _standalone_or_red(
                session,
                book=book,
                evidence=evidence,
                candidates=ids,
                state="miss",
            )
    matcher = getattr(engine, "exercise_match", None)
    if not source_names or not candidates or not callable(matcher):
        return _standalone_or_red(
            session, book=book, evidence=evidence, candidates=ids, state="skipped"
        )
    request = ExerciseMatchInput(
        title=str(book.title or book.book_id),
        chapters=source_names,
        candidates=tuple(
            ExerciseMatchCandidate(
                book_id=other.book_id,
                title=str(other.title or other.book_id),
                chapters=names,
            )
            for other, names in candidates
        ),
    )
    try:
        choice = matcher(request)
    except EngineError as exc:
        logger.warning("习题集目录匹配调用失败（%s）：退回独立目录（%s）", book.book_id, exc)
        return _standalone_or_red(
            session, book=book, evidence=evidence, candidates=ids, state="failed"
        )
    if choice is None:
        return _standalone_or_red(
            session, book=book, evidence=evidence, candidates=ids, state="skipped"
        )
    target = next(
        (other for other, _ in candidates if other.book_id == choice.book_id), None
    )
    if target is None:
        return _standalone_or_red(
            session, book=book, evidence=evidence, candidates=ids, state="miss"
        )
    names = next(names for other, names in candidates if other.book_id == target.book_id)
    # 全局解答证据门（模型命中出口，与 _companion_outline_match 同一纪律）
    if evidence is not None and not _exercise_solution_evidence(session, book=book, evidence=evidence):
        return ExerciseOutlineMatch(state="red", candidates=ids)
    return ExerciseOutlineMatch(
        state="hit",
        target_book_id=target.book_id,
        candidates=ids,
        chapters=_validated_chapter_pairs(choice.chapters, source_names, names),
    )


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

    模式与目标先读来源书清单（最近一次习题批次写下的），清单不在位时退到批次流水。题行恒读**本书**：
    配套提取把教材题面与本册解答配成一行落在本书自己的章下（REQ-170），旧跨书合并的行集不再进读面。
    配套模式的 `parent_id` 按章号改挂**目标教材**的同号章（读时派生，库行不动）：屏上的目录列的是
    目标书那套章，退回本书章号会让全部题行落成待梳理；目标书缺同号章时保留本书的行。
    状态优先取章级索引（写盘时的读数），索引缺席的行按题包指针兜底。
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
    if kind != "companion":
        kind = "standalone"
        target_book_id = None

    rows = KpRepository(session).list_for_book(book.book_id)
    live = [row for row in rows if row.gen_status != "rejected"]
    types = node_types_of(live)
    target_chapters: dict[int, str] = {}
    if kind == "companion" and target_book_id:
        target_chapters = _chapters_by_number(
            KpRepository(session).list_for_book(target_book_id)
        )
    own_by_id = {row.node_id: row for row in live}
    item_rows = [
        row
        for row in live
        if row.node_kind == NODE_KIND_POINT and types.get(row.node_id) == NODE_TYPE_EXERCISE
    ]
    statuses = _exercise_index_statuses(data_root, book)
    items: list[dict[str, Any]] = []
    for row in item_rows:
        status = statuses.get(row.node_id) or _exercise_pack_status_of(
            session, data_root=data_root, row=row
        )
        display_parent = row.parent_id
        if target_chapters:
            chapter = _target_chapter_of(row.parent_id, own_by_id, types)
            number = chapter_number(chapter.name or "") if chapter is not None else None
            if number is not None:
                display_parent = target_chapters.get(number, row.parent_id)
        items.append(
            {
                **exercise_item_ref(row),
                "parent_id": display_parent,
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


#: 读数腿交给模型的候选题上限：一章上百题时只给前一段，读数不追求覆盖整章。
EXERCISE_READING_TARGET_LIMIT = 40


def _exercise_classify(
    engine: KnowledgeEngine,
    *,
    title: str,
    items: Sequence[ExerciseItem],
    targets: Sequence[tuple[str, str]],
    target_title: str = "",
) -> tuple[str, dict[str, ExerciseClassifyItem]]:
    """一节习题的逐题机器读数（REQ-161 W5）：是否对应教材题目＋题型。

    模型只做判读，切分与落行仍走规则腿；读数不改变题号归属。引擎没有这条腿时状态
    `skipped`、零读数；调用失败状态 `failed`、零读数——失败如实披露，不拿空读数冒充
    「都不对应」。回域外题号或对应题不在候选里的条目一律丢弃，读数只认本节自己的题。
    """
    classifier = getattr(engine, "exercise_classify", None)
    if not items or not callable(classifier):
        return "skipped", {}
    request = ExerciseClassifyInput(
        title=title,
        items=tuple((item.item_no, item.text) for item in items),
        targets=tuple(targets),
        target_title=target_title,
    )
    try:
        reply = classifier(request)
    except EngineError as exc:
        logger.warning("习题解析读数调用失败（%s）：不造读数（%s）", title, exc)
        return "failed", {}
    if reply is None:
        return "skipped", {}
    allowed = {item.item_no for item in items}
    known_targets = {number for number, _ in targets}
    readings: dict[str, ExerciseClassifyItem] = {}
    for entry in getattr(reply, "items", ()) or ():
        number = str(entry.item_no or "").strip()
        if not number or number not in allowed or number in readings:
            continue
        target = str(entry.target or "").strip()
        if target and known_targets and target not in known_targets:
            target = ""
        readings[number] = ExerciseClassifyItem(
            item_no=number,
            target=target,
            kind=str(entry.kind or "").strip()[:20],
        )
    return "done", readings


def _exercise_reading_meta(
    state: str, reading: ExerciseClassifyItem | None
) -> dict[str, Any]:
    """题行 meta 的读数三键（REQ-161 W5）：`match_state` 恒随节，两枚读数按有无给。

    `skipped`（引擎没有这条腿）不写键——旧引擎的题行形状不因新腿出现而无故多出空值。
    """
    if state not in ("done", "failed"):
        return {}
    meta: dict[str, Any] = {"match_state": state}
    if reading is not None:
        if reading.target:
            meta["target_item"] = reading.target
        if reading.kind:
            meta["exercise_type"] = reading.kind
    return meta


def _exercise_reading_line(
    state: str, reading: ExerciseClassifyItem | None, *, target_title: str
) -> str:
    """题包来源段的读数行（REQ-161 W5）：判读什么写什么，失败只写失败。"""
    if state == "failed":
        return "对应读数：模型判定失败（未造读数）"
    if state != "done" or reading is None:
        return ""
    where = reading.target
    if where and target_title:
        where = f"《{target_title}》{where}"
    headline = f"对应教材题：{where}" if where else "对应教材题：无"
    if reading.kind:
        headline += f"；题型：{reading.kind}"
    return headline


def _verify_pair_rounds(
    pairs: Sequence[tuple[str, str, str]],
) -> list[list[tuple[str, str, str]]]:
    """一章的题对按正文预算分轮（同 `_exercise_rounds` 的切法，不重排题序）。"""
    rounds: list[list[tuple[str, str, str]]] = []
    current: list[tuple[str, str, str]] = []
    size = 0
    for pair in pairs:
        length = len(pair[1]) + len(pair[2])
        if current and size + length > EXERCISE_VERIFY_TEXT_BUDGET:
            rounds.append(current)
            current, size = [], 0
        current.append(pair)
        size += length
    if current:
        rounds.append(current)
    return rounds


def _verify_readings(
    verifier: Callable[[ExerciseVerifyInput], Any],
    request: ExerciseVerifyInput,
    allowed: set[str],
) -> dict[str, ExerciseVerifyReading] | None:
    """一次校验调用 → 命中域内题号的读数；引擎没有这条腿返回 `None`。

    两侧折到同一把键再比（REQ-178）：模型常把输入里的 `[N]`／`（N）` 原样照回，书名一侧的键
    是裸的——不折键就全部对不上（b07 的 472＋98 笔失败）。折不出键的文本与域外结论一律丢弃。
    """
    reply = verifier(request)
    if reply is None:
        return None
    readings: dict[str, ExerciseVerifyReading] = {}
    for entry in getattr(reply, "items", ()) or ():
        raw = str(getattr(entry, "item_no", "") or "").strip()
        number = exercise_item_key(raw) or raw
        if not number or number not in allowed or number in readings:
            continue
        verdict = str(getattr(entry, "verdict", "") or "").strip()
        if verdict not in EXERCISE_VERIFY_VERDICTS:
            continue
        readings[number] = entry
    return readings


def _exercise_verify_fragments(
    engine: KnowledgeEngine,
    *,
    title: str,
    pairs: Sequence[tuple[str, str, str]],
) -> dict[str, dict[str, Any]]:
    """配对校验（REQ-170 第三步，`KA-70`）：一章的「题目＋解答」对交模型判四件事——切分对不对、
    是否同一题、数学结论对不对、解答是否完整需要补全；英文由引擎先译后判，note 用中文。

    返回按章内局部题号索引的 `meta` 片段。读数只披露、不否决成行：调用失败或某题没拿到结论
    只把它记 `verify_state='failed'`，行与题包照出、配对状态不动；引擎没有这条腿时零键，
    旧引擎的题行形状不因新腿出现而变。域外题号与域外结论一律丢弃，不拿它们冒充读数。

    坏轮同轮重试一次（REQ-178）：调用抛 `EngineError`（坏 JSON／空回复）或回了但一枚域内读数
    都没有，都不再直接记 failed——先原样重发同一批材料一次，两次都坏才按失败披露。
    """
    verifier = getattr(engine, "exercise_verify", None)
    if not pairs or not callable(verifier):
        return {}
    fragments: dict[str, dict[str, Any]] = {}
    for chunk in _verify_pair_rounds(pairs):
        request = ExerciseVerifyInput(
            title=title,
            items=tuple(
                ExerciseVerifyItem(item_no=number, problem=problem, solution=solution)
                for number, problem, solution in chunk
            ),
        )
        allowed = {number for number, _, _ in chunk}
        readings: dict[str, ExerciseVerifyReading] | None = None
        for attempt in (1, 2):
            try:
                readings = _verify_readings(verifier, request, allowed)
            except EngineError as exc:
                logger.warning(
                    "习题配对校验第 %d 次调用失败（%s）：%s", attempt, title, exc
                )
                readings = None
                continue
            if readings is None:
                return {}
            if readings:
                break
            logger.warning("习题配对校验第 %d 次零读数（%s）：同轮重试一次", attempt, title)
        if not readings:
            logger.warning("习题配对校验重试后仍无可用读数（%s）：不否决成行", title)
            for number, _, _ in chunk:
                fragments[number] = {"verify_state": "failed"}
            continue
        for number in allowed:
            reading = readings.get(number)
            if reading is None:
                fragments[number] = {"verify_state": "failed"}
                continue
            fragment: dict[str, Any] = {
                "verify_state": "done",
                "verify_verdict": reading.verdict,
            }
            note = str(getattr(reading, "note", "") or "").strip()
            if note:
                fragment["verify_note"] = note[:300]
            fragments[number] = fragment
    return fragments


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

    成行写包之后按 `KA-70` 走一次配对校验：答案段回填过的题以**回填后**的解答交问，读数由
    `set_exercise_verify` 写回行 `meta`（`verify_state`／`verify_verdict`／`verify_note`），
    只披露、不否决成行；已确认行不碰。
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
    # 空白解答的题（PLAN-036 W11）：题号 → 重写所需的全部输入，答案段读完一次统一回配。
    blanks: dict[str, list[dict[str, Any]]] = {}
    # 配对校验的候选（`KA-70`）：成行且题包写成功的题对，答案回填之后统一交问。
    verify_entries: list[dict[str, Any]] = []

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
        pages = _section_block_bodies(evidence, span)
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
        # 独立模式没有目标教材：读数只判题型，「对应教材题」恒无。
        reading_state, readings = _exercise_classify(
            engine, title=span.title or "", items=items, targets=()
        )

        for ordinal, item in enumerate(items, start=1):
            draft = by_key.get(item.item_no)
            # REQ-179 R2：模型未回该题草稿＝判定失败（未造读数）——解答段写显式「不可用」标记，
            # 不再用「未配到解答」占位句把失败说明固化成正文；两者在 `exercise_pack_status`
            # 都按「仅题面」读，根侧屏面可据标记文案区分「真没配到」与「判定失败」。
            problem = draft.problem if draft is not None else ""
            solution = draft.solution if draft is not None else ""
            if draft is None:
                solution = EXERCISE_UNAVAILABLE_TEXT
            status = _exercise_status(problem, solution)
            reading = readings.get(item.item_no)
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
                        **_exercise_reading_meta(reading_state, reading),
                    },
                )
            )
            item_rows += 1
            statuses.append((row.node_id, status))
            source_line = f"《{book_title}》{_exercise_page_span([item.page_start, item.page_end])}"
            reading_line = _exercise_reading_line(
                reading_state, reading, target_title=""
            )
            problem_text = _exercise_problem_text(
                problem,
                book_title=book_title,
                pages=[item.page_start, item.page_end],
            )
            source_lines = [source_line] + ([reading_line] if reading_line else [])
            # REQ-179 R1（写入面）：题包两段的产物图片引用改写成 8902 读腿 URL 后落盘。
            pack_problem, pack_solution = rewrite_pack_image_urls(
                problem_text, solution, book_id=acceptance.book_id
            )
            if _write_exercise_pack(
                session,
                data_root=data_root,
                node_id=row.node_id,
                label=item.label,
                problem=pack_problem,
                solution=pack_solution,
                source_lines=source_lines,
            ):
                pack_rows += 1
                verify_entries.append(
                    {
                        "node_id": row.node_id,
                        "item_no": item.item_no,
                        "problem": problem,
                        "solution": solution,
                        "parent_id": span.parent_id,
                    }
                )
                if problem.strip() and not solution.strip():
                    blanks.setdefault(item.item_no, []).append(
                        {
                            "node_id": row.node_id,
                            "label": item.label,
                            "problem": problem_text,
                            "source_lines": source_lines,
                            "status_index": len(statuses) - 1,
                        }
                    )
        session.commit()

    answer_pairs, filled_solutions = _fill_textbook_answers(
        session, data_root=data_root, evidence=evidence, blanks=blanks, statuses=statuses
    )
    _verify_independent_pairs(
        session,
        engine,
        book_id=acceptance.book_id,
        entries=verify_entries,
        filled=filled_solutions,
        outline_nodes=outline_nodes,
    )

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
        answer_pairs=answer_pairs,
    )


def _fill_textbook_answers(
    session: Session,
    *,
    data_root: Path | str,
    evidence: EvidenceSet,
    blanks: Mapping[str, list[dict[str, Any]]],
    statuses: list[tuple[str, str]],
) -> tuple[int, dict[str, str]]:
    """答案段回配（PLAN-036 W11）：只填空白解答，按题号唯一命中，逐题重写题包。

    取材只认连续运行页眉窗（`answer_section_window`），切分只认严格递增题号（`textbook_answer_key`）。
    同一题号对上多枚空白行时不猜——章节各自编号的书上会撞号，猜错就是把别人的答案写进这一题。状态
    不落库，但章级索引在本批收尾才写，所以这里就地改 `statuses` 的对应枚，派生视图与题包一致。

    返回（填上的枚数，题行 `node_id` → 新解答）：后一枚供配对校验（`KA-70`）在回填之后按回填
    文本交问——判的是读者最终看到的「题目＋解答」对，不是回填前的空白。
    """
    window = answer_section_window(evidence.blocks)
    if window is None or not blanks:
        return 0, {}
    start, end, _ = window
    key = textbook_answer_key(
        [
            (page.page, "\n".join(block.text or "" for block in page.blocks))
            for page in evidence.blocks
            if start <= page.page <= end
        ]
    )
    pairs = 0
    filled: dict[str, str] = {}
    for entry in key:
        candidates = blanks.get(entry.item_no) or []
        if len(candidates) != 1:
            continue
        payload = candidates[0]
        node_id = str(payload["node_id"])
        problem = str(payload["problem"])
        if not _write_exercise_pack(
            session,
            data_root=data_root,
            node_id=node_id,
            label=str(payload["label"]),
            problem=problem,
            solution=entry.text,
            source_lines=list(payload["source_lines"]),
        ):
            continue
        statuses[int(payload["status_index"])] = (
            node_id,
            _exercise_status(problem, entry.text),
        )
        filled[node_id] = entry.text
        pairs += 1
    session.commit()
    return pairs, filled


def _outline_root(
    node_id: str | None, by_id: Mapping[str, OutlineNode]
) -> OutlineNode | None:
    """目录行 → 父链顶端的章行；断链时取能走到的最后一枚。"""
    seen: set[str] = set()
    current = by_id.get(node_id or "")
    top: OutlineNode | None = None
    while current is not None and current.row.node_id not in seen:
        seen.add(current.row.node_id)
        top = current
        current = by_id.get(current.row.parent_id or "")
    return top


def _verify_independent_pairs(
    session: Session,
    engine: KnowledgeEngine,
    *,
    book_id: str,
    entries: Sequence[dict[str, Any]],
    filled: Mapping[str, str],
    outline_nodes: Sequence[OutlineNode],
) -> int:
    """独立模式的配对校验（`KA-70`）：答案回填之后按顶层章分组交问，读数写回行 `meta`。

    同一章内题号重复（本书各节各自起号）只把首个题号交问——`_exercise_verify_fragments` 按章内
    局部题号索引，撞号会让两题的结论互相覆盖；后出现的那枚不猜、不给读数。题面或解答仍为空的
    对不交问：没有两段可判的材料，交问只会换来一枚凭空的结论。已确认行由 repository 的
    `confirmed=0` 守卫挡在写外。返回真正写下读数的行数。
    """
    verifier = getattr(engine, "exercise_verify", None)
    if not entries or not callable(verifier):
        return 0
    by_id = {node.row.node_id: node for node in outline_nodes}
    groups: dict[str, dict[str, Any]] = {}
    for entry in entries:
        solution = filled.get(str(entry["node_id"]), str(entry["solution"]))
        problem = str(entry["problem"])
        if not problem.strip() or not solution.strip():
            continue
        root = _outline_root(entry["parent_id"], by_id)
        group = groups.setdefault(
            root.row.node_id if root is not None else str(entry["parent_id"] or ""),
            {
                "title": str(root.row.name or "") if root is not None else "",
                "pairs": [],
                "nodes": {},
            },
        )
        number = str(entry["item_no"])
        if number in group["nodes"]:
            continue
        group["nodes"][number] = str(entry["node_id"])
        group["pairs"].append((number, problem, solution))

    repo = KpRepository(session)
    written = 0
    for group in groups.values():
        fragments = _exercise_verify_fragments(
            engine, title=group["title"], pairs=group["pairs"]
        )
        for number, node_id in group["nodes"].items():
            fragment = fragments.get(number)
            if fragment is None:
                continue
            if repo.set_exercise_verify(
                book_id=book_id,
                node_id=node_id,
                state=str(fragment.get("verify_state") or ""),
                verdict=str(fragment.get("verify_verdict") or ""),
                note=str(fragment.get("verify_note") or ""),
            ):
                written += 1
    session.commit()
    return written


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


def _chapters_by_number(rows: Sequence[AfKp]) -> dict[int, str]:
    """一批目录行 → 章号 → 章行 `node_id`：同号取书序第一枚，前言／附件这类无章号的行不进表。"""
    live = [row for row in rows if row.gen_status != "rejected"]
    types = node_types_of(live)
    chapters: dict[int, str] = {}
    for row in sorted(
        live, key=lambda item: item.order_hint if item.order_hint is not None else 0
    ):
        if row.node_kind != NODE_KIND_OUTLINE or types.get(row.node_id) != NODE_TYPE_CHAPTER:
            continue
        number = chapter_number(row.name or "")
        if number is not None:
            chapters.setdefault(number, row.node_id)
    return chapters


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


# ------------------------------------------------------------- 配套提取（KA-53／REQ-170）


@dataclass(frozen=True, slots=True)
class _TargetProblemTable:
    """教材习题段的确定性读数：章号 → 章行、章号 → 题号 → 题（题面与页区间），与跳过的段。"""

    title: str
    chapters: Mapping[int, AfKp]
    problems: Mapping[int, Mapping[str, ExerciseItem]]
    skipped: tuple[dict[str, Any], ...] = ()


def _label_block_index(page: BlocksPage | None, label: str) -> int | None:
    """题号原文 → 页产物里的块序（NFKC 折叠后的行首匹配）：认不出回 `None`＝整页。"""
    needle = unicodedata.normalize("NFKC", (label or "").strip())
    if page is None or not needle:
        return None
    for block in sorted(page.blocks, key=lambda item: (item.order, item.index)):
        for line in (block.text or "").splitlines():
            if unicodedata.normalize("NFKC", line).strip().startswith(needle):
                return block.index
    return None


def _exercise_item_bounds(
    items: Sequence[ExerciseItem], pages: Mapping[int, BlocksPage]
) -> list[tuple[int | None, int | None]]:
    """每题的块界 `(起点块, 终点块)`：起点块按题号行认；终点块＝同一页下一题的起点块减一。"""
    starts = [_label_block_index(pages.get(item.page_start), item.label) for item in items]
    bounds: list[tuple[int | None, int | None]] = []
    for index, item in enumerate(items):
        end: int | None = None
        if index + 1 < len(items) and items[index + 1].page_start == item.page_end:
            next_start = starts[index + 1]
            if next_start is not None and next_start > 0:
                end = next_start - 1
        bounds.append((starts[index], end))
    return bounds


def _exercise_item_anchors(
    item: ExerciseItem, *, start_block: int | None, end_block: int | None
) -> list[dict[str, Any]]:
    """题行锚点（`KA-18`）：本题在**本书本版**的题号页与块界；跨页再补一枚末页锚。"""
    anchors = [_page_anchor(item.page_start, start_block, item.text[:200])]
    if item.page_end != item.page_start:
        anchors.append(_page_anchor(item.page_end, end_block, ""))
    return anchors


def _chapter_local_item_no(item_no: str, chapter_no: int) -> str:
    """章内比对键：`7-1` 与教材 `1.` 在同一章里是同一道题（`KA-53` 的章＋题号配对）。"""
    prefix = f"{chapter_no}-"
    return item_no[len(prefix) :] if item_no.startswith(prefix) else item_no


def _item_order(item_no: str) -> tuple[int, ...]:
    """题序的比较键：数字段逐段比，`2-10` 大于 `2-9`。"""
    return tuple(int(part) for part in re.split(r"[^0-9]+", item_no) if part)


def _target_problem_table(
    session: Session, *, data_root: Path | str, target: AfBook
) -> _TargetProblemTable:
    """教材的习题段 → 「章号 → 章内题号 → 题」（`KA-53`）。

    题面与页区间只取教材在册目录行里的习题段（`meta.toc_kind='exercise'`），按该段**自身取证版本**
    重跑同一把分题规则（`KA-18`）。习题段的起点页早于本章起点页时整章跳过并披露——那是锚点数据
    不可信（b05 第 4 章曾把习题段锚在 p7），拿它切段只会把书首正文冒充成一整章题。
    """
    rows = KpRepository(session).list_for_book(target.book_id, gen_status="candidate")
    by_id = {row.node_id: row for row in rows}
    types = node_types_of(rows)
    spans = _target_exercise_spans(rows, target)
    span_by_id = {span.parent_id: span for span in spans if span.parent_id}
    chapters: dict[int, AfKp] = {}
    problems: dict[int, dict[str, ExerciseItem]] = {}
    skipped: list[dict[str, Any]] = []
    for row in rows:
        meta = row.meta or {}
        if row.node_kind != NODE_KIND_OUTLINE or str(meta.get("toc_kind") or "") != "exercise":
            continue
        chapter = _target_chapter_of(row.parent_id, by_id, types)
        number = chapter_number(chapter.name or "") if chapter is not None else None
        if chapter is None or number is None:
            skipped.append(
                {"node_id": row.node_id, "item_no": "", "source_pages": [], "reason": "习题段上溯不到带章号的章行"}
            )
            continue
        span = span_by_id.get(row.node_id)
        if span is None:
            skipped.append(
                {"node_id": row.node_id, "item_no": "", "source_pages": [], "reason": "习题段取不到页区间"}
            )
            continue
        first_page = _first_anchor_page(chapter)
        if first_page is not None and span.page_start < first_page:
            skipped.append(
                {
                    "node_id": row.node_id,
                    "item_no": "",
                    "source_pages": [span.page_start, span.page_end],
                    "reason": (
                        f"习题段起点页 {span.page_start} 早于本章起点页 {first_page}（锚点不可信，整章跳过）"
                    ),
                }
            )
            continue
        pages_dir = (
            version_dir_for(
                data_root,
                target.domain_id,
                target.course_id,
                target.book_id,
                row.job_id or target.active_job_id or "",
            )
            / "pages"
        )
        pages: list[tuple[int, str]] = []
        missing = False
        for page_no in range(span.page_start, span.page_end + 1):
            try:
                pages.append(
                    (page_no, (pages_dir / f"p{page_no:04d}.md").read_text(encoding="utf-8"))
                )
            except OSError:
                missing = True
                break
        if missing:
            skipped.append(
                {
                    "node_id": row.node_id,
                    "item_no": "",
                    "source_pages": [span.page_start, span.page_end],
                    "reason": "本章习题段缺页产物",
                }
            )
            continue
        items = split_exercise_items(pages)
        if not items:
            skipped.append(
                {
                    "node_id": row.node_id,
                    "item_no": "",
                    "source_pages": [span.page_start, span.page_end],
                    "reason": "本章习题段没切出题号",
                }
            )
            continue
        chapters[number] = chapter
        table = problems.setdefault(number, {})
        for item in items:
            key = _chapter_local_item_no(item.item_no, number)
            if key in table:
                skipped.append(
                    {
                        "node_id": row.node_id,
                        "item_no": item.item_no,
                        "source_pages": [item.page_start, item.page_end],
                        "reason": "同一章两段习题撞了同一题号",
                    }
                )
                continue
            table[key] = item
    return _TargetProblemTable(
        title=str(getattr(target, "title", "") or target.book_id),
        chapters=chapters,
        problems=problems,
        skipped=tuple(skipped),
    )


@dataclass(frozen=True, slots=True)
class _OwnReading:
    """本书切出的一道题：规则切分、模型读数（可缺）与它在本书本版的块界。"""

    item: ExerciseItem
    draft: ExerciseDraft | None
    span: SectionSpan
    start_block: int | None
    end_block: int | None


def _own_chapter_numbers(
    outline_nodes: Sequence[OutlineNode],
) -> tuple[dict[int, OutlineNode], dict[str, int]]:
    """本书的章行：`章号 → 节点` 与 `节点号 → 章号`（章名由教材章名复用，认名字里的章号）。"""
    by_number: dict[int, OutlineNode] = {}
    of_node: dict[str, int] = {}
    for node in sorted(
        outline_nodes,
        key=lambda item: (item.row.order_hint if item.row.order_hint is not None else 0),
    ):
        number = chapter_number(node.row.name or "")
        if number is None or number in by_number:
            continue
        by_number[number] = node
        of_node[node.row.node_id] = number
    return by_number, of_node


def _chapter_number_of(
    node_id: str | None, by_id: Mapping[str, OutlineNode], chapter_of: Mapping[str, int]
) -> int | None:
    """目录行沿父链上溯到最近的章行；断链或没有章号回 `None`。"""
    seen: set[str] = set()
    current = by_id.get(node_id or "")
    while current is not None and current.row.node_id not in seen:
        seen.add(current.row.node_id)
        if current.row.node_id in chapter_of:
            return chapter_of[current.row.node_id]
        current = by_id.get(current.row.parent_id or "")
    return None


_PHYSICAL_ITEM_NO = re.compile(r"^(\d{1,4})-(\d{1,4})$")


def extract_companion_exercise_items(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    data_root: Path | str,
) -> ExerciseBookBuild:
    """配套提取（`KA-53`／REQ-170）：题面取教材、解答取本书，按章配对后逐题落回本书自己的章下。

    用户 2026-11-16 定的链路：目录复用教材的章节段（章名已是教材章名），本腿再按章号对齐两边的题——
    教材该章习题段切出的每道题是屏上的一行（原始题序），本书同章切出的解答按「章＋题号」折到同一把
    键上来配。三种读数：

    - 配对成功：行锚在**本书本版**的解答页块上，题面取教材同一题的正文（跨书页只进 `meta`，不当锚，
      `KA-18`），`meta.pair_state='matched'`；
    - 教材有题、本书没解答：行照样成（题面完整、解答段留占位），`pair_state='unpaired'` 并点名原因；
    - 本书切出的题号在教材该章找不到：另占一行、排在配对行之后（`pair_state='unpaired'`，原因
      「教材本章未收录本题」）——它是「下列可选解答」的那一类，由人勾选确认。

    本腿**不在教材写任何行、任何题包**：配套提取的产出只落在本书（取代旧的跨书合并）。
    """
    repo = KpRepository(session)
    book = BookRepository(session).get(acceptance.book_id)
    if book is None:
        raise KpGateError(404, f"书目不存在：{acceptance.book_id}")
    target_book_id = acceptance.target_book_id or ""
    target = BookRepository(session).get(target_book_id)
    if target is None:
        raise KpGateError(404, f"目标教材不存在：{target_book_id}")
    table = _target_problem_table(session, data_root=data_root, target=target)
    book_title = str(getattr(book, "title", "") or acceptance.book_id)
    node_meta = {node.row.node_id: (node.row.meta or {}) for node in outline_nodes}
    structural = {node.row.parent_id for node in outline_nodes if node.row.parent_id}
    outline_by_id = {node.row.node_id: node for node in outline_nodes}
    chapters, chapter_of = _own_chapter_numbers(outline_nodes)
    pages_by_page = {page.page: page for page in evidence.blocks}

    item_rows = pack_rows = empty_segments = orphan_segments = failed = 0
    failed_details: list[dict[str, Any]] = []
    unmatched_details: list[dict[str, Any]] = [dict(item) for item in table.skipped]
    failed_chapters: set[int] = set()
    own: dict[int, dict[str, _OwnReading]] = {}

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
        pages = _section_block_bodies(evidence, span)
        try:
            items, reply, round_failures = _exercise_section_reply(engine, span, pages)
        except EngineError as exc:
            failed += 1
            failed_details.append(_exercise_failure_detail(span, exc))
            physical = _chapter_number_of(span.parent_id, outline_by_id, chapter_of)
            if physical is not None:
                failed_chapters.add(physical)
            continue
        if round_failures:
            failed += 1
            failed_details.extend(round_failures)
            physical = _chapter_number_of(span.parent_id, outline_by_id, chapter_of)
            if physical is not None:
                failed_chapters.add(physical)
        if not items:
            empty_segments += 1
            continue
        by_key, unmatched = _group_exercise_drafts(items, reply, span)
        unmatched_details.extend(unmatched)
        bounds = _exercise_item_bounds(items, pages_by_page)
        physical = _chapter_number_of(span.parent_id, outline_by_id, chapter_of)
        for index, item in enumerate(items):
            matched = _PHYSICAL_ITEM_NO.match(item.item_no)
            attributed = (
                int(matched.group(1)) if matched is not None and int(matched.group(1)) in chapters else physical
            )
            if attributed is None:
                if by_key.get(item.item_no) is not None:
                    unmatched_details.append(
                        {
                            "item_no": item.item_no,
                            "source_pages": [item.page_start, item.page_end],
                            "reason": "本题上溯不到本书的章行",
                        }
                    )
                continue
            local = _chapter_local_item_no(item.item_no, attributed)
            start_block, end_block = bounds[index]
            own.setdefault(attributed, {}).setdefault(
                local,
                _OwnReading(
                    item=item,
                    draft=by_key.get(item.item_no),
                    span=span,
                    start_block=start_block,
                    end_block=end_block,
                ),
            )

    for number in sorted(table.chapters):
        if number not in chapters:
            unmatched_details.append(
                {
                    "item_no": "",
                    "source_pages": [],
                    "reason": f"教材第 {number} 章在本书目录里没有对应章",
                }
            )

    statuses: list[tuple[str, str]] = []
    for number in sorted(chapters):
        chapter_node = chapters[number]
        target_items = table.problems.get(number, {})
        own_items = own.get(number, {})
        chapter_has_target = number in table.problems
        entries: list[tuple[str, str, ExerciseItem | None, _OwnReading | None, str, str]] = []
        for key, target_item in sorted(
            target_items.items(), key=lambda pair: _item_order(pair[0])
        ):
            entries.append((key, key, target_item, own_items.get(key), "", ""))
        for key in sorted(own_items, key=_item_order):
            if key in target_items:
                continue
            reason = "教材本章未收录本题" if chapter_has_target else "教材本章习题段取不到"
            entries.append((key, "", None, own_items[key], "unpaired", reason))
        if not entries:
            unmatched_details.append(
                {
                    "item_no": "",
                    "source_pages": [],
                    "reason": f"第 {number} 章教材与本书都没切出题",
                }
            )
            continue

        plans: list[dict[str, Any]] = []
        pairs: list[tuple[str, str, str]] = []
        for ordinal, (local, target_key, target_item, reading, forced_state, forced_reason) in enumerate(
            entries, start=1
        ):
            draft = reading.draft if reading is not None else None
            solution = draft.solution.strip() if draft is not None else ""
            # REQ-179 R2：两种「没解答」在题包里要可区分——判定失败（读取失败 / 模型没回该题）
            # 写显式「不可用」标记；「习题集未收录本题」与「模型未读出解答」维持占位句
            # （前者是来源事实、后者是真没配到），都不冒充「有解答」。
            failed_read = (
                (reading is None and number in failed_chapters)
                or (draft is None and reading is not None)
            )
            if not solution and failed_read:
                solution = EXERCISE_UNAVAILABLE_TEXT
            if target_item is not None:
                problem_raw = target_item.text
            elif draft is not None:
                problem_raw = draft.problem
            else:
                problem_raw = ""
            if forced_state:
                state, reason = forced_state, forced_reason
            elif reading is None:
                state = "unpaired"
                reason = "本章解答读取失败" if number in failed_chapters else "习题集未收录本题"
            elif not solution:
                state, reason = "unpaired", "模型未读出解答"
            else:
                state, reason = "matched", ""
            label = (
                reading.item.label
                if reading is not None
                else (target_item.label if target_item is not None else local)
            )
            anchors = (
                _exercise_item_anchors(
                    reading.item, start_block=reading.start_block, end_block=reading.end_block
                )
                if reading is not None
                else []
            )
            own_pages = (
                sorted({reading.item.page_start, reading.item.page_end})
                if reading is not None
                else []
            )
            target_pages = (
                sorted({target_item.page_start, target_item.page_end})
                if target_item is not None
                else []
            )
            plans.append(
                {
                    "ordinal": ordinal,
                    "local": local,
                    "target_key": target_key,
                    "target_item": target_item,
                    "reading": reading,
                    "state": state,
                    "reason": reason,
                    "problem_raw": problem_raw,
                    "solution": solution,
                    "label": label,
                    "anchors": anchors,
                    "own_pages": own_pages,
                    "target_pages": target_pages,
                }
            )
            if problem_raw.strip() and solution:
                pairs.append((local, problem_raw, solution))
        verify_meta = _exercise_verify_fragments(
            engine, title=str(chapter_node.row.name or ""), pairs=pairs
        )

        for plan in plans:
            local = plan["local"]
            target_key = plan["target_key"]
            target_item = plan["target_item"]
            reading = plan["reading"]
            state = plan["state"]
            reason = plan["reason"]
            problem_raw = plan["problem_raw"]
            solution = plan["solution"]
            label = plan["label"]
            anchors = plan["anchors"]
            own_pages = plan["own_pages"]
            target_pages = plan["target_pages"]
            row_meta: dict[str, Any] = {
                "toc_kind": "exercise",
                "source_book_id": acceptance.book_id,
                "source_pages": own_pages,
                "target_book_id": target_book_id,
                "target_item": target_key,
                "problem_book_id": target_book_id if target_item is not None else "",
                "problem_pages": target_pages,
                "pair_state": state,
                "match_state": (
                    "done" if state == "matched" else ("failed" if reading is not None else "")
                ),
            }
            if reason:
                row_meta["pair_reason"] = reason
            row_meta.update(verify_meta.get(local, {}))
            row = repo.create(
                KpNodeRow(
                    node_id=new_node_id(),
                    book_id=acceptance.book_id,
                    job_id=acceptance.evidence_job_id,
                    node_kind=NODE_KIND_POINT,
                    parent_id=chapter_node.row.node_id,
                    order_hint=plan["ordinal"],
                    name=label[:500],
                    norm_key=norm_key_for(local)[:200],
                    summary="",
                    anchors=anchors,
                    gen_status="draft",
                    gen_batch=acceptance.gen_batch,
                    model_ref=EXERCISE_LEG_MODEL_REF,
                    meta=row_meta,
                )
            )
            item_rows += 1
            status = _exercise_status(problem_raw, solution)
            statuses.append((row.node_id, status))
            source_lines: list[str] = []
            if target_item is not None:
                source_lines.append(
                    f"《{table.title}》{_exercise_page_span(target_pages)}（题面）"
                )
            if reading is not None:
                source_lines.append(
                    f"《{book_title}》{_exercise_page_span(own_pages)}（解答）"
                )
            # REQ-179 R1（写入面）：题包两段的产物图片引用改写成 8902 读腿 URL 后落盘。
            pack_problem, pack_solution = rewrite_pack_image_urls(
                _exercise_problem_text(
                    problem_raw,
                    book_title=table.title if target_item is not None else book_title,
                    pages=target_pages or own_pages,
                ),
                solution,
                book_id=acceptance.book_id,
            )
            if _write_exercise_pack(
                session,
                data_root=data_root,
                node_id=row.node_id,
                label=label,
                problem=pack_problem,
                solution=pack_solution,
                source_lines=source_lines,
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
        merged_rows=0,
    )


# --------------------------------------------------------------- 选中题重解析（KA-53／W17）


def _exercise_retry_rows(
    session: Session, *, book_id: str, owner_id: str, selected: Sequence[str]
) -> list[AfKp]:
    """修补批次的行集（`KA-53`）：逐枚校验「行在归属书 ∧ 来源书＝本书 ∧ 未确认」。

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
                f"选中题重解析不受理：{node_id} 不在归属书 {owner_id} 的行里（`KA-53`，零写入）",
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

    配套提取之后题行恒在本书（REQ-170），但旧跨书合并留下的行仍在目标教材里；两条通路先后在
    同一本书上跑过也不猜错行集——先认显式目标，再认本书，最后按最近一批的载荷认旧归属。
    """
    if target_book_id:
        try:
            _exercise_retry_rows(
                session, book_id=book_id, owner_id=target_book_id, selected=selected
            )
            return target_book_id
        except KpGateError:
            _exercise_retry_rows(session, book_id=book_id, owner_id=book_id, selected=selected)
            return book_id
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
                [(page.page_no, page.markdown) for page in _section_block_bodies(evidence, span)]
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
    problem_tables: dict[str, _TargetProblemTable | None] = {}

    for segment_id, jobs in pending.items():
        span = span_by_segment[segment_id]
        pages = _section_block_bodies(evidence, span)
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
                # 配套提取的行（`meta.target_item` 在）重读时题面仍取教材同一题：解答换了，
                # 题面不该跟着模型读出的那份漂。取不到教材表时退回模型读数（披露由明细承接）。
                row_meta = row.meta or {}
                remembered_target = str(row_meta.get("target_book_id") or "")
                target_item_key = str(row_meta.get("target_item") or "")
                target_problem: ExerciseItem | None = None
                if remembered_target and target_item_key:
                    if remembered_target not in problem_tables:
                        table_target = BookRepository(session).get(remembered_target)
                        problem_tables[remembered_target] = (
                            _target_problem_table(
                                session, data_root=data_root, target=table_target
                            )
                            if table_target is not None
                            else None
                        )
                    table = problem_tables[remembered_target]
                    chapter_row = owner_by_id.get(row.parent_id or "")
                    number = (
                        chapter_number(chapter_row.name or "")
                        if chapter_row is not None
                        else None
                    )
                    if table is not None and number is not None:
                        target_problem = table.problems.get(number, {}).get(target_item_key)
                problem_raw = target_problem.text if target_problem is not None else ""
                if not problem_raw.strip():
                    problem_raw = problem_read
                problem = _exercise_problem_text(
                    problem_raw,
                    book_title=(
                        problem_tables[remembered_target].title
                        if target_problem is not None and problem_tables.get(remembered_target)
                        else book_title
                    ),
                    pages=[item.page_start, item.page_end],
                )
                source_lines = []
                if target_problem is not None:
                    source_lines.append(
                        f"《{problem_tables[remembered_target].title}》"
                        f"{_exercise_page_span([target_problem.page_start, target_problem.page_end])}"
                        "（题面）"
                    )
                source_lines.append(
                    f"《{book_title}》{_exercise_page_span([item.page_start, item.page_end])}（解答）"
                )
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
    toc_pages: Iterable[int] = (),
) -> ChapterInput | None:
    """一章的材料两块（`KA-65`）：章前正文块、其下各知识点的总结清单。

    章前正文＝章锚页到首个点子行锚页之前的正文块（`chapter_material_blocks`）：首点更后页时取
    整段页，章与首点同页时只取首点标题块之前的块——子点正文一块都不进章材料。子行按 `order_hint`
    与起点页排成屏上序，空总结的行不进清单（空字符串在 prompt 里只会教模型编一段出来）。
    章没有可解释的起点页时回 `None`：没有页就连章前正文也取不到，发一次只有标题的调用等于付费
    让模型按名字编。无子点章回空块表，由调用方按「无子点章零调用」处理。
    """
    start = _first_anchor_page(row)
    if start is None:
        return None
    page_of = {other.node_id: _first_anchor_page(other) for other in rows}
    children = _point_children(row, rows, types, page_of)
    if not children:
        return None
    first_child = next((child for child in children if page_of[child.node_id]), None)
    first_child_page = page_of[first_child.node_id] if first_child is not None else None
    first_child_block = (
        _first_anchor_block(first_child, first_child_page) if first_child is not None else None
    )
    blocks = chapter_material_blocks(
        evidence.blocks,
        page_start=start,
        first_child_page=first_child_page,
        first_child_block=first_child_block,
        exclude_pages=toc_pages,
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
        blocks=tuple(
            SectionBlock(page=block.page, block_index=block.block_index, text=block.text)
            for block in blocks
        ),
        point_summaries=listed,
    )


def _point_children(
    row: AfKp, rows: Sequence[AfKp], types: Mapping[str, str], page_of: Mapping[str, int | None]
) -> list[AfKp]:
    """一章其下全部知识点行（含更深层），按屏上序：`order_hint` 再锚页（`KA-65`）。"""
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
    return children


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


#: 章级检索的取词函数形状（PLAN-033）：输入章标题与子点名称，返回一组校准资料。
#: `None`＝本轮不取检索资料（测试与未接检索的调用方），章材料与提示词按无检索照常生成。
ReferenceFetcher = Callable[[str, Sequence[str]], Sequence[ReferenceNote]]


def _with_reference_notes(
    material: ChapterInput,
    point_titles: Sequence[str],
    fetcher: ReferenceFetcher | None,
    *,
    node_id: str,
) -> ChapterInput:
    """给章材料挂上检索资料（PLAN-033）：检索只是增强面，任何外部故障都退回无检索材料。"""
    if fetcher is None:
        return material
    try:
        notes = tuple(fetcher(material.title, tuple(point_titles)))
    except Exception:  # noqa: BLE001 - 外部源故障不得吃掉整章
        logger.warning("章级检索失败，按无检索资料继续：%s", node_id, exc_info=True)
        return material
    return replace(material, reference_notes=notes) if notes else material


#: C-6 确定性校验（PLAN-033 W6）：页数／页区间、知识点计数与「全书结构」表述。
_PAGE_FACT_PATTERNS = (
    re.compile(r"共\s*\d+\s*页"),
    re.compile(r"[pP]\s*\d+\s*[-–—~～至]\s*[pP]?\s*\d+"),
    re.compile(r"第\s*\d+\s*[-–—~～至]\s*\d+\s*页"),
    re.compile(r"\d+\s*个知识点"),
)
_BOOK_STRUCTURE_WORDS = ("全书", "本书", "这本", "这门课", "本课程", "整本书", "本章位于")
_LATER_CHAPTER_PATTERNS = (
    re.compile(r"为第\s*[一二三四五六七八九十百零两\d]+\s*章"),
    re.compile(r"第\s*[一二三四五六七八九十百零两\d]+\s*章[^。；;\n]{0,12}(打下|奠定|提供|做|铺|准备|服务)"),
    re.compile(r"后续章节"),
    re.compile(r"下一章"),
    re.compile(r"后面[^。；;\n]{0,6}章"),
)
#: 总结是纯知识概述（用户口径 2026-11-09）：「本章／这一章」自称不进总结。
_SUMMARY_SELF_REFERENCE = re.compile(r"本章节|本章|这一章|这章|整章|全章")
#: 课程用途陈述（同一口径）：论断不写这一章在后续课程里「提供／打下／奠定」什么。
_FUTURE_USE = re.compile(
    r"为[^。；;\n]{0,24}?(?:提供|打下|奠定|做|铺|准备)[^。；;\n]{0,12}?(?:支撑|基础|前提|条件|准备|铺垫)"
)
#: 导读以知识推进收尾（同一口径）：不写「学完这一章……」式能力宣言。
_NARRATIVE_CLOSER = re.compile(r"学完(?:这一章|本章|这章)")
#: 带名称的结果词：名称须在材料或检索资料里有出处（C-6 的第三条）。
_NAMED_RESULT = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9·εη\-]{0,12})(定理|定义|准则|公式|判别法|检验法|法则|引理|推论)"
)
_GENERIC_NAME_PREFIXES = frozenset(
    {
        "这个", "那个", "该", "上述", "如下", "以下", "一个", "每个", "任何", "所有",
        "其", "此", "本", "这些", "那些", "两个", "三个", "几个", "这种", "那种",
    }
)


def _squash(text: str) -> str:
    """校验用的紧凑形：去空白并转小写——PDF 抽取的正文常把术语拆在空格两侧。"""
    return re.sub(r"\s+", "", text or "").lower()


def _chapter_corpus(material: ChapterInput) -> str:
    """出处语料：章名＋章前正文＋子点名称与总结＋检索资料（标题与摘要）。"""
    parts = [material.title]
    parts.extend(block.text for block in material.blocks)
    for title, summary in material.point_summaries:
        parts.extend((title, summary))
    for note in material.reference_notes:
        parts.extend((note.title, note.text))
    return _squash(" ".join(parts))


def _named_result_is_grounded(run: str, corpus: str) -> bool:
    """名称出处判据：紧邻结果词之前的最长可认后缀在语料里出现过即算有出处。

    取后缀而不是整段，是因为「本章给出数列极限的定义」里结果词前的整段话不是名称；
    而「数列极限」「柯西」这类真正的术语名总能在材料里找到。
    """
    cleaned = _squash(run).rstrip("的")
    for length in range(min(len(cleaned), 12), 1, -1):
        suffix = cleaned[-length:]
        if suffix in _GENERIC_NAME_PREFIXES:
            continue
        if suffix in corpus:
            return True
    return False


def _chapter_text_issues(text: str, corpus: str) -> list[str]:
    """一段章级正文的确定性校验；空文本由调用方按「未给出」另判。"""
    value = (text or "").strip()
    if not value:
        return []
    issues: list[str] = []
    for pattern in _PAGE_FACT_PATTERNS:
        match = pattern.search(value)
        if match:
            issues.append(f"出现页数或页区间表述：「{match.group(0).strip()}」")
    for word in _BOOK_STRUCTURE_WORDS:
        if word in value:
            issues.append(f"出现全书结构表述：「{word}」")
    for pattern in _LATER_CHAPTER_PATTERNS:
        match = pattern.search(value)
        if match:
            issues.append(f"出现后续章节承接句：「{match.group(0).strip()}」")
    match = _FUTURE_USE.search(value)
    if match:
        issues.append(f"出现超出本章的用途陈述：「{match.group(0).strip()}」")
    seen: set[str] = set()
    for match in _NAMED_RESULT.finditer(value):
        run, keyword = match.group(1), match.group(2)
        if len(_squash(run)) < 2:
            continue
        if _named_result_is_grounded(run, corpus):
            continue
        name = (run.strip()[-8:] + keyword).lstrip("的")
        if name in seen:
            continue
        seen.add(name)
        issues.append(f"「{name}」在材料与检索资料中没有出处")
    return issues


def chapter_issues(
    material: ChapterInput,
    replied: ChapterReply,
    *,
    check_summary: bool = True,
    check_narrative: bool = True,
) -> tuple[str, ...]:
    """章腿的确定性校验（C-6）：两段非空、禁页数／全书结构／后续章节／课程用途、带名著述须有出处。

    总结另禁「本章／这一章」自称（纯知识概述），导读另禁「学完这一章……」式收束（用户口径 2026-11-09）。
    单点腿只校验它真正要写的那一段（总结腿不看导读、导读腿不看总结）；覆盖率不作失败条件。
    """
    corpus = _chapter_corpus(material)
    issues: list[str] = []
    if check_summary:
        summary = replied.summary or ""
        if not summary.strip():
            issues.append("章总结为空")
        else:
            issues.extend(_chapter_text_issues(summary, corpus))
            match = _SUMMARY_SELF_REFERENCE.search(summary)
            if match:
                issues.append(
                    f"总结出现章节自称：「{match.group(0)}」（直接写知识内容，不以章节口吻组织）"
                )
    if check_narrative:
        narrative = replied.narrative or ""
        if not narrative.strip():
            issues.append("章导读为空")
        else:
            issues.extend(_chapter_text_issues(narrative, corpus))
            match = _NARRATIVE_CLOSER.search(narrative)
            if match:
                issues.append(
                    f"导读出现总结陈词：「{match.group(0)}」（停在知识推进上，不写学完后的能力宣言）"
                )
    deduped: list[str] = []
    for issue in issues:
        if issue not in deduped:
            deduped.append(issue)
    return tuple(deduped[:10])


def _engine_model_name(engine: KnowledgeEngine) -> str:
    """只读探当前文字槽位模型名（PLAN-033 W5）；引擎没有这条腿或探测失败都回空串。"""
    probe = getattr(engine, "current_model_name", None)
    if not callable(probe):
        return ""
    try:
        return str(probe() or "").strip()
    except Exception:  # noqa: BLE001 - 探名只是注记，任何故障都不阻断生成
        logger.warning("章侧模型名探测失败，注记留空", exc_info=True)
        return ""


def chapter_reply_with_retry(
    engine: KnowledgeEngine,
    material: ChapterInput,
    *,
    check_summary: bool = True,
    check_narrative: bool = True,
) -> tuple[ChapterReply, tuple[str, ...]]:
    """一次生成＋同模型一次重试（C-5／C-6）：不过则附修改要求重发，仍不过则原样交回并给命中项。

    首版调用失败照旧抛出（失败域不变）；重试调用失败保留首版与命中项——此时「照写并注明」比
    整章作废更接近用户口径。重试返回空段时用首版该段兜底，命中项按最终要写的两段重算。
    """
    replied: ChapterReply = engine.chapter(material)
    issues = chapter_issues(
        material, replied, check_summary=check_summary, check_narrative=check_narrative
    )
    if not issues:
        return replied, ()
    try:
        retried: ChapterReply = engine.chapter(replace(material, retry_issues=issues))
    except EngineError as exc:
        logger.warning("章腿重试失败，保留首版并落注记：%s（%s）", material.parent_id, exc)
        return replied, issues
    merged = ChapterReply(
        summary=retried.summary or replied.summary,
        narrative=retried.narrative or replied.narrative,
    )
    remaining = chapter_issues(
        material, merged, check_summary=check_summary, check_narrative=check_narrative
    )
    return merged, remaining


def extract_chapters(
    session: Session,
    engine: KnowledgeEngine,
    acceptance: KpAcceptance,
    evidence: EvidenceSet,
    outline_nodes: Sequence[OutlineNode],
    *,
    data_root: Path | str,
    reference_fetcher: ReferenceFetcher | None = None,
) -> ChapterBuild:
    """章级腿（`KA-65`）：先逐节、后逐章的两遍制——章是文件夹，材料只到首个知识点之前。

    每章一次调用，产出**总结＋导读两段**：总结走 `set_outline_summary`（与节级总结同一条系统腿、
    同一条「原值为空才写」的判据），导读走 `write_narrative`＋`set_narrative_ref`（`KA-30` 的
    正文写腿与指针写腿）。导读已有指针或已有文件时**不覆盖**：同一份导读重跑一次内容批不该把
    人改过的那篇冲掉，而对齐正文的办法是显式重发，不是静默改写。

    调用上界＝区间内有子点的章数：无子点的章零调用，区间内零子点的章也零调用——章自己的正文
    只在有知识点时才有「文件夹」的意义。子点有失败节时章腿照发，只读已落库的点总结。

    只由 `content` 批次发起：基本版本的正式通路是「只目录 → 内容」，`full` 是 M5 的旧通路，
    它连目录都是这一批现成的、节总结也才刚写，追加章调用只会让旧批次多付二十次钱。
    """
    repo = KpRepository(session)
    rows = [node.row for node in outline_nodes]
    types = node_types_of(rows)
    page_of = {row.node_id: _first_anchor_page(row) for row in rows}
    toc_pages = {page.page for page in evidence.blocks if toc_entries(page)}
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
        # 区间判据（`KA-65`）：本批重读过的子点才让章值得再发一问；章自己的锚页不参与。
        children = _point_children(row, rows, types, page_of)
        if not any(
            page_of.get(child.node_id) is not None
            and acceptance.page_start <= page_of[child.node_id] <= acceptance.page_end
            for child in children
        ):
            continue
        material = _chapter_material(
            row,
            rows,
            types,
            evidence,
            summaries,
            page_end=ends.get(row.node_id),
            toc_pages=toc_pages,
        )
        if material is None:
            continue
        material = _with_reference_notes(
            material,
            tuple(child.name or child.node_id for child in children),
            reference_fetcher,
            node_id=row.node_id,
        )
        try:
            replied, check_issues = chapter_reply_with_retry(engine, material)
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
        if check_issues and repo.set_chapter_check(
            book_id=acceptance.book_id,
            node_id=row.node_id,
            issues=check_issues,
            model=_engine_model_name(engine),
        ):
            logger.warning(
                "章级校验重试后仍不过，照写并落注记：%s（%s）",
                row.node_id,
                "；".join(check_issues),
            )
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
    reference_fetcher: ReferenceFetcher | None = None,
) -> tuple[ChapterInput, tuple[int, int], EvidenceSet]:
    """单点章腿共用的取材前序（`KA-45`／`KA-47`）：在飞闸 → 版本 → 页区间 → 取证集 → 章前块与子点总结。

    两条同步腿只差写面，前序一字不差：挡在飞是因为本机模型服务多为单并发，两路并跑只会让两边都
    超时；材料不够时宁可 422，也不付一次「没读到正文」的百秒级调用。`revision` 是这一次重写的
    上下文（`KA-48`），随同一次批次绑定交给引擎。`reference_fetcher` 与内容批次同一注入点：
    空值＝不查外部资料，外部源故障按无检索资料继续。
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
        node,
        rows,
        types,
        evidence,
        summaries,
        page_end=ends.get(node.node_id),
        toc_pages={page.page for page in evidence.blocks if toc_entries(page)},
    )
    if material is None:
        raise KpGateError(
            422,
            f"这一章没有可用的章前材料：{node.node_id}（无子知识点或锚点页不可用）",
        )
    page_of = {other.node_id: _first_anchor_page(other) for other in rows}
    point_titles = tuple(
        child.name or child.node_id
        for child in _point_children(node, rows, types, page_of)
    )
    material = _with_reference_notes(
        material, point_titles, reference_fetcher, node_id=node.node_id
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
    reference_fetcher: ReferenceFetcher | None = None,
) -> AfKp:
    """单点总结重生成（`KA-45`）：梳理屏详情面「一键生成／重新生成」的总结腿。

    判定序与写面同族：行不存在 404 → `if_rev` 不匹配 409 → 类型不合 422 → 同书在飞 409 → 模型失败 502
    → 模型没给总结 422。**章节与知识点**可重生成：点走单节取材（`section_spans_of` 区间＋眉脚剔除），
    章走 `_chapter_material` 的章前块与子点总结并一次 `engine.chapter` 调用（回复里的导读丢弃）；两条都只覆盖该行
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
    # 类型要在整册行上派生（`KA-68`）：单行看不到子行，「预备知识／附录」这类没有知识点子行的一级行
    # 会被单行算式判成章，走到章腿才发现没有材料。
    node_type = node_types_of(repo.list_for_book(node.book_id)).get(node_id) or node_type_of(
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
            session,
            data_root=data_root,
            engine=engine,
            node=node,
            revision=revision,
            reference_fetcher=reference_fetcher,
        )
        try:
            replied, check_issues = chapter_reply_with_retry(
                engine, material, check_narrative=False
            )
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
        if check_issues:
            repo.set_chapter_check(
                book_id=node.book_id,
                node_id=node_id,
                issues=check_issues,
                model=_engine_model_name(engine),
            )
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
    reference_fetcher: ReferenceFetcher | None = None,
) -> tuple[AfKp, str]:
    """章导读单点生成（`KA-47`）：知识点梳理屏详情面「一键生成／重新生成」的导读腿，复用内容批次的章材料与章提示词。

    与内容批次章腿同一条取材与同一次调用（`_chapter_material` 章前块＋`engine.chapter`），差别只在
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
    node_type = node_types_of(repo.list_for_book(node.book_id)).get(node_id) or node_type_of(
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
        session,
        data_root=data_root,
        engine=engine,
        node=node,
        revision=revision,
        reference_fetcher=reference_fetcher,
    )
    try:
        replied, check_issues = chapter_reply_with_retry(
            engine, material, check_summary=False
        )
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
    if check_issues:
        repo.set_chapter_check(
            book_id=node.book_id,
            node_id=node_id,
            issues=check_issues,
            model=_engine_model_name(engine),
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
    doc_kind: str | None = None,
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

    `stage='outline'` 在 `doc_kind='exercise'` 的书上（`KA-6`）同样只覆目录行：习题集的题行是
    `exercise` 腿的产出，重写一层章目录不是它们的过期读数；教材侧目录重跑维持旧口径。

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
            node_kind=(
                NODE_KIND_POINT
                if stage in ("content", "exercise")
                else NODE_KIND_OUTLINE
                if stage == "outline" and doc_kind == DOC_KIND_EXERCISE
                else None
            ),
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
    #: 逐页判页腿实判过的页（P-1~P-5）：空集＝这一轮没有逐页判定（无标记或引擎无此腿）。
    outline_judged_pages: tuple[int, ...] = ()
    #: 核对腿救回来的条目名（`KA-38`）：只有「落在哪一页」被模型改过，名字与层级照旧。
    verified_outline_entries: tuple[str, ...] = ()
    #: 附件行枚数（`KA-39`）：正文前后的引言／符号表等成行但标 `reference`，不计入知识点。
    reference_rows: int = 0
    #: 成行处折叠掉的重影行数（D-11／D-12）：同父同名只留书序第一枚，书级附件名全书唯一。
    duplicate_outline_rows: int = 0
    #: 三级细目折入二级的枚数（KA-62）：不单独成行，所属二级的显示段因此覆盖它们的页范围。
    folded_sub_items: int = 0
    #: P0 种类审校审过的在册行数（`KA-63`，第三十七键）：只数真正过判据的行，拆分行自身不算。
    type_review_rows: int = 0
    #: 行名就是习题标签、因而未发点腿的行名（第三十八键）：`KA-63` ①只披露不改写。
    type_review_flags: tuple[str, ...] = ()
    #: 种类审校拆出并新增的习题行数（第三十九键）：`model_ref='rule:exercise_split'`，零模型调用。
    split_exercise_rows: int = 0
    #: 点腿按预算切出的取材窗口数（`KA-64`，第四十键）：一节超预算就分几次发问。
    point_windows: int = 0
    #: 相邻小节并为一次发问的合并数（`KA-64`，第四十一键）：合并省的是调用，账面照记。
    point_merges: int = 0
    #: 写成 `meta.material_ranges` 的点行枚数（`KA-66`，第四十二键）：详情取材落库的行数。
    material_ranges_rows: int = 0
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
        """协议之外的能力透传：包装器不能顺手关掉适配器自己的资源。

        `toc_outline`（`KA-57`）与 `toc_page`（P-2）是可选腿：引擎有它才透传，并且透传的是**记过账**
        的那一枚——两条腿都是模型调用，成本与其它腿同额入账（`KA-23`）。引擎没有这条腿时照旧
        `AttributeError`，`build_outline` 的 `getattr` 探测才能把新旧两条读数分开。
        """
        value = getattr(self._engine, item)
        if item not in {"toc_outline", "toc_page"} or not callable(value):
            return value
        if item == "toc_page":

            def recorded_page(page_no: int, text: str) -> Any:
                return self._record(
                    "toc_page",
                    lambda: value(page_no, text),
                    {"page": page_no, "char_count": len(text)},
                )

            return recorded_page

        def recorded(rows: Sequence[TocOutlineRow]) -> list[TocOutlineRow]:
            return self._record(
                "toc_outline",
                lambda: value(rows),
                {"count": len(rows), "names": [row.name[:60] for row in rows][:20]},
            )

        return recorded

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

    def exercise_match(self, material: ExerciseMatchInput) -> ExerciseMatchChoice | None:
        """习题集目录匹配腿（REQ-161 W4）也是一条模型调用，一调用一行。

        内层引擎没有这条腿时回 `None`（编排层按「一次调用都没发」跳过），而不是抛
        `AttributeError`——匹配腿是旁路，不能因引擎能力缺失把整批目录拖败。
        """
        matcher = getattr(self._engine, "exercise_match", None)
        if not callable(matcher):
            return None
        return self._record(
            "exercise_match",
            lambda: matcher(material),
            {
                "title": material.title,
                "chapter_count": len(material.chapters),
                "candidate_count": len(material.candidates),
            },
        )

    def exercise_classify(self, material: ExerciseClassifyInput) -> ExerciseClassifyReply | None:
        """逐题读数腿（REQ-161 W5）也是一条模型调用，一调用一行；引擎没有这条腿回 `None`。"""
        classifier = getattr(self._engine, "exercise_classify", None)
        if not callable(classifier):
            return None
        return self._record(
            "exercise_classify",
            lambda: classifier(material),
            {
                "title": material.title,
                "item_count": len(material.items),
                "target_count": len(material.targets),
            },
        )

    def exercise_verify(self, material: ExerciseVerifyInput) -> ExerciseVerifyReply | None:
        """配对校验腿（REQ-170 第三步）也是一条模型调用，一调用一行；引擎没有这条腿回 `None`。"""
        verifier = getattr(self._engine, "exercise_verify", None)
        if not callable(verifier):
            return None
        return self._record(
            "exercise_verify",
            lambda: verifier(material),
            {"title": material.title, "item_count": len(material.items)},
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
    reference_fetcher: ReferenceFetcher | None = None,
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
    book = BookRepository(session).get(acceptance.book_id)
    # 配套目标随目录腿落进来源书清单：习题腿不再要求每次点选都重带目标（用户 2026-11-16 的链路＝
    # 目录先复用教材章段，随后按同一目标提取题与解）。修补批次沿用受理面已定的归属，不在这里改。
    if (
        acceptance.stage == "exercise"
        and not acceptance.exercise_retry_node_ids
        and acceptance.target_book_id is None
        and book is not None
        and book.doc_kind == DOC_KIND_EXERCISE
    ):
        remembered = str(read_exercise_meta(data_root, book).get("target_book_id") or "")
        if remembered:
            acceptance = replace(acceptance, target_book_id=remembered)
    exercise_outline_match = ExerciseOutlineMatch()
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
        outline_sink: _KpOutlineSink | None = None
        exercise_outline: OutlineBuild | None = None
        if book is not None and book.doc_kind == DOC_KIND_EXERCISE:
            # 习题集的目录腿不判页、不开模型（PLAN-036 W9）：章行取目录、无目录由题号派生，
            # 答案段另补一枚附件行。这一条腿没有逐页早落面，`outline_sink` 留空即「只有定稿」。
            # 匹配在成行之前做（S1／S2）：同套只剩一本教材时确定性命中，章名直接复用教材章名；
            # 多本候选才发一次模型匹配。选题读取面与成行腿共用同一份章读数。
            hints = exercise_outline_hints(evidence=evidence)
            if hints:
                exercise_outline_match = match_exercise_outline(
                    session,
                    engine,
                    book=book,
                    chapters=tuple(hint.name for hint in hints),
                    evidence=evidence,
                )
                if exercise_outline_match.state == "red":
                    # 红灯＝既非配套型也配不出题目＋解答（`KA-71`）：零目录行，且**不得**回退
                    # 教材目录腿——那会让一本不可用的习题集借教材骨架长出一套假目录。
                    exercise_outline = OutlineBuild(
                        nodes=(), dropped_depth=0, dropped_malformed=0
                    )
                else:
                    chapter_names = (
                        _target_chapter_names(session, exercise_outline_match.target_book_id)
                        if exercise_outline_match.target_book_id
                        else {}
                    )
                    exercise_outline = build_exercise_outline(
                        book=book,
                        evidence=evidence,
                        gen_batch=acceptance.gen_batch,
                        hints=hints,
                        chapter_names=chapter_names,
                    )
        if exercise_outline is not None:
            outline = exercise_outline
        else:
            outline_sink = _KpOutlineSink(
                repo=repo,
                session=session,
                book_id=acceptance.book_id,
                gen_batch=acceptance.gen_batch,
            )
            outline = build_outline(acceptance, evidence, engine, sink=outline_sink)
        # 定稿去重可能折掉逐页早落的行（D-11／D-12）：定稿不再认它，就必须在翻面之前删掉——
        # 批次翻面是无条件 draft→candidate，留下一枚就是屏上又出现的那枚重影。
        kept_ids = {node.row.node_id for node in outline.nodes}
        landed_ids = outline_sink.landed_ids if outline_sink is not None else set()
        for stale_id in sorted(landed_ids - kept_ids):
            repo.delete_node(stale_id)
        for node in outline.nodes:
            if node.row.node_id in landed_ids:
                continue
            repo.create(node.row)
        session.commit()
        outline_nodes = list(outline.nodes)
        section_nodes = outline_nodes

    # P0（`KA-63`）：内容批次点腿之前先按规则审一次种类，把段内大块习题拆成并列行。零模型调用、
    # 零新阶段；写出的行仍是 `draft`，随本批收尾一起翻面。
    kind_review_build = (
        kind_review_pass(session, acceptance, evidence, section_nodes)
        if acceptance.stage == "content"
        else KindReviewBuild()
    )
    # 本批拆出的习题行并入点腿的切段输入（`KA-68`）：它们的锚块就是点材料该停的那条边界，
    # 不并进来这一批仍会把习题段读进总结材料（行是 `draft`，再读一次库也拿不到）。
    if kind_review_build.nodes:
        section_nodes = [*section_nodes, *kind_review_build.nodes]

    # 习题腿按书种类分两条（`KA-53`）：教材侧规则分题（题行父＝习题段行、零模型调用），习题集侧
    # 规则定界＋模型读内容（题行父＝题目所在的章／节行、逐题写题包）。两条腿的行集互不相交。
    if (
        acceptance.stage == "exercise"
        and book is not None
        and book.doc_kind == DOC_KIND_EXERCISE
    ):
        # 有目标＝配套提取（题面取教材、解答取本书、行落本书）；缺省＝本书自成一册；
        # 带选中题行的是修补批次（W17）——只改写那几枚，不走首轮的成行面。
        if acceptance.exercise_retry_node_ids:
            exercise_book = retry_exercise_book_items(
                session, engine, acceptance, evidence, outline_nodes, data_root=data_root
            )
        else:
            exercise_book = (
                extract_companion_exercise_items
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
            skip_node_ids=kind_review_build.flag_node_ids,
        )
        if acceptance.stage in ("full", "content")
        else PointBuild(0, 0, 0, 0)
    )
    chapters = (
        extract_chapters(
            session,
            engine,
            acceptance,
            evidence,
            section_nodes,
            data_root=data_root,
            reference_fetcher=reference_fetcher,
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
        doc_kind=book.doc_kind if book is not None else None,
    )
    # 种类审校的拆分行由证据决定（`KA-63`）：本批已按当前标签重算过一遍，别的批次在区间内留下的
    # 旧拆分行就是过期读数（`KA-36` 改判）。新行此刻已翻成 `candidate`，按 `gen_batch` 排除在外。
    if acceptance.stage == "content":
        superseded = repo.reject_superseded_candidates(
            acceptance.book_id,
            acceptance.gen_batch,
            page_start=acceptance.page_start,
            page_end=acceptance.page_end,
            node_kind=NODE_KIND_OUTLINE,
            model_ref=EXERCISE_SPLIT_MODEL_REF,
        )
        closing = replace(closing, superseded_rows=closing.superseded_rows + superseded)
    # 习题集题腿的旧读数不在收尾的 `KA-36` 作废面里：收尾那一档按教材侧题行的产出者标识收窄，
    # 本腿的题行是模型腿（`llm:exercise`）。补在同一次提交里、翻面之后——作废的是**别的批次**的
    # 未确认候选行，本批刚翻成 `candidate` 的行按 `gen_batch` 排除在外。配套提取把行落在本书自己
    # 的章下，本书上一轮的未确认题行同样是被取代的旧读数；修补批次一律不作废（它只改写选中的几行）。
    if (
        not acceptance.exercise_retry_node_ids
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
    # 题包索引（`KA-53`／W16）：收尾后整份重写章级索引与来源书清单。题行与索引都在本书的
    # `kp/exercises/`；来源书清单恒写本书（它记的是「这本书的题去了哪里」）。派生视图写失败
    # 只告警，不把一批已经落好的行改判为失败。
    if (
        acceptance.stage == "outline"
        and book is not None
        and book.doc_kind == DOC_KIND_EXERCISE
    ):
        # 目录腿的匹配读数在收尾后落清单（REQ-161 W4／REQ-178 R2）：命中定配套目标；未命中／
        # 调用失败／没有候选都退回独立目录；红灯单独记 `kind="red"`（内部读数，读面据此锁内容钮）。
        # 未匹配解答沿用上一轮的披露，目录批不动题级读数。
        previous = read_exercise_meta(data_root, book)
        red = exercise_outline_match.state == "red"
        write_exercise_meta(
            session,
            data_root=data_root,
            book_id=acceptance.book_id,
            kind=(
                "red"
                if red
                else "companion"
                if exercise_outline_match.target_book_id
                else "standalone"
            ),
            target_book_id=exercise_outline_match.target_book_id,
            gen_batch=acceptance.gen_batch,
            unmatched=[
                item for item in (previous.get("unmatched") or []) if isinstance(item, dict)
            ],
            match={
                "state": exercise_outline_match.state,
                "candidates": list(exercise_outline_match.candidates),
                "chapters": [
                    {"source": source, "target": target}
                    for source, target in exercise_outline_match.chapters
                ],
            },
        )
    if (
        acceptance.stage == "exercise"
        and book is not None
        and book.doc_kind == DOC_KIND_EXERCISE
    ):
        write_exercise_indexes(
            session,
            data_root=data_root,
            book_id=acceptance.book_id,
            gen_batch=acceptance.gen_batch,
        )
        previous_match = read_exercise_meta(data_root, book).get("match")
        write_exercise_meta(
            session,
            data_root=data_root,
            book_id=acceptance.book_id,
            kind="companion" if acceptance.target_book_id else "standalone",
            target_book_id=acceptance.target_book_id,
            gen_batch=acceptance.gen_batch,
            unmatched=exercise_book.unmatched_solution_details,
            match=previous_match if isinstance(previous_match, dict) else None,
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
        unlocated_outline_entries=(
            *outline.unlocated,
            *points.skipped_unlocated,
            *exercise.skipped_unlocated,
        ),
        dropped_toc_pages=outline.dropped_toc_pages,
        outline_toc_pages=outline.toc_pages,
        outline_fallback_pages=outline.fallback_pages,
        outline_judged_pages=outline.judged_pages,
        verified_outline_entries=outline.verified,
        reference_rows=outline.reference_rows,
        duplicate_outline_rows=outline.dropped_duplicates,
        folded_sub_items=outline.folded_sub_items,
        type_review_rows=kind_review_build.rows,
        type_review_flags=kind_review_build.flags,
        split_exercise_rows=kind_review_build.splits,
        point_windows=points.point_windows,
        point_merges=points.point_merges,
        material_ranges_rows=points.material_ranges_rows,
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
            "outline_judged_pages": list(outcome.outline_judged_pages),
            "verified_outline_entries": list(outcome.verified_outline_entries),
            "reference_rows": outcome.reference_rows,
            "duplicate_outline_rows": outcome.duplicate_outline_rows,
            "folded_sub_items": outcome.folded_sub_items,
            "type_review_rows": outcome.type_review_rows,
            "type_review_flags": list(outcome.type_review_flags),
            "split_exercise_rows": outcome.split_exercise_rows,
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
            "point_windows": outcome.point_windows,
            "point_merges": outcome.point_merges,
            "material_ranges_rows": outcome.material_ranges_rows,
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


def _resolve_dropped(mapping: Mapping[str, str], node_id: str | None) -> str | None:
    """沿「被折叠 id → 留下 id」的映射取终值；映射成环（脏数据）时立即停，不把读面拖进死循环。"""
    seen: set[str] = set()
    current = node_id
    while current is not None and current in mapping and current not in seen:
        seen.add(current)
        current = mapping[current]
    return current


def _collapse_outline_pass(
    outlines: Sequence[AfKp], parent_remap: Mapping[str, str]
) -> tuple[list[AfKp], dict[str, str]]:
    """一趟折叠：父位先按上一趟的映射归位再算键；同键时未作废者胜，两者同态才留书序第一枚。"""
    seen: dict[tuple[str, str], AfKp] = {}
    slots: dict[tuple[str, str], int] = {}
    kept: list[AfKp] = []
    dropped: dict[str, str] = {}
    for row in outlines:
        meta = row.meta or {}
        role = str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE)
        kind = str(meta.get("toc_kind") or "")
        norm_key = row.norm_key or norm_key_for(row.name or "")[:200]
        parent = _resolve_dropped(parent_remap, row.parent_id)
        key = _outline_row_key(parent, norm_key, role, kind)
        winner = seen.get(key)
        if winner is None:
            seen[key] = row
            slots[key] = len(kept)
            kept.append(row)
            continue
        if row.gen_status != "rejected" and winner.gen_status == "rejected":
            dropped[winner.node_id] = row.node_id
            seen[key] = row
            kept[slots[key]] = row
        else:
            dropped[row.node_id] = winner.node_id
    # 留下的行也要落在留下的父上：父位被上一趟折掉时，本趟按映射归位（只改读面对象，不写库）。
    for row in kept:
        parent = _resolve_dropped(parent_remap, row.parent_id)
        if parent != row.parent_id:
            set_committed_value(row, "parent_id", parent)
    return kept, dropped


def collapse_outline_duplicates(rows: Sequence[AfKp]) -> list[AfKp]:
    """读面折叠存量重复目录行（D-13）：同键留一枚，**作废行不压过未作废行**，子行父位跟着留下的那枚。

    入参须已按书序排好（`order_for_export`）——两者同态时「第一枚」就是这个次序里先出的那枚。
    点行不参与折叠：同名知识点是两处正文的各自读数，不是同一行的重影；但它的父位同样要改指
    留下的那枚目录行，否则内容批次的点在屏上悬空。存量重影来自成行去重落地前的批次；一本书里
    同一章／节常常留有「rejected 旧读数 ＋ candidate 新读数」两枚，旧读数还排在书序前面。只认
    「第一枚」会把屏上的章换成作废行，而孩子仍指向被折掉的候选行——整棵子树在屏上消失（真机
    b18 第1–4章）。折叠按层迭代到映射稳定：子行的键含父位，父位归一之后同名的两枚节才折得掉。
    全过程零数据修补：父位改名只落在读面对象上（`set_committed_value()`，不留 ORM 脏写）。
    """
    outlines = [row for row in rows if row.node_kind == NODE_KIND_OUTLINE]
    kept, dropped = _collapse_outline_pass(outlines, {})
    for _ in range(OUTLINE_MAX_DEPTH + 1):
        remapped, folded = _collapse_outline_pass(outlines, dropped)
        kept = remapped
        if folded == dropped:
            break
        dropped = folded
    if dropped:
        for row in rows:
            if row.node_kind == NODE_KIND_OUTLINE:
                continue
            parent = _resolve_dropped(dropped, row.parent_id)
            if parent != row.parent_id:
                set_committed_value(row, "parent_id", parent)
    kept_ids = {row.node_id for row in kept}
    return [
        row for row in rows if row.node_kind != NODE_KIND_OUTLINE or row.node_id in kept_ids
    ]


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
