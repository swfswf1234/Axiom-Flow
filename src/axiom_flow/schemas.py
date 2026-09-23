"""Axiom-Flow 统一格式 schema：内外契约单一事实源（设计见 docs/design/parsing-pipeline.md）。

产物形态（`<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`）：
- `book.json`               ← BookMeta
- `manifest.json`           ← ManifestEntry 列表（根清单不递归 `versions/`）
- `versions/<job_id>/`      ← 一次解析任务 = 一个完整快照版本（book.json + pages/ + chunks.json + manifest.json）
- `chunks.json`             ← ChunkSet（派生、不入库、可重建）
- `pages/p<NNNN>.blocks.json` ← BlocksPage（块级结构化，渲染 + 向量化共同基座）
- `pages/p<NNNN>.md`          ← 页级 Markdown（`$$...$$` / `$...$` 内嵌公式）

生效版本由 `af_books.active_job_id` 单点指认（NULL = 根视图 legacy）；读取端点与 URL 不因
版本化改变（PLAN-004 / REQ-080）。

API 契约模型：BookOut / BookSyncItem / IngestResponse / PageData / EditRecord / ParseVersionOut / ParseJob。
块类型 10 种；解析来源 source ∈ {mineru, paddleocr-vl, qwen-vl}（后两者预留）。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------- 数据根边界（PLAN-003 条文 7） ----------

# 路径段：首字符限 `\w`（因而拒绝 `/`、`\`、`.` 与 `..`），其余允许 `\w`、`.`、`-`、`_`；
# 中文段合法（`\w` 在 Unicode 下含汉字），但 Windows 盘符 `D:` 因 `:` 不在字符类内被拒。
SAFE_SEGMENT_PATTERN = r"^\w[\w.\-]{0,63}$"
OPTIONAL_SEGMENT_PATTERN = r"^(?:\w[\w.\-]{0,63})?$"
# `file_path` 段：**黑名单**式（REQ-083）。中文书名的全角标点（`（）：`、`【】`）是 Ps/Pe/Po 而非
# 词字符，用 `\w` 白名单会让一条违规殃及整批 sync（根仓库实测 422 后靠改书名止血）。防遍历与
# Windows 非法字符仍必须拒：`\`、半角 `:`（盘符）、`/ : * ? " < > |`、控制字符、前导 `.`（含 `..`）
# 与首尾空白/句点；段内空格与非 ASCII 标点放行。全角 `：` 与半角 `:` 是不同字符，放宽不引入盘符风险。
_PATH_SEGMENT = r"(?![.\s])[^\\/:*?\"<>\x00-\x1f|]{1,255}(?<![.\s])"
RELATIVE_PATH_PATTERN = rf"^(?:{_PATH_SEGMENT}(?:/{_PATH_SEGMENT})*)?$"
# pydantic 的 `pattern=` 走 Rust regex，不支持前后视断言，因此本模式只能由 Python `re` 执行
# （见 `BookSyncItem._validate_file_path`）。
_RELATIVE_PATH = re.compile(RELATIVE_PATH_PATTERN)

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
    """解析来源：实际引擎标识（模型无感知，差异在适配层消化）。"""

    MINERU = "mineru"
    PADDLEOCR_VL = "paddleocr-vl"
    QWEN_VL = "qwen-vl"


class JobStatus(StrEnum):
    """解析任务状态（终态四值见 ADR 0007 决定 1）。

    `completed` 与版本激活门槛同源（目标页齐备且产物在位）；`partial` = 至少一页成功但未齐备；
    `interrupted` 只由启动期归一写入，管线自身不产出。
    """

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PageState(StrEnum):
    """页状态（af_pages.status）。"""

    PENDING = "pending"
    PARSED = "parsed"
    FAILED = "failed"


class Verdict(StrEnum):
    """块级人工判定。"""

    NONE = ""
    OK = "ok"
    BAD = "bad"


class IngestStatus(StrEnum):
    """ingest 状态（af_books.ingest_status）。"""

    NONE = "none"
    INGESTING = "ingesting"
    INGESTED = "ingested"
    FAILED = "failed"


class ParseStatus(StrEnum):
    """书目解析**进度**（af_books.parse_status）。

    ADR 0007 决定 3：本列只表达「有没有任务在跑」，不表达结局；结局在 `last_parse_result`，
    「哪一版可读」唯一事实源是 `af_books.active_job_id`。
    """

    PENDING = "pending"
    PARSING = "parsing"


class LastParseResult(StrEnum):
    """书目最近一次解析任务的**结局**（af_books.last_parse_result）。`NONE` = 该书从未有任务。"""

    NONE = ""
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


# ---------- 基础类型 ----------

BBox = Annotated[
    tuple[int, int, int, int],
    Field(description="页内坐标 [x0, y0, x1, y1]（原页图像素坐标系，p<NNNN>.png 为基准）"),
]


class Author(BaseModel):
    """书目作者（af_books.authors / book.json.authors）。"""

    name: str
    role: str = ""


# ---------- 块 ----------


class Block(BaseModel):
    """统一块模型：type 判别 + 按类型校验必需字段。

    宽松构造（可选字段全部可选），由 model_validator 按 type 强制必需字段，
    兼顾契约稳定与引擎输出归一化容错。
    """

    type: BlockType
    bbox: BBox
    index: int = Field(ge=0, description="页内块数组下标（0 基，与块编辑定位一致）")
    order: int = Field(ge=0, description="阅读顺序（0 基）")
    # heading / paragraph / caption / header / footer / page_number
    text: str | None = None
    level: int | None = Field(default=None, ge=1, le=6, description="heading 层级 1-6")
    # formula
    latex: str | None = Field(
        default=None,
        description="裸 LaTeX 源码，不含 $/$$ 等数学定界符（数学解析器探索入口）",
    )
    display: bool = Field(default=False, description="行间式（true）或行内式（false）")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="识别置信度")
    # table
    html: str | None = Field(default=None, description="表格 HTML")
    # image
    path: str | None = Field(default=None, description="页图内图片文件相对路径")
    caption: str | None = None
    # list
    items: list[str] | None = None

    @model_validator(mode="after")
    def _check_required_by_type(self) -> Block:
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
    """页质量信号（空块率/公式平均置信度/表格成功数/文本长度比 + 可见丢弃计数）。"""

    empty_block_ratio: float = Field(ge=0.0, le=1.0)
    avg_formula_confidence: float = Field(ge=0.0, le=1.0)
    table_count: int = Field(ge=0)
    text_length_ratio: float = Field(ge=0.0)
    dropped_block_count: int = Field(
        default=0, ge=0, description="引擎返回但未能归一为统一块的条目数（字面值未覆盖或必需字段缺失）"
    )
    dropped_types: list[str] = Field(
        default_factory=list, description="被丢弃条目的原始 type 字面值（去重升序，供人工判定映射缺口）"
    )
    blank_page: bool = Field(
        default=False,
        description="引擎确认该页存在但未回任何内容（合法空白页）；与「解析失败」和「映射缺口」可区分",
    )


class BlocksPage(BaseModel):
    """pages/p<NNNN>.blocks.json：页级结构化数据。"""

    page: int = Field(ge=1, description="页码（1 基，对应 p0001.png）")
    image_size: tuple[int, int] = Field(description="原页图像素尺寸 [width, height]（bbox 基准）")
    source: Source
    blocks: list[Block]
    quality: QualityMetrics | None = None


# ---------- book.json ----------


class BookMeta(BaseModel):
    """book.json：书目元数据（产物侧事实源；af_books 承载同步与进度）。"""

    book_id: str = Field(description="目录名即 book_id，同源 qt_books")
    title: str
    authors: list[Author] = Field(default_factory=list)
    page_count: int = Field(ge=1)
    sha256: str = Field(description="源 PDF SHA-256（64 位十六进制）")
    source_file: str = Field(default="", description="源 PDF 数据根相对路径（同源 qt_books.file_path）")
    engine: str = Field(default="mineru", description="实际解析引擎标识")
    ingested_at: datetime | None = Field(default=None, description="ingest 完成时间（UTC naive）")

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


# ---------- 分块（chunks.json） ----------


class BlockRef(BaseModel):
    """chunk 的块级溯源引用。"""

    page: int = Field(ge=1)
    index: int = Field(ge=0)


class ChunkParams(BaseModel):
    """分块参数（可经 GET /chunks 查询参数覆盖后重算）。"""

    max_chars: int = Field(default=1000, ge=1)
    overlap_blocks: int = Field(default=1, ge=0)


class Chunk(BaseModel):
    """知识单元：blocks 合并结果（formula/table 不可拆），带页/块溯源。"""

    chunk_id: str
    order: int = Field(ge=0)
    type: str = Field(description="mixed / paragraph / formula / table / …（按主导块类型）")
    text: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    block_refs: list[BlockRef]
    bbox: BBox
    char_count: int = Field(ge=0)


class ChunkSet(BaseModel):
    """chunks.json：书级分块产物（派生、不入库、可重建）。"""

    book_id: str
    params: ChunkParams = Field(default_factory=ChunkParams)
    chunks: list[Chunk] = Field(default_factory=list)


# ---------- API：书目 ----------


class BookOut(BaseModel):
    """GET /books 响应：af_books 行（课程归属 + 进度 + 本侧字段）。"""

    book_id: str
    domain_id: str = ""
    course_id: str = ""
    course_name: str = ""
    knowledge_id: str = ""
    title: str = ""
    part: str = ""
    display_title: str = ""
    authors: list[Author] = Field(default_factory=list)
    file_path: str = ""
    page_count: int | None = None
    sha256: str = ""
    ingest_status: str = IngestStatus.NONE
    parse_status: str = ParseStatus.PENDING
    last_parse_result: str = LastParseResult.NONE
    pages_done: int = 0
    active_job_id: str | None = Field(
        default=None, description="当前生效解析版本（af_books.active_job_id）；None = 根视图 legacy"
    )
    notes: str | None = None


class BookSyncItem(BaseModel):
    """POST /books/sync 请求行（同源 qt_books 只读快照）。

    `book_id`/`domain_id`/`course_id` 是数据根路径段，`file_path` 是数据根相对路径，均受
    条文 7 的边界校验约束（违规 422）；绝对路径与 `..` 一律拒绝。
    """

    book_id: str = Field(pattern=SAFE_SEGMENT_PATTERN)
    domain_id: str = Field(default="", pattern=OPTIONAL_SEGMENT_PATTERN)
    course_id: str = Field(default="", pattern=OPTIONAL_SEGMENT_PATTERN)
    course_name: str = ""
    knowledge_id: str = ""
    title: str = ""
    part: str = ""
    display_title: str = ""
    authors: list[Author] = Field(default_factory=list)
    file_path: str = Field(default="", max_length=512)

    @field_validator("file_path")
    @classmethod
    def _validate_file_path(cls, value: str) -> str:
        """数据根相对路径校验（条文 7 + REQ-083）：违规 422。"""
        if not _RELATIVE_PATH.match(value):
            raise ValueError(f"file_path 含遍历段或非法字符：{value!r}")
        return value


class SyncResponse(BaseModel):
    """POST /books/sync 响应：幂等 upsert 统计与结果行。"""

    synced: int = Field(ge=0)
    updated: int = Field(ge=0)
    books: list[BookOut] = Field(default_factory=list)


class BookUpdate(BaseModel):
    """PATCH /books/{id} 请求：Axiom-Flow 自有字段。"""

    notes: str | None = None


class IngestResponse(BaseModel):
    """POST /books/{id}/ingest 响应。"""

    book_id: str
    page_count: int = Field(ge=1)
    sha256: str
    ingest_status: str = IngestStatus.INGESTED


# ---------- API：对照与编辑 ----------


class EditRecord(BaseModel):
    """块级人工编辑记录（af_block_edits 覆盖层，绑定其所属解析版本）。"""

    edit_id: str
    book_id: str
    job_id: str = ""
    page_no: int = Field(ge=1)
    block_index: int = Field(ge=0)
    block_type: str = ""
    verdict: Verdict = Verdict.NONE
    note: str = ""
    corrected_text: str | None = None
    corrected_bbox: BBox | None = None
    edited_at: datetime | None = None
    updated_at: datetime | None = None


class ParseVersionOut(BaseModel):
    """GET /books/{id}/versions 响应行：一个解析版本（= 一次任务）的自证信息。

    只读列举，供对照视图切换与审计；`parsed` 计入从基线继承的页，因此逐页重解析的版本同样
    显示为完整快照的页数。
    """

    job_id: str
    engine: str = ""
    status: str = ""
    page_count: int | None = None
    parsed: int = 0
    source_sha256: str = ""
    created_at: datetime | None = None
    active: bool = False


class EditRequest(BaseModel):
    """PUT /blocks/{index}/edit 请求（同块重复编辑覆盖更新）。"""

    verdict: Verdict = Verdict.NONE
    note: str = Field(default="", max_length=1000)
    corrected_text: str | None = None
    corrected_bbox: BBox | None = None


class PageStateOut(BaseModel):
    """`GET /books/{id}/pages` 的清单行：**轻量**，不含 blocks / markdown / 质量信号。

    客户端只需一条判据 `not has_artifact or status == "failed"` 即可覆盖「从未跑过 / 跑失败 /
    产物被外部删除」三种形态；`status` 是页行原值，读取面不改写。
    """

    page_no: int
    status: PageState
    has_artifact: bool = Field(description="该页 blocks.json 在生效作用域目录内存在（磁盘事实）")
    error: str = ""
    edit_count: int = Field(default=0, description="本版本作用域内该页的 af_block_edits 行数")


class PageListOut(BaseModel):
    """`GET /books/{id}/pages` 响应：**不分页**，逐页连续覆盖 `1..total`。

    `scope`/`job_id` 是作用域自证（读了哪一版），与 `af_books.active_job_id` 同源。
    """

    book_id: str
    job_id: str
    scope: Literal["active", "legacy"]
    total: int
    pages: list[PageStateOut] = Field(default_factory=list)


class PageData(BaseModel):
    """GET /books/{id}/pages/{no} 响应：对照主数据源（已合并人工编辑）。"""

    blocks: BlocksPage
    markdown: str = ""
    image_url: str
    edits: list[EditRecord] = Field(default_factory=list)


# ---------- API：解析任务 ----------


class ParseJobCreate(BaseModel):
    """POST /parse-jobs 请求体。"""

    book_id: str = Field(pattern=SAFE_SEGMENT_PATTERN)
    pages: list[int] | None = Field(default=None, description="页范围（1 基）；None = 全部页（全本解析）")
    engine: Literal["mineru", "paddleocr-vl", "qwen-vl"] | None = Field(
        default=None, description="解析引擎；缺省回落 AXIOM_OCR_ENGINE（不自动兜底）"
    )


class ParseJob(BaseModel):
    """解析任务（状态与进度，GET 响应）。"""

    id: str
    book_id: str
    pages: list[int] | None = None
    engine: str = "mineru"
    status: JobStatus
    progress: dict[str, Any] = Field(default_factory=dict, description="如 {'parsed': 2, 'total': 5}")
    failed_pages: list[int] = Field(default_factory=list, description="失败页码（ADR 0007 决定 2，不截断）")
    error: str = ""


class ParseJobListItem(ParseJob):
    """`GET /parse-jobs` 的行：`ParseJob` 字段原样继承 + 列表专用四字段。

    排序键 `created_at` 必须出现在响应里——客户端看不到它就无法稳定翻页，列表默认倒序也无处展示；
    `status` 仍由 `JobStatus` 六态原值透传，读取面不做归并（ADR 0004 无静默兼容层）。
    """

    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    active: bool = Field(default=False, description="是否为所属书目的当前生效版本（= af_books.active_job_id）")


class ParseJobListOut(BaseModel):
    """`GET /parse-jobs` 响应信封：`total` 是**过滤后的总行数**，客户端据此判定还有没有下一页。"""

    jobs: list[ParseJobListItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 50
    offset: int = 0


# ---------- API：非公开管理面（PLAN-010 / ARCH-024） ----------


class AdminPurgeScope(BaseModel):
    """清除作用域：`all` 与 `book_ids` **互斥且必填**，且没有默认值。

    「清哪些」猜不得：两者皆缺或同时给出由端点判 400（不进删除）。缺省即全删的接口一旦被人误调，
    代价是不可回滚的产物与库行丢失，所以宁可让调用方每次显式说清范围。
    """

    model_config = ConfigDict(extra="forbid")

    all: bool | None = Field(default=None, description="true = 全项目（af_* 四表全部行 + parsed/ 所有书目录）")
    book_ids: list[str] | None = Field(
        default=None, description="按书目清除（含其全部解析版本与任务行）；每项须为安全路径段"
    )

    @field_validator("book_ids")
    @classmethod
    def _validate_book_ids(cls, value: list[str] | None) -> list[str] | None:
        for book_id in value or []:
            if not re.match(SAFE_SEGMENT_PATTERN, book_id):
                raise ValueError(f"book_id 含非法路径段：{book_id!r}")
        return value


class AdminPurgeCounts(BaseModel):
    """清除计数：`dry_run` 下是**将删**计数，真删下是**已删**计数（两态形状相同）。"""

    af_books: int = 0
    af_parse_jobs: int = 0
    af_pages: int = 0
    af_block_edits: int = 0
    directories: int = 0


class AdminPurgeIn(BaseModel):
    """`POST /api/v1/admin/purge` 请求体（双确认：`dry_run` 缺省 true + 逐字 `confirm`）。"""

    model_config = ConfigDict(extra="forbid")

    scope: AdminPurgeScope
    dry_run: bool = Field(default=True, description="缺省即演练：只回清单与计数，磁盘与库零改动")
    confirm: str = Field(default="", description="仅 dry_run=false 时校验，须逐字为 PURGE")


class AdminPurgeOut(BaseModel):
    """清除结果自证：删了什么、删掉多少、哪些没成。

    `targets` 是**相对数据根**的路径（绝对磁盘位置绝不进响应）；`failures` 是人可读中文摘要，
    同样不含凭据与绝对路径。任一步失败不回滚已成功步，残行由下一次幂等清除收掉。
    """

    scope: AdminPurgeScope
    dry_run: bool
    targets: list[str] = Field(default_factory=list)
    deleted: AdminPurgeCounts = Field(default_factory=AdminPurgeCounts)
    failures: list[str] = Field(default_factory=list)
