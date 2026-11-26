"""课程级章总结与导读的共享目标持久化编排（PLAN-034 W4）。

预检与生成分离，生成后重新锁行；正文文件先原子落盘，三列再在同一事务更新并提交。
设计关联：docs/history/plans/2026-11/2026-11-10-merge-chapter-text-generation.md（W0／W4）
实现状态：Current
被测代码：tests/unit/test_shared_chapter_text.py
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from axiom_flow.db.shared_knowledge import SharedChapterTarget, SharedKnowledgeRepository
from axiom_flow.engines.base import CHAPTER_TEXT_PROMPT_VERSION, ChapterReply, EngineError, KnowledgeEngine
from axiom_flow.ingest.artifacts import atomic_write_bytes, ensure_safe_segment, ensure_within, sha256_file
from axiom_flow.orchestrator.knowledge import (
    ReferenceFetcher,
    chapter_text_check,
    chapter_text_evidence_sha256,
    generate_chapter_text,
)
from axiom_flow.schemas import ChapterTextCheck, ChapterTextOut, ChapterTextPersistence, ChapterTextRequest


class ChapterTextError(Exception):
    """课程级章腿的可映射失败：状态码与可披露原因。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def merge_chapter_text(
    session: Session,
    request: ChapterTextRequest,
    engine: KnowledgeEngine,
    *,
    data_root: Path | str,
    reference_fetcher: ReferenceFetcher | None = None,
) -> ChapterTextOut:
    """生成、回读或更新明确课程级章行；失败不改变旧三列。"""
    digest = chapter_text_evidence_sha256(request)
    repository = SharedKnowledgeRepository(session)
    target = _read_target(repository, request.chapter_kp_id, lock=False, missing_status=404)
    _validate_target(target, request)
    previous_hash = _completed_hash(target)
    reused = _reuse(target, request, digest, data_root=data_root)
    if reused is not None:
        return reused

    _validate_engine(engine)
    session.commit()
    bind = engine.bind_chapter_text
    bind(
        course_id=request.course_id,
        chapter_kp_id=request.chapter_kp_id,
        evidence_sha256=digest,
    )
    replied, issues, model = generate_chapter_text(engine, request, reference_fetcher)
    check = chapter_text_check(issues, model)
    narrative_ref = _narrative_ref(request.course_id, request.chapter_kp_id, digest)
    result = _response(request, digest, narrative_ref, replied, check, reused=False)

    locked = _read_target(repository, request.chapter_kp_id, lock=True, missing_status=409)
    _validate_target(locked, request)
    if (
        locked.summary,
        locked.narrative_ref,
        locked.meta,
        locked.updated_at,
    ) != (target.summary, target.narrative_ref, target.meta, target.updated_at):
        raise ChapterTextError(409, "生成后目标已被并发改写，拒绝覆盖")
    if _completed_hash(locked) != previous_hash:
        raise ChapterTextError(409, "生成后目标证据哈希已改变，拒绝覆盖")
    _persist(repository, locked, result, data_root=data_root)
    return result


def _read_target(
    repository: SharedKnowledgeRepository,
    kp_id: str,
    *,
    lock: bool,
    missing_status: int,
) -> SharedChapterTarget:
    try:
        target = repository.get(kp_id, lock=lock)
    except ValueError as exc:
        raise ChapterTextError(409, f"目标行 meta 不可用：{exc}") from exc
    if target is None:
        raise ChapterTextError(missing_status, f"目标知识点行不存在：{kp_id}")
    return target


def _validate_target(target: SharedChapterTarget, request: ChapterTextRequest) -> None:
    if target.course_id != request.course_id:
        raise ChapterTextError(422, f"目标 course_id 不匹配：{target.course_id}")
    if target.plane != "course":
        raise ChapterTextError(422, f"目标不是课程级行：plane={target.plane}")
    if target.node_kind != "outline":
        raise ChapterTextError(422, f"目标不是目录章行：node_kind={target.node_kind}")
    if target.confirm_state != "unconfirmed":
        raise ChapterTextError(409, f"目标处于受保护确认态：{target.confirm_state}")


