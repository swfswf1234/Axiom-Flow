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
    CHAPTER_TEXT_PROMPT_VERSION,
    ChapterInput,
    ChapterReply,
    ChapterTextInput,
    EngineError,
    ExerciseClassifyInput,
    ExerciseClassifyItem,
    ExerciseClassifyReply,
    ExerciseDraft,
    ExerciseInput,
    ExerciseMatchChoice,
    ExerciseMatchInput,
    ExerciseReply,
    ExerciseVerifyInput,
    ExerciseVerifyReading,
    ExerciseVerifyReply,
    MemberPoints,
    OutlineEntry,
    PageSample,
    ReferenceNote,
    ReferencePage,
    RevisionContext,
    SectionBlock,
    SectionInput,
    SectionPoints,
    TocOutlineRow,
    TocPagePick,
    TocPageReading,
    TocVerifyAnswer,
    TocVerifyItem,
)
from axiom_flow.normalize.exercise import item_number
from axiom_flow.normalize.toc import TOC_REFERENCE_KINDS

logger = logging.getLogger(__name__)

#: 槽位登记名：与 `engines/factory.py` 的文本闭集、`config.Settings.kp_engine` 默认值同为一个值。
KP_SLOT_NAME = "qed-gateway"

_TEXT_PATH = "/api/v1/llm/text"
_CALLS_PATH = "/api/v1/llm/calls"
_HEALTH_PATH = "/api/v1/health"
#: 只读模型清单（PLAN-033 W5）：当前生效文字槽位的模型名，仅用于异常注记。
_CONFIG_MODELS_PATH = "/api/v1/config/models"
#: 探活不等批次：提交期探活用短预算，长预算留给真正的模型调用。
_HEALTH_TIMEOUT = 5.0
#: 模型名探测同样不占批次预算：失败只是注记里少一个名字。
_MODEL_PROBE_TIMEOUT = 5.0

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

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏，形如
  {"section_summary": "本点核心要点与关键定义的梳理",
   "points": [
     {"name": "知识点名", "type": "concept", "role": "core", "summary": "1~3 句说明它是什么、怎么用",
      "must_master": ["学完要能做到的一件事", "再看能做到的第二件事"],
      "key_definitions": [{"term": "术语原文", "definition": "一句话定义"}],
      "anchors": [{"page": 7, "snippet": "该页支撑它的原文片段"}],
      "links": [{"kind": "prerequisite", "to": "另一端的名字", "note": "一句话"}],
      "parent_ref": "本节标题原文"}
   ]}）：
- section_summary：一段通顺连贯的话（约 100~220 字），目标是让人不读原文也能抓住本点的核心要点与
  关键定义。写法：
  · 第一句落到本点要处理的对象与问题，把它问得具体（带上对象，例如「怎样用有限的手段刻画无限接近」）；
  · 接着列出本点的核心结论：每条带上成立条件与适用范围（哪条定义、对什么成立），并点明它由哪条
    定义或定理得到；
  · 关键定义、记号与公式给规范表述：术语照正文写法，定义说清它指什么、条件是什么；
  · 这是要点的梳理，不是原文压缩：不复述推导过程、不逐段缩写、不写本节的章节位置、不写后续用在哪里；
  · 材料以本节正文为准；材料里混入了相邻节的内容时，只取与本节标题相符的部分；
  · 定理名、条件与例子都取自正文，正文没有的不写。
- name：简短可读（不超过约 50 字），用本节出现的说法或通行术语。
- type 只能取四值之一：memory＝要记住的定义或结论；procedure＝会做的做法与计算步骤；
  concept＝要理解的概念与思想；design＝方法的选择与设计思路。
- role 只能取四值之一：core＝这一节的主干；explainer＝帮助讲解的辅助点；example＝例题或实例；
  拿不准就留空串。
- summary 是学习者先看到的那一行：先说它是什么，再说用它做什么。
- must_master：这个点学完要能做到什么，给 2~4 条，每条一句、不超过 60 字，用可检验的动作写
  （例如「能写出 ε-N 定义的三处量词顺序」「会用定义判断给定数列是否收敛」），不写「理解了」这类空话。
- key_definitions：这个点的关键定义，给 2~6 条，每条是一枚只含 term 与 definition 的对象；
  term 照正文里的写法（术语原文，不超过 30 字），definition 用一句话说清它指什么、条件是什么
  （不超过 120 字）。本节正文没有给出定义时，这一项给 []——不要拿别处的定义补。
- anchors 至少一条：page 必须落在本轮给出的页里，snippet 抄该页原文（不超过 200 字）。
- links 可以整体省略；要写时 kind 取 prerequisite／co_requisite／explains／exemplifies／extends 之一，
  to 只能指向本数组里排在它之前的某个 name，或本节标题——指向别处的名字会让这一条被判为无效行。
- parent_ref 只在确实属于别的节时填那一节的标题原文；不确定就留空（留空进未归档池等人工挂父）。
- 抽不出可靠知识点时 points 给 []，section_summary 照写。宁可少产出，也不要编造名称或锚点。
"""

_BLOCK_POINTS_NOTE = """\
（本轮材料按块给出，每块以「p{页}·b{序}」前缀起头；同一节可能分多笔材料送入，本笔只是其中一段，
只按本笔材料写这一段的 section_summary。块型已由渲染结果体现：公式保留 LaTeX 定界符、表格保留
HTML 原文、图片给路径——照原文引用，不转写、不重排。）
"""

_MERGED_POINTS_PROMPT = """\
本笔是一次合批调用：材料覆盖多个同级知识点，按「知识点 i（id=…）」分段给出，每块以
「p{页}·b{序}」前缀起头。请逐点作答，输出只含一个 JSON 对象，形如
  {"sections": [{"id": "给出的 id 原样照抄", "section_summary": "该点核心要点与关键定义的梳理", "points": [...]}]}
- sections 与给出的知识点一一对应，id 照抄、不增不减；每个知识点的 section_summary 与 points
  写法与单节规则相同；
