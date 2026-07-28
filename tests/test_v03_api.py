"""
模块职责：验证 v0.3 API v1 的上传、任务协议、统一错误与静态工作台。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
被测代码：backend/api/main.py、backend/api/schemas.py、backend/app/main.py
"""

from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.app.config import Settings


class UnusedProvider:
    """API 进程只入队，测试供应商不应收到调用。"""

    calls = 0

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        raise AssertionError("API 进程不得执行解析")

    async def extract_knowledge(self, markdown: str) -> dict:
        raise AssertionError("API 进程不得执行抽取")


def _pdf(path: Path) -> None:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "API v1")
    pdf.save(path)
    pdf.close()


def test_api_v1_uploads_idempotently_and_enqueues(tmp_path: Path, mysql_settings: Settings, mysql_store):
    source = tmp_path / "api.pdf"
    _pdf(source)
    settings = Settings(data_dir=tmp_path / "data", mysql_database=mysql_settings.mysql_database)
    application = create_app(settings, lambda: (UnusedProvider(), UnusedProvider()))

    with TestClient(application) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok", "version": "0.3.0"}
        assert "v0.3" in client.get("/").text
        with source.open("rb") as stream:
            first = client.post("/api/v1/documents", files={"file": ("api.pdf", stream, "application/pdf")})
        with source.open("rb") as stream:
            duplicate = client.post("/api/v1/documents", files={"file": ("again.pdf", stream, "application/pdf")})
        assert first.status_code == 201
        assert duplicate.json()["id"] == first.json()["id"]

        document_id = first.json()["id"]
        command = client.post(f"/api/v1/documents/{document_id}/parse-jobs")
        repeated = client.post(f"/api/v1/documents/{document_id}/parse-jobs")
        assert command.status_code == 202
        assert command.json()["created"] is True
        assert repeated.json()["created"] is False
        assert client.get(f"/api/v1/jobs/{command.json()['job']['id']}").json()["status"] == "queued"

        missing = client.get("/api/v1/jobs/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


def test_api_rejects_oversized_upload(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = Settings(data_dir=tmp_path / "data", mysql_database=mysql_settings.mysql_database, max_upload_bytes=4)
    application = create_app(settings)
    with TestClient(application) as client:
        response = client.post("/api/v1/documents", files={"file": ("large.pdf", b"too-large", "application/pdf")})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
