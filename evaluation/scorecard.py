"""
模块职责：校验解析实验的样本构成、预算、人工评分和采纳门槛。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/test_evaluation_scorecard.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


VALID_DIMENSIONS = {
    "text",
    "structure",
    "formula",
    "table_figure",
    "source_evidence",
}
REQUIRED_CATEGORY_COUNTS = {
    "math_text": 3,
    "math_scanned": 3,
    "math_formula": 3,
    "cs_table_figure": 3,
}
REQUIRED_PAGE_COUNT = 12
DEFAULT_MAX_MODEL_CALLS = 36
MINIMUM_AVERAGE_SCORE = 1.5
SENSITIVE_CONFIG_KEYS = {"api_key", "authorization", "password", "secret", "token"}


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str) and key.lower() in SENSITIVE_CONFIG_KEYS:
                return True
            if _contains_sensitive_key(child):
                return True
    if isinstance(value, list):
        return any(_contains_sensitive_key(child) for child in value)
    return False


def validate_manifest(manifest: dict[str, Any]) -> None:
    experiment_id = manifest.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise ValueError("manifest.experiment_id must be a non-empty string")

    required_page_count = manifest.get("required_page_count", REQUIRED_PAGE_COUNT)
    if required_page_count != REQUIRED_PAGE_COUNT:
        raise ValueError(f"required_page_count must be {REQUIRED_PAGE_COUNT}")

    budget = _require_mapping(manifest.get("budget"), "manifest.budget")
    max_model_calls = budget.get("max_model_calls", DEFAULT_MAX_MODEL_CALLS)
    if not isinstance(max_model_calls, int) or max_model_calls < 1:
        raise ValueError("max_model_calls must be a positive integer")
    if max_model_calls > DEFAULT_MAX_MODEL_CALLS:
        raise ValueError(f"max_model_calls cannot exceed {DEFAULT_MAX_MODEL_CALLS}")

    pages = manifest.get("pages")
    if not isinstance(pages, list) or len(pages) != REQUIRED_PAGE_COUNT:
        raise ValueError(f"manifest.pages must contain {REQUIRED_PAGE_COUNT} pages")

    page_ids: set[str] = set()
    category_counts = {category: 0 for category in REQUIRED_CATEGORY_COUNTS}
    for page in pages:
        page = _require_mapping(page, "manifest.pages item")
        page_id = page.get("id")
        if not isinstance(page_id, str) or not page_id.strip() or page_id in page_ids:
            raise ValueError("every manifest page requires a unique non-empty id")
        page_ids.add(page_id)
        if not isinstance(page.get("artifact_id"), str) or not page["artifact_id"].strip():
            raise ValueError(f"page {page_id} requires artifact_id")
        if not isinstance(page.get("page_no"), int) or page["page_no"] < 1:
            raise ValueError(f"page {page_id} requires a positive page_no")
        category = page.get("category")
        if category not in REQUIRED_CATEGORY_COUNTS:
            raise ValueError(f"page {page_id} has an invalid category")
        category_counts[category] += 1
        dimensions = page.get("dimensions")
        if not isinstance(dimensions, list) or not dimensions:
            raise ValueError(f"page {page_id} requires dimensions")
        if len(dimensions) != len(set(dimensions)) or not set(dimensions) <= VALID_DIMENSIONS:
            raise ValueError(f"page {page_id} has invalid dimensions")
        if "source_evidence" not in dimensions:
            raise ValueError(f"page {page_id} must include source_evidence")
    if category_counts != REQUIRED_CATEGORY_COUNTS:
        raise ValueError("manifest must contain three pages for each required category")


def score_experiment(manifest: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest)
    if results.get("experiment_id") != manifest["experiment_id"]:
        raise ValueError("results.experiment_id must match manifest.experiment_id")
    if not isinstance(results.get("run_artifact_id"), str) or not results["run_artifact_id"].strip():
        raise ValueError("results.run_artifact_id must be a non-empty string")
    model_config = _require_mapping(results.get("model_config"), "results.model_config")
    if not model_config:
        raise ValueError("results.model_config must not be empty")
    if _contains_sensitive_key(model_config):
        raise ValueError("results.model_config must not contain credentials")
    for field_name in ("duration_seconds", "cost_estimate"):
        value = results.get(field_name)
        if not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"results.{field_name} must be a non-negative number")

    model_calls = results.get("model_calls")
    if not isinstance(model_calls, int) or model_calls < 0:
        raise ValueError("results.model_calls must be a non-negative integer")
    max_calls = manifest["budget"].get("max_model_calls", DEFAULT_MAX_MODEL_CALLS)
    if model_calls > max_calls:
        raise ValueError("results.model_calls exceeds the manifest budget")

    result_pages = results.get("pages")
    if not isinstance(result_pages, list):
        raise ValueError("results.pages must be a list")
    result_page_ids = []
    by_id = {}
    for page in result_pages:
        page = _require_mapping(page, "results.pages item")
        result_page_ids.append(page.get("id"))
        by_id[page.get("id")] = page
    manifest_pages = {page["id"]: page for page in manifest["pages"]}
    if len(result_page_ids) != len(set(result_page_ids)):
        raise ValueError("results.pages must not contain duplicate ids")
    if set(by_id) != set(manifest_pages):
        raise ValueError("results.pages must match manifest page ids exactly")

    scores: list[int] = []
    critical_failures: list[str] = []
    for page_id, manifest_page in manifest_pages.items():
        result_page = _require_mapping(by_id[page_id], f"results page {page_id}")
        page_scores = _require_mapping(result_page.get("scores"), f"scores for {page_id}")
        expected_dimensions = set(manifest_page["dimensions"])
        if set(page_scores) != expected_dimensions:
            raise ValueError(f"scores for {page_id} must match manifest dimensions")
        for dimension, score in page_scores.items():
            if not isinstance(score, int) or score not in {0, 1, 2}:
                raise ValueError(f"score for {page_id}.{dimension} must be 0, 1, or 2")
            scores.append(score)
            if dimension == "source_evidence" and score == 0:
                critical_failures.append(f"{page_id}: source_evidence=0")
            if dimension == "formula" and score == 0:
                critical_failures.append(f"{page_id}: formula=0")
        errors = result_page.get("critical_errors", [])
        if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
            raise ValueError(f"critical_errors for {page_id} must be a string list")
        critical_failures.extend(f"{page_id}: {error}" for error in errors if error.strip())
        if not isinstance(result_page.get("review_reason"), str) or not result_page["review_reason"].strip():
            raise ValueError(f"results page {page_id} requires review_reason")

    average = sum(scores) / len(scores)
    accepted = average >= MINIMUM_AVERAGE_SCORE and not critical_failures
    return {
        "experiment_id": manifest["experiment_id"],
        "run_artifact_id": results["run_artifact_id"],
        "model_calls": model_calls,
        "max_model_calls": max_calls,
        "average_score": round(average, 3),
        "duration_seconds": results["duration_seconds"],
        "cost_estimate": results["cost_estimate"],
        "minimum_average_score": MINIMUM_AVERAGE_SCORE,
        "critical_failures": critical_failures,
        "accepted": accepted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    scorecard = score_experiment(manifest, results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
