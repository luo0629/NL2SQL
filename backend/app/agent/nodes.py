from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, cast

from app.agent.state import AgentState
from app.config import get_settings
from app.database.executor import SQLExecutor
from app.rag.business_semantics import conversational_enum_mapping, conversational_enum_mapping_for_field
from app.agent.value_validation import MissingValueIssue, build_missing_value_prompt, extract_value_predicates
from app.rag.schema_models import BusinessSemanticLayer, SchemaCatalog, SchemaRelation, SchemaTable
from app.services.llm_service import LLMService
from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


logger = logging.getLogger(__name__)


def _extract_text(content: str | list[str | dict[str, str]]) -> str:
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _extract_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return None
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _invoke_model_text(model: Any, prompt: str) -> str | None:
    try:
        response = model.invoke(prompt)
        return _extract_text(cast(str | list[str | dict[str, str]], response.content)).strip()
    except Exception:
        return None


def _invoke_model_json(model: Any, prompt: str) -> dict[str, Any] | None:
    content = _invoke_model_text(model, prompt)
    if content is None:
        return None
    return _extract_json_object(content)


async def _ainvoke_model_text(
    model: Any,
    prompt: str,
    *,
    timeout_seconds: float | None = None,
    stage: str,
) -> tuple[str | None, str | None]:
    started_at = time.monotonic()
    try:
        if hasattr(model, "ainvoke"):
            invocation = model.ainvoke(prompt)
        else:
            invocation = asyncio.to_thread(model.invoke, prompt)

        if timeout_seconds is not None and timeout_seconds > 0:
            response = await asyncio.wait_for(invocation, timeout=timeout_seconds)
        else:
            response = await invocation
        elapsed_ms = (time.monotonic() - started_at) * 1000
        logger.info("llm.%s.end duration_ms=%.2f", stage, elapsed_ms)
        return _extract_text(cast(str | list[str | dict[str, str]], response.content)).strip(), None
    except TimeoutError:
        logger.warning("llm.%s.timeout timeout_seconds=%.2f", stage, timeout_seconds or 0)
        return None, "timeout"
    except Exception as error:
        logger.warning("llm.%s.error error_class=%s", stage, error.__class__.__name__)
        return None, error.__class__.__name__


async def _ainvoke_model_json(
    model: Any,
    prompt: str,
    *,
    timeout_seconds: float | None = None,
    stage: str,
) -> tuple[dict[str, Any] | None, str | None]:
    content, error = await _ainvoke_model_text(
        model,
        prompt,
        timeout_seconds=timeout_seconds,
        stage=stage,
    )
    if content is None:
        return None, error
    payload = _extract_json_object(content)
    if payload is None:
        return None, "invalid_json"
    return payload, None


def _question_text(state: AgentState) -> str:
    return (state.get("user_input") or state.get("question") or "").strip()


def _current_sql(state: AgentState) -> str:
    return state.get("generated_sql") or state.get("sql") or ""


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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = value.strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _normalize_sql(candidate: str) -> str:
    cleaned = candidate.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
    if not cleaned.endswith(";"):
        cleaned = f"{cleaned};"
    return cleaned


def _catalog_tables(catalog: SchemaCatalog | None) -> list[SchemaTable]:
    return catalog.tables if catalog and catalog.tables else []


def _catalog_semantics(catalog: SchemaCatalog | None) -> BusinessSemanticLayer | None:
    return catalog.business_semantics if catalog and catalog.business_semantics else None


_LOW_VALUE_SEMANTIC_TERMS = {
    "id",
    "主键",
    "名称",
    "名字",
    "name",
    "status",
    "type",
    "sort",
    "image",
    "description",
    "remark",
    "create_time",
    "update_time",
    "create_user",
    "update_user",
    "deleted",
}

_EXPLICIT_IDENTIFIER_TERMS = (
    "id",
    "编号",
    "代码",
    "编码",
    "号码",
    "单号",
    "code",
    " no",
    "number",
)
_INTERNAL_AUDIT_COLUMNS = {
    "create_user",
    "update_user",
    "created_by",
    "updated_by",
    "creator_id",
    "updater_id",
    "created_user_id",
    "updated_user_id",
    "deleted",
    "is_deleted",
    "delete_flag",
}
_INTERNAL_AUDIT_TIME_COLUMNS = {
    "create_time",
    "update_time",
    "created_at",
    "updated_at",
    "create_date",
    "update_date",
    "modified_at",
    "modified_time",
}
_DISPLAY_NAME_TOKENS = ("name", "title", "label", "subject", "summary", "description", "detail", "remark")
_BUSINESS_VALUE_TOKENS = (
    "amount",
    "price",
    "total",
    "status",
    "time",
    "date",
    "quantity",
    "qty",
    "count",
    "number",
    "phone",
    "type",
)


def _is_low_value_semantic_term(term: str) -> bool:
    normalized = term.strip().lower()
    if not normalized:
        return True
    if normalized in _LOW_VALUE_SEMANTIC_TERMS:
        return True
    if normalized.endswith("_id") or normalized.endswith("id"):
        return True
    return len(normalized) <= 1


def _question_explicitly_requests_identifier(question: str) -> bool:
    normalized = f" {(question or '').lower()} "
    return any(term in normalized for term in _EXPLICIT_IDENTIFIER_TERMS)


def _is_identifier_like_column_name(column_name: str) -> bool:
    normalized = column_name.lower()
    return normalized == "id" or normalized.endswith("_id") or normalized.endswith("_code") or normalized.endswith("_no") or normalized == "code"


def _is_internal_output_column(column: Any) -> bool:
    name = str(getattr(column, "name", "")).lower()
    role = str(getattr(column, "semantic_role", "") or "").lower()
    if role in {"identifier", "foreign_key", "internal"}:
        return True
    if _is_identifier_like_column_name(name):
        return True
    if name in _INTERNAL_AUDIT_COLUMNS or name in _INTERNAL_AUDIT_TIME_COLUMNS:
        return True
    return False


def _column_output_hint(column: Any) -> str:
    name = str(getattr(column, "name", "")).lower()
    role = str(getattr(column, "semantic_role", "") or "").lower()
    if role == "foreign_key" or name.endswith("_id"):
        return "join/filter/internal; do not select by default"
    if role in {"identifier", "internal"} or _is_identifier_like_column_name(name) or name in _INTERNAL_AUDIT_COLUMNS:
        return "internal identifier; do not select by default"
    if name in _INTERNAL_AUDIT_TIME_COLUMNS:
        return "audit timestamp; select only when the user asks for creation/update time"
    return "business-readable"


def _display_column_rank(column: Any, *, include_internal: bool = False) -> tuple[int, int]:
    name = str(getattr(column, "name", "")).lower()
    role = str(getattr(column, "semantic_role", "") or "").lower()
    if include_internal and (role in {"identifier", "foreign_key"} or _is_identifier_like_column_name(name)):
        return (-1, 0)
    if not include_internal and _is_internal_output_column(column):
        return (90, 0)
    if role == "dimension" and any(token in name for token in _DISPLAY_NAME_TOKENS):
        return (0, 0)
    if any(token in name for token in _DISPLAY_NAME_TOKENS):
        return (1, 0)
    if role in {"metric", "dimension", "timestamp"}:
        return (2, 0)
    if any(token in name for token in _BUSINESS_VALUE_TOKENS):
        return (3, 0)
    if _is_internal_output_column(column):
        return (80, 0)
    return (10, 0)


