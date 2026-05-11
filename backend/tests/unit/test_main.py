from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from app.main import lifespan


@pytest.mark.anyio
async def test_lifespan_refreshes_schema_driven_artifacts_on_startup() -> None:
    fake_settings = SimpleNamespace(
        schema_watcher_enabled=False,
        effective_database_names=["testdb"],
        schema_watcher_interval_seconds=30.0,
    )
    with patch("app.main.configure_logging") as configure_logging_mock, patch(
        "app.main.get_settings", return_value=fake_settings
    ), patch("app.main.refresh_startup_schema_artifacts", new_callable=AsyncMock) as refresh_mock, patch(
        "app.main.schema_watcher.start", new_callable=AsyncMock
    ) as start_mock, patch("app.main.schema_watcher.stop", new_callable=AsyncMock) as stop_mock:
        async with lifespan(FastAPI()):
            pass

    configure_logging_mock.assert_called_once()
    refresh_mock.assert_awaited_once()
    start_mock.assert_not_called()
    stop_mock.assert_awaited_once()


@pytest.mark.anyio
async def test_lifespan_tolerates_startup_refresh_failure() -> None:
    fake_settings = SimpleNamespace(
        schema_watcher_enabled=False,
        effective_database_names=["testdb"],
        schema_watcher_interval_seconds=30.0,
    )
    with patch("app.main.configure_logging"), patch(
        "app.main.get_settings", return_value=fake_settings
    ), patch(
        "app.main.refresh_startup_schema_artifacts", new_callable=AsyncMock, side_effect=RuntimeError("db down")
    ) as refresh_mock, patch("app.main.logger") as logger_mock, patch(
        "app.main.schema_watcher.stop", new_callable=AsyncMock
    ) as stop_mock:
        async with lifespan(FastAPI()):
            pass

    refresh_mock.assert_awaited_once()
    logger_mock.exception.assert_called_once()
    stop_mock.assert_awaited_once()
