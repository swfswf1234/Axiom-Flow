"""
模块职责：集中装配配置、应用用例与基础设施适配器。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
关联测试：tests/test_architecture_dependencies.py、tests/test_v03_api.py
"""

from dataclasses import dataclass

from axiom_flow.application.documents import DocumentApplicationService
from axiom_flow.application.jobs import JobApplicationService, JobPolicy
from axiom_flow.application.ports import ProviderFactory
from axiom_flow.application.reviews import ReviewApplicationService
from axiom_flow.application.workbooks import WorkbookService
from axiom_flow.infrastructure.bailian import BailianProvider
from axiom_flow.infrastructure.config import Settings
from axiom_flow.infrastructure.files import LocalFileLocator
from axiom_flow.infrastructure.mysql import MySQLRepository
from axiom_flow.infrastructure.pdf_pipeline import PDFPipeline
from axiom_flow.infrastructure.workbooks import OpenPyxlWorkbookGateway


@dataclass(slots=True)
class ApplicationContainer:
    """API 与 Worker 共享的进程级依赖集合。"""

    settings: Settings
    documents: DocumentApplicationService
    jobs: JobApplicationService
    reviews: ReviewApplicationService
    workbooks: WorkbookService
    _repository: MySQLRepository

    def start(self) -> None:
        """校验运行 schema，不隐式迁移。"""
        self._repository.require_schema()

    def close(self) -> None:
        self._repository.dispose()


def build_container(
    settings: Settings | None = None,
    provider_factory: ProviderFactory | None = None,
) -> ApplicationContainer:
    """按进程配置创建仓储、pipeline 和应用用例。"""
    resolved = settings or Settings()
    repository = MySQLRepository(
        resolved.mysql_url, resolved.mysql_pool_size, resolved.mysql_max_overflow,
    )

    def providers():
        if provider_factory:
            return provider_factory()
        provider = BailianProvider(resolved)
        return provider, provider

    def pipeline_factory(bound_budget: int | None = None):
        execution_settings = (
            resolved.model_copy(update={"model_call_budget": bound_budget})
            if bound_budget is not None else resolved
        )
        vision, knowledge = providers()
        return PDFPipeline(repository, execution_settings, vision, knowledge)

    policy = JobPolicy(
        vision_model=resolved.vision_model,
        vision_contract_version=resolved.vision_contract_version,
        vision_max_tokens=resolved.vision_max_tokens,
        vision_page_attempts=resolved.vision_page_attempts,
        model_call_budget=resolved.model_call_budget,
        knowledge_model=resolved.knowledge_model,
        worker_lease_seconds=resolved.worker_lease_seconds,
    )
    jobs = JobApplicationService(repository, policy, pipeline_factory)
    files = LocalFileLocator(resolved.data_dir)
    return ApplicationContainer(
        settings=resolved,
        documents=DocumentApplicationService(repository, pipeline_factory(), files, resolved.data_dir),
        jobs=jobs,
        reviews=ReviewApplicationService(repository),
        workbooks=WorkbookService(
            repository, OpenPyxlWorkbookGateway(resolved.data_dir), files,
        ),
        _repository=repository,
    )
