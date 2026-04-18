from pydantic import BaseModel


class SQLExecutionResult(BaseModel):
    rows: list[dict[str, object]] = []
    row_count: int = 0


class SQLExecutionError(BaseModel):
    message: str
    detail: str | None = None
