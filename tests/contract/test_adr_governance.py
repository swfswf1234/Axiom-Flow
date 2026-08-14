"""
模块职责：验证 ADR 编号、元数据、登记表和必需章节保持一致（v2 纪元）。
设计关联（DesignRef）：docs/standards/adr-governance.md
实现状态：Current
被测代码：docs/adr
守护面：ADR 治理
失效后果：ADR 编号/元数据/登记/章节破坏决策追溯
"""

import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
ADR_INDEX = ADR_DIR / "index.md"
ADR_FILE = re.compile(r"(?P<id>\d{4})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.md")
INDEX_ENTRY = re.compile(r"\[(?P<id>\d{4})]\((?P<path>[^)]+\.md)\)")
VALID_STATUSES = {"Draft", "Accepted", "Superseded", "Historical"}
REQUIRED_SECTIONS = ("## 背景", "## 决定", "## 后果", "## 取代")


def _records() -> dict[str, dict[str, object]]:
    records = {}
    for path in sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"), key=lambda p: p.name):
        match = ADR_FILE.fullmatch(path.name)
        assert match, path.name
        adr_id = match.group("id")
        assert adr_id not in records
        content = path.read_text(encoding="utf-8")
        records[adr_id] = {
            "path": path,
            "content": content,
            "status": next(
                line.removeprefix("状态：").strip()
                for line in content.splitlines()
                if line.startswith("状态：")
            ),
        }
    return records


def test_adr_files_use_stable_global_ids_and_complete_metadata():
    records = _records()
    assert records

    for adr_id, record in records.items():
        content = str(record["content"])
        assert content.startswith(f"# ADR {adr_id}：")
        assert record["status"] in VALID_STATUSES
        metadata_fields = ("状态", "最后更新", "关联代码", "关联测试")
        positions = [content.index(f"{name}：") for name in metadata_fields]
        assert positions == sorted(positions)
        assert content.index("#") < positions[0]


def test_adr_index_registers_every_file_once_and_declares_next_id():
    records = _records()
    content = ADR_INDEX.read_text(encoding="utf-8")
    entries = [(match.group("id"), match.group("path")) for match in INDEX_ENTRY.finditer(content)]

    assert Counter(adr_id for adr_id, _ in entries) == Counter(records.keys())
    assert [adr_id for adr_id, _ in entries] == sorted(records)

    next_id = f"{max(map(int, records)) + 1:04d}"
    assert f"下一个可用编号：{next_id}" in content


def test_new_adrs_have_required_sections_in_order():
    for adr_id, record in _records().items():
        content = str(record["content"])
        positions = [content.index(section) for section in REQUIRED_SECTIONS]
        assert positions == sorted(positions), adr_id