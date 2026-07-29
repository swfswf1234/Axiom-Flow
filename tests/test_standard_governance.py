"""
模块职责：验证标准目录的边界、统一契约、索引和测试反向关联保持一致。
设计关联（DesignRef）：docs/standards/documentation.md
实现状态：Current
被测代码：docs/standards、AGENTS.md
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STANDARDS = ROOT / "docs" / "standards"
STANDARD_INDEX = STANDARDS / "index.md"
STANDARD_FILES = {
    "task-lifecycle.md",
    "documentation.md",
    "adr-governance.md",
    "code-document-traceability.md",
}
REQUIRED_FIELDS = ("状态", "最后更新", "治理对象", "依据 ADR", "关联测试")
REQUIRED_SECTIONS = ("## 目的与边界", "## 强制规则", "## 执行与门禁", "## 变更与取代")
PATH_REFERENCE = re.compile(r"`((?:docs/adr|tests)/[^`]+)`")
INDEX_LINK = re.compile(r"\[(?P<title>[^]]+)]\((?P<path>[^)]+\.md)\)")


def _field(content: str, name: str) -> str:
    matches = re.findall(rf"^{re.escape(name)}：(.*)$", content, re.MULTILINE)
    assert len(matches) == 1, f"{name} 必须恰好出现一次"
    return matches[0].strip()


def _standards() -> dict[str, dict[str, object]]:
    records = {}
    for filename in sorted(STANDARD_FILES):
        content = (STANDARDS / filename).read_text(encoding="utf-8")
        records[filename] = {
            "title": content.splitlines()[0].removeprefix("# "),
            "content": content,
            "object": _field(content, "治理对象"),
            "tests": set(PATH_REFERENCE.findall(_field(content, "关联测试"))),
        }
    return records


def _index_entries() -> dict[str, dict[str, object]]:
    entries = {}
    for line in STANDARD_INDEX.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| ["):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 4, line
        match = INDEX_LINK.fullmatch(cells[0])
        assert match, line
        entries[match.group("path")] = {
            "title": match.group("title"),
            "object": cells[1],
            "tests": set(PATH_REFERENCE.findall(cells[3])),
        }
    return entries


def test_standards_directory_has_only_governed_documents_and_index():
    filenames = {path.name for path in STANDARDS.glob("*.md")}
    assert filenames == {"index.md", *STANDARD_FILES}


def test_every_standard_has_uniform_metadata_and_sections():
    for filename, record in _standards().items():
        content = str(record["content"])
        for field in REQUIRED_FIELDS:
            assert _field(content, field), f"{filename}: {field} 不能为空"
        assert _field(content, "状态") == "Current", filename
        positions = [content.index(section) for section in REQUIRED_SECTIONS]
        assert positions == sorted(positions), filename

        for reference in PATH_REFERENCE.findall(_field(content, "依据 ADR")):
            assert reference.startswith("docs/adr/"), reference
            assert (ROOT / reference).is_file(), reference
        assert record["tests"], filename
        for test_path in record["tests"]:
            assert (ROOT / test_path).is_file(), test_path


def test_declared_tests_reference_their_governing_standard():
    for filename, record in _standards().items():
        design_ref = f"设计关联（DesignRef）：docs/standards/{filename}"
        for test_path in record["tests"]:
            content = (ROOT / test_path).read_text(encoding="utf-8")
            assert design_ref in content, f"{test_path} 未反向关联 {filename}"


def test_standards_index_matches_body_boundaries_and_tests():
    standards = _standards()
    entries = _index_entries()
    assert set(entries) == set(standards)
    for filename, record in standards.items():
        assert entries[filename] == {
            "title": record["title"],
            "object": record["object"],
            "tests": record["tests"],
        }


def test_agents_routes_to_all_standard_sources_without_old_push_rule():
    content = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "`docs/standards/` 是工程治理规则的唯一事实源" in content
    for filename in STANDARD_FILES:
        assert f"docs/standards/{filename}" in content
    assert "数据重建、正式发布和远端推送属于 D 类操作" not in content
