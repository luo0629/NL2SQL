from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # 应用基础信息
    app_name: str = "NL2SQL Agent API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8787
    frontend_origin: str = "http://localhost:4242"
    # 基础依赖配置
    database_url: str = "sqlite+aiosqlite:///./nl2sql.db"
    redis_url: str = "redis://localhost:6379/0"
    query_result_limit: int = 200
    database_readonly_required: bool = True
    schema_cache_ttl_seconds: int = 300
    llm_request_timeout_seconds: int = 45
    llm_temperature: float = 0.0
    # LLM 配置
    llm_provider: str = "mock"
    llm_model: str = "mock-nl2sql"
    zhipu_api_key: SecretStr | None = None
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # 使用缓存避免重复解析环境变量。
    return Settings()
