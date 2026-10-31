"""梳理文本引擎适配器（槽位名 `qed-gateway`）：prompt 在本仓组装，出站唯一＝8900 的 `POST /api/v1/llm/text`。

R8 甲（PLAN-026 W6）把梳理生成腿接成「本仓组 prompt、本仓发起调用、8900 只做统一中转与记账」：

- **一条调用一行账，且只在 8900 落**：归因四键 `service`／`task`／`step`／`meta` 随请求体上送，
  本仓的 `KA-23` 自写腿在这条通路上停用（`JobRunner` 据 `accounts_externally` 跳过包装器）；
  同库两处写同一张 `qed_llm_calls` 会双计，比缺账更难查。
- **密钥与模型名一律不下发**：供应商密钥与模型档位都归 8900（ADR 0004 的集中配置），本适配器
  连 `model` 字段都不给——梳理批次点名的是「8900 当前生效的文字槽位」，不是某个具体模型。
- **四类故障全归一为 `EngineError`**（`success=false`／非 2xx／非 JSON／传输异常）：`extract_points`
  的节失败域只捕这一种，httpx 穿透出去就成了整批回滚。空回复同样判故障——它多半是输出被截断，
  静默返回 `[]` 会在读数里长得像「这一节没有知识点」。
- **「不是 JSON」先过一枚非法转义补计腿**（DEFECT-026 甲）：模型写 LaTeX 时把 `$\\mu$`、`$\\,$`
  原样放进字符串，`json.loads` 就报 `Invalid \\escape`，一处坏转义不该吃掉一整节知识点；
  补计只加倍串内不合法的那枚反斜杠（合法转义逐字节不动），补完仍不可解析才判故障并说出「补过」。
- **批次身份必须先绑定再调用**：`meta` 里的 `book_id`／`gen_batch` 是这条账唯一的可指认处，协议
  （`KnowledgeEngine`）不携带受理结果，所以由执行体在跑批次的第一眼交进来（`bind_batch`）；没绑定
  就拒跑，而不是发出一条查不到主人的调用。

prompt 正体在本仓（AF-T3 甲）：梳理的取材边界是解析侧知识，与 `KA-5` 两阶段划分同处才好维护。
输出形状严格贴 `orchestrator/knowledge.py` 的校验器（点行闭集），形状外者由编排层计数丢弃，
不在这里替模型兜底。知识点腿的外层回一枚对象（`section_summary`＋`points`，`KA-43`），裸数组
仍照老形状收下（总结记空串）——外层认两种，内层点行的闭集只有一种。目录三条腿也在本模块组 prompt（判页／整理 `KA-37`、核对 `KA-38`）：判页与核对回 JSON，整理回逐行三段式——竖线行坏一行只坏一行，JSON 会把整页目录推过本机吞吐墙。

HTTP 客户端恒以 `trust_env=False` 构造（`MI-10`）：8900 在回环上，交给 Windows 系统代理会出现
502／0 字节的假象。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

import httpx

from axiom_flow.db.llm_ledger import KP_LEDGER_TASK, LLM_CALL_SERVICE
from axiom_flow.engines.base import (
    ChapterInput,
    ChapterReply,
    EngineError,
    OutlineEntry,
    PageSample,
    ReferencePage,
    SectionInput,
    SectionPoints,
    TocPagePick,
    TocVerifyAnswer,
    TocVerifyItem,
)
from axiom_flow.normalize.toc import TOC_REFERENCE_KINDS

logger = logging.getLogger(__name__)

#: 槽位登记名：与 `engines/factory.py` 的文本闭集、`config.Settings.kp_engine` 默认值同为一个值。
KP_SLOT_NAME = "qed-gateway"

_TEXT_PATH = "/api/v1/llm/text"
_HEALTH_PATH = "/api/v1/health"
#: 探活不等批次：提交期探活用短预算，长预算留给真正的模型调用。
_HEALTH_TIMEOUT = 5.0

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)

#: JSON 里 `\` 之后允许的两类字符：单字符转义与 `u`（后者还要跟四枚十六进制）。
_ESCAPE_CHARS = frozenset('"\\/bfnrtu')
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def _repair_json_escapes(text: str) -> str:
    """把字符串字面量里**不合法**的单枚反斜杠补成 `\\\\`；合法转义与串外内容逐字节不动。

    DEFECT-026 甲的动机是一枚真机回复：模型写 LaTeX 时把 `$\\mu$`、`$\\,$` 原样放进 JSON 字符串，
    `json.loads` 当场 `Invalid \\escape`，一整节知识点就没了——数学教材里这形状的正文是常态。
    只做这一件事：不补引号、不修结构、不猜缺失的括号；串外出现的反斜杠一概不碰（合法 JSON 里
    根本没有），合法转义连同 `\\uXXXX` 整对原样保留（把 `\\n` 补成 `\\\\n` 比不修更坏——正文从此不可信）。
    """
    out: list[str] = []
    in_string = False
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if not in_string:
            in_string = char == '"'
            out.append(char)
            index += 1
            continue
        if char != "\\":
            if char == '"':
                in_string = False
            out.append(char)
            index += 1
            continue
        nxt = text[index + 1] if index + 1 < length else ""
        complete = nxt == "u" and all(c in _HEX_DIGITS for c in text[index + 2 : index + 6])
        if nxt in _ESCAPE_CHARS and (nxt != "u" or complete):
            kept = 6 if nxt == "u" else 2
            out.append(text[index : index + kept])
            index += kept
            continue
        out.append("\\\\")
        index += 1
    return "".join(out)


_POINTS_PROMPT = """\
你是知识点抽取器。本轮只处理一节，节内材料按页给出，每页以「[页 N]」起头。

