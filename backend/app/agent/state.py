from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    # 用户原始输入，question 保持 API/测试兼容，user_input 是新链路主字段。
    question: str
    user_input: str
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
    sql: str
    sql_params: list[Any]
    # sql_validator 输出与重试控制。
    validation_error: str
    validation_errors: list[str]
    validation_issues: list[dict[str, Any]]
    retry_count: int
    max_retries: int
    # sql_executor 输出。
    query_result: list[dict[str, Any]]
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
