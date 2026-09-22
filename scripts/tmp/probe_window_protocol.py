"""PLAN-007 W0：MinerU 3.4.4 窗口协议冻结探测（只读模型服务、单占用、零写入真实数据根）。

要冻结的四项假设（缺一不进 W1，判据见 PLAN-007 前置条件 1）：

- ① `start_page_id` / `end_page_id` 是否含端点、0 基还是 1 基；
- ② 窗口内返回的 `content_list[].page_idx` 与 `middle_json.pdf_info[]` 索引是**窗口内**还是**全书**；
- ③ 空白页落在窗口内部 / 整窗空白时的响应形状（决定「空白页合法化」的判据写法）；
- ④ 56 页窗口的真机耗时（决定 `ocr_timeout` 由「整本 600s」改为「每块」后的默认值）。

为什么用**合成标记 PDF** 冻结 ①②：b05 全本无文本层（每页 `get_text()` 长度为 0，页面由扫描图
构成），无法用源文本反推窗口覆盖了哪些物理页；标记页把「哪一页」变成 md 里的可直接观测事实，
且页索引语义属 PDF 层、与内容无关，结论可迁移到真书。④ 必须用真书（扫描工作量才是真实量级）。

用法（阶段一冻结 ①②③，再按冻结口径算出阶段二真书参数）：

    D:/software/anaconda3/envs/QED_env/python.exe scripts/tmp/probe_window_protocol.py \
        --sample marked --window mk-a:2:4 --window mk-b:3:5 --window mk-blank:5:6

    ... probe_window_protocol.py --sample marked --window mk-blank-only:5:5
    ... probe_window_protocol.py --window real-blank:?? --window real-wide:??   # 参数按 ① 换算

产出：`data/probes/window-req086-<日期>/`（`data/` 已 gitignore）——逐次原始响应 + `manifest.jsonl`
（含 sha256）+ `windows.jsonl`（每窗结论）+ `summary.md`（①~④ 判定）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = "http://127.0.0.1:5002"
# b05 源 PDF 只读取（317 页；空白页 48/98/136/162/282 以「整页零墨」像素法核实）
DEFAULT_PDF = (
    REPO_ROOT.parent / "dataset" / "raw" / "math-advanced" / "math_analysis" / "数学分析原理_341544f3.pdf"
)
MARKED_PAGES = 12
MARKED_BLANKS = frozenset({5, 6})  # 1 基：合成样本内的空白页，用于冻结 ③
MARKER_RE = re.compile(r"MK\d{4}[0-9A-Z]{3}")
SUBMIT_ROUTE = "/tasks"
STATUS_ROUTE = "/tasks/{task_id}"
RESULT_ROUTE = "/tasks/{task_id}/result"
# 只设产物形态开关 + 窗口字段；解析调参（backend/parse_method/公式开关）一律不发送（ADR 0004）。
OUTPUT_FLAGS = {"return_md": "true", "return_content_list": "true", "return_middle_json": "true"}
_DONE = {"done", "completed", "finished", "success"}
_FAIL = {"fail", "failed", "error"}


class Budget:
    """请求/提交/挂钟三重预算：任一超限立即中止，不压垮本地模型服务。"""

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
    """把每次 HTTP 往返的原始响应落盘并登记哈希（续跑时序号从既有 manifest 接续）。"""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.raw_dir = out_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = out_dir / "manifest.jsonl"
        self._seq = len(self._rows(self.manifest_path))

    @staticmethod
    def _rows(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def record(self, *, label: str, method: str, url: str, response: httpx.Response) -> Path:
        self._seq += 1
        path = self.raw_dir / f"{self._seq:03d}_{_safe(label)}_{response.status_code}.bin"
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
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return path


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in text)


def _json_object(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def make_marked_pdf(out_dir: Path) -> tuple[Path, dict[str, int]]:
    """生成 12 页合成样本：每页一个大号唯一标记 `MK<页号><后缀>`，第 5/6 页整页留白。

    标记只用于「窗口覆盖到哪一页」的反查，不参与任何真实书目数据。
    """
    import pymupdf

    samples = out_dir / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    path = samples / f"marked-{MARKED_PAGES}p.pdf"
    markers: dict[str, int] = {}
    doc = pymupdf.open()
    for page_no in range(1, MARKED_PAGES + 1):
        page = doc.new_page(width=595, height=842)
        if page_no in MARKED_BLANKS:
            continue
        marker = f"MK{page_no:04d}{chr(65 + page_no)}{chr(65 + (page_no * 3) % 26)}{chr(65 + (page_no * 7) % 26)}"
        markers[marker] = page_no
        page.insert_text((60, 120), marker, fontsize=34)
        page.insert_text((60, 220), f"Marker body line for page {page_no}: {marker}", fontsize=14)
        page.insert_text((60, 260), f"Alpha beta gamma delta line {page_no} of the probe sample.", fontsize=14)
    path.write_bytes(doc.tobytes())
    doc.close()
    return path, markers


def _payload_of(body: Any, filename: str) -> dict[str, Any]:
    """解包 `results[<file_stem>]`；单文件键不匹配时按唯一键回落（与适配器同口径）。"""
    if not isinstance(body, dict):
        return {}
    results = body.get("results")
    if isinstance(results, dict) and results:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        grouped = results.get(stem)
        if not isinstance(grouped, dict) and len(results) == 1:
            grouped = next(iter(results.values()))
        return grouped if isinstance(grouped, dict) else {}
    return body


def _load(field: Any) -> Any:
    if isinstance(field, str):
        try:
            return json.loads(field)
        except json.JSONDecodeError:
            return None
    return field


def window_shape(data: dict[str, Any], markers: dict[str, int]) -> dict[str, Any]:
    """从结果载荷提取冻结所需形状事实：页索引、pdf_info 长度、md 标记反查、块类型分布。"""
    content_list = _load(data.get("content_list")) or []
    idx: list[int] = []
    per_page_blocks: dict[int, int] = {}
    types: dict[str, int] = {}
    if isinstance(content_list, list):
        for item in content_list:
            if not isinstance(item, dict):
                continue
            try:
                page_idx = int(item.get("page_idx") or 0)
            except (TypeError, ValueError):
                page_idx = 0
            idx.append(page_idx)
            per_page_blocks[page_idx] = per_page_blocks.get(page_idx, 0) + 1
            key = str(item.get("type"))
            types[key] = types.get(key, 0) + 1
    middle = _load(data.get("middle_json")) or {}
    pdf_info = middle.get("pdf_info") if isinstance(middle, dict) else None
    pdf_info = pdf_info if isinstance(pdf_info, list) else []
    md = ""
    for key in ("md_content", "markdown"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            md = value
            break
    present = set(idx)
    found = sorted({markers[token] for token in MARKER_RE.findall(md) if token in markers})
    return {
        "content_list_items": len(idx),
        "page_idx_distinct": sorted(present),
        "blocks_per_page": {str(key): value for key, value in sorted(per_page_blocks.items())},
        "block_types": dict(sorted(types.items())),
        "pdf_info_len": len(pdf_info),
        "pdf_info_idx": [info.get("page_idx") for info in pdf_info if isinstance(info, dict)],
        "md_chars": len(md),
        "md_marker_pages_1based": found,
    }


def require_idle(client: httpx.Client, base: str, rec: Recorder, budget: Budget, *, allow_busy: bool) -> dict[str, Any]:
    """单占用自证：提交前 `/health` 必须 queued+processing 为 0，否则不提交（用户裁决：只允许一个解析在飞）。"""
    budget.take_request()
    response = client.get(f"{base}/health", timeout=10)
    rec.record(label="health-before-submit", method="GET", url=response.request.url, response=response)
    body = _json_object(response)
    body = body if isinstance(body, dict) else {}
    queued = body.get("queued_tasks")
    processing = body.get("processing_tasks")
    if (queued or 0) + (processing or 0) and not allow_busy:
        raise RuntimeError(f"模型服务非空闲（queued={queued} processing={processing}），按单占用裁决不提交")
    return {"queued": queued, "processing": processing, "version": body.get("version")}


def probe_window(
    client: httpx.Client,
    base: str,
    rec: Recorder,
    budget: Budget,
    *,
    pdf: Path,
    label: str,
    start: int,
    end: int,
    markers: dict[str, int],
    poll_interval: float,
    deadline_minutes: float,
    escalate_after: int,
) -> dict[str, Any]:
    """提交一个窗口 → 轮询终态 → 取结果；全程串行、单占用、逐次留档。"""
    payload = {**OUTPUT_FLAGS, "start_page_id": str(start), "end_page_id": str(end)}
    budget.take_submit()
    budget.take_request()
    started = time.monotonic()
    response = client.post(
        f"{base}{SUBMIT_ROUTE}",
        files={"files": (pdf.name, pdf.read_bytes(), "application/pdf")},
        data=payload,
        timeout=180,
    )
    rec.record(label=f"{label}-submit", method="POST", url=response.request.url, response=response)
    body = _json_object(response)
    task_id = body.get("task_id") if isinstance(body, dict) else None
    result: dict[str, Any] = {
        "label": label,
        "pdf": pdf.name,
        "request": payload,
        "submit_status": response.status_code,
        "task_id": task_id,
        "observed_states": [],
    }
    if not task_id:
        result["error"] = f"提交未返回 task_id：{json.dumps(body, ensure_ascii=False)[:300]}"
        return result

    states: list[str] = []
    poll_deadline = time.monotonic() + deadline_minutes * 60
    polls = 0
    result_http: int | None = None
    data: dict[str, Any] = {}
    while time.monotonic() < poll_deadline:
        polls += 1
        budget.take_request()
        response = client.get(f"{base}{STATUS_ROUTE.format(task_id=task_id)}", timeout=30)
        rec.record(label=f"{label}-status{polls}", method="GET", url=response.request.url, response=response)
        status_body = _json_object(response)
        status = ""
        if isinstance(status_body, dict):
            value = status_body.get("status")
            if not isinstance(value, str):
                nested = status_body.get("data")
                value = nested.get("state") if isinstance(nested, dict) else None
            status = value.lower() if isinstance(value, str) else ""
        if status and status not in states:
            states.append(status)
        if status in _FAIL:
            result["error"] = f"服务报终态 {status}：{json.dumps(status_body, ensure_ascii=False)[:400]}"
            break
        if status in _DONE:
            while time.monotonic() < poll_deadline:
                budget.take_request()
                response = client.get(f"{base}{RESULT_ROUTE.format(task_id=task_id)}", timeout=180)
                result_http = response.status_code
                rec.record(label=f"{label}-result", method="GET", url=response.request.url, response=response)
                if response.status_code == 202:
                    time.sleep(poll_interval)
                    continue
                data = _payload_of(_json_object(response), pdf.name)
                break
            break
        time.sleep(poll_interval if polls < escalate_after else poll_interval * 2.5)

    result.update(
        {
            "observed_states": states,
            "polls": polls,
            "result_http": result_http,
            "elapsed_seconds": round(time.monotonic() - started, 1),
            **window_shape(data, markers),
        }
    )
    return result


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dedupe_by_label(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同名窗口以最后一次留档为准（重跑修正不重复列示）。"""
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest[str(row.get("label"))] = row
    return list(latest.values())


