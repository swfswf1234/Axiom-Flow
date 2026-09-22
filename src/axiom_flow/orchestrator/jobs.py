"""后台任务执行器：单 worker 线程池，任务在独立 Session 中执行并落库。

设计事实源：`docs/design/parsing-pipeline.md`（错误处理与质量验收）。
- `background=True`（服务默认）：串行后台执行，避免并发压垮本地模型服务；
- `background=False`（测试）：同步内联执行；
- 引擎按任务行上已解析的引擎名构造（`engine_factory(name)`），不在执行期改选引擎。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.repositories import BookRepository, PageRepository, ParseJobRepository
from axiom_flow.engines.base import ParseEngine
from axiom_flow.orchestrator.pipeline import DEFAULT_WINDOW_SIZE, ParseOutcome, run_parse_job
from axiom_flow.schemas import JobStatus, LastParseResult, ParseStatus

logger = logging.getLogger(__name__)


class JobRunner:
    """解析任务执行器：提交后由后台线程（或同步）调用 `run_parse_job`。"""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        engine_factory: Callable[[str], ParseEngine],
        data_root: Path | str,
        background: bool = True,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._engine_factory = engine_factory
        self._data_root = Path(data_root)
        self._window_size = window_size
        self._executor: Executor | None = ThreadPoolExecutor(max_workers=1) if background else None

    def submit(self, *, job_id: str, book_id: str, pages: list[int] | None, engine: str) -> None:
        """提交任务：后台模式立即返回，内联模式同步执行。`engine` 为已解析的引擎名。"""
        if self._executor is None:
            self._run(job_id, book_id, pages, engine)
            return
        self._executor.submit(self._run, job_id, book_id, pages, engine)

    def _run(self, job_id: str, book_id: str, pages: list[int] | None, engine_name: str) -> None:
        engine = self._engine_factory(engine_name)
        try:
            with self._session_factory() as session:
                outcome: ParseOutcome = run_parse_job(
                    session=session,
                    engine=engine,
                    job_id=job_id,
                    book_id=book_id,
                    pages=pages,
                    data_root=self._data_root,
                    window_size=self._window_size,
                )
                session.commit()
            logger.info("parse job %s %s（parsed=%s/%s）", job_id, outcome.status, outcome.parsed, outcome.total)
        except Exception:  # noqa: BLE001 - 后台线程异常必须记录并落库，避免任务悬挂
            logger.exception("parse job %s 执行异常", job_id)
            with self._session_factory() as session:
                ParseJobRepository(session).mark_finished(
                    job_id, status=JobStatus.FAILED, error="内部错误（详见服务日志）"
                )
                # 书目进度必须同步收敛：否则异常任务把书永久留在 `parsing`（ADR 0007 决定 3）
                BookRepository(session).update_parse(
                    book_id,
                    parse_status=ParseStatus.PENDING,
                    pages_done=PageRepository(session).count_parsed(book_id, job_id),
                    last_parse_result=LastParseResult.FAILED,
                )
                session.commit()
        finally:
            close = getattr(engine, "close", None)
            if callable(close):
                close()

    def reconcile_on_startup(self) -> list[tuple[str, str]]:
        """启动期归一（[ADR 0007](../../../docs/adr/0007-parse-status-semantics.md) 决定 4）。

        把上一次进程遗留的 `queued`/`running` 任务改写为 `interrupted`，并把对应书目的进度收回
        `pending`；结局只在「该书最新一次任务被归一」时改为 `interrupted`。**必须在开始受理请求
        之前调用，且前提是 8902 单实例部署**：本表无跨进程所有权标记，多实例会把其他实例的活任务
        误判为中断（多实例前须改为心跳 + 后台回收）。返回被归一的 `(job_id, book_id)` 列表。
        """
        with self._session_factory() as session:
            jobs = ParseJobRepository(session)
            books = BookRepository(session)
            affected: list[tuple[str, str]] = []
            for job in jobs.reconcile_interrupted():
                latest = jobs.list_for_book(job.book_id)
                is_latest = bool(latest) and latest[-1].job_id == job.job_id
                books.mark_parse_idle(
                    job.book_id,
                    last_parse_result=LastParseResult.INTERRUPTED if is_latest else None,
                )
                affected.append((job.job_id, job.book_id))
            session.commit()
        if affected:
            logger.warning("启动期归一：%d 个遗留活动任务改写为 interrupted", len(affected))
        return affected

    def shutdown(self) -> None:
        """服务关闭：不再接收新任务，已排队任务取消。"""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
