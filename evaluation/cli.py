"""
模块职责：提供文档中心评测命令，并由独立生产 Worker 执行真实解析任务。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/unit/test_evaluation_cli.py、tests/integration/test_evaluation_api.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from axiom_flow.bootstrap import ApplicationContainer, build_container
from axiom_flow.domain.models import DomainError
from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.evaluation_workspace import EvaluationWorkspace

TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Axiom-Flow 文档中心解析评测")
    subparsers = parser.add_subparsers(dest="command", required=True)

    document = subparsers.add_parser("document", help="查询版本化与本地文档 case")
    document_subparsers = document.add_subparsers(dest="document_command", required=True)
    document_subparsers.add_parser("list", help="列出文档 case")

    run = subparsers.add_parser("run", help="提交生产 Job，等待独立 Worker 并捕获中性快照")
    _snapshot_arguments(run)
    run.add_argument("--source", type=Path)
    run.add_argument("--manifest", type=Path)
    run.add_argument("--wait-timeout", type=float, default=2700.0)

    capture = subparsers.add_parser("capture", help="捕获已经完成的生产 ParseRun")
    _snapshot_arguments(capture)
    capture.add_argument("--parse-run", required=True)

    assess = subparsers.add_parser("assess", help="创建单运行绝对质量评估")
    assess.add_argument("--document", required=True)
    assess.add_argument("--snapshot", required=True)
    assess.add_argument("--manifest", type=Path, required=True)

    compare = subparsers.add_parser("compare", help="比较同一文档的冻结基线与候选")
    compare.add_argument("--document", required=True)
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)

    report = subparsers.add_parser("report", help="根据 assessment 或 comparison 生成报告")
    report_target = report.add_mutually_exclusive_group(required=True)
    report_target.add_argument("--assessment")
    report_target.add_argument("--comparison")
    return parser


def _snapshot_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--document", required=True)
    parser.add_argument("--label", required=True)


def _revision() -> dict[str, Any]:
    commit = _git("rev-parse", "HEAD").strip().lower()
    branch = _git("branch", "--show-current").strip() or "detached"
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    dirty = bool(status.strip())
    diff_hash = None
    if dirty:
        digest = hashlib.sha256()
        digest.update(status.encode("utf-8"))
        digest.update(_git_bytes("diff", "--no-textconv", "HEAD", "--binary"))
        for line in status.splitlines():
            if not line.startswith("?? "):
                continue
            path = Path(line[3:])
            if path.is_file():
                digest.update(path.as_posix().encode("utf-8"))
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        diff_hash = digest.hexdigest()
    return {"commit": commit, "branch": branch, "dirty": dirty, "diff_hash": diff_hash}


def _git(*arguments: str) -> str:
    return _git_bytes(*arguments).decode("utf-8", errors="strict")


def _git_bytes(*arguments: str) -> bytes:
    result = subprocess.run(["git", *arguments], check=False, capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"无法读取 Git 修订：{message}")
    return result.stdout


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取评估 manifest：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("评估 manifest 必须是 JSON 对象")
    return value


def _with_container(settings: Settings) -> ApplicationContainer:
    container = build_container(settings)
    container.start()
    return container


def _run_contract(
    case: dict[str, Any], manifest: dict[str, Any] | None, settings: Settings,
) -> tuple[int, int]:
    default_range = case.get("default_page_range") or [1, case.get("page_count")]
    if not manifest:
        return int(default_range[0]), int(default_range[1])
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    artifact = str(source.get("artifact_id") or "")
    if artifact and artifact != f"sha256:{case['source_hash']}":
        raise ValueError("运行 manifest 来源哈希与文档 case 不一致")
    page_range = manifest.get("page_range")
    if not isinstance(page_range, dict):
        raise ValueError("运行 manifest 缺少 page_range")
    page_start, page_end = page_range.get("start"), page_range.get("end")
    if not isinstance(page_start, int) or not isinstance(page_end, int) or page_end < page_start:
        raise ValueError("运行 manifest 页范围非法")
    if manifest.get("model") and manifest["model"] != settings.vision_model:
        raise ValueError("运行 manifest 模型与当前配置不一致")
    request = manifest.get("request") if isinstance(manifest.get("request"), dict) else {}
    expected_request = {
        "max_tokens": settings.vision_max_tokens,
        "timeout_seconds": int(settings.model_timeout_seconds),
        "max_attempts_per_page": settings.vision_page_attempts,
    }
    for key, expected in expected_request.items():
        if key in request and request[key] != expected:
            raise ValueError(f"运行 manifest {key} 与当前配置不一致")
    budget = manifest.get("budget") if isinstance(manifest.get("budget"), dict) else {}
    max_calls = budget.get("max_model_calls")
    selected_count = page_end - page_start + 1
    effective_calls = min(settings.model_call_budget, selected_count * settings.vision_page_attempts)
    if not isinstance(max_calls, int) or max_calls != effective_calls:
        raise ValueError("运行 manifest 调用预算与当前任务策略不一致")
    return page_start, page_end


def _run_case(
    container: ApplicationContainer,
    case_id: str,
    source: Path | None,
    manifest: dict[str, Any] | None,
    wait_timeout: float,
) -> str:
    if wait_timeout <= 0:
        raise ValueError("等待超时必须大于 0")
    if source is not None:
        container.evaluations.materialize_document(case_id, source)
    case = container.evaluations.get_document(case_id)
    source_file = container.evaluations.source_file(case_id)
    page_start, page_end = _run_contract(case, manifest, container.settings)
    document = container.documents.import_pdf(source_file, f"{case['title']}.pdf")
    job, _ = container.jobs.submit_parse(document["id"], page_start, page_end)
    deadline = time.monotonic() + wait_timeout
    while True:
        current = container.jobs.get_job(job["id"])
        if current["status"] in TERMINAL_JOB_STATUSES:
            if current["status"] != "succeeded":
                error = current.get("error")
                if isinstance(error, dict):
                    error = error.get("message") or error.get("code")
                raise RuntimeError(f"评测解析任务未成功：{error or current['status']}")
            return str(current["result"]["run_id"])
        if time.monotonic() >= deadline:
            raise RuntimeError(f"等待独立 Worker 超时，Job 保持可恢复：{job['id']}")
        time.sleep(max(0.05, float(container.settings.worker_poll_seconds)))


def execute(args: argparse.Namespace) -> Any:
    settings = Settings()
    if args.command == "document":
        workspace = EvaluationWorkspace(
            settings.evaluation_data_dir, settings.evaluation_definitions_dir,
        )
        return workspace.list_cases()

    container = _with_container(settings)
    try:
        if args.command == "run":
            manifest = _load_json(args.manifest) if args.manifest else None
            run_id = _run_case(
                container, args.document, args.source, manifest, args.wait_timeout,
            )
            return container.evaluations.capture(
                run_id, args.label, _revision(), args.document,
            )
        if args.command == "capture":
            return container.evaluations.capture(
                args.parse_run, args.label, _revision(), args.document,
            )
        if args.command == "assess":
            return container.evaluations.assess(
                args.document, args.snapshot, _load_json(args.manifest),
            )
        if args.command == "compare":
            return container.evaluations.compare(
                args.document, args.baseline, args.candidate,
            )
        if args.command == "report":
            if args.assessment:
                return container.evaluations.report_assessment(args.assessment)
            return container.evaluations.report_comparison(args.comparison)
        raise ValueError("未知评测命令")
    finally:
        container.close()


def main() -> None:
    args = _parser().parse_args()
    try:
        result = execute(args)
    except (DomainError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
