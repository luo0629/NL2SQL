from collections.abc import Generator
from typing import override

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_agent_service
from app.main import app
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService


class StubLLMService(LLMService):
    @override
    def build_chat_model(self) -> None:
        return None


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        llm_service=StubLLMService()
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
