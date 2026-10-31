"""8902 API v1 契约测试：端点结构与 schemas 一致、错误语义、af_* 持久化与分块。

隔离：SQLite 内存库 + 临时数据根 + 假引擎（不访问 MySQL/公网/模型服务）。
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from axiom_flow.api.main import create_app
from axiom_flow.config import AdminConfigError, Settings, load_settings
from axiom_flow.db.llm_ledger import LlmCallIdentity
from axiom_flow.db.models import (
    AF_TABLES,
    LIFECYCLE_EVENT,
    LIFECYCLE_INTENT,
    OP_KIND_KP,
    OP_KIND_PARSE,
    OP_KIND_STRUCTURE,
    AfBook,
    AfKp,
    AfOperation,
    AfPage,
    Base,
)
from axiom_flow.db.repositories import (
    KP_PAYLOAD_KEYS,
    KP_RESULT_KEYS,
    LEGACY_JOB_ID,
    BookRepository,
    BookSyncRow,
    KpNodeRow,
    KpRepository,
    OperationRepository,
    PageRepository,
    ParseJobRow,
    RegionIntent,
)
from axiom_flow.engines.base import (
    DocumentResult,
    EngineError,
    ModelLoad,
    OutlineEntry,
    PageResult,
    SectionPoints,
    TocPagePick,
)
from axiom_flow.engines.factory import build_text_engine
from axiom_flow.engines.kp_gateway import KP_SLOT_NAME
from axiom_flow.orchestrator.knowledge import norm_key_for
from axiom_flow.schemas import KP_REVIEW_NOTE_MAX_CHARS, NARRATIVE_MAX_CHARS, BlockType
from tests.support.llm_ledger_schema import create_ledger_table, read_ledger_rows

#: 梳理调用账的身份三列：契约层只关心「有没有接线」，取值本身给桩值。
#: `mode='local'` 时 `provider` 按根侧 DDL 注释是 runtime 名（不是厂商名），桩就叫桩的 runtime。
_TEST_LLM_IDENTITY = LlmCallIdentity(mode="local", provider="stub-runtime", model="stub-text-model")


def _parse_rows(session, *, book_id: str | None = None) -> list[AfOperation]:
    """parse 事件行的唯一读法（旧 `select(AfParseJob)`）：契约测试断言库内侧，不看响应镜像。

    折叠后「任务表」不再是独立表，行集合由 `op_kind='parse'` 且 `lifecycle='event'` 界定；
    作用域条件只写在这一个函数里，避免各处各自拼 WHERE 而漏掉一半判据。
    """
    stmt = select(AfOperation).where(
        AfOperation.lifecycle == LIFECYCLE_EVENT, AfOperation.op_kind == OP_KIND_PARSE
    )
    if book_id is not None:
        stmt = stmt.where(AfOperation.book_id == book_id)
    return list(session.scalars(stmt.order_by(AfOperation.job_id)))


def _intent_rows(session, op_kind: str, *, book_id: str | None = None) -> list[AfOperation]:
    """人工意图行的读法（旧 `select(AfPageRegion)`）：只取 `lifecycle='intent'` 的那一侧。

    几何落在 `payload` 里，需要读几何的用例按 `row.payload[...]` 取——直接写属性名会静默拿到
    `None`，让「落库了没有」的断言退化成恒真。
    """
    stmt = select(AfOperation).where(
        AfOperation.lifecycle == LIFECYCLE_INTENT, AfOperation.op_kind == op_kind
    )
    if book_id is not None:
        stmt = stmt.where(AfOperation.book_id == book_id)
    return list(session.scalars(stmt.order_by(AfOperation.page_no, AfOperation.target_id)))


class FakeEngine:
    """假引擎：全本返回两页结构化块；单页返回一页文本块（不访问模型服务）。"""

    name = "mineru"

    def __init__(self, *, healthy: bool = True, fail_pages: set[int] | None = None) -> None:
        self.healthy = healthy
        self.fail_pages = fail_pages or set()
        self.closed = False

    def health(self) -> bool:
        return self.healthy

    def model_load(self) -> ModelLoad:
        """恒为空闲：契约面只跑单一占用闸门的放行分支（占用分支由编排单测守护）。"""
        return ModelLoad(queued=0, processing=0)

    def close(self) -> None:
        self.closed = True

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        return DocumentResult(
            pages=[
                PageResult(
                    page=1,
                    blocks=[
                        {"type": "title", "text": "第 1 章", "text_level": 1, "bbox": [72, 54, 540, 80]},
                        {"type": "text", "text": "正文一", "bbox": [72, 96, 540, 132]},
                        {"type": "interline_equation", "text": "x^2", "bbox": [72, 150, 540, 175]},
                    ],
                    page_size=(595.0, 842.0),
                ),
                PageResult(
                    page=2,
                    blocks=[{"type": "text", "text": "正文二", "bbox": [72, 96, 540, 132]}],
                    page_size=(595.0, 842.0),
                ),
            ]
        )

    def parse_page(self, image_bytes: bytes, page_no: int) -> PageResult:
        if page_no in self.fail_pages:
            raise EngineError(f"第 {page_no} 页解析失败")
        return PageResult(
            page=page_no,
            blocks=[{"type": "text", "text": f"第 {page_no} 页重解析", "bbox": [72, 96, 540, 132]}],
            page_size=(595.0, 842.0),
        )


class _WindowedEngine(FakeEngine):
    """按窗口回**全书绝对页号**的假引擎：记录每次提交的 1 基含两端区间，可指定失败窗口。"""

    def __init__(self, *, fail_spans: set[tuple[int, int]] | None = None) -> None:
        super().__init__()
        self.fail_spans = fail_spans or set()
        self.spans: list[tuple[int, int]] = []

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        span = (start_page, end_page)
        self.spans.append(span)
        if span in self.fail_spans:
            raise EngineError("MinerU 轮询超时：单块 deadline 到期")
        return DocumentResult(
            pages=[
                PageResult(
                    page=no,
                    blocks=[{"type": "text", "text": f"第 {no} 页", "bbox": [72, 96, 540, 132]}],
                    page_size=(595.0, 842.0),
                )
                for no in range(span[0], span[1] + 1)
            ]
        )


class _RepeatedHeaderEngine(FakeEngine):
    """每页同一高度一条**类型未判**的段落眉＋纵向错开的正文：只有线维认得出它（AF-T6 乙半边）。"""

    def parse_document(
        self, pdf_bytes: bytes, filename: str, *, start_page: int | None = None, end_page: int | None = None
    ) -> DocumentResult:
        return DocumentResult(
            pages=[
                PageResult(
                    page=no,
                    blocks=[
                        {
                            "type": "text",
                            "text": "PRINCIPLES OF MATHEMATICAL ANALYSIS",
                            "bbox": [72, 58, 540, 80],
                        },
                        {
                            "type": "text",
                            "text": f"正文 {no}",
                            "bbox": [72, 200 + no * 90, 540, 236 + no * 90],
                        },
                    ],
                    page_size=(595.0, 842.0),
                )
                for no in range(1, 5)
            ]
        )


def _make_pdf(path: Path, pages: int = 2) -> Path:
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((72, 72), "API contract test")
        doc.save(path)
    finally:
        doc.close()
    return path


def _build(
    tmp_path,
    monkeypatch,
    *,
    ocr_engine: str | None = None,
    engine_factory=None,
    book_pages: int = 2,
    parse_window_size: int | None = None,
    max_regions_per_page: int | None = None,
    admin_enabled: bool = False,
    admin_token: str | None = "",
    kp_engine: str = "",
    knowledge_engine_factory=None,
    kp_call_identity=_TEST_LLM_IDENTITY,
    background_jobs: bool = False,
):
    """隔离数据根 + SQLite 内存库 + 假引擎；返回 (client, session_factory)。

    管理面两参数**始终显式传入**（缺省 `admin_enabled=False`）：不传就会读到开发者 `.env` 里的
    `QED_ADMIN_*`，同一份测试在两台机器上语义不同。`kp_engine` 同理**恒显式传入**（缺省空＝槽位
    未接入），否则 `AXIOM_KP_ENGINE` 会从环境漏进用例，把「未接入即 503」的门变成环境依赖。
    `kp_call_identity` 是梳理调用账的三枚身份列（`KA-23`／R6 甲案）：缺省给一个桩身份让批次能跑，
    传 `None` 就是「接了引擎没接身份」那一条形，端点面应 503 且零写入。
    `background_jobs` 缺省 `False`（内联执行，用例不必和线程抢库）；只有「受理到落行之间」那一窗
    判据（`KA-40`）必须让批次真的跑在别人手里，才由那一条显式打开后台 worker。
    """
    monkeypatch.chdir(tmp_path)
    data_root = tmp_path / "data"
    raw_dir = data_root / "raw" / "math-advanced" / "math_analysis"
    raw_dir.mkdir(parents=True)
    _make_pdf(raw_dir / "book.pdf", pages=book_pages)
    settings = load_settings(
        data_dir=data_root,
        ocr_engine=ocr_engine,
        parse_window_size=parse_window_size,
        max_regions_per_page=max_regions_per_page,
        admin_enabled=admin_enabled,
        admin_token=admin_token,
        kp_engine=kp_engine,
    )
    db_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(db_engine)
    # 梳理调用账的表在测试库里也要在位：缺表时写入器只降级告警，端点面就再也看不见自己写的行
    create_ledger_table(db_engine)
    session_factory = sessionmaker(bind=db_engine, future=True)
    app = create_app(
        settings,
        session_factory=session_factory,
        engine_factory=engine_factory or (lambda name: FakeEngine()),
        knowledge_engine_factory=knowledge_engine_factory,
        kp_call_identity=kp_call_identity,
        background_jobs=background_jobs,
    )
    return TestClient(app), session_factory


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    test_client, _ = _build(tmp_path, monkeypatch)
    with test_client as live:
        yield live


def _sync_book(client: TestClient, book_id: str = "mathanalysis-b05") -> None:
    response = client.post(
        "/api/v1/books/sync",
        json=[
            {
                "book_id": book_id,
                "domain_id": "math-advanced",
                "course_id": "math_analysis",
                "course_name": "数学分析",
                "knowledge_id": "kt-mathanalysis-3",
                "title": "数学分析原理",
                "part": "教程",
                "display_title": "Rudin 数学分析原理",
                "authors": [{"name": "Walter Rudin", "role": "author"}],
                "file_path": "raw/math-advanced/math_analysis/book.pdf",
            }
        ],
    )
    assert response.status_code == 200, response.text


def _ingest(client: TestClient, book_id: str = "mathanalysis-b05") -> None:
    response = client.post(f"/api/v1/books/{book_id}/ingest")
    assert response.status_code == 200, response.text


def _parsed_book(client: TestClient) -> None:
    """sync → ingest → 全本解析：给出一个**有生效版本**的书（结构面与编辑面的共同前置）。"""
    _sync_book(client)
    _ingest(client)
    job = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
    assert job.status_code == 202, job.text
    assert job.json()["status"] == "completed"


def _add_region(session_factory, book_id: str, page_no: int = 1) -> None:
    """经仓储落一条人工区域行（结构端点归 `TestStructureEndpoint`，这里只喂删除面与跨版本存活判据）。"""
    with session_factory() as session:
        OperationRepository(session).replace_page_intents(
            book_id, page_no, [RegionIntent(op="add", bbox=[72, 900, 600, 1010])]
        )
        session.commit()


class TestHealthEndpoint:
    def test_health_returns_200(self, client):
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def _seed_fs_book(data_root: Path, book_id: str, *, domain_id: str, course_id: str = "c1") -> Path:
    """手工铺设**文件系统残留**（无库行书目的产物目录）：反向判据「文件系统不再是书目来源」。"""
    segments = ["parsed", *([domain_id] if domain_id else []), course_id, book_id]
    book_dir = data_root.joinpath(*segments)
    (book_dir / "pages").mkdir(parents=True)
    (book_dir / "pages" / "p0001.png").write_bytes(b"png")
    (book_dir / "book.json").write_text('{"book_id": "ghost", "page_count": 1}', encoding="utf-8")
    return book_dir


def _seed_db_book(
    session_factory, data_root: Path, book_id: str, *, domain_id: str, course_id: str = "c1"
) -> Path:
    """DB 行 + 页图产物（REQ-097 后的书目来源：af_books 是唯一枚举域）。"""
    segments = ["parsed", *([domain_id] if domain_id else []), course_id, book_id]
    book_dir = data_root.joinpath(*segments)
    (book_dir / "pages").mkdir(parents=True)
    (book_dir / "pages" / "p0001.png").write_bytes(b"png")
    with session_factory() as session:
        repo = BookRepository(session)
        repo.upsert_many(
            [
                BookSyncRow(
                    book_id=book_id,
                    domain_id=domain_id,
                    course_id=course_id,
                    title="深度无关回归样本",
                    file_path="raw/probe/book.pdf",
                )
            ]
        )
        repo.update_ingest(book_id, page_count=1, sha256="0" * 64)
        session.commit()
    return book_dir


class TestBookEnumerationDepthAgnostic:
    """书目来源收敛到 af_books（PLAN-016 C7 的 REQ-097 修订）：目录解析走库行段名，两层三层等价。

    文件系统回落删除后，深度无关由 `book_dir_for` 承载（空 `domain_id` 段被 pathlib 折叠）；
    不在 af_books 的书一律 404——文件系统残留不再算书，也不得成为第二事实源。
    """

    def test_two_layer_book_pages_resolve_via_db_row(self, tmp_path, monkeypatch):
        client, session_factory = _build(tmp_path, monkeypatch)
        book_dir = _seed_db_book(session_factory, tmp_path / "data", "twolayer-b01", domain_id="")

        with client as live:
            response = live.get("/api/v1/books/twolayer-b01/pages")

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["total"], body["scope"], book_dir.is_dir()) == (1, "legacy", True)

    def test_three_layer_book_pages_resolve_via_db_row(self, tmp_path, monkeypatch):
        client, session_factory = _build(tmp_path, monkeypatch)
        _seed_db_book(
            session_factory, tmp_path / "data", "threelayer-b01", domain_id="math-advanced"
        )

        with client as live:
            response = live.get("/api/v1/books/threelayer-b01/pages")

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1

    def test_filesystem_residue_without_db_row_is_invisible(self, tmp_path, monkeypatch):
        """反向判据：book.json 与页图俱在但无库行 ⇒ 列表为空、读取 404（独立性由此更强）。"""
        client, _ = _build(tmp_path, monkeypatch)
        _seed_fs_book(tmp_path / "data", "ghost-b01", domain_id="")

        with client as live:
            listed = live.get("/api/v1/books")
            single = live.get("/api/v1/books/ghost-b01")
            pages = live.get("/api/v1/books/ghost-b01/pages")

        assert listed.json() == []
        assert single.status_code == 404
        assert pages.status_code == 404


class TestTransientResidueWarning:
    """瞬态残留计数告警（PLAN-016 W5 / Q3 裁决）：启动期与管理面清除两路径，只告警不清理。"""

    def test_lifespan_warns_on_transient_residue(self, tmp_path, monkeypatch, caplog):
        client, _ = _build(tmp_path, monkeypatch)
        residue = tmp_path / "data" / "parsed" / ".leftover.tmp-deadbeef"
        residue.parent.mkdir(parents=True, exist_ok=True)
        residue.write_text("crash", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="axiom_flow.ingest.artifacts"):
            with client as live:
                assert live.get("/api/v1/health").status_code == 200

        assert any("检出 1 个瞬态残留" in record.getMessage() for record in caplog.records)

    def test_purge_dry_run_warns_on_transient_residue(self, tmp_path, monkeypatch, caplog):
        client, _ = _admin_build(tmp_path, monkeypatch)
        residue = tmp_path / "data" / "parsed" / ".view.old-deadbeef"

        with client as live, caplog.at_level(logging.WARNING, logger="axiom_flow.ingest.artifacts"):
            # 进入 lifespan 后再落残留：隔离启动期告警，只验证管理面清除路径的计数告警。
            residue.parent.mkdir(parents=True, exist_ok=True)
            residue.write_text("crash", encoding="utf-8")
            response = live.post(
                PURGE_URL, json={"scope": {"all": True}}, headers=_admin_headers()
            )

        assert response.status_code == 200, response.text
        assert any("检出 1 个瞬态残留" in record.getMessage() for record in caplog.records)


class TestBooksEndpoints:
    """书目同步/CRUD：af_books 幂等 upsert 与响应结构。"""

    def test_sync_then_list_and_get(self, client):
        _sync_book(client)

        listed = client.get("/api/v1/books")
        assert listed.status_code == 200
        books = listed.json()
        assert len(books) == 1
        assert books[0]["book_id"] == "mathanalysis-b05"
        assert books[0]["course_id"] == "math_analysis"
        assert books[0]["ingest_status"] == "none"
        assert books[0]["authors"][0]["name"] == "Walter Rudin"

        fetched = client.get("/api/v1/books/mathanalysis-b05")
        assert fetched.status_code == 200
        assert fetched.json()["title"] == "数学分析原理"

    def test_sync_is_idempotent(self, client):
        _sync_book(client)
        response = client.post(
            "/api/v1/books/sync",
            json=[{"book_id": "mathanalysis-b05", "title": "数学分析原理（改）"}],
        )
        assert response.json()["synced"] == 0
        assert response.json()["updated"] == 1
        assert len(client.get("/api/v1/books").json()) == 1

    def test_patch_notes(self, client):
        _sync_book(client)
        response = client.patch("/api/v1/books/mathanalysis-b05", json={"notes": "重点核对第 3 章"})
        assert response.status_code == 200
        assert response.json()["notes"] == "重点核对第 3 章"

    def test_delete_book(self, client):
        _sync_book(client)
        assert client.delete("/api/v1/books/mathanalysis-b05").status_code == 200
        assert client.get("/api/v1/books/mathanalysis-b05").status_code == 404

    def test_get_missing_book_returns_404(self, client):
        assert client.get("/api/v1/books/nope").status_code == 404

    def test_books_listing_has_no_filesystem_fallback(self, client, tmp_path):
        """REQ-097：空表不再回落文件系统——删了库行，即使页图产物还在，书目也不成书。"""
        _sync_book(client)
        _ingest(client)
        assert client.delete("/api/v1/books/mathanalysis-b05").status_code == 200

        assert client.get("/api/v1/books").json() == []
        assert client.get("/api/v1/books/mathanalysis-b05").status_code == 404

    def test_ingest_renders_pages_and_updates_progress(self, client):
        _sync_book(client)
        _ingest(client)

        book = client.get("/api/v1/books/mathanalysis-b05").json()
        assert book["ingest_status"] == "ingested"
        assert book["page_count"] == 2
        assert len(book["sha256"]) == 64

    def test_ingest_missing_source_pdf_returns_404(self, client):
        client.post("/api/v1/books/sync", json=[{"book_id": "ghost", "file_path": "raw/ghost.pdf"}])

        assert client.post("/api/v1/books/ghost/ingest").status_code == 404


class TestPagesAndManifest:
    """对照供给：页数据、页图、manifest、编辑合并。"""

    def test_page_returns_blocks_markdown_and_image_url(self, client):
        _parsed_book(client)

        response = client.get("/api/v1/books/mathanalysis-b05/pages/1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"blocks", "markdown", "image_url", "edits", "furniture_blocks"}
        assert payload["furniture_blocks"] == []
        assert payload["blocks"]["page"] == 1
        assert payload["blocks"]["source"] == "mineru"
        assert payload["blocks"]["image_size"][0] > 0
        assert payload["blocks"]["blocks"][0]["type"] == "heading"
        assert payload["blocks"]["blocks"][0]["index"] == 0
        assert "$$" in payload["markdown"]
        assert payload["image_url"] == "/api/v1/books/mathanalysis-b05/pages/1/image"
        assert payload["edits"] == []

    def test_page_image_serves_png(self, client):
        _parsed_book(client)

        response = client.get("/api/v1/books/mathanalysis-b05/pages/1/image")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")

    def test_missing_page_returns_404(self, client):
        _parsed_book(client)
        assert client.get("/api/v1/books/mathanalysis-b05/pages/99").status_code == 404
        assert client.get("/api/v1/books/mathanalysis-b05/pages/99/image").status_code == 404

    def test_manifest_requires_active_version(self, client, tmp_path):
        """manifest 是**版本**的自证：ingest 过但无生效版本时，根视图没有 manifest 可给 → 404。

        旧行为在 legacy 作用域对根目录 `write_manifest`，等于把已退役的根清单又写回去
        （违反 S1「根视图只有 pages/p*.png」）；端点不再制造产物文件。
        """
        _sync_book(client)
        _ingest(client)

        response = client.get("/api/v1/books/mathanalysis-b05/manifest")

        assert response.status_code == 404
        assert "生效版本" in response.json()["detail"]
        assert not (tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05" / "manifest.json").exists()

    def test_manifest_lists_artifacts_with_hashes(self, client):
        _parsed_book(client)

        response = client.get("/api/v1/books/mathanalysis-b05/manifest")
        assert response.status_code == 200
        entries = response.json()
        paths = {entry["path"] for entry in entries}
        assert "book.json" in paths
        assert "chunks.json" in paths
        assert "pages/p0001.png" in paths
        assert "pages/p0001.blocks.json" in paths
        assert "manifest.json" not in paths
        assert all(len(entry["sha256"]) == 64 for entry in entries)

    def test_edit_upsert_and_merge(self, client):
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "bad", "note": "文字错", "corrected_text": "正文（修正）"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["verdict"] == "bad"
        assert response.json()["block_type"] == "paragraph"

        merged = client.get("/api/v1/books/mathanalysis-b05/pages/1").json()
        assert len(merged["edits"]) == 1
        assert merged["edits"][0]["corrected_text"] == "正文（修正）"

        # 同块重复编辑覆盖更新
        again = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok"},
        )
        assert again.status_code == 200
        edits = client.get("/api/v1/books/mathanalysis-b05/pages/1/edits").json()
        assert len(edits) == 1
        assert edits[0]["verdict"] == "ok"

    def test_edit_out_of_range_returns_400(self, client):
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/99/edit",
            json={"verdict": "ok"},
        )
        assert response.status_code == 400

    # ---------- REQ-142 W2／PLAN-026 W2：人工改判块类型（corrected_type） ----------

    def test_corrected_type_is_echoed_and_snapshot_stays(self, client):
        """改判键与快照键分职：`block_type` 仍是产物原类型，`corrected_type` 才是人工结论。"""
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "corrected_type": "header"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["block_type"] == "paragraph"
        assert response.json()["corrected_type"] == "header"

        edits = client.get("/api/v1/books/mathanalysis-b05/pages/1/edits").json()
        assert edits[0]["corrected_type"] == "header"

    def test_absent_corrected_type_means_no_relabel(self, client):
        """没改判＝载荷里这一枚是 null，不是空串——回放腿要能分辨「没判」与「判成某种」。"""
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "bad", "corrected_text": "正文（修正）"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["corrected_type"] is None

    @pytest.mark.parametrize("block_type", sorted(item.value for item in BlockType))
    def test_all_ten_block_types_are_accepted(self, client, block_type):
        """标注集＝`BlockType` 十枚全集（闭集本身不另写一份名字表，用例直取枚举）。"""
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "corrected_type": block_type},
        )
        assert response.status_code == 200, f"{block_type} → {response.status_code} {response.text}"
        assert response.json()["corrected_type"] == block_type

    def test_corrected_type_outside_closed_set_is_422(self, client):
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "corrected_type": "title"},
        )
        assert response.status_code == 422, response.text

    def test_free_payload_key_is_422(self, client):
        """闭集外的请求键当场拒：静默吞键会让「什么操作可声明」失去定义（同 `database-design.md` 的禁自由键条）。"""
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "block_type": "header"},
        )
        assert response.status_code == 422, response.text

    def test_relabel_is_overwritten_not_duplicated(self, client):
        """同块重复改判走同一条 upsert：行数恒 1，后判覆盖前判。"""
        _parsed_book(client)

        client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "corrected_type": "header"},
        )
        again = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "corrected_type": "page_number"},
        )
        assert again.status_code == 200, again.text
        edits = client.get("/api/v1/books/mathanalysis-b05/pages/1/edits").json()
        assert len(edits) == 1
        assert edits[0]["corrected_type"] == "page_number"


    # ---------- REQ-142 W3／PLAN-026 W3：读侧回放（改判覆盖产物类型，只改判别维） ----------

    def test_relabel_is_replayed_onto_the_page_blocks(self, client):
        """`GET /pages/{no}` 的块序列读到的是改判后的类型；`edits[]` 仍留产物原类型快照。"""
        _parsed_book(client)
        edited = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/0/edit",
            json={"verdict": "ok", "corrected_type": "header"},
        )
        assert edited.status_code == 200, edited.text

        page = client.get("/api/v1/books/mathanalysis-b05/pages/1")
        assert page.status_code == 200, page.text
        blocks = page.json()["blocks"]["blocks"]
        assert blocks[0]["type"] == "header"
        assert blocks[0]["text"] == "第 1 章"
        assert page.json()["edits"][0]["block_type"] == "heading"

    def test_relabel_does_not_backfill_a_field_the_artifact_lacks(self, client):
        """改判只改判别维、不补内容字段：判成 formula 而产物无 latex 时照样读得出，不 500。"""
        _parsed_book(client)
        client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "ok", "corrected_type": "formula"},
        )

        page = client.get("/api/v1/books/mathanalysis-b05/pages/1")
        assert page.status_code == 200, page.text
        block = page.json()["blocks"]["blocks"][1]
        assert (block["type"], block["latex"], block["text"]) == ("formula", None, "正文一")

    def test_blocks_are_unchanged_without_a_relabel(self, client):
        """反向断言一：没有改判行时块读数与改前逐字相等——回放腿不给未核对的页带来任何漂移。"""
        _parsed_book(client)
        before = client.get("/api/v1/books/mathanalysis-b05/pages/1").json()["blocks"]

        client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
            json={"verdict": "bad", "note": "文字错", "corrected_text": "正文（修正）"},
        )
        after = client.get("/api/v1/books/mathanalysis-b05/pages/1").json()["blocks"]

        assert after == before, "改判之外的编辑键不动块序列：corrected_text 属意图行，不写回产物"

    def test_relabel_from_another_version_is_not_replayed_onto_blocks(self, tmp_path, monkeypatch):
        """反向断言二：改判绑在它所属的解析版本上，生效版的块读数回到产物原类型。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _parsed_book(client)
            with session_factory() as session:
                OperationRepository(session).upsert_edit(
                    "mathanalysis-b05",
                    "job-not-the-active-one",
                    1,
                    0,
                    block_type="heading",
                    verdict="ok",
                    corrected_type="header",
                )
                session.commit()

            blocks = client.get("/api/v1/books/mathanalysis-b05/pages/1").json()["blocks"]["blocks"]

        assert blocks[0]["type"] == "heading"


