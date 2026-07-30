"""
模块职责：实现 v0.3 MySQL 任务租约、版本化抽取和审阅事件仓储。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
关联测试：tests/integration/test_jobs.py、tests/integration/test_api.py
"""

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from axiom_flow.domain.models import (
    DocumentStatus,
    ExtractionRunStatus,
    JobKind,
    JobResource,
    JobStatus,
    ParseRunStatus,
    ReviewStatus,
)
from axiom_flow.infrastructure.artifacts import ArtifactFile, ParseArtifactWriter
from axiom_flow.infrastructure.database import DatabaseConnection, utc_now


class MySQLRepository(DatabaseConnection):
    """实现 v0.3 文档、解析、任务、审阅和发布的唯一 MySQL 适配器。"""

    def recover_interrupted_runs(self) -> None:
        """本地单用户进程重启后，把失去执行者的解析运行收敛为中断。"""
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE af_parse_runs SET status='interrupted' WHERE status='parsing'"))
            connection.execute(text("UPDATE af_documents SET status='failed' WHERE status='parsing'"))

    def truncate_all(self) -> None:
        """仅供隔离测试库使用；调用方负责验证数据库名称。"""
        tables = (
            "af_source_spans", "af_content_blocks", "af_quality_reports", "af_review_events",
            "af_edges", "af_candidates", "af_extraction_runs", "af_releases",
            "af_workbook_revisions", "af_artifacts", "af_pages", "af_parse_run_selections",
            "af_parse_runs", "af_jobs", "af_documents",
        )
        with self.engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in tables:
                connection.execute(text(f"TRUNCATE TABLE {table}"))
            connection.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    def create_document(
        self, filename: str, content_hash: str, source_path: Path, page_count: int,
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()), "filename": filename, "content_hash": content_hash,
            "source_path": str(source_path), "page_count": page_count,
            "status": "imported", "created_at": utc_now(),
        }
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO af_documents
                (id,filename,content_hash,source_path,page_count,status,created_at)
                VALUES (:id,:filename,:content_hash,:source_path,:page_count,:status,:created_at)"""), row)
        return row

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM af_documents WHERE id=:id", {"id": document_id})

    def list_documents(self) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM af_documents ORDER BY created_at DESC")

    def update_document_status(self, document_id: str, status: str) -> None:
        status = DocumentStatus(status).value
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE af_documents SET status=:status WHERE id=:id"),
                {"id": document_id, "status": status},
            )

    def get_page(self, page_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_pages WHERE id=:id", {"id": page_id})
        return self._decode_page(row) if row else None

    def accepted_pages(self, document_id: str) -> list[dict[str, Any]]:
        return [
            page for page in self.list_pages(document_id)
            if page["review_status"] == "accepted" and page["page_kind"] not in {"reference", "visualization"}
        ]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_candidates WHERE id=:id", {"id": candidate_id})
        return self._decode_candidate(row) if row else None

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_edges WHERE id=:id", {"id": edge_id})
        return self._decode_edge(row) if row else None

    def _list_unversioned_candidates(self, document_id: str) -> list[dict[str, Any]]:
        rows = self._many(
            "SELECT * FROM af_candidates WHERE document_id=:id ORDER BY title", {"id": document_id},
        )
        return [self._decode_candidate(row) for row in rows]

    def _list_unversioned_edges(self, document_id: str) -> list[dict[str, Any]]:
        rows = self._many(
            "SELECT * FROM af_edges WHERE document_id=:id ORDER BY relation,id", {"id": document_id},
        )
        return [self._decode_edge(row) for row in rows]

    def accepted_snapshot(self, document_id: str) -> dict[str, Any]:
        nodes = [item for item in self.list_candidates(document_id) if item["review_status"] == "accepted"]
        accepted_ids = {item["id"] for item in nodes}
        edges = [
            item for item in self.list_edges(document_id)
            if item["review_status"] == "accepted"
            and item["source_id"] in accepted_ids and item["target_id"] in accepted_ids
        ]
        return {"nodes": nodes, "edges": edges}

    def create_workbook_revision(
        self, document_id: str, path: Path, snapshot: dict[str, Any], status: str = "draft",
    ) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()), "document_id": document_id, "path": str(path),
            "snapshot_json": json.dumps(snapshot, ensure_ascii=False),
            "status": status, "created_at": utc_now(),
        }
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO af_workbook_revisions
                (id,document_id,path,snapshot_json,status,created_at)
                VALUES (:id,:document_id,:path,CAST(:snapshot_json AS JSON),:status,:created_at)"""), row)
        return {**row, "snapshot": snapshot}

    def latest_workbook_revision(self, document_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM af_workbook_revisions WHERE document_id=:id ORDER BY created_at DESC LIMIT 1",
            {"id": document_id},
        )
        return self._decode_revision(row) if row else None

    def create_release(self, document_id: str, revision_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": str(uuid.uuid4()), "document_id": document_id, "revision_id": revision_id,
            "snapshot_json": json.dumps(snapshot, ensure_ascii=False),
            "status": "published", "created_at": utc_now(),
        }
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO af_releases
                (id,document_id,revision_id,snapshot_json,status,created_at)
                VALUES (:id,:document_id,:revision_id,CAST(:snapshot_json AS JSON),:status,:created_at)"""), row)
        return {**row, "snapshot": snapshot}

    def latest_release(self, document_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM af_releases WHERE document_id=:id AND status='published' "
            "ORDER BY created_at DESC LIMIT 1", {"id": document_id},
        )
        if not row:
            return None
        row["snapshot"] = self._json(row.pop("snapshot_json"))
        return row

    def _review(self, table: str, record_id: str, status: str, reason: str = "") -> None:
        status = ReviewStatus(status).value
        allowed = {"af_pages", "af_candidates", "af_edges"}
        if table not in allowed:
            raise ValueError("不支持的审阅目标")
        reason_clause = ",review_reason=:reason" if table != "af_edges" else ""
        with self.engine.begin() as connection:
            changed = connection.execute(
                text(f"UPDATE {table} SET review_status=:status{reason_clause} WHERE id=:id"),
                {"id": record_id, "status": status, "reason": reason},
            )
            if changed.rowcount != 1:
                raise KeyError("审阅目标不存在")

    def get_document_by_hash(self, content_hash: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM af_documents WHERE content_hash = :hash", {"hash": content_hash})

    def register_artifact(
        self,
        document_id: str,
        run_id: str | None,
        kind: str,
        path: Path,
        *,
        content_hash: str | None = None,
        size_bytes: int | None = None,
        mime_type: str = "application/octet-stream",
        metadata: dict[str, Any] | None = None,
        data_root: Path | None = None,
    ) -> dict[str, Any]:
        """登记文件产物；新产物只保存相对于数据根的路径。"""
        resolved = path.resolve()
        stored_path = str(path)
        if data_root is not None:
            root = data_root.resolve()
            if not resolved.is_relative_to(root):
                raise ValueError("产物路径超出数据目录")
            stored_path = resolved.relative_to(root).as_posix()
        digest = content_hash
        if digest is None:
            import hashlib

            digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
        size = resolved.stat().st_size if size_bytes is None else size_bytes
        encoded_metadata = json.dumps(metadata or {}, ensure_ascii=False)
        with self.engine.begin() as connection:
            existing = connection.execute(
                text("""SELECT id FROM af_artifacts WHERE document_id = :document_id
                    AND kind = :kind AND path = :path AND (run_id = :run_id OR (run_id IS NULL AND :run_id IS NULL))
                    LIMIT 1"""),
                {"document_id": document_id, "kind": kind, "path": stored_path, "run_id": run_id},
            ).scalar_one_or_none()
            if existing:
                return self.get_artifact(str(existing))
            artifact_id = str(uuid.uuid4())
            connection.execute(
                text("""INSERT INTO af_artifacts
                    (id, document_id, run_id, kind, path, content_hash, mime_type, size_bytes,
                     metadata_json, created_at)
                    VALUES (:id, :document_id, :run_id, :kind, :path, :content_hash, :mime_type,
                     :size_bytes, CAST(:metadata_json AS JSON), :created_at)"""),
                {"id": artifact_id, "document_id": document_id, "run_id": run_id, "kind": kind,
                 "path": stored_path, "content_hash": digest, "mime_type": mime_type,
                 "size_bytes": size, "metadata_json": encoded_metadata, "created_at": utc_now()},
            )
        return self.get_artifact(artifact_id)

    def register_artifact_file(
        self, document_id: str, run_id: str | None, artifact: ArtifactFile, data_root: Path,
    ) -> dict[str, Any]:
        return self.register_artifact(
            document_id, run_id, artifact.kind, artifact.path,
            content_hash=artifact.content_hash, size_bytes=artifact.size_bytes,
            mime_type=artifact.mime_type, metadata=artifact.metadata, data_root=data_root,
        )

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_artifacts WHERE id=:id", {"id": artifact_id})
        return self._decode_artifact(row) if row else None

    def list_artifacts_for_run(self, run_id: str) -> list[dict[str, Any]]:
        run = self.get_parse_run(run_id)
        if not run:
            raise KeyError("解析运行不存在")
        if run.get("artifact_state", "available") != "available":
            raise ValueError("解析运行产物已清理")
        rows = self._many(
            "SELECT * FROM af_artifacts WHERE run_id=:id ORDER BY kind,path", {"id": run_id},
        )
        return [self._decode_artifact(row) for row in rows]

    def replace_pages(self, run_id: str, document_id: str, pages: list[dict[str, Any]]) -> None:
        """保存兼容页快照，并将块、来源和质量拆为可查询记录。"""
        for page in pages:
            self.append_page(run_id, document_id, page)

    def append_page(self, run_id: str, document_id: str, page: dict[str, Any]) -> None:
        """原子追加单页规范事实，供长文档检查点使用。"""
        block_statement = text("""INSERT INTO af_content_blocks
            (id,page_id,kind,content,latex,order_no,confidence,source)
            VALUES (:id,:page_id,:kind,:content,:latex,:order_no,:confidence,:source)""")
        span_statement = text("""INSERT INTO af_source_spans
            (id,block_id,page_no,bbox_json,quoted_text)
            VALUES (:id,:block_id,:page_no,CAST(:bbox_json AS JSON),:quoted_text)""")
        quality_statement = text("""INSERT INTO af_quality_reports
            (id,page_id,status,issues_json,metrics_json,created_at)
            VALUES (:id,:page_id,:status,CAST(:issues_json AS JSON),CAST(:metrics_json AS JSON),:created_at)""")
        page_statement = text("""INSERT INTO af_pages
            (id, run_id, document_id, page_no, markdown, blocks_json, evidence_json, quality_json,
             image_path, page_kind, review_status, review_reason)
            VALUES (:id, :run_id, :document_id, :page_no, :markdown, CAST(:blocks_json AS JSON),
             CAST(:evidence_json AS JSON), CAST(:quality_json AS JSON), :image_path, :page_kind,
             :review_status, :review_reason)""")
        with self.engine.begin() as connection:
            connection.execute(page_statement, page)
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
                "created_at": utc_now(),
            })

    def list_pages_for_run(self, run_id: str) -> list[dict[str, Any]]:
        run = self.get_parse_run(run_id)
        if not run:
            raise KeyError("解析运行不存在")
        if run.get("artifact_state", "available") != "available":
            raise ValueError("解析运行产物已清理")
        rows = self._many("SELECT * FROM af_pages WHERE run_id=:id ORDER BY page_no", {"id": run_id})
        return [self._decode_page(row) for row in rows]

    def get_page_for_run(self, run_id: str, page_no: int) -> dict[str, Any] | None:
        run = self.get_parse_run(run_id)
        if not run:
            raise KeyError("解析运行不存在")
        if run.get("artifact_state", "available") != "available":
            raise ValueError("解析运行产物已清理")
        row = self._one(
            "SELECT * FROM af_pages WHERE run_id=:run_id AND page_no=:page_no",
            {"run_id": run_id, "page_no": page_no},
        )
        return self._decode_page(row) if row else None

    def list_page_index(self, run_id: str) -> list[dict[str, Any]]:
        run = self.get_parse_run(run_id)
        if not run:
            raise KeyError("解析运行不存在")
        if run.get("artifact_state", "available") != "available":
            raise ValueError("解析运行产物已清理")
        rows = self._many(
            "SELECT page_no,page_kind,review_status,quality_json FROM af_pages WHERE run_id=:id ORDER BY page_no",
            {"id": run_id},
        )
        result = []
        for row in rows:
            quality = self._json(row.pop("quality_json")) or {}
            result.append({
                **row, "quality_status": quality.get("status", "needs_review"),
                "issue_count": len(quality.get("issues") or []),
            })
        return result

    def list_pages(self, document_id: str) -> list[dict[str, Any]]:
        document = self.get_document(document_id)
        if not document:
            raise KeyError("文档不存在")
        run_id = document.get("current_parse_run_id")
        return self.list_pages_for_run(str(run_id)) if run_id else []

    def get_parse_run_for_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_parse_runs WHERE job_id=:id", {"id": job_id})
        return self._decode_parse_run(row) if row else None

    def checkpoint_parse_run(self, run_id: str, model_calls: int) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE af_parse_runs SET model_calls=:calls WHERE id=:id"),
                {"id": run_id, "calls": model_calls},
            )

    def enqueue_job(
        self,
        kind: JobKind,
        aggregate_id: str,
        input_version: str,
        payload: dict[str, Any] | None = None,
        max_attempts: int = 3,
    ) -> tuple[dict[str, Any], bool]:
        """锁定聚合根后创建任务，避免并发请求产生两个活动任务。"""
        now = utc_now()
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
        now = utc_now()
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
            "id": job_id, "worker": worker_id, "expires": utc_now() + timedelta(seconds=lease_seconds),
            "now": utc_now(), "current": progress_current, "total": progress_total,
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
        now = utc_now()
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
        now = utc_now()
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
        now = utc_now()
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
        if job_id:
            existing = self.get_parse_run_for_job(job_id)
            if existing:
                with self.engine.begin() as connection:
                    connection.execute(
                        text("""UPDATE af_parse_runs SET status='parsing', finished_at=NULL, error_json=NULL
                            WHERE id=:id"""),
                        {"id": existing["id"]},
                    )
                existing["status"] = "parsing"
                existing["finished_at"] = None
                existing["error"] = None
                return existing
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "job_id": job_id, "status": "parsing",
               "provider_summary": json.dumps(provider_summary, ensure_ascii=False), "model_calls": 0,
               "created_at": utc_now()}
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
                {"id": run_id, "status": status, "calls": model_calls, "finished": utc_now(),
                 "error": json.dumps(error, ensure_ascii=False) if error else None},
            )

    def list_parse_runs(self, document_id: str) -> list[dict[str, Any]]:
        rows = self._many(
            "SELECT * FROM af_parse_runs WHERE document_id=:id ORDER BY created_at DESC", {"id": document_id}
        )
        return [self.get_parse_run_summary(str(row["id"])) for row in rows]

    def get_parse_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_parse_runs WHERE id=:id", {"id": run_id})
        return self._decode_parse_run(row) if row else None

    def get_current_parse_run(self, document_id: str) -> dict[str, Any] | None:
        document = self.get_document(document_id)
        if not document:
            raise KeyError("文档不存在")
        run_id = document.get("current_parse_run_id")
        return self.get_parse_run_summary(str(run_id)) if run_id else None

    def select_current_parse_run(
        self, document_id: str, run_id: str, reason: str, data_root: Path,
    ) -> dict[str, Any]:
        """校验不可变清单后显式切换当前运行，并追加选择历史。"""
        reason = reason.strip()
        if not reason:
            raise ValueError("切换当前解析运行必须填写原因")
        document = self.get_document(document_id)
        run = self.get_parse_run(run_id)
        if not document or not run or run["document_id"] != document_id:
            raise ValueError("解析运行不属于该文档")
        if run["status"] != "parsed" or run.get("artifact_state", "available") != "available":
            raise ValueError("只能选择产物可用的成功解析运行")
        ParseArtifactWriter(data_root, document["content_hash"], run_id).verify_manifest()
        if document.get("current_parse_run_id") == run_id:
            return self.get_parse_run_summary(run_id)
        now = utc_now()
        with self.engine.begin() as connection:
            current = connection.execute(
                text("SELECT current_parse_run_id FROM af_documents WHERE id=:id FOR UPDATE"),
                {"id": document_id},
            ).scalar_one()
            connection.execute(
                text("UPDATE af_documents SET current_parse_run_id=:run_id WHERE id=:id"),
                {"run_id": run_id, "id": document_id},
            )
            connection.execute(
                text("""INSERT INTO af_parse_run_selections
                    (id,document_id,previous_run_id,selected_run_id,reason,created_at)
                    VALUES (:id,:document_id,:previous_run_id,:selected_run_id,:reason,:created_at)"""),
                {"id": str(uuid.uuid4()), "document_id": document_id,
                 "previous_run_id": current, "selected_run_id": run_id,
                 "reason": reason, "created_at": now},
            )
        return self.get_parse_run_summary(run_id)

    def materialize_shared_page_assets(self, document_id: str, run_id: str, data_root: Path) -> list[dict[str, Any]]:
        """把既有当前运行页图硬链接到文档共享目录，并更新页面定位。"""
        document = self.get_document(document_id)
        if not document or document.get("current_parse_run_id") != run_id:
            raise ValueError("只能为文档当前解析运行建立共享页图")
        writer = ParseArtifactWriter(data_root, document["content_hash"], run_id)
        pages = self.list_pages_for_run(run_id)
        result = []
        for page in pages:
            source = Path(page["image_path"])
            source = source if source.is_absolute() else data_root / source
            artifact = writer.link_shared_page_image(int(page["page_no"]), source)
            registered = self.register_artifact_file(document_id, None, artifact, data_root)
            with self.engine.begin() as connection:
                connection.execute(
                    text("UPDATE af_pages SET image_path=:path WHERE id=:id"),
                    {"path": writer.relative_path(artifact.path), "id": page["id"]},
                )
            result.append(registered)
        return result

    def list_parse_run_selections(self, document_id: str) -> list[dict[str, Any]]:
        return self._many(
            "SELECT * FROM af_parse_run_selections WHERE document_id=:id ORDER BY created_at ASC,id ASC",
            {"id": document_id},
        )

    def get_parse_run_summary(self, run_id: str) -> dict[str, Any]:
        run = self.get_parse_run(run_id)
        if not run:
            raise KeyError("解析运行不存在")
        stats = self._one("""SELECT COUNT(*) AS page_count, MIN(page_no) AS page_start,
            MAX(page_no) AS page_end,
            SUM(review_status='accepted') AS accepted_count,
            SUM(review_status='rejected') AS rejected_count,
            SUM(review_status='reparse_requested') AS reparse_requested_count
            FROM af_pages WHERE run_id=:id""", {"id": run_id}) or {}
        artifacts = self._one("""SELECT COUNT(*) AS artifact_count,
            COALESCE(SUM(size_bytes),0) AS size_bytes,
            MAX(CASE WHEN kind='parse_manifest' THEN content_hash END) AS manifest_hash
            FROM af_artifacts WHERE run_id=:id""", {"id": run_id}) or {}
        document = self.get_document(run["document_id"])
        tombstone = run.get("prune_summary") or {}
        is_pruned = run.get("artifact_state") == "pruned"
        run.update({
            "is_current": bool(document and document.get("current_parse_run_id") == run_id),
            "page_count": int((tombstone.get("page_count") if is_pruned else stats.get("page_count")) or 0),
            "page_start": tombstone.get("page_start") if is_pruned else stats.get("page_start"),
            "page_end": tombstone.get("page_end") if is_pruned else stats.get("page_end"),
            "review_counts": tombstone.get("review_counts") if is_pruned else {
                "accepted": int(stats.get("accepted_count") or 0),
                "rejected": int(stats.get("rejected_count") or 0),
                "reparse_requested": int(stats.get("reparse_requested_count") or 0),
            },
            "artifact_count": int((tombstone.get("artifact_count") if is_pruned else artifacts.get("artifact_count")) or 0),
            "size_bytes": int((tombstone.get("size_bytes") if is_pruned else artifacts.get("size_bytes")) or 0),
            "manifest_hash": tombstone.get("manifest_hash") if is_pruned else artifacts.get("manifest_hash"),
        })
        return run

    def get_artifact_summary(self, run_id: str) -> dict[str, Any]:
        run = self.get_parse_run_summary(run_id)
        grouped = self._many("""SELECT kind,COUNT(*) AS count,COALESCE(SUM(size_bytes),0) AS size_bytes
            FROM af_artifacts WHERE run_id=:id GROUP BY kind ORDER BY kind""", {"id": run_id})
        return {"run": run, "groups": grouped}

    def create_extraction_run(self, document_id: str, parse_run_id: str, job_id: str | None, summary: dict[str, Any]) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "parse_run_id": parse_run_id,
               "job_id": job_id, "status": "extracting", "provider_summary": json.dumps(summary, ensure_ascii=False),
               "created_at": utc_now()}
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
                {"id": run_id, "status": status, "calls": calls, "now": utc_now(),
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
            return self._list_unversioned_candidates(document_id)
        rows = self._many("SELECT * FROM af_candidates WHERE extraction_run_id=:id ORDER BY title", {"id": extraction_id})
        return [self._decode_candidate(row) for row in rows]

    def list_edges(self, document_id: str) -> list[dict[str, Any]]:
        extraction_id = self._latest_extraction_id(document_id)
        if not extraction_id:
            return self._list_unversioned_edges(document_id)
        rows = self._many("SELECT * FROM af_edges WHERE extraction_run_id=:id ORDER BY relation,id", {"id": extraction_id})
        return [self._decode_edge(row) for row in rows]

    def review_page(self, page_id: str, status: str, reason: str) -> None:
        self._review("af_pages", page_id, status, reason)
        self._add_review_event("page", page_id, status, reason)

    def review_candidate(self, candidate_id: str, status: str, reason: str) -> None:
        self._review("af_candidates", candidate_id, status, reason)
        self._add_review_event("knowledge_node", candidate_id, status, reason)

    def review_edge(self, edge_id: str, status: str, reason: str = "") -> None:
        status = ReviewStatus(status).value
        self._review("af_edges", edge_id, status)
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
                 "status": status, "reason": reason, "created_at": utc_now()},
            )

    @classmethod
    def _decode_page(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["blocks"] = cls._json(row.pop("blocks_json"))
        row["evidence"] = cls._json(row.pop("evidence_json"))
        row["quality"] = cls._json(row.pop("quality_json"))
        return row

    @classmethod
    def _decode_candidate(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["evidence"] = cls._json(row.pop("evidence_json"))
        return row

    @classmethod
    def _decode_edge(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["evidence"] = cls._json(row.pop("evidence_json"))
        return row

    @classmethod
    def _decode_revision(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["snapshot"] = cls._json(row.pop("snapshot_json"))
        return row

    @classmethod
    def _decode_job(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = cls._json(row.pop("payload_json"))
        row["result"] = cls._json(row.pop("result_json"))
        row["error"] = cls._json(row.pop("error_json"))
        return row

    @classmethod
    def _decode_parse_run(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["provider_summary"] = cls._json(row["provider_summary"])
        row["error"] = cls._json(row.pop("error_json", None))
        row["prune_summary"] = cls._json(row.pop("prune_summary_json", None))
        return row

    @classmethod
    def _decode_artifact(cls, row: dict[str, Any]) -> dict[str, Any]:
        row["metadata"] = cls._json(row.pop("metadata_json", None)) or {}
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
