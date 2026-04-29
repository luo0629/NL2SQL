from collections.abc import Generator
from typing import override

import pytest
from fastapi.testclient import TestClient

from app.database.executor import SQLExecutor
from app.dependencies import get_agent_service
from app.main import app
from app.schemas.sql import SQLExecutionResult
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService


class StubLLMService(LLMService):
    @override
    def build_chat_model(self) -> None:
        return None


class StubSQLExecutor(SQLExecutor):
    @override
    async def execute(self, sql: str) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[{"id": 1, "name": "mock-row"}],
            row_count=1,
            columns=["id", "name"],
            execution_summary="查询执行成功，共返回 1 行。",
        )


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    app.dependency_overrides[get_agent_service] = lambda: AgentService(
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