class TestPageFurnitureMarks:
    """页数据读投影的家具标记（PLAN-026 W12 ③；根侧 REQ-143 W20／第八令 Q4 乙）。

    标记是**读时派生**：类型维吃回放后的 `type`，线维吃整本重复线（`furniture_hints`）；产物文件与
    `markdown` 一字不改（`KA-19` 的「对照视图按原文判」不破）。输出块下标，屏侧不必自算跨页线
    （第二处事实源，禁）。
    """

    def _book_dir(self, tmp_path) -> Path:
        return tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"

    def test_typed_furniture_is_marked_only_in_the_read_projection(self, client, tmp_path):
        _parsed_book(client)
        url = "/api/v1/books/mathanalysis-b05/pages/1"
        before = client.get(url).json()

        response = client.put(
            f"{url}/blocks/1/edit", json={"verdict": "ok", "corrected_type": "header"}
        )
        assert response.status_code == 200, response.text

        payload = client.get(url).json()
        assert payload["furniture_blocks"] == [1]
        assert payload["markdown"] == before["markdown"]
        job_id = client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"]
        artifact = json.loads(
            (self._book_dir(tmp_path) / "versions" / job_id / "pages" / "p0001.blocks.json").read_text(
                encoding="utf-8"
            )
        )
        assert artifact["blocks"][1]["type"] == "paragraph", "改判不写回产物：标记只活在读投影里"

    def test_untyped_repeated_line_is_marked_on_every_carrying_page(self, tmp_path, monkeypatch):
        test_client, _ = _build(
            tmp_path, monkeypatch, book_pages=4, engine_factory=lambda name: _RepeatedHeaderEngine()
        )
        with test_client as client:
            _parsed_book(client)

            for page_no in range(1, 5):
                payload = client.get(f"/api/v1/books/mathanalysis-b05/pages/{page_no}").json()
                assert payload["furniture_blocks"] == [0], f"第 {page_no} 页的眉未被标记"
                assert payload["blocks"]["blocks"][0]["type"] == "paragraph", "线维不改类型"
                assert (
                    "PRINCIPLES OF MATHEMATICAL ANALYSIS" in payload["markdown"]
                ), "markdown 原样：剔除不发生在页数据投影"

    def test_a_book_without_furniture_reports_an_empty_list(self, client):
        _parsed_book(client)

        assert client.get("/api/v1/books/mathanalysis-b05/pages/1").json()["furniture_blocks"] == []
        assert client.get("/api/v1/books/mathanalysis-b05/pages/2").json()["furniture_blocks"] == []

    def test_relabel_and_revert_take_effect_across_reads(self, client):
        """缓存失效判据：先读两次（第二次命中缓存），改判与撤回后的下一次读必须跟着变。"""
        _parsed_book(client)
        url = "/api/v1/books/mathanalysis-b05/pages/1"
        assert client.get(url).json()["furniture_blocks"] == []
        assert client.get(url).json()["furniture_blocks"] == []

        changed = client.put(f"{url}/blocks/1/edit", json={"verdict": "ok", "corrected_type": "header"})
        assert changed.status_code == 200, changed.text
        assert client.get(url).json()["furniture_blocks"] == [1]

        reverted = client.put(
            f"{url}/blocks/1/edit", json={"verdict": "ok", "corrected_type": "paragraph"}
        )
        assert reverted.status_code == 200, reverted.text
        assert client.get(url).json()["furniture_blocks"] == []


PAGE1_BLOCK_URL = "/api/v1/books/mathanalysis-b05/pages/1"
STRUCTURE_URL = "/api/v1/books/mathanalysis-b05/pages/1/structure"
STRUCTURE_URL2 = "/api/v1/books/mathanalysis-b05/pages/2/structure"


class TestStructureEndpoint:
    """`PUT /api/v1/books/{id}/pages/{no}/structure`（PLAN-018 W2 / ADR 0010）：全量声明与几何锚换算。

    W2 交付面是**落库意图行**（`RegionRecord[]`）；带 `state` 的投影视图 `RegionView` 与读取面
    `regions` 字段归 W3（前置 3 要求先改 `api.md` 读取面契约）。本类同时守住验收锚点的中间换算：
    根侧只给块下标，服务端换算成受理时生效版本产物里的几何快照，落库值必须与 `GET …/pages/{no}`
    读到的 `bbox` 逐字相同——转译错一次就是在污染人工核对现场。
    """

    def test_add_declares_manual_box(self, client):
        _parsed_book(client)

        response = client.put(STRUCTURE_URL, json={"adds": [[72, 900, 600, 1010]]})

        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["op"] == "add"
        assert rows[0]["bbox"] == [72, 900, 600, 1010]
        assert rows[0]["anchor_bbox"] is None and rows[0]["members"] is None
        assert rows[0]["kind"] == "" and rows[0]["note"] == ""
        assert rows[0]["region_id"].startswith("rgn_"), "身份由服务端派生并回给根侧长期持有"
        assert rows[0]["created_at"] and rows[0]["updated_at"]

    def test_repeated_declaration_reuses_region_id(self, tmp_path, monkeypatch):
        """同一意图重复提交 ⇒ 同一 `region_id` 且只有一行（幂等，无需 undo 端点）。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _parsed_book(client)
            first = client.put(
                STRUCTURE_URL, json={"adds": [[72, 900, 600, 1010]], "deletes": [{"block_index": 1}]}
            ).json()

            second = client.put(
                STRUCTURE_URL, json={"adds": [[72, 900, 600, 1010]], "deletes": [{"block_index": 1}]}
            )

            assert second.status_code == 200, second.text
            assert sorted(row["region_id"] for row in second.json()) == sorted(
                row["region_id"] for row in first
            )
            with session_factory() as session:
                assert len(_intent_rows(session, OP_KIND_STRUCTURE)) == 2

    def test_declaration_is_scoped_to_one_page(self, tmp_path, monkeypatch):
        """缺席即撤销只作用于**本页**：他页人工框不受本次声明影响。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _parsed_book(client)
            client.put(STRUCTURE_URL, json={"adds": [[10, 10, 20, 20], [30, 30, 40, 40]]})
            kept = client.put(STRUCTURE_URL2, json={"adds": [[72, 900, 600, 1010]]}).json()

            after = client.put(STRUCTURE_URL, json={"adds": [[30, 30, 40, 40]]}).json()

            assert [row["bbox"] for row in after] == [[30, 30, 40, 40]]
            with session_factory() as session:
                rows = _intent_rows(session, OP_KIND_STRUCTURE)
                assert [(row.page_no, row.payload["bbox"]) for row in rows] == [
                    (1, [30, 30, 40, 40]),
                    (2, [72, 900, 600, 1010]),
                ]
                assert rows[1].target_id == kept[0]["region_id"]

    def test_delete_by_block_index_anchors_to_artifact_geometry(self, client):
        """下标 → 几何的中间换算：`anchor_bbox` 必须等于生效版本产物里该块的 bbox。"""
        _parsed_book(client)
        blocks = client.get(PAGE1_BLOCK_URL).json()["blocks"]["blocks"]

        response = client.put(STRUCTURE_URL, json={"deletes": [{"block_index": 1}]})

        assert response.status_code == 200, response.text
        rows = response.json()
        assert rows[0]["op"] == "delete"
        assert rows[0]["anchor_bbox"] == blocks[1]["bbox"]
        assert rows[0]["bbox"] is None, "delete 不声明新框，只留受理时的几何快照"

    def test_delete_by_bbox_keeps_geometry_as_is(self, client):
        _parsed_book(client)

        rows = client.put(STRUCTURE_URL, json={"deletes": [{"bbox": [600, 96, 1100, 210]}]}).json()

        assert rows[0]["anchor_bbox"] == [600, 96, 1100, 210]

    def test_region_id_locator_reuses_stored_anchor(self, client):
        """根侧用上一次响应里的 `region_id` 重声明 ⇒ 落回同一行、同一锚（跨请求定位）。"""
        _parsed_book(client)
        blocks = client.get(PAGE1_BLOCK_URL).json()["blocks"]["blocks"]
        rows = client.put(STRUCTURE_URL, json={"deletes": [{"block_index": 1}]}).json()

        again = client.put(
            STRUCTURE_URL, json={"deletes": [{"region_id": rows[0]["region_id"]}]}
        ).json()

        assert [row["region_id"] for row in again] == [row["region_id"] for row in rows]
        assert again[0]["anchor_bbox"] == blocks[1]["bbox"]

    def test_unknown_region_id_is_400(self, client):
        """`region_id` 只在本页已落库行内可解：解不出就报错，不按几何猜一个块（ADR 0004 无兜底）。"""
        _parsed_book(client)

        response = client.put(STRUCTURE_URL, json={"deletes": [{"region_id": "rgn_deadbeef0000"}]})

        assert response.status_code == 400, response.text

    def test_merge_keeps_member_order_and_single_fact_source(self, client):
        """合并：`members` 数组序即阅读序；成员快照**唯一**落在 `members`（不另存并集，避免两处漂移）。"""
        _parsed_book(client)
        blocks = client.get(PAGE1_BLOCK_URL).json()["blocks"]["blocks"]

        first = client.put(
            STRUCTURE_URL,
            json={"merges": [{"targets": [{"block_index": 0}, {"block_index": 2}]}]},
        ).json()

        assert first[0]["op"] == "merge"
        assert first[0]["members"] == [blocks[0]["bbox"], blocks[2]["bbox"]]
        assert first[0]["anchor_bbox"] is None

        again = client.put(
            STRUCTURE_URL,
            json={"merges": [{"targets": [{"block_index": 2}, {"block_index": 0}]}]},
        ).json()

        assert [row["region_id"] for row in again] == [row["region_id"] for row in first]
        assert again[0]["members"] == [blocks[2]["bbox"], blocks[0]["bbox"]], "同组改判阅读序不换身份"

    def test_locator_shape_and_range_violations_are_400(self, client):
        """定位三项全缺 / 合并成员少于 2 / bbox 形状非法（x1≤x0）/ 下标越界 ⇒ 一律 400 且零落库。"""
        _parsed_book(client)
        cases = [
            {"deletes": [{}]},
            {"deletes": [{"block_index": 99}]},
            {"merges": [{"targets": [{"block_index": 0}]}]},
            {"adds": [[72, 96, 72, 132]]},
            {"adds": [[-1, 96, 540, 132]]},
        ]

        for body in cases:
            response = client.put(STRUCTURE_URL, json=body)
            assert response.status_code == 400, (body, response.text)
        with_assert_empty = client.put(STRUCTURE_URL, json={"adds": [[0, 0, 10, 10]]})
        assert with_assert_empty.status_code == 200
        assert len(with_assert_empty.json()) == 1, "前面的 400 批次没有半途写入"

    def test_field_shape_violations_are_422(self, client):
        """未知字段与越出字段约束（`block_index ≥ 0`）由 schema 挡在门外，不进端点语义判断。"""
        _parsed_book(client)

        for body in (
            {"adds": [[0, 0, 10, 10]], "undo": True},
            {"deletes": [{"block_index": -1}]},
        ):
            response = client.put(STRUCTURE_URL, json=body)
            assert response.status_code == 422, (body, response.text)

    def test_legacy_page_rejects_every_structure_op(self, client):
        """Q3 定判（2026-09-29）：无生效版本时 add/delete/merge **一律** 404，不回落根视图。"""
        _sync_book(client)
        _ingest(client)

        for body in (
            {"adds": [[0, 0, 10, 10]]},
            {"deletes": [{"bbox": [0, 0, 10, 10]}]},
            {"merges": [{"targets": [{"bbox": [0, 0, 10, 10]}, {"bbox": [20, 20, 30, 30]}]}]},
        ):
            response = client.put(STRUCTURE_URL, json=body)
            assert response.status_code == 404, (body, response.text)
            # 判据要指到原因：404 也可能是路由没挂、书不存在或页产物缺失（三者恢复动作不同）
            assert "无生效解析版本" in response.json()["detail"], response.text

    def test_unknown_book_is_404(self, client):
        _parsed_book(client)

        response = client.put(
            "/api/v1/books/no-such-book/pages/1/structure", json={"adds": [[0, 0, 10, 10]]}
        )

        assert response.status_code == 404, response.text
        assert "书目" in response.json()["detail"], response.text

    def test_declaration_over_limit_is_400_before_write(self, tmp_path, monkeypatch):
        """每框一次单页提交 ⇒ 成本必须可见：超 `AXIOM_MAX_REGIONS_PER_PAGE` 受理前判 400。

        上限本身是**结构意图的取值违规**（归 400，同定位三项全缺/合并少于 2）；422 那一档属于
        提交面（`POST /parse-jobs` 的框数上限），那里才涉及「不让请求打到模型」。
        """
        test_client, session_factory = _build(tmp_path, monkeypatch, max_regions_per_page=2)
        with test_client as client:
            _parsed_book(client)

            response = client.put(
                STRUCTURE_URL,
                json={"adds": [[0, 0, 10, 10], [20, 20, 30, 30], [40, 40, 50, 50]]},
            )

            assert response.status_code == 400, response.text
            assert "每页上限" in response.json()["detail"], response.text
            with session_factory() as session:
                assert _intent_rows(session, OP_KIND_STRUCTURE) == []

    def test_page_beyond_book_is_404_before_write(self, tmp_path, monkeypatch):
        """页码域按 `af_books.page_count` 在受理前判：只有两页的书不能声明第 3 页的人工框。

        页不存在归 404（与书目/产物缺失同码，草案口径）；不查页图是否在位是因为「先画框后解析」
        是页级输入的正常次序。
        """
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _parsed_book(client)

            response = client.put(
                "/api/v1/books/mathanalysis-b05/pages/3/structure", json={"adds": [[0, 0, 10, 10]]}
            )

            assert response.status_code == 404, response.text
            assert "页不存在" in response.json()["detail"], response.text
            with session_factory() as session:
                assert _intent_rows(session, OP_KIND_STRUCTURE) == []


