"""
模块职责：验证 v0.3 API v1 的上传、任务协议、统一错误与静态工作台。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
被测代码：src/axiom_flow/api/main.py、src/axiom_flow/api/schemas.py、src/axiom_flow/main.py
"""

from pathlib import Path

from fastapi.testclient import TestClient

from axiom_flow.api.main import create_app
from axiom_flow.infrastructure.config import Settings
from axiom_flow.worker.runner import Worker
from tests.support.pdf import write_text_pdf
from tests.support.providers import ParsingProvider, UnusedProvider


def test_api_v1_uploads_idempotently_and_enqueues(tmp_path: Path, mysql_settings: Settings, mysql_store):
    source = tmp_path / "api.pdf"
    write_text_pdf(source, ["API v1"])
    settings = Settings(data_dir=tmp_path / "data", mysql_database=mysql_settings.mysql_database)
    application = create_app(settings, lambda: (UnusedProvider(), UnusedProvider()))

    with TestClient(application) as client:
        assert client.get("/api/v1/health").json() == {"status": "ok", "version": "0.3.0"}
        workbench = client.get("/").text
        assert "v0.3" in workbench
        assert 'id="review-workspace"' in workbench
        assert 'id="parse-dialog"' in workbench
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


def test_api_lists_and_downloads_parse_artifacts(tmp_path: Path, mysql_settings: Settings, mysql_store):
    source = tmp_path / "artifacts.pdf"
    write_text_pdf(source, ["API v1"])
    settings = Settings(data_dir=tmp_path / "data", mysql_database=mysql_settings.mysql_database)
    provider = ParsingProvider()
    application = create_app(settings, lambda: (provider, provider))

    with TestClient(application) as client:
        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents", files={"file": ("artifacts.pdf", stream, "application/pdf")},
            ).json()
        client.post(f"/api/v1/documents/{document['id']}/parse-jobs").raise_for_status()
        assert provider.calls == 0
        completed = Worker(application.state.jobs, "api-artifact-worker").run_once()
        run_id = completed["result"]["run_id"]

        assert client.get(f"/api/v1/documents/{document['id']}/current-parse-run").status_code == 404
        selected = client.post(
            f"/api/v1/documents/{document['id']}/current-parse-run",
            json={"run_id": run_id, "reason": "API 集成测试"},
        )
        assert selected.status_code == 200
        assert selected.json()["is_current"] is True
        assert client.get(f"/api/v1/documents/{document['id']}/current-parse-run").json()["id"] == run_id
        assert client.get(f"/api/v1/parse-runs/{run_id}/pages/1").json()["page_no"] == 1
        assert client.get(f"/api/v1/parse-runs/{run_id}/artifact-summary").json()["run"]["page_count"] == 1
        assert client.get(f"/api/v1/parse-runs/{run_id}/page-index").json()[0]["page_no"] == 1

        response = client.get(f"/api/v1/parse-runs/{run_id}/artifacts")
        assert response.status_code == 200
        artifacts = response.json()
        assert all("path" not in artifact for artifact in artifacts)
        manifest = next(artifact for artifact in artifacts if artifact["kind"] == "parse_manifest")
        downloaded = client.get(manifest["download_url"])
        assert downloaded.status_code == 200
        assert downloaded.json()["page_count"] == 1


def test_api_submits_and_executes_an_inclusive_page_range(
    tmp_path: Path, mysql_settings: Settings, mysql_store,
):
    source = tmp_path / "range.pdf"
    write_text_pdf(source, [f"Range page {index + 1}" for index in range(3)])
    settings = Settings(data_dir=tmp_path / "data", mysql_database=mysql_settings.mysql_database)
    provider = ParsingProvider()
    application = create_app(settings, lambda: (provider, provider))

    with TestClient(application) as client:
        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents", files={"file": ("range.pdf", stream, "application/pdf")},
            ).json()
        command = client.post(
            f"/api/v1/documents/{document['id']}/parse-jobs",
            json={"page_start": 2, "page_end": 3},
        )
        assert command.status_code == 202
        assert command.json()["job"]["payload"]["page_count"] == 2
        completed = Worker(application.state.jobs, "api-range-worker").run_once()
        pages = client.get(f"/api/v1/parse-runs/{completed['result']['run_id']}/pages").json()
        assert [page["page_no"] for page in pages] == [2, 3]
