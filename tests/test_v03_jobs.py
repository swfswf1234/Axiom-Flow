"""
模块职责：验证 v0.3 任务幂等、租约、版本历史、任务级预算与审阅事件。
设计关联（DesignRef）：docs/design/background-jobs.md
实现状态：Current
被测代码：backend/application/jobs.py、backend/infrastructure/mysql.py、backend/worker/runner.py
"""

from pathlib import Path

import fitz
from sqlalchemy import text

from backend.app.config import Settings
from backend.app.pipeline import PipelineService
from backend.application.jobs import JobApplicationService
from backend.infrastructure.mysql import V03Store
from backend.worker.runner import Worker


class FakeProvider:
    """每个任务新建的确定性供应商，用于验证调用量不会跨任务累计。"""

    def __init__(self) -> None:
        self.calls = 0

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        self.calls += 1
        return {"markdown": raw_text, "blocks": [
            {"kind": "paragraph", "content": raw_text, "quote": raw_text[:20], "confidence": 0.9}
        ]}

    async def extract_knowledge(self, markdown: str) -> dict:
        self.calls += 1
        return {"nodes": [
            {"kind": "concept", "title": "Axiom", "content": "可追溯单元", "evidence_quote": "Axiom"},
        ], "edges": []}


def _settings(tmp_path: Path, mysql_settings: Settings) -> Settings:
    return Settings(data_dir=tmp_path / "data", mysql_database=mysql_settings.mysql_database)


def _document(store: V03Store, settings: Settings, tmp_path: Path) -> dict:
    source = tmp_path / "v03.pdf"
    pdf = fitz.open()
    for page_text in ("Axiom page one", "Axiom page two"):
        page = pdf.new_page()
        page.insert_text((72, 72), page_text)
    pdf.save(source)
    pdf.close()
    provider = FakeProvider()
    return PipelineService(store, settings, provider, provider).import_pdf(source)


def _service(store: V03Store, settings: Settings) -> JobApplicationService:
    return JobApplicationService(store, settings, lambda: (FakeProvider(), FakeProvider()))


def test_jobs_are_idempotent_leased_and_versioned(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    store = V03Store(settings.mysql_url)
    document = _document(store, settings, tmp_path)
    service = _service(store, settings)

    first, created = service.submit_parse(document["id"])
    duplicate, duplicate_created = service.submit_parse(document["id"])
    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == first["id"]

    worker = Worker(service, "worker-a")
    completed = worker.run_once()
    assert completed["status"] == "succeeded"
    assert completed["result"]["model_calls"] == 2
    pages = store.list_pages(document["id"])
    assert len(pages) == 2
    for page in pages:
        store.review_page(page["id"], "accepted", "证据一致")
        assert len(store.list_review_events("page", page["id"])) == 1

    extraction, _ = service.submit_extraction(document["id"])
    extracted = worker.run_once()
    assert extracted["id"] == extraction["id"]
    assert extracted["status"] == "succeeded"
    assert len(store.list_candidates(document["id"])) == 1

    repeated_extraction, repeated_created = service.submit_extraction(document["id"])
    assert repeated_created is True
    assert worker.run_once()["id"] == repeated_extraction["id"]
    with store.engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM af_extraction_runs")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM af_candidates")).scalar_one() == 2

    second, second_created = service.submit_parse(document["id"])
    assert second_created is True
    worker.run_once()
    runs = store.list_parse_runs(document["id"])
    assert len(runs) == 2
    assert {run["model_calls"] for run in runs} == {2}
    store.dispose()


def test_two_workers_cannot_claim_the_same_job(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    first_store = V03Store(settings.mysql_url)
    second_store = V03Store(settings.mysql_url)
    document = _document(first_store, settings, tmp_path)
    job, _ = _service(first_store, settings).submit_parse(document["id"])

    claimed = first_store.claim_next_job("worker-a", 120)
    competing = second_store.claim_next_job("worker-b", 120)

    assert claimed["id"] == job["id"]
    assert competing is None
    with first_store.engine.begin() as connection:
        connection.execute(text("UPDATE af_jobs SET lease_expires_at='2000-01-01 00:00:00' WHERE id=:id"), {"id": job["id"]})
    reclaimed = second_store.claim_next_job("worker-b", 120)
    assert reclaimed["id"] == job["id"]
    assert reclaimed["attempt"] == 2
    second_store.fail_job(job["id"], "worker-b", {"code": "test", "message": "停止"}, False)
    first_store.dispose()
    second_store.dispose()


def test_queued_job_can_be_cancelled(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    store = V03Store(settings.mysql_url)
    document = _document(store, settings, tmp_path)
    job, _ = _service(store, settings).submit_parse(document["id"])

    cancelled = store.request_job_cancel(job["id"])

    assert cancelled["status"] == "cancelled"
    assert store.claim_next_job("worker-a", 120) is None
    store.dispose()
