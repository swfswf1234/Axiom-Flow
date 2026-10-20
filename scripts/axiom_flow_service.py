"""Axiom-Flow 8902 API 服务生命周期管理：start / stop / restart / status。

子进程 = `sys.executable -m uvicorn axiom_flow.api.main:app`（继承当前解释器，天然落在
QED_env）；PID 文件 logs/qed-axiom.pid，子进程 stdout/stderr 落 logs/qed-axiom-serve.log。
环境：从仓库根查找 `.env`，setdefault 注入 `AXIOM_*` / `QED_*`（已有环境不覆盖；密钥不注入，
配置由 config.py 自读），standalone 启动不缺配置。
配置简化（2026-09-17，PLAN-002）：`--mode` 与模式持久化退役——解析始终直连模型服务
（ADR 0004），配置三段式（服务 / 数据与数据库 / 解析引擎）见 docs/design/model-integration.md。
接口契约（含 8900 控制中心接入方式）见 docs/architecture/api.md ①生命周期与健康分类。

退出码：0 成功或幂等（含按端口真停外部实例）；1 运行失败（spawn 失败、health 超时、stop 无法终止）；
2 参数错误（argparse）；3 端口由非本脚本启动的进程监听且 `netstat` 反查不到监听 PID（无从处置，
本脚本不终止任何进程；REQ-126／PLAN-023 三形分界）。
Windows 注意：os.kill(pid, 0) 会直接 TerminateProcess，进程存在性检测用 tasklist。

停止分诊（2026-10-20，PLAN-023 W1~W4）：本脚本是 8902 端口的生命周期属主，「无 PID 文件而端口在听」
＝PID 文件丢失的外部实例形，按端口反查监听 PID 后走同一条「优雅停止 → 强杀兜底 → 存活复核」链真停
（与根仓库自有 8900/8903 脚本的「只检测不杀」有意不同：控制中心对非托管进程无处置权，脚本对自己
端口的实例有）；反查不到 PID 时以 3 回报且零终止调用。`cmd_restart` 取回停止结果，非 0 即中止。

停止可靠性（2026-09-04 修复，与根仓库 scripts/qed_engine_service.py 同构）：判活用
_proc_alive（ctypes OpenProcess，毫秒级）——tasklist 探测失败曾被当作「进程已死」导致
假 stopped；优雅信号（CTRL_BREAK）在 conda run 等跨 console 语境下可能静默空放，一律以
_proc_alive 确认 + taskkill 强杀树兜底，终止失败显式报错退出 1。
"""

from __future__ import annotations

import argparse
import ctypes
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
SERVE_LOG = LOG_DIR / "qed-axiom-serve.log"

DEFAULT_PORT = 8902

NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
CTRL_BREAK_EVENT = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGTERM)

STOP_GRACE_SECONDS = 5.0
STOP_POLL_INTERVAL = 0.2
HEALTH_TIMEOUT_SECONDS = 30.0
HEALTH_INTERVAL_SECONDS = 0.5

