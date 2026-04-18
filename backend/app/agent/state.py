from typing import TypedDict


class AgentState(TypedDict, total=False):
    question: str
    schema_context: list[str]
    sql: str
    validation_errors: list[str]
    execution_summary: str
