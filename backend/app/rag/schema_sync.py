from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging

from app.config import get_settings
from app.database.executor import SQLExecutor
from app.rag.business_semantics import attach_business_semantics
from app.rag.schema_governance import build_relationship_graph_artifact
from app.rag.schema_enrichment import (
    get_column_enrichment,
    get_relation_enrichment,
    get_table_enrichment,
    load_schema_enrichment,
)
from app.rag.schema_introspection import (
    _schema_include_tables_by_database,
    _table_names_for_schema,
    inspect_live_schema,
)
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable
from app.rag.value_mapping_loader import (
    get_fallback_mapping_for_column,
    load_value_mappings,
    merge_column_description,
)


logger = logging.getLogger(__name__)


TABLE_DESCRIPTIONS: dict[str, str] = {
    "address_book": "用户收货地址表",
    "category": "菜品与套餐分类表",
    "dish": "菜品主表",
    "dish_flavor": "菜品口味表",
    "employee": "员工表",
    "order_detail": "订单明细表",
    "orders": "订单主表",
    "setmeal": "套餐主表",
    "setmeal_dish": "套餐与菜品关系表",
    "shopping_cart": "购物车表",
    "user": "用户表",
}

RELATION_HINTS: list[tuple[str, str, str, str, str | None]] = []

_INFERRED_JOIN_EXCLUDED_COLUMN_NAMES = {
    "id",
    "tenant_id",
    "deleted",
    "revision",
    "creator",
    "updater",
    "create_user",
    "update_user",
    "create_time",
    "update_time",
    "created_at",
    "updated_at",
    "status",
    "type",
    "name",
    "remark",
}

_PREFERRED_JOIN_DESCRIPTION_TOKENS = ("编号", "代码", "code", "key", "number", "no")
_DOWNRANK_JOIN_DESCRIPTION_TOKENS = ("临时", "预", "保留", "备用", "审计", "创建", "更新", "删除")
_GENERIC_SHARED_KEY_NAMES = {
    "code",
    "number",
    "no",
    "key",
    "ref_no",
    "ref_code",
    "batch_no",
    "sync_batch_no",
}


@dataclass
class _ProbeMetrics:
    sample_size: int
    non_null_ratio: float
    distinct_ratio: float
    distinct_values: set[str] = field(default_factory=set)


@dataclass
class _InferredRelationCandidate:
    database_name: str | None
    from_table: SchemaTable
    from_column: SchemaColumn
    to_table: SchemaTable
    to_column: SchemaColumn
    shared_name: str
    metadata_score: float
    final_score: float
    confidence: str
    notes: list[str] = field(default_factory=list)
    validation_summary: str | None = None


def _normalized_column_name(value: str) -> str:
    return value.strip().lower()


def _normalized_table_name(table: SchemaTable) -> str:
    return table.qualified_name.lower()


def _is_blocked_join_column(column: SchemaColumn) -> bool:
    name = _normalized_column_name(column.name)
    role = str(column.semantic_role or "").lower()
    if name.startswith("reserve") or name in _INFERRED_JOIN_EXCLUDED_COLUMN_NAMES:
        return True
    if role == "internal":
        return True
    return False


def _data_type_family(data_type: str) -> str:
    normalized = str(data_type or "").strip().lower()
    if any(token in normalized for token in ("bigint", "smallint", "tinyint", "integer", "int")):
        return "integer"
    if any(token in normalized for token in ("decimal", "numeric", "double", "float", "real")):
        return "number"
    if any(token in normalized for token in ("char", "text", "clob", "json", "uuid", "enum", "set")):
        return "text"
    if any(token in normalized for token in ("date", "time", "year")):
        return "time"
    if "bool" in normalized or "bit" in normalized:
        return "boolean"
    return normalized.split("(", 1)[0].strip() or "unknown"


def _compatible_join_types(left: SchemaColumn, right: SchemaColumn) -> bool:
    left_family = _data_type_family(left.data_type)
    right_family = _data_type_family(right.data_type)
    if left_family == right_family:
        return True
    return {left_family, right_family} <= {"integer", "number"}


