"""Axiom-Flow 8902 API 服务生命周期管理：start / stop / restart / status。

子进程 = `sys.executable -m uvicorn axiom_flow.api.main:app`（继承当前解释器，天然落在
QED_env）；PID 文件 logs/qed-axiom.pid，子进程 stdout/stderr 落 logs/qed-axiom-serve.log。
环境：从仓库根查找 `.env`，setdefault 注入 `AXIOM_*` / `QED_*` 与 `API_KEY`（已有环境
不覆盖，逐厂商 key 别名已退役），standalone 启动不缺凭据。
模型模式：`--mode local|qed-engine`（start/restart 可传），持久化到 logs/qed-axiom-mode；
不传时默认读自身 .env 的 QED_API_SELECT（缺省 local）；子进程 env 注入 QED_API_SELECT。
接口契约（含 8900 控制中心接入方式）见 docs/architecture/api.md ①生命周期与健康分类；
模型模式设计见 docs/design/model-mode-config.md。

退出码：0 成功/幂等；1 运行失败（spawn 失败、health 超时）；2 参数错误（argparse）。
Windows 注意：os.kill(pid, 0) 会直接 TerminateProcess，进程存在性检测用 tasklist。
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
PID_FILE = LOG_DIR / "qed-axiom.pid"
MODE_FILE = LOG_DIR / "qed-axiom-mode"
SERVE_LOG = LOG_DIR / "qed-axiom-serve.log"

DEFAULT_PORT = 8902

NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CTRL_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)

STOP_GRACE_SECONDS = 5.0
STOP_POLL_INTERVAL = 0.2
HEALTH_TIMEOUT_SECONDS = 30.0
HEALTH_INTERVAL_SECONDS = 0.5


def default_port() -> int:
    """health 探测端口：--port > QED_AXIOM_URL > AXIOM_PORT > 8902。"""
    for env_name in ("QED_AXIOM_URL", "AXIOM_PORT"):
        raw = os.environ.get(env_name)
        if not raw:
            continue
        try:
            if env_name == "QED_AXIOM_URL":
                return int(raw.rstrip("/").rsplit(":", 1)[1])
            return int(raw)
        except (ValueError, IndexError):
            continue
    return DEFAULT_PORT


def serve_command(port: int) -> list[str]:
    """子进程命令：当前解释器 + uvicorn + 8902 API 应用。"""
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "axiom_flow.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def _load_env(start: Path) -> None:
    """从 start 向上查找 `.env`，注入 `AXIOM_*` / `QED_*` 与 `API_KEY`（已有环境变量不覆盖）。

    独立启动 `axiom_flow_service.py` 时补上仓库统一配置，保持与调用方注入环境一致。
    逐厂商 key 别名（QWEN_API_KEY / DEEPSEEK_API_KEY / GLM_API_KEY）按根仓库裁决退役。
    """
    env_path = next(
        (candidate / ".env" for candidate in [start, *start.parents] if (candidate / ".env").is_file()), None
    )
    if env_path is None:
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith(("AXIOM_", "QED_")) or key == "API_KEY":
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def _pid_is_alive(pid: int) -> bool:
    """Windows 进程存在性检测：tasklist（os.kill(pid, 0) 会直接 TerminateProcess）。

    中文 Windows 下 tasklist 表头为 GBK 编码，text=True 默认 utf-8 解码失败会使
    readerthread 中断导致 stdout=None；errors="replace" 容忍乱码（PID 数字为 ASCII
    不受影响），stdout 为空时兜底返回 False（V2-012）。
    """
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return str(pid) in (result.stdout or "")


def read_pid() -> int | None:
    if not PID_FILE.is_file():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _health_ok(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1.0) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def default_mode() -> str:
    """模式默认：自身 .env 的 QED_API_SELECT（缺省 local）。"""
    env_path = ROOT / ".env"
    if env_path.is_file():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "QED_API_SELECT":
                value = value.strip().strip('"').strip("'")
                if value:
                    return value
    return "local"


def read_mode() -> str:
    """当前运行模式：状态文件优先，缺省读自身 .env。"""
    if MODE_FILE.is_file():
        try:
            value = MODE_FILE.read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            pass
    return default_mode()


def write_mode(mode: str) -> None:
    MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    MODE_FILE.write_text(mode, encoding="utf-8")


def _spawn(port: int, mode: str) -> int:
    """拉起服务进程并写 PID 文件；返回退出码。env 注入 QED_API_SELECT（模式生效）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(SERVE_LOG, "ab")  # noqa: SIM115 - 子进程继承句柄，随其生命周期
    try:
        proc = subprocess.Popen(
            serve_command(port),
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=NEW_PROCESS_GROUP,
            env={**os.environ.copy(), "QED_API_SELECT": mode},
        )
    except Exception as exc:
        log_file.close()
        print(f"spawn failed: {exc}")
        return 1
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    print(f"pid: {proc.pid}")
    print(f"log: {SERVE_LOG}")
    return 0


