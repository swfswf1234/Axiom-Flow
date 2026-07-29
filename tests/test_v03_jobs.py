"""
模块职责：验证 v0.3 任务幂等、租约、版本历史、任务级预算与审阅事件。
设计关联（DesignRef）：docs/design/background-jobs.md
实现状态：Current
被测代码：src/axiom_flow/application/jobs.py、src/axiom_flow/infrastructure/mysql.py、src/axiom_flow/worker/runner.py
"""

from pathlib import Path

import fitz
import pytest
from sqlalchemy import text

from axiom_flow.application.jobs import JobApplicationService, JobPolicy
from axiom_flow.domain.models import RetryableJobError
from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.mysql import MySQLRepository
from axiom_flow.infrastructure.pdf_pipeline import PDFPipeline
from axiom_flow.worker.runner import Worker


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


def _document(store: MySQLRepository, settings: Settings, tmp_path: Path) -> dict:
    source = tmp_path / "v03.pdf"
    pdf = fitz.open()
    for page_text in ("Axiom page one", "Axiom page two"):
        page = pdf.new_page()
        page.insert_text((72, 72), page_text)
    pdf.save(source)
    pdf.close()
    provider = FakeProvider()
    return PDFPipeline(store, settings, provider, provider).import_pdf(source)


def _policy(settings: Settings) -> JobPolicy:
    return JobPolicy(
        vision_model=settings.vision_model,
        vision_contract_version=settings.vision_contract_version,
        vision_max_tokens=settings.vision_max_tokens,
        vision_page_attempts=settings.vision_page_attempts,
        model_call_budget=settings.model_call_budget,
        knowledge_model=settings.knowledge_model,
        worker_lease_seconds=settings.worker_lease_seconds,
    )


def _service(store: MySQLRepository, settings: Settings) -> JobApplicationService:
    def factory(bound_budget: int | None):
        execution = settings.model_copy(update={"model_call_budget": bound_budget})
        return PDFPipeline(store, execution, FakeProvider(), FakeProvider())

    return JobApplicationService(store, _policy(settings), factory)


def test_jobs_are_idempotent_leased_and_versioned(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    store = MySQLRepository(settings.mysql_url)
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
    assert store.list_pages(document["id"]) == []
    store.select_current_parse_run(
        document["id"], completed["result"]["run_id"], "测试选择当前运行", settings.data_dir,
    )
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
    first_store = MySQLRepository(settings.mysql_url)
    second_store = MySQLRepository(settings.mysql_url)
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
    store = MySQLRepository(settings.mysql_url)
    document = _document(store, settings, tmp_path)
    job, _ = _service(store, settings).submit_parse(document["id"])

    cancelled = store.request_job_cancel(job["id"])

    assert cancelled["status"] == "cancelled"
    assert store.claim_next_job("worker-a", 120) is None
    store.dispose()


def test_parse_job_binds_an_inclusive_page_range(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    store = MySQLRepository(settings.mysql_url)
    document = _document(store, settings, tmp_path)
    service = _service(store, settings)

    job, _ = service.submit_parse(document["id"], 2, 2)
    completed = Worker(service, "range-worker").run_once()

    assert job["payload"] == {
        "model_call_budget": 3, "page_count": 1, "page_start": 2, "page_end": 2,
    }
    assert completed["status"] == "succeeded"
    assert completed["progress_current"] == 1
    assert completed["progress_total"] == 1
    run = store.list_parse_runs(document["id"])[0]
    assert [page["page_no"] for page in store.list_pages_for_run(run["id"])] == [2]
    assert run["provider_summary"]["page_range"] == {"start": 2, "end": 2, "inclusive": True}
    store.dispose()


def test_parse_job_rejects_a_range_outside_the_document(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    store = MySQLRepository(settings.mysql_url)
    document = _document(store, settings, tmp_path)

    with pytest.raises(ValueError, match="解析页范围"):
        _service(store, settings).submit_parse(document["id"], 2, 3)

    store.dispose()


def test_scanned_parse_retry_reuses_page_checkpoint(tmp_path: Path, mysql_settings: Settings, mysql_store):
    settings = _settings(tmp_path, mysql_settings)
    store = MySQLRepository(settings.mysql_url)
    source = tmp_path / "scanned.pdf"
    pdf = fitz.open()
    for _ in range(2):
        page = pdf.new_page()
        page.draw_rect(fitz.Rect(72, 72, 300, 300), color=(0, 0, 0), fill=(0, 0, 0))
    pdf.save(source)
    pdf.close()
    bootstrap = FakeProvider()
    document = PDFPipeline(store, settings, bootstrap, bootstrap).import_pdf(source)
    parsed_page_numbers = []
    instances = 0

    class InterruptingProvider(FakeProvider):
        def __init__(self, should_fail: bool) -> None:
            super().__init__()
            self.should_fail = should_fail

        async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
            self.calls += 1
            parsed_page_numbers.append(page_no)
            if self.should_fail and page_no == 2:
                    raise RetryableJobError("temporary outage")
            return {
                "markdown": f"扫描页 {page_no}", "page_kind": "content",
                "blocks": [{
                    "kind": "paragraph", "content": f"扫描页 {page_no}",
                    "bbox_1000": [100, 100, 900, 900], "confidence": 0.9,
                }],
            }

    def factory(bound_budget: int | None):
        nonlocal instances
        instances += 1
        provider = InterruptingProvider(should_fail=instances == 1)
        execution = settings.model_copy(update={"model_call_budget": bound_budget})
        return PDFPipeline(store, execution, provider, provider)

    service = JobApplicationService(store, _policy(settings), factory)
    service.submit_parse(document["id"])
    worker = Worker(service, "resume-worker")

    first = worker.run_once()
    assert first["status"] == "queued"
    run = store.list_parse_runs(document["id"])[0]
    assert [page["page_no"] for page in store.list_pages_for_run(run["id"])] == [1]

    second = worker.run_once()
    assert second["status"] == "succeeded"
    assert parsed_page_numbers == [1, 2, 2]
    assert second["result"]["model_calls"] == 3
    assert store.list_pages(document["id"]) == []
    store.select_current_parse_run(
        document["id"], second["result"]["run_id"], "测试选择恢复运行", settings.data_dir,
    )
    assert len(store.list_pages(document["id"])) == 2
    assert {item["kind"] for item in store.list_artifacts_for_run(run["id"])} >= {
        "page_markdown", "page_json", "parse_manifest",
    }
    assert len(store._many(
        "SELECT id FROM af_artifacts WHERE document_id=:id AND run_id IS NULL AND kind='page_image'",
        {"id": document["id"]},
    )) == 2
    store.dispose()
