"""解析状态语义收口：进度与结局分列 + 失败页结构化（ADR 0007 / PLAN-006 W5）

Revision ID: 20260921_0001
Revises: 20260920_0002
Create Date: 2026-09-21

契约事实源：`docs/adr/0007-parse-status-semantics.md` 决定 1/2/3。旧 `af_books.parse_status`
一页两用——既是进度（`pending`/`parsing`）又是结局（`completed`/`failed`/`interrupted`），
DEFECT-007 的「结局被下一次进度覆盖」与 DEFECT-006 的「部分完成无列可表达」都源于此。
本迁移把两件事拆成两列：

- `af_books.last_parse_result`：最近一次任务结局（`''` = 从未有任务）。回填只做**平移**——
  旧值里的终态搬进新列，`parsing` 不是结局（它是进度），新列留 `''`。
- `af_books.parse_status`：收窄为纯进度，存量统一归 `pending`。旧「parsing 遗留」不在此处
  改写为 `interrupted`：`interrupted` 的唯一写入者是启动期归一（ADR 0007 决定 4），迁移
  若也写终态就有第二个写入者，下次冷启动的归一会给出同一结论，无需在此抢先。
- `af_parse_jobs.failed_pages`：失败页码数组，不截断（决定 2）。**历史 job 状态一律不改写**，
  旧 `completed` 行保持 `completed`——判据已随代码变更，回填旧行为新三态属于伪造历史。

实现约束：`af_parse_jobs` 的 JSON 新列不能用字面量 `DEFAULT`（MySQL 拒绝 JSON/BLOB 的
`DEFAULT '[]'`，且 SQLite 的 `ADD COLUMN NOT NULL` 又强制要求默认值）。故走「加可空列 →
回填 `[]` → 收紧 NOT NULL」三步，同一份 DDL 在 SQLite（批处理重建表）与 MySQL（直接
ALTER）上都成立；重建发生在收紧那一步，因此先回填再收，存量行不会撞 NOT NULL。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260921_0001"
down_revision: str | None = "20260920_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FAILED_PAGES_COMMENT = "失败页码数组，不截断（ADR 0007 决定 2）"


def upgrade() -> None:
    op.add_column(
        "af_books",
        sa.Column(
            "last_parse_result",
            sa.String(16),
            nullable=False,
            server_default=sa.text("''"),
            comment="最近一次任务结局；'' = 从未有任务（ADR 0007 决定 1）",
        ),
    )
    # 旧 parse_status 是「进度 + 结局」混合枚举，终态值只能平移进新列一次。
    op.execute(
        """
        UPDATE af_books
        SET last_parse_result = CASE parse_status
                WHEN 'completed' THEN 'completed'
                WHEN 'partial' THEN 'partial'
                WHEN 'failed' THEN 'failed'
                WHEN 'interrupted' THEN 'interrupted'
                ELSE ''
            END,
            parse_status = 'pending'
        """
    )
    # `failed_pages` 需要 NOT NULL，但 JSON 列在 MySQL 上不接受字面量 DEFAULT，SQLite 的
    # `ADD COLUMN NOT NULL` 又强制要求默认值：先加可空、回填、再收紧是唯一两方言都成立的顺序。
    with op.batch_alter_table("af_parse_jobs") as batch:
        batch.add_column(
            sa.Column("failed_pages", sa.JSON(), nullable=True, comment=_FAILED_PAGES_COMMENT)
        )
    op.execute("UPDATE af_parse_jobs SET failed_pages = '[]' WHERE failed_pages IS NULL")
    with op.batch_alter_table("af_parse_jobs") as batch:
        batch.alter_column(
            "failed_pages",
            existing_type=sa.JSON(),
            nullable=False,
            comment=_FAILED_PAGES_COMMENT,
        )


def downgrade() -> None:
    """降级：删两列并把结局搬回旧 `parse_status`（旧枚举无法表达 `partial`）。

    `partial` 在旧结构里没有对应值，按旧语义（`completed` = 任务正常结束）归为 `completed`，
    因为降级后读取面本就无法区分部分完成——这是降级本身的信息损失，不是回填猜测。
    """
    with op.batch_alter_table("af_parse_jobs") as batch:
        batch.drop_column("failed_pages")
    op.execute(
        """
        UPDATE af_books
        SET parse_status = CASE
                WHEN last_parse_result IN ('completed', 'partial') THEN 'completed'
                WHEN last_parse_result IN ('failed', 'interrupted') THEN last_parse_result
                ELSE parse_status
            END
        """
    )
    op.drop_column("af_books", "last_parse_result")
