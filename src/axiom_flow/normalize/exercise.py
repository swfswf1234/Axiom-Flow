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

#: 题号闭集（`KA-53`，NFKC 折叠后判）：`【N】`／`[N]`／`(N)`／`N-M`／`N.`／`N、`／`Problem N.M:`。
#: `N-M` 与 `N.M` 都归一成 `N-M`——比对键只求同书同章内可比，不求还原排法。
#: 行末终止符后面不许紧跟数字：`1.2 极限` 是节号不是题号（那正是「回退视为正文」之外的
#: 另一条排除），而 `3.求导` 这种中文紧贴题号的排法必须认。
#: ASCII 方括号与 `【】` 同族：题腿 prompt 用 `[N]` 展示题号、模型照抄回来（`KA-56`），不认它
#: 就等于整节都对不上原文。
#: 数字段到四位：b07／b11 全书题号递增到 4462（PLAN-036 W10），三位封顶会把章尾整段题切成正文。
#: 英文题号（b17）是 `Problem N.M:`：`Problem` 只是引导词，题号取 `problem_no` 那一段。
#: 题号后面**必须有**冒号或左括号：`Problem 8.26 will imply …` 是正文里的交叉引用（b17 p98），
#: 冒号可省会把整行抄成一枚假题；括号形是带备注的真题标题（`Problem 6.11 (with …)`）。
_ITEM_NO = re.compile(
    r"^\s*(?:"
    r"【\s*(?P<bracket>\d{1,4}(?:\s*[-—–]\s*\d{1,4})?)\s*】"
    r"|\[\s*(?P<square>\d{1,4}(?:\s*[-—–]\s*\d{1,4})?)\s*\]"
    r"|\(\s*(?P<paren>\d{1,4}(?:\s*[-—–]\s*\d{1,4})?)\s*\)"
    r"|(?P<dash>\d{1,4}\s*[-—–]\s*\d{1,4})(?=$|[\s.、．:：])"
    r"|(?P<dot>\d{1,4}(?:[.．]\d{1,4})*)\s*[.、．](?=$|[^\d])"
    r"|(?P<problem>Problem\s+(?P<problem_no>\d{1,4}\s*[.．]\s*\d{1,4}))\s*(?=[:：（(])"
    r")\s*",
    re.IGNORECASE,
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

    形状族＝括号三种、外置三种（`dot` 兼 `N、`）与英文 `problem`。同一节里只认首枚被接受题号的
    形状族：主问题用 `1.`、小问用 `(1)` 是排版的常态，小问的 `(2)` 在数值上大于主问题的 `1`，只看
    单调序会把它切成一枚假题——形状族不同的行一律当正文。英文题号的 `Problem` 只是引导词，
    归一键取 `problem_no`，题号原样只到 `N.M`（尾冒号不进名字）。
    """
    folded = _fold(text)
    match = _ITEM_NO.match(folded)
    if match is None:
        return None
    groups = match.groupdict()
    kind = next((name for name, value in groups.items() if value), "")
    raw = groups.get("problem_no") if kind == "problem" else groups.get(kind)
    normalized = _normalize_no(raw or "")
    if not normalized:
        return None
    label = folded[: match.end()].strip()
    if kind == "problem":
        label = label.rstrip(":：")
    if kind == "bracket":
        # REQ-179 R3（b07 真机取证）：`【N】` 是书排的题号形状，与正文枚举 `(N)` 必须分族——
        # 两者同族时正文里的枚举小问能通过「同族＋单调」闸收成假题，吞掉其后整段教材正文。
        family = "bracket"
    elif kind == "problem":
        family = "problem"
    elif kind in ("square", "paren"):
        family = "paren"
    else:
        family = "plain"
    return (normalized, label, family)


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
    if re.fullmatch(r"\d{1,4}(?:\s*[-—–.]\s*\d{1,4})*", text) is None:
        return None
    return _normalize_no(text) or None


def _dominant_family(pages: Sequence[tuple[int, str]]) -> str | None:
    """本节的题号形状族（`KA-53`）：族由整节**最强**的形状定，不看行序先后。

    强形状＝书排的题目标题，不可能是一枚小问或公式编号：显式英文题号 `Problem N.M:`（b17）
    与方头括号 `【N】`（b07／b11，REQ-179 R3 取证）。节内两者任一出现即锁族——裸括号 `(N)`
    在这些书里是正文枚举小问（下确界定义的 `(1)/(2)`）或公式编号，先被接受会把族与单调序
    水印一并锁死；裸括号自成节的排法（整节只有 `(N)`）没有更强形状在场，族仍由首枚括号定。
    """
    for name in ("problem", "bracket"):
        for _, markdown in pages:
            for line in (markdown or "").splitlines():
                matched = _match_label(line)
                if matched is not None and matched[2] == name:
                    return name
    return None


def split_exercise_items(pages: Sequence[tuple[int, str]]) -> tuple[ExerciseItem, ...]:
    """一节正文的逐页 markdown → 这道段里被接受的题（`KA-53`）。

    只认「题号严格大于上一枚已接受题号」的行首；小问重新从 `(1)` 起、章内重复编号等回退一律
    视为正文——宁可少切，也不把一行正文抄成一道假题。每页独立喂进来，页区间跟着题号行与它
    正文的末行；题号行之前的内容（段首导语、例题说明）不属于任何题，丢弃。

    一节一次调用，序在调用内重置：跨章的编号重置由「按节分别调用」天然给出，不靠猜。

    族在开切前先定（`_dominant_family`）：本节出现 `Problem N.M:` 或 `【N】` 即锁成该族——
    它们是整节最强的形状，不可能是一枚小问或公式编号。章界按页切（b17 取 `Problem N.M` 首现页），
    章首屏常带着上一章尾巴里的公式编号 `(7)`；弱形状先被接受会把族与单调序水印一并锁死，
    整章题全丢。
    """
    items: list[ExerciseItem] = []
    current_no: str | None = None
    current_label = ""
    current_lines: list[str] = []
    current_start = 0
    current_end = 0
    order: tuple[int, ...] | None = None
    family = _dominant_family(pages)
    seen: set[str] = set()

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
                # 显式英文题号是排版出来的题目标题，节内允许乱序（b17 第 3 章把 3.21／3.22 排在
                # 小节之前），只按「本节没出现过这枚题号」判新题；其余形状族仍靠严格单调水印。
                if (
                    same_family
                    and number not in seen
                    and (family == "problem" or order is None or candidate > order)
                ):
                    close()
                    current_no = number
                    current_label = label
                    current_lines = [line]
                    current_start = page_no
                    current_end = page_no
                    seen.add(number)
                    order = candidate if order is None else max(order, candidate)
                    family = shape
                    continue
            if current_no is not None:
                current_lines.append(line)
                if line.strip():
                    current_end = page_no
    close()
    return tuple(items)


# --------------------------------------------------------------------------- 无目录书的章界（PLAN-036 W9）


#: `Problem N.M:` 的题号：只有题号本身进比对，`Problem` 是引导词、尾冒号是排版。
_PROBLEM_NO = re.compile(r"(?im)^[ \t]*Problem\s+(\d{1,4})\s*[.．]\s*\d{1,4}\s*[:：]")


def homework_chapters(pages: Sequence[tuple[int, str]]) -> tuple[tuple[int, int], ...]:
    """一本无目录的英文解答册 → `(章号, 首页)` 序列（b17 形状，PLAN-036 W9）。

    章界不靠目录页：`Problem N.M` 的首段就是所属作业／章的号，取该号**首现页**。页序按调用方给的
    顺序扫，同号只记第一次；一枚题号都认不出时返回空——那本书的目录腿该退回既有通路，而不是造章。
    """
    first_page: dict[int, int] = {}
    for page_no, markdown in pages:
        for matched in _PROBLEM_NO.finditer(markdown or ""):
            first_page.setdefault(int(matched.group(1)), page_no)
    return tuple(sorted(first_page.items()))


# --------------------------------------------------------------------------- 答案段回配（PLAN-036 W11）


@dataclass(frozen=True, slots=True)
class AnswerEntry:
    """答案段里的一枚读数：题号比对键与答案正文（不含题号本身）。"""

    item_no: str
    text: str


#: 答案段的行内题号：`16.`／`4457.1.`；末尾的点后面不能再跟数字，否则 `0.41%`、`1.01` 这类
#: 小数会冒充题号。前一眼也不能紧贴数字或点，避免从 `2.49` 的尾巴上切出 `49`。分级题号的点两侧
#: 不许有空白：`10. 5.` 是两条答案，不是 `10.5` 这一枚。
_ANSWER_NO = re.compile(r"(?<![\d.])(\d{1,4}(?:[.．]\d{1,4})*)[.．](?![\d])")

#: 答案段的结构行：运行页眉「答案」与章标题——它们是导航，不是上一条答案的正文。
_ANSWER_STRUCTURE = re.compile(
    r"^(?:答\s*案|提\s*示\s*与\s*解\s*答|习题\s*答\s*案"
    r"|第?\s*[0-9０-９一二三四五六七八九十百]+\s*章)$"
)


def textbook_answer_key(pages: Sequence[tuple[int, str]]) -> tuple[AnswerEntry, ...]:
    """一本教材的答案段 → `(题号, 答案)` 序列（b11 形状，PLAN-036 W11）。

    答案在书尾成段连排（`16. 0; 1. 17. …`），题号与答案正文只能靠**严格递增**切开：候选题号必须
    大于上一枚已接受题号，否则它是上一条答案里的数值（`0; 1.` 里那枚 `1`）或小问号，一律归入上一条
    正文。章标题与运行页眉整行剔除，不并入任何答案；整段从第一个可接受题号起算。

    判据只认题号本身是否可解释，不判断答案对不对：跳号（只有部分题给答案）与 OCR 残行都如实留下，
    配不上的题在配对侧按「无答案」处理——宁缺，不猜。
    """
    kept = [
        line
        for _, markdown in pages
        for line in (markdown or "").splitlines()
        if not _ANSWER_STRUCTURE.match(_fold(line).strip())
    ]
    text = "\n".join(kept)
    accepted: list[tuple[tuple[int, ...], int, int, str]] = []
    last: tuple[int, ...] | None = None
    for matched in _ANSWER_NO.finditer(text):
        number = _normalize_no(matched.group(1))
        order = _order_of(number)
        if not order or (last is not None and order <= last):
            continue
        last = order
        accepted.append((order, matched.start(), matched.end(), number))
    entries: list[AnswerEntry] = []
    for index, (_, _, end, number) in enumerate(accepted):
        stop = accepted[index + 1][1] if index + 1 < len(accepted) else len(text)
        body = text[end:stop].strip()
        if body:
            entries.append(AnswerEntry(item_no=number, text=body))
    return tuple(entries)
