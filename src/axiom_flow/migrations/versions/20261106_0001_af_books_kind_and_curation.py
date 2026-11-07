"""af_books 加两列：doc_kind 与 last_curation_result（ADR 0019）

Revision ID: 20261106_0001
Revises: 20261022_0001
Create Date: 2026-11-06

加列不加表（ADR 0016 决定 11）：`doc_kind`＝书种类两值闭集，由同步行 `roles` 派生写；
`last_curation_result`＝最近一次梳理批次结局，只由批次收尾与启动期归一写。两列都不落 `meta`
（同步 upsert 禁写该列，ADR 0016 决定 10 ①）。

不回填：存量行两列取默认 `''`，读法即「未声明／从未梳理」。`doc_kind` 不可由本仓数据重算，
唯一补法是同步腿再灌一次（幂等）。降级删两列即回到旧形状。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_0001"
down_revision: str | None = "20261022_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY = sa.text("''")


def upgrade() -> None:
    op.add_column(
        "af_books",
        sa.Column(
            "doc_kind",
            sa.String(16),
            nullable=False,
            server_default=_EMPTY,
            comment="书种类两值闭集（同步行 roles 派生）；'' = 未声明",
        ),
    )
    op.add_column(
        "af_books",
        sa.Column(
            "last_curation_result",
            sa.String(16),
            nullable=False,
            server_default=_EMPTY,
            comment="最近一次梳理批次结局；'' = 从未梳理",
        ),
    )


def downgrade() -> None:
    op.drop_column("af_books", "last_curation_result")
    op.drop_column("af_books", "doc_kind")
