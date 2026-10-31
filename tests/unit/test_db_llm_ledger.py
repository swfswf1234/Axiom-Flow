"""共享表 `qed_llm_calls` 追加写通路的单测（`KA-23`，来源 PLAN-025 W7）。

守护面：本组件对这张表**只追加行、不碰结构**——写出的列只能落在根侧 DDL 的字面用途里，
三枚 `NOT NULL` 身份列（`mode`/`provider`/`model`）缺值就当场拒发，库不可达只降级不抛。

设计关联（DesignRef）：docs/design/knowledge-analysis.md（`KA-23`）、
docs/architecture/database-design.md（共享表节）
关联测试：本文件；真表与「结构未被 ALTER」的取证在 `tests/integration/test_llm_call_ledger.py`

隔离：SQLite 内存库 + 测试自建的同名表（列面在 `tests/support/llm_ledger_schema.py` 一处镜像，
供单测与集成层共用），不连本机 MySQL、不读写真实数据根。
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from axiom_flow.db.engine import utc_now
from axiom_flow.db.llm_ledger import (
    KpCallRow,
    LlmCallIdentity,
    LlmCallLedger,
)
from axiom_flow.db.models import Base
from tests.support.llm_ledger_schema import create_ledger_table, read_ledger_rows


@pytest.fixture
def ledger_engine():
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool, future=True)
    create_ledger_table(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def ledger(ledger_engine) -> LlmCallLedger:
    return LlmCallLedger(sessionmaker(bind=ledger_engine, class_=Session, future=True))


def _rows(engine) -> list[dict]:
    return read_ledger_rows(engine)


def _row(**overrides) -> KpCallRow:
    fields = {
        "step": "points",
        "status": "success",
        "duration_ms": 1234,
        # `mode='local'` 时 `provider` 是 runtime 名而不是厂商名（根侧 DDL 注释），取值由接线的人负责
        "identity": LlmCallIdentity(mode="local", provider="vllm", model="qwen-plus"),
        "meta": {"book_id": "mathanalysis-b05", "gen_batch": "afkp-aaaaaaaaaaaa"},
        "error": "",
    }
    fields.update(overrides)
    return KpCallRow(**fields)


class TestLedgerAppend:
    """追加面的形状：一调用一行，常量归常量，不知道的东西留 NULL。"""

    def test_one_call_appends_exactly_one_row(self, ledger, ledger_engine):
        row_id = ledger.record(_row())

        rows = _rows(ledger_engine)
        assert len(rows) == 1
        assert row_id == rows[0]["id"]
        assert isinstance(row_id, int)

    def test_the_row_carries_the_constants_of_this_lane(self, ledger, ledger_engine):
        """`service`/`endpoint`/`task` 是本通路的常量，不接受调用方给值：给了也没地方给。"""
        ledger.record(_row())

        row = _rows(ledger_engine)[0]
        assert (row["service"], row["endpoint"], row["task"]) == ("axiom_flow", "text", "kp-curation")

    def test_the_identity_three_columns_come_from_the_injection(self, ledger, ledger_engine):
        ledger.record(_row())

        row = _rows(ledger_engine)[0]
        assert (row["mode"], row["provider"], row["model"]) == ("local", "vllm", "qwen-plus")

    def test_prompt_and_response_are_left_null(self, ledger, ledger_engine):
        """真实串只有适配器知道；拿摘要冒充「完整 prompt／模型原始响应文本」就是往共享表里写假事实。"""
        ledger.record(_row())

        row = _rows(ledger_engine)[0]
        assert row["prompt"] is None
        assert row["response"] is None
        assert row["prompt_template"] is None

    def test_meta_carries_the_batch_attribution(self, ledger, ledger_engine):
        """`meta` 是根侧定义的扩展槽（未定档字段先入本列），批次归因放这里而不是放身份列。"""
        ledger.record(_row())

        row = _rows(ledger_engine)[0]
        assert json.loads(row["meta"]) == {
            "book_id": "mathanalysis-b05",
            "gen_batch": "afkp-aaaaaaaaaaaa",
        }

    def test_the_review_columns_are_never_written(self, ledger, ledger_engine):
        """审核动作归 8900 控制台：本通路一列都不碰，写出来的只能是库侧默认值。"""
        ledger.record(_row())

        row = _rows(ledger_engine)[0]
        assert (row["review_status"], row["review_note"]) == ("unreviewed", "")

    def test_the_step_column_names_the_stage(self, ledger, ledger_engine):
        ledger.record(_row(step="toc_entries"))
        ledger.record(_row(step="points"))

        assert [row["step"] for row in _rows(ledger_engine)] == ["toc_entries", "points"]

    def test_the_writer_stamps_created_at_itself(self, ledger, ledger_engine):
        """时间戳不接受调用方给值：账面行不能被人往后改一个 `created_at` 来挪成本归属。"""
        assert "created_at" not in {f.name for f in dataclasses.fields(KpCallRow)}
        before = utc_now().replace(microsecond=0)
        ledger.record(_row())
        after = utc_now().replace(microsecond=0)

        stamped = datetime.strptime(_rows(ledger_engine)[0]["created_at"], "%Y-%m-%d %H:%M:%S")
        assert before <= stamped <= after

    def test_a_failed_call_is_recorded_as_error_with_a_reason(self, ledger, ledger_engine):
        ledger.record(_row(status="error", error="上游 502"))

        row = _rows(ledger_engine)[0]
        assert (row["status"], row["error"]) == ("error", "上游 502")

    def test_error_text_is_truncated_to_the_column_width(self, ledger, ledger_engine):
        """`error` 是 VARCHAR(500)：不截断的话整条 INSERT 会被 MySQL 拒掉，账就整行丢了。"""
        ledger.record(_row(status="error", error="故" * 900))

        assert len(_rows(ledger_engine)[0]["error"]) == 500

class TestLedgerPreChecks:
    """发出前的形状闸：错在参数上就拒，别让一条脏行进共享表。"""

    @pytest.mark.parametrize("step", ["", "outline", "outline2", "point", "KP"])
    def test_an_unknown_step_is_rejected(self, ledger, ledger_engine, step):
        with pytest.raises(ValueError, match="step"):
            ledger.record(_row(step=step))
        assert _rows(ledger_engine) == []

    @pytest.mark.parametrize("status", ["", "ok", "failed", "SUCCESS"])
    def test_an_unknown_status_is_rejected(self, ledger, ledger_engine, status):
        with pytest.raises(ValueError, match="status"):
            ledger.record(_row(status=status))
        assert _rows(ledger_engine) == []

    def test_a_mode_outside_the_root_contract_is_rejected(self, ledger):
        """`mode` 只有 api/local 两值（根侧 DDL 注释）；写出第三值就是在污染 8900 的检索面。"""
        with pytest.raises(ValueError, match="mode"):
            ledger.record(_row(identity=LlmCallIdentity(mode="direct", provider="vllm", model="qwen-plus")))

    @pytest.mark.parametrize("blank", ["mode", "provider", "model"])
    def test_a_blank_identity_column_is_rejected(self, ledger, ledger_engine, blank):
        """三列都是 NOT NULL 且无默认：空串发出去，MySQL 会以整条 INSERT 失败收场，账目反而更少。"""
        values = {"mode": "local", "provider": "qwen", "model": "qwen-plus"}
        values[blank] = "   "
        with pytest.raises(ValueError, match=blank):
            ledger.record(_row(identity=LlmCallIdentity(**values)))
        assert _rows(ledger_engine) == []

    def test_a_negative_duration_is_rejected(self, ledger):
        with pytest.raises(ValueError, match="duration_ms"):
            ledger.record(_row(duration_ms=-1))


class TestLedgerDegradation:
    """库不可达只降级：调用账是审计线索，不能反过来把模型调用打挂。"""

    def test_a_missing_table_degrades_to_a_warning_and_returns_none(self, caplog, ledger_engine):
        with ledger_engine.begin() as conn:
            conn.execute(text("DROP TABLE qed_llm_calls"))

        with caplog.at_level("WARNING"):
            assert ledger_engine is not None and _broken_ledger(ledger_engine).record(_row()) is None
        assert "qed_llm_calls" in caplog.text

    def test_a_failing_session_factory_never_escapes(self, caplog):
        def _boom() -> Session:
            raise OSError("库连不上")

        with caplog.at_level("WARNING"):
            assert LlmCallLedger(_boom).record(_row()) is None
        assert "降级" in caplog.text


def _broken_ledger(engine) -> LlmCallLedger:
    return LlmCallLedger(sessionmaker(bind=engine, class_=Session, future=True))


class TestLedgerTouchesNoStructure:
    """「只写行、不改结构」的可执行形式：语句只有 INSERT，表也不进本组件的元数据。"""

    def test_the_writer_holds_only_an_insert_statement(self):
        """按词元比对而非子串：`created_at` 这一列名里天然含 `CREATE`，子串判据会误伤。"""
        statement = LlmCallLedger.insert_statement()
        tokens = [token.upper() for token in re.split(r"[^A-Za-z_]+", statement) if token]
        assert tokens[:3] == ["INSERT", "INTO", "QED_LLM_CALLS"]
        assert not {"CREATE", "ALTER", "DROP", "UPDATE", "DELETE", "TRUNCATE", "GRANT", "REPLACE"} & set(tokens)
        assert ";" not in statement

    def test_the_ledger_table_is_not_declared_in_our_metadata(self):
        """不建 ORM 模型是刻意的：`af_` 命名空间守护（`test_db_table_family_governance.py`）
        扫的就是 `Base.metadata`，把共享表挂上去等于把它当自有表。"""
        assert "qed_llm_calls" not in Base.metadata.tables
        assert {name for name in Base.metadata.tables if name.startswith("qed_")} == set()
