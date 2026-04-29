from app.config import get_settings
from langchain_openai import ChatOpenAI


class LLMService:
    def build_chat_model(self) -> ChatOpenAI | None:
        # 统一从配置读取当前模型后端信息。
        settings = get_settings()

        # 非 zhipu 提供方直接视为“模型不可用”，交给上游走 fallback。
        if settings.llm_provider != "zhipu":
            return None

        # 未配置密钥时不初始化模型，避免运行时硬错误。
        if not settings.zhipu_api_key:
            return None

        # 通过 OpenAI 兼容接口访问 Zhipu。
        # temperature=0 保证相同输入产生相同输出，提高 SQL 生成一致性。
        return ChatOpenAI(
            api_key=settings.zhipu_api_key,
            base_url=settings.zhipu_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            request_timeout=settings.llm_request_timeout_seconds,
        )

    def describe_backend_model(self) -> str:
        # 便于在日志/调试信息中快速查看当前模型配置。
        settings = get_settings()
        details = [
            f"provider={settings.llm_provider}",
            f"model={settings.llm_model}",
        ]

        if settings.llm_provider == "zhipu":
            details.append(f"base_url={settings.zhipu_base_url}")

        return ", ".join(details)
