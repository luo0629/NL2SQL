from typing import cast

from langgraph.graph import END, START, StateGraph

from app.agent.nodes import (
    finalize_response,
    generate_sql,
    retrieve_schema,
    validate_sql,
)
from app.agent.state import AgentState
from app.services.llm_service import LLMService
from app.services.rag_service import RagService
from app.validator.sql_validator import SQLValidator


def build_agent_graph(
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
):
    graph_builder = StateGraph(AgentState)

    def retrieve_schema_node(state: object) -> AgentState:
        return retrieve_schema(cast(AgentState, state), rag_service)

    def generate_sql_node(state: object) -> AgentState:
        return generate_sql(cast(AgentState, state), llm_service)

    def validate_sql_node(state: object) -> AgentState:
        return validate_sql(cast(AgentState, state), validator)

    _ = graph_builder.add_node("retrieve_schema", retrieve_schema_node)
    _ = graph_builder.add_node("generate_sql", generate_sql_node)
    _ = graph_builder.add_node("validate_sql", validate_sql_node)
    _ = graph_builder.add_node("finalize_response", finalize_response)

    _ = graph_builder.add_edge(START, "retrieve_schema")
    _ = graph_builder.add_edge("retrieve_schema", "generate_sql")
    _ = graph_builder.add_edge("generate_sql", "validate_sql")
    _ = graph_builder.add_edge("validate_sql", "finalize_response")
    _ = graph_builder.add_edge("finalize_response", END)

    return graph_builder.compile()


def run_agent(
    question: str,
    rag_service: RagService,
    llm_service: LLMService,
    validator: SQLValidator,
) -> AgentState:
    graph = build_agent_graph(rag_service, llm_service, validator)
    initial_state: AgentState = {"question": question}

    # TODO(learning): 这里每次请求都会重新 compile 图，后续可以把它提升为应用级单例来学习性能优化。
    return cast(AgentState, graph.invoke(initial_state))