任务：抽出这一节里的知识点，并为这一节写一段节级总结。

输出要求（不合形状的回复会让整节作废，请严格遵守）：
- 只输出一个 JSON 对象，不写解释、不写代码围栏，形如
  {"section_summary": "这一节的一段话总结",
   "points": [
     {"name": "知识点名", "type": "concept", "role": "core", "summary": "1~3 句说明它是什么、怎么用",
      "anchors": [{"page": 7, "snippet": "该页支撑它的原文片段"}],
      "links": [{"kind": "prerequisite", "to": "另一端的名字", "note": "一句话"}],
      "parent_ref": "本节标题原文"}
   ]}
- section_summary：写成一段通顺连贯的话（约 80~200 字），先讲这一节说的是什么，再讲它的结论
  怎么用，最后讲它在本章里承接了什么、引出了什么；用完整的句子把它们串成一段，而不是把要点
  用分号排成一串。只写本节正文里有的内容。
- name：简短可读（不超过约 50 字），用本节出现的说法或通行术语。
- type 只能取四值之一：memory＝要记住的定义或结论；procedure＝会做的做法与计算步骤；
  concept＝要理解的概念与思想；design＝方法的选择与设计思路。
- role 只能取四值之一：core＝这一节的主干；explainer＝帮助讲解的辅助点；example＝例题或实例；
  拿不准就留空串。
- summary 是学习者先看到的那一行：先说它是什么，再说用它做什么。
- anchors 至少一条：page 必须落在本轮给出的页里，snippet 抄该页原文（不超过 200 字）。
- links 可以整体省略；要写时 kind 取 prerequisite／co_requisite／explains／exemplifies／extends 之一，
  to 只能指向本数组里排在它之前的某个 name，或本节标题——指向别处的名字会让这一条被判为无效行。
- parent_ref 只在确实属于别的节时填那一节的标题原文；不确定就留空（留空进未归档池等人工挂父）。
- 抽不出可靠知识点时 points 给 []，section_summary 照写。宁可少产出，也不要编造名称或锚点。
"""


_TOC_PAGES_PROMPT = """\
你是教材结构识别器。任务：从给出的页样本里认出两类页。
- 目录页：书自己排的「条目名＋页码」清单页，包含它的续页。
- 附件页：正文之前或正文之后的**非知识点**页——前言、引言、序言、译名表、重要符号表、附录、
  参考文献、索引、习题答案等。

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏）：
{"toc_pages": [页号], "reference_pages": [{"page": 页号, "name": "这一页叫什么", "kind": "类别"}]}
- 页号只能取本轮样本里出现过的页号；样本里没有的页号一律不要写。
- 拿不准的页不要写；同一页号不得同时出现在两个数组里。
- kind 只能取七值之一：front_matter＝正文前的引言类；back_matter＝正文后的记述类；
  symbol_table＝符号表／记号表；appendix＝附录；bibliography＝参考文献；index＝索引；
  exercise＝习题与习题答案。
