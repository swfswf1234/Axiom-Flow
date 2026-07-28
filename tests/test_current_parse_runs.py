"""
模块职责：验证当前 ParseRun 的显式选择、历史记录和非法候选保护。
设计关联（DesignRef）：docs/adr/0011-current-parse-run-and-prunable-artifacts.md
实现状态：Current
被测代码：backend/infrastructure/mysql.py、backend/infrastructure/artifacts.py
"""

from pathlib import Path

import pytest
from sqlalchemy import text

from backend.infrastructure.artifacts import ParseArtifactWriter
from backend.infrastructure.mysql import MySQLRepository


def _document(store: MySQLRepository, root: Path, name: str) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    source = root / f"{name}.pdf"
    source.write_bytes(b"pdf")
    return store.create_document(name, (name.encode().hex() + "0" * 64)[:64], source, 1)


def _parsed_run(store: MySQLRepository, root: Path, document: dict, marker: str) -> dict:
    run = store.create_parse_run(document["id"], {"contract_version": marker})
    writer = ParseArtifactWriter(root, document["content_hash"], run["id"])
    writer.write_page_payloads(1, marker, {"page_no": 1}, {"markdown": marker})
    artifacts = writer.finalize([{"page_no": 1, "markdown": marker}], {"contract_version": marker})
    for artifact in artifacts:
        store.register_artifact_file(document["id"], run["id"], artifact, root)
    store.finish_parse_run(run["id"], "parsed", 1)
    return run


def test_current_run_is_explicit_and_selection_history_is_append_only(
    tmp_path: Path, mysql_settings, mysql_store,
):
    root = tmp_path / "data"
    store = MySQLRepository(mysql_settings.mysql_url)
    try:
        document = _document(store, root, "one")
        first = _parsed_run(store, root, document, "v1")
        selected = store.select_current_parse_run(document["id"], first["id"], "初始基线", root)
        assert selected["is_current"] is True

        second = _parsed_run(store, root, document, "v2")
        assert store.get_current_parse_run(document["id"])["id"] == first["id"]
        assert store.get_parse_run_summary(second["id"])["is_current"] is False

        store.select_current_parse_run(document["id"], second["id"], "人工提升", root)
        history = store.list_parse_run_selections(document["id"])
        assert [item["selected_run_id"] for item in history] == [first["id"], second["id"]]
        assert history[-1]["previous_run_id"] == first["id"]
    finally:
        store.dispose()


def test_current_run_rejects_cross_document_failed_and_pruned_candidates(
    tmp_path: Path, mysql_settings, mysql_store,
):
    root = tmp_path / "data"
    store = MySQLRepository(mysql_settings.mysql_url)
    try:
        first_document = _document(store, root, "first")
        second_document = _document(store, root, "second")
        cross = _parsed_run(store, root, second_document, "cross")
        with pytest.raises(ValueError, match="不属于"):
            store.select_current_parse_run(first_document["id"], cross["id"], "错误选择", root)

        failed = store.create_parse_run(first_document["id"], {"contract_version": "failed"})
        store.finish_parse_run(failed["id"], "failed", 1)
        with pytest.raises(ValueError, match="成功"):
            store.select_current_parse_run(first_document["id"], failed["id"], "错误选择", root)

        pruned = _parsed_run(store, root, first_document, "pruned")
        with store.engine.begin() as connection:
            connection.execute(
                text("UPDATE af_parse_runs SET artifact_state='pruned' WHERE id=:id"), {"id": pruned["id"]},
            )
        with pytest.raises(ValueError, match="成功"):
            store.select_current_parse_run(first_document["id"], pruned["id"], "错误选择", root)
    finally:
        store.dispose()
