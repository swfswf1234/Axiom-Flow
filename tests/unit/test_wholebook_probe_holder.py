"""W6 探针驱动的 WSL 常驻前置（`--wsl-holder`）契约测试。

事实来源＝PLAN-007「W6 归因更正」：本机 WSL 在**最后一个 Linux 进程退出约 15 秒后回收整个发行版**
（`Stopping docker.service` → `final.target`，下次 `wsl.exe` 调用重新开机），表现为解析中途
`WinError 10054` + 剩余页 `MODEL_UNAVAILABLE`。`vmIdleTimeout` 在本机不生效，唯一可靠的机器侧
前置＝窗口全程让一个常驻 Linux 进程存活。本测试只验证脚本里那段**纯逻辑**（命令形状与滚动循环），
不启动真实进程、不触碰 WSL/Docker、不读写真实数据根。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tmp" / "live_wholebook_req086.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("live_wholebook_req086", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEvent:
    """停止位替身：第 `after` 次查询起返回 True（查询次数即轮询节奏，逐 tick 精确可推）。"""

    def __init__(self, after: int) -> None:
        self.after = after
        self.queries = 0

    def is_set(self) -> bool:
        self.queries += 1
        return self.queries > self.after


class FakeProc:
    """`exits_after=0` 表示 poll 首查即已退出；负数表示永不退出。"""

    def __init__(self, exits_after: int = -1) -> None:
        self.polls = 0
        self.exits_after = exits_after
        self.terminated = False

    def poll(self) -> int | None:
        self.polls += 1
        if self.exits_after < 0:
            return None
        return 0 if self.polls > self.exits_after else None

    def terminate(self) -> None:
        self.terminated = True


def test_holder_command_targets_default_or_named_distro_only(mod) -> None:
    assert mod._holder_command(300, None) == ["wsl", "bash", "-c", "sleep 300"]
    assert mod._holder_command(120, "Ubuntu-24.04") == [
        "wsl", "-d", "Ubuntu-24.04", "bash", "-c", "sleep 120",
    ]


def test_holder_command_never_asks_wsl_to_terminate_the_distro(mod) -> None:
    """反向判据：常驻命令若含 `--terminate`/`--shutdown`，前置本身就会毁掉整个解析窗口。"""
    command = " ".join(mod._holder_command(300, "Ubuntu-24.04")).lower()
    for forbidden in ("--terminate", "--shutdown", "--version"):
        assert forbidden not in command, forbidden


def test_holder_loop_rolls_a_fresh_child_after_each_exit(mod) -> None:
    """滚动续期：子进程一退出就立刻再拉一个，绝不留 ≥15s 的空闲窗给 WSL 回收发行版。"""
    procs = [FakeProc(exits_after=1), FakeProc(exits_after=1)]
    spawned: list[list[str]] = []

    def spawn(command: list[str]) -> FakeProc:
        spawned.append(list(command))
        return procs[len(spawned) - 1]

    ticks = mod._holder_loop(
        stop_event=FakeEvent(after=4),
        command=["wsl", "bash", "-c", "sleep 300"],
        spawn=spawn,
        sleep=lambda _seconds: None,
    )

    assert ticks == 2 and len(spawned) == 2
    assert spawned[0] == spawned[1] == ["wsl", "bash", "-c", "sleep 300"]
    assert not any(proc.terminated for proc in procs), "自然退出的子进程不该再被 terminate"


def test_holder_loop_terminates_the_live_child_when_stopped(mod) -> None:
    """收尾必须收掉仍在跑的那个常驻进程，否则脚本退出后留一个无人认领的 holder。"""
    proc = FakeProc(exits_after=-1)

    ticks = mod._holder_loop(
        stop_event=FakeEvent(after=2),
        command=["wsl", "bash", "-c", "sleep 300"],
        spawn=lambda _command: proc,
        sleep=lambda _seconds: None,
    )

    assert ticks == 1
    assert proc.terminated is True


def test_holder_loop_reports_each_spawn_immediately(mod) -> None:
    """滚动计数必须在拉起时就地可见：S0/summary 是**运行中**写的，恒为 0 就等于没留证。"""
    seen: list[int] = []

    mod._holder_loop(
        stop_event=FakeEvent(after=4),
        command=["wsl", "bash", "-c", "sleep 300"],
        spawn=lambda _command: FakeProc(exits_after=1),
        sleep=lambda _seconds: None,
        on_spawn=lambda n: seen.append(n),
    )

    assert seen == [1, 2], seen
