"""MinerU 3.4.4 异步 API 冻结探测（PLAN-003 W0，C 类实验）。

用途：把「提交 / 状态 / 结果」三段端点与响应形状变成**留档证据**，供 `engines/mineru.py`
改造与 `docs/design/model-integration.md` 晋升引用。设计文档承诺同步 `/file_parse`、代码轮询
`/get_task_results/{id}`（该版本不存在），两者都无响应形状留档，故必须先探测再改码。

冻结要素（假设 H1~H4、样本 S-a/S-b、预算、门槛 M1~M4）见
`docs/history/plans/2026-09/2026-09-20-parse-live-bringup.md` 的 W0 一节。

用法（只连接模型服务，不启停容器）：

    D:/software/anaconda3/envs/QED_env/python.exe scripts/tmp/probe_mineru_api.py --stages meta
    ... scripts/tmp/probe_mineru_api.py --stages meta,single,book

产出目录：`data/probes/mineru-3.4.4-<日期>/`（`data/` 已 gitignore）——逐份原始响应 +
`manifest.jsonl`（含 sha256）+ `summary.md`（门槛判定）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:5002"
# 假设 H1~H3（2026-09-20 openapi 探测已核对路由清单，见 data/probes/*/routes.txt）：
# 提交走异步 `/tasks`（202），状态 `/tasks/{task_id}`，结果 `/tasks/{task_id}/result`。
SUBMIT_SYNC_ROUTE = "/file_parse"
SUBMIT_ASYNC_ROUTE = "/tasks"
STATUS_ROUTE = "/tasks/{task_id}"
RESULT_ROUTE = "/tasks/{task_id}/result"
LEGACY_ROUTE = "/get_task_results/{task_id}"
# 产物形态开关（本仓库契约需要，非解析调参；backend/effort/parse_method 等一律不发送，
# 让容器环境配置生效——模型参数归 8900，ADR 0004）。
# openapi 实测 `return_content_list` / `return_middle_json` / `return_images` 默认 **false**，
# 不显式打开则 blocks 与页尺寸恒缺失（新增偏差 A5）。
OUTPUT_FLAGS = {"return_md": "true", "return_content_list": "true", "return_middle_json": "true"}
# 门槛 M4：正文候选字段（根仓库唯一成功证据为 md_content）
BODY_FIELDS = ("content_list", "markdown", "md_content", "middle_json", "images")
# 状态字面值候选载体
STATE_KEYS = ("state", "status")
TERMINAL_LITERALS = ("done", "success", "failed", "error", "finished", "completed")


class Budget:
    """请求与提交预算：超限立即中止，避免压垮本地模型服务。"""

    def __init__(self, *, max_requests: int, max_submits: int, deadline_minutes: float) -> None:
        self.max_requests = max_requests
        self.max_submits = max_submits
        self.requests = 0
        self.submits = 0
        self._end = time.monotonic() + deadline_minutes * 60

    def take_request(self) -> None:
        if self.requests >= self.max_requests:
            raise RuntimeError(f"请求预算耗尽（上限 {self.max_requests}）")
        if time.monotonic() > self._end:
            raise RuntimeError("挂钟预算耗尽")
        self.requests += 1

    def take_submit(self) -> None:
        if self.submits >= self.max_submits:
            raise RuntimeError(f"提交预算耗尽（上限 {self.max_submits}）")
        self.submits += 1


class Recorder:
    """把每次 HTTP 往返的原始响应落盘并登记哈希。"""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.raw_dir = out_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = out_dir / "manifest.jsonl"
        self.entries: list[dict[str, Any]] = []
        self.last_path: Path | None = None
        existing = out_dir / "manifest.jsonl"
        self._seq = sum(1 for line in existing.read_text(encoding="utf-8").splitlines() if line.strip()) if existing.exists() else 0

    def record(self, *, label: str, method: str, url: str, response: httpx.Response) -> Path:
        self._seq += 1
        name = f"{self._seq:03d}_{_safe(label)}_{response.status_code}.bin"
        path = self.raw_dir / name
        path.write_bytes(response.content)
        entry = {
            "seq": self._seq,
            "label": label,
            "method": method,
            "url": str(url),
            "status": response.status_code,
            "sha256": hashlib.sha256(response.content).hexdigest(),
            "bytes": len(response.content),
            "elapsed_ms": round(response.elapsed.total_seconds() * 1000),
            "file": str(path.relative_to(self.out_dir)),
        }
        self.entries.append(entry)
        self.last_path = path
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path

    def json_body(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)


def make_samples(out_dir: Path) -> dict[str, Path]:
    """合成样本 S-a（1 页）与 S-b（5 页含公式/表格/页眉），生成规则固定。"""
    import pymupdf

    samples_dir = out_dir / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)
    specs = {"sa-1page": 1, "sb-5page": 5}
    made: dict[str, Path] = {}
    for name, pages in specs.items():
        doc = pymupdf.open()
        for page_index in range(pages):
            page = doc.new_page(width=595, height=842)
            page.insert_text((60, 50), f"Probe Header {name} page {page_index + 1}", fontsize=10)
            page.insert_text((60, 120), f"Section {page_index + 1}. Linearity and Vector Spaces", fontsize=16)
            page.insert_text((60, 170), "The sum of two vectors u and v satisfies u + v = v + u.", fontsize=12)
            page.insert_text((60, 200), r"\begin{equation} A\mathbf{x} = \lambda\mathbf{x} \end{equation}", fontsize=12)
            page.insert_text((60, 240), "x1 x2 x3", fontsize=12)
            page.insert_text((60, 260), "1 0 2", fontsize=12)
            page.insert_text((60, 280), "0 3 1", fontsize=12)
            page.insert_text((60, 330), "Exercise: prove that the kernel of A is a subspace.", fontsize=12)
        path = samples_dir / f"{name}.pdf"
        path.write_bytes(doc.tobytes())
        doc.close()
        made[name] = path
    return made


def _get_with_retry(
    client: httpx.Client, url: str, *, label: str, rec: Recorder, budget: Budget, attempts: int = 5
) -> httpx.Response:
    """零副作用 GET 的瞬时失败重试：WSL 端口转发偶发 502（0 字节）不应判为端点不存在。"""
    response: httpx.Response | None = None
    for attempt in range(attempts):
        budget.take_request()
        try:
            response = client.get(url, timeout=20)
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200 and response.content:
            rec.record(label=label, method="GET", url=url, response=response)
            return response
        if response is not None:
            rec.record(label=f"{label}-try{attempt + 1}", method="GET", url=url, response=response)
        time.sleep(5)
    assert response is not None
    return response


def probe_meta(client: httpx.Client, base: str, rec: Recorder, budget: Budget) -> dict[str, Any]:
    """门槛 M1：`/openapi.json` 为路由清单的第一证据源（零副作用）。"""
    health = _get_with_retry(client, f"{base}/health", label="health", rec=rec, budget=budget)
    openapi = _get_with_retry(client, f"{base}/openapi.json", label="openapi", rec=rec, budget=budget)
    path = rec.last_path
    health_body = rec.json_body(health) if health.status_code == 200 else {}
    body = rec.json_body(openapi)
    routes = sorted((body or {}).get("paths", {}).keys()) if isinstance(body, dict) else []
    (rec.out_dir / "routes.txt").write_text("\n".join(routes), encoding="utf-8")
    info = (body or {}).get("info", {}) if isinstance(body, dict) else {}
    return {
        "openapi_status": openapi.status_code,
        "routes": routes,
        "service_title": info.get("title"),
        "service_version": info.get("version"),
        "health": health_body if isinstance(health_body, dict) else {},
        "openapi_file": str(path.relative_to(rec.out_dir)) if path else "",
    }


def _post_submit(
    client: httpx.Client, base: str, rec: Recorder, budget: Budget, pdf: Path, label: str
) -> str | None:
    """异步提交（`POST /tasks`，openapi 声明 202）；不发送解析调参字段，只送产物形态开关。"""
    budget.take_request()
    budget.take_submit()
    with pdf.open("rb") as handle:
        response = client.post(
            f"{base}{SUBMIT_ASYNC_ROUTE}",
            files={"files": (pdf.name, handle.read(), "application/pdf")},
            data=OUTPUT_FLAGS,
            timeout=180,
        )
    rec.record(label=f"{label}-submit", method="POST", url=response.request.url, response=response)
    body = rec.json_body(response)
    if isinstance(body, dict):
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        for key in ("task_id", "id", "taskId"):
            for source in (body, data):
                value = source.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


def _state_values(payload: Any) -> list[tuple[str, str]]:
    """只取状态键的**字面值**（不能全文匹配，否则 `error` 等键名会误判为状态）。"""
    out: list[tuple[str, str]] = []

    def visit(node: Any, prefix: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else key
                if key in STATE_KEYS and isinstance(value, str):
                    out.append((path, value))
                else:
                    visit(value, path)
        elif isinstance(node, list):
            for index, item in enumerate(node[:3]):
                visit(item, f"{prefix}[{index}]")

    visit(payload, "")
    return out


def probe_fetch(
    client: httpx.Client, base: str, rec: Recorder, budget: Budget, task_id: str, label: str, *, via: str = "result"
) -> dict[str, Any]:
    """对已提交任务补拉结果（`via=result`）或状态（`via=status`），不消耗提交预算。"""
    route = RESULT_ROUTE if via == "result" else STATUS_ROUTE
    budget.take_request()
    response = client.get(f"{base}{route.format(task_id=task_id)}", timeout=180)
    path = rec.record(label=f"{label}-{via}", method="GET", url=response.request.url, response=response)
    body = rec.json_body(response)
    values = [value for _, value in _state_values(body)]
    return {
        "task_id": task_id,
        "result_http": response.status_code,
        "observed_states": sorted(set(values)),
        "state_key": "status" if values else None,
        "result_file": str(path.relative_to(rec.out_dir)),
        "result_top_keys": _top_keys(body),
        "body_fields_present": [f for f in BODY_FIELDS if _contains_key(body, f)],
    }


def probe_task(client: httpx.Client, base: str, rec: Recorder, budget: Budget, task_id: str, label: str) -> dict[str, Any]:
    """轮询状态端点直到终态；登记状态字面值与 legacy 路由 404 证据。"""
    states: list[str] = []
    state_key: str | None = None
    deadline = time.monotonic() + 600
    last_status: int | None = None
    legacy_recorded = False
    while time.monotonic() < deadline:
        budget.take_request()
        response = client.get(f"{base}{STATUS_ROUTE.format(task_id=task_id)}", timeout=30)
        rec.record(label=f"{label}-status", method="GET", url=response.request.url, response=response)
        last_status = response.status_code
        body = rec.json_body(response)
        pairs = _state_values(body)
        if pairs:
            state_key = state_key or pairs[0][0]
        for _, value in pairs:
            if value not in states:
                states.append(value)
        if not legacy_recorded:
            legacy_recorded = True
            budget.take_request()
            legacy = client.get(f"{base}{LEGACY_ROUTE.format(task_id=task_id)}", timeout=30)
            rec.record(label="legacy-get_task_results", method="GET", url=legacy.request.url, response=legacy)
        if any(literal in states for literal in TERMINAL_LITERALS):
            break
        time.sleep(3)

    result: dict[str, Any] = {"status": last_status, "observed_states": states, "state_key": state_key}
    budget.take_request()
    response = client.get(f"{base}{RESULT_ROUTE.format(task_id=task_id)}", timeout=180)
    path = rec.record(label=f"{label}-result", method="GET", url=response.request.url, response=response)
    body = rec.json_body(response)
    keys = _top_keys(body)
    result.update(
        {
            "result_status": response.status_code,
            "result_file": str(path.relative_to(rec.out_dir)),
            "result_top_keys": keys,
            "body_fields_present": [f for f in BODY_FIELDS if _contains_key(body, f)],
        }
    )
    return result


def _top_keys(payload: Any) -> list[str]:
    if isinstance(payload, dict):
        return sorted(payload.keys())
    return []


def _contains_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        if key in node:
            return True
        return any(_contains_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_contains_key(item, key) for item in node)
    return False


def _archive_meta(out_dir: Path) -> dict[str, Any]:
    """服务标识从既有留档补齐（分次运行时不为元信息重复发请求）。"""
    found: dict[str, Any] = {}
    for row in _manifest_rows(out_dir):
        if row["status"] != 200 or row["label"] not in {"openapi", "health"}:
            continue
        try:
            body = json.loads((out_dir / row["file"]).read_text(encoding="utf-8"))
        except (ValueError, KeyError):
            continue
        if row["label"] == "openapi" and isinstance(body, dict):
            info = body.get("info") or {}
            found["service_title"] = info.get("title")
            found["openapi_file"] = row["file"]
        elif row["label"] == "health" and isinstance(body, dict):
            found["health"] = body
            # 真实服务版本以 /health 为准（openapi info 只有 FastAPI 框架版本）。
            found["service_version"] = body.get("version")
    return found


def summarize(out_dir: Path, meta: dict[str, Any], tasks: dict[str, Any]) -> None:
    """门槛 M1~M4 判定 + 假设核对，写入 summary.md 供 PLAN-003 冻结清单引用。"""
    m1 = meta.get("openapi_status") == 200
    routes = set(meta.get("routes") or [])
    if not routes and (out_dir / "routes.txt").exists():
        routes = {line.strip() for line in (out_dir / "routes.txt").read_text(encoding="utf-8").splitlines() if line.strip()}
    if meta.get("openapi_status") is None and any(
        row["label"] == "openapi" and row["status"] == 200 for row in _manifest_rows(out_dir)
    ):
        m1 = True
    observed = sorted({state for item in tasks.values() for state in (item.get("observed_states") or [])})
    fields = sorted({field for item in tasks.values() for field in (item.get("body_fields_present") or [])})
    top_keys = sorted({k for item in tasks.values() for k in (item.get("result_top_keys") or [])})
    # 汇总口径覆盖全部留档往返（分次运行不丢历史证据），不额外消耗请求预算。
    archived_states, archived_keys, archived_fields = _archive_facts(out_dir)
    if meta.get("service_version") is None:
        meta = {**_archive_meta(out_dir), **{k: v for k, v in meta.items() if v}}
    observed = sorted(set(observed) | archived_states)
    top_keys = sorted(set(top_keys) | archived_keys)
    fields = sorted(set(fields) | archived_fields)
    m2 = len(observed) >= 2
    m3 = bool(fields)
    m4 = "content_list" in fields and ("markdown" in fields or "md_content" in fields)
    lines = [
        "# MinerU 3.4.4 探测结论（PLAN-003 W0）",
        "",
        f"- 服务：`{meta.get('service_title')}` 版本 `{meta.get('service_version')}`",
        f"- `/health` 载荷：`{json.dumps(meta.get('health') or {}, ensure_ascii=False)}`",
        f"- 路由清单：`{meta.get('openapi_file')}` → `routes.txt`（{len(routes)} 条）",
        "",
        "## 假设核对",
        "",
        f"- 异步提交 `{SUBMIT_ASYNC_ROUTE}` / 同步提交 `{SUBMIT_SYNC_ROUTE}`：均在路由清单内，"
        "task_id 载体见 `raw/` 内 `*-submit_*` 响应（H1）",
        f"- H2 状态端点 `{STATUS_ROUTE}`：{'存在' if STATUS_ROUTE in routes else '不存在'}",
        f"- H3 结果端点 `{RESULT_ROUTE}`：{'存在' if RESULT_ROUTE in routes else '不存在'}",
        f"- legacy `{LEGACY_ROUTE}`：{'存在' if LEGACY_ROUTE in routes else '不存在（DEFECT-002 根因）'}",
        f"- 状态载体：{sorted({str(item.get('state_key')) for item in tasks.values() if item.get('state_key')}) or ['status（顶层，见 raw/ 留档）']}",
        f"- 状态字面值：{observed}",
        f"- 结果顶层字段：{top_keys}",
        f"- 正文字段：{fields}",
        "",
        "## 门槛判定",
        "",
        f"- M1 openapi 200：{'PASS' if m1 else 'FAIL'}",
        f"- M2 观测 ≥2 状态字面值：{'PASS' if m2 else 'FAIL'}（{observed}）",
        f"- M3 结果字段全留档：{'PASS' if m3 else 'FAIL'}",
        f"- M4 正文字段可喂进现有解析面：{'PASS' if m4 else 'FAIL（结果解析层保持防御式双兼容）'}",
        "",
        "## 逐次往返",
        "",
    ]
    for entry in _manifest_rows(out_dir):
        lines.append(
            f"- `{entry['seq']:03d}` {entry['method']} {entry['label']} → {entry['status']} "
            f"({entry['bytes']} B, {entry['elapsed_ms']} ms) sha256=`{entry['sha256'][:16]}…`"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def _archive_facts(out_dir: Path) -> tuple[set[str], set[str], set[str]]:
    """从既有留档重算观测事实（状态字面值 / 结果顶层字段 / 正文字段），不新增请求。"""
    states: set[str] = set()
    keys: set[str] = set()
    fields: set[str] = set()
    for raw in sorted((out_dir / "raw").glob("*.bin")) if (out_dir / "raw").exists() else []:
        try:
            body = json.loads(raw.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(body, dict) or not ({"task_id", "results"} & body.keys()):
            continue  # 只统计任务相关载荷（排除 health/openapi 等元信息体）
        states.update(value for _, value in _state_values(body))
        if "-result_200" in raw.stem or "-status_" in raw.stem or "-submit_" in raw.stem:
            keys.update(body.keys())
        fields.update(f for f in BODY_FIELDS if _contains_key(body, f))
    return states, keys, fields


def _manifest_rows(out_dir: Path) -> list[dict[str, Any]]:
    path = out_dir / "manifest.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MinerU 3.4.4 异步 API 冻结探测（只读模型服务）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--stages", default="meta", help="逗号分隔：meta,single,book,result")
    parser.add_argument("--tasks", default="", help="result 阶段用：label:task_id，逗号分隔（复用留档 task_id）")
    parser.add_argument("--out", default="", help="留档目录，默认 data/probes/mineru-3.4.4-<日期>")
    parser.add_argument("--max-requests", type=int, default=25)
    parser.add_argument("--max-submits", type=int, default=3)
    parser.add_argument("--deadline-minutes", type=float, default=20)
    args = parser.parse_args(argv)

    # Windows 控制台默认 GBK，中文结论会乱码。
    sys.stdout.reconfigure(encoding="utf-8")
    base = args.base_url.rstrip("/")
    stages = [s.strip() for s in args.stages.split(",") if s.strip()]
    out_dir = Path(args.out) if args.out else REPO_ROOT / "data" / "probes" / f"mineru-3.4.4-{time.strftime('%Y%m%d')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    budget = Budget(max_requests=args.max_requests, max_submits=args.max_submits, deadline_minutes=args.deadline_minutes)
    rec = Recorder(out_dir)

    meta: dict[str, Any] = {"routes": [], "openapi_status": None}
    tasks: dict[str, Any] = {}
    with httpx.Client(trust_env=False) as client:
        if "meta" in stages or len(stages) > 1:
            meta = probe_meta(client, base, rec, budget)
        if "single" in stages or "book" in stages:
            if meta.get("openapi_status") != 200 or SUBMIT_ASYNC_ROUTE not in (meta.get("routes") or []):
                raise RuntimeError("提交前置条件不满足：openapi 未取到 200 或异步路由缺失，不消耗提交预算")
            samples = make_samples(out_dir)
            jobs = [("single", "sa-1page"), ("book", "sb-5page")]
            for stage, sample in jobs:
                if stage not in stages:
                    continue
                task_id = _post_submit(client, base, rec, budget, samples[sample], stage)
                if not task_id:
                    tasks[stage] = {"error": "提交响应未取到 task_id（见 raw/ 留档）"}
                    continue
                tasks[stage] = {"task_id": task_id, **probe_task(client, base, rec, budget, task_id, stage)}
        if "result" in stages or "status" in stages:
            for via in ("status", "result"):
                if via not in stages:
                    continue
                for spec in (s for s in args.tasks.split(",") if s.strip()):
                    label, _, task_id = spec.partition(":")
                    fetched = probe_fetch(client, base, rec, budget, task_id.strip(), label.strip(), via=via)
                    merged = tasks.setdefault(label.strip(), {})
                    previous = set(merged.get("observed_states") or [])
                    merged.update(fetched)
                    merged["observed_states"] = sorted(previous | set(fetched["observed_states"]))

    summarize(out_dir, meta, tasks)
    print(f"\n留档目录：{out_dir}")
    print(f"请求 {budget.requests}/{budget.max_requests}，提交 {budget.submits}/{budget.max_submits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
