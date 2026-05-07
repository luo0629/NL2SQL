from __future__ import annotations

import asyncio
import logging
import time
from typing import cast

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    async_intent_parser,
    async_result_formatter,
    async_sql_generator,
    schema_retriever,
    sql_executor,
    sql_validator,
)
from app.agent.state import AgentState
from app.config import get_settings
from app.database.executor import SQLExecutor
from app.rag.schema_models import SchemaCatalog
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator

logger = logging.getLogger(__name__)

_compiled_graph = None
_graph_executor_key: tuple | None = None


def reset_agent_graph() -> None:
    """重置单例 Graph，用于测试或依赖变更时。"""
    global _compiled_graph, _graph_executor_key
    _compiled_graph = None
    _graph_executor_key = None


def _after_sql_validation(state: object) -> str:
    s = cast(AgentState, state)
    if s.get("validation_error") and s.get("retry_count", 0) < s.get("max_retries", 3):
        return "sql_generator"
    if s.get("validation_error"):
        return "result_formatter"
    return "sql_executor"


def build_agent_graph(
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
    executor: SQLExecutor,
    catalog: SchemaCatalog | None = None,
):
    graph_builder = StateGraph(AgentState)

    async def intent_parser_node(state: object) -> AgentState:
        return await async_intent_parser(cast(AgentState, state), llm_service, catalog)

    def schema_retriever_node(state: object) -> AgentState:
        started_at = time.monotonic()
        result = schema_retriever(cast(AgentState, state), catalog)
        logger.info("agent.schema_retriever.end duration_ms=%.2f", (time.monotonic() - started_at) * 1000)
        return result

    async def sql_generator_node(state: object) -> AgentState:
        return await async_sql_generator(cast(AgentState, state), llm_service, catalog)

    async def sql_validator_node(state: object) -> AgentState:
        return await sql_validator(cast(AgentState, state), validator, executor)

    async def sql_executor_node(state: object) -> AgentState:
        return await sql_executor(cast(AgentState, state), executor)

    async def result_formatter_node(state: object) -> AgentState:
        return await async_result_formatter(cast(AgentState, state), llm_service)

    _ = graph_builder.add_node("intent_parser", intent_parser_node)
    _ = graph_builder.add_node("schema_retriever", schema_retriever_node)
    _ = graph_builder.add_node("sql_generator", sql_generator_node)
    _ = graph_builder.add_node("sql_validator", sql_validator_node)
    _ = graph_builder.add_node("sql_executor", sql_executor_node)
    _ = graph_builder.add_node("result_formatter", result_formatter_node)

    _ = graph_builder.add_edge(START, "intent_parser")
    _ = graph_builder.add_edge("intent_parser", "schema_retriever")
    _ = graph_builder.add_edge("schema_retriever", "sql_generator")
    _ = graph_builder.add_edge("sql_generator", "sql_validator")
    _ = graph_builder.add_conditional_edges(
        "sql_validator",
        _after_sql_validation,
        {
            "sql_generator": "sql_generator",
            "sql_executor": "sql_executor",
            "result_formatter": "result_formatter",
        },
    )
    _ = graph_builder.add_edge("sql_executor", "result_formatter")
    _ = graph_builder.add_edge("result_formatter", END)

    return graph_builder.compile()


def get_agent_graph(
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
    executor: SQLExecutor,
    catalog: SchemaCatalog | None = None,
):
    """获取单例 Graph，首次调用时编译，后续复用。executor 变更时重新编译。"""
    global _compiled_graph, _graph_executor_key
    executor_key = (
        id(rag_service),
        rag_service.__class__.__qualname__,
        id(llm_service),
        llm_service.__class__.__qualname__,
        id(validator),
        validator.__class__.__qualname__,
        id(executor),
        executor.__class__.__qualname__,
        id(catalog),
    )
    if _compiled_graph is None or _graph_executor_key != executor_key:
        _compiled_graph = build_agent_graph(
            rag_service, llm_service, validator, executor, catalog
        )
        _graph_executor_key = executor_key
    return _compiled_graph


async def run_agent(
    question: str,
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
    executor: SQLExecutor,
) -> AgentState:
    from app.services.rag_service import _get_schema_catalog

    settings = get_settings()
    schema_started_at = time.monotonic()
    try:
        catalog = await asyncio.wait_for(
            _get_schema_catalog(),
            timeout=settings.schema_sync_timeout_seconds,
        )
    except TimeoutError:
        elapsed_ms = (time.monotonic() - schema_started_at) * 1000
        logger.warning(
            "agent.schema_catalog.timeout timeout_seconds=%.2f duration_ms=%.2f",
            settings.schema_sync_timeout_seconds,
            elapsed_ms,
        )
        return {
            "question": question,
            "user_input": question,
            "sql": "",
            "generated_sql": "",
            "status": "error",
            "rows": [],
            "columns": [],
            "row_count": 0,
            "execution_summary": "读取数据库 schema 超时，已停止本次查询。",
            "explanation": "读取数据库 schema 超时，请确认数据库连接可用后重试。",
            "debug_trace": {
                "schema_catalog": {
                    "status": "timeout",
                    "timeout_seconds": settings.schema_sync_timeout_seconds,
                    "duration_ms": round(elapsed_ms, 2),
                }
            },
        }

    logger.info(
        "agent.schema_catalog.end table_count=%s duration_ms=%.2f",
        len(catalog.tables),
        (time.monotonic() - schema_started_at) * 1000,
    )
    graph = get_agent_graph(rag_service, llm_service, validator, executor, catalog)
    initial_state: AgentState = {
        "question": question,
        "user_input": question,
        "retry_count": 0,
        "max_retries": 3,
    }
    return cast(AgentState, await graph.ainvoke(initial_state))
