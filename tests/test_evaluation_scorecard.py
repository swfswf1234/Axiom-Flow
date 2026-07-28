"""
模块职责：验证解析实验评分门禁的输入校验和采纳结论。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：evaluation/scorecard.py
"""

import pytest

from evaluation.scorecard import score_experiment, validate_manifest


def _manifest():
    pages = []
    for index in range(12):
        dimensions = ["text", "structure", "source_evidence"]
        if index >= 6:
            dimensions.append("formula")
        if index >= 9:
            dimensions.append("table_figure")
        pages.append(
            {
                "id": f"page-{index + 1}",
                "artifact_id": f"artifact-{index + 1}",
                "page_no": index + 1,
                "category": "scanned_math_textbook",
                "dimensions": dimensions,
            }
        )
    return {
        "experiment_id": "parser-v1",
        "required_page_count": 12,
        "required_categories": {"scanned_math_textbook": 12},
        "budget": {"max_model_calls": 24},
        "pages": pages,
    }


def _results(manifest):
    return {
        "experiment_id": manifest["experiment_id"],
        "run_artifact_id": "local-run-001",
        "model_config": {"provider": "bailian", "model": "candidate"},
        "model_calls": 12,
        "duration_seconds": 42.5,
        "cost_estimate": 0.18,
        "pages": [
            {
                "id": page["id"],
                "scores": {dimension: 2 for dimension in page["dimensions"]},
                "critical_errors": [],
                "review_reason": "清晰可用",
            }
            for page in manifest["pages"]
        ],
    }


def test_scorecard_accepts_complete_high_quality_results():
    manifest = _manifest()

    scorecard = score_experiment(manifest, _results(manifest))

    assert scorecard["accepted"] is True
    assert scorecard["average_score"] == 2.0
    assert scorecard["critical_failures"] == []


def test_manifest_requires_fixed_page_count():
    manifest = _manifest()
    manifest["pages"] = manifest["pages"][:-1]

    with pytest.raises(ValueError, match="12 pages"):
        validate_manifest(manifest)


def test_manifest_requires_declared_category_count():
    manifest = _manifest()
    manifest["pages"][3]["category"] = "unknown"

    with pytest.raises(ValueError, match="invalid category"):
        validate_manifest(manifest)


def test_scorecard_rejects_formula_zero_even_with_high_average():
    manifest = _manifest()
    results = _results(manifest)
    formula_page = next(page for page in results["pages"] if "formula" in page["scores"])
    formula_page["scores"]["formula"] = 0

    scorecard = score_experiment(manifest, results)

    assert scorecard["accepted"] is False
    assert f"{formula_page['id']}: formula=0" in scorecard["critical_failures"]


def test_scorecard_rejects_budget_overrun():
    manifest = _manifest()
    results = _results(manifest)
    results["model_calls"] = 25

    with pytest.raises(ValueError, match="exceeds the manifest budget"):
        score_experiment(manifest, results)


def test_scorecard_requires_all_review_reasons():
    manifest = _manifest()
    results = _results(manifest)
    results["pages"][0]["review_reason"] = ""

    with pytest.raises(ValueError, match="requires review_reason"):
        score_experiment(manifest, results)


def test_scorecard_rejects_duplicate_result_page_ids():
    manifest = _manifest()
    results = _results(manifest)
    results["pages"][1]["id"] = results["pages"][0]["id"]

    with pytest.raises(ValueError, match="duplicate ids"):
        score_experiment(manifest, results)


def test_scorecard_rejects_credentials_in_model_configuration():
    manifest = _manifest()
    results = _results(manifest)
    results["model_config"]["api_key"] = "must-not-be-reported"

    with pytest.raises(ValueError, match="must not contain credentials"):
        score_experiment(manifest, results)