def _join_column_score(column: SchemaColumn) -> float | None:
    if _is_blocked_join_column(column):
        return None

    name = _normalized_column_name(column.name)
    description = (column.description or "").lower()
    role = str(column.semantic_role or "").lower()
    score = 0.0

    if role == "foreign_key":
        score += 5
    elif role == "identifier":
        score += 2
    elif role == "dimension":
        score += 1

    if column.is_primary_key and name != "id":
        score += 2
    if name.endswith("_id"):
        score += 2
    if name.endswith("_no") or name.endswith("_code"):
        score += 2
    if any(token in description for token in _PREFERRED_JOIN_DESCRIPTION_TOKENS):
        score += 4
    if any(token in description for token in _DOWNRANK_JOIN_DESCRIPTION_TOKENS):
        score -= 3
    if name in _GENERIC_SHARED_KEY_NAMES:
        score -= 2
    if column.nullable:
        score -= 1

    return score if score > 0 else None


def _join_quality_notes(column: SchemaColumn) -> list[str]:
    notes: list[str] = []
    name = _normalized_column_name(column.name)
    description = (column.description or "").lower()
    if column.nullable:
        notes.append("字段可空")
    if name in _GENERIC_SHARED_KEY_NAMES:
        notes.append("字段名偏泛化")
    if any(token in description for token in _DOWNRANK_JOIN_DESCRIPTION_TOKENS):
        notes.append("描述带临时/审计语义")
    return notes


def _infer_relation_confidence(score: float) -> str:
    if score >= 10:
        return "high"
    if score >= 6:
        return "medium"
    return "low"


def _orient_inferred_relation(
    left_table: SchemaTable,
    left_column: SchemaColumn,
    right_table: SchemaTable,
    right_column: SchemaColumn,
) -> tuple[SchemaTable, SchemaColumn, SchemaTable, SchemaColumn]:
    left_role = str(left_column.semantic_role or "").lower()
    right_role = str(right_column.semantic_role or "").lower()
    if left_role == "foreign_key" and (right_column.is_primary_key or right_role == "identifier"):
        return left_table, left_column, right_table, right_column
    if right_role == "foreign_key" and (left_column.is_primary_key or left_role == "identifier"):
        return right_table, right_column, left_table, left_column
    if left_column.is_primary_key and not right_column.is_primary_key:
        return right_table, right_column, left_table, left_column
    if right_column.is_primary_key and not left_column.is_primary_key:
        return left_table, left_column, right_table, right_column
    if left_table.name <= right_table.name:
        return left_table, left_column, right_table, right_column
    return right_table, right_column, left_table, left_column


def _build_inferred_relation_candidates(tables: list[SchemaTable]) -> list[_InferredRelationCandidate]:
    candidates: list[_InferredRelationCandidate] = []
    tables_by_database: dict[str | None, list[SchemaTable]] = {}
    for table in tables:
        tables_by_database.setdefault(table.database, []).append(table)

    for database_name, database_tables in tables_by_database.items():
        for left_index, left_table in enumerate(database_tables):
            left_columns = {_normalized_column_name(column.name): column for column in left_table.columns}
            for right_table in database_tables[left_index + 1:]:
                right_columns = {_normalized_column_name(column.name): column for column in right_table.columns}
                for shared_name in sorted(set(left_columns) & set(right_columns)):
                    left_column = left_columns[shared_name]
                    right_column = right_columns[shared_name]
                    left_score = _join_column_score(left_column)
                    right_score = _join_column_score(right_column)
                    if left_score is None or right_score is None:
                        continue
                    if not _compatible_join_types(left_column, right_column):
                        continue

                    metadata_score = min(left_score, right_score)
                    if _data_type_family(left_column.data_type) in {"integer", "number", "text"}:
                        metadata_score += 1
                    if not left_column.nullable and not right_column.nullable:
                        metadata_score += 1

                    notes = [*_join_quality_notes(left_column), *_join_quality_notes(right_column)]
                    from_table, from_column, to_table, to_column = _orient_inferred_relation(
                        left_table,
                        left_column,
                        right_table,
                        right_column,
                    )
                    candidates.append(
                        _InferredRelationCandidate(
                            database_name=database_name,
                            from_table=from_table,
                            from_column=from_column,
                            to_table=to_table,
                            to_column=to_column,
                            shared_name=shared_name,
                            metadata_score=metadata_score,
                            final_score=metadata_score,
                            confidence=_infer_relation_confidence(metadata_score),
                            notes=notes,
                        )
                    )
    return candidates


def _preferred_probe_order_columns(table: SchemaTable, column_name: str) -> list[str]:
    order_columns = [key for key in table.primary_keys if key]
    if column_name not in order_columns:
        order_columns.append(column_name)
    return order_columns or [column_name]


def _normalize_probe_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.casefold()


