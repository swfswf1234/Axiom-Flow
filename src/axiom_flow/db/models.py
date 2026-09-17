"""Axiom-Flow 私有表 ORM 模型（`qed` 库 af_* 命名空间）。

表结构事实源：`docs/architecture/database-design.md`；Alembic 迁移见
`src/axiom_flow/migrations/versions/`。四表一次建全（2026-09-17 用户裁决，PLAN-002）。
索引名与迁移/DDL 保持一致（显式命名，避免 autogenerate 漂移）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """af_* 表族声明基类。"""


class AfBook(Base):
    """af_books：已验证书快照 + ingest/解析进度（本侧维护）。"""

    __tablename__ = "af_books"
    __table_args__ = (
        Index("ix_af_books_course", "course_id"),
        Index("ix_af_books_status", "parse_status"),
    )

    book_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    domain_id: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    course_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    course_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    knowledge_id: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    part: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    display_title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    authors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ingest_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    parse_status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    pages_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AfParseJob(Base):
    """af_parse_jobs：一次解析任务（逐页/全本，状态与进度）。"""

    __tablename__ = "af_parse_jobs"
    __table_args__ = (Index("ix_af_parse_jobs_book", "book_id"),)

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(100), nullable=False)
    pages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    engine: Mapped[str] = mapped_column(String(24), nullable=False, default="mineru")
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AfPage(Base):
    """af_pages：一页解析状态与质量信号。"""

    __tablename__ = "af_pages"

    book_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AfBlockEdit(Base):
    """af_block_edits：块级人工编辑覆盖层（同块重复编辑覆盖更新）。"""

    __tablename__ = "af_block_edits"
    __table_args__ = (UniqueConstraint("book_id", "page_no", "block_index", name="uq_af_block_edits_pos"),)

    edit_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(100), nullable=False)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    verdict: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


AF_TABLES = (AfBook.__table__, AfParseJob.__table__, AfPage.__table__, AfBlockEdit.__table__)
