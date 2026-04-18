from typing import override

from app.schemas.query import NLQueryRequest
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService


class StubLLMService(LLMService):
    @override
    def build_chat_model(self) -> None:
        return None


def test_agent_service_returns_mock_response() -> None:
    service = AgentService(llm_service=StubLLMService())

    response = service.generate_sql(
        NLQueryRequest(question="近 30 天收入最高的客户是谁？")
    )

    assert response.status == "mock"
    assert "SELECT" in response.sql
    assert "DROP" not in response.sql
    assert response.explanation
