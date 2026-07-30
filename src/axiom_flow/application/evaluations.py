"""
模块职责：编排中性解析快照、单运行质量评估、版本比较、人工评审与报告用例。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/unit/test_evaluation_comparison.py、tests/unit/test_evaluation_assessment.py、tests/integration/test_evaluation_workspace.py、tests/integration/test_evaluation_api.py
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from axiom_flow.application.evaluation_analysis import (
    COMPARISON_VERDICTS,
    assessment_quality,
    compare_page,
    inspect_page,
    validate_assessment_manifest,
    validate_assessment_review,
)
from axiom_flow.domain.models import ConflictError, FileResource, NotFoundError


@dataclass(frozen=True, slots=True)
class SnapshotFile:
    """需要冻结到评测快照的一个生产解析文件。"""

    relative_path: str
    source: Path
    kind: str
    content_hash: str
    size_bytes: int
    mime_type: str
    metadata: dict[str, Any]


class EvaluationRunRepository(Protocol):
    """评测捕获所需的最小生产运行查询端口。"""

    def get_parse_run(self, run_id: str) -> dict[str, Any] | None: ...
    def get_document(self, document_id: str) -> dict[str, Any] | None: ...
    def list_pages_for_run(self, run_id: str) -> list[dict[str, Any]]: ...
    def list_artifacts_for_run(self, run_id: str) -> list[dict[str, Any]]: ...


class EvaluationWorkspacePort(Protocol):
    """可读文件评测工作区端口。"""

    def list_cases(self) -> list[dict[str, Any]]: ...
    def get_case(self, case_id: str) -> dict[str, Any]: ...
    def ensure_case(self, case: dict[str, Any], source: Path) -> dict[str, Any]: ...
    def source_file(self, case_id: str) -> Path: ...
    def list_snapshots(self, case_id: str) -> list[dict[str, Any]]: ...
    def get_snapshot(self, case_id: str, snapshot_id: str) -> dict[str, Any]: ...
    def create_snapshot(
        self, case_id: str, snapshot: dict[str, Any], files: list[SnapshotFile],
    ) -> dict[str, Any]: ...
    def verify_snapshot(self, case_id: str, snapshot_id: str) -> dict[str, Any]: ...
    def snapshot_page(
        self, case_id: str, snapshot_id: str, page_no: int,
    ) -> dict[str, Any] | None: ...
    def snapshot_asset(
        self, case_id: str, snapshot_id: str, kind: str, page_no: int,
    ) -> FileResource: ...
    def list_assessments(self, case_id: str) -> list[dict[str, Any]]: ...
    def create_assessment(
        self, case_id: str, assessment: dict[str, Any], pages: list[dict[str, Any]],
    ) -> dict[str, Any]: ...
    def get_assessment(self, assessment_id: str) -> dict[str, Any]: ...
    def get_assessment_page(self, assessment_id: str, page_no: int) -> dict[str, Any]: ...
    def append_assessment_review(
        self, assessment_id: str, review: dict[str, Any],
    ) -> dict[str, Any]: ...
    def list_assessment_reviews(self, assessment_id: str) -> list[dict[str, Any]]: ...
    def write_assessment_report(
        self, assessment_id: str, report: dict[str, Any], markdown: str,
    ) -> dict[str, Any]: ...
    def list_comparisons(self, case_id: str) -> list[dict[str, Any]]: ...
    def create_comparison(
        self, case_id: str, comparison: dict[str, Any], pages: list[dict[str, Any]],
    ) -> dict[str, Any]: ...
    def get_comparison(self, comparison_id: str) -> dict[str, Any]: ...
    def get_comparison_page(self, comparison_id: str, page_no: int) -> dict[str, Any]: ...
    def append_comparison_review(
        self, comparison_id: str, review: dict[str, Any],
    ) -> dict[str, Any]: ...
    def list_comparison_reviews(self, comparison_id: str) -> list[dict[str, Any]]: ...
    def write_comparison_report(
        self, comparison_id: str, report: dict[str, Any], markdown: str,
    ) -> dict[str, Any]: ...


class EvaluationApplicationService:
    """保证评测只冻结生产 ParseRun，并把质量事实留在独立文件工作区。"""

    def __init__(
        self, store: EvaluationRunRepository, workspace: EvaluationWorkspacePort, data_dir: Path,
    ) -> None:
        self.store = store
        self.workspace = workspace
        self.data_dir = data_dir.resolve()

    def list_documents(self) -> list[dict[str, Any]]:
        return [self._case_resource(case) for case in self.workspace.list_cases()]

    def get_document(self, case_id: str) -> dict[str, Any]:
        case = self.workspace.get_case(case_id)
        result = self._case_resource(case)
        result["snapshots"] = [
            self._snapshot_resource(item) for item in self.workspace.list_snapshots(case_id)
        ]
        result["assessments"] = self.workspace.list_assessments(case_id)
        result["comparisons"] = self.workspace.list_comparisons(case_id)
        return result

    def materialize_document(self, case_id: str, source: Path) -> dict[str, Any]:
        """校验私有来源并只在本地工作区生成可运行副本。"""
        case = self.workspace.get_case(case_id)
        return self._case_resource(self.workspace.ensure_case(case, source.resolve()))

    def source_file(self, case_id: str) -> Path:
        return self.workspace.source_file(case_id)

    def capture(
        self,
        parse_run_id: str,
        label: str,
        revision: dict[str, Any],
        case_id: str | None = None,
    ) -> dict[str, Any]:
        revision = self._validate_revision(revision)
        run = self.store.get_parse_run(parse_run_id)
        if not run:
            raise NotFoundError("解析运行不存在")
        if run.get("status") != "parsed" or run.get("artifact_state", "available") != "available":
            raise ConflictError("只能捕获产物可用的成功 ParseRun")
        document = self.store.get_document(str(run["document_id"]))
        if not document:
            raise NotFoundError("解析运行所属文档不存在")

        content_hash = str(document["content_hash"])
        resolved_case_id = make_case_id(str(document["filename"]), content_hash)
        existing_case: dict[str, Any] | None = None
        if case_id:
            existing_case = self.workspace.get_case(case_id)
            if existing_case.get("source_hash") != content_hash:
                raise ConflictError("ParseRun 来源哈希与评测文档不一致")
            resolved_case_id = case_id
        case = {
            "schema_version": 1,
            "case_id": resolved_case_id,
            "title": str((existing_case or {}).get("title") or Path(str(document["filename"])).stem),
            "source_hash": content_hash,
            "page_count": int(document["page_count"]),
            "default_page_range": (existing_case or {}).get("default_page_range"),
            "visibility": (existing_case or {}).get("visibility"),
        }
        source_path = self._resolve_data_file(str(document["source_path"]))
        self.workspace.ensure_case(case, source_path)

        pages = self.store.list_pages_for_run(parse_run_id)
        page_numbers = sorted(int(page["page_no"]) for page in pages)
        if not page_numbers:
            raise ConflictError("ParseRun 没有可评估页面")
        files = self._snapshot_files(self.store.list_artifacts_for_run(parse_run_id))
        image_pages = {
            int(item.metadata["page_no"])
            for item in files
            if item.kind == "page_image" and item.metadata.get("page_no") is not None
        }
        for page in pages:
            page_no = int(page["page_no"])
            if page_no in image_pages:
                continue
            image = self._resolve_data_file(str(page["image_path"]))
            files.append(SnapshotFile(
                relative_path=f"artifacts/page_image/page-{page_no:04d}{image.suffix or '.png'}",
                source=image,
                kind="page_image",
                content_hash=_file_hash(image),
                size_bytes=image.stat().st_size,
                mime_type="image/png",
                metadata={"page_no": page_no, "render_contract": "render-200dpi-v1"},
            ))
        if not any(item.kind == "parse_manifest" for item in files):
            raise ConflictError("ParseRun 缺少最终 manifest")
        if not all(
            any(item.kind == "page_json" and item.metadata.get("page_no") == number for item in files)
            for number in page_numbers
        ):
            raise ConflictError("ParseRun 缺少页面 JSON 产物")

        snapshot_id = make_snapshot_id(label, str(revision["commit"]), parse_run_id)
        snapshot = {
            "schema_version": 2,
            "snapshot_id": snapshot_id,
            "case_id": resolved_case_id,
            "label": label.strip(),
            "parse_run_id": parse_run_id,
            "document_hash": content_hash,
            "page_numbers": page_numbers,
            "page_start": page_numbers[0],
            "page_end": page_numbers[-1],
            "model_calls": int(run.get("model_calls") or 0),
            "provider": run.get("provider_summary") or {},
            "created_at": _iso(run.get("created_at")),
            "finished_at": _iso(run.get("finished_at")),
            "revision": revision,
            "baseline_eligible": revision["branch"] == "main" and not revision["dirty"],
            "captured_at": _now(),
        }
        return self._snapshot_resource(
            self.workspace.create_snapshot(resolved_case_id, snapshot, files),
        )

    def assess(
        self, case_id: str, snapshot_id: str, manifest: dict[str, Any],
    ) -> dict[str, Any]:
        snapshot = self.workspace.verify_snapshot(case_id, snapshot_id)
        contract = validate_assessment_manifest(manifest, snapshot)
        image_pages = {
            int(item["metadata"]["page_no"])
            for item in snapshot.get("files", [])
            if item.get("kind") == "page_image" and item.get("metadata", {}).get("page_no") is not None
        }
        pages = [
            inspect_page(
                int(page_no),
                self.workspace.snapshot_page(case_id, snapshot_id, int(page_no)),
                has_page_image=int(page_no) in image_pages,
            )
            for page_no in snapshot["page_numbers"]
        ]
        assessment_id = make_assessment_id(snapshot_id, str(contract["experiment_id"]))
        execution_status = (
            "complete" if all(page["automatic_status"] == "passed" for page in pages) else "failed"
        )
        assessment = {
            "schema_version": 1,
            "assessment_id": assessment_id,
            "case_id": case_id,
            "snapshot_id": snapshot_id,
            **contract,
            "page_numbers": snapshot["page_numbers"],
            "execution_status": execution_status,
            "quality_status": "review_required",
            "pending_review_pages": contract["review_page_numbers"],
            "created_at": _now(),
        }
        return self.workspace.create_assessment(case_id, assessment, pages)

    def get_assessment(self, assessment_id: str) -> dict[str, Any]:
        assessment = self.workspace.get_assessment(assessment_id)
        reviews = self.workspace.list_assessment_reviews(assessment_id)
        result = dict(assessment)
        result["reviews"] = reviews
        result.update(assessment_quality(assessment, _latest_reviews(reviews)))
        return result

    def get_assessment_page(self, assessment_id: str, page_no: int) -> dict[str, Any]:
        self.workspace.get_assessment(assessment_id)
        page = self.workspace.get_assessment_page(assessment_id, page_no)
        page["source_image_url"] = (
            f"/api/v1/evaluations/assessments/{assessment_id}/pages/{page_no}/assets/source"
        )
        return page

    def assessment_asset(self, assessment_id: str, page_no: int) -> FileResource:
        assessment = self.workspace.get_assessment(assessment_id)
        return self.workspace.snapshot_asset(
            str(assessment["case_id"]), str(assessment["snapshot_id"]), "page_image", page_no,
        )

    def review_assessment(
        self,
        assessment_id: str,
        page_no: int,
        verdict: str,
        reason: str,
        reviewer: str,
        scores: dict[str, int] | None = None,
        critical_errors: list[str] | None = None,
    ) -> dict[str, Any]:
        assessment = self.workspace.get_assessment(assessment_id)
        validated = validate_assessment_review(
            assessment, page_no, verdict, scores, critical_errors,
        )
        if not reason.strip():
            raise ValueError("人工评估必须填写理由")
        review = {
            "schema_version": 1,
            "assessment_id": assessment_id,
            "page_no": page_no,
            **validated,
            "reason": reason.strip(),
            "reviewer": reviewer.strip() or "local-reviewer",
            "created_at": _now(),
        }
        return self.workspace.append_assessment_review(assessment_id, review)

    def report_assessment(self, assessment_id: str) -> dict[str, Any]:
        assessment = self.workspace.get_assessment(assessment_id)
        reviews = self.workspace.list_assessment_reviews(assessment_id)
        latest = _latest_reviews(reviews)
        quality = assessment_quality(assessment, latest)
        report = {
            "schema_version": 1,
            "assessment_id": assessment_id,
            "case_id": assessment["case_id"],
            "snapshot_id": assessment["snapshot_id"],
            "profile": assessment["profile"],
            "experiment_id": assessment["experiment_id"],
            "decision_scope": assessment["decision_scope"],
            "execution_status": assessment["execution_status"],
            **quality,
            "reviews": [latest[key] for key in sorted(latest)],
            "generated_at": _now(),
        }
        return self.workspace.write_assessment_report(
            assessment_id, report, _assessment_report_markdown(report),
        )

    def compare(self, case_id: str, baseline_id: str, candidate_id: str) -> dict[str, Any]:
        if baseline_id == candidate_id:
            raise ValueError("基线与候选必须是不同快照")
        baseline = self.workspace.verify_snapshot(case_id, baseline_id)
        candidate = self.workspace.verify_snapshot(case_id, candidate_id)
        if not baseline.get("baseline_eligible"):
            raise ConflictError("baseline 必须来自干净 main 修订")
        if baseline.get("document_hash") != candidate.get("document_hash"):
            raise ConflictError("基线与候选来源文档哈希不一致")
        if baseline.get("page_numbers") != candidate.get("page_numbers"):
            raise ConflictError("基线与候选页范围不一致")

        page_results = [
            compare_page(
                int(page_no),
                self.workspace.snapshot_page(case_id, baseline_id, int(page_no)),
                self.workspace.snapshot_page(case_id, candidate_id, int(page_no)),
            )
            for page_no in baseline["page_numbers"]
        ]
        changed_pages = [item["page_no"] for item in page_results if item["changed"]]
        comparison_id = make_comparison_id(case_id, baseline_id, candidate_id)
        comparison = {
            "schema_version": 2,
            "comparison_id": comparison_id,
            "case_id": case_id,
            "baseline_snapshot_id": baseline_id,
            "candidate_snapshot_id": candidate_id,
            "document_hash": baseline["document_hash"],
            "page_numbers": baseline["page_numbers"],
            "changed_pages": changed_pages,
            "pending_review_pages": changed_pages,
            "conclusion": "review_required" if changed_pages else "no_regression_detected",
            "has_gold": False,
            "created_at": _now(),
        }
        return self.workspace.create_comparison(case_id, comparison, page_results)

    def get_comparison(self, comparison_id: str) -> dict[str, Any]:
        comparison = self.workspace.get_comparison(comparison_id)
        reviews = self.workspace.list_comparison_reviews(comparison_id)
        result = dict(comparison)
        result["reviews"] = reviews
        result["pending_review_pages"] = _pending_comparison_pages(comparison, reviews)
        return result

    def get_comparison_page(self, comparison_id: str, page_no: int) -> dict[str, Any]:
        self.workspace.get_comparison(comparison_id)
        page = self.workspace.get_comparison_page(comparison_id, page_no)
        base = f"/api/v1/evaluations/comparisons/{comparison_id}/pages/{page_no}/assets"
        page["source_image_url"] = f"{base}/source"
        page["baseline_image_url"] = f"{base}/baseline"
        page["candidate_image_url"] = f"{base}/candidate"
        return page

    def comparison_asset(self, comparison_id: str, pane: str, page_no: int) -> FileResource:
        comparison = self.workspace.get_comparison(comparison_id)
        if pane in {"source", "baseline"}:
            snapshot_id = str(comparison["baseline_snapshot_id"])
        elif pane == "candidate":
            snapshot_id = str(comparison["candidate_snapshot_id"])
        else:
            raise ValueError("评测资源 pane 必须是 source、baseline 或 candidate")
        return self.workspace.snapshot_asset(
            str(comparison["case_id"]), snapshot_id, "page_image", page_no,
        )

    def review_comparison(
        self, comparison_id: str, page_no: int, verdict: str, reason: str, reviewer: str,
    ) -> dict[str, Any]:
        if verdict not in COMPARISON_VERDICTS:
            raise ValueError("比较 verdict 非法")
        comparison = self.workspace.get_comparison(comparison_id)
        if page_no not in comparison.get("page_numbers", []):
            raise ValueError("评审页不在 comparison 页范围内")
        if not reason.strip():
            raise ValueError("人工比较必须填写理由")
        review = {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "page_no": page_no,
            "verdict": verdict,
            "reason": reason.strip(),
            "reviewer": reviewer.strip() or "local-reviewer",
            "created_at": _now(),
        }
        return self.workspace.append_comparison_review(comparison_id, review)

    def report_comparison(self, comparison_id: str) -> dict[str, Any]:
        comparison = self.workspace.get_comparison(comparison_id)
        reviews = self.workspace.list_comparison_reviews(comparison_id)
        pending = _pending_comparison_pages(comparison, reviews)
        latest = _latest_reviews(reviews)
        needs_review = pending or any(
            item["verdict"] == "needs_review" for item in latest.values()
        )
        if not comparison.get("changed_pages"):
            conclusion = "no_regression_detected"
        elif needs_review:
            conclusion = "review_required"
        else:
            conclusion = "changed"
        report = {
            "schema_version": 1,
            "comparison_id": comparison_id,
            "case_id": comparison["case_id"],
            "conclusion": conclusion,
            "changed_pages": comparison.get("changed_pages", []),
            "pending_review_pages": pending,
            "reviews": [latest[key] for key in sorted(latest)],
            "generated_at": _now(),
        }
        return self.workspace.write_comparison_report(
            comparison_id, report, _comparison_report_markdown(report),
        )

    def _snapshot_files(self, artifacts: list[dict[str, Any]]) -> list[SnapshotFile]:
        result = []
        used: set[str] = set()
        for index, artifact in enumerate(artifacts, start=1):
            source = self._resolve_data_file(str(artifact["path"]))
            kind = str(artifact["kind"])
            metadata = dict(artifact.get("metadata") or {})
            page_no = metadata.get("page_no")
            suffix = source.suffix or ".bin"
            name = (
                f"page-{int(page_no):04d}{suffix}"
                if page_no is not None else f"artifact-{index:04d}{suffix}"
            )
            relative = f"artifacts/{kind}/{name}"
            if relative in used:
                relative = f"artifacts/{kind}/{index:04d}-{name}"
            used.add(relative)
            result.append(SnapshotFile(
                relative_path=relative,
                source=source,
                kind=kind,
                content_hash=str(artifact["content_hash"]),
                size_bytes=int(artifact["size_bytes"]),
                mime_type=str(artifact["mime_type"]),
                metadata=metadata,
            ))
        return result

    def _resolve_data_file(self, stored_path: str) -> Path:
        path = Path(stored_path)
        candidate = path.resolve() if path.is_absolute() else (self.data_dir / path).resolve()
        if not candidate.is_relative_to(self.data_dir) or not candidate.is_file():
            raise ConflictError("生产解析文件缺失或超出数据目录")
        return candidate

    @staticmethod
    def _validate_revision(revision: dict[str, Any]) -> dict[str, Any]:
        commit = str(revision.get("commit") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
            raise ValueError("评测修订必须包含 7 到 64 位 Git commit")
        branch = str(revision.get("branch") or "detached")
        dirty = bool(revision.get("dirty"))
        diff_hash = str(revision.get("diff_hash") or "")
        if dirty and not re.fullmatch(r"[0-9a-f]{64}", diff_hash):
            raise ValueError("脏工作树快照必须记录完整 diff hash")
        return {
            "commit": commit,
            "branch": branch,
            "dirty": dirty,
            "diff_hash": diff_hash or None,
        }

    @staticmethod
    def _case_resource(case: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value for key, value in case.items()
            if key not in {"source_path", "definition_path"}
        }

    @staticmethod
    def _snapshot_resource(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in snapshot.items() if key != "files"}


def make_case_id(title: str, content_hash: str) -> str:
    """生成 Windows 可用且保留中文可读性的稳定文档目录名。"""
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("文档内容哈希必须是 64 位小写 SHA-256")
    text = unicodedata.normalize("NFC", Path(title).stem)
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"\s+", "-", text).strip(" .-")
    text = re.sub(r"-+", "-", text)[:48].rstrip(" .-") or "document"
    return f"{text}--{content_hash[:12]}"


def validate_case_id(case_id: str, content_hash: str) -> None:
    """拒绝目录逃逸、短哈希错配和非规范 case ID。"""
    if not case_id or case_id in {".", ".."} or "/" in case_id or "\\" in case_id:
        raise ValueError("评测文档 ID 包含路径语义")
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("文档内容哈希必须是 64 位小写 SHA-256")
    if not case_id.endswith(f"--{content_hash[:12]}"):
        raise ValueError("评测文档 ID 与来源哈希不匹配")
    title = case_id.rsplit("--", 1)[0]
    if make_case_id(title, content_hash) != case_id:
        raise ValueError("评测文档 ID 未按规范化规则生成")


def make_snapshot_id(label: str, commit: str, parse_run_id: str) -> str:
    """生成不携带比较角色且区分真实 ParseRun 的快照 ID。"""
    label_text = _safe_label(label, 32)
    if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        raise ValueError("快照 commit 非法")
    run_fingerprint = hashlib.sha256(parse_run_id.encode("utf-8")).hexdigest()[:8]
    return f"{label_text}-{commit[:12]}-{run_fingerprint}"


def make_assessment_id(snapshot_id: str, experiment_id: str) -> str:
    prefix = _safe_label(experiment_id, 32)
    digest = hashlib.sha256(f"{snapshot_id}\0{experiment_id}".encode()).hexdigest()[:12]
    return f"assessment-{prefix}-{digest}"


def make_comparison_id(case_id: str, baseline_id: str, candidate_id: str) -> str:
    digest = hashlib.sha256(
        f"{case_id}\0{baseline_id}\0{candidate_id}".encode(),
    ).hexdigest()[:16]
    return f"comparison-{digest}"


def _safe_label(value: str, limit: int) -> str:
    text = unicodedata.normalize("NFC", value.strip())
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", text)
    text = re.sub(r"\s+", "-", text).strip(" .-")[:limit].rstrip(" .-")
    if not text:
        raise ValueError("评测标签非法")
    return text


def _pending_comparison_pages(
    comparison: dict[str, Any], reviews: list[dict[str, Any]],
) -> list[int]:
    latest = _latest_reviews(reviews)
    return [
        int(page) for page in comparison.get("changed_pages", []) if int(page) not in latest
    ]


def _latest_reviews(reviews: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(item["page_no"]): item for item in reviews}


def _assessment_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 单运行解析质量报告",
        "",
        f"- Assessment：`{report['assessment_id']}`",
        f"- 实验：`{report['experiment_id']}`",
        f"- 决策范围：`{report['decision_scope']}`",
        f"- 执行状态：`{report['execution_status']}`",
        f"- 质量状态：`{report['quality_status']}`",
        f"- 待审页：{report['pending_review_pages'] or '无'}",
        "",
        "## 人工结论",
        "",
    ]
    if not report["reviews"]:
        lines.append("尚无人工结论。")
    else:
        lines.extend(
            f"- 第 {item['page_no']} 页：`{item['verdict']}` - {item['reason']}"
            for item in report["reviews"]
        )
    return "\n".join(lines) + "\n"


def _comparison_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 解析比较报告",
        "",
        f"- Comparison：`{report['comparison_id']}`",
        f"- 结论：`{report['conclusion']}`",
        f"- 变化页：{report['changed_pages'] or '无'}",
        f"- 待审页：{report['pending_review_pages'] or '无'}",
        "",
        "## 人工结论",
        "",
    ]
    if not report["reviews"]:
        lines.append("尚无人工结论。")
    else:
        lines.extend(
            f"- 第 {item['page_no']} 页：`{item['verdict']}` - {item['reason']}"
            for item in report["reviews"]
        )
    return "\n".join(lines) + "\n"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
