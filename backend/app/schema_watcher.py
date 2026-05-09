"""Schema 变更检测器 -- 定时轮询 INFORMATION_SCHEMA，有变更时触发同步。"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from sqlalchemy import text

from app.database.engine import engine
from app.rag.schema_sync import sync_schema_metadata
from app.services.rag_service import invalidate_schema_cache

logger = logging.getLogger(__name__)


async def _compute_schema_signature(databases: list[str]) -> str:
    """查询 INFORMATION_SCHEMA.COLUMNS 计算 schema 签名。"""
    parts: list[str] = []
    async with engine.connect() as conn:
        for database in databases:
            result = await conn.execute(
                text(
                    "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, "
                    "COLUMN_COMMENT, IS_NULLABLE, COLUMN_DEFAULT "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = :database "
                    "ORDER BY TABLE_NAME, ORDINAL_POSITION"
                ),
                {"database": database},
            )
            for row in result:
                parts.append("|".join(str(v) for v in row))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


class SchemaWatcher:
    """后台轮询 INFORMATION_SCHEMA 检测 schema 变更。"""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    async def start(self, databases: list[str], interval_seconds: float) -> None:
        """启动后台轮询协程。"""
        if not databases:
            logger.info("Schema watcher skipped: no databases configured.")
            return
        self._task = asyncio.create_task(
            self._watch_loop(databases, interval_seconds),
            name="schema-watcher",
        )
        logger.info(
            "Schema watcher started (databases=%s, interval=%.1fs).",
            databases,
            interval_seconds,
        )

    async def stop(self) -> None:
        """停止后台轮询。"""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("Schema watcher stopped.")

    async def _watch_loop(self, databases: list[str], interval_seconds: float) -> None:
        last_signature: str | None = None
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                current = await _compute_schema_signature(databases)
                if last_signature is not None and current != last_signature:
                    logger.info(
                        "Schema change detected (old=%s, new=%s), syncing ...",
                        last_signature,
                        current,
                    )
                    await sync_schema_metadata()
                    await invalidate_schema_cache()
                    logger.info("Schema sync complete after change.")
                last_signature = current
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Schema watcher tick failed")


schema_watcher = SchemaWatcher()