- 没有目录页时 toc_pages 给空数组；没有附件页时 reference_pages 给空数组。
"""

_TOC_ENTRIES_PROMPT = """\
你是目录抄录器。输入是被认定为目录页的那一页原文块。
任务：把这一页的目录条目逐条抄成行，条目名照抄、一个字都不改。

输出要求（一行一条条目，不写解释、不写代码围栏、不写表头）：
层级|印刷页码|条目名
- 层级＝1（章／篇一级）或 2（节／小节），只认这两个数字。
- 印刷页码＝这一行末尾印着的页码，只写阿拉伯数字；行末没有页码就写一个减号 `-`。
- 罗马数字页码的行按「没有页码」处理（该位写 `-`）。
- 条目名照抄书里的写法：不翻译、不补全、不改标点、不删空格以外的任何东西。
- 正文段落、页眉页脚、以及不是目录条目的行都不要抄进来。
- 这一页抄不出任何目录条目时，只输出一行 NONE。
"""

_TOC_VERIFY_PROMPT = """\
你是目录条目定位器。下面每一条都是书目录里的一行条目名，它在正文里没有被逐字命中——常见原因是
OCR 把词拆坏、两条条目黏成一行，或目录里的说法与正文里的写法异形。
任务：只看给出的候选页里的行，判断这一条其实起在哪一页。

输出要求（只输出一个 JSON 数组，不写解释）：
[{"name": "条目名原文", "page": 页号}, {"name": "条目名原文", "page": null}]
- page 只能取该条候选页里出现过的页号；候选页里都不像就写 null。
- name 照抄给它的条目名原文，一个字都不改（改了就对不回原来那一行）。
- 每条给且只给一个对象，对象数必须与给出的条目数相同。
"""


def _strip_fence(reply: str) -> str:
    """剥掉代码围栏取本体；没有围栏就原样返回（竖线行协议本来就不是 JSON）。"""
    fenced = _FENCE.search(reply)
    return (fenced.group(1) if fenced else reply).strip()


def _as_page(value: object) -> int | None:
    """页号只认正整数：`"12"`／`12.0` 都收，坏值、0 与负数回 `None` 由调用方丢这一条。

    布尔值要单独挡在前面——Python 里 `True` 是 `int` 的子类，放行就成了「第 1 页」。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value.is_integer() and value > 0 else None
    text = str(value).strip()
    if text.isdigit():
        page = int(text)
        return page if page > 0 else None
    return None


def _pipe_row(line: str) -> OutlineEntry | None:
    """`层级|印刷页码|条目名` → `OutlineEntry`；三形坏任一形回 `None`，页码坏只缺页不丢行。

    页码段认不出数字（`-`／罗马数字／OCR 粘连）按「这一行没有印刷页码」处理（`KA-37`）：名字是
    书自己写的话，定锚还有一条按序回定位的路可走，丢行等于把目录少一行且账面查不到原因。
    名字段不裁剪尾部标点——把 `Appendix.` 洗成 `Appendix` 就是机器替书改了名。列表符号只从层级段剥。
    """
    parts = line.split("|")
    if len(parts) != 3:
        return None
    level_text = parts[0].strip("-·*• \t")
    name = parts[2].strip()
    if level_text not in ("1", "2") or not name:
        return None
    return OutlineEntry(
        level=int(level_text), printed_page=_as_page(parts[1].strip()), name=name[:200]
    )


