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

from axiom_flow.application.jobs import JobApplicationService

LOGGER = logging.getLogger("axiom_flow.worker")


class Worker:
    """单进程 Worker；并发通过运行多个进程扩展。"""

    def __init__(self, service: JobApplicationService, worker_id: str, poll_seconds: float = 1.0) -> None:
        self.service = service
        self.worker_id = worker_id
        self.poll_seconds = poll_seconds

    def run_once(self) -> dict[str, Any] | None:
        job = self.service.claim_next(self.worker_id)
        if not job:
            return None
        try:
            result = asyncio.run(self.service.execute(job, self.worker_id))
            if self.service.cancel_requested(job["id"]):
                return self.service.cancel(job["id"], self.worker_id)
            else:
                return self.service.complete(job["id"], self.worker_id, result)
        except InterruptedError:
            return self.service.cancel(job["id"], self.worker_id)
        except BaseException as exc:
            LOGGER.exception("任务执行失败：%s", job["id"])
            return self.service.fail(job["id"], self.worker_id, exc)

    def run_forever(self) -> None:
        while True:
            if self.run_once() is None:
                time.sleep(self.poll_seconds)


def build_worker(settings: Any | None = None) -> Worker:
    from axiom_flow.bootstrap import build_container

    container = build_container(settings)
    container.start()
    return Worker(container.jobs, container.settings.worker_id, container.settings.worker_poll_seconds)
