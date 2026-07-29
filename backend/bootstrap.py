"""
模块职责：集中装配配置、应用用例与基础设施适配器。
设计关联（DesignRef）：docs/architecture/runtime-architecture.md
实现状态：Current
关联测试：tests/test_architecture_dependencies.py、tests/test_v03_api.py
"""

from dataclasses import dataclass

from backend.application.jobs import JobApplicationService, JobPolicy
from backend.application.ports import ProviderFactory
from backend.application.workbooks import WorkbookService
from backend.infrastructure.bailian import BailianProvider
from backend.infrastructure.config import Settings
from backend.infrastructure.mysql import MySQLRepository
from backend.infrastructure.pdf_pipeline import PDFPipeline


@dataclass(slots=True)
class ApplicationContainer:
    """API 与 Worker 共享的进程级依赖集合。"""

    settings: Settings
    repository: MySQLRepository
    jobs: JobApplicationService
    import_pipeline: PDFPipeline
    workbooks: WorkbookService


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
    return ApplicationContainer(
        settings=resolved,
        repository=repository,
        jobs=jobs,
        import_pipeline=pipeline_factory(),
        workbooks=WorkbookService(repository, resolved.data_dir),
    )
