from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.database.executor import SQLExecutor
from app.schemas.sql import SQLExecutionResult
from app.utils.exceptions import DangerousSQLError


@pytest.mark.anyio
async def test_sql_executor_executes_select_and_serializes_values() -> None:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TABLE customers (id INTEGER, name TEXT, amount REAL, created_at TEXT)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO customers (id, name, amount, created_at) VALUES (1, 'Alice', 12.5, '2026-04-28T00:00:00')"
        )

    executor = SQLExecutor(engine=engine)
    result: SQLExecutionResult = await executor.execute(
        "SELECT id, name, amount, created_at FROM customers"
    )

    assert result.row_count == 1
    assert result.columns == ["id", "name", "amount", "created_at"]
    assert result.rows == [
        {
            "id": 1,
            "name": "Alice",
            "amount": 12.5,
            "created_at": "2026-04-28T00:00:00",
        }
    ]
    assert result.execution_summary == "查询执行成功，共返回 1 行。"

    await engine.dispose()


@pytest.mark.anyio
async def test_sql_executor_executes_parameterized_select() -> None:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE dish (id INTEGER, name TEXT, status INTEGER)")
        await connection.exec_driver_sql("INSERT INTO dish (id, name, status) VALUES (1, 'Noodles', 1)")
        await connection.exec_driver_sql("INSERT INTO dish (id, name, status) VALUES (2, 'Soup', 0)")

    executor = SQLExecutor(engine=engine)
    result = await executor.execute(
        "SELECT id, name FROM dish WHERE status = :p0 ORDER BY id ASC",
        params=[1],
    )

    assert result.rows == [{"id": 1, "name": "Noodles"}]
    assert result.columns == ["id", "name"]
    assert result.execution_summary == "查询执行成功，共返回 1 行。"

    await engine.dispose()


@pytest.mark.anyio
async def test_sql_executor_applies_max_rows() -> None:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as connection:
        await connection.exec_driver_sql("CREATE TABLE dish (id INTEGER)")
        await connection.exec_driver_sql("INSERT INTO dish (id) VALUES (1)")
        await connection.exec_driver_sql("INSERT INTO dish (id) VALUES (2)")

    executor = SQLExecutor(engine=engine)
    result = await executor.execute("SELECT id FROM dish ORDER BY id ASC", max_rows=1)

    assert result.row_count == 2
    assert result.rows == [{"id": 1}]
    assert result.truncated is True

    await engine.dispose()


@pytest.mark.anyio
async def test_sql_executor_returns_empty_result_summary() -> None:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")

    async with engine.begin() as connection:
        await connection.exec_driver_sql(
            "CREATE TABLE customers (id INTEGER, name TEXT)"
        )

    executor = SQLExecutor(engine=engine)
    result = await executor.execute("SELECT id, name FROM customers")

    assert result.row_count == 0
    assert result.rows == []
    assert result.columns == ["id", "name"]
    assert result.execution_summary == "查询执行成功，但没有返回记录。"

    await engine.dispose()


@pytest.mark.anyio
async def test_sql_executor_blocks_dangerous_sql() -> None:
    engine: AsyncEngine = create_async_engine("sqlite+aiosqlite:///:memory:")
    executor = SQLExecutor(engine=engine)

    with pytest.raises(DangerousSQLError):
        await executor.execute("DELETE FROM customers")

    await engine.dispose()
