"""C2/D4 + C3/G5 只读取证：凭据不外泄、库表边界、配置三段生效来源。

只读：不写业务库、不写真实数据根、不修改任何 `.env`；密码只以「命中次数」呈现，绝不回显。
用法：python scripts/tmp/live_check_config_credentials.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import httpx  # noqa: E402

from axiom_flow.config import Settings, load_settings  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "data" / "probes" / "live-bringup-20260920"
KEYS = ("QED_DATA_ROOT", "QED_DB_HOST", "QED_DB_PORT", "QED_DB_NAME", "QED_DB_USER",
        "QED_DB_PASSWORD", "QED_OCR_MODEL_URL", "AXIOM_OCR_ENGINE", "AXIOM_OCR_TIMEOUT")
SECRET_KEYS = {"QED_DB_PASSWORD", "API_KEY"}


def _parse_env(path: Path) -> dict[str, str]:
    """按 `config._env_file_values` 的口径读单个 .env（跳过注释/空值，去引号）。"""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            out[key.strip()] = value
    return out


def _mask(key: str, value: str) -> str:
    return "set(已遮蔽)" if (key in SECRET_KEYS and value) else value


def _layers() -> dict[str, dict[str, str]]:
    """逐键记录三层来源（真实环境变量 / 本仓库 .env / 根 .env）与生效层。"""
    repo_env = _parse_env(REPO / ".env")
    root_env = _parse_env(REPO.parent / ".env")
    out: dict[str, dict[str, str]] = {}
    for key in KEYS:
        env_value = os.environ.get(key, "")
        effective = ("environ" if env_value else "repo .env" if repo_env.get(key)
                     else "root .env" if root_env.get(key) else "内置默认")
        out[key] = {
            "environ": _mask(key, "set" if env_value else ""),
            "repo_env": _mask(key, repo_env.get(key, "")),
            "root_env": _mask(key, root_env.get(key, "")),
            "effective": effective,
        }
    return out


def _credential_leak(settings: Settings) -> dict[str, object]:
    """密码是否出现在日志/异常文本/接口响应/配置 repr 中（只报命中数，不回显内容）。"""
    secret = settings.db_password
    result: dict[str, object] = {"secret_len": len(secret), "repr_clean": secret not in repr(settings)}
    if not secret:
        result["skipped"] = "db_password 为空，无法做命中取证"
        return result
    log_dir = REPO / "logs"
    hits: dict[str, int] = {}
    for path in sorted(log_dir.glob("*.log")) if log_dir.is_dir() else []:
        text = path.read_text(encoding="utf-8", errors="replace")
        hits[path.name] = text.count(secret)
    result["log_hits"] = hits
    api_hits: dict[str, int] = {}
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.port}",
                      trust_env=False, timeout=10) as client:
        for path in ("/api/v1/health", "/api/v1/books", "/api/v1/parse-jobs",
                     "/api/v1/books/linearalgebra-b01", "/api/v1/books/linearalgebra-b01/chunks"):
            response = client.get(path)
            api_hits[f"{response.status_code} {path}"] = response.text.count(secret)
    result["api_hits"] = api_hits
    result["src_grep"] = {}
    for name in ("db/engine.py", "config.py"):
        path = REPO / "src" / "axiom_flow" / name
        if path.is_file():
            src = path.read_text(encoding="utf-8")
            result["src_grep"][name] = bool(re.search(r"(print|log|logger)\S*\([^)]*db_password", src))
    return result


def _table_boundary() -> dict[str, object]:
    """本服务可触达的表名与写面：只应出现 af_*，且不含 qed_* 的写语句。"""
    src = REPO / "src" / "axiom_flow"
    names = set(re.findall(r'__tablename__\s*=\s*"([^"]+)"', (src / "db" / "models.py").read_text(encoding="utf-8")))
    mig = REPO / "src" / "axiom_flow" / "migrations" / "versions"
    mig_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(mig.glob("*.py")))
    mig_names = set(re.findall(r'create_table\(\s*"([^"]+)"', mig_text))
    non_af = {n for n in names | mig_names if not n.startswith("af_")}
    writes = re.findall(r"\b(INSERT|UPDATE|DELETE)\b", (src / "db" / "models.py").read_text(encoding="utf-8"), re.I)
    return {"tables": sorted(names), "migration_tables": sorted(mig_names), "non_af_tables": sorted(non_af),
            "raw_dml_in_orm_layer": len(writes)}


def main() -> int:
    settings = load_settings()
    report = {
        "G5_config_layers": _layers(),
        "G5_effective": {
            "data_dir": str(settings.data_dir),
            "ocr_model_url": settings.ocr_model_url,
            "db_name": settings.db_name,
            "db_user": settings.db_user,
            "port": settings.port,
        },
        "D4_credentials": _credential_leak(settings),
        "D4_table_boundary": _table_boundary(),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / "D4_G5_config_credential.txt"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    out.write_text(text, encoding="utf-8")
    print(text)
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
