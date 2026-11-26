"""书自身目录页的条目腿：把「目录页的一行条目」拆成条目名＋印刷页码，再用偏移标定把条目名回指到正文页。

判据正文见 [knowledge-analysis.md](../../docs/design/knowledge-analysis.md) 的 `KA-33`（回落腿）与
`KA-37`／`KA-38`（正腿复用本模块的定锚判据）、`KA-41`（`clean_entry_rows`＝条目腿与模型整理腿共用的
那一把行清洗，与比对口径的闭集标点折算）、`KA-46`（习题段标签的整块判据：`is_exercise_label`／
`exercise_entries`）。本模块只吃 `BlocksPage`（页产物回放后的块序列），
产出纯读数：不写库、不改块类型、不碰磁盘产物——合成出来的标题读数由编排层并进取证面，
`af_pages` 的任何列都不经这里。

现网存在整本零 `heading` 块的教材（章名与节名一律落 `paragraph`），目录阶段因此零输入；这条腿补的
就是那份输入。印刷页码**永不进锚点**，它只用来标定「绝对页 − 印刷页」的本书偏移，锚点只认落在
`印刷页＋偏移 ± TOC_LOCATE_SLACK` 窗口内的命中——目录页里的条目名有大量正文回声，放开窗口就是把
一整节的锚指到别的章。同一条定锚判据也被模型正腿复用（`KA-38`）：名字由模型给，页与块由这里算。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from axiom_flow.normalize.blocks import blocks_to_markdown
from axiom_flow.normalize.furniture import FURNITURE_TYPES
from axiom_flow.schemas import BlocksPage, BlockType

#: 一个段落块要被当作目录页，至少要这么多行。两行的「带页码重复」是巧合（正文里引一条定理也这样写）。
TOC_MIN_ENTRIES = 3
#: 回定位的窗口半径（单位＝页）。同偏移的条目只允许在各页抖动一两页的范围内命中；并列众数取较小偏移。
TOC_LOCATE_SLACK = 1
#: 标定偏移所需的最少支持条目数。只有一枚候选时它自己就是自己的众数，把它当偏移等于拿回声造假锚——
#: 支持数不足即标定不出，全书条目一律走「未定位披露」而不是猜。
TOC_MIN_OFFSET_SUPPORT = 2

#: 判页腿的取材封顶（`KA-37`）：目录在一本书里是局部事实，前 `TOC_PAGE_SCAN_LIMIT` 页与末同数页
#: 就够模型认出「哪几页是目录、哪几页是正文前后的附件」；每页再截 `TOC_PAGE_SAMPLE_MAX_CHARS` 字符，
#: 单次回复长度压到千字符级才是本机吞吐能过超时墙的形状。
TOC_PAGE_SCAN_LIMIT = 10
TOC_PAGE_SAMPLE_MAX_CHARS = 400

#: 附件页类别闭集（`KA-39`）：模型只许在这七值里选，域外值折算成空串（不替模型造第八个域值）。
TOC_REFERENCE_KINDS = (
    "front_matter",
    "back_matter",
    "symbol_table",
    "appendix",
    "bibliography",
    "index",
    "exercise",
)
TOC_ROLE_KNOWLEDGE = "knowledge"
TOC_ROLE_REFERENCE = "reference"

#: 四值类型闭集（`KA-44`，基本版本二次令文）：屏上标签只认这四词，由结构与两枚身份位派生。
NODE_TYPE_CHAPTER = "chapter"
NODE_TYPE_POINT = "point"
NODE_TYPE_EXERCISE = "exercise"
NODE_TYPE_OTHER = "other"

#: 回落腿的名字集合（`KA-39`）：模型判页不成时，附件行只能按名字认。顺序即判序——符号表要先于
#: 前言类判（一枚名字同时含两枚关键词时，符号表是更具体的一类），习题殿后（`附录 习题解答` 归附录）。
_REFERENCE_NAME_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("symbol_table", re.compile(r"符号表|记号表|重要符号")),
    ("front_matter", re.compile(r"前言|引言|序言|译名表|编者附言|导读|内容简介|致读者|作者与出版者")),
    ("appendix", re.compile(r"^\s*附\s*录|^\s*appendix", re.IGNORECASE)),
    ("bibliography", re.compile(r"参考文献|书目|参考书目")),
    ("index", re.compile(r"^\s*索\s*索?\s*引|^\s*index", re.IGNORECASE)),
    ("back_matter", re.compile(r"(?:校订|译|编)?后\s*记|^\s*跋")),
    ("exercise", re.compile(r"^\s*习\s*题|^\s*练\s*习|习题解答|习题提示")),
)

#: 行末＝印刷页码＋它前面的引导点（`…`／`·`／`.`／连字符／破折号／任意空白）。只作用在行尾：
#: 名字中间的数字（`2.10 一致连续`）与名字末尾的句点（`RIEMANN-STIEI.`）一概不碰。
_TRAILING_PAGE = re.compile(r"^(?P<name>.+?)[\s·・.…—–\-]*?(?P<page>[0-9]+)$")

#: 行内第二处「引导点＋印刷页码」：产物把两条条目黏成一行（`第4章 连续性 …… 73函数的极限 …… 79`）
#: 同样是常态，不拆则整条名字在正文里永不命中。只认两枚以上的引导符（`2.10` 的单句点不算边界），
#: 或「空白＋单枚引导符」，且数字后面还有内容——行尾那份页码仍归 `_TRAILING_PAGE`。
_INNER_PAGE = re.compile(r"(?:[·・.…—–\-]{2,}\s*|\s+[·・.…—–\-]\s*)(?P<page>[0-9]+)")

#: 行末的引导符串与其中的**强引导符**（`KA-41`）：只有串里含至少一枚强引导符才剔。单枚 ASCII 句点与
#: 连字符是名字的一部分（`RIEMANN-STIEI.`），剔了就改掉书自己写的话——这与「行末有页码时连引导点一起剥」
#: 是两条判据：那一枚有数字作证，这一枚只有符号形状作证，所以只认不会出现在名字里的那几种。
_LEADER_CHARS = " \t·・.…—–-"
_STRONG_LEADERS = "·・…"

#: 比对口径的闭集标点折算（`KA-41`）：只折这两枚，编号里的点与逗号一律不动。
_PUNCT_FOLD = str.maketrans({"。": ".", "、": ","})

#: 条目行形状 → 层级。`第N章` 与**单级**编号（`1.`／`3 `）起头认作章（1），`N.M` 多级编号与其余行
#: 认作节（2）；机器不补层级，最终 `depth` 判定按这条形状直接成行（`KA-35`）。
#: `第` 可缺（真机 `四章 利用导数研究函数` 是 OCR 吃掉「第」的原形），`绪论／导论／绪言` 按章论。
_CHAPTER_SHAPE = re.compile(
    r"^(?:第?\s*[0-9０-９一二三四五六七八九十百]+\s*[章节篇卷部]"
    r"|绪\s*论|导\s*论|绪\s*言"
    r"|[0-9]+[ \t.．·](?![0-9]))"
)

#: 书级层级归一的「明写章」判据（`KA-57`）：只有真章号才算章。`1. 前言` 这类单级编号在明写章号的书里
#: 是节，不能靠 `_CHAPTER_SHAPE` 抬成章；`第N节` 明写的是节，同样不抬。
_EXPLICIT_CHAPTER = re.compile(
    r"^(?:第?\s*[0-9０-９一二三四五六七八九十百]+\s*[章篇卷部]|绪\s*论|导\s*论|绪\s*言)"
)

#: 书目级附件类别（`KA-57`）：目录自身列着书尾／书前件时，它们与章同级成顶级行，不挂到最后一章底下。
#: 习题类别不在其中——章末习题段是章的子行，不是附件。成行去重的「全书唯一」也认这一组（D-12）。
TOP_LEVEL_REFERENCE_KINDS = frozenset(
    {"front_matter", "back_matter", "symbol_table", "appendix", "bibliography", "index"}
)


@dataclass(frozen=True, slots=True)
class TocEntry:
    """目录页里的一条条目：清洗后的条目名、行末的印刷页码（折行前半截为 `None`）、行形状给的层级。

    `block_index` ＝该条目所在的**段落块**下标，未定位时编排层用它把读数落回目录页自身。
    """

    name: str
    printed_page: int | None
    level: int
    block_index: int


@dataclass(frozen=True, slots=True)
class LocatedEntry:
    """一条条目的回定位读数：与输入同序，`located=False` 时 `page_no` 为 `None`（不造锚）。

    编排层据此决定合成标题读数的坐标——命中页＋命中块序，或落回目录页＋目录块序并进账面点名。
    """

    entry: TocEntry
    page_no: int | None
    block_index: int
    located: bool


@dataclass(frozen=True, slots=True)
class TocLocation:
    """一次回定位的两件读数：本书偏移（标定不出为 `None`）与逐条条目读数。

    `offset` 单独给出，是因为「偏移标定成功」是先于逐条命中的一条事实：页级复核要知道它才能
    区分「这一页真的不是目录」与「本书偏移还没标定出来」。
    """

    offset: int | None
    entries: tuple[LocatedEntry, ...]


def _squash(text: str) -> str:
    """比对口径＝NFKC（全角转半角）＋闭集标点折算＋**删掉全部空白**＋小写。

    与 `orchestrator/knowledge.norm_key_for`、`normalize/furniture._fold` 同一条归一算法，区别只在
    空白处理：那两处按「整行/整名」比对，折叠成一个空格即可；这里要比的条目名在产物里被折成多行，
    名字内部的换行与缩进必须整枚删掉，否则「前缀命中」对不上。分层上不反向依赖编排层，故就地三行。

    折标点只折 `_PUNCT_FOLD` 那两枚（`KA-41`）：模型把原文的 ASCII 句点抄成中文句号是实测常态，
    不折就是同一个名字判成两枚；而编号里的点（`2.10`）是教材目录的身份，删全部标点会把不同条目折成同名。
    """
    folded = unicodedata.normalize("NFKC", text or "").translate(_PUNCT_FOLD)
    return "".join(folded.split()).casefold()


def _trailing_page(line: str) -> tuple[str, int] | None:
    """一行 → `(条目名, 印刷页码)`；行末不是「引导点＋阿拉伯数字」则返回 `None`。"""
    matched = _TRAILING_PAGE.match(line.strip())
    if matched is None:
        return None
    return matched.group("name").strip(), int(matched.group("page"))


def _is_chapter_shape(name: str) -> bool:
    return _CHAPTER_SHAPE.match(name.lstrip()) is not None


def entry_level(name: str) -> int:
    """条目名 → 层级（1＝章、2＝节）：只由行形状给，机器不补层级（`KA-35`）。

    公开是因为整理腿也要用它——黏行拆出的第二段不是模型报的那一行，它的层级只能由它自己的形状给。
    """
    return 1 if _is_chapter_shape(name) else 2


#: 编号起头的行（`1. 前言`／`§1. 引言`）是正文条目而不是书前件：`前言`／`引言` 那几枚名字形状是
#: 不锚定的，不加这一道，编号条目会被名字里的关键词误判成附件并抬成章（`KA-57`）。节号前缀 `§`
#: 同样算编号——b13 的 `§1. 引言` 是第十一章的第一节，不是书前件。
_ENTRY_NUMBER_PREFIX = re.compile(r"^(?:§\s*)?[0-9]+[ \t.．·](?![0-9])")


def _is_top_level_name(name: str) -> bool:
    """书级归一的顶级判据：明写章号，或目录自己列出的书前／书尾附件（`KA-57`）。

    附件按名字集合判，与 `reference_role_for_name` 同一份闭集——两处各判一次就会让「目录里的索引行」
    与「判页腿认出的索引页行」差出一级。编号起头的行一律不是附件：`1. 前言` 是节。
    """
    text = (name or "").lstrip()
    if _ENTRY_NUMBER_PREFIX.match(text):
        return False
    if _EXPLICIT_CHAPTER.match(text):
        return True
    role, kind = reference_role_for_name(text)
    return role == TOC_ROLE_REFERENCE and kind in TOP_LEVEL_REFERENCE_KINDS


def normalize_entry_levels(entries: Sequence[TocEntry]) -> list[TocEntry]:
    """书级层级归一（`KA-57`）：本书有明写章号时，其余一／二级行归节，显式三级及以上保留。

    `entry_level` 是**单行**判据，它把 `1. 前言` 读成章（有些书确实这样写）。但一本同时写着
    `第一章…` 与 `1. 前言` 的书里，章只有一个含义——不归一就会让 262 条编号条目全成顶级行，
    每一条都把上一章挤出父位。附件行与章同级，习题段留在章内。
    """
    items = list(entries)
    if not any(_EXPLICIT_CHAPTER.match((entry.name or "").lstrip()) for entry in items):
        return items
    return [
        replace(entry, level=1 if _is_top_level_name(entry.name) else max(2, entry.level))
        for entry in items
    ]


#: 纯编号条目（`34. 单调整序变量`）：不含 `§` 的分节号，与 `_ENTRY_NUMBER_PREFIX` 分开判。
_PLAIN_NUMBER_PREFIX = re.compile(r"^[0-9]+[ \t.．·](?![0-9])")


def fold_numbered_sub_items(entries: Sequence[TocEntry]) -> list[TocEntry]:
    """三级细目识别（`KA-62`）：`§` 节下的纯编号条目降为三级，交给成行处折入二级。

    真机 b12（微积分学教程）的目录是「章 → § 节 → 34./35. 编号细目」三层；模型协议早先只准报
    1／2，细目全被压成二级，屏上每个 § 节被第一个细目切成半截。这条判据是除模型自报三级之外的
    确定性兜底：同一章内，紧跟在 `§` 开头的二级节之后的纯编号条目就是它的细目。没有 `§` 节的
    书一条也不动——纯编号的二级节（`1. 前言` 这类）仍是节，机器不替书降级。
    """
    out: list[TocEntry] = []
    open_section = False
    for entry in entries:
        text = (entry.name or "").lstrip()
        if entry.level <= 1:
            open_section = False
        elif entry.level == 2:
            if text.startswith("§"):
                open_section = True
            elif open_section and _PLAIN_NUMBER_PREFIX.match(text):
                out.append(replace(entry, level=3))
                continue
            else:
                open_section = False
        out.append(entry)
    return out


def _explode_inner_entries(line: str) -> list[str]:
    """一行 → 若干「虚拟行」：行内第二处「引导点＋页码」处拆开，前半截补回它自己的印刷页码。

    只切分不改字：左边是上一条的名字、那串数字是它的行末页码，右边整段是下一条条目（可能还要再拆）。
    末尾那份页码不在这里处理——`_TRAILING_PAGE` 才是行末的唯一解析处。
    """
    segments: list[str] = []
    rest = line.strip()
    while True:
        matched = _INNER_PAGE.search(rest)
        if matched is None:
            segments.append(rest)
            return segments
        left = rest[: matched.start()].strip()
        after = rest[matched.end() :].strip()
        if not after:
            segments.append(rest)
            return segments
        if not left:
            rest = after
            continue
        segments.append(f"{left} …… {matched.group('page')}")
        rest = after


def _strip_trailing_leaders(name: str) -> str:
    """剔掉名字末尾的引导符串；只有一枚弱引导符时一字不动（`RIEMANN-STIEI.` 的句点是名字的一部分）。

    两枚以上的引导符串从不属于名字（`名字....`／`名字 ……` 都是目录排版），单枚只有强引导符
    （`…`／`·`／`・`）才剔——单枚 ASCII 句点与连字符既可能是名字的一部分，也可能是引导符。
    """
    text = (name or "").strip()
    end = len(text)
    while end and text[end - 1] in _LEADER_CHARS:
        end -= 1
    if end == len(text):
        return text
    run = text[end:]
    if len(run) < 2 and not any(char in _STRONG_LEADERS for char in run):
        return text
    return text[:end].rstrip()


#: 名字内部的引导点串（`KA-41`）：两枚以上连排的省略号／间隔号是目录排版（`第1章……集合论`），折成
#: 单个空格。连字符不在闭集里——`--` 是 LaTeX 的 en-dash、`——` 可以是书名自己的破折号；单枚一律不动。
_INNER_LEADER_RUN = re.compile(r"[·・]{2,}|\.{3,}|…{2,}|⋯{2,}")


def _clean_entry_name(name: str) -> str:
    """条目名的末道清洗（`KA-41`）：剔行末引导点串，再把名字内部的引导点串折成一个空格。"""
    return " ".join(_INNER_LEADER_RUN.sub(" ", _strip_trailing_leaders(name)).split())


def _is_furniture_line(line: str) -> bool:
    """目录页的家具行（`KA-41`）：空行、光秃秃的页码、书眉「目录／Contents（续）」。

    这三形最危险的位置是折行拼回——混进待拼碎片就会粘到下一条名字前面。判据与判页标记同一闭集：
    两处各写一份，`目录（续）` 会在一处算书眉、在另一处算条目。
    """
    folded = unicodedata.normalize("NFKC", line or "").strip()
    return not folded or folded.isdigit() or is_toc_marker_line(folded)


def clean_entry_rows(lines: Sequence[str]) -> list[tuple[str, int | None]]:
    """若干原始行 → `(条目名, 印刷页码)` 序列：**行清洗的唯一正本**（`KA-41`）。

    四步＝家具行剔除（空行／裸页码／目录书眉）→ 黏行按行内第二处「引导点＋页码」拆开 → 行末引导点
    与印刷页码一律剔离 → 无页码的碎片先与前一条拼回再判；名字内部的引导点串在所有出口折成一个空格。
    确定性条目腿（`toc_entries`）与模型整理腿（`orchestrator/knowledge.build_outline`）共用这一把：
    名字是入库的那份，`norm_key` 与屏上读数都由它派生，两处各洗一份必漂移。

    拼回的判据只看带页码那一行——它以章／节形状起头时自己是新条目，前半截只能独立成条
    （`printed_page=None`，随后走未定位披露）；否则并给它（`附录` 紧跟 `第2章 …… 49` 时不并）。
    """
    rows: list[tuple[str, int | None]] = []
    pending: list[str] = []
    for raw in lines:
        for line in _explode_inner_entries(raw):
            if _is_furniture_line(line):
                continue
            parsed = _trailing_page(line)
            if parsed is None:
                pending.append(line.strip())
                continue
            name, printed = parsed
            if not _is_chapter_shape(name) and pending:
                name = " ".join([*pending, name])
                pending = []
            elif pending:
                tail = _clean_entry_name(" ".join(pending))
                if tail:
                    rows.append((tail, None))
                pending = []
            name = _clean_entry_name(name)
            if not name:
                continue
            rows.append((name, printed))
    if pending:
        tail = _clean_entry_name(" ".join(pending))
        if tail:
            rows.append((tail, None))
    return rows


def _split_block(lines: Sequence[str], block_index: int) -> list[TocEntry]:
    """一个目录页块的有效行 → 条目序列（清洗与折行拼回都在 `clean_entry_rows` 里发生）。"""
    return [
        TocEntry(name=name, printed_page=printed, level=entry_level(name), block_index=block_index)
        for name, printed in clean_entry_rows(lines)
    ]


def toc_entries(page: BlocksPage) -> tuple[TocEntry, ...]:
    """一页产物 → 目录页条目读数；这页不是目录页时出空元组。

    只吃 `paragraph` 块：有真标题的书走 `_heading_evidence` 原路，本条腿零输出（`KA-33` 的边界）。
    目录页判据只有两条，都按**单块**判——同一块内 ≥`TOC_MIN_ENTRIES` 行、且以数字结尾的行占多数；
    一条条目黏成一行时它仍是少数行里的一行，宁缺毋滥地整体落进未定位披露。
    """
    entries: list[TocEntry] = []
    for block in page.blocks:
        if block.type is not BlockType.PARAGRAPH:
            continue
        lines = [line.strip() for line in (block.text or "").split("\n") if line.strip()]
        if len(lines) < TOC_MIN_ENTRIES:
            continue
        numbered = sum(1 for line in lines if _trailing_page(line) is not None)
        if numbered * 2 <= len(lines):
            continue
        entries.extend(_split_block(lines, block.index))
    return tuple(entries)


#: 整段抄录允许的块类型（`KA-57`）：判页腿已把这一页认成目录页，条目可能落在段落、列表或标题块里；
#: 页眉／页脚／页码三型是家具，真机页脚那枚光秃秃的数字不该被当成条目。
_TOC_TEXT_TYPES = frozenset({BlockType.PARAGRAPH, BlockType.LIST, BlockType.HEADING})

#: 行内页码标记（`KA-57`）三形：括号页码（`(162)`）、引导点连排（`....162`／`…… 162`）、
#: 空白夹着的单枚引导符（` - 162`）。单枚 ASCII 句点不是边界——`2.10`／`3.14` 里的点属于编号和小数。
_PAGE_MARKER = re.compile(
    r"(?:\((?P<paren>[0-9]{1,4})\)"
    r"|(?P<leaders>[·・.…—–\-](?:[ \t]*[·・.…—–\-])+)[ \t]*(?P<leader_page>[0-9]{1,4})"
    r"|(?<=\s)(?P<spaced>[·・.…—–\-])[ \t]*(?P<spaced_page>[0-9]{1,4}))"
)


def _split_page_markers(line: str) -> list[tuple[str, int | None]]:
    """一行 → `(名字片段, 页码)` 序列：每个页码标记切一刀，标记之前的文字归这一条。

    最后一段没有页码时也作为片段返回（`printed_page=None`），由整段抄录腿拿它和下一页的头一段拼回。
    没有任何标记的整行就是一段无页码文字（折行的前半截）。
    """
    pieces: list[tuple[str, int | None]] = []
    position = 0
    for matched in _PAGE_MARKER.finditer(line):
        page_text = (
            matched.group("paren") or matched.group("leader_page") or matched.group("spaced_page")
        )
        if page_text is None:
            continue
        pieces.append((line[position : matched.start()], int(page_text)))
        position = matched.end()
    if position < len(line):
        pieces.append((line[position:], None))
    return pieces or [(line, None)]


def toc_entries_across_pages(pages: Sequence[BlocksPage]) -> tuple[tuple[int, TocEntry], ...]:
    """整段目录页 → `(来源页, 条目)` 序列（`KA-57`）：页码只由原文给，跨页续行拼回。

    `toc_entries`（`KA-33`）是「不知道哪几页是目录」时的单页兜底，只看行末；这一把吃判页腿已经
    认下的整段目录页，处理一页多块、一行多条目（`1. 前言 (1) 2. …`）、跨页续行（`及其应用 (50)`）
    与续页起头的孤立页码（`(162) 96. …`）。行末引导点与页码在切分时已经离开名字，不再需要第二遍清洗。

    没有回收出页码的片段不单独成行：本轮只按「有页数的」建结构，缺页的残尾弃用（OCR 伤了页码的
    那几条会进账面的缺失数，不拿目录页当锚硬凑）。层级在整段读完之后按书级判据归一（`KA-57`）。
    """
    entries: list[TocEntry] = []
    owners: list[int] = []
    pending: list[str] = []
    for page in pages:
        for block in page.blocks:
            if block.type not in _TOC_TEXT_TYPES:
                continue
            for raw in (block.text or "").splitlines():
                line = unicodedata.normalize("NFKC", raw).strip()
                if _is_furniture_line(line):
                    continue
                for fragment, printed in _split_page_markers(line):
                    fragment = fragment.strip()
                    if printed is None:
                        if fragment:
                            pending.append(fragment)
                        continue
                    # 编号起头的新条目不受上一行残尾牵连：残尾没有页码，并进来会把两枚名字粘成一条。
                    if pending and _is_chapter_shape(fragment):
                        pending = []
                    name = _clean_entry_name(" ".join([*pending, fragment]))
                    pending = []
                    if not name:
                        continue
                    entries.append(
                        TocEntry(
                            name=name,
                            printed_page=printed,
                            level=entry_level(name),
                            block_index=block.index,
                        )
                    )
                    owners.append(page.page)
    return tuple(zip(owners, normalize_entry_levels(entries), strict=True))


def estimate_print_offset(candidates: Sequence[tuple[int, int]]) -> int | None:
    """`(印刷页, 命中页)` 候选序列 → 本书偏移（众数）；标定不出时 `None`。

    同一本书的正文绝对页与印刷页只差一个常数（前言、扉页占掉的页数），所以取众数而不是取第一枚命中；
    众数并列取较小偏移（读数确定，守护可对偶）。支持数不足 `TOC_MIN_OFFSET_SUPPORT` 条即返回 `None`——
    那意味着「命中」全是各条各的孤立回声，标定出来的偏移只会把假锚做得更像真的。
    """
    deltas = [hit_page - printed_page for printed_page, hit_page in candidates]
    if not deltas:
        return None
    counts = Counter(deltas)
    top = max(counts.values())
    if top < TOC_MIN_OFFSET_SUPPORT:
        return None
    return min(d for d, c in counts.items() if c == top)


def _folded_prefix_hit(
    folded: Sequence[tuple[int, int, str]],
    entry: TocEntry,
    offset: int | None,
    slack: int,
) -> tuple[int, int] | None:
    """折公式壳后的前缀命中（`KA-61`）：只认窗口内最早的一条，取法与第一遍同一套。

    条目自己没有引导编号时，正文那一侧的编号也剥掉再比一次——目录把 `150.` 排进公式后那一行就只剩
    `$\frac{0}{0}$ 型不定式`，正文标题仍带着编号。有编号的条目绝不走这条（`2.10` 不能吃掉 `210`）。
    """
    if offset is None or entry.printed_page is None:
        return None
    key = _fold_inline(entry.name)
    if not key:
        return None
    target = entry.printed_page + offset
    bare = not _ENTRY_ORDINAL_SHAPE.match(unicodedata.normalize("NFKC", entry.name).lstrip())
    for page_no, block_index, text in folded:
        if abs(page_no - target) > slack:
            continue
        if text.startswith(key) or text.startswith("第" + key):
            return page_no, block_index
        if bare and re.sub(r"^[0-9]+[.．·]?", "", text).startswith(key):
            return page_no, block_index
    return None


def locate_entries(
    entries: Sequence[TocEntry], body_pages: Sequence[BlocksPage], *, slack: int = TOC_LOCATE_SLACK
) -> TocLocation:
    """条目序列 ＋ 正文页序列 → 本书偏移与逐条回定位读数（`entries` 同序）。

    命中判据＝**去空白前缀**：正文某块的归一文本以条目名的归一文本起头。前缀而不是子串，是因为目录
    行与正文里的章名行是同一起头形状，而子串会把「见第2章实数与极限第93页」这类引用句也算成命中。
    每条条目先收集全部命中，再只接受 `印刷页＋偏移 ± slack` 窗口内的那一枚，按 `(页号, 块下标)` 取最早
    的——窗口外的回声一律不算，因为假锚比缺锚难发现得多（`KA-26` 同轴：未定位是披露，不是失败）。

    第一遍查不中的行再折一遍公式壳（`KA-61`）：正文标题把符号排成行内公式、或目录那一行把编号排进
    公式，两形都只在**前缀位置**认，窗口与「取最早」的取法不变。

    `body_pages` 由调用方给出（不含目录页自身）；本函数不读盘、不查库，也不改任何页读数。
    """
    keyed: list[tuple[int, int, str]] = [
        (page.page, block.index, _squash(block.text or ""))
        for page in body_pages
        for block in page.blocks
        if (block.text or "").strip()
    ]
    folded: list[tuple[int, int, str]] = sorted(
        (
            (page.page, block.index, _fold_inline(block.text or ""))
            for page in body_pages
            for block in page.blocks
            if (block.text or "").strip()
        ),
        key=lambda item: (item[0], item[1]),
    )
    hits: list[list[tuple[int, int]]] = []
    for entry in entries:
        name = _squash(entry.name)
        hit_pages = (
            sorted({(page_no, block_index) for page_no, block_index, text in keyed if name and text.startswith(name)})
            if name
            else []
        )
        hits.append(hit_pages)

    offset = estimate_print_offset(
        [
            (entry.printed_page, page_no)
            for entry, hit_pages in zip(entries, hits, strict=True)
            if entry.printed_page is not None
            for page_no, _ in hit_pages
        ]
    )

    located: list[LocatedEntry] = []
    for entry, hit_pages in zip(entries, hits, strict=True):
        anchor: tuple[int, int] | None = None
        if offset is not None and entry.printed_page is not None:
            target = entry.printed_page + offset
            for page_no, block_index in hit_pages:
                if abs(page_no - target) <= slack:
                    anchor = (page_no, block_index)
                    break
        if anchor is None:
            anchor = _folded_prefix_hit(folded, entry, offset, slack)
        if anchor is None:
            located.append(LocatedEntry(entry=entry, page_no=None, block_index=entry.block_index, located=False))
        else:
            located.append(LocatedEntry(entry=entry, page_no=anchor[0], block_index=anchor[1], located=True))
    return TocLocation(offset=offset, entries=tuple(located))


#: 习题集目录腿的章行判据（PLAN-036 W9）：只认**章**（`第N章`／绪论等）。`篇／卷／部` 是分部行，
#: 单独判——b11 的目录同时写着「第一部分 一元函数」与「第一章 分析引论」，两者都按 `_EXPLICIT_CHAPTER`
#: 认的话分部行没有正文页可定位，会被当成未定位条目报出来；习题集目录只留一层章，分部行不进目录。
_CHAPTER_NUMBERED = re.compile(
    r"^(?:第?\s*[0-9０-９一二三四五六七八九十百]+\s*章|绪\s*论|导\s*论|绪\s*言)"
)
_PART_NUMBERED = re.compile(r"^(?:第?\s*[0-9０-９一二三四五六七八九十百]+\s*[篇卷部])")


def chapter_name_hint(name: str) -> bool:
    """名字是不是章级标题（`第N章`／绪论一类，PLAN-036 W9）：目录章行与 heading 回落共用这把尺。"""
    return bool(_CHAPTER_NUMBERED.match((name or "").lstrip()))


_CHAPTER_NO = re.compile(
    r"^(?:第\s*([0-9０-９一二三四五六七八九十百零〇两]+)\s*[章篇卷部]|Homework\s+(\d{1,4})\b)",
    re.IGNORECASE,
)
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100}


def _cn_int(text: str) -> int | None:
    """中文／全半角数字段 → 整数（`十一`＝11、`二十`＝20）；认不出给 `None`。"""
    folded = unicodedata.normalize("NFKC", text or "").strip()
    if folded.isdigit():
        return int(folded)
    total = current = 0
    for char in folded:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[char]
            current = 0
        else:
            return None
    return total + current


def chapter_number(name: str) -> int | None:
    """章名里的章号（`第 6 章…`／`第六章…`／`Homework 3`）；前言与附件这类名字给 `None`。

    配套提取靠它把习题集的章与教材的章对上（`第N章` 两边同一把尺），所以两端都从**名字**里读，
    不靠行序或页面位置。
    """
    matched = _CHAPTER_NO.match((name or "").lstrip())
    if matched is None:
        return None
    return _cn_int(matched.group(1) or matched.group(2) or "")


@dataclass(frozen=True, slots=True)
class ExerciseOutlineHint:
    """习题集目录的一条确定性读数：直接成行的章（或答案段）锚点，零模型调用。

    与 `TocEntry` 的区别是它已经**回定位过**：`page_no`／`block_index` 是正文页与块，不是目录页
    自身的坐标；`role`／`kind` 由调用方按行种类给（章＝knowledge，答案段＝reference/back_matter）。
    """

    name: str
    page_no: int
    block_index: int
    role: str = TOC_ROLE_KNOWLEDGE
    kind: str = ""


def exercise_chapter_hints(pages: Sequence[BlocksPage]) -> tuple[ExerciseOutlineHint, ...]:
    """习题集整本页块 → 已定位的章行读数（PLAN-036 W9，S3 的确定性落地路径）。

    取材与定锚复用目录条目腿（`toc_entries`＋`locate_entries`），区别只在**选哪些条目**：有章行时
    只取章（分部行一条不进），零章行时才退分部行。未定位的章按 `KA-33` 同一纪律丢行、不进读数——
    拿目录页当正文锚会把书自己的目录抄进目录。重名只留书序第一枚（与成行处的 D-11／D-12 同向）。
    """
    per_page = [(page.page, toc_entries(page)) for page in pages]
    toc_page_nos = {page_no for page_no, entries in per_page if entries}
    entries = [entry for _, page_entries in per_page for entry in page_entries]
    chapters = [
        entry
        for entry in entries
        if entry.printed_page is not None and chapter_name_hint(entry.name)
    ]
    if not chapters:
        chapters = [
            entry
            for entry in entries
            if entry.printed_page is not None and _PART_NUMBERED.match(entry.name.lstrip())
        ]
    unique: list[TocEntry] = []
    seen: set[str] = set()
    for entry in chapters:
        key = _squash(entry.name)
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(entry)
    if not unique:
        return ()
    location = locate_entries(unique, [page for page in pages if page.page not in toc_page_nos])
    return tuple(
        ExerciseOutlineHint(
            name=item.entry.name,
            page_no=item.page_no,
            block_index=item.block_index,
        )
        for item in location.entries
        if item.located and item.page_no is not None
    )


#: 答案段的运行页眉：整行只有「答案」两个字。正文里「下面给出答案」不算，目录条目「答案 …… 336」也不算。
_ANSWER_HEADING = re.compile(r"^答\s*案$")


def _answer_heading_block(page: BlocksPage) -> int | None:
    for block in page.blocks:
        for line in (block.text or "").splitlines():
            if _ANSWER_HEADING.match(line.strip()):
                return block.index
    return None


def answer_section_window(pages: Sequence[BlocksPage]) -> tuple[int, int, int] | None:
    """答案段的 `(起始页, 结束页, 页眉块)`；没有答案段时 `None`（PLAN-036 W11）。

    判据只认**连续运行页眉**：从第一枚整行「答案」起，逐个后继页收，遇到没有该页眉的页即断。b11 的
    53 页答案段（348~400）正是这一形；401 页起的人名对照表与译后记没有这条页眉，自然被挡在窗外。
    目录页上的「答案」条目不算页眉（目录页整页排除），正文里提到「答案」的句子也不算（整行不等）。
    """
    toc_page_nos = {page.page for page in pages if toc_entries(page)}
    start: int | None = None
    end: int | None = None
    start_block = 0
    for page in sorted(pages, key=lambda item: item.page):
        if page.page in toc_page_nos:
            continue
        block_index = _answer_heading_block(page)
        if block_index is None:
            if start is not None:
                break
            continue
        if start is None:
            start, end, start_block = page.page, page.page, block_index
        elif end is not None and page.page == end + 1:
            end = page.page
        else:
            break
    if start is None:
        return None
    return (start, end if end is not None else start, start_block)


#: 容错比对（`KA-49`）的两枚下限：近似比值与折叠名长度。短名（`数 e`／`习题`）没有鉴别力，
#: 放开近似等于让任意两枚短名互认——近似腿只在长名上生效，短名只走整行同形。
TOC_TOLERANT_SIMILARITY = 0.85
TOC_TOLERANT_MIN_CHARS = 6

#: 数学宏壳（`KA-49`）：`\\mathscr{L}^2` 与 `\\mathcal{L}^2` 是同一枚符号的两种写法，壳里那枚才是名字。
#: 只剥「宏名＋花括号」这一形，花括号里不再嵌套（嵌套由不动点循环消化）。
_MACRO_SHELL = re.compile(
    r"\\(?:mathscr|mathcal|mathfrak|mathbf|mathrm|mathit|mathbb|mathsf|boldsymbol|pmb"
    r"|text|textrm|textit|textbf|operatorname|bb)\s*\{([^{}]*)\}"
)

#: 希腊宏名折其字符：目录把 `$\\Gamma$ 函数` 抄成 `Γ函数` 是同一句话的两种排法（真机 `afkp-1c8c7a0bed99`）。
_GREEK_LETTERS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π", "rho": "ρ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "ϕ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Rho": "Ρ",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_GREEK_MACRO = re.compile(r"\\([A-Za-z]+)")


def _fold_math(text: str) -> str:
    """容错比对口径＝NFKC＋剥数学宏壳（到不动点）＋希腊宏名折字符＋只留字母与数字＋小写。

    与 `_squash` 的分工是刻意的：那一把是**第一遍**的口径（去空白前缀，编号里的点一律不动，
    `2.10` 与 `210` 必须是两个名字），这一把只在第一遍查不中时启用，把排版壳与 OCR 掉字折成同一串。
    两把分开，旧读数才逐字不变（`KA-49` 的边界）。

    代价是这一把没有鉴别力可言：标点全删后 `矩的计算法。质心` 与 `矩的计算法质心` 同串，所以它
    只能当**第二遍**用，且命中仍受窗口约束、短名仍不许近似。
    """
    folded = unicodedata.normalize("NFKC", text or "")
    for _ in range(4):
        stripped = _MACRO_SHELL.sub(r"\1", folded)
        if stripped == folded:
            break
        folded = stripped
    folded = _GREEK_MACRO.sub(lambda match: _GREEK_LETTERS.get(match.group(1), match.group(1)), folded)
    return "".join(char for char in folded if char.isalnum()).casefold()


#: 前缀第二遍的口径（`KA-61`）：与 `_fold_math` 的分工是刻意的——那一把给整行同形／近似，标点全删；
#: 这一把只做前缀，编号里的句点必须留下（`2.10` 与 `210` 是两条目录，折成同串就互相顶替）。
_INLINE_MACRO = re.compile(r"\\[A-Za-z]+")

#: 行内公式里的函数名是名字的一部分（`$\sin x$` 折成 `sinx`），其余控制词只是排版壳：`\frac`／
#: `\sqrt`／`\quad` 一律删掉，`\pi`／`\Gamma` 折字符（同 `_fold_math` 的希腊表）。
_INLINE_FUNCTIONS = frozenset(
    {
        "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "arccot",
        "sinh", "cosh", "tanh", "coth", "sh", "ch", "th", "tg", "ctg",
        "log", "lg", "ln", "exp", "lim", "max", "min", "sup", "inf", "det", "dim", "arg",
    }
)

#: 条目名自己的引导编号形状（`161.`／`3 `）：有编号的条目不许再剥正文那一侧的编号，否则 `2.10`
#: 会吃掉正文的 `210`（同 `_fold_math` 的边界，这是它的第二道保险）。
_ENTRY_ORDINAL_SHAPE = re.compile(r"^[0-9]+(?:[ \t.．·]|$)")


def _fold_inline(text: str) -> str:
    """前缀第二遍的折叠（`KA-61`）：NFKC＋剥宏壳＋函数名留字＋希腊折字符，只留字母数字与编号点。

    真机几形合起来只解决一件事：目录那一行的名字与正文标题行的「同一句话」被排版壳隔开——
    `161. $n$ 维算术空间`（正文公式化）、`$\\frac{0}{0}$ 型不定式`（目录那一行把编号排进公式、OCR
    丢掉编号）、`展开 $\\sin x$`（函数名在公式里）、`数 $\\pi$ 的计算`（希腊符号在公式里）。
    都只在前缀位置上认，且仍受偏移窗口约束：窗口外的回声照旧不算。
    """
    folded = unicodedata.normalize("NFKC", text or "")
    for _ in range(4):
        stripped = _MACRO_SHELL.sub(r"\1", folded)
        if stripped == folded:
            break
        folded = stripped
    folded = _INLINE_MACRO.sub(_inline_macro_word, folded)
    return "".join(char for char in folded if char.isalnum() or char == ".").casefold()


def _inline_macro_word(match: re.Match[str]) -> str:
    name = match.group(0)[1:]
    if name in _GREEK_LETTERS:
        return _GREEK_LETTERS[name]
    return name if name in _INLINE_FUNCTIONS else ""


def _title_line(text: str) -> str:
    """一个块的候选标题行＝块里第一行非空文本的原样（不折、不清洗，`KA-50` 取的就是这一行）。"""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


#: 索引行（`名字, 5`／`名字，5`）是「名字＋页码」，不是正文标题：容错腿把目录条目
#: `5. 阿基米德公理` 认成索引行（真机 b12 第 531 页），`KA-50` 再拿索引行当净名，
#: 行名就成了「阿基米德公理, 5」。标题行从不以页码收尾，这一形一律不进候选面。
_INDEX_LINE = re.compile(r"[,，]\s*[0-9]{1,4}\s*$")


@dataclass(frozen=True, slots=True)
class BodyReading:
    """容错比对命中的一行正文：`name` 是正文那一行的原文，坐标是 `(页号, 块序)`。

    跨块拼接的那一枚 `name` 以一枚空格相连、`block_index` 取**第一块**（标题起头那块），
    两处都是 `KA-49`／`KA-50` 写在条文里的取法。
    """

    name: str
    page_no: int
    block_index: int


class BodyNameIndex:
    """正文块首行 → 折叠键的索引（`KA-49` 的输入面）：整本扫一遍，逐条查询走字典。

    候选面只有两件事：非家具块（`FURNITURE_TYPES` 三型不进候选，书眉里的同名行是回声）与每块的
    首行（块中间那一行是正文句子，不是这一节的题目）。同页相邻两块的首行再拼一枚候选，那是产物里
    「标题折两块」的常态。索引行（`名字, 5`）也不是标题，不进候选面。索引一次建好、逐条查询零扫描——目录腿的条目数是百枚级，逐条重扫整本就
    是万次级。
    """

    def __init__(self, pages: Sequence[BlocksPage]) -> None:
        exact: dict[str, list[BodyReading]] = {}
        singles: dict[str, list[BodyReading]] = {}
        for page in pages:
            titles: list[tuple[int, str]] = []
            for block in page.blocks:
                if block.type in FURNITURE_TYPES:
                    continue
                line = _title_line(block.text or "")
                if line and not _INDEX_LINE.search(line):
                    titles.append((block.index, line))
            for position, (block_index, line) in enumerate(titles):
                reading = BodyReading(name=line, page_no=page.page, block_index=block_index)
                exact.setdefault(_fold_math(line), []).append(reading)
                singles.setdefault(_fold_math(line), []).append(reading)
                if position + 1 >= len(titles):
                    continue
                next_index, next_line = titles[position + 1]
                if next_index != block_index + 1:
                    continue
                joined = BodyReading(
                    name=f"{line} {next_line}", page_no=page.page, block_index=block_index
                )
                exact.setdefault(_fold_math(joined.name), []).append(joined)
        self._exact = exact
        #: 近似腿的候选桶＝**单块首行**的两把下标（首字符与尾字符）：OCR 掉字多半掉在中间，两头仍能
        #: 对上号。拼接候选不进这一把——标题后面接着的那一行是正文句子，拿它去近似等于让任何一枚长名
        #: 都能撞上「标题＋一句正文」，近似腿的鉴别力就没了。
        self._buckets: dict[str, list[tuple[str, BodyReading]]] = {}
        for key, readings in singles.items():
            if len(key) < TOC_TOLERANT_MIN_CHARS:
                continue
            for reading in readings:
                for bucket_key in {key[0], key[-1]}:
                    self._buckets.setdefault(bucket_key, []).append((key, reading))

    def find(self, name: str) -> tuple[BodyReading, ...]:
        """条目名 → 正文里的候选行序列（按 `(页号, 块序)` 升序）；三条命中形状见 `KA-49`。

        整行同形（单块或相邻两块拼接）与近似都算候选；近似只在折叠名长 ≥`TOC_TOLERANT_MIN_CHARS`
        且长度差在一成以内时判，比值下限 `TOC_TOLERANT_SIMILARITY`，且只在单块首行上判。**前缀不算
        候选**：那是段落起头的形状，认了就会把题干抄成名字。
        """
        folded = _fold_math(name)
        if not folded:
            return ()
        found: list[BodyReading] = list(self._exact.get(folded, ()))
        if len(folded) >= TOC_TOLERANT_MIN_CHARS:
            seen = {(item.page_no, item.block_index, item.name) for item in found}
            for key, reading in self._buckets.get(folded[0], []) + self._buckets.get(folded[-1], []):
                marker = (reading.page_no, reading.block_index, reading.name)
                if key == folded or marker in seen:
                    continue
                seen.add(marker)
                if abs(len(key) - len(folded)) > max(len(key), len(folded)) * 0.1:
                    continue
                if SequenceMatcher(None, folded, key, autojunk=False).ratio() >= TOC_TOLERANT_SIMILARITY:
                    found.append(reading)
        return tuple(sorted(found, key=lambda item: (item.page_no, item.block_index)))


def pick_in_window(
    readings: Sequence[BodyReading],
    *,
    printed_page: int | None,
    offset: int | None,
    slack: int = TOC_LOCATE_SLACK,
) -> BodyReading | None:
    """候选行里只接受 `印刷页＋偏移 ± slack` 窗口内的最早一枚（`KA-49` 的位置腿）。

    与第一遍同一条约束：窗口外的回声一律不算，假锚比缺锚难发现。偏移标定不出或条目没有印刷页码
    时不造锚——那时命中的是回声而不是这一节。
    """
    if offset is None or printed_page is None:
        return None
    target = printed_page + offset
    for reading in sorted(readings, key=lambda item: (item.page_no, item.block_index)):
        if abs(reading.page_no - target) <= slack:
            return reading
    return None


def drop_unlocated_toc_pages(
    items: Sequence[tuple[int, LocatedEntry]], offset: int | None
) -> tuple[tuple[tuple[int, LocatedEntry], ...], tuple[int, ...]]:
    """页级复核（`KA-33`）：偏移标定成功时，某页条目**过半**回定位不中 ⇒ 该页整页不算目录页。

    `items` ＝ `(目录页号, 回定位读数)`，与条目同序。符号表／索引页与目录页的行形状完全相同（一行一条、
    行末带页码），偏移标定得出来时它们的「页码」与本书正文无关，条目几乎全是未定位——不整页剔掉，
    每一条都会变成一枚锚在符号表页上的目录行。

    偏移标定不出时一律不剔：那时「全部未定位」是标定失败的读数，不是「这页不是目录」的证据。
    返回保序的剩余条目与按页号升序的剔除页号。
    """
    if offset is None:
        return tuple(items), ()
    grouped: dict[int, list[bool]] = {}
    for page_no, item in items:
        grouped.setdefault(page_no, []).append(item.located)
    dropped = tuple(
        sorted(
            page_no
            for page_no, flags in grouped.items()
            if sum(1 for located in flags if not located) * 2 > len(flags)
        )
    )
    if not dropped:
        return tuple(items), ()
    gone = set(dropped)
    return tuple((page_no, item) for page_no, item in items if page_no not in gone), dropped


def page_sample_text(page: BlocksPage, *, max_chars: int = TOC_PAGE_SAMPLE_MAX_CHARS) -> str:
    """一页产物 → 判页腿的一页样本：逐块带类型标注，整页截到 `max_chars`（`KA-37` 的输入封顶）。

    标注块类型是给模型「这一页是清一色段落还是带标题」的形状线索；截断只发生在样本腿，整理腿吃的是
    该页段落块原文（不截），两腿的输入面分别是「认页」和「抄行」，不是一件事。

    封顶按**预算内截字**而不是整块丢弃：真机一枚段落块就是 500 字符级，整块丢会让样本空成串，
    判页腿对着空文本只能答「没有目录页」——那本书就被整本推回确定性条目腿了。
    """
    lines: list[str] = []
    budget = max_chars
    for block in page.blocks:
        text = (block.text or "").strip()
        if not text:
            continue
        line = f"[{block.type.value}] {text}"
        if lines:
            budget -= 1  # 连接用的换行符同样算进封顶
        if budget <= 0:
            break
        lines.append(line if len(line) <= budget else line[:budget])
        budget -= len(lines[-1])
    return "\n".join(lines)[:max_chars]


#: 目录页标记（P-1）：独立成行的「目录／Contents」，续页可带括号后缀（`目录（续）`）。
_TOC_MARKER = re.compile(r"^(?:目\s*录|(?:table\s+of\s+)?contents?)(?:\s*\([^)]*\))?$", re.IGNORECASE)


def is_toc_marker_line(text: str) -> bool:
    """一行是不是目录页的标记行（P-1）：整行只写「目录／Contents」，正文句子里提到的「目录」不算。"""
    folded = unicodedata.normalize("NFKC", text or "").strip()
    return _TOC_MARKER.fullmatch(folded) is not None


def toc_marker_pages(
    blocks: Sequence[BlocksPage], *, front_limit: int = TOC_PAGE_SCAN_LIMIT
) -> tuple[int, ...]:
    """逐页判页腿的候选面（P-1）：前 `front_limit` 页有标记就只认这几页，否则按页序扫全书。

    这只是确定性文本扫描（零模型调用），只决定「从哪一页开始逐页判」，不决定目录区间——
    区间由逐页判定在第一张非目录页处收住。
    """

    def marked(page: BlocksPage) -> bool:
        return any(
            is_toc_marker_line(line)
            for block in page.blocks
            if block.type in _TOC_TEXT_TYPES
            for line in (block.text or "").splitlines()
        )

    front = [page.page for page in blocks[:front_limit] if marked(page)]
    if front:
        return tuple(front)
    return tuple(page.page for page in blocks if marked(page))


def page_full_text(page: BlocksPage) -> str:
    """整页原文（P-2）：逐块带类型标注、**不截断**——判「这一页是不是目录」要看整页。"""
    lines: list[str] = []
    for block in page.blocks:
        text = (block.text or "").strip()
        if text:
            lines.append(f"[{block.type.value}] {text}")
    return "\n".join(lines)


def reference_role_for_name(name: str) -> tuple[str, str]:
    """条目名 → `(toc_role, toc_kind)`（`KA-39` 的回落腿）：认不出类别的行就是知识点行。

    只在模型判页不成时使用；正腿的 role/kind 由判页读数直接给，不靠名字猜。
    """
    folded = (name or "").strip().casefold()
    if not folded:
        return TOC_ROLE_KNOWLEDGE, ""
    # 编号起头的行是正文条目：`1. 前言`／`§1. 引言` 是节，不是书前件（b12／b13 真机各一枚）。
    if _ENTRY_NUMBER_PREFIX.match(folded):
        return TOC_ROLE_KNOWLEDGE, ""
    for kind, shape in _REFERENCE_NAME_SHAPES:
        if shape.search(folded):
            return TOC_ROLE_REFERENCE, kind
    return TOC_ROLE_KNOWLEDGE, ""


#: 习题段标签（`KA-46`）：整个块就是「习题／练习／Exercises／Problems ＋可选编号」。编号认书里真在用的
#: 写法（`1`／`1.1`／`1-3`／`三`），全角数字先经 NFKC 折过。只认整块：行首命中不算——`习题解答` 是答案侧，
#: `第一章 习题课` 是章名，带题干的段落是题目本身。
_EXERCISE_LABEL = re.compile(
    r"^(?:习\s*题|练\s*习|exercises?|problems?)"
    r"(?:[\s第]*(?:[0-9]{1,3}(?:[.\-—–~～][0-9]{1,3}){0,2}|[一二三四五六七八九十百]{1,4}))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ExerciseEntry:
    """一页里的一枚习题段标签：原样去空白后的名字与它所在块的数组下标（锚点要指回这一块）。"""

    name: str
    block_index: int


def is_exercise_label(text: str) -> bool:
    """一段文字是不是「章末习题段的标题」（`KA-46`）：整块短标签＋可选编号。

    宁缺毋滥：认错一枚会在目录里多出一行假结构，而漏认只回到改判前「靠模型偶发抄行」的现状。
    """
    folded = unicodedata.normalize("NFKC", text or "").strip()
    if not folded:
        return False
    return _EXERCISE_LABEL.match(folded) is not None


def exercise_entries(page: BlocksPage) -> tuple[ExerciseEntry, ...]:
    """一页产物 → 本页的习题段标签（按块序）；没有就出空元组。

    块类型不设限：真机里这一段既可能是 `heading`（书有层级）也可能是 `paragraph`（整本零标题的老教材），
    身份只由名字形状给。目录页的排除不在这里——一页是不是目录页要全书偏移才判得出来，那是编排层的事。
    """
    return tuple(
        ExerciseEntry(name=(block.text or "").strip(), block_index=block.index)
        for block in page.blocks
        if is_exercise_label(block.text or "")
    )


#: 行首题号闭集（`KA-53`）：书自己写在题首的六形，全角先经 NFKC 折过再判。点号形额外要求分隔符
#: 后面不是数字——`1.2 函数极限` 是节号、`3.14` 是小数，都不是题号；范围形要求后面是行末或分隔符，
#: 免得把 `2017-01-01` 一类日期读成题号（四条题号共用一个函数，W9 的习题集腿也走它）。
_EXERCISE_ITEM = re.compile(
    r"^(?:"
    r"【(?P<lenticular>\d{1,4})】"
    r"|\((?P<paren>\d{1,4})\)"
    r"|(?P<range_first>\d{1,4})\s*[-–—~～]\s*(?P<range_second>\d{1,4})(?=$|[.、\s])"
    r"|(?P<dotted>\d{1,4})[.、](?!\d)"
    r")"
)


@dataclass(frozen=True, slots=True)
class ExerciseItemEntry:
    """一段习题正文里的一枚题号：`index` 指输入行序列的下标（锚点靠它回指页与块）。

    `item_no` 是书自己写的题号（只做空白与全半角折叠，范围形保留两端的数）；`order_key` 只服务
    单调序，取题号里的第一枚整数——`10-12` 排在 `9.` 之后，与书里的先后一致。
    """

    index: int
    item_no: str
    order_key: int


def exercise_item_entries(lines: Sequence[str]) -> tuple[ExerciseItemEntry, ...]:
    """一段正文的行序列 → 题号行（`KA-53`）：闭集形状 ＋ 同父单调序，其余行一律视作正文。

    边界只由规则给、模型不参与分题：同一父行下只有题号严格大于上一枚已接受的才开新题。
    编号回退（小问重新从 `(1)` 起、重复题号）不算新题——那正是「(1)(2) 小问不是题」的判据来源。
    跨段重置不在这里：调用方按每个习题段各调一次，`order_key` 的比较域自然收在段内。
    识别不出时返回空元组，这是披露不是失败（`KA-53`）。
    """
    entries: list[ExerciseItemEntry] = []
    last_key = 0
    for index, raw in enumerate(lines):
        folded = unicodedata.normalize("NFKC", raw or "").strip()
        if not folded:
            continue
        match = _EXERCISE_ITEM.match(folded)
        if match is None:
            continue
        first = (
            match.group("lenticular")
            or match.group("paren")
            or match.group("range_first")
            or match.group("dotted")
        )
        order_key = int(first)
        if order_key <= last_key:
            continue
        last_key = order_key
        entries.append(
            ExerciseItemEntry(
                index=index,
                item_no=re.sub(r"\s+", "", match.group(0)),
                order_key=order_key,
            )
        )
    return tuple(entries)


def node_type_of(
    *,
    depth: int | None,
    toc_role: str,
    toc_kind: str,
    ancestor_kinds: Sequence[str] = (),
) -> str:
    """一行目录的四值类型（`KA-44`）：按序首中，只派生不落列。

    `exercise` 排在最前：习题的正文里也有「1.2 习题 3」这类子行，子行的身份位可能为空，靠祖先那一枚
    `toc_kind` 才认得出来。`toc_kind='exercise'` 的目录行在 `KA-39` 里同时带 `toc_role='reference'`
    （不抽点），所以不能先判 `reference`，否则习题会被标成「其他」。其余 `reference` 行（符号表、书目、
    附录、前后记）标「其他」。知识点行按层级 1 分成章、2 及以上分成点——探到第三层的行在
    `KA-6` 就不成行，`depth` 只有 1／2 两值。单行看不到子行：`KA-68` 的「无子行的一级行记其他」
    只在 `node_types_of` 那一趟补上，批量读面与取材侧用的都是那一份。
    """
    if toc_kind == "exercise" or "exercise" in ancestor_kinds:
        return NODE_TYPE_EXERCISE
    if toc_role == TOC_ROLE_REFERENCE:
        return NODE_TYPE_OTHER
    return NODE_TYPE_CHAPTER if (depth or 0) <= 1 else NODE_TYPE_POINT


def node_types_of(rows: Iterable[object], *, exercise_book: bool = False) -> dict[str, str]:
    """一批目录行 → 每行的四值类型（`KA-44`／`KA-68`）。

    认的是三枚只读属性 `node_id`／`parent_id`／`meta` 与两枚 `depth`／`node_kind`：`AfKp` 与点腿的
    `KpNodeRow` 都长得这个样，读面与取材侧因此共用同一份派生——两处各写一次类型判，屏上的标签迟早
    与批次里实际走的腿分叉。父不在这一批里时只按本行的身份位判（不猜）。

    第二趟（`KA-68`）：基础类型是章的行**只有真有可装的东西时**才是章。其下没有任何知识点行（或
    题行）的一级知识行记「其他」——预备知识、附录这类书前件／书后件没有子行，章腿对它们零调用，
    屏上也不该发章级总结与导读的入口。判据是结构而不是名字：按名字认（「第X章」）会把 b12 的
    「绪论 实数」判错，它有四个知识点子行。算作子行的是派生类型 `point` 的知识点行与
    `node_kind='point'` 的题行（习题集的章只装题行）；拆出的习题段行（`toc_kind='exercise'` 的
    目录行）不算——附录 A 的唯一子行就是这样一枚。

    `exercise_book=True` 时第二趟整趟跳过（PLAN-036 S4，用户裁决 2026-11-13）：习题集的目录只有
    一层章、题行是后一批才落的，按教材口径先判「无子行」会让整本目录在题行落库前被一律降成「其他」
    ——目录批自己看不见章，习题受理门也就永远放不开。章的判定对这本书不靠子行，靠书种类。
    """
    row_list = list(rows)
    by_id = {row.node_id: row for row in row_list}
    types: dict[str, str] = {}
    for row in row_list:
        meta = getattr(row, "meta", None) or {}
        ancestor_kinds: list[str] = []
        seen = {row.node_id}
        parent_id = getattr(row, "parent_id", None)
        while parent_id and parent_id not in seen:
            parent = by_id.get(parent_id)
            if parent is None:
                break
            seen.add(parent.node_id)
            ancestor_kinds.append(str((getattr(parent, "meta", None) or {}).get("toc_kind") or ""))
            parent_id = getattr(parent, "parent_id", None)
        types[row.node_id] = node_type_of(
            depth=getattr(row, "depth", None),
            toc_role=str(meta.get("toc_role") or TOC_ROLE_KNOWLEDGE),
            toc_kind=str(meta.get("toc_kind") or ""),
            ancestor_kinds=tuple(ancestor_kinds),
        )
    if exercise_book:
        return types
    folders: set[str] = set()
    for row in row_list:
        holds_content = types.get(row.node_id) == NODE_TYPE_POINT or (
            getattr(row, "node_kind", None) == "point"
        )
        if not holds_content:
            continue
        seen = {row.node_id}
        parent_id = getattr(row, "parent_id", None)
        while parent_id and parent_id not in seen and parent_id in by_id:
            seen.add(parent_id)
            folders.add(parent_id)
            parent_id = getattr(by_id[parent_id], "parent_id", None)
    for row in row_list:
        if types.get(row.node_id) == NODE_TYPE_CHAPTER and row.node_id not in folders:
            types[row.node_id] = NODE_TYPE_OTHER
    return types


def anchor_block_for_name(page: BlocksPage, name: str) -> int:
    """一页里指认一条条目名落在哪一块：优先「含该名」的那一块，否则页首块（`KA-38` 的锚块位）。

    目录页里条目名出现在段落块内部（不是行首），所以这里用**包含**而不是前缀命中——前缀口径只服务于
    正文回定位（`locate_entries`），两处口径不同是刻意的：这里要的是「锚指到哪一块能被读出来」。
    """
    squashed = _squash(name)
    if squashed:
        for block in page.blocks:
            if squashed in _squash(block.text or ""):
                return block.index
    return page.blocks[0].index if page.blocks else 0


# --------------------------------------------------------------------------- 内容链（KA-63~KA-65）

#: 点腿单次输入的正文上限（`KA-64`）：按字符近似、不引分词器。本机槽位加载 40,960 token，9B 带思维链
#: 时隐式输出不可控，10,000 汉字加提示词仍留余量。
CONTENT_INPUT_BUDGET_CHARS = 10000
#: 相邻同级小节的合批下限（`KA-64`）：各自低于这么多字才值得并进一次调用；合后仍不得超预算。
CONTENT_MERGE_FLOOR_CHARS = 1500

#: 种类审校的改判理由闭集（`KA-63`）：只认「行名就是整块习题标签」这一条。
KIND_REVIEW_EXERCISE_LABEL = "exercise_label"


@dataclass(frozen=True, slots=True)
class KindReviewRow:
    """种类审校的一枚输入行（`KA-63`）：本批区间内一条在册目录行的只读快照。"""

    node_id: str
    parent_id: str | None
    name: str
    node_type: str
    page_start: int
    page_end: int


@dataclass(frozen=True, slots=True)
class KindSplit:
    """审校拆出的一枚习题行：父＝所属章行，锚点＝那枚标签块的（页，块序）。"""

    parent_id: str
    name: str
    page: int
    block_index: int


@dataclass(frozen=True, slots=True)
class KindFlag:
    """审校点名的一行：行名本身是习题标签，不发点腿。`reason` 只取理由闭集。"""

    node_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class KindReview:
    """一次种类审校的读数：点名清单、拆分行清单与被审行数（`type_review_rows` 的取值）。"""

    flags: tuple[KindFlag, ...]
    splits: tuple[KindSplit, ...]
    rows: int


def kind_review(
    rows: Sequence[KindReviewRow], pages: Sequence[BlocksPage]
) -> KindReview:
    """`KA-63`：在册目录行 ＋ 本批页块 → 点名清单与拆分行清单；零模型调用、零写库。

    两条判据：① 行名是整块习题标签（`is_exercise_label`）⇒ 点名、不发点腿；② 行的取证区间里出现
    整块标签 ⇒ 拆成父＝章节行的并列习题行。章行自己也扫区间：章尾习题不属于任何点，只有章区间盖得住。
    拆分行按（父, 归一名字, 锚页）去重——章区间与点区间重叠时同一枚标签只出一行；已在册的行
    （同页同名）不再造第二枚，这条让 ① 点名的行不会同时以拆分行身份出现。
    """
    by_page = {page.page: page for page in pages}
    existing = {(row.page_start, _squash(row.name)) for row in rows}
    flags: list[KindFlag] = []
    splits: list[KindSplit] = []
    seen: set[tuple[str, str, int]] = set()
    for row in rows:
        if row.node_type not in (NODE_TYPE_CHAPTER, NODE_TYPE_POINT):
            continue
        if is_exercise_label(row.name):
            flags.append(KindFlag(node_id=row.node_id, reason=KIND_REVIEW_EXERCISE_LABEL))
            continue
        owner = row.node_id if row.node_type == NODE_TYPE_CHAPTER else row.parent_id
        if not owner:
            continue
        for page_no in range(row.page_start, row.page_end + 1):
            page = by_page.get(page_no)
            if page is None:
                continue
            for block in sorted(page.blocks, key=lambda item: item.index):
                text = (block.text or "").strip()
                if not is_exercise_label(text):
                    continue
                key = (owner, _squash(text), page_no)
                if key in seen or (page_no, _squash(text)) in existing:
                    continue
                seen.add(key)
                splits.append(
                    KindSplit(
                        parent_id=owner,
                        name=text,
                        page=page_no,
                        block_index=block.index,
                    )
                )
    return KindReview(flags=tuple(flags), splits=tuple(splits), rows=len(rows))


@dataclass(frozen=True, slots=True)
class MaterialBlock:
    """取材的一枚块：坐标（页，块序）＋渲染后的正文。切窗只切 `text`，坐标跟着走。"""

    page: int
    block_index: int
    block_type: str
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


def select_material_blocks(
    pages: Sequence[BlocksPage],
    *,
    page_start: int,
    page_end: int,
    exclude_pages: Iterable[int] = (),
    start_block: int | None = None,
    end_block: int | None = None,
) -> tuple[MaterialBlock, ...]:
    """`KA-64`：区间内的页块 → 取材块序列（页序＋块序，家具与排除页零入，按页级渲染器出文）。

    `exclude_pages` 给已判目录页（`KA-29` 的剔除在编排层已做，这里收编排层算出的页号）。
    `start_block`／`end_block` 是两端的块界（含端点）：只作用在起点页／终点页——本节正文延续到
    下一节起始页时，终点页按块界只收到下一标题块之前，中间页整页收。
    """
    skipped = {page_no for page_no in exclude_pages}
    blocks: list[MaterialBlock] = []
    for page in sorted(pages, key=lambda item: item.page):
        if page.page < page_start or page.page > page_end or page.page in skipped:
            continue
        low = start_block if page.page == page_start else None
        high = end_block if page.page == page_end else None
        for block in sorted(page.blocks, key=lambda item: item.index):
            if low is not None and block.index < low:
                continue
            if high is not None and block.index > high:
                continue
            if block.type in FURNITURE_TYPES:
                continue
            text = blocks_to_markdown([block]).strip()
            if not text:
                continue
            blocks.append(
                MaterialBlock(
                    page=page.page,
                    block_index=block.index,
                    block_type=block.type.value,
                    text=text,
                )
            )
    return tuple(blocks)


def chapter_material_blocks(
    pages: Sequence[BlocksPage],
    *,
    page_start: int,
    first_child_page: int | None,
    first_child_block: int | None = None,
    exclude_pages: Iterable[int] = (),
) -> tuple[MaterialBlock, ...]:
    """`KA-65`：章前材料＝章锚页到首个点子行锚页之前的正文块。

    首点更后页时取 `[章锚页, 首点页)` 整段；章与首点同页时只取该页首点标题块之前的块——
    章标题与章前言归章，点从自己的标题块起重读，两边不重叠。无子点章、脏数据（子点页早于章页、
    同页却判不出切块位置）一律回空表：章不拿自己的标题页冒充材料，也不取走点的正文。
    """
    if first_child_page is None or first_child_page < page_start:
        return ()
    if first_child_page > page_start:
        return select_material_blocks(
            pages,
            page_start=page_start,
            page_end=first_child_page - 1,
            exclude_pages=exclude_pages,
        )
    if first_child_block is None:
        return ()
    return tuple(
        block
        for block in select_material_blocks(
            pages, page_start=page_start, page_end=page_start, exclude_pages=exclude_pages
        )
        if block.block_index < first_child_block
    )


@dataclass(frozen=True, slots=True)
class PointSection:
    """一笔点调用的一节：节点身份＋屏上序标题＋本节全量材料块。"""

    node_id: str
    parent_id: str | None
    title: str
    blocks: tuple[MaterialBlock, ...]


@dataclass(frozen=True, slots=True)
class PointCall:
    """计划里的一笔调用：合并组时 `sections` 多于一项，`blocks` 是本笔实际送出的窗口块。"""

    sections: tuple[PointSection, ...]
    blocks: tuple[MaterialBlock, ...]

    @property
    def node_ids(self) -> tuple[str, ...]:
        return tuple(section.node_id for section in self.sections)

    @property
    def chars(self) -> int:
        return _blocks_chars(self.blocks)

    @property
    def merged(self) -> bool:
        return len(self.sections) > 1


#: 句界（`KA-64` 的退路）：单个块自身超预算时才按它切，且切点留在标点之后。
_SENTENCE_ENDS = "。！？!?；;\n"


def _blocks_chars(blocks: Sequence[MaterialBlock]) -> int:
    """材料字符数：块间以空行连接，连接符算进预算。"""
    if not blocks:
        return 0
    return sum(block.chars for block in blocks) + 2 * (len(blocks) - 1)


def _split_oversized(block: MaterialBlock, budget: int) -> list[MaterialBlock]:
    """单块超预算 → 按句界切到预算内；句界落不进窗口时硬切（不丢字）。"""
    text = block.text
    pieces: list[MaterialBlock] = []
    start = 0
    while len(text) - start > budget:
        window = text[start : start + budget]
        cut = max(window.rfind(mark) for mark in _SENTENCE_ENDS)
        end = start + cut + 1 if cut > 0 else start + budget
        pieces.append(
            MaterialBlock(
                page=block.page,
                block_index=block.block_index,
                block_type=block.block_type,
                text=text[start:end],
            )
        )
        start = end
    tail = text[start:]
    if tail:
        pieces.append(
            MaterialBlock(
                page=block.page,
                block_index=block.block_index,
                block_type=block.block_type,
                text=tail,
            )
        )
    return [piece for piece in pieces if piece.text.strip()]


def _window_pieces(
    blocks: Sequence[MaterialBlock], budget: int
) -> list[MaterialBlock]:
    pieces: list[MaterialBlock] = []
    for block in blocks:
        if block.chars <= budget:
            pieces.append(block)
        else:
            pieces.extend(_split_oversized(block, budget))
    return pieces


def plan_point_calls(
    sections: Sequence[PointSection],
    *,
    budget: int = CONTENT_INPUT_BUDGET_CHARS,
    floor: int = CONTENT_MERGE_FLOOR_CHARS,
) -> tuple[PointCall, ...]:
    """`KA-64`：节点材料 → 发送计划（按屏上序）。

    每个节点先按块边界切窗（单块超预算按句界切），超预算的节点独占多次调用；只产出一窗且正文低于
    合批下限的相邻同级节点，在合并后不超预算时并成一笔调用。低于下限的节点若左右都合不上，仍单独
    发一笔——合批是省固定开销，不是丢节点的理由。
    """
    planned: list[tuple[PointSection, list[PointCall]]] = []
    for section in sections:
        windows: list[PointCall] = []
        current: list[MaterialBlock] = []
        for block in _window_pieces(section.blocks, budget):
            if current and _blocks_chars([*current, block]) > budget:
                windows.append(PointCall(sections=(section,), blocks=tuple(current)))
                current = []
            current.append(block)
        windows.append(PointCall(sections=(section,), blocks=tuple(current)))
        planned.append((section, windows))

    calls: list[PointCall] = []
    mergeable: list[bool] = []
    for section, windows in planned:
        if len(windows) != 1:
            calls.extend(windows)
            mergeable.extend([False] * len(windows))
            continue
        call = windows[0]
        can_merge = bool(call.blocks) and call.chars < floor
        if (
            can_merge
            and calls
            and mergeable[-1]
            and calls[-1].sections[0].parent_id == section.parent_id
            and calls[-1].chars + 2 + call.chars <= budget
        ):
            previous = calls[-1]
            calls[-1] = PointCall(
                sections=(*previous.sections, section),
                blocks=(*previous.blocks, *call.blocks),
            )
            continue
        calls.append(call)
        mergeable.append(can_merge)
    return tuple(calls)
