"""
模块职责：守护架构正文与 Mermaid 视图保持同步。
设计关联（DesignRef）：docs/standards/doc-governance.md
实现状态：Current
被测代码：docs/architecture
守护面：架构文档治理
失效后果：架构正文/Mermaid 与实现漂移时偏差未被发现
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = ROOT / "docs" / "architecture"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)
CURRENT_DOCUMENTS = {
    "overview.md",
    "api.md",
    "database-design.md",
    "code-map.md",
}
# 体裁规则见 docs/adr/0008-fixed-doc-form-and-metadata-genres.md：`code-map.md` 属映射与索引类（只声明
# `状态`）、`index.md` 属目录索引类，两者都不声明 `实现状态`；本守护只覆盖声明 `实现状态` 的架构正文。
# 取值事实源：docs/standards/doc-governance.md「元数据」节
STATUS_DOCUMENTS = {"overview.md", "api.md", "database-design.md"}
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
    lines = (ARCHITECTURE / document).read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("实现状态："))
    following = lines[index + 1] if index + 1 < len(lines) else ""
    assert not following.strip() or following.split("：")[0].strip() in METADATA_KEYS, (
        f"{document}: 实现状态换行续写（日期化过程应写 trackers/completed.md）"
    )
    return lines[index].removeprefix("实现状态：").strip()


def test_architecture_metadata_declares_legal_bounded_status():
    """R1：`实现状态` 取值属合法枚举，且单行 ≤60 字。"""
    for document in STATUS_DOCUMENTS:
        value = _status_line(document)
        assert len(value) <= STATUS_LINE_LIMIT, f"{document}: 实现状态 {len(value)} 字"
        assert value.split("（")[0].strip() in IMPLEMENTATION_STATUSES, f"{document}: {value!r}"


def _mermaid(document: str) -> list[str]:
    content = (ARCHITECTURE / document).read_text(encoding="utf-8")
    return [match.group("body") for match in MERMAID_BLOCK.finditer(content)]


def test_architecture_directory_has_one_index_and_two_current_documents():
    assert {path.name for path in ARCHITECTURE.glob("*.md")} == {
        "index.md",
        *CURRENT_DOCUMENTS,
    }
    index = (ARCHITECTURE / "index.md").read_text(encoding="utf-8")
    for document in CURRENT_DOCUMENTS:
        assert f"]({document})" in index


def test_system_context_diagram_names_boundary_inputs_and_outputs():
    diagrams = _mermaid("overview.md")
    assert len(diagrams) == 1
    for label in ("QED-Engine", "8900 网关", "MinerU", "PaddleOCR-VL", "parsed"):
        assert label in diagrams[0]


def test_code_map_lists_v2_modules_with_design_references():
    content = (ARCHITECTURE / "code-map.md").read_text(encoding="utf-8")
    for module in (
        "src/axiom_flow/api/",
        "src/axiom_flow/ingest/",
        "src/axiom_flow/orchestrator/",
        "src/axiom_flow/engines/",
        "src/axiom_flow/schemas.py",
    ):
        assert f"`{module}`" in content, module
    assert "docs/design/parsing-pipeline.md" in content


def test_api_document_declares_interface_categories():
    """四类公共契约面 + 非公开管理面必须成类可查；**端点清单不在本守护硬编码**——
    它由 `test_fixed_document_form.py` 与运行时路由表双向对账（钉字符串会让清单两头漂移）。"""
    content = (ARCHITECTURE / "api.md").read_text(encoding="utf-8")
    for heading in ("生命周期与健康", "数据查询", "解析结果与 PDF 对照", "RAG·知识图谱预留"):
        assert heading in content, heading
    for section in (
        "### ① 生命周期与健康\n",
        "### ② 数据查询（af_*）\n",
        "### ③ 解析结果与 PDF 对照\n",
        "### ④ 未来 RAG·知识图谱预留（无端点）\n",
        "### 非公开管理面（af_* 与产物一键清除）\n",
    ):
        assert section in content, section
    assert "src/axiom_flow/schemas.py" in content


def test_database_design_document_declares_af_namespace_and_tables():
    content = (ARCHITECTURE / "database-design.md").read_text(encoding="utf-8")
    for table in ("af_books", "af_parse_jobs", "af_pages", "af_block_edits"):
        assert table in content, table
    assert "af_*" in content
    assert "qed" in content


def test_architecture_documents_declare_confirmation_status():
    for document in CURRENT_DOCUMENTS:
        content = (ARCHITECTURE / document).read_text(encoding="utf-8")
        assert "确认状态：" in content, document