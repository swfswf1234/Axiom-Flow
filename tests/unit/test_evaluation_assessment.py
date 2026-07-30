"""
模块职责：验证单运行 assessment 的自动检查、profile、人工结论和报告范围。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：src/axiom_flow/application/evaluations.py、src/axiom_flow/application/evaluation_analysis.py
"""

from pathlib import Path

import pytest

from axiom_flow.application.evaluation_analysis import assessment_quality, validate_assessment_manifest
from tests.unit.test_evaluation_comparison import _capture, evaluation_service


def _engineering_manifest(source_hash: str) -> dict:
    return {
        "profile": "engineering_chain",
        "experiment_id": "engineering-v1",
        "source": {"artifact_id": f"sha256:{source_hash}"},
        "page_range": {"start": 1, "end": 2, "inclusive": True},
        "budget": {"max_model_calls": 6},
        "manual_review": {"pages": [1, 2], "criteria": ["text", "formula", "evidence"]},
    }


def test_engineering_assessment_requires_review_and_separates_execution_from_quality(tmp_path: Path):
    service, store, _ = evaluation_service(tmp_path, page_numbers=[1, 2])
    snapshot = _capture(service, "trial", "a" * 40, "feature", dirty=True)
    assessment = service.assess(
        snapshot["case_id"], snapshot["snapshot_id"], _engineering_manifest(store.source_hash),
    )
    assert assessment["execution_status"] == "complete"
    assert assessment["quality_status"] == "review_required"
    assert assessment["decision_scope"] == "engineering_chain_only"
    assert service.get_assessment_page(assessment["assessment_id"], 1)["automatic_status"] == "passed"

    service.review_assessment(assessment["assessment_id"], 1, "pass", "正文可读", "tester")
    assert service.report_assessment(assessment["assessment_id"])["quality_status"] == "review_required"
    service.review_assessment(assessment["assessment_id"], 2, "failed", "主要公式错误", "tester")
    report = service.report_assessment(assessment["assessment_id"])
    assert report["quality_status"] == "failed"
    assert report["decision_scope"] == "engineering_chain_only"


def test_assessment_rejects_pages_outside_snapshot_and_excess_budget(tmp_path: Path):
    service, store, workspace = evaluation_service(tmp_path)
    snapshot = _capture(service, "trial", "a" * 40, "feature")
    frozen = workspace.verify_snapshot(snapshot["case_id"], snapshot["snapshot_id"])
    manifest = _engineering_manifest(store.source_hash)
    with pytest.raises(ValueError, match="人工审阅页"):
        validate_assessment_manifest(manifest, frozen)
    manifest["manual_review"]["pages"] = [1]
    manifest["budget"]["max_model_calls"] = 0
    with pytest.raises(ValueError, match="max_model_calls"):
        validate_assessment_manifest(manifest, frozen)


def test_formal_scorecard_uses_dimension_scores_and_critical_failures():
    assessment = {
        "profile": "formal_scorecard",
        "review_page_numbers": [1],
        "minimum_average_score": 1.5,
    }
    reviews = {1: {
        "verdict": "failed",
        "scores": {"text": 2, "formula": 0, "source_evidence": 2},
        "critical_errors": [],
    }}
    result = assessment_quality(assessment, reviews)
    assert result["quality_status"] == "failed"
    assert "page-1: formula=0" in result["critical_failures"]
