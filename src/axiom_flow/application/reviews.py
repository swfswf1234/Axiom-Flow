"""
模块职责：提供页面、知识节点和知识关系的人工审阅用例。
设计关联（DesignRef）：docs/design/web-workbench.md
实现状态：Current
关联测试：tests/test_document_workflow.py、tests/test_v03_api.py
"""

from typing import Any, Protocol

from axiom_flow.domain.models import NotFoundError


class ReviewRepository(Protocol):
    def get_document(self, document_id: str) -> dict[str, Any] | None: ...
    def get_page(self, page_id: str) -> dict[str, Any] | None: ...
    def review_page(self, page_id: str, status: str, reason: str) -> None: ...
    def list_candidates(self, document_id: str) -> list[dict[str, Any]]: ...
    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None: ...
    def review_candidate(self, candidate_id: str, status: str, reason: str) -> None: ...
    def list_edges(self, document_id: str) -> list[dict[str, Any]]: ...
    def get_edge(self, edge_id: str) -> dict[str, Any] | None: ...
    def review_edge(self, edge_id: str, status: str, reason: str = "") -> None: ...


class ReviewApplicationService:
    def __init__(self, store: ReviewRepository) -> None:
        self.store = store

    def review_page(self, page_id: str, status: str, reason: str) -> dict[str, Any]:
        if not self.store.get_page(page_id):
            raise NotFoundError("页面不存在")
        self.store.review_page(page_id, status, reason)
        page = self.store.get_page(page_id)
        if not page:
            raise NotFoundError("页面不存在")
        return page

    def list_nodes(self, document_id: str) -> list[dict[str, Any]]:
        self._document(document_id)
        return self.store.list_candidates(document_id)

    def review_node(self, node_id: str, status: str, reason: str) -> dict[str, Any]:
        if not self.store.get_candidate(node_id):
            raise NotFoundError("知识节点不存在")
        self.store.review_candidate(node_id, status, reason)
        node = self.store.get_candidate(node_id)
        if not node:
            raise NotFoundError("知识节点不存在")
        return node

    def list_edges(self, document_id: str) -> list[dict[str, Any]]:
        self._document(document_id)
        return self.store.list_edges(document_id)

    def review_edge(self, edge_id: str, status: str, reason: str) -> dict[str, Any]:
        if not self.store.get_edge(edge_id):
            raise NotFoundError("知识关系不存在")
        self.store.review_edge(edge_id, status, reason)
        edge = self.store.get_edge(edge_id)
        if not edge:
            raise NotFoundError("知识关系不存在")
        return edge

    def _document(self, document_id: str) -> None:
        if not self.store.get_document(document_id):
            raise NotFoundError("文档不存在")
