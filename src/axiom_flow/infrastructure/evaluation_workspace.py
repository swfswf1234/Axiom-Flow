"""
模块职责：实现文档中心评测的安全路径、原子 JSON、不可变快照和文件资源定位。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/integration/test_evaluation_workspace.py、tests/unit/test_evaluation_comparison.py、tests/integration/test_evaluation_api.py
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from axiom_flow.application.evaluations import SnapshotFile, validate_case_id
from axiom_flow.domain.models import ConflictError, FileResource, NotFoundError


class EvaluationWorkspace:
    """把可版本化 case 与本地评测运行统一为可读文件工作区。"""

    def __init__(
        self,
        evaluation_data_dir: Path = Path("data/evaluation"),
        definitions_dir: Path = Path("evaluation/documents"),
    ) -> None:
        self.root = (evaluation_data_dir / "documents").resolve()
        self.definitions_dir = definitions_dir.resolve()

    def list_cases(self) -> list[dict[str, Any]]:
        cases: dict[str, dict[str, Any]] = {}
        for root in (self.definitions_dir, self.root):
            if not root.is_dir():
                continue
            for case_path in sorted(root.iterdir(), key=lambda value: value.name):
                if not case_path.is_dir() or not (case_path / "case.json").is_file():
                    continue
                case = self._load_case(case_path / "case.json")
                case["location"] = "runtime" if root == self.root else "versioned"
                cases[str(case["case_id"])] = case
        return [cases[key] for key in sorted(cases)]

    def get_case(self, case_id: str) -> dict[str, Any]:
        _safe_component(case_id, "评测文档 ID")
        for root in (self.root, self.definitions_dir):
            path = self._child(root, case_id) / "case.json"
            if path.is_file():
                case = self._load_case(path)
                case["location"] = "runtime" if root == self.root else "versioned"
                return case
        raise NotFoundError("评测文档不存在")

    def ensure_case(self, case: dict[str, Any], source: Path) -> dict[str, Any]:
        case_id = str(case.get("case_id") or "")
        content_hash = str(case.get("source_hash") or "")
        validate_case_id(case_id, content_hash)
        if _sha256(source) != content_hash:
            raise ConflictError("评测来源 PDF 哈希不一致")
        directory = self._child(self.root, case_id)
        case_path = directory / "case.json"
        if case_path.exists():
            existing = self._load_case(case_path)
            if existing["source_hash"] != content_hash:
                raise ConflictError("评测文档目录已绑定其他来源")
        else:
            directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "case_id": case_id,
                "title": str(case.get("title") or case_id.rsplit("--", 1)[0]),
                "source_hash": content_hash,
                "page_count": int(case.get("page_count") or 0),
                "default_page_range": case.get("default_page_range") or [1, int(case.get("page_count") or 0)],
                "source": {"path": "source/source.pdf", "sha256": content_hash},
            }
            if case.get("visibility"):
                payload["visibility"] = case["visibility"]
            _write_json_atomic(case_path, payload)
        target = directory / "source" / "source.pdf"
        if target.is_file():
            if _sha256(target) != content_hash:
                raise ConflictError("评测工作区中的来源 PDF 已损坏")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            _link_or_copy(source.resolve(), target)
        return self.get_case(case_id)

    def source_file(self, case_id: str) -> Path:
        case = self.get_case(case_id)
        source = case.get("source") or {}
        relative = str(source.get("path") or "source/source.pdf")
        root = self.root if case.get("location") == "runtime" else self.definitions_dir
        path = self._child(self._child(root, case_id), relative)
        if not path.is_file():
            raise NotFoundError("评测文档未提供本地来源 PDF")
        expected = str(source.get("sha256") or case["source_hash"])
        if _sha256(path) != expected:
            raise ConflictError("评测来源 PDF 哈希校验失败")
        return path

    def list_snapshots(self, case_id: str) -> list[dict[str, Any]]:
        self.get_case(case_id)
        runs = self._child(self._child(self.root, case_id), "runs")
        if not runs.is_dir():
            return []
        return [
            self._load_json(path / "run.json", "评测快照")
            for path in sorted(runs.iterdir(), key=lambda value: value.name)
            if path.is_dir() and (path / "run.json").is_file()
        ]

    def get_snapshot(self, case_id: str, snapshot_id: str) -> dict[str, Any]:
        self.get_case(case_id)
        _safe_component(snapshot_id, "评测快照 ID")
        path = self._child(self._child(self._child(self.root, case_id), "runs"), snapshot_id) / "run.json"
        if not path.is_file():
            raise NotFoundError("评测快照不存在")
        return self._load_json(path, "评测快照")

    def create_snapshot(
        self, case_id: str, snapshot: dict[str, Any], files: list[SnapshotFile],
    ) -> dict[str, Any]:
        case = self.get_case(case_id)
        if case["source_hash"] != snapshot.get("document_hash"):
            raise ConflictError("评测快照来源哈希与文档 case 不一致")
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        _safe_component(snapshot_id, "评测快照 ID")
        runs_root = self._child(self._child(self.root, case_id), "runs")
        target = self._child(runs_root, snapshot_id)
        if target.exists():
            raise ConflictError("同名评测快照已经存在，禁止覆盖")
        temporary = self._child(runs_root, f".tmp-{uuid.uuid4().hex[:8]}")
        temporary.mkdir(parents=True, exist_ok=False)
        entries = []
        try:
            for item in files:
                relative = _safe_relative(item.relative_path)
                destination = self._child(temporary, relative)
                if not item.source.is_file() or _sha256(item.source) != item.content_hash:
                    raise ConflictError(f"待捕获解析文件哈希不一致：{relative}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                _link_or_copy(item.source, destination)
                entries.append({
                    "path": relative,
                    "kind": item.kind,
                    "sha256": item.content_hash,
                    "size_bytes": item.size_bytes,
                    "mime_type": item.mime_type,
                    "metadata": item.metadata,
                })
            payload = {**snapshot, "files": entries}
            _write_json_atomic(temporary / "run.json", payload)
            runs_root.mkdir(parents=True, exist_ok=True)
            temporary.replace(target)
        except BaseException:
            if temporary.is_dir() and temporary.is_relative_to(runs_root):
                shutil.rmtree(temporary)
            raise
        return self.verify_snapshot(case_id, snapshot_id)

    def verify_snapshot(self, case_id: str, snapshot_id: str) -> dict[str, Any]:
        snapshot = self.get_snapshot(case_id, snapshot_id)
        root = self._snapshot_root(case_id, snapshot_id)
        files = snapshot.get("files")
        if not isinstance(files, list):
            raise ConflictError("评测快照文件清单非法")
        for item in files:
            if not isinstance(item, dict):
                raise ConflictError("评测快照文件项非法")
            relative = _safe_relative(str(item.get("path") or ""))
            candidate = self._child(root, relative)
            if not candidate.is_file():
                raise ConflictError(f"评测快照文件缺失：{relative}")
            if _sha256(candidate) != item.get("sha256"):
                raise ConflictError(f"评测快照文件哈希不一致：{relative}")
        return snapshot

    def snapshot_page(self, case_id: str, snapshot_id: str, page_no: int) -> dict[str, Any] | None:
        snapshot = self.get_snapshot(case_id, snapshot_id)
        item = self._find_snapshot_file(snapshot, "page_json", page_no)
        if item is None:
            return None
        path = self._child(self._snapshot_root(case_id, snapshot_id), str(item["path"]))
        return self._load_json(path, f"第 {page_no} 页快照")

    def snapshot_asset(
        self, case_id: str, snapshot_id: str, kind: str, page_no: int,
    ) -> FileResource:
        snapshot = self.get_snapshot(case_id, snapshot_id)
        item = self._find_snapshot_file(snapshot, kind, page_no)
        if item is None:
            raise NotFoundError("评测页面资源不存在")
        path = self._child(self._snapshot_root(case_id, snapshot_id), str(item["path"]))
        if not path.is_file() or _sha256(path) != item.get("sha256"):
            raise ConflictError("评测页面资源完整性校验失败")
        return FileResource(path=path, media_type=str(item["mime_type"]), filename=path.name)

    def list_assessments(self, case_id: str) -> list[dict[str, Any]]:
        self.get_case(case_id)
        root = self._child(self._child(self.root, case_id), "assessments")
        return self._list_resources(root, "assessment.json", "单运行评估")

    def create_assessment(
        self, case_id: str, assessment: dict[str, Any], pages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.get_case(case_id)
        assessment_id = str(assessment.get("assessment_id") or "")
        _safe_component(assessment_id, "单运行评估 ID")
        assessments = self._child(self._child(self.root, case_id), "assessments")
        target = self._child(assessments, assessment_id)
        if target.exists():
            existing = self._load_json(target / "assessment.json", "单运行评估")
            stable_fields = ("case_id", "snapshot_id", "profile", "experiment_id", "page_numbers")
            if any(existing.get(key) != assessment.get(key) for key in stable_fields):
                raise ConflictError("同名单运行评估已绑定其他输入")
            return existing
        temporary = self._child(assessments, f".tmp-{uuid.uuid4().hex[:8]}")
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            _write_json_atomic(temporary / "assessment.json", assessment)
            for page in pages:
                _write_json_atomic(
                    temporary / "pages" / f"page-{int(page['page_no']):04d}.json", page,
                )
            assessments.mkdir(parents=True, exist_ok=True)
            temporary.replace(target)
        except BaseException:
            if temporary.is_dir() and temporary.is_relative_to(assessments):
                shutil.rmtree(temporary)
            raise
        return self._load_json(target / "assessment.json", "单运行评估")

    def get_assessment(self, assessment_id: str) -> dict[str, Any]:
        _, root = self._locate_resource(
            assessment_id, "assessments", "assessment.json", "单运行评估",
        )
        return self._load_json(root / "assessment.json", "单运行评估")

    def get_assessment_page(self, assessment_id: str, page_no: int) -> dict[str, Any]:
        _, root = self._locate_resource(
            assessment_id, "assessments", "assessment.json", "单运行评估",
        )
        path = root / "pages" / f"page-{page_no:04d}.json"
        if not path.is_file():
            raise NotFoundError("单运行评估页面不存在")
        return self._load_json(path, "单运行评估页面")

    def append_assessment_review(
        self, assessment_id: str, review: dict[str, Any],
    ) -> dict[str, Any]:
        _, root = self._locate_resource(
            assessment_id, "assessments", "assessment.json", "单运行评估",
        )
        return self._append_review(root, review)

    def list_assessment_reviews(self, assessment_id: str) -> list[dict[str, Any]]:
        _, root = self._locate_resource(
            assessment_id, "assessments", "assessment.json", "单运行评估",
        )
        return self._list_reviews(root)

    def write_assessment_report(
        self, assessment_id: str, report: dict[str, Any], markdown: str,
    ) -> dict[str, Any]:
        _, root = self._locate_resource(
            assessment_id, "assessments", "assessment.json", "单运行评估",
        )
        return self._write_report(root, report, markdown)

    def list_comparisons(self, case_id: str) -> list[dict[str, Any]]:
        self.get_case(case_id)
        root = self._child(self._child(self.root, case_id), "comparisons")
        return self._list_resources(root, "comparison.json", "评测比较")

    def create_comparison(
        self, case_id: str, comparison: dict[str, Any], pages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.get_case(case_id)
        comparison_id = str(comparison.get("comparison_id") or "")
        _safe_component(comparison_id, "评测比较 ID")
        comparisons = self._child(self._child(self.root, case_id), "comparisons")
        target = self._child(comparisons, comparison_id)
        if target.exists():
            existing = self._load_json(target / "comparison.json", "评测比较")
            stable_fields = ("case_id", "baseline_snapshot_id", "candidate_snapshot_id", "document_hash", "page_numbers")
            if any(existing.get(key) != comparison.get(key) for key in stable_fields):
                raise ConflictError("同名 comparison 已绑定其他输入")
            return existing
        temporary = self._child(comparisons, f".tmp-{uuid.uuid4().hex[:8]}")
        temporary.mkdir(parents=True, exist_ok=False)
        try:
            _write_json_atomic(temporary / "comparison.json", comparison)
            for page in pages:
                _write_json_atomic(temporary / "pages" / f"page-{int(page['page_no']):04d}.json", page)
            comparisons.mkdir(parents=True, exist_ok=True)
            temporary.replace(target)
        except BaseException:
            if temporary.is_dir() and temporary.is_relative_to(comparisons):
                shutil.rmtree(temporary)
            raise
        return comparison

    def get_comparison(self, comparison_id: str) -> dict[str, Any]:
        _, root = self._locate_comparison(comparison_id)
        return self._load_json(root / "comparison.json", "评测比较")

    def get_comparison_page(self, comparison_id: str, page_no: int) -> dict[str, Any]:
        _, root = self._locate_comparison(comparison_id)
        path = root / "pages" / f"page-{page_no:04d}.json"
        if not path.is_file():
            raise NotFoundError("评测比较页面不存在")
        return self._load_json(path, "评测比较页面")

    def append_comparison_review(
        self, comparison_id: str, review: dict[str, Any],
    ) -> dict[str, Any]:
        _, root = self._locate_comparison(comparison_id)
        return self._append_review(root, review)

    def list_comparison_reviews(self, comparison_id: str) -> list[dict[str, Any]]:
        _, root = self._locate_comparison(comparison_id)
        return self._list_reviews(root)

    def write_comparison_report(
        self, comparison_id: str, report: dict[str, Any], markdown: str,
    ) -> dict[str, Any]:
        _, root = self._locate_comparison(comparison_id)
        return self._write_report(root, report, markdown)

    @staticmethod
    def _append_review(root: Path, review: dict[str, Any]) -> dict[str, Any]:
        path = root / "review.jsonl"
        payload = json.dumps(review, ensure_ascii=False, separators=(",", ":")) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return review

    @staticmethod
    def _list_reviews(root: Path) -> list[dict[str, Any]]:
        path = root / "review.jsonl"
        if not path.is_file():
            return []
        reviews = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"人工评审 JSONL 第 {number} 行损坏：{exc}") from exc
            if not isinstance(value, dict):
                raise ValueError(f"人工评审 JSONL 第 {number} 行必须是对象")
            reviews.append(value)
        return reviews

    @staticmethod
    def _write_report(
        root: Path, report: dict[str, Any], markdown: str,
    ) -> dict[str, Any]:
        _write_json_atomic(root / "report.json", report)
        _write_text_atomic(root / "report.md", markdown)
        return report

    def _load_case(self, path: Path) -> dict[str, Any]:
        case = self._load_json(path, "评测文档 case")
        case_id = str(case.get("case_id") or path.parent.name)
        source_hash = str(case.get("source_hash") or "")
        validate_case_id(case_id, source_hash)
        if path.parent.name != case_id:
            raise ValueError("评测文档目录名与 case_id 不一致")
        case["case_id"] = case_id
        return case

    @staticmethod
    def _load_json(path: Path, name: str) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"{name} 无法读取：{exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{name} 必须是 JSON 对象")
        return value

    def _snapshot_root(self, case_id: str, snapshot_id: str) -> Path:
        return self._child(self._child(self._child(self.root, case_id), "runs"), snapshot_id)

    @staticmethod
    def _find_snapshot_file(
        snapshot: dict[str, Any], kind: str, page_no: int,
    ) -> dict[str, Any] | None:
        return next((
            item for item in snapshot.get("files", [])
            if item.get("kind") == kind and int((item.get("metadata") or {}).get("page_no", -1)) == page_no
        ), None)

    def _locate_comparison(self, comparison_id: str) -> tuple[str, Path]:
        return self._locate_resource(
            comparison_id, "comparisons", "comparison.json", "评测比较",
        )

    def _locate_resource(
        self, resource_id: str, directory: str, filename: str, name: str,
    ) -> tuple[str, Path]:
        _safe_component(resource_id, f"{name} ID")
        if not self.root.is_dir():
            raise NotFoundError(f"{name}不存在")
        matches = []
        for case_path in self.root.iterdir():
            if not case_path.is_dir():
                continue
            candidate = self._child(self._child(case_path, directory), resource_id)
            if (candidate / filename).is_file():
                matches.append((case_path.name, candidate))
        if not matches:
            raise NotFoundError(f"{name}不存在")
        if len(matches) > 1:
            raise ConflictError(f"{name} ID 在多个文档中冲突")
        return matches[0]

    def _list_resources(self, root: Path, filename: str, name: str) -> list[dict[str, Any]]:
        if not root.is_dir():
            return []
        return [
            self._load_json(path / filename, name)
            for path in sorted(root.iterdir(), key=lambda value: value.name)
            if path.is_dir() and (path / filename).is_file()
        ]

    @staticmethod
    def _child(root: Path, relative: str) -> Path:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root.resolve()):
            raise ValueError("评测路径超出工作区")
        return candidate


def _safe_component(value: str, name: str) -> str:
    if not value or value in {".", ".."} or any(character in value for character in "/\\\x00"):
        raise ValueError(f"{name} 包含路径语义")
    if value.rstrip(" .") != value or any(character in value for character in '<>:"|?*'):
        raise ValueError(f"{name} 不是安全目录名")
    return value


def _safe_relative(value: str) -> str:
    path = Path(value.replace("\\", "/"))
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("评测文件相对路径非法")
    return path.as_posix()


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, default=_json_default) + "\n")


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _json_default(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    raise TypeError(f"无法序列化的评测值：{type(value).__name__}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
