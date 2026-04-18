from functools import lru_cache
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NL2SQL Agent API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8787
    frontend_origin: str = "http://localhost:4242"
    database_url: str = "sqlite+aiosqlite:///./nl2sql.db"
    redis_url: str = "redis://localhost:6379/0"
    llm_provider: str = "mock"
    llm_model: str = "mock-nl2sql"

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
