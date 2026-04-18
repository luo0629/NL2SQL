from app.config import Settings, get_settings
from app.services.agent_service import AgentService


def get_app_settings() -> Settings:
    return get_settings()


def get_agent_service() -> AgentService:
    return AgentService()
