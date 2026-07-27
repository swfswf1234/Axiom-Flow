"""
模块职责：读取 v0.2 本地存储和百炼模型配置。
设计关联（DesignRef）：docs/design/document-pipeline.md
实现状态：Current
关联测试：tests/test_v02_pipeline.py
"""

from pathlib import Path

from pydantic import AliasChoices, Field
from sqlalchemy.engine import URL
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """v0.2 配置描述 MySQL 运行库、本地文件产物和百炼模型。"""

    data_dir: Path = Path("data")
    api_key: str = Field(default="", validation_alias=AliasChoices("AXIOM_API_KEY", "API_KEY"))
    vision_model: str = "qwen-vl-ocr"
    vision_fallback_model: str = "qwen-vl-plus"
    knowledge_model: str = "qwen-plus"
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    model_call_budget: int = 36
    model_timeout_seconds: float = 45.0
    mysql_host: str = Field(default="127.0.0.1", validation_alias=AliasChoices("AXIOM_MYSQL_HOST", "XQFM_MYSQL_HOST"))
    mysql_port: int = Field(default=3306, validation_alias=AliasChoices("AXIOM_MYSQL_PORT", "XQFM_MYSQL_PORT"))
    mysql_database: str = Field(default="xqfm11", validation_alias=AliasChoices("AXIOM_MYSQL_DATABASE", "XQFM_MYSQL_DATABASE"))
    mysql_user: str = Field(default="root", validation_alias=AliasChoices("AXIOM_MYSQL_USER", "XQFM_MYSQL_USER"))
    mysql_password: str = Field(default="", validation_alias=AliasChoices("AXIOM_MYSQL_PASSWORD", "XQFM_MYSQL_PASSWORD"))
    mysql_test_database: str = Field(default="axiom_flow_test", validation_alias="AXIOM_MYSQL_TEST_DATABASE")
    mysql_pool_size: int = 5
    mysql_max_overflow: int = 10

    model_config = SettingsConfigDict(env_file=".env", env_prefix="AXIOM_", extra="ignore", populate_by_name=True)

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"

    @property
    def mysql_url(self) -> str:
        """生成不写入日志的 MySQL URL，避免手工拼接时泄露或转义密码。"""
        return self.mysql_url_for(self.mysql_database)

    def mysql_url_for(self, database: str) -> str:
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            database=database,
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)
