from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from app.config import get_settings
from app.config_loader import reload_app_config
from app.rag.business_semantics import _column_kind, _extract_enum_values
from app.rag.schema_introspection import LiveSchemaSnapshot, inspect_live_schema
from app.rag.schema_models import SchemaColumn
from app.rag.schema_sync import sync_schema_metadata

logger = logging.getLogger(__name__)

_STRUCTURE_CONFIG_FILES = {
    "table_relations": "table_relations.yaml",
    "field_semantics": "field_semantics.yaml",
    "enum_mappings": "enum_mappings.yaml",
    "business_terms": "business_terms.yaml",
}

_COMMON_TABLE_SUFFIXES = (
    "主表",
    "明细表",
    "关系表",
    "状态表",
    "配置表",
    "记录表",
    "信息表",
    "数据表",
    "表",
)

def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read config YAML for refresh: %s", path)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _dump_yaml_mapping(payload: dict[str, Any]) -> str:
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _write_yaml_mapping(path: Path, payload: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = _dump_yaml_mapping(payload)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == rendered:
        return False
    path.write_text(rendered, encoding="utf-8")
    return True


def _qualified_table_name(database_name: str | None, table_name: str, *, expose_table_database: bool) -> str:
    if expose_table_database and database_name:
        return f"{database_name}.{table_name}"
    return table_name


def _clean_description(description: str | None, fallback: str) -> str:
    text = (description or "").strip()
    return text or f"{fallback} 表"


def _base_keyword(description: str | None, table_name: str) -> str:
    text = (description or "").strip()
    if not text:
        return table_name
    for suffix in _COMMON_TABLE_SUFFIXES:
        if text.endswith(suffix):
            candidate = text.removesuffix(suffix).strip()
            if candidate:
                return candidate
    return text


def _build_column_usage(snapshot: LiveSchemaSnapshot) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = defaultdict(list)
    for database_name, inspection in snapshot.inspections.items():
        for table_name in inspection.table_names:
            qualified = _qualified_table_name(database_name, table_name, expose_table_database=snapshot.expose_table_database)
            for raw_column in inspection.columns_by_table.get(table_name, []):
                column_name = str(raw_column.get("name", "")).strip()
                if column_name:
                    usage[column_name.lower()].append(qualified)
    return usage


def _cross_table_diff(
    *,
    column_name: str,
    table_name: str,
    primary_keys: set[str],
    foreign_key_targets: dict[str, str],
    duplicate_tables: list[str],
) -> str:
    if len(duplicate_tables) <= 1:
        return f"仅存在于 {table_name} 表"
    lowered = column_name.lower()
    normalized_primary_keys = {key.lower() for key in primary_keys}
    normalized_foreign_key_targets = {key.lower(): value for key, value in foreign_key_targets.items()}
    if lowered in normalized_primary_keys:
        return "当前表主键；该字段也出现在其他表中，需结合具体表语境区分，不要跨表误用其他同名编号"
    if lowered in normalized_foreign_key_targets:
        return f"外键字段，关联 {normalized_foreign_key_targets[lowered]}；跨表查询优先按该关系 JOIN，不要改用其他同名业务字段"
    if lowered.startswith("reserve") or lowered in {"deleted", "revision"}:
        return "保留/状态控制字段，在多个表中出现；不要作为 JOIN 键，通常也不应用作业务主过滤条件"
    if lowered in {"creator", "updater", "create_user", "update_user"}:
        return "审计字段，在多个表中出现；不要作为 JOIN 键，除非用户明确按创建人/更新人查询"
    if lowered.endswith("_time") or lowered in {"created_at", "updated_at", "create_time", "update_time"}:
        return "时间字段，在多个表中出现，但表示各自表内的时间语义；不要作为 JOIN 键"
    if lowered in {"status", "type", "name", "remark"}:
        return "同名通用字段，在多个表中出现，具体含义需结合当前表语境理解；默认不要直接作为跨表 JOIN 键"
    return "该字段在多个表中出现，具体含义需结合当前表语境理解；若存在明确关系提示或主业务编号，优先使用明确关系而非盲目同名 JOIN"


def _build_relations_payload(snapshot: LiveSchemaSnapshot) -> dict[str, Any]:
    relations: list[dict[str, Any]] = []
    routing_suggestions: list[dict[str, Any]] = []
    table_profiles: dict[str, Any] = {}
    column_usage = _build_column_usage(snapshot)

    relation_edges: list[dict[str, str]] = []
    seen_relations: set[tuple[str, str, str, str]] = set()

    for database_name, inspection in snapshot.inspections.items():
        for foreign_key in inspection.foreign_keys:
            from_table = _qualified_table_name(
                foreign_key["from_database"],
                str(foreign_key["from_table"]),
                expose_table_database=snapshot.expose_table_database,
            )
            to_table = _qualified_table_name(
                foreign_key["to_database"],
                str(foreign_key["to_table"]),
                expose_table_database=snapshot.expose_table_database,
            )
            from_column = str(foreign_key["from_column"])
            to_column = str(foreign_key["to_column"])
            relation_key = (from_table, from_column, to_table, to_column)
            if relation_key in seen_relations:
                continue
            seen_relations.add(relation_key)
            relations.append(
                {
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_table": to_table,
                    "to_column": to_column,
                    "relation_type": "many-to-one",
                    "description": f"{from_table} 关联 {to_table}",
                    "business_meaning": f"{from_table} 通过 {from_column} 关联到 {to_table}.{to_column}",
                    "join_direction": f"从 {from_table} 查 {to_table} 时用 INNER JOIN；从 {to_table} 查 {from_table} 时用 LEFT JOIN",
                }
            )
            relation_edges.append(
                {
                    "from_table": from_table,
                    "from_column": from_column,
                    "to_table": to_table,
                    "to_column": to_column,
                }
            )

        for table_name in inspection.table_names:
            qualified = _qualified_table_name(database_name, table_name, expose_table_database=snapshot.expose_table_database)
            description = _clean_description(inspection.comments_by_table.get(table_name), table_name)
            keyword = _base_keyword(description, table_name)
            if keyword:
                routing_suggestions.append({"keyword": keyword, "tables": [qualified]})

            primary_keys = set(inspection.primary_keys_by_table.get(table_name, []))
            foreign_key_targets = {
                str(fk["from_column"]): f"{_qualified_table_name(fk['to_database'], str(fk['to_table']), expose_table_database=snapshot.expose_table_database)}.{fk['to_column']}"
                for fk in inspection.foreign_keys
                if str(fk["from_table"]) == table_name
            }
            cross_table_fields: list[dict[str, str]] = []
            for raw_column in inspection.columns_by_table.get(table_name, []):
                column_name = str(raw_column.get("name", "")).strip()
                duplicate_tables = column_usage.get(column_name.lower(), [])
                if len(duplicate_tables) > 1:
                    cross_table_fields.append(
                        {
                            "field": column_name,
                            "description": _cross_table_diff(
                                column_name=column_name,
                                table_name=table_name,
                                primary_keys=primary_keys,
                                foreign_key_targets=foreign_key_targets,
                                duplicate_tables=duplicate_tables,
                            ),
                        }
                    )

            table_profiles[qualified] = {
                "description": description,
                "routing_hints": [
                    {
                        "intent": f"查询{keyword}",
                        "description": f"当用户查询 {keyword} 相关信息时优先路由到此表",
                    }
                ],
                "cross_table_fields": cross_table_fields,
            }

    multi_hop_paths: list[dict[str, Any]] = []
    seen_paths: set[tuple[str, str, str]] = set()
    for first in relation_edges:
        first_endpoints = {first["from_table"], first["to_table"]}
        for second in relation_edges:
            if first is second:
                continue
            second_endpoints = {second["from_table"], second["to_table"]}
            shared = first_endpoints & second_endpoints
            if len(shared) != 1:
                continue
            middle = next(iter(shared))
            others = list((first_endpoints | second_endpoints) - {middle})
            if len(others) != 2 or others[0] == others[1]:
                continue
            path = (others[0], middle, others[1])
            reverse_path = (others[1], middle, others[0])
            if path in seen_paths or reverse_path in seen_paths:
                continue
            seen_paths.add(path)
            multi_hop_paths.append(
                {
                    "path": [others[0], middle, others[1]],
                    "description": f"从 {others[0]} 经 {middle} 关联到 {others[1]}",
                    "business_scenario": f"当用户需要跨表查询 {others[0]} 与 {others[1]} 时，可通过 {middle} 作为中间表关联",
                    "join_chain": [
                        {"from": f"{first['from_table']}.{first['from_column']}", "to": f"{first['to_table']}.{first['to_column']}"},
                        {"from": f"{second['from_table']}.{second['from_column']}", "to": f"{second['to_table']}.{second['to_column']}"},
                    ],
                }
            )

    return {
        "generated": {
            "relations": relations,
            "routing_suggestions": routing_suggestions,
            "table_profiles": table_profiles,
            "multi_hop_paths": multi_hop_paths,
        },
        "overrides": {
            "relations": [],
            "routing_suggestions": [],
            "table_profiles": {},
            "multi_hop_paths": [],
        },
    }


def _build_field_semantics_payload(snapshot: LiveSchemaSnapshot) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {}
    column_usage = _build_column_usage(snapshot)

    for database_name, inspection in snapshot.inspections.items():
        for table_name in inspection.table_names:
            qualified = _qualified_table_name(database_name, table_name, expose_table_database=snapshot.expose_table_database)
            primary_keys = set(inspection.primary_keys_by_table.get(table_name, []))
            foreign_key_targets = {
                str(fk["from_column"]): f"{_qualified_table_name(fk['to_database'], str(fk['to_table']), expose_table_database=snapshot.expose_table_database)}.{fk['to_column']}"
                for fk in inspection.foreign_keys
                if str(fk["from_table"]) == table_name
            }
            table_fields: dict[str, Any] = {}
            for raw_column in inspection.columns_by_table.get(table_name, []):
                column_name = str(raw_column.get("name", "")).strip()
                comment = str(raw_column.get("comment") or "").strip() or None
                data_type = str(raw_column.get("type") or "unknown")
                enum_values = _extract_enum_values(comment)
                value_range = ", ".join(f"{key}={value}" for key, value in enum_values.items()) or None
                duplicate_tables = column_usage.get(column_name.lower(), [])
                semantic_role = _column_kind(
                    SchemaColumn(
                        name=column_name,
                        data_type=data_type,
                        nullable=bool(raw_column.get("nullable", True)),
                        is_primary_key=column_name in primary_keys,
                        description=comment,
                    )
                )
                table_fields[column_name] = {
                    "description": comment,
                    "value_range": value_range,
                    "cross_table_diff": _cross_table_diff(
                        column_name=column_name,
                        table_name=table_name,
                        primary_keys=primary_keys,
                        foreign_key_targets=foreign_key_targets,
                        duplicate_tables=duplicate_tables,
                    ),
                    "business_terms": [],
                    "semantic_role": semantic_role,
                }
            fields[qualified] = table_fields

    return {
        "generated": {"fields": fields},
        "overrides": {"fields": {}},
    }


def _build_enum_mappings_payload(snapshot: LiveSchemaSnapshot) -> dict[str, Any]:
    enums: dict[str, Any] = {}
    for database_name, inspection in snapshot.inspections.items():
        for table_name in inspection.table_names:
            qualified = _qualified_table_name(database_name, table_name, expose_table_database=snapshot.expose_table_database)
            for raw_column in inspection.columns_by_table.get(table_name, []):
                column_name = str(raw_column.get("name", "")).strip()
                comment = str(raw_column.get("comment") or "").strip() or None
                values = _extract_enum_values(comment)
                if not values and column_name.lower() == "deleted":
                    values = {"0": "未删除", "1": "删除"}
                if values:
                    enums[f"{qualified}.{column_name}"] = {"values": values}
    return {
        "generated": {"enums": enums},
        "overrides": {"enums": {}},
    }


def _build_business_terms_payload(snapshot: LiveSchemaSnapshot) -> dict[str, Any]:
    terms: list[dict[str, Any]] = []
    for database_name, inspection in snapshot.inspections.items():
        for table_name in inspection.table_names:
            qualified = _qualified_table_name(database_name, table_name, expose_table_database=snapshot.expose_table_database)
            description = _clean_description(inspection.comments_by_table.get(table_name), table_name)
            alias = _base_keyword(description, table_name)
            business_terms = [description] if description and description != alias else []
            terms.append(
                {
                    "alias": alias,
                    "standard": table_name,
                    "tables": [qualified],
                    "business_terms": business_terms,
                }
            )
    return {
        "generated": {"terms": terms},
        "overrides": {"terms": []},
    }


def _preserve_overrides(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = _read_yaml_mapping(path)
    overrides = current.get("overrides")
    if isinstance(overrides, dict):
        payload["overrides"] = overrides
    return payload


async def refresh_generated_config_yaml(
    snapshot: LiveSchemaSnapshot | None = None,
    *,
    reload_config: bool = True,
) -> bool:
    settings = get_settings()
    config_dir = Path(settings.config_dir)
    snapshot = snapshot or await inspect_live_schema()

    payloads = {
        "table_relations": _build_relations_payload(snapshot),
        "field_semantics": _build_field_semantics_payload(snapshot),
        "enum_mappings": _build_enum_mappings_payload(snapshot),
        "business_terms": _build_business_terms_payload(snapshot),
    }

    changed = False
    for section, filename in _STRUCTURE_CONFIG_FILES.items():
        path = config_dir / filename
        payload = _preserve_overrides(path, payloads[section])
        changed = _write_yaml_mapping(path, payload) or changed

    if reload_config:
        reload_app_config()
    logger.info("Refreshed generated config YAML files under %s (changed=%s)", config_dir, changed)
    return changed


async def refresh_startup_schema_artifacts() -> bool:
    snapshot = await inspect_live_schema()
    config_changed = await refresh_generated_config_yaml(snapshot=snapshot, reload_config=True)
    await sync_schema_metadata(snapshot=snapshot, yaml_enabled_override=True)
    return config_changed
