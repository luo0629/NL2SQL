from typing import override

import pytest

from app.database.executor import SQLExecutor
from app.schemas.query import NLQueryRequest
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
        return SQLExecutionResult(
            rows=[{"id": 1, "name": "Alice"}],
            row_count=1,
            columns=["id", "name"],
            execution_summary="查询执行成功，共返回 1 行。",
        )


@pytest.mark.anyio
async def test_agent_service_returns_mock_response() -> None:
    service = AgentService(
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )

    response = await service.generate_sql(
        NLQueryRequest(question="近 30 天收入最高的客户是谁？")
    )

    assert response.status == "ready"
    assert "SELECT" in response.sql
    assert "DROP" not in response.sql
    assert response.explanation
    assert response.rows == [{"id": 1, "name": "Alice"}]
    assert response.row_count == 1
    assert response.columns == ["id", "name"]
    assert response.execution_summary == "查询执行成功，共返回 1 行。"
    assert response.debug is not None
    assert "query_understanding" in response.debug
    assert "schema_links" in response.debug
    assert "join_paths" in response.debug
    assert "sql_plan" in response.debug
    assert response.debug["fallback"] == {"used": False}
