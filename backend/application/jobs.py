"""
模块职责：编排任务提交和 Worker 执行，隔离 HTTP、供应商与领域规则。
设计关联（DesignRef）：docs/design/background-jobs.md
实现状态：Current
关联测试：tests/test_v03_jobs.py、tests/test_v03_api.py
"""

from collections.abc import Callable
from typing import Any

import httpx

from backend.app.config import Settings
from backend.app.pipeline import PipelineService
from backend.app.providers import BailianProvider, KnowledgeProvider, VisionProvider
from backend.domain.models import JobKind
from backend.infrastructure.mysql import V03Store

ProviderFactory = Callable[[], tuple[VisionProvider, KnowledgeProvider]]


class JobApplicationService:
    """提交幂等命令，并在持有租约时执行对应应用用例。"""

    def __init__(
        self, store: V03Store, settings: Settings,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.provider_factory = provider_factory or self._default_providers

    def submit_parse(self, document_id: str) -> tuple[dict[str, Any], bool]:
        document = self._document(document_id)
        version = f"{document['content_hash']}:{self.settings.vision_model}:{self.settings.vision_fallback_model}"
        return self.store.enqueue_job(JobKind.PARSE_DOCUMENT, document_id, version)

    def submit_extraction(self, document_id: str) -> tuple[dict[str, Any], bool]:
        self._document(document_id)
        pages = self.store.accepted_pages(document_id)
        if not pages:
            raise ValueError("没有已接受的知识正文页面")
        version = f"{pages[0]['run_id']}:{self.settings.knowledge_model}"
        return self.store.enqueue_job(JobKind.EXTRACT_KNOWLEDGE, document_id, version)

    async def execute(self, job: dict[str, Any], worker_id: str) -> dict[str, Any]:
        vision, knowledge = self.provider_factory()
        pipeline = PipelineService(self.store, self.settings, vision, knowledge)

        def progress(current: int, total: int) -> None:
            if not self.store.heartbeat_job(
                job["id"], worker_id, self.settings.worker_lease_seconds, current, total,
            ):
                raise RuntimeError("任务租约已失效")

        def cancelled() -> bool:
            return self.store.job_cancel_requested(job["id"])

        if job["kind"] == JobKind.PARSE_DOCUMENT.value:
            return await pipeline.parse_document(job["aggregate_id"], job["id"], progress, cancelled)
        if job["kind"] == JobKind.EXTRACT_KNOWLEDGE.value:
            if cancelled():
                raise InterruptedError("任务已请求取消")
            result = await pipeline.generate_candidates(job["aggregate_id"], job["id"])
            progress(1, 1)
            return {"document_id": job["aggregate_id"], "candidate_count": len(result)}
        raise ValueError(f"不支持的任务类型：{job['kind']}")

    @staticmethod
    def is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False

    def _document(self, document_id: str) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if not document:
            raise KeyError("文档不存在")
        return document

    def _default_providers(self) -> tuple[VisionProvider, KnowledgeProvider]:
        provider = BailianProvider(self.settings)
        return provider, provider
