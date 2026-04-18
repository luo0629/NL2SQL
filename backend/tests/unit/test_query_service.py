from app.schemas.query import NLQueryRequest
from app.services.agent_service import AgentService


def test_agent_service_returns_mock_response() -> None:
    service = AgentService()

    response = service.generate_sql(
        NLQueryRequest(question="近 30 天收入最高的客户是谁？")
    )

    assert response.status == "mock"
    assert "SELECT" in response.sql
    assert response.explanation
