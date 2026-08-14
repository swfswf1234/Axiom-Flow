"""Axiom-Flow 统一格式 schema：内外契约单一事实源（spec 第 2 节）。

产物形态（data/books/<book_id>/）：
- pages/pXXXX.blocks.json  ← BlocksPage（块级结构化，QED-Engine 渲染 + Milvus 切分共同基座）
- book.json               ← BookMeta
- manifest.json           ← ManifestEntry 列表
- 解析任务（API 契约）     ← ParseJob / ParseJobCreate

块类型：heading / paragraph / formula / table / image / list / caption / header / footer / page_number
来源：mineru | qwen-vl-plus（兜底标记）
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------- 枚举 ----------


class BlockType(StrEnum):
    """块类型枚举（10 种，spec 第 2 节）。"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    FORMULA = "formula"
    TABLE = "table"
    IMAGE = "image"
    LIST = "list"
    CAPTION = "caption"
    HEADER = "header"
    FOOTER = "footer"
    PAGE_NUMBER = "page_number"


class Source(StrEnum):
    """解析来源：mineru 主引擎 / qwen-vl-plus 兜底。"""

    MINERU = "mineru"
    QWEN_VL_PLUS = "qwen-vl-plus"


class Strategy(StrEnum):
    """任务策略：local 纯本地 / hybrid 质量不达标走兜底。"""

    LOCAL = "local"
    HYBRID = "hybrid"


class JobStatus(StrEnum):
    """解析任务状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PageState(StrEnum):
    """页状态（state.sqlite 页级）。"""

    PENDING = "pending"
    PARSED = "parsed"
    FALLBACK = "fallback"
    FAILED = "failed"


# ---------- 基础类型 ----------

BBox = Annotated[
    tuple[int, int, int, int],
    Field(description="页内坐标 [x0, y0, x1, y1]（原页图像素坐标系，pXXXX.png 为基准）"),
]


# ---------- 块 ----------


class Block(BaseModel):
    """统一块模型：type 判别 + 按类型校验必需字段。

    宽松构造（字段全部可选），由 model_validator 按 type 强制必需字段，
    兼顾契约稳定与 MinerU 输出归一化容错。
    """

    type: BlockType
    bbox: BBox
    # heading / paragraph / caption / header / footer / page_number
    text: str | None = None
    level: int | None = Field(default=None, ge=1, le=6, description="heading 层级 1-6")
    # formula
    latex: str | None = Field(default=None, description="LaTeX 源码（数学解析器探索入口）")
    display: bool = False
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="识别置信度")
    # table
    html: str | None = Field(default=None, description="表格 HTML")
    # image
    path: str | None = Field(default=None, description="页图内图片文件相对路径")
    caption: str | None = None
    # list
    items: list[str] | None = None

    @model_validator(mode="after")
    def _check_required_by_type(self) -> "Block":
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"bbox 倒置: {self.bbox}")
        required: dict[BlockType, str] = {
            BlockType.HEADING: "text",
            BlockType.PARAGRAPH: "text",
            BlockType.FORMULA: "latex",
            BlockType.TABLE: "html",
            BlockType.IMAGE: "path",
            BlockType.LIST: "items",
            BlockType.CAPTION: "text",
            BlockType.HEADER: "text",
            BlockType.FOOTER: "text",
            BlockType.PAGE_NUMBER: "text",
        }
        field = required[self.type]
        if getattr(self, field) is None:
            raise ValueError(f"块类型 {self.type.value} 缺少必需字段 {field}")
        if self.type == BlockType.HEADING and self.level is None:
            raise ValueError("heading 块缺少 level")
        return self


# ---------- 页级三件套 ----------


class QualityMetrics(BaseModel):
    """页质量信号（spec：空块率/公式平均置信度/表格成功数/文本长度比）。"""

    empty_block_ratio: float = Field(ge=0.0, le=1.0)
    avg_formula_confidence: float = Field(ge=0.0, le=1.0)
    table_count: int = Field(ge=0)
    text_length_ratio: float = Field(ge=0.0)


class BlocksPage(BaseModel):
    """pages/pXXXX.blocks.json：页级结构化数据。"""

    page: int = Field(ge=1, description="页码（1 基，对应 p0001.png）")
    source: Source
    blocks: list[Block]
    quality: QualityMetrics | None = None


# ---------- book.json ----------


class BookMeta(BaseModel):
    """book.json：书目元数据。"""

    book_id: str = Field(description="目录名即 book_id，如 01-rudin-en-principles-of-math-analysis")
    title: str
    author: str | None = None
    page_count: int = Field(ge=1)
    sha256: str = Field(description="源 PDF SHA-256（64 位十六进制）")
    strategy: Strategy

    @field_validator("sha256")
    @classmethod
    def _sha256_hex64(cls, v: str) -> str:
        if len(v) != 64 or any(c not in "0123456789abcdefABCDEF" for c in v):
            raise ValueError("sha256 必须为 64 位十六进制")
        return v.lower()


# ---------- manifest.json ----------


class ManifestEntry(BaseModel):
    """manifest.json 条目：文件路径 + 大小 + 哈希（防篡改/增量）。"""

    path: str
    size: int = Field(ge=0)
    sha256: str = Field(description="64 位十六进制")


# ---------- 解析任务（API 契约） ----------


class ParseJobCreate(BaseModel):
    """POST /api/v1/parse-jobs 请求体。"""

    book_id: str
    pages: list[int] | None = Field(default=None, description="页范围（1 基）；None = 全部页")
    strategy: Strategy = Strategy.LOCAL


class ParseJob(BaseModel):
    """解析任务（状态与进度，GET 响应）。"""

    id: str
    book_id: str
    pages: list[int] | None = None
    strategy: Strategy
    status: JobStatus
    progress: dict[str, int] = Field(default_factory=dict, description="如 {'parsed': 2, 'total': 5}")
