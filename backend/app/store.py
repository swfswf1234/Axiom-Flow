"""
模块职责：管理 v0.2 MySQL 事实、审阅状态和发布快照。
设计关联（DesignRef）：docs/architecture/data-lifecycle.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py、tests/test_mysql_migrations.py
"""

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from backend.domain.models import DocumentStatus, ParseRunStatus, ReviewStatus

ROOT = Path(__file__).resolve().parents[2]


def _now() -> datetime:
    """MySQL DATETIME 不保存时区，统一写入 UTC 的无时区时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def upgrade_database(database_url: str) -> None:
    """由部署命令或测试显式调用，禁止应用启动时隐式修改表结构。"""
    command.upgrade(_alembic_config(database_url), "head")


class MySQLStore:
    """单用户 MySQL 仓储；所有运行表均由 Alembic 创建并使用 af_ 前缀。"""

    def __init__(self, database_url: str, pool_size: int = 5, max_overflow: int = 10):
        self.database_url = database_url
        self.engine: Engine = create_engine(
            database_url,
            pool_pre_ping=True,
            pool_size=pool_size,
            max_overflow=max_overflow,
            future=True,
        )

    def require_schema(self) -> None:
        """确认库版本已达到当前迁移头，避免服务在半初始化库上启动。"""
        config = _alembic_config(self.database_url)
        expected = ScriptDirectory.from_config(config).get_current_head()
        try:
            with self.engine.connect() as connection:
                actual = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise RuntimeError("MySQL schema 未初始化，请先执行 alembic upgrade head") from exc
        if actual != expected:
            raise RuntimeError("MySQL schema 版本不是当前版本，请执行 alembic upgrade head")

    def recover_interrupted_runs(self) -> None:
        """本地单用户进程重启后不存在有效运行中的任务，统一收敛为失败。"""
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE af_parse_runs SET status = 'interrupted' WHERE status = 'parsing'"))
            connection.execute(text("UPDATE af_documents SET status = 'failed' WHERE status = 'parsing'"))

    def dispose(self) -> None:
        self.engine.dispose()

    def truncate_all(self) -> None:
        """仅供隔离测试库使用；调用方必须保证连接的是 axiom_flow_test。"""
        tables = (
            "af_source_spans", "af_content_blocks", "af_quality_reports", "af_review_events",
            "af_edges", "af_candidates", "af_extraction_runs", "af_releases",
            "af_workbook_revisions", "af_artifacts", "af_pages", "af_parse_runs", "af_jobs", "af_documents",
        )
        with self.engine.begin() as connection:
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            for table in tables:
                connection.execute(text(f"TRUNCATE TABLE {table}"))
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    def create_document(self, filename: str, content_hash: str, source_path: Path, page_count: int) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "filename": filename, "content_hash": content_hash, "source_path": str(source_path), "page_count": page_count, "status": "imported", "created_at": _now()}
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO af_documents
                (id, filename, content_hash, source_path, page_count, status, created_at)
                VALUES (:id, :filename, :content_hash, :source_path, :page_count, :status, :created_at)"""), row)
        return row

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM af_documents WHERE id = :id", {"id": document_id})

    def list_documents(self) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM af_documents ORDER BY created_at DESC")

    def update_document_status(self, document_id: str, status: str) -> None:
        status = DocumentStatus(status).value
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE af_documents SET status = :status WHERE id = :id"), {"id": document_id, "status": status})

    def create_parse_run(
        self, document_id: str, provider_summary: dict[str, Any], job_id: str | None = None,
    ) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "job_id": job_id,
               "status": "parsing", "provider_summary": json.dumps(provider_summary, ensure_ascii=False),
               "model_calls": 0, "created_at": _now()}
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO af_parse_runs
                (id, document_id, job_id, status, provider_summary, model_calls, created_at)
                VALUES (:id, :document_id, :job_id, :status, CAST(:provider_summary AS JSON),
                :model_calls, :created_at)"""), row)
        return row

    def finish_parse_run(
        self, run_id: str, status: str, model_calls: int, error: dict[str, Any] | None = None,
    ) -> None:
        status = ParseRunStatus(status).value
        with self.engine.begin() as connection:
            connection.execute(text("""UPDATE af_parse_runs SET status=:status, model_calls=:model_calls,
                finished_at=:finished_at, error_json=CAST(:error_json AS JSON) WHERE id=:id"""),
                {"id": run_id, "status": status, "model_calls": model_calls, "finished_at": _now(),
                 "error_json": json.dumps(error, ensure_ascii=False) if error else None})

    def replace_pages(self, run_id: str, document_id: str, pages: list[dict[str, Any]]) -> None:
        """写入一次解析运行的页面事实，历史 ParseRun 的产物不得被新运行覆盖。"""
        statement = text("""INSERT INTO af_pages
            (id, run_id, document_id, page_no, markdown, blocks_json, evidence_json, quality_json,
             image_path, page_kind, review_status, review_reason)
            VALUES (:id, :run_id, :document_id, :page_no, :markdown, CAST(:blocks_json AS JSON),
             CAST(:evidence_json AS JSON), CAST(:quality_json AS JSON), :image_path, :page_kind,
             :review_status, :review_reason)""")
        with self.engine.begin() as connection:
            connection.execute(statement, pages)

    def list_pages(self, document_id: str) -> list[dict[str, Any]]:
        rows = self._many("""SELECT p.* FROM af_pages p JOIN af_parse_runs r ON p.run_id = r.id
            WHERE p.document_id = :document_id AND r.status = 'parsed'
            ORDER BY r.created_at DESC, p.page_no ASC""", {"document_id": document_id})
        if rows:
            rows = [row for row in rows if row["run_id"] == rows[0]["run_id"]]
        return [self._decode_page(row) for row in rows]

    def get_page(self, page_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_pages WHERE id = :id", {"id": page_id})
        return self._decode_page(row) if row else None

    def review_page(self, page_id: str, status: str, reason: str) -> None:
        self._review("af_pages", page_id, status, reason)

    def accepted_pages(self, document_id: str) -> list[dict[str, Any]]:
        return [page for page in self.list_pages(document_id) if page["review_status"] == "accepted" and page["page_kind"] not in {"reference", "visualization"}]

    def replace_candidates(self, document_id: str, candidates: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        candidate_statement = text("""INSERT INTO af_candidates
            (id, document_id, kind, title, content, evidence_json, review_status, review_reason)
            VALUES (:id, :document_id, :kind, :title, :content, CAST(:evidence_json AS JSON), :review_status, :review_reason)""")
        edge_statement = text("""INSERT INTO af_edges
            (id, document_id, source_id, target_id, relation, evidence_json, review_status)
            VALUES (:id, :document_id, :source_id, :target_id, :relation, CAST(:evidence_json AS JSON), :review_status)""")
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM af_edges WHERE document_id = :document_id"), {"document_id": document_id})
            connection.execute(text("DELETE FROM af_candidates WHERE document_id = :document_id"), {"document_id": document_id})
            if candidates:
                connection.execute(candidate_statement, candidates)
            if edges:
                connection.execute(edge_statement, edges)

    def list_candidates(self, document_id: str) -> list[dict[str, Any]]:
        return [self._decode_candidate(row) for row in self._many("SELECT * FROM af_candidates WHERE document_id = :document_id ORDER BY title", {"document_id": document_id})]

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_candidates WHERE id = :id", {"id": candidate_id})
        return self._decode_candidate(row) if row else None

    def review_candidate(self, candidate_id: str, status: str, reason: str) -> None:
        self._review("af_candidates", candidate_id, status, reason)

    def list_edges(self, document_id: str) -> list[dict[str, Any]]:
        return [self._decode_edge(row) for row in self._many("SELECT * FROM af_edges WHERE document_id = :document_id ORDER BY relation, id", {"document_id": document_id})]

    def get_edge(self, edge_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_edges WHERE id = :id", {"id": edge_id})
        return self._decode_edge(row) if row else None

    def review_edge(self, edge_id: str, status: str) -> None:
        with self.engine.begin() as connection:
            result = connection.execute(text("UPDATE af_edges SET review_status = :status WHERE id = :id"), {"id": edge_id, "status": status})
        if result.rowcount != 1:
            raise KeyError("知识关系不存在")

    def accepted_snapshot(self, document_id: str) -> dict[str, Any]:
        candidates = [candidate for candidate in self.list_candidates(document_id) if candidate["review_status"] == "accepted"]
        ids = {candidate["id"] for candidate in candidates}
        edges = [edge for edge in self.list_edges(document_id) if edge["source_id"] in ids and edge["target_id"] in ids and edge["review_status"] == "accepted"]
        return {"nodes": candidates, "edges": edges}

    def create_workbook_revision(self, document_id: str, path: Path, snapshot: dict[str, Any], status: str = "draft") -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "path": str(path), "snapshot_json": json.dumps(snapshot, ensure_ascii=False), "status": status, "created_at": _now()}
        with self.engine.begin() as connection:
            connection.execute(text("""INSERT INTO af_workbook_revisions
                (id, document_id, path, snapshot_json, status, created_at)
                VALUES (:id, :document_id, :path, CAST(:snapshot_json AS JSON), :status, :created_at)"""), row)
        return row

    def latest_workbook_revision(self, document_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_workbook_revisions WHERE document_id = :document_id ORDER BY created_at DESC LIMIT 1", {"document_id": document_id})
        return self._decode_revision(row) if row else None

    def create_release(self, document_id: str, revision_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        row = {"id": str(uuid.uuid4()), "document_id": document_id, "revision_id": revision_id, "snapshot_json": json.dumps(snapshot, ensure_ascii=False), "status": "published", "created_at": _now()}
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE af_releases SET status = 'superseded' WHERE document_id = :document_id AND status = 'published'"), {"document_id": document_id})
            connection.execute(text("""INSERT INTO af_releases
                (id, document_id, revision_id, snapshot_json, status, created_at)
                VALUES (:id, :document_id, :revision_id, CAST(:snapshot_json AS JSON), :status, :created_at)"""), row)
        return row

    def latest_release(self, document_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM af_releases WHERE document_id = :document_id AND status = 'published' ORDER BY created_at DESC LIMIT 1", {"document_id": document_id})
        if not row:
            return None
        row["snapshot"] = self._json(row.pop("snapshot_json"))
        return row

    def _review(self, table: str, record_id: str, status: str, reason: str) -> None:
        status = ReviewStatus(status).value
        messages = {"af_pages": "页面不存在", "af_candidates": "知识候选不存在"}
        with self.engine.begin() as connection:
            result = connection.execute(text(f"UPDATE {table} SET review_status = :status, review_reason = :reason WHERE id = :id"), {"id": record_id, "status": status, "reason": reason})
        if result.rowcount != 1:
            raise KeyError(messages[table])

    def _one(self, statement: str, values: dict[str, Any]) -> dict[str, Any] | None:
        with self.engine.connect() as connection:
            row = connection.execute(text(statement), values).mappings().first()
        return dict(row) if row else None

    def _many(self, statement: str, values: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = connection.execute(text(statement), values or {}).mappings().all()
        return [dict(row) for row in rows]

    @staticmethod
    def _json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

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
