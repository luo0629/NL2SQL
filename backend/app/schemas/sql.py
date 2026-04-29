from pydantic import BaseModel, Field


class SQLExecutionResult(BaseModel):
    rows: list[dict[str, object]] = Field(default_factory=list)
    row_count: int = 0
    columns: list[str] = Field(default_factory=list)
    truncated: bool = False
    execution_summary: str | None = None


class SQLExecutionError(BaseModel):
    message: str
    detail: str | None = None
