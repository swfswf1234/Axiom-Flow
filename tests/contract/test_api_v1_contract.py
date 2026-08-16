"""
模块职责：守护 8902 API v1 端点返回结构与 schemas 的一致性（对外契约单一事实源）。
设计关联（DesignRef）：docs/design/8902-integration-contract.md
实现状态：Current
被测代码：src/axiom_flow/api/
守护面：API 契约
失效后果：8902 返回结构与 schemas 漂移，QED-Engine 8900 适配（REQ-034）失效
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from axiom_flow.ingest import default_data_dir

pytestmark = pytest.mark.contract


def _make_book(data_dir: Path, book_id: str = "01-test-book") -> None:
    """构造最小合法产物目录（book.json + 页三件套），模拟真实解析产物。"""
    book_dir = data_dir / "books" / book_id
    pages_dir = book_dir / "pages"
    pages_dir.mkdir(parents=True)
    (book_dir / "book.json").write_text(
        json.dumps(
            {
                "book_id": book_id,
                "title": "Test Book",
                "author": "T. Author",
                "page_count": 2,
                "sha256": "a" * 64,
                "strategy": "local",
            }
        ),
        encoding="utf-8",
    )
    for no in (1, 2):
        (pages_dir / f"p{no:04d}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake")
        (pages_dir / f"p{no:04d}.md").write_text(f"# Page {no}\n\nSome text\n", encoding="utf-8")
        (pages_dir / f"p{no:04d}.blocks.json").write_text(
            json.dumps(
                {
                    "page": no,
                    "source": "qwen-vl-plus",
                    "blocks": [
                        {"type": "heading", "level": 1, "text": f"Page {no}", "bbox": [0, 0, 100, 50]},
                        {"type": "paragraph", "text": "Some text", "bbox": [0, 50, 100, 100]},
                    ],
                }
            ),
            encoding="utf-8",
        )


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """隔离数据目录 + TestClient。"""
    monkeypatch.setenv("AXIOM_FLOW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("AXIOM_API_KEY", "test-key")
    _make_book(default_data_dir())
    from axiom_flow.api.main import app

    with TestClient(app) as test_client:
        yield test_client


class TestBooksEndpoints:
    """书目与页数据端点：结构符合 schemas，错误语义正确。"""

    def test_list_books_returns_book_meta_list(self, client):
        response = client.get("/api/v1/books")

        assert response.status_code == 200
        books = response.json()
        assert len(books) == 1
        assert set(books[0]) == {"book_id", "title", "author", "page_count", "sha256", "strategy"}
        assert books[0]["book_id"] == "01-test-book"
        assert books[0]["page_count"] == 2

    def test_get_book_meta(self, client):
        response = client.get("/api/v1/books/01-test-book")

        assert response.status_code == 200
        assert response.json()["title"] == "Test Book"

    def test_get_missing_book_returns_404(self, client):
        assert client.get("/api/v1/books/nope").status_code == 404

    def test_get_page_returns_blocks_markdown_and_image_url(self, client):
        response = client.get("/api/v1/books/01-test-book/pages/1")
        if response.status_code != 200:
            pytest.fail(f"状态 {response.status_code}：{response.text}")

        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {"blocks", "markdown", "image_url"}
        assert payload["blocks"]["page"] == 1
        assert payload["blocks"]["source"] == "qwen-vl-plus"
        assert payload["blocks"]["blocks"][0]["type"] == "heading"
        assert payload["markdown"] == "# Page 1\n\nSome text\n"
        assert payload["image_url"] == "/api/v1/books/01-test-book/pages/1/image"

    def test_get_page_image_serves_png(self, client):
        response = client.get("/api/v1/books/01-test-book/pages/1/image")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG")

    def test_get_missing_page_returns_404(self, client):
        assert client.get("/api/v1/books/01-test-book/pages/99").status_code == 404
        assert client.get("/api/v1/books/01-test-book/pages/99/image").status_code == 404

    def test_manifest_lists_artifacts_with_hashes(self, client):
        response = client.get("/api/v1/books/01-test-book/manifest")

        assert response.status_code == 200
        entries = response.json()
        assert len(entries) >= 5
        for entry in entries:
            assert set(entry) == {"path", "size", "sha256"}
            assert len(entry["sha256"]) == 64


class TestParseJobsEndpoints:
    """解析任务：提交/查询，返回 ParseJob 结构（识别调用被 mock）。"""

    def test_create_and_query_job(self, client, monkeypatch):
        from axiom_flow.fallback import QwenVLPlusClient

        def fake_recognize(self, image, page_no=1):
            from axiom_flow.fallback.client import VisionResult

            return VisionResult(f"## Recognized {page_no}\n\n$$x^{page_no}$$", "qwen-vl-markdown-v1", page_no)

        monkeypatch.setattr(QwenVLPlusClient, "recognize_page", fake_recognize)

        created = client.post(
            "/api/v1/parse-jobs",
            json={"book_id": "01-test-book", "pages": [1], "strategy": "local"},
        )
        assert created.status_code == 200
        job = created.json()
        assert set(job) == {"id", "book_id", "pages", "strategy", "status", "progress"}
        assert job["book_id"] == "01-test-book"
        assert job["status"] == "completed"

        fetched = client.get(f"/api/v1/parse-jobs/{job['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["id"] == job["id"]

    def test_parse_job_rejects_missing_book(self, client):
        response = client.post(
            "/api/v1/parse-jobs",
            json={"book_id": "nope", "pages": [1], "strategy": "local"},
        )
        assert response.status_code == 404

    def test_parse_job_invalid_payload(self, client):
        response = client.post("/api/v1/parse-jobs", json={})
        assert response.status_code == 422

    def test_get_unknown_job_returns_404(self, client):
        assert client.get("/api/v1/parse-jobs/does-not-exist").status_code == 404
