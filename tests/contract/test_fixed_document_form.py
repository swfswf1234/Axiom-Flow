"""
模块职责：守护固定文档的体裁化元数据、按体裁的骨架白名单、正文去日期化，以及契约表与运行态真源的双向对账。
设计关联（DesignRef）：docs/adr/0008-fixed-doc-form-and-metadata-genres.md、
docs/adr/0017-database-design-eight-chapter-skeleton.md、
docs/standards/doc-governance.md「元数据」「固定文档骨架与可对账契约」
实现状态：Current
被测代码：docs/architecture/、docs/design/、src/axiom_flow/api/、src/axiom_flow/db/models.py
守护面：固定文档形式与契约对账
失效后果：固定文档体裁漂移、正文混入台账式过程叙述，或契约表与代码脱节时无人发现
"""

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from axiom_flow.api.main import create_app
from axiom_flow.config import load_settings
from axiom_flow.db.models import AF_TABLES, Base

ROOT = Path(__file__).resolve().parents[2]
ARCHITECTURE = ROOT / "docs" / "architecture"
DESIGN = ROOT / "docs" / "design"

CONTRACT_DOCUMENTS = ("overview.md", "api.md", "database-design.md")
MAPPING_DOCUMENT = "code-map.md"
DATABASE_DESIGN_DOCUMENT = "database-design.md"
FOUR_SECTION_DOCUMENTS = ("overview.md", "api.md", MAPPING_DOCUMENT)
DESIGN_DOCUMENTS = (
    "parsing-pipeline.md",
    "parsing-management.md",
    "model-integration.md",
    "knowledge-analysis.md",
)
INDEX_DOCUMENTS = (ARCHITECTURE / "index.md", DESIGN / "index.md")

SKELETON = ("目的与边界", "当前契约", "失败与兼容语义", "验证与追溯")
EIGHT_CHAPTER_SKELETON = (
    "一、前言",
    "二、遵循的规则",
    "三、本项目表总览",
    "四、整体架构（本项目所需部分）",
    "五、详细设计",
    "六、与 QED-Engine 的交互与同步契约",
    "七、变更与运维",
    "八、守护与验证",
)
TRACE_HEADING = "## 验证与追溯"
DESIGN_TRACE_HEADING = "## 追溯"
DATABASE_DESIGN_TRACE_HEADING = "## 八、守护与验证"

CONTRACT_METADATA = ("设计状态", "确认状态", "实现状态", "最后更新", "关联代码", "关联测试", "关联 ADR")
MAPPING_METADATA = ("状态", "确认状态", "最后更新", "覆盖范围")

PROCESS_REFERENCE = re.compile(r"\b(?:REQ|PLAN|ARCH|DEFECT)-\d")
ABSOLUTE_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b")
H2 = re.compile(r"^## (.+?)\s*$", re.M)
TABLE_ROW = re.compile(r"^\|(.+)\|$")
ENDPOINT_SECTION = re.compile(r"^#### (GET|POST|PUT|PATCH|DELETE) (\S+)\s*$", re.M)
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
INDEX_STATUS_LINE = re.compile(r"^状态：(?:Current|Superseded)\s*$", re.M)
INDEX_DATE_LINE = re.compile(r"^最后更新：20\d\d-\d\d-\d\d\s*$", re.M)
INDEX_METADATA_PREFIXES = ("状态：", "最后更新：")


def _text(document: Path) -> str:
    return document.read_text(encoding="utf-8")


def _metadata_block(document: Path) -> list[str]:
    """首个 H2 之前的元数据行（形如 `键：值`）。"""
    block: list[str] = []
    for line in _text(document).splitlines():
        if line.startswith("## "):
            break
        if "：" in line and not line.startswith(("#", ">", "-")):
            block.append(line.split("：", 1)[0].strip())
    return block


def _contract_body(document: Path) -> str:
    """契约正文段：首个 H2 起、到末段追溯段之前（追溯段允许归档壳式来源引用）。

    追溯段的标题按体裁取——数据库设计篇用八章体的末章，其余取四段骨架的末段。
    """
    trace_headings = (
        (DATABASE_DESIGN_TRACE_HEADING,)
        if document.name == DATABASE_DESIGN_DOCUMENT
        else (TRACE_HEADING, DESIGN_TRACE_HEADING)
    )
    lines = _text(document).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("## "))
    trace = next(
        (i for i in range(start, len(lines)) if lines[i] in trace_headings),
        len(lines),
    )
    return "\n".join(lines[start:trace])


# ---------------------------------------------------------------- 体裁元数据


@pytest.mark.parametrize("name", CONTRACT_DOCUMENTS)
def test_contract_documents_declare_contract_genre_metadata(name: str) -> None:
    """契约正文类的元数据字段集合固定，且不得混入映射类的 `状态：` 字段。"""
    keys = _metadata_block(ARCHITECTURE / name)
    assert tuple(keys) == CONTRACT_METADATA, f"{name}: 元数据字段 {keys}"
    body = _text(ARCHITECTURE / name)
    assert "\n状态：" not in body, f"{name}: 契约正文类不得用映射类的 `状态`"