class TestRegionDrivenSubmission:
    """`POST /api/v1/parse-jobs` 区域驱动受理门（PLAN-018 W1 / ADR 0010 决定 4）：框数判在受理前。

    状态码分档：声明面（`PUT …/structure`）超上限是取值违规 → 400；提交面超上限会把成本
    （每框一次单页提交）打到模型 → 422 且**不创建任务行**。本类守受理面；执行面「每框一次
    调用」由编排单测守护（PP-21）。
    """

    LIMIT = 2

    @staticmethod
    def _seed(session_factory, page_no: int, *, adds: int, deletes: int = 0) -> None:
        intents = [
            RegionIntent(op="add", bbox=[10 + 60 * i, 900, 60 + 60 * i, 1000]) for i in range(adds)
        ]
        intents += [
            RegionIntent(op="delete", anchor_bbox=[72, 96 + 20 * i, 540, 132 + 20 * i])
            for i in range(deletes)
        ]
        with session_factory() as session:
            OperationRepository(session).replace_page_intents("mathanalysis-b05", page_no, intents)
            session.commit()

    @staticmethod
    def _job_rows(session_factory) -> int:
        with session_factory() as session:
            return len(_parse_rows(session))

    def test_over_limit_rejected_422_without_job_row(self, tmp_path, monkeypatch):
        test_client, session_factory = _build(tmp_path, monkeypatch, max_regions_per_page=self.LIMIT)
        with test_client as client:
            _parsed_book(client)
            self._seed(session_factory, 1, adds=self.LIMIT + 1)
            before = self._job_rows(session_factory)

            response = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1]}
            )

            assert response.status_code == 422, response.text
            assert "第 1 页" in response.text and "上限" in response.text
            assert self._job_rows(session_factory) == before

    def test_whole_book_submission_checks_every_page(self, tmp_path, monkeypatch):
        """`pages` 缺省＝全本：所有目标页都进框数检查，越限页码写进 detail。"""
        test_client, session_factory = _build(tmp_path, monkeypatch, max_regions_per_page=self.LIMIT)
        with test_client as client:
            _parsed_book(client)
            self._seed(session_factory, 2, adds=self.LIMIT + 1)

            response = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})

            assert response.status_code == 422, response.text
            assert "第 2 页" in response.text

    def test_regions_on_unrequested_page_do_not_block(self, tmp_path, monkeypatch):
        """门只查**目标页**：第 2 页 3 框不拒第 1 页的任务。"""
        test_client, session_factory = _build(tmp_path, monkeypatch, max_regions_per_page=self.LIMIT)
        with test_client as client:
            _parsed_book(client)
            self._seed(session_factory, 2, adds=self.LIMIT + 1)

            response = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1]}
            )

            assert response.status_code == 202, response.text

    def test_only_add_intents_count_toward_limit(self, tmp_path, monkeypatch):
        """删除/合并意图不产生模型提交，不计入框数上限（成本口径＝提交数）。"""
        test_client, session_factory = _build(tmp_path, monkeypatch, max_regions_per_page=self.LIMIT)
        with test_client as client:
            _parsed_book(client)
            self._seed(session_factory, 1, adds=self.LIMIT, deletes=3)

            response = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1]}
            )

            assert response.status_code == 202, response.text

    def test_over_limit_judged_before_model_probe(self, tmp_path, monkeypatch):
        """非法请求连探活都不做：服务已不可用时框数越限仍判 422 而非 503（门槛顺序守护）。"""
        engine = FakeEngine()
        test_client, session_factory = _build(
            tmp_path, monkeypatch, max_regions_per_page=self.LIMIT, engine_factory=lambda name: engine
        )
        with test_client as client:
            _parsed_book(client)
            self._seed(session_factory, 1, adds=self.LIMIT + 1)
            engine.healthy = False

            response = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1]}
            )

            assert response.status_code == 422, response.text


class TestChunksEndpoint:
    """分块预览：落盘产物 + 参数重算。"""

    def test_chunks_from_artifact(self, client):
        _parsed_book(client)

        response = client.get("/api/v1/books/mathanalysis-b05/chunks")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["book_id"] == "mathanalysis-b05"
        assert payload["params"] == {"max_chars": 1000, "overlap_blocks": 1}
        assert payload["chunks"]
        chunk = payload["chunks"][0]
        assert chunk["block_refs"][0]["page"] == 1
        assert chunk["page_start"] >= 1

    def test_chunks_with_params_recomputes(self, client):
        _parsed_book(client)

        response = client.get(
            "/api/v1/books/mathanalysis-b05/chunks",
            params={"max_chars": 10, "overlap_blocks": 0},
        )
        assert response.status_code == 200
        assert response.json()["params"] == {"max_chars": 10, "overlap_blocks": 0}


class TestParseJobsEndpoints:
    """解析任务：202 受理、状态查询、失败语义（引擎被替换为假实现）。"""

    def test_parse_job_gate_is_db_driven_not_book_json(self, client, tmp_path):
        """受理门槛看 `af_books.ingest_status`：磁盘上有旧布局 book.json 也不算已 ingest。

        根 book.json 退役（REQ-097）后「已 ingest」的判据只剩库行——磁盘文件不能证明页数
        与哈希已回写 af_books。
        """
        _sync_book(client)
        book_dir = (
            tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"
        )
        book_dir.mkdir(parents=True)
        (book_dir / "book.json").write_text('{"page_count": 2}', encoding="utf-8")

        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})

        assert created.status_code == 404, created.text
        assert "尚未 ingest" in created.json()["detail"]

    def test_whole_book_job_completes(self, client):
        _sync_book(client)
        _ingest(client)

        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
        assert created.status_code == 202
        job = created.json()
        assert job["pages"] is None
        assert job["engine"] == "mineru"
        assert job["status"] == "completed"
        assert job["progress"]["parsed"] == 2

        fetched = client.get(f"/api/v1/parse-jobs/{job['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == job["id"]

        book = client.get("/api/v1/books/mathanalysis-b05").json()
        assert book["parse_status"] == "pending", "进度列只表达有没有任务在跑（ADR 0007）"
        assert book["last_parse_result"] == "completed"
        assert book["pages_done"] == 2
        assert job["failed_pages"] == []

    def test_whole_book_window_failure_only_loses_that_window(self, tmp_path, monkeypatch):
        """S1：单块失败只损失该块页（其余块照常完成），任务终态 `partial` 而非整本 `failed`。

        块级**不做重试**：最坏挂钟 = 块数 × 单块超时，重试把它翻倍；同 job 重跑即续跑。
        """
        fake = _WindowedEngine(fail_spans={(3, 4)})
        client, session_factory = _build(
            tmp_path,
            monkeypatch,
            engine_factory=lambda name: fake,
            book_pages=6,
            parse_window_size=2,
        )
        _sync_book(client)
        _ingest(client)

        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()
        job = client.get(f"/api/v1/parse-jobs/{created['id']}").json()

        assert fake.spans == [(1, 2), (3, 4), (5, 6)], "每块一次独立提交：deadline 与失败域都按块计"
        assert job["status"] == "partial"
        assert job["failed_pages"] == [3, 4]
        assert job["progress"]["parsed"] == 4
        assert "窗口 3-4" in job["error"], "块失败原因要能在终态读出，不能只剩页号清单"
        with session_factory() as reader:
            rows = reader.execute(select(AfPage).where(AfPage.job_id == created["id"])).scalars()
            statuses = {row.page_no: row.status for row in rows}
        assert statuses == {
            1: "parsed",
            2: "parsed",
            3: "failed",
            4: "failed",
            5: "parsed",
            6: "parsed",
        }, "失败域 = 该块目标页，不扩散到相邻块"
        assert all("deadline" in row.error for row in rows if row.status == "failed")
        assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] is None

    def test_whole_book_progress_advances_per_window(self, tmp_path, monkeypatch):
        """S2：全本按窗口串行提交，进度与已提交窗口一致（真机非零中间值留档见 PLAN-007 W6）。"""
        fake = _WindowedEngine()
        client, _ = _build(
            tmp_path, monkeypatch, engine_factory=lambda name: fake, book_pages=6, parse_window_size=2
        )
        _sync_book(client)
        _ingest(client)

        job = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()

        assert fake.spans == [(1, 2), (3, 4), (5, 6)], "窗口必须连续、不重不漏、串行"
        assert job["status"] == "completed"
        assert job["progress"] == {"parsed": 6, "total": 6, "failed": 0, "error": ""}
        assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] == job["id"]

    def test_partial_job_reports_failed_pages_and_keeps_pointer(self, tmp_path, monkeypatch):
        """部分成功在契约面上不再被 `completed` 掩盖，也不夺走生效指针。"""
        client, _ = _build(
            tmp_path,
            monkeypatch,
            engine_factory=lambda name: FakeEngine(fail_pages={2}),
        )
        _sync_book(client)
        _ingest(client)

        completed = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1]})
        assert completed.json()["status"] == "completed"
        active = client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"]

        partial = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1, 2]})
        job = partial.json()
        assert job["status"] == "partial"
        assert job["failed_pages"] == [2], "失败页必须是结构化数组，可在契约面直接读"
        assert job["progress"]["failed"] == 1

        book = client.get("/api/v1/books/mathanalysis-b05").json()
        assert book["parse_status"] == "pending"
        assert book["last_parse_result"] == "partial"
        assert book["active_job_id"] == active, "partial 不激活（V3 与 completed 同判据）"

    def test_per_page_job_repares_single_page(self, client):
        _sync_book(client)
        _ingest(client)

        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [2]})
        assert created.status_code == 202
        assert created.json()["progress"]["parsed"] == 1

        page = client.get("/api/v1/books/mathanalysis-b05/pages/2").json()
        assert page["blocks"]["blocks"][0]["text"] == "第 2 页重解析"

    def test_pages_out_of_range_returns_422_and_creates_no_job(self, tmp_path, monkeypatch):
        """DEFECT-006：pages 越界在受理前拒收（真机曾因此写出第 999 页幽灵行）。

        故意用 `healthy=False` 的假引擎：422 必须先于 503 判出，否则越界请求仍会打模型。
        """
        test_client, session_factory = _build(
            tmp_path, monkeypatch, engine_factory=lambda name: FakeEngine(healthy=False)
        )
        with test_client as client:
            _sync_book(client)
            _ingest(client)  # page_count == 2
            for pages in ([3], [0], [-1], [1, 999]):
                response = client.post(
                    "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": pages}
                )
                assert response.status_code == 422, pages
                assert "页码越界" in response.json()["detail"], pages
            with session_factory() as session:
                assert _parse_rows(session) == []

    def test_job_missing_book_returns_404(self, client):
        assert client.post("/api/v1/parse-jobs", json={"book_id": "nope"}).status_code == 404

    def test_job_without_ingest_returns_404(self, client):
        _sync_book(client)
        assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 404

    def test_invalid_payload_returns_422(self, client):
        assert client.post("/api/v1/parse-jobs", json={}).status_code == 422

    def test_unknown_job_returns_404(self, client):
        assert client.get("/api/v1/parse-jobs/does-not-exist").status_code == 404

    def test_startup_reconciles_leftover_active_rows_before_serving(self, tmp_path, monkeypatch):
        """ADR 0007 决定 4：上一次进程留下的 `running` 在首次响应前就被归一，读取端看不到假活动态。"""
        client, session_factory = _build(tmp_path, monkeypatch)
        with session_factory() as session:
            BookRepository(session).upsert_many(
                [
                    BookSyncRow(
                        book_id="mathanalysis-b05",
                        domain_id="math-advanced",
                        course_id="math_analysis",
                        title="数学分析原理",
                        file_path="raw/math-advanced/math_analysis/book.pdf",
                    )
                ]
            )
            jobs = OperationRepository(session)
            jobs.create(ParseJobRow(job_id="ghost", book_id="mathanalysis-b05", engine="mineru"))
            jobs.mark_running("ghost")
            BookRepository(session).update_parse(
                "mathanalysis-b05", parse_status="parsing", pages_done=1
            )
            session.commit()

        with client:  # 进入 TestClient 上下文才触发 lifespan（启动期归一发生在此）
            assert client.get("/api/v1/health").status_code == 200
            ghost = client.get("/api/v1/parse-jobs/ghost").json()
            book = client.get("/api/v1/books/mathanalysis-b05").json()

        assert ghost["status"] == "interrupted"
        assert "服务重启中断" in ghost["error"]
        assert book["parse_status"] == "pending"
        assert book["last_parse_result"] == "interrupted"

    def test_startup_rejects_leftover_kp_draft_batches(self, tmp_path, monkeypatch):
        """梳理批次的中断归一与解析同一条启动通路（`KA-9`）：残留 `draft` 在首次响应前整批判 `rejected`。

        不处置就没有下一次：那批行会让 `KA-4` 的在飞判据永久为真，这本书再发不出新批次。
        """
        client, session_factory = _build(tmp_path, monkeypatch)
        with session_factory() as session:
            repo = KpRepository(session)
            repo.create(
                KpNodeRow(
                    node_id="afkp-ghost",
                    book_id="mathanalysis-b05",
                    name="中断批次的点",
                    norm_key="中断批次的点",
                    gen_status="draft",
                    gen_batch="afkp-aaaaaaaaaaaa",
                )
            )
            endorsed = repo.create(
                KpNodeRow(
                    node_id="afkp-kept",
                    book_id="mathanalysis-b05",
                    name="人已背书的点",
                    norm_key="人已背书的点",
                    gen_status="candidate",
                    gen_batch="afkp-bbbbbbbbbbbb",
                )
            )
            kept_id = endorsed.node_id
            repo.set_confirmed(kept_id, confirmed=True)
            session.commit()

        with client:  # 进入 TestClient 上下文才触发 lifespan（启动期归一发生在此）
            assert client.get("/api/v1/health").status_code == 200

        with session_factory() as session:
            assert session.get(AfKp, "afkp-ghost").gen_status == "rejected"
            kept = session.get(AfKp, kept_id)
            assert (kept.gen_status, kept.confirmed) == ("candidate", 1)
            assert KpRepository(session).find_draft_batch("mathanalysis-b05") is None