def _build_probe_metrics(values: list[object]) -> _ProbeMetrics:
    sample_size = len(values)
    normalized = [_normalize_probe_value(value) for value in values]
    non_null_values = [value for value in normalized if value is not None]
    distinct_values = set(non_null_values)
    non_null_ratio = (len(non_null_values) / sample_size) if sample_size else 0.0
    distinct_ratio = (len(distinct_values) / len(non_null_values)) if non_null_values else 0.0
    return _ProbeMetrics(
        sample_size=sample_size,
        non_null_ratio=non_null_ratio,
        distinct_ratio=distinct_ratio,
        distinct_values=distinct_values,
    )


def _relation_probe_adjustment(
    left_metrics: _ProbeMetrics,
    right_metrics: _ProbeMetrics,
) -> tuple[float, str]:
    score_delta = 0.0
    min_non_null = min(left_metrics.non_null_ratio, right_metrics.non_null_ratio)
    min_distinct = min(left_metrics.distinct_ratio, right_metrics.distinct_ratio)
    overlap_ratio = 0.0
    overlap_base = min(len(left_metrics.distinct_values), len(right_metrics.distinct_values))
    if overlap_base > 0:
        overlap_ratio = len(left_metrics.distinct_values & right_metrics.distinct_values) / overlap_base

    if min_non_null < 0.5:
        score_delta -= 4
    elif min_non_null < 0.8:
        score_delta -= 2
    elif min_non_null >= 0.95:
        score_delta += 1

    if min_distinct < 0.15:
        score_delta -= 4
    elif min_distinct < 0.35:
        score_delta -= 2
    elif min_distinct >= 0.8:
        score_delta += 1

    if overlap_base == 0:
        score_delta -= 4
    elif overlap_ratio < 0.1:
        score_delta -= 5
    elif overlap_ratio < 0.35:
        score_delta -= 2
    elif overlap_ratio >= 0.75:
        score_delta += 3
    elif overlap_ratio >= 0.5:
        score_delta += 1

    summary = (
        "sample_probe("
        f"rows={left_metrics.sample_size}/{right_metrics.sample_size}; "
        f"non_null={left_metrics.non_null_ratio:.2f}/{right_metrics.non_null_ratio:.2f}; "
        f"distinct={left_metrics.distinct_ratio:.2f}/{right_metrics.distinct_ratio:.2f}; "
        f"overlap={overlap_ratio:.2f}"
        ")"
    )
    return score_delta, summary


def _probe_group_key(candidate: _InferredRelationCandidate) -> tuple[str | None, tuple[str, str]]:
    pair = tuple(sorted((_normalized_table_name(candidate.from_table), _normalized_table_name(candidate.to_table))))
    return candidate.database_name, pair


def _select_probe_candidates(candidates: list[_InferredRelationCandidate], top_k: int) -> list[_InferredRelationCandidate]:
    if top_k <= 0:
        return []
    grouped: dict[tuple[str | None, tuple[str, str]], list[_InferredRelationCandidate]] = {}
    for candidate in candidates:
        grouped.setdefault(_probe_group_key(candidate), []).append(candidate)

    selected: list[tuple[int, _InferredRelationCandidate]] = []
    for group in grouped.values():
        ranked = sorted(
            group,
            key=lambda item: (-item.metadata_score, item.shared_name, item.from_table.qualified_name, item.to_table.qualified_name),
        )
        priority = 1 if len(group) > 1 else 0
        selected.extend((priority, candidate) for candidate in ranked[: min(2, len(ranked))])

    ranked_selected = sorted(
        selected,
        key=lambda item: (
            -item[0],
            -item[1].metadata_score,
            item[1].shared_name,
            item[1].from_table.qualified_name,
            item[1].to_table.qualified_name,
        ),
    )
    return [candidate for _priority, candidate in ranked_selected[:top_k]]


