"""
模块职责：非公开管理面路由——af_* 表族行与 `QED_DATA_ROOT/parsed/` 产物的一键清除（先清后建）。
设计关联（DesignRef）：docs/architecture/api.md（「非公开管理面」节）、docs/design/parsing-management.md
实现状态：Current（PLAN-010 / ARCH-024）
关联测试：tests/contract/test_api_v1_contract.py（TestAdminPurgeEndpoint）
守护面：作用域守卫、双确认、fail-closed 挂载
失效后果：误调即不可回滚地删掉解析产物与库行，或管理面在无人知情时被暴露

路由内依赖**不用** `from __future__ import annotations`：会话依赖是闭包里的 `Depends(db_session)`，
字符串化注解会让 FastAPI 解析不到它，退化成 `session` 查询参数（422）。
"""

import hmac
import logging
from collections.abc import Sequence
from pathlib import Path
from shutil import rmtree
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from axiom_flow.config import Settings
from axiom_flow.db.models import (
    AF_TABLES,
    AfBlockEdit,
    AfBook,
    AfPage,
    AfPageRegion,
    AfParseJob,
)
from axiom_flow.db.repositories import (
    BookRepository,
    EditRepository,
    PageRepository,
    ParseJobRepository,
    RegionRepository,
)
from axiom_flow.ingest import warn_transient_residue
from axiom_flow.schemas import AdminPurgeCounts, AdminPurgeIn, AdminPurgeOut, AdminPurgeScope

log = logging.getLogger(__name__)

PURGE_URL = "/api/v1/admin/purge"
PURGE_CONFIRM = "PURGE"
ADMIN_TOKEN_HEADER = "X-QED-Admin-Token"
# 「在飞」的唯一判据 = ADR 0007 的两个活动态；终态（含 interrupted/partial）正是待清理对象。
ACTIVE_JOB_STATUSES = ("queued", "running")
# 书目录最深 = `parsed/<domain>/<course>/<book>`；更深的层属于版本目录，不遍历。
_BOOK_DIR_MAX_DEPTH = 3
# 删除面覆盖的 ORM 模型元组：判据守护要求其集合 ≡ `AF_TABLES`（加表只改这一处 + 仓储分支）。
# 标识符刻意不带张数——带张数就是把「表族有几张」写进名字，加一张表即全仓改名。
_AF_TABLE_MODELS = (AfBook, AfParseJob, AfPage, AfBlockEdit, AfPageRegion)
#: 行数计数字段名 = 表名（唯一口径来自 `AF_TABLES`，不在本文件逐字写死表名）。
_ROW_COUNT_FIELDS = tuple(tbl.name for tbl in AF_TABLES)


class PurgeScopeError(ValueError):
    """作用域越界或冲突 ⇒ 整请求拒绝、一个都不删（半删状态最难排查）。"""


def _parsed_root(data_root: Path) -> Path:
    return data_root / "parsed"


def _relative(parsed_root: Path, target: Path) -> str:
    """响应只给**相对 parsed/** 的 POSIX 风格路径：绝对磁盘位置（盘符、用户目录）绝不外泄。"""
    return f"parsed/{target.relative_to(parsed_root.resolve()).as_posix()}"


def _checked_dir(parsed_root: Path, candidate: Path) -> Path:
    """磁盘目标必须落在 `parsed/` 之内（不信任库里的历史脏值：`domain_id='..'` 等）。"""
    root = parsed_root.resolve()
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise PurgeScopeError(f"清除目标越出 parsed/ 作用域：{candidate.name}")
    return resolved


def is_book_dir(candidate: Path) -> bool:
    """书目录的识别只看产物标记，不看层数：`book.json` / `pages/` / `versions/` 任一即在。"""
    return (candidate / "book.json").is_file() or any(
        (candidate / name).is_dir() for name in ("pages", "versions")
    )


def iter_book_dirs(parsed_root: Path) -> list[Path]:
    """深度无关地枚举书目录——**不能**按固定层数 glob。

    书目录的层数随 `domain_id`/`course_id` 是否为空而变：空段在 pathlib 拼接时被折叠，业务库 17 本
    全落在 `parsed/<course_id>/<book_id>/`（两层）。按三层取会把 `pages/`、`versions/` 当成书目录，
    清除后 `book.json`/`manifest.json` 与空目录残留、重跑幂等不成立（判例：2026-09-23 业务侧首次
    dry-run）。命中即剪枝，不下钻进版本目录。清除侧与读取侧（`api/main.py` 书目枚举回退）
    共用本判据，防止两侧各自实现再次漂移。
    """
    found: list[Path] = []

    def walk(current: Path, depth: int) -> None:
        for child in sorted(p for p in current.iterdir() if p.is_dir()):
            if is_book_dir(child):
                found.append(child)
            elif depth < _BOOK_DIR_MAX_DEPTH:
                walk(child, depth + 1)

    if parsed_root.is_dir():
        walk(parsed_root, 1)
    return found


