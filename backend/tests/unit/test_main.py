from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from app.main import lifespan


@pytest.mark.anyio
async def test_lifespan_refreshes_generated_config_yaml_on_startup() -> None:
    fake_settings = SimpleNamespace(
        schema_watcher_enabled=False,
        effective_database_names=["testdb"],
        schema_watcher_interval_seconds=30.0,
    )
    with patch("app.main.configure_logging") as configure_logging_mock, patch(
        "app.main.get_settings", return_value=fake_settings
    ), patch("app.main.refresh_generated_config_yaml", new_callable=AsyncMock) as refresh_mock, patch(
        "app.main.schema_watcher.start", new_callable=AsyncMock
    ) as start_mock, patch("app.main.schema_watcher.stop", new_callable=AsyncMock) as stop_mock:
        async with lifespan(FastAPI()):
            pass

    configure_logging_mock.assert_called_once()
    refresh_mock.assert_awaited_once()
    start_mock.assert_not_called()
    stop_mock.assert_awaited_once()