class TestParseJobListEndpoint:
    """`GET /api/v1/parse-jobs`（PLAN-008 W3）：六态原值、多值 OR 过滤、稳定倒序与过滤后总数。

    契约条文见 `docs/architecture/api.md` ③ 类同名节；存在理由是 8903「任务中心抽屉」要能枚举
    与过滤作业，而 `GET /parse-jobs/{id}` 只能按已知 id 查单个。
    """

    def _seed_job(
        self,
        session_factory,
        job_id: str,
        *,
        book_id: str = "mathanalysis-b05",
        status: str = "queued",
        error: str = "",
        failed_pages: list[int] | None = None,
    ) -> None:
        with session_factory() as session:
            jobs = OperationRepository(session)
            jobs.create(ParseJobRow(job_id=job_id, book_id=book_id, engine="mineru"))
            if status != "queued":
                jobs.mark_finished(job_id, status=status, error=error, failed_pages=failed_pages)
            session.commit()

    def test_lists_newest_first_with_parsejob_fields_and_timestamps(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            first = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            second = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]

            response = client.get("/api/v1/parse-jobs")

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"jobs", "total", "limit", "offset"}
        assert (body["total"], body["limit"], body["offset"]) == (2, 50, 0)

        rows = body["jobs"]
        assert [row["id"] for row in rows] == [second, first], "默认倒序：任务中心首屏要看到最新一次"
        for row in rows:
            assert {"id", "book_id", "pages", "engine", "status", "progress", "failed_pages", "error"} <= set(row)
            assert {"created_at", "started_at", "finished_at", "active"} <= set(row)
            assert row["status"] == "completed"
            assert row["created_at"] is not None and row["started_at"] is not None
        assert rows[0]["active"] is True, "生效版本必须可从列表判断，否则前端要再查一次书"
        assert rows[1]["active"] is False

    def test_status_filter_is_repeatable_or_and_values_pass_through_verbatim(self, tmp_path, monkeypatch):
        """S1：六态原值透传（`interrupted` 不并入 `failed`）；多值 OR 让「只列活跃」一次请求即可。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        self._seed_job(session_factory, "done1", status="completed")
        with test_client as client:
            # queued 行只能在进入 lifespan 之后写入：启动期归一会把它改成 interrupted（ADR 0007）
            self._seed_job(session_factory, "wait1", status="queued")
            self._seed_job(session_factory, "half1", status="partial", failed_pages=[2])
            self._seed_job(session_factory, "boot1", status="interrupted", error="服务重启中断，可重跑")

            active = client.get("/api/v1/parse-jobs?status=queued&status=running")
            finished = client.get("/api/v1/parse-jobs?status=interrupted")
            two = client.get("/api/v1/parse-jobs?status=partial&status=interrupted")

        assert {row["id"] for row in active.json()["jobs"]} == {"wait1"}
        assert active.json()["total"] == 1
        assert [row["id"] for row in finished.json()["jobs"]] == ["boot1"]
        assert finished.json()["jobs"][0]["error"] == "服务重启中断，可重跑"
        assert {row["id"] for row in two.json()["jobs"]} == {"half1", "boot1"}
        assert two.json()["total"] == 2

    def test_invalid_query_params_return_422(self, tmp_path, monkeypatch):
        """枚举与区间在 API 层判掉（仓储面对未知 status 只给空集，不能把非法入参变成合法空结果）。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            responses = [
                client.get("/api/v1/parse-jobs?status=not-a-status"),
                client.get("/api/v1/parse-jobs?limit=0"),
                client.get("/api/v1/parse-jobs?limit=201"),
                client.get("/api/v1/parse-jobs?offset=-1"),
                client.get("/api/v1/parse-jobs?book_id=..%2Fescape"),
            ]

        assert [r.status_code for r in responses] == [422, 422, 422, 422, 422]

    def test_book_id_filter_and_missing_book_is_empty_not_404(self, tmp_path, monkeypatch):
        """列表过滤不是资源定位：不存在的书给 200 + 空集，与单本读取面 404 分列。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        self._seed_job(session_factory, "own1")
        self._seed_job(session_factory, "other1", book_id="other-book")
        with test_client as client:
            scoped = client.get("/api/v1/parse-jobs?book_id=mathanalysis-b05")
            missing = client.get("/api/v1/parse-jobs?book_id=never-synced")

        assert [row["id"] for row in scoped.json()["jobs"]] == ["own1"]
        assert scoped.json()["total"] == 1
        assert missing.status_code == 200
        assert (missing.json()["jobs"], missing.json()["total"]) == ([], 0)

    def test_pagination_slices_rows_but_total_stays_filtered_count(self, tmp_path, monkeypatch):
        test_client, session_factory = _build(tmp_path, monkeypatch)
        for index in range(3):
            self._seed_job(session_factory, f"job{index}", status="completed")
        with test_client as client:
            page1 = client.get("/api/v1/parse-jobs?limit=2&offset=0").json()
            page2 = client.get("/api/v1/parse-jobs?limit=2&offset=2").json()
            beyond = client.get("/api/v1/parse-jobs?limit=2&offset=9").json()

        assert [row["id"] for row in page1["jobs"]] == ["job2", "job1"]
        assert [row["id"] for row in page2["jobs"]] == ["job0"]
        assert (page1["total"], page2["total"], beyond["total"]) == (3, 3, 3)
        assert beyond["jobs"] == [], "越界偏移给空行集而不是报错，客户端按 total 自行收敛"

    def test_same_instant_orders_by_job_id_desc(self, tmp_path, monkeypatch):
        """MySQL `DATETIME` 只到秒：排序键必须在响应里可复现，否则翻页漏行/重行。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        same = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
        for job_id in ("aaa", "bbb", "ccc"):
            self._seed_job(session_factory, job_id, status="completed")
        with session_factory() as session:
            for job_id in ("aaa", "bbb", "ccc"):
                OperationRepository(session).get(job_id).created_at = same
            session.commit()
        with test_client as client:
            ids = [row["id"] for row in client.get("/api/v1/parse-jobs?limit=2").json()["jobs"]]
            next_page = [row["id"] for row in client.get("/api/v1/parse-jobs?limit=2&offset=2").json()["jobs"]]

        assert (ids, next_page) == (["ccc", "bbb"], ["aaa"])

    def test_deleted_book_audit_rows_stay_listed_and_inactive(self, tmp_path, monkeypatch):
        """不读产物（不经 `_read_scope`）：书与版本目录已删，作业行仍以 200 列出且 `active=false`。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            assert client.delete("/api/v1/books/mathanalysis-b05?purge=true").status_code == 200
            assert not (tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05").exists()

            body = client.get("/api/v1/parse-jobs").json()
            single = client.get(f"/api/v1/parse-jobs/{job_id}")

        assert [row["id"] for row in body["jobs"]] == [job_id]
        assert body["total"] == 1, "不带 book_id 过滤时 total 可大于现存书目的作业数（审计留痕）"
        assert body["jobs"][0]["active"] is False
        assert single.status_code == 200, "历史作业仍可查，删书不抹掉任务事实"


class TestPageListEndpoint:
    """`GET /api/v1/books/{id}/pages`（PLAN-008 W4）：逐页状态清单一次给全集。

    存在理由：单页端点下「哪些页还没有产物」要 1..page_count 逐页试（486 页 = 486 次往返）。
    客户端判据只有一条：`not has_artifact or status == "failed"` ⇒ 需重解析。
    """

    def _book_dir(self, tmp_path) -> Path:
        return tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"

    def test_ingested_but_unparsed_book_lists_every_page_as_pending(self, tmp_path, monkeypatch):
        """`pending` 的定义 = 该版本没有这一页的页行；页图在位不等于块产物在位。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)

            response = client.get("/api/v1/books/mathanalysis-b05/pages")

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["book_id"], body["job_id"], body["scope"], body["total"]) == (
            "mathanalysis-b05",
            "legacy",
            "legacy",
            2,
        )
        assert [page["page_no"] for page in body["pages"]] == [1, 2], "逐页连续覆盖 1..total，不缺项"
        assert [page["status"] for page in body["pages"]] == ["pending", "pending"]
        assert [page["has_artifact"] for page in body["pages"]] == [False, False]
        assert [page["error"] for page in body["pages"]] == ["", ""]
        assert [page["edit_count"] for page in body["pages"]] == [0, 0]

    def test_completed_version_lists_parsed_pages_in_active_scope(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]

            body = client.get("/api/v1/books/mathanalysis-b05/pages").json()

        assert (body["job_id"], body["scope"]) == (job_id, "active"), "作用域自证：读的是哪一版必须可见"
        assert [(p["status"], p["has_artifact"]) for p in body["pages"]] == [("parsed", True), ("parsed", True)]

    def test_page_rows_in_the_read_scope_pass_through_verbatim(self, tmp_path, monkeypatch):
        """`failed` 行与 `error` 原文透传，读取面不改写：失败清单是重跑按钮的输入。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with session_factory() as session:
            BookRepository(session).upsert_many(
                [
                    BookSyncRow(
                        book_id="mathanalysis-b05",
                        domain_id="math-advanced",
                        course_id="math_analysis",
                        title="数学分析原理",
                        file_path="raw/math-advanced/math_analysis/book.pdf",
                    )
                ]
            )
            session.commit()
        with session_factory() as session:
            pages = PageRepository(session)
            pages.upsert("mathanalysis-b05", LEGACY_JOB_ID, 1, status="parsed", source="mineru")
            pages.upsert("mathanalysis-b05", LEGACY_JOB_ID, 2, status="failed", error="引擎超时")
            book_dir = self._book_dir(tmp_path)
            book_dir.mkdir(parents=True)
            # 不跑 ingest：直接在 af_books 记 ingest 结果，避免根视图页行被重新 ingest 作废
            # （V8 的 reset_legacy），也不再造根 book.json（REQ-097：枚举域只看库行）。
            BookRepository(session).update_ingest(
                "mathanalysis-b05", page_count=2, sha256="0" * 64
            )
            session.commit()

        with test_client as client:
            body = client.get("/api/v1/books/mathanalysis-b05/pages").json()

        assert [(p["page_no"], p["status"], p["error"]) for p in body["pages"]] == [
            (1, "parsed", ""),
            (2, "failed", "引擎超时"),
        ]

    def test_parsed_page_with_deleted_artifact_is_exposed_not_rewritten(self, tmp_path, monkeypatch):
        """`parsed` + 无产物只可能来自外部删除：如实暴露，静默改判会让磁盘事故从台账上消失。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            # 产物按**生效版本作用域**判定，故删版本目录内的那一份，不是根视图的
            (self._book_dir(tmp_path) / "versions" / job_id / "pages" / "p0002.blocks.json").unlink()

            body = client.get("/api/v1/books/mathanalysis-b05/pages").json()
            single = client.get("/api/v1/books/mathanalysis-b05/pages/2")

        assert [(p["status"], p["has_artifact"]) for p in body["pages"]] == [("parsed", True), ("parsed", False)]
        assert single.status_code == 404, "清单如实暴露不一致，读该页仍然 404"

    def test_edit_count_is_scoped_to_the_read_version(self, tmp_path, monkeypatch):
        """`edit_count` 承载 ARCH-006「页清单暴露编辑存在性」，且按版本作用域计。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            assert client.put(
                "/api/v1/books/mathanalysis-b05/pages/1/blocks/0/edit", json={"verdict": "bad", "note": "改"}
            ).status_code == 200
            assert client.put(
                "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit", json={"verdict": "ok"}
            ).status_code == 200
            with session_factory() as session:
                OperationRepository(session).upsert_edit("mathanalysis-b05", "other-version", 2, 0, verdict="bad")
                session.commit()

            body = client.get("/api/v1/books/mathanalysis-b05/pages").json()

        assert [p["edit_count"] for p in body["pages"]] == [2, 0]
        assert body["job_id"] == job_id

    def test_total_is_db_only_and_404_when_page_count_null(self, tmp_path, monkeypatch):
        """`total` 只取 `af_books.page_count`：NULL 即 404，**不回落任何 book.json**（REQ-097 S1）。

        「枚举域未知」不能伪装成空清单；根布局残留的旧 book.json 带页数也不作数——
        文件系统不再是元数据第二事实源。
        """
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            with session_factory() as session:
                book = BookRepository(session).get("mathanalysis-b05")
                book.page_count = None
                session.commit()

            missing = client.get("/api/v1/books/mathanalysis-b05/pages")
            (self._book_dir(tmp_path) / "book.json").write_text('{"page_count": 2}', encoding="utf-8")
            stale = client.get("/api/v1/books/mathanalysis-b05/pages")

        assert missing.status_code == 404
        assert "产物缺失" in missing.json()["detail"]
        assert stale.status_code == 404, "根 book.json 不再是回落来源"

    def test_missing_book_missing_effective_directory_and_bad_segment(self, tmp_path, monkeypatch):
        """404 书不存在 / 404 生效版本产物缺失（**不回落根视图**）/ 422 非法路径段。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            assert client.get("/api/v1/books/never-synced/pages").status_code == 404

            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            shutil.rmtree(self._book_dir(tmp_path) / "versions" / job_id)

            dropped = client.get("/api/v1/books/mathanalysis-b05/pages")
            unsafe = client.get("/api/v1/books/bad!id/pages")

        assert dropped.status_code == 404
        assert dropped.json()["detail"].startswith("生效版本产物缺失"), "不得静默回落 legacy 根视图"
        assert unsafe.status_code == 422


class TestParseVersions:
    """PLAN-004 / REQ-080 读取面：生效版本唯一指针、URL 契约不变、版本列举与重 ingest 共存。"""

    #: S4：根仓库前端硬编码的字面量模板——版本化改造后必须逐字不变。
    IMAGE_URL_TEMPLATE = "/api/v1/books/{book_id}/pages/{page_no}/image"

    def _book_dir(self, tmp_path) -> Path:
        return tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"

    def test_book_out_exposes_active_job_id(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] is None

            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]

            assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] == job_id
            listed = client.get("/api/v1/books").json()[0]
            assert listed["active_job_id"] == job_id

    def test_page_urls_and_image_url_literal_unchanged(self, tmp_path, monkeypatch):
        """S4：读取端点换的是目录，不是 URL；页图字面量模板逐字不变。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})

            payload = client.get("/api/v1/books/mathanalysis-b05/pages/1").json()

            assert payload["image_url"] == self.IMAGE_URL_TEMPLATE.format(book_id="mathanalysis-b05", page_no=1)
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1/image").status_code == 200

    def test_second_version_replaces_the_effective_one(self, tmp_path, monkeypatch):
        """S1：端点只出生效版；激活成功后被替换版本目录即删（磁盘只留生效正本，W4）。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            first = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            second = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [2]}
            ).json()["id"]

            assert second != first
            assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] == second
            page2 = client.get("/api/v1/books/mathanalysis-b05/pages/2").json()
            assert page2["blocks"]["blocks"][0]["text"] == "第 2 页重解析"
            # 未重解析的第 1 页由版本基线继承，端点继续可读（生效版本是完整快照）
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1").status_code == 200
            book_dir = self._book_dir(tmp_path)
            assert not (book_dir / "versions" / first).exists(), "W4：被替换版本随激活删除，不留第二正本"
            assert (book_dir / "versions" / second / "pages" / "p0002.blocks.json").is_file()
            assert (book_dir / "versions" / second / "pages" / "p0001.blocks.json").is_file(), (
                "生效版本自带继承页产物，可读性不依赖旧版本目录存活"
            )
            assert "重解析" in (book_dir / "versions" / second / "pages" / "p0002.md").read_text(
                encoding="utf-8"
            )

    def test_manifest_serves_the_effective_version(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]

            entries = client.get("/api/v1/books/mathanalysis-b05/manifest").json()

            paths = {entry["path"] for entry in entries}
            assert {"book.json", "chunks.json", "pages/p0001.png", "pages/p0001.blocks.json"} <= paths
            assert not [p for p in paths if p.startswith("versions/")], "根清单不递归历史版本"
            assert all("manifest.json" != p for p in paths)
            # 指纹对得上生效版本目录里的真实文件
            version_dir = self._book_dir(tmp_path) / "versions" / job_id
            entry = next(item for item in entries if item["path"] == "pages/p0001.blocks.json")
            assert entry["size"] == (version_dir / "pages" / "p0001.blocks.json").stat().st_size

    def test_versions_endpoint_lists_parse_versions(self, tmp_path, monkeypatch):
        """V9：版本列举供对照视图切换/审计（只读，不改变生效指针）。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            first = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            second = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [2]}
            ).json()["id"]

            versions = client.get("/api/v1/books/mathanalysis-b05/versions").json()

            assert [item["job_id"] for item in versions] == [first, second]
            assert [item["active"] for item in versions] == [False, True]
            assert versions[0]["engine"] == "mineru"
            assert versions[0]["status"] == "completed"
            assert versions[0]["page_count"] == 2
            assert versions[0]["parsed"] == 2
            assert len(versions[0]["source_sha256"]) == 64
            assert versions[0]["created_at"] is not None
            assert versions[1]["parsed"] == 2, "继承页计入该版本的已解析页数"

    def test_edits_are_written_against_the_effective_version(self, tmp_path, monkeypatch):
        """块编辑绑定其所属版本：换版本后旧编辑不污染新解析结果（V7）。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            first = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            edited = client.put(
                "/api/v1/books/mathanalysis-b05/pages/1/blocks/1/edit",
                json={"verdict": "bad", "note": "旧版判错"},
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["job_id"] == first

            second = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [1]}
            ).json()["id"]

            merged = client.get("/api/v1/books/mathanalysis-b05/pages/1").json()
            assert merged["edits"] == [], "新版本的块下标语义不同，旧编辑不得直接套用"
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1/edits").json() == []
            assert client.get(f"/api/v1/parse-jobs/{second}").status_code == 200

    def test_missing_effective_version_directory_returns_404(self, tmp_path, monkeypatch):
        """生效指针指向已被删掉的目录：显式 404，不静默回落根视图（ADR 0004 无兜底）。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            shutil.rmtree(self._book_dir(tmp_path) / "versions" / job_id)

            assert client.get("/api/v1/books/mathanalysis-b05/pages/1").status_code == 404
            assert client.get("/api/v1/books/mathanalysis-b05/manifest").status_code == 404
            assert client.get("/api/v1/books/mathanalysis-b05/chunks").status_code == 404

    def test_reingest_preserves_versions_and_falls_back_to_root_view(self, tmp_path, monkeypatch):
        """S6：重 ingest 只替换根视图；历史版本目录与页行保留，读取回到根视图。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            book_dir = self._book_dir(tmp_path)

            _ingest(client)

            assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] is None
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1").status_code == 404
            assert (book_dir / "versions" / job_id / "pages" / "p0001.blocks.json").is_file()
            with session_factory() as session:
                rows = session.scalars(select(AfPage).where(AfPage.job_id == job_id)).all()
            assert len(rows) == 2, "版本作用域页行不随重 ingest 删除（V8）"
            # 页图数量不翻倍（V4 回归钉）：重 ingest 重建根视图，版本目录共享同一批 inode
            root_pngs = sorted((book_dir / "pages").glob("p*.png"))
            assert len(root_pngs) == 2
            version_pngs = sorted((book_dir / "versions" / job_id / "pages").glob("p*.png"))
            assert len(version_pngs) == 2

    def test_purge_deletes_the_whole_book_tree(self, tmp_path, monkeypatch):
        """purge 删整本书（含 versions/），非 purge 只删行不动文件。"""
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]
            book_dir = self._book_dir(tmp_path)
            assert client.delete("/api/v1/books/mathanalysis-b05").status_code == 200
            assert book_dir.is_dir(), "非 purge 不删产物"

            _sync_book(client)
            _ingest(client)
            client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
            assert client.delete("/api/v1/books/mathanalysis-b05?purge=true").status_code == 200

            assert not book_dir.exists()
            with session_factory() as session:
                assert session.scalars(select(AfPage).where(AfPage.job_id == job_id)).all() == []


def test_manifest_json_is_readable_on_disk(client, tmp_path):
    """产物事实源：生效版本的 chunks.json 为合法 JSON 且含溯源字段。"""
    _sync_book(client)
    _ingest(client)
    job_id = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()["id"]

    book_dir = tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"
    payload = json.loads((book_dir / "versions" / job_id / "chunks.json").read_text(encoding="utf-8"))
    assert payload["book_id"] == "mathanalysis-b05"
    assert payload["chunks"][0]["block_refs"]



