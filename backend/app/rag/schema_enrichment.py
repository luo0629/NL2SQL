from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TableEnrichment(BaseModel):
    aliases: list[str] = Field(default_factory=list)
    business_terms: list[str] = Field(default_factory=list)


class ColumnEnrichment(BaseModel):
    business_terms: list[str] = Field(default_factory=list)
    semantic_role: str | None = None
    cross_table_diff: str | None = None


class RelationEnrichment(BaseModel):
    confidence: str | None = None
    join_hint: str | None = None


class SchemaEnrichment(BaseModel):
    table_enrichments: dict[str, TableEnrichment] = Field(default_factory=dict)
    column_enrichments: dict[str, dict[str, ColumnEnrichment]] = Field(default_factory=dict)
    relation_enrichments: dict[str, RelationEnrichment] = Field(default_factory=dict)


def _build_enrichment_from_config() -> SchemaEnrichment:
    """Build SchemaEnrichment from YAML config files via AppConfig."""
    from app.config_loader import get_app_config

    app_config = get_app_config()
    table_enrichments: dict[str, TableEnrichment] = {}
    column_enrichments: dict[str, dict[str, ColumnEnrichment]] = {}
    relation_enrichments: dict[str, RelationEnrichment] = {}

    # --- Table enrichments from business_terms.yaml ---
    terms_config = app_config.business_terms
    for term_entry in terms_config.get("terms", []):
        alias = term_entry.get("alias", "")
        tables = term_entry.get("tables", [])
        business_terms = term_entry.get("business_terms", [])
        for table_ref in tables:
            table_key = _normalize_key(table_ref)
            if table_key not in table_enrichments:
                table_enrichments[table_key] = TableEnrichment()
            if alias:
                existing_aliases = table_enrichments[table_key].aliases
                if alias not in existing_aliases:
                    existing_aliases.append(alias)
            for term in business_terms:
                existing_terms = table_enrichments[table_key].business_terms
                if term not in existing_terms:
                    existing_terms.append(term)

    # --- Column enrichments from field_semantics.yaml ---
    fields_config = app_config.field_semantics
    for table_ref, fields in fields_config.get("fields", {}).items():
        table_key = _normalize_key(table_ref)
        table_columns: dict[str, ColumnEnrichment] = {}
        for column_name, field_data in fields.items():
            if not isinstance(field_data, dict):
                continue
            col_key = _normalize_key(column_name)
            business_terms = field_data.get("business_terms", []) or []
            semantic_role = field_data.get("semantic_role")
            cross_table_diff = field_data.get("cross_table_diff")
            if business_terms or semantic_role or cross_table_diff:
                table_columns[col_key] = ColumnEnrichment(
                    business_terms=business_terms,
                    semantic_role=semantic_role,
                    cross_table_diff=cross_table_diff,
                )
        if table_columns:
            column_enrichments[table_key] = table_columns

    # --- Relation enrichments from table_relations.yaml ---
    relations_config = app_config.table_relations
    for rel in relations_config.get("relations", []):
        from_table = rel.get("from_table", "")
        from_column = rel.get("from_column", "")
        to_table = rel.get("to_table", "")
        to_column = rel.get("to_column", "")
        if not all([from_table, from_column, to_table, to_column]):
            continue
        key = _relation_key(from_table, from_column, to_table, to_column)
        confidence = rel.get("confidence")
        join_hint = rel.get("join_hint") or rel.get("description")
        if confidence or join_hint:
            relation_enrichments[key] = RelationEnrichment(
                confidence=confidence,
                join_hint=join_hint,
            )

    return SchemaEnrichment(
        table_enrichments=table_enrichments,
        column_enrichments=column_enrichments,
        relation_enrichments=relation_enrichments,
    )


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("`", "")


def _table_key_candidates(value: str) -> list[str]:
    normalized = _normalize_key(value)
    if "." not in normalized:
        return [normalized]
    short_name = normalized.rsplit(".", 1)[-1]
    return [normalized, short_name]


def _relation_key(from_table: str, from_column: str, to_table: str, to_column: str) -> str:
    return f"{_normalize_key(from_table)}.{_normalize_key(from_column)}->{_normalize_key(to_table)}.{_normalize_key(to_column)}"


def load_schema_enrichment() -> SchemaEnrichment:
    return _build_enrichment_from_config()


def get_table_enrichment(enrichment: SchemaEnrichment, table_name: str) -> TableEnrichment:
    table_candidates = _table_key_candidates(table_name)
    for table_key in table_candidates:
        table_enrichment = enrichment.table_enrichments.get(table_key)
        if table_enrichment is not None:
            return table_enrichment
    for existing_key, table_enrichment in enrichment.table_enrichments.items():
        if any(existing_key.endswith(table_key) for table_key in table_candidates):
            return table_enrichment
    return TableEnrichment()


def get_column_enrichment(
    enrichment: SchemaEnrichment,
    *,
    table_name: str,
    column_name: str,
) -> ColumnEnrichment:
    table_candidates = _table_key_candidates(table_name)
    column_key = _normalize_key(column_name)
    for table_key in table_candidates:
        table_columns = enrichment.column_enrichments.get(table_key, {})
        column_enrichment = table_columns.get(column_key)
        if column_enrichment is not None:
            return column_enrichment
    for existing_key, table_columns in enrichment.column_enrichments.items():
        if any(existing_key.endswith(table_key) for table_key in table_candidates):
            column_enrichment = table_columns.get(column_key)
            if column_enrichment is not None:
                return column_enrichment
    return ColumnEnrichment()


def get_relation_enrichment(
    enrichment: SchemaEnrichment,
    *,
    from_table: str,
    from_column: str,
    to_table: str,
    to_column: str,
) -> RelationEnrichment:
    from_column_key = _normalize_key(from_column)
    to_column_key = _normalize_key(to_column)
    from_table_candidates = _table_key_candidates(from_table)
    to_table_candidates = _table_key_candidates(to_table)
    for from_table_key in from_table_candidates:
        for to_table_key in to_table_candidates:
            key = f"{from_table_key}.{from_column_key}->{to_table_key}.{to_column_key}"
            relation = enrichment.relation_enrichments.get(key)
            if relation is not None:
                return relation
    for key, relation in enrichment.relation_enrichments.items():
        left_key, right_key = key.split("->", 1)
        left_table_key, left_column_key = left_key.rsplit(".", 1)
        right_table_key, right_column_key = right_key.rsplit(".", 1)
        if (
            left_column_key == from_column_key
            and right_column_key == to_column_key
            and any(left_table_key.endswith(from_table_key) for from_table_key in from_table_candidates)
            and any(right_table_key.endswith(to_table_key) for to_table_key in to_table_candidates)
        ):
            return relation
    return RelationEnrichment()
