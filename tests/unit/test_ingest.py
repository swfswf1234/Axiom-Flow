"""ingest 单元测试：源 PDF → 150 DPI 页图 + book.json + manifest.json（parsed/ 布局）。"""

import hashlib
import os
from pathlib import Path

import pymupdf
import pytest

from axiom_flow.ingest import (
    book_dir_for,
    build_manifest,
    ingest_book,
    is_transient_name,
    link_pages_into,
    resolve_source_pdf,
    scan_transient_residue,
    seed_version_from,
    version_dir_for,
    write_json,
)


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


class TestTransientResidueRecognition:
    """PP-6 瞬态残留三式机械识别（PLAN-016 W5）：只读枚举、只告警不清理（Q3 裁决）。"""

    def test_three_forms_recognized(self):
        names = (".book.json.tmp-ab12cd34", ".b05.old-deadbeef", ".b05.tmp-0badf00d")

        assert all(is_transient_name(name) for name in names)

    def test_regular_and_plain_hidden_not_recognized(self):
        for name in ("book.json", "p0001.md", "manifest.json", "chunks.json", ".notes"):
            assert not is_transient_name(name)

    def test_scan_finds_residue_skips_clean_files(self, tmp_path):
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / ".p0001.md.tmp-cafe1234").write_text("x", encoding="utf-8")
        (tmp_path / ".b05.tmp-0badf00d").mkdir()
        (tmp_path / "book.json").write_text("{}", encoding="utf-8")

        found = scan_transient_residue(tmp_path)

        assert [path.name for path in found] == [".b05.tmp-0badf00d", ".p0001.md.tmp-cafe1234"]

    def test_scan_missing_root_returns_empty(self, tmp_path):
        assert scan_transient_residue(tmp_path / "absent") == []


class TestIngestBook:
    """ingest 主流程：根视图只写 `pages/p*.png`、SHA-256、幂等覆盖与异常。

    REQ-097（PLAN-017 W2）：`book.json`/`manifest.json` 不再落根视图——书目元数据的
    单一事实源是 af_books，产物正本只在 `versions/<job_id>/`。
    """

    def test_ingest_writes_only_page_images_into_root_view(self, tmp_path):
        data_root = tmp_path / "data"
        source_file = _source_pdf(tmp_path)

        result = ingest_book(
            book_id="mathanalysis-b05",
            source_file=source_file,
            data_root=data_root,
            domain_id="math-advanced",
            course_id="math_analysis",
        )

        book_dir = data_root / "parsed" / "math-advanced" / "math_analysis" / "mathanalysis-b05"
        assert result.book_dir == book_dir
        assert result.page_count == 3
        pngs = sorted(book_dir.glob("pages/*.png"))
        assert [p.name for p in pngs] == ["p0001.png", "p0002.png", "p0003.png"]
        assert all(p.stat().st_size > 0 for p in pngs)
        # 根视图除页图外不写任何东西（含派生的 chunks.json）
        stray = [
            p
            for p in book_dir.rglob("*")
            if p.is_file() and p.name not in {q.name for q in pngs}
        ]
        assert stray == []
        assert not (book_dir / "book.json").exists()
        assert not (book_dir / "manifest.json").exists()

    def test_ingest_writes_no_root_manifest(self, tmp_path):
        """manifest 只属于版本目录：ingest 后根视图上不得出现 manifest.json。"""
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)

        assert not (result.book_dir / "manifest.json").exists()
        assert not (result.book_dir / "chunks.json").exists()

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

    def test_reingest_preserves_versions_directory(self, tmp_path):
        """V8/L3：重新 ingest 只替换根视图页图，历史解析版本目录保留。

        旧实现 `rmtree(book_dir)` 会连 `versions/` 一起删掉——那等于把已解析结果永久删除。
        旧布局残留的根 `book.json`/`manifest.json` 在替换时一并清退：根视图一旦允许残留，
        「正本只在 versions/」的前提就名存实亡。
        """
        data_root = tmp_path / "data"
        book_dir = ingest_book(
            book_id="b1", source_file=_source_pdf(tmp_path, name="first.pdf", pages=2), data_root=data_root
        ).book_dir
        version_dir = book_dir / "versions" / "job-a"
        (version_dir / "pages").mkdir(parents=True)
        (version_dir / "pages" / "p0001.blocks.json").write_text("{}", encoding="utf-8")
        (book_dir / "chunks.json").write_text('{"book_id": "b1"}', encoding="utf-8")
        (book_dir / "book.json").write_text('{"book_id": "b1"}', encoding="utf-8")
        (book_dir / "manifest.json").write_text("[]", encoding="utf-8")

        ingest_book(
            book_id="b1",
            source_file=_source_pdf(tmp_path, name="second.pdf", pages=3, text="version two"),
            data_root=data_root,
        )

        assert (version_dir / "pages" / "p0001.blocks.json").is_file()
        assert len(list(book_dir.glob("pages/*.png"))) == 3
        assert not (book_dir / "chunks.json").exists(), "旧根视图的派生文件一并作废"
        assert not (book_dir / "book.json").exists(), "旧布局的根 book.json 随替换清退"
        assert not (book_dir / "manifest.json").exists(), "旧布局的根 manifest.json 随替换清退"
        assert not list(book_dir.parent.glob(".b1.tmp-*"))
        assert not list(book_dir.parent.glob(".b1.old-*"))

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

    def test_book_dir_for_rejects_traversal_segment(self, tmp_path):
        """`PP-5`：book_id 必须是单一路径段（schemas 之外的纵深防御，含数据根内的 `..`）。"""
        with pytest.raises(ValueError, match="非法 book_id"):
            book_dir_for(tmp_path / "data", "d", "c", "../../escape")
        with pytest.raises(ValueError, match="非法 domain_id"):
            book_dir_for(tmp_path / "data", "d/../../x", "c", "b01")

    def test_resolve_source_pdf_rejects_absolute_path_outside_root(self, tmp_path):
        outside = tmp_path / "elsewhere" / "book.pdf"
        outside.parent.mkdir(parents=True)
        _make_pdf(outside)

        with pytest.raises(ValueError, match="越出数据根"):
            resolve_source_pdf(tmp_path / "data", str(outside))

    def test_resolve_source_pdf_rejects_relative_escape(self, tmp_path):
        with pytest.raises(ValueError, match="越出数据根"):
            resolve_source_pdf(tmp_path / "data", "../outside.pdf")

    def test_resolve_source_pdf_accepts_within_root(self, tmp_path):
        source_file = _source_pdf(tmp_path)

        assert resolve_source_pdf(tmp_path / "data", source_file).is_file()


