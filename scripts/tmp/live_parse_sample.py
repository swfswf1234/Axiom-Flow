"""ARCH-009 代表书抽查窗口：对**单本书的一批指定页**提交一次解析并取证（写路径）。

用法：
    python scripts/tmp/live_parse_sample.py <book_id> <pages 逗号分隔> [--deadline 秒] [--out 目录]

D 类操作规程（本脚本自证）：
1. **一次调用 = 一个任务**：脚本串行执行、提交前复验 C-0 排他（模型侧 `queued+processing == 0`
   且本机无 `queued` 行）。资源只够一个 MinerU 在飞（用户 2026-09-21 裁决），并行提交一律禁止。
2. **只经 8902 API 驱动**，不在进程内跑管线：要证明的是前端实际链路（提交门槛 → 后台执行 →
   生效指针 → 页端点读取）。
3. **不删不改其他书目**：只写目标书的 `versions/<job_id>/`；旧生效版本在任务失败时保持不变
   （激活门槛只看本次目标页，未齐备则 `partial`/`failed` 且不激活）。
4. **凭据不外泄**：只连本机回环，数据库仅读 `af_parse_jobs` 计数；不打印连接串。
5. **留档**：`data/probes/window-<日期>/` 下写入前后快照 + 轮询采样。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.ingest.artifacts import book_dir_for  # noqa: E402
from axiom_flow.schemas import JobStatus  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EXPECTED_REVISION = "20260921_0001"
TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.PARTIAL.value,
    JobStatus.FAILED.value,
    JobStatus.INTERRUPTED.value,
}

# 本地流量恒为直连：注册表代理会把 127.0.0.1 变成 502 假象（见 live_reparse_b01_pages.py 同源注释）
_HTTP = httpx.Client(trust_env=False, timeout=60.0)


def _get(url: str, **kwargs):
    return _HTTP.get(url, **kwargs)


def _post(url: str, **kwargs):
    return _HTTP.post(url, **kwargs)


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _dump(evidence: Path, name: str, payload: dict) -> None:
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"wrote {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("book_id")
    parser.add_argument("pages", help="逗号分隔页码，如 1,2,3 或 1-3,10")
    parser.add_argument("--deadline", type=float, default=3600.0)
    parser.add_argument("--out", type=Path, default=REPO / "data/probes/window-20260922")
    args = parser.parse_args()

    pages: list[int] = []
    for token in args.pages.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            lo, hi = token.split("-")
            pages.extend(range(int(lo), int(hi) + 1))
        else:
            pages.append(int(token))
    pages = sorted(set(pages))
    settings = load_settings()
    api = f"http://127.0.0.1:{settings.port}/api/v1"
    db_engine = create_engine_for(settings, mysql_url(settings))
    evidence = args.out / args.book_id
    _log(f"目标：{args.book_id}，{len(pages)} 页 {pages[0]}..{pages[-1]}")

    # ---------- 0) 部署自证：迁移到位 + 新语义字段在返回体里 ----------
    with db_engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    book = _get(f"{api}/books/{args.book_id}").json()
    if revision != EXPECTED_REVISION or "last_parse_result" not in book:
        _log(f"ABORT：部署不符（revision={revision}，字段={sorted(book)}）")
        return 3
    if not pages:
        _log("ABORT：空页清单")
        return 3
    if (book["page_count"] or 0) < pages[-1]:
        _log(f"ABORT：页码越界（page_count={book['page_count']}）")
        return 3

    # ---------- 1) C-0 排他：占用以模型侧计数为准 ----------
    # 服务重启后执行器为空，重启前遗留的 `running` 行是无人推进的孤儿，不阻塞窗口。
    load_body = _get(f"{settings.ocr_model_url}/health", timeout=10.0).json()
    model_busy = int(load_body.get("queued_tasks") or 0) + int(load_body.get("processing_tasks") or 0)
    with db_engine.connect() as conn:
        local_queued = [
            r[0] for r in conn.execute(text("SELECT job_id FROM af_parse_jobs WHERE status='queued'"))
        ]
    _dump(evidence, "P0_exclusive.json", {
        "model_health": {k: load_body.get(k) for k in ("status", "queued_tasks", "processing_tasks",
                                                       "completed_tasks", "max_concurrent_requests")},
        "model_busy_tasks": model_busy,
        "local_queued_jobs": local_queued,
        "rule": "占用 = 模型侧 queued+processing > 0 或本机有 queued 行",
    })
    if model_busy or local_queued:
        _log(f"ABORT：窗口不排他（模型侧 {model_busy}，本机 queued {len(local_queued)}）")
        return 3

    before = {
        "book": {k: book.get(k) for k in ("page_count", "parse_status", "last_parse_result",
                                          "active_job_id", "pages_done")},
        "versions_before": _get(f"{api}/books/{args.book_id}/versions", timeout=30.0).json(),
    }
    _dump(evidence, "P1_before.json", before)

    # ---------- 2) 单次提交 + 轮询到终态 ----------
    created = _post(f"{api}/parse-jobs", json={"book_id": args.book_id, "pages": pages,
                                               "engine": "mineru"}, timeout=120.0)
    if created.status_code != 202:
        _log(f"ABORT：提交失败 {created.status_code} {created.text[:200]}")
        _dump(evidence, "P2_submit_fail.json", {"status": created.status_code,
                                                "body": created.text[:500]})
        return 3
    job_id = created.json()["id"]
    _log(f"job={job_id}，轮询…")
    polls: list[dict] = []
    deadline = time.time() + args.deadline
    while time.time() < deadline:
        current = _get(f"{api}/parse-jobs/{job_id}", timeout=30.0).json()
        polls.append({"at": round(args.deadline - (deadline - time.time()), 1),
                      "status": current["status"], "progress": current.get("progress"),
                      "failed_pages": current.get("failed_pages")})
        if current["status"] in TERMINAL:
            break
        time.sleep(10.0)
    else:
        _log(f"ABORT：{args.deadline}s 内未达终态")
    _dump(evidence, "P2_polls.json", {"job_id": job_id, "polls": polls[-12:]})
    _log(f"终态：{polls[-1] if polls else '（无采样）'}")

    # ---------- 3) 终态取证：激活 / 产物 / 页端点 ----------
    after = _get(f"{api}/books/{args.book_id}", timeout=30.0).json()
    versions = _get(f"{api}/books/{args.book_id}/versions", timeout=30.0).json()
    version_dir = book_dir_for(settings.data_dir, after["domain_id"], after["course_id"],
                               args.book_id) / "versions" / job_id
    per_page = {}
    for page_no in pages:
        response = _get(f"{api}/books/{args.book_id}/pages/{page_no}", timeout=60.0)
        payload = response.json() if response.status_code == 200 else {}
        blocks = payload.get("blocks") or {}
        body = blocks.get("blocks") or []
        quality = blocks.get("quality") or {}
        per_page[page_no] = {
            "status": response.status_code,
            "blocks": len(body),
            "types": sorted({str(b.get("type")) for b in body}),
            "dropped": quality.get("dropped_block_count"),
            "dropped_types": quality.get("dropped_types"),
        }
    on_disk = {
        "blocks_files": len(list((version_dir / "pages").glob("*.blocks.json")))
        if (version_dir / "pages").is_dir() else 0,
        "md_files": len(list((version_dir / "pages").glob("*.md")))
        if (version_dir / "pages").is_dir() else 0,
        "png_files": len(list((version_dir / "pages").glob("*.png")))
        if (version_dir / "pages").is_dir() else 0,
        "chunks_exists": (version_dir / "chunks.json").is_file(),
        "manifest_exists": (version_dir / "manifest.json").is_file(),
    }
    summary = {
        "book_id": args.book_id,
        "job_id": job_id,
        "requested_pages": pages,
        "final": polls[-1] if polls else None,
        "job_terminal": {k: after.get(k) for k in ()},
        "book_after": {k: after.get(k) for k in ("parse_status", "last_parse_result",
                                                 "active_job_id", "pages_done")},
        "activated": after.get("active_job_id") == job_id,
        "versions_after": versions,
        "version_dir": str(version_dir),
        "on_disk": on_disk,
        "per_page": per_page,
        "dropped_total": sum(int(v.get("dropped") or 0) for v in per_page.values()),
    }
    _dump(evidence, "P3_after.json", summary)
    dropped_literals: dict[str, int] = {}
    for v in per_page.values():
        for lit in v.get("dropped_types") or []:
            dropped_literals[str(lit)] = dropped_literals.get(str(lit), 0) + 1
    _log(
        f"job {job_id} 终态 {summary['final']['status'] if summary['final'] else '?'}、"
        f"激活 {summary['activated']}、落盘 blocks/md/png = "
        f"{on_disk['blocks_files']}/{on_disk['md_files']}/{on_disk['png_files']}、"
        f"chunks {on_disk['chunks_exists']}、丢弃 {summary['dropped_total']} {dropped_literals}"
    )
    return 0 if summary["activated"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
