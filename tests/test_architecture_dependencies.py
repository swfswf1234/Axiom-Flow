"""
模块职责：守护 Axiom-Flow 领域、应用、API、Worker 与基础设施的单向依赖。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
被测代码：src/axiom_flow/domain、src/axiom_flow/application、src/axiom_flow/api、src/axiom_flow/worker
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _python_files(directory: str) -> list[Path]:
    return [path for path in (ROOT / directory).rglob("*.py") if path.name != "__init__.py"]


def test_domain_has_no_framework_or_outer_layer_dependencies():
    forbidden = (
        "fastapi", "sqlalchemy", "httpx", "openpyxl", "fitz",
        "axiom_flow.application", "axiom_flow.infrastructure", "axiom_flow.api",
        "axiom_flow.worker", "axiom_flow.bootstrap",
    )
    for path in _python_files("src/axiom_flow/domain"):
        imports = _imports(path)
        assert not {name for name in imports if name.startswith(forbidden)}, path


def test_application_does_not_import_adapters_or_delivery_layers():
    forbidden = (
        "fastapi", "sqlalchemy", "httpx", "openpyxl", "fitz",
        "axiom_flow.infrastructure", "axiom_flow.api", "axiom_flow.worker", "axiom_flow.bootstrap",
    )
    for path in _python_files("src/axiom_flow/application"):
        imports = _imports(path)
        assert not {name for name in imports if name.startswith(forbidden)}, path


def test_api_and_worker_use_only_application_or_composition_root():
    forbidden = ("axiom_flow.infrastructure",)
    for directory in ("src/axiom_flow/api", "src/axiom_flow/worker"):
        for path in _python_files(directory):
            imports = _imports(path)
            assert not {name for name in imports if name.startswith(forbidden)}, path


def test_old_package_roots_have_no_imports_or_files():
    assert not (ROOT / "backend").exists()
    assert not (ROOT / "src/axiom_flow/app").exists()
    for path in _python_files("src/axiom_flow") + _python_files("tests") + _python_files("evaluation"):
        imports = _imports(path)
        assert not any(name == "backend" or name.startswith("backend.") for name in imports), path
        assert not any(name == "axiom_flow.app" or name.startswith("axiom_flow.app.") for name in imports), path
