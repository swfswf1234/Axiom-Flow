"""
模块职责：定义应用用例依赖的解析供应商与执行流水线端口。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
关联测试：tests/contract/test_architecture_dependencies.py、tests/integration/test_jobs.py
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


class VisionProvider(Protocol):
    """把单页图像归一化为页面事实的供应商端口。"""

    calls: int

    async def parse_page(self, image_bytes: bytes, raw_text: str, page_no: int) -> dict[str, Any]: ...


class KnowledgeProvider(Protocol):
    """从已接受正文生成知识候选的供应商端口。"""

    calls: int

    async def extract_knowledge(self, markdown: str) -> dict[str, Any]: ...


class DocumentPipeline(Protocol):
    """应用任务可调用的文档处理流水线端口。"""

    def import_pdf(self, source: Path, original_filename: str | None = None) -> dict[str, Any]: ...

    async def parse_document(
        self, document_id: str, job_id: str | None = None,
        progress: Callable[[int, int], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        page_start: int = 1, page_end: int | None = None,
    ) -> dict[str, Any]: ...

    async def generate_candidates(
        self, document_id: str, job_id: str | None = None,
    ) -> list[dict[str, Any]]: ...


PipelineFactory = Callable[[int | None], DocumentPipeline]
ProviderFactory = Callable[[], tuple[VisionProvider, KnowledgeProvider]]
