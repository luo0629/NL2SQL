from app.schemas.sql import SQLExecutionResult
from app.validator.sql_validator import SQLValidator


class SQLExecutor:
    def __init__(self) -> None:
        self.validator: SQLValidator = SQLValidator()

    def execute(self, sql: str) -> SQLExecutionResult:
        self.validator.validate_read_only(sql)
        return SQLExecutionResult(rows=[], row_count=0)
