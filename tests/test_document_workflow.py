"""
模块职责：验证 v0.2 从 PDF 到页面审阅、知识、工作簿和发布图谱的基本闭环。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
被测代码：src/axiom_flow/infrastructure/pdf_pipeline.py、src/axiom_flow/infrastructure/mysql.py、src/axiom_flow/application/workbooks.py、src/axiom_flow/api/main.py
"""

import asyncio
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from axiom_flow.api.main import create_app
from axiom_flow.application.workbooks import WorkbookService
from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.files import LocalFileLocator
from axiom_flow.infrastructure.mysql import MySQLRepository
from axiom_flow.infrastructure.pdf_pipeline import PDFPipeline
from axiom_flow.infrastructure.workbooks import OpenPyxlWorkbookGateway
from axiom_flow.worker.runner import Worker


class FakeProvider:
    """确定性模型替身，覆盖领域转换而不触发外部模型调用。"""

    def __init__(self) -> None:
        self.calls = 0

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        self.calls += 1
        return {
            "markdown": raw_text,
            "blocks": [{"kind": "paragraph", "content": raw_text, "quote": "Axiom", "confidence": 0.9}],
        }

    async def extract_knowledge(self, markdown: str) -> dict:
        self.calls += 1
        return {
            "nodes": [
                {"kind": "concept", "title": "Axiom", "content": "可追溯的知识单元", "evidence_quote": "Axiom"},
                {"kind": "result", "title": "Flow", "content": "由页面事实生成", "evidence_quote": "Flow"},
            ],
            "edges": [{"source_title": "Axiom", "target_title": "Flow", "relation": "DEFINES", "evidence_quote": "Axiom"}],
        }


def _pdf(path: Path) -> None:
    document = fitz.open()
    for text in ("Axiom is a source-grounded unit.", "Flow keeps evidence attached."):
        page = document.new_page()
        page.insert_text((72, 72), text)
    document.save(path)
    document.close()


def _settings(tmp_path: Path, mysql_settings: Settings) -> Settings:
    return Settings(data_dir=tmp_path / "data", model_call_budget=36, mysql_database=mysql_settings.mysql_database)


def test_pipeline_workbook_publish_and_supersede(
    tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLRepository,
):
    source = tmp_path / "sample.pdf"
    _pdf(source)
    settings = _settings(tmp_path, mysql_settings)
    store = mysql_store
    provider = FakeProvider()
    pipeline = PDFPipeline(store, settings, provider, provider)
    document = pipeline.import_pdf(source)

    result = asyncio.run(pipeline.parse_document(document["id"]))

    assert result["status"] == "parsed"
    assert store.list_pages(document["id"]) == []
    store.select_current_parse_run(
        document["id"], result["run_id"], "测试选择工作流基线", settings.data_dir,
    )
    pages = store.list_pages(document["id"])
    assert len(pages) == 2
    assert pages[0]["blocks"][0]["bbox"] is not None
    for page in pages:
        store.review_page(page["id"], "accepted", "文字层与页图一致")

    candidates = asyncio.run(pipeline.generate_candidates(document["id"]))
    assert {candidate["title"] for candidate in candidates} == {"Axiom", "Flow"}
    for candidate in candidates:
        store.review_candidate(candidate["id"], "accepted", "证据可定位")
    edges = store.list_edges(document["id"])
    assert len(edges) == 1
    store.review_edge(edges[0]["id"], "accepted")

    workbook = WorkbookService(
        store, OpenPyxlWorkbookGateway(settings.data_dir), LocalFileLocator(settings.data_dir),
    )
    revision = workbook.export_draft(document["id"])
    assert Path(revision["path"]).is_file()
    workbook.import_draft(document["id"], Path(revision["path"]))
    assert len(store.latest_workbook_revision(document["id"])["snapshot"]["nodes"]) == 2
    first_release = workbook.publish_latest(document["id"])
    workbook.export_draft(document["id"])
    second_release = workbook.publish_latest(document["id"])

    assert first_release["status"] == "published"
    assert store.latest_release(document["id"])["id"] == second_release["id"]
    assert store.latest_release(document["id"])["snapshot"]["edges"][0]["relation"] == "DEFINES"


