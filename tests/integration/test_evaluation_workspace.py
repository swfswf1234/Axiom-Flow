"""
模块职责：验证文档中心评测命名、文件工作区与不可变快照边界。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：src/axiom_flow/infrastructure/evaluation_workspace.py
"""

import hashlib
import json
from pathlib import Path

import pytest

from axiom_flow.application.evaluations import SnapshotFile, make_case_id, validate_case_id
from axiom_flow.domain.models import ConflictError
from axiom_flow.infrastructure.evaluation_workspace import EvaluationWorkspace


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _case(workspace: EvaluationWorkspace, source: Path) -> dict:
    content_hash = _hash(source)
    case_id = make_case_id("数学 分析：原理?.pdf", content_hash)
    return workspace.ensure_case({
        "case_id": case_id, "title": "数学分析原理", "source_hash": content_hash, "page_count": 1,
    }, source)


def test_readable_case_id_normalizes_chinese_and_rejects_traversal():
    content_hash = "a" * 64
    case_id = make_case_id("  数学 分析：原理?.pdf  ", content_hash)
    assert case_id == "数学-分析：原理--aaaaaaaaaaaa"
    validate_case_id(case_id, content_hash)
    with pytest.raises(ValueError, match="路径语义"):
        validate_case_id("../数学--aaaaaaaaaaaa", content_hash)
    with pytest.raises(ValueError, match="不匹配"):
        validate_case_id("数学--bbbbbbbbbbbb", content_hash)
    with pytest.raises(ValueError, match="64 位"):
        make_case_id("数学", "short")


def test_workspace_rejects_malformed_case_json(tmp_path: Path):
    definitions = tmp_path / "definitions"
    directory = definitions / "数学--aaaaaaaaaaaa"
    directory.mkdir(parents=True)
    (directory / "case.json").write_text("{broken", encoding="utf-8")
    workspace = EvaluationWorkspace(tmp_path / "evaluation-data", definitions)
    with pytest.raises(ValueError, match="无法读取"):
        workspace.list_cases()


def test_snapshot_survives_source_removal_and_detects_tampering(tmp_path: Path):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"private-pdf")
    artifact = tmp_path / "page-0001.json"
    artifact.write_text(json.dumps({"page_no": 1, "markdown": "正文"}), encoding="utf-8")
    workspace = EvaluationWorkspace(tmp_path / "evaluation-data", tmp_path / "definitions")
    case = _case(workspace, source)
    snapshot = workspace.create_snapshot(case["case_id"], {
        "schema_version": 2,
        "snapshot_id": "test-abcdef123456-deadbeef",
        "case_id": case["case_id"],
        "document_hash": case["source_hash"],
        "page_numbers": [1],
    }, [SnapshotFile(
        "artifacts/page_json/page-0001.json", artifact, "page_json", _hash(artifact),
        artifact.stat().st_size, "application/json", {"page_no": 1},
    )])
    artifact.unlink()
    assert workspace.verify_snapshot(case["case_id"], snapshot["snapshot_id"])["page_numbers"] == [1]
    frozen = workspace.root / case["case_id"] / "runs" / snapshot["snapshot_id"] / "artifacts" / "page_json" / "page-0001.json"
    frozen.write_text("tampered", encoding="utf-8")
    with pytest.raises(ConflictError, match="哈希不一致"):
        workspace.verify_snapshot(case["case_id"], snapshot["snapshot_id"])


def test_snapshot_copy_fallback_is_independent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf")
    artifact = tmp_path / "manifest.json"
    artifact.write_text("{}", encoding="utf-8")
    workspace = EvaluationWorkspace(tmp_path / "evaluation-data", tmp_path / "definitions")
    case = _case(workspace, source)

    def fail_link(*_args):
        raise OSError("hardlink unavailable")

    monkeypatch.setattr("axiom_flow.infrastructure.evaluation_workspace.os.link", fail_link)
    snapshot = workspace.create_snapshot(case["case_id"], {
        "schema_version": 2,
        "snapshot_id": "main-abcdef123456-deadbeef",
        "case_id": case["case_id"],
        "document_hash": case["source_hash"],
        "page_numbers": [],
    }, [SnapshotFile(
        "artifacts/parse_manifest/manifest.json", artifact, "parse_manifest", _hash(artifact),
        artifact.stat().st_size, "application/json", {},
    )])
    assert workspace.verify_snapshot(case["case_id"], snapshot["snapshot_id"])["snapshot_id"] == snapshot["snapshot_id"]