UNMANAGED_LISTENER_EXIT = 3


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
    """从 start 向上查找 `.env`，注入 `AXIOM_*` / `QED_*`（已有环境变量不覆盖）。

    独立启动 `axiom_flow_service.py` 时补上仓库统一配置，保持与调用方注入环境一致。
    密钥（`API_KEY`、`QED_DB_PASSWORD` 等）不注入进程环境——配置由 `config.py` 读取 .env 自取。
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
        if key.startswith(("AXIOM_", "QED_")):
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


def _proc_alive(pid: int) -> bool:
    """进程存活判定（ctypes kernel32，毫秒级、无 WMI/GBK 依赖）——stop 路径专用。

    2026-09-04 修复：tasklist 版 _pid_is_alive 探测超时/失败返回 False，与「进程已死」
    语义混淆，曾致 stop 假成功（打印 stopped 而服务进程存活）。此处句柄打开失败按
    错误码区分：ERROR_ACCESS_DENIED（存活但无权限查询）按存活，其余按死亡；
    GetExitCodeProcess 查询失败按存活（未知 ≠ 已死：宁可多轮询/强杀，不可假成功）。
    """
    if os.name != "nt":
        return _pid_is_alive(pid)
    if pid <= 0:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259  # WAIT_TIMEOUT 值复用为「未退出」退出码
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED：无权限查询但确定存活
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True  # 查询失败 ≠ 已死
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


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


def _parse_netstat_pid(text: str, port: int) -> int | None:
    """从 `netstat -ano` 文本取该端口 LISTENING 行的 PID；无匹配返回 None。"""
    suffix = f":{port}"
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 5 or cols[0] != "TCP" or cols[3] != "LISTENING":
            continue
        if cols[1].endswith(suffix):
            try:
                return int(cols[4])
            except ValueError:
                return None
    return None


def _listening_pid(port: int) -> int | None:
    """端口反查监听进程 PID（REQ-126：外部实例形需要它才能落到真停链）。

    字节捕获 + 容错解码（本机 netstat 表头走 GBK 管道，`text=True` 会让 readerthread
    中断而吞掉整段输出，判例同 `_pid_is_alive` 的 V2-012）；命令不可用或无法解析时
    返回 None，由调用方降级为「未取得监听 PID」并以退出码 3 回报。
    """
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    blob = result.stdout
    text = blob.decode("utf-8", errors="replace") if isinstance(blob, bytes) else (blob or "")
    return _parse_netstat_pid(text, port)


def _spawn(port: int) -> int:
    """拉起服务进程并写 PID 文件；返回退出码（子进程继承当前环境，配置由 config.py 自读）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(SERVE_LOG, "ab")  # noqa: SIM115 - 子进程继承句柄，随其生命周期
    try:
        proc = subprocess.Popen(
            serve_command(port),
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=NEW_PROCESS_GROUP,
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
    if _spawn(port) != 0:
        return 1
    if args.wait and args.wait > 0:
        return _wait_healthy(port, args.wait)
    return 0


def _kill_tree(pid: int) -> bool:
    """taskkill 强杀进程树（优雅停止超时后的兜底）；返回是否确认成功。

    2026-09-04 修复：不再静默吞错——失败输出直接打印，调用方以 _proc_alive
    复核，两腿都失效时 cmd_stop 显式退出码 1（假 stopped 不再可能）。
    """
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[kill] taskkill /PID {pid} 执行异常：{exc}")
        return False
    if result.returncode == 0:
        return True
    detail = " ".join(
        part.strip() for part in (result.stdout or "", result.stderr or "") if part.strip()
    )
    print(f"[kill] taskkill /PID {pid} 失败（退出码 {result.returncode}）：{detail}")
    return False


def _graceful_then_force(pid: int) -> tuple[bool, bool]:
    """既有停止链（优雅 → 宽限轮询 → taskkill 强杀兜底 → 存活复核）。

    返回 `(是否确认终止, 是否走了强杀兜底)`；两腿都失效时由调用方显式报错，绝不假 stopped。
    """
    forced = False
    try:
        os.kill(pid, CTRL_BREAK_EVENT)
    except (OSError, SystemError):
        # 无交互控制台环境（服务/管道）下 GenerateConsoleCtrlEvent 抛 WinError 87，
        # CPython 包装为 SystemError；跨 console 语境还可能静默空放——
        # 一律走 taskkill 强杀兜底，后续以 _proc_alive 复核。
        if _kill_tree(pid):
            forced = True
    deadline = time.monotonic() + STOP_GRACE_SECONDS
    while time.monotonic() < deadline and _proc_alive(pid):
        time.sleep(STOP_POLL_INTERVAL)
    if _proc_alive(pid):
        # 优雅信号未生效（空放/被忽略）→ taskkill 强杀树；判活用 _proc_alive，
        # 探测失败不会再被误判为已死（2026-09-04 假 stopped 根因修复）。
        forced = True
        _kill_tree(pid)
        deadline = time.monotonic() + STOP_GRACE_SECONDS
        while time.monotonic() < deadline and _proc_alive(pid):
            time.sleep(STOP_POLL_INTERVAL)
    return not _proc_alive(pid), forced


def _stop_unmanaged(args: argparse.Namespace) -> int:
    """无 PID 文件的分诊：端口不通＝真未运行（幂等 0）；端口在听＝外部实例，按反查 PID 真停。

    本脚本对 8902 端口有生命周期属主身份，因此这里执行真停（区别于根仓库 8900 控制中心：
    它不终止非本中心启动的进程）。反查不到监听 PID 时无从处置，退出码 3 且零终止调用。
    """
    port = getattr(args, "port", None) or default_port()
    if not _port_open(port):
        print("not running (no pid file)")
        return 0
    listener = _listening_pid(port)
    if listener is None:
        print(
            f"port {port} 由非本脚本启动的进程监听，但 netstat 反查不到监听 PID，"
            "本脚本未终止任何进程（请人工按端口处置）"
        )
        return UNMANAGED_LISTENER_EXIT
    stopped, forced = _graceful_then_force(listener)
    if not stopped:
        print(
            f"stop failed: 端口 {port} 上的未纳管进程 pid {listener} "
            f"优雅停止与 taskkill 强杀后仍存活，请手动 taskkill /PID {listener} /T /F"
        )
        return 1
    print(f"stopped (unmanaged listener pid {listener}{', forced' if forced else ''})")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    pid = read_pid()
    if pid is None:
        return _stop_unmanaged(args)
    if not _proc_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        print("not running (stale pid file)")
        return 0
    stopped, forced = _graceful_then_force(pid)
    if not stopped:
        print(f"stop failed: pid {pid} 优雅停止与 taskkill 强杀后仍存活，请手动 taskkill /PID {pid} /T /F")
        return 1
    PID_FILE.unlink(missing_ok=True)
    print("stopped" + (" (forced)" if forced else ""))
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    stopped = cmd_stop(args)
    if stopped != 0:
        print(f"restart aborted: stop 未确认成功（退出码 {stopped}），不拉起新实例")
        return stopped
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    port = args.port if args.port is not None else default_port()
    pid = read_pid()
    if pid is not None and _pid_is_alive(pid):
        print(f"running (pid {pid})")
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
    start.set_defaults(func=cmd_start)

    stop = subparsers.add_parser("stop", help="停止服务（优雅 + 强杀兜底）")
    stop.set_defaults(func=cmd_stop)

    restart = subparsers.add_parser("restart", help="重启服务")
    restart.add_argument(
        "--wait", nargs="?", const=HEALTH_TIMEOUT_SECONDS, type=float, default=0.0,
        help=f"等待 /api/v1/health 就绪，默认 {HEALTH_TIMEOUT_SECONDS:g}s",
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