"""
模块职责：守护 code-map 登记的唯一性、DesignRef 有效性与活跃文档元数据。
设计关联（DesignRef）：docs/standards/documentation.md
实现状态：Current
被测代码：docs/architecture/code-map.md、docs/design、docs/architecture
守护面：代码与文档映射
失效后果：code-map 登记重复、DesignRef 失效或活跃文档元数据缺失时追溯断裂
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_MAP = ROOT / "docs" / "architecture" / "code-map.md"
ACTIVE_DOCUMENTS = [
    ROOT / "docs" / "design" / "parsing-pipeline.md",
    ROOT / "docs" / "architecture" / "overview.md",
    ROOT / "docs" / "architecture" / "api.md",
    ROOT / "docs" / "architecture" / "database-design.md",
]


def _parse_code_map() -> list[dict[str, str]]:
    entries = []
    in_managed = False
    for line in CODE_MAP.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_managed = line.startswith("## 受管模块")
            continue
        if not in_managed or not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) >= 5, line
        entries.append(
            {
                "path": cells[0].strip("`"),
                "design": cells[3].strip("`"),
                "status": cells[2],
            }
        )
    return entries


def test_code_map_entries_are_unique():
    entries = _parse_code_map()
    mapped_paths = [entry["path"] for entry in entries]
    assert mapped_paths
    assert len(mapped_paths) == len(set(mapped_paths))


def test_mapped_design_references_exist_and_are_active():
    for entry in _parse_code_map():
        assert entry["design"].startswith("docs/"), entry
        assert not entry["design"].startswith("docs/history/"), entry
        assert (ROOT / entry["design"]).is_file(), entry["design"]


def test_active_architecture_and_design_documents_declare_metadata():
    for document in ACTIVE_DOCUMENTS:
        content = document.read_text(encoding="utf-8")
        assert "关联代码：" in content, document
        assert "关联测试：" in content, document
        assert "关联 ADR：" in content, document