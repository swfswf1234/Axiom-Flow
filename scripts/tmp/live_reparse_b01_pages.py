"""真机 C/D 窗口取证脚本（PLAN-004 版本布局 + DEFECT-004/005/006 修复后的一次性重解析）。

用法：
    python scripts/tmp/live_reparse_b01_pages.py                 # 全流程：前 20 页重解析 + 取证
    python scripts/tmp/live_reparse_b01_pages.py 1 20            # 指定页区间
    python scripts/tmp/live_reparse_b01_pages.py --check-only    # 只跑前置与只读取证，不写不解析

D 类操作规程（本脚本自证）：
1. **只经 8902 API 驱动**，不在进程内直接跑管线：窗口要证明的是「前端实际会走的那条链路」
   成立，含提交期门槛（DEFECT-006 越界 422）、生效指针与只读端点。
2. **就绪判定不信任 8900 的一次性提示**：MinerU 5002 由根仓库 8900 启停，其前端轮询截止是 15s，
   而 vLLM 权重加载需要分钟级 → 8900 报「失败」时容器往往仍在起。本脚本对 `GET 5002/health`
   做**轮询到就绪**（默认 300s 截止），不在单次失败上中止窗口。
3. **单次解析**：产物只落 `versions/<job_id>/`（V1）；页图由 ingest 的根视图硬链接进版本目录。
   根仓库已在 2026-09-21 清空 `dataset/parsed/`，故本脚本先按需 re-ingest（`pages/` 齐备才解析），
   re-ingest 同时按 V8 作废 legacy 页行（含真机第 999 页幽灵行）。
4. **不越界、不删数据**：只写目标书目录与 `af_*` 私有表相关行；不删任何文件，不触其他书目；
   越界 422 探针在确认新代码/新迁移之后才发（否则会给旧部署写出幽灵行）。
5. **留档**：`data/probes/live-window-<日期>/` 下 S0 前置、S1 解析前快照、S2 解析后取证。

回滚方式：`alembic downgrade 20260917_0001`（**有损**，见 operations.md runbook）+ 按 S1 快照
还原；未激活的版本目录天然读不到，无需删除。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from axiom_flow.config import load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.db.models import AfPage  # noqa: E402
from axiom_flow.ingest.artifacts import book_dir_for  # noqa: E402
from axiom_flow.schemas import JobStatus  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BOOK_ID = "linearalgebra-b01"
EXPECTED_REVISION = "20260921_0001"
TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.PARTIAL.value,
    JobStatus.FAILED.value,
    JobStatus.INTERRUPTED.value,
}

# 本地流量恒为**直连**：Windows 注册表代理不豁免 127.0.0.1，交给代理就是 502/0 字节的「端点坏了」
# 假象（与 MinerU 适配器条文 11 同源；2026-09-21 20:2x 窗口实证——代理被打开后本脚本探针全变 502，
# 而 curl 直连返回 200）。窗口工具若走代理会把好环境判成故障。
_HTTP = httpx.Client(trust_env=False, timeout=30.0)


def _get(url: str, **kwargs):
    return _HTTP.get(url, **kwargs)


def _post(url: str, **kwargs):
    return _HTTP.post(url, **kwargs)


def _log(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


def _dump(evidence: Path, name: str, payload: dict) -> None:
    (evidence / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"wrote {name}")


def _wait_health(url: str, *, deadline: float, label: str, interval: float = 5.0) -> dict:
    """轮询到 200 才算就绪；8900 的一次性「失败」提示不作为中止依据（规程 2）。"""
    started = time.time()
    attempts = []
    while time.time() - started < deadline:
        try:
            response = _get(url, timeout=5.0)
            attempts.append({"status": response.status_code, "at": round(time.time() - started, 1)})
            if response.status_code == 200:
                _log(f"{label} 就绪：{url}（{attempts[-1]['at']}s，{len(attempts)} 次探测）")
                return {"url": url, "ready": True, "seconds": attempts[-1]["at"], "attempts": len(attempts)}
        except Exception as exc:  # noqa: BLE001 - 容器启动期连接被拒是预期内
            attempts.append({"error": str(exc)[:160], "at": round(time.time() - started, 1)})
        time.sleep(interval)
    _log(f"{label} 在 {deadline}s 内未就绪：{url}")
    return {"url": url, "ready": False, "seconds": deadline, "attempts": len(attempts)}


def _metrics(blocks_page: dict) -> dict:
    """块几何与丢弃对账（DEFECT-004/005 的判据）。"""
    blocks = blocks_page.get("blocks") or []
    width, height = blocks_page.get("image_size") or [0, 0]
    bboxes = [b.get("bbox") for b in blocks if b.get("bbox")]
    quality = blocks_page.get("quality") or {}
    return {
        "block_count": len(blocks),
        "image_size": [width, height],
        "types": sorted({str(b.get("type")) for b in blocks}),
        "clipped_right": sum(1 for b in bboxes if b[2] >= width - 1),
        "clipped_bottom": sum(1 for b in bboxes if b[3] >= height - 1),
        "degenerate": sum(1 for b in bboxes if b[2] - b[0] <= 2 or b[3] - b[1] <= 2),
        "max_x1": max((b[2] for b in bboxes), default=None),
        "dropped_block_count": quality.get("dropped_block_count"),
        "dropped_types": quality.get("dropped_types"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="真机 C/D 窗口单次重解析与取证")
    parser.add_argument("lo", nargs="?", type=int, default=1)
    parser.add_argument("hi", nargs="?", type=int, default=20)
    parser.add_argument("--check-only", action="store_true", help="只做前置与只读取证，不 ingest 不解析")
    parser.add_argument("--evidence-only", metavar="JOB_ID", default=None,
                        help="不提交新解析，只对既有 job 收集 S2 取证（避免为补证据再解析一次）")
    parser.add_argument("--model-deadline", type=float, default=300.0)
    parser.add_argument("--parse-deadline", type=float, default=1800.0)
    args = parser.parse_args()
    pages = list(range(args.lo, args.hi + 1))

    settings = load_settings()
    evidence = REPO / "data" / "probes" / f"live-window-{time.strftime('%Y%m%d')}"
    evidence.mkdir(parents=True, exist_ok=True)
    api = f"http://127.0.0.1:{settings.port}/api/v1"
    data_root = Path(settings.data_dir)

    # ---------- S0 前置：就绪与部署自证 ----------
    model = _wait_health(f"{settings.ocr_model_url}/health", deadline=args.model_deadline, label="MinerU 5002")
    service = _wait_health(f"{api}/health", deadline=90.0, label="Axiom-Flow 8902")
    _dump(evidence, "S0_preflight.json", {"model": model, "service": service, "api": api,
                                          "data_root": str(data_root), "check_only": args.check_only})
    if not (model["ready"] and service["ready"]):
        _log("ABORT：模型服务或 8902 未就绪（见 S0_preflight.json）")
        return 3

    db_engine = create_engine_for(settings, mysql_url(settings))
    with db_engine.connect() as conn:
        revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    book_http = _get(f"{api}/books/{BOOK_ID}", timeout=30.0)
    if book_http.status_code != 200:
        _log(f"ABORT：8902 无该书（{book_http.status_code}）；先由 8900 同步书目")
        return 2
    book = book_http.json()
    if "active_job_id" not in book or "last_parse_result" not in book or revision != EXPECTED_REVISION:
        _log(
            f"ABORT：部署未就绪（alembic={revision}，"
            f"active_job_id={'有' if 'active_job_id' in book else '无'}，"
            f"last_parse_result={'有' if 'last_parse_result' in book else '无'}）"
        )
        _log(f"      期望 {EXPECTED_REVISION} + 新代码同窗口发布，见 operations.md runbook")
        return 2

    book_row_dir = book_dir_for(data_root, book["domain_id"], book["course_id"], BOOK_ID)
    root_pages = book_row_dir / "pages"
    png_count = len(list(root_pages.glob("p*.png")))
    _log(f"根视图：page_count={book['page_count']}，页图 {png_count} 张，active_job_id={book['active_job_id']}")

    with db_engine.connect() as conn:
        page_rows = list(conn.execute(
            select(AfPage.book_id, AfPage.job_id, AfPage.page_no, AfPage.status)
            .where(AfPage.book_id == BOOK_ID)
            .order_by(AfPage.job_id, AfPage.page_no)
        ))
    ghost = [r._asdict() for r in page_rows if r.page_no > (book["page_count"] or 0)]
    legacy_rows = [r._asdict() for r in page_rows if r.job_id == "legacy"]
    _dump(evidence, "S1_before.json", {
        "book": book,
        "alembic_revision": revision,
        "root_png_count": png_count,
        "row_total": len(page_rows),
        "legacy_rows": len(legacy_rows),
        "ghost_rows": ghost,
        "versions_on_disk": sorted(p.name for p in (book_row_dir / "versions").glob("*"))
        if (book_row_dir / "versions").is_dir() else [],
        "root_tmp_exists": (data_root / "tmp").exists(),
    })
    _log(f"解析前：页行 {len(page_rows)}（legacy {len(legacy_rows)}，越界幽灵 {len(ghost)}）")

    if args.evidence_only:
        # 只为补全 S2 取证而运行：不 ingest、不打门槛探针、不提交解析（**不再解析一次**）。
        job_id = args.evidence_only
        polls = [{"status": "evidence-only", "progress": None}]
        _log(f"EVIDENCE-ONLY：跳过写路径，直接对 job={job_id} 取证")
    else:
        # ---------- 1) ingest 补齐根视图（根仓库已清空 parsed/，页图必须重渲染） ----------
        if png_count < (book["page_count"] or 0):
            if args.check_only:
                _log(f"CHECK-ONLY：跳过 ingest（根视图页图 {png_count}/{book['page_count']} 不完整）")
            else:
                _log("提交 ingest（486 页 150 DPI 渲染，分钟级）…")
                ingested = _post(f"{api}/books/{BOOK_ID}/ingest", timeout=1800.0)
                if ingested.status_code != 200:
                    _log(f"ABORT：ingest 失败 {ingested.status_code} {ingested.text[:200]}")
                    return 3
                _log(f"ingest 完成：page_count={ingested.json()['page_count']}")
                with db_engine.connect() as conn:
                    after_legacy = list(conn.execute(
                        select(AfPage.page_no).where(AfPage.book_id == BOOK_ID, AfPage.job_id == "legacy")
                    ))
                _log(f"re-ingest 后 legacy 行数：{len(after_legacy)}（V8：预期 0，越界幽灵行随之作废）")

        # ---------- 2) 提交期门槛复验（DEFECT-006）：越界必须 422 且不落任务行 ----------
        # 实测：`parse-jobs` 的「尚未 ingest」404 排在 pages 门槛之前，故根视图未补齐时该探针打不到
        # 门槛——记为待复验而非失败，D 步 ingest 之后同一次运行内自动补打。
        page_count = book["page_count"] or 0
        png_after_ingest = len(list(root_pages.glob("p*.png")))
        if png_after_ingest < page_count:
            _log(f"SKIP 越界探针：根视图页图 {png_after_ingest}/{page_count}，未 ingest 的书先撞 404")
            _dump(evidence, "S1b_gate_probe_skipped.json", {
                "reason": "not_ingested", "png_count": png_after_ingest, "page_count": page_count})
        else:
            with db_engine.connect() as conn:
                jobs_before = conn.execute(text("SELECT COUNT(*) FROM af_parse_jobs")).scalar()
            rejects = {}
            for bad in ([page_count + 5], [0], [1, page_count + 1]):
                response = _post(f"{api}/parse-jobs", json={"book_id": BOOK_ID, "pages": bad}, timeout=60.0)
                rejects[str(bad)] = {"status": response.status_code, "detail": (response.text or "")[:200]}
            with db_engine.connect() as conn:
                jobs_after = conn.execute(text("SELECT COUNT(*) FROM af_parse_jobs")).scalar()
            _log(f"越界探针：{[(k, v['status']) for k, v in rejects.items()]}，任务行增量 {jobs_after - jobs_before}")
            if any(v["status"] != 422 for v in rejects.values()) or jobs_after != jobs_before:
                _log("ABORT：越界未被拒或任务行被写出——DEFECT-006 未生效，停窗口")
                _dump(evidence, "S1b_gate_probe_fail.json", {"rejects": rejects,
                                                             "jobs_before": jobs_before, "jobs_after": jobs_after})
                return 4

        if args.check_only:
            _log("CHECK-ONLY 完成：前置与部署自证已取证"
                 + ("（提交期门槛待 D 步 ingest 后复验）" if png_after_ingest < page_count else ""))
            return 0
        if png_after_ingest < page_count:
            _log("ABORT：ingest 后根视图仍不完整，提交期门槛无法复验")
            return 4

        # ---------- 3) 单次重解析（版本布局）----------
        # C-0 窗口排他（ARCH-017）：**占用判据以模型侧计数为准**——服务重启后执行器为空，重启前遗留的
        # `running` 行是孤儿（无人再推进），只有「模型侧非空 ∪ 本机 queued 待取」才是真实并发来源。
        load_body = _get(f"{settings.ocr_model_url}/health", timeout=10.0).json()
        model_busy = int(load_body.get("queued_tasks") or 0) + int(load_body.get("processing_tasks") or 0)
        with db_engine.connect() as conn:
            nonterminal = [
                {k: (str(v) if v is not None else None) for k, v in r._mapping.items()}
                for r in conn.execute(
                    text(
                        "SELECT job_id, book_id, status, started_at, "
                        "TIMESTAMPDIFF(SECOND, COALESCE(started_at, created_at), NOW()) AS idle_seconds "
                        "FROM af_parse_jobs WHERE status IN ('queued','running')"
                    )
                )
            ]
        queued = [r for r in nonterminal if r["status"] == "queued"]
        _dump(evidence, "S1c_exclusive.json", {
            "model_health": {k: load_body.get(k) for k in ("status", "queued_tasks", "processing_tasks",
                                                            "completed_tasks", "max_concurrent_requests")},
            "model_busy_tasks": model_busy,
            "local_nonterminal_jobs": nonterminal,
            "local_queued_jobs": [r["job_id"] for r in queued],
            "rule": "占用 = 模型侧 queued+processing > 0 或本机有 queued 行；重启前的 running 孤儿不阻塞窗口",
        })
        if model_busy or queued:
            _log(f"ABORT：窗口不排他（C-0）——模型侧在飞/排队 {model_busy}，本机 queued {len(queued)}")
            return 3
        _log(f"C-0 排他：模型侧排队/在飞 0、本机 queued 0；遗留非终态行 {len(nonterminal)} 个（孤儿，仅登记）"
             f" {[(r['job_id'], r['status'], r['idle_seconds']) for r in nonterminal]}")

        _log(f"提交解析任务：pages=[{args.lo}..{args.hi}]")
        created = _post(
            f"{api}/parse-jobs", json={"book_id": BOOK_ID, "pages": pages, "engine": "mineru"}, timeout=120.0
        )
        if created.status_code != 202:
            _log(f"ABORT：提交失败 {created.status_code} {created.text[:200]}")
            _dump(evidence, "S2_submit_fail.json", {"status": created.status_code, "body": created.text[:500]})
            return 3
        job_id = created.json()["id"]
        _log(f"job={job_id}，轮询进度…")
        polls = []
        deadline = time.time() + args.parse_deadline
        while time.time() < deadline:
            current = _get(f"{api}/parse-jobs/{job_id}", timeout=30.0).json()
            polls.append({"status": current["status"], "progress": current.get("progress")})
            if current["status"] in TERMINAL:
                break
            time.sleep(10.0)
        else:
            _log(f"ABORT：{args.parse_deadline}s 内未达终态")
        _log(f"终态：{polls[-1] if polls else '（无采样）'}")

    # ---------- 4) 解析后取证 ----------
    active = _get(f"{api}/books/{BOOK_ID}", timeout=30.0).json()
    versions = _get(f"{api}/books/{BOOK_ID}/versions", timeout=30.0).json()
    version_dir = book_row_dir / "versions" / job_id
    per_page = {}
    for page_no in pages:
        response = _get(f"{api}/books/{BOOK_ID}/pages/{page_no}", timeout=60.0)
        per_page[page_no] = (
            # 页端点返回信封：结构化块在 `blocks` 子对象里（BlocksPage），不是顶层列表
            {"status": response.status_code, **_metrics(response.json().get("blocks") or {})}
            if response.status_code == 200
            else {"status": response.status_code}
        )
    with db_engine.connect() as conn:
        scoped = list(conn.execute(
            select(AfPage.page_no, AfPage.status, AfPage.error)
            .where(AfPage.book_id == BOOK_ID, AfPage.job_id == job_id)
            .order_by(AfPage.page_no)
        ))
    report = {
        "job_id": job_id,
        "polls": polls[-5:],
        "book_after": {k: active.get(k) for k in ("parse_status", "pages_done", "active_job_id", "page_count")},
        "activated": active.get("active_job_id") == job_id,
        "versions": versions,
        "version_dir_files": sorted(p.relative_to(version_dir).as_posix() for p in version_dir.rglob("*") if p.is_file())
        if version_dir.is_dir() else [],
        "version_png_hardlinked": [
            f"p{p:04d}.png" for p in pages
            if (version_dir / "pages" / f"p{p:04d}.png").is_file()
            and (root_pages / f"p{p:04d}.png").is_file()
            and (version_dir / "pages" / f"p{p:04d}.png").stat().st_ino
            == (root_pages / f"p{p:04d}.png").stat().st_ino
        ] if version_dir.is_dir() else [],
        "root_view_has_no_chunks": not (book_row_dir / "chunks.json").exists(),
        "data_root_tmp_absent": not (data_root / "tmp").exists(),
        "af_pages_scoped": [r._asdict() for r in scoped],
        "pages": per_page,
        "clip_summary": {
            "clipped_right": sum(p.get("clipped_right", 0) for p in per_page.values() if "clipped_right" in p),
            "clipped_bottom": sum(p.get("clipped_bottom", 0) for p in per_page.values() if "clipped_bottom" in p),
            "degenerate": sum(p.get("degenerate", 0) for p in per_page.values() if "degenerate" in p),
            "dropped_blocks": sum(p.get("dropped_block_count") or 0 for p in per_page.values()),
            "types_seen": sorted({t for p in per_page.values() for t in (p.get("types") or [])}),
        },
    }
    _dump(evidence, "S2_after.json", report)
    _log(f"激活：{report['activated']}；clip R/B/D = "
         f"{report['clip_summary']['clipped_right']}/{report['clip_summary']['clipped_bottom']}/{report['clip_summary']['degenerate']}"
         f"；丢弃 {report['clip_summary']['dropped_blocks']}；类型 {report['clip_summary']['types_seen']}")
    _log(f"版本目录产物 {len(report['version_dir_files'])} 个；根视图无 chunks={report['root_view_has_no_chunks']}；"
         f"数据根 tmp 不存在={report['data_root_tmp_absent']}")
    if not report["activated"]:
        _log("注意：未激活 → 端点仍读旧生效版（V3/IV4 门槛），产物保留在 versions/ 供排查")
    return 0 if report["activated"] else 5


if __name__ == "__main__":
    raise SystemExit(main())