class TestEngineDispatch:
    """W4：引擎槽位显式分派——配置真正生效，且绝不静默回落到 MinerU（`MI-2`）。"""

    def test_omitted_engine_uses_configured_default(self, tmp_path, monkeypatch):
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
            assert created.status_code == 202, created.text
            assert created.json()["engine"] == "mineru"
            with session_factory() as session:
                job = OperationRepository(session).get(created.json()["id"])
            assert job is not None and job.engine == "mineru"

    def test_payload_engine_wins_over_config(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch, ocr_engine="mineru")
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "engine": "mineru"}
            )
            assert created.status_code == 202, created.text
            assert created.json()["engine"] == "mineru"

    def test_unimplemented_config_engine_returns_400_not_silent_mineru(self, tmp_path, monkeypatch):
        """D1 回归：`AXIOM_OCR_ENGINE=paddleocr-vl` 必须显式报错，不得回落到 MinerU。"""
        test_client, session_factory = _build(tmp_path, monkeypatch, ocr_engine="paddleocr-vl")
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
            assert created.status_code == 400, created.text
            assert "尚未实现" in created.json()["detail"]
            with session_factory() as session:
                assert _parse_rows(session) == []

    def test_unimplemented_payload_engine_returns_400(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "engine": "qwen-vl"}
            )
            assert created.status_code == 400, created.text

    def test_unknown_engine_literal_returns_422(self, tmp_path, monkeypatch):
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post(
                "/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "engine": "gpt-4o"}
            )
            assert created.status_code == 422, created.text


class TestSubmitTimeHealthProbe:
    """W5：提交期探活 503（受理前）与执行期 failed（受理后）两段并存（`api.md` 错误码表 503 行）。"""

    def test_model_down_returns_503_and_creates_no_job_row(self, tmp_path, monkeypatch):
        test_client, session_factory = _build(
            tmp_path, monkeypatch, engine_factory=lambda name: FakeEngine(healthy=False)
        )
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
            assert created.status_code == 503, created.text
            assert "模型服务不可用" in created.json()["detail"]
            with session_factory() as session:
                assert _parse_rows(session) == []

    def test_read_only_endpoints_unaffected_by_model_down(self, tmp_path, monkeypatch):
        test_client, _ = _build(
            tmp_path, monkeypatch, engine_factory=lambda name: FakeEngine(healthy=False)
        )
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 503
            assert client.get("/api/v1/health").status_code == 200
            assert client.get("/api/v1/books").status_code == 200
            assert client.get("/api/v1/books/mathanalysis-b05").status_code == 200
            # REQ-097：未解析过 = legacy 作用域，清单端点固定 404（不再回落根 manifest 重算）。
            assert client.get("/api/v1/books/mathanalysis-b05/manifest").status_code == 404
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1/image").status_code == 200
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1").status_code == 404


class TestDataBoundary:
    """W6：安全路径段与数据根边界（`PP-5`）。"""

    def test_sync_rejects_unsafe_path_segments(self, client):
        for payload in (
            {"book_id": "../evil", "title": "穿越"},
            {"book_id": "ok-1", "domain_id": "a/b", "title": "穿越"},
            {"book_id": ".hidden", "title": "隐藏段"},
            {"book_id": "ok-2", "file_path": "/etc/passwd"},
        ):
            response = client.post("/api/v1/books/sync", json=[payload])
            assert response.status_code == 422, payload

    def test_sync_accepts_fullwidth_punctuation_in_file_path(self, client):
        """REQ-083：全角标点不是遍历风险；一条含 `（）：` 的路径不得殃及整批 422。"""
        response = client.post(
            "/api/v1/books/sync",
            json=[
                {"book_id": "mathanalysis-b17", "file_path": "raw/数学分析（第2版）.pdf"},
                {"book_id": "probability-b03", "file_path": "raw/概率论：教程.pdf"},
            ],
        )

        assert response.status_code == 200, response.text
        assert sorted(book["book_id"] for book in response.json()["books"]) == [
            "mathanalysis-b17",
            "probability-b03",
        ]

    def test_parse_job_rejects_unsafe_book_id(self, client):
        response = client.post("/api/v1/parse-jobs", json={"book_id": "../evil"})
        assert response.status_code == 422, response.text


class TestIngestIdempotency:
    """W6：重复 ingest 回置解析状态并只清根视图页行；purge 删页行留任务行（`PM-16`）。"""

    def test_reingest_resets_parse_state_and_keeps_job_history(self, tmp_path, monkeypatch):
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
            job_id = created.json()["id"]
            assert created.json()["status"] == "completed"
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1").status_code == 200

            _add_region(session_factory, "mathanalysis-b05", page_no=2)
            _ingest(client)  # 重复 ingest：只原子替换根视图，人工框跨版本存活（ADR 0010 决定 1）
            with session_factory() as session:
                assert len(_intent_rows(session, OP_KIND_STRUCTURE)) == 1

            book = client.get("/api/v1/books/mathanalysis-b05").json()
            assert book["parse_status"] == "pending"
            assert book["pages_done"] == 0
            assert client.get("/api/v1/books/mathanalysis-b05/pages/1").status_code == 404
            assert client.get(f"/api/v1/parse-jobs/{job_id}").status_code == 200
            with session_factory() as session:
                assert (
                    session.scalars(
                        select(AfPage).where(AfPage.book_id == "mathanalysis-b05", AfPage.job_id == "legacy")
                    ).all()
                    == []
                )
                assert len(
                    session.scalars(
                        select(AfPage).where(AfPage.book_id == "mathanalysis-b05", AfPage.job_id == job_id)
                    ).all()
                ) == 2

    def test_purge_deletes_page_rows(self, tmp_path, monkeypatch):
        test_client, session_factory = _build(tmp_path, monkeypatch)
        with test_client as client:
            _sync_book(client)
            _ingest(client)
            _add_region(session_factory, "mathanalysis-b05")
            assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 202
            assert client.delete("/api/v1/books/mathanalysis-b05?purge=true").status_code == 200
            with session_factory() as session:
                assert session.scalars(select(AfPage)).all() == []
                assert len(_parse_rows(session)) == 1
                # 删书带走人工框：书行没了区域就是无主行，留着会在重建同 id 书时凭空复活旧框
                assert _intent_rows(session, OP_KIND_STRUCTURE) == []


# --------------------------------------------------------------------------- ④ 知识点梳理端点面


class _StubKpEngine:
    """梳理引擎桩：目录三条腿各记一次名，知识点每节一个点；`healthy` 只影响提交期探活那一条分支。

    目录腿的桩取值刻意走「判页挑中第一页、整理回一条 `depth=1`」这一形：端点面要证的是**调用形状**
    （判页一次＋逐页整理每页一次、`stage=outline` 时知识点腿零调用），而不是抽取质量——抽取判据的
    守护在 `tests/unit/test_knowledge_pipeline.py`。页码留 `None` 是把核对腿关在门外：坏行只取带
    印刷页码的那些，桩给了页码就会多出一条与端点面无关的核对调用。
    """

    name = "fake-text"

    def __init__(self, *, healthy: bool = True) -> None:
        self.healthy = healthy
        self.closed = False
        self.outline_calls: list[tuple[str, object]] = []
        self.section_calls: list[object] = []

    def health(self) -> bool:
        return self.healthy

    def close(self) -> None:
        self.closed = True

    def toc_pages(self, samples):
        self.outline_calls.append(("toc_pages", tuple(sample.page_no for sample in samples)))
        first = samples[0].page_no if samples else 1
        return TocPagePick(toc_pages=(first,))

    def toc_entries(self, page_no, text):
        self.outline_calls.append(("toc_entries", page_no))
        return [OutlineEntry(level=1, printed_page=None, name="第 1 章")]

    def toc_verify(self, items):
        self.outline_calls.append(("toc_verify", tuple(item.name for item in items)))
        return []

    def points(self, section):
        self.section_calls.append(section)
        return SectionPoints(
            [
                {
                    "name": f"{section.title} 的点",
                    "type": "concept",
                    "summary": "一句话总结",
                    "anchors": [{"page": section.page_start, "block_indexes": [0], "snippet": "回指正文"}],
                }
            ],
            f"{section.title} 这一讲讲的是什么。",
        )


