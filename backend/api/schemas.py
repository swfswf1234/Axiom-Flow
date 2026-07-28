"""
模块职责：定义 v0.3 HTTP API 的请求、响应和统一错误结构。
设计关联（DesignRef）：docs/architecture/v03-target.md
实现状态：Current
关联测试：tests/test_v03_api.py
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody


class DocumentResponse(BaseModel):
    id: str
    filename: str
    content_hash: str
    page_count: int
    status: str
    created_at: datetime


class ReviewRequest(BaseModel):
    status: Literal["accepted", "rejected", "reparse_requested"]
    reason: str = Field(default="", max_length=4000)


class ParseJobRequest(BaseModel):
    """限定解析任务使用的 PDF 物理页范围，端点均包含。"""

    page_start: int = Field(default=1, ge=1)
    page_end: int | None = Field(default=None, ge=1)


class CurrentParseRunRequest(BaseModel):
    """显式选择文档当前解析运行。"""

    run_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=4000)


class JobResponse(BaseModel):
    id: str
    kind: str
    aggregate_id: str
    input_version: str
    status: str
    progress_current: int
    progress_total: int
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class CommandResponse(BaseModel):
    job: JobResponse
    created: bool


class PageResponse(BaseModel):
    id: str
    run_id: str
    document_id: str
    page_no: int
    markdown: str
    blocks: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    quality: dict[str, Any]
    page_kind: str
    review_status: str
    review_reason: str
    image_url: str | None = None


class ArtifactResponse(BaseModel):
    id: str
    document_id: str
    run_id: str | None
    kind: str
    content_hash: str
    mime_type: str
    size_bytes: int
    metadata: dict[str, Any]
    download_url: str


class KnowledgeNodeResponse(BaseModel):
    id: str
    document_id: str
    kind: str
    title: str
    content: str
    evidence: list[dict[str, Any]]
    review_status: str
    review_reason: str


class KnowledgeEdgeResponse(BaseModel):
    id: str
    document_id: str
    source_id: str
    target_id: str
    relation: str
    evidence: list[dict[str, Any]]
    review_status: str
