"""`af_parse_jobs.parser_signature`：解析代际签名补列（PLAN-019 W2 / C1）

Revision ID: 20260928_0001
Revises: 20260926_0002
Create Date: 2026-10-01

契约事实源：PLAN-019 壳「契约草案 C1」（签名输入集已冻结）与 [ADR 0015]（重跑与就绪判据的
DB 单源）。签名 = SHA-256(键按字典序的 JSON 规范化串)，输入只含影响块产物的参数（引擎名、模型
服务 `version`/`protocol_version`、块策略版本）；`(source_sha256, parser_signature)` 双全等才叫
「不用重跑」。

存量口径：回填哨兵 `''` ＝ **未知代际**，不回算伪造签名——这些版本的参数组合已不可考，算出来的是
第二份假事实源（判例同 `20260926_0001` 的 `ingested_at` 不回填）。与「不回填 NULL」的差别只在形状：
本列 NOT NULL，判定面只需比等与不等，不必分 `NULL` 与 `''` 两种空值语义。后果如实登记：全部存量
生效版本都带 `''`，重跑清单首日会把它们全判 `need_reparse`（`reason=signature_unknown`），据此
「未知代际」与「参数确实变了」在清单里分列，且 `''` 不作为过时判据（C1 边界三条）。

不可空加列在 SQLite 与 MySQL 上都是纯 `ADD COLUMN`（带 DDL 默认 `''`），无批处理重建表；`downgrade`
只删列，任务行与索引不动。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260928_0001"
down_revision: str | None = "20260926_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "af_parse_jobs",
        sa.Column(
            "parser_signature",
            sa.String(64),
            nullable=False,
            server_default=sa.text("''"),
            comment="本版本的解析代际签名；'' = 未记录（未知代际，只判需重跑不判过时）",
        ),
    )


def downgrade() -> None:
    op.drop_column("af_parse_jobs", "parser_signature")