def _wait_healthy(port: int, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _health_ok(port):
            print(f"healthy: http://127.0.0.1:{port}/api/v1/health")
            return 0
        time.sleep(HEALTH_INTERVAL_SECONDS)
    print(f"health not OK within {timeout:g}s")
    return 1


def cmd_start(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else default_port()
    pid = read_pid()
    if pid is not None and _pid_is_alive(pid):
        print(f"already running (pid {pid})")
        return 0
    if _port_open(port):
        print(f"already running (port {port})")
        return 0
    mode = getattr(args, "mode", None) or default_mode()
    write_mode(mode)
    if _spawn(port, mode) != 0:
        return 1
    if args.wait and args.wait > 0:
        return _wait_healthy(port, args.wait)
    return 0


def _kill_tree(pid: int) -> None:
    """taskkill 强杀进程树（优雅停止超时后的兜底）。"""
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def cmd_stop(args: argparse.Namespace) -> int:
    pid = read_pid()
    if pid is None:
        print("not running (no pid file)")
        return 0
    if not _pid_is_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        print("not running (stale pid file)")
        return 0
    forced = False
    try:
        os.kill(pid, CTRL_BREAK_EVENT)
    except (OSError, SystemError):
        # 无交互控制台环境（服务/管道）下 GenerateConsoleCtrlEvent 抛 WinError 87，
        # CPython 包装为 SystemError；一律走 taskkill 强杀兜底，不崩溃。
        _kill_tree(pid)
        forced = True
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline and _pid_is_alive(pid):
        time.sleep(STOP_POLL_INTERVAL)
    if _pid_is_alive(pid):
        _kill_tree(pid)
        forced = True
    PID_FILE.unlink(missing_ok=True)
    print("stopped" + (" (forced)" if forced else ""))
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cmd_stop(args)
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else default_port()
    pid = read_pid()
    if pid is not None and _pid_is_alive(pid):
        print(f"running (pid {pid}, mode {read_mode()})")
        return 0
    if pid is not None:
        PID_FILE.unlink(missing_ok=True)
    if _port_open(port) and _health_ok(port):
        print(f"running (port probe {port})")
        return 0
    print("stopped")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="axiom_flow_service",
        description="Axiom-Flow 8902 API 服务生命周期管理（start/stop/restart/status）。",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="health 探测端口（默认 QED_AXIOM_URL 解析或 AXIOM_PORT 或 8902）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="启动服务（默认立即返回，--wait 可选等待健康就绪）")
    start.add_argument(
        "--wait", nargs="?", const=HEALTH_TIMEOUT_SECONDS, type=float, default=0.0,
        help=f"等待 /api/v1/health 就绪，默认 {HEALTH_TIMEOUT_SECONDS:g}s",
    )
    start.add_argument(
        "--mode", choices=("local", "qed-engine"), default=None,
        help="模型模式：local=直连 qwen-vl / qed-engine=经 8900 网关（默认读自身 .env 的 QED_API_SELECT）",
    )
    start.set_defaults(func=cmd_start)

    stop = subparsers.add_parser("stop", help="停止服务（优雅 + 强杀兜底）")
    stop.set_defaults(func=cmd_stop)

    restart = subparsers.add_parser("restart", help="重启服务（可 --mode 换模式，重启后生效）")
    restart.add_argument(
        "--wait", nargs="?", const=HEALTH_TIMEOUT_SECONDS, type=float, default=0.0,
        help=f"等待 /api/v1/health 就绪，默认 {HEALTH_TIMEOUT_SECONDS:g}s",
    )
    restart.add_argument(
        "--mode", choices=("local", "qed-engine"), default=None,
        help="模型模式：local=直连 qwen-vl / qed-engine=经 8900 网关（默认读自身 .env 的 QED_API_SELECT）",
    )
    restart.set_defaults(func=cmd_restart)

    status = subparsers.add_parser("status", help="查询服务状态")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    _load_env(ROOT)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.port is None:
        args.port = default_port()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())