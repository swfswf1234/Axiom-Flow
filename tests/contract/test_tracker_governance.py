"""
模块职责：验证任务 ID、活跃计划镜像、承载引用、列长与并发上限、关闭证据和无状态路线图保持一致。
设计关联（DesignRef）：docs/standards/task-lifecycle.md、docs/adr/0006-ledger-plan-carrier-contract.md
实现状态：Current
被测代码：docs/trackers、docs/plans
守护面：计划与任务治理
失效后果：任务 ID 复用、计划镜像断裂、任务无承载或台账进度膨胀、并发超限
"""

import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKERS = ROOT / "docs" / "trackers"
PLANS = ROOT / "docs" / "plans"
TASK_ID = re.compile(r"[A-Z][A-Z0-9]*-\d{1,3}")
PLAN_LINK = re.compile(r"^\[(?P<title>[^]]+)]\(\.\./plans/(?P<path>[^)]+\.md)\)$")
ACTIVE_STATUSES = {"Accepted", "In Progress", "Blocked"}
TERMINAL_STATUSES = {"Completed", "Cancelled", "Superseded"}
CLOSE_RESULTS = {"Achieved", "Rejected", "Partial", "Not Applicable"}
TODO_COLUMNS = ("ID", "类别", "优先级", "状态", "任务", "关闭条件", "证据/下一条件")
PLAN_REF = re.compile(r"\.\./plans/([^\s)]+\.md)")
EVIDENCE_LIMIT = 200
CLOSE_CONDITION_LIMIT = 80
MAX_BRANCH_IN_PROGRESS = 3


def _rows_from(
    lines: list[str], header: str, columns: tuple[str, ...]
) -> list[dict[str, str]]:
    """收集文档中所有匹配表头的表格行（todo 按主线/治理/长期分表）。"""
    rows: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        if line != header:
            continue
        for row_line in lines[index + 2 :]:
            if not row_line.startswith("|"):
                break
            cells = [cell.strip() for cell in row_line.strip().strip("|").split("|")]
            assert len(cells) == len(columns), row_line
            rows.append(dict(zip(columns, cells, strict=True)))
    return rows