def _select_display_columns(table: SchemaTable, question: str, limit: int = 5) -> list[str]:
    include_internal = _question_explicitly_requests_identifier(question)
    ranked = sorted(
        enumerate(table.columns),
        key=lambda item: (*_display_column_rank(item[1], include_internal=include_internal), item[0]),
    )
    selected = [column.name for _index, column in ranked if include_internal or not _is_internal_output_column(column)]
    if selected:
        return selected[:limit]
    fallback = [column.name for column in table.columns[:limit]]
    return fallback or ["*"]


def _semantic_item_matches_question(name: str, aliases: list[str], question: str) -> bool:
    if not question:
        return False
    normalized = question.lower()
    for term in [name, *aliases]:
        candidate = term.strip().lower()
        if candidate and not _is_low_value_semantic_term(candidate) and candidate in normalized:
            return True
    return False


def _semantic_matches(question: str, semantics: BusinessSemanticLayer | None, table_names: set[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if semantics is None:
        return []
    normalized = question.lower()
    matches: list[dict[str, Any]] = []
    allowed = table_names or set()
    for term in semantics.terms:
        if _is_low_value_semantic_term(term.term):
            continue
        if term.term.lower() not in normalized:
            continue
        if allowed and not any(table in allowed for table in term.tables):
            continue
        matches.append(
            {
                "term": term.term,
                "kind": term.kind,
                "tables": [table for table in term.tables if not allowed or table in allowed],
                "columns": [column for column in term.columns if not allowed or any(column.startswith(f"{table}.") for table in allowed)],
                "sources": term.sources,
            }
        )
        if len(matches) >= limit:
            break
    return matches


def _render_semantic_context(semantics: BusinessSemanticLayer | None, selected_table_names: set[str], question: str = "") -> str:
    if semantics is None:
        return ""
    lines: list[str] = []
    matched = _semantic_matches(question, semantics, selected_table_names, limit=16) if question else []
    if matched:
        lines.append("Matched business terms:")
        for item in matched:
            lines.append(
                f"- {item['term']} ({item['kind']}): tables={', '.join(item['tables'])}; columns={', '.join(item['columns'])}; sources={', '.join(item['sources'])}"
            )
    metrics = [
        metric
        for metric in semantics.metrics
        if metric.table in selected_table_names and _semantic_item_matches_question(metric.name, metric.aliases, question)
    ][:8]
    if metrics:
        lines.append("Business metrics:")
        for metric in metrics:
            expr = f"; expression={metric.expression}" if metric.expression else ""
            lines.append(f"- {metric.name}: {metric.table}.{metric.column}; aliases={', '.join(metric.aliases)}{expr}; source={metric.source}")
    dimensions = [
        dimension
        for dimension in semantics.dimensions
        if dimension.table in selected_table_names and _semantic_item_matches_question(dimension.name, dimension.aliases, question)
    ][:8]
    if dimensions:
        lines.append("Business dimensions:")
        for dimension in dimensions:
            lines.append(f"- {dimension.name}: {dimension.table}.{dimension.column}; aliases={', '.join(dimension.aliases)}; source={dimension.source}")
    enums = [
        enum
        for enum in semantics.enums
        if enum.table in selected_table_names
        and _semantic_item_matches_question(
            enum.name,
            enum.aliases + list(enum.values.values()) + [alias for aliases in enum.value_aliases.values() for alias in aliases],
            question,
        )
    ][:6]
    if enums:
        lines.append("Business enums:")
        for enum in enums:
            mapping = conversational_enum_mapping(enum)
            lines.append(f"- {enum.name}: {enum.table}.{enum.column}; mapping={mapping}; values={json.dumps(enum.values, ensure_ascii=False)}; source={enum.source}")
    filters = [
        item
        for item in semantics.default_filters
        if item.table in selected_table_names and _semantic_item_matches_question(item.name, item.aliases, question)
    ][:6]
    if filters:
        lines.append("Default filters from validated overrides:")
        for item in filters:
            lines.append(f"- {item.name}: table={item.table}; condition={item.condition}; aliases={', '.join(item.aliases)}")
    return "\n".join(lines)


def _table_score(question: str, table: SchemaTable, semantics: BusinessSemanticLayer | None = None) -> int:
    normalized = question.lower()
    score = 0
    candidates = [table.name, _table_identity(table), table.description or "", *table.aliases, *table.business_terms]
    for term in candidates:
        term = term.strip()
        if term and term.lower() in normalized:
            score += 6 if term in {table.name, _table_identity(table)} else 4
    for column in table.columns:
        column_terms = [column.name, column.description or "", *(column.business_terms or [])]
        for term in column_terms:
            term = term.strip()
            if term and not _is_low_value_semantic_term(term) and term.lower() in normalized:
                score += 2
    if semantics is not None:
        for signal in semantics.terms:
            if _is_low_value_semantic_term(signal.term):
                continue
            if _table_identity(table) in signal.tables and signal.term.lower() in normalized:
                score += 8 if "override" in signal.sources else 4
    return score


def _expand_selected_tables_with_relations(selected: list[str], catalog: SchemaCatalog | None, limit: int = 4) -> list[str]:
    if catalog is None or len(selected) < 2 or len(selected) >= limit:
        return selected[:limit]
    selected_set = set(selected)
    expanded = list(selected)

    # Preserve real-schema join ability without broadening single-table queries:
    # add only a one-hop bridge table that connects two already-selected tables.
    relation_pairs = [({relation.from_qualified_table, relation.to_qualified_table}, relation) for relation in catalog.relations]
    for first_endpoints, _first_relation in relation_pairs:
        if len(expanded) >= limit:
            break
        bridge_candidates = first_endpoints - selected_set
        if len(bridge_candidates) != 1 or not (first_endpoints & selected_set):
            continue
        bridge = next(iter(bridge_candidates))
        for second_endpoints, _second_relation in relation_pairs:
            if bridge not in second_endpoints:
                continue
            if second_endpoints & (selected_set - first_endpoints):
                expanded.append(bridge)
                selected_set.add(bridge)
                break

    return expanded[:limit]


def _selected_join_relations(selected_table_names: set[str], catalog: SchemaCatalog | None) -> list[str]:
    if catalog is None:
        return []
    relations: list[str] = []
    for relation in catalog.relations:
        if relation.from_qualified_table not in selected_table_names or relation.to_qualified_table not in selected_table_names:
            continue
        description = f"{relation.from_qualified_table}.{relation.from_column} -> {relation.to_qualified_table}.{relation.to_column}"
        if relation.relation_type:
            description += f" ({relation.relation_type})"
        if relation.confidence:
            description += f" [confidence={relation.confidence}]"
        if relation.ranking_score is not None:
            description += f" [score={relation.ranking_score:.2f}]"
        if relation.validation_summary:
            description += f" [validation={relation.validation_summary}]"
        if relation.join_hint:
            description += f" [{relation.join_hint}]"
        relations.append(description)
    return relations


def _table_ref_variants(table_ref: str) -> set[str]:
    normalized = table_ref.strip().lower()
    if not normalized:
        return set()
    parts = [part for part in normalized.split(".") if part]
    variants = {normalized}
    if parts:
        variants.add(parts[-1])
    if len(parts) >= 2:
        variants.add(".".join(parts[-2:]))
    return variants


def _format_table_column_reference(table_ref: str, column: str) -> str:
    parts = [part for part in table_ref.split(".") if part]
    if len(parts) >= 2:
        return f"{_quote_identifier(parts[-2])}.{_quote_identifier(parts[-1])}.{_quote_identifier(column)}"
    return f"{_quote_identifier(table_ref)}.{_quote_identifier(column)}"


def _join_relation_pair_key(relation: SchemaRelation) -> tuple[str, str]:
    return tuple(sorted((relation.from_qualified_table.lower(), relation.to_qualified_table.lower())))


def _join_relation_identity(relation: SchemaRelation) -> tuple[str, str, str, str]:
    return (
        relation.from_qualified_table.lower(),
        relation.from_column.lower(),
        relation.to_qualified_table.lower(),
        relation.to_column.lower(),
    )


def _join_relation_pair_matches_tables(relation: SchemaRelation, left_table: str, right_table: str) -> bool:
    left_variants = _table_ref_variants(left_table)
    right_variants = _table_ref_variants(right_table)
    relation_from_variants = _table_ref_variants(relation.from_qualified_table)
    relation_to_variants = _table_ref_variants(relation.to_qualified_table)
    return (
        bool(left_variants & relation_from_variants)
        and bool(right_variants & relation_to_variants)
    ) or (
        bool(left_variants & relation_to_variants)
        and bool(right_variants & relation_from_variants)
    )


def _join_relation_matches(
    relation: SchemaRelation,
    left_table: str,
    left_column: str,
    right_table: str,
    right_column: str,
) -> bool:
    if not _join_relation_pair_matches_tables(relation, left_table, right_table):
        return False
    left_column_normalized = left_column.lower()
    right_column_normalized = right_column.lower()
    return (
        left_column_normalized == relation.from_column.lower()
        and right_column_normalized == relation.to_column.lower()
    ) or (
        left_column_normalized == relation.to_column.lower()
        and right_column_normalized == relation.from_column.lower()
    )


def _graph_edge_tags_by_relation(catalog: SchemaCatalog | None) -> dict[tuple[str, str, str, str], list[str]]:
    if catalog is None or catalog.relationship_graph is None:
        return {}
    tags: dict[tuple[str, str, str, str], list[str]] = {}
    for edge in catalog.relationship_graph.edges:
        identity = (
            edge.from_table.lower(),
            edge.from_column.lower(),
            edge.to_table.lower(),
            edge.to_column.lower(),
        )
        tags[identity] = edge.governance_tags
        tags[(edge.to_table.lower(), edge.to_column.lower(), edge.from_table.lower(), edge.from_column.lower())] = edge.governance_tags
    return tags


def _relation_confidence_bonus(confidence: str | None) -> float:
    normalized = (confidence or "").strip().lower()
    if normalized == "high":
        return 12.0
    if normalized == "medium":
        return 6.0
    if normalized == "low":
        return 0.0
    return 2.0 if normalized else 0.0


def _relation_type_bonus(relation_type: str | None) -> float:
    normalized = (relation_type or "").strip().lower()
    if normalized == "foreign_key":
        return 40.0
    if normalized == "configured":
        return 24.0
    if normalized == "inferred-shared-key":
        return 10.0
    return 4.0 if normalized else 0.0


def _relation_governance_penalty(tags: list[str]) -> tuple[float, list[str]]:
    penalty = 0.0
    reasons: list[str] = []
    lowered = {tag.lower() for tag in tags}
    if "deprecated_endpoint" in lowered:
        penalty += 18.0
        reasons.append("命中了疑似已废弃字段")
    elif "suspected_endpoint" in lowered:
        penalty += 10.0
        reasons.append("命中了疑似保留/临时字段")
    if "runtime_validated" not in lowered:
        penalty += 2.0
    return penalty, reasons


def _relation_preference_score(relation: SchemaRelation, tags: list[str]) -> tuple[float, list[str]]:
    score = _relation_type_bonus(relation.relation_type) + _relation_confidence_bonus(relation.confidence)
    reasons: list[str] = []
    if relation.ranking_score is not None:
        score += relation.ranking_score
        reasons.append(f"score={relation.ranking_score:.2f}")
    if relation.validation_summary:
        score += 4.0
        reasons.append("含 runtime probe")
    penalty, penalty_reasons = _relation_governance_penalty(tags)
    score -= penalty
    reasons.extend(penalty_reasons)
    if relation.confidence:
        reasons.append(f"confidence={relation.confidence}")
    if relation.relation_type:
        reasons.append(f"type={relation.relation_type}")
    return score, reasons


def _sorted_selected_relations(selected_table_names: set[str], catalog: SchemaCatalog | None) -> list[tuple[SchemaRelation, list[str], float, list[str]]]:
    if catalog is None:
        return []
    edge_tags = _graph_edge_tags_by_relation(catalog)
    selected: list[tuple[SchemaRelation, list[str], float, list[str]]] = []
    for relation in catalog.relations:
        if relation.from_qualified_table not in selected_table_names or relation.to_qualified_table not in selected_table_names:
            continue
        tags = edge_tags.get(_join_relation_identity(relation), [])
        score, reasons = _relation_preference_score(relation, tags)
        selected.append((relation, tags, score, reasons))
    return sorted(
        selected,
        key=lambda item: (
            _join_relation_pair_key(item[0]),
            -item[2],
            item[0].from_column.lower(),
            item[0].to_column.lower(),
        ),
    )


def _render_join_priority_context(selected_table_names: set[str], catalog: SchemaCatalog | None) -> str:
    ranked = _sorted_selected_relations(selected_table_names, catalog)
    if not ranked:
        return ""

    pair_groups: dict[tuple[str, str], list[tuple[SchemaRelation, list[str], float, list[str]]]] = {}
    for item in ranked:
        pair_groups.setdefault(_join_relation_pair_key(item[0]), []).append(item)

    preferred_lines: list[str] = []
    avoid_lines: list[str] = []
    for relations in pair_groups.values():
        best_relation, _best_tags, best_score, best_reasons = relations[0]
        preferred_lines.append(
            "- "
            f"{_relation_endpoint(best_relation.from_database, best_relation.from_table, best_relation.from_column)} = "
            f"{_relation_endpoint(best_relation.to_database, best_relation.to_table, best_relation.to_column)} "
            f"[preferred_score={best_score:.2f}; reasons={'; '.join(best_reasons[:4])}]"
        )
        for relation, tags, score, reasons in relations[1:]:
            lowered_tags = {tag.lower() for tag in tags}
            if best_score - score < 4 and "suspected_endpoint" not in lowered_tags and "deprecated_endpoint" not in lowered_tags:
                continue
            avoid_lines.append(
                "- "
                f"避免优先使用 {_relation_endpoint(relation.from_database, relation.from_table, relation.from_column)} = "
                f"{_relation_endpoint(relation.to_database, relation.to_table, relation.to_column)} "
                f"[preferred_score={score:.2f}; reason={'; '.join(reasons[:4])}]"
            )

    lines: list[str] = []
    if preferred_lines:
        lines.append("Preferred join candidates:")
        lines.extend(preferred_lines[:12])
    if avoid_lines:
        lines.append("Avoid weaker join candidates:")
        lines.extend(avoid_lines[:12])
    return "\n".join(lines)


def _rank_table_candidates(question: str, catalog: SchemaCatalog | None, limit: int = 6) -> list[str]:
    tables = _catalog_tables(catalog)
    if not tables:
        return []
    semantics = _catalog_semantics(catalog)
    scored = [(_table_identity(table), _table_score(question, table, semantics), index) for index, table in enumerate(tables)]
    ranked = sorted(scored, key=lambda item: (-item[1], item[2]))
    visible = [item for item in ranked if item[1] > 0] or ranked[:limit]
    return [f"{name}:{score}" for name, score, _index in visible[:limit]]


def _sql_preview(sql: str, limit: int = 200) -> str:
    normalized = " ".join(sql.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}..."


def _fallback_relevant_tables(question: str, catalog: SchemaCatalog | None, limit: int = 4) -> list[str]:
    tables = _catalog_tables(catalog)
    if not tables:
        return []
    semantics = _catalog_semantics(catalog)
    scored = [(_table_identity(table), _table_score(question, table, semantics), index) for index, table in enumerate(tables)]
    selected = [name for name, score, _index in sorted(scored, key=lambda item: (-item[1], item[2])) if score > 0]
    if not selected:
        selected = [_table_identity(table) for table in tables]
    return _expand_selected_tables_with_relations(selected[:limit], catalog, limit=limit)


def _build_intent_prompt(question: str, table_names: list[str], catalog: SchemaCatalog | None) -> str:
    table_summaries: list[str] = []
    semantics = _catalog_semantics(catalog)
    for table in _catalog_tables(catalog):
        terms = [table.description or "", *table.aliases, *table.business_terms, *table.searchable_terms]
        semantic_terms = [
            item["term"]
            for item in _semantic_matches(question, semantics, {_table_identity(table)}, limit=6)
        ] if semantics else []
        summary = "、".join(term for term in _dedupe([*terms, *semantic_terms]) if term) or "无补充说明"
        table_summaries.append(f"- {_table_identity(table)}: {summary}")
    matched_semantics = _render_semantic_context(semantics, set(table_names), question)
    return "\n".join(
        [
            "你是 NL2SQL 的 intent_parser。只返回一个 JSON 对象，不要生成 SQL。",
            "任务：用中文概括用户查询意图，并从真实表名列表中选择 1 到 4 张最相关表。",
            "JSON 格式：{\"intent\": \"...\", \"relevant_tables\": [\"table_a\"]}",
            "relevant_tables 只能使用给定表名，不能编造表。",
            f"真实表名列表：{', '.join(table_names) if table_names else '(空)'}",
            "表说明：",
            "\n".join(table_summaries[:80]),
            "业务语义上下文（来自真实 schema 与已验证覆盖文件，只能作为选表线索）：",
            matched_semantics or "(无匹配业务语义)",
            f"用户问题：{question}",
        ]
    )


def _build_intent_result(
    state: AgentState,
    payload: dict[str, Any] | None,
    catalog: SchemaCatalog | None,
    *,
    source: str,
    llm_error: str | None = None,
) -> AgentState:
    question = _question_text(state)
    table_names = [_table_identity(table) for table in _catalog_tables(catalog)]
    table_lookup = _build_table_lookup(_catalog_tables(catalog))
    fallback_tables = _fallback_relevant_tables(question, catalog)
    candidate_scores = _rank_table_candidates(question, catalog)
    intent = f"查询需求：{question}" if question else "查询数据库信息"
    relevant_tables = fallback_tables
    requested_tables: list[str] = []
    filtered_tables: list[str] = []

    if payload is not None:
        candidate_intent = payload.get("intent")
        if isinstance(candidate_intent, str) and candidate_intent.strip():
            intent = candidate_intent.strip()
        raw_tables = payload.get("relevant_tables", [])
        if isinstance(raw_tables, list):
            requested_tables = [str(item).strip() for item in raw_tables if str(item).strip()]
            filtered_tables = [
                _table_identity(table_lookup[name])
                for name in requested_tables
                if name in table_lookup
            ]
            if filtered_tables:
                relevant_tables = _expand_selected_tables_with_relations(list(dict.fromkeys(filtered_tables))[:4], catalog, limit=4)

    semantic_signals = _semantic_matches(question, _catalog_semantics(catalog), set(relevant_tables))
    join_relations = _selected_join_relations(set(relevant_tables), catalog)
    logger.info(
        "agent.intent_parser.selection source=%s relevant_tables=%s requested_tables=%s filtered_tables=%s candidate_scores=%s semantic_signals=%s join_relations=%s llm_error=%s",
        source,
        relevant_tables,
        requested_tables,
        filtered_tables,
        candidate_scores,
        [signal.get("term") for signal in semantic_signals],
        join_relations,
        llm_error,
    )
    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["intent_parser"] = {
        "source": source,
        "available_table_count": len(table_names),
        "relevant_tables": relevant_tables,
        "semantic_signal_count": len(semantic_signals),
        "llm_error": llm_error,
    }
    if catalog and catalog.business_semantics and catalog.business_semantics.diagnostics:
        debug_trace["business_semantics"] = {
            "diagnostics": catalog.business_semantics.diagnostics,
        }
    return {
        "user_input": question,
        "intent": intent,
        "relevant_tables": relevant_tables,
        "available_tables": table_names,
        "semantic_signals": semantic_signals,
        "debug_trace": debug_trace,
    }


def intent_parser(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    question = _question_text(state)
    table_names = [_table_identity(table) for table in _catalog_tables(catalog)]
    model = llm_service.build_chat_model()
    payload: dict[str, Any] | None = None
    source = "deterministic"
    if model is not None:
        payload = _invoke_model_json(model, _build_intent_prompt(question, table_names, catalog))
        source = "llm" if payload is not None else "deterministic"
    return _build_intent_result(state, payload, catalog, source=source)


async def async_intent_parser(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    question = _question_text(state)
    table_names = [_table_identity(table) for table in _catalog_tables(catalog)]
    model = llm_service.build_chat_model()
    payload: dict[str, Any] | None = None
    llm_error: str | None = None
    source = "deterministic"
    if model is not None:
        settings = get_settings()
        payload, llm_error = await _ainvoke_model_json(
            model,
            _build_intent_prompt(question, table_names, catalog),
            timeout_seconds=settings.agent_llm_node_timeout_seconds,
            stage="intent_parser",
        )
        source = "llm" if payload is not None else "deterministic"
    return _build_intent_result(state, payload, catalog, source=source, llm_error=llm_error)


def _format_table_schema(
    table: SchemaTable,
    catalog: SchemaCatalog | None,
    selected_table_names: set[str] | None = None,
) -> str:
    table_identity = _table_identity(table)
    lines = [f"Table {_qualified_table_name(table)}"]
    if table.description:
        lines.append(f"Comment: {table.description}")
    if table.primary_keys:
        lines.append(f"Primary keys: {', '.join(_quote_identifier(key) for key in table.primary_keys)}")
    display_columns = _select_display_columns(table, "", limit=6)
    if display_columns and display_columns != ["*"]:
        lines.append(
            "Preferred SELECT output columns: "
            + ", ".join(_quote_identifier(column) for column in display_columns)
            + " (prefer these business-readable fields over internal IDs unless the user explicitly asks for IDs/codes/numbers)"
        )
    lines.append("Columns:")
    for column in table.columns:
        attrs = [column.data_type]
        attrs.append("NULL" if column.nullable else "NOT NULL")
        if column.is_primary_key:
            attrs.append("PRIMARY KEY")
        if column.default is not None:
            attrs.append(f"default={column.default}")
        if column.semantic_role:
            attrs.append(f"role={column.semantic_role}")
        attrs.append(f"output={_column_output_hint(column)}")
        enum_mapping = conversational_enum_mapping_for_field(_catalog_semantics(catalog), table_identity, column.name)
        if column.description:
            comment = column.description
            if enum_mapping:
                comment = f"{comment}; enum_mapping: {enum_mapping}"
            attrs.append(f"comment={comment}")
        elif enum_mapping:
            attrs.append(f"comment=enum_mapping: {enum_mapping}")
        if column.business_terms:
            attrs.append(f"terms={', '.join(column.business_terms)}")
        if getattr(column, "cross_table_diff", None):
            attrs.append(f"cross_table_diff={column.cross_table_diff}")
        lines.append(f"- {_quote_identifier(column.name)} ({'; '.join(attrs)})")

    selected_table_names = selected_table_names or {table_identity}
    relations = [
        item for item in _sorted_selected_relations(selected_table_names, catalog)
        if item[0].from_qualified_table == table_identity or item[0].to_qualified_table == table_identity
    ]
    if relations:
        lines.append("Relations:")
        for relation, tags, preferred_score, _reasons in relations:
            confidence = f"; confidence={relation.confidence}" if relation.confidence else ""
            score = f"; score={relation.ranking_score:.2f}" if relation.ranking_score is not None else ""
            validation = f"; validation={relation.validation_summary}" if relation.validation_summary else ""
            hint = f"; hint={relation.join_hint}" if relation.join_hint else ""
            governance = f"; governance={', '.join(tags)}" if tags else ""
            preferred = f"; preferred_score={preferred_score:.2f}"
            lines.append(
                f"- {_relation_endpoint(relation.from_database, relation.from_table, relation.from_column)} -> "
                f"{_relation_endpoint(relation.to_database, relation.to_table, relation.to_column)}"
                f" ({relation.relation_type or 'relation'}{confidence}{score}{preferred}{validation}{hint}{governance})"
            )
    return "\n".join(lines)


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


def schema_retriever(state: AgentState, catalog: SchemaCatalog | None = None) -> AgentState:
    relevant = state.get("relevant_tables", [])
    table_lookup = _build_table_lookup(_catalog_tables(catalog))
    selected_tables = []
    seen_selected: set[str] = set()
    for name in relevant:
        table = table_lookup.get(name)
        if table is None or _table_identity(table) in seen_selected:
            continue
        selected_tables.append(table)
        seen_selected.add(_table_identity(table))
    if not selected_tables:
        selected_tables = _catalog_tables(catalog)[:4]
    selected_table_names = {_table_identity(table) for table in selected_tables}

    relations_overview = _build_table_relations_overview(selected_table_names)
    join_priority_context = _render_join_priority_context(selected_table_names, catalog)
    table_schemas = "\n\n".join(
        _format_table_schema(table, catalog, selected_table_names)
        for table in selected_tables
    )
    schema_sections = [section for section in [relations_overview, join_priority_context, table_schemas] if section]
    schema_context = "\n\n".join(schema_sections)

    semantic_context = _render_semantic_context(
        _catalog_semantics(catalog),
        selected_table_names,
        _question_text(state),
    )
    matched_table_names = [_table_identity(table) for table in selected_tables]
    join_relations = _selected_join_relations(selected_table_names, catalog)
    logger.info(
        "agent.schema_retriever.selection tables=%s join_relations=%s schema_context_chars=%s semantic_context_chars=%s",
        matched_table_names,
        join_relations,
        len(schema_context),
        len(semantic_context),
    )
    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["schema_retriever"] = {
        "tables": matched_table_names,
        "schema_context_chars": len(schema_context),
        "semantic_context_chars": len(semantic_context),
        "relations_overview_chars": len(relations_overview),
        "relation_signals": join_relations[:12],
    }
    return {
        "schema_context": schema_context,
        "semantic_context": semantic_context,
        "relevant_tables": matched_table_names,
        "debug_trace": debug_trace,
    }


def _build_sql_generation_prompt(state: AgentState) -> str:
    retry_count = state.get("retry_count", 0)
    previous_sql = state.get("generated_sql") or state.get("previous_sql") or ""
    validation_error = state.get("validation_error", "")
    parts = [
        "你是 MySQL NL2SQL 生成器。只输出一条 SQL，不要解释，不要 Markdown。",
        "硬性规则：只能生成只读 SELECT/WITH 查询；禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/EXEC/SLEEP/BENCHMARK。",
        "所有表名和字段名必须用反引号包裹。跨数据库或 schema_context 中显示为数据库限定的表，必须使用 MySQL 全限定表名，如 `jc_config`.`table`、`jc_experimental`.`table`。",
        "只能使用 schema_context 中出现的表和字段；业务语义只用于解释同义词、指标、枚举和默认过滤，不能引入未出现在 schema_context 的表字段。",
        "JOIN 规则：优先使用 schema_context 中 Preferred join candidates、Relations、Table Relations、hint、confidence、preferred_score 明确推荐的联表键；若存在 Avoid weaker join candidates 或 governance=suspected_endpoint/deprecated_endpoint，除非用户明确要求，否则不要使用这些联表字段。",
        "SELECT 输出默认优先选择 schema_context 标注的 Preferred SELECT output columns 或 output=business-readable 字段，例如 name/title/amount/status/time/description。",
        "WHERE 字段匹配规则：带 enum_mapping/枚举对照的字段必须使用精确匹配（= 或 IN），匹配值只能来自 schema_context 中该字段的 enum_mapping，禁止编造枚举值。",
        "名称类字符串字段（如 city/城市、name/姓名/客户名、product_name/商品名、title/标题、description/描述等）默认使用 LIKE 模糊匹配，并用通配符包裹用户给出的关键词。",
        "当字段类型或业务含义不确定时，优先使用 LIKE 模糊匹配，不要直接使用等号精确匹配。",
        "id、*_id、create_user、update_user、create_time、update_time 等 output=internal/join/filter/audit 字段仍可用于 JOIN、WHERE、ORDER BY 和校验，但除非用户明确询问 ID/编号/code/no/number 或没有更可读字段，否则不要放进 SELECT 列表。",
        "如需要 LIMIT，必须同时给出稳定 ORDER BY。",
        "默认添加合理 LIMIT 200，除非用户明确要求更小。",
        f"用户问题：{_question_text(state)}",
        f"意图：{state.get('intent', '')}",
        f"相关表：{', '.join(state.get('relevant_tables', []))}",
        "business_semantic_context:",
        state.get("semantic_context", "") or "(无)",
        "schema_context:",
        state.get("schema_context", ""),
    ]
    if validation_error:
        parts.extend(
            [
                f"这是第 {retry_count + 1} 次生成。上一轮 SQL 校验失败，请针对性修复。",
                f"上一轮 SQL：{previous_sql}",
                f"校验错误：{validation_error}",
            ]
        )
    return "\n".join(parts)


def build_fallback_sql(question: str, catalog: SchemaCatalog | None = None, relevant_tables: list[str] | None = None) -> str:
    tables = _catalog_tables(catalog)
    if not tables:
        return "SELECT 1 AS result;"
    table_by_name = _build_table_lookup(tables)
    selected_name = None
    for name in relevant_tables or []:
        if name in table_by_name:
            selected_name = _table_identity(table_by_name[name])
            break
    if selected_name is None:
        selected = _fallback_relevant_tables(question, catalog, limit=1)
        selected_name = selected[0] if selected else _table_identity(tables[0])
    table = table_by_name[selected_name]
    columns = _select_display_columns(table, question, limit=5)
    select_expr = ", ".join("*" if column == "*" else _quote_identifier(column) for column in columns)
    order_column = next((column.name for column in table.columns if column.is_primary_key), None) or (table.columns[0].name if table.columns else None)
    sql = f"SELECT {select_expr} FROM {_qualified_table_name(table)}"
    if order_column:
        sql += f" ORDER BY {_quote_identifier(order_column)} DESC"
    sql += " LIMIT 20;"
    return sql


def _build_sql_generator_result(
    state: AgentState,
    catalog: SchemaCatalog | None,
    generated_sql: str | None,
    *,
    llm_error: str | None = None,
) -> AgentState:
    used_fallback = False
    status = "ready"
    previous_sql = _current_sql(state)
    if not generated_sql:
        generated_sql = build_fallback_sql(
            _question_text(state),
            catalog,
            state.get("relevant_tables", []),
        )
        used_fallback = True
        status = "mock"

    logger.info(
        "agent.sql_generator.selection retry_count=%s used_fallback=%s relevant_tables=%s validation_error=%s sql_preview=%s llm_error=%s",
        state.get("retry_count", 0),
        used_fallback,
        state.get("relevant_tables", []),
        state.get("validation_error", ""),
        _sql_preview(generated_sql),
        llm_error,
    )
    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["sql_generator"] = {
        "retry_count": state.get("retry_count", 0),
        "used_fallback": used_fallback,
        "had_validation_error": bool(state.get("validation_error")),
        "llm_error": llm_error,
    }
    return {
        "generated_sql": generated_sql,
        "previous_sql": previous_sql,
        "sql_params": [],
        "status": cast(Any, status),
        "used_fallback": used_fallback,
        "debug_trace": debug_trace,
        "explanation": "已基于真实 schema context 直接生成 MySQL 只读 SQL。",
    }


def sql_generator(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    model = llm_service.build_chat_model()
    generated_sql: str | None = None
    if model is not None:
        content = _invoke_model_text(model, _build_sql_generation_prompt(state))
        if content:
            generated_sql = _normalize_sql(content)
    return _build_sql_generator_result(state, catalog, generated_sql)


async def async_sql_generator(state: AgentState, llm_service: LLMService, catalog: SchemaCatalog | None = None) -> AgentState:
    model = llm_service.build_chat_model()
    generated_sql: str | None = None
    llm_error: str | None = None
    if model is not None:
        settings = get_settings()
        content, llm_error = await _ainvoke_model_text(
            model,
            _build_sql_generation_prompt(state),
            timeout_seconds=settings.agent_llm_node_timeout_seconds,
            stage="sql_generator",
        )
        if content:
            generated_sql = _normalize_sql(content)
    return _build_sql_generator_result(state, catalog, generated_sql, llm_error=llm_error)


def _strip_identifier_quotes(value: str) -> str:
    text = value.strip()
    if text.startswith("`") and text.endswith("`"):
        return text[1:-1]
    return text


def _normalize_sql_identifier_path(path: str) -> tuple[str, ...]:
    return tuple(
        part for part in (_strip_identifier_quotes(item.strip()) for item in path.split(".")) if part
    )


def _extract_sql_table_aliases(sql: str) -> dict[str, str]:
    aliases: dict[str, str] = {}
    pattern = re.compile(
        r"\b(?:from|join)\s+((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][\w$]*))?)"
        r"(?:\s+(?:as\s+)?)?(`[^`]+`|[A-Za-z_][\w$]*)?",
        re.IGNORECASE,
    )
    for match in pattern.finditer(sql):
        raw_table = match.group(1)
        raw_alias = match.group(2)
        parts = _normalize_sql_identifier_path(raw_table)
        if len(parts) == 1:
            qualified_table = parts[0]
            short_name = parts[0]
        elif len(parts) == 2:
            qualified_table = f"{parts[0]}.{parts[1]}"
            short_name = parts[1]
        else:
            continue
        aliases[qualified_table.lower()] = qualified_table
        aliases[short_name.lower()] = qualified_table
        if raw_alias:
            alias = _strip_identifier_quotes(raw_alias)
            if alias.lower() not in {"on", "where", "left", "right", "inner", "outer", "group", "order", "limit"}:
                aliases[alias.lower()] = qualified_table
    return aliases


def _resolve_sql_operand_table(parts: tuple[str, ...], aliases: dict[str, str]) -> tuple[str, str] | None:
    if len(parts) == 2:
        table_ref, column = parts
        resolved_table = aliases.get(table_ref.lower(), table_ref)
        return resolved_table, column
    if len(parts) == 3:
        database_name, table_name, column = parts
        return f"{database_name}.{table_name}", column
    return None


def _extract_join_equalities(sql: str) -> list[tuple[str, str, str, str]]:
    aliases = _extract_sql_table_aliases(sql)
    comparison_pattern = re.compile(
        r"((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][\w$]*)){1,2})"
        r"\s*=\s*"
        r"((?:`[^`]+`|[A-Za-z_][\w$]*)(?:\s*\.\s*(?:`[^`]+`|[A-Za-z_][\w$]*)){1,2})",
        re.IGNORECASE,
    )
    equalities: list[tuple[str, str, str, str]] = []
    for match in comparison_pattern.finditer(sql):
        left = _resolve_sql_operand_table(_normalize_sql_identifier_path(match.group(1)), aliases)
        right = _resolve_sql_operand_table(_normalize_sql_identifier_path(match.group(2)), aliases)
        if left is None or right is None:
            continue
        left_table, left_column = left
        right_table, right_column = right
        if left_table.lower() == right_table.lower():
            continue
        equality = (left_table, left_column, right_table, right_column)
        reverse = (right_table, right_column, left_table, left_column)
        if equality not in equalities and reverse not in equalities:
            equalities.append(equality)
    return equalities


def _best_alternative_join_message(sql: str, catalog: SchemaCatalog | None) -> str | None:
    if catalog is None:
        return None

    edge_tags = _graph_edge_tags_by_relation(catalog)
    relations_by_pair: dict[tuple[str, str], list[tuple[SchemaRelation, list[str], float, list[str]]]] = {}
    for relation in catalog.relations:
        tags = edge_tags.get(_join_relation_identity(relation), [])
        score, reasons = _relation_preference_score(relation, tags)
        relations_by_pair.setdefault(_join_relation_pair_key(relation), []).append((relation, tags, score, reasons))
    for relations in relations_by_pair.values():
        relations.sort(key=lambda item: (-item[2], item[0].from_column.lower(), item[0].to_column.lower()))

    for left_table, left_column, right_table, right_column in _extract_join_equalities(sql):
        candidates = next(
            (
                relations
                for relations in relations_by_pair.values()
                if relations and _join_relation_pair_matches_tables(relations[0][0], left_table, right_table)
            ),
            None,
        )
        if not candidates or len(candidates) < 2:
            continue
        chosen = next(
            (
                item for item in candidates
                if _join_relation_matches(item[0], left_table, left_column, right_table, right_column)
            ),
            None,
        )
        if chosen is None:
            continue
        best = candidates[0]
        if chosen[0] == best[0]:
            continue
        chosen_tags = {tag.lower() for tag in chosen[1]}
        if best[2] - chosen[2] < 4 and "suspected_endpoint" not in chosen_tags and "deprecated_endpoint" not in chosen_tags:
            continue
        return (
            "检测到当前 JOIN 选择了较弱候选："
            f"{_format_table_column_reference(left_table, left_column)} = {_format_table_column_reference(right_table, right_column)}。"
            "请改用同一对表中更优的关联键："
            f"{_relation_endpoint(best[0].from_database, best[0].from_table, best[0].from_column)} = "
            f"{_relation_endpoint(best[0].to_database, best[0].to_table, best[0].to_column)}。"
            f"更优依据：{'; '.join(best[3][:4])}；当前候选问题：{'; '.join(chosen[3][:4]) or 'preferred_score 更低'}。"
            "避免继续使用疑似保留/临时/低覆盖的联表字段。"
        )
    return None


def _should_run_mysql_explain() -> bool:
    database_url = (get_settings().database_url or "").lower()
    return "mysql" in database_url or "asyncmy" in database_url or "pymysql" in database_url


async def sql_validator(state: AgentState, validator: SQLValidator, executor: SQLExecutor) -> AgentState:
    sql = _current_sql(state)
    retry_count = state.get("retry_count", 0)
    debug_trace = dict(state.get("debug_trace", {}))
    settings = get_settings()
    should_explain = _should_run_mysql_explain()
    started_at = time.monotonic()
    try:
        validator.validate_read_only(sql)
        join_message = _best_alternative_join_message(sql, state.get("schema_catalog"))
        if join_message:
            raise DangerousSQLError(join_message)
        if should_explain:
            await executor.explain(
                sql,
                params=state.get("sql_params", []),
                timeout_seconds=settings.sql_explain_timeout_seconds,
            )
    except DangerousSQLError as error:
        message = str(error)
    except TimeoutError:
        message = "EXPLAIN 预检超时"
    except Exception as error:
        message = f"EXPLAIN 预检失败：{error.__class__.__name__}"
    else:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        debug_trace["sql_validator"] = {
            "passed": True,
            "retry_count": retry_count,
            "explain": "mysql" if should_explain else "skipped_non_mysql",
            "duration_ms": round(elapsed_ms, 2),
            "timeout_seconds": settings.sql_explain_timeout_seconds if should_explain else None,
        }
        logger.info("agent.sql_validator.end passed=true duration_ms=%.2f", elapsed_ms)
        return {"validation_error": "", "validation_errors": [], "validation_issues": [], "debug_trace": debug_trace}

    next_retry_count = retry_count + 1
    elapsed_ms = (time.monotonic() - started_at) * 1000
    issue = {
        "level": "error",
        "code": "SQL_VALIDATION_OR_EXPLAIN_FAILED",
        "message": message,
        "repairable": next_retry_count < state.get("max_retries", 3),
    }
    debug_trace["sql_validator"] = {
        "passed": False,
        "retry_count": retry_count,
        "next_retry_count": next_retry_count,
        "error": message,
        "duration_ms": round(elapsed_ms, 2),
        "timeout_seconds": settings.sql_explain_timeout_seconds if should_explain else None,
    }
    logger.info("agent.sql_validator.end passed=false duration_ms=%.2f error=%s", elapsed_ms, message)
    return {
        "validation_error": message,
        "validation_errors": [*state.get("validation_errors", []), message],
        "validation_issues": [*state.get("validation_issues", []), issue],
        "retry_count": next_retry_count,
        "debug_trace": debug_trace,
        "explanation": f"SQL 验证未通过：{message}",
    }


async def value_validator(state: AgentState, executor: SQLExecutor, catalog: SchemaCatalog | None = None) -> AgentState:
    sql = _current_sql(state)
    debug_trace = dict(state.get("debug_trace", {}))
    if catalog is None or not hasattr(executor, "value_exists") or not hasattr(executor, "suggest_similar_values"):
        debug_trace["value_validator"] = {"status": "skipped"}
        return {"debug_trace": debug_trace}

    predicates = extract_value_predicates(sql, catalog)
    if not predicates:
        debug_trace["value_validator"] = {"status": "skipped", "predicate_count": 0}
        return {"debug_trace": debug_trace}

    settings = get_settings()
    issues: list[MissingValueIssue] = []
    for predicate in predicates:
        exists = await executor.value_exists(
            predicate.table,
            predicate.column,
            predicate.value,
            timeout_seconds=settings.sql_explain_timeout_seconds,
        )
        if exists:
            continue
        suggestions = await executor.suggest_similar_values(
            predicate.table,
            predicate.column,
            predicate.value,
            timeout_seconds=settings.sql_explain_timeout_seconds,
        )
        issues.append(
            MissingValueIssue(
                table=predicate.table,
                column=predicate.column,
                value=predicate.value,
                suggestions=suggestions,
            )
        )

    if not issues:
        debug_trace["value_validator"] = {"status": "passed", "predicate_count": len(predicates)}
        return {"validation_error": "", "validation_errors": [], "validation_issues": [], "debug_trace": debug_trace}

    retry_count = state.get("retry_count", 0)
    next_retry_count = retry_count + 1
    message = build_missing_value_prompt(_question_text(state), issues)
    issue = {
        "level": "error",
        "code": "VALUE_NOT_FOUND",
        "message": message,
        "repairable": next_retry_count < state.get("max_retries", 3),
    }
    debug_trace["value_validator"] = {
        "status": "failed",
        "predicate_count": len(predicates),
        "missing_count": len(issues),
        "retry_count": retry_count,
        "next_retry_count": next_retry_count,
    }
    return {
        "validation_error": message,
        "validation_errors": [*state.get("validation_errors", []), message],
        "validation_issues": [*state.get("validation_issues", []), issue],
        "retry_count": next_retry_count,
        "debug_trace": debug_trace,
        "explanation": message,
    }


async def sql_executor(state: AgentState, executor: SQLExecutor) -> AgentState:
    sql = _current_sql(state)
    started_at = time.monotonic()
    try:
        settings = get_settings()
        result = await executor.execute(
            sql,
            params=state.get("sql_params", []),
            max_rows=settings.query_result_limit,
            timeout_seconds=settings.query_execution_timeout_seconds,
        )
        elapsed_ms = (time.monotonic() - started_at) * 1000
        summary = result.execution_summary or ""
        is_error = summary.startswith("查询执行失败") or summary.startswith("查询执行超时")
        debug_trace = dict(state.get("debug_trace", {}))
        debug_trace["sql_executor"] = {
            "row_count": 0 if is_error else result.row_count,
            "columns": [] if is_error else result.columns,
            "execution_time_ms": round(elapsed_ms, 2),
            "status": "error" if is_error else "ready",
            "timeout_seconds": settings.query_execution_timeout_seconds,
        }
        logger.info(
            "agent.sql_executor.end status=%s row_count=%s duration_ms=%.2f",
            "error" if is_error else "ready",
            0 if is_error else result.row_count,
            elapsed_ms,
        )
        return {
            "status": "error" if is_error else "ready",
            "rows": [] if is_error else result.rows,
            "columns": [] if is_error else result.columns,
            "row_count": 0 if is_error else result.row_count,
            "truncated": False if is_error else result.truncated,
            "execution_summary": summary,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_error": {"summary": summary} if is_error else {},
            "debug_trace": debug_trace,
        }
    except Exception as error:
        elapsed_ms = (time.monotonic() - started_at) * 1000
        summary = f"查询执行失败：{error.__class__.__name__}"
        debug_trace = dict(state.get("debug_trace", {}))
        debug_trace["sql_executor"] = {
            "row_count": 0,
            "columns": [],
            "execution_time_ms": round(elapsed_ms, 2),
            "status": "error",
        }
        return {
            "status": "error",
            "rows": [],
            "columns": [],
            "row_count": 0,
            "truncated": False,
            "execution_summary": summary,
            "execution_time_ms": round(elapsed_ms, 2),
            "execution_error": {"summary": summary},
            "debug_trace": debug_trace,
        }


def _build_formatter_prompt(state: AgentState) -> str:
    preview_rows = state.get("rows", [])[:20]
    return "\n".join(
        [
            "你是 SQL 查询结果解读助手。用中文自然语言直接回答用户问题，不要泄露内部错误细节。",
            "可以简要提到结果行数；不要隐藏 SQL 是否生成，SQL 会由系统单独展示。",
            f"用户问题：{_question_text(state)}",
            f"意图：{state.get('intent', '')}",
            f"SQL：{state.get('generated_sql', '')}",
            f"执行摘要：{state.get('execution_summary', '')}",
            f"行数：{state.get('row_count', 0)}",
            f"结果预览：{json.dumps(preview_rows, ensure_ascii=False, default=str)}",
        ]
    )


def _default_final_answer(state: AgentState) -> tuple[str, str, str]:
    status = state.get("status", "ready")
    validation_error = state.get("validation_error", "")
    execution_summary = state.get("execution_summary", "")
    row_count = state.get("row_count", 0)

    if validation_error:
        status = "error"
        execution_summary = execution_summary or "SQL 验证失败，已停止执行。"
        final_answer = "抱歉，生成的 SQL 未通过安全或语法预检，已停止执行。请换一种更明确的问法后重试。"
    elif status == "error":
        final_answer = "抱歉，查询执行失败。系统已返回脱敏后的错误摘要，请稍后重试或调整问题。"
    elif row_count == 0:
        final_answer = "查询执行成功，但没有找到符合条件的数据。可以尝试放宽筛选条件或调整时间范围。"
        execution_summary = execution_summary or "查询执行成功，但没有返回记录。"
    else:
        final_answer = f"查询执行成功，共返回 {row_count} 行结果。"
    return cast(str, status), execution_summary, final_answer


def _build_formatter_result(
    state: AgentState,
    *,
    status: str,
    execution_summary: str,
    final_answer: str,
    llm_error: str | None = None,
) -> AgentState:
    row_count = state.get("row_count", 0)
    explanation = final_answer
    if state.get("execution_time_ms") is not None:
        explanation = f"{explanation} 执行耗时：{state.get('execution_time_ms'):.0f}ms。"

    debug_trace = dict(state.get("debug_trace", {}))
    debug_trace["validation_errors"] = state.get("validation_errors", [])
    debug_trace["validation_issues"] = state.get("validation_issues", [])
    debug_trace["result_formatter"] = {
        "status": status,
        "row_count": row_count,
        "has_validation_error": bool(state.get("validation_error", "")),
        "llm_error": llm_error,
    }
    debug_trace["execution"] = {
        "row_count": row_count,
        "truncated": state.get("truncated", False),
        "execution_time_ms": state.get("execution_time_ms"),
        "summary": execution_summary,
    }
    debug_trace["fallback"] = {"used": False}
    debug_trace["direct_sql_fallback"] = {"used": state.get("used_fallback", False)}

    result: AgentState = {
        "status": cast(Any, status),
        "final_answer": final_answer,
        "explanation": explanation,
        "execution_summary": execution_summary,
        "debug_trace": debug_trace,
    }
    if status == "error":
        result.update({"rows": [], "columns": [], "row_count": 0})
    return result


def result_formatter(state: AgentState, llm_service: LLMService) -> AgentState:
    status, execution_summary, final_answer = _default_final_answer(state)
    model = llm_service.build_chat_model()
    if model is not None and not state.get("validation_error", ""):
        formatted = _invoke_model_text(model, _build_formatter_prompt(state))
        if formatted:
            final_answer = formatted.strip()
    return _build_formatter_result(
        state,
        status=status,
        execution_summary=execution_summary,
        final_answer=final_answer,
    )


async def async_result_formatter(state: AgentState, llm_service: LLMService) -> AgentState:
    status, execution_summary, final_answer = _default_final_answer(state)
    llm_error: str | None = None
    model = llm_service.build_chat_model()
    if model is not None and not state.get("validation_error", ""):
        settings = get_settings()
        formatted, llm_error = await _ainvoke_model_text(
            model,
            _build_formatter_prompt(state),
            timeout_seconds=settings.result_formatter_llm_timeout_seconds,
            stage="result_formatter",
        )
        if formatted:
            final_answer = formatted.strip()
    return _build_formatter_result(
        state,
        status=status,
        execution_summary=execution_summary,
        final_answer=final_answer,
        llm_error=llm_error,
    )