- 某点的材料读不出本点结论时，该点 section_summary 照写、points 给 []，不要拿相邻点的内容顶替。
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
- 层级＝1（章／篇一级）、2（节／小节）或 3（节下的编号细目／小标题），只认这三个数字。
- 三级细目也要照抄：展示时会并入它所属的二级，漏抄会让二级的内容范围缩水。
- 印刷页码＝这一行末尾印着的页码，只写阿拉伯数字；行末没有页码就写一个减号 `-`。
- 罗马数字页码的行按「没有页码」处理（该位写 `-`）。
- 条目名照抄书里的写法：不翻译、不补全、不改标点、不删空格以外的任何东西。
- 正文段落、页眉页脚、以及不是目录条目的行都不要抄进来。
- 这一页抄不出任何目录条目时，只输出一行 NONE。
"""

_TOC_PAGE_PROMPT = """\
你是教材结构识别器。输入是书的完整一页（逐块给出块类型与原文），请只看这一页。
任务一：判断这一页是不是书的目录页——书自己排的「条目名＋页码」清单页（含它的续页）。
任务二：如果是目录页，把这一页的目录条目逐条抄成行，条目名照抄、一个字都不改。

输出要求（第一行写判定，后面一行一条条目；不写解释、不写代码围栏、不写表头）：
判定行：TOC 或 NOT_TOC
条目行（只有判定为 TOC 时才写）：层级|印刷页码|条目名
- 层级＝1（章／篇一级）、2（节／小节）或 3（节下的编号细目／小标题），只认这三个数字。
- 三级细目也要照抄：展示时会并入它所属的二级，漏抄会让二级的内容范围缩水。
- 印刷页码＝这一行末尾印着的页码，只写阿拉伯数字；行末没有页码就写一个减号 `-`。
- 罗马数字页码的行按「没有页码」处理（该位写 `-`）。
- 条目名照抄书里的写法：不翻译、不补全、不改标点、不删空格以外的任何东西。
- 正文段落、页眉页脚、以及不是目录条目的行都不要抄进来。
- 不是目录页时只写一行 NOT_TOC。
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


_TOC_OUTLINE_PROMPT = """\
你是教材目录的层级校订器。输入是书自己目录里的章／节级条目，已经按原文顺序抄好：
序号|层级|条目名
其中可能有 OCR 造成的截断、粘连、公式残渣或多出来的引导点。

任务：只订正这些条目的名字，让章号／节号完整、名字里不残留排版符号。

输出要求（一行一条，只写需要订正的行，不写解释、不写代码围栏、不写表头）：
序号|规范名
- 序号照抄输入里的那一个，一行只能对应一条输入。
- 规范名只补全被 OCR 吃掉的章号／节号（如「四章」补成「第四章」）、删掉名字里的公式残渣与页尾引导点。
- 不翻译、不缩写、不合并条目、不改层级、不增删条目。
- 拿不准的行整行不写；没有任何需要订正的行时只输出一行 NONE。
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
    if level_text not in ("1", "2", "3") or not name:
        return None
    return OutlineEntry(
        level=int(level_text), printed_page=_as_page(parts[1].strip()), name=name[:200]
    )


def _toc_verdict(line: str) -> bool | None:
    """逐页判页的判定行 → `True`／`False`；认不出回 `None`（该页判失败，不猜）。"""
    folded = re.sub(r"[\s_\-]+", "", (line or "").strip().upper())
    if folded in {"NOTTOC", "NO", "否", "不是", "非目录"}:
        return False
    if folded in {"TOC", "YES", "是", "目录"}:
        return True
    return None


def _outline_row(line: str) -> tuple[int, str] | None:
    """`序号|规范名` → `(序号, 名字)`；序号不是整数或名字为空回 `None`（该行不采用）。

    模型常会照抄输入的 `序号|层级|名字` 三列再在末尾补一列：第二列是 1／2／3 时按「层级」吞掉。
    名字段允许含竖线（只切前两刀），因为丢掉一条条目比多留一枚竖线更难查。
    """
    parts = line.split("|")
    if len(parts) < 2:
        return None
    index_text = parts[0].strip()
    if not index_text.isdigit():
        return None
    if len(parts) == 2:
        name = parts[1].strip()
    elif parts[1].strip() in ("1", "2", "3"):
        name = "|".join(parts[2:]).strip()
    else:
        name = "|".join(parts[1:]).strip()
    if not name:
        return None
    return int(index_text), name[:200]


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


def _revision_block(revision: RevisionContext | None) -> list[str]:
    """重写上下文段（`KA-48`）：修改建议与现有成品成对出现，两样都空＝整段不出现。

    与批次备注（`_review_note_block`）分两段：备注是「上一轮批注哪里错了」，本段是「这一篇怎么写更好」。
    建议只调写法与详略，事实与结论仍以正文材料为准——底稿因此标成「可以增删改写的底稿」而不是事实源。
    """
    if revision is None:
        return []
    note = revision.note.strip()
    previous = revision.previous.strip()
    if not note and not previous:
        return []
    lines: list[str] = [""]
    if note:
        lines.extend(["本次修改建议（按它调整写法与详略；事实与结论以正文材料为准）：", note, ""])
    if previous:
        lines.extend([f"现有{revision.previous_kind}（这次重写以它为底稿，可以增删改写）：", previous, ""])
    return lines


def _points_prompt(
    section: SectionInput, review_note: str = "", revision: RevisionContext | None = None
) -> str:
    parent = section.parent_id or "（无：本段不在目录树上）"
    lines = [_POINTS_PROMPT]
    if section.members:
        lines.append(_MERGED_POINTS_PROMPT)
    elif section.blocks:
        lines.append(_BLOCK_POINTS_NOTE)
    lines.extend(
        [
            f"节标题：{section.title or '（目录未覆盖区间）'}",
            f"页区间：{section.page_start}..{section.page_end}",
            f"所属节点：{parent}",
            "",
            "节内正文：",
            "",
        ]
    )
    if section.members:
        for ordinal, member in enumerate(section.members, start=1):
            lines.append(f"知识点 {ordinal}（id={member.node_id}）：{member.title}")
            lines.append("")
            _block_lines(lines, member.blocks)
    elif section.blocks:
        _block_lines(lines, section.blocks)
    else:
        for page in section.pages:
            lines.append(f"[页 {page.page_no}]")
            lines.append(page.markdown.rstrip())
            lines.append("")
    lines.extend(_review_note_block(review_note))
    lines.extend(_revision_block(revision))
    return "\n".join(lines)


def _block_lines(lines: list[str], blocks: Sequence[SectionBlock]) -> None:
    """块取材的正文面（`KA-64`）：每块一枚「p{页}·b{序}」前缀，块间空行。"""
    for block in blocks:
        lines.append(f"p{block.page}·b{block.block_index}")
        lines.append(block.text.rstrip())
        lines.append("")


_CHAPTER_PROMPT = """\
你是教材章节总结与导读作者。本轮只处理一章，材料分三块：章前正文（按「p页·b块序」逐块给出，
只含首个知识点之前的内容）、其下各知识点的节级总结清单、以及可选的检索资料（用于校准术语与定义）。
只谈这一章，不写全书结构与后续章节。