async def _apply_lightweight_relation_validation(
    candidates: list[_InferredRelationCandidate],
    executor: SQLExecutor,
    *,
    sample_limit: int,
    top_k: int,
    timeout_seconds: float,
) -> None:
    for candidate in _select_probe_candidates(candidates, top_k):
        try:
            left_values = await executor.sample_column_values(
                candidate.from_table.qualified_name,
                candidate.from_column.name,
                order_by=_preferred_probe_order_columns(candidate.from_table, candidate.from_column.name),
                limit=sample_limit,
                timeout_seconds=timeout_seconds,
            )
            right_values = await executor.sample_column_values(
                candidate.to_table.qualified_name,
                candidate.to_column.name,
                order_by=_preferred_probe_order_columns(candidate.to_table, candidate.to_column.name),
                limit=sample_limit,
                timeout_seconds=timeout_seconds,
            )
        except TimeoutError:
            candidate.validation_summary = "sample_probe(timeout)"
            logger.info(
                "schema_sync.relation_probe.skip reason=timeout from=%s.%s to=%s.%s",
                candidate.from_table.qualified_name,
                candidate.from_column.name,
                candidate.to_table.qualified_name,
                candidate.to_column.name,
            )
            continue
        except Exception as error:
            candidate.validation_summary = f"sample_probe(skipped:{error.__class__.__name__})"
            logger.info(
                "schema_sync.relation_probe.skip reason=%s from=%s.%s to=%s.%s",
                error.__class__.__name__,
                candidate.from_table.qualified_name,
                candidate.from_column.name,
                candidate.to_table.qualified_name,
                candidate.to_column.name,
            )
            continue

        left_metrics = _build_probe_metrics(left_values)
        right_metrics = _build_probe_metrics(right_values)
        score_delta, summary = _relation_probe_adjustment(left_metrics, right_metrics)
        candidate.final_score = max(0.0, candidate.metadata_score + score_delta)
        candidate.confidence = _infer_relation_confidence(candidate.final_score)
        candidate.validation_summary = summary
        logger.info(
            "schema_sync.relation_probe.end from=%s.%s to=%s.%s metadata_score=%.2f final_score=%.2f summary=%s",
            candidate.from_table.qualified_name,
            candidate.from_column.name,
            candidate.to_table.qualified_name,
            candidate.to_column.name,
            candidate.metadata_score,
            candidate.final_score,
            summary,
        )


def _candidate_join_hint(candidate: _InferredRelationCandidate) -> str:
    parts = [
        f"自动推断：字段 `{candidate.shared_name}` 在两表间都像业务关联键。",
    ]
    deduped_notes = list(dict.fromkeys(note for note in candidate.notes if note))
    if deduped_notes:
        parts.append("元数据排序信号：" + "、".join(deduped_notes[:3]) + "。")
    if candidate.validation_summary:
        parts.append("轻量验证：" + candidate.validation_summary + "。")
    parts.append("优先使用该字段联表，避免改用 reserve/deleted/revision/审计/时间类字段。")
    return "".join(parts)


def _finalize_inferred_relations(candidates: list[_InferredRelationCandidate]) -> list[SchemaRelation]:
    relations: list[SchemaRelation] = []
    for candidate in sorted(
        candidates,
        key=lambda item: (
            item.database_name or "",
            item.from_table.qualified_name,
            item.to_table.qualified_name,
            -item.final_score,
            item.shared_name,
        ),
    ):
        relations.append(
            SchemaRelation(
                from_database=candidate.database_name,
                from_table=candidate.from_table.name,
                from_column=candidate.from_column.name,
                to_database=candidate.database_name,
                to_table=candidate.to_table.name,
                to_column=candidate.to_column.name,
                relation_type="inferred-shared-key",
                confidence=candidate.confidence,
                join_hint=_candidate_join_hint(candidate),
                ranking_score=round(candidate.final_score, 2),
                validation_summary=candidate.validation_summary,
            )
        )
    return relations


def _infer_relations_from_shared_columns(tables: list[SchemaTable]) -> list[SchemaRelation]:
    return _finalize_inferred_relations(_build_inferred_relation_candidates(tables))


async def _infer_relations_from_shared_columns_with_probes(
    tables: list[SchemaTable],
    executor: SQLExecutor,
    *,
    probe_enabled: bool,
    probe_top_k: int,
    probe_sample_limit: int,
    probe_timeout_seconds: float,
) -> list[SchemaRelation]:
    candidates = _build_inferred_relation_candidates(tables)
    if probe_enabled and candidates:
        await _apply_lightweight_relation_validation(
            candidates,
            executor,
            sample_limit=probe_sample_limit,
            top_k=probe_top_k,
            timeout_seconds=probe_timeout_seconds,
        )
    return _finalize_inferred_relations(candidates)


def _add_search_variants(terms: set[str], value: str | None) -> None:
    text = (value or "").strip()
    if not text:
        return

    normalized = text.lower()
    variants = {
        text,
        normalized,
        text.replace("_", " "),
        normalized.replace("_", " "),
    }
    terms.update(item.strip() for item in variants if item.strip())


