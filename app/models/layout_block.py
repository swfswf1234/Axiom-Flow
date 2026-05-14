import enum
import uuid
from sqlalchemy import String, Integer, Float, Enum, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class BlockType(str, enum.Enum):
    TEXT = "text"
    FORMULA = "formula"
    TABLE = "table"
    FIGURE = "figure"
    HEADING = "heading"


class LayoutBlock(Base):
    __tablename__ = "layout_blocks"

    block_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.doc_id", ondelete="CASCADE"), nullable=False, index=True)
    page_no: Mapped[int] = mapped_column(Integer, nullable=False)
    block_type: Mapped[BlockType] = mapped_column(Enum(BlockType), nullable=False)
    content: Mapped[str] = mapped_column(String, nullable=True)
    latex: Mapped[str] = mapped_column(String, nullable=True)
    bbox: Mapped[dict] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=True)
    extra_meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
