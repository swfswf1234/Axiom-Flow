"""ingest：预处理——源 PDF → 150 DPI 页图 + book.json / manifest.json（不调模型）。

设计关联（DesignRef）：docs/design/parsing-pipeline.md
实现状态：Current
关联测试：tests/unit/test_ingest.py
"""

from axiom_flow.ingest.artifacts import (
    atomic_write_bytes,
    atomic_write_text,
    book_dir_for,
    build_manifest,
    ensure_safe_segment,
    ensure_within,
    png_size,
    read_book_meta,
    sha256_file,
    write_book_meta,
    write_json,
    write_manifest,
)
from axiom_flow.ingest.importer import (
    DEFAULT_DPI,
    IngestResult,
    ingest_book,
    resolve_source_pdf,
    write_page_markdown,
)

__all__ = [
    "DEFAULT_DPI",
    "IngestResult",
    "atomic_write_bytes",
    "atomic_write_text",
    "book_dir_for",
    "build_manifest",
    "ensure_safe_segment",
    "ensure_within",
    "ingest_book",
    "png_size",
    "read_book_meta",
    "resolve_source_pdf",
    "sha256_file",
    "write_book_meta",
    "write_json",
    "write_manifest",
    "write_page_markdown",
]
