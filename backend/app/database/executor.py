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


class QueryExecutionTimeoutError(RuntimeError):
    pass


class SQLExecutor:
    def __init__(
        self,
        engine: AsyncEngine | None = None,
        validator: SQLValidator | None = None,
    ) -> None:
        self.engine = engine or default_engine
        self.validator = validator or SQLValidator()
        self.result_limit = get_settings().query_result_limit

    async def execute(
        self,
        sql: str,
        params: list[object] | None = None,
        max_rows: int | None = None,
        timeout_seconds: float | None = None,
    ) -> SQLExecutionResult:
        self.validator.validate_read_only(sql)

        sql_params = {
            f"p{index}": value
            for index, value in enumerate(params or [])
        }
        previous_limit = self.result_limit
        if max_rows is not None:
            self.result_limit = max_rows

        try:
            async with self.engine.connect() as connection:
                execution = connection.execute(text(sql), sql_params)
                if timeout_seconds is not None:
                    import asyncio

                    result = await asyncio.wait_for(execution, timeout=timeout_seconds)
                else:
                    result = await execution
                return self._build_execution_result(result)
        except TimeoutError:
            return SQLExecutionResult(
                rows=[],
                row_count=0,
                columns=[],
                truncated=False,
                execution_summary="查询执行超时。",
            )
        except SQLAlchemyError as error:
            return SQLExecutionResult(
                rows=[],
                row_count=0,
                columns=[],
                truncated=False,
                execution_summary=f"查询执行失败：{error.__class__.__name__}",
            )
        finally:
            self.result_limit = previous_limit

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
