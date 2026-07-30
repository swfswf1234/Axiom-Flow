"""
模块职责：提供评测页面自动检查、结构化差异和评估 profile 的纯函数规则。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/unit/test_evaluation_comparison.py、tests/unit/test_evaluation_assessment.py
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any

COMPARISON_VERDICTS = {
    "candidate_better", "baseline_better", "equivalent", "both_failed", "needs_review",
}
ENGINEERING_VERDICTS = {"pass", "failed", "needs_review"}
FORMAL_VERDICTS = {"pass", "failed", "needs_review"}
VALID_ASSESSMENT_PROFILES = {"engineering_chain", "formal_scorecard"}
VALID_SCORE_DIMENSIONS = {
    "text", "structure", "formula", "table_figure", "source_evidence",
}
FORMAL_PAGE_COUNT = 12
FORMAL_MAX_MODEL_CALLS = 36
FORMAL_MINIMUM_AVERAGE = 1.5


def compare_page(
    page_no: int,
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any]:
    """按可解释维度比较单页规范事实，不推断公式语义等价。"""
    if baseline is None or candidate is None:
        return {
            "page_no": page_no,
            "changed": True,
            "changed_dimensions": ["availability"],
            "review_status": "pending",
            "baseline": baseline,
            "candidate": candidate,
            "dimensions": {
                "availability": {
                    "changed": True,
                    "baseline": baseline is not None,
                    "candidate": candidate is not None,
                },
            },
        }
    left_text = normalize_text(baseline.get("markdown"))
    right_text = normalize_text(candidate.get("markdown"))
    left_blocks = baseline.get("blocks") if isinstance(baseline.get("blocks"), list) else []
    right_blocks = candidate.get("blocks") if isinstance(candidate.get("blocks"), list) else []
    dimensions = {
        "text": {
            "changed": left_text != right_text,
            "diff": list(difflib.unified_diff(
                left_text.splitlines(), right_text.splitlines(), lineterm="",
            )),
        },
        "structure": _dimension(_block_signature(left_blocks), _block_signature(right_blocks)),
        "formulas": _dimension(_formula_signature(left_blocks), _formula_signature(right_blocks)),
        "tables": _dimension(_table_signature(left_blocks), _table_signature(right_blocks)),
        "figures": _dimension(
            _figure_signature(baseline, left_blocks),
            _figure_signature(candidate, right_blocks),
        ),
        "evidence": _dimension(_evidence_signature(baseline), _evidence_signature(candidate)),
    }
    changed = [name for name, value in dimensions.items() if value["changed"]]
    return {
        "page_no": page_no,
        "changed": bool(changed),
        "changed_dimensions": changed,
        "review_status": "pending" if changed else "not_required",
        "baseline": {
            "markdown": baseline.get("markdown", ""), "page_kind": baseline.get("page_kind"),
        },
        "candidate": {
            "markdown": candidate.get("markdown", ""), "page_kind": candidate.get("page_kind"),
        },
        "dimensions": dimensions,
    }


def inspect_page(
    page_no: int,
    page: dict[str, Any] | None,
    *,
    has_page_image: bool,
) -> dict[str, Any]:
    """生成不宣称语义正确性的单页确定性完整性检查。"""
    if page is None:
        checks = {
            "page_json": False,
            "page_image": has_page_image,
            "markdown": False,
            "blocks": False,
            "evidence": False,
            "evidence_bounds": False,
        }
        return _inspection_result(page_no, page, checks)

    blocks = page.get("blocks") if isinstance(page.get("blocks"), list) else []
    evidence = page.get("evidence") if isinstance(page.get("evidence"), list) else []
    checks = {
        "page_json": True,
        "page_image": has_page_image,
        "markdown": bool(normalize_text(page.get("markdown"))),
        "blocks": bool(blocks) and all(isinstance(item, dict) for item in blocks),
        "evidence": bool(evidence) and all(isinstance(item, dict) for item in evidence),
        "evidence_bounds": bool(evidence) and all(_valid_evidence(item, page_no) for item in evidence),
    }
    return _inspection_result(page_no, page, checks)


def validate_assessment_manifest(
    manifest: dict[str, Any], snapshot: dict[str, Any],
) -> dict[str, Any]:
    """冻结 assessment profile、审阅页、预算和决策范围。"""
    profile = str(manifest.get("profile") or "")
    if profile not in VALID_ASSESSMENT_PROFILES:
        raise ValueError("评估 manifest profile 非法")
    experiment_id = str(manifest.get("experiment_id") or "").strip()
    if not experiment_id:
        raise ValueError("评估 manifest 缺少 experiment_id")

    snapshot_pages = [int(page) for page in snapshot.get("page_numbers", [])]
    if not snapshot_pages:
        raise ValueError("评估快照没有页面")
    budget = manifest.get("budget") if isinstance(manifest.get("budget"), dict) else {}
    max_calls = budget.get("max_model_calls")
    if not isinstance(max_calls, int) or max_calls < 1:
        raise ValueError("评估 manifest 必须声明正整数 max_model_calls")
    if int(snapshot.get("model_calls") or 0) > max_calls:
        raise ValueError("快照模型调用数超过评估 manifest 预算")

    if profile == "engineering_chain":
        review = manifest.get("manual_review")
        if not isinstance(review, dict):
            raise ValueError("工程链路 manifest 缺少 manual_review")
        review_pages = review.get("pages")
        criteria = review.get("criteria")
        if not isinstance(review_pages, list) or not review_pages:
            raise ValueError("工程链路 manifest 必须声明人工审阅页")
        if not isinstance(criteria, list) or not criteria or not all(
            isinstance(item, str) and item.strip() for item in criteria
        ):
            raise ValueError("工程链路 manifest 必须声明人工审阅标准")
        resolved_pages = [int(page) for page in review_pages]
        if len(resolved_pages) != len(set(resolved_pages)) or not set(resolved_pages) <= set(snapshot_pages):
            raise ValueError("人工审阅页必须唯一且位于快照页范围")
        return {
            "profile": profile,
            "experiment_id": experiment_id,
            "review_page_numbers": resolved_pages,
            "criteria": [str(item).strip() for item in criteria],
            "decision_scope": "engineering_chain_only",
            "max_model_calls": max_calls,
        }

    if max_calls > FORMAL_MAX_MODEL_CALLS:
        raise ValueError(f"正式 scorecard 最大模型调用数为 {FORMAL_MAX_MODEL_CALLS}")
    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != FORMAL_PAGE_COUNT:
        raise ValueError(f"正式 scorecard 必须包含 {FORMAL_PAGE_COUNT} 页")
    page_contracts: dict[int, dict[str, Any]] = {}
    for item in pages:
        if not isinstance(item, dict) or not isinstance(item.get("page_no"), int):
            raise ValueError("正式 scorecard 页面契约非法")
        page_no = int(item["page_no"])
        dimensions = item.get("dimensions")
        if (
            page_no in page_contracts
            or page_no not in snapshot_pages
            or not isinstance(dimensions, list)
            or not dimensions
            or not set(dimensions) <= VALID_SCORE_DIMENSIONS
            or "source_evidence" not in dimensions
        ):
            raise ValueError("正式 scorecard 页码或维度非法")
        page_contracts[page_no] = {
            "dimensions": list(dimensions), "category": item.get("category"),
        }
    return {
        "profile": profile,
        "experiment_id": experiment_id,
        "review_page_numbers": sorted(page_contracts),
        "page_contracts": page_contracts,
        "decision_scope": "model_quality_scorecard",
        "max_model_calls": max_calls,
        "minimum_average_score": FORMAL_MINIMUM_AVERAGE,
    }


def validate_assessment_review(
    assessment: dict[str, Any],
    page_no: int,
    verdict: str,
    scores: dict[str, int] | None,
    critical_errors: list[str] | None,
) -> dict[str, Any]:
    """按 assessment profile 校验人工结论载荷。"""
    if page_no not in assessment.get("review_page_numbers", []):
        raise ValueError("评审页不在 assessment 人工页范围内")
    profile = assessment.get("profile")
    if profile == "engineering_chain":
        if verdict not in ENGINEERING_VERDICTS:
            raise ValueError("工程链路 verdict 非法")
        if scores:
            raise ValueError("工程链路评审不接受维度分数")
        return {"verdict": verdict, "scores": None, "critical_errors": critical_errors or []}

    if verdict not in FORMAL_VERDICTS:
        raise ValueError("正式 scorecard verdict 非法")
    expected = set(assessment["page_contracts"][str(page_no)]["dimensions"])
    if not isinstance(scores, dict) or set(scores) != expected:
        raise ValueError("正式 scorecard 分数维度不完整")
    if any(not isinstance(value, int) or value not in {0, 1, 2} for value in scores.values()):
        raise ValueError("正式 scorecard 分数只能是 0、1、2")
    errors = critical_errors or []
    if not all(isinstance(item, str) and item.strip() for item in errors):
        raise ValueError("critical_errors 必须是非空字符串列表")
    return {"verdict": verdict, "scores": scores, "critical_errors": errors}


def assessment_quality(
    assessment: dict[str, Any], latest_reviews: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """在不扩大 manifest 决策范围的前提下汇总质量结论。"""
    pending = [
        int(page) for page in assessment.get("review_page_numbers", []) if int(page) not in latest_reviews
    ]
    if pending or any(item.get("verdict") == "needs_review" for item in latest_reviews.values()):
        return {"quality_status": "review_required", "pending_review_pages": pending}
    if assessment.get("profile") == "engineering_chain":
        passed = all(item.get("verdict") == "pass" for item in latest_reviews.values())
        return {
            "quality_status": "passed" if passed else "failed",
            "pending_review_pages": [],
        }

    scores = [
        score
        for review in latest_reviews.values()
        for score in (review.get("scores") or {}).values()
    ]
    critical = [
        error
        for review in latest_reviews.values()
        for error in review.get("critical_errors", [])
    ]
    for page_no, review in latest_reviews.items():
        for dimension, score in (review.get("scores") or {}).items():
            if dimension in {"formula", "source_evidence"} and score == 0:
                critical.append(f"page-{page_no}: {dimension}=0")
    average = round(sum(scores) / len(scores), 3) if scores else 0.0
    passed = average >= float(assessment["minimum_average_score"]) and not critical
    return {
        "quality_status": "passed" if passed else "failed",
        "pending_review_pages": [],
        "average_score": average,
        "critical_failures": critical,
    }


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _inspection_result(
    page_no: int, page: dict[str, Any] | None, checks: dict[str, bool],
) -> dict[str, Any]:
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "page_no": page_no,
        "automatic_status": "passed" if not failed else "failed",
        "failed_checks": failed,
        "checks": checks,
        "snapshot": None if page is None else {
            "markdown": page.get("markdown", ""),
            "page_kind": page.get("page_kind"),
            "blocks": page.get("blocks") or [],
            "evidence": page.get("evidence") or [],
            "content_images": page.get("content_images") or [],
        },
    }


def _valid_evidence(item: Any, page_no: int) -> bool:
    if not isinstance(item, dict) or item.get("page_no") != page_no:
        return False
    bbox = item.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    if not all(isinstance(value, (int, float)) for value in bbox):
        return False
    left, top, right, bottom = bbox
    return 0 <= left < right and 0 <= top < bottom


def _dimension(left: Any, right: Any) -> dict[str, Any]:
    return {"changed": left != right, "baseline": left, "candidate": right}


def _block_signature(blocks: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "order": item.get("order_no", index), "kind": item.get("kind"),
            "content": normalize_text(item.get("content")),
        }
        for index, item in enumerate(blocks) if isinstance(item, dict)
    ]


def _formula_signature(blocks: list[Any]) -> list[str]:
    return [
        re.sub(r"\s+", "", normalize_text(item.get("latex") or item.get("content")))
        for item in blocks if isinstance(item, dict) and item.get("kind") == "formula"
    ]


def _table_signature(blocks: list[Any]) -> list[list[list[str]]]:
    return [
        _table_matrix(item.get("content"))
        for item in blocks if isinstance(item, dict) and item.get("kind") == "table"
    ]


def _table_matrix(value: Any) -> list[list[str]]:
    rows = []
    for line in normalize_text(value).splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            rows.append(cells)
    return rows


def _figure_signature(page: dict[str, Any], blocks: list[Any]) -> dict[str, Any]:
    figures = [
        {
            "kind": item.get("kind"), "content": normalize_text(item.get("content")),
            "bbox": item.get("bbox"),
        }
        for item in blocks
        if isinstance(item, dict) and item.get("kind") in {"figure", "image", "caption"}
    ]
    return {"blocks": figures, "content_images": page.get("content_images") or []}


def _evidence_signature(page: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = page.get("evidence") if isinstance(page.get("evidence"), list) else []
    return [
        {
            "kind": item.get("kind"), "page_no": item.get("page_no"),
            "quote": normalize_text(item.get("quote")), "bbox": item.get("bbox"),
        }
        for item in evidence if isinstance(item, dict)
    ]