def test_mapping_document_declares_mapping_genre_metadata() -> None:
    """映射与索引类（code-map）只声明映射体裁字段，不得声明设计/实现状态。"""
    document = ARCHITECTURE / MAPPING_DOCUMENT
    assert tuple(_metadata_block(document)) == MAPPING_METADATA, _metadata_block(document)
    body = _text(document)
    for forbidden in ("设计状态：", "实现状态："):
        assert forbidden not in body, f"{MAPPING_DOCUMENT}: 映射表不得声明 {forbidden}"
    assert re.search(r"^状态：(?:Current|Superseded)\s*$", body, re.M), "映射表状态取值受限"


# ---------------------------------------------------------------- 骨架白名单


@pytest.mark.parametrize("name", FOUR_SECTION_DOCUMENTS)
def test_architecture_documents_follow_four_section_skeleton(name: str) -> None:
    headings = tuple(H2.findall(_text(ARCHITECTURE / name)))
    assert headings == SKELETON, f"{name}: H2 = {headings}"


def test_database_design_document_follows_eight_chapter_skeleton() -> None:
    """数据库设计篇按章与根侧总纲对读，故改用八章编号体（ADR 0017 体裁分治）。"""
    headings = tuple(H2.findall(_text(ARCHITECTURE / DATABASE_DESIGN_DOCUMENT)))
    assert headings == EIGHT_CHAPTER_SKELETON, f"H2 = {headings}"


# ---------------------------------------------------------------- 正文去日期化


@pytest.mark.parametrize("name", (*CONTRACT_DOCUMENTS, MAPPING_DOCUMENT))
def test_architecture_body_carries_no_process_narrative(name: str) -> None:
    """契约正文段不得出现任务编号与具体日期——过程叙述的事实源是 trackers 与承载计划。"""
    body = _contract_body(ARCHITECTURE / name)
    offenders = [
        line.strip()[:80]
        for line in body.splitlines()
        if PROCESS_REFERENCE.search(line) or ABSOLUTE_DATE.search(line)
    ]
    assert not offenders, f"{name}: 正文含过程叙述\n" + "\n".join(offenders)


@pytest.mark.parametrize("name", DESIGN_DOCUMENTS)
def test_design_body_carries_no_process_narrative(name: str) -> None:
    body = _contract_body(DESIGN / name)
    offenders = [
        line.strip()[:80]
        for line in body.splitlines()
        if PROCESS_REFERENCE.search(line) or ABSOLUTE_DATE.search(line)
    ]
    assert not offenders, f"{name}: 正文含过程叙述\n" + "\n".join(offenders)


# ---------------------------------------------------------------- 目录索引体裁


@pytest.mark.parametrize("document", INDEX_DOCUMENTS)
def test_index_documents_declare_navigation_only_metadata(document: Path) -> None:
    """目录索引类只声明 `状态` + `最后更新`，不得混入契约正文类或映射类的状态字段。"""
    body = _text(document)
    for key in ("设计状态：", "实现状态：", "确认状态："):
        assert not re.search(rf"^{key}", body, re.M), f"{document.name}: 目录索引类不得声明 {key}"
    assert len(INDEX_STATUS_LINE.findall(body)) == 1, f"{document.name}: 状态行缺失或取值越界"
    assert len(INDEX_DATE_LINE.findall(body)) == 1, f"{document.name}: 最后更新行缺失或重复"


@pytest.mark.parametrize("document", INDEX_DOCUMENTS)
def test_index_documents_navigate_without_process_narrative(document: Path) -> None:
    """索引承载导航事实（文档 → 唯一问题），不承载任务编号与日期化过程。"""
    lines = [
        line
        for line in _text(document).splitlines()
        if not line.startswith(INDEX_METADATA_PREFIXES)
    ]
    offenders = [
        line.strip()[:80]
        for line in lines
        if PROCESS_REFERENCE.search(line) or ABSOLUTE_DATE.search(line)
    ]
    assert not offenders, f"{document.name}: 索引含过程叙述\n" + "\n".join(offenders)


# ---------------------------------------------------------------- 端点对账


@pytest.fixture(scope="module")
def runtime_routes(tmp_path_factory: pytest.TempPathFactory) -> set[tuple[str, str]]:
    """运行态路由真源：管理面按启用态装配（文档对账需要看到全部端点）。"""
    settings = load_settings(
        data_dir=tmp_path_factory.mktemp("form-reconcile"),
        admin_enabled=True,
        admin_token="contract-reconcile-token",
    )
    db_engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(db_engine)
    app = create_app(
        settings,
        session_factory=sessionmaker(bind=db_engine, future=True),
        engine_factory=lambda name: None,
        background_jobs=False,
    )
    return _routes_of(app)


def _routes_of(app: FastAPI) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods & HTTP_METHODS:
            routes.add((method, route.path))
    return routes


def _overview_rows() -> list[tuple[str, str]]:
    lines = _text(ARCHITECTURE / "api.md").splitlines()
    start = next(i for i, line in enumerate(lines) if line == "### 端点总表")
    rows: list[tuple[str, str]] = []
    for line in lines[start + 1 :]:
        if line.startswith("### ") or line.startswith("## "):
            break
        match = TABLE_ROW.match(line.strip())
        if not match:
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        method, path = cells[0].strip("`"), cells[1].strip("`")
        if method in HTTP_METHODS:
            rows.append((method, path))
    return rows


