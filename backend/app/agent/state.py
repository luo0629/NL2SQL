from typing import Any, Literal, TypedDict

from app.rag.schema_models import SchemaCatalog


class AgentState(TypedDict, total=False):
    # 用户原始输入。
    user_input: str
    # schema 加载输出：真实 schema catalog 与加载错误。
    schema_catalog: SchemaCatalog | None
    schema_catalog_error: str
    # intent_parser 输出：自然语言意图 + 真实 schema 中筛选出的相关表。
    intent: str
    relevant_tables: list[str]
    available_tables: list[str]
    # schema_retriever 输出：仅包含相关表的真实 schema 上下文。
    schema_context: str
    semantic_context: str
    semantic_signals: list[dict[str, Any]]
    # sql_generator 输出。
    generated_sql: str
    previous_sql: str
    sql_params: list[Any]
    # sql_validator 输出与重试控制。
    validation_error: str
    validation_errors: list[str]
    validation_issues: list[dict[str, Any]]
    retry_count: int
    max_retries: int
    # sql_executor 输出。
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    truncated: bool
    execution_summary: str
    execution_time_ms: float
    execution_error: dict[str, Any]
    # result_formatter 输出。
    final_answer: str
    explanation: str
    status: Literal["mock", "ready", "error"]
    used_fallback: bool
    # 开发调试追踪信息。
    debug_trace: dict[str, Any]
