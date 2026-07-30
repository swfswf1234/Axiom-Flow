"""
模块职责：以 dry-run、可恢复暂存和显式清空三阶段清理旧解析运行产物。
设计关联（DesignRef）：docs/adr/0011-current-parse-run-and-prunable-artifacts.md
实现状态：Current
关联测试：tests/integration/test_prune_parse_runs.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text

from axiom_flow.infrastructure.artifacts import ParseArtifactWriter
from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.database import utc_now
from axiom_flow.infrastructure.mysql import MySQLRepository


class ParseRunPruner:
    """保护当前结果、引用链和数据根边界的解析运行清理器。"""

    def __init__(self, store: MySQLRepository, data_root: Path) -> None:
        self.store = store
        self.data_root = data_root.resolve()
        self.trash_root = self.data_root / "trash"

    def plan(self, document_hash: str, keep_run_id: str, target_run_ids: list[str]) -> dict[str, Any]:
        document = self.store.get_document_by_hash(document_hash)
        if not document or document.get("content_hash") != document_hash:
            raise ValueError("文档 SHA-256 不匹配")
        if document.get("current_parse_run_id") != keep_run_id:
            raise ValueError("keep-run-id 必须是文档当前解析运行")
        keep = self.store.get_parse_run(keep_run_id)
        if not keep or keep["document_id"] != document["id"] or keep["status"] != "parsed":
            raise ValueError("保留运行不存在、未成功或不属于该文档")
        if keep.get("artifact_state", "available") != "available":
            raise ValueError("保留运行产物不可用")
        ParseArtifactWriter(self.data_root, document_hash, keep_run_id).verify_manifest()

        unique_targets = list(dict.fromkeys(target_run_ids))
        if not unique_targets:
            raise ValueError("至少指定一个待清理运行")
        targets = [self._inspect_target(document, keep_run_id, run_id) for run_id in unique_targets]
        return {
            "document_id": document["id"], "document_hash": document_hash,
            "keep_run_id": keep_run_id, "target_run_ids": unique_targets,
            "reclaimable_bytes": sum(int(item["summary"]["size_bytes"]) for item in targets),
            "targets": targets,
        }

    def stage(
        self, document_hash: str, keep_run_id: str, target_run_ids: list[str], *, apply: bool = False,
    ) -> dict[str, Any]:
        plan = self.plan(document_hash, keep_run_id, target_run_ids)
        if not apply:
            return {"mode": "dry-run", **plan}
        operation_id = str(uuid.uuid4())
        operation_dir = self._safe_path(self.trash_root / operation_id, self.trash_root)
        operation_dir.mkdir(parents=True, exist_ok=False)
        backup = {"operation_id": operation_id, "status": "preparing", "created_at": utc_now(), **plan}
        for target in backup["targets"]:
            target["pages_backup"] = self.store.list_pages_for_run(target["run_id"])
            target["artifacts_backup"] = self.store.list_artifacts_for_run(target["run_id"])
        operation_path = operation_dir / "operation.json"
        self._write_operation(operation_path, backup)

        moved: list[tuple[Path, Path]] = []
        try:
            for target in backup["targets"]:
                source = Path(target["source_dir"])
                if source.is_dir():
                    destination = self._safe_path(operation_dir / "parse-runs" / target["run_id"], operation_dir)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(destination))
                    if source.exists() or not destination.is_dir():
                        raise RuntimeError(f"运行目录未完整移入 trash：{target['run_id']}")
                    target["trash_dir"] = str(destination)
                    moved.append((source, destination))
            with self.store.engine.begin() as connection:
                for target in backup["targets"]:
                    run_id = target["run_id"]
                    connection.execute(text("DELETE FROM af_artifacts WHERE run_id=:id"), {"id": run_id})
                    connection.execute(text("DELETE FROM af_pages WHERE run_id=:id"), {"id": run_id})
                    connection.execute(
                        text("""UPDATE af_parse_runs SET artifact_state='pruned',
                            prune_summary_json=CAST(:summary AS JSON),pruned_at=:now WHERE id=:id"""),
                        {"id": run_id, "summary": json.dumps(target["summary"], ensure_ascii=False, default=str),
                         "now": utc_now()},
                    )
            ParseArtifactWriter(self.data_root, document_hash, keep_run_id).verify_manifest()
            backup["status"] = "staged"
            backup["staged_at"] = utc_now()
            self._write_operation(operation_path, backup)
            return self._public_operation(backup)
        except BaseException:
            self._restore_database(backup)
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
            raise

    def rollback(self, operation_id: str, document_hash: str, keep_run_id: str, *, apply: bool = False) -> dict[str, Any]:
        operation_path, operation = self._load_operation(operation_id)
        self._validate_operation(operation, document_hash, keep_run_id)
        if operation["status"] != "staged":
            raise ValueError("只有 staged 清理操作可以回滚")
        if not apply:
            return {"mode": "dry-run", "action": "rollback", **self._public_operation(operation)}
        for target in operation["targets"]:
            trash_value = target.get("trash_dir")
            if not trash_value:
                continue
            trash = self._safe_path(Path(trash_value), operation_path.parent)
            source = self._safe_path(Path(target["source_dir"]), self.data_root)
            if trash.is_dir():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(trash), str(source))
        self._restore_database(operation)
        operation["status"] = "rolled_back"
        operation["rolled_back_at"] = utc_now()
        self._write_operation(operation_path, operation)
        return self._public_operation(operation)

    def purge(self, operation_id: str, document_hash: str, keep_run_id: str, *, apply: bool = False) -> dict[str, Any]:
        operation_path, operation = self._load_operation(operation_id)
        self._validate_operation(operation, document_hash, keep_run_id)
        if operation["status"] != "staged":
            raise ValueError("只有 staged 清理操作可以 purge")
        ParseArtifactWriter(self.data_root, document_hash, keep_run_id).verify_manifest()
        result = {"operation_id": operation_id, "status": "purged", "target_run_ids": operation["target_run_ids"]}
        if not apply:
            return {"mode": "dry-run", "action": "purge", **result}
        operation_dir = self._safe_path(operation_path.parent, self.trash_root)
        shutil.rmtree(operation_dir)
        return result

    def _inspect_target(self, document: dict[str, Any], keep_run_id: str, run_id: str) -> dict[str, Any]:
        run = self.store.get_parse_run(run_id)
        if not run or run["document_id"] != document["id"]:
            raise ValueError(f"待清理运行不存在或跨文档：{run_id}")
        if run_id == keep_run_id or run_id == document.get("current_parse_run_id"):
            raise ValueError(f"拒绝清理当前或保留运行：{run_id}")
        if run.get("artifact_state", "available") != "available":
            raise ValueError(f"运行已经清理：{run_id}")
        protected = self.store._one("""SELECT
            EXISTS(SELECT 1 FROM af_jobs j WHERE j.id=r.job_id
                AND j.status IN ('queued','running','cancel_requested')) AS active_job,
            EXISTS(SELECT 1 FROM af_pages p WHERE p.run_id=r.id
                AND p.review_status <> 'needs_review') AS reviewed_page,
            EXISTS(SELECT 1 FROM af_review_events e JOIN af_pages p ON p.id=e.target_id
                WHERE e.target_type='page' AND p.run_id=r.id) AS review_event,
            EXISTS(SELECT 1 FROM af_extraction_runs x WHERE x.parse_run_id=r.id) AS extraction_ref,
            EXISTS(SELECT 1 FROM af_releases rel JOIN af_workbook_revisions w ON w.id=rel.revision_id
                WHERE rel.document_id=r.document_id AND
                (CAST(rel.snapshot_json AS CHAR) LIKE :needle OR CAST(w.snapshot_json AS CHAR) LIKE :needle)) AS release_ref
            FROM af_parse_runs r WHERE r.id=:id""", {"id": run_id, "needle": f"%{run_id}%"}) or {}
        reasons = [name for name, value in protected.items() if bool(value)]
        if run["status"] == "parsing":
            reasons.append("active_run")
        if reasons:
            raise ValueError(f"运行受引用或审阅保护：{run_id} ({', '.join(reasons)})")
        source = self._safe_path(
            self.data_root / "documents" / document["content_hash"] / "parse-runs" / run_id,
            self.data_root,
        )
        summary = self.store.get_parse_run_summary(run_id)
        tombstone = {
            key: summary.get(key) for key in (
                "status", "provider_summary", "model_calls", "created_at", "finished_at",
                "page_count", "page_start", "page_end", "review_counts", "artifact_count",
                "size_bytes", "manifest_hash",
            )
        }
        return {"run_id": run_id, "source_dir": str(source), "summary": tombstone}

    def _restore_database(self, operation: dict[str, Any]) -> None:
        for target in operation.get("targets", []):
            run_id = target["run_id"]
            existing = self.store.get_parse_run(run_id)
            if not existing or existing.get("artifact_state", "available") != "pruned":
                continue
            for page in target.get("pages_backup", []):
                self.store.append_page(run_id, page["document_id"], {
                    **page,
                    "blocks_json": json.dumps(page["blocks"], ensure_ascii=False),
                    "evidence_json": json.dumps(page["evidence"], ensure_ascii=False),
                    "quality_json": json.dumps(page["quality"], ensure_ascii=False),
                })
            with self.store.engine.begin() as connection:
                for artifact in target.get("artifacts_backup", []):
                    connection.execute(text("""INSERT INTO af_artifacts
                        (id,document_id,run_id,kind,path,content_hash,mime_type,size_bytes,metadata_json,created_at)
                        VALUES (:id,:document_id,:run_id,:kind,:path,:content_hash,:mime_type,:size_bytes,
                        CAST(:metadata_json AS JSON),:created_at)"""), {
                            **artifact, "metadata_json": json.dumps(artifact.get("metadata") or {}, ensure_ascii=False),
                        })
                connection.execute(text("""UPDATE af_parse_runs SET artifact_state='available',
                    prune_summary_json=NULL,pruned_at=NULL WHERE id=:id"""), {"id": run_id})

    def _load_operation(self, operation_id: str) -> tuple[Path, dict[str, Any]]:
        if not operation_id or Path(operation_id).name != operation_id:
            raise ValueError("清理操作 ID 无效")
        path = self._safe_path(self.trash_root / operation_id / "operation.json", self.trash_root)
        if not path.is_file():
            raise KeyError("清理操作不存在")
        return path, json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _validate_operation(operation: dict[str, Any], document_hash: str, keep_run_id: str) -> None:
        if operation.get("document_hash") != document_hash or operation.get("keep_run_id") != keep_run_id:
            raise ValueError("文档 SHA-256 或 keep-run-id 与清理操作不匹配")

    @staticmethod
    def _safe_path(path: Path, root: Path) -> Path:
        resolved, resolved_root = path.resolve(), root.resolve()
        if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
            raise ValueError("清理路径超出允许的数据目录")
        return resolved

    @staticmethod
    def _write_operation(path: Path, operation: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(operation, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _public_operation(operation: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in operation.items() if key != "targets"} | {
            "targets": [
                {key: value for key, value in target.items() if not key.endswith("_backup")}
                for target in operation["targets"]
            ]
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="受保护地清理旧 ParseRun 产物")
    parser.add_argument("action", choices=["stage", "rollback", "purge"])
    parser.add_argument("--document-sha256", required=True)
    parser.add_argument("--keep-run-id", required=True)
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--operation-id")
    parser.add_argument("--apply", action="store_true", help="默认仅 dry-run；显式指定后才修改数据")
    args = parser.parse_args()
    settings = Settings()
    store = MySQLRepository(settings.mysql_url, settings.mysql_pool_size, settings.mysql_max_overflow)
    store.require_schema()
    try:
        pruner = ParseRunPruner(store, settings.data_dir)
        if args.action == "stage":
            result = pruner.stage(args.document_sha256, args.keep_run_id, args.run_id, apply=args.apply)
        elif args.action == "rollback":
            result = pruner.rollback(
                args.operation_id or "", args.document_sha256, args.keep_run_id, apply=args.apply,
            )
        else:
            result = pruner.purge(
                args.operation_id or "", args.document_sha256, args.keep_run_id, apply=args.apply,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        store.dispose()


if __name__ == "__main__":
    main()
