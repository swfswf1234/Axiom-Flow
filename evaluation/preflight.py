"""
模块职责：以单页、最多两次视觉模型调用验证百炼解析链路，不写入生产数据。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
关联测试：tests/test_evaluation_preflight.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

import fitz

from backend.app.config import Settings
from backend.app.providers import BailianProvider


class PreflightVisionProvider(Protocol):
    async def parse_page_primary(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]: ...

    async def parse_page_fallback(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]: ...


def _safe_error(error: Exception, api_key: str) -> str:
    """报告只保留诊断摘要，屏蔽密钥和本地绝对路径。"""
    message = str(error).replace(api_key, "[REDACTED]") if api_key else str(error)
    message = re.sub(r"[A-Za-z]:\\[^\s]+", "[local-path]", message)
    return message[:300]


def _render_page(source: Path, page_no: int) -> tuple[bytes, str]:
    with fitz.open(source) as document:
        if page_no < 1 or page_no > len(document):
            raise ValueError(f"page_no 必须位于 1 到 {len(document)}")
        page = document[page_no - 1]
        pixmap = page.get_pixmap(matrix=fitz.Matrix(200 / 72, 200 / 72), alpha=False)
        return pixmap.tobytes("png"), page.get_text("text").strip()


async def run_preflight(
    source: Path,
    page_no: int,
    provider: PreflightVisionProvider,
    primary_model: str,
    fallback_model: str,
    data_dir: Path,
    api_key: str = "",
) -> dict[str, Any]:
    """执行主模型和可选回退模型，并返回不含论文正文的可提交摘要。"""
    payload = source.read_bytes()
    artifact_id = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    image_bytes, raw_text = _render_page(source, page_no)
    artifact_dir = data_dir / artifact_id.removeprefix("sha256:") / f"page-{page_no:03d}"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    attempts: list[dict[str, Any]] = []

    async def attempt(model: str, method: Callable[[bytes, str, int], Awaitable[dict[str, Any]]], artifact_name: str) -> bool:
        started_at = time.perf_counter()
        try:
            result = await method(image_bytes, raw_text, page_no)
            if not isinstance(result, dict):
                raise ValueError("视觉模型输出不是 JSON 对象")
            (artifact_dir / artifact_name).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            attempts.append(
                {
                    "model": model,
                    "status": "success",
                    "duration_seconds": round(time.perf_counter() - started_at, 3),
                    "response_artifact": f"{artifact_id}/page-{page_no:03d}/{artifact_name}",
                    "markdown_chars": len(str(result.get("markdown") or "")),
                    "block_count": len(result.get("blocks") or []),
                }
            )
            return True
        except Exception as exc:
            attempts.append(
                {
                    "model": model,
                    "status": "failed",
                    "duration_seconds": round(time.perf_counter() - started_at, 3),
                    "error_type": type(exc).__name__,
                    "error_summary": _safe_error(exc, api_key),
                }
            )
            return False

    primary_ok = await attempt(primary_model, provider.parse_page_primary, "primary.json")
    fallback_ok: bool | None = None
    if not primary_ok and fallback_model != primary_model:
        fallback_ok = await attempt(fallback_model, provider.parse_page_fallback, "fallback.json")
    status = "primary_available" if primary_ok else "fallback_only" if fallback_ok else "unavailable"
    return {
        "experiment_id": "parser-v1-preflight",
        "artifact_id": artifact_id,
        "page_no": page_no,
        "status": status,
        "primary_available": primary_ok,
        "fallback_available": fallback_ok,
        "model_calls": len(attempts),
        "max_model_calls": 2,
        "attempts": attempts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--page-no", type=int, default=1)
    parser.add_argument("--data-dir", type=Path, default=Path("data/evaluation/preflight"))
    parser.add_argument("--report", type=Path, default=Path("evaluation/reports/parser-v1-preflight.json"))
    args = parser.parse_args()

    settings = Settings().model_copy(update={"model_call_budget": 2})
    provider = BailianProvider(settings)
    report = asyncio.run(
        run_preflight(
            args.pdf,
            args.page_no,
            provider,
            settings.vision_model,
            settings.vision_fallback_model,
            args.data_dir,
            settings.api_key,
        )
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "model_calls": report["model_calls"]}, ensure_ascii=False))
    if report["status"] != "primary_available":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