class _BlockingKpEngine(_StubKpEngine):
    """把批次停在「已受理、一行未落」那一窗的桩：整理腿进去就阻塞，收到释放信号才出行。

    真机连吃 19 枚 404 的正是这一窗（`KA-40` 的原证）——判页与逐页整理都发生在落第一行之前，
    而批次号由受理方发在 202 里。批次结束的信号取 `close`：它是执行体 `finally` 的最后一手，
    流水行已经写完，用例因此不必靠轮询或睡眠判「跑完了」。

    但 `close` 不止执行体一家会按：**提交期探活**（`_probe_model_service` 的 `finally`）在受理当口
    就把同一枚桩关一次。信号因此只认「释放之后」的那一次关——释放前的一枚 close 属于探活，那时
    批次连整理腿都还没进。
    """

    def __init__(self, *, entered: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release
        self.finished = threading.Event()

    def toc_entries(self, page_no, text):
        self.entered.set()
        assert self.release.wait(5), "用例没释放：批次不该永远停在落行之前"
        return super().toc_entries(page_no, text)

    def close(self) -> None:
        super().close()
        if self.release.is_set():
            self.finished.set()


def _kp_build(
    tmp_path, monkeypatch, *, engine=None, kp_engine: str = "fake-text", knowledge_engine_factory=None, **kwargs
):
    """④ 面的装配：文本引擎桩＋槽位名（`kp_engine=""` 即槽位未接入）。返回 `(client, sf, stub)`。

    `knowledge_engine_factory` 是留给闭集守护的口子：桩接受任何名字，只有换上真工厂才测得到
    「配了一个没登记的名字」那一条形——桩替不了真登记表，正如它替不了 `KA-22` 的回落纪律。
    """
    stub = engine if engine is not None else _StubKpEngine()
    client, session_factory = _build(
        tmp_path,
        monkeypatch,
        kp_engine=kp_engine,
        knowledge_engine_factory=knowledge_engine_factory or (lambda name: stub),
        **kwargs,
    )
    return client, session_factory, stub


class TestKpCurationEndpoints:
    """④ 梳理端点面：受理门零写入、流水与在飞判据分列、背书与确认的并发次序、整批目录与导出序。"""

    BOOK = "mathanalysis-b05"

    def _live(self, tmp_path, monkeypatch, **kwargs):
        """sync → ingest → 全本解析：给出「有生效版本且逐页有产物」的书（受理门的最简前置）。"""
        client, session_factory, stub = _kp_build(tmp_path, monkeypatch, **kwargs)
        _parsed_book(client)
        return client, session_factory, stub

    def _submit(self, client, **extra) -> str:
        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK, **extra})
        assert response.status_code == 202, response.text
        return response.json()["gen_batch"]

    def _kp_count(self, session_factory) -> int:
        with session_factory() as session:
            return int(
                session.scalar(select(func.count()).select_from(AfKp).where(AfKp.book_id == self.BOOK)) or 0
            )

    def _ledger_count(self, session_factory) -> int:
        with session_factory() as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(AfOperation)
                    .where(AfOperation.book_id == self.BOOK, AfOperation.op_kind == OP_KIND_KP)
                )
                or 0
            )

    def _call_rows(self, session_factory) -> list[dict]:
        """`qed_llm_calls` 的读法：共享表不在 `Base.metadata` 里，只能按原始行读回。"""
        return read_ledger_rows(session_factory.kw["bind"])

    def _seed(self, session_factory, *, node_id: str, name: str, **fields) -> None:
        """直填一行（喂门与导出面的对手集合）：走机器腿 `create`，其余列取 `KpNodeRow` 默认。"""
        with session_factory() as session:
            KpRepository(session).create(
                KpNodeRow(
                    node_id=node_id,
                    book_id=self.BOOK,
                    name=name,
                    norm_key=norm_key_for(name),
                    **fields,
                )
            )
            session.commit()

    def _workspace(self, client) -> dict:
        response = client.get(f"/api/v1/books/{self.BOOK}/kp")
        assert response.status_code == 200, response.text
        return response.json()

    def _node(self, client, *, kind: str = "point") -> dict:
        rows = [node for node in self._workspace(client)["nodes"] if node["node_kind"] == kind]
        assert rows, f"工作区里没有 {kind} 行"
        return rows[0]

    # ---------------------------------------------------------------- 受理门

    def test_submission_lands_one_candidate_batch_and_one_ledger_row(self, tmp_path, monkeypatch):
        client, session_factory, stub = self._live(tmp_path, monkeypatch)
        accepted = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert body["status"] == "queued"
        assert body["gen_batch"].startswith("afkp-")
        assert body["page_start"] == 1 and body["page_end"] == 2

        progress = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{body['gen_batch']}")
        assert progress.status_code == 200, progress.text
        reading = progress.json()
        assert reading["in_flight"] is False
        assert reading["counts"]["candidate"] >= 1
        assert reading["counts"]["outline"] >= 1
        ledger = reading["ledger"]
        assert ledger["status"] == "ok"
        assert ledger["gen_batch"] == body["gen_batch"]
        assert ledger["job_id"] == body["evidence_job_id"]
        assert set(ledger["result"]) <= set(KP_RESULT_KEYS)
        assert ledger["result"]["candidate_rows"] == reading["counts"]["candidate"]
        assert ledger["created_at"] is not None
        # 目录阶段以模型为正腿（`KA-35` 改判）：判页一次＋逐页整理每页一次，知识点每节一次；跑完照常关闭
        assert [name for name, _ in stub.outline_calls] == ["toc_pages", "toc_entries"]
        assert len(stub.section_calls) >= 1
        assert stub.closed is True
        assert self._kp_count(session_factory) == reading["counts"]["candidate"]

    def test_review_note_rides_the_request_and_comes_back_on_the_progress_face(
        self, tmp_path, monkeypatch
    ):
        """AF-T5 甲・备注通道：受理收下人工评审意见、批次流水存档、进度面回显——零新列。

        备注落在 `payload`（这一批的**入参**）而不是 `note` 实体列：后者的语义是人工意图行的批注，
        与「这一批是照着哪句意见跑的」不是一回事。缺省空串而非缺键，读侧只维护一种形状。
        """
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        note = "第 3 章的节标题把书眉粘进来了，剔掉"
        accepted = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK, "review_note": note})
        assert accepted.status_code == 202, accepted.text
        gen_batch = accepted.json()["gen_batch"]

        reading = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{gen_batch}").json()
        assert reading["ledger"]["review_note"] == note
        with session_factory() as session:
            row = OperationRepository(session).find_kp_batch(self.BOOK, gen_batch)
            assert row.payload["review_note"] == note

        plain = self._submit(client)
        assert client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{plain}").json()["ledger"]["review_note"] == ""

        # 超长＝422 且当场拒（排在受理门之前，与页区间同一条纪律：非法请求不打模型也不落账）
        before = self._ledger_count(session_factory)
        over = client.post(
            "/api/v1/kp-jobs",
            json={"book_id": self.BOOK, "review_note": "长" * (KP_REVIEW_NOTE_MAX_CHARS + 1)},
        )
        assert over.status_code == 422
        assert self._ledger_count(session_factory) == before

    def test_stage_outline_runs_the_outline_leg_only(self, tmp_path, monkeypatch):
        """`KA-34`：`stage=outline` ⇒ 知识点阶段整条不发起（零点次调用、零点行），目录行照常落库。

        这一枚入参存在的原因＝人工要在屏上先看目录：知识点阶段每节一次调用，整本按几十到上百节估，
        没有只目录入参就得等整本跑完，或把页区间划窄——划窄得到的是一份不完整的目录。
        """
        client, session_factory, stub = self._live(tmp_path, monkeypatch)
        gen_batch = self._submit(client, stage="outline")

        reading = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{gen_batch}").json()
        assert stub.section_calls == [], "只目录批次不许碰知识点阶段"
        steps = [row["step"] for row in self._call_rows(session_factory)]
        assert steps == ["toc_pages", "toc_entries"], "调用账只含目录步（`KA-34`／`KA-35`）：知识点腿零笔"
        assert stub.closed is True, "收尾照跑、引擎照常回收"
        assert {node["node_kind"] for node in self._workspace(client)["nodes"]} == {"outline"}
        assert reading["counts"]["point"] == 0
        assert reading["counts"]["draft"] == 0, "三态不因 stage 分叉：本批照样翻到 candidate"
        assert reading["counts"]["candidate"] >= 1
        assert reading["ledger"]["stage"] == "outline"
        assert reading["ledger"]["review_note"] == ""
        with session_factory() as session:
            row = OperationRepository(session).find_kp_batch(self.BOOK, gen_batch)
        assert set(row.payload) == set(KP_PAYLOAD_KEYS), "入参闭集四键扩五键，不多不少（`KA-27`）"
        assert row.payload["stage"] == "outline", "受理值原样进 payload，读侧回声不是缺省值回填"

    def test_stage_defaults_to_full_and_rejects_values_out_of_the_closed_set(
        self, tmp_path, monkeypatch
    ):
        """缺省＝两腿全跑（既有调用方与用例不改期望）；域外值 422 且**零写入**（形状闸先于受理门）。"""
        client, session_factory, stub = self._live(tmp_path, monkeypatch)
        plain = self._submit(client)
        assert stub.section_calls, "缺省 `full` 逐字同旧行为"
        assert client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{plain}").json()["ledger"]["stage"] == "full"

        before_rows = self._kp_count(session_factory)
        before_ledger = self._ledger_count(session_factory)
        rejected = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK, "stage": "point"})
        assert rejected.status_code == 422
        assert self._kp_count(session_factory) == before_rows
        assert self._ledger_count(session_factory) == before_ledger

    def test_acceptance_gates_write_nothing(self, tmp_path, monkeypatch):
        client, session_factory, stub = _kp_build(tmp_path, monkeypatch)
        _sync_book(client)
        _ingest(client)
        assert client.post("/api/v1/kp-jobs", json={"book_id": "nope-not-here"}).status_code == 404
        # 有书目、有页图，但**无生效解析版本**：锚点将无处解释（KA-1），判 422 而不是回落根视图
        unparsed = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert unparsed.status_code == 422
        assert "生效解析版本" in unparsed.text
        # 请求体点名引擎＝绕过「未接入就拒」这道门，`extra="forbid"` 在 schema 层拒掉
        assert client.post(
            "/api/v1/kp-jobs", json={"book_id": self.BOOK, "engine": "gpt"}
        ).status_code == 422
        assert self._kp_count(session_factory) == 0
        assert self._ledger_count(session_factory) == 0
        assert stub.outline_calls == []

    def test_page_range_must_come_in_pairs_and_stay_inside_the_book(self, tmp_path, monkeypatch):
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        for payload in (
            {"page_start": 1},
            {"page_end": 2},
            {"page_start": 1, "page_end": 99},
            {"page_start": 2, "page_end": 1},
        ):
            response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK, **payload})
            assert response.status_code == 422, f"{payload} 应判页区间违规：{response.text}"
            assert "页区间" in response.text or "起止页" in response.text
        assert self._kp_count(session_factory) == 0
        assert self._ledger_count(session_factory) == 0

    def test_in_flight_batch_blocks_a_second_submission(self, tmp_path, monkeypatch):
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        self._seed(session_factory, node_id="afkp-draft9", name="残留草稿", gen_status="draft",
                   gen_batch="afkp-batch9")
        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert response.status_code == 409, response.text
        assert "afkp-batch9" in response.text
        assert self._kp_count(session_factory) == 1
        assert self._ledger_count(session_factory) == 0

    def test_unconfigured_text_slot_is_503_and_creates_no_batch(self, tmp_path, monkeypatch):
        """T1 甲随步：槽位默认值是内置实名，文案不再教人「请配置 `AXIOM_KP_ENGINE`」。

        清空默认值仍是 503（不回落成任何引擎），只是把话说回「服务侧接线」这一边。
        """
        client, session_factory, stub = self._live(tmp_path, monkeypatch, kp_engine="")
        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert response.status_code == 503, response.text
        assert "未接入" in response.text
        assert "请配置" not in response.text
        assert self._kp_count(session_factory) == 0
        assert self._ledger_count(session_factory) == 0
        assert stub.outline_calls == []

    def test_an_unknown_slot_name_is_503_as_well_and_creates_no_batch(self, tmp_path, monkeypatch):
        """闭集收在 `engines/factory.py`，端点不另写一份名字表：拿**真工厂**接线才证得到那条门。

        桩工厂替不了它——桩接受任何名字，误配就读成 202。HTTP 面仍是门槛表登记的那一个 503：
        请求体没有 `engine` 字段，误配的是服务侧环境，不该读成调用方的 400。
        """
        probe = _StubKpEngine()
        client, session_factory, stub = self._live(
            tmp_path,
            monkeypatch,
            kp_engine="gpt-4o",
            engine=probe,
            knowledge_engine_factory=lambda name: build_text_engine(name, Settings(data_dir=Path())),
        )
        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert response.status_code == 503, response.text
        assert "gpt-4o" in response.text
        assert self._kp_count(session_factory) == 0
        assert self._ledger_count(session_factory) == 0
        assert stub.outline_calls == []

    def test_the_default_wiring_reaches_the_slot_factory(self, tmp_path, monkeypatch):
        """缺省装配面：不注入 `knowledge_engine_factory` 时，端点走 `create_app` 里的那一条 `or` 分支。

        ④ 面其余用例恒注入桩工厂，正好把默认分支遮住——线上起服跑的却就是这条分支，少一个 import
        名字照样能在读回 202 之前一声不响。这里把真工厂换成**同签名**的假工厂，只证两件事：默认
        分支被接上、且它收到的是槽位名本身（不是别的什么）。
        """
        stub = _StubKpEngine()
        seen: list[str] = []

        def fake_build_text_engine(name, _settings):
            seen.append(name)
            return stub

        monkeypatch.setattr("axiom_flow.api.main.build_text_engine", fake_build_text_engine)
        client, session_factory = _build(tmp_path, monkeypatch, kp_engine=KP_SLOT_NAME)
        _parsed_book(client)

        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})

        assert response.status_code == 202, response.text
        assert seen and set(seen) == {KP_SLOT_NAME}  # 探活一枚、执行体一枚：两次都走默认分支
        assert stub.section_calls != []
        assert self._kp_count(session_factory) > 0

    def test_a_wired_slot_without_a_call_identity_is_503_and_creates_no_batch(self, tmp_path, monkeypatch):
        """R6 甲案的端点面：接了引擎却没给身份 ⇒ 受理期拒跑，读成 503（错在服务侧接线，不在请求体）。"""
        client, session_factory, stub = self._live(tmp_path, monkeypatch, kp_call_identity=None)
        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert response.status_code == 503, response.text
        assert "身份" in response.text
        assert self._kp_count(session_factory) == 0
        assert self._ledger_count(session_factory) == 0
        assert self._call_rows(session_factory) == []
        assert stub.outline_calls == []

    def test_a_submitted_batch_leaves_one_call_ledger_row_per_model_call(self, tmp_path, monkeypatch):
        """判据 10 的端点面：一次梳理作业在 `qed_llm_calls` 留下与模型调用一一对应的追加行。

        断言里连「不该有的列」一起看：`prompt`／`response` 留 NULL（真实串只有适配器知道，摘要不算），
        `review_status` 保持库侧默认（审核动作归 8900，本通路一列不碰）。
        """
        client, session_factory, stub = self._live(tmp_path, monkeypatch)
        gen_batch = self._submit(client)

        rows = self._call_rows(session_factory)
        # 目录两条正腿同样进账（`KA-23`／`KA-35` 改判）：账的顺序＝调用发生的顺序，知识点腿每节一笔
        assert [row["step"] for row in rows] == ["toc_pages", "toc_entries"] + ["points"] * len(stub.section_calls)
        assert {row["service"] for row in rows} == {"axiom_flow"}
        assert {row["endpoint"] for row in rows} == {"text"}
        assert {row["task"] for row in rows} == {"kp-curation"}
        assert {(row["mode"], row["provider"], row["model"]) for row in rows} == {
            ("local", "stub-runtime", "stub-text-model")
        }
        assert {json.loads(row["meta"])["gen_batch"] for row in rows} == {gen_batch}
        assert all(row["prompt"] is None and row["response"] is None for row in rows)
        assert {row["review_status"] for row in rows} == {"unreviewed"}

    def test_unavailable_text_service_is_503_at_submit_time(self, tmp_path, monkeypatch):
        client, session_factory, stub = self._live(tmp_path, monkeypatch, engine=_StubKpEngine(healthy=False))
        response = client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        assert response.status_code == 503, response.text
        assert stub.closed is True
        assert self._kp_count(session_factory) == 0
        assert self._ledger_count(session_factory) == 0

    def test_acceptance_gates_precede_the_engine_slot(self, tmp_path, monkeypatch):
        """非法请求不打模型，也不该先撞槽位门：书目与页区间判在槽位与探活之前。"""
        client, _, _ = self._live(tmp_path, monkeypatch, kp_engine="")
        assert client.post("/api/v1/kp-jobs", json={"book_id": "nope-not-here"}).status_code == 404
        assert client.post(
            "/api/v1/kp-jobs", json={"book_id": self.BOOK, "page_start": 1, "page_end": 99}
        ).status_code == 422

    # ---------------------------------------------------------------- 进度端点

    def test_progress_in_flight_reads_draft_rows_not_the_ledger_status(self, tmp_path, monkeypatch):
        """事件行禁作当前态判据（`KA-4`/`KA-9`）：流水写「ok」而 `draft` 行还在 ⇒ `in_flight` 必须为真。"""
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        gen_batch = self._submit(client)
        self._seed(session_factory, node_id="afkp-draft9", name="在飞行", gen_status="draft",
                   gen_batch=gen_batch)
        reading = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{gen_batch}").json()
        assert reading["ledger"]["status"] == "ok"
        assert reading["in_flight"] is True
        assert reading["counts"]["draft"] == 1

    def test_progress_of_a_batch_between_acceptance_and_landing_is_200_not_404(self, tmp_path, monkeypatch):
        """`KA-40` 的读面缺口：受理到落行之间零行零流水，进度面仍得 200＋在飞＋`ledger=null`。

        这一窗在真机上长达约 10 分钟（判页 152s ＋逐页整理 291s／枚），8903 每 1.5s 轮一次；判 404
        就是把活批次读成死链路，人工据此重复点钮（`afkp-8d5978d985eb` 原证：404 连吃 19 枚期间三笔
        调用全 `success`，批末照样产出 119 行）。批次必须真的跑在另一个线程里，所以这条开后台 worker：
        两条腿的完成信号都取假引擎的 `close`（执行体 `finally` 的最后一手），且都只认「真的干过活」
        的那一枚实例——探活也建引擎、也关引擎，不Gate就会在活儿没跑完时放行用例。
        """
        parsed = threading.Event()

        class _SignallingParseEngine(FakeEngine):
            """完成信号只由**真跑过解析**的那个实例发：提交期探活另建一枚引擎并在 `finally` 里
            `close` 它（`_probe_model_service`），照单全收就会在解析还没跑完时放行用例。"""

            def __init__(self, **kwargs) -> None:
                super().__init__(**kwargs)
                self.used = False

            def parse_document(self, pdf_bytes, filename, **kwargs):  # noqa: ANN001, ANN201
                self.used = True
                return super().parse_document(pdf_bytes, filename, **kwargs)

            def close(self) -> None:
                super().close()
                if self.used:
                    parsed.set()

        entered, release = threading.Event(), threading.Event()
        stub = _BlockingKpEngine(entered=entered, release=release)
        client, _, _ = _kp_build(
            tmp_path,
            monkeypatch,
            engine=stub,
            background_jobs=True,
            engine_factory=lambda name: _SignallingParseEngine(),
        )
        _sync_book(client)
        _ingest(client)
        assert client.post("/api/v1/parse-jobs", json={"book_id": self.BOOK}).status_code == 202
        assert parsed.wait(10), "解析没跑完：后台 worker 的最后一手是 close"
        assert client.get(f"/api/v1/books/{self.BOOK}").json()["active_job_id"], "生效版本没落，受理门会 422"

        gen_batch = self._submit(client)
        assert entered.wait(10), "整理腿没被发起：批次没停在落行之前"
        reading = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{gen_batch}")
        assert reading.status_code == 200, reading.text
        body = reading.json()
        assert body["in_flight"] is True
        assert body["ledger"] is None
        assert (body["counts"]["draft"], body["counts"]["outline"]) == (0, 0)

        release.set()
        assert stub.finished.wait(10), "批次没跑完：close 是执行体最后一手"
        after = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/{gen_batch}").json()
        assert after["ledger"]["status"] == "ok", after["ledger"]
        assert (after["counts"]["outline"], after["counts"]["draft"]) == (1, 0), after
        assert after["in_flight"] is False, "收尾是这批唯一的一次三态迁移：行与登记都该撤下"

    def test_progress_of_unknown_batch_is_404(self, tmp_path, monkeypatch):
        """反向判据（`KA-40` 不收的形）：行、流水、在途登记三者全无的批次号照旧 404——登记不是万能钥匙。"""
        client, _, _ = self._live(tmp_path, monkeypatch)
        response = client.get(f"/api/v1/books/{self.BOOK}/kp-jobs/afkp-never")
        assert response.status_code == 404
        assert "afkp-never" in response.text

    # ---------------------------------------------------------------- 工作区读面

    def test_workspace_does_not_follow_the_active_version_pointer(self, tmp_path, monkeypatch):
        """锚点按**行自身的 `job_id`** 解释（`KA-18`）：产物目录没了也不 404，只落 `review_flags`。"""
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        evidence_job_id = client.get(f"/api/v1/books/{self.BOOK}").json()["active_job_id"]
        shutil.rmtree(next((tmp_path / "data" / "parsed").rglob(evidence_job_id)))
        workspace = self._workspace(client)
        assert workspace["nodes"], "换产物不换知识点行：一行都不该因产物缺失而消失"
        assert any("缺该页产物" in flag["reason"] for flag in workspace["review_flags"])
        assert workspace["in_flight_batch"] is None
        # 对照：走生效指针的读端点仍然 404，两者不是同一作用域入口
        assert client.get(f"/api/v1/books/{self.BOOK}/manifest").status_code == 404

    def test_workspace_of_missing_book_is_404_and_empty_book_is_200(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        assert client.get("/api/v1/books/nope-not-here/kp").status_code == 404
        empty = self._workspace(client)
        assert empty["nodes"] == [] and empty["in_flight_batch"] is None

    # ---------------------------------------------------------------- 背书 PATCH

    def test_patch_writes_only_the_keys_present_in_the_body(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        node = self._node(client)
        before = node["rev"]
        patched = client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}", json={"summary": "改过的摘要", "if_rev": before}
        )
        assert patched.status_code == 200, patched.text
        after = patched.json()
        assert after["summary"] == "改过的摘要"
        assert after["name"] == node["name"] and after["anchors"] == node["anchors"]
        assert after["rev"] == before + 1
        # 显式 null 与缺席两件事：给出 `parent_id: null` 就是退回未归档池
        retreated = client.patch(f"/api/v1/kp/nodes/{node['node_id']}", json={"parent_id": None}).json()
        assert retreated["parent_id"] is None
        assert retreated["summary"] == "改过的摘要"

    def test_patch_with_stale_if_rev_is_409(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        node = self._node(client)
        response = client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}", json={"summary": "旧表单", "if_rev": node["rev"] + 5}
        )
        assert response.status_code == 409, response.text
        assert client.get(f"/api/v1/books/{self.BOOK}/kp").json()["nodes"]

    def test_patch_rejects_unknown_node_and_foreign_book_fields(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        assert client.patch("/api/v1/kp/nodes/afkp-never", json={"summary": "x"}).status_code == 404
        self._submit(client)
        node = self._node(client)
        # 来源三列与确认位不在请求模型里：改不动是形状保证，不是运行期判断（`KA-11`）
        for field in ("gen_status", "gen_batch", "model_ref", "confirmed", "book_id", "node_kind"):
            assert client.patch(f"/api/v1/kp/nodes/{node['node_id']}", json={field: "x"}).status_code == 422

    def test_patch_rejects_edges_that_break_the_server_invariants(self, tmp_path, monkeypatch):
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        node = self._node(client)
        missing = client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}",
            json={"links": [{"kind": "prerequisite", "to": "afkp-nope"}]},
        )
        assert missing.status_code == 422, missing.text
        assert "afkp-nope" in missing.text
        selfloop = client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}",
            json={"links": [{"kind": "extends", "to": node["node_id"]}]},
        )
        assert selfloop.status_code == 422
        assert "自环" in selfloop.text
        # 对岸那行用固定 id 直填：成环的两条边必须分属两行，否则「自环」会先把环那一问遮蔽掉
        self._seed(session_factory, node_id="afkp-peer9", name="对岸的点")
        first = client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}",
            json={"links": [{"kind": "prerequisite", "to": "afkp-peer9"}]},
        )
        assert first.status_code == 200, first.text
        closing = client.patch(
            "/api/v1/kp/nodes/afkp-peer9",
            json={"links": [{"kind": "prerequisite", "to": node["node_id"]}]},
        )
        assert closing.status_code == 422, closing.text
        assert "环" in closing.text

    def test_patch_rejects_a_type_on_an_outline_row(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        outline = self._node(client, kind="outline")
        response = client.patch(f"/api/v1/kp/nodes/{outline['node_id']}", json={"type": "concept"})
        assert response.status_code == 422, response.text
        assert client.get(f"/api/v1/books/{self.BOOK}/kp").json()

    # ---------------------------------------------------------------- 确认与撤回

    def test_confirm_names_every_missing_endorsement_and_writes_nothing(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        node = self._node(client)
        stripped = client.patch(f"/api/v1/kp/nodes/{node['node_id']}", json={"anchors": []}).json()
        assert stripped["anchors"] == []
        denied = client.post(f"/api/v1/kp/nodes/{node['node_id']}/confirm", json={"confirmed": True})
        assert denied.status_code == 422, denied.text
        assert denied.json()["missing"], "422 的正文必须逐条点名缺的是什么"
        assert denied.json()["confirmed"] == 0
        assert denied.json()["rev"] == stripped["rev"]
        out_of_range = client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}",
            json={"anchors": [{"kind": "page_block", "page": 99, "block_indexes": [0]}]},
        )
        assert out_of_range.status_code == 200, out_of_range.text
        second = client.post(f"/api/v1/kp/nodes/{node['node_id']}/confirm", json={"confirmed": True})
        assert second.status_code == 422, second.text
        assert any("页集" in item for item in second.json()["missing"])

    def test_confirm_conflict_precedes_the_shape_check(self, tmp_path, monkeypatch):
        """旧表单的冲突不得读成「行不合形状」的 422：`rev` 比对排在可回指校验之前。"""
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        node = self._node(client)
        client.patch(f"/api/v1/kp/nodes/{node['node_id']}", json={"anchors": []})
        response = client.post(
            f"/api/v1/kp/nodes/{node['node_id']}/confirm",
            json={"confirmed": True, "if_rev": node["rev"] + 3},
        )
        assert response.status_code == 409, response.text

    def test_confirm_then_withdraw_is_always_possible(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        node = self._node(client)
        confirmed = client.post(f"/api/v1/kp/nodes/{node['node_id']}/confirm", json={"confirmed": True})
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["confirmed"] == 1
        assert confirmed.json()["rev"] == node["rev"] + 1
        # 撤坏锚的行同样能撤确认（`KA-17`：撤回永远可执行，不能卡在已确认态）
        client.patch(
            f"/api/v1/kp/nodes/{node['node_id']}",
            json={"anchors": [{"kind": "page_block", "page": 99, "block_indexes": [0]}]},
        )
        withdrawn = client.post(f"/api/v1/kp/nodes/{node['node_id']}/confirm", json={"confirmed": False})
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["confirmed"] == 0
        assert self._node(client, kind="point")["gen_status"] == "candidate"

    def test_confirm_of_unknown_node_is_404(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        response = client.post("/api/v1/kp/nodes/afkp-never/confirm", json={"confirmed": True})
        assert response.status_code == 404

    # ---------------------------------------------------------------- 目录整批声明

    def test_outline_declaration_detaches_children_of_absent_entries_and_deletes_nothing(
        self, tmp_path, monkeypatch
    ):
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        kept = self._node(client, kind="outline")
        self._seed(session_factory, node_id="afkp-o9", name="第 9 章", node_kind="outline", depth=1,
                   order_hint=9)
        point = self._node(client, kind="point")
        client.patch(f"/api/v1/kp/nodes/{point['node_id']}", json={"parent_id": "afkp-o9"})

        declared = client.put(
            f"/api/v1/books/{self.BOOK}/kp/outline",
            json={"declarations": [{"node_id": kept["node_id"], "order_hint": 1}]},
        )
        assert declared.status_code == 200, declared.text
        body = declared.json()
        assert [node["node_id"] for node in body["outline"]] == [kept["node_id"]]
        assert body["detached_points"] == 1

        nodes = self._workspace(client)["nodes"]
        ids = {node["node_id"] for node in nodes}
        assert "afkp-o9" in ids, "缺席只降级，不下线：目录行不删、不改身份"
        absent = next(node for node in nodes if node["node_id"] == "afkp-o9")
        assert absent["name"] == "第 9 章" and absent["order_hint"] == 9
        moved = next(node for node in nodes if node["node_id"] == point["node_id"])
        assert moved["parent_id"] is None

        again = client.put(
            f"/api/v1/books/{self.BOOK}/kp/outline",
            json={"declarations": [{"node_id": kept["node_id"], "order_hint": 1}]},
        ).json()
        assert again["detached_points"] == 0, "重复提交同一份声明结果不变（幂等，无需 undo 端点）"

    def test_outline_declaration_rejects_bad_shape_before_any_write(self, tmp_path, monkeypatch):
        client, session_factory, _ = self._live(tmp_path, monkeypatch)
        gen_batch = self._submit(client)
        kept = self._node(client, kind="outline")
        point = self._node(client, kind="point")
        before = {
            node["node_id"]: (node["parent_id"], node["order_hint"], node["rev"])
            for node in self._workspace(client)["nodes"]
        }
        for payload in (
            [{"node_id": "afkp-nope"}],
            [{"node_id": point["node_id"], "order_hint": 7}],
            [{"node_id": kept["node_id"], "parent_id": kept["node_id"], "order_hint": 1}],
        ):
            response = client.put(
                f"/api/v1/books/{self.BOOK}/kp/outline", json={"declarations": payload}
            )
            assert response.status_code == 422, f"{payload} 应整批拒收：{response.text}"
        assert self._workspace(client)["nodes"] and before == {
            node["node_id"]: (node["parent_id"], node["order_hint"], node["rev"])
            for node in self._workspace(client)["nodes"]
        }
        assert self._kp_count(session_factory) == len(before)
        assert self._ledger_count(session_factory) == 1
        assert gen_batch

    # ---------------------------------------------------------------- 只读导出面

    def test_export_gives_confirmed_rows_only_with_parents_first(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        self._submit(client)
        empty = client.get(f"/api/v1/books/{self.BOOK}/kp/export")
        assert empty.status_code == 200, empty.text
        assert empty.json()["nodes"] == [] and empty.json()["total"] == 0

        outline = self._node(client, kind="outline")
        point = self._node(client, kind="point")
        client.post(f"/api/v1/kp/nodes/{point['node_id']}/confirm", json={"confirmed": True})
        solo = client.get(f"/api/v1/books/{self.BOOK}/kp/export").json()
        assert [node["node_id"] for node in solo["nodes"]] == [point["node_id"]]
        assert solo["nodes"][0]["rev"] >= 1

        client.post(f"/api/v1/kp/nodes/{outline['node_id']}/confirm", json={"confirmed": True})
        both = client.get(f"/api/v1/books/{self.BOOK}/kp/export").json()
        assert [node["node_id"] for node in both["nodes"]] == [outline["node_id"], point["node_id"]]
        assert {node["confirmed"] for node in both["nodes"]} == {1}
        assert client.get("/api/v1/books/nope-not-here/kp/export").status_code == 404


    # ---------------------------------------------------------------- 单点重生成

    def test_regenerating_a_point_summary_overwrites_it_and_returns_the_new_rev(
        self, tmp_path, monkeypatch
    ):
        """`KA-45`：人工按「重新生成总结」⇒ 覆盖该行的 `summary`、`rev` 递增，一次模型调用随响应回来。"""
        client, session_factory, stub = self._live(tmp_path, monkeypatch)
        self._seed(
            session_factory,
            node_id="afkp-point1",
            name="1.1 数列极限",
            node_kind="outline",
            depth=2,
            order_hint=1,
            anchors=[{"page": 1, "block_indexes": [0], "snippet": "回指正文"}],
        )
        node = self._node(client, kind="outline")
        assert node["node_type"] == "point"

        response = client.post(
            f"/api/v1/kp/nodes/{node['node_id']}/summary", json={"if_rev": node["rev"]}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"] == "1.1 数列极限 这一讲讲的是什么。"
        assert body["rev"] == node["rev"] + 1
        assert stub.section_calls and stub.section_calls[-1].title == "1.1 数列极限"

    def test_regenerating_a_chapter_or_an_exercise_summary_is_422(self, tmp_path, monkeypatch):
        """只有「知识点」这一型可重生成：章节走内容批次的章腿，习题与其他的令文口径是「什么都不需要」。"""
        client, session_factory, stub = self._live(tmp_path, monkeypatch)
        self._seed(
            session_factory,
            node_id="afkp-ch1",
            name="第 1 章 极限",
            node_kind="outline",
            depth=1,
            order_hint=1,
            anchors=[{"page": 1, "block_indexes": [0], "snippet": "回指正文"}],
        )
        self._seed(
            session_factory,
            node_id="afkp-ex1",
            name="习题一",
            node_kind="outline",
            depth=2,
            order_hint=2,
            anchors=[{"page": 2, "block_indexes": [0], "snippet": "回指正文"}],
            meta={"toc_role": "reference", "toc_kind": "exercise"},
        )

        for node_id in ("afkp-ch1", "afkp-ex1"):
            response = client.post(f"/api/v1/kp/nodes/{node_id}/summary", json={})
            assert response.status_code == 422, response.text
            assert "只有知识点可以重生成总结" in response.json()["detail"]
        assert stub.section_calls == [], "类型不合不发起模型调用"

    def test_regenerating_an_unknown_node_is_404(self, tmp_path, monkeypatch):
        client, _, _ = self._live(tmp_path, monkeypatch)
        assert client.post("/api/v1/kp/nodes/afkp-never/summary", json={}).status_code == 404


class TestKpNarrativeLegs:
    """`KA-30` 长文两条腿：判定序、409 先于落盘、先落盘再落列、缺文件的 200 披露位。

    路径在测试里按 `KA-20` 的派生式**自己算一遍**（`parsed/<domain>/<course>/<book>/kp/<node_id>.md`），
    不沿用实现的私设基准——jail 挪了位就转红。两条腿读写的必须是同一个文件，所以两侧共用这条算式。
    """

    BOOK = "mathanalysis-b05"
    BODY = "学什么\n\nε-δ 语言把「无限接近」写成有限不等式。\n\n为什么要学\n\n后续极限定理都靠它。"

    def _live(self, tmp_path, monkeypatch):
        client, session_factory, _ = _kp_build(tmp_path, monkeypatch)
        _parsed_book(client)
        client.post("/api/v1/kp-jobs", json={"book_id": self.BOOK})
        return client, session_factory

    def _file(self, tmp_path, node_id: str) -> Path:
        return (
            tmp_path
            / "data"
            / "parsed"
            / "math-advanced"
            / "math_analysis"
            / self.BOOK
            / "kp"
            / f"{node_id}.md"
        )

    def _node(self, client, *, kind: str = "point") -> dict:
        response = client.get(f"/api/v1/books/{self.BOOK}/kp")
        rows = [node for node in response.json()["nodes"] if node["node_kind"] == kind]
        assert rows, f"工作区里没有 {kind} 行"
        return rows[0]

    def _row(self, session_factory, node_id: str) -> tuple[str, int]:
        """库内侧读数：`(narrative_ref, rev)`——列有没有被写、序号动不动，不看响应镜像。"""
        with session_factory() as session:
            node = KpRepository(session).get(node_id)
            assert node is not None
            return node.narrative_ref, node.rev

    def _workspace_nodes(self, client) -> list[dict]:
        return client.get(f"/api/v1/books/{self.BOOK}/kp").json()["nodes"]

    # ---------------------------------------------------------------- 写腿

    def test_write_lands_the_file_and_the_pointer_and_echoes_the_body(self, tmp_path, monkeypatch):
        """200 回显＝写完不必再发一次读腿；`bytes` 按 UTF-8 计，与正文的字符数不同口径。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        before = node["rev"]

        response = client.put(
            f"/api/v1/kp/nodes/{node['node_id']}/narrative",
            json={"content": self.BODY, "if_rev": before},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["node_id"] == node["node_id"]
        assert body["narrative_ref"] == f"{node['node_id']}.md"
        assert body["exists"] is True and body["content"] == self.BODY
        assert body["bytes"] == len(self.BODY.encode("utf-8"))
        assert body["rev"] == before + 1 and body["updated_at"] is not None
        assert self._file(tmp_path, node["node_id"]).read_text(encoding="utf-8") == self.BODY
        assert self._row(session_factory, node["node_id"]) == (f"{node['node_id']}.md", before + 1)

    def test_the_read_leg_returns_the_same_file_the_write_leg_wrote(self, tmp_path, monkeypatch):
        """两条腿共用同一条派生式：写进去与读回来必须是同一篇，`GET` 不修列也不动序号。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        client.put(f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": self.BODY})

        read = client.get(f"/api/v1/kp/nodes/{node['node_id']}/narrative")

        assert read.status_code == 200, read.text
        assert read.json()["content"] == self.BODY and read.json()["exists"] is True
        assert self._row(session_factory, node["node_id"]) == (f"{node['node_id']}.md", node["rev"] + 1)

    def test_rewriting_the_same_body_is_idempotent_on_disk_and_bumps_rev(self, tmp_path, monkeypatch):
        """重发一次即收敛：文件名由 `node_id` 派生、同名覆盖，目录里始终只有那一篇。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        first = client.put(
            f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": "第一版", "if_rev": node["rev"]}
        )
        assert first.status_code == 200, first.text

        second = client.put(
            f"/api/v1/kp/nodes/{node['node_id']}/narrative",
            json={"content": "第二版", "if_rev": first.json()["rev"]},
        )

        assert second.status_code == 200, second.text
        assert second.json()["content"] == "第二版"
        assert second.json()["rev"] == first.json()["rev"] + 1
        directory = self._file(tmp_path, node["node_id"]).parent
        assert [path.name for path in directory.iterdir()] == [f"{node['node_id']}.md"]

    def test_both_kinds_carry_their_own_narrative(self, tmp_path, monkeypatch):
        """章与点各有自己的详解（`KA-30` 的边界：`node_kind` 两种都能写）。"""
        client, _ = self._live(tmp_path, monkeypatch)
        outline = self._node(client, kind="outline")
        point = self._node(client, kind="point")

        for node, text in ((outline, "章的详解"), (point, "点的详解")):
            response = client.put(
                f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": text}
            )
            assert response.status_code == 200, response.text
            assert self._file(tmp_path, node["node_id"]).read_text(encoding="utf-8") == text

    # ---------------------------------------------------------------- 判定序

    def test_stale_if_rev_is_409_and_precedes_every_disk_write(self, tmp_path, monkeypatch):
        """409 排在落盘之前：磁盘写入不可回滚，旧表单先把文件改了再被序号拒就是没人认领的正文。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)

        response = client.put(
            f"/api/v1/kp/nodes/{node['node_id']}/narrative",
            json={"content": "旧表单写的正文", "if_rev": node["rev"] + 5},
        )

        assert response.status_code == 409, response.text
        assert str(node["rev"]) in response.text and str(node["rev"] + 5) in response.text
        assert not self._file(tmp_path, node["node_id"]).exists()
        assert self._row(session_factory, node["node_id"]) == ("", node["rev"])

    def test_empty_or_oversized_body_is_422_and_leaves_no_trace(self, tmp_path, monkeypatch):
        """超限判 422 **不截断**（与 `summary[:1000]` 的截断方向相反）：拒收的表单磁盘与库里都不动。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)

        for content in ("", "x" * (NARRATIVE_MAX_CHARS + 1), "x" * (NARRATIVE_MAX_CHARS * 2)):
            response = client.put(
                f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": content}
            )
            assert response.status_code == 422, f"{len(content)} 字应判 422：{response.text[:200]}"
        assert not self._file(tmp_path, node["node_id"]).exists()
        assert self._row(session_factory, node["node_id"]) == ("", node["rev"])

    def test_the_cap_is_inclusive(self, tmp_path, monkeypatch):
        """上限是「恰好等于可写」：`max_length` 与判据同一条边界，写成 `>` 就少一个字。"""
        client, _ = self._live(tmp_path, monkeypatch)
        node = self._node(client)

        response = client.put(
            f"/api/v1/kp/nodes/{node['node_id']}/narrative",
            json={"content": "x" * NARRATIVE_MAX_CHARS},
        )

        assert response.status_code == 200, response.text[:200]
        assert response.json()["bytes"] == NARRATIVE_MAX_CHARS

    def test_the_shape_gate_runs_before_the_row_lookup(self, tmp_path, monkeypatch):
        """形状闸先于查行是 schema 层的事实，不是实现漏洞：空正文的表单不该多花一次查行。"""
        client, _ = self._live(tmp_path, monkeypatch)
        assert client.put("/api/v1/kp/nodes/afkp-never/narrative", json={"content": ""}).status_code == 422

    def test_missing_row_is_404_and_neither_leg_touches_the_disk(self, tmp_path, monkeypatch):
        client, session_factory = self._live(tmp_path, monkeypatch)
        write = client.put("/api/v1/kp/nodes/afkp-never/narrative", json={"content": "正文"})
        read = client.get("/api/v1/kp/nodes/afkp-never/narrative")

        assert write.status_code == 404 and "afkp-never" in write.text
        assert read.status_code == 404 and "afkp-never" in read.text
        assert not (tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / self.BOOK / "kp").exists()

    def test_the_request_model_gives_no_way_to_name_a_path(self, tmp_path, monkeypatch):
        """jail 一字不放宽：文件名与路径都不是调用方能给的东西（`extra="forbid"` 的形状保证）。"""
        client, _ = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        for field in ("path", "narrative_ref", "node_id", "book_id", "confirmed", "gen_batch"):
            response = client.put(
                f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": "正文", field: "x"}
            )
            assert response.status_code == 422, f"{field} 不该出现在请求模型里：{response.text}"

    # ---------------------------------------------------------------- 缺文件的披露位

    def test_missing_file_reads_as_exists_false_without_touching_columns(self, tmp_path, monkeypatch):
        """200 带 `exists=false` 而不是 404：把「行在而正文缺」报成「节点不存在」会让界面谎报行本身。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        written = client.put(f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": self.BODY})
        pointer, rev = self._row(session_factory, node["node_id"])
        self._file(tmp_path, node["node_id"]).unlink()

        response = client.get(f"/api/v1/kp/nodes/{node['node_id']}/narrative")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["exists"] is False and body["content"] == "" and body["bytes"] == 0
        assert body["narrative_ref"] == pointer and body["rev"] == rev
        assert self._row(session_factory, node["node_id"]) == (pointer, rev)
        assert written.json()["narrative_ref"] == pointer

    def test_a_row_that_never_had_a_narrative_reads_as_exists_false(self, tmp_path, monkeypatch):
        """列为空是可读现状不是错误：新梳理出来的行本来就还没有详解。"""
        client, _ = self._live(tmp_path, monkeypatch)
        node = self._node(client)

        response = client.get(f"/api/v1/kp/nodes/{node['node_id']}/narrative")

        assert response.status_code == 200, response.text
        assert response.json()["exists"] is False and response.json()["narrative_ref"] == ""

    def test_a_file_on_disk_reads_under_its_derived_name_even_with_an_empty_column(self, tmp_path, monkeypatch):
        """读腿读的是**文件**：磁盘上有那一篇就按派生名回，列还没落也不圆场成 `exists=false`。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        path = self._file(tmp_path, node["node_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("磁盘上先行存在的一篇", encoding="utf-8")

        response = client.get(f"/api/v1/kp/nodes/{node['node_id']}/narrative")

        assert response.status_code == 200, response.text
        assert response.json()["exists"] is True and response.json()["content"] == "磁盘上先行存在的一篇"
        assert response.json()["narrative_ref"] == f"{node['node_id']}.md"
        assert self._row(session_factory, node["node_id"])[0] == ""

    def test_the_workspace_still_carries_no_bodies(self, tmp_path, monkeypatch):
        """整册工作区不带正文（`KA-30` 的边界）：长文只能逐篇取，一次一篇是形状决定。"""
        client, _ = self._live(tmp_path, monkeypatch)
        node = self._node(client)
        client.put(f"/api/v1/kp/nodes/{node['node_id']}/narrative", json={"content": self.BODY})

        rows = self._workspace_nodes(client)

        assert node["node_id"] in [row["node_id"] for row in rows]
        for row in rows:
            assert "content" not in row and "exists" not in row and "bytes" not in row


PURGE_URL = "/api/v1/admin/purge"
PURGE_CONFIRM = "PURGE"
ADMIN_TOKEN = "af-admin-contract-token"
B05_DIR = "parsed/math-advanced/math_analysis/mathanalysis-b05"
B01_DIR = "parsed/math-advanced/math_analysis/linearalgebra-b01"


def _admin_headers(token: str = ADMIN_TOKEN) -> dict[str, str]:
    return {"X-QED-Admin-Token": token}


def _admin_build(tmp_path, monkeypatch, *, token: str = ADMIN_TOKEN):
    return _build(tmp_path, monkeypatch, admin_enabled=True, admin_token=token)


def _fail_rmtree(*_args, **_kwargs):
    raise OSError("磁盘忙（测试注入）")


class TestAdminPurgeEndpoint:
    """非公开管理面 `POST /api/v1/admin/purge`（PLAN-010 / ARCH-024）：S1~S6 逐条守护。

    隔离与其余契约测试同一（SQLite + 临时数据根 + 假引擎）；管理面开关在测试里**显式**传入，
    默认态由 `test_admin_route_not_mounted_by_default` 反向守护。
    """

    @staticmethod
    def _book_root(tmp_path, book_id: str, domain_id: str = "math-advanced") -> Path:
        return tmp_path / "data" / "parsed" / domain_id / "math_analysis" / book_id

    def _seed(self, client: TestClient, book_id: str, *, domain_id: str = "math-advanced") -> str:
        """sync → ingest → 解析（假引擎同步执行完）→ 一条块编辑，返回 job_id。"""
        synced = client.post(
            "/api/v1/books/sync",
            json=[
                {
                    "book_id": book_id,
                    "domain_id": domain_id,
                    "course_id": "math_analysis",
                    "course_name": "数学分析",
                    "knowledge_id": "kt-mathanalysis-3",
                    "title": f"书目 {book_id}",
                    "part": "教程",
                    "display_title": f"展示 {book_id}",
                    "authors": [{"name": "Walter Rudin", "role": "author"}],
                    "file_path": "raw/math-advanced/math_analysis/book.pdf",
                }
            ],
        )
        assert synced.status_code == 200, synced.text
        _ingest(client, book_id)
        created = client.post("/api/v1/parse-jobs", json={"book_id": book_id})
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        edited = client.put(
            f"/api/v1/books/{book_id}/pages/1/blocks/0/edit",
            json={"verdict": "ok", "note": "已核对"},
        )
        assert edited.status_code == 200, edited.text
        return job_id

    def _counts(self, session_factory) -> dict[str, int]:
        """表族行数快照：表集合由 `AF_TABLES` 派生（加表不必改本方法，PLAN-020 S2）。"""
        with session_factory() as session:
            return {
                table.name: int(session.scalar(select(func.count()).select_from(table)))
                for table in AF_TABLES
            }

    def _add_job(self, session_factory, book_id: str, job_id: str, status: str) -> None:
        with session_factory() as session:
            OperationRepository(session).create(
                ParseJobRow(job_id=job_id, book_id=book_id, engine="mineru", status=status)
            )
            session.commit()

    def test_admin_route_not_mounted_by_default(self, tmp_path, monkeypatch):
        """S1：默认不挂载 ⇒ 404（不是 405/403），且 OpenAPI 不收录该路径。"""
        test_client, _ = _build(tmp_path, monkeypatch)
        with test_client as client:
            response = client.post(PURGE_URL, json={"scope": {"all": True}})

            assert response.status_code == 404
            assert PURGE_URL not in client.get("/openapi.json").json()["paths"]

    def test_enabled_without_token_fails_at_startup(self, tmp_path, monkeypatch):
        """S2 反向：启用却无 token ⇒ 拒绝启动，不静默放行成「无凭据管理面」。"""
        with pytest.raises(AdminConfigError):
            _build(tmp_path, monkeypatch, admin_enabled=True, admin_token="")

    def test_missing_and_wrong_token_are_401(self, tmp_path, monkeypatch):
        test_client, _ = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            body = {"scope": {"all": True}}

            assert client.post(PURGE_URL, json=body).status_code == 401
            assert client.post(PURGE_URL, json=body, headers=_admin_headers("nope")).status_code == 401

    def test_dry_run_is_default_and_touches_nothing(self, tmp_path, monkeypatch):
        """S3：缺省即演练 ⇒ 给将删清单与计数，磁盘与库零改动。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._seed(client, "linearalgebra-b01")
            before = self._counts(session_factory)

            response = client.post(PURGE_URL, json={"scope": {"all": True}}, headers=_admin_headers())

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["dry_run"] is True
            assert sorted(payload["targets"]) == sorted([B05_DIR, B01_DIR])
            assert payload["deleted"]["af_books"] == before["af_books"]
            assert payload["deleted"]["directories"] == 2
            assert payload["failures"] == []
            assert self._counts(session_factory) == before
            assert self._book_root(tmp_path, "mathanalysis-b05").is_dir()

    def test_real_delete_requires_verbatim_confirm(self, tmp_path, monkeypatch):
        """S3 第二道闸：`dry_run:false` 还须逐字 `confirm`——缺省与小写都不算确认。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            before = self._counts(session_factory)

            missing = client.post(
                PURGE_URL, json={"scope": {"all": True}, "dry_run": False}, headers=_admin_headers()
            )
            wrong = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": "purge"},
                headers=_admin_headers(),
            )

            assert missing.status_code == 400
            assert wrong.status_code == 400
            assert self._counts(session_factory) == before
            assert self._book_root(tmp_path, "mathanalysis-b05").is_dir()

    def test_book_scope_purge_deletes_table_family_and_dir(self, tmp_path, monkeypatch):
        """按书目清除：表族每一张表的行一起走（`af_operations` 带走 parse 事件行与 edit/structure 意图行），他书完好。

        structure 意图行是 S5 删除面同构的判据行：人工框按 `(book_id, page_no)` 作用域、不绑版本，
        折进 `af_operations` 之后它与 parse 行同表异 `op_kind`，因此删书必须整表按书清，否则人工框
        会变成无主行永久残留。
        """
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._seed(client, "linearalgebra-b01")
            # 他书有在飞任务：按本清除只守自己作用域，不被别书阻塞
            self._add_job(session_factory, "linearalgebra-b01", "job-other-queued", "queued")
            _add_region(session_factory, "mathanalysis-b05")
            _add_region(session_factory, "linearalgebra-b01")

            response = client.post(
                PURGE_URL,
                json={
                    "scope": {"book_ids": ["mathanalysis-b05"]},
                    "dry_run": False,
                    "confirm": PURGE_CONFIRM,
                },
                headers=_admin_headers(),
            )

            assert response.status_code == 200, response.text
            assert response.json()["deleted"] == {
                "af_books": 1,
                "af_operations": 3,
                "af_pages": 2,
                "af_kp": 0,
                "directories": 1,
            }
            assert not self._book_root(tmp_path, "mathanalysis-b05").exists()
            assert self._book_root(tmp_path, "linearalgebra-b01").is_dir()
            with session_factory() as session:
                assert (
                    session.scalars(select(AfBook).where(AfBook.book_id == "mathanalysis-b05")).all()
                    == []
                )
                assert _parse_rows(session, book_id="mathanalysis-b05") == []
                assert len(_parse_rows(session)) == 2
                b05_regions = _intent_rows(session, OP_KIND_STRUCTURE, book_id="mathanalysis-b05")
                assert b05_regions == []
                # 他书人工框不随本清除消失：区域按 book_id 作用域，与解析版本无关
                assert len(_intent_rows(session, OP_KIND_STRUCTURE)) == 1

    def test_in_flight_in_scope_returns_409(self, tmp_path, monkeypatch):
        """S5：自己作用域内有 `queued`/`running` ⇒ 409 且零删除（不留半删状态）。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._add_job(session_factory, "mathanalysis-b05", "job-running-here", "running")
            before = self._counts(session_factory)

            response = client.post(
                PURGE_URL,
                json={
                    "scope": {"book_ids": ["mathanalysis-b05"]},
                    "dry_run": False,
                    "confirm": PURGE_CONFIRM,
                },
                headers=_admin_headers(),
            )

            assert response.status_code == 409
            assert self._counts(session_factory) == before
            assert self._book_root(tmp_path, "mathanalysis-b05").is_dir()

    def test_all_scope_purge_then_idempotent_rerun(self, tmp_path, monkeypatch):
        """全项目真删 + S6 幂等：第二次「没什么可删」是 200 全零，不是失败。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._seed(client, "linearalgebra-b01")
            _add_region(session_factory, "mathanalysis-b05", page_no=2)

            first = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )
            second = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )

            assert first.status_code == 200, first.text
            assert first.json()["deleted"]["af_books"] == 2
            # 5 = 2 个 parse 事件行 + 2 个 edit 意图行 + 1 个 structure 意图行（整族扫尾按表清，不分 op_kind）
            assert first.json()["deleted"]["af_operations"] == 5
            assert self._counts(session_factory) == {table.name: 0 for table in AF_TABLES}
            # 磁盘作用域 = 书目目录；空的 domain/course 父级不裁剪（operations.md 同一口径）
            assert not self._book_root(tmp_path, "mathanalysis-b05").exists()
            assert not self._book_root(tmp_path, "linearalgebra-b01").exists()
            assert self._book_root(tmp_path, "mathanalysis-b05").parent.is_dir()
            assert second.status_code == 200, second.text
            assert second.json()["targets"] == []
            assert second.json()["failures"] == []
            assert second.json()["deleted"] == {
                "af_books": 0,
                "af_operations": 0,
                "af_pages": 0,
                "af_kp": 0,
                "directories": 0,
            }

    def test_scope_shape_violations(self, tmp_path, monkeypatch):
        """作用域没有默认值：皆缺/冲突 400，非法路径段与未知字段 422，且一律零删除。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            before = self._counts(session_factory)
            cases = [
                ({"scope": {}}, 400),
                ({"scope": {"all": True, "book_ids": ["mathanalysis-b05"]}}, 400),
                ({"scope": {"book_ids": ["../escape"]}}, 422),
                ({"scope": {"all": True}, "unknown": 1}, 422),
            ]

            responses = [
                client.post(PURGE_URL, json=body, headers=_admin_headers()) for body, _ in cases
            ]

            for (_, expected), response in zip(cases, responses, strict=True):
                assert response.status_code == expected, response.text
            assert self._counts(session_factory) == before
            assert self._book_root(tmp_path, "mathanalysis-b05").is_dir()

    def test_unsafe_stored_segment_refused_before_any_deletion(self, tmp_path, monkeypatch):
        """S4：磁盘目标越界 ⇒ 整请求 400、一个都不删（不信任库里的历史脏值）。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._seed(client, "linearalgebra-b01")
            with session_factory() as session:
                book = session.get(AfBook, "mathanalysis-b05")
                assert book is not None
                book.domain_id = ".."  # 绕过 sync 校验的直写脏值（存量或人工修数）
                session.commit()
            before = self._counts(session_factory)

            response = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )

            assert response.status_code == 400
            assert self._counts(session_factory) == before
            assert self._book_root(tmp_path, "linearalgebra-b01").is_dir()

    def test_unsafe_segment_beats_in_flight_conflict(self, tmp_path, monkeypatch):
        """400 优先于 409：脏落点与在飞任务同时命中时先报作用域违规（真机演练 K6 判例）。

        两条守卫各自成立过，但真机上同时命中才暴露出先后次序——按本形态的 409 判定不得抢在
        落点守卫之前，否则把「配置已脏」误报成「解析在飞」，运维据此等任务跑完仍会撞同一堵墙。
        """
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._seed(client, "linearalgebra-b01")
            self._add_job(session_factory, "linearalgebra-b01", "job-queued-here", "queued")
            with session_factory() as session:
                book = session.get(AfBook, "mathanalysis-b05")
                assert book is not None
                book.domain_id = ".."
                session.commit()
            before = self._counts(session_factory)

            response = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )

            assert response.status_code == 400, response.text
            assert self._counts(session_factory) == before
            assert self._book_root(tmp_path, "linearalgebra-b01").is_dir()

    def test_disk_failure_is_visible_and_keeps_db_rows(self, tmp_path, monkeypatch):
        """S6：磁盘删除失败 ⇒ `failures` 明写、该书库行保留（可重跑），不假装成功。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        monkeypatch.setattr("axiom_flow.api.admin.rmtree", _fail_rmtree)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            before = self._counts(session_factory)

            response = client.post(
                PURGE_URL,
                json={
                    "scope": {"book_ids": ["mathanalysis-b05"]},
                    "dry_run": False,
                    "confirm": PURGE_CONFIRM,
                },
                headers=_admin_headers(),
            )

            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["deleted"]["directories"] == 0
            assert payload["deleted"]["af_books"] == 0
            assert len(payload["failures"]) == 1
            assert "mathanalysis-b05" in payload["failures"][0]
            assert self._counts(session_factory) == before

    def test_response_leaks_neither_paths_nor_token(self, tmp_path, monkeypatch):
        """S4 + 凭据纪律：响应只给相对路径与计数，绝不含绝对路径与 token。"""
        test_client, _ = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")

            response = client.post(PURGE_URL, json={"scope": {"all": True}}, headers=_admin_headers())

            assert response.status_code == 200, response.text
            assert ADMIN_TOKEN not in response.text
            assert str(tmp_path / "data") not in response.text

    def test_empty_domain_book_dir_is_purged_whole(self, tmp_path, monkeypatch):
        """S5 反向：`domain_id=''`（业务库 17 本的真实形态）时书目录只有**两层**。

        固定深度 glob 会把 `pages/`、`versions/` 当成书目录：清除后产物标记与空书目录残留，
        第二次清除的清单不再是空 ⇒ 「重跑幂等」在真实布局下不成立。
        """
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b09", domain_id="")
            book_dir = self._book_root(tmp_path, "mathanalysis-b09", domain_id="")
            assert book_dir.is_dir() and (book_dir / "pages").is_dir()

            dry = client.post(
                PURGE_URL, json={"scope": {"all": True}}, headers=_admin_headers()
            )
            assert dry.json()["targets"] == ["parsed/math_analysis/mathanalysis-b09"]

            response = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )

            assert response.status_code == 200, response.text
            assert response.json()["failures"] == []
            assert not book_dir.exists(), f"书目录残留：{[p.name for p in book_dir.iterdir()]}"
            assert self._counts(session_factory) == {table.name: 0 for table in AF_TABLES}

            again = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )
            assert again.json()["targets"] == []
            assert again.json()["deleted"].get("directories", 0) == 0


class TestDerivedPageRange:
    """④ 读面派生的正文区间两键（`KA-42`）：目录行到「下一条目录行之前」，点行到自己锚点的两端。

    需求方第九令要的是屏上那句「从 X 页到 Y 页」写得准，而算式的正本在点腿取材里——屏侧自推就会
    与模型实际读过的正文分叉。两键读时算、不落列（零 DDL），根侧 8900 字节转发零改码。
    """

    BOOK = "mathanalysis-b05"

    def _live(self, tmp_path, monkeypatch, *, page_count: int | None = 12):
        client, session_factory, _ = _kp_build(tmp_path, monkeypatch)
        _parsed_book(client)
        with session_factory() as session:
            book = BookRepository(session).get(self.BOOK)
            assert book is not None
            book.page_count = page_count
            session.commit()
        return client, session_factory

    def _seed(self, session_factory, *, node_id: str, name: str, **fields) -> None:
        with session_factory() as session:
            KpRepository(session).create(
                KpNodeRow(
                    node_id=node_id,
                    book_id=self.BOOK,
                    name=name,
                    norm_key=norm_key_for(name),
                    **fields,
                )
            )
            session.commit()

    def _ranges(self, client) -> dict[str, tuple[int | None, int | None]]:
        response = client.get(f"/api/v1/books/{self.BOOK}/kp")
        assert response.status_code == 200, response.text
        nodes = response.json()["nodes"]
        assert nodes, "工作区里没有行"
        return {node["node_id"]: (node["page_start"], node["page_end"]) for node in nodes}

    def test_outline_rows_run_to_the_row_before_the_next_one(self, tmp_path, monkeypatch):
        """末行到全书末页，其余各行到下一条目录行的前一页——不是它自己的锚点页。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        for index, page in enumerate((2, 5, 9), start=1):
            self._seed(
                session_factory,
                node_id=f"kp-n{index}",
                name=f"第{index}章",
                node_kind="outline",
                depth=1,
                order_hint=index,
                anchors=[{"page": page}],
            )

        assert self._ranges(client) == {
            "kp-n1": (2, 4),
            "kp-n2": (5, 8),
            "kp-n3": (9, 12),
        }

    def test_a_let_go_row_does_not_take_part_in_the_split(self, tmp_path, monkeypatch):
        """`rejected` 行不进序：它是被本批取代的旧读数，拿它切区间会把中间那一段凭空挖掉。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        for index, (page, status) in enumerate(((2, "candidate"), (5, "rejected"), (9, "candidate")), start=1):
            self._seed(
                session_factory,
                node_id=f"kp-n{index}",
                name=f"第{index}章",
                node_kind="outline",
                depth=1,
                order_hint=index,
                gen_status=status,
                anchors=[{"page": page}],
            )

        assert self._ranges(client) == {
            "kp-n1": (2, 8),
            "kp-n2": (None, None),
            "kp-n3": (9, 12),
        }

    def test_a_missing_page_count_falls_back_to_each_rows_own_start_page(self, tmp_path, monkeypatch):
        """`af_books.page_count` 缺 ⇒ 折回本行起点页，且一行都不丢：两键退化不是整面 500。"""
        client, session_factory = self._live(tmp_path, monkeypatch, page_count=None)
        for index, page in enumerate((2, 5), start=1):
            self._seed(
                session_factory,
                node_id=f"kp-n{index}",
                name=f"第{index}章",
                node_kind="outline",
                depth=1,
                order_hint=index,
                anchors=[{"page": page}],
            )

        assert self._ranges(client) == {"kp-n1": (2, 2), "kp-n2": (5, 5)}

    def test_a_row_without_anchors_has_no_range_to_report(self, tmp_path, monkeypatch):
        """没有锚点就没有起点页：两键为空是披露，机器不替它编一个区间（`KA-26` 同轴）。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        self._seed(
            session_factory,
            node_id="kp-n1",
            name="第1章",
            node_kind="outline",
            depth=1,
            order_hint=1,
            anchors=[],
        )

        assert self._ranges(client) == {"kp-n1": (None, None)}

    def test_point_rows_take_the_two_ends_of_their_own_anchors(self, tmp_path, monkeypatch):
        """点行两键＝锚点 min／max：屏侧只留一枚消费口，不给「目录行读派生键、点行自算」两套代码。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        self._seed(
            session_factory,
            node_id="kp-p1",
            name="ε-δ 语言",
            node_kind="point",
            anchors=[{"page": 4}, {"page": 3}],
        )

        assert self._ranges(client)["kp-p1"] == (3, 4)

    def test_a_single_row_echo_keeps_its_derived_range(self, tmp_path, monkeypatch):
        """背书腿的单行回显同样带两键：屏上已读到的区间不该被一次人工写入抹成 `null`。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        for index, page in enumerate((2, 5), start=1):
            self._seed(
                session_factory,
                node_id=f"kp-n{index}",
                name=f"第{index}章",
                node_kind="outline",
                depth=1,
                order_hint=index,
                anchors=[{"page": page}],
            )

        response = client.patch("/api/v1/kp/nodes/kp-n1", json={"summary": "人工写的章级总结"})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["summary"] == "人工写的章级总结"
        assert (body["page_start"], body["page_end"]) == (2, 4)

    def test_a_chapter_runs_to_the_next_chapter_not_the_next_row(self, tmp_path, monkeypatch):
        """章区间按树序算：首节与章共用一页时，章不塌成那一页（`DEFECT-028` 的读面一侧）。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        rows = (
            ("kp-c1", "第1章 实数", 1, 2, None),
            ("kp-s11", "1.1 数列极限", 2, 2, "kp-c1"),
            ("kp-s12", "1.2 函数极限", 2, 7, "kp-c1"),
            ("kp-c2", "第2章 一元微分", 1, 9, None),
        )
        for order, (node_id, name, depth, page, parent) in enumerate(rows, start=1):
            self._seed(
                session_factory,
                node_id=node_id,
                name=name,
                node_kind="outline",
                depth=depth,
                order_hint=order,
                parent_id=parent,
                anchors=[{"page": page}],
            )

        assert self._ranges(client) == {
            "kp-c1": (2, 8),
            "kp-s11": (2, 6),
            "kp-s12": (7, 8),
            "kp-c2": (9, 12),
        }

    def test_a_reference_row_spans_only_its_own_page(self, tmp_path, monkeypatch):
        """附件行只占自己那页：书目／索引之后没有内容可读，别把书尾算进它的区间。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        self._seed(
            session_factory,
            node_id="kp-sym",
            name="重要符号表",
            node_kind="outline",
            depth=1,
            order_hint=1,
            anchors=[{"page": 5}],
            meta={"toc_role": "reference", "toc_kind": "symbol_table"},
        )
        self._seed(
            session_factory,
            node_id="kp-c1",
            name="第1章 实数",
            node_kind="outline",
            depth=1,
            order_hint=2,
            anchors=[{"page": 9}],
        )

        assert self._ranges(client) == {"kp-sym": (5, 5), "kp-c1": (9, 12)}

    def test_the_read_face_reports_the_four_value_type(self, tmp_path, monkeypatch):
        """四值类型读时派生（`KA-44`）：章节／知识点／习题／其他，屏上不再按 `node_kind` 猜。"""
        client, session_factory = self._live(tmp_path, monkeypatch)
        rows = (
            ("kp-c1", "第1章 实数", "outline", 1, None, None),
            ("kp-s11", "1.1 数列极限", "outline", 2, "kp-c1", None),
            ("kp-ex", "习题 1.1", "outline", 1, None, {"toc_role": "reference", "toc_kind": "exercise"}),
            ("kp-bib", "参考书目", "outline", 1, None, {"toc_role": "reference", "toc_kind": "bibliography"}),
        )
        for order, (node_id, name, kind, depth, parent, meta) in enumerate(rows, start=1):
            self._seed(
                session_factory,
                node_id=node_id,
                name=name,
                node_kind=kind,
                depth=depth,
                order_hint=order,
                parent_id=parent,
                anchors=[{"page": order}],
                meta=meta,
            )

        response = client.get(f"/api/v1/books/{self.BOOK}/kp")

        assert response.status_code == 200, response.text
        types = {node["node_id"]: node["node_type"] for node in response.json()["nodes"]}
        assert types == {
            "kp-c1": "chapter",
            "kp-s11": "point",
            "kp-ex": "exercise",
            "kp-bib": "other",
        }
