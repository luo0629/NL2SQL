import asyncio
from types import SimpleNamespace
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


@pytest.mark.anyio
async def test_agent_service_timeout_returns_structured_error_without_fallback_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slow_run_agent(**_kwargs: object) -> dict[str, object]:
        await asyncio.sleep(0.05)
        return {}

    monkeypatch.setattr("app.services.agent_service.run_agent", slow_run_agent)
    monkeypatch.setattr(
        "app.services.agent_service.get_settings",
        lambda: SimpleNamespace(agent_request_timeout_seconds=0.001),
    )

    service = AgentService(
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )

    response = await service.generate_sql(NLQueryRequest(question="查询菜品"))

    assert response.status == "error"
    assert response.sql == ""
    assert response.rows == []
    assert response.columns == []
    assert response.row_count == 0
    assert response.error_message == "查询处理超时，已由后端主动停止。"
    assert response.debug is not None
    assert response.debug["agent_request"] == {
        "status": "timeout",
        "timeout_seconds": 0.001,
        "duration_ms": response.debug["agent_request"]["duration_ms"],
    }
