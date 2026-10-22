"""MySQL 层并发判据反向用例（PLAN-024 CW-3／CW-4／CW-6，[ADR 0005](../../docs/adr/0005-mysql-integration-test-isolation.md)）。

守护面：三条并发判据的**锁与隔离**只在真 MySQL 上可观测。SQLite 是单写者库，同一份代码在那里
既不会在唯一键上排队、也不会渲染 `FOR UPDATE`（`repositories._lock_book` 的 docstring 明写了
这一点），所以单测只能验幂等与纯函数，验不到「并发同坐标到底各造几行」。本文件把三条判据的
反面钉成会红的用例：撤掉唯一键、撤掉行锁、把条件 UPDATE 改成读改写，本层立即转红。

隔离边界（ADR 0005）：只在 `QED_TEST_MYSQL=1` 时连本机 MySQL，只使用 `qed_test` 库里本仓库自有的
`af_*` 表族（+ `alembic_version`）；缺 opt-in 时整体 skip，默认门禁零外部依赖。
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.db.models import AfKp
from axiom_flow.db.repositories import (
    BookRepository,
    BookSyncRow,
    KpNodeRow,
    KpRepository,
    OperationRepository,
    ParseJobRow,
    RegionIntent,
    region_id_for,
)

BOOK_ID = "cw-live"
PAGE_NO = 1
NODE_ID = "kp-cw-6"
LOCK_WAIT_SECONDS = 2


@pytest.fixture()
def makers(mysql_store: Engine) -> tuple[sessionmaker[Session], sessionmaker[Session], sessionmaker[Session]]:
    """三条独立 sessionmaker：同一 engine 的不同连接才是真并发（NullPool 每次取新连接）。"""
    return tuple(sessionmaker(bind=mysql_store, future=True) for _ in range(3))  # type: ignore[return-value]


def _wait(session: Session, seconds: int) -> None:
    """把本连接的锁等待压到秒级：等待本身就是断言，绝不能让用例挂在默认 50 秒上。"""
    session.execute(text(f"SET SESSION innodb_lock_wait_timeout = {int(seconds)}"))


def _is_lock_wait(exc: BaseException) -> bool:
    return "Lock wait timeout" in str(exc) or "1205" in str(exc)


def _seed_book(writer: sessionmaker[Session]) -> None:
    with writer() as session:
        BookRepository(session).upsert_many(
            [
                BookSyncRow(
                    book_id=BOOK_ID,
                    domain_id="math-advanced",
                    course_id="cw",
                    title="并发判据样本",
                    file_path="raw/math-advanced/cw/book.pdf",
                )
            ]
        )
        session.commit()


def _count(session: Session) -> int:
    """该页在库的 structure 意图行数（只数意图行，事件行的 `intent_key` 为 NULL）。"""
    return int(
        session.execute(
            text(
                "SELECT COUNT(*) FROM af_operations "
                "WHERE intent_key IS NOT NULL AND book_id=:b AND op_kind='structure' AND page_no=:p"
            ),
            {"b": BOOK_ID, "p": PAGE_NO},
        ).scalar()
    )


def test_cw3_concurrent_declaration_on_same_coordinate_collapses_to_one_row(
    makers: tuple[sessionmaker[Session], sessionmaker[Session], sessionmaker[Session]],
) -> None:
    """CW-3 反面：两个人同时声明同一坐标必须**合成一行**，且身份取先到者。

    写法撤掉 `uq_af_operations_intent`（或把单语句 upsert 改成「先查再插」）即各造一行，本用例转红。
    """
    a_make, b_make, c_make = makers
    _seed_book(a_make)
    intent = RegionIntent(op="add", bbox=[10, 20, 30, 40])
    target_id = region_id_for(BOOK_ID, PAGE_NO, intent)

    with a_make() as a, b_make() as b:
        OperationRepository(a).replace_page_intents(BOOK_ID, PAGE_NO, [intent])
        first = a.execute(
            text(
                "SELECT op_id, created_at FROM af_operations "
                "WHERE book_id=:b AND op_kind='structure' AND page_no=:p AND target_id=:t"
            ),
            {"b": BOOK_ID, "p": PAGE_NO, "t": target_id},
        ).one()
        # A 未提交：B 的同坐标声明只能在唯一键上排队，排不到就判超时（＝锁真实存在）
        _wait(b, LOCK_WAIT_SECONDS)
        with pytest.raises(OperationalError) as blocked:
            OperationRepository(b).upsert_intent(
                op_kind="structure",
                book_id=BOOK_ID,
                page_no=PAGE_NO,
                target_id=target_id,
                note="B 的覆写",
            )
        assert _is_lock_wait(blocked.value), str(blocked.value)[:200]

        a.commit()
        b.rollback()
        _wait(b, 10)
        row_b = OperationRepository(b).upsert_intent(
            op_kind="structure",
            book_id=BOOK_ID,
            page_no=PAGE_NO,
            target_id=target_id,
            note="B 的覆写",
        )
        b.commit()

        # 身份与首次落库时刻不换手，内容按后到者覆盖
        assert (row_b.op_id, row_b.created_at) == (first.op_id, first.created_at)
        assert row_b.note == "B 的覆写"
        with c_make() as reader:
            assert _count(reader) == 1, "同一坐标两行＝人工当前态出现两个版本，判据崩塌"
            assert reader.execute(
                text("SELECT note FROM af_operations WHERE op_id=:i"), {"i": first.op_id}
            ).scalar() == "B 的覆写"


def test_cw3_event_rows_stay_outside_the_intent_unique_key(
    makers: tuple[sessionmaker[Session], sessionmaker[Session], sessionmaker[Session]],
) -> None:
    """CW-3 的边界：`intent_key` 只约束意图行，事件行的 NULL 之间不互相排斥（多 NULL 可并存）。

    这条钉的是「流水永不当判据位」的库学前提：若唯一键把 NULL 也算重复，第二条解析版本就写不进去。
    """
    a_make, _, c_make = makers
    _seed_book(a_make)
    with a_make() as a:
        for suffix in ("job-1", "job-2"):
            OperationRepository(a).create(
                ParseJobRow(job_id=f"{BOOK_ID}-{suffix}", book_id=BOOK_ID, engine="mineru", pages=None)
            )
        a.commit()
    with c_make() as reader:
        null_intent = int(
            reader.execute(
                text(
                    "SELECT COUNT(*) FROM af_operations WHERE book_id=:b AND intent_key IS NULL"
                ),
                {"b": BOOK_ID},
            ).scalar()
        )
        assert null_intent == 2, "MySQL 唯一键把多 NULL 判重＝事件流水被唯一键误伤"
        assert reader.execute(
            text("SELECT @@transaction_isolation")
        ).scalar() in {"REPEATABLE-READ", "READ-COMMITTED"}, "库的隔离级别是本层判据的前提"


def test_cw4_second_page_declaration_blocks_on_the_book_row_lock(
    makers: tuple[sessionmaker[Session], sessionmaker[Session], sessionmaker[Session]],
) -> None:
    """CW-4 反面：同页全量声明必须被 `_lock_book` 串起来，否则后到者把先到者判成缺席。

    撤掉 `with_for_update()` 后 B 不再等待而会**立刻成功**，本用例的超时断言即转红；
    静默丢失（先到者的框被抹掉）正是这条判据要拦的故障。
    """
    a_make, b_make, c_make = makers
    _seed_book(a_make)
    with a_make() as a, b_make() as b:
        OperationRepository(a).replace_page_intents(
            BOOK_ID, PAGE_NO, [RegionIntent(op="add", bbox=[1, 1, 2, 2])]
        )
        _wait(b, LOCK_WAIT_SECONDS)
        with pytest.raises(OperationalError) as blocked:
            OperationRepository(b).replace_page_intents(
                BOOK_ID, PAGE_NO, [RegionIntent(op="add", bbox=[3, 3, 4, 4])]
            )
        assert _is_lock_wait(blocked.value), str(blocked.value)[:200]

        a.commit()
        b.rollback()
        _wait(b, 10)
        after = OperationRepository(b).replace_page_intents(
            BOOK_ID, PAGE_NO, [RegionIntent(op="add", bbox=[3, 3, 4, 4])]
        )
        b.commit()
        assert [row.bbox for row in after] == [[3, 3, 4, 4]], "等到锁后必须按最新态重算差异"
        with c_make() as reader:
            assert _count(reader) == 1, "两批声明共存＝A 的框没被缺席撤销，页上留着无人声明的意图"


def test_cw6_rev_conflict_judges_the_latest_committed_row_not_the_snapshot(
    makers: tuple[sessionmaker[Session], sessionmaker[Session], sessionmaker[Session]],
) -> None:
    """CW-6 反面：带着旧 `rev` 提交的表单必须判冲突，且库里留的是先到者的结论。

    B 先读到 `rev=0`（快照），A 提交把 `rev` 推到 1，B 再按 `if_rev=0` 条件更新——只有真库的
    锁定读会看到最新版本，SQLite 上读改写与条件 UPDATE 看不出差别，所以这条只能在 MySQL 层成立。
    """
    a_make, b_make, c_make = makers
    _seed_book(a_make)
    with a_make() as a:
        KpRepository(a).create(
            KpNodeRow(node_id=NODE_ID, book_id=BOOK_ID, name="cw6", norm_key="cw6")
        )
        a.commit()
        base_rev = int(
            a.execute(text("SELECT rev FROM af_kp WHERE node_id=:n"), {"n": NODE_ID}).scalar()
        )

    with b_make() as b:
        assert b.get(AfKp, NODE_ID) is not None, "先建立 B 的读快照（人已在该页打开表单）"
        with a_make() as a:
            ok = KpRepository(a).update(NODE_ID, {"summary": "A 的结论"}, if_rev=base_rev)
            assert (ok.ok, ok.rev_conflict) == (True, False)
            a.commit()
        stale = KpRepository(b).update(NODE_ID, {"summary": "B 的旧表单"}, if_rev=base_rev)
        assert (stale.ok, stale.rev_conflict) == (False, True), "旧 rev 竟写进去了＝409 判据失效"
        b.rollback()

    with c_make() as reader:
        row = reader.execute(
            text("SELECT summary, rev FROM af_kp WHERE node_id=:n"), {"n": NODE_ID}
        ).one()
        assert row.summary == "A 的结论"
        assert row.rev == base_rev + 1, "只有成功写入才递增 rev"
