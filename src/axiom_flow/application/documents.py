"""
模块职责：提供文档、解析运行、页面和产物的应用用例。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/integration/test_api.py、tests/system/test_document_release_flow.py
"""

from pathlib import Path
from typing import Any, Protocol

from axiom_flow.application.ports import DocumentPipeline
from axiom_flow.domain.models import FileResource, NotFoundError


class DocumentRepository(Protocol):
    def list_documents(self) -> list[dict[str, Any]]: ...
    def get_document(self, document_id: str) -> dict[str, Any] | None: ...
    def list_parse_runs(self, document_id: str) -> list[dict[str, Any]]: ...
    def get_current_parse_run(self, document_id: str) -> dict[str, Any] | None: ...
    def list_parse_run_selections(self, document_id: str) -> list[dict[str, Any]]: ...
    def select_current_parse_run(
        self, document_id: str, run_id: str, reason: str, data_root: Path,
    ) -> dict[str, Any]: ...
    def list_pages_for_run(self, run_id: str) -> list[dict[str, Any]]: ...
    def get_page_for_run(self, run_id: str, page_no: int) -> dict[str, Any] | None: ...
    def get_artifact_summary(self, run_id: str) -> dict[str, Any]: ...
    def list_page_index(self, run_id: str) -> list[dict[str, Any]]: ...
    def list_artifacts_for_run(self, run_id: str) -> list[dict[str, Any]]: ...
    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None: ...
    def list_pages(self, document_id: str) -> list[dict[str, Any]]: ...
    def get_page(self, page_id: str) -> dict[str, Any] | None: ...


class FileLocator(Protocol):
    def resolve(self, stored_path: str, media_type: str, filename: str | None = None) -> FileResource: ...


class DocumentApplicationService:
    """把文档聚合查询与命令隔离在 HTTP 和 MySQL 之间。"""

    def __init__(
        self, store: DocumentRepository, pipeline: DocumentPipeline,
        files: FileLocator, data_dir: Path,
    ) -> None:
        self.store = store
        self.pipeline = pipeline
        self.files = files
        self.data_dir = data_dir

    def import_pdf(self, source: Path, original_filename: str) -> dict[str, Any]:
        return self.pipeline.import_pdf(source, original_filename)

    def list_documents(self) -> list[dict[str, Any]]:
        return self.store.list_documents()

    def get_document(self, document_id: str) -> dict[str, Any]:
        document = self.store.get_document(document_id)
        if not document:
            raise NotFoundError("文档不存在")
        return document

    def list_parse_runs(self, document_id: str) -> list[dict[str, Any]]:
        self.get_document(document_id)
        return self.store.list_parse_runs(document_id)

    def current_parse_run(self, document_id: str) -> dict[str, Any]:
        self.get_document(document_id)
        run = self.store.get_current_parse_run(document_id)
        if not run:
            raise NotFoundError("尚未选择当前解析运行")
        return {**run, "selection_history": self.store.list_parse_run_selections(document_id)}

    def select_current_parse_run(self, document_id: str, run_id: str, reason: str) -> dict[str, Any]:
        self.get_document(document_id)
        return self.store.select_current_parse_run(document_id, run_id, reason, self.data_dir)

    def list_run_pages(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_pages_for_run(run_id)

    def get_run_page(self, run_id: str, page_no: int) -> dict[str, Any]:
        page = self.store.get_page_for_run(run_id, page_no)
        if not page:
            raise NotFoundError("解析页面不存在")
        return page

    def artifact_summary(self, run_id: str) -> dict[str, Any]:
        return self.store.get_artifact_summary(run_id)

    def page_index(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_page_index(run_id)

    def list_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        return self.store.list_artifacts_for_run(run_id)

    def artifact_file(self, artifact_id: str) -> FileResource:
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise NotFoundError("解析产物不存在")
        return self.files.resolve(artifact["path"], artifact["mime_type"])

    def list_current_pages(self, document_id: str) -> list[dict[str, Any]]:
        self.get_document(document_id)
        return self.store.list_pages(document_id)

    def page_image(self, page_id: str) -> FileResource:
        page = self.store.get_page(page_id)
        if not page:
            raise NotFoundError("页面不存在")
        return self.files.resolve(page["image_path"], "image/png")
