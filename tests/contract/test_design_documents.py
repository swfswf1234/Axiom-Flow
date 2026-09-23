"""
模块职责：守护设计文档集合、Mermaid 视图和元数据保持同步。
设计关联（DesignRef）：docs/standards/doc-governance.md
实现状态：Current
被测代码：docs/design
守护面：设计文档治理
失效后果：设计正文/Mermaid/元数据与实现漂移时偏差未被发现
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "docs" / "design"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)
CURRENT_DOCUMENTS = {
    "parsing-pipeline.md",
    "parsing-management.md",
    "model-integration.md",
}
# 取值事实源：docs/standards/doc-governance.md「元数据」节
IMPLEMENTATION_STATUSES = {
    "Not Started",
    "In Progress",
    "Implemented",
    "Verified",
    "Blocked",
    "Completed",
}
STATUS_LINE_LIMIT = 60
METADATA_KEYS = (
    "设计状态",
    "确认状态",
    "实现状态",
    "最后更新",
    "关联代码",
    "关联测试",
    "关联 ADR",
    "关联 Tracker",
)


def _status_line(document: str) -> str:
    """取 `实现状态` 一行，并断言它**没有换行续写**（轮次历史不得写进元数据）。"""
    lines = (DESIGN / document).read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("实现状态："))
    following = lines[index + 1] if index + 1 < len(lines) else ""
    assert not following.strip() or following.split("：")[0].strip() in METADATA_KEYS, (
        f"{document}: 实现状态换行续写（日期化过程应写 trackers/completed.md）"
    )
    return lines[index].removeprefix("实现状态：").strip()


def test_design_metadata_declares_legal_bounded_status():
    """R1：`实现状态` 取值属合法枚举，且单行 ≤60 字。"""
    for document in CURRENT_DOCUMENTS:
        value = _status_line(document)
        assert len(value) <= STATUS_LINE_LIMIT, f"{document}: 实现状态 {len(value)} 字"
        assert value.split("（")[0].strip() in IMPLEMENTATION_STATUSES, f"{document}: {value!r}"


def _mermaid(document: str) -> list[str]:
    content = (DESIGN / document).read_text(encoding="utf-8")
    return [match.group("body") for match in MERMAID_BLOCK.finditer(content)]


def test_design_directory_has_one_index_and_one_current_contract():
    assert {path.name for path in DESIGN.glob("*.md")} == {"index.md", *CURRENT_DOCUMENTS}
    index = (DESIGN / "index.md").read_text(encoding="utf-8")
    for document in CURRENT_DOCUMENTS:
        assert f"]({document})" in index


# 叙述框架（PLAN-012）：三篇同构的六段骨架，H2 顺序固定、白名单穷举。
SECTION_SKELETON = ("目标与非目标", "边界与事实源", "核心设计", "失败与质量语义", "验收判据", "追溯")
# 核心设计内的守则段统一名（2026-09-23 呈现轮：由「不变式与条文」→「已确定守则」→「确定规范」，
# 并把篇末「条文表」并入同一张表）。
CLAUSE_SECTION = "确定规范"
# R2：条文编号必须在本篇内解析（`PP-n`/`PM-n`/`MI-n`），跨壳引用一律降级为「来源：PLAN-0xx（已归档）」。
CROSS_SHELL_CLAUSE = re.compile(r"PLAN-\d+[^\n。]{0,16}条文")
# R3：不做第二份契约表——列名清单与响应字段清单的事实源在 `architecture/`。
# 必须带 MULTILINE：表头行出现在文档中部，缺该标志时 `^` 只锚定文首，守护会**恒不命中**。
CONTRACT_TABLE = re.compile(r"^\| *(字段|列|响应字段|参数) *\|", re.MULTILINE)


def _h2_headings(document: str) -> list[str]:
    content = (DESIGN / document).read_text(encoding="utf-8")
    return re.findall(r"^## (.+?)\s*$", content, re.MULTILINE)


def test_design_documents_follow_six_section_skeleton():
    """S1：三篇 H2 逐字等于六段骨架（顺序与集合都固定，小节层级不越界成 H2）。"""
    for document in CURRENT_DOCUMENTS:
        assert _h2_headings(document) == list(SECTION_SKELETON), document


def test_design_clauses_resolve_inside_their_own_document():
    """R2：design 正文不出现 `PLAN-0xx … 条文` 形式的跨壳编号。"""
    for document in CURRENT_DOCUMENTS:
        content = (DESIGN / document).read_text(encoding="utf-8")
        matched = CROSS_SHELL_CLAUSE.search(content)
        assert matched is None, f"{document}: 跨壳条文编号 {matched.group(0)!r}"


def test_design_documents_declare_numbered_clauses_with_carriers():
    """S3（表格化后）：守则表逐行给出「结论 ＋ 判据 / 理由／例外／边界 / 实现落点 → 守护测试」四列。

    编号在篇内唯一、从 1 起**按序连续**；结论必须以加粗一行开头；落点与守护合并在末列，
    故该列必须含 `→` 分隔且两侧非空（原「条文表」并入本表后不再有第二处登记表）。
    """
    carriers = {
        "PM": "parsing-management.md",
        "PP": "parsing-pipeline.md",
        "MI": "model-integration.md",
    }
    for prefix, document in carriers.items():
        content = (DESIGN / document).read_text(encoding="utf-8")
        assert f"### {CLAUSE_SECTION}" in content, f"{document}: 缺守则段标题"
        assert "### 条文表" not in content, f"{document}: 条文表应已并入守则表"
        rows = re.findall(rf"^\| {prefix}-(\d+) \|(.+)$", content, re.MULTILINE)
        listed = [number for number, _ in rows]
        assert listed == [str(n) for n in range(1, len(listed) + 1)], f"{document}: 编号 {listed} 不按序连续"
        for number, tail in rows:
            cells = [cell.strip() for cell in tail.strip().rstrip("|").split("|")]
            assert len(cells) == 3, f"{document}: {prefix}-{number} 列数不是「结论＋判据 / 理由 / 落点→守护」"
            clause, _, carrier = cells
            assert clause.startswith("**"), f"{document}: {prefix}-{number} 缺加粗结论"
            assert carrier and carrier != "—", f"{document}: {prefix}-{number} 缺实现落点"
            left, sep, right = carrier.partition(" → ")
            assert sep and left.strip() and right.strip(), f"{document}: {prefix}-{number} 落点与守护未用 → 分列"



def test_design_documents_hold_no_second_contract_table():
    """R4（框架 R3）：design 不承载表结构/端点响应字段清单，只写语义 + 指向 `architecture/` 的链接。"""
    for document in CURRENT_DOCUMENTS:
        content = (DESIGN / document).read_text(encoding="utf-8")
        matched = CONTRACT_TABLE.search(content)
        assert matched is None, f"{document}: 出现第二份契约表 {matched.group(0)!r}"


def test_every_current_design_has_one_embedded_mermaid_view():
    for document in CURRENT_DOCUMENTS:
        assert len(_mermaid(document)) == 1, document


def test_active_design_documents_declare_metadata():
    for document in CURRENT_DOCUMENTS:
        content = (DESIGN / document).read_text(encoding="utf-8")
        for field in ("设计状态：", "实现状态：", "最后更新：", "关联代码：", "关联测试：", "关联 ADR："):
            assert field in content, f"{document}: 缺少 {field}"


def test_active_design_documents_declare_confirmation_status():
    for document in CURRENT_DOCUMENTS:
        content = (DESIGN / document).read_text(encoding="utf-8")
        assert "确认状态：" in content, document