import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Enum, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DocStatus(str, enum.Enum):
    PENDING = "pending"
    PARSING = "parsing"
    PARSED = "parsed"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    course_number: Mapped[str] = mapped_column(String(32), nullable=True)
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[DocStatus] = mapped_column(Enum(DocStatus), default=DocStatus.PENDING)
    layout_ref: Mapped[str] = mapped_column(String(1024), nullable=True)
    extra_meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )
