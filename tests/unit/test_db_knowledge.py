"""
模块职责：守护 `af_kp` 四个写者的列域分列（机器来源域／人工背书域／确认位／长文指针）、目录整批声明的
「只改两列＋缺席只降级」形状，确认前置校验（可回指判据）与读面按取证版本分组，以及删节点与
清引用边的同事务形状和 `narrative_ref` 的单段名闸。
设计关联（DesignRef）：docs/design/knowledge-analysis.md（KA-3、KA-7、KA-8、KA-11、KA-12、KA-14、KA-17、KA-18、KA-20、KA-21、KA-30）
设计关联（DesignRef）：docs/architecture/database-design.md（`af_kp` 列清单与服务端不变式）
实现状态：Current
被测代码：src/axiom_flow/db/repositories.py:KpRepository
守护面：知识点梳理写入通路的写者分列与人工整批面
失效后果：机器与人工互覆同一列、目录声明删掉了行、或坏锚点被确认进导出面时，梳理结论失去可回指性
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from axiom_flow.db import repositories
from axiom_flow.db.models import GEN_STATUSES, AfKp, AfOperation, Base
from axiom_flow.db.repositories import (
    BookRepository,
    BookSyncRow,
    KpConfirmResult,
    KpNodeRow,
    KpOutlineDecl,
    KpRepository,
    PageRepository,
)

ANCHORS = [{"kind": "block", "ref_id": "", "page": 3, "block_indexes": [1, 2], "snippet": "ε-δ"}]
LINKS = [{"kind": "prerequisite", "to": "afkp-a2", "note": ""}]

#: 机器来源域（`KA-11`）：身份、结构、锚点、`summary` 与来源三列——即 `KpNodeRow` 的全部输入面。
SOURCE_COLUMNS = (
    "node_id",
    "book_id",
    "job_id",
    "node_kind",
    "parent_id",
    "depth",
    "order_hint",
    "name",
    "norm_key",
    "role",
    "type",
    "summary",
    "links",
    "anchors",
    "narrative_ref",
    "gen_status",
    "gen_batch",
    "model_ref",
    "meta",
)

#: 人工背书域（`KA-11` 列出的六项）：名称、结构挂载（`parent_id`/`order_hint`，即 `KA-7` 的重排两列）、
#: `links`、`anchors`、`role`/`type`、`summary`。确认位另走 `set_confirmed`（`KA-17`）。
ENDORSEMENT_COLUMNS = (
    "name",
    "parent_id",
    "order_hint",
    "links",
    "anchors",
    "role",
    "type",
    "summary",
)


@pytest.fixture()
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def session(engine):
    with Session(engine) as session:
        yield session


def _machine_row(node_id: str = "afkp-a1", **overrides) -> KpNodeRow:
    """机器梳理产出的行：带取证版本、批次号与产出者，`gen_status` 从 `draft` 起。"""
    payload = {
        "node_id": node_id,
        "book_id": "b05",
        "job_id": "job-a1",
        "node_kind": "point",
        "name": "极限的 ε-δ 定义",
        "norm_key": "极限的ε-δ定义",
        "summary": "一句话总结",
        "anchors": ANCHORS,
        "links": LINKS,
        "gen_status": "draft",
        "gen_batch": "afkp-0123456789ab",
        "model_ref": "qwen-vl",
    }
    payload.update(overrides)
    return KpNodeRow(**payload)


def _kp(session: Session, node_id: str = "afkp-a1", **overrides) -> AfKp:
    return KpRepository(session).create(_machine_row(node_id, **overrides))


def _count(session: Session, condition) -> int:
    return int(session.scalar(select(func.count()).select_from(AfKp).where(condition)) or 0)


def _column_values(node: AfKp, columns: tuple[str, ...]) -> dict[str, object]:
    return {column: getattr(node, column) for column in columns}


class TestMachineSourceLeg:
    """`KA-11` 的机器腿：来源域由 `create` 写入，`confirmed` 不在它的输入面里。"""

    def test_create_writes_the_whole_source_domain(self, session):
        node = _kp(
            session,
            "afkp-a1",
            node_kind="outline",
            depth=1,
            order_hint=7,
            narrative_ref="afkp-a1.md",
            meta={"probe": 1},
        )

        assert node.job_id == "job-a1"
        assert (node.node_kind, node.depth, node.order_hint) == ("outline", 1, 7)
        assert node.narrative_ref == "afkp-a1.md"
        assert node.meta == {"probe": 1}
        assert node.gen_status == "draft"
        assert node.gen_batch == "afkp-0123456789ab"
        assert node.model_ref == "qwen-vl"
        assert (node.confirmed, node.rev) == (0, 0)

    def test_insert_input_is_exactly_the_source_domain(self):
        """`confirmed`/`rev`/时刻列不进写入输入面：机器腿「恒写 0」是构造事实而非纪律自觉。"""
        assert {field.name for field in dataclasses.fields(KpNodeRow)} == set(SOURCE_COLUMNS)

    def test_confirmed_counter_stays_zero_after_source_legs(self, session):
        """`KA-11` 反向判据（机器腿部分）：来源域入口跑完后的 `confirmed` 非零计数恒 0。"""
        _kp(session, "afkp-a1")
        _kp(session, "afkp-a2", node_kind="outline", gen_status="candidate")
        KpRepository(session).update("afkp-a2", {"summary": "人工改过一次"})
        KpRepository(session).set_confirmed("afkp-a2", confirmed=False)

        assert _count(session, AfKp.confirmed != 0) == 0

    def test_source_leg_writes_no_gen_status_outside_the_closed_set(self, session):
        """`KA-28` 的写者面自证：机器插入的 `gen_status` 只落在三值闭集内。"""
        _kp(session, "afkp-a1", gen_status="draft")
        _kp(session, "afkp-a2", gen_status="candidate")

        assert _count(session, ~AfKp.gen_status.in_(GEN_STATUSES)) == 0


class TestHumanEndorsementLeg:
    """`KA-11`/`KA-12` 的人工腿：只写背书域，来源三列与身份/确认位一律拒绝。"""

    def test_endorsement_columns_are_writable(self, session):
        _kp(session, "afkp-a2")
        _kp(session, "afkp-a1")
        repo = KpRepository(session)
        values: dict[str, object] = {
            "name": "改名后的节题",
            "parent_id": "afkp-a2",
            "order_hint": 12,
            "links": [{"kind": "extends", "to": "afkp-a2", "note": ""}],
            "anchors": [{"kind": "block", "ref_id": "", "page": 9, "block_indexes": [0], "snippet": ""}],
            "role": "core",
            "type": "concept",
            "summary": "人重写的一句话",
        }

        for column in ENDORSEMENT_COLUMNS:
            assert repo.update("afkp-a1", {column: values[column]}).ok, column

        assert _column_values(repo.get("afkp-a1"), ENDORSEMENT_COLUMNS) == values

    def test_source_and_identity_columns_are_rejected(self, session):
        """域外列在语句发出前就拒：机器写 `gen_status` 的入口不是人工腿（`KA-11`、`KA-12`）。"""
        _kp(session)
        repo = KpRepository(session)
        rejected = sorted(
            {column.name for column in AfKp.__table__.columns}
            - set(ENDORSEMENT_COLUMNS)
            - {"node_id", "book_id"}
        )

        for column in rejected:
            with pytest.raises(ValueError, match="af_kp"):
                repo.update("afkp-a1", {column: "x"})
            assert column not in ENDORSEMENT_COLUMNS
        assert {"gen_status", "gen_batch", "model_ref", "confirmed"} <= set(rejected)

    def test_endorsement_keeps_provenance_and_bumps_rev(self, session):
        """`KA-12`：人工改写后 `gen_batch`/`model_ref`/`gen_status`/`job_id` 原值不变，只 `rev+1`。"""
        node = _kp(session)
        before = _column_values(node, ("gen_batch", "model_ref", "gen_status", "job_id"))

        result = KpRepository(session).update(
            "afkp-a1", {"name": "人改的名字", "summary": "人改的总结", "parent_id": "afkp-o1"}
        )

        assert result.ok and not result.rev_conflict
        assert result.node is not None
        assert _column_values(result.node, tuple(before)) == before
        assert (result.node.rev, result.node.name) == (1, "人改的名字")

    def test_stale_if_rev_reports_conflict_with_latest_row(self, session):
        """人工腿的并发代价面：旧表单提交判冲突而不是静默覆盖，响应给最新行。"""
        _kp(session)
        repo = KpRepository(session)
        repo.update("afkp-a1", {"summary": "他人已改"}, if_rev=0)

        stale = repo.update("afkp-a1", {"summary": "旧表单"}, if_rev=0)

        assert (stale.ok, stale.rev_conflict) == (False, True)
        assert stale.node is not None and (stale.node.rev, stale.node.summary) == (1, "他人已改")


class TestConfirmedLeg:
    """`KA-17`：确认位只有 `set_confirmed` 一个入口，撤回不回收任何行。"""

    def test_set_confirmed_is_the_only_gate(self, session):
        _kp(session)
        repo = KpRepository(session)

        assert repo.set_confirmed("afkp-a1", confirmed=True).ok is True
        assert (repo.get("afkp-a1").confirmed, repo.get("afkp-a1").rev) == (1, 1)

        assert repo.set_confirmed("afkp-a1", confirmed=False, if_rev=0).rev_conflict is True
        assert repo.get("afkp-a1").confirmed == 1

        assert repo.set_confirmed("afkp-a1", confirmed=False, if_rev=1).ok is True
        assert repo.get("afkp-a1").confirmed == 0

    def test_unconfirm_touches_only_the_confirmed_bit(self, session):
        """撤回只改本侧列：其余列逐列不变、零删行、零流水行（`KA-17` 的「不发消息」判据位）。"""
        repo = KpRepository(session)
        _kp(session)
        repo.set_confirmed("afkp-a1", confirmed=True)
        watch = tuple(
            column.name
            for column in AfKp.__table__.columns
            if column.name not in {"confirmed", "rev", "updated_at"}
        )
        before = _column_values(repo.get("afkp-a1"), watch)
        ops_before = int(session.scalar(select(func.count()).select_from(AfOperation)) or 0)

        repo.set_confirmed("afkp-a1", confirmed=False)

        after = repo.get("afkp-a1")
        assert _column_values(after, watch) == before
        assert after.confirmed == 0
        assert _count(session, AfKp.node_id == "afkp-a1") == 1
        assert int(session.scalar(select(func.count()).select_from(AfOperation)) or 0) == ops_before

    def test_missing_node_is_not_reported_as_conflict(self, session):
        result = KpRepository(session).set_confirmed("afkp-nope", confirmed=True)

        assert (result.ok, result.rev_conflict, result.node) == (False, False, None)


class TestEvidenceVersionGrouping:
    """`KA-18`：锚点按行上的 `job_id` 那一版解释，生效版本切换不改写读数。"""

    def _book(self, session: Session, active_job_id: str | None) -> None:
        BookRepository(session).upsert_many(
            [
                BookSyncRow(
                    book_id="b05",
                    domain_id="math-advanced",
                    course_id="math_analysis",
                    course_name="数学分析",
                    knowledge_id="kt-mathanalysis-3",
                    title="数学分析原理",
                    part="教程",
                    display_title="Rudin 数学分析原理",
                    authors="Walter Rudin",
                    file_path="books/b05.pdf",
                )
            ]
        )
        assert BookRepository(session).set_active_job("b05", active_job_id)

    def test_grouping_is_by_each_row_version_not_the_active_one(self, session):
        _kp(session, "afkp-o1", job_id="job-a1", node_kind="outline", order_hint=1)
        _kp(session, "afkp-p1", job_id="job-a1", order_hint=2)
        _kp(session, "afkp-p2", job_id="job-b2", order_hint=3)
        _kp(session, "afkp-h1", job_id="", order_hint=4)

        grouped = KpRepository(session).list_by_evidence_version("b05")

        assert {key: [node.node_id for node in nodes] for key, nodes in grouped.items()} == {
            "job-a1": ["afkp-o1", "afkp-p1"],
            "job-b2": ["afkp-p2"],
            "": ["afkp-h1"],
        }

    def test_anchors_read_unchanged_after_activation_switch(self, session):
        """换生效版本只动 `af_books` 一列：行的取证版本与锚点读数逐字不变。"""
        self._book(session, "job-a1")
        _kp(session, "afkp-p1", job_id="job-a1", anchors=ANCHORS)
        before = KpRepository(session).get("afkp-p1")
        assert (before.job_id, before.anchors) == ("job-a1", ANCHORS)

        assert BookRepository(session).set_active_job("b05", "job-b2")

        after = KpRepository(session).get("afkp-p1")
        assert after.anchors == ANCHORS
        assert list(KpRepository(session).list_by_evidence_version("b05")) == ["job-a1"]


def _outline(
    session: Session,
    node_id: str,
    *,
    name: str,
    norm_key: str | None = None,
    parent: str | None = None,
    depth: int = 1,
    order: int = 0,
    book_id: str = "b05",
    **overrides,
) -> AfKp:
    """目录行：`node_kind='outline'` 且带 `depth`（`KA-6` 的两层）；`links` 默认不带（目录不参与关系边）。"""
    return _kp(
        session,
        node_id,
        book_id=book_id,
        node_kind="outline",
        name=name,
        norm_key=norm_key if norm_key is not None else name,
        parent_id=parent,
        depth=depth,
        order_hint=order,
        links=None,
        **overrides,
    )


def _decl(node_id: str, *, parent: str | None = None, order: int = 0) -> KpOutlineDecl:
    return KpOutlineDecl(node_id=node_id, parent_id=parent, order_hint=order)


class TestOutlineWholeSetDeclaration:
    """`KA-8`/`KA-7`：目录整批声明只改 `parent_id`/`order_hint` 两列，缺席只降级、零删除。"""

    def _scene(self, session: Session) -> None:
        _outline(session, "afkp-o1", name="第 1 章", order=1)
        _outline(session, "afkp-o2", name="第 2 章", order=2)
        _outline(session, "afkp-o3", name="第 3 章", order=3)
        _kp(
            session,
            "afkp-p1",
            name="数列极限",
            norm_key="数列极限",
            parent_id="afkp-o1",
            order_hint=1,
            links=[{"kind": "explains", "to": "afkp-o1", "note": ""}],
        )
        _kp(
            session,
            "afkp-p2",
            name="函数极限",
            norm_key="函数极限",
            parent_id="afkp-o3",
            order_hint=7,
            links=[{"kind": "explains", "to": "afkp-o3", "note": ""}],
        )

    def test_absent_outline_is_kept_and_its_children_move_to_the_pool(self, session):
        """缺席即撤销的「只降级」形态：行留在表里，只有它的子点退回未归档池。"""
        self._scene(session)
        repo = KpRepository(session)

        rows = repo.replace_outline("b05", [_decl("afkp-o2", order=1), _decl("afkp-o1", order=2)])

        assert [node.node_id for node in rows] == ["afkp-o2", "afkp-o1"]
        assert [node.order_hint for node in rows] == [1, 2]
        assert repo.get("afkp-o3") is not None
        assert repo.get("afkp-o3").node_kind == "outline"
        assert repo.get("afkp-p2").parent_id is None
        assert _count(session, AfKp.book_id == "b05") == 5

    def test_referenced_absent_outline_leaves_no_dangling_edge(self, session):
        """KA-8 的理由条落成真判据：不删行，所以点行指向缺席目录行的边仍然落得到端点。"""
        self._scene(session)
        repo = KpRepository(session)

        repo.replace_outline("b05", [_decl("afkp-o1", order=1)])

        ids = {node.node_id for node in repo.list_for_book("b05")}
        for node in repo.list_for_book("b05"):
            for link in node.links or []:
                assert link["to"] in ids, (node.node_id, link)

    def test_declaration_writes_only_the_two_structure_columns(self, session):
        """`KA-7` 的重排两列：除 `parent_id`/`order_hint`（与 `rev`/`updated_at`）外逐列不变。"""
        self._scene(session)
        repo = KpRepository(session)
        watch = tuple(
            column.name
            for column in AfKp.__table__.columns
            if column.name not in {"parent_id", "order_hint", "rev", "updated_at"}
        )
        before = {node.node_id: _column_values(node, watch) for node in repo.list_for_book("b05")}

        repo.replace_outline("b05", [_decl("afkp-o1", order=5), _decl("afkp-o2", order=6)])

        after = {node.node_id: _column_values(node, watch) for node in repo.list_for_book("b05")}
        assert after == before
        mounted = {node.node_id: (node.parent_id, node.order_hint) for node in repo.list_for_book("b05")}
        assert (mounted["afkp-o1"], mounted["afkp-o2"]) == ((None, 5), (None, 6))
        assert mounted["afkp-o3"] == (None, 3)

    def test_resending_the_same_declaration_is_idempotent(self, session):
        """全量声明天然幂等（与结构意图同一形态）：第二次读到的父与序完全相同，不复活任何行。"""
        self._scene(session)
        repo = KpRepository(session)
        declarations = [_decl("afkp-o2", order=1), _decl("afkp-o1", order=2)]

        repo.replace_outline("b05", declarations)
        reading = {node.node_id: (node.parent_id, node.order_hint) for node in repo.list_for_book("b05")}
        repo.replace_outline("b05", declarations)

        assert {node.node_id: (node.parent_id, node.order_hint) for node in repo.list_for_book("b05")} == reading

    def test_a_point_row_cannot_be_declared_as_an_outline_entry(self, session):
        self._scene(session)

        with pytest.raises(ValueError, match="outline"):
            KpRepository(session).replace_outline("b05", [_decl("afkp-p1")])

    def test_entries_must_belong_to_this_book(self, session):
        """跨书 `node_id` 与不存在的 `node_id` 同判：目录声明的作用域就是一册书。"""
        self._scene(session)
        _outline(session, "afkp-x9", name="别的书的章", book_id="b11")

        for unknown in ("afkp-x9", "afkp-nope"):
            with pytest.raises(ValueError, match=unknown):
                KpRepository(session).replace_outline("b05", [_decl("afkp-o1"), _decl(unknown)])

    def test_depth_one_entry_must_not_declare_a_parent(self, session):
        """`depth=1` 是章，父必须为空——给父就不是两层目录了（`KA-6`）。"""
        self._scene(session)

        with pytest.raises(ValueError, match="depth=1"):
            KpRepository(session).replace_outline("b05", [_decl("afkp-o1", parent="afkp-o2")])

    def test_depth_two_parent_must_be_a_declared_depth_one_entry(self, session):
        """`depth=2` 的父只能是**本批声明的** `depth=1` 行：指自己、指不在本批的章、指另一个节，三种都拒。"""
        self._scene(session)
        _outline(session, "afkp-o4", name="1.1 小节", depth=2, parent="afkp-o1")
        _outline(session, "afkp-o5", name="1.2 小节", depth=2, parent="afkp-o1")

        with pytest.raises(ValueError, match="afkp-o4"):
            KpRepository(session).replace_outline(
                "b05", [_decl("afkp-o1"), _decl("afkp-o4", parent="afkp-o4")]
            )
        with pytest.raises(ValueError, match="afkp-o4"):
            KpRepository(session).replace_outline("b05", [_decl("afkp-o4", parent="afkp-o3")])
        with pytest.raises(ValueError, match="afkp-o5"):
            KpRepository(session).replace_outline(
                "b05",
                [
                    _decl("afkp-o1"),
                    _decl("afkp-o4", parent="afkp-o1"),
                    _decl("afkp-o5", parent="afkp-o4"),
                ],
            )

    def test_depth_two_is_valid_when_its_parent_is_declared_in_this_batch(self, session):
        self._scene(session)
        _outline(session, "afkp-o4", name="1.1 小节", depth=2, parent="afkp-o1")

        rows = KpRepository(session).replace_outline(
            "b05", [_decl("afkp-o1", order=1), _decl("afkp-o4", parent="afkp-o1", order=2)]
        )

        assert [(node.node_id, node.parent_id, node.order_hint) for node in rows] == [
            ("afkp-o1", None, 1),
            ("afkp-o4", "afkp-o1", 2),
        ]

    def test_same_parent_duplicate_name_is_rejected_and_names_the_existing_row(self, session):
        """`ix_af_kp_norm` 的「同父重名」在整批形态下同样成立：422 的点名位给既有 `node_id`。"""
        _outline(session, "afkp-o1", name="第 1 章", order=1)
        _outline(session, "afkp-o2", name="第一章", norm_key="第 1 章", order=2)

        with pytest.raises(ValueError, match="afkp-o1"):
            KpRepository(session).replace_outline(
                "b05", [_decl("afkp-o1", order=1), _decl("afkp-o2", order=2)]
            )
        assert _count(session, AfKp.book_id == "b05") == 2

    def test_a_name_moving_to_another_parent_is_not_a_conflict(self, session):
        """整批形态的查重按**声明后的最终父**比：两行只是换父，不该被判成重名。"""
        _outline(session, "afkp-o1", name="第 1 章", order=1)
        _outline(session, "afkp-o2", name="第 2 章", order=2)
        _outline(session, "afkp-o3", name="同号", norm_key="同号", order=3)
        _outline(session, "afkp-o4", name="同号", norm_key="同号", depth=2, parent="afkp-o3")

        rows = KpRepository(session).replace_outline(
            "b05",
            [
                _decl("afkp-o1", order=1),
                _decl("afkp-o2", order=2),
                _decl("afkp-o3", order=3),
                _decl("afkp-o4", parent="afkp-o2", order=4),
            ],
        )

        assert [node.node_id for node in rows] == ["afkp-o1", "afkp-o2", "afkp-o3", "afkp-o4"]


class TestConfirmPrechecks:
    """`KA-14` 的可回指前置校验与 `KA-3` 的确认门析取支：`confirm` 是唯一带校验的确认入口。"""

    def _parsed(self, session: Session, *pages: int, job_id: str = "job-a1") -> None:
        repo = PageRepository(session)
        for page_no in pages:
            repo.upsert("b05", job_id, page_no, status="parsed", source="mineru")
        session.commit()

    def _anchored(self, session: Session, node_id: str = "afkp-p1", *, page: int = 3) -> AfKp:
        return _kp(
            session,
            node_id,
            gen_status="candidate",
            anchors=[{"kind": "page_block", "ref_id": "", "page": page, "block_indexes": [0], "snippet": ""}],
            links=None,
        )

    def test_empty_anchors_is_refused_by_name(self, session):
        self._parsed(session, 1, 2, 3)
        node = _kp(session, "afkp-p1", gen_status="candidate", anchors=None, links=None)

        result = KpRepository(session).confirm(node.node_id, confirmed=True)

        assert (result.ok, result.rev_conflict) == (False, False)
        assert any("anchors" in item for item in result.missing)
        assert KpRepository(session).get("afkp-p1").confirmed == 0

    def test_anchor_page_must_be_parsed_in_the_rows_own_evidence_version(self, session):
        """取证版本按**行自身** `job_id` 读（`KA-18`）：页在别的版本里 parsed 不算可回指。"""
        self._parsed(session, 1, 2)
        self._parsed(session, 3, job_id="job-b2")
        node = self._anchored(session, page=3)

        assert any("3" in item for item in KpRepository(session).confirm(node.node_id, confirmed=True).missing)

        self._parsed(session, 3)

        assert KpRepository(session).confirm(node.node_id, confirmed=True).ok is True

    def test_missing_link_endpoint_is_named_in_the_response(self, session):
        self._parsed(session, 3)
        node = self._anchored(session)
        KpRepository(session).update(node.node_id, {"links": [{"kind": "prerequisite", "to": "afkp-nope", "note": ""}]})

        result = KpRepository(session).confirm(node.node_id, confirmed=True)

        assert result.ok is False
        assert any("afkp-nope" in item for item in result.missing)

    def test_self_loop_edge_is_refused(self, session):
        """「无自环」是确认前置的一部分：自环行一旦进主表，根侧的复习序列当场无解。"""
        self._parsed(session, 3)
        node = self._anchored(session)
        KpRepository(session).update(node.node_id, {"links": [{"kind": "extends", "to": node.node_id, "note": ""}]})

        assert KpRepository(session).confirm(node.node_id, confirmed=True).ok is False

    def test_row_without_evidence_version_cannot_be_confirmed(self, session):
        """`job_id=''` 的人工直填行没有取证版本，锚点页无处解释（`KA-14` 的页集判据当场不成立）。"""
        self._parsed(session, 3)
        node = self._anchored(session)

        with pytest.raises(ValueError):
            # 机器列不在背书域：人工腿改不动 job_id，本用例只能另造一行
            KpRepository(session).update(node.node_id, {"job_id": ""})
        fresh = _kp(session, "afkp-h1", job_id="", gen_status="candidate", links=None, anchors=ANCHORS)

        result = KpRepository(session).confirm(fresh.node_id, confirmed=True)

        assert result.ok is False
        assert any("job_id" in item for item in result.missing)

    def test_gate_off_allows_unconfirmed_pages_and_gate_on_refuses_them(self, session, monkeypatch):
        """`KA-3` 的两个析取支都取：门未启用可确认；门启用则锚点页须已 `confirmed`。"""
        self._parsed(session, 3)
        node = self._anchored(session)

        assert KpRepository(session).confirm(node.node_id, confirmed=True).ok is True
        KpRepository(session).set_confirmed(node.node_id, confirmed=False)
        monkeypatch.setattr(repositories, "PAGE_CONFIRM_GATE_ENABLED", True)

        refused = KpRepository(session).confirm(node.node_id, confirmed=True)
        assert refused.ok is False
        assert any("3" in item for item in refused.missing)

        page = PageRepository(session).get("b05", "job-a1", 3)
        assert page is not None
        page.confirm_state = "confirmed"
        session.commit()

        assert KpRepository(session).confirm(node.node_id, confirmed=True).ok is True

    def test_page_confirm_gate_is_off_until_its_writer_lands(self):
        """反向判据：`confirm_state` 的写者属另一条线（PLAN-018 W8），本案不得提前把门当已启用。"""
        assert repositories.PAGE_CONFIRM_GATE_ENABLED is False

    def test_stale_if_rev_is_a_conflict_not_a_missing_item(self, session):
        """先比 `rev` 再校验形状：旧表单的 409 不该被读成「行不合形状」的 422。"""
        self._parsed(session, 1, 2)
        node = _kp(session, "afkp-p1", gen_status="candidate", anchors=None, links=None)

        result = KpRepository(session).confirm(node.node_id, confirmed=True, if_rev=99)

        assert (result.ok, result.rev_conflict, result.missing) == (False, True, ())
        assert result.node is not None and result.node.rev == 0

    def test_missing_node_is_reported_without_conflict_or_items(self, session):
        result = KpRepository(session).confirm("afkp-nope", confirmed=True)

        assert (result.ok, result.rev_conflict, result.missing, result.node) == (False, False, (), None)

    def test_unconfirm_skips_prechecks_and_keeps_the_row(self, session):
        """`KA-17`：撤回只影响本侧导出面，永远可撤——否则坏锚的行会永久卡在已确认态。"""
        self._parsed(session, 3)
        node = self._anchored(session)
        repo = KpRepository(session)
        assert repo.confirm(node.node_id, confirmed=True).ok is True

        repo.update(node.node_id, {"anchors": None})
        withdrawn = repo.confirm(node.node_id, confirmed=False)

        assert withdrawn.ok is True
        assert withdrawn.node is not None and withdrawn.node.confirmed == 0
        assert _count(session, AfKp.node_id == node.node_id) == 1

    def test_confirm_result_carries_the_written_row(self, session):
        """成功路径委托 `set_confirmed`：确认位仍只有那一个入口，`rev` 只加一、其余列逐列不变。"""
        self._parsed(session, 3)
        node = self._anchored(session)
        repo = KpRepository(session)
        before = repo.get(node.node_id)
        assert before is not None
        watch = tuple(
            column.name
            for column in AfKp.__table__.columns
            if column.name not in {"confirmed", "rev", "updated_at"}
        )
        before_values = _column_values(before, watch)
        before_rev = before.rev

        result = repo.confirm(node.node_id, confirmed=True)

        assert isinstance(result, KpConfirmResult)
        assert result.ok is True and result.missing == ()
        assert result.node is not None
        assert (result.node.confirmed, result.node.rev) == (1, before_rev + 1)
        assert _column_values(result.node, watch) == before_values


class TestNarrativeRefShape:
    """`KA-20`／验收判据 9 的列侧一半：`narrative_ref` 只可能是空串或单段文件名。

    判据位在 [database-design.md](../../architecture/database-design.md) 的 `narrative_ref` 行——
    「路径基准与数据根双闸沿用产物约定（单段名 + 根内路径，拒绝越根）」。这一列的**写者域**未定
    （PLAN-025 的 R5 等裁），但形状闸与写者域无关：谁写都得过这道闸。
    """

    @pytest.mark.parametrize(
        "value",
        ["afkp-a1/../a2.md", "kp/afkp-a1.md", "\\afkp-a1.md", ".hidden.md", ".."],
    )
    def test_a_path_like_value_is_rejected_before_the_insert(self, session, value):
        with pytest.raises(ValueError, match="narrative_ref"):
            _kp(session, "afkp-a1", narrative_ref=value)

    def test_a_single_segment_name_is_accepted(self, session):
        """单段名合法：本轮唯一能写这一列的腿就是机器 `create`。"""
        assert _kp(session, "afkp-a1", narrative_ref="afkp-a1.md").narrative_ref == "afkp-a1.md"

    def test_the_empty_value_still_means_none(self, session):
        assert _kp(session, "afkp-a1").narrative_ref == ""


class TestNarrativeRefSystemLeg:
    """`KA-30` 的仓储一半：`narrative_ref` 在 UPDATE 通路上唯一由第四条系统腿落，其余三条腿都碰不到它。

    「只由写腿落」在库内侧的可守形状＝一条单列 UPDATE ＋ 三条反向断言：机器 `create` 只在插入时给值
    （梳理通路恒给空串）、人工 `update` 的白名单不含这列、确认位与归一各条不途经。`rev` 随成功写入
    自增与归一各条同形（`KA-11` 的「系统腿也带序号」），所以序号不是「只有人改过」的计数器——
    判据位是 `confirmed`。
    """

    def test_the_leg_writes_the_pointer_and_the_two_stamp_columns_only(self, session):
        """整表逐列对账：除指针与两枚序号/时刻列，没有任何一列被这条腿改过。"""
        _kp(session, "afkp-a1")
        repo = KpRepository(session)
        columns = tuple(column.name for column in AfKp.__table__.columns)
        # 先按值快照：同一行的两个句柄是同一个身份映射对象，写后对比会「全等」而什么都测不到
        snapshot = _column_values(repo.get("afkp-a1"), columns)
        assert snapshot is not None

        written = repo.set_narrative_ref("afkp-a1", "afkp-a1.md")

        assert written.ok is True and written.rev_conflict is False
        assert written.node is not None
        after = _column_values(written.node, columns)
        assert {column for column in columns if after[column] != snapshot[column]} == {
            "narrative_ref",
            "rev",
            "updated_at",
        }
        assert after["narrative_ref"] == "afkp-a1.md"
        assert after["rev"] == snapshot["rev"] + 1
        assert after["updated_at"] >= snapshot["updated_at"]

    def test_the_leg_does_not_move_the_confirmation_bit_or_the_source_columns(self, session):
        """`KA-11` 的分域在第四条腿上同样成立：确认位与来源三列不因写长文而变。"""
        _kp(session, "afkp-a1", gen_status="candidate")
        repo = KpRepository(session)
        repo.set_confirmed("afkp-a1", confirmed=True)
        before = _column_values(
            repo.get("afkp-a1"), ("confirmed", "gen_status", "gen_batch", "model_ref", "job_id")
        )

        written = repo.set_narrative_ref("afkp-a1", "afkp-a1.md")

        assert written.node is not None
        assert _column_values(written.node, tuple(before)) == before

    def test_the_endorsement_whitelist_does_not_carry_the_pointer(self, session):
        """人工腿改不动这一列（`KA-11` 的写者分域）：白名单与行为两处都守。"""
        _kp(session, "afkp-a1")
        repo = KpRepository(session)

        assert "narrative_ref" not in repositories._KP_ENDORSEMENT_WRITABLE
        with pytest.raises(ValueError, match="af_kp"):
            repo.update("afkp-a1", {"narrative_ref": "afkp-a1.md"})
        assert repo.get("afkp-a1").narrative_ref == ""

    @pytest.mark.parametrize("value", ["", "afkp-a1/../a1.md", "kp/afkp-a1.md", "\\afkp-a1.md", ".."])
    def test_a_bad_pointer_is_rejected_before_the_statement(self, session, value):
        """形状闸与写者域无关：这一条腿也只能落单段文件名（`KA-20`），空值更不是合法指针。"""
        _kp(session, "afkp-a1")
        repo = KpRepository(session)

        with pytest.raises(ValueError, match="narrative_ref"):
            repo.set_narrative_ref("afkp-a1", value)

        assert repo.get("afkp-a1").narrative_ref == ""

    def test_stale_if_rev_writes_no_column(self, session):
        """`if_rev` 钉在 `WHERE` 里：端点在落盘前另判一次，这里守的是竞态那半边。"""
        _kp(session, "afkp-a1")
        repo = KpRepository(session)

        result = repo.set_narrative_ref("afkp-a1", "afkp-a1.md", if_rev=repo.get("afkp-a1").rev + 5)

        assert result.ok is False and result.rev_conflict is True
        assert result.node is not None and result.node.narrative_ref == ""

    def test_a_row_that_does_not_exist_cannot_be_pointed_at(self, session):
        result = KpRepository(session).set_narrative_ref("afkp-never", "afkp-never.md")

        assert result.ok is False and result.rev_conflict is False and result.node is None

    def test_the_other_three_legs_still_leave_the_pointer_empty(self, session):
        """唯一写者的反向对账：机器插入、人工背书、确认与归一各条跑完，这一列仍是空串。"""
        _kp(session, "afkp-a1")
        _kp(session, "afkp-a2", node_kind="outline", gen_status="candidate")
        repo = KpRepository(session)
        repo.update("afkp-a1", {"summary": "人改过", "name": "改后的名"})
        repo.set_confirmed("afkp-a1", confirmed=True)
        repo.flip_batch_status("b05", "afkp-0123456789ab", source="draft", target="candidate")
        repo.reject_draft_nodes(["afkp-a1"])

        assert _count(session, AfKp.narrative_ref != "") == 0
        assert repo.set_narrative_ref("afkp-a1", "afkp-a1.md").ok is True
        assert _count(session, AfKp.narrative_ref != "") == 1


class TestNodeDeletion:
    """`KA-21` 的仓储一半：删行与清引用边同事务，边清不完则整笔回滚。

    「删除必须清引用边」的处置列在 database-design.md 的「服务端不变式」里点名＝`links`，
    且要求**锁内整列覆写**；长文文件的清除在事务之外，形状见
    `tests/unit/test_knowledge_pipeline.py::TestNodeDeletionPath`。
    """

    def _edge_pair(self, session: Session) -> None:
        """三行两指向：`a1 → a2`（默认 `links`）与 `a2 → {a3, a1}`，用来点出「只摘那一条」。"""
        _kp(session, "afkp-a1")
        _kp(
            session,
            "afkp-a2",
            links=[
                {"kind": "explains", "to": "afkp-a3", "note": "n"},
                {"kind": "exemplifies", "to": "afkp-a1", "note": ""},
            ],
        )
        _kp(session, "afkp-a3", links=None)

    def test_deleting_a_node_scrubs_the_edges_pointing_at_it(self, session):
        self._edge_pair(session)
        repo = KpRepository(session)

        assert repo.delete_node("afkp-a2") is True

        assert repo.get("afkp-a2") is None
        assert [link["to"] for link in repo.get("afkp-a1").links or []] == []

    def test_the_scrub_leaves_every_other_edge_of_the_row_intact(self, session):
        """只摘指向被删节点的那条：其余边逐条原样，键序与 `note` 都不动（读侧并集靠它们）。"""
        self._edge_pair(session)
        repo = KpRepository(session)
        before = [dict(link) for link in repo.get("afkp-a2").links]

        assert repo.delete_node("afkp-a3") is True

        assert repo.get("afkp-a2").links == before[1:]

    def test_row_and_edge_scrub_share_one_transaction(self, session):
        """回滚必须把行与边一起还原——半边落库就是「端点不存在」的 422 源头（`KA-14`）。"""
        self._edge_pair(session)
        session.commit()
        repo = KpRepository(session)

        repo.delete_node("afkp-a2")
        session.rollback()

        assert repo.get("afkp-a2") is not None
        assert [link["to"] for link in repo.get("afkp-a1").links] == ["afkp-a2"]

    def test_deleting_a_missing_node_writes_nothing(self, session):
        self._edge_pair(session)
        repo = KpRepository(session)
        before = _column_values(repo.get("afkp-a1"), ("links", "rev", "updated_at"))

        assert repo.delete_node("afkp-nope") is False

        after = _column_values(repo.get("afkp-a1"), ("links", "rev", "updated_at"))
        assert after == before

    def test_only_links_and_the_write_bookkeeping_columns_move(self, session):
        """被清边的行只改 `links`＋`rev`＋`updated_at`：`confirmed` 由人工独占（`KA-11` 的不变式推广）。"""
        self._edge_pair(session)
        repo = KpRepository(session)
        holder = repo.get("afkp-a1")
        assert holder is not None
        repo.set_confirmed("afkp-a1", confirmed=True)
        watch = tuple(
            column.name
            for column in AfKp.__table__.columns
            if column.name not in {"links", "rev", "updated_at"}
        )
        before = _column_values(repo.get("afkp-a1"), watch)
        before_rev = repo.get("afkp-a1").rev

        assert repo.delete_node("afkp-a2") is True

        after = repo.get("afkp-a1")
        assert _column_values(after, watch) == before
        assert (after.confirmed, after.rev) == (1, before_rev + 1)

    def test_parent_id_is_outside_the_edge_scrub(self, session):
        """`KA-21` 只点名 `links`：失父的行保留 `parent_id`，由导出面尾置承接（`order_for_export`）、
        由人重挂或退池（`KA-7`/`KA-8`）。本方法不替人改结构，也不静默把子节点搬进池。
        """
        _kp(session, "afkp-a2", node_kind="outline", depth=1)
        _kp(session, "afkp-a1", parent_id="afkp-a2", links=None)
        repo = KpRepository(session)

        assert repo.delete_node("afkp-a2") is True

        assert repo.get("afkp-a1").parent_id == "afkp-a2"

    def test_the_scrub_does_not_write_an_operation_row(self, session):
        """删除不写 `op_kind='purge'` 流水：该值仍是「建值不建写者」（`test_db.py` 的恒 0 守护不动）。"""
        self._edge_pair(session)

        assert KpRepository(session).delete_node("afkp-a2") is True

        assert int(session.scalar(select(func.count()).select_from(AfOperation)) or 0) == 0

