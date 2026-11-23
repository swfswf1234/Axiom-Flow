"""章级检索资料源（PLAN-033 W3）：只用公开只读接口校准术语与定义，不为结论找依据。

章腿的模型上下文由两块构成：教材自己的正文与子点总结、以及本模块取回的「检索资料」。
检索是**增强**而不是事实源：取不到、超时、条目缺失都只返回空表，不阻断生成；教材结论优先。

取词是确定性的：章标题与子点名称去掉章／节编号后去重，至多八条。取源按可信度排序——
中文维基 → 英文维基 → Encyclopedia of Mathematics；每源一次批量请求（MediaWiki `action=query`
的 `titles` 多值），命中一条即停，不让后面的源重复查同一条。摘要只取首段、清洗后单条至多 400 字、
整包至多 3,000 字；结果按（源, 词）进程内缓存，不落库不落盘。

HTTP 客户端恒以 `trust_env=False` 构造（`MI-10`：回环与直连都不走系统代理），并带说明用途的
User-Agent——维基媒体会拒绝没有 UA 的匿名请求。默认门禁一律注入替身，本模块不自行出站。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from axiom_flow.engines.base import ReferenceNote

logger = logging.getLogger(__name__)

#: 单次检索的取词上限：再多的词只会换来一堆同义条目，反而把材料预算挤掉。
QUERY_LIMIT = 8
#: 单条资料与整包的字数上限（用户口径：长度是推荐，但检索资料必须有硬预算）。
NOTE_CHARS = 400
TOTAL_CHARS = 3000
#: 单源批量请求的等待预算：检索失败可以空手，不让整批章腿陪它等。
TIMEOUT = 5.0
_USER_AGENT = "AxiomFlow/0.1 (knowledge-reference; local research assistant)"


@dataclass(frozen=True, slots=True)
class _Source:
    label: str
    api: str


_SOURCES: tuple[_Source, ...] = (
    _Source("维基百科", "https://zh.wikipedia.org/w/api.php"),
    _Source("Wikipedia", "https://en.wikipedia.org/w/api.php"),
    _Source("Encyclopedia of Mathematics", "https://encyclopediaofmath.org/api.php"),
)

_HEADING_RE = re.compile(r"==+[^=]+==+")
_CHAPTER_PREFIX_RE = re.compile(r"^第\s*[0-9一二三四五六七八九十百]+\s*[章节篇部]\s*")
_SECTION_PREFIX_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*[.、]?\s*")
_OPTIONAL_SUFFIX_RE = re.compile(r"\s*[（(]选学[）)]\s*$|\s*\*\s*$")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_ENDS = ("。", "；", "！", "？", ". ", "; ")

#: 进程内缓存：键＝（源 API, 查询词）。命中与缺失都缓存——同一条缺失不反复打网络。
_CACHE: dict[tuple[str, str], ReferenceNote | None] = {}


def clear_reference_cache() -> None:
    """清空进程内检索缓存（测试与显式重取用；生产没有调用方）。"""
    _CACHE.clear()


def _subject(text: str) -> str:
    cleaned = _WHITESPACE_RE.sub(" ", (text or "").strip())
    cleaned = _CHAPTER_PREFIX_RE.sub("", cleaned)
    cleaned = _SECTION_PREFIX_RE.sub("", cleaned)
    cleaned = _OPTIONAL_SUFFIX_RE.sub("", cleaned)
    return cleaned.strip()


def reference_query_terms(
    chapter_title: str, point_titles: Sequence[str], *, limit: int = QUERY_LIMIT
) -> tuple[str, ...]:
    """章标题＋子点名称 → 去重后的查询词（确定性、保序，至多 `limit` 条）。

    去掉「第 N 章」「3.1」这类编号与选学星标后再去重：编号进不了维基的条目标题，
    带上只会把本来能命中的词查空。
    """
    terms: list[str] = []
    seen: set[str] = set()
    for title in (chapter_title, *point_titles):
        term = _subject(title)
        key = term.casefold()
        if not term or key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= limit:
            break
    return tuple(terms)


def _clean_extract(text: str, limit: int = NOTE_CHARS) -> str:
    """摘要清洗：去标题行、压空白、按句末截断到 `limit` 字；截不出完整句子时硬截。"""
    cleaned = _HEADING_RE.sub(" ", text or "")
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    for mark in _SENTENCE_ENDS:
        index = cut.rfind(mark)
        if index >= limit // 2:
            return cut[: index + len(mark)].strip()
    return cut.rstrip()


def _final_title(title: str, chain: dict[str, str]) -> str:
    """沿归一化／重定向链找到最终条目名；环由 `seen` 截断。"""
    seen: set[str] = set()
    while title in chain and title not in seen:
        seen.add(title)
        title = chain[title]
    return title


def _fetch_source(client: httpx.Client, source: _Source, terms: Sequence[str]) -> None:
    """一次批量取回一组词的摘要并写缓存；任何网络／形状故障只记一条 warning。"""
    pending = [term for term in terms if (source.api, term) not in _CACHE]
    if not pending:
        return
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "extracts",
        "exintro": "1",
        "explaintext": "1",
        "redirects": "1",
        "titles": "|".join(pending),
    }
    try:
        # 编码面必须显式钉住：httpx 默认的 `br, zstd` 会被维基边缘节点按机器人策略 403，
        # 无论 UA 是否合规（2026-11-09 真机实证：identity／gzip, deflate 均 200）。
        response = client.get(
            source.api,
            params=params,
            headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "gzip, deflate"},
        )
        response.raise_for_status()
        payload = response.json()
        query = payload.get("query") if isinstance(payload, dict) else None
        pages = query.get("pages") if isinstance(query, dict) else None
        if not isinstance(pages, list):
            raise ValueError("检索响应缺 query.pages 数组")
        chain: dict[str, str] = {}
        for key in ("normalized", "redirects"):
            items = query.get(key) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and item.get("from") and item.get("to"):
                    chain[str(item["from"])] = str(item["to"])
        extracts = {
            str(page.get("title")): str(page.get("extract"))
            for page in pages
            if isinstance(page, dict) and page.get("extract")
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        logger.warning("检索源不可用，本轮跳过（%s）：%s", source.label, exc)
        return
    for term in pending:
        raw = extracts.get(term) or extracts.get(_final_title(term, chain))
        text = _clean_extract(raw) if raw else ""
        _CACHE[(source.api, term)] = (
            ReferenceNote(source=source.label, title=term, text=text) if len(text) >= 20 else None
        )


def collect_reference_notes(
    chapter_title: str,
    point_titles: Sequence[str],
    *,
    client: httpx.Client | None = None,
    limit: int = QUERY_LIMIT,
    total_chars: int = TOTAL_CHARS,
) -> tuple[ReferenceNote, ...]:
    """章标题＋子点名称 → 检索资料（维基优先、逐源回退、限长、fail-open）。

    `client` 由调用方注入（测试传替身）；缺省构造的客户端只在本函数内使用，并显式关闭。
    一个词的取词顺序是中文维基 → 英文维基 → 数学百科；都没有就跳过这一条，不让空条目占预算。
    """
    terms = reference_query_terms(chapter_title, point_titles, limit=limit)
    if not terms:
        return ()
    owned = client is None
    if client is None:
        client = httpx.Client(
            timeout=TIMEOUT,
            trust_env=False,
            headers={"User-Agent": _USER_AGENT},
        )
    try:
        notes_by_term: dict[str, ReferenceNote] = {}
        for source in _SOURCES:
            pending = [term for term in terms if term not in notes_by_term]
            if not pending:
                break
            _fetch_source(client, source, pending)
            for term in pending:
                note = _CACHE.get((source.api, term))
                if note is not None:
                    notes_by_term[term] = note
        ordered = [notes_by_term[term] for term in terms if term in notes_by_term]
    finally:
        if owned:
            client.close()
    budgeted: list[ReferenceNote] = []
    used = 0
    for note in ordered:
        remaining = total_chars - used
        if remaining < 50:
            break
        text = note.text if len(note.text) <= remaining else _clean_extract(note.text, remaining)
        if len(text) < 20:
            continue
        budgeted.append(ReferenceNote(source=note.source, title=note.title, text=text))
        used += len(text)
    return tuple(budgeted)
