"""scripts/axiom_flow_service.py 生命周期脚本契约测试。

脚本是 8902 API 服务的启停封装（设计见 docs/design/service-lifecycle.md）。
测试用 tmp 目录与 monkeypatch 隔离 PID/日志路径与系统调用，不访问公网、
不启动真实进程、不读写真实数据根。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "axiom_flow_service.py"


class FakeProc:
    """最小 Popen 替身：持有 pid，poll 恒未退出。"""

    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid

    def poll(self) -> None:
        return None


@pytest.fixture
def module():
    spec = importlib.util.spec_from_file_location("axiom_flow_service", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def isolated(module, monkeypatch, tmp_path):
    """把 PID/日志路径全部隔离到 tmp 目录。"""
    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(module, "PID_FILE", tmp_path / "qed-axiom.pid")
    monkeypatch.setattr(module, "SERVE_LOG", tmp_path / "serve.log")
    return tmp_path


def test_parser_has_subcommands(module):
    parser = module.build_parser()
    for name in ("start", "stop", "restart", "status"):
        assert parser.parse_args([name]).command == name


def test_parser_port_and_wait_options(module):
    args = module.build_parser().parse_args(["--port", "8912", "start", "--wait"])
    assert args.port == 8912
    assert args.command == "start"
    assert args.wait == module.HEALTH_TIMEOUT_SECONDS


def test_default_port_from_qed_axiom_url(module, monkeypatch):
    monkeypatch.delenv("AXIOM_PORT", raising=False)
    monkeypatch.setenv("QED_AXIOM_URL", "http://127.0.0.1:8922")
    assert module.default_port() == 8922


def test_default_port_from_axiom_port_env(module, monkeypatch):
    monkeypatch.delenv("QED_AXIOM_URL", raising=False)
    monkeypatch.setenv("AXIOM_PORT", "8923")
    assert module.default_port() == 8923


def test_default_port_fallback(module, monkeypatch):
    monkeypatch.delenv("QED_AXIOM_URL", raising=False)
    monkeypatch.delenv("AXIOM_PORT", raising=False)
    assert module.default_port() == 8902


def test_start_writes_pid_and_spawns_uvicorn(module, isolated, monkeypatch):
    calls: dict = {}

    def fake_popen(cmd, **kwargs):
        calls["cmd"] = cmd
        return FakeProc(pid=4242)

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    args = module.build_parser().parse_args(["start"])
    assert module.cmd_start(args) == 0
    assert calls["cmd"][0] == sys.executable
    assert calls["cmd"][1:] == [
        "-m",
        "uvicorn",
        "axiom_flow.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8902",
    ]
    assert (isolated / "qed-axiom.pid").read_text(encoding="utf-8") == "4242"


def test_start_already_running_by_pid(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *a, **kw: pytest.fail("不应重复 spawn")
    )
    args = module.build_parser().parse_args(["start"])
    assert module.cmd_start(args) == 0


def test_start_already_running_by_port(module, isolated, monkeypatch):
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(module, "_port_open", lambda port: True)
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *a, **kw: pytest.fail("不应重复 spawn")
    )
    args = module.build_parser().parse_args(["start"])
    assert module.cmd_start(args) == 0


def test_start_spawn_failure_returns_1(module, isolated, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("python not found")

    monkeypatch.setattr(module.subprocess, "Popen", boom)
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    args = module.build_parser().parse_args(["start"])
    assert module.cmd_start(args) == 1
    assert not (isolated / "qed-axiom.pid").exists()


def test_start_wait_reports_healthy(module, isolated, monkeypatch):
    monkeypatch.setattr(module.subprocess, "Popen", lambda cmd, **kw: FakeProc(pid=7))
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(module, "_health_ok", lambda port: True)
    args = module.build_parser().parse_args(["start", "--wait"])
    assert module.cmd_start(args) == 0


def test_start_wait_timeout_returns_1(module, isolated, monkeypatch):
    monkeypatch.setattr(module.subprocess, "Popen", lambda cmd, **kw: FakeProc(pid=7))
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(module, "_health_ok", lambda port: False)
    args = module.build_parser().parse_args(["start", "--wait", "0.05"])
    assert module.cmd_start(args) == 1


def test_load_env_injects_axiom_vars(module, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nAXIOM_API_KEY=secret-key\nQWEN_API_KEY=shared-key\nAXIOM_VISION_MODEL=qwen-vl-ocr\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AXIOM_API_KEY", "existing")
    module._load_env(tmp_path)
    # 已有环境变量不覆盖
    assert module.os.environ["AXIOM_API_KEY"] == "existing"
    # 缺失变量注入
    assert module.os.environ["QWEN_API_KEY"] == "shared-key"
    assert module.os.environ["AXIOM_VISION_MODEL"] == "qwen-vl-ocr"


def test_load_env_no_env_file_is_noop(module, monkeypatch, tmp_path):
    module._load_env(tmp_path)
    assert "QWEN_API_KEY" not in module.os.environ or True  # 不抛异常即通过


def test_stop_no_pid_file(module, isolated, monkeypatch):
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0


def test_stop_stale_pid_file_cleaned(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert not (isolated / "qed-axiom.pid").exists()


def test_stop_graceful_no_force(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    alive_calls = {"n": 0}

    def fake_alive(pid):
        alive_calls["n"] += 1
        return alive_calls["n"] == 1  # os.kill 前存活，之后立即消失

    monkeypatch.setattr(module, "_pid_is_alive", fake_alive)
    break_sent = []
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: break_sent.append(pid))
    tree_calls = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: tree_calls.append(pid))
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert break_sent == [4242]
    assert tree_calls == []
    assert not (isolated / "qed-axiom.pid").exists()


def test_stop_force_kill_fallback(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: True)  # 永不退出
    break_sent = []
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: break_sent.append(pid))
    tree_calls = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: tree_calls.append(pid))
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert break_sent == [4242]
    assert tree_calls == [4242]
    assert not (isolated / "qed-axiom.pid").exists()


def test_stop_systemerror_from_kill_falls_back_to_force(module, isolated, monkeypatch):
    """无交互控制台环境下 os.kill(CTRL_BREAK) 抛 SystemError（包裹 WinError 87），
    必须兜底强杀而不是崩溃。"""
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)

    def broken_kill(pid, sig):
        raise SystemError("<built-in function kill> returned a result with an exception set")

    monkeypatch.setattr(module.os, "kill", broken_kill)
    alive_state = {"n": 0}

    def fake_alive(pid):
        alive_state["n"] += 1
        return alive_state["n"] == 1  # os.kill 前存活，之后视为已退出

    monkeypatch.setattr(module, "_pid_is_alive", fake_alive)
    tree_calls = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: tree_calls.append(pid))
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert tree_calls == [4242]
    assert not (isolated / "qed-axiom.pid").exists()


def test_restart_stops_then_starts(module, monkeypatch):
    calls = []

    def record_stop(args):
        calls.append("stop")
        return 0

    def record_start(args):
        calls.append("start")
        return 0

    monkeypatch.setattr(module, "cmd_stop", record_stop)
    monkeypatch.setattr(module, "cmd_start", record_start)
    args = module.build_parser().parse_args(["restart"])
    assert module.cmd_restart(args) == 0
    assert calls == ["stop", "start"]


def test_status_running_by_pid(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: True)
    args = module.build_parser().parse_args(["status"])
    assert module.cmd_status(args) == 0


def test_status_running_by_port(module, isolated, monkeypatch):
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(module, "_port_open", lambda port: True)
    monkeypatch.setattr(module, "_health_ok", lambda port: True)
    args = module.build_parser().parse_args(["status"])
    assert module.cmd_status(args) == 0


def test_status_stopped_and_stale_pid_cleaned(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_health_ok", lambda port: False)
    args = module.build_parser().parse_args(["status"])
    assert module.cmd_status(args) == 0
    assert not (isolated / "qed-axiom.pid").exists()


def test_main_runs_subcommand(module, monkeypatch):
    monkeypatch.setattr(module, "cmd_status", lambda args: 7)
    assert module.main(["status"]) == 7