def test_api_endpoint_table_matches_runtime_routes(runtime_routes: set[tuple[str, str]]) -> None:
    """公共契约总表 ↔ 运行时路由双向对账：多一行是虚报，少一行是漏登。"""
    table = set(_overview_rows())
    public = {route for route in runtime_routes if not route[1].startswith("/api/v1/admin")}
    assert table, "端点总表未解析到任何行"
    assert table == public, (
        f"总表多出（虚报）：{sorted(table - public)}；总表缺失（漏登）：{sorted(public - table)}"
    )
    assert len(list(_overview_rows())) == len(table), "端点总表存在重复行"


def test_api_endpoint_sections_match_table() -> None:
    """总表每一行都要有同名端点小节；端点小节不得游离在总表之外（管理面单独登记）。"""
    document = _text(ARCHITECTURE / "api.md")
    sections = set(ENDPOINT_SECTION.findall(document))
    table = set(_overview_rows())
    missing = table - sections
    assert not missing, f"总表端点缺小节：{sorted(missing)}"
    public_sections = {(method, path) for method, path in sections if "/admin/" not in path}
    assert public_sections == table, (
        f"公共端点小节与总表不一致：多出 {sorted(public_sections - table)}，"
        f"缺失 {sorted(table - public_sections)}"
    )
    assert ("POST", "/api/v1/admin/purge") in sections, "管理面端点须单独成节"


def test_admin_endpoint_absent_from_public_overview() -> None:
    """管理面不进公共契约总表——总表是「8900/8903 可消费面」的对账入口。"""
    assert not [path for _, path in _overview_rows() if path.startswith("/api/v1/admin")]


# ---------------------------------------------------------------- 列清单对账


def _column_rows(table_name: str) -> dict[str, tuple[str, str, str]]:
    """取 `### <table> 列清单` 表：列名 → (类型, 可空, 键)。"""
    lines = _text(ARCHITECTURE / "database-design.md").splitlines()
    start = next(i for i, line in enumerate(lines) if line == f"### {table_name} 列清单")
    rows: dict[str, tuple[str, str, str]] = {}
    for line in lines[start + 1 :]:
        if line.startswith("### ") or line.startswith("## "):
            break
        match = TABLE_ROW.match(line.strip())
        if not match:
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if len(cells) < 4 or not cells[0].startswith("`"):
            continue
        rows[cells[0].strip("`")] = (cells[1], cells[2], cells[3])
    return rows


@pytest.mark.parametrize("table_name", [table.name for table in AF_TABLES])
def test_column_list_matches_orm_columns(table_name: str) -> None:
    """列清单 ↔ SQLAlchemy 列双向对账，并核对可空性与主键标记与 VARCHAR 长度。"""
    table = next(t for t in AF_TABLES if t.name == table_name)
    rows = _column_rows(table_name)
    assert rows, f"{table_name}: 未解析到列清单"
    assert set(rows) == {column.name for column in table.columns}, (
        f"{table_name}: 文档多出 {sorted(set(rows) - {c.name for c in table.columns})}，"
        f"文档缺失 {sorted({c.name for c in table.columns} - set(rows))}"
    )
    for column in table.columns:
        declared_type, nullable, key = rows[column.name]
        assert (nullable == "是") == bool(column.nullable), f"{table_name}.{column.name}: 可空性"
        assert (key == "PK") == bool(column.primary_key), f"{table_name}.{column.name}: 主键标记"
        length = getattr(column.type, "length", None)
        if length is not None:
            assert declared_type == f"VARCHAR({length})", (
                f"{table_name}.{column.name}: 类型 {declared_type} ≠ VARCHAR({length})"
            )


@pytest.mark.parametrize("table_name", [table.name for table in AF_TABLES])
def test_table_constraints_and_indexes_are_documented(table_name: str) -> None:
    """主键 / 唯一键 / 索引名必须在表所在小节出现（列清单只描述列，键形状单独可查）。"""
    table = next(t for t in AF_TABLES if t.name == table_name)
    body = _contract_body(ARCHITECTURE / "database-design.md")
    primary_key_columns = [column.name for column in table.primary_key.columns]
    assert f"### {table_name} 列清单" in body, table_name
    for constraint in table.constraints:
        name = getattr(constraint, "name", None)
        if name:
            assert name in body, f"{table_name}: 唯一键 {name} 未在文档出现"
    for index in table.indexes:
        assert index.name in body, f"{table_name}: 索引 {index.name} 未在文档出现"
    section = body.split(f"### {table_name} 列清单", 1)[1].split("\n###", 1)[0]
    marked = re.findall(r"^\| `(\w+)` \|[^|]*\|[^|]*\| PK ", section, re.M)
    assert sorted(marked) == sorted(primary_key_columns), (
        f"{table_name}: 文档主键列 {marked} ≠ ORM {primary_key_columns}"
    )
