from app.config import get_settings
from langchain_openai import ChatOpenAI


class LLMService:
    def build_chat_model(self) -> ChatOpenAI | None:
        settings = get_settings()

        if settings.llm_provider != "zhipu":
            return None

        if not settings.zhipu_api_key:
            return None

        return ChatOpenAI(
            api_key=settings.zhipu_api_key,
            base_url=settings.zhipu_base_url,
            model=settings.llm_model,
        )

    def describe_backend_model(self) -> str:
        settings = get_settings()
        details = [
            f"provider={settings.llm_provider}",
            f"model={settings.llm_model}",
        ]

        if settings.llm_provider == "zhipu":
            details.append(f"base_url={settings.zhipu_base_url}")

        return ", ".join(details)
