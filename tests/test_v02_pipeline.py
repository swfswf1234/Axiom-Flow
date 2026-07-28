"""
模块职责：验证 v0.2 从 PDF 到页面审阅、知识、工作簿和发布图谱的基本闭环。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
被测代码：backend/app/pipeline.py、backend/app/store.py、backend/app/workbook.py、backend/app/main.py
"""

import asyncio
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.pipeline import PipelineService
from backend.app.store import MySQLStore
from backend.app.workbook import WorkbookService


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


def test_pipeline_workbook_publish_and_supersede(tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLStore):
    source = tmp_path / "sample.pdf"
    _pdf(source)
    settings = _settings(tmp_path, mysql_settings)
    store = mysql_store
    provider = FakeProvider()
    pipeline = PipelineService(store, settings, provider, provider)
    document = pipeline.import_pdf(source)

    result = asyncio.run(pipeline.parse_document(document["id"]))

    assert result["status"] == "parsed"
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

    workbook = WorkbookService(store, settings.data_dir)
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


def test_http_api_accepts_upload_and_serves_static_workbench(tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLStore):
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


def test_store_recovers_an_interrupted_parse_run(tmp_path: Path, mysql_settings: Settings, mysql_store: MySQLStore):
    settings = _settings(tmp_path, mysql_settings)
    store = mysql_store
    document = store.create_document("unfinished.pdf", "hash-for-recovery", tmp_path / "unfinished.pdf", 1)
    store.create_parse_run(document["id"], {"vision_model": "fake"})
    store.update_document_status(document["id"], "parsing")

    recovered = MySQLStore(settings.mysql_url)
    recovered.recover_interrupted_runs()

    assert recovered.get_document(document["id"])["status"] == "failed"
    recovered.dispose()
