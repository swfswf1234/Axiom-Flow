"""
模块职责：守护架构正文、Mermaid 视图、领域状态和已知实现偏差保持同步。
设计关联（DesignRef）：docs/standards/code-document-traceability.md
实现状态：Current
被测代码：docs/architecture、backend/domain/models.py、backend/api/main.py
"""

import ast
import re
from pathlib import Path

from backend.domain.models import (
    DocumentStatus,
    ExtractionRunStatus,
    JobStatus,
    ParseRunStatus,
)

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = ROOT / "docs" / "architecture"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)


def _mermaid(document: str) -> list[str]:
    content = (ARCHITECTURE / document).read_text(encoding="utf-8")
    return [match.group("body") for match in MERMAID_BLOCK.finditer(content)]


def _api_gets_repository_from_container() -> bool:
    source = (ROOT / "backend" / "api" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "container"
        and node.attr == "repository"
        for node in ast.walk(tree)
    )


def test_architecture_directory_has_one_index_and_four_current_documents():
    assert {path.name for path in ARCHITECTURE.glob("*.md")} == {
        "index.md",
        "overview.md",
        "runtime-architecture.md",
        "data-lifecycle.md",
        "code-map.md",
    }
    index = (ARCHITECTURE / "index.md").read_text(encoding="utf-8")
    for document in ("overview.md", "runtime-architecture.md", "data-lifecycle.md", "code-map.md"):
        assert f"]({document})" in index
    assert "domain-boundaries.md" not in index


def test_system_context_diagram_names_boundary_inputs_and_outputs():
    diagrams = _mermaid("overview.md")
    assert len(diagrams) == 1
    for label in ("用户", "技术 PDF", "Axiom-Flow", "阿里百炼", "Excel", "已发布知识"):
        assert label in diagrams[0]


def test_runtime_diagrams_cover_topology_and_package_dependencies():
    diagrams = _mermaid("runtime-architecture.md")
    assert len(diagrams) == 2
    for label in ("Web 工作台", "API v1", "独立 Worker", "MySQL", "本地产物", "阿里百炼"):
        assert label in diagrams[0]
    for package in ("api", "worker", "bootstrap", "application", "infrastructure", "domain"):
        assert f"[{package}]" in diagrams[1]


def test_data_state_diagrams_cover_every_domain_enum_value():
    diagrams = _mermaid("data-lifecycle.md")
    assert len(diagrams) == 5
    state_diagrams = "\n".join(diagrams[1:])
    for enum_type in (DocumentStatus, JobStatus, ParseRunStatus, ExtractionRunStatus):
        for member in enum_type:
            assert member.value in state_diagrams, f"{enum_type.__name__}.{member.name}"


def test_arch_001_tracks_api_repository_access_until_it_is_removed():
    runtime = (ARCHITECTURE / "runtime-architecture.md").read_text(encoding="utf-8")
    backlog = (ROOT / "docs" / "trackers" / "backlog.md").read_text(encoding="utf-8")
    has_direct_access = _api_gets_repository_from_container()

    assert ("ARCH-001" in runtime) is has_direct_access
    assert ("ARCH-001" in backlog) is has_direct_access
    assert ("实现状态：In Progress" in runtime) is has_direct_access
