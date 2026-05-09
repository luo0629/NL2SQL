from __future__ import annotations

import asyncio
import time

from app.config import get_settings
from app.rag.business_semantics import attach_business_semantics, conversational_enum_mapping, conversational_enum_mapping_for_field
from app.rag.schema_models import BusinessSemanticLayer, SchemaCatalog, SchemaTable
from app.rag.schema_sync import sync_schema_metadata


_catalog_cache: dict[str, SchemaCatalog] = {}
_catalog_cached_at: dict[str, float] = {}
_catalog_lock = asyncio.Lock()


async def invalidate_schema_cache() -> None:
    """清除 schema 缓存，下次查询时重新同步。"""
    async with _catalog_lock:
        _catalog_cache.clear()
        _catalog_cached_at.clear()


def _ensure_business_semantics(catalog: SchemaCatalog) -> SchemaCatalog:
    if catalog.business_semantics is None:
        settings = get_settings()
        return attach_business_semantics(
            catalog,
            settings.business_semantic_override_path,
            yaml_enabled=settings.business_semantic_yaml_enabled,
            database_url=settings.schema_scope_key,
            yaml_dir=settings.business_semantic_yaml_dir,
        )
    return catalog


async def _get_schema_catalog(refresh: bool = False) -> SchemaCatalog:
    settings = get_settings()
    cache_key = settings.schema_scope_key
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
            if _table_identity(table) in signal.tables and signal.term.lower() in normalized:
                score += 5 if "override" in signal.sources else 3
    return score


def _quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _qualified_table_name(table: SchemaTable) -> str:
    if table.database:
        return f"{_quote_identifier(table.database)}.{_quote_identifier(table.name)}"
    return _quote_identifier(table.name)


def _table_identity(table: SchemaTable) -> str:
    return table.qualified_name


def _relation_endpoint(database: str | None, table: str, column: str) -> str:
    if database:
        return f"{_quote_identifier(database)}.{_quote_identifier(table)}.{_quote_identifier(column)}"
    return f"{_quote_identifier(table)}.{_quote_identifier(column)}"


def _build_table_lookup(tables: list[SchemaTable]) -> dict[str, SchemaTable]:
    lookup: dict[str, SchemaTable] = {}
    counts: dict[str, int] = {}
    for table in tables:
        counts[table.name.lower()] = counts.get(table.name.lower(), 0) + 1
    for table in tables:
        lookup[table.qualified_name] = table
        lookup[table.qualified_name.lower()] = table
        if counts[table.name.lower()] == 1:
            lookup[table.name] = table
            lookup[table.name.lower()] = table
    return lookup


def _matching_semantic_lines(table: SchemaTable, semantics: BusinessSemanticLayer | None) -> list[str]:
    if semantics is None:
        return []
    lines: list[str] = []
    table_identity = _table_identity(table)
    matched_terms = [term for term in semantics.terms if table_identity in term.tables][:12]
    if matched_terms:
        lines.append("business_terms: " + "; ".join(
            f"{term.term} -> tables={','.join(term.tables)} columns={','.join(term.columns)}"
            for term in matched_terms
        ))
    matched_metrics = [metric for metric in semantics.metrics if metric.table == table_identity][:8]
    if matched_metrics:
        lines.append("metrics: " + "; ".join(
            f"{metric.name}={metric.table}.{metric.column} aliases={','.join(metric.aliases)}"
            for metric in matched_metrics
        ))
    matched_dimensions = [dimension for dimension in semantics.dimensions if dimension.table == table_identity][:8]
    if matched_dimensions:
        lines.append("dimensions: " + "; ".join(
            f"{dimension.name}={dimension.table}.{dimension.column} aliases={','.join(dimension.aliases)}"
            for dimension in matched_dimensions
        ))
    matched_enums = [enum for enum in semantics.enums if enum.table == table_identity][:6]
    if matched_enums:
        lines.append("enums: " + "; ".join(
            f"{enum.name}={enum.table}.{enum.column} mapping={conversational_enum_mapping(enum)} values={enum.values}"
            for enum in matched_enums
        ))
    matched_filters = [item for item in semantics.default_filters if item.table == table_identity][:6]
    if matched_filters:
        lines.append("default_filters: " + "; ".join(
            f"{item.name}: {item.condition}"
            for item in matched_filters
        ))
    return lines


