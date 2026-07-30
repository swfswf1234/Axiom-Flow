"""
模块职责：定义 v0.3 跨层共享的状态、任务资源和领域错误。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
关联测试：tests/integration/test_jobs.py
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobKind(StrEnum):
    """可由持久 Worker 执行的任务类型。"""

    PARSE_DOCUMENT = "parse_document"
    EXTRACT_KNOWLEDGE = "extract_knowledge"


class JobStatus(StrEnum):
    """后台任务状态。"""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = {JobStatus.QUEUED.value, JobStatus.RUNNING.value, JobStatus.CANCEL_REQUESTED.value}


class ReviewStatus(StrEnum):
    """人工审阅可提交的状态。"""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REPARSE_REQUESTED = "reparse_requested"


class DocumentStatus(StrEnum):
    """文档聚合根状态。"""

    IMPORTED = "imported"
    PARSING = "parsing"
    NEEDS_REVIEW = "needs_review"
    KNOWLEDGE_REVIEW = "knowledge_review"
    PUBLISHED = "published"
    FAILED = "failed"


class ParseRunStatus(StrEnum):
    PARSING = "parsing"
    PARSED = "parsed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExtractionRunStatus(StrEnum):
    EXTRACTING = "extracting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DomainError(Exception):
    """可安全翻译为客户端错误的领域异常。"""

    code = "domain_error"


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class PermanentJobError(DomainError):
    """重试不能修复的任务错误。"""

    code = "permanent_job_error"


class JobCancelled(DomainError):
    code = "job_cancelled"


class RetryableJobError(DomainError):
    """基础设施暂时失败，允许任务按既定 attempt 策略重试。"""

    code = "retryable_job_error"


@dataclass(frozen=True, slots=True)
class FileResource:
    """应用层交给传输层的受约束本地文件。"""

    path: Path
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class JobResource:
    """应用层返回给 API 和 Worker 的稳定任务视图。"""

    id: str
    kind: JobKind
    aggregate_id: str
    input_version: str
    status: JobStatus
    progress_current: int
    progress_total: int
    attempt: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
