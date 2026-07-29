"""
模块职责：验证百炼 OCR 单模型连通性预检和脱敏报告。
设计关联（DesignRef）：docs/design/evaluation-governance.md
实现状态：Current
被测代码：evaluation/preflight.py、src/axiom_flow/infrastructure/bailian.py
"""

import asyncio
from pathlib import Path

import fitz

from evaluation.preflight import run_preflight


class FakePreflightProvider:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls = 0

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict:
        self.calls += 1
        if self.error:
            raise self.error
        return {"markdown": "不应出现在报告中的完整正文", "blocks": [{"kind": "paragraph"}]}


def _pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "A local preflight page")
    document.save(path)
    document.close()


def test_primary_success_writes_only_local_response_and_redacted_summary(tmp_path: Path):
    source = tmp_path / "sample.pdf"
    _pdf(source)
    provider = FakePreflightProvider()

    report = asyncio.run(run_preflight(source, 1, provider, "qwen-vl-ocr", tmp_path / "artifacts"))

    assert report["status"] == "available"
    assert report["model_calls"] == 1
    assert "不应出现在报告中的完整正文" not in str(report)
    artifact = tmp_path / "artifacts" / report["artifact_id"].removeprefix("sha256:") / "page-001" / "response.json"
    assert artifact.is_file()


def test_failure_does_not_use_fallback_and_masks_key(tmp_path: Path):
    source = tmp_path / "sample.pdf"
    _pdf(source)
    provider = FakePreflightProvider(RuntimeError("request failed token-value"))

    report = asyncio.run(run_preflight(
        source, 1, provider, "qwen-vl-ocr", tmp_path / "artifacts", api_key="token-value",
    ))

    assert report["status"] == "unavailable"
    assert report["model_calls"] == 1
    assert provider.calls == 1
    assert report["attempts"][0]["error_summary"] == "request failed [REDACTED]"


def test_failure_does_not_expose_source_path(tmp_path: Path):
    source = tmp_path / "sample.pdf"
    _pdf(source)
    provider = FakePreflightProvider(RuntimeError(str(source)))

    report = asyncio.run(run_preflight(source, 1, provider, "qwen-vl-ocr", tmp_path / "artifacts"))

    assert report["status"] == "unavailable"
    assert report["model_calls"] == 1
    assert str(source) not in str(report)
