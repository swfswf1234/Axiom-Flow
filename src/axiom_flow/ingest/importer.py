"""ingest 导入逻辑：PDF 导入、页图渲染与 book.json 生成（设计文档 §组件职责）。

设计关联（DesignRef）：docs/design/pdf-parsing-and-rendering.md
实现状态：Current
关联测试：tests/unit/test_ingest.py
"""

import hashlib
import os
import shutil
import uuid
from pathlib import Path

import pymupdf

from axiom_flow.schemas import BookMeta, Strategy


def default_data_dir() -> Path:
    """数据根目录：环境变量 AXIOM_FLOW_DATA_DIR 优先，未设置时回退仓库内 data/。"""
    override = os.environ.get("AXIOM_FLOW_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data"


def _sha256_of(path: Path) -> str:
    """流式计算文件 SHA-256（分块读入，避免大 PDF 整读内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_book(
    source_pdf: Path,
    book_id: str,
    title: str,
    author: str | None,
    strategy: Strategy,
    data_dir: Path | None = None,
) -> BookMeta:
    """导入源 PDF：渲染原页高清图 + 生成 book.json，返回书目元数据。

    - 源 PDF 只读（dataset/ 语义），不复制；sha256 记录源文件校验值。
    - 幂等覆盖：同 book_id 重导入时先写临时目录再原子替换，不留半成品。
    """
    if not source_pdf.is_file():
        raise ValueError(f"源 PDF 不存在：{source_pdf}")
    data_dir = Path(data_dir) if data_dir is not None else default_data_dir()
    digest = _sha256_of(source_pdf)

    book_dir = data_dir / "books" / book_id
    tmp_dir = data_dir / "books" / f".{book_id}.tmp-{uuid.uuid4().hex[:8]}"
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
                pix = doc[index].get_pixmap(dpi=150)
                pix.save(pages_dir / f"p{index + 1:04d}.png")
            page_count = doc.page_count
        meta = BookMeta(
            book_id=book_id,
            title=title,
            author=author,
            page_count=page_count,
            sha256=digest,
            strategy=strategy,
        )
        (tmp_dir / "book.json").write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
        if book_dir.exists():
            shutil.rmtree(book_dir)
        tmp_dir.rename(book_dir)
        return meta
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