def summarize(out_dir: Path, results: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    """把历次窗口结论写入 summary.md（按留档重算，不额外发请求）。"""
    lines = [
        "# MinerU 窗口协议冻结结论（PLAN-007 W0）",
        "",
        f"- 服务版本：`{meta.get('version')}`；探测日期：{time.strftime('%Y-%m-%d')}",
        f"- 提交前 `/health`：queued=`{meta.get('queued')}` processing=`{meta.get('processing')}`（单占用自证）",
        f"- 合成样本：`marked-{MARKED_PAGES}p.pdf`（空白页 1 基 {sorted(MARKED_BLANKS)}）；真书：`{meta.get('pdf')}` {meta.get('pages')} 页",
        "",
        "## 逐窗口留档",
        "",
        "| label | start/end | 终态 | 耗时(s) | content_list 条目 | page_idx 集 | pdf_info 长度 | pdf_info_idx 首末 | md 字符 | md 标记命中页(1基) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        idx = row.get("page_idx_distinct") or []
        pi = row.get("pdf_info_idx") or []
        pages = row.get("md_marker_pages_1based") or []
        lines.append(
            f"| {row.get('label')} | {(row.get('request') or {}).get('start_page_id')}/"
            f"{(row.get('request') or {}).get('end_page_id')} | {row.get('observed_states')} "
            f"| {row.get('elapsed_seconds')} | {row.get('content_list_items')} "
            f"| {f'{idx[0]}…{idx[-1]}（{len(idx)}）' if idx else '-'} | {row.get('pdf_info_len')} "
            f"| {f'{pi[0]}…{pi[-1]}' if pi else '-'} | {row.get('md_chars')} | {pages or '-'} |"
        )
    lines += [
        "",
        "## 四项假设判定",
        "",
        "- ① 端点与基数：含端点性看 `pdf_info_len` 是否等于 `|end-start|+1`；基数看 `md 标记命中页` 首项与 `start` 的差。",
        "- ② 索引空间：`page_idx 集` 自 0/1 起＝窗口内相对；自 `start` 附近起＝全书绝对。",
        "- ③ 空白页形状：看空白窗口的 `submit_status`/终态/`content_list 条目`/`pdf_info_len` 组合。",
        "- ④ 宽窗口耗时：`real-wide*` 行的「耗时(s)」。",
        "",
        "## 逐次往返",
        "",
    ]
    for row in Recorder._rows(out_dir / "manifest.jsonl"):
        lines.append(
            f"- `{row['seq']:03d}` {row['method']} {row['label']} → {row['status']} "
            f"({row['bytes']} B, {row['elapsed_ms']} ms) sha256=`{row['sha256'][:16]}…`"
        )
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[: 12 + len(results) + 12]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MinerU 窗口协议冻结探测（只读、单占用）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--pdf", default="", help="真书 PDF；--sample marked 时忽略")
    parser.add_argument("--sample", choices=("marked", "real"), default="real")
    parser.add_argument("--window", action="append", default=[], help="label:start:end，可重复")
    parser.add_argument("--out", default="")
    parser.add_argument("--max-requests", type=int, default=250)
    parser.add_argument("--max-submits", type=int, default=5)
    parser.add_argument("--deadline-minutes", type=float, default=60.0, help="整脚本挂钟预算")
    parser.add_argument("--poll-deadline-minutes", type=float, default=20.0, help="单窗口轮询预算")
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--escalate-after", type=int, default=10, help="前 N 次用 poll-interval，之后 ×2.5")
    parser.add_argument("--allow-busy", action="store_true", help="显式放宽单占用自证（默认不允许）")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")
    base = args.base_url.rstrip("/")
    out_dir = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "data" / "probes" / f"window-req086-{time.strftime('%Y%m%d')}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    markers: dict[str, int] = {}
    if args.sample == "marked":
        pdf, markers = make_marked_pdf(out_dir)
    else:
        pdf = Path(args.pdf or DEFAULT_PDF)
        if not pdf.is_file():
            raise SystemExit(f"源 PDF 不存在：{pdf}")

    import pymupdf

    doc = pymupdf.open(pdf)
    total_pages = doc.page_count
    doc.close()

    budget = Budget(
        max_requests=args.max_requests,
        max_submits=args.max_submits,
        deadline_minutes=args.deadline_minutes,
    )
    rec = Recorder(out_dir)
    with httpx.Client(trust_env=False) as client:
        meta = require_idle(client, base, rec, budget, allow_busy=args.allow_busy)
        meta.update({"pdf": pdf.name, "pages": total_pages})
        for spec in args.window:
            label, _, span = spec.partition(":")
            start_text, _, end_text = span.partition(":")
            if not start_text:
                raise SystemExit(f"--window 需要 label:start:end，收到 {spec}")
            row = probe_window(
                client,
                base,
                rec,
                budget,
                pdf=pdf,
                label=label.strip(),
                start=int(start_text),
                end=int(end_text or start_text),
                markers=markers,
                poll_interval=args.poll_interval,
                deadline_minutes=args.poll_deadline_minutes,
                escalate_after=args.escalate_after,
            )
            with (out_dir / "windows.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps(row, ensure_ascii=False, indent=2))
    summarize(out_dir, _dedupe_by_label(_rows(out_dir / "windows.jsonl")), meta)
    print(f"\n留档目录：{out_dir}")
    print(f"请求 {budget.requests}/{budget.max_requests}，提交 {budget.submits}/{budget.max_submits}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
