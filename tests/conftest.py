"""
模块职责：附加唯一测试层级、阻止外部网络并延迟提供隔离数据库 fixture。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
关联测试：tests/contract/test_test_suite_governance.py
"""

import ipaddress
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from axiom_flow.infrastructure.config import Settings

ROOT = Path(__file__).resolve().parents[1]
LAYERS = {"unit", "contract", "integration", "system", "smoke"}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """目录是层级事实源，收集时附加恰好一个同名 marker。"""
    for item in items:
        relative = Path(item.path).resolve().relative_to(ROOT)
        layer = relative.parts[1] if len(relative.parts) > 1 else ""
        if layer not in LAYERS:
            raise pytest.UsageError(f"测试必须位于唯一分层目录：{relative.as_posix()}")
        item.add_marker(getattr(pytest.mark, layer))


def _is_loopback(host: object) -> bool:
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch: pytest.MonkeyPatch):
    """所有自动测试只允许 MySQL 与 smoke 使用本机回环连接。"""
    original_connect = socket.socket.connect

    def guarded_connect(instance: socket.socket, address):
        if isinstance(address, tuple) and address and _is_loopback(address[0]):
            return original_connect(instance, address)
        raise RuntimeError(f"自动测试禁止外部网络连接：{address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(scope="session")
def mysql_settings() -> "Settings":
    """需要数据库的层首次请求时才加载 MySQL 支持。"""
    from tests.support.mysql import prepare_mysql_settings

    return prepare_mysql_settings()


@pytest.fixture
def mysql_store(mysql_settings: "Settings"):
    """每项数据库测试前后清理唯一隔离测试库。"""
    from tests.support.mysql import isolated_mysql_store

    with isolated_mysql_store(mysql_settings) as store:
        yield store
