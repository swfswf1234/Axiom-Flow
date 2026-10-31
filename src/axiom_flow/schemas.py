"""Axiom-Flow 统一格式 schema：内外契约单一事实源（设计见 docs/design/parsing-pipeline.md）。

产物形态（`<QED_DATA_ROOT>/parsed/<domain_id>/<course_id>/<book_id>/`）：
- 根视图（ingest 拥有）   ← 只有 `pages/p*.png` 页图；`book.json`/`manifest.json` 已退役（REQ-097）
- `versions/<job_id>/`   ← 一次解析任务 = 一个完整快照版本（book.json + pages/ + chunks.json + manifest.json，唯一正本）
- `chunks.json`             ← ChunkSet（派生、不入库、可重建；版本目录内）
- `pages/p<NNNN>.blocks.json` ← BlocksPage（块级结构化，渲染 + 向量化共同基座）
- `pages/p<NNNN>.md`          ← 页级 Markdown（`$$...$$` / `$...$` 内嵌公式）

生效版本由 `af_books.active_job_id` 单点指认（NULL = 根视图 legacy）；读取端点与 URL 不因
版本化改变（PLAN-004 / REQ-080）。

API 契约模型：BookOut / BookSyncItem / IngestResponse / PageData / EditRecord / StructureRequest /
RegionRecord / ParseVersionOut / ParseJob，与知识点梳理面 `Kp*`／`AnchorReviewOut`（`PLAN-025`）。
块类型 10 种；解析来源 source ∈ {mineru, paddleocr-vl, qwen-vl}（后两者预留）。
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------- 数据根边界（PP-5） ----------

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


class RegionOp(StrEnum):
    """页级人工结构意图三态（`af_operations` structure 意图行的 `payload.op`）。"""

    ADD = "add"
    DELETE = "delete"
    MERGE = "merge"


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
    """页质量信号（空块率/公式平均置信度/表格成功数/文本长度比 + 可见丢弃与空框计数）。"""

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
    empty_region_count: int = Field(
        default=0,
        ge=0,
        description="区域驱动页中无识别内容的框数（PP-21 可见计数）；非区域页恒 0",
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
    """版本级 book.json：书目元数据快照（事实源是 af_books，由解析任务生成到版本目录）。"""

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
    `PP-5` 的边界校验约束（违规 422）；绝对路径与 `..` 一律拒绝。
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
        """数据根相对路径校验（`PP-5` + REQ-083）：违规 422。"""
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
    """块级人工编辑记录（`af_operations` 的 edit 意图行，绑定其所属解析版本）。

    `block_type`＝产物原类型的只读快照，`corrected_type`＝人工改判（`None`＝没改判过，
    回放腿据此分辨「没判」与「判成某类型」）。
    """

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
    corrected_type: BlockType | None = None
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
    """PUT /blocks/{index}/edit 请求（同块重复编辑覆盖更新）。

    `corrected_type` 是**人工改判后的类型**（REQ-142 的标注腿），取值受 `BlockType` 十枚闭集校验；
    与响应侧的 `block_type`（产物原类型只读快照）分职——前者可改、后者是「改之前长什么样」的证据。
    `extra="forbid"`：闭集外的键当场 422，不静默吞（`database-design.md` 的「禁自由键」同一条纪律）。
    """

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict = Verdict.NONE
    note: str = Field(default="", max_length=1000)
    corrected_text: str | None = None
    corrected_bbox: BBox | None = None
    corrected_type: BlockType | None = None


# ---------- 页级结构意图（af_operations 的 structure 意图行 / PUT …/structure，ADR 0010） ----------


class BlockLocator(BaseModel):
    """结构意图的定位入参：三选一，服务端受理时统一换算为几何锚。

    全部字段可选是**有意**的——「三项全缺」要判 400（语义错误、告诉调用方怎么改），而不是
    422（形状错误）；由端点统一裁决，避免 pydantic 的聚合报错把定位问题说成字段缺失。
    `block_index` 只在当前生效版本内有意义，换版后同下标指向别的块。
    """

    model_config = ConfigDict(extra="forbid")

    block_index: int | None = Field(default=None, ge=0)
    region_id: str | None = None
    bbox: BBox | None = None


class MergeGroup(BaseModel):
    """一组待合并块：`targets` 的数组序即合并后的阅读序。"""

    model_config = ConfigDict(extra="forbid")

    targets: list[BlockLocator] = Field(default_factory=list)


class StructureRequest(BaseModel):
    """`PUT …/pages/{page_no}/structure` 请求体：该页结构意图的**全量声明**。

    缺席即撤销——本次未列出的既有人工意图会被删除，因此重复提交同一组意图结果不变（幂等）。
    几何与数量约束（负坐标、`x1≤x0`、合并成员少于 2、意图总数超上限）由端点判 400，不放在这里：
    这些是业务口径而非字段形状，需要与页块几何一起判。
    """

    model_config = ConfigDict(extra="forbid")

    adds: list[BBox] = Field(default_factory=list)
    deletes: list[BlockLocator] = Field(default_factory=list)
    merges: list[MergeGroup] = Field(default_factory=list)


class RegionRecord(BaseModel):
    """structure 意图行的响应投影（页级人工结构意图，不绑解析版本）。

    `region_id` 由服务端按 `op` + 几何身份确定性派生：同一意图重复提交得到同一 id，跨版本不变，
    根侧可长期持有。`anchor_bbox` / `members` 是受理时从生效版本产物抓的**几何快照**，
    版本切换后按 IoU 重锚定用它作参照。
    """

    region_id: str
    op: RegionOp
    bbox: BBox | None = None
    anchor_bbox: BBox | None = None
    members: list[BBox] | None = None
    kind: str = ""
    note: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PageStateOut(BaseModel):
    """`GET /books/{id}/pages` 的清单行：**轻量**，不含 blocks / markdown / 质量信号。

    客户端只需一条判据 `not has_artifact or status == "failed"` 即可覆盖「从未跑过 / 跑失败 /
    产物被外部删除」三种形态；`status` 是页行原值，读取面不改写。
    """

    page_no: int
    status: PageState
    has_artifact: bool = Field(description="该页 blocks.json 在生效作用域目录内存在（磁盘事实）")
    error: str = ""
    edit_count: int = Field(default=0, description="本版本作用域内该页的 edit 意图行数")


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
    """GET /books/{id}/pages/{no} 响应：对照主数据源（已合并人工编辑）。

    `furniture_blocks` 是**读时派生**的家具块下标（人工改判三型 ∪ 跨页重复线，见
    `normalize/furniture.py`）：产物文件与 `markdown` 不经它，屏侧据此逐块隐藏；没有家具的页是空清单。
    """

    blocks: BlocksPage
    markdown: str = ""
    image_url: str
    edits: list[EditRecord] = Field(default_factory=list)
    furniture_blocks: list[int] = Field(
        default_factory=list,
        description="家具块下标（0 基、版本内块坐标；类型维∪跨页重复线，升序）",
    )


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


# ---------- API：知识点梳理（af_kp，PLAN-025 / KA-n） ----------


#: 人工评审备注的上限（AF-T5 甲）：一段到几段意见够用，再长就是在本体之外塞正文了。
#: 超限判 422 而不是截断——备注被砍半会静默改变这一轮梳理的取向，与 `NARRATIVE_MAX_CHARS`
#: 「超限不截」同一条口径；批次流水的 `payload` 是 JSON，落库存全文、不受列宽约束。
KP_REVIEW_NOTE_MAX_CHARS = 2000

#: 一批跑哪几条腿（`KA-34`／`KA-43`）：`full`＝目录＋知识点两腿，`outline`＝只跑目录、知识点阶段
#: 整条不发起，`content`＝目录腿不发起、拿**在册**目录行做知识点并同批补节级总结。
#: 用三值闭集而不是一枚布尔，是因为「跑哪几条腿」不是一个轴上的开关：`content` 有库侧前置
#: （零枚在册目录行即拒），布尔形状表达不出「这一值要读库才判得出能不能受理」。
KpStage = Literal["full", "outline", "content"]


class KpJobCreate(BaseModel):
    """`POST /kp-jobs` 请求体：一册书 + 可选页区间（`KA-25`，缺省＝整本书）+ 可选人工评审备注
    + 可选阶段（`KA-34`，缺省＝两腿全跑）。

    **没有 `engine` 字段**：梳理引擎取服务端槽位（`AXIOM_KP_ENGINE`），缺配即受理时 503（`KA-22`）。
    让请求体能点名引擎，等于让调用方绕过「未接入就拒」这道门槛，成本归因（`KA-23`）也就失去落点。

    `stage` 是同一件事的范围参数，与 `page_start`／`page_end` 同类——它不另开端点，因为「这一批
    做了什么」在流水里必须是一行两处读（`KA-27`）。域外值由 pydantic 判 422，先于六道受理门；
    `content` 另有一道库侧前置（在册目录行零枚即拒，`KA-43`），排在页区间之后、在飞之前。
    """

    model_config = ConfigDict(extra="forbid")

    book_id: str = Field(pattern=SAFE_SEGMENT_PATTERN)
    page_start: int | None = Field(default=None, ge=1, description="单章范围的起始页（1 基）")
    page_end: int | None = Field(default=None, ge=1, description="单章范围的结束页（1 基，含）")
    review_note: str = Field(
        default="",
        max_length=KP_REVIEW_NOTE_MAX_CHARS,
        description="人工评审意见：注入本轮两阶段 prompt，并随批次流水 payload 存档（AF-T5 甲）",
    )
    stage: KpStage = Field(
        default="full",
        description=(
            "本批跑哪几条腿：full＝目录＋知识点，outline＝只梳理目录（KA-34），"
            "content＝拿在册目录只补知识点与节级总结（KA-43）"
        ),
    )


class KpJobAccepted(BaseModel):
    """受理成功的批次身份：客户端拿 `gen_batch` 轮询进度，之后不再自己猜批次号。"""

    book_id: str
    gen_batch: str
    evidence_job_id: str = Field(description="本批的取证版本（受理一刻的生效版本，`KA-1`/`KA-18`）")
    page_start: int
    page_end: int
    status: Literal["queued"] = "queued"


class KpLedgerOut(BaseModel):
    """一行批次流水（`op_kind='kp'`，`KA-27`）：只答「这一批发生过什么、账面如何」。

    `result` 是执行体跑完的计数集（键闭集见 `KP_RESULT_KEYS`）；失败批次没有 `result`——
    账面只在跑通的那条路上存在，失败的原因在 `error`。本行**不是当前态判据**（在飞读 `af_kp`）。
    """

    gen_batch: str
    book_id: str
    job_id: str
    status: Literal["ok", "failed"]
    page_start: int | None = None
    page_end: int | None = None
    #: 这一批是照着哪句人工意见跑的（AF-T5 甲）：取 `payload.review_note`，缺省空串而不是缺键。
    review_note: str = ""
    #: 这一批跑了哪条腿（`KA-34`）：取 `payload.stage`，缺省 `full`——早期批次没有这一键，
    #: 而它们确实是两腿全跑的，回落值与事实一致，不必为「有没有这一键」维持两种形状。
    stage: KpStage = "full"
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: datetime | None = None


class KpJobProgressOut(BaseModel):
    """`GET /books/{id}/kp-jobs/{gen_batch}`：批次三态计数 + 流水行，`in_flight` 由 `draft` 行数判。

    在飞判据与 `KA-4` 同源（`af_kp` 里有没有 `draft` 行），不看流水行的 `status`——事件行禁作判据。
    `ledger` 可为 `null`：批次还在跑时流水行尚未写出，这一位就是「还在跑」的读数。
    """

    book_id: str
    gen_batch: str
    in_flight: bool
    counts: dict[str, int] = Field(default_factory=dict, description="按 gen_status 与 node_kind 的计数")
    ledger: KpLedgerOut | None = None


class AnchorReviewOut(BaseModel):
    """一条复核标记（`KA-19` 的越界与 `KA-3` 的未确认锚点）：点名节点、页与原因，不改写数据。"""

    node_id: str
    page: int
    reason: str


class KpNodeOut(BaseModel):
    """`af_kp` 一行的响应投影：人工背书域 + 来源与状态读数。

    `norm_key` 与整槽 `meta` 不外发——前者是去重用的派生值（冲突由 422 的点名承载），后者是扩展槽；
    唯一的例外是 `meta` 里的两枚身份位 `toc_role`／`toc_kind`（`KA-39`），它们逐字带出供屏上标「非知识点」。
    `rev` 每次服务端写入自增，是 `if_rev` 乐观并发的对手值（CW-6）；确认位只有 `confirmed` 一列。
    """

    node_id: str
    book_id: str
    job_id: str = Field(description="取证版本；''＝人工直填行，无锚可解释（`KA-18`）")
    node_kind: Literal["outline", "point"]
    node_type: Literal["chapter", "point", "exercise", "other"] = Field(
        default="chapter",
        description="四值类型（`KA-44`，读时派生不落列）：exercise＝`toc_kind='exercise'` 或其祖先为习题；"
        "other＝其余 `toc_role='reference'` 行（符号表／书目／附录／前后记）；knowledge 行按层级 1 分成"
        " chapter、2 及以上分成 point。屏上标签只认这四词，不再按 `node_kind` 出「章节」。",
    )
    parent_id: str | None = None
    depth: int | None = None
    order_hint: int | None = None
    name: str
    role: str = ""
    type: str = ""
    summary: str = ""
    links: list[dict[str, Any]] = Field(default_factory=list)
    anchors: list[dict[str, Any]] = Field(default_factory=list)
    page_start: int | None = Field(
        default=None,
        description="读时派生的正文区间起点（`KA-42`，不落列）：目录行＝自身锚点页的最小值，"
        "点行＝自身锚点页的最小值；没有锚点即 `null`（披露，不编区间）。",
    )
    page_end: int | None = Field(
        default=None,
        description="读时派生的正文区间终点（`KA-42`，不落列）：目录行＝排序后下一枚在册目录行的起点页 − 1，"
        "末行到 `af_books.page_count`（该列缺 ⇒ 折回本行起点页）；点行＝自身锚点页的最大值。",
    )
    narrative_ref: str = ""
    gen_status: Literal["draft", "candidate", "rejected"]
    gen_batch: str = ""
    model_ref: str = ""
    toc_role: str = Field(
        default="knowledge",
        description="目录行的知识点身份位（`KA-39`，取自 `meta`）：'reference'＝正文前后的附件行。"
        "整槽 `meta` 仍不外发，只把这两枚键原样带出；点行与旧批次行恒为 'knowledge'。",
    )
    toc_kind: str = Field(
        default="",
        description="附件行的类别（`KA-39`）：front_matter／back_matter／symbol_table／appendix／"
        "bibliography／index／exercise；`toc_role='knowledge'` 时为空串。",
    )
    confirmed: int = 0
    rev: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class KpWorkspaceOut(BaseModel):
    """`GET /books/{id}/kp`：整册工作区（目录＋点＋未归档池一次给全）与复核标记。

    不分页是形状决定：目录树与「谁挂在谁下面」必须整册读，翻页会把结构切成两屏。
    `in_flight_batch` 就是 `KA-4` 的在飞批次号（无在飞为 `null`），界面据此禁用提交按钮。
    """

    book_id: str
    in_flight_batch: str | None = None
    nodes: list[KpNodeOut] = Field(default_factory=list)
    review_flags: list[AnchorReviewOut] = Field(default_factory=list)


class KpLinkIn(BaseModel):
    """一条关系边：`kind` 闭集五种，端点由服务端校验存在性与自环（`database-design.md`「服务端不变式」）。"""

    kind: Literal["prerequisite", "co_requisite", "explains", "exemplifies", "extends"]
    to: str = Field(min_length=1, max_length=64)
    note: str = ""


class KpAnchorIn(BaseModel):
    """一条来源锚点：页序是 **PDF 页序**，`block_indexes` 属该行 `job_id` 那一版的块序空间（`KA-18`）。"""

    kind: str = Field(default="page_block", min_length=1, max_length=32)
    ref_id: str = ""
    page: int = Field(ge=1)
    block_indexes: list[int] = Field(default_factory=list)
    snippet: str = ""


class KpNodePatch(BaseModel):
    """人工背书域的整节点声明（`PUT`／`PATCH` 的 body）：只含 `KA-11` 人工可写的列。

    缺席与显式 `null` 是两件事：只有出现在请求体里的键才参与写入（`model_fields_set`），
    否则「只想改 `summary`」会变成把其余六列一起覆盖成空。`if_rev` 缺省＝不比序号。
    来源三列（`gen_batch`/`model_ref`/`gen_status`）与 `confirmed` 不在本模型里——改不动是形状保证。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = Field(default=None, max_length=64)
    order_hint: int | None = None
    links: list[KpLinkIn] | None = None
    anchors: list[KpAnchorIn] | None = None
    role: Literal["", "core", "explainer", "example"] | None = None
    type: Literal["", "memory", "procedure", "concept", "design"] | None = None
    summary: str | None = Field(default=None, max_length=1000)
    if_rev: int | None = Field(default=None, ge=0, description="乐观并发：与当前 `rev` 不符即 409（CW-6）")


