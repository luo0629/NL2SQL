from datetime import datetime, timezone

from sqlalchemy import inspect

from app.database.engine import engine
from app.rag.schema_enrichment import (
    get_column_enrichment,
    get_relation_enrichment,
    get_table_enrichment,
    load_schema_enrichment,
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
    table_description: str | None,
    table_aliases: list[str],
    table_business_terms: list[str],
    columns: list[SchemaColumn],
) -> list[str]:
    terms: set[str] = set()
    _add_search_variants(terms, table_name)
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
    enrichment = load_schema_enrichment()
    value_mappings = load_value_mappings()

    async with engine.connect() as connection:
        database_name = connection.engine.url.database or "unknown"

        def inspect_schema(sync_connection):
            inspector = inspect(sync_connection)
            table_names = sorted(inspector.get_table_names())
            columns_by_table: dict[str, list[dict[str, object]]] = {}
            primary_keys_by_table: dict[str, list[str]] = {}
            foreign_keys: list[dict[str, object]] = []
            indexes_by_table: dict[str, list[str]] = {}

            for table_name in table_names:
                primary_key = inspector.get_pk_constraint(table_name) or {}
                constrained_columns = primary_key.get("constrained_columns") or []
                primary_keys_by_table[table_name] = [str(column) for column in constrained_columns]
                columns_by_table[table_name] = list(inspector.get_columns(table_name))
                indexes_by_table[table_name] = [
                    str(index.get("name"))
                    for index in inspector.get_indexes(table_name)
                    if index.get("name")
                ]
                for foreign_key in inspector.get_foreign_keys(table_name):
                    referred_table = foreign_key.get("referred_table")
                    constrained = foreign_key.get("constrained_columns") or []
                    referred = foreign_key.get("referred_columns") or []
                    if not referred_table or not constrained or not referred:
                        continue
                    foreign_keys.append(
                        {
                            "from_table": table_name,
                            "from_column": str(constrained[0]),
                            "to_table": str(referred_table),
                            "to_column": str(referred[0]),
                        }
                    )

            return table_names, columns_by_table, primary_keys_by_table, foreign_keys, indexes_by_table

        (
            table_names,
            raw_columns_by_table,
            primary_keys_by_table,
            foreign_keys,
            indexes_by_table,
        ) = await connection.run_sync(inspect_schema)

    tables: list[SchemaTable] = []
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
                    description=merge_column_description(
                        db_description=comment_value,
                        fallback_mapping=fallback_mapping,
                    ),
                    business_terms=column_enrichment.business_terms,
                    semantic_role=column_enrichment.semantic_role,
                )
            )

        table_enrichment = get_table_enrichment(enrichment, table_name)
        table_description = TABLE_DESCRIPTIONS.get(table_name)
        tables.append(
            SchemaTable(
                name=table_name,
                description=table_description,
                aliases=table_enrichment.aliases,
                business_terms=table_enrichment.business_terms,
                columns=columns,
                primary_keys=primary_keys,
                indexes=indexes_by_table.get(table_name, []),
                searchable_terms=_build_search_terms(
                    table_name,
                    table_description,
                    table_enrichment.aliases,
                    table_enrichment.business_terms,
                    columns,
                ),
            )
        )

    table_name_set = {table.name for table in tables}
    relations: list[SchemaRelation] = []
    seen_relations: set[tuple[str, str, str, str]] = set()

    for foreign_key in foreign_keys:
        relation_key = (
            str(foreign_key["from_table"]),
            str(foreign_key["from_column"]),
            str(foreign_key["to_table"]),
            str(foreign_key["to_column"]),
        )
        seen_relations.add(relation_key)
        relation_enrichment = get_relation_enrichment(
            enrichment,
            from_table=relation_key[0],
            from_column=relation_key[1],
            to_table=relation_key[2],
            to_column=relation_key[3],
        )
        relations.append(
            SchemaRelation(
                from_table=relation_key[0],
                from_column=relation_key[1],
                to_table=relation_key[2],
                to_column=relation_key[3],
                relation_type="foreign_key",
                confidence=relation_enrichment.confidence,
                join_hint=relation_enrichment.join_hint,
            )
        )

    for from_table, from_column, to_table, to_column, relation_type in RELATION_HINTS:
        if from_table not in table_name_set or to_table not in table_name_set:
            continue
        relation_key = (from_table, from_column, to_table, to_column)
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
                from_table=from_table,
                from_column=from_column,
                to_table=to_table,
                to_column=to_column,
                relation_type=relation_type,
                confidence=relation_enrichment.confidence,
                join_hint=relation_enrichment.join_hint,
            )
        )

    return SchemaCatalog(
        database=database_name,
        tables=tables,
        relations=relations,
        synced_at=datetime.now(timezone.utc).isoformat(),
    )
