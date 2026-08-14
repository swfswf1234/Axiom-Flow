"""
模块职责：守护架构正文与 Mermaid 视图保持同步。
设计关联（DesignRef）：docs/standards/documentation.md
实现状态：Current
被测代码：docs/architecture
守护面：架构文档治理
失效后果：架构正文/Mermaid 与实现漂移时偏差未被发现
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = ROOT / "docs" / "architecture"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)
CURRENT_DOCUMENTS = {
    "overview.md",
    "code-map.md",
}


def _mermaid(document: str) -> list[str]:
    content = (ARCHITECTURE / document).read_text(encoding="utf-8")
    return [match.group("body") for match in MERMAID_BLOCK.finditer(content)]


def test_architecture_directory_has_one_index_and_two_current_documents():
    assert {path.name for path in ARCHITECTURE.glob("*.md")} == {
        "index.md",
        *CURRENT_DOCUMENTS,
    }
    index = (ARCHITECTURE / "index.md").read_text(encoding="utf-8")
    for document in CURRENT_DOCUMENTS:
        assert f"]({document})" in index


def test_system_context_diagram_names_boundary_inputs_and_outputs():
    diagrams = _mermaid("overview.md")
    assert len(diagrams) == 1
    for label in ("QED-Engine", "Windows", "WSL", "mineru-api", "vLLM", "qwen-vl-plus", "data/books"):
        assert label in diagrams[0]


def test_code_map_lists_v2_modules_with_design_references():
    content = (ARCHITECTURE / "code-map.md").read_text(encoding="utf-8")
    for module in (
        "src/axiom_flow/api/",
        "src/axiom_flow/orchestrator/",
        "src/axiom_flow/ingest/",
        "src/axiom_flow/fallback/",
        "src/axiom_flow/schemas.py",
    ):
        assert f"`{module}`" in content, module
    assert "docs/design/pdf-parsing-and-rendering.md" in content