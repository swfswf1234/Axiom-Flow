"""
模块职责：守护流程设计、Mermaid 视图与当前代码契约保持同步。
设计关联（DesignRef）：docs/standards/code-document-traceability.md
实现状态：Current
被测代码：docs/design、API schema、任务、工作簿、Web 与评测常量
"""

import ast
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import get_args

from backend.api.schemas import PageResponse, ReviewRequest
from backend.domain.models import JobKind, JobStatus
from evaluation.scorecard import (
    DEFAULT_MAX_MODEL_CALLS,
    MINIMUM_AVERAGE_SCORE,
    REQUIRED_PAGE_COUNT,
    VALID_DIMENSIONS,
)

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs" / "design"
MERMAID_BLOCK = re.compile(r"```mermaid\s*\n(?P<body>.*?)```", re.DOTALL)
CURRENT_DOCUMENTS = {
    "document-pipeline.md",
    "excel-release-workflow.md",
    "background-jobs.md",
    "web-workbench.md",
    "evaluation-governance.md",
}


class _ViewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.views: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "button" and values.get("data-view"):
            self.views.add(str(values["data-view"]))


def _content(document: str) -> str:
    return (DESIGN / document).read_text(encoding="utf-8")


def _mermaid(document: str) -> list[str]:
    return [match.group("body") for match in MERMAID_BLOCK.finditer(_content(document))]


def _string_sets(path: Path) -> list[set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Set) and all(
            isinstance(element, ast.Constant) and isinstance(element.value, str)
            for element in node.elts
        ):
            values.append({str(element.value) for element in node.elts})
    return values


def test_design_directory_has_one_index_and_five_current_contracts():
    assert {path.name for path in DESIGN.glob("*.md")} == {"index.md", *CURRENT_DOCUMENTS}
    index = _content("index.md")
    for document in CURRENT_DOCUMENTS:
        assert f"]({document})" in index
    for removed in ("normalized-content.md", "quality-review.md", "knowledge-model.md"):
        assert removed not in index


def test_every_current_design_has_one_embedded_mermaid_view():
    for document in CURRENT_DOCUMENTS:
        assert len(_mermaid(document)) == 1, document


def test_document_pipeline_tracks_ports_page_fields_and_review_statuses():
    content = _content("document-pipeline.md")
    for port in ("VisionProvider", "KnowledgeProvider", "DocumentPipeline"):
        assert f"`{port}`" in content
    for field_name in PageResponse.model_fields:
        assert f"`{field_name}`" in content
    for status in get_args(ReviewRequest.model_fields["status"].annotation):
        assert f"`{status}`" in content


def test_background_job_design_tracks_domain_kinds_and_statuses():
    content = _content("background-jobs.md")
    for member in (*JobKind, *JobStatus):
        assert f"`{member.value}`" in content


def test_workbook_design_tracks_required_sheets_and_relation_types():
    source = ROOT / "backend" / "application" / "workbooks.py"
    string_sets = _string_sets(source)
    required_sheets = next(values for values in string_sets if "documents" in values)
    relations = next(values for values in string_sets if "RELATED_TO" in values)
    content = _content("excel-release-workflow.md")

    for sheet in required_sheets:
        assert f"`{sheet}`" in content
    for relation in relations:
        assert f"`{relation}`" in content


def test_web_design_tracks_exact_main_view_ids():
    parser = _ViewParser()
    parser.feed((ROOT / "web" / "index.html").read_text(encoding="utf-8"))
    assert parser.views == {"pages", "knowledge", "workbook", "graph"}
    content = _content("web-workbench.md")
    for view in parser.views:
        assert f"`{view}`" in content


def test_evaluation_design_tracks_scorecard_contract():
    content = _content("evaluation-governance.md")
    assert f"固定要求 {REQUIRED_PAGE_COUNT} 页" in content
    assert f"最大模型调用数为 {DEFAULT_MAX_MODEL_CALLS}" in content
    assert f"平均分至少为 {MINIMUM_AVERAGE_SCORE}" in content
    for dimension in VALID_DIMENSIONS:
        assert f"`{dimension}`" in content


def test_known_design_gaps_are_registered_in_contracts_and_todo():
    todo = (ROOT / "docs" / "trackers" / "todo.md").read_text(encoding="utf-8")
    assert "DES-001" in _content("document-pipeline.md") and "DES-001" in todo
    assert "DES-002" in _content("excel-release-workflow.md") and "DES-002" in todo