def test_http_api_accepts_upload_and_serves_static_workbench(
    tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLRepository,
):
    source = tmp_path / "sample.pdf"
    _pdf(source)
    provider = FakeProvider()
    application = create_app(_settings(tmp_path, mysql_settings), lambda: (provider, provider))

    with TestClient(application) as client:
        assert client.get("/api/v1/health").json()["version"] == "0.3.0"
        home = client.get("/")
        assert home.status_code == 200
        assert "Axiom-Flow" in home.text
        with source.open("rb") as input_file:
            response = client.post("/api/v1/documents", files={"file": ("sample.pdf", input_file, "application/pdf")})
        assert response.status_code == 201
        document_id = response.json()["id"]
        command = client.post(f"/api/v1/documents/{document_id}/parse-jobs")
        assert command.status_code == 202
        assert command.json()["job"]["status"] == "queued"


def test_http_worker_flow_reaches_a_traceable_release(
    tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLRepository,
):
    """主链验收只能经过公开 HTTP 命令和 Worker，不直接调用仓储或 pipeline。"""
    source = tmp_path / "release.pdf"
    _pdf(source)
    settings = _settings(tmp_path, mysql_settings)
    provider = FakeProvider()
    application = create_app(settings, lambda: (provider, provider))

    with TestClient(application) as client:
        with source.open("rb") as stream:
            document = client.post(
                "/api/v1/documents",
                files={"file": ("release.pdf", stream, "application/pdf")},
            ).json()
        parse_job = client.post(f"/api/v1/documents/{document['id']}/parse-jobs").json()["job"]
        parsed = Worker(application.state.jobs, "release-worker").run_once()
        assert parsed["id"] == parse_job["id"] and parsed["status"] == "succeeded"

        run_id = parsed["result"]["run_id"]
        selected = client.post(
            f"/api/v1/documents/{document['id']}/current-parse-run",
            json={"run_id": run_id, "reason": "完整主链验收"},
        )
        selected.raise_for_status()
        pages = client.get(f"/api/v1/parse-runs/{run_id}/pages").json()
        for page in pages:
            accepted = client.post(
                f"/api/v1/pages/{page['id']}/reviews",
                json={"status": "accepted", "reason": "页图与正文一致"},
            )
            accepted.raise_for_status()

        extraction = client.post(f"/api/v1/documents/{document['id']}/extraction-jobs")
        extraction.raise_for_status()
        assert Worker(application.state.jobs, "release-worker").run_once()["status"] == "succeeded"

        nodes = client.get(f"/api/v1/documents/{document['id']}/knowledge-nodes").json()
        edges = client.get(f"/api/v1/documents/{document['id']}/knowledge-edges").json()
        for node in nodes:
            client.post(
                f"/api/v1/knowledge-nodes/{node['id']}/reviews",
                json={"status": "accepted", "reason": "证据可定位"},
            ).raise_for_status()
        for edge in edges:
            client.post(
                f"/api/v1/knowledge-edges/{edge['id']}/reviews",
                json={"status": "accepted", "reason": "关系有原文证据"},
            ).raise_for_status()

        client.post(f"/api/v1/documents/{document['id']}/workbook-exports").raise_for_status()
        workbook = client.get(f"/api/v1/documents/{document['id']}/workbook")
        workbook.raise_for_status()
        imported = client.post(
            f"/api/v1/documents/{document['id']}/workbook-imports",
            files={"file": ("reviewed.xlsx", workbook.content, workbook.headers["content-type"])},
        )
        imported.raise_for_status()
        release = client.post(f"/api/v1/documents/{document['id']}/releases")
        release.raise_for_status()
        graph = client.get(f"/api/v1/documents/{document['id']}/graph").json()

        assert graph["release_id"] == release.json()["id"]
        assert {node["title"] for node in graph["nodes"]} == {"Axiom", "Flow"}
        assert graph["edges"][0]["relation"] == "DEFINES"
        assert graph["nodes"][0]["evidence"][0]["page_no"] in {1, 2}


def test_store_recovers_an_interrupted_parse_run(
    tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLRepository,
):
    settings = _settings(tmp_path, mysql_settings)
    store = mysql_store
    document = store.create_document("unfinished.pdf", "hash-for-recovery", tmp_path / "unfinished.pdf", 1)
    store.create_parse_run(document["id"], {"vision_model": "fake"})
    store.update_document_status(document["id"], "parsing")

    recovered = MySQLRepository(settings.mysql_url)
    recovered.recover_interrupted_runs()

    assert recovered.get_document(document["id"])["status"] == "failed"
    recovered.dispose()
