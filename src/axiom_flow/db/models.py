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


#: 「根视图」哨兵 job_id：迁移前存量产物的归属版本，也是 `active_job_id IS NULL` 时的读取作用域。
LEGACY_JOB_ID = "legacy"


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
    page_count: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="全书页数（ingest 写入；页清单枚举域唯一来源）"
    )
    sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="源 PDF SHA-256（ingest 写入；book.json 退役后的唯一事实源）"
    )
    ingest_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    ingested_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="ingest 完成时刻（UTC naive）；NULL = 从未 ingest（REQ-097）"
    )
    parse_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", comment="解析进度（pending/parsing）；不表达结局"
    )
    last_parse_result: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", comment="最近一次任务结局；'' = 从未有任务（ADR 0007）"
    )
    pages_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_job_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        comment="当前生效解析版本（NULL = 根视图 legacy）；生效指针唯一事实源",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AfParseJob(Base):
    """af_parse_jobs：一次解析任务即一个解析版本（状态、进度与源快照自证）。"""

    __tablename__ = "af_parse_jobs"
    __table_args__ = (Index("ix_af_parse_jobs_book", "book_id"),)

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(100), nullable=False)
    pages: Mapped[list | None] = mapped_column(JSON, nullable=True)
    engine: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="解析后显式引擎名；无默认值（缺引擎名应报错而非静默回落 MinerU）"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    failed_pages: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="失败页码数组（不截断；ADR 0007 决定 2）"
    )
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    source_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="本版本解析的源 PDF 快照（自证版本与文件对应）"
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="建任务时的全书页数快照")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AfPage(Base):
    """af_pages：一页在一个解析版本内的状态与质量信号。"""

    __tablename__ = "af_pages"

    book_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=LEGACY_JOB_ID, comment="所属解析版本；'legacy' = 根视图存量"
    )
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    quality: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AfBlockEdit(Base):
    """af_block_edits：块级人工编辑覆盖层（编辑绑定其所属解析版本）。"""

    __tablename__ = "af_block_edits"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "job_id", "page_no", "block_index", name="uq_af_block_edits_pos"
        ),
    )

    edit_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(100), nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=LEGACY_JOB_ID, comment="编辑绑定的解析版本；'legacy' = 根视图存量"
    )
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[str] = mapped_column(String(24), nullable=False, default="")
    verdict: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    edited_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AfPageRegion(Base):
    """af_page_regions：人工声明的**页级**结构意图（新框 / 删块 / 并块）。

    主键不含 `job_id`（ADR 0010 决定 1）：区域是页解析的**输入**，必须跨版本存活，否则「改框再解析
    块随框变化」与「删除不复活」不可达；与 `af_block_edits`（版本级人工判定）语义正交。
    """

    __tablename__ = "af_page_regions"
    __table_args__ = (Index("ix_af_page_regions_page", "book_id", "page_no", "op"),)

    book_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    page_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        comment="区域稳定身份（服务端按几何派生，跨版本不变；响应回给根侧长期定位）",
    )
    op: Mapped[str] = mapped_column(
        String(16), nullable=False, default="add", comment="add=人工新框 / delete=删块 / merge=并块"
    )
    bbox: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="op=add 必填：人工框几何（原页图像素）"
    )
    anchor_bbox: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="op=delete 必填：操作对象在受理时生效版本产物中的几何快照"
    )
    members: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="op=merge 必填：参与合并的几何快照列表，数组序即阅读序"
    )
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, default="", comment="区域用途提示（属性路线，不扩 BlockType 枚举）"
    )
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


AF_TABLES = (
    AfBook.__table__,
    AfParseJob.__table__,
    AfPage.__table__,
    AfBlockEdit.__table__,
    AfPageRegion.__table__,
)
