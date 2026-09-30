"""`af_page_regions`：页级人工结构意图表（ADR 0010 / PLAN-018 W2）

Revision ID: 20260926_0002
Revises: 20260926_0001
Create Date: 2026-09-30

契约事实源：[ADR 0010]（页框图是页级解析输入、不是版本内覆盖层）与 PLAN-020 评审壳
「新表结构与表族关联」节。主键 `(book_id, page_no, region_id)` 且**刻意不含 `job_id`**：
区域承载的是人的意图，必须跨解析版本存活，否则「改框再解析块随框变化」与「删除块不复活」
不可达；版本级的人工判定仍归 `af_block_edits`（其唯一键含 `job_id`，语义不变）。

存量口径：新表起始为空，无回填（区域只能由人工声明产生，猜一个框等于伪造意图）。
表族无物理外键（按业务键关联），故本迁移只建一张表、不触碰其余 `af_*` 表；`downgrade` 直接 DROP，
区域行是人工声明的可重建输入，不做有损保留。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_0002"
down_revision: str | None = "20260926_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MYSQL = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


def upgrade() -> None:
    op.create_table(
        "af_page_regions",
        sa.Column("book_id", sa.String(100), nullable=False, comment="→ af_books.book_id"),
        sa.Column("page_no", sa.Integer(), nullable=False, comment="1 基页码"),
        sa.Column(
            "region_id",
            sa.String(64),
            nullable=False,
            comment="区域稳定身份（服务端按几何派生，跨版本不变）",
        ),
        sa.Column(
            "op",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'add'"),
            comment="add=人工新框 / delete=删块 / merge=并块",
        ),
        sa.Column("bbox", sa.JSON(), nullable=True, comment="op=add 必填：人工框几何（原页图像素）"),
        sa.Column(
            "anchor_bbox",
            sa.JSON(),
            nullable=True,
            comment="op=delete 必填：操作对象在受理时生效版本产物中的几何快照，重解析后按 IoU 重定位",
        ),
        sa.Column(
            "members",
            sa.JSON(),
            nullable=True,
            comment="op=merge 必填：参与合并的几何快照列表，数组序即阅读序",
        ),
        sa.Column(
            "kind",
            sa.String(24),
            nullable=False,
            server_default=sa.text("''"),
            comment="区域用途提示（属性路线，不扩 BlockType 枚举）",
        ),
        sa.Column("note", sa.String(1000), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("book_id", "page_no", "region_id"),
        comment="页级区域表：人工声明的解析输入与结构意图（Axiom-Flow 私有，跨版本存活）",
        **_MYSQL,
    )
    op.create_index("ix_af_page_regions_page", "af_page_regions", ["book_id", "page_no", "op"])


def downgrade() -> None:
    op.drop_index("ix_af_page_regions_page", table_name="af_page_regions")
    op.drop_table("af_page_regions")
