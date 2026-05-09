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

RELATION_HINTS: list[tuple[str, str, str, str, str | None]] = [
    ("dish", "category_id", "category", "id", "many-to-one"),
    ("setmeal", "category_id", "category", "id", "many-to-one"),
    ("dish_flavor", "dish_id", "dish", "id", "many-to-one"),
    ("order_detail", "order_id", "orders", "id", "many-to-one"),
    ("order_detail", "dish_id", "dish", "id", "many-to-one"),
    ("orders", "user_id", "user", "id", "many-to-one"),
    ("shopping_cart", "user_id", "user", "id", "many-to-one"),
    ("shopping_cart", "dish_id", "dish", "id", "many-to-one"),
    ("shopping_cart", "setmeal_id", "setmeal", "id", "many-to-one"),
    ("setmeal_dish", "setmeal_id", "setmeal", "id", "many-to-one"),
    ("setmeal_dish", "dish_id", "dish", "id", "many-to-one"),
    ("orders", "address_book_id", "address_book", "id", "many-to-one"),
]


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
                    confidence=relation_enrichment.confidence,
                    join_hint=relation_enrichment.join_hint,
                )
            )

    for database_name in inspected_databases:
        relation_database = database_name if expose_table_database else None
        for from_table, from_column, to_table, to_column, relation_type in RELATION_HINTS:
            if (relation_database, from_table) not in table_identity_set or (relation_database, to_table) not in table_identity_set:
                continue
            relation_key = (relation_database, from_table, from_column, relation_database, to_table, to_column)
            if relation_key in seen_relations:
                continue
            relation_enrichment = get_relation_enrichment(
                enrichment,
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
            )
            relations.append(
                SchemaRelation(
                    from_database=database_name if expose_table_database else None,
                    from_table=from_table,
                    from_column=from_column,
                    to_database=database_name if expose_table_database else None,
                    to_table=to_table,
                    to_column=to_column,
                    relation_type=relation_type,
                    confidence=relation_enrichment.confidence,
                    join_hint=relation_enrichment.join_hint,
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