def _build_search_terms(
    table_name: str,
    database_name: str | None,
    table_description: str | None,
    table_aliases: list[str],
    table_business_terms: list[str],
    columns: list[SchemaColumn],
) -> list[str]:
    terms: set[str] = set()
    _add_search_variants(terms, table_name)
    if database_name:
        _add_search_variants(terms, database_name)
        _add_search_variants(terms, f"{database_name}.{table_name}")
    _add_search_variants(terms, table_description)

    for alias in table_aliases:
        _add_search_variants(terms, alias)

    for term in table_business_terms:
        _add_search_variants(terms, term)

    for column in columns:
        _add_search_variants(terms, column.name)
        _add_search_variants(terms, column.description)
        for business_term in column.business_terms:
            _add_search_variants(terms, business_term)
        _add_search_variants(terms, column.semantic_role)

    return sorted(terms)


async def sync_schema_metadata() -> SchemaCatalog:
    settings = get_settings()
    enrichment = load_schema_enrichment()
    value_mappings = load_value_mappings()
    snapshot = await inspect_live_schema()

    default_database = snapshot.default_database
    configured_databases = snapshot.configured_databases
    expose_table_database = snapshot.expose_table_database
    inspected_databases = snapshot.inspections

    tables: list[SchemaTable] = []
    for database_name, metadata in inspected_databases.items():
        table_names = metadata.table_names
        raw_columns_by_table = metadata.columns_by_table
        primary_keys_by_table = metadata.primary_keys_by_table
        indexes_by_table = metadata.indexes_by_table
        comments_by_table = metadata.comments_by_table
        for table_name in table_names:
            columns: list[SchemaColumn] = []
            primary_keys = primary_keys_by_table.get(table_name, [])
            for raw_column in raw_columns_by_table.get(table_name, []):
                column_name = str(raw_column.get("name", ""))
                raw_type = raw_column.get("type")
                raw_comment = raw_column.get("comment")
                comment_value = None
                if raw_comment is not None:
                    raw_comment_text = str(raw_comment).strip()
                    if raw_comment_text:
                        comment_value = raw_comment_text

                fallback_mapping = get_fallback_mapping_for_column(
                    value_mappings,
                    table_name=table_name,
                    column_name=column_name,
                )
                column_enrichment = get_column_enrichment(
                    enrichment,
                    table_name=table_name,
                    column_name=column_name,
                )
                columns.append(
                    SchemaColumn(
                        name=column_name,
                        data_type=str(raw_type or "unknown"),
                        nullable=bool(raw_column.get("nullable", True)),
                        is_primary_key=column_name in primary_keys,
                        default=str(raw_column.get("default")) if raw_column.get("default") is not None else None,
                        description=merge_column_description(
                            db_description=comment_value,
                            fallback_mapping=fallback_mapping,
                        ),
                        cross_table_diff=column_enrichment.cross_table_diff,
                        business_terms=column_enrichment.business_terms,
                        semantic_role=column_enrichment.semantic_role,
                    )
                )

            table_enrichment = get_table_enrichment(enrichment, table_name)
            table_description = comments_by_table.get(table_name) or TABLE_DESCRIPTIONS.get(table_name)
            tables.append(
                SchemaTable(
                    name=table_name,
                    database=database_name if expose_table_database else None,
                    description=table_description,
                    aliases=table_enrichment.aliases,
                    business_terms=table_enrichment.business_terms,
                    columns=columns,
                    primary_keys=primary_keys,
                    indexes=indexes_by_table.get(table_name, []),
                    searchable_terms=_build_search_terms(
                        table_name,
                        database_name,
                        table_description,
                        table_enrichment.aliases,
                        table_enrichment.business_terms,
                        columns,
                    ),
                )
            )

    table_identity_set = {(table.database, table.name) for table in tables}
    relations: list[SchemaRelation] = []
    seen_relations: set[tuple[str | None, str, str, str | None, str, str]] = set()

    for metadata in inspected_databases.values():
        for foreign_key in metadata.foreign_keys:
            relation_key = (
                foreign_key["from_database"],
                str(foreign_key["from_table"]),
                str(foreign_key["from_column"]),
                foreign_key["to_database"],
                str(foreign_key["to_table"]),
                str(foreign_key["to_column"]),
            )
            from_identity = (relation_key[0] if expose_table_database else None, relation_key[1])
            to_identity = (relation_key[3] if expose_table_database else None, relation_key[4])
            if from_identity not in table_identity_set or to_identity not in table_identity_set:
                continue
            seen_relations.add(relation_key)
            relation_enrichment = get_relation_enrichment(
                enrichment,
                from_table=relation_key[1],
                from_column=relation_key[2],
                to_table=relation_key[4],
                to_column=relation_key[5],
            )
            relations.append(
                SchemaRelation(
                    from_database=relation_key[0] if expose_table_database else None,
                    from_table=relation_key[1],
                    from_column=relation_key[2],
                    to_database=relation_key[3] if expose_table_database else None,
                    to_table=relation_key[4],
                    to_column=relation_key[5],
                    relation_type="foreign_key",
                    confidence=relation_enrichment.confidence or "high",
                    join_hint=relation_enrichment.join_hint,
                )
            )

    from app.config_loader import get_app_config

    configured_relations = get_app_config().table_relations.get("relations", [])
    for relation in configured_relations:
        from_table_ref = str(relation.get("from_table") or "").strip()
        from_column = str(relation.get("from_column") or "").strip()
        to_table_ref = str(relation.get("to_table") or "").strip()
        to_column = str(relation.get("to_column") or "").strip()
        if not all([from_table_ref, from_column, to_table_ref, to_column]):
            continue

        from_database, from_table = from_table_ref.split(".", 1) if "." in from_table_ref else (None, from_table_ref)
        to_database, to_table = to_table_ref.split(".", 1) if "." in to_table_ref else (None, to_table_ref)
        relation_key = (
            from_database if expose_table_database else None,
            from_table,
            from_column,
            to_database if expose_table_database else None,
            to_table,
            to_column,
        )
        from_identity = (relation_key[0], relation_key[1])
        to_identity = (relation_key[3], relation_key[4])
        if from_identity not in table_identity_set or to_identity not in table_identity_set:
            continue
        if relation_key in seen_relations:
            continue
        seen_relations.add(relation_key)
        relations.append(
            SchemaRelation(
                from_database=relation_key[0],
                from_table=relation_key[1],
                from_column=relation_key[2],
                to_database=relation_key[3],
                to_table=relation_key[4],
                to_column=relation_key[5],
                relation_type=str(relation.get("relation_type") or "configured"),
                confidence=str(relation.get("confidence") or "high"),
                join_hint=str(relation.get("join_hint") or relation.get("description") or ""),
            )
        )

    inferred_relations = await _infer_relations_from_shared_columns_with_probes(
        tables,
        SQLExecutor(),
        probe_enabled=settings.relation_probe_enabled,
        probe_top_k=max(0, int(settings.relation_probe_top_k)),
        probe_sample_limit=max(1, int(settings.relation_probe_sample_limit)),
        probe_timeout_seconds=max(0.1, float(settings.relation_probe_timeout_seconds)),
    )
    for relation in inferred_relations:
        relation_key = (
            relation.from_database if expose_table_database else None,
            relation.from_table,
            relation.from_column,
            relation.to_database if expose_table_database else None,
            relation.to_table,
            relation.to_column,
        )
        if relation_key in seen_relations:
            continue
        from_identity = (relation_key[0], relation_key[1])
        to_identity = (relation_key[3], relation_key[4])
        if from_identity not in table_identity_set or to_identity not in table_identity_set:
            continue
        seen_relations.add(relation_key)
        relations.append(
            SchemaRelation(
                from_database=relation_key[0],
                from_table=relation.from_table,
                from_column=relation.from_column,
                to_database=relation_key[3],
                to_table=relation.to_table,
                to_column=relation.to_column,
                relation_type=relation.relation_type,
                confidence=relation.confidence,
                join_hint=relation.join_hint,
                ranking_score=relation.ranking_score,
                validation_summary=relation.validation_summary,
            )
        )

    catalog = SchemaCatalog(
        database=",".join(configured_databases) or default_database,
        tables=tables,
        relations=relations,
        synced_at=datetime.now(timezone.utc).isoformat(),
    )
    catalog = attach_business_semantics(
        catalog,
        settings.business_semantic_override_path,
        yaml_enabled=settings.business_semantic_yaml_enabled,
        database_url=settings.schema_scope_key,
        yaml_dir=settings.business_semantic_yaml_dir,
    )
    catalog.relationship_graph = build_relationship_graph_artifact(
        catalog,
        scope_key=settings.schema_scope_key,
        artifact_dir=settings.schema_governance_artifact_dir,
        generated_at=catalog.synced_at,
    )
    return catalog
