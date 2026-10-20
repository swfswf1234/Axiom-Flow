"""scripts/axiom_flow_service.py 生命周期脚本契约测试。

脚本是 8902 API 服务的启停封装（契约见 docs/architecture/api.md ①生命周期与健康）。
测试用 tmp 目录与 monkeypatch 隔离 PID/日志路径与系统调用，不访问公网、
不启动真实进程、不读写真实数据根。
停止可靠性（2026-09-04，与根仓库脚本同构）：stop 路径判活用 _proc_alive
（ctypes），_kill_tree 返回 bool 并可见失败，优雅+强杀后仍存活必须退 1
（绝不假 stopped）。
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
    """把 PID/日志路径与 ROOT（.env 查找基准）全部隔离到 tmp 目录。"""
    monkeypatch.setattr(module, "LOG_DIR", tmp_path)
    monkeypatch.setattr(module, "PID_FILE", tmp_path / "qed-axiom.pid")
    monkeypatch.setattr(module, "SERVE_LOG", tmp_path / "serve.log")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    return tmp_path


class FakeRunResult:
    """subprocess.run 替身：仅暴露 stdout（V2-012 编码修复回归）。"""

    def __init__(self, stdout: str | None) -> None:
        self.stdout = stdout


def test_pid_is_alive_stdout_none_returns_false(module, monkeypatch):
    """解码失败（readerthread 中断 → stdout=None）时兜底返回 False，不抛 TypeError。"""
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: FakeRunResult(None))
    assert module._pid_is_alive(4242) is False


def test_pid_is_alive_passes_errors_replace(module, monkeypatch):
    """中文 Windows tasklist 输出为 GBK，subprocess 必须 errors=replace 容忍。"""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return FakeRunResult("")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module._pid_is_alive(4242)
    assert captured["errors"] == "replace"


def test_pid_is_alive_ascii_stdout_matches(module, monkeypatch):
    """正常 ASCII tasklist 输出仍能正确判定进程存活。"""
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: FakeRunResult("python.exe          4242\n"),
    )
    assert module._pid_is_alive(4242) is True


def test_pid_is_alive_stdout_without_pid_returns_false(module, monkeypatch):
    """stdout 不含目标 PID 时返回 False（既有语义不回退）。"""
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *a, **kw: FakeRunResult("python.exe          9999\n"),
    )
    assert module._pid_is_alive(4242) is False


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


def test_parser_has_no_mode_option(module):
    """配置简化（PLAN-002）：--mode 退役，start/restart 不接受该参数。"""
    for cmd in ("start", "restart"):
        with pytest.raises(SystemExit):
            module.build_parser().parse_args([cmd, "--mode", "qed-engine"])


def test_start_spawns_uvicorn_without_mode_env(module, isolated, monkeypatch):
    calls: dict = {}

    def fake_popen(cmd, **kwargs):
        calls["kwargs"] = kwargs
        return FakeProc(pid=4242)

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: False)
    args = module.build_parser().parse_args(["start"])
    assert module.cmd_start(args) == 0
    assert "env" not in calls["kwargs"], "配置简化后不再注入模式/密钥环境（config.py 自读 .env）"


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


def test_load_env_injects_axiom_qed_without_secrets(module, monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "AXIOM_API_KEY=secret-key\n"
        "QWEN_API_KEY=retired-key\n"
        "AXIOM_VISION_MODEL=qwen-vl-ocr\n"
        "API_KEY=sk-test\n"
        "QED_OCR_MODEL_URL=http://127.0.0.1:5002\n"
        "AXIOM_PORT=8902\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AXIOM_API_KEY", "existing")
    monkeypatch.delenv("API_KEY", raising=False)
    module._load_env(tmp_path)
    # 已有环境变量不覆盖
    assert module.os.environ["AXIOM_API_KEY"] == "existing"
    # 缺失变量注入（AXIOM_* / QED_*；密钥 API_KEY 不注入——配置简化后由 config.py 自读）
    assert module.os.environ["QED_OCR_MODEL_URL"] == "http://127.0.0.1:5002"
    assert module.os.environ["AXIOM_PORT"] == "8902"
    assert module.os.environ["AXIOM_VISION_MODEL"] == "qwen-vl-ocr"
    assert "API_KEY" not in module.os.environ
    assert "QWEN_API_KEY" not in module.os.environ


def test_load_env_no_env_file_is_noop(module, monkeypatch, tmp_path):
    monkeypatch.delenv("QED_OCR_MODEL_URL", raising=False)
    module._load_env(tmp_path)
    assert "QED_OCR_MODEL_URL" not in module.os.environ  # 不抛异常即通过


def test_stop_no_pid_file(module, isolated, monkeypatch):
    """真未运行形（无 PID 文件且端口不通）仍幂等退出 0；端口探测必须打桩，不触碰真实 8902。"""
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0


def test_stop_stale_pid_file_cleaned(module, isolated, monkeypatch):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_proc_alive", lambda pid: False)
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

    monkeypatch.setattr(module, "_proc_alive", fake_alive)
    break_sent = []
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: break_sent.append(pid))
    tree_calls = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: tree_calls.append(pid) or True)
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert break_sent == [4242]
    assert tree_calls == []
    assert not (isolated / "qed-axiom.pid").exists()


def test_stop_force_kill_fallback(module, isolated, monkeypatch, capsys):
    """优雅信号未生效：宽限后 taskkill 强杀生效 → stopped (forced) + PID 文件清理。"""
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)  # 信号发出但被忽略
    alive = {"flag": True}
    monkeypatch.setattr(module, "_proc_alive", lambda pid: alive["flag"])
    tree_calls = []

    def fake_kill_tree(pid):
        tree_calls.append(pid)
        alive["flag"] = False
        return True

    monkeypatch.setattr(module, "_kill_tree", fake_kill_tree)
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert tree_calls == [4242]
    assert "stopped (forced)" in capsys.readouterr().out
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

    monkeypatch.setattr(module, "_proc_alive", fake_alive)
    tree_calls = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: tree_calls.append(pid) or True)
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


def test_status_running_by_pid(module, isolated, monkeypatch, capsys):
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_pid_is_alive", lambda pid: True)
    args = module.build_parser().parse_args(["status"])
    assert module.cmd_status(args) == 0
    assert "running (pid 4242)" in capsys.readouterr().out


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


# --- 停止可靠性（2026-09-04 假 stopped 根因修复，与根仓库脚本同构） ---


def test_proc_alive_real_process_liveness(module):
    """_proc_alive 真实判定：当前进程 True；已退出子进程 False；pid<=0 False。"""
    import os
    import subprocess

    assert module._proc_alive(os.getpid()) is True, "当前进程应判定存活"
    victim = subprocess.Popen([sys.executable, "-c", "pass"])
    victim.wait()
    assert module._proc_alive(victim.pid) is False, "已退出进程应判定死亡"
    assert module._proc_alive(0) is False and module._proc_alive(-1) is False


def test_kill_tree_visible_failure_and_success(module, monkeypatch):
    """_kill_tree 返回 bool：成功 True；taskkill 失败/异常打印诊断返回 False（不静默吞错）。"""
    captured: dict = {}

    def fake_run_ok(cmd, **kwargs):
        captured.update(cmd=cmd, **kwargs)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(module.subprocess, "run", fake_run_ok)
    assert module._kill_tree(123) is True
    assert captured["cmd"] == ["taskkill", "/PID", "123", "/T", "/F"], "必须 /T /F 强杀整树"

    monkeypatch.setattr(
        module.subprocess, "run",
        lambda cmd, **kw: type("R", (), {"returncode": 128, "stdout": "错误: 拒绝访问。", "stderr": ""})(),
    )
    assert module._kill_tree(123) is False

    def fake_run_boom(cmd, **kwargs):
        raise module.subprocess.SubprocessError("taskkill vanished")

    monkeypatch.setattr(module.subprocess, "run", fake_run_boom)
    assert module._kill_tree(123) is False, "taskkill 异常不得抛出、必须返回 False"


def test_cmd_stop_never_fakes_success(module, isolated, monkeypatch, capsys):
    """缺陷回归：优雅+强杀两腿都失效时，stop 必须退出码 1 报错，绝不打印假 stopped。"""
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "_proc_alive", lambda pid: True)  # 模拟目标始终存活
    monkeypatch.setattr(module, "_kill_tree", lambda pid: False)  # 模拟强杀失效
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 1
    out = capsys.readouterr().out
    assert "stop failed" in out, "两腿失效必须显式报错"
    assert "stopped" not in out.replace("stop failed", ""), "绝不打印假 stopped"
    assert (isolated / "qed-axiom.pid").exists(), "失败时不清理 PID 文件（保留现场供手动恢复）"


def test_cmd_stop_forced_when_graceful_signal_ignored(module, isolated, monkeypatch, capsys):
    """CTRL_BREAK 空放场景：宽限后 taskkill 强杀生效 → stopped (forced) + 清理 PID 文件。"""
    (isolated / "qed-axiom.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)  # 信号发出但被忽略
    alive = {"flag": True}

    def fake_kill_tree(pid):
        alive["flag"] = False
        return True

    monkeypatch.setattr(module, "_proc_alive", lambda pid: alive["flag"])
    monkeypatch.setattr(module, "_kill_tree", fake_kill_tree)
    args = module.build_parser().parse_args(["stop"])
    assert module.cmd_stop(args) == 0
    assert "stopped (forced)" in capsys.readouterr().out
    assert not (isolated / "qed-axiom.pid").exists()


# --- 端口兜底真停（REQ-126 / PLAN-023 W1~W4，2026-10-20） ---


def test_parse_netstat_pid_reads_listen_line(module):
    """端口→监听 PID 反查解析：只认 TCP LISTENING 且本地地址以 `:端口` 结尾，
    89020 前缀行与 UDP 行不算命中。"""
    text = (
        "  协议  本地地址          外部地址        状态           PID\n"
        "  TCP    127.0.0.1:8902           0.0.0.0:0              LISTENING       4321\n"
        "  TCP    127.0.0.1:89020          0.0.0.0:0              LISTENING       9999\n"
        "  UDP    0.0.0.0:8902             *:*                                    7777\n"
    )
    assert module._parse_netstat_pid(text, 8902) == 4321
    assert module._parse_netstat_pid(text, 8999) is None


def test_listening_pid_uses_byte_capture_and_degrades_on_error(module, monkeypatch):
    """netstat 表头在本机走 GBK 管道：必须字节捕获后自行容错解码；命令异常降级为
    「未取得 PID」而不是抛出（调用方据此走 3 形）。"""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(cmd=cmd, **kwargs)
        return type(
            "R", (), {"stdout": b"  TCP    127.0.0.1:8902   0.0.0.0:0   LISTENING  4321\n"}
        )()

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    assert module._listening_pid(8902) == 4321
    assert captured["cmd"] == ["netstat", "-ano"], "只读反查，不带任何终止参数"
    assert "text" not in captured, "text=True 会让 GBK 表头解码失败并吞掉整段输出"

    def boom(*args, **kwargs):
        raise module.subprocess.SubprocessError("netstat 不可用")

    monkeypatch.setattr(module.subprocess, "run", boom)
    assert module._listening_pid(8902) is None


def test_stop_no_pid_file_but_port_listening_stops_listener_by_port(
    module, isolated, monkeypatch, capsys
):
    """W1/W2 主形：无 PID 文件而端口在听＝外部实例，按反查到的监听 PID 走既有
    「优雅停止 → 强杀兜底 → 存活复核」链真停，输出与托管形可区分。"""
    monkeypatch.setattr(module, "_port_open", lambda port: True)
    monkeypatch.setattr(module, "_listening_pid", lambda port: 4321)
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)  # 跨 console：信号空放
    alive = {"flag": True}

    def fake_kill_tree(pid):
        alive["flag"] = False
        return True

    monkeypatch.setattr(module, "_proc_alive", lambda pid: alive["flag"])
    monkeypatch.setattr(module, "_kill_tree", fake_kill_tree)
    args = module.build_parser().parse_args(["--port", "8902", "stop"])
    assert module.cmd_stop(args) == 0
    out = capsys.readouterr().out
    assert "stopped (unmanaged listener pid 4321" in out
    assert "not running" not in out, "外部实例形不得再说成未运行"
    assert not (isolated / "qed-axiom.pid").exists()


def test_stop_no_pid_file_and_port_free_is_idempotent_zero(module, isolated, monkeypatch, capsys):
    """三形分界①：真未运行（端口不通）仍 0，与「端口在听」两形互不混淆、零终止调用。"""
    monkeypatch.setattr(module, "_port_open", lambda port: False)
    monkeypatch.setattr(module, "_listening_pid", lambda port: 4321)
    touched: list = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: touched.append(("taskkill", pid)) or True)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: touched.append(("kill", pid)))
    args = module.build_parser().parse_args(["--port", "8902", "stop"])
    assert module.cmd_stop(args) == 0
    out = capsys.readouterr().out
    assert "not running (no pid file)" in out
    assert "4321" not in out
    assert touched == []


def test_stop_returns_unmanaged_exit_code_when_listener_pid_unresolved(
    module, isolated, monkeypatch, capsys
):
    """三形分界②：端口在听但反查不到监听 PID＝无从处置，退出码 3 且不终止任何进程
    （8900 以 0/3 一并交端口复核，据 stdout 给原因）。"""
    monkeypatch.setattr(module, "_port_open", lambda port: True)
    monkeypatch.setattr(module, "_listening_pid", lambda port: None)
    touched: list = []
    monkeypatch.setattr(module, "_kill_tree", lambda pid: touched.append(pid) or True)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: touched.append(pid))
    args = module.build_parser().parse_args(["--port", "8902", "stop"])
    assert module.UNMANAGED_LISTENER_EXIT == 3
    assert module.cmd_stop(args) == module.UNMANAGED_LISTENER_EXIT
    out = capsys.readouterr().out
    assert "8902" in out and "未终止" in out
    assert touched == []
    assert "not running" not in out


def test_stop_unmanaged_listener_still_alive_returns_1(module, isolated, monkeypatch, capsys):
    """三形分界③：外部实例反查到 PID 但优雅+强杀两腿都失效 → 1（沿用不假 stopped 的口径）。"""
    monkeypatch.setattr(module, "_port_open", lambda port: True)
    monkeypatch.setattr(module, "_listening_pid", lambda port: 4321)
    monkeypatch.setattr(module, "_proc_alive", lambda pid: True)
    monkeypatch.setattr(module, "_kill_tree", lambda pid: False)
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    monkeypatch.setattr(module.os, "kill", lambda pid, sig: None)
    args = module.build_parser().parse_args(["--port", "8902", "stop"])
    assert module.cmd_stop(args) == 1
    out = capsys.readouterr().out
    assert "stop failed" in out and "4321" in out
    assert "stopped" not in out.replace("stop failed", "")


def test_restart_aborts_when_stop_unconfirmed(module, monkeypatch, capsys):
    """W4：cmd_stop 非 0 即中止并原样返回该码，不拉起第二实例（停止失败仍重启＝双实例）。"""
    monkeypatch.setattr(module, "cmd_stop", lambda args: module.UNMANAGED_LISTENER_EXIT)
    started: list = []
    monkeypatch.setattr(module, "cmd_start", lambda args: started.append(args) or 0)
    args = module.build_parser().parse_args(["restart"])
    assert module.cmd_restart(args) == 3
    assert started == [], "stop 未确认成功时不得启动新实例"
    assert "restart aborted" in capsys.readouterr().out
