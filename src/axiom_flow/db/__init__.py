"""af_* 私有表数据访问层（`qed` 库，Axiom-Flow 命名空间）。

- `engine`：连接与 DSN（`QED_DB_*`）；
- `models`：表族 ORM（结构事实源 `docs/architecture/database-design.md`）；
- `repositories`：事件流水、人工意图与结果索引的读写（设计 `docs/design/parsing-management.md`）。

迁移：`src/axiom_flow/migrations/`（Alembic，档 B 后单条 base 迁移一次建齐四表）。
导出集按「模型四张 + 仓储四个 + 输入/投影行」对称给出，不再单边漏项。
"""

from axiom_flow.db.engine import create_engine_for, mysql_url, utc_now
from axiom_flow.db.models import (
    ACTORS,
    AF_TABLES,
    GEN_STATUSES,
    LEGACY_JOB_ID,
    LIFECYCLE_EVENT,
    LIFECYCLE_INTENT,
    OP_KINDS,
    AfBook,
    AfKp,
    AfOperation,
    AfPage,
    Base,
)
from axiom_flow.db.repositories import (
    ACTIVE_JOB_STATUSES,
    BookRepository,
    BookSyncRow,
    EditRow,
    KpNodeRow,
    KpRepository,
    KpWriteResult,
    OperationRepository,
    PageRepository,
    PageStateRow,
    ParseJobRow,
    RegionIntent,
    RegionRow,
    SyncResult,
    intent_key_for,
    job_timing,
    new_node_id,
    new_op_id,
    public_progress,
    region_id_for,
    region_identity,
)

__all__ = [
    "ACTORS",
    "ACTIVE_JOB_STATUSES",
    "AF_TABLES",
    "AfBook",
    "AfKp",
    "AfOperation",
    "AfPage",
    "Base",
    "BookRepository",
    "BookSyncRow",
    "EditRow",
    "GEN_STATUSES",
    "KpNodeRow",
    "KpRepository",
    "KpWriteResult",
    "LEGACY_JOB_ID",
    "LIFECYCLE_EVENT",
    "LIFECYCLE_INTENT",
    "OP_KINDS",
    "OperationRepository",
    "PageRepository",
    "PageStateRow",
    "ParseJobRow",
    "RegionIntent",
    "RegionRow",
    "SyncResult",
    "create_engine_for",
    "intent_key_for",
    "job_timing",
    "mysql_url",
    "new_node_id",
    "new_op_id",
    "public_progress",
    "region_id_for",
    "region_identity",
    "utc_now",
]