def _build_table_relations_overview(selected_table_names: set[str]) -> str:
    """Build a natural-language overview of table relations for the LLM.

    Reads from table_relations.yaml via AppConfig.  Returns an empty string
    when no relation data is available or no selected tables match.
    """
    from app.config_loader import get_app_config

    relations_config = get_app_config().table_relations
    if not relations_config:
        return ""

    sections: list[str] = []

    # --- Part 1: Table profiles (responsibilities & routing) ---
    profiles = relations_config.get("table_profiles", {})
    profile_lines: list[str] = []
    for table_name, profile in profiles.items():
        if table_name not in selected_table_names:
            continue
        desc = profile.get("description", "")
        hints = profile.get("routing_hints", [])
        cross_fields = profile.get("cross_table_fields", [])
        parts: list[str] = []
        if desc:
            parts.append(f"  - {table_name}: {desc}")
        for hint in hints:
            intent = hint.get("intent", "")
            hint_desc = hint.get("description", "")
            if intent and hint_desc:
                parts.append(f"    - {intent}: {hint_desc}")
        for cf in cross_fields:
            field = cf.get("field", "")
            cf_desc = cf.get("description", "")
            if field and cf_desc:
                parts.append(f"    - field `{field}`: {cf_desc}")
        if parts:
            profile_lines.extend(parts)
    if profile_lines:
        sections.append("## Table Responsibilities & Routing\n" + "\n".join(profile_lines))

    # --- Part 2: Relations (join guidance) ---
    relations = relations_config.get("relations", [])
    relation_lines: list[str] = []
    for rel in relations:
        from_table = rel.get("from_table", "")
        to_table = rel.get("to_table", "")
        if from_table not in selected_table_names or to_table not in selected_table_names:
            continue
        from_col = rel.get("from_column", "")
        to_col = rel.get("to_column", "")
        rel_type = rel.get("relation_type", "relation")
        business_meaning = rel.get("business_meaning", "")
        join_direction = rel.get("join_direction", "")
        join_hint = rel.get("join_hint", "")
        line = f"  - {from_table}.{from_col} -> {to_table}.{to_col} ({rel_type})"
        if business_meaning:
            line += f"\n    meaning: {business_meaning}"
        if join_direction:
            line += f"\n    join: {join_direction}"
        elif join_hint:
            line += f"\n    hint: {join_hint}"
        relation_lines.append(line)
    if relation_lines:
        sections.append("## Table Relations (Join Guidance)\n" + "\n".join(relation_lines))

    # --- Part 2b: Multi-hop paths ---
    multi_hop = relations_config.get("multi_hop_paths", [])
    hop_lines: list[str] = []
    for path_entry in multi_hop:
        path_tables = path_entry.get("path", [])
        if not any(t in selected_table_names for t in path_tables):
            continue
        desc = path_entry.get("description", "")
        scenario = path_entry.get("business_scenario", "")
        join_chain = path_entry.get("join_chain", [])
        chain_str = " -> ".join(
            f"{step.get('from', '')} -> {step.get('to', '')}" for step in join_chain
        )
        line = f"  - Path: {' -> '.join(path_tables)}"
        if desc:
            line += f"\n    description: {desc}"
        if scenario:
            line += f"\n    scenario: {scenario}"
        if chain_str:
            line += f"\n    join_chain: {chain_str}"
        hop_lines.append(line)
    if hop_lines:
        sections.append("## Multi-Hop Join Paths\n" + "\n".join(hop_lines))

    return "\n\n".join(sections)


def _render_table_context(table: SchemaTable, catalog: SchemaCatalog, selected_tables: set[str]) -> str:
    table_identity = _table_identity(table)
    lines = [f"table {table_identity} qualified {_qualified_table_name(table)}"]
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
        enum_mapping = conversational_enum_mapping_for_field(catalog.business_semantics, table_identity, column.name)
        if column.description:
            description = column.description
            if enum_mapping:
                description = f"{description}; enum_mapping: {enum_mapping}"
            attrs.append(f"description: {description}")
        elif enum_mapping:
            attrs.append(f"description: enum_mapping: {enum_mapping}")
        if column.semantic_role:
            attrs.append(f"role: {column.semantic_role}")
        if column.business_terms:
            attrs.append(f"terms: {', '.join(column.business_terms)}")
        lines.append(f"- {column.name} ({'; '.join(attrs)})")

    relations = [
        relation
        for relation in catalog.relations
        if relation.from_qualified_table in selected_tables and relation.to_qualified_table in selected_tables
        and (relation.from_qualified_table == table_identity or relation.to_qualified_table == table_identity)
    ]
    if relations:
        lines.append("relations:")
        for relation in relations:
            hint = f"; hint: {relation.join_hint}" if relation.join_hint else ""
            relation_type = relation.relation_type or "relation"
            lines.append(
                f"- {_relation_endpoint(relation.from_database, relation.from_table, relation.from_column)} -> "
                f"{_relation_endpoint(relation.to_database, relation.to_table, relation.to_column)} ({relation_type}{hint})"
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

        table_by_name = _build_table_lookup(catalog.tables)
        selected_names = [_table_identity(table_by_name[name]) for name in relevant_tables or [] if name in table_by_name]
        if not selected_names:
            scored = [
                (_table_identity(table), _score_table(question, table, catalog.business_semantics), index)
                for index, table in enumerate(catalog.tables)
            ]
            selected_names = [
                name
                for name, score, _index in sorted(scored, key=lambda item: (-item[1], item[2]))
                if score > 0
            ]
        if not selected_names:
            selected_names = [_table_identity(table) for table in catalog.tables]

        selected_names = list(dict.fromkeys(selected_names))[:limit]
        selected_set = set(selected_names)

        # Build table relations overview from YAML config
        relations_overview = _build_table_relations_overview(selected_set)

        table_contexts = [
            _render_table_context(table_by_name[name], catalog, selected_set)
            for name in selected_names
        ]

        # Prepend relations overview as the first element if available
        if relations_overview:
            return [relations_overview] + table_contexts
        return table_contexts
