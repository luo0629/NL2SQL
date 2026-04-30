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
    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        rows = [{"sql": sql, "params": list(params or [])}]
        return SQLExecutionResult(
            rows=rows,
            row_count=len(rows),
            columns=["sql", "params"],
            execution_summary=f"查询执行成功，共返回 {len(rows)} 行。",
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
