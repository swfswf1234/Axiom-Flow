"""共享 `qed_knowledge_point` 的课程级章腿原始 SQL 通路（PLAN-034 W4）。

本仓不复制根侧 ORM，也不建表；只按显式 `kp_id` 读取、加锁并更新三列。
MySQL 使用 `SELECT ... FOR UPDATE`，SQLite 隔离测试以 `updated_at` 乐观条件防写覆。
设计关联：docs/architecture/database-design.md（共享表登记面 A、课程级直写通路）
实现状态：Current
被测代码：tests/unit/test_shared_chapter_text.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from axiom_flow.db.engine import utc_now

SHARED_KNOWLEDGE_POINT = "qed_knowledge_point"


@dataclass(frozen=True, slots=True)
class SharedChapterTarget:
    kp_id: str
    course_id: str
    plane: str
    node_kind: str
    confirm_state: str
    summary: str
    narrative_ref: str
    meta: dict[str, Any]
    updated_at: datetime


class SharedKnowledgeRepository:
    """共享主表的显式目标读取与条件更新；事务由调用方掌握。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, kp_id: str, *, lock: bool = False) -> SharedChapterTarget | None:
        suffix = " FOR UPDATE" if lock and self.session.bind.dialect.name == "mysql" else ""
        row = self.session.execute(
            text(
                f"SELECT kp_id, course_id, plane, node_kind, confirm_state, summary, "
                f"narrative_ref, meta, updated_at FROM {SHARED_KNOWLEDGE_POINT} "
                f"WHERE kp_id = :kp_id{suffix}"
            ),
            {"kp_id": kp_id},
        ).mappings().one_or_none()
        return self._target(row) if row is not None else None

    def update_text(
        self,
        target: SharedChapterTarget,
        *,
        summary: str,
        narrative_ref: str,
        meta: dict[str, Any],
    ) -> bool:
        result = self.session.execute(
            text(
                f"UPDATE {SHARED_KNOWLEDGE_POINT} SET summary = :summary, "
                "narrative_ref = :narrative_ref, meta = :meta, updated_at = :updated_at "
                "WHERE kp_id = :kp_id AND updated_at = :expected_updated_at"
            ),
            {
                "summary": summary,
                "narrative_ref": narrative_ref,
                "meta": json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                "updated_at": utc_now(),
                "kp_id": target.kp_id,
                "expected_updated_at": target.updated_at,
            },
        )
        return result.rowcount == 1

    @staticmethod
    def _target(row) -> SharedChapterTarget:
        raw_meta = row["meta"]
        if raw_meta is None or raw_meta == "":
            meta: Any = {}
        elif isinstance(raw_meta, bytes):
            meta = json.loads(raw_meta.decode("utf-8"))
        elif isinstance(raw_meta, str):
            meta = json.loads(raw_meta)
        else:
            meta = raw_meta
        if not isinstance(meta, dict):
            raise ValueError("共享知识点行的 meta 不是 JSON 对象")
        return SharedChapterTarget(
            kp_id=row["kp_id"],
            course_id=row["course_id"],
            plane=row["plane"],
            node_kind=row["node_kind"],
            confirm_state=row["confirm_state"],
            summary=row["summary"] or "",
            narrative_ref=row["narrative_ref"] or "",
            meta=meta,
            updated_at=row["updated_at"],
        )
