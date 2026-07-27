"""
模块职责：定义 v0.2 API 请求与领域返回模型。
设计关联（DesignRef）：docs/design/normalized-content.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py
"""

from typing import Any

from pydantic import BaseModel, Field


class ReviewRequest(BaseModel):
    """人工审阅页面或知识候选时提交的结论。"""

    status: str = Field(pattern="^(accepted|rejected|reparse_requested)$")
    reason: str = ""


class DocumentResponse(BaseModel):
    id: str
    filename: str
    page_count: int
    status: str


class PageResponse(BaseModel):
    id: str
    page_no: int
    markdown: str
    blocks: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    quality: dict[str, Any]
    review_status: str
    image_url: str


class CandidateResponse(BaseModel):
    id: str
    kind: str
    title: str
    content: str
    evidence: list[dict[str, Any]]
    review_status: str


class EdgeResponse(BaseModel):
    id: str
    source_id: str
    target_id: str
    relation: str
    evidence: list[dict[str, Any]]
    review_status: str


class ParseResponse(BaseModel):
    run_id: str
    document_id: str
    status: str
    model_calls: int
