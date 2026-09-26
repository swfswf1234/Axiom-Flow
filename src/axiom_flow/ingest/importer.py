"""ingest 导入逻辑：源 PDF（数据根相对）→ 150 DPI 页图（根视图唯一产物 = `pages/p*.png`）。

设计关联（DesignRef）：docs/design/parsing-pipeline.md（预处理阶段）。
产物落 `<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`；源 PDF 只读不复制；
幂等覆盖：先写临时目录再原子替换，不留半成品。
REQ-097（PLAN-017 W2）：ingest 不再写根 `book.json`/`manifest.json`——书目元数据由调用方
回写 af_books（唯一事实源），产物正本只在 `versions/<job_id>/`。
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from axiom_flow.ingest.artifacts import (
    TRANSIENT_OLD_INFIX,
    TRANSIENT_TMP_INFIX,
    atomic_write_text,
    book_dir_for,
    ensure_within,
    sha256_file,
)

DEFAULT_DPI = 150


@dataclass(frozen=True, slots=True)
class IngestResult:
    """ingest 结果：页数、源文件哈希与产物目录。"""

    book_id: str
    page_count: int
    sha256: str
    book_dir: Path


def resolve_source_pdf(data_root: Path | str, source_file: str) -> Path:
    """源 PDF 路径（数据根相对，同源 `qt_books.file_path`）。

    绝对路径同样限在数据根之内（`PP-5`）：越界一律 `ValueError`，避免把数据根外的
    文件当作教材读入或写入。
    """
    path = Path(source_file)
    candidate = path if path.is_absolute() else Path(data_root) / path
    return ensure_within(data_root, candidate)


def _swap_root_view(book_dir: Path, staging_dir: Path) -> None:
    """原子替换**根视图页图**：只交换 `pages/`，`versions/` 完整保留（V8/L3）。

    解析版本目录里的页图是根页图的硬链接，旧实现整本 `rmtree` 会把历史版本一并删掉——
    那等于不可逆地删除已解析结果。中途失败时把旧 `pages/` 换回原位，不留半成品。
    旧扁平布局文件（`book.json`/`manifest.json`/`chunks.json`）随替换一并清退：REQ-097 后
    根视图只有页图，留着就是第二份陈旧事实源，读取端会把它当正本。
    """
    book_dir.mkdir(parents=True, exist_ok=True)
    old_pages = book_dir / "pages"
    trash = book_dir.parent / f".{book_dir.name}{TRANSIENT_OLD_INFIX}{uuid.uuid4().hex[:8]}"
    if old_pages.is_dir():
        old_pages.rename(trash)
    try:
        (staging_dir / "pages").rename(book_dir / "pages")
    except Exception:
        if trash.is_dir() and not old_pages.exists():
            trash.rename(old_pages)
        raise
    shutil.rmtree(trash, ignore_errors=True)
    for stale in ("book.json", "manifest.json", "chunks.json"):
        (book_dir / stale).unlink(missing_ok=True)


def ingest_book(
    *,
    book_id: str,
    source_file: str,
    data_root: Path | str,
    domain_id: str = "",
    course_id: str = "",
    dpi: int = DEFAULT_DPI,
) -> IngestResult:
    """渲染页图（根视图唯一产物 = `pages/p*.png`），返回页数与源文件校验值。

    - 源 PDF 只读（`raw/` 语义），不复制；sha256 记录源文件校验值；
    - 幂等覆盖：同 book_id 重导入时先写同层临时目录再原子替换；
    - 不调模型（解析由 orchestrator 提交任务触发）；
    - 不回写库：页数/哈希由调用方经 `BookRepository.update_ingest` 落 af_books。
    """
    source_pdf = resolve_source_pdf(data_root, source_file)
    if not source_pdf.is_file():
        raise ValueError(f"源 PDF 不存在：{source_pdf}")
    digest = sha256_file(source_pdf)

    book_dir = book_dir_for(data_root, domain_id, course_id, book_id)
    book_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = book_dir.parent / f".{book_id}{TRANSIENT_TMP_INFIX}{uuid.uuid4().hex[:8]}"
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
        _swap_root_view(book_dir, tmp_dir)
        return IngestResult(book_id=book_id, page_count=page_count, sha256=digest, book_dir=book_dir)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def write_page_markdown(book_dir: Path, page_no: int, markdown: str) -> Path:
    """页级 Markdown 原子写入（`pages/p<NNNN>.md`）。"""
    path = book_dir / "pages" / f"p{page_no:04d}.md"
    atomic_write_text(path, markdown.rstrip("\n") + "\n")
    return path
