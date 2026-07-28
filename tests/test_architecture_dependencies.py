"""
模块职责：守护 Backend 领域、应用、API、Worker 与基础设施的单向依赖。
设计关联（DesignRef）：docs/architecture/v03-target.md
实现状态：Current
被测代码：backend/domain、backend/application、backend/api、backend/worker
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
        "backend.application", "backend.infrastructure", "backend.api",
        "backend.worker", "backend.bootstrap",
    )
    for path in _python_files("backend/domain"):
        imports = _imports(path)
        assert not {name for name in imports if name.startswith(forbidden)}, path


def test_application_does_not_import_adapters_or_delivery_layers():
    forbidden = ("backend.infrastructure", "backend.api", "backend.worker", "backend.bootstrap")
    for path in _python_files("backend/application"):
        imports = _imports(path)
        assert not {name for name in imports if name.startswith(forbidden)}, path


def test_api_and_worker_use_only_application_or_composition_root():
    forbidden = ("backend.infrastructure",)
    for directory in ("backend/api", "backend/worker"):
        for path in _python_files(directory):
            imports = _imports(path)
            assert not {name for name in imports if name.startswith(forbidden)}, path


def test_removed_backend_app_has_no_imports_or_files():
    assert not (ROOT / "backend/app").exists()
    for path in _python_files("backend") + _python_files("tests"):
        assert not any(name == "backend.app" or name.startswith("backend.app.") for name in _imports(path)), path
