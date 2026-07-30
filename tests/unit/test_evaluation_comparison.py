"""
模块职责：验证中性 ParseRun 快照、baseline 资格、逐维比较和相对人工结论。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：src/axiom_flow/application/evaluations.py、src/axiom_flow/application/evaluation_analysis.py
"""

import hashlib
import json
from pathlib import Path

import pytest

from axiom_flow.application.evaluations import EvaluationApplicationService
from axiom_flow.domain.models import ConflictError
from axiom_flow.infrastructure.evaluation_workspace import EvaluationWorkspace


class FakeRunStore:
    def __init__(self, data_dir: Path, markdown: str = "公式 $x$", page_numbers: list[int] | None = None) -> None:
        self.data_dir = data_dir
        self.page_numbers = page_numbers or [1]
        source = data_dir / "documents" / "source.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"pdf-source")
        self.source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        self.document = {
            "id": "document-1", "filename": "数学分析原理.pdf", "content_hash": self.source_hash,
            "source_path": str(source), "page_count": max(self.page_numbers),
        }
        self.run = {
            "id": "run-1", "document_id": "document-1", "status": "parsed", "artifact_state": "available",
            "model_calls": len(self.page_numbers), "provider_summary": {"vision_model": "fake"},
        }
        self.pages = [{"page_no": page_no} for page_no in self.page_numbers]
        self.artifacts = []
        for page_no in self.page_numbers:
            page = data_dir / f"page-{page_no:04d}.json"
            page.write_text(json.dumps({
                "page_no": page_no,
                "markdown": markdown,
                "page_kind": "content",
                "blocks": [
                    {"order_no": 0, "kind": "paragraph", "content": markdown},
                    {"order_no": 1, "kind": "formula", "content": "x", "latex": "x"},
                ],
                "evidence": [{"kind": "text", "page_no": page_no, "quote": markdown, "bbox": [1, 1, 2, 2]}],
            }, ensure_ascii=False), encoding="utf-8")
            image = data_dir / f"page-{page_no:04d}.png"
            image.write_bytes(f"image-{page_no}".encode())
            self.artifacts.extend([
                self._artifact(page, "page_json", {"page_no": page_no}),
                self._artifact(image, "page_image", {"page_no": page_no}),
            ])
        manifest = data_dir / "manifest.json"
        manifest.write_text("{}", encoding="utf-8")
        self.artifacts.append(self._artifact(manifest, "parse_manifest", {}))

    @staticmethod
    def _artifact(path: Path, kind: str, metadata: dict) -> dict:
        payload = path.read_bytes()
        return {
            "path": str(path), "kind": kind, "content_hash": hashlib.sha256(payload).hexdigest(),
            "size_bytes": len(payload), "mime_type": "application/json" if path.suffix == ".json" else "image/png",
            "metadata": metadata,
        }

    def get_parse_run(self, run_id: str):
        return self.run if run_id == self.run["id"] else None

    def get_document(self, document_id: str):
        return self.document if document_id == self.document["id"] else None

    def list_pages_for_run(self, _run_id: str):
        return self.pages

    def list_artifacts_for_run(self, _run_id: str):
        return self.artifacts


def evaluation_service(
    tmp_path: Path, markdown: str = "公式 $x$", page_numbers: list[int] | None = None,
):
    data_dir = tmp_path / "data"
    store = FakeRunStore(data_dir, markdown, page_numbers)
    workspace = EvaluationWorkspace(tmp_path / "evaluation-data", tmp_path / "definitions")
    return EvaluationApplicationService(store, workspace, data_dir), store, workspace


def _capture(service: EvaluationApplicationService, label: str, commit: str, branch: str, dirty=False):
    revision = {"commit": commit, "branch": branch, "dirty": dirty}
    if dirty:
        revision["diff_hash"] = "d" * 64
    return service.capture("run-1", label, revision)


def test_capture_is_neutral_and_derives_baseline_eligibility(tmp_path: Path):
    service, store, _ = evaluation_service(tmp_path)
    trial = _capture(service, "trial", "a" * 40, "feature/formula", dirty=True)
    baseline = _capture(service, "main", "b" * 40, "main")

    assert "role" not in trial
    assert trial["baseline_eligible"] is False
    assert baseline["baseline_eligible"] is True
    assert trial["revision"]["diff_hash"] == "d" * 64
    assert "files" not in trial
    assert trial["document_hash"] == store.source_hash


def test_comparison_rejects_ineligible_baseline_and_mismatched_page_set(tmp_path: Path):
    service, _, workspace = evaluation_service(tmp_path)
    trial = _capture(service, "trial", "a" * 40, "feature")
    candidate = _capture(service, "candidate", "b" * 40, "feature")
    with pytest.raises(ConflictError, match="干净 main"):
        service.compare(trial["case_id"], trial["snapshot_id"], candidate["snapshot_id"])

    baseline = _capture(service, "main", "c" * 40, "main")
    run_path = workspace.root / baseline["case_id"] / "runs" / candidate["snapshot_id"] / "run.json"
    payload = json.loads(run_path.read_text(encoding="utf-8"))
    payload["page_numbers"] = [1, 2]
    run_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ConflictError, match="页范围"):
        service.compare(baseline["case_id"], baseline["snapshot_id"], candidate["snapshot_id"])


def test_changed_page_requires_review_and_report_never_claims_improved(tmp_path: Path):
    service, store, _ = evaluation_service(tmp_path)
    baseline = _capture(service, "main", "a" * 40, "main")
    page_path = Path(next(item["path"] for item in store.artifacts if item["kind"] == "page_json"))
    changed = json.loads(page_path.read_text(encoding="utf-8"))
    changed["markdown"] = "公式 $y$"
    changed["blocks"][1]["latex"] = "y"
    replacement = page_path.with_suffix(".replacement")
    replacement.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    replacement.replace(page_path)
    artifact = next(item for item in store.artifacts if item["kind"] == "page_json")
    artifact.update(FakeRunStore._artifact(page_path, "page_json", {"page_no": 1}))
    candidate = _capture(service, "formula-fix", "b" * 40, "feature")

    comparison = service.compare(baseline["case_id"], baseline["snapshot_id"], candidate["snapshot_id"])
    assert comparison["conclusion"] == "review_required"
    assert comparison["pending_review_pages"] == [1]
    page = service.get_comparison_page(comparison["comparison_id"], 1)
    assert set(page["changed_dimensions"]) >= {"text", "formulas"}
    assert service.report_comparison(comparison["comparison_id"])["conclusion"] == "review_required"
    service.review_comparison(
        comparison["comparison_id"], 1, "candidate_better", "公式已修正", "tester",
    )
    report = service.report_comparison(comparison["comparison_id"])
    assert report["conclusion"] == "changed"
    assert report["conclusion"] != "improved"


def test_identical_snapshots_report_no_regression_detected(tmp_path: Path):
    service, _, _ = evaluation_service(tmp_path)
    baseline = _capture(service, "main", "a" * 40, "main")
    candidate = _capture(service, "same", "b" * 40, "feature")
    comparison = service.compare(baseline["case_id"], baseline["snapshot_id"], candidate["snapshot_id"])
    assert comparison["changed_pages"] == []
    assert service.report_comparison(comparison["comparison_id"])["conclusion"] == "no_regression_detected"
