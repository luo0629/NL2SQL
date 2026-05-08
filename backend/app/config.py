from functools import lru_cache
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import Field, SecretStr, field_validator
from sqlalchemy.engine import make_url
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # 应用基础信息
    app_name: str = "NL2SQL Agent API"
    app_env: str = "development"
    app_host: str = "127.0.0.1"
    app_port: int = 8787
    frontend_origin: str = "http://localhost:4242"
    # 基础依赖配置
    database_url: str = "sqlite+aiosqlite:///./nl2sql.db"
    database_names: Annotated[list[str], NoDecode] = Field(default_factory=list)
    schema_include_tables: Annotated[list[str], NoDecode] = Field(default_factory=list)
    redis_url: str = "redis://localhost:6379/0"
    query_result_limit: int = 200
    database_readonly_required: bool = True
    schema_cache_ttl_seconds: int = 300
    schema_sync_timeout_seconds: float = 8.0
    business_semantic_yaml_enabled: bool = False
    business_semantic_yaml_dir: str = str(PROJECT_ROOT / "yaml")
    business_semantic_override_path: str | None = None
    llm_request_timeout_seconds: int = 45
    agent_llm_node_timeout_seconds: float = 12.0
    result_formatter_llm_timeout_seconds: float = 6.0
    sql_explain_timeout_seconds: float = 8.0
    query_execution_timeout_seconds: float = 25.0
    agent_request_timeout_seconds: float = 55.0
    llm_temperature: float = 0.0
    # LLM 配置
    llm_provider: str = "mock"
    llm_model: str = "mock-nl2sql"
    zhipu_api_key: SecretStr | None = None
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    xiaomi_api_key: SecretStr | None = None
    xiaomi_base_url: str = "https://api.xiaomimimo.com/v1"

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("database_names", "schema_include_tables", mode="before")
    @classmethod
    def _parse_comma_separated_list(cls, value: object) -> list[str]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @property
    def effective_database_names(self) -> list[str]:
        if self.database_names:
            return list(dict.fromkeys(self.database_names))
        try:
            database = make_url(self.database_url).database
        except Exception:
            database = None
        return [database] if database else []

    @property
    def effective_schema_include_tables(self) -> list[str]:
        seen: set[str] = set()
        tables: list[str] = []
        for table in self.schema_include_tables:
            normalized = table.strip().replace("`", "")
            if not normalized:
                continue
            dedupe_key = normalized.casefold()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            tables.append(normalized)
        return tables

    @property
    def schema_scope_key(self) -> str:
        database_scope = ",".join(self.effective_database_names)
        table_scope = ",".join(self.effective_schema_include_tables)
        try:
            url = make_url(self.database_url)
        except Exception:
            base_url = self.database_url
        else:
            driver_name = url.drivername.lower()
            if self.database_names and ("mysql" in driver_name or "mariadb" in driver_name):
                url = url._replace(database=None)
            base_url = url.render_as_string(hide_password=True)
        return f"{base_url}|databases={database_scope}|tables={table_scope}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # 使用缓存避免重复解析环境变量。
    return Settings()
