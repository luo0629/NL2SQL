from __future__ import annotations

import asyncio
import time

from app.config import get_settings
from app.rag.retriever import SchemaRetriever
from app.rag.schema_models import SchemaCatalog
from app.rag.schema_sync import sync_schema_metadata


_catalog_cache: SchemaCatalog | None = None
_catalog_cached_at: float | None = None
_catalog_lock = asyncio.Lock()


async def _get_schema_catalog() -> SchemaCatalog:
    global _catalog_cache, _catalog_cached_at

    settings = get_settings()
    ttl_seconds = max(0, int(settings.schema_cache_ttl_seconds))
    if ttl_seconds <= 0:
        return await sync_schema_metadata()

    now = time.monotonic()
    cached_at = _catalog_cached_at
    if _catalog_cache is not None and cached_at is not None and (now - cached_at) < ttl_seconds:
        return _catalog_cache

    async with _catalog_lock:
        now = time.monotonic()
        cached_at = _catalog_cached_at
        if _catalog_cache is not None and cached_at is not None and (now - cached_at) < ttl_seconds:
            return _catalog_cache

        catalog = await sync_schema_metadata()
        _catalog_cache = catalog
        _catalog_cached_at = now
        return catalog


class RagService:
    async def retrieve_relevant_schema(self, question: str) -> list[str]:
        catalog = await _get_schema_catalog()
        retriever = SchemaRetriever(catalog)
        return retriever.search(question)