_FIELD_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def _salvage_field(reply: str, key: str) -> str:
    """从不完整的 JSON 文本里抢回一枚字符串字段（#129 容错腿）：只认已读完的那段字面量。

    真机读数是「总结写完了，点数组写到一半被截断」：整段 `json.loads` 失败，可总结本身一字不缺。
    这条腿只做一件事——从 `"key": "` 起逐字符读到未转义的收尾引号或文本末尾，按 JSON 转义规则
    还原；不补括号、不猜缺失字段。抢不到（键不存在、键后不是字符串）回空串，由调用方按原故障处理。
    """
    marker = f'"{key}"'
    index = reply.find(marker)
    if index == -1:
        return ""
    colon = reply.find(":", index + len(marker))
    if colon == -1:
        return ""
    start = colon + 1
    while start < len(reply) and reply[start] in " \t\r\n":
        start += 1
    if start >= len(reply) or reply[start] != '"':
        return ""
    out: list[str] = []
    pos = start + 1
    while pos < len(reply):
        char = reply[pos]
        if char == "\\" and pos + 1 < len(reply):
            nxt = reply[pos + 1]
            if nxt == "u" and pos + 5 < len(reply):
                code = reply[pos + 2 : pos + 6]
                if all(digit in _HEX_DIGITS for digit in code):
                    out.append(chr(int(code, 16)))
                    pos += 6
                    continue
            out.append(_FIELD_ESCAPES.get(nxt, nxt))
            pos += 2
            continue
        if char == '"':
            break
        out.append(char)
        pos += 1
    return "".join(out).strip()


def _review_note_block(review_note: str) -> list[str]:
    """备注段（AF-T5 甲）：有备注才追加，空备注整段不出现——留一枚空标题等于下了一条空指令。

    备注只进 prompt，不进归因 `meta`：它是这一轮给模型读的上下文，账目维仍只有批次身份与取材事实。
    """
    note = review_note.strip()
    if not note:
        return []
    return [
        "",
        "人工评审意见（上一轮人工在原文块上的修订与批注，本轮据此避错）：",
        note,
        "",
    ]


def _points_prompt(section: SectionInput, review_note: str = "") -> str:
    parent = section.parent_id or "（无：本段不在目录树上）"
    lines = [
        _POINTS_PROMPT,
        f"节标题：{section.title or '（目录未覆盖区间）'}",
        f"页区间：{section.page_start}..{section.page_end}",
        f"所属节点：{parent}",
        "",
        "节内正文：",
        "",
    ]
    for page in section.pages:
        lines.append(f"[页 {page.page_no}]")
        lines.append(page.markdown.rstrip())
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


_CHAPTER_PROMPT = """\
你是教材章节导读器。本轮只处理一章，材料分两块：章首正文（按页给出，每页以「[页 N]」起头）
与其下各知识点的节级总结清单。

任务：为这一章写一段章级总结与一篇章级导读。

输出要求（不合形状的回复会让整章作废，请严格遵守）：
- 只输出一个 JSON 对象，不写解释、不写代码围栏，形如
  {"chapter_summary": "这一章的一段话总结", "chapter_narrative": "这一章的导读"}
- chapter_summary：一段通顺连贯的话（约 80~200 字），先讲这一章要解决什么问题，再讲它给出了
  哪些主要结论，最后讲它在这一门课里承接了什么、为后面哪一章做了准备；不要用分号把要点排成串。
- chapter_narrative：一篇导读（约 300~600 字），分三段写：第一段讲这一章的总问题与为什么难；
  第二段按顺序讲各个知识点怎样一层层推进到结论；第三段讲学完这一章应当能做什么、看什么时
  会用到它。只用材料里有的内容，不编造材料里没有的定理与例子。
- 材料里的「节级总结清单」是逐节已经写好的总结，导读要在此基础上串成一条线，不要逐条复述。
"""


