"""
模块职责：领取 MySQL 租约并可靠执行 v0.3 后台任务。
设计关联（DesignRef）：docs/design/background-jobs.md
实现状态：Current
关联测试：tests/test_v03_jobs.py
"""

import asyncio
import logging
import time
from typing import Any

from backend.app.config import Settings
from backend.application.jobs import JobApplicationService
from backend.infrastructure.mysql import V03Store

LOGGER = logging.getLogger("axiom_flow.worker")


class Worker:
    """单进程 Worker；并发通过运行多个进程扩展。"""

    def __init__(self, service: JobApplicationService, worker_id: str) -> None:
        self.service = service
        self.store = service.store
        self.worker_id = worker_id

    def run_once(self) -> dict[str, Any] | None:
        job = self.store.claim_next_job(self.worker_id, self.service.settings.worker_lease_seconds)
        if not job:
            return None
        try:
            result = asyncio.run(self.service.execute(job, self.worker_id))
            if self.store.job_cancel_requested(job["id"]):
                self.store.cancel_job(job["id"], self.worker_id)
            else:
                self.store.complete_job(job["id"], self.worker_id, result)
        except InterruptedError:
            self.store.cancel_job(job["id"], self.worker_id)
        except BaseException as exc:
            error = {"code": type(exc).__name__, "message": str(exc)[:1000]}
            self.store.fail_job(job["id"], self.worker_id, error, self.service.is_retryable(exc))
            LOGGER.exception("任务执行失败：%s", job["id"])
        return self.store.get_job(job["id"])

    def run_forever(self) -> None:
        while True:
            if self.run_once() is None:
                time.sleep(self.service.settings.worker_poll_seconds)


def build_worker(settings: Settings | None = None) -> Worker:
    resolved = settings or Settings()
    store = V03Store(resolved.mysql_url, resolved.mysql_pool_size, resolved.mysql_max_overflow)
    store.require_schema()
    return Worker(JobApplicationService(store, resolved), resolved.worker_id)
