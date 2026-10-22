"""af_* 表族整族重建：单一 base 建齐四表（qed 库 af_* 命名空间）

Revision ID: 20261022_0001
Revises:
Create Date: 2026-10-22

档 B 整族重建（PLAN-024 / 根侧 REQ-136）：旧链六条随族作废并从链中移除，本条是**新的唯一
base**（`down_revision = None`），一次建齐目标形状四表——`af_books`（保留 + 增扩展槽 `meta`）、
`af_operations`（新建事件明细表，吸收 `af_parse_jobs` + `af_block_edits` + `af_page_regions`）、
`af_pages`（保留 + 增 `artifacts`/`confirm_state`/`section_kind`/`meta`）、`af_kp`（新建单表）。

结构事实源：docs/architecture/database-design.md「目标形状」节。存量不迁移、不回填（现役作业行
随 DROP 清除，`af_pages` 的唯一恢复路径是逐本重新 ingest + 重新解析）；真库执行属 D 类人工窗口
（逐表备份并留指纹 → 用户批准 → 执行），见 docs/guides/operations.md。
本仓库只建自己的 af_* 表，不触碰共享 qed_* 表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261022_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}
_EMPTY = sa.text("''")


def upgrade() -> None:
    op.create_table(
        "af_books",
        sa.Column("book_id", sa.String(100), nullable=False, comment="同源 qt_books.book_id"),
        sa.Column("domain_id", sa.String(32), nullable=False, server_default=_EMPTY),
        sa.Column("course_id", sa.String(64), nullable=False, server_default=_EMPTY),
        sa.Column("course_name", sa.String(200), nullable=False, server_default=_EMPTY),
        sa.Column("knowledge_id", sa.String(100), nullable=False, server_default=_EMPTY),
        sa.Column("title", sa.String(500), nullable=False, server_default=_EMPTY),
        sa.Column("part", sa.String(32), nullable=False, server_default=_EMPTY),
        sa.Column("display_title", sa.String(500), nullable=False, server_default=_EMPTY),
        sa.Column("authors", sa.JSON(), nullable=False, comment="list[{name, role}]"),
        sa.Column("file_path", sa.String(512), nullable=False, server_default=_EMPTY, comment="数据根相对路径"),
        sa.Column("page_count", sa.Integer(), nullable=True, comment="ingest 时计算"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=_EMPTY, comment="ingest 时计算"),
        sa.Column("ingest_status", sa.String(16), nullable=False, server_default=sa.text("'none'")),
        sa.Column("ingested_at", sa.DateTime(), nullable=True, comment="NULL = 从未 ingest"),
        sa.Column("parse_status", sa.String(24), nullable=False, server_default=sa.text("'pending'")),
        sa.Column(
            "last_parse_result", sa.String(16), nullable=False, server_default=_EMPTY, comment="'' = 从未有任务"
        ),
        sa.Column("pages_done", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "active_job_id",
            sa.String(64),
            nullable=True,
            comment="生效解析版本 = af_operations.op_id；NULL = 根视图 legacy",
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "meta",
            sa.JSON(),
            nullable=True,
            comment="扩展槽；同步 upsert 不得触碰（双写者列域不重叠）",
        ),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("book_id"),
        comment="书目表：QED-Tracker 已验证书目快照 + ingest/解析进度 + 生效版本指针（Axiom-Flow 私有）",
        **_MYSQL,
    )
    op.create_index("ix_af_books_course", "af_books", ["course_id"])
    op.create_index("ix_af_books_status", "af_books", ["parse_status"])

    op.create_table(
        "af_operations",
        sa.Column(
            "op_id",
            sa.String(64),
            nullable=False,
            comment="事件身份；parse 行的 op_id 就是版本号（产物目录与生效指针按值指它）",
        ),
        sa.Column("book_id", sa.String(100), nullable=False),
        sa.Column(
            "job_id",
            sa.String(64),
            nullable=False,
            server_default=_EMPTY,
            comment="parse 行等于 op_id；edit/confirm 行＝绑定版本；kp 行＝取证版本；structure/ingest/sync/purge 行＝''",
        ),
        sa.Column(
            "op_kind",
            sa.String(24),
            nullable=False,
            comment="闭集 parse/edit/structure/confirm/ingest/sync/purge/index/kp（index 与 kp 本轮无写者）",
        ),
        sa.Column(
            "lifecycle",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'event'"),
            comment="event=不可变流水（禁作判据）/ intent=人工当前态（唯一合法判据位）",
        ),
        sa.Column(
            "actor", sa.String(16), nullable=False, server_default=sa.text("'human'"), comment="human/system/model"
        ),
        sa.Column("page_no", sa.Integer(), nullable=True),
        sa.Column("block_index", sa.Integer(), nullable=True, comment="edit 行的块坐标（绑版本）"),
        sa.Column(
            "target_id",
            sa.String(64),
            nullable=False,
            server_default=_EMPTY,
            comment="structure 行＝region_id、kp 相关行＝node_id，其余 ''",
        ),
        sa.Column(
            "intent_key",
            sa.String(64),
            nullable=True,
            comment="意图幂等键；event 行恒 NULL（多行 NULL ⇒ 只对意图行生效的唯一索引）",
        ),
        sa.Column(
            "engine", sa.String(24), nullable=False, server_default=_EMPTY, comment="parse 行的显式引擎名，无死兜底"
        ),
        sa.Column("pages", sa.JSON(), nullable=True, comment="parse 行的请求页集；NULL = 全本"),
        sa.Column("page_count", sa.Integer(), nullable=True, comment="建任务时的全书页数快照"),
        sa.Column("source_sha256", sa.String(64), nullable=False, server_default=_EMPTY, comment="本版实际源文件指纹"),
        sa.Column(
            "parser_signature",
            sa.String(64),
            nullable=False,
            server_default=_EMPTY,
            comment="解析代际签名；'' = 未知代际（只判需重跑，不判过时）",
        ),
        sa.Column(
            "embedding_signature",
            sa.String(64),
            nullable=False,
            server_default=_EMPTY,
            comment="检索索引代际签名；本轮建列不建写者（守护断言非空计数恒 0）",
        ),
        sa.Column(
            "render_profile",
            sa.String(32),
            nullable=False,
            server_default=_EMPTY,
            comment="版本级格式档案（闭集常量）；同代内不允许逐页异构",
        ),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'queued'"),
            comment="parse 行六态原值；非 parse 行只用 ok/failed",
        ),
        sa.Column("progress", sa.JSON(), nullable=False, comment='{"parsed":N,"total":M}（被轮询的热列）'),
        sa.Column("failed_pages", sa.JSON(), nullable=False, comment="结构化失败页码数组，不截断"),
        sa.Column("error", sa.String(500), nullable=False, server_default=_EMPTY, comment="人读摘要"),
        sa.Column("payload", sa.JSON(), nullable=False, comment="操作载荷（形状闭集，禁自由键）"),
        sa.Column("result", sa.JSON(), nullable=True, comment="操作结果读数；NULL＝无额外读数"),
        sa.Column("note", sa.String(1000), nullable=False, server_default=_EMPTY),
        sa.Column("meta", sa.JSON(), nullable=True, comment="扩展槽"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="事件发生时刻"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="event 行恒等于 created_at"),
        sa.PrimaryKeyConstraint("op_id"),
        sa.UniqueConstraint("intent_key", name="uq_af_operations_intent"),
        comment="事件明细表：一次操作一行（解析版本 / 块编辑 / 结构声明 / 确认 / ingest / 同步 / 清除 / 梳理流水）",
        **_MYSQL,
    )
    op.create_index("ix_af_operations_book", "af_operations", ["book_id", "op_kind", "created_at"])
    op.create_index("ix_af_operations_version", "af_operations", ["job_id", "page_no"])
    op.create_index("ix_af_operations_sig", "af_operations", ["book_id", "parser_signature", "status"])

    op.create_table(
        "af_pages",
        sa.Column("book_id", sa.String(100), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False, comment="'legacy' = 根视图存量"),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("source", sa.String(24), nullable=False, server_default=_EMPTY, comment="实际引擎标识"),
        sa.Column("quality", sa.JSON(), nullable=True, comment="质量信号"),
        sa.Column("error", sa.String(500), nullable=False, server_default=_EMPTY),
        sa.Column(
            "artifacts",
            sa.JSON(),
            nullable=True,
            comment="在位产物索引 {png/md/blocks:{present,bytes,sha256,block_count}}；不作就绪判据",
        ),
        sa.Column(
            "confirm_state",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'unconfirmed'"),
            comment="版本内人工确认态；不跨版本继承",
        ),
        sa.Column(
            "section_kind",
            sa.String(16),
            nullable=False,
            server_default=_EMPTY,
            comment="页域角色闭集 cover/preface/ack/toc/chapter/appendix/back/other；''＝未判",
        ),
        sa.Column("meta", sa.JSON(), nullable=True, comment="扩展槽"),
        sa.Column("parsed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("book_id", "job_id", "page_no"),
        comment="结果索引表：一页在一个解析版本内的状态/质量/产物索引/确认态/页域（唯一结果索引）",
        **_MYSQL,
    )

    op.create_table(
        "af_kp",
        sa.Column(
            "node_id",
            sa.String(64),
            nullable=False,
            comment="稳定身份 afkp-<hex12>（服务端随机发放，一经发放不可变）",
        ),
        sa.Column("book_id", sa.String(100), nullable=False, comment="梳理作用域＝一册书"),
        sa.Column(
            "job_id", sa.String(64), nullable=False, server_default=_EMPTY, comment="取证版本；只作溯源不进作用域键"
        ),
        sa.Column(
            "node_kind", sa.String(16), nullable=False, server_default=sa.text("'point'"), comment="outline/point"
        ),
        sa.Column("parent_id", sa.String(64), nullable=True, comment="自引用；NULL 的 point 行＝未归档池"),
        sa.Column("depth", sa.Integer(), nullable=True, comment="outline 1=章 2=节；point 恒 NULL"),
        sa.Column("order_hint", sa.Integer(), nullable=True, comment="目录前序序号／学习顺序提示"),
        sa.Column("name", sa.String(500), nullable=False, comment="展示名"),
        sa.Column(
            "norm_key", sa.String(200), nullable=False, comment="去重键；同父唯一由服务端校验，不建唯一键"
        ),
        sa.Column(
            "role", sa.String(16), nullable=False, server_default=_EMPTY, comment="''/core/explainer/example"
        ),
        sa.Column(
            "type",
            sa.String(16),
            nullable=False,
            server_default=_EMPTY,
            comment="point 行 memory/procedure/concept/design；outline 行必须 ''",
        ),
        sa.Column(
            "summary", sa.String(1000), nullable=False, server_default=_EMPTY, comment="一句话总结；长叙述不落本列"
        ),
        sa.Column("links", sa.JSON(), nullable=True, comment="[{kind,to,note}]；kind 闭集"),
        sa.Column("anchors", sa.JSON(), nullable=True, comment="[{kind,ref_id,page,block_indexes,snippet}]"),
        sa.Column(
            "narrative_ref", sa.String(200), nullable=False, server_default=_EMPTY, comment="长文数据根相对路径（单段名）"
        ),
        sa.Column(
            "gen_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'candidate'"),
            comment="draft/candidate/rejected；只有机器写",
        ),
        sa.Column(
            "confirmed", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="人工确认位；机器恒写 0"
        ),
        sa.Column(
            "gen_batch", sa.String(64), nullable=False, server_default=_EMPTY, comment="梳理批次（保留最新 2 批的分组键）"
        ),
        sa.Column("model_ref", sa.String(128), nullable=False, server_default=_EMPTY, comment="manual/模型名"),
        sa.Column("rev", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="乐观并发序号"),
        sa.Column("meta", sa.JSON(), nullable=True, comment="扩展槽"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("node_id"),
        comment="知识点表：章／节／知识点的当前结论（层级靠 parent_id、关系靠 links、并发靠 rev）",
        **_MYSQL,
    )
    op.create_index("ix_af_kp_book", "af_kp", ["book_id", "node_kind", "gen_status"])
    op.create_index("ix_af_kp_parent", "af_kp", ["parent_id"])
    op.create_index("ix_af_kp_batch", "af_kp", ["gen_batch"])
    op.create_index("ix_af_kp_norm", "af_kp", ["book_id", "node_kind", "norm_key"])


def downgrade() -> None:
    """降级＝整族清空（档 B 无「回到旧五表」的形状可降，旧链已随族作废）。"""
    for name in ("ix_af_kp_norm", "ix_af_kp_batch", "ix_af_kp_parent", "ix_af_kp_book"):
        op.drop_index(name, table_name="af_kp")
    op.drop_table("af_kp")
    op.drop_table("af_pages")
    for name in ("ix_af_operations_sig", "ix_af_operations_version", "ix_af_operations_book"):
        op.drop_index(name, table_name="af_operations")
    op.drop_table("af_operations")
    op.drop_index("ix_af_books_status", table_name="af_books")
    op.drop_index("ix_af_books_course", table_name="af_books")
    op.drop_table("af_books")
