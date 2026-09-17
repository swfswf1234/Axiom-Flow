"""8902 API v1 契约测试：端点结构与 schemas 一致、错误语义、af_* 持久化与分块。

隔离：SQLite 内存库 + 临时数据根 + 假引擎（不访问 MySQL/公网/模型服务）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from axiom_flow.api.main import create_app
from axiom_flow.config import load_settings
from axiom_flow.db.models import Base
from axiom_flow.engines.base import DocumentResult, PageResult


class FakeEngine:
    """假引擎：全本返回两页结构化块；单页返回一页文本块（不访问模型服务）。"""

    name = "mineru"

    def health(self) -> bool:
        return True

    def parse_document(self, pdf_bytes: bytes, filename: str) -> DocumentResult:
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
        return PageResult(
            page=page_no,
            blocks=[{"type": "text", "text": f"第 {page_no} 页重解析", "bbox": [72, 96, 540, 132]}],
            page_size=(595.0, 842.0),
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


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """隔离数据根 + SQLite 内存库 + 假引擎；数据根含一册源 PDF（raw/ 布局）。"""
    monkeypatch.chdir(tmp_path)
    data_root = tmp_path / "data"
    raw_dir = data_root / "raw" / "math-advanced" / "math_analysis"
    raw_dir.mkdir(parents=True)
    _make_pdf(raw_dir / "book.pdf", pages=2)
    settings = load_settings(data_dir=data_root)
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
        engine_factory=lambda: FakeEngine(),
        background_jobs=False,
    )
    with TestClient(app) as test_client:
        yield test_client


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

    def test_books_fallback_to_filesystem_when_db_empty(self, client, tmp_path):
        """空表回退文件系统产物（独立性降级）：不 sync 也能列出已 ingest 的书。"""
        _sync_book(client)
        _ingest(client)
        # 清空 DB 行，模拟 af_books 未建/空表
        assert client.delete("/api/v1/books/mathanalysis-b05").status_code == 200

        books = client.get("/api/v1/books").json()
        assert [book["book_id"] for book in books] == ["mathanalysis-b05"]
        assert books[0]["ingest_status"] == "ingested"

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
        assert book["parse_status"] == "completed"
        assert book["pages_done"] == 2

    def test_per_page_job_repares_single_page(self, client):
        _sync_book(client)
        _ingest(client)

        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05", "pages": [2]})
        assert created.status_code == 202
        assert created.json()["progress"]["parsed"] == 1

        page = client.get("/api/v1/books/mathanalysis-b05/pages/2").json()
        assert page["blocks"]["blocks"][0]["text"] == "第 2 页重解析"

    def test_job_missing_book_returns_404(self, client):
        assert client.post("/api/v1/parse-jobs", json={"book_id": "nope"}).status_code == 404

    def test_job_without_ingest_returns_404(self, client):
        _sync_book(client)
        assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 404

    def test_invalid_payload_returns_422(self, client):
        assert client.post("/api/v1/parse-jobs", json={}).status_code == 422

    def test_unknown_job_returns_404(self, client):
        assert client.get("/api/v1/parse-jobs/does-not-exist").status_code == 404


def test_manifest_json_is_readable_on_disk(client, tmp_path):
    """产物事实源：chunks.json 为合法 JSON 且含溯源字段。"""
    _sync_book(client)
    _ingest(client)
    assert client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).status_code == 202

    book_dir = tmp_path / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"
    payload = json.loads((book_dir / "chunks.json").read_text(encoding="utf-8"))
    assert payload["book_id"] == "mathanalysis-b05"
    assert payload["chunks"][0]["block_refs"]
