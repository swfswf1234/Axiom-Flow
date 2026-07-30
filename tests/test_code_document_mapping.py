"""
模块职责：守护代码、设计文档和测试之间的双向映射关系。
设计关联（DesignRef）：docs/standards/code-document-traceability.md
实现状态：Current
被测代码：docs/architecture/code-map.md
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_MAP = ROOT / "docs" / "architecture" / "code-map.md"
MANAGED_DIRECTORIES = ("src/axiom_flow", "evaluation", "tests")
MANAGED_WEB_FILES = ("web/index.html", "web/style.css", "web/app.js")
MANAGED_TOOL_FILES = ("alembic.ini", "src/axiom_flow/migrations/script.py.mako")
EXEMPT_FILENAMES = {"__init__.py"}
ACTIVE_DOCUMENTS = tuple(
    path
    for directory in (ROOT / "docs" / "architecture", ROOT / "docs" / "design")
    for path in directory.glob("*.md")
    if path.name != "index.md"
)


def _parse_code_map():
    """读取受控 Markdown 表格，避免额外引入配置格式和解析依赖。"""
    entries = []
    for line in CODE_MAP.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 6:
            raise AssertionError(f"映射表列数错误：{line}")
        entries.append(
            {
                "path": cells[0].strip("`"),
                "layer": cells[1],
                "status": cells[2],
                "design": cells[3].strip("`"),
                "tests": cells[4].strip("`"),
            }
        )
    return entries


def _managed_modules():
    modules = set()
    for directory in MANAGED_DIRECTORIES:
        for path in (ROOT / directory).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if path.name not in EXEMPT_FILENAMES:
                modules.add(relative)
    modules.update(MANAGED_WEB_FILES)
    modules.update(MANAGED_TOOL_FILES)
    return modules


def _metadata_references(content, field_name, allowed_prefixes):
    """提取元数据行中的仓库相对路径，忽略“尚未实现”等说明文本。"""
    line = next(
        (candidate for candidate in content.splitlines() if candidate.startswith(field_name)),
        "",
    )
    references = re.findall(r"`([^`]+)`", line)
    return [reference for reference in references if reference.startswith(allowed_prefixes)]


def test_every_non_exempt_module_is_mapped_once():
    entries = _parse_code_map()
    mapped_paths = [entry["path"] for entry in entries]

    assert len(mapped_paths) == len(set(mapped_paths))
    assert set(mapped_paths) == _managed_modules()


def test_mapped_paths_and_design_references_exist():
    for entry in _parse_code_map():
        assert (ROOT / entry["path"]).is_file()
        assert (ROOT / entry["design"]).is_file()
        if entry["tests"] != "—":
            assert (ROOT / entry["tests"]).is_file()


def test_module_headers_match_mapping_status_and_design_reference():
    for entry in _parse_code_map():
        source = (ROOT / entry["path"]).read_text(encoding="utf-8")
        if entry["path"].endswith(".ini"):
            assert f"DesignRef: {entry['design']}" in source
            assert f"Status: {entry['status']}" in source
        else:
            assert f"设计关联（DesignRef）：{entry['design']}" in source
            assert f"实现状态：{entry['status']}" in source
        if entry["status"] == "Legacy":
            assert entry["design"].startswith("docs/history/")
        else:
            assert entry["design"].startswith("docs/")
            assert not entry["design"].startswith("docs/history/")


def test_active_architecture_and_design_documents_declare_metadata():
    entries_by_path = {entry["path"]: entry for entry in _parse_code_map()}
    for document in ACTIVE_DOCUMENTS:
        content = document.read_text(encoding="utf-8")
        assert "关联代码：" in content, document
        assert "关联测试：" in content, document
        assert "关联 ADR：" in content, document

        for code_path in _metadata_references(
            content,
            "关联代码：",
            ("alembic.ini", "app/", "src/axiom_flow/", "evaluation/", "web/"),
        ):
            assert (ROOT / code_path).is_file(), document
            assert code_path in entries_by_path, document
            assert entries_by_path[code_path]["design"] == document.relative_to(ROOT).as_posix()
        for test_path in _metadata_references(content, "关联测试：", ("tests/",)):
            assert (ROOT / test_path).is_file(), document
        for adr_path in _metadata_references(content, "关联 ADR：", ("docs/adr/",)):
            assert (ROOT / adr_path).is_file(), document
