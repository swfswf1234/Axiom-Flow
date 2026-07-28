"""
模块职责：实现 v0.3 MySQL 任务租约、版本化抽取和审阅事件仓储。
设计关联（DesignRef）：docs/architecture/v03-target.md
实现状态：Current
关联测试：tests/test_v03_jobs.py、tests/test_v03_api.py
"""

import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.app.store import MySQLStore, _now
from backend.domain.models import ExtractionRunStatus, JobKind, JobResource, JobStatus, ParseRunStatus, ReviewStatus


class V03Store(MySQLStore):
    """在 v0.2 数据访问之上提供 v0.3 追加式和任务化语义。"""

    def get_document_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM af_documents WHERE content_hash = :hash", {"hash": content_hash})

    def register_artifact(self, document_id: str, run_id: str | None, kind: str, path: Path) -> None:
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.engine.begin() as connection:
            existing = connection.execute(
                text("""SELECT id FROM af_artifacts WHERE document_id = :document_id
                    AND kind = :kind AND content_hash = :content_hash LIMIT 1"""),
                {"document_id": document_id, "kind": kind, "content_hash": content_hash},
            ).scalar_one_or_none()
            if existing:
                return
            connection.execute(
                text("""INSERT INTO af_artifacts
                    (id, document_id, run_id, kind, path, content_hash, created_at)
                    VALUES (:id, :document_id, :run_id, :kind, :path, :content_hash, :created_at)"""),
                {"id": str(uuid.uuid4()), "document_id": document_id, "run_id": run_id, "kind": kind,
                 "path": str(path), "content_hash": content_hash, "created_at": _now()},
            )

    def replace_pages(self, run_id: str, document_id: str, pages: list[dict[str, Any]]) -> None:
        """保存兼容页快照，并将块、来源和质量拆为可查询记录。"""
        super().replace_pages(run_id, document_id, pages)
        block_statement = text("""INSERT INTO af_content_blocks
            (id,page_id,kind,content,latex,order_no,confidence,source)
            VALUES (:id,:page_id,:kind,:content,:latex,:order_no,:confidence,:source)""")
        span_statement = text("""INSERT INTO af_source_spans
            (id,block_id,page_no,bbox_json,quoted_text)
            VALUES (:id,:block_id,:page_no,CAST(:bbox_json AS JSON),:quoted_text)""")
        quality_statement = text("""INSERT INTO af_quality_reports
            (id,page_id,status,issues_json,metrics_json,created_at)
            VALUES (:id,:page_id,:status,CAST(:issues_json AS JSON),CAST(:metrics_json AS JSON),:created_at)""")
        with self.engine.begin() as connection:
            for page in pages:
                blocks = json.loads(page["blocks_json"])
                for order, item in enumerate(blocks):
                    block_id = str(uuid.uuid4())
                    connection.execute(block_statement, {
                        "id": block_id, "page_id": page["id"], "kind": str(item.get("kind") or "paragraph"),
                        "content": str(item.get("content") or ""), "latex": item.get("latex"),
                        "order_no": int(item.get("order_no", order)),
                        "confidence": float(item.get("confidence", 0.0)), "source": str(item.get("source") or "unknown"),
                    })
                    connection.execute(span_statement, {
                        "id": str(uuid.uuid4()), "block_id": block_id, "page_no": page["page_no"],
                        "bbox_json": json.dumps(item.get("bbox")) if item.get("bbox") is not None else None,
                        "quoted_text": str(item.get("quote") or item.get("content") or "")[:1000],
                    })
                quality = json.loads(page["quality_json"])
                connection.execute(quality_statement, {
                    "id": str(uuid.uuid4()), "page_id": page["id"],
                    "status": str(quality.get("status") or "needs_review"),
                    "issues_json": json.dumps(quality.get("issues") or [], ensure_ascii=False),
                    "metrics_json": json.dumps({key: value for key, value in quality.items() if key not in {"status", "issues"}}, ensure_ascii=False),
                    "created_at": _now(),
                })
                self.register_artifact(document_id, run_id, "page_image", Path(page["image_path"]))

    def list_pages_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._many("SELECT * FROM af_pages WHERE run_id=:id ORDER BY page_no", {"id": run_id})
        return [self._decode_page(row) for row in rows]

    def enqueue_job(
        self,
        kind: JobKind,
        aggregate_id: str,
        input_version: str,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> tuple[dict[str, Any], bool]:
        """锁定聚合根后创建任务，避免并发请求产生两个活动任务。"""
        now = _now()
        with self.engine.begin() as connection:
            document = connection.execute(
                text("SELECT id FROM af_documents WHERE id = :id FOR UPDATE"), {"id": aggregate_id}
            ).scalar_one_or_none()
            if not document:
                raise KeyError("文档不存在")
            active = connection.execute(
                text("""SELECT * FROM af_jobs WHERE aggregate_id = :aggregate_id AND kind = :kind
                    AND input_version = :input_version AND status IN ('queued','running','cancel_requested')
                    ORDER BY created_at DESC LIMIT 1"""),
                {"aggregate_id": aggregate_id, "kind": kind.value, "input_version": input_version},
            ).mappings().first()
            if active:
                return self._decode_job(dict(active)), False
            row = {
                "id": str(uuid.uuid4()), "kind": kind.value, "aggregate_id": aggregate_id,
                "input_version": input_version, "status": JobStatus.QUEUED.value,
                "payload_json": json.dumps(payload or {}, ensure_ascii=False), "max_attempts": max_attempts,
                "created_at": now, "updated_at": now,
            }
            connection.execute(
                text("""INSERT INTO af_jobs
                    (id, kind, aggregate_id, input_version, status, payload_json, progress_current,
                     progress_total, attempt, max_attempts, created_at, updated_at)
                    VALUES (:id, :kind, :aggregate_id, :input_version, :status, CAST(:payload_json AS JSON),
                     0, 0, 0, :max_attempts, :created_at, :updated_at)"""), row,
            )
        return self.get_job(row["id"]), True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_jobs WHERE id = :id", {"id": job_id})
        return self._decode_job(row) if row else None

    def list_jobs(self, aggregate_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if aggregate_id:
            rows = self._many(
                "SELECT * FROM af_jobs WHERE aggregate_id = :id ORDER BY created_at DESC LIMIT :limit",
                {"id": aggregate_id, "limit": limit},
            )
        else:
            rows = self._many("SELECT * FROM af_jobs ORDER BY created_at DESC LIMIT :limit", {"limit": limit})
        return [self._decode_job(row) for row in rows]

    def claim_next_job(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = _now()
        expires = now + timedelta(seconds=lease_seconds)
        with self.engine.begin() as connection:
            connection.execute(
                text("""UPDATE af_jobs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                    updated_at = :now WHERE status = 'running' AND lease_expires_at < :now
                    AND attempt < max_attempts"""), {"now": now}
            )
            connection.execute(
                text("""UPDATE af_jobs SET status = 'failed', lease_owner = NULL, lease_expires_at = NULL,
                    error_json = JSON_OBJECT('code','lease_expired','message','任务租约耗尽'),
                    finished_at = :now, updated_at = :now WHERE status = 'running'
                    AND lease_expires_at < :now AND attempt >= max_attempts"""), {"now": now}
            )
            row = connection.execute(
                text("""SELECT * FROM af_jobs WHERE status = 'queued' ORDER BY created_at ASC
                    LIMIT 1 FOR UPDATE SKIP LOCKED""")
            ).mappings().first()
            if not row:
                return None
            connection.execute(
                text("""UPDATE af_jobs SET status = 'running', lease_owner = :worker,
                    lease_expires_at = :expires, attempt = attempt + 1,
                    started_at = COALESCE(started_at, :now), updated_at = :now WHERE id = :id"""),
                {"worker": worker_id, "expires": expires, "now": now, "id": row["id"]},
            )
        return self.get_job(str(row["id"]))

    def heartbeat_job(
        self, job_id: str, worker_id: str, lease_seconds: int,
        progress_current: int | None = None, progress_total: int | None = None,
    ) -> bool:
        values = {
            "id": job_id, "worker": worker_id, "expires": _now() + timedelta(seconds=lease_seconds),
            "now": _now(), "current": progress_current, "total": progress_total,
        }
        with self.engine.begin() as connection:
            result = connection.execute(
                text("""UPDATE af_jobs SET lease_expires_at = :expires, updated_at = :now,
                    progress_current = COALESCE(:current, progress_current),
                    progress_total = COALESCE(:total, progress_total)
                    WHERE id = :id AND lease_owner = :worker AND status IN ('running','cancel_requested')"""), values,
            )
        return result.rowcount == 1

    def request_job_cancel(self, job_id: str) -> dict[str, Any]:
        now = _now()
        with self.engine.begin() as connection:
            row = connection.execute(text("SELECT status FROM af_jobs WHERE id = :id FOR UPDATE"), {"id": job_id}).first()
            if not row:
                raise KeyError("任务不存在")
            if row[0] == JobStatus.QUEUED.value:
                connection.execute(
                    text("UPDATE af_jobs SET status='cancelled', finished_at=:now, updated_at=:now WHERE id=:id"),
                    {"id": job_id, "now": now},
                )
            elif row[0] == JobStatus.RUNNING.value:
                connection.execute(
                    text("UPDATE af_jobs SET status='cancel_requested', updated_at=:now WHERE id=:id"),
                    {"id": job_id, "now": now},
                )
        return self.get_job(job_id)

    def job_cancel_requested(self, job_id: str) -> bool:
        row = self._one("SELECT status FROM af_jobs WHERE id = :id", {"id": job_id})
        return bool(row and row["status"] == JobStatus.CANCEL_REQUESTED.value)

    def complete_job(self, job_id: str, worker_id: str, result: dict[str, Any]) -> None:
        self._finish_job(job_id, worker_id, JobStatus.SUCCEEDED, result=result)

    def cancel_job(self, job_id: str, worker_id: str) -> None:
        self._finish_job(job_id, worker_id, JobStatus.CANCELLED, result={"cancelled": True})

    def fail_job(self, job_id: str, worker_id: str, error: dict[str, Any], retryable: bool) -> None:
        now = _now()
        with self.engine.begin() as connection:
            row = connection.execute(
                text("SELECT attempt, max_attempts FROM af_jobs WHERE id=:id AND lease_owner=:worker FOR UPDATE"),
                {"id": job_id, "worker": worker_id},
            ).first()
            if not row:
                return
            retry = retryable and row[0] < row[1]
            connection.execute(
                text("""UPDATE af_jobs SET status=:status, error_json=CAST(:error AS JSON),
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=:now, finished_at=:finished
                    WHERE id=:id"""),
                {"id": job_id, "status": "queued" if retry else "failed",
                 "error": json.dumps(error, ensure_ascii=False), "now": now, "finished": None if retry else now},
            )

    def _finish_job(
        self, job_id: str, worker_id: str, status: JobStatus, result: dict[str, Any] | None = None,
    ) -> None:
        now = _now()
        with self.engine.begin() as connection:
            changed = connection.execute(
                text("""UPDATE af_jobs SET status=:status, result_json=CAST(:result AS JSON),
                    lease_owner=NULL, lease_expires_at=NULL, finished_at=:now, updated_at=:now
                    WHERE id=:id AND lease_owner=:worker AND status IN ('running','cancel_requested')"""),
                {"id": job_id, "worker": worker_id, "status": status.value,
                 "result": json.dumps(result or {}, ensure_ascii=False), "now": now},
            )
            if changed.rowcount != 1:
                raise RuntimeError("任务租约已失效")

    def create_parse_run(self, document_id: str, provider_summary: dict[str, Any], job_id: str | None = None) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "job_id": job_id, "status": "parsing",
               "provider_summary": json.dumps(provider_summary, ensure_ascii=False), "model_calls": 0,
               "created_at": _now()}
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO af_parse_runs
                    (id, document_id, job_id, status, provider_summary, model_calls, created_at)
                    VALUES (:id, :document_id, :job_id, :status, CAST(:provider_summary AS JSON),
                    :model_calls, :created_at)"""), row,
            )
        return row

    def finish_parse_run(self, run_id: str, status: str, model_calls: int, error: dict[str, Any] | None = None) -> None:
        status = ParseRunStatus(status).value
        with self.engine.begin() as connection:
            connection.execute(
                text("""UPDATE af_parse_runs SET status=:status, model_calls=:calls, finished_at=:finished,
                    error_json=CAST(:error AS JSON) WHERE id=:id"""),
                {"id": run_id, "status": status, "calls": model_calls, "finished": _now(),
                 "error": json.dumps(error, ensure_ascii=False) if error else None},
            )

    def list_parse_runs(self, document_id: str) -> list[dict[str, Any]]:
        rows = self._many(
            "SELECT * FROM af_parse_runs WHERE document_id=:id ORDER BY created_at DESC", {"id": document_id}
        )
        for row in rows:
            row["provider_summary"] = self._json(row["provider_summary"])
            row["error"] = self._json(row.pop("error_json"))
        return rows

    def create_extraction_run(self, document_id: str, parse_run_id: str, job_id: str | None, summary: dict[str, Any]) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "parse_run_id": parse_run_id,
               "job_id": job_id, "status": "extracting", "provider_summary": json.dumps(summary, ensure_ascii=False),
               "created_at": _now()}
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO af_extraction_runs
                    (id, document_id, parse_run_id, job_id, status, provider_summary, model_calls, created_at)
                    VALUES (:id,:document_id,:parse_run_id,:job_id,:status,CAST(:provider_summary AS JSON),0,:created_at)"""), row,
            )
        return row

    def finish_extraction_run(self, run_id: str, status: str, calls: int, error: dict[str, Any] | None = None) -> None:
        status = ExtractionRunStatus(status).value
        with self.engine.begin() as connection:
            connection.execute(
                text("""UPDATE af_extraction_runs SET status=:status, model_calls=:calls,
                    error_json=CAST(:error AS JSON), finished_at=:now WHERE id=:id"""),
                {"id": run_id, "status": status, "calls": calls, "now": _now(),
                 "error": json.dumps(error, ensure_ascii=False) if error else None},
            )

    def append_candidates(
        self, extraction_run_id: str, document_id: str,
        candidates: list[dict[str, Any]], edges: list[dict[str, Any]],
    ) -> None:
        for row in candidates:
            row["extraction_run_id"] = extraction_run_id
        for row in edges:
            row["extraction_run_id"] = extraction_run_id
        with self.engine.begin() as connection:
            if candidates:
                connection.execute(text("""INSERT INTO af_candidates
                    (id, document_id, extraction_run_id, kind, title, content, evidence_json, review_status, review_reason)
                    VALUES (:id,:document_id,:extraction_run_id,:kind,:title,:content,CAST(:evidence_json AS JSON),:review_status,:review_reason)"""), candidates)
            if edges:
                connection.execute(text("""INSERT INTO af_edges
                    (id, document_id, extraction_run_id, source_id, target_id, relation, evidence_json, review_status)
                    VALUES (:id,:document_id,:extraction_run_id,:source_id,:target_id,:relation,CAST(:evidence_json AS JSON),:review_status)"""), edges)

    def _latest_extraction_id(self, document_id: str) -> str | None:
        row = self._one("""SELECT id FROM af_extraction_runs WHERE document_id=:id AND status='succeeded'
            ORDER BY created_at DESC LIMIT 1""", {"id": document_id})
        return str(row["id"]) if row else None

    def list_candidates(self, document_id: str) -> list[dict[str, Any]]:
        extraction_id = self._latest_extraction_id(document_id)
        if not extraction_id:
            return super().list_candidates(document_id)
        rows = self._many("SELECT * FROM af_candidates WHERE extraction_run_id=:id ORDER BY title", {"id": extraction_id})
        return [self._decode_candidate(row) for row in rows]

    def list_edges(self, document_id: str) -> list[dict[str, Any]]:
        extraction_id = self._latest_extraction_id(document_id)
        if not extraction_id:
            return super().list_edges(document_id)
        rows = self._many("SELECT * FROM af_edges WHERE extraction_run_id=:id ORDER BY relation,id", {"id": extraction_id})
        return [self._decode_edge(row) for row in rows]

    def review_page(self, page_id: str, status: str, reason: str) -> None:
        super().review_page(page_id, status, reason)
        self._add_review_event("page", page_id, status, reason)

    def review_candidate(self, candidate_id: str, status: str, reason: str) -> None:
        super().review_candidate(candidate_id, status, reason)
        self._add_review_event("knowledge_node", candidate_id, status, reason)

    def review_edge(self, edge_id: str, status: str, reason: str = "") -> None:
        status = ReviewStatus(status).value
        super().review_edge(edge_id, status)
        self._add_review_event("knowledge_edge", edge_id, status, reason)

    def list_review_events(self, target_type: str, target_id: str) -> list[dict[str, Any]]:
        return self._many("""SELECT * FROM af_review_events WHERE target_type=:type AND target_id=:id
            ORDER BY created_at ASC""", {"type": target_type, "id": target_id})

    def _add_review_event(self, target_type: str, target_id: str, status: str, reason: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO af_review_events (id,target_type,target_id,status,reason,created_at)
                    VALUES (:id,:target_type,:target_id,:status,:reason,:created_at)"""),
                {"id": str(uuid.uuid4()), "target_type": target_type, "target_id": target_id,
                 "status": status, "reason": reason, "created_at": _now()},
            )

    @classmethod
    def _decode_job(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = cls._json(row.pop("payload_json"))
        row["result"] = cls._json(row.pop("result_json"))
        row["error"] = cls._json(row.pop("error_json"))
        return row

    @staticmethod
    def as_job_resource(row: dict[str, Any]) -> JobResource:
        return JobResource(
            id=row["id"], kind=JobKind(row["kind"]), aggregate_id=row["aggregate_id"],
            input_version=row["input_version"], status=JobStatus(row["status"]),
            progress_current=row["progress_current"], progress_total=row["progress_total"],
            attempt=row["attempt"], max_attempts=row["max_attempts"], payload=row["payload"],
            result=row.get("result"), error=row.get("error"),
        )