class TestVersionDir:
    """解析版本目录（PLAN-004 / REQ-080）：`<book_dir>/versions/<job_id>/` 与页图硬链接。"""

    def test_version_dir_lands_under_book_dir(self, tmp_path):
        root = tmp_path / "data"

        version_dir = version_dir_for(root, "d", "c", "b05", "job1")

        assert version_dir == root / "parsed" / "d" / "c" / "b05" / "versions" / "job1"

    def test_version_dir_rejects_unsafe_job_id(self, tmp_path):
        """job_id 来自任务行但仍可能是历史脏值：与 book_id 同一道段规则闸。"""
        with pytest.raises(ValueError, match="非法 job_id"):
            version_dir_for(tmp_path / "data", "d", "c", "b05", "../../escape")

    def test_build_manifest_skips_versions_subtree(self, tmp_path):
        """清单只描述目录内文件：`versions/` 子树不入任何清单（L6）。

        根视图不再有 manifest.json（REQ-097），但判据保留在 `build_manifest` 上：误把书目录
        传进来时不得递归全部历史版本重算哈希。
        """
        book_dir = tmp_path / "b1"
        (book_dir / "pages").mkdir(parents=True)
        (book_dir / "pages" / "p0001.png").write_bytes(b"png")
        version_dir = book_dir / "versions" / "job1"
        (version_dir / "pages").mkdir(parents=True)
        (version_dir / "pages" / "p0001.blocks.json").write_text("{}", encoding="utf-8")

        paths = [entry.path for entry in build_manifest(book_dir)]

        assert paths == ["pages/p0001.png"]

    def test_version_manifest_is_relative_to_version_dir(self, tmp_path):
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        version_dir = result.book_dir / "versions" / "job1"
        (version_dir / "pages").mkdir(parents=True)
        (version_dir / "pages" / "p0001.blocks.json").write_text("{}", encoding="utf-8")

        paths = [entry.path for entry in build_manifest(version_dir)]

        assert paths == ["pages/p0001.blocks.json"]

    def test_link_pages_into_hardlinks_root_page_image(self, tmp_path):
        """页图硬链接而非复制：N 个版本不应让磁盘上的页图成倍增长（V4）。"""
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        version_dir = result.book_dir / "versions" / "job1"

        linked = link_pages_into(result.book_dir / "pages", version_dir / "pages", 1)

        assert linked == version_dir / "pages" / "p0001.png"
        assert linked.is_file()
        if linked.stat().st_nlink > 1:  # 平台支持硬链接时校验同一 inode
            assert linked.stat().st_ino == (result.book_dir / "pages" / "p0001.png").stat().st_ino

    def test_link_pages_into_falls_back_to_copy(self, tmp_path, monkeypatch):
        """不支持硬链接的平台：回落到复制，产物仍可读（跨平台契约）。"""

        def _no_link(*_args, **_kwargs):
            raise OSError("link not supported")

        monkeypatch.setattr(os, "link", _no_link)
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        version_dir = result.book_dir / "versions" / "job9"

        linked = link_pages_into(result.book_dir / "pages", version_dir / "pages", 2)

        assert linked.read_bytes() == (result.book_dir / "pages" / "p0002.png").read_bytes()

    def test_link_pages_into_missing_root_image_returns_none(self, tmp_path):
        """根视图没有该页图时不造空文件：让上层按页失败，而不是产出损坏版本。"""
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)

        linked = link_pages_into(
            result.book_dir / "pages", result.book_dir / "versions" / "j" / "pages", 99
        )

        assert linked is None
        assert not (result.book_dir / "versions" / "j" / "pages" / "p0099.png").exists()


