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
    value_validator,
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
_graph_dependency_key: tuple[int, int, int] | None = None


def reset_agent_graph() -> None:
    """重置单例 Graph，用于测试或依赖变更时。"""
    global _compiled_graph, _graph_dependency_key
    _compiled_graph = None
    _graph_dependency_key = None


def _build_graph_dependency_key(
    llm_service: LLMService,
    validator: SQLValidator,
    executor: SQLExecutor,
) -> tuple[int, int, int]:
    return (id(llm_service), id(validator), id(executor))


def _after_sql_validation(state: object) -> str:
    s = cast(AgentState, state)
    if s.get("validation_error") and s.get("retry_count", 0) < s.get("max_retries", 3):
        return "sql_generator"
    if s.get("validation_error"):
        return "result_formatter"
    return "value_validator"


def _after_schema_catalog_load(state: object) -> str:
    s = cast(AgentState, state)
    return "schema_catalog_failed" if s.get("schema_catalog_error") else "intent_parser"


def _after_value_validation(state: object) -> str:
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
    _ = rag_service

    async def load_schema_catalog_node(state: object) -> AgentState:
        base_state = cast(AgentState, state)
        if catalog is not None:
            debug_trace = dict(base_state.get("debug_trace", {}))
            debug_trace["schema_catalog"] = {
                "status": "provided",
                "table_count": len(catalog.tables),
            }
            return {
                "schema_catalog": catalog,
                "schema_catalog_error": "",
                "debug_trace": debug_trace,
            }

        from app.services.rag_service import _get_schema_catalog

        settings = get_settings()
        started_at = time.monotonic()
        try:
            loaded_catalog = await asyncio.wait_for(
                _get_schema_catalog(),
                timeout=settings.schema_sync_timeout_seconds,
            )
        except TimeoutError:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            logger.warning(
                "agent.schema_catalog.timeout timeout_seconds=%.2f duration_ms=%.2f",
                settings.schema_sync_timeout_seconds,
                elapsed_ms,
            )
            return {
                "schema_catalog": None,
                "schema_catalog_error": "读取数据库 schema 超时，请确认数据库连接可用后重试。",
                "generated_sql": "",
                "status": "error",
                "rows": [],
                "columns": [],
                "row_count": 0,
                "execution_summary": "读取数据库 schema 超时，已停止本次查询。",
                "explanation": "读取数据库 schema 超时，请确认数据库连接可用后重试。",
                "final_answer": "读取数据库 schema 超时，请确认数据库连接可用后重试。",
                "debug_trace": {
                    **dict(base_state.get("debug_trace", {})),
                    "schema_catalog": {
                        "status": "timeout",
                        "timeout_seconds": settings.schema_sync_timeout_seconds,
                        "duration_ms": round(elapsed_ms, 2),
                    },
                },
            }
        except Exception as error:
            elapsed_ms = (time.monotonic() - started_at) * 1000
            logger.warning(
                "agent.schema_catalog.error error_class=%s duration_ms=%.2f",
                error.__class__.__name__,
                elapsed_ms,
            )
            return {
                "schema_catalog": None,
                "schema_catalog_error": "读取数据库 schema 失败，请确认数据库连接和权限后重试。",
                "generated_sql": "",
                "status": "error",
                "rows": [],
                "columns": [],
                "row_count": 0,
                "execution_summary": "读取数据库 schema 失败，已停止本次查询。",
                "explanation": "读取数据库 schema 失败，请确认数据库连接和权限后重试。",
                "final_answer": "读取数据库 schema 失败，请确认数据库连接和权限后重试。",
                "debug_trace": {
                    **dict(base_state.get("debug_trace", {})),
                    "schema_catalog": {
                        "status": "error",
                        "error_class": error.__class__.__name__,
                        "duration_ms": round(elapsed_ms, 2),
                    },
                },
            }

        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "agent.schema_catalog.end table_count=%s duration_ms=%.2f",
            len(loaded_catalog.tables),
            elapsed_ms,
        )
        debug_trace = dict(base_state.get("debug_trace", {}))
        debug_trace["schema_catalog"] = {
            "status": "loaded",
            "table_count": len(loaded_catalog.tables),
            "duration_ms": round(elapsed_ms, 2),
        }
        return {
            "schema_catalog": loaded_catalog,
            "schema_catalog_error": "",
            "debug_trace": debug_trace,
        }

    async def intent_parser_node(state: object) -> AgentState:
        agent_state = cast(AgentState, state)
        return await async_intent_parser(
            agent_state,
            llm_service,
            agent_state.get("schema_catalog"),
        )

    def schema_retriever_node(state: object) -> AgentState:
        agent_state = cast(AgentState, state)
        started_at = time.monotonic()
        result = schema_retriever(agent_state, agent_state.get("schema_catalog"))
        logger.info("agent.schema_retriever.end duration_ms=%.2f", (time.monotonic() - started_at) * 1000)
        return result

    async def sql_generator_node(state: object) -> AgentState:
        agent_state = cast(AgentState, state)
        return await async_sql_generator(
            agent_state,
            llm_service,
            agent_state.get("schema_catalog"),
        )

    async def sql_validator_node(state: object) -> AgentState:
        return await sql_validator(cast(AgentState, state), validator, executor)

    async def value_validator_node(state: object) -> AgentState:
        agent_state = cast(AgentState, state)
        return await value_validator(agent_state, executor, agent_state.get("schema_catalog"))

    async def sql_executor_node(state: object) -> AgentState:
        return await sql_executor(cast(AgentState, state), executor)

    async def result_formatter_node(state: object) -> AgentState:
        return await async_result_formatter(cast(AgentState, state), llm_service)

    async def schema_catalog_failed_node(_state: object) -> AgentState:
        return {}

    _ = graph_builder.add_node("load_schema_catalog", load_schema_catalog_node)
    _ = graph_builder.add_node("schema_catalog_failed", schema_catalog_failed_node)
    _ = graph_builder.add_node("intent_parser", intent_parser_node)
    _ = graph_builder.add_node("schema_retriever", schema_retriever_node)
    _ = graph_builder.add_node("sql_generator", sql_generator_node)
    _ = graph_builder.add_node("sql_validator", sql_validator_node)
    _ = graph_builder.add_node("value_validator", value_validator_node)
    _ = graph_builder.add_node("sql_executor", sql_executor_node)
    _ = graph_builder.add_node("result_formatter", result_formatter_node)

    _ = graph_builder.add_edge(START, "load_schema_catalog")
    _ = graph_builder.add_conditional_edges(
        "load_schema_catalog",
        _after_schema_catalog_load,
        {
            "intent_parser": "intent_parser",
            "schema_catalog_failed": "schema_catalog_failed",
        },
    )
    _ = graph_builder.add_edge("schema_catalog_failed", END)
    _ = graph_builder.add_edge("intent_parser", "schema_retriever")
    _ = graph_builder.add_edge("schema_retriever", "sql_generator")
    _ = graph_builder.add_edge("sql_generator", "sql_validator")
    _ = graph_builder.add_conditional_edges(
        "sql_validator",
        _after_sql_validation,
        {
            "sql_generator": "sql_generator",
            "value_validator": "value_validator",
            "result_formatter": "result_formatter",
        },
    )
    _ = graph_builder.add_conditional_edges(
        "value_validator",
        _after_value_validation,
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
):
    """获取单例 Graph，依赖实例变更时重新编译。"""
    global _compiled_graph, _graph_dependency_key
    dependency_key = _build_graph_dependency_key(llm_service, validator, executor)
    if _compiled_graph is None or _graph_dependency_key != dependency_key:
        _compiled_graph = build_agent_graph(
            rag_service,
            llm_service,
            validator,
            executor,
        )
        _graph_dependency_key = dependency_key
    return _compiled_graph


async def run_agent(
    question: str,
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
    executor: SQLExecutor,
) -> AgentState:
    graph = get_agent_graph(rag_service, llm_service, validator, executor)
    initial_state: AgentState = {
        "user_input": question,
        "retry_count": 0,
        "max_retries": 3,
    }
    return cast(AgentState, await graph.ainvoke(initial_state))