class KpSummaryRegenIn(BaseModel):
    """单点重生成（`KA-45`）：详情面「重新生成总结」的请求体，只有一枚可选序号。

    没有正文键：新总结由模型按该行区间现读现写，人给的那份走 `PATCH` 的 `summary`。
    `if_rev` 与其余写腿同一口径——屏上读到的那一份被别处改过时先撞 409，不静默覆盖。
    """

    model_config = ConfigDict(extra="forbid")

    if_rev: int | None = Field(default=None, ge=0, description="乐观并发：与当前 `rev` 不符即 409（CW-6）")


class KpConfirmIn(BaseModel):
    """确认／撤回（`KA-14`/`KA-17`）：置位跑「可回指」前置校验，撤回永远可执行。"""

    model_config = ConfigDict(extra="forbid")

    confirmed: bool
    if_rev: int | None = Field(default=None, ge=0)


class KpConfirmOut(BaseModel):
    """确认动作的结果：`missing` 逐条点名缺了哪个锚点页、哪条边的端点（422 的正文）。"""

    node_id: str
    confirmed: int
    rev: int
    missing: list[str] = Field(default_factory=list)


#: 详解正文的上限（`KA-30`）：与其余长度闸不同，这一枚**不背列宽**——`af_kp.narrative_ref` 只存单段
#: 文件名，正文在磁盘上。上限管的是「一次请求能吃下多大一篇」，取值口径与根侧读长文的字节闸同量级。
NARRATIVE_MAX_CHARS = 512_000


