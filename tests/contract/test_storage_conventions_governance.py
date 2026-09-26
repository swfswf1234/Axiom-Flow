"""存储治理守护：白名单哨兵、虚构约定字样、写入面收口与仓内留档区形状（ADR 0009 决定 5）。

模块职责：把「我们往哪里写、能写什么」从约定升级为可断言规则——

1. `overview.md` 哨兵区四条目录与常量清单逐字一致（白名单漂移由本文件发现）；
2. 虚构约定 `tmp/axiom-flow` 不得作为有效约定出现：固定治理文档（`standards/`、`architecture/`）
   命中为 0；条文域（`design/`、`guides/`、`adr/`）只允许出现在否定/判缺陷语境——PP-6 判据与
   运维核对步必须点名该目录才能禁止它。历史、台账与计划壳是叙事/临时载体，不入扫描；
3. 数据根缺配即报错、仓内 `data/` 永久不作数据根（决定 1/2，`config.py` 落地）；
4. 写入面锁 `parsed/`：构造器前缀 + `ensure_within` 越界必抛（决定 4）；
5. 仓内 `data/` 递归不含产物形状（`book.json`/`pages/`/`versions/`），
   唯一例外 `data/probes/*/root/` 探针数据根（决定 2/5；「顶层」按递归口径判，
   探针根豁免的存在本身要求递归，否则守不住嵌套残骸）。

被测代码：docs/architecture/overview.md、docs/{standards,architecture,design,guides,adr}/**、
src/axiom_flow/config.py、src/axiom_flow/ingest/artifacts.py、仓内 data/（只读列举）。
关联设计：docs/adr/0009-data-root-must-be-explicit.md 决定 1~5、
docs/architecture/overview.md「数据边界与产物布局」、docs/design/parsing-pipeline.md PP-5/PP-6。
失效后果：白名单漂移、缺配静默回退复活、越界写入或留档区产物形状重新堆积时无人发现。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from axiom_flow.config import DataRootError, load_settings
from axiom_flow.ingest.artifacts import book_dir_for, ensure_within, version_dir_for

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
OVERVIEW = DOCS / "architecture" / "overview.md"
REPO_DATA = ROOT / "data"

#: 数据根顶层白名单（ADR 0009 决定 3，沿用根仓库 ADR 0018，本仓不自行增删）。
WHITELIST = ("raw", "tmp", "parsed", "backups")
#: 虚构约定目录名：根仓库 ADR 0018 已删除，本仓只允许以「禁止它」的语境出现。
FICTION = "tmp/axiom-flow"
#: 否定/判缺陷语境白名单：点名是为了禁止，不是在陈述有效约定。
NEGATION_TOKENS = (
    "虚构",
    "永不",
    "恒不",
    "不存在",
    "不写",
    "即缺陷",
    "视为缺陷",
    "改判",
    "已删除",
    "命中为 0",
    "不实现",
)
#: 固定治理文档：字面命中为 0（不含任何合法点名场景）。
FIXED_SCOPE = (DOCS / "standards", DOCS / "architecture")
#: 条文域：必须点名目录才能立判据，故按行否定门控。
NEGATED_SCOPE = (DOCS / "design", DOCS / "guides", DOCS / "adr")
#: 产物目录形状（ADR 0009 决定 2：留档区顶层不得出现）。
PRODUCT_MARKERS = ("book.json", "pages", "versions")


def _iter_lines(directories: tuple[Path, ...]) -> Iterator[tuple[Path, int, str]]:
    for directory in directories:
        for path in sorted(directory.rglob("*.md")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                yield path, lineno, line


def test_whitelist_sentinel_matches_constant() -> None:
    """哨兵区恰好一对且四条目录与常量清单逐字一致（增删条目须先改根侧 ADR 0018）。"""
    text = OVERVIEW.read_text(encoding="utf-8")
    assert text.count("<!-- data-root-whitelist:start -->") == 1
    assert text.count("<!-- data-root-whitelist:end -->") == 1
    block = text.split("<!-- data-root-whitelist:start -->")[1].split(
        "<!-- data-root-whitelist:end -->"
    )[0]
    entries = tuple(
        line.split("`")[1].rstrip("/") for line in block.splitlines() if line.startswith("- `")
    )
    assert entries == WHITELIST


def test_fiction_absent_from_fixed_governance_docs() -> None:
    """固定治理文档（standards/architecture）不得出现虚构约定字样——命中即为 0。"""
    hits = [
        f"{path.relative_to(ROOT)}:{lineno}"
        for path, lineno, line in _iter_lines(FIXED_SCOPE)
        if FICTION in line
    ]
    assert hits == [], f"虚构约定字样出现在固定治理文档：{hits}"


def test_fiction_only_in_negated_context() -> None:
    """条文域点名只允许是否定/判缺陷语境（PP-6 判据、运维核对步、守护清单自身）。"""
    offenders = [
        f"{path.relative_to(ROOT)}:{lineno}: {line.strip()[:80]}"
        for path, lineno, line in _iter_lines(NEGATED_SCOPE)
        if FICTION in line and not any(token in line for token in NEGATION_TOKENS)
    ]
    assert not offenders, "虚构约定只允许在否定/判缺陷语境出现：\n" + "\n".join(offenders)


def test_out_of_data_root_write_raises(tmp_path: Path) -> None:
    """决定 4：任何越出数据根的写路径在写盘前必抛（假数据根探针）。"""
    root = tmp_path / "dataset"
    (root / "parsed").mkdir(parents=True)

    with pytest.raises(ValueError, match="越出数据根"):
        ensure_within(root, tmp_path / "outside")


def test_product_paths_live_under_parsed(tmp_path: Path) -> None:
    """决定 4：两个产物路径构造器一律带 `parsed/` 前缀（写入面唯一落区）。"""
    root = tmp_path / "dataset"
    root.mkdir()

    book_dir = book_dir_for(root, "d1", "c1", "b1")
    version_dir = version_dir_for(root, "d1", "c1", "b1", "job1")

    assert book_dir.is_relative_to(root / "parsed")
    assert version_dir.is_relative_to(root / "parsed")


def test_missing_data_root_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """决定 1：缺 `QED_DATA_ROOT` 即启动期报错，绝不静默回退仓内目录（ADR 0009）。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("QED_DATA_ROOT", raising=False)

    with pytest.raises(DataRootError, match="QED_DATA_ROOT"):
        load_settings()


def test_repo_data_dir_rejected_as_data_root() -> None:
    """决定 2：仓内 `data/` 是留档与备份区，配置成数据根同样拒绝（永久不是数据根）。"""
    with pytest.raises(DataRootError, match="QED_DATA_ROOT"):
        load_settings(data_dir=REPO_DATA)


def test_repo_archive_dir_has_no_product_shape() -> None:
    """决定 2/5：仓内留档区递归不含产物形状；探针数据根 `probes/<窗口>/root/` 豁免。"""
    if not REPO_DATA.is_dir():
        pytest.skip("仓内留档区不存在（干净检出）")

    offenders: list[str] = []
    for path in sorted(REPO_DATA.rglob("*")):
        relative = path.relative_to(REPO_DATA)
        parts = relative.parts
        # 豁免：探针数据根内的产物形状是窗口脚本显式声明的数据根，其内布局正当。
        if len(parts) >= 3 and parts[0] == "probes" and parts[2] == "root":
            continue
        if path.name in PRODUCT_MARKERS:
            offenders.append(relative.as_posix())

    assert not offenders, (
        "仓内留档区出现产物形状（野生区残骸，按 Q2 清理并核对 operations.md runbook）：\n"
        + "\n".join(offenders[:20])
    )
