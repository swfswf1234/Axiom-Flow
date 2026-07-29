"""
模块职责：编排工作簿草稿、校验快照和显式知识发布。
设计关联（DesignRef）：docs/design/excel-release-workflow.md
实现状态：Current
关联测试：tests/test_document_workflow.py
"""

from pathlib import Path
from typing import Any, Protocol

from axiom_flow.application.documents import FileLocator
from axiom_flow.domain.models import FileResource, NotFoundError


class WorkbookRepository(Protocol):
    def get_document(self, document_id: str) -> dict[str, Any] | None: ...
    def accepted_snapshot(self, document_id: str) -> dict[str, Any]: ...
    def create_workbook_revision(
        self, document_id: str, path: Path, snapshot: dict[str, Any], status: str = "draft",
    ) -> dict[str, Any]: ...
    def latest_workbook_revision(self, document_id: str) -> dict[str, Any] | None: ...
    def create_release(self, document_id: str, revision_id: str, snapshot: dict[str, Any]) -> dict[str, Any]: ...
    def latest_release(self, document_id: str) -> dict[str, Any] | None: ...
    def update_document_status(self, document_id: str, status: str) -> None: ...


class WorkbookGateway(Protocol):
    def export(self, document_id: str, snapshot: dict[str, Any]) -> Path: ...
    def import_file(self, document_id: str, path: Path) -> tuple[Path, dict[str, Any]]: ...


class WorkbookService:
    """工作簿只是人工编辑入口，发布必须创建新的 MySQL 快照。"""

    def __init__(
        self, store: WorkbookRepository, gateway: WorkbookGateway, files: FileLocator,
    ) -> None:
        self.store = store
        self.gateway = gateway
        self.files = files

    def export_draft(self, document_id: str) -> dict[str, Any]:
        self._document(document_id)
        snapshot = self.store.accepted_snapshot(document_id)
        path = self.gateway.export(document_id, snapshot)
        return self.store.create_workbook_revision(document_id, path, snapshot)

    def import_draft(self, document_id: str, path: Path) -> dict[str, Any]:
        self._document(document_id)
        stored, snapshot = self.gateway.import_file(document_id, path)
        return self.store.create_workbook_revision(document_id, stored, snapshot)

    def download(self, document_id: str) -> FileResource:
        self._document(document_id)
        revision = self.store.latest_workbook_revision(document_id)
        if not revision:
            raise NotFoundError("尚无工作簿草稿")
        return self.files.resolve(revision["path"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "axiom-flow-draft.xlsx")

    def publish_latest(self, document_id: str) -> dict[str, Any]:
        self._document(document_id)
        revision = self.store.latest_workbook_revision(document_id)
        if not revision:
            raise ValueError("没有可发布的工作簿草稿")
        nodes = revision["snapshot"]["nodes"]
        if not nodes:
            raise ValueError("工作簿没有可发布的知识节点")
        if any(node["review_status"] != "accepted" for node in nodes):
            raise ValueError("工作簿包含未接受的知识节点")
        node_ids = {node["id"] for node in nodes}
        edges = revision["snapshot"]["edges"]
        if any(edge["review_status"] != "accepted" for edge in edges):
            raise ValueError("工作簿包含未接受的知识关系")
        if any(edge["source_id"] not in node_ids or edge["target_id"] not in node_ids for edge in edges):
            raise ValueError("工作簿关系引用了不存在的节点")
        release = self.store.create_release(document_id, revision["id"], revision["snapshot"])
        self.store.update_document_status(document_id, "published")
        return release

    def graph(self, document_id: str) -> dict[str, Any]:
        self._document(document_id)
        release = self.store.latest_release(document_id)
        if not release:
            raise NotFoundError("尚未发布知识图谱")
        return {"release_id": release["id"], **release["snapshot"]}

    def _document(self, document_id: str) -> None:
        if not self.store.get_document(document_id):
            raise NotFoundError("文档不存在")