任务：为这一章写「本章学习内容」（`chapter_summary`，屏上栏名，正文是纯知识概述）与
「本章导读」（`chapter_narrative`）。
语言专业、精炼、准确；两段都只依据本章材料与检索资料，事实与结论以教材正文为准。

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏，形如
  {"chapter_summary": "学习内容正文", "chapter_narrative": "本章导读"}）：
- chapter_summary 是纯知识概述：一段通顺连贯的话（约 120~280 字），
  直接说清这里的数学对象、要解决的问题与结论链。
  写法：
  · 开头直接点出要学的数学对象与要解决的具体问题（例如「实数系为什么需要完备性」），
    不要用「本章」「这一章」「整章」这类自称；
  · 中段给结论链：由哪几个定义、定理或构造把问题解决到什么程度，条件和结论都点出具体名称；
  · 覆盖本章主要知识点为尽力目标；有些点没了解全时，不要为了凑全而改写语句意义；
  · 不写页数、页区间、知识点数量；不写「本章位于全书…」「本书／全书／这门课」这类全书结构；
    不写「为第 X 章…」「后面哪一章会用到」这类后续章节安排，也不写这一章在课程里的用途
    （如「为处理…提供理论支撑」「为后续学习打下基础」）；
  · 不拼凑：不用「首先／其次／最后」硬串，不逐条复述节级总结，把各点还原成结论再串成链条；
  · 术语第一次出现时给一句白话解释，长句拆成短句。
- chapter_narrative：一篇**只谈本章**的导读（约 350~650 字，第一轮可以详写），分两段，像一位老师
  带着学生走一遍这一章：
  · 第一段讲这一章的总问题，以及它难在哪个具体环节；
  · 第二段按材料顺序讲各知识点怎样推进到结论：每一层点明用到的定义、定理或构造，说清它解决了
    上一层留下的什么，相邻两节之间写出「上一步留下了什么，下一步要做什么」；
  · 收尾停在知识推进上：不写「学完这一章……」或「这套理论是……基石」这类能力宣言与励志陈词，
    不写后面哪一章会用到、不写全书位置；
  · 写推进关系而不是逐条复述节级总结；语言像讲课时的口语引导，短句、具体的比喻都可以，
    空泛的口号不要；
  · 每一段的论断都能在材料或检索资料里找到依据，材料之外的定理与例子不写。
- 检索资料只用于校准术语与规范定义；它没有覆盖的内容按教材正文写，教材结论与检索资料不一致时
  以教材为准。没有检索资料时只按章前正文与子点总结写，不因此改变上述口径。
"""


_COURSE_CHAPTER_PROMPT = (
    "你是课程级章节总结与导读作者。本轮把多本教材中对应章节的章级总结与子点总结归并成一章。\n"
    "材料不含页码或块锚，不得补造出处坐标。\n"
    + _CHAPTER_PROMPT[_CHAPTER_PROMPT.index("只谈这一章") :].replace(
        "只按章前正文与子点总结写", "只按多教材同章材料写"
    )
)


def _reference_block(notes: Sequence[ReferenceNote]) -> list[str]:
    """检索资料段（PLAN-033）：有资料才出现；标注来源与条目名，提示模型这是校准面而不是事实源。"""
    if not notes:
        return []
    lines = ["检索资料（只用于校准术语与定义；教材结论优先）：", ""]
    for note in notes:
        lines.append(f"- [{note.source}·{note.title}] {note.text}")
    lines.append("")
    return lines


def _retry_block(issues: Sequence[str]) -> list[str]:
    """同模型重试的修改要求（PLAN-033）：只在确定性校验未过时出现，逐条点名要改什么。"""
    cleaned = [issue.strip() for issue in issues if issue.strip()]
    if not cleaned:
        return []
    lines = ["本次修改要求（上一版命中以下问题，请改写；事实与结论仍以材料与检索资料为准）："]
    lines.extend(f"- {issue}" for issue in cleaned)
    lines.append("")
    return lines


def _chapter_prompt(
    material: ChapterInput, review_note: str = "", revision: RevisionContext | None = None
) -> str:
    lines = [
        _CHAPTER_PROMPT,
        f"章标题：{material.title}",
        "",
    ]
    if material.blocks:
        lines.extend(["章前正文（每块前是「p页·b块序」）：", ""])
        _block_lines(lines, material.blocks)
    else:
        lines.extend(["章前正文：（这一章没有单独的章前正文）", ""])
    lines.extend(["其下各知识点的节级总结：", ""])
    if material.point_summaries:
        for title, summary in material.point_summaries:
            lines.append(f"- {title}：{summary}")
    else:
        lines.append("- （其下各知识点都没有总结）")
    lines.append("")
    lines.extend(_reference_block(material.reference_notes))
    lines.extend(_review_note_block(review_note))
    lines.extend(_revision_block(revision))
    lines.extend(_retry_block(material.retry_issues))
    return "\n".join(lines)


def _chapter_text_prompt(material: ChapterTextInput) -> str:
    lines = [_COURSE_CHAPTER_PROMPT, f"章标题：{material.chapter_name}", "", "现有课程章材料：", ""]
    if material.chapter_summary:
        lines.extend([f"现有课程章总结：{material.chapter_summary}", ""])
    if material.chapter_narrative:
        lines.extend([f"现有课程章导读：{material.chapter_narrative}", ""])
    lines.extend(["多教材同章材料：", ""])
    for source in material.sources:
        title = f"{source.title}（{source.book_id}）" if source.title else source.book_id
        lines.extend([f"教材来源：{title}", f"同章名：{source.chapter_name}"])
        if source.summary:
            lines.append(f"章级总结：{source.summary}")
        lines.append("子点总结：")
        for point in source.points:
            lines.append(f"- {point.name}：{point.summary}")
        lines.append("")
    lines.extend(_reference_block(material.reference_notes))
    lines.extend(_retry_block(material.retry_issues))
    return "\n".join(lines)


_EXERCISE_PROMPT = """\
你是习题集的题目与解答切分器。本轮只处理一节；题号与每题的正文边界已由规则给出，不得增删题号、
不得改题号、不得自行分题。

