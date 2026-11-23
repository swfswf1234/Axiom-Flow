"""PLAN-019：重跑目标页的新版本覆写不得破坏旧版本产物（硬链接 + 原子替换不变量）。"""

from __future__ import annotations

from axiom_flow.ingest.artifacts import seed_version_from, write_json


def _page_bytes(page_no: int, text: str) -> str:
    return f'{{"page": {page_no}, "blocks": [{{"text": "{text}"}}]}}'


def test_overwrite_seeded_page_keeps_old_version_intact(tmp_path):
    """目标页在新版本被覆写后，旧版本同名文件内容保持原样（硬链接被原子替换打破）。"""
    old = tmp_path / "old"
    new = tmp_path / "new"
    (old / "pages").mkdir(parents=True)
    old_file = old / "pages" / "p0001.blocks.json"
    write_json(old_file, _page_bytes(1, "OLD"))

    seeded = seed_version_from(old, new)
    assert seeded == 1
    new_file = new / "pages" / "p0001.blocks.json"
    assert new_file.samefile(old_file), "基线以硬链接接入"

    write_json(new_file, _page_bytes(1, "NEW"))

    assert "NEW" in new_file.read_text(encoding="utf-8")
    assert "OLD" in old_file.read_text(encoding="utf-8"), "覆写新版本不得污染旧版本"
    assert not new_file.samefile(old_file), "覆写应打破硬链接，而非改同一 inode"
