from typing import Literal, TypedDict


class AgentState(TypedDict, total=False):
    question: str
    schema_context: list[str]
    sql: str
    explanation: str
    status: Literal["mock", "ready"]
    used_fallback: bool
    validation_errors: list[str]
    execution_summary: str
