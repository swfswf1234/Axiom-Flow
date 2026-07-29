"""
模块职责：验证旧解析运行清理的预演、保护、墓碑、回滚和显式清空。
设计关联（DesignRef）：docs/adr/0011-current-parse-run-and-prunable-artifacts.md
实现状态：Current
被测代码：src/axiom_flow/tools/prune_parse_runs.py
"""

import json
import uuid
from pathlib import Path

import pytest

from axiom_flow.infrastructure.artifacts import ParseArtifactWriter
from axiom_flow.infrastructure.mysql import MySQLRepository
from axiom_flow.tools.prune_parse_runs import ParseRunPruner


def _run(store: MySQLRepository, root: Path, document: dict, text: str) -> dict:
    run = store.create_parse_run(document["id"], {"contract_version": text})
    writer = ParseArtifactWriter(root, document["content_hash"], run["id"])
    image = writer.write_page_image(1, b"same-page-image")
    payloads = writer.write_page_payloads(1, text, {"page_no": 1}, {"markdown": text})
    finals = writer.finalize([{"page_no": 1, "markdown": text}], {"contract_version": text}, [image])
    store.register_artifact_file(document["id"], None, image, root)
    for artifact in [*payloads, *finals]:
        store.register_artifact_file(document["id"], run["id"], artifact, root)
    store.append_page(run["id"], document["id"], {
        "id": str(uuid.uuid4()), "run_id": run["id"], "document_id": document["id"], "page_no": 1,
        "markdown": text, "blocks_json": json.dumps([{"kind": "paragraph", "content": text}]),
        "evidence_json": "[]", "quality_json": json.dumps({"status": "needs_review", "issues": []}),
        "image_path": writer.relative_path(image.path), "page_kind": "content",
        "review_status": "needs_review", "review_reason": "等待人工确认",
    })
    store.finish_parse_run(run["id"], "parsed", 1)
    return run


def test_pruner_stages_rolls_back_and_purges_only_after_explicit_apply(
    tmp_path: Path, mysql_settings, mysql_store,
):
    root = tmp_path / "data"
    root.mkdir()
    source = root / "source.pdf"
    source.write_bytes(b"pdf")
    document_hash = "a" * 64
    store = MySQLRepository(mysql_settings.mysql_url)
    try:
        document = store.create_document("book.pdf", document_hash, source, 1)
        keep = _run(store, root, document, "keep")
        target = _run(store, root, document, "old")
        store.select_current_parse_run(document["id"], keep["id"], "保留运行", root)
        pruner = ParseRunPruner(store, root)

        dry_run = pruner.stage(document_hash, keep["id"], [target["id"]])
        assert dry_run["mode"] == "dry-run"
        assert store.get_parse_run(target["id"])["artifact_state"] == "available"
        with pytest.raises(ValueError, match="当前或保留"):
            pruner.plan(document_hash, keep["id"], [keep["id"]])
        with pytest.raises(ValueError, match="超出"):
            pruner._safe_path(root, root)

        staged = pruner.stage(document_hash, keep["id"], [target["id"]], apply=True)
        operation_id = staged["operation_id"]
        assert store.get_parse_run(target["id"])["artifact_state"] == "pruned"
        assert store.get_parse_run(target["id"])["prune_summary"]["page_count"] == 1
        summary = store.get_parse_run_summary(target["id"])
        assert summary["page_count"] == 1
        assert summary["artifact_count"] == 5
        assert summary["manifest_hash"]

        pruner.rollback(operation_id, document_hash, keep["id"], apply=True)
        assert store.get_parse_run(target["id"])["artifact_state"] == "available"
        assert len(store.list_pages_for_run(target["id"])) == 1

        staged_again = pruner.stage(document_hash, keep["id"], [target["id"]], apply=True)
        purge_preview = pruner.purge(staged_again["operation_id"], document_hash, keep["id"])
        assert purge_preview["mode"] == "dry-run"
        operation_dir = root / "trash" / staged_again["operation_id"]
        assert operation_dir.is_dir()
        pruner.purge(staged_again["operation_id"], document_hash, keep["id"], apply=True)
        assert not operation_dir.exists()
    finally:
        store.dispose()


def test_pruner_rejects_reviewed_runs(tmp_path: Path, mysql_settings, mysql_store):
    root = tmp_path / "data"
    root.mkdir()
    source = root / "source.pdf"
    source.write_bytes(b"pdf")
    store = MySQLRepository(mysql_settings.mysql_url)
    try:
        document = store.create_document("reviewed.pdf", "b" * 64, source, 1)
        keep = _run(store, root, document, "keep")
        reviewed = _run(store, root, document, "reviewed")
        store.select_current_parse_run(document["id"], keep["id"], "保留运行", root)
        page = store.list_pages_for_run(reviewed["id"])[0]
        store.review_page(page["id"], "accepted", "已人工确认")
        with pytest.raises(ValueError, match="保护"):
            ParseRunPruner(store, root).plan(document["content_hash"], keep["id"], [reviewed["id"]])
    finally:
        store.dispose()
