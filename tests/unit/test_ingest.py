"""ingest 单元测试：PDF 导入、页图渲染与 book.json 生成（设计文档 §组件职责）。"""

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from axiom_flow.ingest import default_data_dir, import_book
from axiom_flow.schemas import Strategy

REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_pdf(path: Path, pages: int = 3, text: str = "QED ingest test") -> Path:
    """用 pymupdf 构造 pages 页小 PDF（测试环境无真实 PDF 样本）。"""
    doc = pymupdf.open()
    try:
        for _ in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), text)
        doc.save(path)
    finally:
        doc.close()
    return path


class TestImportBook:
    """导入主流程：产物结构、SHA-256、幂等覆盖与异常。"""

    def test_import_success_writes_book_json_and_pages(self, tmp_path):
        source = _make_pdf(tmp_path / "source.pdf", pages=3)
        data_dir = tmp_path / "data"

        meta = import_book(source, "01-test-book", "Test Book", "T. Author", Strategy.LOCAL, data_dir)

        book_dir = data_dir / "books" / "01-test-book"
        assert meta.book_id == "01-test-book"
        assert meta.title == "Test Book"
        assert meta.author == "T. Author"
        assert meta.page_count == 3
        assert len(meta.sha256) == 64
        assert meta.strategy == Strategy.LOCAL
        raw = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
        assert raw["book_id"] == "01-test-book"
        assert raw["title"] == "Test Book"
        assert raw["author"] == "T. Author"
        assert raw["page_count"] == 3
        assert raw["sha256"] == meta.sha256
        assert raw["strategy"] == "local"
        pngs = sorted(book_dir.glob("pages/*.png"))
        assert [p.name for p in pngs] == ["p0001.png", "p0002.png", "p0003.png"]
        assert all(p.stat().st_size > 0 for p in pngs)

    def test_sha256_matches_source_pdf(self, tmp_path):
        source = _make_pdf(tmp_path / "source.pdf", pages=2, text="hash me")

        meta = import_book(source, "hash-book", "T", None, Strategy.HYBRID, tmp_path / "data")

        assert meta.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()

    def test_idempotent_reimport_overwrites_artifacts(self, tmp_path):
        data_dir = tmp_path / "data"
        first = _make_pdf(tmp_path / "first.pdf", pages=2, text="version one")
        second = _make_pdf(tmp_path / "second.pdf", pages=5, text="version two")

        meta1 = import_book(first, "01-book", "T", None, Strategy.LOCAL, data_dir)
        meta2 = import_book(second, "01-book", "T", None, Strategy.LOCAL, data_dir)

        book_dir = data_dir / "books" / "01-book"
        assert meta2.page_count == 5
        assert meta2.sha256 != meta1.sha256
        raw = json.loads((book_dir / "book.json").read_text(encoding="utf-8"))
        assert raw["sha256"] == meta2.sha256
        assert len(list(book_dir.glob("pages/*.png"))) == 5

    def test_rejects_non_pdf(self, tmp_path):
        fake = tmp_path / "fake.pdf"
        fake.write_bytes(b"definitely not a pdf")

        with pytest.raises(ValueError):
            import_book(fake, "bad-book", "T", None, Strategy.LOCAL, tmp_path / "data")

    def test_rejects_missing_source(self, tmp_path):
        with pytest.raises(ValueError):
            import_book(tmp_path / "nope.pdf", "bad-book", "T", None, Strategy.LOCAL, tmp_path / "data")


class TestDefaultDataDir:
    """数据根默认值：AXIOM_FLOW_DATA_DIR 覆盖，未设置时回退仓库 data/。"""

    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AXIOM_FLOW_DATA_DIR", str(tmp_path / "custom"))

        assert default_data_dir() == tmp_path / "custom"

    def test_falls_back_to_repo_data(self, monkeypatch):
        monkeypatch.delenv("AXIOM_FLOW_DATA_DIR", raising=False)

        assert default_data_dir() == REPO_ROOT / "data"

    def test_import_uses_env_data_dir_when_not_passed(self, monkeypatch, tmp_path):
        source = _make_pdf(tmp_path / "source.pdf", pages=1)
        monkeypatch.setenv("AXIOM_FLOW_DATA_DIR", str(tmp_path / "env-data"))

        meta = import_book(source, "env-book", "T", None, Strategy.LOCAL)

        assert meta.book_id == "env-book"
        assert (tmp_path / "env-data" / "books" / "env-book" / "book.json").is_file()
