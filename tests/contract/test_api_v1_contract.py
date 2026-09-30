"""8902 API v1 契约测试：端点结构与 schemas 一致、错误语义、af_* 持久化与分块。

隔离：SQLite 内存库 + 临时数据根 + 假引擎（不访问 MySQL/公网/模型服务）。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from axiom_flow.api.main import create_app
from axiom_flow.config import AdminConfigError, load_settings
from axiom_flow.db.models import AfBlockEdit, AfBook, AfPage, AfParseJob, Base
from axiom_flow.db.repositories import (
    LEGACY_JOB_ID,
    BookRepository,
    BookSyncRow,
    EditRepository,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
)
from axiom_flow.engines.base import DocumentResult, EngineError, ModelLoad, PageResult


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
    admin_enabled: bool = False,
    admin_token: str | None = "",
):
    """隔离数据根 + SQLite 内存库 + 假引擎；返回 (client, session_factory)。

    管理面两参数**始终显式传入**（缺省 `admin_enabled=False`）：不传就会读到开发者 `.env` 里的
    `QED_ADMIN_*`，同一份测试在两台机器上语义不同。
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
        admin_enabled=admin_enabled,
        admin_token=admin_token,
    )
    db_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(db_engine)
    session_factory = sessionmaker(bind=db_engine, future=True)
    app = create_app(
        settings,
        session_factory=session_factory,
        engine_factory=engine_factory or (lambda name: FakeEngine()),
        background_jobs=False,
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

    def _parsed_book(self, client: TestClient) -> None:
        _sync_book(client)
        _ingest(client)
        job = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
        assert job.status_code == 202, job.text
        assert job.json()["status"] == "completed"

    def test_page_returns_blocks_markdown_and_image_url(self, client):
        self._parsed_book(client)

        response = client.get("/api/v1/books/mathanalysis-b05/pages/1")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"blocks", "markdown", "image_url", "edits"}
        assert payload["blocks"]["page"] == 1
        assert payload["blocks"]["source"] == "mineru"
        assert payload["blocks"]["image_size"][0] > 0
        assert payload["blocks"]["blocks"][0]["type"] == "heading"
        assert payload["blocks"]["blocks"][0]["index"] == 0
        assert "$$" in payload["markdown"]
        assert payload["image_url"] == "/api/v1/books/mathanalysis-b05/pages/1/image"
        assert payload["edits"] == []

    def test_page_image_serves_png(self, client):
        self._parsed_book(client)

        response = client.get("/api/v1/books/mathanalysis-b05/pages/1/image")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")

    def test_missing_page_returns_404(self, client):
        self._parsed_book(client)
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
        self._parsed_book(client)

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
        self._parsed_book(client)

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
        self._parsed_book(client)

        response = client.put(
            "/api/v1/books/mathanalysis-b05/pages/1/blocks/99/edit",
            json={"verdict": "ok"},
        )
        assert response.status_code == 400


class TestChunksEndpoint:
    """分块预览：落盘产物 + 参数重算。"""

    def _parsed_book(self, client: TestClient) -> None:
        _sync_book(client)
        _ingest(client)
        assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 202

    def test_chunks_from_artifact(self, client):
        self._parsed_book(client)

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
        self._parsed_book(client)

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
                assert session.scalars(select(AfParseJob)).all() == []

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
            jobs = ParseJobRepository(session)
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
            jobs = ParseJobRepository(session)
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
                ParseJobRepository(session).get(job_id).created_at = same
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
                EditRepository(session).upsert("mathanalysis-b05", "other-version", 2, 0, verdict="bad")
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
                job = ParseJobRepository(session).get(created.json()["id"])
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
                assert session.scalars(select(AfParseJob)).all() == []

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
                assert session.scalars(select(AfParseJob)).all() == []

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

            _ingest(client)  # 重复 ingest：只原子替换根视图

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
            assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 202
            assert client.delete("/api/v1/books/mathanalysis-b05?purge=true").status_code == 200
            with session_factory() as session:
                assert session.scalars(select(AfPage)).all() == []
                assert len(session.scalars(select(AfParseJob)).all()) == 1


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
        with session_factory() as session:
            return {
                table: int(session.scalar(select(func.count()).select_from(model)))
                for table, model in (
                    ("af_books", AfBook),
                    ("af_parse_jobs", AfParseJob),
                    ("af_pages", AfPage),
                    ("af_block_edits", AfBlockEdit),
                )
            }

    def _add_job(self, session_factory, book_id: str, job_id: str, status: str) -> None:
        with session_factory() as session:
            ParseJobRepository(session).create(
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

    def test_book_scope_purge_deletes_four_tables_and_dir(self, tmp_path, monkeypatch):
        """按书目清除：表族行含 `af_parse_jobs` 一起走（`PM-17`），他书完好。"""
        test_client, session_factory = _admin_build(tmp_path, monkeypatch)
        with test_client as client:
            self._seed(client, "mathanalysis-b05")
            self._seed(client, "linearalgebra-b01")
            # 他书有在飞任务：按本清除只守自己作用域，不被别书阻塞
            self._add_job(session_factory, "linearalgebra-b01", "job-other-queued", "queued")

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
                "af_parse_jobs": 1,
                "af_pages": 2,
                "af_block_edits": 1,
                "directories": 1,
            }
            assert not self._book_root(tmp_path, "mathanalysis-b05").exists()
            assert self._book_root(tmp_path, "linearalgebra-b01").is_dir()
            with session_factory() as session:
                assert (
                    session.scalars(select(AfBook).where(AfBook.book_id == "mathanalysis-b05")).all()
                    == []
                )
                assert (
                    session.scalars(
                        select(AfParseJob).where(AfParseJob.book_id == "mathanalysis-b05")
                    ).all()
                    == []
                )
                assert len(session.scalars(select(AfParseJob)).all()) == 2

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
            assert self._counts(session_factory) == {
                "af_books": 0,
                "af_parse_jobs": 0,
                "af_pages": 0,
                "af_block_edits": 0,
            }
            # 磁盘作用域 = 书目目录；空的 domain/course 父级不裁剪（operations.md 同一口径）
            assert not self._book_root(tmp_path, "mathanalysis-b05").exists()
            assert not self._book_root(tmp_path, "linearalgebra-b01").exists()
            assert self._book_root(tmp_path, "mathanalysis-b05").parent.is_dir()
            assert second.status_code == 200, second.text
            assert second.json()["targets"] == []
            assert second.json()["failures"] == []
            assert second.json()["deleted"] == {
                "af_books": 0,
                "af_parse_jobs": 0,
                "af_pages": 0,
                "af_block_edits": 0,
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
            assert self._counts(session_factory) == dict.fromkeys(
                ("af_books", "af_parse_jobs", "af_pages", "af_block_edits"), 0
            )

            again = client.post(
                PURGE_URL,
                json={"scope": {"all": True}, "dry_run": False, "confirm": PURGE_CONFIRM},
                headers=_admin_headers(),
            )
            assert again.json()["targets"] == []
            assert again.json()["deleted"].get("directories", 0) == 0