def _chapter_prompt(material: ChapterInput, review_note: str = "") -> str:
    lines = [
        _CHAPTER_PROMPT,
        f"章标题：{material.title}",
        f"页区间：{material.page_start}..{material.page_end}",
        "",
    ]
    bodies = material.intro_pages or material.title_pages
    if bodies:
        lines.extend(["章首正文：", ""])
        for page in bodies:
            lines.append(f"[页 {page.page_no}]")
            lines.append(page.markdown.rstrip())
            lines.append("")
    else:
        lines.extend(["章首正文：（这一章与首节同页起，没有单独的章首正文）", ""])
    lines.extend(["其下各知识点的节级总结：", ""])
    if material.point_summaries:
        for title, summary in material.point_summaries:
            lines.append(f"- {title}：{summary}")
    else:
        lines.append("- （其下各知识点都没有总结）")
    lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


def _toc_pages_prompt(samples: Sequence[PageSample], review_note: str = "") -> str:
    """判页腿的 prompt：样本页逐页给出（页号＋已截断的带类型文本），备注同段注入。"""
    lines = [_TOC_PAGES_PROMPT, f"本轮样本页：{', '.join(str(sample.page_no) for sample in samples)}", "", "样本：", ""]
    for sample in samples:
        lines.append(f"[页 {sample.page_no}]")
        lines.append(sample.text)
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


def _toc_entries_prompt(page_no: int, text: str, review_note: str = "") -> str:
    lines = [_TOC_ENTRIES_PROMPT, f"目录页页号：{page_no}", "", "该页原文块：", "", text, ""]
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


def _toc_verify_prompt(items: Sequence[TocVerifyItem], review_note: str = "") -> str:
    lines = [_TOC_VERIFY_PROMPT, ""]
    for item in items:
        printed = "-" if item.printed_page is None else str(item.printed_page)
        lines.append(f"- 条目名：{item.name}（印刷页码 {printed}）")
        grouped: dict[int, list[str]] = {}
        for page_no, line in item.candidates:
            grouped.setdefault(page_no, []).append(line)
        if not grouped:
            lines.append("  候选页：（无）")
        for page_no, page_lines in grouped.items():
            lines.append(f"  候选页 {page_no}：" + " ／ ".join(page_lines[:12]))
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


