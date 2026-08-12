"""
模块职责：验证跨项目请求的承接登记、回执证据和设计文档模板保持一致。
设计关联（DesignRef）：docs/standards/cross-project-collaboration.md
实现状态：Current
被测代码：docs/trackers/todo.md、docs/design
守护面：跨项目协作
失效后果：ALN 承接行缺失发起方标注或跨项目设计文档缺模板字段时，协作流程发生漂移
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TODO = ROOT / "docs" / "trackers" / "todo.md"
DESIGN = ROOT / "docs" / "design"
TEMPLATE_FIELDS = ("需求方", "执行方", "接口面", "评审方", "验收标准")


def _todo_rows() -> list[dict[str, str]]:
    lines = TODO.read_text(encoding="utf-8").splitlines()
    header = "| ID | 类型 | 优先级 | 状态 | 任务 | 证据/下一条件 |"
    start = lines.index(header)
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 6, line
        rows.append(
            dict(zip(("ID", "类型", "优先级", "状态", "任务", "证据"), cells, strict=True))
        )
    return rows


def test_cross_project_request_rows_are_marked_with_root_initiator():
    rows = [row for row in _todo_rows() if row["类型"] == "Request" and row["ID"].startswith("ALN-")]
    assert rows
    for row in rows:
        combined = row["任务"] + row["证据"]
        assert "QED-Engine 发起" in combined, f"{row['ID']} 缺少发起方标注"


def test_cross_project_design_documents_declare_template_fields():
    flagged = [path for path in DESIGN.glob("*.md") if "需求方" in path.read_text(encoding="utf-8")]
    assert flagged
    for path in flagged:
        content = path.read_text(encoding="utf-8")
        for field in TEMPLATE_FIELDS:
            assert f"{field}：" in content, f"{path.name}: 缺少模板字段 {field}"
