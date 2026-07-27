"""
模块职责：v0.1 PostgreSQL、MinerU 和队列配置。
设计关联（DesignRef）：docs/history/2026-07-mineru-baseline/environment.md
实现状态：Legacy
关联测试：无；当前主链将在新架构重构时替换。
"""

from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # v0.1 的 PostgreSQL 连接配置。
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "axiom_flow"
    pg_user: str = "postgres"
    pg_password: str = ""

    # v0.1 的 MinerU 输出目录。
    mineru_output_dir: str = "data/parsed"

    # v0.1 预留的 Qdrant 配置。
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # v0.1 预留的 Redis 配置。
    redis_host: str = "localhost"
    redis_port: int = 6379

    # v0.1 应用监听配置。
    app_port: int = 8002
    app_host: str = "0.0.0.0"

    @property
    def db_url(self) -> str:
        return f"postgresql+asyncpg://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

    @property
    def db_url_sync(self) -> str:
        return f"postgresql+psycopg2://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parent.parent.parent

    @property
    def parsed_dir(self) -> Path:
        return self.project_root / self.mineru_output_dir

    class Config:
        env_file = ".env"
        env_prefix = "AXIOM_"
