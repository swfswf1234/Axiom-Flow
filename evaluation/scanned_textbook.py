"""
模块职责：按冻结 manifest 运行扫描教材候选解析器并生成可人工评分的本地产物。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/test_scanned_textbook_evaluation.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import fitz

from axiom_flow.infrastructure.bailian import BailianProvider
from axiom_flow.infrastructure.config import Settings
from evaluation.scorecard import validate_manifest


async def run_candidate(
    source: Path, manifest: dict[str, Any], output_dir: Path, settings: Settings,
    *, calls_already_used: int = 0, timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """运行一个候选并保留完整响应；返回不包含凭证和绝对路径的摘要。"""
    validate_manifest(manifest)
    source_hash = _sha256(source)
    artifact_ids = {str(page["artifact_id"]) for page in manifest["pages"]}
    if artifact_ids != {f"sha256:{source_hash}"}:
        raise ValueError("manifest artifact_id 与源 PDF 哈希不一致")
    budget = int(manifest["budget"]["max_model_calls"])
    if calls_already_used < 0 or calls_already_used >= budget:
        raise ValueError("calls_already_used 必须小于 manifest 预算")
    updates: dict[str, Any] = {"model_call_budget": budget}
    if timeout_seconds is not None:
        updates["model_timeout_seconds"] = timeout_seconds
    resolved = settings.model_copy(update=updates)
    provider = BailianProvider(resolved)
    provider.calls = calls_already_used
    candidate_dir = output_dir / resolved.vision_model
    candidate_dir.mkdir(parents=True, exist_ok=True)
    prepare = getattr(provider, "prepare_document", None)
    if prepare:
        await prepare(source, candidate_dir)

    started = time.perf_counter()
    summaries = []
    with fitz.open(source) as document:
        for item in manifest["pages"]:
            page_no = int(item["page_no"])
            if page_no > len(document):
                raise ValueError(f"manifest 页码超出 PDF：{page_no}")
            page = document[page_no - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False)
            image_path = candidate_dir / f"page-{page_no:04d}.png"
            image_path.write_bytes(pixmap.tobytes("png"))
            response = await provider.parse_page(image_path.read_bytes(), page.get_text("text").strip(), page_no)
            response_path = candidate_dir / f"page-{page_no:04d}.json"
            response_path.write_text(
                json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            summaries.append({
                "id": item["id"], "page_no": page_no,
                "markdown_chars": len(str(response.get("markdown") or "")),
                "block_count": len(response.get("blocks") or []),
                "response_sha256": _sha256(response_path),
            })
    duration = time.perf_counter() - started
    result = {
        "experiment_id": manifest["experiment_id"],
        "run_artifact_id": f"sha256:{_directory_hash(candidate_dir)}",
        "candidate": resolved.vision_model,
        "model_config": {
            "provider": "bailian", "vision_model": resolved.vision_model,
        },
        "model_calls": int(getattr(provider, "calls", 0)),
        "duration_seconds": round(duration, 3),
        "cost_estimate": 0.0,
        "pages": summaries,
    }
    (candidate_dir / "run-summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return result


def review_template(manifest: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """生成人工评分模板，不用默认分数伪装评测结论。"""
    return {
        key: run[key]
        for key in (
            "experiment_id", "run_artifact_id", "model_config", "model_calls",
            "duration_seconds", "cost_estimate",
        )
    } | {
        "pages": [
            {
                "id": item["id"], "scores": {dimension: None for dimension in item["dimensions"]},
                "critical_errors": [], "review_reason": "",
            }
            for item in manifest["pages"]
        ]
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for candidate in sorted(item for item in path.rglob("*") if item.is_file()):
        if candidate.name in {"run-summary.json", "review-template.json"}:
            continue
        digest.update(candidate.relative_to(path).as_posix().encode("utf-8"))
        digest.update(bytes.fromhex(_sha256(candidate)))
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calls-already-used", type=int, default=0)
    parser.add_argument("--timeout-seconds", type=float)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = asyncio.run(run_candidate(
        args.source, manifest, args.output_dir, Settings(),
        calls_already_used=args.calls_already_used, timeout_seconds=args.timeout_seconds,
    ))
    template = review_template(manifest, result)
    destination = args.output_dir / Settings().vision_model / "review-template.json"
    destination.write_text(json.dumps(template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