class TestSeedVersion:
    """版本基线写时复制：新版本从上一生效作用域继承产物，缺一页就永远不齐（V3 激活门槛的前提）。"""

    def test_seed_links_previous_artifacts(self, tmp_path):
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        (result.book_dir / "pages" / "p0001.blocks.json").write_text("{}", encoding="utf-8")
        version_dir = result.book_dir / "versions" / "job2"

        seeded = seed_version_from(result.book_dir, version_dir)

        assert (version_dir / "pages" / "p0001.png").is_file()
        assert (version_dir / "pages" / "p0001.blocks.json").is_file()
        assert seeded == 4  # 3 页图 + 1 页 blocks（根视图不再有 book.json，REQ-097）
        if (version_dir / "pages" / "p0001.png").stat().st_nlink > 1:
            assert (version_dir / "pages" / "p0001.png").stat().st_ino == (
                result.book_dir / "pages" / "p0001.png"
            ).stat().st_ino

    def test_seed_never_inherits_book_json(self, tmp_path):
        """`book.json` 只由本次任务从 af_books 生成（PP-15/W3）：即便基线目录还留着旧文件也不继承。

        旧版本目录的 book.json 记的是旧引擎/旧时刻，继承过来就是第二份互相矛盾的正本。
        """
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        (result.book_dir / "book.json").write_text('{"book_id": "b1"}', encoding="utf-8")
        version_dir = result.book_dir / "versions" / "job2"

        seeded = seed_version_from(result.book_dir, version_dir)

        assert not (version_dir / "book.json").exists()
        assert seeded == 3  # 只有 3 张页图

    def test_seed_skips_derived_and_previous_versions(self, tmp_path):
        """manifest/chunks 由本次重算；`versions/` 子树不递归继承（否则版本指数级嵌套）。"""
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        old = result.book_dir / "versions" / "job1"
        (old / "pages").mkdir(parents=True)
        (old / "pages" / "p0001.blocks.json").write_text("{}", encoding="utf-8")
        (old / "manifest.json").write_text("[]", encoding="utf-8")
        (old / "chunks.json").write_text("{}", encoding="utf-8")

        seeded = seed_version_from(old, result.book_dir / "versions" / "job2")

        paths = sorted(p.relative_to(result.book_dir / "versions" / "job2").as_posix() for p in (result.book_dir / "versions" / "job2").rglob("*") if p.is_file())
        assert paths == ["pages/p0001.blocks.json"]
        assert seeded == 1

    def test_seed_is_idempotent_and_tolerates_missing_source(self, tmp_path):
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        version_dir = result.book_dir / "versions" / "job3"

        first = seed_version_from(result.book_dir, version_dir)
        second = seed_version_from(result.book_dir, version_dir)

        assert first == 3
        assert second == 0, "重跑同一版本不重复接入（幂等）"
        assert seed_version_from(result.book_dir / "nope", result.book_dir / "versions" / "job4") == 0

    def test_overwriting_a_seeded_file_leaves_the_original_intact(self, tmp_path):
        """原子替换只换目录项：新版本写 blocks 不会污染上一版本（硬链接继承的前提）。"""
        data_root = tmp_path / "data"
        result = ingest_book(book_id="b1", source_file=_source_pdf(tmp_path), data_root=data_root)
        source_blocks = result.book_dir / "pages" / "p0001.blocks.json"
        source_blocks.write_text("旧内容", encoding="utf-8")
        version_dir = result.book_dir / "versions" / "job5"
        seed_version_from(result.book_dir, version_dir)

        write_json(version_dir / "pages" / "p0001.blocks.json", "新内容")

        assert source_blocks.read_text(encoding="utf-8") == "旧内容"
        assert (version_dir / "pages" / "p0001.blocks.json").read_text(encoding="utf-8").strip() == "新内容"
