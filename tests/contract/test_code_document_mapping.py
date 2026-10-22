"""
模块职责：守护 code-map 登记的唯一性、映射目标有效性与活跃文档元数据。
设计关联（DesignRef）：docs/standards/doc-governance.md
实现状态：Current
被测代码：docs/architecture/code-map.md、docs/design、docs/architecture
守护面：代码与文档映射
失效后果：code-map 登记重复、映射指向不存在的模块/文档/测试，或活跃文档元数据缺失时追溯断裂
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE_MAP = ROOT / "docs" / "architecture" / "code-map.md"
ACTIVE_DOCUMENTS = [
    ROOT / "docs" / "design" / "parsing-pipeline.md",
    ROOT / "docs" / "design" / "parsing-management.md",
    ROOT / "docs" / "design" / "model-integration.md",
    ROOT / "docs" / "design" / "knowledge-analysis.md",
    ROOT / "docs" / "architecture" / "overview.md",
    ROOT / "docs" / "architecture" / "api.md",
    ROOT / "docs" / "architecture" / "database-design.md",
]
PATH_CELL = re.compile(r"`([^`]+)`")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
DOC_REFERENCE = re.compile(r"docs/[^\s`)\]|，、（]+\.md")


def _table_rows(heading: str) -> list[list[str]]:
    """取 `### <heading>` 表格里以 `| ` 开头的数据行（跳过表头与分隔行）。"""
    lines = CODE_MAP.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line == f"### {heading}")
    except StopIteration:  # pragma: no cover - 骨架守护负责报缺段
        return []
    rows: list[list[str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("### ") or line.startswith("## "):
            break
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= {"-", ":"} or cells[0].startswith("路径"):
            continue
        rows.append(cells)
    return rows


def test_managed_module_paths_are_unique_and_exist() -> None:
    rows = _table_rows("受管模块")
    assert rows, "code-map 未登记任何受管模块"
    paths = [PATH_CELL.findall(row[0])[0] for row in rows]
    assert len(paths) == len(set(paths)), f"模块路径重复：{paths}"
    for path in paths:
        assert (ROOT / path.rstrip("/")).exists(), f"code-map 指向不存在的模块：{path}"


def _resolved_references(cell: str) -> list[Path]:
    """从单元格里抽出文档引用（字面 `docs/**.md` 或相对链接），解析为仓库内绝对路径。"""
    references: set[Path] = set()
    for literal in DOC_REFERENCE.findall(cell):
        references.add((ROOT / literal).resolve())
    for link in LINK.finditer(cell):
        target = link.group(2)
        if target.startswith(("http://", "https://", "#")):
            continue
        references.add((CODE_MAP.parent / target).resolve())
    return sorted(references)


def test_mapped_document_references_exist_and_are_active() -> None:
    """「事实源」列引用的文档必须真实存在，且不得指向历史区。"""
    references: list[Path] = []
    for row in _table_rows("受管模块"):
        references.extend(_resolved_references(" ".join(row)))
    assert references, "code-map 未引用任何文档事实源"
    for path in references:
        relative = path.relative_to(ROOT).as_posix()
        assert not relative.startswith("docs/history/"), relative
        assert path.is_file(), f"事实源文档不存在：{relative}"


def test_mapped_test_references_exist() -> None:
    """「关联测试」列指向的测试文件必须在地面上存在（映射表不得指向不存在的回归面）。"""
    checked = 0
    for row in _table_rows("受管模块"):
        for token in PATH_CELL.findall(row[-1]):
            token = token.strip()
            if not token.startswith("tests/"):
                continue
            checked += 1
            assert (ROOT / token).is_file(), f"关联测试不存在：{token}（{row[0]}）"
    assert checked, "code-map 未登记任何关联测试"


def test_unmanaged_paths_exist() -> None:
    rows = _table_rows("非受管配置")
    assert rows
    for row in rows:
        if "根仓库" in " ".join(row):  # 根仓库侧路径不在本工作区，只登记不校验
            continue
        for path in PATH_CELL.findall(row[0]):
            candidate = path.split("（")[0].rstrip("/")
            if not candidate:
                continue
            assert (ROOT / candidate).exists(), f"非受管配置指向不存在的路径：{candidate}"


def test_active_architecture_and_design_documents_declare_metadata() -> None:
    for document in ACTIVE_DOCUMENTS:
        content = document.read_text(encoding="utf-8")
        assert "关联代码：" in content, document
        assert "关联测试：" in content, document
        assert "关联 ADR：" in content, document
