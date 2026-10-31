"""后台任务执行器：单 worker 线程池，任务在独立 Session 中执行并落库。

设计事实源：`docs/design/parsing-pipeline.md`（错误处理与质量验收）、
`docs/design/knowledge-analysis.md`（`KA-4` 的跨书不并发与 `KA-27` 的批次流水）。
- `background=True`（服务默认）：串行后台执行，避免并发压垮本地模型服务；
- `background=False`（测试）：同步内联执行；
- 引擎按任务行上已解析的引擎名构造（`engine_factory(name)`），不在执行期改选引擎。

梳理批次与解析任务**共用这一个 worker**：`KA-4` 的「跨书不并发」沿用 `PM-13` 的单队列前提，
两条腿各自排队就等于没有串行。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.llm_ledger import LlmCallIdentity, LlmCallLedger
from axiom_flow.db.repositories import (
    BookRepository,
    KpBatchRow,
    OperationRepository,
    PageRepository,
)
from axiom_flow.engines.base import KnowledgeEngine, ParseEngine
from axiom_flow.orchestrator.knowledge import (
    KpAcceptance,
    LedgeredKnowledgeEngine,
    kp_batch_row,
    run_knowledge_job,
)
from axiom_flow.orchestrator.pipeline import DEFAULT_WINDOW_SIZE, ParseOutcome, run_parse_job
from axiom_flow.schemas import JobStatus, LastParseResult, ParseStatus

logger = logging.getLogger(__name__)


class KpLedgerWiringError(RuntimeError):
    """梳理槽位接线不全：接了引擎却没接账、或接了账却没给身份。

    `KA-23` 要「梳理作业与调用账同批交付」，R6 甲案把它落成执行体的拒跑面：受理期抛错、零写入，
    不静默跑一批没有账的调用。API 面映射为 503（误配的是服务侧接线，不是调用方的请求体）。
    """


class JobRunner:
    """解析任务与梳理批次的执行器：提交后由后台线程（或同步）调用对应的 `run_*_job`。"""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        engine_factory: Callable[[str], ParseEngine],
        data_root: Path | str,
        background: bool = True,
        window_size: int = DEFAULT_WINDOW_SIZE,
        knowledge_engine_factory: Callable[[str], KnowledgeEngine] | None = None,
        kp_call_ledger: LlmCallLedger | None = None,
        kp_call_identity: LlmCallIdentity | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._engine_factory = engine_factory
        self._knowledge_engine_factory = knowledge_engine_factory
        self._kp_call_ledger = kp_call_ledger
        self._kp_call_identity = kp_call_identity
        self._data_root = Path(data_root)
        self._window_size = window_size
        self._executor: Executor | None = ThreadPoolExecutor(max_workers=1) if background else None
        #: 本进程正在跑的梳理批次（`(book_id, gen_batch)`）：只服务进度读面那一窗，见 `kp_in_flight`。
        self._kp_inflight: set[tuple[str, str]] = set()

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
                OperationRepository(session).mark_finished(
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

    def submit_knowledge(self, *, acceptance: KpAcceptance, engine_name: str) -> None:
        """提交梳理批次：与解析任务共用同一 worker 串行（`KA-4` 的「跨书不并发」沿用 `PM-13` 单队列）。

        受理门已在 API 面跑过（404／422／409 都判在提交之前），本函数只负责排队；
        执行期第一眼还会复查在飞（见 `run_knowledge_job`）。

        排队之前先看接线：`KA-23` 要求梳理作业与调用账同批交付，所以「有引擎无账」或「有账无身份」
        都在这里当场拒（R6 甲案）。宁可 503 也不跑一批查无此账的调用——那正是把账目推成永久欠账的形状。
        例外只有一种（R8 甲）：适配器自报 `accounts_externally`，账由 8900 在同一批里落，本仓再包
        一层就成了同一次调用两行账。判据取自适配器本身而不是引擎名——名字是配置值，声明才是实现面的交代。
        """
        if self._knowledge_engine_factory is None:
            raise RuntimeError("梳理槽位未接入：文本引擎工厂缺失（API 面应先判 503，`KA-22`）")
        engine = self._accounted(self._knowledge_engine_factory(engine_name), acceptance)
        # 登记发在派发之前（`KA-40`）：从这一刻起进度面就认得这一批，不等第一行落地。
        self._kp_inflight.add((acceptance.book_id, acceptance.gen_batch))
        if self._executor is None:
            self._run_knowledge(engine, acceptance)
            return
        self._executor.submit(self._run_knowledge, engine, acceptance)

    def kp_in_flight(self, book_id: str, gen_batch: str) -> bool:
        """本进程是否正在跑这一批（`KA-40` 的凭据腿，只补进度读面那一窗）。

        `KA-4` 的在飞判据仍只看 `af_kp` 的 `draft` 行：逐页落行之后，第一页整理完库里就有色。但
        「受理到第一行落库」之间一行也没有、批次流水也还没发（`KA-27` 的整批一行发在批末），进度面
        那一刻只有 404 可读——本登记补的就是这一窗。

        它**不参与**受理门与执行期复查（那两条只认库里的行，跨进程排他不靠进程内的记性），也不落盘：
        进程死了登记随之消失，而残留的 `draft` 行由启动期归一批 `rejected`（`KA-9`）。两条腿各管各的，
        所以「登记说在飞、库里没行」只会出现在批次真的正在跑的那几分钟里，不会读成永久在飞。
        """
        return (book_id, gen_batch) in self._kp_inflight

    def _accounted(self, engine: KnowledgeEngine, acceptance: KpAcceptance) -> KnowledgeEngine:
        """决定这一批的账走哪条腿：本仓包一层（`KA-23`），或由适配器声明账在门外（R8 甲）。"""
        if getattr(engine, "accounts_externally", False):
            return engine
        ledger = self._kp_call_ledger
        identity = self._kp_call_identity
        missing = [
            label
            for label, slot in (("调用账（`LlmCallLedger`）", ledger), ("调用身份（`LlmCallIdentity`）", identity))
            if slot is None
        ]
        if missing:
            raise KpLedgerWiringError(
                f"梳理槽位接线不全：缺 {'、'.join(missing)}——受理期拒跑，不静默跑一批无账的调用"
            )
        return LedgeredKnowledgeEngine(
            engine, ledger, identity, book_id=acceptance.book_id, gen_batch=acceptance.gen_batch
        )

    def _run_knowledge(self, engine: KnowledgeEngine, acceptance: KpAcceptance) -> None:
        """跑一批梳理并落一行批次流水（`KA-27`）：成功与异常各一行，`result` 只在成功时有。

        进来的引擎已由 `_accounted` 决定过账面归属（本仓包装器或门外自记），本函数是梳理通路
        唯一的执行入口：批次身份交给适配器那一步在 `run_knowledge_job` 的第一眼。

        异常路径**不**处置残留的 `draft` 行：`KA-9` 把中断批次的归一排给启动期那一步，执行期再开
        一条作废路径就是设计里的第四种读法（该形状缺口已在 PLAN-025 登记等裁，不在实现批自裁）。
        """
        try:
            with self._session_factory() as session:
                outcome = run_knowledge_job(
                    session, data_root=self._data_root, engine=engine, acceptance=acceptance
                )
                session.commit()
            self._record_kp_batch(kp_batch_row(acceptance, outcome))
            logger.info(
                "kp batch %s ok（candidate=%s outline=%s point=%s）",
                acceptance.gen_batch,
                outcome.candidate_rows,
                outcome.outline_rows,
                outcome.point_rows,
            )
        except Exception:  # noqa: BLE001 - 后台线程的失败必须落账，否则批次无声消失
            logger.exception("kp batch %s 执行异常", acceptance.gen_batch)
            self._record_kp_batch(
                KpBatchRow(
                    gen_batch=acceptance.gen_batch,
                    book_id=acceptance.book_id,
                    job_id=acceptance.evidence_job_id,
                    status="failed",
                    page_start=acceptance.page_start,
                    page_end=acceptance.page_end,
                    stage=acceptance.stage,
                    error="内部错误（详见服务日志）",
                )
            )
        finally:
            # 撤凭据发在关引擎之前（`KA-40`）：`close` 是「这批已经结束」的信号点，先撤再关——
            # 反过来就有一窗「事件已发出、登记还没撤」，等待中的读面会把已结束的批次读成在飞。
            self._kp_inflight.discard((acceptance.book_id, acceptance.gen_batch))
            close = getattr(engine, "close", None)
            if callable(close):
                close()

    def _record_kp_batch(self, row: KpBatchRow) -> None:
        """流水单独一个会话写：执行批次的会话此刻要么已提交要么正回滚，失败批次的账面不能跟着消失。"""
        with self._session_factory() as session:
            OperationRepository(session).create_kp_batch(row)
            session.commit()

    def reconcile_on_startup(self) -> list[tuple[str, str]]:
        """启动期归一（[ADR 0007](../../../docs/adr/0007-parse-status-semantics.md) 决定 4）。

        把上一次进程遗留的 `queued`/`running` 任务改写为 `interrupted`，并把对应书目的进度收回
        `pending`；结局只在「该书最新一次任务被归一」时改为 `interrupted`。**必须在开始受理请求
        之前调用，且前提是 8902 单实例部署**：本表无跨进程所有权标记，多实例会把其他实例的活任务
        误判为中断（多实例前须改为心跳 + 后台回收）。返回被归一的 `(job_id, book_id)` 列表。
        """
        with self._session_factory() as session:
            jobs = OperationRepository(session)
            books = BookRepository(session)
            affected: list[tuple[str, str]] = []
            for job in jobs.reconcile_interrupted():
                latest = jobs.list_versions(job.book_id)
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
        """服务关闭：不再接收新任务，已排队任务取消，在途登记一并清空（`KA-40` 的凭据不跨进程）。"""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._kp_inflight.clear()