class KpNarrativeWriteIn(BaseModel):
    """长文写腿的请求体（`KA-30`）：只收一篇**已成文**的正文。

    形状闸在模型里而不在处理函数里：空正文与超限都在查行之前判 422，且**不截断**——长文被砍一半
    不会报错，只会让人对着一篇缺尾的详解讲不下去（与 `summary[:1000]` 的截断方向故意相反）。
    文件名、路径与所属书都不在请求体里：路径唯一由 `KA-20` 的派生式算出，越根的口子是形状保证，
    不是运行期判断。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=NARRATIVE_MAX_CHARS)
    if_rev: int | None = Field(default=None, ge=0, description="乐观并发：与当前 `rev` 不符即 409（CW-6）")


class KpNarrativeOut(BaseModel):
    """长文两条腿共用的响应体：`exists` 是「列有值而文件不在位」的披露位（`KA-30`）。

    读的是**文件**不是列：`narrative_ref` 只存单段文件名，路径永远现算。缺文件仍回 200 带
    `exists=false`——判 404 会把「节点存在但正文缺」谎报成「节点不存在」，而这个矛盾正是读侧要
    显示出来的东西。`bytes` 按 UTF-8 计，与 `content` 的字符数是两个口径。
    """

    node_id: str
    narrative_ref: str = ""
    exists: bool
    content: str = ""
    bytes: int = 0
    rev: int
    updated_at: datetime | None = None


class KpOutlineDeclIn(BaseModel):
    """目录声明的一项：只有身份与挂载两列（`KA-8`），层数与名不是声明面能改的东西。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64)
    parent_id: str | None = Field(default=None, max_length=64)
    order_hint: int = 0


