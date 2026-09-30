"""af_* 私有表数据访问层（`qed` 库，Axiom-Flow 命名空间）。

- `engine`：连接与 DSN（`QED_DB_*`）；
- `models`：表族 ORM（结构事实源 `docs/architecture/database-design.md`）；
- `repositories`：幂等 upsert 与状态回写（设计 `docs/design/parsing-management.md`）。

迁移：`src/axiom_flow/migrations/`（Alembic，四表一次建全）。
"""

from axiom_flow.db.engine import create_engine_for, mysql_url, utc_now
from axiom_flow.db.models import AF_TABLES, LEGACY_JOB_ID, AfBlockEdit, AfBook, AfPage, AfParseJob, Base
from axiom_flow.db.repositories import (
    BookRepository,
    BookSyncRow,
    EditRepository,
    PageRepository,
    ParseJobRepository,
    ParseJobRow,
    SyncResult,
    edit_id_for,
)

__all__ = [
    "AF_TABLES",
    "AfBlockEdit",
    "AfBook",
    "AfPage",
    "AfParseJob",
    "Base",
    "BookRepository",
    "BookSyncRow",
    "EditRepository",
    "LEGACY_JOB_ID",
    "PageRepository",
    "ParseJobRepository",
    "ParseJobRow",
    "SyncResult",
    "create_engine_for",
    "edit_id_for",
    "mysql_url",
    "utc_now",
]
