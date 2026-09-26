"""af_books 元数据事实源补全：新增 `ingested_at`（REQ-097 / PLAN-017 W1）

Revision ID: 20260926_0001
Revises: 20260921_0001
Create Date: 2026-09-26

契约事实源：根仓库 REQ-097 裁决「根视图只保留页图，元数据单一事实源是 af_books」。
根视图 `book.json` 随本批退役后，其 `ingested_at`（ingest 完成时刻）在 af_books 没有
对应列——`synced_at` 是同步时刻、`updated_at` 被任何回写刷新，都不能冒充 ingest 时刻。
`sha256` 列已存在且由 `update_ingest` 维护，本轮**不**新增 `ingest_sha256`（PLAN-017 壳
字段核查表偏差修正，见壳「登记与结论」）。

回填口径：不回填。存量书的 `ingested_at` 留 NULL 是**如实**——时刻只在发生时有意义，
从 `updated_at` 猜一个值会造出第二份假事实源。NULL 的判读口径 = 「本轮规则之前 ingest」，
读取面不依赖该列做门槛（门槛是 `ingest_status`）。可空加列在 SQLite 与 MySQL 上都是
纯 `ADD COLUMN`，无默认值、无回填、批处理重建表都不需要。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260926_0001"
down_revision: str | None = "20260921_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "af_books",
        sa.Column(
            "ingested_at",
            sa.DateTime(),
            nullable=True,
            comment="ingest 完成时刻（UTC naive）；NULL = 从未 ingest（REQ-097）",
        ),
    )


def downgrade() -> None:
    op.drop_column("af_books", "ingested_at")
