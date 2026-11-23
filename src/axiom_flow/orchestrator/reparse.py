"""重跑范围解析与只读建议（PLAN-019，2026-11-23 用户裁决）。

设计事实源：`docs/plans/2026-09-28-parse-artifact-trust.md`（2026-11-23 重定的 S1/S2）、
`docs/adr/0015-db-single-source-for-readiness.md`（就地修订：DB 管状态、文件核验合法）。

- **系统只给建议、不替人决定重跑**：`build_reparse_plan` 逐页给出 `status`（DB）、
  `has_artifact`（文件核验）、`suggested` 与 `reason`；它不阻断任何提交。
- **范围由人表达**：`resolve_scope` 把 `scope + pages` 解析成明确的目标页列表；缺省全本向后兼容。
- **未完成集合**：`status != parsed` ∪ (`parsed` 且产物文件缺失)；文件缺失的页理由 `artifact_missing`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from axiom_flow.db.models import AfPage

#: 提交范围闭集。`selected` 时 `pages` 必填；两者都缺省 = 全本（向后兼容 8903）。
SCOPE_ALL = "all"
SCOPE_UNFINISHED = "unfinished"
SCOPE_FAILED = "failed"
SCOPE_SELECTED = "selected"


@dataclass(frozen=True, slots=True)
class PageAdvice:
    page_no: int
    status: str
    has_artifact: bool
    suggested: bool
    reason: str


def page_advice(page: AfPage | None, *, page_no: int, has_artifact: bool) -> PageAdvice:
    """逐页建议：DB 状态 + 产物核验双读；只给建议，不改状态。"""
    status = page.status if page is not None else "pending"
    if status != "parsed":
        return PageAdvice(page_no, status, has_artifact, True, "not_parsed")
    if not has_artifact:
        return PageAdvice(page_no, status, has_artifact, True, "artifact_missing")
    return PageAdvice(page_no, status, has_artifact, False, "")


def build_reparse_plan(
    pages: list[AfPage],
    *,
    total_pages: int,
    version_dir: Path | None,
) -> list[PageAdvice]:
    """全书逐页建议（按页号升序，连续覆盖 1..total_pages）。

    `version_dir=None` 表示 legacy 根视图：页行按传入的 `pages` 匹配，无行的页按 pending 处理。
    """
    by_no = {row.page_no: row for row in pages}
    advice: list[PageAdvice] = []
    for no in range(1, total_pages + 1):
        has_artifact = bool(
            version_dir is not None
            and (version_dir / "pages" / f"p{no:04d}.blocks.json").is_file()
        )
        advice.append(page_advice(by_no.get(no), page_no=no, has_artifact=has_artifact))
    return advice


def resolve_scope(
    *,
    scope: str | None,
    pages: list[int] | None,
    advice: list[PageAdvice],
    failed_pages: list[int] | None = None,
    total_pages: int,
) -> tuple[list[int], dict[int, str]]:
    """把人的范围表达解析为 `(目标页列表, {页号: 理由})`；不替人过滤已完成的页。

    - `pages` 显式 ⇒ 按该列表（人已决定，可重跑已完成页）；
    - `scope=all` 或都缺省 ⇒ 全书；
    - `scope=unfinished` ⇒ 建议集合（not_parsed / artifact_missing）；
    - `scope=failed` ⇒ `status=failed` 的页 ∪ 任务 `failed_pages`；
    - `scope=selected` ⇒ `pages` 必填（由调用方校验并报 422）。
    """
    if pages:
        return list(pages), {no: "selected" for no in pages}
    if scope in (None, SCOPE_ALL):
        return list(range(1, total_pages + 1)), {no: "all" for no in range(1, total_pages + 1)}
    if scope == SCOPE_UNFINISHED:
        picked = [row for row in advice if row.suggested]
        return [row.page_no for row in picked], {row.page_no: row.reason for row in picked}
    if scope == SCOPE_FAILED:
        failed = set(failed_pages or ()) | {
            row.page_no for row in advice if row.status == "failed"
        }
        ordered = sorted(no for no in failed if 1 <= no <= total_pages)
        return ordered, {no: "failed" for no in ordered}
    return [], {}
