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
    "templates",
    "history",
)
CLOSED_STATES = ("实现状态：Completed", "状态：Completed", "状态：Superseded")


def test_each_document_directory_has_readme_entrypoint():
    for directory in DOCUMENT_DIRECTORIES:
        assert (DOCS / directory / "README.md").is_file(), directory


def test_root_agents_is_the_only_active_agent_protocol():
    assert (ROOT / "AGENTS.md").is_file()
    assert not (DOCS / "agents_read.md").exists()
    assert not list(DOCS.glob("agents*.md"))


def test_active_plans_do_not_contain_closed_work():
    for plan in (DOCS / "plans").glob("*.md"):
        if plan.name == "README.md":
            continue
        content = plan.read_text(encoding="utf-8")
        assert not any(state in content for state in CLOSED_STATES), plan


def test_active_architecture_and_guides_use_stable_names():
    versioned = []
    for directory in (DOCS / "architecture", DOCS / "guides"):
        versioned.extend(path for path in directory.glob("*.md") if path.stem.startswith(("v0", "v1")))
    assert not versioned


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
        "docs/README.md",
    ):
        assert required in content
    assert "事实优先级" not in content
    assert "设计状态：" not in content


def test_docs_readme_guides_module_development():
    content = (DOCS / "README.md").read_text(encoding="utf-8")
    for required in ("## 项目结构", "## 运行框架", "## 主数据流", "## 模块职责", "## 开发流程"):
        assert required in content
    assert content.count("```mermaid") >= 3
    assert "## 按任务阅读" not in content
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
        "docs/trackers/current.md",
        "docs/standards/task-lifecycle.md",
    ):
        assert required in content


def test_dependency_install_entrypoints_have_one_source():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    local_guide = (DOCS / "guides" / "local-development.md").read_text(encoding="utf-8")

    assert "dev" in pyproject["project"]["optional-dependencies"]
    assert (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()[-1] == "-e ."
    assert not (ROOT / "requirements-dev.txt").exists()
    assert 'python -m pip install -e ".[dev]"' in local_guide
    assert "requirements-dev.txt" not in local_guide
