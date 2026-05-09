"""Tests for the schema change watcher."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.schema_watcher import SchemaWatcher, _compute_schema_signature


# ---------------------------------------------------------------------------
# _compute_schema_signature
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_compute_schema_signature_consistent_for_same_data() -> None:
    """Same row data should produce the same hash every time."""
    mock_rows = [
        ("users", "id", "int", "primary key", "NO", None),
        ("users", "name", "varchar", "user name", "YES", None),
    ]
    mock_result = AsyncMock()
    mock_result.__iter__ = lambda self: iter(mock_rows)

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock(return_value=mock_result)
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)

    with patch("app.schema_watcher.engine") as mock_engine:
        mock_engine.connect.return_value = mock_conn
        sig1 = await _compute_schema_signature(["testdb"])
        sig2 = await _compute_schema_signature(["testdb"])

    assert sig1 == sig2
    assert len(sig1) == 16


@pytest.mark.anyio
async def test_compute_schema_signature_differs_for_different_data() -> None:
    """Different row data should produce different hashes."""

    def _make_conn(rows):
        mock_result = AsyncMock()
        mock_result.__iter__ = lambda self: iter(rows)
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_conn.__aexit__ = AsyncMock(return_value=False)
        return mock_conn

    rows_a = [("users", "id", "int", "", "NO", None)]
    rows_b = [
        ("users", "id", "int", "", "NO", None),
        ("orders", "id", "int", "", "NO", None),
    ]

    with patch("app.schema_watcher.engine") as mock_engine:
        mock_engine.connect.return_value = _make_conn(rows_a)
        sig_a = await _compute_schema_signature(["testdb"])

    with patch("app.schema_watcher.engine") as mock_engine:
        mock_engine.connect.return_value = _make_conn(rows_b)
        sig_b = await _compute_schema_signature(["testdb"])

    assert sig_a != sig_b


# ---------------------------------------------------------------------------
# SchemaWatcher lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_schema_watcher_start_creates_task() -> None:
    """start() should create a background task."""
    watcher = SchemaWatcher()

    with patch("app.schema_watcher._compute_schema_signature", new_callable=AsyncMock, return_value="abc123"):
        await watcher.start(databases=["testdb"], interval_seconds=60.0)
        assert watcher._task is not None
        assert not watcher._task.done()

        await watcher.stop()
        assert watcher._task is None


@pytest.mark.anyio
async def test_schema_watcher_stop_cancels_task() -> None:
    """stop() should cancel the running task."""
    watcher = SchemaWatcher()

    with patch("app.schema_watcher._compute_schema_signature", new_callable=AsyncMock, return_value="abc123"):
        await watcher.start(databases=["testdb"], interval_seconds=60.0)
        task = watcher._task
        assert task is not None

        await watcher.stop()
        assert task.cancelled() or task.done()
        assert watcher._task is None


@pytest.mark.anyio
async def test_schema_watcher_start_skips_when_no_databases() -> None:
    """start() with empty databases should not create a task."""
    watcher = SchemaWatcher()
    await watcher.start(databases=[], interval_seconds=30.0)
    assert watcher._task is None


@pytest.mark.anyio
async def test_schema_watcher_stop_noop_when_not_started() -> None:
    """stop() on a fresh watcher should be a no-op."""
    watcher = SchemaWatcher()
    await watcher.stop()  # should not raise
    assert watcher._task is None


@pytest.mark.anyio
async def test_schema_watcher_exception_in_loop_does_not_propagate() -> None:
    """Exceptions inside the watch loop should be caught and not crash the task."""
    watcher = SchemaWatcher()

    with patch(
        "app.schema_watcher._compute_schema_signature",
        new_callable=AsyncMock,
        side_effect=RuntimeError("db down"),
    ):
        await watcher.start(databases=["testdb"], interval_seconds=0.01)
        # Let the loop tick a few times
        await asyncio.sleep(0.1)
        # Task should still be alive despite exceptions
        assert watcher._task is not None
        assert not watcher._task.done()

        await watcher.stop()


@pytest.mark.anyio
async def test_schema_watcher_refreshes_config_yaml_after_schema_change() -> None:
    watcher = SchemaWatcher()

    with patch(
        "app.schema_watcher._compute_schema_signature",
        new_callable=AsyncMock,
        side_effect=["sig-a", "sig-b"],
    ), patch("app.schema_watcher.sync_schema_metadata", new_callable=AsyncMock) as sync_mock, patch(
        "app.schema_watcher.refresh_generated_config_yaml", new_callable=AsyncMock
    ) as refresh_mock, patch("app.schema_watcher.invalidate_schema_cache", new_callable=AsyncMock) as invalidate_mock:
        await watcher.start(databases=["testdb"], interval_seconds=0.01)
        await asyncio.sleep(0.05)
        await watcher.stop()

    sync_mock.assert_awaited()
    refresh_mock.assert_awaited()
    invalidate_mock.assert_awaited()