任务：对给出的每一道题，只看这一题自己的正文（每行前面有行号），判断解答从第几行开始、到第几行
结束——只报行号，不要抄写任何正文。

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏）：
{"items": [{"item_no": "题号原文", "solution_start": 行号, "solution_end": 行号}]}
- item_no 照抄输入方括号里的题号，一个字不改。
- solution_start：解答第一行前面的行号；这一题没有解答给 0；整段都是解答（题面为空）给 1。
- solution_end：解答最后一行的行号（含）。解答一直延续到本题末尾时给本题最后一行的行号；
  判不出结束位置时给 -1——程序会按「延续到本题末尾」处理，不会猜一个截断点。
- 判不出解答起点时 solution_start 给 -1，不要猜；行号按输入里每行前面的数字，空行也有行号。
- 每道题给且只给一个对象，对象数必须与输入题数相同。
"""


def _exercise_prompt(section: ExerciseInput, review_note: str = "") -> str:
    """题腿 prompt（`KA-56`）：题号边界由规则给，模型只判解答起始行，正文由程序切出。"""
    lines = [
        _EXERCISE_PROMPT,
        f"节标题：{section.title or '（目录未覆盖区间）'}",
        f"页区间：{section.page_start}..{section.page_end}",
        f"本节题数：{len(section.items)}",
        "",
        "各题正文（方括号里是题号；每行前面的数字是行号）：",
        "",
    ]
    for item in section.items:
        lines.append(f"[{item.item_no}]（第 {item.page_start}..{item.page_end} 页）")
        for index, line in enumerate(item.text.splitlines(), start=1):
            lines.append(f"  {index}| {line}")
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


_EXERCISE_MATCH_PROMPT = """\
你是习题集与教材的目录匹配器。输入是一本习题集的顶层章名，和若干本候选教材（编号、书名、
各自的顶层章名与节名）。

任务：
1. 判断这本习题集最可能配套哪一本候选教材；没有一本对得上时 book_id 给 null。
2. 选中后，把习题集的每一章对应到该教材的一章：source 照抄习题集的章名，target 照抄教材的
   章名。对不上的章不要猜，也不要给该章的对象。

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏）：
{"book_id": "候选编号或 null", "chapters": [{"source": "章名原文", "target": "教材章名原文"}]}
- book_id 只能是输入候选编号之一，或 null。
- 章名一字不改，按输入原文照抄。
"""


def _exercise_match_prompt(material: ExerciseMatchInput, review_note: str = "") -> str:
    """习题集书目匹配腿的 prompt（REQ-161 W4）：本书章名＋候选教材各自的章名，只做选择与对应。"""
    lines = [_EXERCISE_MATCH_PROMPT, f"习题集：{material.title}", "", "本书顶层章名："]
    if material.chapters:
        lines.extend(f"- {name}" for name in material.chapters)
    else:
        lines.append("- （无）")
    lines.extend(["", "候选教材："])
    for candidate in material.candidates:
        lines.append(f"[{candidate.book_id}] {candidate.title}")
        if candidate.chapters:
            lines.extend(f"  - {name}" for name in candidate.chapters)
        else:
            lines.append("  - （无目录行）")
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


_EXERCISE_CLASSIFY_PROMPT = """\
你是习题集的逐题判读器。对给出的每一道题只做两件事：
1. 判断它是否对应候选教材中的某一道题：对应时 target 照抄那道题的题号；没有候选或对不上时
   target 给空串。
2. 判断题型：从「选择题、填空题、判断题、计算题、证明题、应用题、作图题、简答题、其他」
   里选一个，只写这个词。

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏）：
{"items": [{"item_no": "题号原文", "target": "教材题号或空串", "kind": "题型"}]}
- item_no 照抄输入方括号里的题号，一个字不改；每道题给且只给一个对象。
- 对不上、拿不准就给空串，不要猜。
"""

#: 读数腿的提示词预算：读数只是判据，不复制整节原文；候选题截得更短，够认题即可。
_READING_ITEM_CHARS = 800
_READING_TARGET_CHARS = 600
_READING_TARGET_LIMIT = 40


def _excerpt(text: str, limit: int) -> str:
    """单行化并截断到预算：模型读的是判据，不是原文副本。"""
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + " …"


def _exercise_classify_prompt(material: ExerciseClassifyInput, review_note: str = "") -> str:
    """逐题读数腿的 prompt（REQ-161 W5）：本节各题＋教材同一章候选题，各带一段截断正文。"""
    lines = [_EXERCISE_CLASSIFY_PROMPT, f"节标题：{material.title}", ""]
    if material.targets:
        lines.append(f"候选教材题目（《{material.target_title or '目标教材'}》同一章）：")
        for number, text in material.targets[:_READING_TARGET_LIMIT]:
            lines.append(f"[{number}] {_excerpt(text, _READING_TARGET_CHARS)}")
        lines.append("")
    else:
        lines.extend(["候选教材题目：（没有目标教材，target 一律给空串）", ""])
    lines.append("本节各题：")
    for number, text in material.items:
        lines.append(f"[{number}] {_excerpt(text, _READING_ITEM_CHARS)}")
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


_EXERCISE_VERIFY_PROMPT = """\
你是习题配对的校验器。输入是已经配好的一段「题目＋解答」对，每对带题号。

