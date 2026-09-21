"""产物路径与原子落盘工具：`<QED_DATA_ROOT>/parsed/<domain>/<course>/<book_id>/`。

规则（docs/design/parsing-pipeline.md）：临时文件 → 校验 → 原子替换，不留半成品；
manifest 排除自身与隐藏临时文件；路径一律 POSIX 相对路径（跨平台契约稳定）。
解析产物按版本划分（PLAN-004）：一次解析任务的全部产物落在 `<book_dir>/versions/<job_id>/`，
页图从根视图硬链接而来（多版本共图不翻倍占盘）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from axiom_flow.schemas import SAFE_SEGMENT_PATTERN, BookMeta, ManifestEntry

logger = logging.getLogger(__name__)

_SEGMENT_RE = re.compile(SAFE_SEGMENT_PATTERN)

#: 版本产物子树目录名：根清单必须排除它，否则每次解析都要重算全部历史版本。
VERSIONS_DIRNAME = "versions"


def ensure_safe_segment(value: str, kind: str, *, allow_empty: bool = False) -> str:
    """单一路径段校验：schemas 层 422 之外的纵深防御（禁分隔符、`..`、隐藏段）。"""
    if allow_empty and not value:
        return value
    if not _SEGMENT_RE.match(value):
        raise ValueError(f"非法 {kind}：{value!r}（须为数据根内的单一路径段）")
    return value


def ensure_within(data_root: Path | str, target: Path | str) -> Path:
    """校验 `target` 落在数据根之内，返回解析后的绝对路径（条文 7 的纵深防御）。

    schemas 层已用段规则拒绝大部分越界入参；此处是写盘前的最后一道闸：符号链接、
    `..` 归一化后的结果同样受限。
    """
    root = Path(data_root).resolve()
    resolved = Path(target).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"路径越出数据根：{resolved}（数据根 {root}）")
    return resolved


def book_dir_for(data_root: Path | str, domain_id: str, course_id: str, book_id: str) -> Path:
    """产物目录：`<data_root>/parsed/<domain_id>/<course_id>/<book_id>/`（越界即报错）。"""
    ensure_safe_segment(domain_id, "domain_id", allow_empty=True)
    ensure_safe_segment(course_id, "course_id", allow_empty=True)
    ensure_safe_segment(book_id, "book_id")
    book_dir = Path(data_root) / "parsed" / domain_id / course_id / book_id
    ensure_within(data_root, book_dir)
    return book_dir


def version_dir_for(
    data_root: Path | str, domain_id: str, course_id: str, book_id: str, job_id: str
) -> Path:
    """解析版本目录：`<book_dir>/versions/<job_id>/`（一次任务一个版本，互不覆盖）。"""
    ensure_safe_segment(job_id, "job_id")
    version_dir = book_dir_for(data_root, domain_id, course_id, book_id) / VERSIONS_DIRNAME / job_id
    ensure_within(data_root, version_dir)
    return version_dir


def link_pages_into(root_pages_dir: Path, version_pages_dir: Path, page_no: int) -> Path | None:
    """把根视图页图接入版本目录，返回目标路径；根视图缺该页图时返回 `None`。

    硬链接而非复制：页图是产物里最大的一块，按版本复制会让磁盘随解析次数线性膨胀。
    平台不支持硬链接（跨文件系统、部分 Windows 文件系统）时回落到复制并告警——
    此处**必须**留可见信号，否则容量问题只会以「磁盘莫名满了」的形式回来。
    """
    source = root_pages_dir / f"p{page_no:04d}.png"
    if not source.is_file():
        return None
    version_pages_dir.mkdir(parents=True, exist_ok=True)
    target = version_pages_dir / source.name
    if target.is_file():
        return target
    return _link_file(source, target)


#: 版本目录不继承的派生文件：每次解析必然重算，留着只会给出旧指纹。
_NOT_SEEDED = frozenset({"manifest.json", "chunks.json"})


def seed_version_from(source_dir: Path, version_dir: Path) -> int:
    """把上一生效作用域的产物接入新版本目录，返回接入文件数（写时复制的版本基线）。

    一个版本必须是**完整快照**：逐页重解析只改写它碰过的页，其余页继承上一版，否则新版本
    天生缺一半页，永不可能满足激活门槛，读取端也会在看不到产物时退回旧作用域。
    硬链接安全性依赖原子替换（`os.replace` 只换目录项），因此新版本覆写不会破坏上一版。
    """
    if not source_dir.is_dir():
        return 0
    seeded = 0
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        if relative.parts[0] == VERSIONS_DIRNAME or relative.name in _NOT_SEEDED:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        target = version_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            continue
        _link_file(path, target)
        seeded += 1
    return seeded


def _link_file(source: Path, target: Path) -> Path:
    """硬链接单个文件，失败时回落复制（同一处兜底逻辑，供页图接入与版本基线共用）。"""
    try:
        os.link(source, target)
    except OSError:
        logger.warning("硬链接失败，回落复制：%s → %s", source, target)
        shutil.copy2(source, target)
    return target


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA-256（分块读入，避免大文件整读内存）。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path: Path) -> tuple[int, int]:
    """读取 PNG 像素尺寸（IHDR 头，24 字节；bbox 基准的事实来源）。"""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是有效的 PNG：{path}")
    return int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")


def atomic_write_text(path: Path, content: str) -> None:
    """文本原子写入：同目录临时文件 + `os.replace`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """二进制原子写入：同目录临时文件 + `os.replace`。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex[:8]}")
    try:
        tmp.write_bytes(content)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_json(path: Path, payload: str) -> None:
    """JSON 文本原子写入（统一末尾换行）。"""
    atomic_write_text(path, payload.rstrip("\n") + "\n")


def write_book_meta(book_dir: Path, meta: BookMeta) -> None:
    write_json(book_dir / "book.json", meta.model_dump_json(indent=2))


def read_book_meta(book_dir: Path) -> BookMeta:
    return BookMeta.model_validate_json((book_dir / "book.json").read_text(encoding="utf-8"))


def build_manifest(book_dir: Path) -> list[ManifestEntry]:
    """产物清单：相对路径 + 大小 + SHA-256（排除 manifest.json 自身与隐藏文件）。

    对根视图调用时额外排除 `versions/` 子树——历史版本各有自己的清单，混进根清单会让
    「本次解析写了什么」失去意义，且每页都要重算哈希。
    """
    entries: list[ManifestEntry] = []
    for path in sorted(book_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(book_dir)
        if relative.name == "manifest.json" or any(part.startswith(".") for part in relative.parts):
            continue
        if relative.parts[0] == VERSIONS_DIRNAME:
            continue
        entries.append(
            ManifestEntry(
                path=relative.as_posix(),
                size=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    return entries


def write_manifest(book_dir: Path) -> list[ManifestEntry]:
    """重算并原子写入 manifest.json（解析推进后调用）。"""
    entries = build_manifest(book_dir)
    write_json(book_dir / "manifest.json", json.dumps([e.model_dump() for e in entries], ensure_ascii=False, indent=2))
    return entries
