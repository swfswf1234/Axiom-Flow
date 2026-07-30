"""
模块职责：校验公开数学 fixture 与实际解析包的完整页面事实和来源证据。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/system/test_public_fixture_regression.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import fitz

REPORT_SCHEMA_VERSION = 1
FIXTURE_SCHEMA_VERSION = 1
DEFAULT_BBOX_IOU = 0.75


def compare_fixture(fixture_dir: Path, actual_run_dir: Path) -> dict[str, Any]:
    """返回逐项检查报告；fixture 契约非法时抛出 ValueError。"""
    fixture_dir = fixture_dir.resolve()
    fixture = _load_json(fixture_dir / "fixture.json", "fixture.json")
    _validate_fixture(fixture_dir, fixture)
    expected_root = (fixture_dir / "expected").resolve()
    expected_run = (expected_root / str(fixture["expected_run"])).resolve()
    if not expected_run.is_relative_to(expected_root):
        raise ValueError("fixture expected_run 超出 expected 目录")
    actual_run = actual_run_dir.resolve()
    expected_manifest = _verify_bundle(expected_run, expected_root, strict=True)

    checks: list[dict[str, Any]] = []
    try:
        actual_manifest = _verify_bundle(actual_run, _infer_data_root(actual_run), strict=False)
        _check(checks, "actual.manifest.integrity", True, "manifest 与全部文件哈希有效")
    except ValueError as exc:
        actual_manifest = _load_manifest_or_empty(actual_run)
        _check(checks, "actual.manifest.integrity", False, str(exc))

    for field in ("schema_version", "document_hash", "page_count", "page_numbers"):
        _check(
            checks,
            f"manifest.{field}",
            actual_manifest.get(field) == expected_manifest.get(field),
            f"expected={expected_manifest.get(field)!r}, actual={actual_manifest.get(field)!r}",
        )
    expected_provider = expected_manifest.get("provider", {})
    actual_provider = actual_manifest.get("provider", {})
    for field in ("adapter", "contract_version", "page_range"):
        _check(
            checks,
            f"manifest.provider.{field}",
            actual_provider.get(field) == expected_provider.get(field),
            f"expected={expected_provider.get(field)!r}, actual={actual_provider.get(field)!r}",
        )

    _compare_text_file(checks, "document.markdown", expected_run / "document.md", actual_run / "document.md")
    threshold = float(fixture.get("bbox_iou_threshold", DEFAULT_BBOX_IOU))
    source = fixture_dir / str(fixture["source"]["path"])
    with fitz.open(source) as document:
        for page_no in expected_manifest.get("page_numbers", []):
            expected_page_path = expected_run / "pages" / f"page-{int(page_no):04d}.json"
            actual_page_path = actual_run / "pages" / f"page-{int(page_no):04d}.json"
            _compare_page(
                checks,
                int(page_no),
                expected_page_path,
                actual_page_path,
                document[int(page_no) - 1].rect,
                threshold,
            )
            _compare_text_file(
                checks,
                f"page-{int(page_no):04d}.markdown",
                expected_run / "pages" / f"page-{int(page_no):04d}.md",
                actual_run / "pages" / f"page-{int(page_no):04d}.md",
            )
            _compare_content_images(checks, int(page_no), expected_page_path, actual_page_path, expected_run, actual_run)

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "fixture_id": fixture["fixture_id"],
        "source_sha256": fixture["source"]["sha256"],
        "expected_manifest_sha256": _sha256(expected_run / "manifest.json"),
        "actual_manifest_sha256": _sha256(actual_run / "manifest.json")
        if (actual_run / "manifest.json").is_file()
        else None,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }


def _validate_fixture(fixture_dir: Path, fixture: dict[str, Any]) -> None:
    if fixture.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError(f"只支持 fixture schema {FIXTURE_SCHEMA_VERSION}")
    fixture_id = fixture.get("fixture_id")
    if not isinstance(fixture_id, str) or not fixture_id.strip():
        raise ValueError("fixture_id 必须是非空字符串")
    source = fixture.get("source")
    if not isinstance(source, dict):
        raise ValueError("fixture.source 必须是对象")
    source_path = (fixture_dir / str(source.get("path", ""))).resolve()
    if not source_path.is_relative_to(fixture_dir) or not source_path.is_file():
        raise ValueError("fixture 源 PDF 缺失或越界")
    if _sha256(source_path) != source.get("sha256"):
        raise ValueError("fixture 源 PDF 哈希不一致")
    with fitz.open(source_path) as document:
        if len(document) != source.get("page_count"):
            raise ValueError("fixture 源 PDF 页数不一致")
    for required in ("source.md", "license.md"):
        if not (fixture_dir / required).is_file():
            raise ValueError(f"fixture 缺少 {required}")
    threshold = fixture.get("bbox_iou_threshold", DEFAULT_BBOX_IOU)
    if not isinstance(threshold, (int, float)) or not 0 < float(threshold) <= 1:
        raise ValueError("bbox_iou_threshold 必须位于 (0, 1]")


def _verify_bundle(run_dir: Path, data_root: Path, *, strict: bool) -> dict[str, Any]:
    manifest = _load_json(run_dir / "manifest.json", "parse manifest")
    if manifest.get("schema_version") != 2:
        raise ValueError("解析 manifest 必须使用 schema v2")
    for collection, root in ((manifest.get("files"), run_dir), (manifest.get("shared_assets"), data_root)):
        if not isinstance(collection, list):
            raise ValueError("manifest 文件清单非法")
        for item in collection:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                raise ValueError("manifest 文件项非法")
            candidate = (root / item["path"]).resolve()
            if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
                raise ValueError(f"解析产物缺失：{item['path']}")
            if _sha256(candidate) != item.get("sha256"):
                raise ValueError(f"解析产物哈希不一致：{item['path']}")
    if strict and manifest.get("page_count") != len(manifest.get("page_numbers", [])):
        raise ValueError("金标 manifest 页数非法")
    return manifest


def _compare_page(
    checks: list[dict[str, Any]],
    page_no: int,
    expected_path: Path,
    actual_path: Path,
    page_rect: fitz.Rect,
    threshold: float,
) -> None:
    name = f"page-{page_no:04d}"
    try:
        expected = _load_json(expected_path, f"{name} expected")
        actual = _load_json(actual_path, f"{name} actual")
    except ValueError as exc:
        _check(checks, f"{name}.json", False, str(exc))
        return
    for field in ("schema_version", "page_no", "page_kind"):
        _check(checks, f"{name}.{field}", actual.get(field) == expected.get(field), "字段必须与金标一致")
    _check(checks, f"{name}.markdown", _normalize_text(actual.get("markdown")) == _normalize_text(expected.get("markdown")), "页面 Markdown 必须与金标一致")

    expected_blocks = expected.get("blocks")
    actual_blocks = actual.get("blocks")
    if not isinstance(expected_blocks, list) or not isinstance(actual_blocks, list):
        _check(checks, f"{name}.blocks", False, "blocks 必须是数组")
    else:
        _check(checks, f"{name}.blocks.count", len(actual_blocks) == len(expected_blocks), f"expected={len(expected_blocks)}, actual={len(actual_blocks)}")
        for index, expected_block in enumerate(expected_blocks):
            if index >= len(actual_blocks) or not isinstance(actual_blocks[index], dict):
                continue
            actual_block = actual_blocks[index]
            prefix = f"{name}.block-{index + 1:02d}"
            for field in ("order_no", "kind"):
                _check(checks, f"{prefix}.{field}", actual_block.get(field) == expected_block.get(field), "块顺序和类型必须一致")
            _check(checks, f"{prefix}.content", _normalize_text(actual_block.get("content")) == _normalize_text(expected_block.get("content")), "块正文必须一致")
            if expected_block.get("kind") == "formula":
                _check(checks, f"{prefix}.latex", _normalize_latex(actual_block.get("latex")) == _normalize_latex(expected_block.get("latex")), "公式 LaTeX 必须一致")
            if expected_block.get("kind") == "table":
                _check(checks, f"{prefix}.table", _table_matrix(actual_block.get("content")) == _table_matrix(expected_block.get("content")), "表格矩阵必须一致")
            _compare_bbox(checks, f"{prefix}.bbox", expected_block.get("bbox"), actual_block.get("bbox"), page_rect, threshold)

    expected_evidence = expected.get("evidence")
    actual_evidence = actual.get("evidence")
    if not isinstance(expected_evidence, list) or not isinstance(actual_evidence, list):
        _check(checks, f"{name}.evidence", False, "evidence 必须是数组")
    else:
        _check(checks, f"{name}.evidence.count", len(actual_evidence) == len(expected_evidence), "来源证据数量必须一致")
        for index, expected_item in enumerate(expected_evidence):
            if index >= len(actual_evidence) or not isinstance(actual_evidence[index], dict):
                continue
            actual_item = actual_evidence[index]
            prefix = f"{name}.evidence-{index + 1:02d}"
            for field in ("kind", "page_no"):
                _check(checks, f"{prefix}.{field}", actual_item.get(field) == expected_item.get(field), "证据类型和页码必须一致")
            _check(checks, f"{prefix}.quote", _normalize_text(actual_item.get("quote")) == _normalize_text(expected_item.get("quote")), "证据引文必须一致")
            _compare_bbox(checks, f"{prefix}.bbox", expected_item.get("bbox"), actual_item.get("bbox"), page_rect, threshold)


def _compare_bbox(
    checks: list[dict[str, Any]], name: str, expected: Any, actual: Any, page_rect: fitz.Rect, threshold: float,
) -> None:
    valid = _valid_bbox(actual, page_rect)
    _check(checks, f"{name}.bounds", valid, "bbox 必须位于页面范围内")
    if not valid or not _valid_bbox(expected, page_rect):
        _check(checks, f"{name}.iou", False, "bbox 缺失或金标非法")
        return
    iou = _iou(expected, actual)
    _check(checks, f"{name}.iou", iou >= threshold, f"IoU={iou:.3f}, minimum={threshold:.3f}")


def _compare_content_images(
    checks: list[dict[str, Any]], page_no: int, expected_page_path: Path, actual_page_path: Path,
    expected_run: Path, actual_run: Path,
) -> None:
    try:
        expected = _load_json(expected_page_path, "expected page")
        actual = _load_json(actual_page_path, "actual page")
    except ValueError:
        return
    expected_images = expected.get("content_images", [])
    actual_images = actual.get("content_images", [])
    name = f"page-{page_no:04d}.content-images"
    _check(checks, f"{name}.count", len(actual_images) == len(expected_images), "内容图片数量必须一致")
    for index, expected_relative in enumerate(expected_images):
        if index >= len(actual_images):
            continue
        expected_path = _resolve_run_resource(expected_run, str(expected_relative))
        actual_path = _resolve_run_resource(actual_run, str(actual_images[index]))
        passed = expected_path.is_file() and actual_path.is_file() and _sha256(expected_path) == _sha256(actual_path)
        _check(checks, f"{name}.{index + 1}", passed, "内容图片像素哈希必须一致")


def _resolve_run_resource(run_dir: Path, relative: str) -> Path:
    marker = "/parse-runs/"
    normalized = relative.replace("\\", "/")
    if marker in normalized:
        suffix = normalized.split(marker, 1)[1].split("/", 1)[1]
        root = run_dir.resolve()
        candidate = (root / suffix).resolve()
    else:
        root = _infer_data_root(run_dir).resolve()
        candidate = (root / normalized).resolve()
    return candidate if candidate.is_relative_to(root) else root / "__invalid_resource__"


def _compare_text_file(checks: list[dict[str, Any]], name: str, expected: Path, actual: Path) -> None:
    if not expected.is_file() or not actual.is_file():
        _check(checks, name, False, "文件缺失")
        return
    _check(checks, name, _normalize_text(actual.read_text(encoding="utf-8")) == _normalize_text(expected.read_text(encoding="utf-8")), "规范化文本必须一致")


def _normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def _normalize_latex(value: Any) -> str:
    return re.sub(r"\s+", "", _normalize_text(value))


def _table_matrix(value: Any) -> list[list[str]]:
    rows = []
    for line in _normalize_text(value).splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            rows.append(cells)
    return rows


def _valid_bbox(value: Any, page_rect: fitz.Rect) -> bool:
    if not isinstance(value, list) or len(value) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return False
    return 0 <= x0 < x1 <= page_rect.width and 0 <= y0 < y1 <= page_rect.height


def _iou(left: list[float], right: list[float]) -> float:
    lx0, ly0, lx1, ly1 = (float(item) for item in left)
    rx0, ry0, rx1, ry1 = (float(item) for item in right)
    width = max(0.0, min(lx1, rx1) - max(lx0, rx0))
    height = max(0.0, min(ly1, ry1) - max(ly0, ry0))
    intersection = width * height
    left_area = (lx1 - lx0) * (ly1 - ly0)
    right_area = (rx1 - rx0) * (ry1 - ry0)
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def _infer_data_root(run_dir: Path) -> Path:
    if run_dir.parent.name != "parse-runs" or run_dir.parent.parent.parent.name != "documents":
        raise ValueError("解析运行目录必须符合 data/documents/<hash>/parse-runs/<run-id>")
    return run_dir.parents[3]


def _load_manifest_or_empty(run_dir: Path) -> dict[str, Any]:
    try:
        return _load_json(run_dir / "manifest.json", "parse manifest")
    except ValueError:
        return {}


def _load_json(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} 无法读取：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} 必须是 JSON 对象")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--actual", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = compare_fixture(args.fixture, args.actual)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
