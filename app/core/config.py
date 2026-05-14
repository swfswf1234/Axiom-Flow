from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    pg_host: str = "localhost"
    pg_port: int = 5432
    pg_database: str = "axiom_flow"
    pg_user: str = "postgres"
    pg_password: str = ""

    # MinerU
    mineru_output_dir: str = "data/parsed"

    # Qdrant (Phase 2)
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333

    # Redis (Phase 3)
    redis_host: str = "localhost"
    redis_port: int = 6379

    # App
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
