"""后台任务执行器：单 worker 线程池，任务在独立 Session 中执行并落库。

设计事实源：`docs/design/parsing-pipeline.md`（错误处理与质量验收）。
- `background=True`（服务默认）：串行后台执行，避免并发压垮本地模型服务；
- `background=False`（测试）：同步内联执行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.repositories import ParseJobRepository
from axiom_flow.engines.base import ParseEngine
from axiom_flow.orchestrator.pipeline import ParseOutcome, run_parse_job
from axiom_flow.schemas import JobStatus

logger = logging.getLogger(__name__)


class JobRunner:
    """解析任务执行器：提交后由后台线程（或同步）调用 `run_parse_job`。"""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        engine_factory: Callable[[], ParseEngine],
        data_root: Path | str,
        background: bool = True,
    ) -> None:
        self._session_factory = session_factory
        self._engine_factory = engine_factory
        self._data_root = Path(data_root)
        self._executor: Executor | None = ThreadPoolExecutor(max_workers=1) if background else None

    def submit(self, *, job_id: str, book_id: str, pages: list[int] | None) -> None:
        """提交任务：后台模式立即返回，内联模式同步执行。"""
        if self._executor is None:
            self._run(job_id, book_id, pages)
            return
        self._executor.submit(self._run, job_id, book_id, pages)

    def _run(self, job_id: str, book_id: str, pages: list[int] | None) -> None:
        engine = self._engine_factory()
        try:
            with self._session_factory() as session:
                outcome: ParseOutcome = run_parse_job(
                    session=session,
                    engine=engine,
                    job_id=job_id,
                    book_id=book_id,
                    pages=pages,
                    data_root=self._data_root,
                )
                session.commit()
            logger.info("parse job %s %s（parsed=%s/%s）", job_id, outcome.status, outcome.parsed, outcome.total)
        except Exception:  # noqa: BLE001 - 后台线程异常必须记录并落库，避免任务悬挂
            logger.exception("parse job %s 执行异常", job_id)
            with self._session_factory() as session:
                ParseJobRepository(session).mark_finished(job_id, status=JobStatus.FAILED, error="内部错误（详见服务日志）")
                session.commit()
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

    def shutdown(self) -> None:
        """服务关闭：不再接收新任务，已排队任务取消。"""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
