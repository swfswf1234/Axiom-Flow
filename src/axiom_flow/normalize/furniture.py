"""跨页重复线的页域判据：同一文本在多数页的同一纵向位置出现 ⇒ 那是一条家具线（页眉／页脚）。

读时派生、零写库、零重解析——输入是整本**生效版本**的页块序列，输出是「哪几块落在这条线上」的读数。
判据正文见 [parsing-management.md](../../docs/design/parsing-management.md) 的 `PM-19`：AF 只供给
第一条条件（跨页重复线），页边带几何与短文本两条合取条件属读取方的渲染判据——在这里合取就把
别人的阈值冻结进解析侧。本模块也不改任何块的类型。

本模块给两个消费面各出投影：梳理取材面两件纯函数（`furniture_texts`＝逐页家具文本，
`strip_furniture`＝按整行删掉送进模型的副本里的家具行），页数据读投影一件（`furniture_blocks`＝
逐页家具块下标，供屏侧逐块隐藏）。三者共用同一判据；产物文件一律不经它们——剔除与标记都只发生在
读取投影。
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from axiom_flow.schemas import BlocksPage, BlockType

#: 家具三值＝「这一行不是内容」。与页级 Markdown 组装跳过的是同一组类型（`blocks._SKIP_IN_MARKDOWN`
#: 即本常量），两处必须同源：一处判三枚、另一处判两枚，就会留下「产物里没有、改判后要剔」的缝隙。
FURNITURE_TYPES: frozenset[BlockType] = frozenset(
    {BlockType.HEADER, BlockType.FOOTER, BlockType.PAGE_NUMBER}
)

#: 默认门槛＝至少 3 页 **且** 覆盖整本的半数页。少于 3 页的重复是巧合；覆盖不到半数页的重复
#: 多半是章名眉那一类（每章换一次文本），它该由人工改判或渲染腿的另两条件来处理，不算全书线。
MIN_PAGES = 3
MIN_PAGE_RATIO = 0.5
#: 纵向位置容差，单位＝页图像素（`BlocksPage.image_size` 是 bbox 基准）。同一条线在各页的位置差
#: 只来自排版抖动，32px 够宽容差又远小于正文行距的一半，不会把正文里两处同文本并成一条线。
Y_TOLERANCE = 32


@dataclass(frozen=True, slots=True)
class FurnitureHint:
    """一条候选家具线：归一后的文本、纵向带心、参与它的 `(页号, 块下标)`。

    `members` 的坐标就是块编辑与锚点用的那一套（页号＋版本内块下标），消费方不需要再折算。
    """

    text: str
    y_center: int
    members: tuple[tuple[int, int], ...]


def _fold(text: str) -> str:
    """归一口径＝NFKC（全角转半角）＋空白折叠＋小写。

    与 `orchestrator/knowledge.norm_key_for` 是同一条算法，但两处不共用一个函数：后处理层反向
    依赖编排层会破分层，而这一步本身只有三行、无配置可共享。
    """
    folded = unicodedata.normalize("NFKC", text)
    return " ".join(folded.split()).casefold()


def furniture_hints(
    pages: Sequence[BlocksPage],
    *,
    min_pages: int = MIN_PAGES,
    page_ratio: float = MIN_PAGE_RATIO,
    y_tolerance: int = Y_TOLERANCE,
) -> tuple[FurnitureHint, ...]:
    """整本页块序列 → 候选家具线，按 `(纵向带心, 文本)` 排序（读数确定，守护可对偶）。

    没有正文的块（公式／表／图）不参与：它们的 `text` 为空，「同一文本」对它们没有定义——
    空串互不相同的话，每页的空块会被并成一条假线。
    """
    if not pages:
        return ()
    total_pages = len(pages)
    occurrences: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    for page in pages:
        for block in page.blocks:
            text = (block.text or "").strip()
            if not text:
                continue
            occurrences[_fold(text)].append((page.page, block.index, int(block.bbox[1])))

    hints: list[FurnitureHint] = []
    for text, hits in occurrences.items():
        distinct_pages = {page_no for page_no, _, _ in hits}
        if len(distinct_pages) < min_pages or len(distinct_pages) < page_ratio * total_pages:
            continue
        positions = [y for _, _, y in hits]
        if max(positions) - min(positions) > y_tolerance:
            continue
        hints.append(
            FurnitureHint(
                text=text,
                y_center=int(median(positions)),
                members=tuple(sorted((page_no, index) for page_no, index, _ in hits)),
            )
        )
    return tuple(sorted(hints, key=lambda hint: (hint.y_center, hint.text)))


def furniture_texts(
    pages: Sequence[BlocksPage], *, hints: Sequence[FurnitureHint] = ()
) -> dict[int, tuple[str, ...]]:
    """整本页块序列 → 逐页的家具文本（归一口径）＝线维（`hints` 的成员落页）∪ 类型维（人工判定的三值）。

    `pages` 必须是**回放后**的块序列：类型维看的是「人工判成什么」，喂产物原判就漏掉刚改判的那一行。
    线维按 `FurnitureHint.members` 的页号落页，不参与这条线的页拿不到它——同一串字出现在别页的正文里
    是内容。页码各页不同文本，线维判不出，这条腿只能由类型维承接（第二令 R10 的「默认剔除」＝两腿合取）。

    输出**稀疏**：没有家具的页不占键，调用方按页取，缺键即「这页不用剔」。
    """
    found: dict[int, set[str]] = defaultdict(set)
    for page in pages:
        for block in page.blocks:
            if block.type not in FURNITURE_TYPES:
                continue
            text = (block.text or "").strip()
            if text:
                found[page.page].add(_fold(text))
    for hint in hints:
        for page_no, _ in hint.members:
            found[page_no].add(hint.text)
    return {page_no: tuple(sorted(texts)) for page_no, texts in sorted(found.items())}


def furniture_blocks(
    pages: Sequence[BlocksPage], *, hints: Sequence[FurnitureHint] = ()
) -> dict[int, tuple[int, ...]]:
    """整本页块序列 → 逐页的家具块下标（`Block.index`）＝类型维（回放后三值）∪ 线维（`hints` 成员）。

    与 `furniture_texts` 同一判据、另一种投影：文本供取材剔除逐行比对，块下标供渲染腿逐块隐藏
    （根侧「美化」开关的数据源）。`pages` 同样必须是**回放后**的块序列——人工改判进不了类型维。

    类型维**不看文本**：判成页眉的空块也是家具，隐藏它不丢内容；线维按 `(页号, 块下标)` 成员落块。
    输出**稀疏**且每页升序去重：没有家具的页不占键。
    """
    found: dict[int, set[int]] = defaultdict(set)
    for page in pages:
        for block in page.blocks:
            if block.type in FURNITURE_TYPES:
                found[page.page].add(block.index)
    for hint in hints:
        for page_no, index in hint.members:
            found[page_no].add(index)
    return {page_no: tuple(sorted(indexes)) for page_no, indexes in sorted(found.items())}


def strip_furniture(markdown: str, texts: Sequence[str]) -> str:
    """页 Markdown → 删掉整行都是家具线的行；其余每一行逐字返回（含空行、缩进、公式定界）。

    判据是**整行折叠相等**，不是「包含」：正文里「见《PRINCIPLES OF MATHEMATICAL ANALYSIS》前言」这类
    引用句是内容，吃掉它就等于替读者删句子。行首的 `#` 是 `blocks_to_markdown` 给标题加的渲染装饰，
    比对时剥掉——页眉被引擎判成标题（b05 章名眉的实际形状）时，它的行与那一条线仍是同一串字。

    只做读时投影：产物文件与 `GET /pages` 的 `markdown` 不经此函数，本函数也不写任何状态。
    """
    furniture = {text for text in texts if text}
    if not furniture:
        return markdown
    return "\n".join(
        line for line in markdown.split("\n") if _fold(line.lstrip("#")) not in furniture
    )
