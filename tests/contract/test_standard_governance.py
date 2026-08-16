"""
模块职责：验证标准目录的边界、统一元数据和索引登记保持一致。
设计关联（DesignRef）：docs/standards/documentation.md
实现状态：Current
被测代码：docs/standards、AGENTS.md
守护面：标准治理
失效后果：标准目录边界、统一元数据或索引镜像发生漂移
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STANDARDS = ROOT / "docs" / "standards"
STANDARD_INDEX = STANDARDS / "index.md"
STANDARD_FILES = {
    "task-lifecycle.md",
    "documentation.md",
    "adr-governance.md",
    "testing.md",
    "cross-project-collaboration.md",
}


def test_standards_directory_has_only_governed_documents_and_index():
    filenames = {path.name for path in STANDARDS.glob("*.md")}
    assert filenames == {"index.md", *STANDARD_FILES}


def test_every_standard_has_uniform_metadata():
    for filename in STANDARD_FILES:
        content = (STANDARDS / filename).read_text(encoding="utf-8")
        assert content.splitlines()[0].startswith("# "), filename
        assert "状态：Current" in content, filename
        assert "最后更新：" in content, filename
        assert "## 目的" in content, filename


def test_standards_index_matches_body_boundaries():
    index = (STANDARD_INDEX).read_text(encoding="utf-8")
    for filename in STANDARD_FILES:
        assert f"]({filename})" in index, filename


def test_agents_routes_to_all_standard_sources():
    content = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/standards/" in content
    for filename in STANDARD_FILES:
        assert f"docs/standards/{filename}" in content, filename