def _book_dirs_for_book(data_root: Path, book_id: str) -> list[Path]:
    parsed_root = _parsed_root(data_root)
    # book_id 已由 schema 判为安全路径段，目录名精确相等不可能穿越；这里再守一次解析结果。
    return [
        _checked_dir(parsed_root, found)
        for found in iter_book_dirs(parsed_root)
        if found.name == book_id
    ]


def _all_book_dirs(data_root: Path) -> list[Path]:
    parsed_root = _parsed_root(data_root)
    return [_checked_dir(parsed_root, found) for found in iter_book_dirs(parsed_root)]


def _planned_counts(session: Session, book_ids: Sequence[str] | None) -> AdminPurgeCounts:
    """演练态的「将删计数」：与真删同一套作用域条件，否则清单数字不可信。"""
    counts = AdminPurgeCounts()
    for model in _AF_TABLE_MODELS:
        stmt = select(func.count()).select_from(model)
        if book_ids is not None:
            stmt = stmt.where(model.book_id.in_(list(book_ids)))
        setattr(counts, model.__table__.name, int(session.scalar(stmt) or 0))
    return counts


def _merge_counts(target: AdminPurgeCounts, add: AdminPurgeCounts) -> None:
    """把一次删除的行数并进总账；字段名按 `AF_TABLES` 派生，加表不必改本函数。"""
    for name in _ROW_COUNT_FIELDS:
        setattr(target, name, getattr(target, name) + getattr(add, name))
    target.directories += add.directories


def _delete_rows_for_book(session: Session, book_id: str) -> AdminPurgeCounts:
    counts = AdminPurgeCounts()
    counts.af_books = BookRepository(session).delete_for_book(book_id)
    counts.af_parse_jobs = ParseJobRepository(session).delete_for_book(book_id)
    counts.af_pages = PageRepository(session).delete_for_book(book_id)
    counts.af_block_edits = EditRepository(session).delete_for_book(book_id)
    counts.af_page_regions = RegionRepository(session).delete_for_book(book_id)
    return counts


def _delete_all_rows(session: Session) -> AdminPurgeCounts:
    counts = AdminPurgeCounts()
    counts.af_books = BookRepository(session).delete_all()
    counts.af_parse_jobs = ParseJobRepository(session).delete_all()
    counts.af_pages = PageRepository(session).delete_all()
    counts.af_block_edits = EditRepository(session).delete_all()
    counts.af_page_regions = RegionRepository(session).delete_all()
    return counts


def resolve_scope(scope: AdminPurgeScope) -> tuple[bool, list[str]]:
    """`all` 与 `book_ids` 互斥且必填，返回 `(是否全项目, 书目列表)`；违规抛 `PurgeScopeError`。"""
    use_all = scope.all is True
    book_ids = list(scope.book_ids or [])
    if use_all and book_ids:
        raise PurgeScopeError("scope.all 与 scope.book_ids 互斥：一次清除只声明一种作用域")
    if not use_all and not book_ids:
        raise PurgeScopeError("作用域必填：scope.all=true 或至少一个 scope.book_ids（不给默认，避免误判成全删）")
    return use_all, book_ids


def _assert_rows_in_scope(session: Session, data_root: Path, book_ids: Sequence[str] | None) -> None:
    """每条在作用域内的书目行，其**落点路径**都必须在 `parsed/` 内——目录不存在也要判。

    只校验磁盘上看得见的路径会留洞：`domain_id='..'` 的书目在 glob 结果里根本不会出现，
    全项目扫尾照样会把它的行删掉。脏值必须在任何删除之前整请求拒掉。
    """
    parsed_root = _parsed_root(data_root)
    for book in BookRepository(session).list_books():
        if book_ids is not None and book.book_id not in book_ids:
            continue
        _checked_dir(parsed_root, parsed_root / book.domain_id / book.course_id / book.book_id)


def assert_purge_scope(
    *, session: Session, data_root: Path, scope: AdminPurgeScope
) -> tuple[bool, list[str]]:
    """删除前的作用域校验（形状 + 书目落点），返回 `(是否全项目, 书目列表)`。

    路由必须在 409（在飞任务）判定**之前**调用它：脏落点是配置问题，报成「等解析跑完」会把
    运维引向一条永远走不通的恢复路径。
    """
    use_all, book_ids = resolve_scope(scope)
    _assert_rows_in_scope(session, data_root, None if use_all else book_ids)
    return use_all, book_ids


