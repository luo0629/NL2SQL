from typing import override

import pytest

from app.database.executor import QueryExecutionTimeoutError, SQLExecutor
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
            truncated=False,
            execution_summary="查询执行成功，共返回 1 行。",
        )


class EmptySQLExecutor(SQLExecutor):
    @override
    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[],
            row_count=0,
            columns=["id", "name"],
            truncated=False,
            execution_summary="查询执行成功，但没有返回记录。",
        )


class TimeoutSQLExecutor(SQLExecutor):
    @override
    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        raise QueryExecutionTimeoutError("查询执行超时：超过 0.01 秒。")


class FailingSQLExecutor(SQLExecutor):
    @override
    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        raise RuntimeError("查询执行失败：OperationalError")


@pytest.mark.anyio
async def test_agent_service_response_includes_debug_trace() -> None:
    service = AgentService(
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )

    response = await service.generate_sql(
        NLQueryRequest(question="近 30 天收入最高的客户是谁？")
    )

    assert response.status == "ready"
    assert response.rows == [{"id": 1, "name": "Alice"}]
    assert response.params is not None
    assert response.debug is not None
    assert set(response.debug) >= {
        "query_understanding",
        "schema_links",
        "value_links",
        "join_paths",
        "sql_plan",
        "validation_errors",
        "validation_issues",
        "fallback",
        "execution",
    }
    assert response.debug["execution"]["row_count"] == 1


@pytest.mark.anyio
async def test_agent_service_handles_empty_result() -> None:
    service = AgentService(
        llm_service=StubLLMService(),
        sql_executor=EmptySQLExecutor(),
    )

    response = await service.generate_sql(NLQueryRequest(question="查询客户"))

    assert response.status == "ready"
    assert response.rows == []
    assert response.row_count == 0
    assert response.execution_summary == "查询执行成功，但没有返回记录。"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "executor_cls",
    [TimeoutSQLExecutor, FailingSQLExecutor],
)
async def test_agent_service_reports_execution_errors(executor_cls: type[SQLExecutor]) -> None:
    service = AgentService(
        llm_service=StubLLMService(),
        sql_executor=executor_cls(),
    )

    response = await service.generate_sql(NLQueryRequest(question="查询客户"))

    assert response.status == "error"
    assert response.rows == []
    assert response.row_count == 0
    assert response.error_message
