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
from dataclasses import dataclass
from difflib import SequenceMatcher

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
    ("index", re.compile(r"^\s*索\s*引|^\s*index", re.IGNORECASE)),
    ("back_matter", re.compile(r"^\s*后\s*记|^\s*跋")),
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
_CHAPTER_SHAPE = re.compile(
    r"^(?:第\s*[0-9０-９一二三四五六七八九十百]+\s*[章节篇卷部]|[0-9]+[ \t.．·](?![0-9]))"
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
    """剔掉名字末尾的引导符串；串里没有强引导符时一字不动（`RIEMANN-STIEI.` 的句点是名字的一部分）。"""
    text = (name or "").strip()
    end = len(text)
    while end and text[end - 1] in _LEADER_CHARS:
        end -= 1
    if end == len(text) or not any(char in _STRONG_LEADERS for char in text[end:]):
        return text
    return text[:end].rstrip()


def clean_entry_rows(lines: Sequence[str]) -> list[tuple[str, int | None]]:
    """若干原始行 → `(条目名, 印刷页码)` 序列：**行清洗的唯一正本**（`KA-41`）。

    三步＝黏行按行内第二处「引导点＋页码」拆开 → 行末引导点与印刷页码一律剔离 → 无页码的碎片先与
    前一条拼回再判。确定性条目腿（`toc_entries`）与模型整理腿（`orchestrator/knowledge.build_outline`）
    共用这一把：名字是入库的那份，`norm_key` 与屏上读数都由它派生，两处各洗一份必漂移。

    拼回的判据只看带页码那一行——它以章／节形状起头时自己是新条目，前半截只能独立成条
    （`printed_page=None`，随后走未定位披露）；否则并给它（`附录` 紧跟 `第2章 …… 49` 时不并）。
    """
    rows: list[tuple[str, int | None]] = []
    pending: list[str] = []
    for raw in lines:
        for line in _explode_inner_entries(raw):
            parsed = _trailing_page(line)
            if parsed is None:
                pending.append(line.strip())
                continue
            name, printed = parsed
            if not _is_chapter_shape(name) and pending:
                name = " ".join([*pending, name])
                pending = []
            elif pending:
                rows.append((_strip_trailing_leaders(" ".join(pending)), None))
                pending = []
            name = _strip_trailing_leaders(name)
            if not name:
                continue
            rows.append((name, printed))
    if pending:
        rows.append((_strip_trailing_leaders(" ".join(pending)), None))
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


def locate_entries(
    entries: Sequence[TocEntry], body_pages: Sequence[BlocksPage], *, slack: int = TOC_LOCATE_SLACK
) -> TocLocation:
    """条目序列 ＋ 正文页序列 → 本书偏移与逐条回定位读数（`entries` 同序）。

    命中判据＝**去空白前缀**：正文某块的归一文本以条目名的归一文本起头。前缀而不是子串，是因为目录
    行与正文里的章名行是同一起头形状，而子串会把「见第2章实数与极限第93页」这类引用句也算成命中。
    每条条目先收集全部命中，再只接受 `印刷页＋偏移 ± slack` 窗口内的那一枚，按 `(页号, 块下标)` 取最早
    的——窗口外的回声一律不算，因为假锚比缺锚难发现得多（`KA-26` 同轴：未定位是披露，不是失败）。

    `body_pages` 由调用方给出（不含目录页自身）；本函数不读盘、不查库，也不改任何页读数。
    """
    keyed: list[tuple[int, int, str]] = [
        (page.page, block.index, _squash(block.text or ""))
        for page in body_pages
        for block in page.blocks
        if (block.text or "").strip()
    ]
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
            located.append(LocatedEntry(entry=entry, page_no=None, block_index=entry.block_index, located=False))
        else:
            located.append(LocatedEntry(entry=entry, page_no=anchor[0], block_index=anchor[1], located=True))
    return TocLocation(offset=offset, entries=tuple(located))


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


def _title_line(text: str) -> str:
    """一个块的候选标题行＝块里第一行非空文本的原样（不折、不清洗，`KA-50` 取的就是这一行）。"""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


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
    「标题折两块」的常态。索引一次建好、逐条查询零扫描——目录腿的条目数是百枚级，逐条重扫整本就
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
                if line:
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


def reference_role_for_name(name: str) -> tuple[str, str]:
    """条目名 → `(toc_role, toc_kind)`（`KA-39` 的回落腿）：认不出类别的行就是知识点行。

    只在模型判页不成时使用；正腿的 role/kind 由判页读数直接给，不靠名字猜。
    """
    folded = (name or "").strip().casefold()
    if not folded:
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
    `KA-6` 就不成行，`depth` 只有 1／2 两值。
    """
    if toc_kind == "exercise" or "exercise" in ancestor_kinds:
        return NODE_TYPE_EXERCISE
    if toc_role == TOC_ROLE_REFERENCE:
        return NODE_TYPE_OTHER
    return NODE_TYPE_CHAPTER if (depth or 0) <= 1 else NODE_TYPE_POINT


def node_types_of(rows: Iterable[object]) -> dict[str, str]:
    """一批目录行 → 每行的四值类型（`KA-44`）。

    认的是三枚只读属性 `node_id`／`parent_id`／`meta` 与一枚 `depth`：`AfKp` 与点腿的 `KpNodeRow`
    都长得这个样，读面与取材侧因此共用同一份派生——两处各写一次类型判，屏上的标签迟早与批次里
    实际走的腿分叉。父不在这一批里时只按本行的身份位判（不猜）。
    """
    by_id = {row.node_id: row for row in rows}
    types: dict[str, str] = {}
    for row in rows:
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