class KpOutlineDeclareIn(BaseModel):
    """目录**整批**声明（`KA-8`）：全集语义——缺席的目录行不下线，只让它的子点退回未归档池。"""

    model_config = ConfigDict(extra="forbid")

    declarations: list[KpOutlineDeclIn] = Field(default_factory=list)


class KpOutlineDeclareOut(BaseModel):
    """声明回显：`outline` 按声明序（界面不需要再排一次），`detached_points` 是退池的行数。"""

    book_id: str
    outline: list[KpNodeOut] = Field(default_factory=list)
    detached_points: int = 0


class KpExportOut(BaseModel):
    """只读导出面（`KA-15`）：`confirmed=1` 的行，一册全给，目录先于点。

    空集是 200 不是 404——「这本书还没有已确认结论」是合法现状，拉取方按 `rev`/`updated_at` 增量。
    `review_flags` 承载 `KA-3` 的外发提示（锚点页未确认），只提示不拦截。
    """

    book_id: str
    nodes: list[KpNodeOut] = Field(default_factory=list)
    review_flags: list[AnchorReviewOut] = Field(default_factory=list)
    total: int = 0


# ---------- API：非公开管理面（PLAN-010 / ARCH-024） ----------


class AdminPurgeScope(BaseModel):
    """清除作用域：`all` 与 `book_ids` **互斥且必填**，且没有默认值。

    「清哪些」猜不得：两者皆缺或同时给出由端点判 400（不进删除）。缺省即全删的接口一旦被人误调，
    代价是不可回滚的产物与库行丢失，所以宁可让调用方每次显式说清范围。
    """

    model_config = ConfigDict(extra="forbid")

    all: bool | None = Field(default=None, description="true = 全项目（af_* 表族全部行 + parsed/ 所有书目录）")
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
    """清除计数：字段名 = `af_*` 表名，由 `AF_TABLES` 派生守护（加表必须同批加字段）。

    `af_operations` 一列合计了旧三表（任务版本 + 编辑 + 结构意图）的行数：删除作用域是
    `(book_id)`，折叠后不再有三个独立计数位。
    `dry_run` 下是**将删**计数，真删下是**已删**计数（两态形状相同）。
    """

    af_books: int = 0
    af_operations: int = 0
    af_pages: int = 0
    af_kp: int = 0
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
