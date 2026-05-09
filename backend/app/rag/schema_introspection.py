from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect

from app.config import get_settings
from app.database.engine import engine


@dataclass(slots=True)
class SchemaInspection:
    table_names: list[str]
    columns_by_table: dict[str, list[dict[str, object]]]
    primary_keys_by_table: dict[str, list[str]]
    foreign_keys: list[dict[str, object]]
    indexes_by_table: dict[str, list[str]]
    comments_by_table: dict[str, str | None]


@dataclass(slots=True)
class LiveSchemaSnapshot:
    default_database: str
    configured_databases: list[str]
    supports_named_schemas: bool
    expose_table_database: bool
    inspections: dict[str | None, SchemaInspection]


def _schema_kw(database_name: str | None) -> dict[str, str]:
    return {"schema": database_name} if database_name else {}


def _normalize_identifier(identifier: str) -> str:
    return identifier.strip().strip("`")


def _split_table_identifier(identifier: str) -> tuple[str | None, str] | None:
    parts = [_normalize_identifier(part) for part in identifier.split(".")]
    parts = [part for part in parts if part]
    if len(parts) == 1:
        return None, parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


def _table_names_for_schema(
    inspector: object,
    schema_name: str | None,
    included_table_names: list[str] | None,
) -> list[str]:
    if included_table_names is not None:
        return list(included_table_names)
    return sorted(inspector.get_table_names(**_schema_kw(schema_name)))


def _schema_include_tables_by_database(
    include_tables: list[str],
    configured_databases: list[str],
) -> dict[str | None, list[str]] | None:
    if not include_tables:
        return None

    configured_lookup = {database.casefold(): database for database in configured_databases}
    default_database = configured_databases[0] if configured_databases else None
    grouped: dict[str | None, list[str]] = {database: [] for database in configured_databases}
    seen: dict[str | None, set[str]] = {database: set() for database in configured_databases}

    for raw_identifier in include_tables:
        parsed = _split_table_identifier(raw_identifier)
        if parsed is None:
            continue
        database_name, table_name = parsed
        target_database = default_database if database_name is None else configured_lookup.get(database_name.casefold())
        if target_database is None:
            continue
        table_key = table_name.casefold()
        if table_key in seen[target_database]:
            continue
        seen[target_database].add(table_key)
        grouped[target_database].append(table_name)

    return grouped


async def inspect_live_schema() -> LiveSchemaSnapshot:
    settings = get_settings()

    async with engine.connect() as connection:
        default_database = connection.engine.url.database or "unknown"
        configured_databases = settings.effective_database_names or [default_database]
        driver_name = connection.engine.url.drivername.lower()
        supports_named_schemas = "mysql" in driver_name or "mariadb" in driver_name
        expose_table_database = supports_named_schemas and bool(settings.database_names)
        included_tables_by_database = _schema_include_tables_by_database(
            settings.effective_schema_include_tables,
            configured_databases,
        )

        def inspect_schema(sync_connection: Any) -> dict[str | None, SchemaInspection]:
            inspector = inspect(sync_connection)
            inspected: dict[str | None, SchemaInspection] = {}
            for database_name in configured_databases:
                schema_name = database_name if supports_named_schemas and database_name != "unknown" else None
                included_table_names = (
                    None if included_tables_by_database is None else included_tables_by_database.get(database_name, [])
                )
                table_names = _table_names_for_schema(inspector, schema_name, included_table_names)
                columns_by_table: dict[str, list[dict[str, object]]] = {}
                primary_keys_by_table: dict[str, list[str]] = {}
                foreign_keys: list[dict[str, object]] = []
                indexes_by_table: dict[str, list[str]] = {}
                comments_by_table: dict[str, str | None] = {}

                for table_name in table_names:
                    primary_key = inspector.get_pk_constraint(table_name, **_schema_kw(schema_name)) or {}
                    constrained_columns = primary_key.get("constrained_columns") or []
                    primary_keys_by_table[table_name] = [str(column) for column in constrained_columns]
                    columns_by_table[table_name] = list(inspector.get_columns(table_name, **_schema_kw(schema_name)))
                    try:
                        table_comment = inspector.get_table_comment(table_name, **_schema_kw(schema_name)) or {}
                    except NotImplementedError:
                        table_comment = {}
                    comments_by_table[table_name] = str(table_comment.get("text") or "").strip() or None
                    indexes_by_table[table_name] = [
                        str(index.get("name"))
                        for index in inspector.get_indexes(table_name, **_schema_kw(schema_name))
                        if index.get("name")
                    ]
                    for foreign_key in inspector.get_foreign_keys(table_name, **_schema_kw(schema_name)):
                        referred_table = foreign_key.get("referred_table")
                        constrained = foreign_key.get("constrained_columns") or []
                        referred = foreign_key.get("referred_columns") or []
                        if not referred_table or not constrained or not referred:
                            continue
                        referred_schema = foreign_key.get("referred_schema") or schema_name
                        foreign_keys.append(
                            {
                                "from_database": schema_name,
                                "from_table": table_name,
                                "from_column": str(constrained[0]),
                                "to_database": str(referred_schema) if referred_schema else None,
                                "to_table": str(referred_table),
                                "to_column": str(referred[0]),
                            }
                        )

                inspected[schema_name] = SchemaInspection(
                    table_names=table_names,
                    columns_by_table=columns_by_table,
                    primary_keys_by_table=primary_keys_by_table,
                    foreign_keys=foreign_keys,
                    indexes_by_table=indexes_by_table,
                    comments_by_table=comments_by_table,
                )
            return inspected

        inspections = await connection.run_sync(inspect_schema)

    return LiveSchemaSnapshot(
        default_database=default_database,
        configured_databases=configured_databases,
        supports_named_schemas=supports_named_schemas,
        expose_table_database=expose_table_database,
        inspections=inspections,
    )