def _table(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = f"| {' | '.join(columns)} |"
    return _rows_from(lines, header, columns)


def _field(content: str, name: str) -> str:
    matches = re.findall(rf"^{re.escape(name)}：(.*)$", content, re.MULTILINE)
    assert len(matches) == 1, f"{name} 必须恰好出现一次"
    return matches[0].strip()


def _todo_rows() -> list[dict[str, str]]:
    return _table(TRACKERS / "todo.md", TODO_COLUMNS)


def _completed_rows() -> list[dict[str, str]]:
    return _table(
        TRACKERS / "completed.md",
        ("日期", "ID", "任务", "终态", "关闭结果", "证据"),
    )


def test_tracker_directory_has_exact_governed_files_and_index_links():
    assert {path.name for path in TRACKERS.glob("*.md")} == {
        "index.md",
        "roadmap.md",
        "project-status.md",
        "todo.md",
        "completed.md",
    }
    index = (TRACKERS / "index.md").read_text(encoding="utf-8")
    for filename in ("roadmap.md", "project-status.md", "todo.md", "completed.md"):
        assert f"]({filename})" in index


def test_project_status_snapshot_declares_metadata():
    content = (TRACKERS / "project-status.md").read_text(encoding="utf-8")
    for field in ("设计状态：", "实现状态：", "最后更新：", "关联代码：", "关联测试：", "关联 ADR："):
        assert field in content, field


def test_task_ids_are_stable_unique_and_rows_are_complete():
    todo = _todo_rows()
    completed = _completed_rows()
    ids = [row["ID"] for row in (*todo, *completed)]

    assert ids
    assert all(TASK_ID.fullmatch(task_id) for task_id in ids)
    assert len(ids) == len(set(ids))
    assert all(all(row.values()) for row in (*todo, *completed))


def test_todo_plan_rows_exactly_mirror_active_plan_bodies():
    plan_rows: dict[str, tuple[dict[str, str], str]] = {}
    for row in _todo_rows():
        link = PLAN_LINK.fullmatch(row["任务"])
        if link is None:
            continue
        assert link.group("path") not in plan_rows, row["ID"]
        plan_rows[link.group("path")] = (row, link.group("title"))

    plan_files = {path.name: path for path in PLANS.glob("*.md") if path.name != "index.md"}
    assert set(plan_rows) == set(plan_files)
    for filename, path in plan_files.items():
        content = path.read_text(encoding="utf-8")
        row, linked_title = plan_rows[filename]
        assert linked_title == content.splitlines()[0].removeprefix("# ")
        assert row["状态"] == _field(content, "状态")
        assert row["状态"] in ACTIVE_STATUSES
        tracker = _field(content, "关联 Tracker")
        assert "docs/trackers/todo.md" in tracker
        assert row["ID"] in tracker


def _expand_ids(text: str) -> set[str]:
    """把「ARCH-003/004/016」这类斜杠列表展开为完整 ID 集合（ADR 0006 承载/聚合判定用）。"""
    ids = set(TASK_ID.findall(text))
    for match in re.finditer(r"([A-Z][A-Z0-9]*)-(\d+(?:/\d+)+)", text):
        prefix, numbers = match.group(1), match.group(2).split("/")
        ids.update(f"{prefix}-{number}" for number in numbers)
    return ids


def test_rows_declare_bounded_columns_and_carrier_reference():
    """ADR 0006：关闭条件/证据列有界；支线行必须有计划承载（关联 Tracker 登记或承载节）。"""
    for row in _todo_rows():
        assert len(row["关闭条件"]) <= CLOSE_CONDITION_LIMIT, row["ID"]
        assert len(row["证据/下一条件"]) <= EVIDENCE_LIMIT, row["ID"]
        if row["类别"] in {"主线", "长期"}:
            continue
        cell = f"{row['任务']} {row['证据/下一条件']}"
        refs = PLAN_REF.findall(cell)
        assert refs, f"{row['ID']}: 支线行缺 ../plans/ 承载引用"
        carried = False
        for ref in refs:
            path = PLANS / ref
            assert path.exists(), f"{row['ID']}: 承载计划不存在 {ref}"
            content = path.read_text(encoding="utf-8")
            tracker_ids = _expand_ids(_field(content, "关联 Tracker"))
            if row["ID"] in tracker_ids or re.search(
                rf"^## 承载 {re.escape(row['ID'])}", content, re.MULTILINE
            ):
                carried = True
        assert carried, f"{row['ID']}: 承载计划未登记本行（关联 Tracker 或 ## 承载 节）"


def test_branch_in_progress_within_concurrency_limit():
    """ADR 0006：支线行同时 In Progress ≤ 3（主线/长期行不计）。"""
    rows = [row for row in _todo_rows() if row["类别"] == "支线"]
    in_progress = [row["ID"] for row in rows if row["状态"] == "In Progress"]
    assert len(in_progress) <= MAX_BRANCH_IN_PROGRESS, in_progress


def test_main_aggregate_rows_list_branch_children():
    """主线聚合行（非计划链接任务）必须列全支线行 ID。"""
    branches = [row["ID"] for row in _todo_rows() if row["类别"] == "支线"]
    aggregates = [
        row
        for row in _todo_rows()
        if row["类别"] == "主线" and PLAN_LINK.fullmatch(row["任务"]) is None
    ]
    assert aggregates
    listed = _expand_ids(
        " ".join(f"{row['任务']} {row['证据/下一条件']}" for row in aggregates)
    )
    for task_id in branches:
        assert task_id in listed, task_id


def test_completed_rows_have_terminal_state_result_date_and_evidence():
    for row in _completed_rows():
        date.fromisoformat(row["日期"])
        assert row["终态"] in TERMINAL_STATUSES
        assert row["关闭结果"] in CLOSE_RESULTS
        assert row["证据"] not in {"—", "-"}


def test_roadmap_is_status_free_and_references_registered_tasks():
    roadmap = TRACKERS / "roadmap.md"
    columns = ("里程碑", "能力目标", "前置里程碑", "退出能力", "关联任务")
    rows = _table(roadmap, columns)
    registered = {row["ID"] for row in (*_todo_rows(), *_completed_rows())}

    assert rows
    assert "| 状态 |" not in roadmap.read_text(encoding="utf-8")
    for row in rows:
        task_ids = TASK_ID.findall(row["关联任务"])
        assert task_ids, row["里程碑"]
        assert set(task_ids) <= registered
