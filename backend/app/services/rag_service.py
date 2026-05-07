from __future__ import annotations

import asyncio
import time

from app.config import get_settings
from app.rag.business_semantics import attach_business_semantics
from app.rag.schema_models import BusinessSemanticLayer, SchemaCatalog, SchemaTable
from app.rag.schema_sync import sync_schema_metadata


_catalog_cache: dict[str, SchemaCatalog] = {}
_catalog_cached_at: dict[str, float] = {}
_catalog_lock = asyncio.Lock()


def _ensure_business_semantics(catalog: SchemaCatalog) -> SchemaCatalog:
    if catalog.business_semantics is None:
        settings = get_settings()
        return attach_business_semantics(
            catalog,
            settings.business_semantic_override_path,
            yaml_enabled=settings.business_semantic_yaml_enabled,
            database_url=settings.database_url,
            yaml_dir=settings.business_semantic_yaml_dir,
        )
    return catalog


async def _get_schema_catalog(refresh: bool = False) -> SchemaCatalog:
    settings = get_settings()
    cache_key = settings.database_url
    ttl_seconds = max(0, int(settings.schema_cache_ttl_seconds))
    if ttl_seconds <= 0 or refresh:
        catalog = _ensure_business_semantics(await sync_schema_metadata())
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

        catalog = _ensure_business_semantics(await sync_schema_metadata())
        _catalog_cache[cache_key] = catalog
        _catalog_cached_at[cache_key] = now
        return catalog


def _score_table(question: str, table: SchemaTable, semantics: BusinessSemanticLayer | None = None) -> int:
    normalized = question.lower()
    score = 0
    for term in [table.name, table.description or "", *table.aliases, *table.business_terms, *table.searchable_terms]:
        term = term.strip()
        if term and term.lower() in normalized:
            score += 4 if term == table.name else 2
    for column in table.columns:
        for term in [column.name, column.description or "", *column.business_terms]:
            term = term.strip()
            if term and term.lower() in normalized:
                score += 1
    if semantics is not None:
        for signal in semantics.terms:
            if table.name in signal.tables and signal.term.lower() in normalized:
                score += 5 if "override" in signal.sources else 3
    return score


def _quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _matching_semantic_lines(table: SchemaTable, semantics: BusinessSemanticLayer | None) -> list[str]:
    if semantics is None:
        return []
    lines: list[str] = []
    matched_terms = [term for term in semantics.terms if table.name in term.tables][:12]
    if matched_terms:
        lines.append("business_terms: " + "; ".join(
            f"{term.term} -> tables={','.join(term.tables)} columns={','.join(term.columns)}"
            for term in matched_terms
        ))
    matched_metrics = [metric for metric in semantics.metrics if metric.table == table.name][:8]
    if matched_metrics:
        lines.append("metrics: " + "; ".join(
            f"{metric.name}={metric.table}.{metric.column} aliases={','.join(metric.aliases)}"
            for metric in matched_metrics
        ))
    matched_dimensions = [dimension for dimension in semantics.dimensions if dimension.table == table.name][:8]
    if matched_dimensions:
        lines.append("dimensions: " + "; ".join(
            f"{dimension.name}={dimension.table}.{dimension.column} aliases={','.join(dimension.aliases)}"
            for dimension in matched_dimensions
        ))
    matched_enums = [enum for enum in semantics.enums if enum.table == table.name][:6]
    if matched_enums:
        lines.append("enums: " + "; ".join(
            f"{enum.name}={enum.table}.{enum.column} values={enum.values}"
            for enum in matched_enums
        ))
    matched_filters = [item for item in semantics.default_filters if item.table == table.name][:6]
    if matched_filters:
        lines.append("default_filters: " + "; ".join(
            f"{item.name}: {item.condition}"
            for item in matched_filters
        ))
    return lines


def _render_table_context(table: SchemaTable, catalog: SchemaCatalog, selected_tables: set[str]) -> str:
    lines = [f"table {table.name}"]
    if table.description:
        lines.append(f"description: {table.description}")
    if table.primary_keys:
        lines.append(f"primary_keys: {', '.join(table.primary_keys)}")
    lines.extend(_matching_semantic_lines(table, catalog.business_semantics))
    lines.append("columns:")
    for column in table.columns:
        attrs = [column.data_type, "nullable" if column.nullable else "not null"]
        if column.is_primary_key:
            attrs.append("primary key")
        if column.default is not None:
            attrs.append(f"default: {column.default}")
        if column.description:
            attrs.append(f"description: {column.description}")
        if column.semantic_role:
            attrs.append(f"role: {column.semantic_role}")
        if column.business_terms:
            attrs.append(f"terms: {', '.join(column.business_terms)}")
        lines.append(f"- {column.name} ({'; '.join(attrs)})")

    relations = [
        relation
        for relation in catalog.relations
        if relation.from_table in selected_tables and relation.to_table in selected_tables
        and (relation.from_table == table.name or relation.to_table == table.name)
    ]
    if relations:
        lines.append("relations:")
        for relation in relations:
            hint = f"; hint: {relation.join_hint}" if relation.join_hint else ""
            relation_type = relation.relation_type or "relation"
            lines.append(
                f"- {_quote_identifier(relation.from_table)}.{_quote_identifier(relation.from_column)} -> "
                f"{_quote_identifier(relation.to_table)}.{_quote_identifier(relation.to_column)} ({relation_type}{hint})"
            )
    return "\n".join(lines)


class RagService:
    async def retrieve_relevant_schema(
        self,
        question: str,
        *,
        relevant_tables: list[str] | None = None,
        refresh_schema: bool = False,
        limit: int = 4,
    ) -> list[str]:
        catalog = await _get_schema_catalog(refresh=refresh_schema)
        if not catalog.tables:
            return []

        table_by_name = {table.name: table for table in catalog.tables}
        selected_names = [name for name in relevant_tables or [] if name in table_by_name]
        if not selected_names:
            scored = [
                (table.name, _score_table(question, table, catalog.business_semantics), index)
                for index, table in enumerate(catalog.tables)
            ]
            selected_names = [
                name
                for name, score, _index in sorted(scored, key=lambda item: (-item[1], item[2]))
                if score > 0
            ]
        if not selected_names:
            selected_names = [table.name for table in catalog.tables]

        selected_names = list(dict.fromkeys(selected_names))[:limit]
        selected_set = set(selected_names)
        return [
            _render_table_context(table_by_name[name], catalog, selected_set)
            for name in selected_names
        ]
