"""PLAN-019（2026-11-23 返工）：人决定重跑范围 + DB 状态与文件核验双读。

覆盖：
- `POST /parse-jobs` 的 `scope + pages`：显式 pages 即范围；缺省行为向后兼容全本；
  `scope=unfinished` 取「status != parsed ∪ parsed 且 has_artifact=false」。
- `GET /books/{id}/reparse-plan` 只读建议：逐页状态/文件核验/理由，不替人决定。
- 受理时对当前可见版本目标页先写 `failed` 并写明理由，新任务不继承旧产物。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from axiom_flow.db.models import AfPage
from axiom_flow.schemas import ParseJobCreate
from tests.contract.test_api_v1_contract import (
    FakeEngine,
    _build,
    _ingest,
    _sync_book,
)


def test_parse_job_create_accepts_scope_and_keeps_backward_compat():
    """scope 是可选字段；缺省形状不变（全本），显式取值受闭集约束。"""
    job = ParseJobCreate(book_id="b05")
    assert job.scope is None
    assert job.pages is None
    for scope in ("all", "unfinished", "failed", "selected"):
        assert ParseJobCreate(book_id="b05", scope=scope).scope == scope
    try:
        ParseJobCreate(book_id="b05", scope="auto")
    except ValueError:
        pass
    else:
        raise AssertionError("scope 闭集外应 422")


def test_reparse_plan_lists_status_artifact_and_reason(tmp_path, monkeypatch):
    """只读建议清单：parsed+文件缺失的页 suggested 且 reason=artifact_missing。"""
    client, session_factory = _build(tmp_path, monkeypatch)
    with client:
        _sync_book(client)
        _ingest(client)
        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        # 人为制造「DB=parsed 但产物缺失」：删掉第 1 页 blocks.json
        version_dir = Path(tmp_path) / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05" / "versions" / job_id
        (version_dir / "pages" / "p0001.blocks.json").unlink()

        response = client.get("/api/v1/books/mathanalysis-b05/reparse-plan")

        assert response.status_code == 200, response.text
        rows = response.json()["pages"]
        assert [row["page_no"] for row in rows] == [1, 2]
        first = rows[0]
        assert first["status"] == "parsed"
        assert first["has_artifact"] is False
        assert first["suggested"] is True
        assert first["reason"] == "artifact_missing"
        assert rows[1]["suggested"] is False


def test_scope_unfinished_marks_target_page_failed_before_new_job(tmp_path, monkeypatch):
    """受理时把当前可见版本目标页写 failed/error=理由；新任务不继承该页旧产物。"""
    client, session_factory = _build(
        tmp_path, monkeypatch, engine_factory=lambda name: FakeEngine()
    )
    with client:
        _sync_book(client)
        _ingest(client)
        created = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"})
        assert created.status_code == 202, created.text
        first_job = created.json()["id"]
        version_dir = Path(tmp_path) / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05" / "versions" / first_job
        (version_dir / "pages" / "p0001.blocks.json").unlink()

        rerun = client.post(
            "/api/v1/parse-jobs",
            json={"book_id": "mathanalysis-b05", "scope": "unfinished"},
        )

        assert rerun.status_code == 202, rerun.text
        assert rerun.json()["id"] != first_job
        with session_factory() as session:
            rows = session.scalars(
                select(AfPage).where(
                    AfPage.book_id == "mathanalysis-b05",
                    AfPage.job_id == first_job,
                    AfPage.page_no == 1,
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].status == "failed"
            assert "artifact_missing" in rows[0].error
        # 新版本重跑后该页恢复 parsed
        states = client.get("/api/v1/books/mathanalysis-b05/pages").json()["pages"]
        assert all(row["status"] == "parsed" and row["has_artifact"] for row in states)


def test_scope_selected_can_rerun_completed_page(tmp_path, monkeypatch):
    """scope=selected + pages 显式：已完成页也可重跑（决策权在人）。"""
    client, _ = _build(tmp_path, monkeypatch, engine_factory=lambda name: FakeEngine())
    with client:
        _sync_book(client)
        _ingest(client)
        first = client.post("/api/v1/parse-jobs", json={"book_id": "mathanalysis-b05"}).json()
        rerun = client.post(
            "/api/v1/parse-jobs",
            json={"book_id": "mathanalysis-b05", "scope": "selected", "pages": [1]},
        )
        assert rerun.status_code == 202, rerun.text
        assert rerun.json()["pages"] == [1]
        assert rerun.json()["id"] != first["id"]

        # 成功后新版本激活，旧版本按 REQ-097「激活即删被替换版本」被清理（属预期）；
        # 关键是新版本自己的目标页产物是重跑得到的独立文件，不是空壳。
        new_dir = Path(tmp_path) / "data" / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05" / "versions" / rerun.json()["id"]
        assert (new_dir / "pages" / "p0001.blocks.json").is_file()
        assert client.get("/api/v1/books/mathanalysis-b05").json()["active_job_id"] == rerun.json()["id"]
