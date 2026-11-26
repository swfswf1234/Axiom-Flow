"""共享 `qed_knowledge_point` 的最小 SQLite 契约夹具；不进入本仓 `af_*` 元数据。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table
from sqlalchemy.engine import Engine


def create_shared_knowledge_table(engine: Engine) -> Table:
    metadata = MetaData()
    table = Table(
        "qed_knowledge_point",
        metadata,
        Column("kp_id", String(64), primary_key=True),
        Column("course_id", String(64), nullable=False),
        Column("plane", String(16), nullable=False),
        Column("node_kind", String(32), nullable=False),
        Column("confirm_state", String(16), nullable=False),
        Column("name", String(500), nullable=False, default=""),
        Column("summary", String(1000), nullable=False, default=""),
        Column("narrative_ref", String(500), nullable=False, default=""),
        Column("meta", JSON, nullable=False, default=dict),
        Column("updated_at", DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None)),
    )
    table.create(engine, checkfirst=True)
    return table


def seed_shared_chapter(
    engine: Engine,
    *,
    kp_id: str = "kp-course-ch3",
    course_id: str = "math_analysis",
    plane: str = "course",
    node_kind: str = "outline",
    confirm_state: str = "unconfirmed",
    summary: str = "旧总结",
    narrative_ref: str = "",
    meta: dict | None = None,
) -> None:
    table = create_shared_knowledge_table(engine)
    stamp = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            table.insert().values(
                kp_id=kp_id,
                course_id=course_id,
                plane=plane,
                node_kind=node_kind,
                confirm_state=confirm_state,
                name="第 3 章 极限",
                summary=summary,
                narrative_ref=narrative_ref,
                meta=meta or {"keep": True},
                updated_at=stamp,
            )
        )
