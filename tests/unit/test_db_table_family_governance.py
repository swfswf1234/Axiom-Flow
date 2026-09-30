"""
模块职责：守护「`af_*` 表族张数」的单一口径——一切表集合必须由 `AF_TABLES` 派生，不得各自硬编码。
设计关联（DesignRef）：docs/architecture/database-design.md（表清单节）、docs/standards/testing.md（隔离铁律唯一例外）
实现状态：Current
关联测试：tests/integration/test_parse_persistence.py（真 MySQL 白名单往返）
守护面：af_* 表族治理（PLAN-020 S2/S5）
失效后果：加第五张表时某处表集合漂移——夹具漏 DROP、清除漏删行、或 ORM 新表不进制裁与白名单而静默逃逸
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import Table

from axiom_flow.db import models
from axiom_flow.db.models import AF_TABLES, Base

REPO_ROOT = Path(__file__).resolve().parents[2]

PREFIX = "af_"
ALEMBIC = "alembic_version"


def _literal_assignment(source: str, name: str) -> tuple[str, ...]:
    """从模块源码里取 `NAME = (…)` 的字符串/引用元组，不执行模块。"""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return tuple(
                        ast.unparse(el) for el in node.value.elts  # noqa: B023
                    )
    raise AssertionError(f"源码中找不到 {name} 的顶层赋值")


def test_af_tables_matches_declared_af_metadata():
    """裁決面：凡以 `af_` 前缀挂上 Base.metadata 的表都必须是 `AF_TABLES` 的成员。

    反向半边（`AF_TABLES` ⊆ metadata）平凡成立；本断言钉的是正向——新表一旦声明却未入
    制裁裁，此处即红，逼迫加表走「AF_TABLES + 权威三处」的唯一改动面（PLAN-020 S2）。
    """
    declared = {
        name for name, tbl in Base.metadata.tables.items() if tbl.name.startswith(PREFIX)
    }
    registered = {tbl.name for tbl in AF_TABLES}
    assert declared == registered, (
        f"af_* 表声明与 AF_TABLES 漂移：未入制={sorted(declared - registered)}，"
        f"悬空引用={sorted(registered - declared)}"
    )


def test_every_registered_table_uses_af_namespace():
    assert all(tbl.name.startswith(PREFIX) for tbl in AF_TABLES)


def test_purge_models_are_exactly_af_tables():
    """管理面清除的模型元组必须与 `AF_TABLES` 同集合（S5 删除面同构的静态半边）。

    `_AF_TABLE_MODELS` 存的是 ORM 类名引用，AST 取字面量后经类 → `__table__.name` 映射回表名，
    与 `AF_TABLES` 对账；加表时漏改 admin.py 即红。元组名不带张数（PLAN-020 S1：张数只在权威
    口径出现，标识符携带张数同样是漂移源）。
    """
    source = (REPO_ROOT / "src/axiom_flow/api/admin.py").read_text(encoding="utf-8")
    class_names = _literal_assignment(source, "_AF_TABLE_MODELS")
    table_names = set()
    for name in class_names:
        model = getattr(models, name)
        table_names.add(model.__table__.name)
    assert table_names == {tbl.name for tbl in AF_TABLES}


def test_admin_purge_counts_fields_cover_af_tables():
    """`AdminPurgeCounts` 的 `af_*` 字段名集合必须恰为表名集合（行数累加面的同构半边）。"""
    from axiom_flow.schemas import AdminPurgeCounts

    fields = {
        name for name in AdminPurgeCounts.model_fields if name.startswith(PREFIX)
    }
    assert fields == {tbl.name for tbl in AF_TABLES}


def test_admin_surface_hardcodes_no_table_name():
    """admin 侧不得逐字写表名：计数与删除都按 `__table__.name` 派生，加表才是真的一处改动。

    PLAN-020 S5「删除面自动覆盖」的可执行形式：表名字面量一旦回到 admin.py，同批就得改元组 +
    字段清单两处，判据退化成人肉记忆。
    """
    source = (REPO_ROOT / "src/axiom_flow/api/admin.py").read_text(encoding="utf-8")
    hardcoded = [tbl.name for tbl in AF_TABLES if f'"{tbl.name}"' in source]
    assert not hardcoded, f"admin.py 逐字硬编码了表名：{hardcoded}"


def sweep_ok(owned: tuple[str, ...]) -> bool:
    """夹具白名单与 `AF_TABLES` 的同构判据（DROP 序列必须逐项、有序、不多不少）。"""
    return owned == tuple(t.name for t in AF_TABLES) + (ALEMBIC,)


def test_owned_tables_whitelist_is_derived():
    """夹具 DROP 白名单＝`AF_TABLES` 表名 + `alembic_version`，同构判据当场成立。"""
    from tests.support.mysql import OWNED_TABLES

    assert OWNED_TABLES == tuple(t.name for t in AF_TABLES) + (ALEMBIC,)


def test_whitelist_gap_mutation_turns_red(monkeypatch):
    """变异复验（S2「白名单漏一项」）：截短/多出/漏收紧三种漂移都必须使同构判据转红。"""
    from tests.support import mysql

    full = tuple(t.name for t in AF_TABLES) + (ALEMBIC,)
    assert sweep_ok(full) is True
    assert sweep_ok(full[:-1]) is False  # 漏 alembic_version
    assert sweep_ok(tuple(t.name for t in AF_TABLES)) is False  # 漏收紧项
    assert sweep_ok((*full, "qt_books")) is False  # 多一项＝碰别人表
    monkeypatch.setattr(mysql, "OWNED_TABLES", full[:-1])
    assert sweep_ok(mysql.OWNED_TABLES) is False


def test_new_table_without_registration_breaks_every_derived_surface():
    """加表纪律的反向自证：只声明 ORM 表、不登记 `AF_TABLES` 时，三道派生面判据应全部失联。

    这张假表只活在测试进程内（metadata 注入 + 即时 drop），不落任何迁移与磁盘。
    """
    from sqlalchemy import Column, Integer, String

    ghost = Table(
        "af_ghost_mutation_probe",
        Base.metadata,
        Column("book_id", String(100), primary_key=True),
        Column("page_no", Integer, primary_key=True),
    )
    try:
        declared = {
            name for name, tbl in Base.metadata.tables.items() if tbl.name.startswith(PREFIX)
        }
        registered = {tbl.name for tbl in AF_TABLES}
        assert declared - registered == {"af_ghost_mutation_probe"}
        assert sweep_ok(tuple(t.name for t in AF_TABLES) + (ALEMBIC,)) is True  # 白名单未含幽灵 ⇒ 与声明面失联
        Base.metadata.remove(ghost)
        assert {n for n in Base.metadata.tables if n.startswith(PREFIX)} == registered
    finally:
        if "af_ghost_mutation_probe" in Base.metadata.tables:
            Base.metadata.remove(ghost)
