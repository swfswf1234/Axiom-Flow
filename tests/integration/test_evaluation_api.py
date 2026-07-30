"""
模块职责：验证评测 case、中性快照、单运行评估、比较、评审、报告和 Web HTTP 边界。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
被测代码：src/axiom_flow/api/main.py、web/index.html、web/app.js、web/style.css
"""

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from axiom_flow.api.main import create_app
from axiom_flow.infrastructure.config import Settings
from axiom_flow.worker.runner import Worker

CASE_ID = "数学分析回归样本--2249d79fb6d0"


class EvaluationProvider:
    """只由测试 Worker 调用的确定性页面解析器。"""

    calls = 0

    async def parse_page(self, _image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        self.calls += 1
        return {
            "markdown": raw_text or f"第 {page_no} 页",
            "page_kind": "content",
            "blocks": [{"order_no": 0, "kind": "paragraph", "content": raw_text or f"第 {page_no} 页"}],
        }

    async def extract_knowledge(self, _markdown: str) -> dict:
        return {"nodes": [], "edges": []}


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(item) for item in value)
    if isinstance(value, str):
        return value.startswith(("/", "\\\\")) or (len(value) > 2 and value[1:3] in {":/", ":\\"})
    return False


def test_evaluation_api_uses_completed_parse_runs_without_leaking_paths(
    tmp_path: Path, mysql_settings: Settings, mysql_store,
):
    provider = EvaluationProvider()
    settings = Settings(
        data_dir=tmp_path / "data",
        evaluation_data_dir=tmp_path / "evaluation-data",
        web_dir=Path("web"),
        evaluation_definitions_dir=Path("evaluation/documents"),
        mysql_database=mysql_settings.mysql_database,
    )
    application = create_app(settings, lambda: (provider, provider))
    source = Path("evaluation/documents") / CASE_ID / "source.pdf"

    with TestClient(application) as client:
        documents = client.get("/api/v1/evaluations/documents")
        assert documents.status_code == 200
        assert any(item["case_id"] == CASE_ID for item in documents.json())
        assert not _contains_absolute_path(documents.json())

        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents", files={"file": ("数学分析回归样本.pdf", stream, "application/pdf")},
            ).json()
        command = client.post(f"/api/v1/documents/{document['id']}/parse-jobs")
        command.raise_for_status()
        completed = Worker(application.state.jobs, "evaluation-api-worker").run_once()
        assert completed and completed["status"] == "succeeded"
        run_id = completed["result"]["run_id"]

        baseline = client.post(f"/api/v1/evaluations/documents/{CASE_ID}/captures", json={
            "parse_run_id": run_id,
            "label": "main",
            "revision": {"commit": "a" * 40, "branch": "main", "dirty": False},
        })
        assert baseline.status_code == 201
        candidate = client.post(f"/api/v1/evaluations/documents/{CASE_ID}/captures", json={
            "parse_run_id": run_id,
            "label": "same-chain",
            "revision": {"commit": "b" * 40, "branch": "feature/evaluation", "dirty": False},
        })
        assert candidate.status_code == 201
        assert not _contains_absolute_path(baseline.json())
        assert baseline.json()["baseline_eligible"] is True
        assert candidate.json()["baseline_eligible"] is False

        assessment = client.post(f"/api/v1/evaluations/documents/{CASE_ID}/assessments", json={
            "snapshot_id": candidate.json()["snapshot_id"],
            "manifest": {
                "profile": "engineering_chain",
                "experiment_id": "api-chain-v1",
                "budget": {"max_model_calls": 12},
                "manual_review": {"pages": [1], "criteria": ["text", "formula", "evidence"]},
            },
        })
        assert assessment.status_code == 201
        assessment_id = assessment.json()["assessment_id"]
        assessment_page = client.get(f"/api/v1/evaluations/assessments/{assessment_id}/pages/1")
        assert assessment_page.status_code == 200
        assert assessment_page.json()["automatic_status"] == "passed"
        assert client.get(
            f"/api/v1/evaluations/assessments/{assessment_id}/pages/1/assets/source",
        ).headers["content-type"] == "image/png"
        absolute_review = client.post(f"/api/v1/evaluations/assessments/{assessment_id}/reviews", json={
            "page_no": 1, "verdict": "pass", "reason": "页面事实完整", "reviewer": "api-test",
        })
        assert absolute_review.status_code == 201
        assessment_report = client.post(f"/api/v1/evaluations/assessments/{assessment_id}/reports")
        assert assessment_report.status_code == 200
        assert assessment_report.json()["quality_status"] == "passed"
        assert assessment_report.json()["decision_scope"] == "engineering_chain_only"

        comparison = client.post(f"/api/v1/evaluations/documents/{CASE_ID}/comparisons", json={
            "baseline_snapshot_id": baseline.json()["snapshot_id"],
            "candidate_snapshot_id": candidate.json()["snapshot_id"],
        })
        assert comparison.status_code == 201
        comparison_id = comparison.json()["comparison_id"]
        page = client.get(f"/api/v1/evaluations/comparisons/{comparison_id}/pages/1")
        assert page.status_code == 200
        assert page.json()["page_no"] == 1
        assert client.get(
            f"/api/v1/evaluations/comparisons/{comparison_id}/pages/1/assets/source",
        ).headers["content-type"] == "image/png"

        review = client.post(f"/api/v1/evaluations/comparisons/{comparison_id}/reviews", json={
            "page_no": 1, "verdict": "equivalent", "reason": "同一运行链路", "reviewer": "api-test",
        })
        assert review.status_code == 201
        report = client.post(f"/api/v1/evaluations/comparisons/{comparison_id}/reports")
        assert report.status_code == 200
        assert report.json()["conclusion"] == "no_regression_detected"
        assert not _contains_absolute_path(client.get(
            f"/api/v1/evaluations/comparisons/{comparison_id}",
        ).json())


def test_web_exposes_document_centric_evaluation_layout():
    html = Path("web/index.html").read_text(encoding="utf-8")
    script = Path("web/app.js").read_text(encoding="utf-8")
    style = Path("web/style.css").read_text(encoding="utf-8")
    assert 'data-view="evaluation"' in html
    assert 'data-evaluation-mode="assessment"' in html
    assert 'data-evaluation-mode="comparison"' in html
    for pane in ("source", "parsed"):
        assert f'data-assessment-pane="{pane}"' in html
    for pane in ("source", "baseline", "candidate"):
        assert f'data-comparison-pane="{pane}"' in html
    assert "evaluation-assessment-workspace" in html
    assert "evaluation-comparison-workspace" in html
    assert "evaluation-assessment-scores" in html and "evaluation-diff" in html
    assert "@media (max-width: 760px)" in style and "evaluation-segments" in style
    assert "/evaluations/assessments/" in script
    assert "/evaluations/comparisons/" in script
    assert "evaluationCase.runs" not in script and ".role" not in script
    assert "git checkout" not in script and "BailianProvider" not in script
