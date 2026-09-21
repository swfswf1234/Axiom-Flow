"""MySQL 集成测试的隔离边界（[ADR 0005](../../docs/adr/0005-mysql-integration-test-isolation.md) 执行面）。

三条硬约束，缺一条即失去在本机共享 MySQL 上跑集成测试的资格：

1. **只连本机、只用测试库**：`QED_TEST_MYSQL=1` 显式 opt-in，库名固定 `qed_test`，
   连接后读 `SELECT DATABASE()` 硬校验，不符直接 `pytest.exit`（不是 skip）——误连业务库
   必须终止整个会话而不是标记跳过；
2. **只拥有本仓库自有的表**：夹具只 DROP/CREATE `af_*` 四表与 `alembic_version`，
   **绝不 DROP DATABASE**（`qed_test` 由根仓库用作 `qed` 的镜像，2026-09-21 用户裁决复用该库）；
3. **缺 opt-in 时 skip**：默认门禁零外部依赖（`tests/conftest.py` 的回环守卫与隔离铁律不变）。

凭据只经 `QED_DB_*` 读取，任何异常与日志文本都不得带出密码。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from axiom_flow.config import Settings, load_settings
from axiom_flow.db.models import AF_TABLES

TEST_SCHEMA = "qed_test"
OPT_IN_ENV = "QED_TEST_MYSQL"
ROOT = Path(__file__).resolve().parents[2]
#: 夹具可 DROP/CREATE 的全部表名——`qed_test` 内其他表（如根仓库镜像的 `qt_*`）一律不碰。
OWNED_TABLES: tuple[str, ...] = tuple(table.name for table in AF_TABLES) + ("alembic_version",)


def _test_settings() -> Settings:
    """业务库名固定替换为测试库：连接参数自行构造，不复用运行库的 `QED_DB_NAME`。"""
    return load_settings(db_name=TEST_SCHEMA)


def _url(settings: Settings, database: str | None) -> str:
    return URL.create(
        "mysql+pymysql",
        host=settings.db_host,
        port=settings.db_port,
        username=settings.db_user,
        password=settings.db_password,
        database=database,
        query={"charset": "utf8mb4"},
    ).render_as_string(hide_password=False)


def _engine(settings: Settings, database: str | None = TEST_SCHEMA) -> Engine:
    return create_engine(_url(settings, database), poolclass=NullPool, future=True)


def _connection_hint(exc: BaseException, settings: Settings) -> str:
    """把连接故障翻译成「环境问题还是遗漏开关」的判别提示，且不回显 DSN（含密码）。"""
    return (
        f"连不上本机 MySQL {settings.db_host}:{settings.db_port}："
        f"{type(exc).__name__}: {exc}\n"
        f"已设置 {OPT_IN_ENV}=1，说明这是环境/凭据问题而非忘记开关（忘设开关时本层会 skip）。"
        "请确认 mysqld 在线且 `.env` 的 QED_DB_* 可用（ADR 0005 决定 1）。"
    )


def _assert_test_schema(store: Engine) -> str:
    with store.connect() as conn:
        actual = conn.execute(text("SELECT DATABASE()")).scalar()
    if actual != TEST_SCHEMA:
        pytest.exit(
            f"集成测试连接的实际库为 {actual!r}，必须是 {TEST_SCHEMA!r}——"
            "疑似误连业务库，按 ADR 0005 决定 2 终止整个会话。",
            returncode=1,
        )
    return str(actual)


def prepare_mysql_settings() -> Settings:
    """session 级入口：opt-in 判定 → 确保测试库存在 → 硬校验库名。

    只 `CREATE DATABASE IF NOT EXISTS`，永不 `DROP DATABASE`：测试库可能被别的仓库共用。
    """
    if os.environ.get(OPT_IN_ENV) != "1":
        pytest.skip(f"默认门禁不连 MySQL：需要时设 {OPT_IN_ENV}=1（ADR 0005 决定 1/4）")
    settings = _test_settings()
    if not settings.db_password:
        pytest.fail(f"{OPT_IN_ENV}=1 但 QED_DB_PASSWORD 为空：集成测试无法连接本机 MySQL")
    try:
        server = _engine(settings, None)
        with server.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{TEST_SCHEMA}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
            conn.commit()
        server.dispose()
        probe = _engine(settings)
        try:
            _assert_test_schema(probe)
        finally:
            probe.dispose()
    except (OSError, ImportError) as exc:  # pymysql 的连接错误都继承 OSError
        pytest.fail(_connection_hint(exc, settings))
    return settings


def _reset_owned_tables(store: Engine) -> None:
    """删除并留给 Alembic 重建的**只有**本仓库自有表。"""
    with store.connect() as conn:
        for table in OWNED_TABLES:
            assert table.startswith("af_") or table == "alembic_version", table
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
        conn.commit()


def _upgrade_head(settings: Settings) -> None:
    """以 `AXIOM_DB_URL` 指向测试库跑 Alembic（env 优先级高于 ini，且不经过 configparser 插值）。"""
    config = AlembicConfig(str(ROOT / "alembic.ini"))
    previous = os.environ.get("AXIOM_DB_URL")
    os.environ["AXIOM_DB_URL"] = _url(settings, TEST_SCHEMA)
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("AXIOM_DB_URL", None)
        else:
            os.environ["AXIOM_DB_URL"] = previous


@contextmanager
def isolated_mysql_store(settings: Settings) -> Iterator[Engine]:
    """每项数据库测试：清空自有表 → 升级到 head → 交还 engine → 再清空自有表。

    交给用例的是 **engine** 而非 session：双连接隔离（ADR 0005 背景 3）要求写/读各建自己的
    `sessionmaker`，共享同一 engine 的两条连接才具备真实事务隔离。
    """
    store = _engine(settings)
    try:
        _assert_test_schema(store)
        _reset_owned_tables(store)
        _upgrade_head(settings)
        yield store
    finally:
        try:
            _reset_owned_tables(store)
        finally:
            store.dispose()
