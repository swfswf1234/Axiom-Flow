"""ingest 导入逻辑：源 PDF（数据根相对）→ 150 DPI 页图 + book.json + manifest.json。

设计关联（DesignRef）：docs/design/parsing-pipeline.md（预处理阶段）。
产物落 `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`；源 PDF 只读不复制；
幂等覆盖：先写临时目录再原子替换，不留半成品。
"""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from axiom_flow.db.engine import utc_now
from axiom_flow.ingest.artifacts import (
    atomic_write_text,
    book_dir_for,
    sha256_file,
    write_book_meta,
    write_manifest,
)
from axiom_flow.schemas import Author, BookMeta

DEFAULT_DPI = 150


def default_data_dir() -> Path:
    """数据根目录：QED_DATA_ROOT 覆盖，未设置时回退仓库内 data/（配置简化，PLAN-002）。"""
    override = os.environ.get("QED_DATA_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


@dataclass(frozen=True, slots=True)
class IngestResult:
    """ingest 结果：页数、源文件哈希与产物目录。"""

    book_id: str
    page_count: int
    sha256: str
    book_dir: Path


def resolve_source_pdf(data_root: Path | str, source_file: str) -> Path:
    """源 PDF 绝对路径：数据根相对路径（同源 qt_books.file_path）；绝对路径原样使用。"""
    path = Path(source_file)
    return path if path.is_absolute() else Path(data_root) / path


def _normalize_authors(authors: Sequence[Author | Mapping[str, str]]) -> list[Author]:
    return [author if isinstance(author, Author) else Author.model_validate(author) for author in authors]


def ingest_book(
    *,
    book_id: str,
    source_file: str,
    data_root: Path | str,
    domain_id: str = "",
    course_id: str = "",
    title: str = "",
    authors: Sequence[Author | Mapping[str, str]] = (),
    engine: str = "mineru",
    dpi: int = DEFAULT_DPI,
) -> IngestResult:
    """渲染页图并生成 book.json / manifest.json，返回 ingest 结果。

    - 源 PDF 只读（`raw/` 语义），不复制；sha256 记录源文件校验值；
    - 幂等覆盖：同 book_id 重导入时先写同层临时目录再原子替换；
    - 不调模型（解析由 orchestrator 提交任务触发）。
    """
    source_pdf = resolve_source_pdf(data_root, source_file)
    if not source_pdf.is_file():
        raise ValueError(f"源 PDF 不存在：{source_pdf}")
    digest = sha256_file(source_pdf)

    book_dir = book_dir_for(data_root, domain_id, course_id, book_id)
    book_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = book_dir.parent / f".{book_id}.tmp-{uuid.uuid4().hex[:8]}"
    try:
        pages_dir = tmp_dir / "pages"
        pages_dir.mkdir(parents=True)
        try:
            doc = pymupdf.open(source_pdf)
        except pymupdf.FileDataError as exc:
            raise ValueError(f"不是有效的 PDF 文件：{source_pdf}") from exc
        with doc:
            if doc.needs_pass:
                raise ValueError(f"PDF 已加密，无法渲染：{source_pdf}")
            if doc.page_count == 0:
                raise ValueError(f"PDF 无页面：{source_pdf}")
            for index in range(doc.page_count):
                pix = doc[index].get_pixmap(dpi=dpi)
                pix.save(pages_dir / f"p{index + 1:04d}.png")
            page_count = doc.page_count
        meta = BookMeta(
            book_id=book_id,
            title=title,
            authors=_normalize_authors(authors),
            page_count=page_count,
            sha256=digest,
            source_file=source_file,
            engine=engine,
            ingested_at=utc_now(),
        )
        write_book_meta(tmp_dir, meta)
        write_manifest(tmp_dir)
        if book_dir.exists():
            shutil.rmtree(book_dir)
        tmp_dir.rename(book_dir)
        return IngestResult(book_id=book_id, page_count=page_count, sha256=digest, book_dir=book_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def write_page_markdown(book_dir: Path, page_no: int, markdown: str) -> Path:
    """页级 Markdown 原子写入（`pages/pXXXX.md`）。"""
    path = book_dir / "pages" / f"p{page_no:04d}.md"
    atomic_write_text(path, markdown.rstrip("\n") + "\n")
    return path
