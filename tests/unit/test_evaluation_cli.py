"""
模块职责：验证评测 CLI 的修订指纹、独立 Worker 轮询、资源关闭和稳定退出码。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：evaluation/cli.py、evaluation/__main__.py
"""

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import __main__ as entrypoint
from evaluation import cli


def test_revision_records_clean_and_dirty_worktrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    commit = "a" * 40

    def clean_git(*arguments: str) -> str:
        return {
            ("rev-parse", "HEAD"): commit,
            ("branch", "--show-current"): "main",
            ("status", "--porcelain=v1", "--untracked-files=all"): "",
        }[arguments]

    monkeypatch.setattr(cli, "_git", clean_git)
    assert cli._revision() == {"commit": commit, "branch": "main", "dirty": False, "diff_hash": None}

    monkeypatch.chdir(tmp_path)
    untracked = tmp_path / "candidate.txt"
    untracked.write_bytes(b"candidate")
    status = " M tracked.py\n?? candidate.txt\n"

    def dirty_git(*arguments: str) -> str:
        if arguments == ("status", "--porcelain=v1", "--untracked-files=all"):
            return status
        return clean_git(*arguments)

    monkeypatch.setattr(cli, "_git", dirty_git)
    diff_arguments = []

    def git_bytes(*arguments: str) -> bytes:
        diff_arguments.append(arguments)
        return b"tracked-diff"

    monkeypatch.setattr(cli, "_git_bytes", git_bytes)
    digest = hashlib.sha256()
    digest.update(status.encode("utf-8"))
    digest.update(b"tracked-diff")
    digest.update(b"candidate.txt")
    digest.update(hashlib.sha256(b"candidate").digest())
    assert cli._revision() == {
        "commit": commit, "branch": "main", "dirty": True, "diff_hash": digest.hexdigest(),
    }
    assert diff_arguments == [("diff", "--no-textconv", "HEAD", "--binary")]


class FakeJobs:
    def __init__(self, statuses: list[dict]):
        self.statuses = iter(statuses)

    def submit_parse(self, document_id: str, start_page: int, end_page: int):
        assert (document_id, start_page, end_page) == ("document-1", 2, 4)
        return {"id": "job-12345678"}, True

    def get_job(self, _job_id: str):
        return next(self.statuses)


def _run_container(statuses: list[dict]):
    evaluations = SimpleNamespace(
        get_document=lambda _case_id: {
            "title": "样本", "source_hash": "a" * 64, "default_page_range": [2, 4],
        },
        source_file=lambda _case_id: Path("source.pdf"),
        materialize_document=lambda _case_id, _source: None,
    )
    documents = SimpleNamespace(
        import_pdf=lambda source, filename: {
            "id": "document-1", "page_count": 8, "source": source, "filename": filename,
        },
    )
    settings = SimpleNamespace(
        worker_poll_seconds=0,
        vision_model="qwen-vl-ocr",
        vision_max_tokens=8192,
        model_timeout_seconds=180,
        vision_page_attempts=3,
        model_call_budget=60,
    )
    return SimpleNamespace(
        evaluations=evaluations, documents=documents, jobs=FakeJobs(statuses), settings=settings,
    )


def test_run_case_waits_for_independent_worker_and_returns_parse_run(monkeypatch: pytest.MonkeyPatch):
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", lambda duration: sleeps.append(duration))
    container = _run_container([
        {"status": "queued"},
        {"status": "running"},
        {"status": "succeeded", "result": {"run_id": "run-1"}},
    ])
    assert cli._run_case(container, "case-1", None, None, 60) == "run-1"
    assert len(sleeps) == 2
    assert not hasattr(cli, "Worker")


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        ("failed", {"message": "provider rejected page"}, "provider rejected page"),
        ("cancelled", None, "cancelled"),
    ],
)
def test_run_case_reports_unsuccessful_terminal_jobs(status, error, message):
    container = _run_container([{"status": status, "error": error}])
    with pytest.raises(RuntimeError, match=message):
        cli._run_case(container, "case-1", None, None, 60)


def test_run_case_timeout_keeps_recoverable_job_id(monkeypatch: pytest.MonkeyPatch):
    container = _run_container([{"status": "queued"}])
    times = iter([0.0, 2.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(times))
    with pytest.raises(RuntimeError, match="Job 保持可恢复：job-12345678"):
        cli._run_case(container, "case-1", None, None, 1)


def test_run_contract_rejects_model_and_budget_drift():
    container = _run_container([])
    case = container.evaluations.get_document("case-1")
    manifest = {
        "source": {"artifact_id": f"sha256:{case['source_hash']}"},
        "page_range": {"start": 2, "end": 4},
        "model": "other-model",
        "request": {"max_tokens": 8192, "timeout_seconds": 180, "max_attempts_per_page": 3},
        "budget": {"max_model_calls": 9},
    }
    with pytest.raises(ValueError, match="模型"):
        cli._run_contract(case, manifest, container.settings)
    manifest["model"] = "qwen-vl-ocr"
    manifest["budget"]["max_model_calls"] = 8
    with pytest.raises(ValueError, match="预算"):
        cli._run_contract(case, manifest, container.settings)


@pytest.mark.parametrize("should_fail", [False, True])
def test_execute_always_closes_the_application_container(should_fail: bool, monkeypatch: pytest.MonkeyPatch):
    closed = []

    def compare(*_args):
        if should_fail:
            raise ValueError("invalid comparison")
        return {"comparison_id": "comparison-1"}

    container = SimpleNamespace(
        evaluations=SimpleNamespace(compare=compare),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(cli, "Settings", lambda: SimpleNamespace())
    monkeypatch.setattr(cli, "_with_container", lambda _settings: container)
    args = argparse.Namespace(
        command="compare", document="case-1", baseline="base-1", candidate="candidate-1",
    )
    if should_fail:
        with pytest.raises(ValueError, match="invalid comparison"):
            cli.execute(args)
    else:
        assert cli.execute(args) == {"comparison_id": "comparison-1"}
    assert closed == [True]


def test_main_prints_json_and_maps_known_errors_to_exit_code_two(monkeypatch, capsys):
    assert entrypoint.main is cli.main
    parser = SimpleNamespace(parse_args=lambda: argparse.Namespace(command="document"))
    monkeypatch.setattr(cli, "_parser", lambda: parser)
    monkeypatch.setattr(cli, "execute", lambda _args: {"title": "数学分析"})
    cli.main()
    assert json.loads(capsys.readouterr().out) == {"title": "数学分析"}

    monkeypatch.setattr(cli, "execute", lambda _args: (_ for _ in ()).throw(RuntimeError("评测失败")))
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 2
    assert capsys.readouterr().err.strip() == "评测失败"
