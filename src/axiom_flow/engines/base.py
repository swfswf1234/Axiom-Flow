"""引擎适配器契约：统一输入（PDF/页图字节）与输出（页级原始块 + Markdown + 页尺寸），
以及梳理引擎的输入形状（知识点按节读正文；目录按页判页与整理，`KA-35`／`KA-37`／`KA-38`）。

设计事实源：`docs/design/model-integration.md`；梳理侧见 `docs/design/knowledge-analysis.md`
（`KA-5` 两阶段与 `KA-22` 走同一适配层）。适配器不暴露模型调参（归 QED-Engine）；
模型差异在适配层消化，`normalize/` 负责归一为统一 blocks。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

BboxSpace = Literal["pdf_points", "normalized_1000", "pixels"]
"""原始块 `bbox` 的坐标空间：PDF 点 / 逐轴 0–1000 归一化 / 已是页图像素。"""


class EngineError(RuntimeError):
    """引擎调用失败（服务不可达/超时/解析失败）——编排层记为单页失败或任务 `failed`。

    模型服务不可用在 API 层另有两道映射：提交期探活失败 → 503（不落任务行），
    受理后进到执行期 → job `failed`（`orchestrator/pipeline.py`）。
    """


@dataclass(frozen=True, slots=True)
class PageResult:
    """引擎页级原始输出（归一化前）。

    `blocks` 为引擎原始结构化条目（如 MinerU content_list 条目）；`bbox_space` 声明其坐标空间，
    由 `normalize/` 按该空间换算到页图像素；`page_size` 为引擎报告的 `pdf_info.page_size`，
    **仅 `pdf_points` 空间可作缩放基准**（MinerU 在 PNG 输入下回的是按 200 DPI 折算的标称点，
    与归一化 bbox 组不出自洽映射，见 DEFECT-004）。
    """

    page: int
    blocks: list[dict] = field(default_factory=list)
    markdown: str = ""
    page_size: tuple[float, float] | None = None
    bbox_space: BboxSpace = "pdf_points"


@dataclass(frozen=True, slots=True)
class DocumentResult:
    """整本文档解析结果：页级结果 + 文档内嵌图片（相对路径 → 字节）。"""

    pages: list[PageResult] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelLoad:
    """模型服务当前负载（编排层单一占用闸门的判据）。

    GPU 资源不足以并行解析，服务侧的并发许可（MinerU `max_concurrent_requests: 3`）**不能**
    替代调用方自我排他。
    """

    queued: int
    processing: int

    @property
    def busy(self) -> bool:
        """有排队或在飞任务即视为占用。"""
        return (self.queued + self.processing) > 0


@runtime_checkable
class ParseEngine(Protocol):
    """解析引擎协议：健康探活、负载探测、全本解析（默认，可按 1 基含两端窗口分块）、单页解析。"""

    name: str

    def health(self) -> bool: ...

    def model_load(self) -> ModelLoad | None:
        """当前负载；引擎不上报、服务未答或载荷不合预期时返回 `None`（无法判定，不是占用）。"""
        ...

    def parse_document(
        self,
        pdf_bytes: bytes,
        filename: str,
        *,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> DocumentResult:
        """一次提交；`start_page`/`end_page` 为 1 基**含两端**的书页号，缺省即整本。

        返回页号恒为**全书绝对页号**（引擎的窗口内相对索引由适配层换算）。
        """
        ...

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult: ...


@dataclass(frozen=True, slots=True)
class HeadingEvidence:
    """一页里的一个标题读数：层级来自 `Block.level`，`block_index` 来自该版 `blocks.json` 的块序。"""

    level: int
    text: str
    block_index: int


@dataclass(frozen=True, slots=True)
class PageBody:
    """知识点阶段的一页正文：来自 `pages/p<NNNN>.md`（产物通路，不读分块文件）。"""

    page_no: int
    markdown: str


@dataclass(frozen=True, slots=True)
class SectionBlock:
    """内容批次的一枚取材块（`KA-64`）：页号＋块序＋渲染后的原文。

    提示词里每块带「p{页}·b{序}」前缀，素材本身逐字来自解析产物（`KA-66` 的格式契约）。
    """

    page: int
    block_index: int
    text: str


@dataclass(frozen=True, slots=True)
class SectionMember:
    """合批调用里的一枚知识点（`KA-64`）：机器身份＋标题＋该点送出的块。

    `node_id` 是回复逐点对账的唯一键：按标题对账会在同名小节上错配。
    """

    node_id: str
    title: str
    blocks: tuple[SectionBlock, ...] = ()


@dataclass(frozen=True, slots=True)
class SectionInput:
    """知识点阶段的一节输入：本批目录给出的节（`parent_id` 为空＝未覆盖区间）与其页区间正文。

    `blocks`／`members` 是内容批次的块取材面（`KA-64`）：给了 `members` 就是合批调用（多于一项时
    模型按点逐条作答），只给 `blocks` 是单节单窗口。两者都空时沿用按页正文的老形状——
    `full` 通路的输入与调用数因此逐字不变。
    """

    parent_id: str | None
    title: str
    page_start: int
    page_end: int
    pages: tuple[PageBody, ...]
    blocks: tuple[SectionBlock, ...] = ()
    members: tuple[SectionMember, ...] = ()


@dataclass(frozen=True, slots=True)
class MemberPoints:
    """合批回复里的一枚知识点（`KA-64`）：节点身份＋总结与草稿；缺席＝该点失败。"""

    node_id: str
    drafts: list[dict]
    summary: str = ""


@dataclass(frozen=True, slots=True)
class SectionPoints:
    """一节回复的两半（`KA-43`）：知识点草稿数组 ＋ 该节的节级总结。

    总结搭着节调用回来，而不是另开一次问：另开＝每节再付一次百秒级调用，而这一节的正文此刻已经
    在手上。`summary` 缺省空串（模型没给就不写库），草稿数组的形状判据一字不动。
    `members` 只在合批调用（`KA-64`）里非空：每点一条，缺项由编排层记失败节。
    """

    drafts: list[dict]
    summary: str = ""
    members: tuple[MemberPoints, ...] = ()


@dataclass(frozen=True, slots=True)
class ReferenceNote:
    """一条章级检索资料（PLAN-033）：来源名、条目名、清洗后的摘要正文。

    它是**校准材料**而不是事实源：术语与规范定义可以参照，教材正文的结论优先；
    取不到时章材料里这一块为空，提示词不因它缺位而改变其余口径。
    """

    source: str
    title: str
    text: str


CHAPTER_TEXT_PROMPT_VERSION = "kp-merge-chapter-v1"


@dataclass(frozen=True, slots=True)
class ChapterTextPoint:
    """课程级证据中的一枚子点。"""

    name: str
    summary: str = ""


@dataclass(frozen=True, slots=True)
class ChapterTextSource:
    """课程级证据中的一本教材同章材料。"""

    book_id: str
    title: str
    chapter_name: str
    summary: str = ""
    points: tuple[ChapterTextPoint, ...] = ()


@dataclass(frozen=True, slots=True)
class ChapterTextInput:
    """无页块溯源的课程级章腿输入。"""

    course_id: str
    chapter_kp_id: str
    chapter_name: str
    chapter_summary: str = ""
    chapter_narrative: str = ""
    sources: tuple[ChapterTextSource, ...] = ()
    reference_notes: tuple[ReferenceNote, ...] = ()
    retry_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ChapterInput:
    """章腿的一枚输入（`KA-65`＋PLAN-033）：章前正文块＋子点总结清单＋检索资料。

    `blocks`＝章锚页到首个点子行锚页之前的正文块（同页时只取首点标题块之前的块），
    子点正文一块都不在里面；`point_summaries` 是「知识点名 → 它的节级总结」按屏上序，
    空总结的行不进清单。`reference_notes` 是章标题与子点名称确定性检索回来的一组摘要，
    只用于校准术语与定义；`retry_issues` 是同一模型重试时附带的修改要求（首次生成恒为空）。
    """

    parent_id: str
    title: str
    page_start: int
    page_end: int
    blocks: tuple[SectionBlock, ...]
    point_summaries: tuple[tuple[str, str], ...]
    reference_notes: tuple[ReferenceNote, ...] = ()
    retry_issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RevisionContext:
    """单点重写的上下文（`KA-48`）：人给的修改建议 ＋ 这一行当前的成品。

    只属于**一次同步生成调用**，不随批次走：`note` 是屏上确认框里那句话（可空＝不改写法），
    `previous` 是引擎要参照的底稿正文（章导读由盘上那一篇现读），`previous_kind` 只决定提示词里
    这枚底稿叫什么（「现有总结」／「现有导读」）。事实与结论始终以正文材料为准。
    """

    note: str = ""
    previous: str = ""
    previous_kind: str = "总结"


@dataclass(frozen=True, slots=True)
class ChapterReply:
    """一章回复的两段（`KA-45`）：章级总结落 `summary`，章级导读走 `narrative` 写腿。

    两段各自缺省空串——模型只给了一段时写那一段，另一段按「没给」处理而不判整章失败。
    """

    summary: str = ""
    narrative: str = ""


@dataclass(frozen=True, slots=True)
class PageSample:
    """判页腿的一页样本：页号＋截断后的页文本（带块类型标注，截断口径由取材侧给）。

    判页要的只是「这一页长得像不像目录」，整页文本对吞吐本机是奢侈品（`KA-37` 的输入封顶）。
    """

    page_no: int
    text: str


@dataclass(frozen=True, slots=True)
class ReferencePage:
    """判页腿认出的一枚正文前／后的附件页：名字与类别都由模型给（`KA-39`）。"""

    page_no: int
    name: str
    kind: str


@dataclass(frozen=True, slots=True)
class TocPagePick:
    """判页的一次读数：目录页号集 ＋ 附件页集。

    两值全空＝这本书里没有可认的目录页，编排层据此整本回落到确定性条目腿（`KA-37`）。
    """

    toc_pages: tuple[int, ...] = ()
    reference_pages: tuple[ReferencePage, ...] = ()


@dataclass(frozen=True, slots=True)
class TocOutlineRow:
    """整体核对腿的一行（`KA-57`）：整段抄录里的下标、层级、条目名。

    页码不进这一行：整段抄录已经把页码从原文里取出来了，核对腿只许订正名字，不许动页码。
    `index` 是它在整段抄录里的下标，回写时按它认行——名字可以改，行不许增删。
    """

    index: int
    level: int
    name: str


@dataclass(frozen=True, slots=True)
class OutlineEntry:
    """整理腿的一行条目：层级、行末的印刷页码（无名行为 `None`）、条目名（`KA-37`）。

    层级只认 1／2，页码只认阿拉伯数字——畸形容错在适配器里发生，编排层只接已定形的行。
    """

    level: int
    printed_page: int | None
    name: str


@dataclass(frozen=True, slots=True)
class TocPageReading:
    """整页判页的一次读数（P-2／P-3）：这一页是不是目录页；是目录页时同一次读数带回本页条目。

    `is_toc=False` 是目录区间的右边界：编排层据此收住连续整理，不再向后的页发判页调用。
    与 `TocPagePick` 的分工——后者一次问一组样本页、只报页号；这一条一次只问一页、判定与条目
    出自同一次读数，整页不截断。

    这是**可选腿**（同 `toc_outline`）：引擎实现 `toc_page(page_no, text)` 才走逐页判定，否则
    编排层用属性探测退回 `toc_pages`＋`toc_entries` 的旧读数。
    """

    is_toc: bool = False
    entries: tuple[OutlineEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class TocVerifyItem:
    """核对腿的一条坏行：条目名、它的印刷页码、以及候选页里的行（`(页号, 行文本)`）。"""

    name: str
    printed_page: int | None
    candidates: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class TocVerifyAnswer:
    """核对腿对一条坏行的判定：页号，或「正文里没有这一行」（`page_no=None`，`KA-38`）。"""

    name: str
    page_no: int | None


@dataclass(frozen=True, slots=True)
class ExerciseItemInput:
    """题腿的一道题：规则切出的边界（题号、页区间）与该题正文切片（`KA-53`）。

    边界由 `normalize.exercise` 给，模型只读题面与解答的**内容**——分题权不在模型手里。
    """

    item_no: str
    label: str
    page_start: int
    page_end: int
    text: str


@dataclass(frozen=True, slots=True)
class ExerciseInput:
    """习题集一节的材料：章节标题、页区间与本节切出的题（`KA-53`）。"""

    title: str
    page_start: int
    page_end: int
    items: tuple[ExerciseItemInput, ...]


@dataclass(frozen=True, slots=True)
class ExerciseDraft:
    """模型读出的一道题：题面与解答两段（都可能为空串）。

    空 `problem`＝这一题的题面没能分出（读面据此标 `source_missing`）；空 `solution`＝只读出题面
    （`problem_only`）。未配到的解答另走 `unmatched_solutions` 披露，不在这里造一行假解答。
    """

    item_no: str
    problem: str = ""
    solution: str = ""


@dataclass(frozen=True, slots=True)
class ExerciseReply:
    """题腿的一节回复：按出题序的草稿数组。缺项由编排层按切分边界补齐并计数。"""

    items: tuple[ExerciseDraft, ...] = ()


@dataclass(frozen=True, slots=True)
class ExerciseMatchCandidate:
    """一枚可匹配的教材（REQ-161 W4）：同课程整本绿的书与其章名清单。"""

    book_id: str
    title: str
    chapters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExerciseMatchInput:
    """习题集目录匹配的一次材料：本书顶层章名 ＋ 候选教材及各自章名。

    匹配只判「这本习题集对的是哪本教材、两边的章怎么对应」；题级对应是另一条腿的事。
    """

    title: str
    chapters: tuple[str, ...] = ()
    candidates: tuple[ExerciseMatchCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ExerciseMatchChoice:
    """匹配腿的读数：选中的教材（没有对得上的即 `None`）与章对应（本书章名 → 教材章名）。"""

    book_id: str | None = None
    chapters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExerciseClassifyItem:
    """一枚习题的机器读数（REQ-161 W5）：对应的教材题目（没有即空串）与题型。"""

    item_no: str
    target: str = ""
    kind: str = ""


@dataclass(frozen=True, slots=True)
class ExerciseClassifyInput:
    """逐题读数的一次材料：本节各题（题号＋正文）与教材同一章下的候选题。

    没有目标教材（独立模式）时 `targets` 为空——模型只判题型，「对应教材题」按无对应计。
    正文由编排层截断到提示词预算，读数本身只是判据，不复制原文。
    """

    title: str
    items: tuple[tuple[str, str], ...] = ()
    targets: tuple[tuple[str, str], ...] = ()
    target_title: str = ""


@dataclass(frozen=True, slots=True)
class ExerciseClassifyReply:
    """读数腿的一节回复：逐题对象；不在本节题号里的条目由编排层丢弃。"""

    items: tuple[ExerciseClassifyItem, ...] = ()


@dataclass(frozen=True, slots=True)
class ExerciseVerifyItem:
    """待检的一题（REQ-170 第三步）：题号、题面与解答两段——就是落行的那两段正文。

    正文由编排层截断到提示词预算：模型判的是「切分与对应」，不是逐字校对原文。
    """

    item_no: str
    problem: str = ""
    solution: str = ""


@dataclass(frozen=True, slots=True)
class ExerciseVerifyInput:
    """按章批检的一次材料：章标题与该章的题对。题号只用章内的局部键（比对键）。"""

    title: str
    items: tuple[ExerciseVerifyItem, ...] = ()


@dataclass(frozen=True, slots=True)
class ExerciseVerifyReading:
    """一题的对读四问：切分、同一题、数学结论、解答完整性（合判为一枚结论＋一句中文理由）。

    `verdict` 闭集 `ok`／`suspect`／`reject`：四问都过才是 `ok`；拿不准按 `suspect` 交人看、
    明确错了给 `reject`——这枚读数只用于披露与人工勾选，不否决成行。
    """

    item_no: str
    verdict: str = "ok"
    note: str = ""


@dataclass(frozen=True, slots=True)
class ExerciseVerifyReply:
    """校验腿的一章回复：逐题结论；不在本轮题号里的条目由编排层丢弃。"""

    items: tuple[ExerciseVerifyReading, ...] = ()


@runtime_checkable
class KnowledgeEngine(Protocol):
    """梳理引擎协议（文本类槽位）：目录三条腿 ＋ 知识点逐节调用。

    目录阶段以模型为正腿（`KA-35`／`KA-37`／`KA-38`）：判页挑出目录页与附件页、逐页把条目整理成形、
    印刷页码查不中的坏行交一次核对。逐页而不是一次整本：一页一次＝单页失败域，坏了的那页回落
    确定性条目腿，其它页照出（与知识点阶段的按节失败同形）。

    逐节而不是一次全本：单节失败域＝该节不落行、批内继续（`knowledge-analysis.md`「失败与质量
    语义」），一次调用坏了就整批坏。返回草稿的形状由编排层校验（`name`／`anchors`／`links`
    与闭集），不合形状者不写、计数披露。
    """

    name: str

    def health(self) -> bool: ...

    def toc_pages(self, samples: Sequence[PageSample]) -> TocPagePick:
        """判页：一次调用，输入＝样本页集，输出＝目录页号集与附件页集。"""
        ...


    def toc_entries(self, page_no: int, text: str) -> list[OutlineEntry]:
        """整理：一页目录原文 → 条目行（层级＋印刷页码＋名字）。"""
        ...

    def toc_verify(self, items: Sequence[TocVerifyItem]) -> list[TocVerifyAnswer]:
        """核对：坏行攒一批一次问，每条回一个页号或「正文里没有这一行」。"""
        ...

    def points(self, section: SectionInput) -> SectionPoints:
        """一节的两半回复：草稿 `[{"name", "type", "role"?, "summary"?, "anchors", "links"?, "parent_ref"?}]`
        与该节的节级总结（`KA-43`）。"""
        ...

    def chapter(self, material: ChapterInput) -> ChapterReply:
        """一章的两段回复（`KA-45`）：章级总结与其导读。材料依赖其下各节的总结，故排在节腿之后。"""
        ...

    def exercise(self, section: ExerciseInput) -> ExerciseReply:
        """习题集一节：按规则给出的题边界读题面与解答（`KA-53`）。

        模型不得增删题号，也不得自行分题——只回给定的那些 `item_no`；某一题读不出题面或解答时
        留空串，由编排层按三态披露（`source_missing`／`problem_only`）。
        """

    def exercise_match(self, request: ExerciseMatchInput) -> ExerciseMatchChoice:
        """习题集目录匹配（REQ-161 W4，可选腿）：本书章名 vs 候选教材章名 → 选中书与章对应。

        与 `toc_outline`／`toc_page` 同一条探测式：实现这条腿的引擎才发匹配调用，其余引擎由
        编排层按「没有可匹配的教材」退回独立目录。回复里的书必须来自候选集，章对只在两边
        都认得出时采信——猜错的对应比没有对应更难查。
        """

    def exercise_classify(self, request: ExerciseClassifyInput) -> ExerciseClassifyReply:
        """习题解析的逐题机器读数（REQ-161 W5，可选腿）：是否对应教材题目＋题型。

        只判读、不参与切分与落行：切分与题号对应仍由规则腿给（`KA-53`／`KA-56`）。实现这条
        腿的引擎才发调用；调用失败在题包来源段与题行 `meta` 如实披露，不造读数。
        """
        ...

    def exercise_verify(self, request: ExerciseVerifyInput) -> ExerciseVerifyReply:
        """习题配套的对读校验（REQ-170 第三步，可选腿）：逐题判切分、同一题、数学结论与完整性。

        输入是**已落行**的题对（题面取教材原文、解答取本书切分），一章按预算分轮问；英文题面与
        解答在引擎内先译成中文再判，note 用中文写。读数只用于披露与人工勾选：`suspect`／
        `reject` 不否决成行、不改配对；调用失败只把该轮题行记 `verify_state='failed'`。实现这条
        腿的引擎才发调用，其余引擎的题行不长出 `verify_*` 键。
        """
        ...
