from __future__ import annotations

import asyncio
import time

from app.config import get_settings
from app.rag.join_path_planner import JoinPathPlanner
from app.rag.retriever import SchemaRetriever
from app.rag.schema_models import SchemaCatalog
from app.rag.schema_sync import sync_schema_metadata
from app.rag.semantic_brief import BusinessSemanticBriefBuilder, QuerySchemaPlan


_catalog_cache: dict[str, SchemaCatalog] = {}
_catalog_cached_at: dict[str, float] = {}
_catalog_lock = asyncio.Lock()


async def _get_schema_catalog(refresh: bool = False) -> SchemaCatalog:
    settings = get_settings()
    cache_key = settings.database_url
    ttl_seconds = max(0, int(settings.schema_cache_ttl_seconds))
    if ttl_seconds <= 0 or refresh:
        catalog = await sync_schema_metadata()
        if ttl_seconds > 0:
            async with _catalog_lock:
                _catalog_cache[cache_key] = catalog
                _catalog_cached_at[cache_key] = time.monotonic()
        return catalog

    now = time.monotonic()
    cached_at = _catalog_cached_at.get(cache_key)
    cached_catalog = _catalog_cache.get(cache_key)
    if cached_catalog is not None and cached_at is not None and (now - cached_at) < ttl_seconds:
        return cached_catalog

    async with _catalog_lock:
        now = time.monotonic()
        cached_at = _catalog_cached_at.get(cache_key)
        cached_catalog = _catalog_cache.get(cache_key)
        if cached_catalog is not None and cached_at is not None and (now - cached_at) < ttl_seconds:
            return cached_catalog

        catalog = await sync_schema_metadata()
        _catalog_cache[cache_key] = catalog
        _catalog_cached_at[cache_key] = now
        return catalog


class RagService:
    async def build_query_schema_plan(
        self,
        question: str,
        query_understanding: dict[str, object] | None = None,
        refresh_schema: bool = False,
    ) -> QuerySchemaPlan:
        catalog = await _get_schema_catalog(refresh=refresh_schema)
        retriever = SchemaRetriever(catalog)
        linking_result = retriever.link(question, query_understanding=query_understanding)
        join_path_plan = JoinPathPlanner().plan(linking_result, catalog)
        business_semantic_brief = BusinessSemanticBriefBuilder().build(
            question,
            linking_result,
            join_path_plan,
        )
        schema_context = retriever.render_linking_result(linking_result)
        return QuerySchemaPlan(
            schema_context=schema_context,
            schema_linking=linking_result,
            join_path_plan=join_path_plan,
            business_semantic_brief=business_semantic_brief,
        )

    async def retrieve_relevant_schema(self, question: str) -> list[str]:
        plan = await self.build_query_schema_plan(question)
        return plan.schema_context
