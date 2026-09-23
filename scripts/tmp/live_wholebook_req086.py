"""W6 真机复测（PLAN-007 S3）：b05 全本 317 页在 **`qed_test` + 探针数据根** 上按页窗口跑通并激活。

用法：
    python scripts/tmp/live_wholebook_req086.py                      # 全流程（迁移→起服→sync→ingest→全本解析→取证）
    python scripts/tmp/live_wholebook_req086.py --preflight-only     # 只到 ingest 为止，不占 MinerU、不解析
    python scripts/tmp/live_wholebook_req086.py --evidence-only JOB   # **不再解析一次**，只对既有 job 补 S3 取证
    python scripts/tmp/live_wholebook_req086.py --stop-service       # 只停探针服务（按 pid 文件）

**用户裁决 2026-09-22**：W6 属 D 类真机写，落 `qed_test` 与探针数据根，业务库 `qed` 与真实
`dataset/parsed/` **零写入**。实现方式是给本进程与探针子进程注入真实环境变量
`QED_DB_NAME` / `QED_DATA_ROOT`（真实 env 优先于 `.env`，故**不改 `.env`**，退出即回业务口径）。

D 类操作规程（本脚本自证）：
1. **硬闸门**：解析前断言 `settings.db_name == qed_test` 且 `settings.data_dir` 就是探针根，
   否则直接退出 4——不信任「我记得设了环境变量」。
2. **业务侧零写入自证**：窗口前后各取一次业务库 b05 行与真实 `dataset/parsed/.../mathanalysis-b05`
   目录快照（文件数 + 最新 mtime + 总字节），逐项比对，变了即判 FAIL。
3. **只经 HTTP 驱动**探针服务（独立端口，不碰 `logs/qed-axiom.pid`，不影响 8902 业务实例）：
   窗口要证明的是前端实际会走的那条链路（API → JobRunner → 串行窗口循环）。
4. **单次解析**：补证据一律 `--evidence-only`；判据不成立时同 job 重跑即续跑（已解析页跳过）。
5. **窗口排他（C-0）**：占用以模型侧 `queued_tasks + processing_tasks == 0` 为准，另查业务库有无
   `queued`/`running` 行——业务实例若在我们窗口内被起任务，会抢同一个 MinerU，故登记为警告并留档。
6. **WSL 常驻前置（默认开，`--wsl-holder 300`）**：本机 WSL 会在最后一个 Linux 进程退出十几秒后
   **回收整个发行版**（`vmIdleTimeout` 不生效），块间空隙正好踩中——表现为中途 `WinError 10054` +
   剩余页 `MODEL_UNAVAILABLE`。脚本因此滚动维持一个 `wsl bash -c "sleep N"` 进程，退出时收掉；
   `--wsl-holder 0` 显式关闭并只留 WARN。该前置**不改任何业务参数**（窗口/deadline 原样）。

判据（PLAN-007 成功标准，逐条打印）：
- S2：`progress.parsed` 随块单调递增且真机可观测到非零（`progress_samples.jsonl` 采样）。
- S3：b05（317 页）全本 job `completed` **且激活**、317 页全 `parsed`、空白页 **1 基 48/98/136/162/282**
  计 parsed 且带 `blank_page` 质量标记（清单为 W0 修正版，非原误记的 47/48/49/98/136/162/282）。
- S5/S4 侧证：全书 `dropped_block_count` 合计为 0（ARCH-019 归零）、`chunks.json` 在位、
  读取端点按生效版本可回显。

留档：`data/probes/wholebook-req086-20260922/`（`S0`~`S3` JSON + 进度采样 + 子服务日志 + `summary.md`）。
探针数据根在其 `root/` 下，整目录可删；`qed_test` 的 `af_*` 四表为本仓库自有（ADR 0005 边界），
清理只需 `DROP TABLE af_books, af_parse_jobs, af_pages, af_block_edits, alembic_version`——**绝不 `DROP DATABASE`**
（该库另有根仓库 `qt_*` 镜像表）。
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy import select, text

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

for _stream in (sys.stdout, sys.stderr):  # 中文日志在 GBK 控制台下会抛 UnicodeEncodeError
    _stream.reconfigure(encoding="utf-8", errors="replace")

from axiom_flow.config import Settings, load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.db.models import AfPage, AfParseJob  # noqa: E402
from axiom_flow.ingest.artifacts import book_dir_for, sha256_file, version_dir_for  # noqa: E402
from axiom_flow.schemas import JobStatus  # noqa: E402

PROBE_DB_NAME = "qed_test"
PROBE_DIR = REPO / "data" / "probes" / "wholebook-req086-20260922"
PROBE_ROOT = PROBE_DIR / "root"
BOOK_ID = "mathanalysis-b05"
SOURCE_SHA256 = "341544f3fa9ce6ac8bf3860b4d9f9e4e86b1d2778e2a8644c1cafb31267ed968"
PAGE_COUNT = 317
BLANK_PAGES = [48, 98, 136, 162, 282]  # W0 像素法冻结（1 基）
EXPECTED_REVISION = "20260921_0001"
PROBE_PORT = 8912
TERMINAL = {
    JobStatus.COMPLETED.value,
    JobStatus.PARTIAL.value,
    JobStatus.FAILED.value,
    JobStatus.INTERRUPTED.value,
}
# 回环一律直连：Windows 注册表代理不豁免 127.0.0.1，走代理会把好环境判成 502（operations.md 不变式）
_HTTP = httpx.Client(trust_env=False, timeout=60.0)
_PID_FILE = PROBE_DIR / "service-8912.pid"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _dump(name: str, payload: dict) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    (PROBE_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _log(f"wrote {name}")


def _append_jsonl(name: str, payload: dict) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    with (PROBE_DIR / name).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _probe_settings() -> Settings:
    """注入真实环境变量后取配置（优先级：真实 env > `.env`），并守住探针口径。"""
    os.environ["QED_DB_NAME"] = PROBE_DB_NAME
    os.environ["QED_DATA_ROOT"] = str(PROBE_ROOT)
    return load_settings()


def _guard_probe_scope(settings: Settings) -> list[str]:
    """硬闸门：库名与数据根必须都在探针口径内，否则返回失败原因。"""
    failures = []
    if settings.db_name != PROBE_DB_NAME:
        failures.append(f"db_name={settings.db_name}，期望 {PROBE_DB_NAME}")
    root = Path(settings.data_dir).resolve()
    if root != PROBE_ROOT.resolve():
        failures.append(f"data_dir={root}，期望探针根 {PROBE_ROOT.resolve()}")
    return failures


def _business_snapshot(business: Settings) -> dict:
    """业务库 b05 行 + 真实产物目录快照（只读，用于「零写入」前后比对）。"""
    out: dict = {"db_name": business.db_name, "data_dir": str(business.data_dir)}
    try:
        engine = create_engine_for(business, mysql_url(business))
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT book_id, page_count, pages_done, parse_status, active_job_id, "
                    "last_parse_result, updated_at FROM af_books WHERE book_id=:b"
                ),
                {"b": BOOK_ID},
            ).mappings().first()
            out["af_books"] = dict(row) if row else None
            out["queued_or_running"] = [
                dict(r._mapping)
                for r in conn.execute(
                    text("SELECT job_id, book_id, status FROM af_parse_jobs WHERE status IN ('queued','running')")
                )
            ]
    except Exception as exc:  # noqa: BLE001 - 业务库不可达不阻塞探针，登记为取证事实
        out["error"] = str(exc)[:200]
    book_dir = book_dir_for(Path(business.data_dir), "", "math_analysis", BOOK_ID)
    files = [p for p in book_dir.rglob("*") if p.is_file()] if book_dir.is_dir() else []
    out["real_dir_exists"] = book_dir.is_dir()
    out["real_file_count"] = len(files)
    out["real_total_bytes"] = sum(p.stat().st_size for p in files)
    out["real_newest_mtime"] = max((p.stat().st_mtime for p in files), default=0)
    return out


def _wait_model(url: str, *, deadline: float) -> dict:
    started = time.time()
    last_error = "no attempt"
    while time.time() - started < deadline:
        try:
            response = _HTTP.get(f"{url}/health", timeout=10.0)
            if response.status_code == 200:
                body = response.json()
                busy = int(body.get("queued_tasks") or 0) + int(body.get("processing_tasks") or 0)
                return {"ready": True, "busy": busy, "health": body}
            last_error = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001 - 容器重启期连接被拒属预期
            last_error = str(exc)[:160]
        time.sleep(5.0)
    return {"ready": False, "busy": None, "error": last_error}


def _service_running(port: int) -> bool:
    try:
        return _HTTP.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=3.0).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _service_start(port: int) -> subprocess.Popen | None:
    """拉起探针服务子进程（独立 PID 文件，绝不动 logs/qed-axiom.pid）。"""
    if _service_running(port):
        _log(f"探针服务已在 :{port} 运行，复用")
        return None
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"  # 中文日志落文件时不随控制台代码页走形
    log_file = (PROBE_DIR / "service-8912.log").open("ab")  # noqa: SIM115 - 子进程继承句柄
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "axiom_flow.api.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(REPO),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=child_env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    _PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if _service_running(port):
            _log(f"探针服务就绪 :{port}（pid {proc.pid}）")
            return proc
        time.sleep(1.0)
    _log(f"FAIL：探针服务 :{port} 60s 内未就绪，见 service-8912.log")
    return proc


def _service_stop(port: int) -> None:
    if _PID_FILE.is_file():
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        _log(f"探针服务已停（pid {pid}）")
        _PID_FILE.unlink(missing_ok=True)
    elif _service_running(port):
        _log(f"WARN：:{port} 有服务但无 pid 文件，未自动停止（请人工确认端口归属）")


def _holder_command(interval: int, distro: str | None = None) -> list[str]:
    """常驻进程的命令：只让 WSL 里睡一个 `interval` 秒的进程，绝不带会关掉发行版的开关。"""
    head = ["wsl", "-d", distro] if distro else ["wsl"]
    return [*head, "bash", "-c", f"sleep {int(interval)}"]


def _holder_loop(
    *,
    stop_event: threading.Event,
    command: list[str],
    spawn: Callable[[list[str]], subprocess.Popen],
    sleep: Callable[[float], None] = time.sleep,
    on_spawn: Callable[[int], None] = lambda _ticks: None,
) -> int:
    """滚动续期：子进程一退出立刻再拉一个，不留 ≥15s 空闲窗；停止时收掉仍在跑的那个。

    `on_spawn` 在每次拉起后立即回调（运行中的取证要看到累计次数，不能只在退出后才有值）。
    """
    ticks = 0
    while not stop_event.is_set():
        proc = spawn(command)
        ticks += 1
        on_spawn(ticks)
        while proc.poll() is None:
            if stop_event.is_set():
                proc.terminate()
                return ticks
            sleep(1.0)
    return ticks


class _WslHolder:
    """把「WSL 空闲时回收整个发行版」这条机器侧前置变成脚本自带（PLAN-007 W6 归因更正）。

    尝试 1~3 的中途 `WinError 10054` + 剩余页 `MODEL_UNAVAILABLE` 里至少有一条腿是这个机制：
    最后一个 Linux 进程退出后发行版十几秒内关机，`mineru-api` 随之优雅停止（`RestartCount=0`、
    `ExitCode=0`），本机 `vmIdleTimeout` 又不生效。这里只做常驻保活，**不改任何业务参数**。
    `interval<=0` 表示显式关闭，只留可见 WARN，不静默兜底。
    """

    def __init__(self, *, interval: int, distro: str | None = None) -> None:
        self.interval = interval
        self.command = _holder_command(interval, distro)
        self.spawns = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.interval > 0

    def start(self) -> None:
        if not self.enabled:
            _log("WARN：WSL 常驻前置已关闭（`--wsl-holder 0`）——块间空闲期发行版可能被整机回收")
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        _log(f"WSL 常驻前置：{' '.join(self.command)}（滚动续期）")

    def _run(self) -> None:
        def spawn(command: list[str]) -> subprocess.Popen:
            return subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )

        self.spawns = _holder_loop(
            stop_event=self._stop,
            command=self.command,
            spawn=spawn,
            sleep=time.sleep,
            on_spawn=lambda ticks: setattr(self, "spawns", ticks),
        )

    def stop(self) -> dict:
        thread, self._thread = self._thread, None
        if thread is not None:
            self._stop.set()
            thread.join(timeout=30.0)
            _log(f"WSL 常驻前置已停（累计滚动 {self.spawns} 次）")
        return self.info()

    def info(self) -> dict:
        return {
            "enabled": self.enabled,
            "command": self.command,
            "spawns": self.spawns,
            "alive": bool(self._thread and self._thread.is_alive()),
        }


def _migrate_head() -> str:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(REPO),
        env=os.environ.copy(),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    settings = load_settings()
    with create_engine_for(settings, mysql_url(settings)).connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar())


def _ensure_source_pdf(business: Settings, settings: Settings) -> dict:
    """源 PDF 复制进探针根（真实 `raw/` 只读；副本落探针根，整目录可删）。"""
    with create_engine_for(business, mysql_url(business)).connect() as conn:
        row = conn.execute(
            text("SELECT domain_id, course_id, course_name, knowledge_id, title, part, display_title, "
                 "authors, file_path FROM af_books WHERE book_id=:b"),
            {"b": BOOK_ID},
        ).mappings().first()
    if row is None:
        raise SystemExit(f"ABORT：业务库无 {BOOK_ID} 行，取不到同步元数据")
    src = Path(business.data_dir) / row["file_path"]
    if not src.is_file():
        raise SystemExit(f"ABORT：源 PDF 缺失 {src}")
    dst = Path(settings.data_dir) / row["file_path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.is_file() or sha256_file(dst) != SOURCE_SHA256:
        shutil.copyfile(src, dst)
    digest = sha256_file(dst)
    if digest != SOURCE_SHA256:
        raise SystemExit(f"ABORT：探针根副本 sha256={digest}，与业务库登记不符")
    payload = dict(row)
    payload["authors"] = json.loads(payload.get("authors") or "[]")
    return payload


def _sync_and_ingest(api: str, meta: dict, settings: Settings) -> dict:
    synced = _HTTP.post(
        f"{api}/books/sync",
        json=[
            {
                "book_id": BOOK_ID,
                "domain_id": meta["domain_id"],
                "course_id": meta["course_id"],
                "course_name": meta["course_name"] or "",
                "knowledge_id": meta["knowledge_id"] or "",
                "title": meta["title"] or "",
                "part": meta["part"] or "",
                "display_title": meta["display_title"] or "",
                "authors": meta["authors"],
                "file_path": meta["file_path"],
            }
        ],
        timeout=120.0,
    )
    if synced.status_code != 200:
        raise SystemExit(f"ABORT：sync {synced.status_code} {synced.text[:300]}")
    book_dir = book_dir_for(Path(settings.data_dir), meta["domain_id"], meta["course_id"], BOOK_ID)
    png_count = len(list((book_dir / "pages").glob("p*.png"))) if (book_dir / "pages").is_dir() else 0
    result: dict = {"sync": synced.json(), "png_before": png_count}
    if png_count >= PAGE_COUNT:
        _log(f"ingest 已在位（页图 {png_count}/{PAGE_COUNT}），跳过重复渲染")
        return result
    _log(f"提交 ingest（{PAGE_COUNT} 页 150 DPI 渲染，分钟级）…")
    started = time.time()
    ingested = _HTTP.post(f"{api}/books/{BOOK_ID}/ingest", timeout=1800.0)
    result["ingest_seconds"] = round(time.time() - started, 1)
    if ingested.status_code != 200:
        raise SystemExit(f"ABORT：ingest {ingested.status_code} {ingested.text[:300]}")
    result["ingest"] = ingested.json()
    result["png_after"] = len(list((book_dir / "pages").glob("p*.png")))
    _log(f"ingest 完成：页图 {result['png_after']}/{PAGE_COUNT}，{result['ingest_seconds']}s")
    return result


def _reload_samples(job_id: str) -> list[dict]:
    """`--evidence-only` 时复用当轮已留档的进度采样（S2 判据不因补取证而失真）。"""
    path = PROBE_DIR / "progress_samples.jsonl"
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("job_id") == job_id:
            row.pop("job_id", None)
            out.append(row)
    return out


def _parsed_steps(samples: list[dict]) -> list[dict]:
    """进度增量（S1/S2 的真机侧证：一次跳一块 ≈ 窗口页数，且每跳都非零）。"""
    steps: list[dict] = []
    for prev, cur in zip(samples, samples[1:], strict=False):
        if cur["parsed"] != prev["parsed"]:
            steps.append({
                "at": cur["at"],
                "from": prev["parsed"],
                "to": cur["parsed"],
                "delta": cur["parsed"] - prev["parsed"],
            })
    return steps


def _submit_and_poll(api: str, settings: Settings, *, deadline_seconds: float) -> dict:
    created = _HTTP.post(f"{api}/parse-jobs", json={"book_id": BOOK_ID, "engine": "mineru"}, timeout=120.0)
    if created.status_code != 202:
        raise SystemExit(f"ABORT：提交解析 {created.status_code} {created.text[:300]}")
    job_id = created.json()["id"]
    _log(f"job={job_id} 已提交（全本 pages=null，按 {settings.parse_window_size} 页窗口串行）")
    started = time.time()
    deadline = started + deadline_seconds
    samples: list[dict] = []
    while time.time() < deadline:
        current = _HTTP.get(f"{api}/parse-jobs/{job_id}", timeout=30.0).json()
        sample = {"at": round(time.time() - started, 1), "status": current["status"], **current["progress"]}
        samples.append(sample)
        _append_jsonl("progress_samples.jsonl", {"job_id": job_id, **sample})
        if len(samples) > 1 and samples[-1]["parsed"] != samples[-2]["parsed"]:
            _log(f"+{sample['parsed'] - samples[-2]['parsed']} 页 → parsed={sample['parsed']}/"
                 f"{sample['total']}（{sample['at']}s，{round(time.time() - started)}s 累计）")
        elif len(samples) % 6 == 1:
            _log(f"parsed={sample['parsed']}/{sample['total']} status={sample['status']}（{sample['at']}s）")
        if current["status"] in TERMINAL:
            return {
                "job_id": job_id,
                "final": current,
                "samples": samples,
                "wall_seconds": round(time.time() - started, 1),
            }
        time.sleep(10.0)
    _log(f"ABORT：{deadline_seconds}s 内未达终态（服务保留，可 --evidence-only {job_id} 补取证）")
    return {"job_id": job_id, "final": None, "samples": samples, "wall_seconds": deadline_seconds}


def _quality_rollup(settings: Settings, job_id: str) -> dict:
    """全书质量对账：丢弃合计/丢弃字面值/空白页标记/页状态分布（全部来自 af_pages.quality）。"""
    engine = create_engine_for(settings, mysql_url(settings))
    with engine.connect() as conn:
        rows = list(
            conn.execute(
                select(AfPage.page_no, AfPage.status, AfPage.error, AfPage.quality)
                .where(AfPage.book_id == BOOK_ID, AfPage.job_id == job_id)
                .order_by(AfPage.page_no)
            )
        )
    by_status: dict[str, int] = {}
    dropped_total = 0
    dropped_types: set[str] = set()
    blank_pages: list[int] = []
    parsed_pages: list[int] = []
    for page_no, status, _error, quality in rows:
        by_status[status] = by_status.get(status, 0) + 1
        quality = quality or {}
        dropped_total += int(quality.get("dropped_block_count") or 0)
        dropped_types.update(quality.get("dropped_types") or [])
        if quality.get("blank_page"):
            blank_pages.append(page_no)
        if status == "parsed":
            parsed_pages.append(page_no)
    return {
        "row_total": len(rows),
        "by_status": by_status,
        "dropped_block_count_total": dropped_total,
        "dropped_types": sorted(dropped_types),
        "blank_flag_pages": blank_pages,
        "parsed_pages": parsed_pages,
        "missing_pages": [no for no in range(1, PAGE_COUNT + 1) if no not in set(parsed_pages)],
        "failed_detail": [
            {"page": page_no, "error": (error or "")[:160]}
            for page_no, status, error, _q in rows
            if status != "parsed"
        ],
    }


def _job_row(settings: Settings, job_id: str) -> dict:
    engine = create_engine_for(settings, mysql_url(settings))
    with engine.connect() as conn:
        # 注意：Core Connection 执行 `select(AfParseJob)` 返回的是**列元组**而非实体
        # （ORM 结果处理只在 Session 里生效），故此处显式列出列。
        job = conn.execute(
            select(
                AfParseJob.job_id,
                AfParseJob.status,
                AfParseJob.pages,
                AfParseJob.engine,
                AfParseJob.progress,
                AfParseJob.failed_pages,
                AfParseJob.error,
                AfParseJob.source_sha256,
                AfParseJob.page_count,
                AfParseJob.started_at,
                AfParseJob.finished_at,
            ).where(AfParseJob.job_id == job_id)
        ).mappings().first()
        book = conn.execute(
            text("SELECT page_count, pages_done, parse_status, active_job_id, last_parse_result "
                 "FROM af_books WHERE book_id=:b"),
            {"b": BOOK_ID},
        ).mappings().first()
        versions = conn.execute(
            text("SELECT job_id, status, page_count FROM af_parse_jobs WHERE book_id=:b ORDER BY created_at"),
            {"b": BOOK_ID},
        ).mappings().all()
    if job is None:
        return {"job": None, "book": dict(book) if book else None, "versions": [dict(v) for v in versions]}
    return {
        "job": {
            "job_id": job["job_id"],
            "status": job["status"],
            "pages": job["pages"],
            "engine": job["engine"],
            "progress": job["progress"],
            "failed_pages": job["failed_pages"],
            "error": (job["error"] or "")[:400],
            "source_sha256": job["source_sha256"],
            "page_count": job["page_count"],
            "started_at": job["started_at"],
            "finished_at": job["finished_at"],
            "elapsed_seconds": None
            if not (job["started_at"] and job["finished_at"])
            else round((job["finished_at"] - job["started_at"]).total_seconds(), 1),
        },
        "book": dict(book) if book else None,
        "versions": [dict(v) for v in versions],
    }


def _artifact_evidence(settings: Settings, meta: dict, job_id: str) -> dict:
    version_dir = version_dir_for(
        Path(settings.data_dir), meta["domain_id"], meta["course_id"], BOOK_ID, job_id
    )
    files = [p for p in version_dir.rglob("*") if p.is_file()] if version_dir.is_dir() else []
    root_pages = book_dir_for(
        Path(settings.data_dir), meta["domain_id"], meta["course_id"], BOOK_ID
    ) / "pages"
    per_page: dict[str, dict] = {}
    for page_no in [1, 3, 48, 98, 136, 162, 282, 317]:
        blocks_file = version_dir / "pages" / f"p{page_no:04d}.blocks.json"
        if not blocks_file.is_file():
            per_page[str(page_no)] = {"blocks_json": False}
            continue
        payload = json.loads(blocks_file.read_text(encoding="utf-8"))
        per_page[str(page_no)] = {
            "blocks_json": True,
            "block_count": len(payload.get("blocks") or []),
            "types": sorted({str(b.get("type")) for b in (payload.get("blocks") or [])}),
            "quality": payload.get("quality"),
            "md_bytes": (version_dir / "pages" / f"p{page_no:04d}.md").stat().st_size
            if (version_dir / "pages" / f"p{page_no:04d}.md").is_file()
            else 0,
        }
    return {
        "version_dir": str(version_dir),
        "file_count": len(files),
        "blocks_json_count": sum(1 for p in files if p.name.endswith(".blocks.json")),
        "md_count": sum(1 for p in files if p.suffix == ".md"),
        "chunks_json": (version_dir / "chunks.json").is_file(),
        "manifest_json": (version_dir / "manifest.json").is_file(),
        "root_view_png": len(list(root_pages.glob("p*.png"))) if root_pages.is_dir() else 0,
        "version_png": len(list((version_dir / "pages").glob("p*.png")))
        if (version_dir / "pages").is_dir()
        else 0,
        "sampled_pages": per_page,
    }


def _readback(api: str, job_id: str) -> dict:
    """读取面回显（生效版本 = 本次 job 才算真机链路打通）。"""
    out: dict = {}
    book = _HTTP.get(f"{api}/books/{BOOK_ID}", timeout=30.0)
    out["book_status"] = book.status_code
    if book.status_code == 200:
        body = book.json()
        out["active_job_id"] = body.get("active_job_id")
        out["pages_done"] = body.get("pages_done")
        out["last_parse_result"] = body.get("last_parse_result")
    for page_no in (1, 48, 98, 317):
        response = _HTTP.get(f"{api}/books/{BOOK_ID}/pages/{page_no}", timeout=60.0)
        blocks = (response.json().get("blocks") or {}) if response.status_code == 200 else {}
        out[f"page_{page_no}"] = {
            "status": response.status_code,
            "block_count": len(blocks.get("blocks") or []),
            "quality": blocks.get("quality"),
        }
    out["versions"] = _HTTP.get(f"{api}/books/{BOOK_ID}/versions", timeout=30.0).json()
    out["job_endpoint"] = _HTTP.get(f"{api}/parse-jobs/{job_id}", timeout=30.0).json()
    return out


def _judge(
    *, job_row: dict, quality: dict, samples: list[dict], blank_expected: list[int]
) -> dict[str, bool]:
    job = job_row.get("job") or {}
    book = job_row.get("book") or {}
    parses = [s["parsed"] for s in samples]
    return {
        "S3_completed": job.get("status") == JobStatus.COMPLETED.value,
        "S3_activated": bool(job.get("job_id")) and book.get("active_job_id") == job["job_id"],
        "S3_all_pages_parsed": quality["row_total"] == PAGE_COUNT and not quality["missing_pages"],
        "S3_blank_pages_counted": all(no in quality["parsed_pages"] for no in blank_expected)
        and sorted(no for no in quality["blank_flag_pages"] if no in blank_expected) == blank_expected,
        "S2_progress_monotonic_nonzero": len({v for v in parses if v}) > 1
        and all(b >= a for a, b in zip(parses, parses[1:], strict=False))
        and max(parses, default=0) > 0,
        "S5_blank_flag_exists": len(quality["blank_flag_pages"]) > 0,
        "dropped_zero": quality["dropped_block_count_total"] == 0 and not quality["dropped_types"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PROBE_PORT)
    parser.add_argument("--preflight-only", action="store_true", help="只到 ingest 为止，不提交解析")
    parser.add_argument("--evidence-only", metavar="JOB_ID", default=None, help="不解析，只对既有 job 补取证")
    parser.add_argument("--stop-service", action="store_true")
    parser.add_argument("--keep-service", action="store_true", help="取证后不停探针服务（排查用）")
    parser.add_argument("--model-deadline", type=float, default=600.0)
    parser.add_argument("--parse-deadline", type=float, default=3600.0)
    parser.add_argument("--window-size", type=int, default=None, help="覆盖 AXIOM_PARSE_WINDOW_SIZE")
    parser.add_argument(
        "--wsl-holder", type=int, default=300, metavar="SECONDS",
        help="WSL 常驻前置的滚动周期（本机 vmIdleTimeout 不生效；0 = 关闭并只留 WARN）",
    )
    parser.add_argument("--wsl-distro", default=None, help="指定发行版名（缺省用 wsl 默认发行版）")
    args = parser.parse_args()

    if args.stop_service:
        _service_stop(args.port)
        return 0

    holder = _WslHolder(interval=args.wsl_holder, distro=args.wsl_distro)
    holder.start()
    atexit.register(holder.stop)  # 早于任何 ABORT 分支起效：所有退出路径都收掉常驻进程

    business = load_settings()  # 覆盖前：业务口径，用于元数据与零写入快照
    before = _business_snapshot(business)
    settings = _probe_settings()
    if args.window_size:
        os.environ["AXIOM_PARSE_WINDOW_SIZE"] = str(args.window_size)
        settings = load_settings()
    gate_failures = _guard_probe_scope(settings)
    model = _wait_model(settings.ocr_model_url, deadline=args.model_deadline)
    _dump("S0_preflight.json", {
        "probe_db": settings.db_name,
        "probe_root": str(settings.data_dir),
        "window_size": settings.parse_window_size,
        "per_block_timeout": settings.ocr_timeout,
        "guard_failures": gate_failures,
        "wsl_holder": holder.info(),
        "model_health": {k: model.get("health", {}).get(k) for k in (
            "status", "queued_tasks", "processing_tasks", "completed_tasks",
            "processing_window_size", "max_concurrent_requests")} if model.get("ready") else {},
        "model_ready": model.get("ready"),
        "business_before": before,
    })
    if gate_failures:
        _log(f"ABORT：探针口径闸门未过——{gate_failures}")
        return 4
    if not model.get("ready"):
        _log(f"ABORT：MinerU {settings.ocr_model_url} 在 {args.model_deadline}s 内未就绪")
        return 3
    if model.get("busy"):
        _log(f"ABORT：窗口不排他（C-0），模型侧在飞/排队 {model['busy']}")
        return 3
    if before.get("queued_or_running"):
        _log(f"WARN：业务库存在非终态任务行 {before['queued_or_running']}（孤儿仅登记，不阻塞）")

    api = f"http://127.0.0.1:{args.port}/api/v1"
    meta = _ensure_source_pdf(business, settings)
    revision = _migrate_head()
    _log(f"alembic head（{settings.db_name}）= {revision}")
    proc = _service_start(args.port)
    prep = _sync_and_ingest(api, meta, settings)
    _dump("S1_prep.json", {
        "alembic_revision": revision,
        "expected_revision": EXPECTED_REVISION,
        "source_sha256": SOURCE_SHA256,
        "book_meta": {k: meta[k] for k in ("domain_id", "course_id", "file_path", "title")},
        **prep,
    })
    if revision != EXPECTED_REVISION:
        _log(f"ABORT：{settings.db_name} 迁移版本 {revision} ≠ {EXPECTED_REVISION}")
        if not args.keep_service:
            _service_stop(args.port)
        return 4

    if args.preflight_only:
        _log("PREFLIGHT-ONLY 完成：探针根/迁移/ingest 就位，未提交解析")
        if not args.keep_service:
            _service_stop(args.port)
        return 0

    if args.evidence_only:
        job_id = args.evidence_only
        _log(f"EVIDENCE-ONLY：跳过写路径，只对 job={job_id} 取证（**不再解析一次**）")
        poll = {
            "job_id": job_id,
            "samples": _reload_samples(job_id),
            "wall_seconds": None,
            "final": {"status": "evidence-only"},
        }
    else:
        poll = _submit_and_poll(api, settings, deadline_seconds=args.parse_deadline)
        job_id = poll["job_id"]
    if not poll.get("final"):
        _log("未取到终态：证据仍收集，但 S3 判据按不成立处理")

    job_row = _job_row(settings, job_id)
    quality = _quality_rollup(settings, job_id)
    artifacts = _artifact_evidence(settings, meta, job_id)
    readback = _readback(api, job_id) if _service_running(args.port) else {"skipped": "service not running"}
    verdict = _judge(
        job_row=job_row, quality=quality, samples=poll.get("samples") or [], blank_expected=BLANK_PAGES
    )
    after = _business_snapshot(business)
    unchanged = {
        "af_books_row": json.dumps(before.get("af_books"), sort_keys=True, default=str)
        == json.dumps(after.get("af_books"), sort_keys=True, default=str),
        "real_dir_files": (before.get("real_file_count") == after.get("real_file_count")
                           and before.get("real_total_bytes") == after.get("real_total_bytes")
                           and before.get("real_newest_mtime") == after.get("real_newest_mtime")),
    }
    verdict["business_zero_write"] = all(unchanged.values())
    _dump("S2_job.json", {"poll_summary": {
        "wall_seconds": poll.get("wall_seconds"),
        "sample_count": len(poll.get("samples") or []),
        "parsed_steps": _parsed_steps(poll.get("samples") or []),
        "final_progress": (poll.get("final") or {}).get("progress"),
    }, "job_row": job_row, "book": job_row.get("book"), "versions": job_row.get("versions")})
    _dump("S3_evidence.json", {
        "job_id": job_id,
        "quality_rollup": {k: v for k, v in quality.items() if k != "parsed_pages"},
        "parsed_page_count": len(quality["parsed_pages"]),
        "artifacts": artifacts,
        "readback": readback,
        "business_after": after,
        "business_unchanged": unchanged,
    })
    _dump("S4_verdict.json", {"criteria": verdict, "all_pass": all(verdict.values())})

    # summary.md：判据逐条 + 复现命令，收口时直接引用
    lines = [
        "# W6 b05 全本真机复测（PLAN-007 S3）",
        "",
        f"- 生成：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 库：`{settings.db_name}` ／ 数据根：`{settings.data_dir}` ／ 端口：`{args.port}`",
        f"- 窗口：`{settings.parse_window_size}` 页/块，单块 deadline `{settings.ocr_timeout}`s",
        f"- WSL 常驻前置：`{' '.join(holder.command)}`，enabled={holder.enabled}，滚动 {holder.spawns} 次",
        f"- job：`{job_id}`，状态 `{(job_row.get('job') or {}).get('status')}`，"
        f"挂钟 `{poll.get('wall_seconds')}`s，进度采样 `{len(poll.get('samples') or [])}` 次",
        "",
        "| 判据 | 结果 |",
        "| --- | --- |",
    ]
    for key, ok in verdict.items():
        lines.append(f"| {key} | {'PASS' if ok else 'FAIL'} |")
    lines += [
        "",
        f"- 页状态分布：`{quality['by_status']}`（行 {quality['row_total']}）",
        f"- 丢弃合计：`{quality['dropped_block_count_total']}`，字面值：`{quality['dropped_types']}`",
        f"- 空白标记页：`{quality['blank_flag_pages']}`（预期 {BLANK_PAGES}）",
        f"- 缺页/失败页：`{quality['failed_detail'][:8]}`",
        f"- 版本目录：{artifacts['file_count']} 文件，blocks.json {artifacts['blocks_json_count']}，"
        f"md {artifacts['md_count']}，chunks={artifacts['chunks_json']}，manifest={artifacts['manifest_json']}",
        "",
        "复现：`python scripts/tmp/live_wholebook_req086.py --evidence-only " + job_id + "`",
        "",
    ]
    (PROBE_DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    _log("wrote summary.md")

    if not args.keep_service:
        _service_stop(args.port)
    elif proc:
        _log(f"探针服务保留 pid={proc.pid}（{_PID_FILE}）")

    failed = [k for k, v in verdict.items() if not v]
    _log("PASS：S2/S3/S5 判据全达成" if not failed else "FAIL：" + "、".join(failed))
    return 0 if not failed else 5


if __name__ == "__main__":
    raise SystemExit(main())
