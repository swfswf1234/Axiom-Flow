"""习题切分的纯规则（`KA-53`）：题号闭集＋同父单调序。

边界只由这里给：模型读题面与解答的**内容**，不得自行分题（每笔照旧进账，`KA-23`）。规则输入是
一节正文的逐页 markdown（`pages/p<NNNN>.md` 那一类产物），输出是题号、页区间与该题正文的切片；
不写库、不碰磁盘。识别不出或序号不成立 ⇒ 零题，不回落模型分题——这是披露，不是失败。

与 `normalize/toc.py` 的 `is_exercise_label`／`exercise_entries`（`KA-46`）是两把不同的尺：那一把认
「章末习题段的标题」，这一把认「段内每一题的题号」。两者互不调用，也互不改写对方的判据。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

#: 题号闭集（`KA-53`，NFKC 折叠后判）：`【N】`／`(N)`／`N-M`／`N.`／`N、`。
#: `N-M` 与 `N.M` 都归一成 `N-M`——比对键只求同书同章内可比，不求还原排法。
#: 行末终止符后面不许紧跟数字：`1.2 极限` 是节号不是题号（那正是「回退视为正文」之外的
#: 另一条排除），而 `3.求导` 这种中文紧贴题号的排法必须认。
_ITEM_NO = re.compile(
    r"^\s*(?:"
    r"【\s*(?P<bracket>\d{1,3}(?:\s*[-—–]\s*\d{1,3})?)\s*】"
    r"|\(\s*(?P<paren>\d{1,3}(?:\s*[-—–]\s*\d{1,3})?)\s*\)"
    r"|(?P<dash>\d{1,3}\s*[-—–]\s*\d{1,3})(?=$|[\s.、．:：])"
    r"|(?P<dot>\d{1,3}(?:\s*[.．]\s*\d{1,3})*)\s*[.、．](?=$|[^\d])"
    r")\s*"
)


@dataclass(frozen=True, slots=True)
class ExerciseItem:
    """一节里切出的一道题：比对键、原样题号、页区间与该题正文切片。

    `item_no` 是归一后的比对键（`12`／`2-10`），合并与查重只认它；`label` 是行首题号的原样
    （`12.`／`【12】`），题行的 `name` 用它。`text` 从题号那一行起、到下一道被接受的题之前。
    """

    item_no: str
    label: str
    page_start: int
    page_end: int
    text: str


def _fold(text: str) -> str:
    """全半角折叠：`（1）`／`１．` 与半角同形后才进闭集判（`KA-53`）。"""
    return unicodedata.normalize("NFKC", text or "")


def _normalize_no(raw: str) -> str:
    """题号的比对键：数字段以 `-` 相连、去空白；`3.2.` 与 `3-2` 同键。"""
    parts = [part for part in re.split(r"[.\-—–\s]+", raw.strip()) if part]
    return "-".join(parts)


def _order_of(item_no: str) -> tuple[int, ...]:
    """单调序的比较键：数字段逐段比；`2-10` 大于 `2-9`，前缀更短者更小。"""
    return tuple(int(part) for part in item_no.split("-") if part.isdigit())


def _match_label(text: str) -> tuple[str, str, str] | None:
    """一行行首的 `(归一键, 原样题号, 形状族)`；不是闭集里的题号形状即 `None`。

    形状族＝括号三种与外置三种（`dot` 兼 `N、`）。同一节里只认首枚被接受题号的形状族：
    主问题用 `1.`、小问用 `(1)` 是排版的常态，小问的 `(2)` 在数值上大于主问题的 `1`，只看
    单调序会把它切成一枚假题——形状族不同的行一律当正文。
    """
    folded = _fold(text)
    match = _ITEM_NO.match(folded)
    if match is None:
        return None
    kind, raw = next(
        ((name, value) for name, value in match.groupdict().items() if value), ("", "")
    )
    normalized = _normalize_no(raw)
    if not normalized:
        return None
    return normalized, folded[: match.end()].strip(), ("paren" if kind in ("bracket", "paren") else "plain")


def item_number(text: str) -> str | None:
    """一行行首的题号归一键；不是闭集里的题号形状即 `None`。

    持久化后的题行 `name` 存的是原样题号，合并两本书时用它把名字折回同一把键
    （`KA-53` 的「章＋题号」配对）。
    """
    matched = _match_label(text)
    return matched[0] if matched is not None else None


def exercise_item_key(value: str) -> str | None:
    """题号文本 → 比对键（`KA-53` 的「章＋题号」配对）：行首形状优先，其次整串按数字段归一。

    模型回的题号不保证带行首标点（`1`／`1.`／`【1】` 都可能），而切分那一侧的键只看数字段；
    两处必须折到同一把键上，配对才谈得上命中。整串归一只收纯数字段形状——`第 1 题` 这类文本
    折出来的键会把一行正文冒充成题号。
    """
    text = _fold(value).strip()
    if not text:
        return None
    matched = _match_label(text)
    if matched is not None:
        return matched[0]
    if re.fullmatch(r"\d{1,3}(?:\s*[-—–.]\s*\d{1,3})*", text) is None:
        return None
    return _normalize_no(text) or None


def split_exercise_items(pages: Sequence[tuple[int, str]]) -> tuple[ExerciseItem, ...]:
    """一节正文的逐页 markdown → 这道段里被接受的题（`KA-53`）。

    只认「题号严格大于上一枚已接受题号」的行首；小问重新从 `(1)` 起、章内重复编号等回退一律
    视为正文——宁可少切，也不把一行正文抄成一道假题。每页独立喂进来，页区间跟着题号行与它
    正文的末行；题号行之前的内容（段首导语、例题说明）不属于任何题，丢弃。

    一节一次调用，序在调用内重置：跨章的编号重置由「按节分别调用」天然给出，不靠猜。
    """
    items: list[ExerciseItem] = []
    current_no: str | None = None
    current_label = ""
    current_lines: list[str] = []
    current_start = 0
    current_end = 0
    order: tuple[int, ...] | None = None
    family: str | None = None

    def close() -> None:
        if current_no is None:
            return
        text = "\n".join(current_lines).strip("\n")
        items.append(
            ExerciseItem(
                item_no=current_no,
                label=current_label,
                page_start=current_start,
                page_end=current_end,
                text=text,
            )
        )

    for page_no, markdown in pages:
        for line in (markdown or "").splitlines():
            matched = _match_label(line)
            if matched is not None:
                number, label, shape = matched
                candidate = _order_of(number)
                same_family = family is None or shape == family
                if same_family and (order is None or candidate > order):
                    close()
                    current_no = number
                    current_label = label
                    current_lines = [line]
                    current_start = page_no
                    current_end = page_no
                    order = candidate
                    family = shape
                    continue
            if current_no is not None:
                current_lines.append(line)
                if line.strip():
                    current_end = page_no
    close()
    return tuple(items)
