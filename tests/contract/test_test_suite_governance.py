"""
模块职责：守护五层测试目录、唯一 marker、公共辅助和根 fixture 边界。
设计关联（DesignRef）：docs/standards/testing.md
实现状态：Current
被测代码：tests、pyproject.toml
"""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
LAYERS = {"unit", "contract", "integration", "system", "smoke"}


def test_test_modules_live_in_exact_layer_directories():
    assert {path.name for path in TESTS.iterdir() if path.is_dir() and path.name != "__pycache__"} == {
        *LAYERS,
        "support",
    }
    assert {path.name for path in TESTS.glob("test_*.py")} == set()
    assert not list((TESTS / "support").glob("test_*.py"))
    for path in TESTS.rglob("test_*.py"):
        assert path.relative_to(TESTS).parts[0] in LAYERS


def test_pytest_registers_every_layer_and_uses_strict_imports():
    content = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'addopts = "--strict-markers --import-mode=importlib"' in content
    for layer in LAYERS:
        assert f'"{layer}:' in content


def test_root_conftest_has_no_eager_application_or_database_imports():
    tree = ast.parse((TESTS / "conftest.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
    )
    assert not any(name.startswith(("axiom_flow", "pymysql", "sqlalchemy")) for name in imported)


def test_collected_test_has_one_directory_marker(request: pytest.FixtureRequest):
    markers = {marker.name for marker in request.node.iter_markers() if marker.name in LAYERS}
    assert markers == {"contract"}