def _completed_hash(target: SharedChapterTarget) -> str | None:
    if "chapter_text" not in target.meta:
        return None
    record = target.meta["chapter_text"]
    if not isinstance(record, dict):
        raise ChapterTextError(409, "目标 chapter_text 完成记录损坏")
    digest = record.get("evidence_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ChapterTextError(409, "目标 chapter_text 证据哈希损坏")
    return digest


def _reuse(
    target: SharedChapterTarget,
    request: ChapterTextRequest,
    digest: str,
    *,
    data_root: Path | str,
) -> ChapterTextOut | None:
    if _completed_hash(target) != digest:
        return None
    record = target.meta["chapter_text"]
    narrative_ref = _narrative_ref(request.course_id, request.chapter_kp_id, digest)
    try:
        check = ChapterTextCheck.model_validate(record["check"])
        path = _narrative_path(data_root, request.course_id, request.chapter_kp_id, digest)
        narrative = path.read_bytes().decode("utf-8")
        valid = (
            record.get("prompt_version") == CHAPTER_TEXT_PROMPT_VERSION
            and target.narrative_ref == narrative_ref
            and record.get("summary_sha256") == _sha256_text(target.summary)
            and record.get("narrative_sha256") == _sha256_text(narrative)
            and record.get("narrative_sha256") == sha256_file(path)
        )
    except (KeyError, OSError, UnicodeError, ValidationError, ValueError) as exc:
        raise ChapterTextError(409, f"已有完成结果不可回读：{exc}") from exc
    if not valid:
        raise ChapterTextError(409, "已有完成结果的文件引用或内容哈希不吻合")
    try:
        return _response(
            request,
            digest,
            narrative_ref,
            ChapterReply(summary=target.summary, narrative=narrative),
            check,
            reused=True,
        )
    except EngineError as exc:
        raise ChapterTextError(409, f"已有完成结果超出响应上限：{exc}") from exc


def _validate_engine(engine: KnowledgeEngine) -> None:
    if getattr(engine, "accounts_externally", False) is not True:
        raise EngineError("课程级章腿要求 accounts_externally=True，拒绝重复本地记账")
    if not callable(getattr(engine, "bind_chapter_text", None)) or not callable(
        getattr(engine, "chapter_text", None)
    ):
        raise EngineError("当前文字引擎未提供 chapter_text 课程级章腿")


def _response(
    request: ChapterTextRequest,
    digest: str,
    narrative_ref: str,
    replied: ChapterReply,
    check: ChapterTextCheck,
    *,
    reused: bool,
) -> ChapterTextOut:
    try:
        return ChapterTextOut(
            summary=replied.summary,
            narrative=replied.narrative,
            check=check,
            persistence=ChapterTextPersistence(
                kp_id=request.chapter_kp_id,
                evidence_sha256=digest,
                narrative_ref=narrative_ref,
                reused=reused,
            ),
        )
    except ValidationError as exc:
        raise EngineError(f"模型回复超出课程级章腿响应上限：{exc}") from exc


def _persist(
    repository: SharedKnowledgeRepository,
    target: SharedChapterTarget,
    result: ChapterTextOut,
    *,
    data_root: Path | str,
) -> None:
    path = _narrative_path(
        data_root,
        target.course_id,
        target.kp_id,
        result.persistence.evidence_sha256,
    )
    content = result.narrative.encode("utf-8")
    created_path: Path | None = None
    try:
        if path.exists():
            if path.read_bytes() != content:
                raise ChapterTextError(409, "内容寻址导读文件已存在且内容不同，拒绝覆盖历史结果")
        else:
            atomic_write_bytes(path, content)
            created_path = path
    except (OSError, ValueError) as exc:
        raise EngineError(f"课程级导读文件写入失败：{exc}") from exc
    try:
        record: dict[str, Any] = {
            "evidence_sha256": result.persistence.evidence_sha256,
            "prompt_version": CHAPTER_TEXT_PROMPT_VERSION,
            "summary_sha256": _sha256_text(result.summary),
            "narrative_sha256": _sha256_text(result.narrative),
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "check": result.check.model_dump(mode="json"),
        }
        meta = {**target.meta, "chapter_text": record}
        if not repository.update_text(
            target,
            summary=result.summary,
            narrative_ref=result.persistence.narrative_ref,
            meta=meta,
        ):
            raise ChapterTextError(409, "生成后目标已被并发改写，拒绝覆盖")
    except Exception:
        try:
            repository.session.rollback()
        finally:
            if created_path is not None:
                created_path.unlink(missing_ok=True)
        raise
    try:
        repository.session.commit()
    except Exception:
        remove_created_path = False
        try:
            repository.session.rollback()
            persisted = repository.get(target.kp_id)
        except Exception:
            pass
        else:
            remove_created_path = (
                persisted is None
                or persisted.narrative_ref != result.persistence.narrative_ref
            )
        if remove_created_path and created_path is not None:
            created_path.unlink(missing_ok=True)
        raise


def _narrative_ref(course_id: str, kp_id: str, digest: str) -> str:
    return (Path("parsed") / "course" / course_id / kp_id / f"{digest}.md").as_posix()


def _narrative_path(data_root: Path | str, course_id: str, kp_id: str, digest: str) -> Path:
    ensure_safe_segment(course_id, "course_id")
    ensure_safe_segment(kp_id, "kp_id")
    ensure_safe_segment(digest, "evidence_sha256")
    return ensure_within(data_root, Path(data_root) / _narrative_ref(course_id, kp_id, digest))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
