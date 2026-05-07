from __future__ import annotations

from typing import cast

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    build_semantic_brief,
    build_semantic_query,
    execute_sql,
    finalize_response,
    generate_sql,
    join_path_planning,
    query_understanding,
    retrieve_schema,
    schema_linking,
    sql_planning,
    sql_repairing,
    validate_sql,
    value_linking,
)
from app.agent.state import AgentState
from app.database.executor import SQLExecutor
from app.rag.schema_models import SchemaCatalog
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator

# 模块级单例：编译后的 Graph 只保留一份，避免每次请求重新编译。
_compiled_graph = None
_graph_executor_key: tuple | None = None


def reset_agent_graph() -> None:
    """重置单例 Graph，用于测试或依赖变更时。"""
    global _compiled_graph, _graph_executor_key
    _compiled_graph = None
    _graph_executor_key = None


def _should_retry_or_fallback(state: object) -> str:
    """条件路由：验证失败、低置信度门控时决定修复、结束或执行。"""
    s = cast(AgentState, state)
    retry_count = s.get("retry_count", 0)
    validation_errors = s.get("validation_errors", [])
    execution_gate = s.get("execution_gate", {})

    if validation_errors and retry_count < 2:
        return "sql_repairing"
    if validation_errors:
        return "finalize_response"
    if execution_gate and not execution_gate.get("allowed", True):
        return "finalize_response"
    return "execute_sql"


def _should_repair_execution_error(state: object) -> str:
    """执行失败最多进入受控修复一次，避免不稳定 SQL 反复查询。"""
    s = cast(AgentState, state)
    if s.get("execution_error") and s.get("retry_count", 0) < 2:
        return "sql_repairing"
    return "finalize_response"


def _after_sql_repairing(state: object) -> str:
    """修复节点显式标记失败时直接结束，避免继续生成并再次执行。"""
    s = cast(AgentState, state)
    if s.get("status") == "error":
        return "finalize_response"
    return "generate_sql"


def build_agent_graph(
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
    executor: SQLExecutor,
    catalog: SchemaCatalog | None = None,
):
    graph_builder = StateGraph(AgentState)

    # LangGraph 节点默认接收 object，做类型收窄后转发给具体节点函数。
    def query_understanding_node(state: object) -> AgentState:
        return query_understanding(cast(AgentState, state), llm_service, catalog)

    async def retrieve_schema_node(state: object) -> AgentState:
        return await retrieve_schema(cast(AgentState, state), rag_service)

    def schema_linking_node(state: object) -> AgentState:
        return schema_linking(cast(AgentState, state))

    def value_linking_node(state: object) -> AgentState:
        return value_linking(cast(AgentState, state))

    def join_path_planning_node(state: object) -> AgentState:
        return join_path_planning(cast(AgentState, state))

    def build_semantic_brief_node(state: object) -> AgentState:
        return build_semantic_brief(cast(AgentState, state))

    def build_semantic_query_node(state: object) -> AgentState:
        return build_semantic_query(cast(AgentState, state))

    def sql_planning_node(state: object) -> AgentState:
        return sql_planning(cast(AgentState, state), llm_service)

    def generate_sql_node(state: object) -> AgentState:
        return generate_sql(cast(AgentState, state), llm_service, catalog)

    def validate_sql_node(state: object) -> AgentState:
        return validate_sql(cast(AgentState, state), validator)

    def sql_repairing_node(state: object) -> AgentState:
        return sql_repairing(cast(AgentState, state), llm_service)

    async def execute_sql_node(state: object) -> AgentState:
        return await execute_sql(cast(AgentState, state), executor)

    def finalize_response_node(state: object) -> AgentState:
        return finalize_response(cast(AgentState, state))

    _ = graph_builder.add_node("query_understanding", query_understanding_node)
    _ = graph_builder.add_node("retrieve_schema", retrieve_schema_node)
    _ = graph_builder.add_node("schema_linking", schema_linking_node)
    _ = graph_builder.add_node("value_linking", value_linking_node)
    _ = graph_builder.add_node("join_path_planning", join_path_planning_node)
    _ = graph_builder.add_node("build_semantic_brief", build_semantic_brief_node)
    _ = graph_builder.add_node("build_semantic_query", build_semantic_query_node)
    _ = graph_builder.add_node("sql_planning", sql_planning_node)
    _ = graph_builder.add_node("generate_sql", generate_sql_node)
    _ = graph_builder.add_node("validate_sql", validate_sql_node)
    _ = graph_builder.add_node("sql_repairing", sql_repairing_node)
    _ = graph_builder.add_node("execute_sql", execute_sql_node)
    _ = graph_builder.add_node("finalize_response", finalize_response_node)

    _ = graph_builder.add_edge(START, "query_understanding")
    _ = graph_builder.add_edge("query_understanding", "retrieve_schema")
    _ = graph_builder.add_edge("retrieve_schema", "schema_linking")
    _ = graph_builder.add_edge("schema_linking", "value_linking")
    _ = graph_builder.add_edge("value_linking", "join_path_planning")
    _ = graph_builder.add_edge("join_path_planning", "build_semantic_brief")
    _ = graph_builder.add_edge("build_semantic_brief", "build_semantic_query")
    _ = graph_builder.add_edge("build_semantic_query", "sql_planning")
    _ = graph_builder.add_edge("sql_planning", "generate_sql")
    _ = graph_builder.add_edge("generate_sql", "validate_sql")

    _ = graph_builder.add_conditional_edges(
        "validate_sql",
        _should_retry_or_fallback,
        {
            "sql_repairing": "sql_repairing",
            "execute_sql": "execute_sql",
            "finalize_response": "finalize_response",
        },
    )

    _ = graph_builder.add_conditional_edges(
        "sql_repairing",
        _after_sql_repairing,
        {
            "generate_sql": "generate_sql",
            "finalize_response": "finalize_response",
        },
    )
    _ = graph_builder.add_conditional_edges(
        "execute_sql",
        _should_repair_execution_error,
        {
            "sql_repairing": "sql_repairing",
            "finalize_response": "finalize_response",
        },
    )
    _ = graph_builder.add_edge("finalize_response", END)

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
    # 用依赖与 catalog 的 id 作为缓存 key，不同测试/数据库 schema 触发重新编译。
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

    catalog = await _get_schema_catalog()
    graph = get_agent_graph(rag_service, llm_service, validator, executor, catalog)
    initial_state: AgentState = {"question": question}
    return cast(AgentState, await graph.ainvoke(initial_state))
