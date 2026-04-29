from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.database.engine import engine as default_engine
from app.schemas.sql import SQLExecutionResult
from app.validator.sql_validator import SQLValidator


class SQLExecutor:
    def __init__(
        self,
        engine: AsyncEngine | None = None,
        validator: SQLValidator | None = None,
    ) -> None:
        self.engine = engine or default_engine
        self.validator = validator or SQLValidator()
        self.result_limit = get_settings().query_result_limit

    async def execute(self, sql: str) -> SQLExecutionResult:
        self.validator.validate_read_only(sql)

        try:
            async with self.engine.connect() as connection:
                result = await connection.execute(text(sql))
                return self._build_execution_result(result)
        except SQLAlchemyError as error:
            return SQLExecutionResult(
                rows=[],
                row_count=0,
                columns=[],
                truncated=False,
                execution_summary=f"查询执行失败：{error.__class__.__name__}",
            )

    def _build_execution_result(self, result: Result[object]) -> SQLExecutionResult:
        columns = list(result.keys())
        mapped_rows = [
            {
                key: self._serialize_value(value)
                for key, value in row.items()
            }
            for row in result.mappings().all()
        ]
        total_rows = len(mapped_rows)
        truncated = total_rows > self.result_limit
        visible_rows = mapped_rows[: self.result_limit]

        if total_rows == 0:
            execution_summary = "查询执行成功，但没有返回记录。"
        elif truncated:
            execution_summary = (
                f"查询共返回 {total_rows} 行，当前仅展示前 {self.result_limit} 行。"
            )
        else:
            execution_summary = f"查询执行成功，共返回 {total_rows} 行。"

        return SQLExecutionResult(
            rows=visible_rows,
            row_count=total_rows,
            columns=columns,
            truncated=truncated,
            execution_summary=execution_summary,
        )

    def _serialize_value(self, value: object) -> object:
        if isinstance(value, Decimal):
            return float(value)

        if isinstance(value, (datetime, date, time)):
            return value.isoformat()

        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")

        return value