class KpGatewayEngine:
    """`KA-5` 梳理两阶段的 8900 网关实现：目录三条腿 ＋ 知识点每节一次调用。"""

    name = KP_SLOT_NAME
    #: 记账在门外（R8 甲）：`JobRunner` 据此跳过 `LedgeredKnowledgeEngine` 包装器与本仓账要求。
    accounts_externally = True

    def __init__(
        self, gateway_url: str, *, timeout: float = 360.0, client: httpx.Client | None = None
    ) -> None:
        self.base_url = gateway_url.rstrip("/")
        self.timeout = float(timeout)
        self._client = client
        self._owns_client = client is None
        self._batch: tuple[str, str] | None = None
        self._review_note = ""

    def bind_batch(
        self, *, book_id: str, gen_batch: str, review_note: str = ""
    ) -> KpGatewayEngine:
        """把当前批次身份交给适配器（返回自身，装配点一行接完）。

        一枚实例只服务一批：`_run_knowledge` 每批新构造，重绑就是换了主人——账跟着走，
        不会把后一批的调用记到前一批头上。

        `review_note` 一并交进来（AF-T5 甲）：它是**这一轮**的上下文，跟着批次身份走同样的
        一次性绑定，执行体不必再为备注开第二条通路。
        """
        self._batch = (book_id, gen_batch)
        self._review_note = review_note
        return self

    def _http(self) -> httpx.Client:
        if self._client is None:
            # trust_env=False：不让系统/环境变量代理接管回环流量（`MI-10`）。
            self._client = httpx.Client(timeout=self.timeout, trust_env=False)
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def health(self) -> bool:
        """探活＝8900 在听（`GET /api/v1/health`）：不可用即提交期 503，不建批次。"""
        try:
            response = self._http().get(
                f"{self.base_url}{_HEALTH_PATH}", timeout=min(self.timeout, _HEALTH_TIMEOUT)
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    def toc_pages(self, samples: Sequence[PageSample]) -> TocPagePick:
        """判页腿（`KA-37`）：一次调用认出目录页与附件页；**样本外的页号整条弃用**。

        弃用发生在这一侧而不是编排层，因为只有这里知道「本轮究竟送了哪些页号」；被弃用的页号留一条
        不带正文的 warning——模型报一枚没读过的页号，下游就会把锚指到没读过的页上（假锚比缺锚难发现）。
        """
        if not samples:
            return TocPagePick()
        allowed = {sample.page_no for sample in samples}
        reply = self._post_text(
            "toc_pages",
            _toc_pages_prompt(samples, self._review_note),
            {"page_count": len(samples), "page_start": min(allowed), "page_end": max(allowed)},
        )
        payload = self._object(reply, "toc_pages")
        toc: list[int] = []
        refs: list[ReferencePage] = []
        for value in payload.get("toc_pages") or []:
            page_no = _as_page(value)
            if page_no is None:
                continue
            if page_no in allowed:
                toc.append(page_no)
            else:
                logger.warning("判页回复含样本外页号，整条弃用（toc_pages）：%s", page_no)
        for item in payload.get("reference_pages") or []:
            if not isinstance(item, dict):
                continue
            page_no = _as_page(item.get("page"))
            name = str(item.get("name") or "").strip()
            if page_no is None or not name:
                continue
            if page_no not in allowed:
                logger.warning("判页回复含样本外附件页，整条弃用（toc_pages）：%s", page_no)
                continue
            kind = str(item.get("kind") or "").strip()
            refs.append(
                ReferencePage(page_no=page_no, name=name[:200], kind=kind if kind in TOC_REFERENCE_KINDS else "")
            )
        toc_pages = tuple(sorted({page for page in toc if page not in {ref.page_no for ref in refs}}))
        return TocPagePick(toc_pages=toc_pages, reference_pages=tuple(refs))

    def toc_entries(self, page_no: int, text: str) -> list[OutlineEntry]:
        """整理腿（`KA-37`）：一页目录原文 → 条目行；竖线行的三种畸形逐行丢掉，**全坏判该页失败**。

        输出协议走逐行三段式而不是 JSON：一页目录上百条条目写成 JSON 会把回复推过本机的吞吐墙，
        而竖线行坏一行只坏一行。`NONE` 是模型自己说「这页没有条目」，与「抄坏了」是两回事——
        前者出空列表（该页不成行），后者判 `EngineError`（该页回落条目腿）。
        """
        reply = self._post_text(
            "toc_entries",
            _toc_entries_prompt(page_no, text, self._review_note),
            {"toc_page": page_no, "chars": len(text)},
        )
        if not reply.strip():
            raise EngineError(f"模型空回复（toc_entries 第 {page_no} 页）：按该页整理失败回落条目腿")
        body = _strip_fence(reply)
        entries: list[OutlineEntry] = []
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if not lines:
            raise EngineError(f"模型回复没有可用行（toc_entries 第 {page_no} 页）：按该页整理失败回落条目腿")
        if len(lines) == 1 and lines[0].upper().startswith("NONE"):
            return []
        for line in lines:
            parsed = _pipe_row(line)
            if parsed is not None:
                entries.append(parsed)
        if not entries:
            raise EngineError(
                f"模型回复的竖线行全不可解析（toc_entries 第 {page_no} 页）：按该页整理失败回落条目腿"
            )
        return entries

    def toc_verify(self, items: Sequence[TocVerifyItem]) -> list[TocVerifyAnswer]:
        """核对腿（`KA-38`）：坏行攒一批一次问，每条回一个候选页里的页号或 null。

        页号是否在候选页里由编排层判（它才知道取证页集），这里只做形状把关：条目名照抄、页号取整数。
        """
        if not items:
            return []
        reply = self._post_text(
            "toc_verify",
            _toc_verify_prompt(items, self._review_note),
            {"count": len(items), "names": [item.name[:60] for item in items][:20]},
        )
        answers: list[TocVerifyAnswer] = []
        for draft in self._drafts(reply, "toc_verify"):
            if not isinstance(draft, dict):
                continue
            name = str(draft.get("name") or "").strip()
            if not name:
                continue
            answers.append(TocVerifyAnswer(name=name, page_no=_as_page(draft.get("page"))))
        return answers

    def points(self, section: SectionInput) -> SectionPoints:
        reply = self._post_text(
            "points",
            _points_prompt(section, self._review_note),
            {
                "section": section.title,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "page_count": len(section.pages),
            },
        )
        return self._section_points(reply, "points")

    def chapter(self, material: ChapterInput) -> ChapterReply:
        """章腿（`KA-45`）：章首正文＋其下各节总结 → 章级总结与导读一段一次调用。

        读取与节腿同一条通路（`_post_text`），归因 `step='chapter'`：章调用在账上认得出是章，
        而不是又一枚「points」。
        """
        reply = self._post_text(
            "chapter",
            _chapter_prompt(material, self._review_note),
            {
                "chapter": material.title,
                "page_start": material.page_start,
                "page_end": material.page_end,
                "intro_pages": [page.page_no for page in material.intro_pages],
                "point_count": len(material.point_summaries),
            },
        )
        if not reply.strip():
            raise EngineError("模型空回复（chapter）：不折算成零产出，按章失败披露")
        try:
            payload = self._object(reply, "chapter")
        except EngineError:
            summary = _salvage_field(reply, "chapter_summary")
            narrative = _salvage_field(reply, "chapter_narrative")
            if not summary and not narrative:
                raise
            logger.warning("章回复的 JSON 不完整，按已读到的字段保留（chapter）")
            return ChapterReply(summary=summary, narrative=narrative)
        summary = payload.get("chapter_summary")
        narrative = payload.get("chapter_narrative")
        return ChapterReply(
            summary=summary.strip() if isinstance(summary, str) else "",
            narrative=narrative.strip() if isinstance(narrative, str) else "",
        )

    def _post_text(self, step: str, prompt: str, meta: dict[str, Any]) -> str:
        """一次调用：出站唯一、归因随体、四类故障各归一条 `EngineError` 文案。"""
        if self._batch is None:
            raise EngineError(f"梳理网关未绑定批次身份，拒跑 {step}：这一条调用将查不到主人（R8 甲）")
        book_id, gen_batch = self._batch
        payload = {
            "prompt": prompt,
            "service": LLM_CALL_SERVICE,
            "task": KP_LEDGER_TASK,
            "step": step,
            "meta": {"book_id": book_id, "gen_batch": gen_batch, "engine": self.name, **meta},
        }
        try:
            response = self._http().post(f"{self.base_url}{_TEXT_PATH}", json=payload)
        except httpx.HTTPError as exc:
            raise EngineError(f"8900 网关不可达（{step}）：{exc}") from exc
        if response.status_code != 200:
            raise EngineError(
                f"8900 网关返回 HTTP {response.status_code}（{step}）：{response.text[:200]}"
            )
        try:
            envelope = response.json()
        except ValueError as exc:
            raise EngineError(f"8900 网关响应不是 JSON（{step}）") from exc
        if not isinstance(envelope, dict):
            raise EngineError(f"8900 网关响应不是 JSON 对象（{step}）")
        if not envelope.get("success"):
            raise EngineError(f"8900 网关调用未成功（{step}）：{envelope.get('error') or '未给出原因'}")
        reply = envelope.get("reply")
        if not isinstance(reply, str):
            raise EngineError(f"8900 网关响应缺 reply 文本（{step}）")
        return reply

    def _object(self, reply: str, step: str) -> dict:
        """回复 → JSON 对象：取首个 `{` 到最后 `}`，非法转义仍走同一枚补计腿（`DEFECT-026` 甲）。

        与 `_drafts` 分开是因为本体形状不同：判页与核对两条腿回的是对象／数组的**外层**不同，
        混成一条函数就要在内部猜「这次该是哪种」，猜错的那次会把坏形状说成好形状。
        """
        body = _strip_fence(reply)
        if "{" not in body or "}" not in body:
            raise EngineError(f"模型回复里没有 JSON 对象（{step}）：{reply[:120]}")
        candidate = body[body.index("{") : body.rindex("}") + 1]
        try:
            payload = json.loads(candidate)
        except ValueError as exc:
            repaired = _repair_json_escapes(candidate)
            if repaired == candidate:
                raise EngineError(f"模型回复不是 JSON（{step}）：{reply[:120]}") from exc
            try:
                payload = json.loads(repaired)
            except ValueError as retry_exc:
                raise EngineError(
                    f"模型回复不是 JSON（{step}，补过非法转义仍不可解析）：{reply[:120]}"
                ) from retry_exc
            logger.warning("梳理回复含非法 JSON 转义，补计后重解析成功（%s）", step)
        if not isinstance(payload, dict):
            raise EngineError(f"模型回复不是 JSON 对象（{step}）：{reply[:120]}")
        return payload

    def _drafts(self, reply: str, step: str) -> list[dict]:
        """回复 → 草稿数组：代码围栏与前后杂讯都取数组本体；坏形状是故障，不是零产出。"""
        if not reply.strip():
            raise EngineError(f"模型空回复（{step}）：不折算成零产出，按节失败披露")
        fenced = _FENCE.search(reply)
        candidate = fenced.group(1) if fenced else reply
        candidate = candidate.strip()
        if not candidate.startswith("[") and "[" in candidate and "]" in candidate:
            candidate = candidate[candidate.index("[") : candidate.rindex("]") + 1]
        try:
            drafts = json.loads(candidate)
        except ValueError as exc:
            repaired = _repair_json_escapes(candidate)
            if repaired == candidate:
                raise EngineError(f"模型回复不是 JSON（{step}）：{reply[:120]}") from exc
            try:
                drafts = json.loads(repaired)
            except ValueError as retry_exc:
                raise EngineError(
                    f"模型回复不是 JSON（{step}，补过非法转义仍不可解析）：{reply[:120]}"
                ) from retry_exc
            # 只留一条痕，不把正文搬进日志（回复里可能带书名与用户写的备注）
            logger.warning("梳理回复含非法 JSON 转义，补计后重解析成功（%s）", step)
        if not isinstance(drafts, list):
            raise EngineError(f"模型回复不是 JSON 数组（{step}）：{reply[:120]}")
        return drafts

    def _section_points(self, reply: str, step: str) -> SectionPoints:
        """回复 → 一节的两半（`KA-43`）：外层认两种形状，内层点行仍只有一种闭集。

        先看首个 `{` 与首个 `[` 谁在前：对象在前走对象支，否则走老的数组支。对象支**不可用**
        （解析不出、或没有一枚数组形的 `points`）而回复里另有数组时回落数组支——模型会把示例写成
        `{name: ...}` 摆在正文里，那枚假对象不该吃掉整节；回复里根本没有数组才判故障，不折算成
        零产出（`KA-32`：坏形状是故障，不是「这一节没有知识点」）。
        """
        if not reply.strip():
            raise EngineError(f"模型空回复（{step}）：不折算成零产出，按节失败披露")
        body = _strip_fence(reply).strip()
        brace, bracket = body.find("{"), body.find("[")
        if brace != -1 and (bracket == -1 or brace < bracket):
            try:
                payload = self._object(reply, step)
            except EngineError as exc:
                # #129 容错腿：总结已写全而点数组被截断时，整段 JSON 不可解析——保住已读完的总结，
                # 不把这一节整节判失败（真机读数：6/98 节有总结，其余多是这种截断）。只救「解析不了」
                # 的那一档：JSON 本身完整而 `points` 类型不对仍是坏形状（`KA-32`），不拿总结替它开脱。
                salvaged = _salvage_field(reply, "section_summary")
                if salvaged:
                    logger.warning("节回复的 JSON 不完整，保住节级总结（%s）", step)
                    return SectionPoints([], salvaged)
                if bracket == -1:
                    raise EngineError(
                        f"模型回复缺可用的 points 数组（{step}）：{reply[:120]}"
                    ) from exc
                return SectionPoints(self._drafts(reply, step))
            points = payload.get("points")
            if isinstance(points, list):
                summary = payload.get("section_summary")
                return SectionPoints(points, summary.strip() if isinstance(summary, str) else "")
            if bracket == -1:
                raise EngineError(f"模型回复缺可用的 points 数组（{step}）：{reply[:120]}")
        return SectionPoints(self._drafts(reply, step))
