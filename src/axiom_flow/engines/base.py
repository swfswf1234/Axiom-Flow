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
class SectionInput:
    """知识点阶段的一节输入：本批目录给出的节（`parent_id` 为空＝未覆盖区间）与其页区间正文。"""

    parent_id: str | None
    title: str
    page_start: int
    page_end: int
    pages: tuple[PageBody, ...]


@dataclass(frozen=True, slots=True)
class SectionPoints:
    """一节回复的两半（`KA-43`）：知识点草稿数组 ＋ 该节的节级总结。

    总结搭着节调用回来，而不是另开一次问：另开＝每节再付一次百秒级调用，而这一节的正文此刻已经
    在手上。`summary` 缺省空串（模型没给就不写库），草稿数组的形状判据一字不动。
    """

    drafts: list[dict]
    summary: str = ""


@dataclass(frozen=True, slots=True)
class ChapterInput:
    """章腿的一枚输入（`KA-45`）：章首正文＋其下各知识点的总结清单。

    `intro_pages`＝章起点页起到首个子行起点页之前那一段（与首个子行同页时为空）；`title_pages`
    只在 `intro_pages` 为空时给（章标题页作兜底），两块不重复送同一页正文。
    `point_summaries` 是「知识点名 → 它的节级总结」按屏上序，空总结的行不进清单。
    """

    parent_id: str
    title: str
    page_start: int
    page_end: int
    intro_pages: tuple[PageBody, ...]
    title_pages: tuple[PageBody, ...]
    point_summaries: tuple[tuple[str, str], ...]


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
class OutlineEntry:
    """整理腿的一行条目：层级、行末的印刷页码（无名行为 `None`）、条目名（`KA-37`）。

    层级只认 1／2，页码只认阿拉伯数字——畸形容错在适配器里发生，编排层只接已定形的行。
    """

    level: int
    printed_page: int | None
    name: str


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
