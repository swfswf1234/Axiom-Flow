"""
模块职责：验证 ADR 编号、元数据、登记表和完整取代关系保持一致。
设计关联（DesignRef）：docs/standards/adr-governance.md
实现状态：Current
被测代码：docs/adr、docs/history/adr
"""

import re
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR_DIR = ROOT / "docs" / "adr"
ADR_HISTORY_DIR = ROOT / "docs" / "history" / "adr"
ADR_INDEX = ADR_DIR / "index.md"
ADR_FILE = re.compile(r"(?P<id>\d{4})-(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)\.md")
ADR_LINK = re.compile(r"\[ADR (?P<id>\d{4})]\((?P<path>[^)]+)\)")
INDEX_ENTRY = re.compile(r"^\| \[`(?P<id>\d{4})`]\((?P<path>[^)]+\.md)\) \|", re.MULTILINE)
VALID_STATUSES = {"Proposed", "Accepted", "Rejected", "Superseded"}
VALID_DOMAINS = {"解析与评测", "数据与持久化", "API 与任务", "审阅与发布", "工程治理"}
STRUCTURED_ADR_MIN_ID = 16
REQUIRED_SECTIONS = ("## 背景", "## 决定", "## 后果", "## 关联")


def _field(content: str, name: str) -> str:
    matches = re.findall(rf"^{re.escape(name)}：(.*)$", content, re.MULTILINE)
    assert len(matches) == 1, f"{name} 必须恰好出现一次"
    return matches[0].strip()


def _records() -> dict[str, dict[str, object]]:
    records = {}
    paths = list(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))
    paths.extend(ADR_HISTORY_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))
    for path in sorted(paths, key=lambda candidate: candidate.name):
        match = ADR_FILE.fullmatch(path.name)
        assert match, path.name
        adr_id = match.group("id")
        assert adr_id not in records
        content = path.read_text(encoding="utf-8")
        records[adr_id] = {
            "path": path,
            "content": content,
            "status": _field(content, "状态"),
            "date": _field(content, "日期"),
            "domain": _field(content, "领域"),
            "stage": _field(content, "决策阶段"),
            "supersedes": _field(content, "取代"),
            "superseded_by": _field(content, "被取代"),
        }
    return records


def _relation_ids(value: object) -> set[str]:
    text = str(value)
    if text == "—":
        return set()
    links = list(ADR_LINK.finditer(text))
    assert links, f"非法 ADR 关系：{text}"
    return {match.group("id") for match in links}


def test_adr_files_use_stable_global_ids_and_complete_metadata():
    assert not [path for path in ADR_DIR.iterdir() if path.is_dir()]
    records = _records()
    assert records

    for adr_id, record in records.items():
        record_path = Path(record["path"])
        content = str(record["content"])
        assert content.startswith(f"# ADR {adr_id}：")
        assert record["status"] in VALID_STATUSES
        date.fromisoformat(str(record["date"]))
        assert record["domain"] in VALID_DOMAINS
        assert re.fullmatch(r"v\d+\.\d+", str(record["stage"]))
        metadata_fields = ("状态", "日期", "领域", "决策阶段", "取代", "被取代")
        positions = [content.index(f"{name}：") for name in metadata_fields]
        assert positions == sorted(positions)
        for relation in (record["supersedes"], record["superseded_by"]):
            for match in ADR_LINK.finditer(str(relation)):
                target = records.get(match.group("id"))
                assert target is not None
                relation_path = (record_path.parent / match.group("path")).resolve()
                assert relation_path == Path(target["path"]).resolve()

        expected_parent = (
            ADR_DIR if record["status"] in {"Proposed", "Accepted"} else ADR_HISTORY_DIR
        )
        assert record_path.parent == expected_parent


def test_adr_index_registers_every_file_once_and_declares_next_id():
    records = _records()
    content = ADR_INDEX.read_text(encoding="utf-8")
    entries = [(match.group("id"), match.group("path")) for match in INDEX_ENTRY.finditer(content)]

    assert Counter(adr_id for adr_id, _ in entries) == Counter(records.keys())
    assert [adr_id for adr_id, _ in entries] == sorted(records)
    for adr_id, relative_path in entries:
        index_path = (ADR_INDEX.parent / relative_path).resolve()
        assert index_path == Path(records[adr_id]["path"]).resolve()

    for line in content.splitlines():
        match = INDEX_ENTRY.match(line)
        if not match:
            continue
        adr_id = match.group("id")
        record = records[adr_id]
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        assert len(cells) == 6
        title = str(record["content"]).splitlines()[0].split("：", 1)[1]
        assert cells[1:5] == [title, record["domain"], record["stage"], record["status"]]
        supersedes = sorted(_relation_ids(record["supersedes"]))
        superseded_by = sorted(_relation_ids(record["superseded_by"]))
        expected_relation = "—"
        if supersedes:
            expected_relation = f"取代 {', '.join(supersedes)}"
        elif superseded_by:
            expected_relation = f"被 {', '.join(superseded_by)} 取代"
        assert cells[5] == expected_relation

    next_id = f"{max(map(int, records)) + 1:04d}"
    assert f"下一个可用编号：{next_id}" in content


def test_complete_supersession_is_bidirectional():
    records = _records()
    for adr_id, record in records.items():
        supersedes = _relation_ids(record["supersedes"])
        superseded_by = _relation_ids(record["superseded_by"])
        assert adr_id not in supersedes | superseded_by

        if supersedes:
            assert record["status"] == "Accepted"
        for old_id in supersedes:
            assert records[old_id]["status"] == "Superseded"
            assert adr_id in _relation_ids(records[old_id]["superseded_by"])

        if record["status"] == "Superseded":
            assert superseded_by
        else:
            assert not superseded_by
        for new_id in superseded_by:
            assert records[new_id]["status"] == "Accepted"
            assert adr_id in _relation_ids(records[new_id]["supersedes"])


def test_new_adrs_have_required_sections_in_order():
    for adr_id, record in _records().items():
        if int(adr_id) < STRUCTURED_ADR_MIN_ID:
            continue
        content = str(record["content"])
        positions = [content.index(section) for section in REQUIRED_SECTIONS]
        assert positions == sorted(positions), adr_id
