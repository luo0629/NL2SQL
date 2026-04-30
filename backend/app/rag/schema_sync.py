from datetime import datetime, timezone

from sqlalchemy import text

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

    async with engine.connect() as connection:
        database_name = connection.engine.url.database or "unknown"
        driver_name = (connection.engine.url.drivername or "").lower()
        is_mysql = "mysql" in driver_name
        value_mappings = load_value_mappings()

        tables_result = await connection.execute(
            text(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = :database_name
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            ),
            {"database_name": database_name},
        )
        table_names = [
            str(row[0])
            for row in tables_result.all()
        ]

        columns_query = """
                SELECT
                    c.table_name,
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    CASE WHEN k.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage k
                  ON c.table_schema = k.table_schema
                 AND c.table_name = k.table_name
                 AND c.column_name = k.column_name
                 AND k.constraint_name = 'PRIMARY'
                WHERE c.table_schema = :database_name
                ORDER BY c.table_name, c.ordinal_position
                """

        if is_mysql:
            columns_query = """
                SELECT
                    c.table_name,
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    c.column_comment,
                    CASE WHEN k.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_primary_key
                FROM information_schema.columns c
                LEFT JOIN information_schema.key_column_usage k
                  ON c.table_schema = k.table_schema
                 AND c.table_name = k.table_name
                 AND c.column_name = k.column_name
                 AND k.constraint_name = 'PRIMARY'
                WHERE c.table_schema = :database_name
                ORDER BY c.table_name, c.ordinal_position
                """

        columns_result = await connection.execute(
            text(
                columns_query
            ),
            {"database_name": database_name},
        )

        columns_by_table: dict[str, list[SchemaColumn]] = {name: [] for name in table_names}
        primary_keys_by_table: dict[str, list[str]] = {name: [] for name in table_names}

        for row in columns_result.all():
            table_name = str(row[0])
            column_name = str(row[1])
            data_type = str(row[2])
            is_nullable = str(row[3])
            comment_value = None
            pk_index = 4
            if is_mysql:
                raw_comment = row[4]
                if raw_comment is not None:
                    raw_comment_text = str(raw_comment).strip()
                    if raw_comment_text:
                        comment_value = raw_comment_text
                pk_index = 5

            is_primary_key = bool(row[pk_index])
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
            column = SchemaColumn(
                name=column_name,
                data_type=data_type,
                nullable=is_nullable.upper() == "YES",
                is_primary_key=is_primary_key,
                description=merge_column_description(
                    db_description=comment_value,
                    fallback_mapping=fallback_mapping,
                ),
                business_terms=column_enrichment.business_terms,
                semantic_role=column_enrichment.semantic_role,
            )
            columns_by_table.setdefault(table_name, []).append(column)
            if is_primary_key:
                primary_keys_by_table.setdefault(table_name, []).append(column_name)

    tables: list[SchemaTable] = []
    for table_name in table_names:
        columns = columns_by_table.get(table_name, [])
        primary_keys = primary_keys_by_table.get(table_name, [])
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
    relations = []
    for from_table, from_column, to_table, to_column, relation_type in RELATION_HINTS:
        if from_table not in table_name_set or to_table not in table_name_set:
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
