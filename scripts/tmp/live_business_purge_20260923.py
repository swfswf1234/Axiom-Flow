"""业务侧一键清除真机执行（PLAN-010 交付后的首次真实使用，D 类操作按 runbook 逐步）。

用法（每阶段单独调用，用户逐次批准）：
    python scripts/tmp/live_business_purge_20260923.py --phase preflight   # 只读盘点 + 起服 + dry-run 留清单
    python scripts/tmp/live_business_purge_20260923.py --phase purge --i-hold-approval
    python scripts/tmp/live_business_purge_20260923.py --phase resync --i-hold-approval
    python scripts/tmp/live_business_purge_20260923.py --phase stop

作用对象是**业务库 `qed` 与真实数据根 `D:\\coding\\QED-Engine\\dataset`**——与 W6 演练（`qed_test` +
探针根）相反，本脚本没有「零写入」保险，只有下列闸门：

1. **口径闸门**：`db_name == qed` 且 `data_dir` 就是真实数据根；不满足直接退出 4。
2. **清单先行**：`purge` 阶段必须已存在 `dry_run.json` 且其 `targets` 非空，删除集只认演练清单。
3. **显式持有批准**：写操作阶段必须带 `--i-hold-approval`（用户对本次作用域的口头批准记录在
   `docs/trackers/project-status.md` 本轮条目）。
4. **先手备份**：任何删除前把 `af_*` 四表整表导出到留档目录（runbook 第 3 步）。
5. **token 只在子进程环境**：现生成、不落 `.env`/日志/留档（留档只记长度）。
6. **独立端口与 pid 文件**：8914，绝不碰业务实例的 `logs/qed-axiom.pid`、8902、8903。

源 PDF 与 `parsed/` 之外的数据根内容不在清除作用域内（端点只删 `parsed/` 下的书目录与 `af_*` 行）。
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
for _stream in (sys.stdout, sys.stderr):
    _stream.reconfigure(encoding="utf-8", errors="replace")

from axiom_flow.config import Settings, load_settings  # noqa: E402
from axiom_flow.db.engine import create_engine_for, mysql_url  # noqa: E402
from axiom_flow.db.models import AfBlockEdit, AfBook, AfPage, AfParseJob  # noqa: E402

BUSINESS_DB = "qed"
BUSINESS_ROOT = Path("D:/coding/QED-Engine/dataset")
EXPECTED_REVISION = "20260921_0001"
PORT = 8914
EVIDENCE = REPO / "data" / "probes" / "purge-business-20260923"
PURGE_URL = "/api/v1/admin/purge"
PURGE_PATH = "/admin/purge"
CONFIRM = "PURGE"
TOKEN_HEADER = "X-QED-Admin-Token"
TABLE_MODELS = {"af_books": AfBook, "af_parse_jobs": AfParseJob, "af_pages": AfPage,
                "af_block_edits": AfBlockEdit}
_ADMIN_TOKEN = os.environ.get("QED_ADMIN_TOKEN") or secrets.token_urlsafe(24)
_HTTP = httpx.Client(trust_env=False, timeout=600.0)
_PID_FILE = EVIDENCE / f"service-{PORT}.pid"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _dump(name: str, payload: object) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    _log(f"wrote {name}")


def _load(name: str) -> dict:
    path = EVIDENCE / name
    if not path.is_file():
        _log(f"ABORT：缺少 {name}（该阶段依赖上一阶段留档）")
        raise SystemExit(4)
    return json.loads(path.read_text(encoding="utf-8"))


def _business_settings() -> Settings:
    os.environ["QED_DB_NAME"] = BUSINESS_DB
    os.environ["QED_DATA_ROOT"] = str(BUSINESS_ROOT)
    os.environ["QED_ADMIN_ENABLED"] = "1"
    os.environ["QED_ADMIN_TOKEN"] = _ADMIN_TOKEN
    return load_settings()


def _gate(settings: Settings) -> list[str]:
    failures = []
    if settings.db_name != BUSINESS_DB:
        failures.append(f"db_name={settings.db_name}，期望 {BUSINESS_DB}")
    if Path(settings.data_dir).resolve() != BUSINESS_ROOT.resolve():
        failures.append(f"data_dir={Path(settings.data_dir).resolve()}，期望 {BUSINESS_ROOT.resolve()}")
    if not settings.admin_enabled:
        failures.append("admin_enabled=False")
    if not settings.admin_token:
        failures.append("admin_token 为空")
    if not (Path(settings.data_dir) / "parsed").is_dir():
        failures.append("数据根下无 parsed/，作用域不明")
    return failures


def _sessions(settings: Settings) -> sessionmaker:
    return sessionmaker(bind=create_engine_for(settings, mysql_url(settings)), future=True)


def _counts(settings: Settings) -> dict:
    with _sessions(settings)() as session:
        return {
            name: int(session.scalar(select(func.count()).select_from(model)) or 0)
            for name, model in TABLE_MODELS.items()
        }


def _revision(settings: Settings) -> str:
    with _sessions(settings)() as session:
        return str(
            session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        )


def _disk_state(settings: Settings) -> dict:
    """书目录识别与端点同一套事实源（`_iter_book_dirs`），避免「脚本以为干净、端点不认」。"""
    from axiom_flow.api.admin import _iter_book_dirs  # noqa: PLC0415 - 取证脚本复用实现

    parsed = Path(settings.data_dir) / "parsed"
    book_dirs = sorted(f"parsed/{p.relative_to(parsed).as_posix()}" for p in _iter_book_dirs(parsed))
    files = [p for p in parsed.rglob("*") if p.is_file()]
    return {
        "book_dirs": book_dirs,
        "file_count": len(files),
        "total_bytes": sum(p.stat().st_size for p in files),
        "newest_mtime": max((p.stat().st_mtime for p in files), default=0),
    }


def _backup_tables(settings: Settings) -> dict:
    with _sessions(settings)() as session:
        rows = {
            "af_books": [
                {c.name: getattr(r, c.name) for c in AfBook.__table__.columns}
                for r in session.scalars(select(AfBook).order_by(AfBook.book_id)
            )],
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


def _service_running() -> bool:
    try:
        return _HTTP.get(f"http://127.0.0.1:{PORT}/api/v1/health", timeout=3.0).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def _service_start() -> None:
    if _service_running():
        return
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env["QED_DB_NAME"] = BUSINESS_DB
    child_env["QED_DATA_ROOT"] = str(BUSINESS_ROOT)
    child_env["QED_ADMIN_ENABLED"] = "1"
    child_env["QED_ADMIN_TOKEN"] = _ADMIN_TOKEN
    log_file = (EVIDENCE / f"service-{PORT}.log").open("ab")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "axiom_flow.api.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(REPO), stdout=log_file, stderr=subprocess.STDOUT, env=child_env,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    _PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    deadline = time.time() + 60.0
    while time.time() < deadline:
        if _service_running():
            _log(f"业务清除实例就绪 :{PORT}（pid {proc.pid}）")
            return
        time.sleep(1.0)
    _log(f"FAIL：:{PORT} 60s 未就绪，见 service-{PORT}.log")
    raise SystemExit(3)


def _service_stop() -> None:
    if _PID_FILE.is_file():
        pid = int(_PID_FILE.read_text(encoding="utf-8").strip())
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
        _log(f"服务已停（pid {pid}）")
        _PID_FILE.unlink(missing_ok=True)
    elif _service_running():
        _log(f"WARN：:{PORT} 有服务但无本脚本 pid 文件，未自动停止")


def _purge(payload: dict) -> httpx.Response:
    return _HTTP.post(
        f"http://127.0.0.1:{PORT}{PURGE_URL}",
        json=payload, headers={TOKEN_HEADER: _ADMIN_TOKEN}, timeout=600.0
    )


def _preflight(settings: Settings) -> int:
    gate = _gate(settings)
    if gate:
        _log(f"ABORT：业务口径闸门未过——{gate}")
        return 4
    revision = _revision(settings)
    if revision != EXPECTED_REVISION:
        _log(f"ABORT：{settings.db_name} 迁移版本 {revision} ≠ {EXPECTED_REVISION}")
        return 4
    rows = _counts(settings)
    dirs = _disk_state(settings)
    with _sessions(settings)() as session:
        in_flight = [
            dict(r._mapping)
            for r in session.execute(text(
                "SELECT job_id, book_id, status FROM af_parse_jobs "
                "WHERE status IN ('queued','running') ORDER BY created_at"
            ))
        ]
    _dump("S0_preflight.json", {
        "db_name": settings.db_name, "data_root": str(settings.data_dir),
        "alembic": revision, "admin_token_length": len(settings.admin_token),
        "rows": rows, "disk": dirs, "in_flight": in_flight,
    })
    _log(f"行数={rows} 书目录={len(dirs['book_dirs'])} {dirs['file_count']} 文件 "
         f"{dirs['total_bytes']/1e9:.2f} GB")
    _service_start()
    dry = _purge({"scope": {"all": True}})  # dry_run 缺省即演练
    body = dry.json() if dry.status_code == 200 else {}
    _dump("dry_run.json", {"status": dry.status_code, "body": body})
    if dry.status_code != 200:
        _log(f"ABORT：dry-run {dry.status_code} {dry.text[:200]}")
        return 5
    unchanged = _counts(settings) == rows and _disk_state(settings) == dirs
    _log(f"dry-run deleted 预测={body.get('deleted')} targets={len(body.get('targets') or [])} "
         f"零改动自证={unchanged}")
    _service_stop()
    return 0 if unchanged else 5


def _purge_phase(settings: Settings, approved: bool) -> int:
    if not approved:
        _log("ABORT：写操作阶段必须带 --i-hold-approval")
        return 4
    gate = _gate(settings)
    if gate:
        _log(f"ABORT：业务口径闸门未过——{gate}")
        return 4
    dry_body = _load("dry_run.json")["body"]
    expected_targets = sorted(dry_body.get("targets") or [])
    if not expected_targets:
        _log("ABORT：dry-run 清单为空，无删除集可校验")
        return 4
    rows = _counts(settings)
    backup_counts = _backup_tables(settings)
    _log(f"af_* 整表备份已导出：{backup_counts}")
    _service_start()
    resp = _purge({"scope": {"all": True}, "dry_run": False, "confirm": CONFIRM})
    body = resp.json() if resp.status_code == 200 else {}
    after_rows = _counts(settings)
    after_dirs = _disk_state(settings)
    ok = (
        resp.status_code == 200
        and sorted(body.get("targets") or []) == expected_targets
        and body.get("failures") == []
        and after_rows == dict.fromkeys(TABLE_MODELS, 0)
        and after_dirs["book_dirs"] == []
    )
    _dump("purge_result.json", {
        "ok": ok, "status": resp.status_code, "body": body,
        "rows_before": rows, "rows_after": after_rows,
        "disk_before": {k: v for k, v in _load("S0_preflight.json")["disk"].items()},
        "disk_after": after_dirs, "backup_counts": backup_counts,
    })
    _log(f"{'PASS' if ok else 'FAIL'} 真删 status={resp.status_code} deleted={body.get('deleted')} "
         f"failures={body.get('failures')} 行数归零={after_rows} 书目录={len(after_dirs['book_dirs'])}")
    _service_stop()
    return 0 if ok else 5


def _resync_phase(settings: Settings, approved: bool) -> int:
    if not approved:
        _log("ABORT：写操作阶段必须带 --i-hold-approval")
        return 4
    backup = _load("backup_af_tables.json")["af_books"]
    items = [
        {k: row.get(k) for k in (
            "book_id", "domain_id", "course_id", "course_name", "knowledge_id",
            "title", "part", "display_title", "authors", "file_path"
        )}
        for row in backup
    ]
    for item in items:
        item["authors"] = item["authors"] or []
    _service_start()
    resp = _HTTP.post(
        f"http://127.0.0.1:{PORT}/api/v1/books/sync", json=items, timeout=120.0
    )
    body = resp.json() if resp.status_code == 200 else {"detail": resp.text[:400]}
    rows = _counts(settings)
    ok = resp.status_code == 200 and rows["af_books"] == len(items)
    _dump("resync_result.json", {"ok": ok, "status": resp.status_code, "items": len(items),
                                 "body": {k: v for k, v in body.items() if k != "books"},
                                 "rows_after": rows})
    _log(f"{'PASS' if ok else 'FAIL'} 书目回灌 {len(items)} 条 status={resp.status_code} "
         f"af_books={rows['af_books']} 其余表={ {k: v for k, v in rows.items() if k != 'af_books'} }")
    _service_stop()
    return 0 if ok else 5


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True,
                        choices=("preflight", "purge", "resync", "status", "stop"))
    parser.add_argument("--i-hold-approval", action="store_true")
    args = parser.parse_args()
    settings = _business_settings()
    if args.phase == "stop":
        _service_stop()
        return 0
    if args.phase == "status":
        _log(f"行数={_counts(settings)} 磁盘={_disk_state(settings)}")
        return 0
    if args.phase == "preflight":
        return _preflight(settings)
    if args.phase == "purge":
        return _purge_phase(settings, args.i_hold_approval)
    return _resync_phase(settings, args.i_hold_approval)


if __name__ == "__main__":
    raise SystemExit(main())
