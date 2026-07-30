"""
模块职责：验证真实 API 与 Worker 命令入口可在隔离环境独立启动。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
被测代码：src/axiom_flow/main.py、src/axiom_flow/worker/__main__.py、src/axiom_flow/bootstrap.py
"""

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from axiom_flow.infrastructure.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _stop(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    return process.stdout.read() if process.stdout else ""


def _wait_for_health(url: str, process: subprocess.Popen[str]) -> dict:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.load(response)
        except Exception as exc:  # 进程启动期间连接拒绝属于预期重试条件。
            last_error = exc
            time.sleep(0.1)
    raise AssertionError(f"API 未在期限内健康：{last_error}")


def test_real_api_and_worker_processes_start_without_model_calls(
    tmp_path: Path,
    smoke_settings: Settings,
):
    port = _free_port()
    environment = os.environ.copy()
    environment.update({
        "AXIOM_MYSQL_DATABASE": smoke_settings.mysql_database,
        "AXIOM_DATA_DIR": str(tmp_path / "data"),
        "AXIOM_WORKER_POLL_SECONDS": "0.1",
        "AXIOM_API_KEY": "",
        "API_KEY": "",
    })
    api = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "axiom_flow.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    worker = subprocess.Popen(
        [sys.executable, "-m", "axiom_flow.worker"],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )
    api_log = ""
    worker_log = ""
    try:
        health = _wait_for_health(f"http://127.0.0.1:{port}/api/v1/health", api)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            home = response.read().decode("utf-8")
        time.sleep(0.3)
        assert health == {"status": "ok", "version": "0.3.0"}
        assert "Axiom-Flow" in home
        assert worker.poll() is None
    finally:
        api_log = _stop(api)
        worker_log = _stop(worker)

    assert api.returncode in {0, -15, 1}, api_log
    assert worker.returncode in {0, -15, 1}, worker_log
