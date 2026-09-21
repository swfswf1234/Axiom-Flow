"""真机 D3 删除与审计演练（PLAN-003 C2 判据 D3）：合成 5 页探针书走完 sync→ingest→解析→删除。

用法：
    python scripts/tmp/live_d3_delete_drill.py                # 全流程（含解析，需 MinerU 在线）
    python scripts/tmp/live_d3_delete_drill.py --cleanup-only # 只清残留探针书与探针 PDF

**用户裁决 2026-09-22**：演练对象用合成样本（`sb-5page.pdf`）另立探针书，真实书目一律不动；
共享库与共享数据根各写一次，收工即删净。

判据（PLAN-003 D3 原文 + api.md `DELETE /books/{book_id}`）：
1. `?purge=true` 后 `af_books` 行与该书全部版本的 `af_pages`/`af_block_edits` 行删净；
2. 产物目录（含 `versions/` 子树）从数据根消失；
3. `af_parse_jobs` 历史行**保留**为审计线索，且 `GET /parse-jobs/{id}` 仍可读；
4. 真实书目行数回到演练前（探针书不留残）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select, text

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.db.models import AfBlockEdit, AfPage, AfParseJob  # noqa: E402
from axiom_flow.ingest.artifacts import book_dir_for  # noqa: E402
from axiom_flow.schemas import JobStatus  # noqa: E402

PROBE_BOOK_ID = "probe-d3-20260922"
PROBE_DOMAIN = "probe"
PROBE_COURSE = "d3-20260922"
PROBE_FILE_PATH = f"raw/{PROBE_DOMAIN}/{PROBE_COURSE}/{PROBE_BOOK_ID}.pdf"
SAMPLE_PDF = REPO / "data/probes/mineru-3.4.4-20260920/samples/sb-5page.pdf"
TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.PARTIAL.value,
    JobStatus.FAILED.value,
    JobStatus.INTERRUPTED.value,
}
# 回环直连：Windows 注册表代理不豁免 127.0.0.1，走代理会把好环境判成 502（operations.md 窗口不变式）
_HTTP = httpx.Client(trust_env=False, timeout=60.0)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _counts(session, book_id: str) -> dict[str, int]:
    def _n(stmt):
        return len(list(session.scalars(stmt)))

    return {
        "af_pages": _n(select(AfPage.page_no).where(AfPage.book_id == book_id)),
        "af_block_edits": _n(select(AfBlockEdit.edit_id).where(AfBlockEdit.book_id == book_id)),
        "af_parse_jobs": _n(select(AfParseJob.job_id).where(AfParseJob.book_id == book_id)),
    }


def _cleanup(api: str, data_root: Path, evidence: Path) -> int:
    response = _HTTP.delete(f"{api}/books/{PROBE_BOOK_ID}", params={"purge": "true"}, timeout=120.0)
    _log(f"cleanup DELETE → {response.status_code} {response.text[:120]}")
    pdf = data_root / PROBE_FILE_PATH
    if pdf.is_file():
        pdf.unlink()
    parent = pdf.parent
    if parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
    _dump(evidence, "cleanup.json", {"delete_status": response.status_code, "pdf_removed": not pdf.exists()})
    return 0


def _dump(evidence: Path, name: str, payload: dict) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _log(f"wrote {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=None)
    parser.add_argument("--out", type=Path, default=REPO / "data/probes/window-20260922")
    parser.add_argument("--cleanup-only", action="store_true")
    parser.add_argument("--keep", action="store_true", help="跳过删除段（调试用，会使判据不成立）")
    args = parser.parse_args()

    settings = load_settings()
    api = args.api or f"http://127.0.0.1:{settings.port}/api/v1"
    data_root = Path(settings.data_dir)
    evidence = args.out / "B_d3_drill"
    health = _HTTP.get(f"{api}/health", timeout=15.0)
    if health.status_code != 200:
        _log(f"ABORT：8902 未就绪（{health.status_code}）")
        return 3
    if args.cleanup_only:
        return _cleanup(api, data_root, evidence)

    failures: list[str] = []
    with create_engine_for(settings, mysql_url(settings)).connect() as conn:
        books_before = conn.execute(text("SELECT COUNT(*) FROM af_books")).scalar()
    _log(f"起点：af_books {books_before} 本")

    # ---------- 1) 探针 PDF 入数据根 + sync ----------
    if not SAMPLE_PDF.is_file():
        _log(f"ABORT：合成样本缺失 {SAMPLE_PDF}")
        return 3
    target = data_root / PROBE_FILE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SAMPLE_PDF, target)
    _log(f"探针 PDF 就位：{target}（{target.stat().st_size} 字节）")
    synced = _HTTP.post(
        f"{api}/books/sync",
        json=[
            {
                "book_id": PROBE_BOOK_ID,
                "domain_id": PROBE_DOMAIN,
                "course_id": PROBE_COURSE,
                "course_name": "D3 删除演练（合成样本）",
                "knowledge_id": "probe",
                "title": "D3 删除演练探针书",
                "part": "",
                "authors": [],
                "file_path": PROBE_FILE_PATH,
            }
        ],
        timeout=60.0,
    )
    if synced.status_code != 200:
        _log(f"ABORT：sync {synced.status_code} {synced.text[:200]}")
        return 3
    ingested = _HTTP.post(f"{api}/books/{PROBE_BOOK_ID}/ingest", timeout=600.0)
    _log(f"ingest → {ingested.status_code} {ingested.text[:160]}")
    if ingested.status_code != 200:
        return 3

    # ---------- 2) 解析（全本 5 页）+ 一次块编辑，凑齐四类行 ----------
    load = _HTTP.get(f"{settings.ocr_model_url}/health", timeout=15.0).json()
    busy = int(load.get("queued_tasks") or 0) + int(load.get("processing_tasks") or 0)
    if busy:
        _log(f"ABORT：窗口不排他（模型侧在飞/排队 {busy}）")
        return 3
    created = _HTTP.post(
        f"{api}/parse-jobs", json={"book_id": PROBE_BOOK_ID, "engine": "mineru"}, timeout=120.0
    )
    if created.status_code != 202:
        _log(f"ABORT：提交 {created.status_code} {created.text[:200]}")
        return 3
    job_id = created.json()["id"]
    _log(f"job={job_id} 轮询…")
    deadline = time.time() + 600.0
    status = ""
    while time.time() < deadline:
        status = _HTTP.get(f"{api}/parse-jobs/{job_id}", timeout=30.0).json()["status"]
        if status in TERMINAL:
            break
        time.sleep(5.0)
    _log(f"任务终态：{status}")
    if status != JobStatus.COMPLETED.value:
        failures.append(f"探针书解析未 completed（{status}）")

    page = _HTTP.get(f"{api}/books/{PROBE_BOOK_ID}/pages/1", timeout=30.0).json()
    blocks = (page.get("blocks") or {}).get("blocks") or []
    edited = _HTTP.put(
        f"{api}/books/{PROBE_BOOK_ID}/pages/1/blocks/0/edit",
        json={"verdict": "ok", "note": "D3 演练写入，随后应随删除消失"},
        timeout=30.0,
    )
    _log(f"页 1 存活块 {len(blocks)}；写编辑 → {edited.status_code}")
    if edited.status_code != 200:
        failures.append(f"块编辑写入失败（{edited.status_code} {edited.text[:120]}）")

    book_dir = book_dir_for(data_root, PROBE_DOMAIN, PROBE_COURSE, PROBE_BOOK_ID)
    before = _counts(_session(settings), PROBE_BOOK_ID)
    before["dir_exists"] = book_dir.is_dir()
    before["versions"] = sorted(p.name for p in (book_dir / "versions").iterdir()) if (book_dir / "versions").is_dir() else []
    before["page_files"] = len(list((book_dir / "pages").glob("*"))) if (book_dir / "pages").is_dir() else 0
    _dump(evidence, "B1_before_delete.json", {"job_id": job_id, "status": status, **before})
    if not (before["af_pages"] and before["af_block_edits"] and before["dir_exists"]):
        failures.append("删除前四类行/目录未齐备，演练无意义")

    if args.keep:
        _log("--keep：跳过删除段（判据不成立）")
        return 1

    # ---------- 3) DELETE ?purge=true 后逐项断言 ----------
    deleted = _HTTP.delete(f"{api}/books/{PROBE_BOOK_ID}", params={"purge": "true"}, timeout=120.0)
    _log(f"DELETE → {deleted.status_code} {deleted.text[:120]}")
    if deleted.status_code != 200 or not deleted.json().get("purged"):
        failures.append(f"DELETE 未回 purged=true（{deleted.status_code}）")

    after = _counts(_session(settings), PROBE_BOOK_ID)
    get_book = _HTTP.get(f"{api}/books/{PROBE_BOOK_ID}", timeout=30.0)
    get_job = _HTTP.get(f"{api}/parse-jobs/{job_id}", timeout=30.0)
    with create_engine_for(settings, mysql_url(settings)).connect() as conn:
        books_after = conn.execute(text("SELECT COUNT(*) FROM af_books")).scalar()
    after_out = {
        "af_pages": after["af_pages"],
        "af_block_edits": after["af_block_edits"],
        "af_parse_jobs_kept": after["af_parse_jobs"],
        "dir_exists": book_dir.is_dir(),
        "get_book_status": get_book.status_code,
        "get_job_status": get_job.status_code,
        "get_job_body": get_job.json() if get_job.status_code == 200 else get_job.text[:200],
        "af_books_total": books_after,
    }
    _dump(evidence, "B2_after_delete.json", after_out)

    if after_out["af_pages"] != 0:
        failures.append(f"af_pages 未删净（{after_out['af_pages']}）")
    if after_out["af_block_edits"] != 0:
        failures.append(f"af_block_edits 未删净（{after_out['af_block_edits']}）")
    if after_out["dir_exists"]:
        failures.append("产物目录仍在（purge 未生效）")
    if after_out["get_book_status"] != 404:
        failures.append(f"删除后书目仍可读（{after_out['get_book_status']}）")
    if after_out["af_parse_jobs_kept"] != before["af_parse_jobs"]:
        failures.append(f"审计行被误删（{before['af_parse_jobs']} → {after_out['af_parse_jobs_kept']}）")
    if after_out["get_job_status"] != 200:
        failures.append("删除后 GET /parse-jobs/{id} 不再可读（审计线索断链）")
    if books_after != books_before:
        failures.append(f"书目总数未回到起点（{books_before} → {books_after}）")

    # ---------- 4) 清理探针 PDF（真实 raw 书库只读，探针是我们自己写入的） ----------
    if target.is_file():
        target.unlink()
    if target.parent.is_dir() and not any(target.parent.iterdir()):
        target.parent.rmdir()
    _dump(evidence, "B3_summary.json", {"failures": failures, "job_id": job_id})
    _log("PASS：D3 四判据全达成" if not failures else "FAIL：" + "；".join(failures))
    return 0 if not failures else 1


def _session(settings):
    from sqlalchemy.orm import Session

    return Session(create_engine_for(settings, mysql_url(settings)))


if __name__ == "__main__":
    raise SystemExit(main())
