"""书自身目录页的条目腿：把「目录页的一行条目」拆成条目名＋印刷页码，再用偏移标定把条目名回指到正文页。

判据正文见 [knowledge-analysis.md](../../docs/design/knowledge-analysis.md) 的 `KA-33`（回落腿）与
`KA-37`／`KA-38`（正腿复用本模块的定锚判据）、`KA-41`（`clean_entry_rows`＝条目腿与模型整理腿共用的
那一把行清洗，与比对口径的闭集标点折算）。本模块只吃 `BlocksPage`（页产物回放后的块序列），
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
