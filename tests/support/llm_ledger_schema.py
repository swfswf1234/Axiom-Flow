"""共享表 `qed_llm_calls` 的测试侧建表助手（列面按根仓库 `backend/qed_engine/services/llm/call_log.py` 的 DDL 镜像）。

本仓库**不认领**这张表的结构：不建 ORM 模型、不进 `Base.metadata`、迁移里不出现它。
这里只有一份 SQLite 形状，供「只追加行」通路的单测与集成层自证使用；列名与可空性照抄根侧，
类型按 SQLite 能收的写法给（`MEDIUMTEXT`→`TEXT`、`BIGINT AUTO_INCREMENT`→`INTEGER AUTOINCREMENT`）。
"""

from __future__ import annotations

from sqlalchemy import Engine, text

LEDGER_TABLE = "qed_llm_calls"

LEDGER_DDL = """
CREATE TABLE qed_llm_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service VARCHAR(32) NOT NULL,
    mode VARCHAR(16) NOT NULL,
    provider VARCHAR(32) NOT NULL,
    model VARCHAR(64) NOT NULL,
    endpoint VARCHAR(16) NOT NULL,
    prompt_template VARCHAR(255),
    prompt TEXT,
    response TEXT,
    duration_ms INT,
    status VARCHAR(16) NOT NULL,
    error VARCHAR(500),
    created_at DATETIME NOT NULL,
    task VARCHAR(64),
    step VARCHAR(32),
    review_status VARCHAR(16) DEFAULT 'unreviewed',
    review_note VARCHAR(1000) DEFAULT '',
    meta JSON
)
"""


def create_ledger_table(engine: Engine) -> None:
    """在给定 SQLite 引擎上建出自名表的测试形状（af_* 四表之外的一站）。"""
    with engine.begin() as conn:
        conn.execute(text(LEDGER_DDL))


def read_ledger_rows(engine: Engine) -> list[dict]:
    """整表按自增序读回为字典：断言只读列值，不假设写入顺序之外的任何事。"""
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(text(f"SELECT * FROM {LEDGER_TABLE} ORDER BY id"))]
