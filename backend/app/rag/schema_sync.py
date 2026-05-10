from datetime import datetime, timezone

from app.config import get_settings
from app.rag.business_semantics import attach_business_semantics
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


def _normalized_column_name(value: str) -> str:
    return value.strip().lower()


def _is_blocked_join_column(column: SchemaColumn) -> bool:
    name = _normalized_column_name(column.name)
    role = str(column.semantic_role or "").lower()
    if name.startswith("reserve") or name in _INFERRED_JOIN_EXCLUDED_COLUMN_NAMES:
        return True
    if role == "internal":
        return True
    return False


def _join_column_score(column: SchemaColumn) -> int | None:
    if _is_blocked_join_column(column):
        return None

    name = _normalized_column_name(column.name)
    description = (column.description or "").lower()
    role = str(column.semantic_role or "").lower()
    score = 0

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
    if any(token in description for token in _PREFERRED_JOIN_DESCRIPTION_TOKENS):
        score += 4
    if any(token in description for token in _DOWNRANK_JOIN_DESCRIPTION_TOKENS):
        score -= 3
    if column.nullable:
        score -= 1

    return score if score > 0 else None


def _infer_relation_confidence(score: int) -> str:
    if score >= 8:
        return "high"
    if score >= 5:
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


def _infer_relations_from_shared_columns(tables: list[SchemaTable]) -> list[SchemaRelation]:
    inferred_relations: list[SchemaRelation] = []
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
                    from_table, from_column, to_table, to_column = _orient_inferred_relation(
                        left_table,
                        left_column,
                        right_table,
                        right_column,
                    )
                    confidence = _infer_relation_confidence(min(left_score, right_score))
                    inferred_relations.append(
                        SchemaRelation(
                            from_database=database_name,
                            from_table=from_table.name,
                            from_column=from_column.name,
                            to_database=database_name,
                            to_table=to_table.name,
                            to_column=to_column.name,
                            relation_type="inferred-shared-key",
                            confidence=confidence,
                            join_hint=(
                                f"自动推断：字段 `{shared_name}` 在两表间都像业务关联键；"
                                "优先使用该字段联表，避免改用 reserve/deleted/revision/审计/时间类字段。"
                            ),
                        )
                    )
    return inferred_relations


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

    for relation in _infer_relations_from_shared_columns(tables):
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
            )
        )

    catalog = SchemaCatalog(
        database=",".join(configured_databases) or default_database,
        tables=tables,
        relations=relations,
        synced_at=datetime.now(timezone.utc).isoformat(),
    )
    return attach_business_semantics(
        catalog,
        settings.business_semantic_override_path,
        yaml_enabled=settings.business_semantic_yaml_enabled,
        database_url=settings.schema_scope_key,
        yaml_dir=settings.business_semantic_yaml_dir,
    )
