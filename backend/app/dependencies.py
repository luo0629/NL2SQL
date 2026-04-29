from app.config import Settings, get_settings
from app.database.executor import SQLExecutor
from app.services.agent_service import AgentService


def get_app_settings() -> Settings:
    # FastAPI 依赖：统一获取应用配置。
    return get_settings()


def get_sql_executor() -> SQLExecutor:
    # FastAPI 依赖：提供 SQLExecutor 实例。
    return SQLExecutor()


def get_agent_service() -> AgentService:
    # FastAPI 依赖：提供 AgentService 实例。
    return AgentService(sql_executor=get_sql_executor())
