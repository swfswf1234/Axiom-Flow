"""
模块职责：守护设计文档集合、Mermaid 视图和元数据保持同步。
设计关联（DesignRef）：docs/standards/documentation.md
实现状态：Current
被测代码：docs/design
守护面：设计文档治理
失效后果：设计正文/Mermaid/元数据与实现漂移时偏差未被发现
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "design"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)
CURRENT_DOCUMENTS = {
    "pdf-parsing-and-rendering.md",
    "8902-integration-contract.md",
    "service-lifecycle.md",
    "service-lifecycle-encoding-fix.md",
    "af-books-sync.md",
    "model-mode-config.md",
    "docs-restructure-alignment.md",
}


def _mermaid(document: str) -> list[str]:
    content = (DESIGN / document).read_text(encoding="utf-8")
    return [match.group("body") for match in MERMAID_BLOCK.finditer(content)]


def test_design_directory_has_one_index_and_one_current_contract():
    assert {path.name for path in DESIGN.glob("*.md")} == {"index.md", *CURRENT_DOCUMENTS}
    index = (DESIGN / "index.md").read_text(encoding="utf-8")
    for document in CURRENT_DOCUMENTS:
        assert f"]({document})" in index


def test_every_current_design_has_one_embedded_mermaid_view():
    for document in CURRENT_DOCUMENTS:
        assert len(_mermaid(document)) == 1, document


def test_active_design_documents_declare_metadata():
    for document in CURRENT_DOCUMENTS:
        content = (DESIGN / document).read_text(encoding="utf-8")
        for field in ("设计状态：", "实现状态：", "最后更新：", "关联代码：", "关联测试：", "关联 ADR："):
            assert field in content, f"{document}: 缺少 {field}"