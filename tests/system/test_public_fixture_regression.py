"""
模块职责：验证公开 fixture 回放、完整事实比较和篡改检测。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：evaluation/tools/fixture_builder.py、evaluation/tools/replay.py、evaluation/tools/regression.py
"""

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

from evaluation.tools.fixture_builder import build_fixture
from evaluation.tools.regression import compare_fixture, main
from evaluation.tools.replay import materialize_fixture

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "evaluation" / "documents" / "数学分析回归样本--2249d79fb6d0"


def _actual(tmp_path: Path) -> Path:
    return materialize_fixture(FIXTURE, tmp_path / "data")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refresh_manifest(run_dir: Path, relative: str) -> None:
    manifest_path = run_dir / "manifest.json"
    manifest = _json(manifest_path)
    target = run_dir / relative
    item = next(entry for entry in manifest["files"] if entry["path"] == relative)
    item["sha256"] = _sha256(target)
    item["size_bytes"] = target.stat().st_size
    _write_json(manifest_path, manifest)


def _failed_names(report: dict) -> set[str]:
    return {item["name"] for item in report["checks"] if not item["passed"]}


def test_public_fixture_replays_to_a_complete_passing_bundle(tmp_path: Path):
    first = _actual(tmp_path / "first")
    second = _actual(tmp_path / "second")

    report = compare_fixture(FIXTURE, first)

    assert report["passed"] is True
    assert report["fixture_id"] == "math-sample-v1"
    assert report["expected_manifest_sha256"]
    assert report["actual_manifest_sha256"]
    assert _sha256(first / "manifest.json") == _sha256(second / "manifest.json")
    assert all(item["passed"] for item in report["checks"])


def test_fixture_builder_creates_a_self_consistent_source_and_gold():
    # Windows 的内容哈希产物路径较长，使用系统短临时根避免 Pytest 节点名消耗路径预算。
    with tempfile.TemporaryDirectory(prefix="af-fixture-") as temporary:
        rebuilt = build_fixture(Path(temporary) / "fixture")
        fixture = _json(rebuilt / "fixture.json")
        committed = _json(FIXTURE / "fixture.json")
        actual = rebuilt / "expected" / fixture["expected_run"]

        assert fixture["fixture_id"] == committed["fixture_id"]
        assert fixture["source"]["page_count"] == committed["source"]["page_count"]
        assert fixture["license"] == "CC0-1.0"
        assert (rebuilt / "source.md").read_text(encoding="utf-8") == (FIXTURE / "source.md").read_text(encoding="utf-8")
        for page_no in range(1, fixture["source"]["page_count"] + 1):
            relative = Path("replay") / "pages" / f"page-{page_no:04d}.json"
            assert _json(rebuilt / relative) == _json(FIXTURE / relative)
        assert compare_fixture(rebuilt, actual)["passed"] is True


def test_regression_rejects_text_and_formula_changes_even_with_refreshed_hash(tmp_path: Path):
    actual = _actual(tmp_path)
    page_path = actual / "pages" / "page-0001.json"
    page = _json(page_path)
    page["markdown"] = page["markdown"].replace("convergence", "divergence")
    formula = next(block for block in page["blocks"] if block["kind"] == "formula")
    formula["latex"] = formula["latex"].replace("a_n", "b_n")
    _write_json(page_path, page)
    _refresh_manifest(actual, "pages/page-0001.json")

    report = compare_fixture(FIXTURE, actual)
    failed = _failed_names(report)

    assert report["passed"] is False
    assert "page-0001.markdown" in failed
    assert "page-0001.block-04.latex" in failed


def test_regression_rejects_table_and_source_bbox_changes(tmp_path: Path):
    actual = _actual(tmp_path)
    page_path = actual / "pages" / "page-0003.json"
    page = _json(page_path)
    table = next(block for block in page["blocks"] if block["kind"] == "table")
    table["content"] = table["content"].replace("0.125", "0.250")
    page["evidence"][0]["bbox"] = [500, 800, 594, 841]
    _write_json(page_path, page)
    _refresh_manifest(actual, "pages/page-0003.json")

    report = compare_fixture(FIXTURE, actual)
    failed = _failed_names(report)

    assert report["passed"] is False
    assert "page-0003.block-03.table" in failed
    assert "page-0003.evidence-01.bbox.iou" in failed


def test_regression_rejects_missing_content_image_and_manifest_tampering(tmp_path: Path):
    actual = _actual(tmp_path)
    content_image = next((actual / "content-images").glob("*.png"))
    content_image.unlink()

    report = compare_fixture(FIXTURE, actual)

    assert report["passed"] is False
    assert "actual.manifest.integrity" in _failed_names(report)
    assert any(name.startswith("page-0003.content-images") for name in _failed_names(report))


def test_regression_rejects_out_of_page_block_bbox(tmp_path: Path):
    actual = _actual(tmp_path)
    page_path = actual / "pages" / "page-0004.json"
    page = _json(page_path)
    page["blocks"][0]["bbox"] = [-1, 10, 100, 50]
    _write_json(page_path, page)
    _refresh_manifest(actual, "pages/page-0004.json")

    report = compare_fixture(FIXTURE, actual)

    assert report["passed"] is False
    assert "page-0004.block-01.bbox.bounds" in _failed_names(report)


def test_regression_cli_uses_stable_success_and_contract_exit_codes(tmp_path: Path, monkeypatch):
    actual = _actual(tmp_path)
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [
        "evaluation.tools.regression", "--fixture", str(FIXTURE),
        "--actual", str(actual), "--output", str(output),
    ])

    main()

    assert _json(output)["passed"] is True
    monkeypatch.setattr(sys, "argv", [
        "evaluation.tools.regression", "--fixture", str(tmp_path / "missing"),
        "--actual", str(actual), "--output", str(output),
    ])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
