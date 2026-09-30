"""存储治理守护：白名单哨兵、虚构约定字样、写入面收口与工作区目录卫生（ADR 0009 决定 5、ADR 0012）。

模块职责：把「我们往哪里写、能写什么」从约定升级为可断言规则——

1. `overview.md` 哨兵区四条目录与常量清单逐字一致（白名单漂移由本文件发现）；
2. 虚构约定 `tmp/axiom-flow` 不得作为有效约定出现：固定治理文档（`standards/`、`architecture/`）
   命中为 0；条文域（`design/`、`guides/`、`adr/`）只允许出现在否定/判缺陷语境——PP-6 判据与
   运维核对步必须点名该目录才能禁止它。历史、台账与计划壳是叙事/临时载体，不入扫描；
3. 数据根缺配即报错、仓内 `data/` 永久不作数据根（决定 1/2，`config.py` 落地）；
4. 写入面锁 `parsed/`：构造器前缀 + `ensure_within` 越界必抛（决定 4）；
5. 工作区目录卫生（ADR 0012 决定 1~4）：`data/`、`scripts/tmp/`、`web/`、`app/` 不得复活；
   根 `tmp/` 是唯一临时区，`.gitignore` 收它且区内一律不入库；`logs/` 只放本项目日志，
   文件数 ≤3 且前缀 `qed-axiom`。原「留档区递归不含产物形状」断言已被「`data/` 不存在」
   取代——目录本身被禁止后形状判据失去作用面（ADR 0012 决定 2 取代 ADR 0009 决定 2 的
   留档定性，数据根守卫继续有效）。

被测代码：docs/architecture/overview.md、docs/{standards,architecture,design,guides,adr}/**、
src/axiom_flow/config.py、src/axiom_flow/ingest/artifacts.py、.gitignore 与仓内工作区目录。
关联设计：docs/adr/0009-data-root-must-be-explicit.md 决定 1~5、
docs/adr/0012-workspace-directory-hygiene.md 决定 1~4、
docs/architecture/overview.md「数据边界与产物布局」与「仓库工作区目录卫生」、
docs/design/parsing-pipeline.md PP-5/PP-6、docs/guides/operations.md 数据目录表。
失效后果：白名单漂移、缺配静默回退复活、越界写入、退役目录复活或临时区重新入库时无人发现。
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from axiom_flow.config import DataRootError, load_settings
from axiom_flow.ingest.artifacts import book_dir_for, ensure_within, version_dir_for

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
OVERVIEW = DOCS / "architecture" / "overview.md"
REPO_DATA = ROOT / "data"
GITIGNORE = ROOT / ".gitignore"
SCRATCH = ROOT / "tmp"
REPO_LOGS = ROOT / "logs"

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
#: 已退役的仓内目录（ADR 0012 决定 1/2/3/5）：不得复活，其下也不得再有入库内容。
RETIRED_DIRS = ("data", "scripts/tmp", "web", "app")
#: 本项目日志口径（ADR 0012 决定 4）：只写本项目日志，文件数有上限。
#: 前缀取 `qed-axiom` 而非 `qed-axiom-`：PID 文件名 `qed-axiom.pid` 由服务脚本固定。
LOG_PREFIX = "qed-axiom"
LOG_MAX_FILES = 3


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
    """决定 2：仓内 `data/`（定性已被 ADR 0012 决定 2 取代）配置成数据根一律拒绝。"""
    with pytest.raises(DataRootError, match="QED_DATA_ROOT"):
        load_settings(data_dir=REPO_DATA)


def _tracked_paths(directory: Path) -> tuple[str, ...]:
    """git 索引内属于该目录的路径；git 不可用即抛（本地门禁以 git 为前置）。"""
    relative = directory.relative_to(ROOT).as_posix()
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--", relative],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - 门禁前置缺失
        raise RuntimeError("契约守护需要 git：无法核对索引内容") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files 失败：{proc.stderr.strip()}")
    return tuple(line for line in proc.stdout.splitlines() if line.strip())


def test_retired_repo_dirs_absent() -> None:
    """决定 1/2/3/5：`data/`、`scripts/tmp/`、`web/`、`app/` 已退役，目录不得复活。"""
    offenders = [relative for relative in RETIRED_DIRS if (ROOT / relative).exists()]
    assert not offenders, "已退役的仓内目录重新出现（ADR 0012）：\n" + "\n".join(offenders)


def test_scratch_area_ignored_and_untracked() -> None:
    """决定 1：根 `tmp/` 是唯一临时区——`.gitignore` 必须收它，且区内一律不入库。"""
    rules = {
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert "/tmp/" in rules, ".gitignore 缺少 `/tmp/`：根临时区重新成为入库载体（ADR 0012 决定 1）"
    tracked = _tracked_paths(SCRATCH)
    assert not tracked, f"根 tmp/ 出现入库内容（临时区不作为事实源）：{tracked[:20]}"


def test_retired_dirs_have_no_tracked_content() -> None:
    """决定 1/2/3/5：退役目录即便被重新创建，也不得以入库形式回流（索引面守卫）。"""
    offenders = {
        relative: _tracked_paths(ROOT / relative)
        for relative in RETIRED_DIRS
        if _tracked_paths(ROOT / relative)
    }
    assert not offenders, f"退役目录存在入库内容：{offenders}"


def test_repo_logs_bounded_and_project_only() -> None:
    """决定 4：`logs/` 只放本项目日志——文件数 ≤3 且一律 `qed-axiom` 前缀。"""
    if not REPO_LOGS.is_dir():
        return
    files = sorted(p.name for p in REPO_LOGS.iterdir() if p.is_file())
    assert len(files) <= LOG_MAX_FILES, (
        f"logs/ 文件数 {len(files)} 超过上限 {LOG_MAX_FILES}（ADR 0012 决定 4）：{files}"
    )
    offenders = [name for name in files if not name.startswith(LOG_PREFIX)]
    assert not offenders, f"logs/ 出现非本项目日志（前缀须为 {LOG_PREFIX}）：{offenders}"
