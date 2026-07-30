"""
模块职责：验证活跃计划的命名、元数据、生命周期状态及计划索引边界保持一致。
设计关联（DesignRef）：docs/standards/task-lifecycle.md
实现状态：Current
被测代码：docs/plans
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANS = ROOT / "docs" / "plans"
PLAN_INDEX = PLANS / "index.md"
PLAN_FILE = re.compile(r"\d{4}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
ACTIVE_STATUSES = {"Accepted", "In Progress", "Blocked"}
TASK_TYPES = {"A", "B", "C", "D"}
REQUIRED_FIELDS = (
    "状态",
    "任务类型",
    "最后更新",
    "关联 ADR",
    "关联设计",
    "关联 Tracker",
    "归档判定",
)
REQUIRED_SECTIONS = (
    "## 目标与成功标准",
    "## 范围与非目标",
    "## 前置条件",
    "## 工作项",
    "## 验证与验收",
    "## 回滚",
    "## 关闭与归档",
)


def _field(content: str, name: str) -> str:
    matches = re.findall(rf"^{re.escape(name)}：(.*)$", content, re.MULTILINE)
    assert len(matches) == 1, f"{name} 必须恰好出现一次"
    return matches[0].strip()


def _active_plans() -> dict[str, dict[str, str]]:
    plans = {}
    for path in sorted(PLANS.glob("*.md")):
        if path.name == "index.md":
            continue
        assert PLAN_FILE.fullmatch(path.name), path.name
        content = path.read_text(encoding="utf-8")
        plans[path.name] = {
            "title": content.splitlines()[0].removeprefix("# "),
            "status": _field(content, "状态"),
            "type": _field(content, "任务类型"),
            "content": content,
        }
    return plans


def test_active_plans_have_complete_metadata_and_sections():
    for filename, plan in _active_plans().items():
        content = plan["content"]
        for field in REQUIRED_FIELDS:
            _field(content, field)
        assert plan["status"] in ACTIVE_STATUSES, filename
        assert plan["type"] in TASK_TYPES, filename
        for section in REQUIRED_SECTIONS:
            assert section in content, f"{filename}: 缺少 {section}"


def test_blocked_plans_declare_evidence_and_recovery_contract():
    for filename, plan in _active_plans().items():
        content = plan["content"]
        if plan["status"] == "Blocked":
            assert "## 阻塞与恢复" in content, filename
            for required in ("阻塞证据：", "恢复条件：", "责任位置：", "复核触发点："):
                assert required in content, f"{filename}: 缺少 {required}"
        else:
            assert "## 阻塞与恢复" not in content, filename


def test_plan_index_is_navigation_only_and_routes_status_to_todo():
    index = PLAN_INDEX.read_text(encoding="utf-8")
    assert "../trackers/todo.md" in index
    assert "| 状态 |" not in index
    for filename in _active_plans():
        assert filename not in index
