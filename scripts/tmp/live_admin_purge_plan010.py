"""W6 真机演练（PLAN-010 / ARCH-024）：管理面一键清除在 **`qed_test` + 独立探针数据根** 上跑通两形态。

用法：
    python scripts/tmp/live_admin_purge_plan010.py                  # 全流程（迁移→起服→播种→K1~K9）
    python scripts/tmp/live_admin_purge_plan010.py --keep-service   # 取证后不停探针服务（人工复跑用）
    python scripts/tmp/live_admin_purge_plan010.py --stop-service   # 只停探针服务（按本探针自己的 pid 文件）

**为什么本演练不碰模型服务**：被验对象是「清除」，不是「解析」。播种用直接 SQL + 手工目录，一次
MinerU 调用都不发起——既守住「同一时刻只有一个解析占用 MinerU」的裁决，也让演练可重复。

D 类操作规程（本脚本自证）：

1. **硬闸门**：任何写入前断言 `db_name == qed_test`、`data_dir` 就是本探针根、管理面已启用且
   token 已生成，否则退出 4——不信任「我记得设了环境变量」。
2. **业务侧零写入自证**：窗口前后各取一次业务库 `qed` 的 `af_*` 四表行数与真实 `dataset/parsed`
   整树指纹（文件数 + 总字节 + 最新 mtime），逐项比对，变了即判 FAIL。
3. **只经 HTTP 驱动**探针服务（端口 8913，独立 pid 文件，绝不碰 `logs/qed-axiom.pid` 与 8902 业务实例）。
4. **token 只在进程环境里**：`secrets.token_urlsafe` 现生成，不落 `.env`、不进留档与日志（留档只记长度）。
5. **探针根全新**：`data/probes/purge-20260923/root/`，不借用也不清除既有探针留档。
6. **播种只按 `book_id` 精确删**（合成书的三本），不 DROP、不 TRUNCATE、绝不 `DROP DATABASE`
   （`qed_test` 另有根仓库 `qt_*` 镜像表，ADR 0005 边界）。

判据（逐条留档 `K*.json`，任一 FAIL 或业务侧写入即退出码 5）：

| # | 判据 |
| --- | --- |
| K1 | 缺 token 与错 token 同为 401 |
| K2 | `dry_run` 缺省即演练：回清单与将删计数，磁盘与库**零改动** |
| K3 | 真删须逐字 `confirm`：缺省与小写都 400 且零删除 |
| K4 | 作用域内有 `queued` 任务 ⇒ 409 零删除（全项目与含该书的按本两形态都判） |
| K5 | 按书目清除：四表行（**含 `af_parse_jobs`**）与书目录一起走，他书（含其 `queued` 行）不受影响 |
| K6 | 库里脏 `domain_id='..'` ⇒ 400 整请求零删除（目录不存在也判，挡住全表扫尾） |
| K7 | 全项目清除：四表归零、书目录清空，**空父级不裁剪** |
| K8 | 幂等重跑：200、`deleted` 全 0、`failures` 空、`targets` 空 |
| K9 | 重启不带 `QED_ADMIN_ENABLED` ⇒ 路由不存在（404），且 `/openapi.json` 不收录该路径 |
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

for _stream in (sys.stdout, sys.stderr):  # 中文日志在 GBK 控制台下会抛 UnicodeEncodeError
    _stream.reconfigure(encoding="utf-8", errors="replace")

from axiom_flow.config import Settings, load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url, utc_now  # noqa: E402
from axiom_flow.db.models import AfBlockEdit, AfBook, AfPage, AfParseJob  # noqa: E402
from axiom_flow.schemas import SAFE_SEGMENT_PATTERN  # noqa: E402

PROBE_DB_NAME = "qed_test"
PROBE_DIR = REPO / "data" / "probes" / "purge-20260923"
PROBE_ROOT = PROBE_DIR / "root"
PROBE_PORT = 8913
EXPECTED_REVISION = "20260921_0001"
DOMAIN = "probe"
COURSE = "purge-w6"
BOOK_1 = "purge-probe-01"  # 两版本 + 编辑：按本清除的主用例
BOOK_2 = "purge-probe-02"  # 脏 domain_id 用例的宿主
BOOK_3 = "purge-probe-03"  # 只挂一条 queued：在飞拒删用例
BOOKS = (BOOK_1, BOOK_2, BOOK_3)
JOB_A = "w6jobaaaaaaaaaaaaaaaaaaaaaaaa1"  # BOOK_1 completed（生效版本）
JOB_B = "w6jobbbbbbbbbbbbbbbbbbbbbbbb2"  # BOOK_1 interrupted（历史版本）
JOB_C = "w6jobcccccccccccccccccccccc3"  # BOOK_2 partial
JOB_Q = "w6jobqqqqqqqqqqqqqqqqqqqqqq4"  # BOOK_3 queued（在飞）
JOBS = {JOB_A: (BOOK_1, "completed"), JOB_B: (BOOK_1, "interrupted"),
        JOB_C: (BOOK_2, "partial"), JOB_Q: (BOOK_3, "queued")}
PURGE_URL = "/api/v1/admin/purge"
PURGE_PATH = "/admin/purge"  # 相对 `/api/v1` 的请求路径（拼错前缀会把管理面判成「不存在」）
CONFIRM = "PURGE"
TOKEN_HEADER = "X-QED-Admin-Token"
TABLE_MODELS = {"af_books": AfBook, "af_parse_jobs": AfParseJob, "af_pages": AfPage,
               "af_block_edits": AfBlockEdit}
# 按本清除用例的期望计数（合成书 BOOK_1 恰好两版本 / 三页行 / 一编辑行）
EXPECTED_BOOK_1 = {"af_books": 1, "af_parse_jobs": 2, "af_pages": 3, "af_block_edits": 1, "directories": 1}
_ADMIN_TOKEN = secrets.token_urlsafe(24)

# 回环一律直连：Windows 注册表代理不豁免 127.0.0.1，走代理会把好环境判成 502（operations.md 不变式）
_HTTP = httpx.Client(trust_env=False, timeout=60.0)
_PID_FILE = PROBE_DIR / f"service-{PROBE_PORT}.pid"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _dump(name: str, payload: object) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    (PROBE_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _log(f"wrote {name}")


def _probe_settings() -> Settings:
    """注入真实环境变量（优先级：真实 env > `.env`，故**不改 `.env`**，退出即回业务口径）。"""
    os.environ["QED_DB_NAME"] = PROBE_DB_NAME
    os.environ["QED_DATA_ROOT"] = str(PROBE_ROOT)
    os.environ["QED_ADMIN_ENABLED"] = "1"
    os.environ["QED_ADMIN_TOKEN"] = _ADMIN_TOKEN
    return load_settings()


def _guard_probe_scope(settings: Settings) -> list[str]:
    """硬闸门：库名、数据根、管理面开关与 token 必须都在探针口径内。"""
    failures = []
    if settings.db_name != PROBE_DB_NAME:
        failures.append(f"db_name={settings.db_name}，期望 {PROBE_DB_NAME}")
    if Path(settings.data_dir).resolve() != PROBE_ROOT.resolve():
        failures.append(f"data_dir={Path(settings.data_dir).resolve()}，期望 {PROBE_ROOT.resolve()}")
    if not settings.admin_enabled:
        failures.append("admin_enabled=False，探针需要显式启用管理面")
    if not settings.admin_token:
        failures.append("admin_token 为空（fail-closed 会拒绝起服）")
    for segment in (DOMAIN, COURSE, *BOOKS):
        if not re.match(SAFE_SEGMENT_PATTERN, segment):
            failures.append(f"合成路径段不安全：{segment}")
    return failures


def _business_snapshot(business: Settings) -> dict:
    """业务库四表行数 + 真实 `dataset/parsed` 整树指纹（只读，用于零写入自证）。"""
    out: dict = {"db_name": business.db_name, "data_root": str(business.data_dir)}
    try:
        with create_engine_for(business, mysql_url(business)).connect() as conn:
            out["table_counts"] = {
                name: int(conn.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar() or 0)
                for name in TABLE_MODELS
            }
            out["non_terminal_jobs"] = [
                dict(row._mapping)
                for row in conn.execute(
                    text("SELECT job_id, book_id, status FROM af_parse_jobs "
                         "WHERE status IN ('queued','running')")
                )
            ]
    except Exception as exc:  # noqa: BLE001 - 业务库不可达不阻塞探针，登记为取证事实
        out["error"] = str(exc)[:200]
    parsed = Path(business.data_dir) / "parsed"
    files = [p for p in parsed.rglob("*") if p.is_file()] if parsed.is_dir() else []
    out["parsed_exists"] = parsed.is_dir()
    out["parsed_file_count"] = len(files)
    out["parsed_total_bytes"] = sum(p.stat().st_size for p in files)
    out["parsed_newest_mtime"] = max((p.stat().st_mtime for p in files), default=0)
    return out


def _sessions(settings: Settings) -> sessionmaker:
    return sessionmaker(bind=create_engine_for(settings, mysql_url(settings)), future=True)


def _migrate_head(settings: Settings) -> str:
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
    with _sessions(settings)() as session:
        return str(session.execute(text("SELECT version_num FROM alembic_version")).scalar())


def _purge_probe_rows(settings: Settings) -> None:
    """播种前只按合成 `book_id` 精确清探针残留行（DML，不 DROP、不 TRUNCATE）。"""
    with _sessions(settings)() as session:
        for model in TABLE_MODELS.values():
            session.execute(delete(model).where(model.book_id.in_(list(BOOKS))))
        session.commit()


def _probe_counts(settings: Settings) -> dict:
    with _sessions(settings)() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in TABLE_MODELS.items()
        }


def _expected_all_counts(settings: Settings, dirs_state: dict) -> dict:
    """全项目形态的期望计数＝**整表**行数 + 探针根下的书目录数。

    `qed_test` 是共享测试 schema，另有前几轮演练留下的书目行（镜像自 `qt_books`）；全项目清除按
    契约就是连它们一起清（这正是 S 判据要证的能力），所以期望值只能实测，不能写死。
    """
    return {**_probe_counts(settings), "directories": len(dirs_state["book_dirs"])}


def _backup_probe_tables(settings: Settings) -> dict:
    """清除类操作的先手备份：四表整表导出为 JSON（runbook 第 3 步的真机执行）。"""
    with _sessions(settings)() as session:
        rows = {
            "af_books": [
                {c.name: getattr(r, c.name) for c in AfBook.__table__.columns}
                for r in session.scalars(select(AfBook))
            ],
            "af_parse_jobs": [
                {c.name: getattr(r, c.name) for c in AfParseJob.__table__.columns}
                for r in session.scalars(select(AfParseJob))
            ],
            "af_pages": [
                {c.name: getattr(r, c.name) for c in AfPage.__table__.columns}
                for r in session.scalars(select(AfPage))
            ],
            "af_block_edits": [
                {c.name: getattr(r, c.name) for c in AfBlockEdit.__table__.columns}
                for r in session.scalars(select(AfBlockEdit))
            ],
        }
    _dump("backup_af_tables.json", rows)
    return {name: len(entries) for name, entries in rows.items()}


def _book_dir(book_id: str) -> Path:
    return PROBE_ROOT / "parsed" / DOMAIN / COURSE / book_id


def _probe_dir_state() -> dict:
    parsed = PROBE_ROOT / "parsed"
    book_dirs = sorted(p.name for p in parsed.glob(f"{DOMAIN}/{COURSE}/*") if p.is_dir())
    files = [p for p in parsed.rglob("*") if p.is_file()] if parsed.is_dir() else []
    return {
        "book_dirs": book_dirs,
        "file_count": len(files),
        "parents_kept": (parsed / DOMAIN / COURSE).is_dir(),
    }


def _seed_disk() -> None:
    for book_id, versions in ((BOOK_1, (JOB_A, JOB_B)), (BOOK_2, (JOB_C,)), (BOOK_3, ())):
        book_dir = _book_dir(book_id)
        (book_dir / "pages").mkdir(parents=True, exist_ok=True)
        (book_dir / "book.json").write_text(json.dumps({"book_id": book_id}), encoding="utf-8")
        (book_dir / "pages" / "page_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        for job_id in versions:
            version = book_dir / "versions" / job_id
            version.mkdir(parents=True, exist_ok=True)
            (version / "blocks.json").write_text("[]", encoding="utf-8")
            (version / "page_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"1" * 32)


def _seed_rows(settings: Settings) -> None:
    now = utc_now()
    active = {BOOK_1: JOB_A, BOOK_2: JOB_C, BOOK_3: None}
    with _sessions(settings)() as session:
        for book_id in BOOKS:
            session.add(
                AfBook(
                    book_id=book_id,
                    domain_id=DOMAIN,
                    course_id=COURSE,
                    course_name="PLAN-010 W6 探针课程",
                    knowledge_id=book_id,
                    title=f"合成书 {book_id}",
                    display_title=f"合成书 {book_id}",
                    authors=[],
                    file_path=f"raw/{DOMAIN}/{COURSE}/{book_id}.pdf",
                    page_count=2,
                    ingest_status="ingested",
                    parse_status="pending",
                    last_parse_result="" if book_id == BOOK_3 else "completed",
                    pages_done=0 if book_id == BOOK_3 else 2,
                    active_job_id=active[book_id],
                    synced_at=now,
                    updated_at=now,
                )
            )
        for job_id, (book_id, status) in JOBS.items():
            session.add(_job_row(job_id, book_id, status, now))
        session.add_all([
            _page_row(BOOK_1, "legacy", 1),
            _page_row(BOOK_1, JOB_A, 1),
            _page_row(BOOK_1, JOB_A, 2),
            _page_row(BOOK_2, JOB_C, 1),
            _edit_row(BOOK_1, JOB_A, 1, 0, now),
            _edit_row(BOOK_2, "legacy", 1, 0, now),
        ])
        session.commit()


def _job_row(job_id: str, book_id: str, status: str, now) -> AfParseJob:
    finished = status in {"completed", "partial", "interrupted", "failed"}
    return AfParseJob(
        job_id=job_id,
        book_id=book_id,
        pages=None,
        engine="mineru",
        status=status,
        progress={"parsed": 0},
        failed_pages=[],
        error="",
        source_sha256="0" * 64,
        page_count=2,
        created_at=now,
        started_at=None if status == "queued" else now,
        finished_at=now if finished else None,
    )


def _page_row(book_id: str, job_id: str, page_no: int) -> AfPage:
    return AfPage(
        book_id=book_id,
        job_id=job_id,
        page_no=page_no,
        status="parsed",
        source="mineru",
        quality={},
        error="",
        parsed_at=utc_now(),
    )


def _edit_row(book_id: str, job_id: str, page_no: int, block_index: int, now) -> AfBlockEdit:
    return AfBlockEdit(
        edit_id=f"w6edit-{book_id}-{job_id}-{page_no}-{block_index}",
        book_id=book_id,
        job_id=job_id,
        page_no=page_no,
        block_index=block_index,
        block_type="PARAGRAPH",
        verdict="correct",
        note="PLAN-010 W6 合成编辑",
        corrected_text="合成正文",
        corrected_bbox=None,
        edited_at=now,
        updated_at=now,
    )


def _service_running(port: int) -> bool:
    try:
        return _HTTP.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=3.0).status_code == 200
    except Exception:  # noqa: BLE001 - 未起服时连接被拒属预期
        return False


def _service_start(port: int, *, admin_enabled: bool) -> None:
    """起探针服务（独立端口 + 独立 pid 文件）；端口已被占用时按本探针的 pid 文件重启，开关由脚本定。"""
    if _service_running(port):
        _service_stop(port)
        time.sleep(2.0)
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    if not admin_enabled:  # 不删两行就没法证明「默认不挂载」是 404 而不是 401
        child_env.pop("QED_ADMIN_ENABLED", None)
        child_env.pop("QED_ADMIN_TOKEN", None)
    log_file = (PROBE_DIR / f"service-{port}.log").open("ab")  # noqa: SIM115 - 子进程继承句柄
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
            _log(f"探针服务就绪 :{port}（pid {proc.pid}，admin_enabled={admin_enabled}）")
            return
        time.sleep(1.0)
    _log(f"FAIL：探针服务 :{port} 60s 内未就绪，见 service-{port}.log")
    raise SystemExit(3)


def _service_stop(port: int) -> None:
    if _PID_FILE.is_file():
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        _log(f"探针服务已停（pid {pid}）")
        _PID_FILE.unlink(missing_ok=True)
    elif _service_running(port):
        _log(f"WARN：:{port} 有服务但无本探针 pid 文件，未自动停止（请人工确认端口归属）")


def _purge(api: str, payload: dict, *, token: str | None = _ADMIN_TOKEN) -> httpx.Response:
    headers = {TOKEN_HEADER: token} if token is not None else {}
    return _HTTP.post(f"{api}{PURGE_PATH}", json=payload, headers=headers, timeout=60.0)


def _real(payload: dict) -> dict:
    return {**payload, "dry_run": False, "confirm": CONFIRM}


def _case(name: str, ok: bool, note: str, evidence: dict) -> bool:
    _dump(name, {"ok": ok, "note": note, **evidence})
    _log(f"{'PASS' if ok else 'FAIL'} {name}：{note}")
    return ok


def run_cases(api: str, port: int, settings: Settings) -> list[bool]:
    results: list[bool] = []
    rows_before = _probe_counts(settings)
    dirs_before = _probe_dir_state()
    expected_all = _expected_all_counts(settings, dirs_before)
    foreign_books = sorted(
        book_id for book_id in dirs_before["book_dirs"] if book_id not in BOOKS
    )
    _dump("S2_scope_before.json", {"rows": rows_before, "dirs": dirs_before,
                                   "expected_all": expected_all, "foreign_book_dirs": foreign_books})
    # 自检：启用态下管理路径必须真在 app 里。否则「404」是探针拼错 URL，不是契约失败——别拿它当判据。
    spec = _HTTP.get(f"{api[: -len('/api/v1')]}/openapi.json", timeout=15.0)
    if PURGE_URL not in (spec.json().get("paths", {}) if spec.status_code == 200 else {}):
        _log(f"ABORT：启用态 OpenAPI 未收录 {PURGE_URL}（HTTP {spec.status_code}）——探针 URL 或服务装配有问题")
        raise SystemExit(4)

    no_token = _purge(api, {"scope": {"all": True}}, token=None)
    bad_token = _purge(api, {"scope": {"all": True}}, token="0123456789abcdef")
    results.append(
        _case(
            "K1_token",
            no_token.status_code == 401 and bad_token.status_code == 401,
            f"缺头 {no_token.status_code} / 错值 {bad_token.status_code}，期望 401/401",
            {"no_token": no_token.text[:200], "bad_token": bad_token.text[:200]},
        )
    )

    dry = _purge(api, {"scope": {"all": True}})
    dry_body = dry.json() if dry.status_code == 200 else {}
    results.append(
        _case(
            "K2_dry_run_default",
            dry.status_code == 200
            and dry_body.get("dry_run") is True
            and dry_body.get("deleted") == expected_all
            and sorted(dry_body.get("targets") or []) == sorted(
                f"parsed/{DOMAIN}/{COURSE}/{b}" for b in dirs_before["book_dirs"]
            )
            and _probe_counts(settings) == rows_before
            and _probe_dir_state() == dirs_before,
            f"演练态 deleted={dry_body.get('deleted')}，行数与目录零改动",
            {"status": dry.status_code, "body": dry_body, "rows": rows_before, "dirs": dirs_before},
        )
    )

    no_confirm = _purge(api, {"scope": {"all": True}, "dry_run": False})
    lower = _purge(api, {"scope": {"all": True}, "dry_run": False, "confirm": "purge"})
    results.append(
        _case(
            "K3_confirm",
            no_confirm.status_code == 400
            and lower.status_code == 400
            and _probe_counts(settings) == rows_before
            and _probe_dir_state() == dirs_before,
            f"缺 confirm {no_confirm.status_code} / 小写 {lower.status_code}，期望 400/400 且零删除",
            {"no_confirm": no_confirm.text[:200], "lower": lower.text[:200]},
        )
    )

    inflight_all = _purge(api, _real({"scope": {"all": True}}))
    inflight_book = _purge(api, _real({"scope": {"book_ids": [BOOK_3]}}))
    results.append(
        _case(
            "K4_in_flight_409",
            inflight_all.status_code == 409
            and inflight_book.status_code == 409
            and _probe_counts(settings) == rows_before,
            f"全项目 {inflight_all.status_code} / 含在飞书的按本 {inflight_book.status_code}，期望 409/409",
            {"all": inflight_all.text[:200], "book": inflight_book.text[:200]},
        )
    )

    after_book_1 = {
        "af_books": rows_before["af_books"] - 1,
        "af_parse_jobs": rows_before["af_parse_jobs"] - 2,
        "af_pages": rows_before["af_pages"] - 3,
        "af_block_edits": rows_before["af_block_edits"] - 1,
    }
    one = _purge(api, _real({"scope": {"book_ids": [BOOK_1]}}))
    one_body = one.json() if one.status_code == 200 else {}
    results.append(
        _case(
            "K5_book_scope",
            one.status_code == 200
            and one_body.get("deleted") == EXPECTED_BOOK_1
            and _probe_counts(settings) == after_book_1
            and not _book_dir(BOOK_1).exists()
            and _book_dir(BOOK_2).is_dir()
            and _book_dir(BOOK_3).is_dir()
            and one_body.get("failures") == [],
            f"按本 deleted={one_body.get('deleted')}；他书目录与其 queued 行不受影响",
            {"status": one.status_code, "body": one_body, "rows_after": _probe_counts(settings)},
        )
    )

    with _sessions(settings)() as session:
        dirty = session.get(AfBook, BOOK_2)
        assert dirty is not None
        dirty.domain_id = ".."  # 绕过 sync 校验的存量脏值形态
        session.commit()
    dirty_rows = _probe_counts(settings)
    dirty_dirs = _probe_dir_state()
    dirty_resp = _purge(api, _real({"scope": {"all": True}}))
    with _sessions(settings)() as session:
        restored = session.get(AfBook, BOOK_2)
        assert restored is not None
        restored.domain_id = DOMAIN
        session.commit()
    results.append(
        _case(
            "K6_dirty_row_400",
            dirty_resp.status_code == 400
            and _probe_counts(settings) == dirty_rows
            and _probe_dir_state() == dirty_dirs,
            f"脏 domain_id ⇒ {dirty_resp.status_code} 整请求零删除",
            {"detail": dirty_resp.text[:200], "rows": dirty_rows},
        )
    )

    with _sessions(settings)() as session:
        # 在飞行改判终态（等价于服务重启后的启动期归一）：K7 要证的是「终态不拦」
        session.execute(
            update(AfParseJob).where(AfParseJob.job_id == JOB_Q).values(status="interrupted")
        )
        session.commit()
    all_resp = _purge(api, _real({"scope": {"all": True}}))
    all_body = all_resp.json() if all_resp.status_code == 200 else {}
    state = _probe_dir_state()
    results.append(
        _case(
            "K7_all_scope",
            all_resp.status_code == 200
            and _probe_counts(settings) == dict.fromkeys(TABLE_MODELS, 0)
            and state["book_dirs"] == []
            and state["parents_kept"]
            and all_body.get("failures") == [],
            f"全项目 deleted={all_body.get('deleted')}；四表归零、书目录清空、空父级保留",
            {"status": all_resp.status_code, "body": all_body, "dirs": state},
        )
    )

    again = _purge(api, _real({"scope": {"all": True}}))
    again_body = again.json() if again.status_code == 200 else {}
    results.append(
        _case(
            "K8_idempotent",
            again.status_code == 200
            and again_body.get("targets") == []
            and again_body.get("failures") == []
            and again_body.get("deleted") == {**dict.fromkeys(TABLE_MODELS, 0), "directories": 0},
            f"幂等重跑 deleted={again_body.get('deleted')}、failures={again_body.get('failures')}",
            {"status": again.status_code, "body": again_body},
        )
    )

    _service_stop(port)
    _service_start(port, admin_enabled=False)
    gone = _purge(api, {"scope": {"all": True}})
    try:
        spec = _HTTP.get(f"{api[: -len('/api/v1')]}/openapi.json", timeout=15.0)
        paths = list(spec.json().get("paths", {})) if spec.status_code == 200 else []
    except Exception as exc:  # noqa: BLE001 - 取不到 spec 记成事实，不抛
        paths = [f"<openapi 取回失败：{type(exc).__name__}>"]
    results.append(
        _case(
            "K9_not_mounted_by_default",
            gone.status_code == 404 and PURGE_URL not in paths,
            f"未启用时 {PURGE_URL} ⇒ {gone.status_code}（期望 404）；OpenAPI 收录={PURGE_URL in paths}",
            {"status": gone.status_code, "body": gone.text[:200],
             "admin_paths": [p for p in paths if "admin" in p]},
        )
    )
    return results


def _write_summary(settings: Settings, results: list[bool], before: dict, after: dict) -> None:
    names = ["K1 token", "K2 dry-run 缺省", "K3 双确认", "K4 在飞 409", "K5 按本清除",
             "K6 脏路径 400", "K7 全项目清除", "K8 幂等重跑", "K9 默认不挂载"]
    lines = [
        "# PLAN-010 W6 真机演练留档（管理面一键清除）",
        "",
        f"- 库：`{settings.db_name}`（业务库 `qed` + 真实 `dataset/parsed` 零写入自证："
        f"**{'通过' if after == before else '失败'}**）",
        f"- 数据根：`{settings.data_dir}`（新建，整目录可删；未借用其他探针留档）",
        "- 模型服务：本轮**零调用**（清除不依赖解析，播种用直接 SQL + 手工目录）",
        f"- 管理 token：只存在于探针子进程环境，长度 {len(_ADMIN_TOKEN)}，未落 `.env`/日志/留档",
        "- 清除前先手备份：`backup_af_tables.json`（`qed_test` 四表整表导出，runbook 第 3 步）。"
        "K7 的全项目形态按契约清**整表**，故本探针窗内前几轮遗留的镜像书目行也一并清掉——"
        "`qed_test` 是本仓库自有测试 schema（ADR 0005），业务库 `qed` 不受影响",
        "",
        "| # | 判据 | 结果 |",
        "| --- | --- | --- |",
    ]
    lines += [f"| K{i} | {name} | {'PASS' if ok else 'FAIL'} |"
              for i, (name, ok) in enumerate(zip(names, results, strict=True), start=1)]
    lines += [
        "",
        f"业务侧指纹（前）：`{json.dumps(before, ensure_ascii=False, default=str)}`",
        "",
        f"业务侧指纹（后）：`{json.dumps(after, ensure_ascii=False, default=str)}`",
        "",
    ]
    (PROBE_DIR / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    _log(f"summary.md 写出；all_pass={all(results)}，业务零写入={after == before}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=PROBE_PORT)
    parser.add_argument("--stop-service", action="store_true")
    parser.add_argument("--keep-service", action="store_true", help="取证后不停探针服务（人工复跑用）")
    args = parser.parse_args()

    if args.stop_service:
        _service_stop(args.port)
        return 0

    business = load_settings()  # 覆盖前：业务口径，用于零写入快照
    before = _business_snapshot(business)
    settings = _probe_settings()
    gate = _guard_probe_scope(settings)
    _dump("S0_preflight.json", {
        "probe_db": settings.db_name,
        "probe_root": str(settings.data_dir),
        "admin_enabled": settings.admin_enabled,
        "admin_token_length": len(settings.admin_token),  # 只记长度，绝不落值
        "alembic_expected": EXPECTED_REVISION,
        "guard_failures": gate,
        "business_before": before,
    })
    if gate:
        _log(f"ABORT：探针口径闸门未过——{gate}")
        return 4

    revision = _migrate_head(settings)
    _log(f"alembic head（{settings.db_name}）= {revision}")
    if revision != EXPECTED_REVISION:
        _log(f"ABORT：{settings.db_name} 迁移版本 {revision} ≠ {EXPECTED_REVISION}")
        return 4

    api = f"http://127.0.0.1:{args.port}/api/v1"
    _purge_probe_rows(settings)
    # 先起服再播种：lifespan 的启动期归一会把 queued 改判 interrupted，在飞用例必须活在归一之后
    _service_start(args.port, admin_enabled=True)
    rows_pre_seed = _probe_counts(settings)
    _seed_disk()
    _seed_rows(settings)
    seeded_delta = {
        name: count - rows_pre_seed[name] for name, count in _probe_counts(settings).items()
    }
    _dump("S1_seed.json", {
        "pre_existing_rows": rows_pre_seed,
        "seeded_delta": seeded_delta,
        "expected_book_1": EXPECTED_BOOK_1,
        "jobs": {job_id: {"book_id": book_id, "status": status} for job_id, (book_id, status) in JOBS.items()},
        "counts": _probe_counts(settings),
        "dirs": _probe_dir_state(),
    })
    expected_delta = {
        "af_books": 3, "af_parse_jobs": 4, "af_pages": 4, "af_block_edits": 2,
    }
    if seeded_delta != expected_delta:
        _log(f"ABORT：播种增量 {seeded_delta} ≠ 期望 {expected_delta}（探针库不干净或契约变化）")
        _service_stop(args.port)
        return 4
    backup_counts = _backup_probe_tables(settings)  # runbook 第 3 步：清除前先整表导出
    _log(f"af_* 整表备份已导出（runbook 第 3 步）：{backup_counts}")

    results = run_cases(api, args.port, settings)
    if args.keep_service:
        _log("--keep-service：探针服务留在台上，复跑用 --stop-service 收掉")
    else:
        _service_stop(args.port)

    after = _business_snapshot(business)
    _write_summary(settings, results, before, after)
    _dump("S2_verdicts.json", {
        "cases": {f"K{i}": bool(ok) for i, ok in enumerate(results, start=1)},
        "all_pass": all(results),
        "business_zero_write": after == before,
    })
    if not all(results) or after != before:
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
