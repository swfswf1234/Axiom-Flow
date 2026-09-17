"""ingest 单元测试：源 PDF → 150 DPI 页图 + book.json + manifest.json（parsed/ 布局）。"""

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from axiom_flow.ingest import book_dir_for, default_data_dir, ingest_book
from axiom_flow.schemas import BookMeta

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


def _source_pdf(tmp_path: Path, name: str = "source.pdf", pages: int = 3, text: str = "QED ingest test") -> Path:
    """在数据根 raw/ 布局下构造源 PDF，返回数据根相对路径。"""
    raw_dir = tmp_path / "data" / "raw" / "math-advanced" / "math_analysis"
    raw_dir.mkdir(parents=True, exist_ok=True)
    _make_pdf(raw_dir / name, pages=pages, text=text)
    return f"raw/math-advanced/math_analysis/{name}"


class TestIngestBook:
    """ingest 主流程：产物结构、SHA-256、幂等覆盖与异常。"""

    def test_ingest_writes_parsed_layout(self, tmp_path):
        data_root = tmp_path / "data"
        source_file = _source_pdf(tmp_path)

        result = ingest_book(
            book_id="mathanalysis-b05",
            source_file=source_file,
            data_root=data_root,
            domain_id="math-advanced",
            course_id="math_analysis",
            title="数学分析原理",
            authors=[{"name": "Walter Rudin", "role": "author"}],
        )

        book_dir = data_root / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"
        assert result.book_dir == book_dir
        assert result.page_count == 3
        meta = BookMeta.model_validate_json((book_dir / "book.json").read_text(encoding="utf-8"))
        assert meta.book_id == "mathanalysis-b05"
        assert meta.title == "数学分析原理"
        assert meta.authors[0].name == "Walter Rudin"
        assert meta.source_file == source_file
        assert meta.engine == "mineru"
        assert meta.ingested_at is not None
        pngs = sorted(book_dir.glob("pages/*.png"))
        assert [p.name for p in pngs] == ["p0001.png", "p0002.png", "p0003.png"]
        assert all(p.stat().st_size > 0 for p in pngs)

    def test_manifest_lists_artifacts_without_self(self, tmp_path):
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)

        entries = json.loads((result.book_dir / "manifest.json").read_text(encoding="utf-8"))
        paths = [entry["path"] for entry in entries]
        assert "book.json" in paths
        assert "pages/p0001.png" in paths
        assert "manifest.json" not in paths
        assert all(len(entry["sha256"]) == 64 for entry in entries)

    def test_sha256_matches_source_pdf(self, tmp_path):
        data_root = tmp_path / "data"
        source_file = _source_pdf(tmp_path, name="hash.pdf", pages=2, text="hash me")

        result = ingest_book(book_id="hash-book", source_file=source_file, data_root=data_root)

        assert result.sha256 == hashlib.sha256((data_root / source_file).read_bytes()).hexdigest()

    def test_idempotent_reingest_replaces_artifacts(self, tmp_path):
        data_root = tmp_path / "data"
        first = _source_pdf(tmp_path, name="first.pdf", pages=2, text="version one")
        second = _source_pdf(tmp_path, name="second.pdf", pages=5, text="version two")

        meta1 = ingest_book(book_id="b1", source_file=first, data_root=data_root)
        meta2 = ingest_book(book_id="b1", source_file=second, data_root=data_root)

        book_dir = meta2.book_dir
        assert meta2.page_count == 5
        assert meta2.sha256 != meta1.sha256
        assert len(list(book_dir.glob("pages/*.png"))) == 5
        assert not list(book_dir.parent.glob(".b1.tmp-*"))  # 临时目录已清理

    def test_rejects_non_pdf(self, tmp_path):
        data_root = tmp_path / "data"
        raw = data_root / "raw" / "fake.pdf"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"definitely not a pdf")

        with pytest.raises(ValueError, match="不是有效的 PDF"):
            ingest_book(book_id="bad", source_file="raw/fake.pdf", data_root=data_root)

    def test_rejects_missing_source(self, tmp_path):
        with pytest.raises(ValueError, match="源 PDF 不存在"):
            ingest_book(book_id="bad", source_file="raw/nope.pdf", data_root=tmp_path / "data")

    def test_book_dir_for_matches_layout(self, tmp_path):
        assert book_dir_for(tmp_path, "d", "c", "b") == tmp_path / "parsed" / "d" / "c" / "b"


class TestDefaultDataDir:
    """数据根默认值：QED_DATA_ROOT 覆盖，未设置时回退仓库 data/。"""

    def test_data_root_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QED_DATA_ROOT", str(tmp_path / "dataset-root"))

        assert default_data_dir() == tmp_path / "dataset-root"

    def test_legacy_flow_var_is_ignored(self, monkeypatch, tmp_path):
        """AXIOM_FLOW_DATA_DIR 已删除（配置简化）：设置旧键不生效。"""
        monkeypatch.delenv("QED_DATA_ROOT", raising=False)
        monkeypatch.setenv("AXIOM_FLOW_DATA_DIR", str(tmp_path / "flow"))

        assert default_data_dir() == REPO_ROOT / "data"

    def test_falls_back_to_repo_data(self, monkeypatch):
        monkeypatch.delenv("QED_DATA_ROOT", raising=False)

        assert default_data_dir() == REPO_ROOT / "data"
