"""
模块职责：`af_page_regions` 的结构不变式与区域仓储单测（SQLite 内存库，不依赖 MySQL）。
设计关联（DesignRef）：docs/adr/0010-page-regions-are-parse-inputs.md（决定 1）、docs/architecture/database-design.md
实现状态：Current
关联测试：tests/unit/test_db_table_family_governance.py（表族派生口径）
守护面：页级区域表的「无 `job_id`」不变式、全量声明的缺席即撤销、几何派生 `region_id` 的幂等、按书/整表清除
失效后果：区域表退化成版本内覆盖层（换版即丢人工框），或同一意图重复提交各造一行（根侧定位键失效）
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from axiom_flow.db.models import AF_TABLES, AfPageRegion, Base
from axiom_flow.db.repositories import RegionIntent, RegionRepository, region_id_for


@pytest.fixture()
def engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture()
def session(engine):
    with Session(engine) as session:
        yield session


def _add(bbox: tuple[int, int, int, int], **overrides) -> RegionIntent:
    payload = {"op": "add", "bbox": list(bbox)}
    payload.update(overrides)
    return RegionIntent(**payload)


def _delete(anchor: tuple[int, int, int, int], **overrides) -> RegionIntent:
    payload = {"op": "delete", "anchor_bbox": list(anchor)}
    payload.update(overrides)
    return RegionIntent(**payload)


def _merge(members: list[list[int]], **overrides) -> RegionIntent:
    payload = {"op": "merge", "members": members}
    payload.update(overrides)
    return RegionIntent(**payload)


class TestRegionTableInvariants:
    def test_regions_table_is_registered_in_the_family(self):
        assert AF_TABLES[-1] is AfPageRegion.__table__, "区域表必须随表族登记（清除面与夹具白名单由它派生）"

    def test_regions_table_carries_no_job_id(self):
        """区域是**页级解析输入**：本表一旦混进 `job_id`，就退化成版本内覆盖层（ADR 0010 决定 1）。"""
        columns = {column.name for column in AfPageRegion.__table__.columns}
        assert "job_id" not in columns, f"af_page_regions 不得含版本作用域列：{sorted(columns)}"
        assert [column.name for column in AfPageRegion.__table__.primary_key.columns] == [
            "book_id",
            "page_no",
            "region_id",
        ]

    def test_index_names_are_explicit(self):
        assert {index.name for index in AfPageRegion.__table__.indexes} == {
            "ix_af_page_regions_page"
        }


class TestRegionRepository:
    def test_replace_page_writes_all_declared_intents(self, session):
        repo = RegionRepository(session)

        repo.replace_page(
            "b05",
            3,
            [
                _add((72, 900, 600, 1010), kind="exercise"),
                _delete((600, 96, 1100, 210), note="图注误检"),
                _merge([[72, 96, 540, 132], [72, 150, 540, 175]]),
            ],
        )

        rows = {row.op: row for row in repo.list_for_page("b05", 3)}
        assert set(rows) == {"add", "delete", "merge"}
        assert rows["add"].bbox == [72, 900, 600, 1010]
        assert rows["add"].kind == "exercise" and rows["add"].note == ""
        assert rows["delete"].anchor_bbox == [600, 96, 1100, 210]
        assert rows["delete"].note == "图注误检"
        assert rows["merge"].members == [[72, 96, 540, 132], [72, 150, 540, 175]]
        assert len({row.region_id for row in rows.values()}) == 3, "三种意图各自持有一个稳定身份"

    def test_absent_intents_are_revoked(self, session):
        """全量声明：本次未列出的既有人工意图被删除（缺席即撤销，无需 undo 端点）。"""
        repo = RegionRepository(session)
        repo.replace_page("b05", 1, [_add((0, 0, 10, 10)), _add((20, 20, 30, 30))])

        repo.replace_page("b05", 1, [_add((0, 0, 10, 10))])

        rows = repo.list_for_page("b05", 1)
        assert [row.bbox for row in rows] == [[0, 0, 10, 10]]

    def test_empty_declaration_clears_the_page_only(self, session):
        repo = RegionRepository(session)
        repo.replace_page("b05", 1, [_add((0, 0, 10, 10))])
        repo.replace_page("b05", 2, [_add((0, 0, 10, 10))])

        repo.replace_page("b05", 1, [])

        assert repo.list_for_page("b05", 1) == []
        assert len(repo.list_for_page("b05", 2)) == 1, "撤销只作用于本页，不跨页外溢"

    def test_repeated_identical_declaration_is_idempotent(self, session):
        """重复提交同一组意图 ⇒ 行数与 region_id 都不变（几何指纹认领，客户端无需记住 id）。"""
        repo = RegionRepository(session)
        intents = [_add((0, 0, 10, 10)), _delete((5, 5, 15, 15)), _merge([[1, 1, 2, 2], [3, 3, 4, 4]])]
        repo.replace_page("b05", 7, intents)
        first = [(row.region_id, row.op) for row in repo.list_for_page("b05", 7)]

        repo.replace_page("b05", 7, intents)

        assert [(row.region_id, row.op) for row in repo.list_for_page("b05", 7)] == first

    def test_same_geometry_never_produces_two_ids(self, session):
        """同一几何在声明里出现两次 ⇒ 只落一行（主键唯一性由服务端身份承载，不靠客户端去重）。"""
        repo = RegionRepository(session)

        repo.replace_page("b05", 1, [_add((0, 0, 9, 9)), _add((0, 0, 9, 9), note="重复声明")])

        rows = repo.list_for_page("b05", 1)
        assert len(rows) == 1
        assert rows[0].note == "重复声明"

    def test_merge_member_order_is_not_part_of_identity(self, session):
        """成员**集合**决定身份，数组序只是阅读序：调换顺序不得换 region_id。"""
        repo = RegionRepository(session)
        repo.replace_page("b05", 1, [_merge([[1, 1, 2, 2], [3, 3, 4, 4]])])
        before = repo.list_for_page("b05", 1)[0].region_id

        repo.replace_page("b05", 1, [_merge([[3, 3, 4, 4], [1, 1, 2, 2]])])

        row = repo.list_for_page("b05", 1)[0]
        assert row.region_id == before
        assert row.members == [[3, 3, 4, 4], [1, 1, 2, 2]], "阅读序按本次声明覆盖"

    def test_delete_for_book_and_delete_all(self, session):
        repo = RegionRepository(session)
        repo.replace_page("b05", 1, [_add((0, 0, 10, 10)), _add((0, 20, 10, 30))])
        repo.replace_page("b05", 2, [_delete((0, 0, 10, 10))])
        repo.replace_page("b11", 1, [_add((0, 0, 10, 10))])

        assert repo.delete_for_book("b05") == 3
        assert repo.list_for_page("b11", 1) != []
        assert repo.delete_all() == 1
        assert repo.list_for_page("b11", 1) == []

    def test_created_at_survives_rewrite(self, session):
        """全量声明改写只刷新 `updated_at`：首次落库时刻是人工意图的审计线索。"""
        repo = RegionRepository(session)
        repo.replace_page("b05", 1, [_add((0, 0, 10, 10), note="初值")])
        created_at = repo.list_for_page("b05", 1)[0].created_at

        repo.replace_page("b05", 1, [_add((0, 0, 10, 10), note="改备注")])

        row = repo.list_for_page("b05", 1)[0]
        assert row.note == "改备注"
        assert row.created_at == created_at
        assert row.updated_at >= row.created_at


def test_region_id_is_derived_from_geometry_identity():
    """身份确定性：`rgn_<12 hex>` 由 (book, page, op, 几何) 派生 ⇒ 同意图天然同 id、跨页不同 id。

    确定性派生让「重复提交即幂等」不依赖读回旧行，也就不存在改写窗口内两次请求各造一行。
    """
    a = _add((0, 0, 10, 10))
    b = _add((0, 0, 10, 10), note="换了备注")

    first = region_id_for("b05", 1, a)
    assert re.fullmatch(r"rgn_[0-9a-f]{12}", first)
    assert region_id_for("b05", 1, b) == first, "备注与 kind 不参与身份（否则每次微调都换身份）"
    assert region_id_for("b05", 2, a) != first, "页码参与身份"
    assert region_id_for("b05", 1, _delete((0, 0, 10, 10))) != first, "op 参与身份"
