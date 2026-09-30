"""
模块职责：验证任务 ID、活跃计划镜像（承载列单指针）、三列语义与列界（成功标准终态、承载列唯一
承载文档指针、文档链接不出现在其他列）、行内禁日期、并发上限、关闭证据和无状态路线图保持一致。
设计关联（DesignRef）：docs/standards/task-lifecycle.md、docs/adr/0006-ledger-plan-carrier-contract.md、
docs/adr/0013-ledger-row-three-elements.md
实现状态：Current
被测代码：docs/trackers、docs/plans
守护面：计划与任务治理
失效后果：任务 ID 复用、计划镜像断裂、任务无承载、台账行混入现状与日期化流水、成功标准退化为验收
流程措辞、并发超限
"""

import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRACKERS = ROOT / "docs" / "trackers"
PLANS = ROOT / "docs" / "plans"
TASK_ID = re.compile(r"[A-Z][A-Z0-9]*-\d{1,3}")
PLAN_LINK = re.compile(r"^\[(?P<title>[^]]+)]\(\.\./plans/(?P<path>[^)]+\.md)\)")
PLAN_REF = re.compile(r"\.\./plans/([^\s)]+\.md)")
ACTIVE_STATUSES = {"Accepted", "In Progress", "Blocked"}
TERMINAL_STATUSES = {"Completed", "Cancelled", "Superseded"}
CLOSE_RESULTS = {"Achieved", "Rejected", "Partial", "Not Applicable"}
TODO_COLUMNS = ("ID", "类别", "优先级", "状态", "任务", "成功标准", "承载")
SUCCESS_LIMIT = 80
ROW_COLUMNS = ("任务", "成功标准", "承载")
LINK_TARGET = re.compile(r"\]\([^)]*\)")
DATE_LIKE = re.compile(r"\d{4}-\d{2}-\d{2}")
MARKDOWN_LINK = re.compile(r"\[[^\]]*]\([^)]+\)")
POINTER_SEPARATORS = re.compile(r"[\s、,，；;]+")
POINTER_LINKS_EXACT = 1
SUCCESS_BANNED = ("门禁", "收口", "全绿", "成文")
SUCCESS_BANNED_RE = re.compile(r"S\d+~S\d+")
MAX_BRANCH_IN_PROGRESS = 3


def _rows_from(
    lines: list[str], header: str, columns: tuple[str, ...]
) -> list[dict[str, str]]:
    """收集文档中所有匹配表头的表格行（todo 按轮次/长期分表）。"""
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

    assert todo, "todo.md 缺少 ADR 0013 列契约的表头行"
    assert ids
    assert all(TASK_ID.fullmatch(task_id) for task_id in ids)
    assert len(ids) == len(set(ids))
    assert all(all(row.values()) for row in (*todo, *completed))


def test_todo_plan_rows_exactly_mirror_active_plan_bodies():
    plan_rows: dict[str, tuple[dict[str, str], str]] = {}
    for row in _todo_rows():
        if not row["ID"].startswith("PLAN-"):
            continue
        link = PLAN_LINK.match(row["承载"])
        assert link, f"{row['ID']}: 计划镜像行的「承载」列唯一指针必须指向 ../plans/ 下的壳"
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


def test_success_criteria_are_concrete_and_bounded():
    """ADR 0013：成功标准＝具体终态，≤80 字，禁验收流程措辞与壳内 S 判据区间引用。"""
    for row in _todo_rows():
        cell = row["成功标准"]
        assert len(cell) <= SUCCESS_LIMIT, f"{row['ID']}: {len(cell)} 字超界"
        hits = [word for word in SUCCESS_BANNED if word in cell]
        assert not hits, f"{row['ID']}: 成功标准含验收流程措辞 {hits}"
        assert not SUCCESS_BANNED_RE.search(cell), f"{row['ID']}: 成功标准引用壳内 S 判据区间"


def test_pointer_column_is_a_single_document_link():
    """ADR 0013：「承载」列＝恰好一个文档指针且无自然语言；文档链接不得出现在「任务／成功标准」列。"""
    for row in _todo_rows():
        for column in ("任务", "成功标准"):
            stray = MARKDOWN_LINK.findall(row[column])
            assert not stray, f"{row['ID']}: {column} 列含文档链接 {stray}（链接只登记在「承载」列）"
        cell = row["承载"]
        links = MARKDOWN_LINK.findall(cell)
        assert links, f"{row['ID']}: 承载列缺文档指针"
        assert len(links) == POINTER_LINKS_EXACT, (
            f"{row['ID']}: 承载列指针数 {len(links)}，一任务只有一个承载文档"
        )
        residual = POINTER_SEPARATORS.sub("", MARKDOWN_LINK.sub("", cell))
        assert not residual, f"{row['ID']}: 承载列混入自然语言残余 {residual!r}"


def test_rows_are_date_free_and_carrier_reference_registered():
    """ADR 0013：三列剥链接目标后禁 ISO 日期；ADR 0006：支线行的「承载」列唯一指针必须指向已登记本行的计划。"""
    for row in _todo_rows():
        for column in ROW_COLUMNS:
            stripped = LINK_TARGET.sub("]", row[column])
            assert not DATE_LIKE.search(stripped), f"{row['ID']}: {column} 列含日期字面量"
        if row["类别"] != "支线":
            continue
        refs = PLAN_REF.findall(row["承载"])
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
    """主线聚合行（ID 非 PLAN- 前缀）必须在「任务」列列全支线行 ID。"""
    branches = [row["ID"] for row in _todo_rows() if row["类别"] == "支线"]
    aggregates = [
        row
        for row in _todo_rows()
        if row["类别"] == "主线" and not row["ID"].startswith("PLAN-")
    ]
    assert aggregates
    listed = _expand_ids(" ".join(row["任务"] for row in aggregates))
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
