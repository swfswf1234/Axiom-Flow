"""
模块职责：验证扫描教材评测只生成待人工填写的评分模板。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：evaluation/scanned_textbook.py
"""

from evaluation.scanned_textbook import review_template


def test_review_template_does_not_invent_scores():
    manifest = {
        "pages": [
            {"id": "scan-01", "dimensions": ["text", "formula", "source_evidence"]},
        ],
    }
    run = {
        "experiment_id": "rudin-scan-v1", "run_artifact_id": "sha256:abc",
        "model_config": {"provider": "fake"}, "model_calls": 1,
        "duration_seconds": 1.0, "cost_estimate": 0.0,
    }

    template = review_template(manifest, run)

    assert template["pages"][0]["scores"] == {
        "text": None, "formula": None, "source_evidence": None,
    }
    assert template["pages"][0]["review_reason"] == ""