任务：对每一对判四件事：
1. 切分是否正确：解答段是否从该题的解答开头起，有没有把下一题的题面或解答抄进来，
   或者把本题题面误算进了解答。
2. 题面与解答是否同一题：这一段解答是否在解这一道题，而不是上一题、下一题或别的题的解答。
3. 数学结论是否正确：解答的推导与最终结论在数学上是否成立。
4. 解答是否完整：是否只写了一半、缺关键步骤或有待补全的占位，需要补全才能算完整。

英文的题面与解答先在内部逐段译成中文，再按上面四件事判；译文不进输出，note 用中文写。

输出要求（只输出一个 JSON 对象，不写解释、不写代码围栏）：
{"items": [{"item_no": "2.5", "verdict": "ok、suspect 或 reject", "note": "一句中文理由"}]}
- item_no 写裸题号：输入方括号里的内容照抄、外层方括号不要带回来（输入 [2.5] → 输出 "2.5"），
  圆括号、【】等包装同样去掉，也不要加「题」字；每道题给且只给一个对象。
- 四件事都过才是 ok，note 给空串。
- 拿不准、看着像错位或不完整，给 suspect，并在 note 里一句话说明哪里不对。
- 明确错了（解答不是这一题的、数学结论错误等）给 reject，同样在 note 里说明。
"""

#: 校验腿的单题预算：题面与解答各截一段，判读不追求逐字校对原文。
_VERIFY_PROBLEM_CHARS = 900
_VERIFY_SOLUTION_CHARS = 1200
_VERIFY_VERDICTS = ("ok", "suspect", "reject")


def _exercise_verify_prompt(material: ExerciseVerifyInput, review_note: str = "") -> str:
    """配对校验腿的 prompt（REQ-170 第三步）：本章各题对逐对给出题面与解答两段。"""
    lines = [_EXERCISE_VERIFY_PROMPT, f"章标题：{material.title}", "", "待检的题对：", ""]
    for item in material.items:
        lines.append(f"[{item.item_no}]")
        lines.append(f"题目：{_excerpt(item.problem, _VERIFY_PROBLEM_CHARS)}")
        lines.append(f"解答：{_excerpt(item.solution, _VERIFY_SOLUTION_CHARS)}")
        lines.append("")
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


def _slice_exercise_text(text: str, start: Any, end: Any = None) -> tuple[str, str]:
    """题腿的正文拼装（`KA-56`）：模型给出解答起止行号，两段从输入原文切出。

    `start` 0＝这一题没有解答（整段都是题面）；越界、非整数或 -1＝切分点没确认，两段都取空串，
    由编排层按「题面未取到」披露——不猜、也不把整段当题面。行序与公式原样保留。

    `end`（REQ-179 R4）是解答最后一行的行号（含）：合法（整数、域内）且 ≥ start 时按双端切，
    截住越界到下一题的长尾；`1 <= end < start` 是矛盾读数——解答按空处理（与 start 域外同一
    「不猜」档，不硬切出一段正文）；`-1`（prompt 约定的「判不出」）、非正数与缺席或其余非法形状
    （非整数、越界）一律回退「切到本题末尾」。
    """
    lines = text.splitlines()
    if isinstance(start, bool) or not isinstance(start, int):
        return "", ""
    if start == 0:
        return "\n".join(lines).strip(), ""
    if not 1 <= start <= len(lines):
        return "", ""
    head = "\n".join(lines[: start - 1]).strip()
    tail = "\n".join(lines[start - 1 :]).strip()
    if isinstance(end, int) and not isinstance(end, bool):
        if end == -1 or end <= 0 or end > len(lines):
            return head, tail  # 判不出 / 非法形状：延续到本题末尾，不猜截断点
        if end < start:
            return head, ""  # 矛盾读数：不硬切出一段正文
        return head, "\n".join(lines[start - 1 : end]).strip()
    return head, tail  # end 缺席或非整数：判不出，延续到本题末尾


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


def _toc_page_prompt(page_no: int, text: str, review_note: str = "") -> str:
    """逐页判页腿的 prompt（P-2／P-3）：整页原文不截断，判定与条目要在同一次读数里回来。"""
    lines = [_TOC_PAGE_PROMPT, f"本页页号：{page_no}", "", "整页原文：", "", text, ""]
    lines.extend(_review_note_block(review_note))
    return "\n".join(lines)


def _toc_outline_prompt(rows: Sequence[TocOutlineRow], review_note: str = "") -> str:
    lines = [_TOC_OUTLINE_PROMPT, ""]
    for row in rows:
        lines.append(f"{row.index}|{row.level}|{row.name}")
    lines.append("")
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
        self._chapter_text: tuple[str, str, str] | None = None
        self._review_note = ""
        self._revision: RevisionContext | None = None

    def bind_batch(
        self,
        *,
        book_id: str,
        gen_batch: str,
        review_note: str = "",
        revision: RevisionContext | None = None,
    ) -> KpGatewayEngine:
        """把当前批次身份交给适配器（返回自身，装配点一行接完）。

        一枚实例只服务一批：`_run_knowledge` 每批新构造，重绑就是换了主人——账跟着走，
        不会把后一批的调用记到前一批头上。

        `review_note` 一并交进来（AF-T5 甲）：它是**这一轮**的上下文，跟着批次身份走同样的
        一次性绑定，执行体不必再为备注开第二条通路。`revision` 属于单点重写（`KA-48`），每次
        绑定都会整个换掉——没传即清空，免得上一枚请求的建议漏进下一枚。
        """
        self._batch = (book_id, gen_batch)
        self._chapter_text = None
        self._review_note = review_note
        self._revision = revision
        return self

    def bind_chapter_text(self, *, course_id: str, chapter_kp_id: str, evidence_sha256: str) -> KpGatewayEngine:
        """绑定课程级章腿的显式目标与证据哈希；账目只在 8900 落。"""
        self._batch = None
        self._chapter_text = (course_id, chapter_kp_id, evidence_sha256)
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

    def current_model_name(self) -> str:
        """只读问一句「当前文字槽位是哪个模型」（PLAN-033 W5）：仅给异常注记用，答不上来就空串。

        8900 的 `/api/v1/llm/text` 不接收逐调用 `model`，生成走当前生效槽位；本仓不切换槽位、
        也不为一次注记多花模型调用。探测失败（不可达、非 200、形状不符）一律回空串——注记里
        少一个名字不构成阻断，生成照跑。
        """
        try:
            response = self._http().get(
                f"{self.base_url}{_CONFIG_MODELS_PATH}",
                timeout=min(self.timeout, _MODEL_PROBE_TIMEOUT),
            )
            if response.status_code != 200:
                return ""
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return ""
        default = payload.get("default") if isinstance(payload, dict) else None
        if not isinstance(default, dict):
            return ""
        return str(default.get("model") or "").strip()

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

    def toc_page(self, page_no: int, text: str) -> TocPageReading:
        """逐页判页腿（P-2／P-3）：整页原文 → 判定＋同页条目；空回复或判定行不可解析判该页失败。

        判定行只认两种结论（`TOC`／`NOT_TOC`，空格与下划线不敏感）；判定为 TOC 时后面的竖线行
        就是本页条目，坏一行只坏一行，**有内容行却一行都解不出**判该页失败、由编排层回落确定性
        条目腿——那与本页「没有条目」不是一回事。
        """
        reply = self._post_text(
            "toc_page",
            _toc_page_prompt(page_no, text, self._review_note),
            {"toc_page": page_no, "chars": len(text)},
        )
        if not reply.strip():
            raise EngineError(f"模型空回复（toc_page 第 {page_no} 页）：该页判定失败回落条目腿")
        lines = [line.strip() for line in _strip_fence(reply).splitlines() if line.strip()]
        if not lines:
            raise EngineError(f"模型回复没有可用行（toc_page 第 {page_no} 页）：该页判定失败回落条目腿")
        verdict = _toc_verdict(lines[0])
        if verdict is None:
            raise EngineError(f"模型回复的判定行不可解析（toc_page 第 {page_no} 页）：{lines[0][:60]}")
        if not verdict:
            return TocPageReading(is_toc=False)
        entries = [parsed for line in lines[1:] if (parsed := _pipe_row(line)) is not None]
        if not entries and len(lines) > 1 and not lines[1].upper().startswith("NONE"):
            raise EngineError(
                f"模型回复的竖线行全不可解析（toc_page 第 {page_no} 页）：按该页整理失败回落条目腿"
            )
        return TocPageReading(is_toc=True, entries=tuple(entries))

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

    def toc_outline(self, rows: Sequence[TocOutlineRow]) -> list[TocOutlineRow]:
        """层级校订腿（`KA-57`）：整段抄录交来的章／节行 → 只含**改动行**的订正表。

        页码不在输入里，也不在输出里：整段抄录已经把页码从原文取出来了，这一腿只做名字校订。
        行下标不在输入里的订正整条弃用（模型多报的行会替书造一条不存在的条目）；同一序号只认第一枚。
        空回复判 `EngineError`（多半是输出被截断），解析不出任何行则按「没有订正」返回空表——
        校订是可选腿，坏了只让名字保持原文，不该让整批目录失败。
        """
        if not rows:
            return []
        reply = self._post_text(
            "toc_outline",
            _toc_outline_prompt(rows, self._review_note),
            {"count": len(rows), "names": [row.name[:60] for row in rows][:20]},
        )
        if not reply.strip():
            raise EngineError("模型空回复（toc_outline）：整段目录保持原文抄录")
        body = _strip_fence(reply)
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if len(lines) == 1 and lines[0].upper().startswith("NONE"):
            return []
        by_index = {row.index: row for row in rows}
        corrected: dict[int, TocOutlineRow] = {}
        for line in lines:
            parsed = _outline_row(line)
            if parsed is None:
                continue
            index, name = parsed
            original = by_index.get(index)
            if original is None:
                logger.warning("目录校订给出输入里没有的序号，整条弃用（toc_outline）：%s", index)
                continue
            corrected.setdefault(
                index, TocOutlineRow(index=index, level=original.level, name=name)
            )
        return list(corrected.values())

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
            _points_prompt(section, self._review_note, self._revision),
            {
                "section": section.title,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "page_count": len(section.pages)
                or len({block.page for block in section.blocks}),
            },
        )
        return self._section_points(reply, "points", merged=bool(section.members))

    def chapter(self, material: ChapterInput) -> ChapterReply:
        """章腿（`KA-45`）：章首正文＋其下各节总结 → 章级总结与导读一段一次调用。

        读取与节腿同一条通路（`_post_text`），归因 `step='chapter'`：章调用在账上认得出是章，
        而不是又一枚「points」。
        """
        reply = self._post_text(
            "chapter",
            _chapter_prompt(material, self._review_note, self._revision),
            {
                "chapter": material.title,
                "page_start": material.page_start,
                "page_end": material.page_end,
                "material_pages": sorted({block.page for block in material.blocks}),
                "point_count": len(material.point_summaries),
            },
        )
        return self._chapter_reply(reply)

    def chapter_text(self, material: ChapterTextInput) -> ChapterReply:
        """课程级多书同章归并；目标与证据哈希决定 8900 账目身份。"""
        reply = self._post_text(
            "chapter",
            _chapter_text_prompt(material),
            {
                "course_id": material.course_id,
                "chapter_kp_id": material.chapter_kp_id,
                "source_count": len(material.sources),
                "point_count": sum(len(source.points) for source in material.sources),
            },
            course_text=True,
        )
        return self._chapter_reply(reply)

    def _chapter_reply(self, reply: str) -> ChapterReply:
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

    def exercise(self, section: ExerciseInput) -> ExerciseReply:
        """题腿（`KA-53`）：规则给出的题边界 ＋ 模型只确认切分点（`KA-56`）。

        模型回一行一题的行号（解答从第几行起），题面与解答的正文由程序按原文切出——模型不再转写
        原文，既省输出也免抄错。题号在适配层折回归一键（`【7】` 与 `7` 同键），切分点按同一键取回
        对应的原文；模型多给的题号丢弃，少给的由编排层按「题面未取到」披露。行号非法（缺键、
        非整数、`-1`、越界）一律切成两段空串，不替模型猜位置，也不因一条坏对象判整节失败。
        """
        reply = self._post_text(
            "exercise",
            _exercise_prompt(section, self._review_note),
            {
                "exercise_section": section.title,
                "page_start": section.page_start,
                "page_end": section.page_end,
                "item_count": len(section.items),
            },
        )
        if not reply.strip():
            raise EngineError("模型空回复（exercise）：不折算成零题，按节失败披露")
        payload = self._object(reply, "exercise")
        raw = payload.get("items")
        if not isinstance(raw, list):
            raise EngineError("题腿回复缺 items 数组（exercise）")
        sources: dict[str, str] = {}
        for item in section.items:
            sources.setdefault(item_number(item.item_no) or item.item_no.strip(), item.text)
        drafts: list[ExerciseDraft] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            number = item_number(str(entry.get("item_no") or "")) or str(entry.get("item_no") or "").strip()
            if not number:
                continue
            problem, solution = _slice_exercise_text(
                sources.get(number, ""), entry.get("solution_start"), entry.get("solution_end")
            )
            drafts.append(ExerciseDraft(item_no=number, problem=problem, solution=solution))
        return ExerciseReply(items=tuple(drafts))

    def exercise_match(self, material: ExerciseMatchInput) -> ExerciseMatchChoice:
        """习题集目录匹配腿（REQ-161 W4）：本书章名 vs 候选教材章名 → 选中的书与章对应。

        选中的书必须是输入候选之一——回一个域外书号时按「没有对得上」处理并留一条警告，不把它
        当成命中，否则来源书清单会指到一本没读过目录的书上。章对应由编排层再按两边原名校一遍，
        这里只收敛形状。
        """
        reply = self._post_text(
            "exercise_match",
            _exercise_match_prompt(material, self._review_note),
            {
                "title": material.title,
                "chapter_count": len(material.chapters),
                "candidate_count": len(material.candidates),
                "candidates": [candidate.book_id for candidate in material.candidates][:20],
            },
        )
        if not reply.strip():
            raise EngineError("模型空回复（exercise_match）：不折算成没有匹配，按调用失败披露")
        payload = self._object(reply, "exercise_match")
        chosen = payload.get("book_id")
        book_ids = {candidate.book_id for candidate in material.candidates}
        if not isinstance(chosen, str) or chosen not in book_ids:
            if chosen is not None:
                logger.warning("习题集匹配回了域外书号（%r）：按没有对得上处理", chosen)
            return ExerciseMatchChoice()
        pairs: list[tuple[str, str]] = []
        raw = payload.get("chapters")
        if isinstance(raw, list):
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                source = str(entry.get("source") or "").strip()
                target = str(entry.get("target") or "").strip()
                if source and target:
                    pairs.append((source, target))
        return ExerciseMatchChoice(book_id=chosen, chapters=tuple(pairs))

    def exercise_classify(self, material: ExerciseClassifyInput) -> ExerciseClassifyReply:
        """逐题读数腿（REQ-161 W5）：模型只判「对应哪道教材题」与题型，不碰切分与落行。

        题号是否属于本节、对应题是否真在候选里，由编排层再校一遍；这里只收敛形状。
        空回复判故障——它多半是输出被截断，静默回零条会在题包里长得像「每题都不对应」。
        """
        reply = self._post_text(
            "exercise_classify",
            _exercise_classify_prompt(material, self._review_note),
            {
                "title": material.title,
                "item_count": len(material.items),
                "target_count": min(len(material.targets), _READING_TARGET_LIMIT),
            },
        )
        if not reply.strip():
            raise EngineError("模型空回复（exercise_classify）：不折算成没有读数，按调用失败披露")
        payload = self._object(reply, "exercise_classify")
        raw = payload.get("items")
        if not isinstance(raw, list):
            raise EngineError("读数腿回复缺 items 数组（exercise_classify）")
        readings: list[ExerciseClassifyItem] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            number = str(entry.get("item_no") or "").strip()
            if not number:
                continue
            readings.append(
                ExerciseClassifyItem(
                    item_no=number,
                    target=str(entry.get("target") or "").strip(),
                    kind=str(entry.get("kind") or "").strip()[:20],
                )
            )
        return ExerciseClassifyReply(items=tuple(readings))

    def exercise_verify(self, material: ExerciseVerifyInput) -> ExerciseVerifyReply:
        """配对校验腿（REQ-170 第三步，`KA-70`）：逐对判切分、同一题、数学结论与解答完整性。

        英文题面与解答在提示词里要求先译成中文再判，note 用中文。结论闭集 `ok`／`suspect`／
        `reject`：域外取值按 `suspect` 收（没听清就不许冒充合格，交人看）。
        空回复判故障——它多半是输出被截断，静默回零条会让整章长得像「全部合格」。
        """
        reply = self._post_text(
            "exercise_verify",
            _exercise_verify_prompt(material, self._review_note),
            {"title": material.title, "item_count": len(material.items)},
        )
        if not reply.strip():
            raise EngineError("模型空回复（exercise_verify）：不折算成全部合格，按调用失败披露")
        payload = self._object(reply, "exercise_verify")
        raw = payload.get("items")
        if not isinstance(raw, list):
            raise EngineError("校验腿回复缺 items 数组（exercise_verify）")
        readings: list[ExerciseVerifyReading] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            number = str(entry.get("item_no") or "").strip()
            if not number:
                continue
            verdict = str(entry.get("verdict") or "").strip().lower()
            if verdict not in _VERIFY_VERDICTS:
                verdict = "suspect"
            readings.append(
                ExerciseVerifyReading(
                    item_no=number,
                    verdict=verdict,
                    note=str(entry.get("note") or "").strip()[:300],
                )
            )
        return ExerciseVerifyReply(items=tuple(readings))

    def _post_text(
        self, step: str, prompt: str, meta: dict[str, Any], *, course_text: bool = False
    ) -> str:
        """一次调用：出站唯一、归因随体、四类故障各归一条 `EngineError` 文案。"""
        if course_text:
            if self._chapter_text is None:
                raise EngineError("梳理网关未绑定 chapter_text 目标与证据哈希，拒跑课程级章腿")
            course_id, chapter_kp_id, evidence_sha256 = self._chapter_text
            payload = {
                "prompt": prompt,
                "service": LLM_CALL_SERVICE,
                "task": f"kp-merge-chapter-{evidence_sha256[:12]}",
                "step": step,
                "prompt_template": f"kp-merge-chapter/{evidence_sha256}@{CHAPTER_TEXT_PROMPT_VERSION}",
                "meta": {
                    "course_id": course_id,
                    "chapter_kp_id": chapter_kp_id,
                    "evidence_sha256": evidence_sha256,
                    "engine": self.name,
                    **meta,
                },
            }
        else:
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
            return self._post_text_once(payload, step)
        except EngineError as exc:
            if not course_text:
                raise
            try:
                return self._recover_course_text(payload)
            except EngineError as recovery_exc:
                raise EngineError(
                    f"{exc}；课程级出站结果不确定：{recovery_exc}（不自动重发）"
                ) from exc

    def _post_text_once(self, payload: dict[str, Any], step: str) -> str:
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

    def _recover_course_text(self, payload: dict[str, Any]) -> str:
        try:
            response = self._http().get(
                f"{self.base_url}{_CALLS_PATH}",
                params={
                    "service": payload["service"],
                    "task": payload["task"],
                    "step": payload["step"],
                    "status": "success",
                    "prompt_template": payload["prompt_template"],
                    "size": 200,
                },
                timeout=min(self.timeout, _MODEL_PROBE_TIMEOUT),
            )
            if response.status_code != 200:
                raise EngineError(f"账本查询返回 HTTP {response.status_code}")
            body = response.json()
        except httpx.HTTPError as exc:
            raise EngineError(f"账本不可达（{type(exc).__name__}）") from exc
        except ValueError as exc:
            raise EngineError("账本响应不是 JSON") from exc
        if not isinstance(body, dict) or not isinstance(body.get("items"), list):
            raise EngineError("账本响应形状不可核对")
        total = body.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total != len(body["items"]):
            raise EngineError("账本总数与返回行数不一致")
        if total > 200:
            raise EngineError("账本匹配超过单页上限，无法证明唯一")
        matches = [
            row["response"]
            for row in body["items"]
            if isinstance(row, dict)
            and all(row.get(key) == payload[key] for key in ("service", "task", "step", "prompt_template"))
            and row.get("status") == "success"
            and row.get("prompt") == payload["prompt"]
            and isinstance(row.get("response"), str)
        ]
        if len(matches) != 1:
            raise EngineError(f"账本精确成功匹配为 {len(matches)} 条")
        return matches[0]

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

    def _section_points(self, reply: str, step: str, *, merged: bool = False) -> SectionPoints:
        """回复 → 一节的两半（`KA-43`）：外层认两种形状，内层点行仍只有一种闭集。

        先看首个 `{` 与首个 `[` 谁在前：对象在前走对象支，否则走老的数组支。对象支**不可用**
        （解析不出、或没有一枚数组形的 `points`）而回复里另有数组时回落数组支——模型会把示例写成
        `{name: ...}` 摆在正文里，那枚假对象不该吃掉整节；回复里根本没有数组才判故障，不折算成
        零产出（`KA-32`：坏形状是故障，不是「这一节没有知识点」）。

        合批调用（`merged`，`KA-64`）只认 `{"sections": [{"id", "section_summary", "points"}]}`：
        身份按 `id` 对账，缺席的点由编排层记失败节。整段解析不出来才判这一笔故障（整组点一起失败）。
        """
        if merged:
            return self._member_points(reply, step)
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

    def _member_points(self, reply: str, step: str) -> SectionPoints:
        """合批回复 → 逐点结果（`KA-64`）：`id` 照抄可回指，缺项与坏项的区别留给编排层点名。"""
        if not reply.strip():
            raise EngineError(f"模型空回复（{step}）：合批组整组失败披露")
        payload = self._object(reply, step)
        rows = payload.get("sections")
        if not isinstance(rows, list):
            raise EngineError(f"模型回复缺可用的 sections 数组（{step}）：{reply[:120]}")
        members: list[MemberPoints] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("id") or "").strip()
            if not node_id:
                continue
            drafts = item.get("points")
            summary = item.get("section_summary")
            members.append(
                MemberPoints(
                    node_id=node_id,
                    drafts=[draft for draft in drafts if isinstance(draft, dict)]
                    if isinstance(drafts, list)
                    else [],
                    summary=summary.strip() if isinstance(summary, str) else "",
                )
            )
        if not members:
            raise EngineError(f"模型回复的 sections 没有可用项（{step}）：{reply[:120]}")
        return SectionPoints([], "", tuple(members))
