import json
from pathlib import Path
from typing import override

import pytest

from app.database.executor import QueryExecutionTimeoutError, SQLExecutor
from app.rag.schema_models import SchemaSearchResult
from app.schemas.query import NLQueryRequest
from app.schemas.sql import SQLExecutionResult
from app.services.agent_service import AgentService
from app.services.llm_service import LLMService
from app.services.rag_service import RagService


class StubLLMService(LLMService):
    @override
    def build_chat_model(self) -> None:
        return None


class StubSQLExecutor(SQLExecutor):
    @override
    async def execute(self, sql: str) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[{"id": 1, "name": "Alice"}],
            row_count=1,
            columns=["id", "name"],
            truncated=False,
            execution_summary="查询执行成功，共返回 1 行。",
        )


class EmptySQLExecutor(SQLExecutor):
    @override
    async def execute(self, sql: str) -> SQLExecutionResult:
        return SQLExecutionResult(
            rows=[],
            row_count=0,
            columns=["id", "name"],
            truncated=False,
            execution_summary="查询执行成功，但没有返回记录。",
        )


class TimeoutSQLExecutor(SQLExecutor):
    @override
    async def execute(self, sql: str) -> SQLExecutionResult:
        raise QueryExecutionTimeoutError("查询执行超时：超过 0.01 秒。")


class FailingSQLExecutor(SQLExecutor):
    @override
    async def execute(self, sql: str) -> SQLExecutionResult:
        raise RuntimeError("查询执行失败：OperationalError")


class StrongRagService(RagService):
    @override
    async def retrieve_schema_result(self, question: str) -> SchemaSearchResult:
        return SchemaSearchResult(
            context=[
                "table orders\n- id: bigint, required\n- status: int, nullable | desc: 1=paid,0=pending",
                "table customers\n- id: bigint, required\n- name: varchar, nullable",
            ],
            candidate_tables=["orders", "customers"],
            candidate_columns=["orders.status", "customers.name"],
            matched_terms=["订单", "客户"],
            confidence="strong",
            fallback_used=False,
        )


class WeakRagService(RagService):
    @override
    async def retrieve_schema_result(self, question: str) -> SchemaSearchResult:
        return SchemaSearchResult(
            context=["table orders\n- id: bigint, required"],
            candidate_tables=["orders"],
            candidate_columns=[],
            matched_terms=[],
            confidence="weak",
            fallback_used=True,
        )


@pytest.mark.anyio
async def test_agent_service_response_includes_stage_trace() -> None:
    service = AgentService(
        rag_service=StrongRagService(),
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )

    response = await service.generate_sql(
        NLQueryRequest(question="近 30 天收入最高的客户是谁？")
    )

    assert set(response.trace) >= {"retrieve_schema", "generate_sql", "validate_sql", "execute_sql", "finalize_response"}
    assert response.trace["retrieve_schema"]["schema_hits"] >= 0
    assert response.trace["generate_sql"]["generator"] == "fallback"
    assert response.trace["validate_sql"]["outcome"] == "passed"
    assert response.trace["execute_sql"]["execution_status"] == "success"
    assert response.trace["finalize_response"]["status"] == response.status


@pytest.mark.anyio
async def test_agent_service_blocks_generation_when_schema_context_is_weak() -> None:
    service = AgentService(
        rag_service=WeakRagService(),
        llm_service=StubLLMService(),
        sql_executor=StubSQLExecutor(),
    )

    response = await service.generate_sql(NLQueryRequest(question="随便查点东西"))

    assert response.status == "blocked"
    assert response.reason_code == "insufficient_schema_context"
    assert response.execution_status == "not_started"
    assert response.sql == "SELECT 1;"
    assert "阻断" in response.explanation or "检索命中不足" in response.explanation
    assert "execute_sql" not in response.trace
    assert response.trace["generate_sql"]["generator"] == "blocked"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("executor_cls", "case_id"),
    [
        (StubSQLExecutor, "fallback-success"),
        (EmptySQLExecutor, "fallback-empty"),
        (TimeoutSQLExecutor, "execution-timeout"),
        (FailingSQLExecutor, "execution-failure"),
    ],
)
async def test_query_eval_cases_match_expected_outcomes(
    executor_cls: type[SQLExecutor], case_id: str
) -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "query_eval_cases.json"
    )
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    case = next(item for item in cases if item["id"] == case_id)

    service = AgentService(
        rag_service=StrongRagService(),
        llm_service=StubLLMService(),
        sql_executor=executor_cls(),
    )
    response = await service.generate_sql(NLQueryRequest(question=case["question"]))

    assert response.status == case["expected_status"]
    assert response.execution_status == case["expected_execution_status"]
    assert response.reason_code == case["expected_reason_code"]
