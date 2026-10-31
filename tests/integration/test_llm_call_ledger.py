"""真 MySQL 层的调用账追加通路（`KA-23`，来源 PLAN-025 W7；隔离边界见 ADR 0005）。

守护面：写入器在**根侧自有的那张 `qed_llm_calls`** 上真落得下一行，且落完之后表结构一字未变。
「只写行、不改结构」在 SQLite 镜像上只是形状，只有在这一层才是事实：单测的表是测试自己建的，
真表的列宽、字符集与默认值由根仓库 8900 决定，本仓库既无权改也无从模拟。

隔离：只在 `QED_TEST_MYSQL=1` 时连本机 MySQL 的 `qed_test` 库（缺开关整体 skip，默认门禁零外部依赖）。
本文件**不建表也不删表**——表不在 `OWNED_TABLES` 白名单里，夹具的清理动作碰不到它，本层也绝不 DROP；
用例只按自己拿到的自增 `id` 删回自己写的那一行，别人的行一条不动。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.llm_ledger import KpCallRow, LlmCallIdentity, LlmCallLedger

#: 根侧 DDL 的 18 列：本层用列名在场来钉「我们读的还是那张表」，不钉类型（类型归 8900）。
_ROOT_COLUMNS = (
    "id",
    "service",
    "mode",
    "provider",
    "model",
    "endpoint",
    "prompt_template",
    "prompt",
    "response",
    "duration_ms",
    "status",
    "error",
    "created_at",
    "task",
    "step",
    "review_status",
    "review_note",
    "meta",
)

#: `mode='local'` 时 `provider` 是 runtime 名（根侧 DDL 注释），本组件出站恒直连。
_IDENTITY = LlmCallIdentity(mode="local", provider="vllm", model="qwen-plus")
_GEN_BATCH = "afkp-integration01"


def _ddl(store: Engine) -> str:
    with store.connect() as conn:
        return str(conn.execute(text("SHOW CREATE TABLE qed_llm_calls")).fetchone()[1])


def _count(store: Engine) -> int:
    with store.connect() as conn:
        return int(conn.execute(text("SELECT COUNT(*) FROM qed_llm_calls")).scalar())


def _row(**overrides) -> KpCallRow:
    fields = {
        "step": "points",
        "status": "success",
        "duration_ms": 1234,
        "identity": _IDENTITY,
        "meta": {"book_id": "cw-live", "gen_batch": _GEN_BATCH},
        "error": "",
    }
    fields.update(overrides)
    return KpCallRow(**fields)


@pytest.fixture()
def ledger(mysql_store: Engine) -> LlmCallLedger:
    return LlmCallLedger(sessionmaker(bind=mysql_store, class_=Session, future=True))


@pytest.fixture()
def appended(mysql_store: Engine, ledger: LlmCallLedger):
    """写一行、交还自增 id 与写前的行数与 DDL，用例结束按 id 删回自己那一行。

    `yield (row_id, count_before, ddl_before)` 而不是让每个用例各自 try/finally：删自己写的行是这一层
    唯一的清理动作，漏掉就把 `qed_test` 当垃圾场——那张表根侧控制台也在读。
    """
    count_before = _count(mysql_store)
    ddl_before = _ddl(mysql_store)
    row_id = ledger.record(_row())
    assert row_id is not None, "真表上落不下行就说明列面或连接变了：降级为 None 只在库不可达时发生"
    try:
        yield row_id, count_before, ddl_before
    finally:
        with mysql_store.connect() as conn:
            conn.execute(text("DELETE FROM qed_llm_calls WHERE id = :id"), {"id": row_id})
            conn.commit()


class TestCallLedgerOnRealTable:
    """追加面在真表上的三条事实：落得下、只有我们的列变了、结构没变。"""

    def test_the_table_is_still_the_root_owned_shape(self, mysql_store: Engine) -> None:
        """前置事实：本层读的就是根侧那张表——18 列都在场，缺列即红（根侧改列时本通路要跟着复核）。"""
        ddl = _ddl(mysql_store)
        assert {column for column in _ROOT_COLUMNS if f"`{column}`" not in ddl} == set()

    def test_one_record_appends_exactly_one_row(self, mysql_store: Engine, appended) -> None:
        row_id, count_before, _ = appended

        assert _count(mysql_store) == count_before + 1
        with mysql_store.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT service, mode, provider, model, endpoint, task, step, status, duration_ms, "
                    "prompt, response, prompt_template, error, review_status, review_note, created_at, meta "
                    "FROM qed_llm_calls WHERE id = :id"
                ),
                {"id": row_id},
            ).fetchone()
        assert row is not None
        assert (row.service, row.endpoint, row.task, row.step, row.status) == (
            "axiom_flow",
            "text",
            "kp-curation",
            "points",
            "success",
        )
        assert (row.mode, row.provider, row.model) == ("local", "vllm", "qwen-plus")
        assert row.duration_ms == 1234
        assert row.created_at is not None
        # 没有事实依据的列留 NULL，审核两列保持库侧默认：本通路一条都不碰
        assert (row.prompt, row.response, row.prompt_template, row.error) == (None, None, None, None)
        assert (row.review_status, row.review_note) == ("unreviewed", "")
        # 驱动可能把 JSON 列交回字符串或已解析对象，两种都在根侧的读法里出现，按形状各读一次
        meta = json.loads(row.meta) if isinstance(row.meta, str) else row.meta
        assert meta["gen_batch"] == _GEN_BATCH

    def test_the_structure_is_untouched_by_the_write(self, mysql_store: Engine, appended) -> None:
        _, _, ddl_before = appended

        assert _ddl(mysql_store) == ddl_before, "写入通路碰了结构：本案的硬边界是零 ALTER、零新列"

    def test_an_overlong_error_is_truncated_before_the_insert(self, mysql_store: Engine, ledger) -> None:
        """`error` 是 VARCHAR(500)：截断是写入器做的，真表上验一次「整条 INSERT 没有被拒」。"""
        before = _count(mysql_store)
        row_id = ledger.record(_row(status="error", error="故" * 900, duration_ms=17))
        assert row_id is not None
        try:
            with mysql_store.connect() as conn:
                row = conn.execute(
                    text("SELECT status, error FROM qed_llm_calls WHERE id = :id"), {"id": row_id}
                ).fetchone()
            assert row is not None and row.status == "error"
            assert len(row.error) == 500
            assert _count(mysql_store) == before + 1
        finally:
            with mysql_store.connect() as conn:
                conn.execute(text("DELETE FROM qed_llm_calls WHERE id = :id"), {"id": row_id})
                conn.commit()