def purge_books(
    *, session: Session, data_root: Path, scope: AdminPurgeScope, dry_run: bool
) -> AdminPurgeOut:
    """执行一次清除（`dry_run=True` 只枚举）。先磁盘后库，任一步失败只记 `failures`，不假装成功。"""
    use_all, book_ids = assert_purge_scope(session=session, data_root=data_root, scope=scope)
    parsed_root = _parsed_root(data_root)
    if use_all:
        dirs = _all_book_dirs(data_root)
    else:
        dirs = [found for book_id in book_ids for found in _book_dirs_for_book(data_root, book_id)]
    targets = sorted({_relative(parsed_root, directory) for directory in dirs})
    # 瞬态残留只计数告警（Q3 裁决）：真删随 rmtree 一并消失，dry_run 留证供停手判断。
    warn_transient_residue(parsed_root, context="管理面清除瞬态残留核对")

    if dry_run:
        counts = _planned_counts(session, None if use_all else book_ids)
        counts.directories = len(dirs)
        return AdminPurgeOut(
            scope=scope, dry_run=True, targets=targets, deleted=counts, failures=[]
        )

    counts = AdminPurgeCounts()
    failures: list[str] = []
    blocked: set[str] = set()
    for directory in dirs:
        relative = _relative(parsed_root, directory)
        try:
            rmtree(directory)
        except OSError as exc:
            # 异常文本常含绝对路径，绝不外泄；完整堆栈只进服务日志。
            log.warning("管理面清除：产物目录删除失败 %s（%s）", relative, exc)
            failures.append(f"产物目录删除失败：{relative}（保留其库行，可重跑）")
            blocked.add(directory.name)
            continue
        counts.directories += 1

    if use_all:
        if failures:
            failures.append("全项目清除未完成：存在失败项，未执行全表扫尾（残行由下一次清除收掉）")
        else:
            _merge_counts(counts, _delete_all_rows(session))
    else:
        for book_id in book_ids:
            if book_id in blocked:
                continue
            try:
                deleted = _delete_rows_for_book(session, book_id)
            except SQLAlchemyError:  # 已删目录不回滚；残行幂等可重收
                log.exception("管理面清除：库行删除失败 %s", book_id)
                failures.append(f"库行清除失败：{book_id}（产物目录已删，重跑本清除即可收掉残行）")
                continue
            _merge_counts(counts, deleted)
    session.commit()
    return AdminPurgeOut(
        scope=scope, dry_run=False, targets=targets, deleted=counts, failures=failures
    )


def build_router(*, settings: Settings, session_factory: sessionmaker[Session]) -> APIRouter:
    """管理面路由：整条前缀挂一个 token 依赖，会话按请求开、异常回滚（与 ②③ 类同一写法）。"""
    router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

    def require_token(
        token: Annotated[str | None, Header(alias=ADMIN_TOKEN_HEADER)] = None,
    ) -> None:
        """缺失与错值同为 401：不区分「没带头」和「头不对」，免得探针据此猜 token 形态。"""
        if not hmac.compare_digest(token or "", settings.admin_token):
            raise HTTPException(status_code=401, detail="管理 token 不匹配")

    def db_session():
        with session_factory() as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    @router.post("/purge", response_model=AdminPurgeOut, dependencies=[Depends(require_token)])
    def purge(
        payload: AdminPurgeIn, session: Annotated[Session, Depends(db_session)]
    ) -> AdminPurgeOut:
        try:
            if not payload.dry_run and payload.confirm != PURGE_CONFIRM:
                raise HTTPException(
                    status_code=400, detail=f"真删必须逐字确认：confirm 需为 {PURGE_CONFIRM}"
                )
            use_all, book_ids = assert_purge_scope(
                session=session, data_root=Path(settings.data_dir), scope=payload.scope
            )
            if not payload.dry_run:
                in_flight = ParseJobRepository(session).count_active(
                    None if use_all else book_ids
                )
                if in_flight:
                    raise HTTPException(
                        status_code=409,
                        detail=f"作用域内有 {in_flight} 个 queued/running 任务，拒绝边解析边清除",
                    )
            return purge_books(
                session=session,
                data_root=Path(settings.data_dir),
                scope=payload.scope,
                dry_run=payload.dry_run,
            )
        except PurgeScopeError as exc:
            # 抛在任何一个删除动作之前；文本只含路径段名，不含绝对路径与凭据。
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router


def mount_admin(
    app: FastAPI, *, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    """装配管理面：**默认不挂载**（路由不进 app ⇒ 请求 404，不返 405/403 泄漏存在性）。

    启用与凭据缺失的组合在**这里**判（而不是运行时逐请求判）：`QED_ADMIN_ENABLED=1` 而无
    `QED_ADMIN_TOKEN` 属配置错误，静默放行等于开一个无凭据的删除接口。
    """
    settings.require_admin_configured()
    if not settings.admin_enabled:
        return
    app.include_router(build_router(settings=settings, session_factory=session_factory))
    log.warning("非公开管理面已挂载：%s（需 X-QED-Admin-Token）", PURGE_URL)
