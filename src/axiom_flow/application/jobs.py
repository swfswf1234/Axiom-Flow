"""
模块职责：编排任务提交和 Worker 执行，隔离 HTTP、供应商与领域规则。
设计关联（DesignRef）：docs/design/background-jobs.md
实现状态：Current
关联测试：tests/integration/test_jobs.py、tests/integration/test_api.py
"""

from dataclasses import dataclass
from typing import Any, Protocol

from axiom_flow.application.ports import PipelineFactory
from axiom_flow.domain.models import JobKind, NotFoundError, RetryableJobError


class JobRepository(Protocol):
    """任务应用服务依赖的仓储端口。"""

    def get_document(self, document_id: str) -> dict[str, Any] | None: ...
    def accepted_pages(self, document_id: str) -> list[dict[str, Any]]: ...
    def enqueue_job(
        self, kind: JobKind, aggregate_id: str, input_version: str,
        payload: dict[str, Any] | None = None, max_attempts: int = 3,
    ) -> tuple[dict[str, Any], bool]: ...
    def heartbeat_job(
        self, job_id: str, worker_id: str, lease_seconds: int,
        progress_current: int | None = None, progress_total: int | None = None,
    ) -> bool: ...
    def job_cancel_requested(self, job_id: str) -> bool: ...
    def list_jobs(self, aggregate_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]: ...
    def get_job(self, job_id: str) -> dict[str, Any] | None: ...
    def request_job_cancel(self, job_id: str) -> dict[str, Any]: ...
    def claim_next_job(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None: ...
    def complete_job(self, job_id: str, worker_id: str, result: dict[str, Any]) -> None: ...
    def cancel_job(self, job_id: str, worker_id: str) -> None: ...
    def fail_job(
        self, job_id: str, worker_id: str, error: dict[str, Any], retryable: bool,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class JobPolicy:
    """提交和执行任务所需的稳定配置快照。"""

    vision_model: str
    vision_contract_version: str
    vision_max_tokens: int
    vision_page_attempts: int
    model_call_budget: int
    knowledge_model: str
    worker_lease_seconds: int


class JobApplicationService:
    """提交幂等命令，并在持有租约时执行对应应用用例。"""

    def __init__(
        self, store: JobRepository, policy: JobPolicy, pipeline_factory: PipelineFactory,
    ) -> None:
        self.store = store
        self.policy = policy
        self.pipeline_factory = pipeline_factory

    def submit_parse(
        self, document_id: str, page_start: int = 1, page_end: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        document = self._document(document_id)
        document_pages = int(document["page_count"])
        resolved_end = page_end if page_end is not None else document_pages
        if page_start < 1 or resolved_end < page_start or resolved_end > document_pages:
            raise ValueError(f"解析页范围必须位于 1 到 {document_pages} 且起点不大于终点")
        selected_count = resolved_end - page_start + 1
        budget = min(self.policy.model_call_budget, selected_count * self.policy.vision_page_attempts)
        version = (
            f"{document['content_hash']}:{self.policy.vision_model}:{page_start}-{resolved_end}:"
            f"{self.policy.vision_contract_version}:{self.policy.vision_max_tokens}:"
            f"{self.policy.vision_page_attempts}:{budget}"
        )
        return self.store.enqueue_job(
            JobKind.PARSE_DOCUMENT, document_id, version,
            {
                "model_call_budget": budget, "page_count": selected_count,
                "page_start": page_start, "page_end": resolved_end,
            },
        )

    def submit_extraction(self, document_id: str) -> tuple[dict[str, Any], bool]:
        self._document(document_id)
        pages = self.store.accepted_pages(document_id)
        if not pages:
            raise ValueError("没有已接受的知识正文页面")
        version = f"{pages[0]['run_id']}:{self.policy.knowledge_model}"
        return self.store.enqueue_job(JobKind.EXTRACT_KNOWLEDGE, document_id, version)

    def list_jobs(self, document_id: str | None = None) -> list[dict[str, Any]]:
        return self.store.list_jobs(document_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise NotFoundError("任务不存在")
        return job

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        self.get_job(job_id)
        return self.store.request_job_cancel(job_id)

    def claim_next(self, worker_id: str) -> dict[str, Any] | None:
        return self.store.claim_next_job(worker_id, self.policy.worker_lease_seconds)

    def cancel_requested(self, job_id: str) -> bool:
        return self.store.job_cancel_requested(job_id)

    def complete(self, job_id: str, worker_id: str, result: dict[str, Any]) -> dict[str, Any]:
        self.store.complete_job(job_id, worker_id, result)
        return self.get_job(job_id)

    def cancel(self, job_id: str, worker_id: str) -> dict[str, Any]:
        self.store.cancel_job(job_id, worker_id)
        return self.get_job(job_id)

    def fail(self, job_id: str, worker_id: str, exc: BaseException) -> dict[str, Any]:
        error = {"code": type(exc).__name__, "message": str(exc)[:1000]}
        self.store.fail_job(job_id, worker_id, error, self.is_retryable(exc))
        return self.get_job(job_id)

    async def execute(self, job: dict[str, Any], worker_id: str) -> dict[str, Any]:
        bound_budget = int(job.get("payload", {}).get("model_call_budget", self.policy.model_call_budget))
        pipeline = self.pipeline_factory(bound_budget)

        def progress(current: int, total: int) -> None:
            if not self.store.heartbeat_job(
                job["id"], worker_id, self.policy.worker_lease_seconds, current, total,
            ):
                raise RuntimeError("任务租约已失效")

        def cancelled() -> bool:
            return self.store.job_cancel_requested(job["id"])

        if job["kind"] == JobKind.PARSE_DOCUMENT.value:
            payload = job.get("payload", {})
            return await pipeline.parse_document(
                job["aggregate_id"], job["id"], progress, cancelled,
                int(payload.get("page_start", 1)), int(payload.get("page_end", payload.get("page_count", 1))),
            )
        if job["kind"] == JobKind.EXTRACT_KNOWLEDGE.value:
            if cancelled():
                raise InterruptedError("任务已请求取消")
            result = await pipeline.generate_candidates(job["aggregate_id"], job["id"])
            progress(1, 1)
            return {"document_id": job["aggregate_id"], "candidate_count": len(result)}
        raise ValueError(f"不支持的任务类型：{job['kind']}")

    @staticmethod
    def is_retryable(exc: BaseException) -> bool:
        return isinstance(exc, RetryableJobError)

    def _document(self, document_id: str) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if not document:
            raise NotFoundError("文档不存在")
        return document
