"""
模块职责：验证活跃文档入口、目录边界、计划归档和 Agent 协议保持单一。
设计关联（DesignRef）：docs/standards/documentation.md
实现状态：Current
被测代码：README.md、AGENTS.md、docs、pyproject.toml、requirements.txt
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOCUMENT_DIRECTORIES = (
    "architecture",
    "design",
    "adr",
    "standards",
    "guides",
    "plans",
    "trackers",
    "history",
)
ACTIVE_GUIDES = {"index.md", "development.md", "operations.md"}
HISTORY_DIRECTORIES = {"adr", "baselines", "plans"}
RETAINED_2026_07_PLANS = {
    "2026-07-v02-first-loop.md",
    "2026-07-v03-architecture-rebuild.md",
    "2026-07-v03-result-workbench-storage.md",
    "2026-07-v03-rudin-scan-ingestion.md",
    "index.md",
}


def test_document_directories_use_explicit_index_entrypoints():
    assert (DOCS / "index.md").is_file()
    for directory in DOCUMENT_DIRECTORIES:
        assert (DOCS / directory / "index.md").is_file(), directory
    assert not (DOCS / "templates").exists()
    assert not list(DOCS.rglob("README.md"))


def test_root_agents_is_the_only_active_agent_protocol():
    assert (ROOT / "AGENTS.md").is_file()
    assert not (DOCS / "agents_read.md").exists()
    assert not list(DOCS.glob("agents*.md"))


def test_active_architecture_and_guides_use_stable_names():
    versioned = []
    for directory in (DOCS / "architecture", DOCS / "guides"):
        versioned.extend(path for path in directory.glob("*.md") if path.stem.startswith(("v0", "v1")))
    assert not versioned


def test_guides_are_two_human_handbooks_and_an_index():
    guide_names = {path.name for path in (DOCS / "guides").glob("*.md")}
    assert guide_names == ACTIVE_GUIDES

    index = (DOCS / "guides" / "index.md").read_text(encoding="utf-8")
    assert "development.md" in index
    assert "operations.md" in index
    assert "testing.md" not in index

    operations = (DOCS / "guides" / "operations.md").read_text(encoding="utf-8")
    backlog = (DOCS / "trackers" / "backlog.md").read_text(encoding="utf-8")
    assert "OPS-001：生产运维基线未实现" in operations
    assert "OPS-001：生产运维基线未实现" in backlog
    assert "不得直接暴露到公网" in operations


def test_history_retains_only_auditable_evidence():
    history = DOCS / "history"
    directories = {path.name for path in history.iterdir() if path.is_dir()}
    assert directories == HISTORY_DIRECTORIES
    assert not (history / "guides").exists()
    assert not (history / "2026-07-mineru-baseline").exists()

    baselines = {path.name for path in (history / "baselines").glob("*") if path.is_file()}
    assert baselines == {"index.md", "v01-mineru.md"}
    baseline = (history / "baselines" / "v01-mineru.md").read_text(encoding="utf-8")
    assert "Git 锚点：`6cc4129`" in baseline
    assert "git show 6cc4129:docs/architecture.md" in baseline

    month = history / "plans" / "2026-07"
    retained = {path.name for path in month.glob("*.md")}
    assert retained == RETAINED_2026_07_PLANS
    index = (month / "index.md").read_text(encoding="utf-8")
    for anchor in ("4961cfa", "be7ec34", "a6ec4e0"):
        assert f"`{anchor}`" in index

def test_root_readme_serves_qed_operators_and_new_developers():
    content = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "QED 的技术 PDF 解析与质量审阅组件",
        "## 核心能力",
        "## 技术栈",
        "## 能力边界",
        "## 快速启动",
        "## 典型流程",
        "AGENTS.md",
        "docs/index.md",
    ):
        assert required in content
    assert "事实优先级" not in content
    assert "设计状态：" not in content


def test_docs_index_only_navigates_document_areas():
    content = (DOCS / "index.md").read_text(encoding="utf-8")
    for required in (
        "architecture/index.md",
        "design/index.md",
        "adr/index.md",
        "guides/index.md",
        "plans/index.md",
        "trackers/index.md",
        "standards/index.md",
        "history/index.md",
    ):
        assert required in content
    assert "```mermaid" not in content
    assert "```powershell" not in content
    assert " passed" not in content
    assert "GitHub Actions" not in content


def test_agents_routes_tasks_problems_and_completion():
    content = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    for required in (
        "## 快速开始",
        "## 任务路由",
        "## 问题定位",
        "## 事实来源与决策",
        "## 完成检查",
        "docs/architecture/code-map.md",
        "docs/index.md",
        "docs/trackers/current.md",
        "docs/standards/task-lifecycle.md",
        "tests/test_architecture_documents.py",
        "tests/test_design_documents.py",
        "docs/guides/development.md",
        "docs/guides/operations.md",
    ):
        assert required in content


def test_dependency_install_entrypoints_have_one_source():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    development_guide = (DOCS / "guides" / "development.md").read_text(encoding="utf-8")

    assert "dev" in pyproject["project"]["optional-dependencies"]
    assert (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()[-1] == "-e ."
    assert not (ROOT / "requirements-dev.txt").exists()
    assert 'python -m pip install -e ".[dev]"' in development_guide
    assert "requirements-dev.txt" not in development_guide
