"""Axiom-Flow 私有表 ORM 模型（`qed` 库 af_* 命名空间）。

表结构事实源：`docs/architecture/database-design.md`；Alembic 迁移见
`src/axiom_flow/migrations/versions/`。档 B 整族重建后四表一次建全（2026-10-21 用户裁决，
PLAN-024）：`af_books`（书目快照与进度）、`af_operations`（事件明细与人工意图）、
`af_pages`（唯一结果索引）、`af_kp`（知识点）。
索引名与迁移/DDL 保持一致（显式命名，避免 autogenerate 漂移）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """af_* 表族声明基类。"""


#: 「根视图」哨兵 job_id：`af_pages.job_id` 的作用域哨兵（`active_job_id IS NULL` 时的读取作用域）。
#: `af_operations` 内不出现该值——事件行必须有真实 `op_id`。
LEGACY_JOB_ID = "legacy"

#: `af_operations.lifecycle` 闭集：流水永不参与当前态判据，人工意图行是唯一合法判据位。
LIFECYCLE_EVENT = "event"
LIFECYCLE_INTENT = "intent"

#: `af_operations.op_kind` 闭集（扩值须评审：种类是判据的分母）。
OP_KIND_PARSE = "parse"
OP_KIND_EDIT = "edit"
OP_KIND_STRUCTURE = "structure"
OP_KIND_CONFIRM = "confirm"
OP_KIND_INGEST = "ingest"
OP_KIND_SYNC = "sync"
OP_KIND_PURGE = "purge"
#: 检索索引版本：**本轮建值但无写者**，守护断言该值行数恒 0（索引行语义随检索面立项才定）。
OP_KIND_INDEX = "index"
#: 梳理批次流水：行语义已定义（`job_id`＝取证版本、`target_id`＝节点、`result`＝计数），
#: 但**本轮建值不建写者**（写者随梳理实现批），与 `index` 同受「计数恒 0」守护。
OP_KIND_KP = "kp"

OP_KINDS = (
    OP_KIND_PARSE,
    OP_KIND_EDIT,
    OP_KIND_STRUCTURE,
    OP_KIND_CONFIRM,
    OP_KIND_INGEST,
    OP_KIND_SYNC,
    OP_KIND_PURGE,
    OP_KIND_INDEX,
    OP_KIND_KP,
)

#: `actor` 闭集：人工确认门与自动归一必须可分列。
ACTORS = ("human", "system", "model")

#: `af_kp.gen_status` 闭集：人工终态只由 `confirmed` 一列表达，域内不设「人已采纳」这类第二值。
GEN_STATUSES = ("draft", "candidate", "rejected")


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
        comment="当前生效解析版本 = af_operations 的 op_id（NULL = 根视图 legacy）；生效指针唯一事实源",
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="扩展槽（结构化临时读数，人写自由文本仍走 notes）；同步 upsert 不得触碰本列",
    )
    synced_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AfOperation(Base):
    """af_operations：一次操作＝一行（事件明细表）。

    两种语义由 `lifecycle` 分列：`event` 是不可变流水（只答「发生过什么」，禁作判据），
    `intent` 是人工当前态（唯一合法判据位，靠 `intent_key` 唯一键做 upsert）。
    `op_kind='parse'` 的行**就是解析版本**：`op_id` 即版本号，`job_id` 强制等于 `op_id`。
    """

    __tablename__ = "af_operations"
    __table_args__ = (
        UniqueConstraint("intent_key", name="uq_af_operations_intent"),
        Index("ix_af_operations_book", "book_id", "op_kind", "created_at"),
        Index("ix_af_operations_version", "job_id", "page_no"),
        Index("ix_af_operations_sig", "book_id", "parser_signature", "status"),
    )

    op_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="事件身份；parse 行的 op_id 就是版本号（产物目录与生效指针按值指它）"
    )
    book_id: Mapped[str] = mapped_column(String(100), nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="版本坐标：parse 行等于 op_id；edit/confirm 行＝绑定的生效版本；kp 行＝取证版本；structure/ingest/sync/purge 行＝''",
    )
    op_kind: Mapped[str] = mapped_column(
        String(24), nullable=False, comment="闭集 parse/edit/structure/confirm/ingest/sync/purge/index/kp（无默认值）"
    )
    lifecycle: Mapped[str] = mapped_column(
        String(16), nullable=False, default=LIFECYCLE_EVENT, comment="event=不可变流水 / intent=人工当前态"
    )
    actor: Mapped[str] = mapped_column(
        String(16), nullable=False, default="human", comment="human/system/model：确认门与自动归一可分列"
    )
    page_no: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="页坐标；NULL＝不落在具体页")
    block_index: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="块坐标（edit）；下标语义随版本产物重排，故编辑绑版本"
    )
    target_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="被操作对象稳定身份：structure 行＝region_id、kp 相关行＝node_id，其余 ''",
    )
    intent_key: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="意图幂等键 md5(坐标) 前 16 位；event 行恒 NULL（MySQL 多行 NULL ⇒ 只对意图行生效的唯一索引）",
    )
    engine: Mapped[str] = mapped_column(
        String(24), nullable=False, default="", comment="parse 行的显式引擎名；缺引擎名应报错而非静默回落"
    )
    pages: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="parse 行的请求页集（NULL＝全本）；受理门与在飞判据都读它"
    )
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="建任务时的全书页数快照")
    source_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="版本自证：本版实际按哪份源文件跑的"
    )
    parser_signature: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="解析代际签名；'' = 未记录（未知代际，只判需重跑不判过时）",
    )
    embedding_signature: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="检索索引代际签名；本轮建列不建写者，任何路径不得写出非空值（守护断言计数恒 0）",
    )
    render_profile: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="版本级格式档案（闭集常量）；同代内不允许逐页异构"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        comment="parse 行六态原值透传；非 parse 行只用 ok/failed",
    )
    progress: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, comment="parse 行进度读数（被轮询的热列，杂项不得混入）"
    )
    failed_pages: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, comment="结构化失败页码数组（不截断；ADR 0007 决定 2）"
    )
    error: Mapped[str] = mapped_column(String(500), nullable=False, default="", comment="人读摘要（≤500）")
    payload: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict, comment="操作载荷（形状闭集由单测断言，禁自由键）"
    )
    result: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="操作结果读数（受影响页/块、生成条数、拒绝原因码）；NULL＝无额外读数"
    )
    note: Mapped[str] = mapped_column(String(1000), nullable=False, default="", comment="人写备注")
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="扩展槽（三条红线见通则指针）")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, comment="事件发生时刻")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, comment="意图行最后改写时刻；event 行只由 parse 行的条件回写移动，其余恒等于 created_at"
    )


class AfPage(Base):
    """af_pages：一页在一个解析版本内的状态、质量、在位产物索引、确认态与页域。

    全族**唯一的结果索引表**：就绪判据只读本表 `status` 与 `af_books.active_job_id`，
    `artifacts` 只作完整性披露、不作判据（DB 是就绪唯一事实源，ADR 0015）。
    """

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
    artifacts: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        comment="页级在位产物索引 {png/md/blocks: {present,bytes,sha256,block_count}}；不存正文与绝对路径，不作就绪判据",
    )
    confirm_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="unconfirmed",
        comment="版本内人工确认态 unconfirmed/confirmed/rejected；不跨版本继承",
    )
    section_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", comment="页域角色闭集（cover/preface/…/other）；''＝未判"
    )
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="扩展槽（三条红线见通则指针）")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AfKp(Base):
    """af_kp：知识点梳理的**单表**（分析模块唯一一张表）。

    层级靠 `parent_id` 自引用，关系靠 `links` JSON（明文越闸例外，转表触发判据见
    `database-design.md`「准入守则的答卷与例外登记」）。`job_id` 只作取证溯源、不进作用域键：
    知识点是跨版本存活的人工资产。批次状态只由 `gen_status` 表达，故不需要批次表。
    """

    __tablename__ = "af_kp"
    __table_args__ = (
        Index("ix_af_kp_book", "book_id", "node_kind", "gen_status"),
        Index("ix_af_kp_parent", "parent_id"),
        Index("ix_af_kp_batch", "gen_batch"),
        Index("ix_af_kp_norm", "book_id", "node_kind", "norm_key"),
    )

    node_id: Mapped[str] = mapped_column(
        String(64), primary_key=True, comment="稳定身份 afkp-<hex12>（服务端随机发放，一经发放不可变）"
    )
    book_id: Mapped[str] = mapped_column(String(100), nullable=False, comment="梳理作用域＝一册书")
    job_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="取证时生效的版本；''＝人工直填或根视图。只作溯源"
    )
    node_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="point", comment="outline＝目录节点 / point＝细知识点"
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="自引用父节点；NULL 的 point 行＝未归档池"
    )
    depth: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="outline 层级 1=章 2=节；point 恒 NULL"
    )
    order_hint: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="目录前序序号／学习顺序提示")
    name: Mapped[str] = mapped_column(String(500), nullable=False, comment="展示名（章名／canonical 名）")
    norm_key: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="去重键（全角转半角＋空白折叠＋小写）；同父唯一由服务端校验，不建唯一键"
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", comment="''/core/explainer/example：节点在所属章内的地位，不是边"
    )
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", comment="point 行四分类 memory/procedure/concept/design；outline 行必须 ''"
    )
    summary: Mapped[str] = mapped_column(
        String(1000), nullable=False, default="", comment="一句话总结（上限与根侧同值，提升零截断）；长叙述不落本列"
    )
    links: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="关系 [{kind,to,note}]；kind 闭集 prerequisite/co_requisite/explains/exemplifies/extends"
    )
    anchors: Mapped[list | None] = mapped_column(
        JSON, nullable=True, comment="来源锚点 [{kind,ref_id,page,block_indexes,snippet}]；页序＝PDF 页序"
    )
    narrative_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, default="", comment="长文在数据根下的相对路径（单段名）；''＝无"
    )
    gen_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="candidate", comment="draft/candidate/rejected；只有机器写"
    )
    confirmed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="人工确认位（0/1）；只有人工可置，机器路径恒写 0"
    )
    gen_batch: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="梳理批次＝一次重编排作业（整批复核/清理/回滚的分组键）"
    )
    model_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="产出者标识（manual/模型名）；不得含密钥与个人信息"
    )
    rev: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="乐观并发序号：每次成功写入自增，提交可带 if_rev"
    )
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="扩展槽（三条红线见通则指针）")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


AF_TABLES = (
    AfBook.__table__,
    AfOperation.__table__,
    AfPage.__table__,
    AfKp.__table__,
)
