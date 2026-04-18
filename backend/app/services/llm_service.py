from app.config import get_settings


class LLMService:
    def describe_backend_model(self) -> str:
        settings = get_settings()
        return f"provider={settings.llm_provider}, model={settings.llm_model}"
