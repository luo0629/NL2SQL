from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy.engine import make_url

import yaml

from pydantic import BaseModel, Field

from app.rag.schema_models import (
    BusinessDefaultFilter,
    BusinessDimension,
    BusinessEnum,
    BusinessMetric,
    BusinessSemanticLayer,
    BusinessSemanticTerm,
    SchemaCatalog,
    SchemaColumn,
    SchemaTable,
)


_REF_PATTERN = re.compile(r"^([A-Za-z_][\w$]*)(?:\.([A-Za-z_][\w$]*))?$")
_QUALIFIED_REF_PATTERN = re.compile(r"`?([A-Za-z_][\w$]*)`?\s*\.\s*`?([A-Za-z_][\w$]*)`?")
_DANGEROUS_FRAGMENT_PATTERN = re.compile(
    r";|--|/\*|\*/|\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|exec|execute|sleep|benchmark)\b",
    re.IGNORECASE,
)
_ENUM_PAIR_PATTERN = re.compile(r"(?P<value>[-+]?\d+)\s*[:=：]?\s*(?P<label>[一-鿿A-Za-z_][一-鿿A-Za-z0-9_ -]{0,20})")
_DEFAULT_YAML_SECTIONS = ("aliases", "metrics", "dimensions", "enums", "default_filters")
_IDENTIFIER_LIKE_COLUMN_PATTERN = re.compile(r"(^id$|_id$|_code$|_no$|^code$)", re.IGNORECASE)
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


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _clean_text(value)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _safe_slug(value: str, fallback: str = "database") -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._-")
    return slug[:48] or fallback


def _database_fingerprint(database_url: str) -> str:
    return hashlib.sha256(database_url.encode("utf-8")).hexdigest()[:16]


def _database_identity(database_url: str) -> dict[str, str]:
    try:
        url = make_url(database_url)
    except Exception:
        driver = "unknown"
    else:
        driver = url.drivername
    return {
        "driver": driver,
        "database_fingerprint": _database_fingerprint(database_url),
    }


def business_semantic_yaml_path(database_url: str, yaml_dir: str | Path) -> Path:
    identity = _database_identity(database_url)
    label = _safe_slug(identity.get("driver", "database"), fallback="database")
    digest = identity["database_fingerprint"]
    return Path(yaml_dir).expanduser() / f"business_semantics_{label}_{digest}.yaml"


def _schema_signature(catalog: SchemaCatalog) -> str:
    parts: list[str] = []
    for table in sorted(catalog.tables, key=lambda item: item.name):
        parts.append(table.name)
        for column in sorted(table.columns, key=lambda item: item.name):
            parts.append(f"{table.name}.{column.name}:{column.data_type}:{column.nullable}:{column.default or ''}:{column.description or ''}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _add_term(terms: dict[str, BusinessSemanticTerm], term: str, *, table: str, column: str | None = None, kind: str, source: str) -> None:
    text = _clean_text(term)
    if not text:
        return
    key = text.lower()
    existing = terms.get(key)
    column_ref = f"{table}.{column}" if column else None
    if existing is None:
        terms[key] = BusinessSemanticTerm(
            term=text,
            kind=kind,
            tables=[table],
            columns=[column_ref] if column_ref else [],
            sources=[source],
        )
        return
    existing.tables = _dedupe([*existing.tables, table])
    if column_ref:
        existing.columns = _dedupe([*existing.columns, column_ref])
    existing.sources = _dedupe([*existing.sources, source])
    if existing.kind == "alias" and kind != "alias":
        existing.kind = kind


def _column_kind(column: SchemaColumn) -> str:
    column_name = column.name.lower()
    data_type = column.data_type.lower()
    if column_name in _INTERNAL_AUDIT_COLUMNS or column_name in _INTERNAL_AUDIT_TIME_COLUMNS:
        return "internal"
    if column.semantic_role in {"metric", "dimension", "timestamp", "identifier", "foreign_key", "internal"}:
        return column.semantic_role
    if column.is_primary_key or _IDENTIFIER_LIKE_COLUMN_PATTERN.search(column_name):
        return "identifier"
    if column_name in {"status", "type", "sort"} or column_name.endswith("_status") or column_name.endswith("_type"):
        return "dimension"
    if column_name.endswith("_user"):
        return "internal"
    if any(token in data_type for token in ["date", "time"]):
        return "timestamp"
    metric_name_tokens = ("amount", "price", "number", "quantity", "qty", "count", "total", "copies")
    if any(token in column_name for token in metric_name_tokens):
        return "metric"
    if any(token in data_type for token in ["decimal", "numeric", "float", "double"]):
        return "metric"
    return "dimension"


def _extract_enum_values(description: str | None) -> dict[str, str]:
    text = _clean_text(description)
    if not text:
        return {}
    matches = list(re.finditer(r"[-+]?\d+", text))
    values: dict[str, str] = {}
    for index, match in enumerate(matches):
        label_start = match.end()
        label_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        label = text[label_start:label_end].strip(" :=：,，;；。")
        if label and len(label) <= 20:
            values[match.group(0)] = label
    if values:
        return values
    for match in _ENUM_PAIR_PATTERN.finditer(text):
        value = match.group("value").strip()
        label = match.group("label").strip(" ,，;；。")
        if label and len(label) <= 20:
            values[value] = label
    return values


def _table_maps(catalog: SchemaCatalog) -> tuple[dict[str, SchemaTable], dict[str, set[str]]]:
    table_by_name = {table.name: table for table in catalog.tables}
    columns_by_table = {table.name: {column.name for column in table.columns} for table in catalog.tables}
    return table_by_name, columns_by_table


def _parse_column_ref(ref: str, table_by_name: dict[str, SchemaTable], columns_by_table: dict[str, set[str]]) -> tuple[str, str] | None:
    match = _REF_PATTERN.match(_clean_text(ref))
    if not match or not match.group(2):
        return None
    table_name, column_name = match.group(1), match.group(2)
    if table_name in table_by_name and column_name in columns_by_table.get(table_name, set()):
        return table_name, column_name
    return None


def _parse_table_ref(ref: str, table_by_name: dict[str, SchemaTable]) -> str | None:
    match = _REF_PATTERN.match(_clean_text(ref))
    if not match or match.group(2):
        return None
    table_name = match.group(1)
    return table_name if table_name in table_by_name else None


def derive_business_semantics(catalog: SchemaCatalog) -> BusinessSemanticLayer:
    terms: dict[str, BusinessSemanticTerm] = {}
    metrics: list[BusinessMetric] = []
    dimensions: list[BusinessDimension] = []
    enums: list[BusinessEnum] = []

    for table in catalog.tables:
        table_sources = [table.name, table.description or "", *table.aliases, *table.business_terms, *table.searchable_terms]
        for term in table_sources:
            _add_term(terms, term, table=table.name, kind="table", source="schema")
        for column in table.columns:
            kind = _column_kind(column)
            column_terms = [column.name, column.description or "", *column.business_terms, column.semantic_role or ""]
            for term in column_terms:
                _add_term(terms, term, table=table.name, column=column.name, kind=kind, source="schema")
            aliases = _dedupe([column.name, *column.business_terms])
            if kind == "metric":
                metrics.append(
                    BusinessMetric(
                        name=column.business_terms[0] if column.business_terms else column.name,
                        table=table.name,
                        column=column.name,
                        aliases=aliases,
                        description=column.description,
                        source="schema",
                    )
                )
            elif kind in {"dimension", "timestamp"}:
                dimensions.append(
                    BusinessDimension(
                        name=column.business_terms[0] if column.business_terms else column.name,
                        table=table.name,
                        column=column.name,
                        aliases=aliases,
                        description=column.description,
                        source="schema",
                    )
                )
            enum_values = _extract_enum_values(column.description)
            if enum_values:
                enum_aliases = _dedupe([column.name, *column.business_terms, *(enum_values.values())])
                enums.append(
                    BusinessEnum(
                        name=f"{table.name}.{column.name}",
                        table=table.name,
                        column=column.name,
                        values=enum_values,
                        aliases=enum_aliases,
                        source="schema",
                    )
                )
                for label in enum_values.values():
                    _add_term(terms, label, table=table.name, column=column.name, kind="enum", source="schema")

    return BusinessSemanticLayer(
        terms=sorted(terms.values(), key=lambda item: item.term.lower()),
        metrics=metrics,
        dimensions=dimensions,
        enums=enums,
        default_filters=[],
        diagnostics=[],
    )


def _read_yaml_mapping(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    diagnostics: list[dict[str, str]] = []
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}, diagnostics
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError as error:
            diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_YAML", "message": f"Semantic override YAML parse failed: {error.__class__.__name__}"})
            return {}, diagnostics
        payload = loaded if loaded is not None else {}
    if not isinstance(payload, dict):
        diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_ROOT", "message": "Semantic override root must be a mapping."})
        return {}, diagnostics
    return payload, diagnostics


def _load_override_payload(path: str | None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    diagnostics: list[dict[str, str]] = []
    if not path:
        return {}, diagnostics
    override_path = Path(path).expanduser()
    if not override_path.exists():
        diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_NOT_FOUND", "message": "Configured semantic override file was not found."})
        return {}, diagnostics
    payload, read_diagnostics = _read_yaml_mapping(override_path)
    return payload, [*diagnostics, *read_diagnostics]


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _validate_columns(raw_columns: Any, table_by_name: dict[str, SchemaTable], columns_by_table: dict[str, set[str]], diagnostics: list[dict[str, str]], owner: str) -> list[str]:
    valid: list[str] = []
    for raw_ref in _as_list(raw_columns):
        ref = _clean_text(raw_ref)
        parsed = _parse_column_ref(ref, table_by_name, columns_by_table)
        if parsed is None:
            diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_COLUMN", "message": f"Ignored invalid column reference for {owner}: {ref}"})
            continue
        valid.append(f"{parsed[0]}.{parsed[1]}")
    return _dedupe(valid)


def _validate_table(raw_table: Any, table_by_name: dict[str, SchemaTable], diagnostics: list[dict[str, str]], owner: str) -> str | None:
    table_name = _parse_table_ref(_clean_text(raw_table), table_by_name)
    if table_name is None:
        diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_TABLE", "message": f"Ignored invalid table reference for {owner}: {_clean_text(raw_table)}"})
    return table_name


def _validate_sql_fragment(
    fragment: Any,
    table_by_name: dict[str, SchemaTable],
    columns_by_table: dict[str, set[str]],
    diagnostics: list[dict[str, str]],
    owner: str,
    *,
    required_table: str | None = None,
) -> tuple[str | None, list[str]]:
    text = _clean_text(fragment)
    if not text:
        return None, []
    if _DANGEROUS_FRAGMENT_PATTERN.search(text):
        diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_UNSAFE_FRAGMENT", "message": f"Ignored unsafe SQL fragment for {owner}."})
        return None, []
    references: list[str] = []
    for table_name, column_name in _QUALIFIED_REF_PATTERN.findall(text):
        if table_name not in table_by_name or column_name not in columns_by_table.get(table_name, set()):
            diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_FRAGMENT_REF", "message": f"Ignored SQL fragment with invalid reference for {owner}: {table_name}.{column_name}"})
            return None, []
        if required_table is not None and table_name != required_table:
            diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_TABLE_COLUMN_MISMATCH", "message": f"Ignored SQL fragment for {owner}: table does not match referenced column."})
            return None, []
        references.append(f"{table_name}.{column_name}")
    return text, _dedupe(references)


def _empty_override_payload() -> dict[str, dict[str, Any]]:
    return {section: {} for section in _DEFAULT_YAML_SECTIONS}


def _generated_yaml_payload(catalog: SchemaCatalog, base: BusinessSemanticLayer, database_url: str) -> dict[str, Any]:
    identity = _database_identity(database_url)
    aliases: dict[str, dict[str, Any]] = {}
    for table in catalog.tables:
        table_aliases = _dedupe([*table.aliases, *table.business_terms])
        if table_aliases:
            aliases[table.name] = {"tables": [table.name], "aliases": table_aliases}
        for column in table.columns:
            column_aliases = _dedupe(column.business_terms)
            if column_aliases:
                aliases[f"{table.name}.{column.name}"] = {"columns": [f"{table.name}.{column.name}"], "aliases": column_aliases}
    return {
        "metadata": {
            "format_version": 1,
            "database_identity": identity,
            "schema_signature": _schema_signature(catalog),
            "note": "Generated from live schema metadata. Edit only the overrides section.",
        },
        "generated": {
            "aliases": aliases,
            "metrics": {metric.name: metric.model_dump(exclude={"source"}, exclude_none=True) for metric in base.metrics if metric.source == "schema"},
            "dimensions": {dimension.name: dimension.model_dump(exclude={"source"}, exclude_none=True) for dimension in base.dimensions if dimension.source == "schema"},
            "enums": {enum.name: enum.model_dump(exclude={"source"}, exclude_none=True) for enum in base.enums if enum.source == "schema"},
            "default_filters": {},
        },
        "overrides": _empty_override_payload(),
    }


def _extract_yaml_overrides(payload: dict[str, Any]) -> dict[str, Any]:
    overrides = payload.get("overrides")
    if isinstance(overrides, dict):
        return {section: overrides.get(section) or {} for section in _DEFAULT_YAML_SECTIONS}
    return {section: payload.get(section) or {} for section in _DEFAULT_YAML_SECTIONS}


def _load_or_refresh_yaml_overrides(catalog: SchemaCatalog, base: BusinessSemanticLayer, database_url: str, yaml_dir: str | Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    diagnostics: list[dict[str, str]] = []
    yaml_path = business_semantic_yaml_path(database_url, yaml_dir)
    try:
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        diagnostics.append({"level": "warning", "code": "SEMANTIC_YAML_DIRECTORY_ERROR", "message": f"Semantic YAML directory could not be prepared: {error.__class__.__name__}"})
        return _empty_override_payload(), diagnostics

    payload: dict[str, Any] = {}
    if yaml_path.exists():
        try:
            payload, diagnostics = _read_yaml_mapping(yaml_path)
        except OSError as error:
            diagnostics.append({"level": "warning", "code": "SEMANTIC_YAML_READ_ERROR", "message": f"Semantic YAML file could not be read: {error.__class__.__name__}"})
            payload = {}
    overrides = _extract_yaml_overrides(payload) if payload else _empty_override_payload()
    refreshed = _generated_yaml_payload(catalog, base, database_url)
    refreshed["overrides"] = overrides
    try:
        yaml_path.write_text(yaml.safe_dump(refreshed, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except OSError as error:
        diagnostics.append({"level": "warning", "code": "SEMANTIC_YAML_WRITE_ERROR", "message": f"Semantic YAML file could not be refreshed: {error.__class__.__name__}"})
    return overrides, diagnostics


def merge_business_semantic_overrides(catalog: SchemaCatalog, base: BusinessSemanticLayer, override_path: str | None = None, override_payload: dict[str, Any] | None = None) -> BusinessSemanticLayer:
    payload, diagnostics = (override_payload or {}, []) if override_payload is not None else _load_override_payload(override_path)
    table_by_name, columns_by_table = _table_maps(catalog)
    terms = {term.term.lower(): term.model_copy(deep=True) for term in base.terms}
    metrics = [metric.model_copy(deep=True) for metric in base.metrics]
    dimensions = [dimension.model_copy(deep=True) for dimension in base.dimensions]
    enums = [enum.model_copy(deep=True) for enum in base.enums]
    default_filters = [item.model_copy(deep=True) for item in base.default_filters]

    for name, item in (payload.get("aliases") or {}).items() if isinstance(payload.get("aliases"), dict) else []:
        if not isinstance(item, dict):
            diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_ALIAS", "message": f"Ignored alias {name}: value must be a mapping."})
            continue
        valid_tables = [_validate_table(table, table_by_name, diagnostics, f"alias {name}") for table in _as_list(item.get("tables"))]
        valid_tables = [table for table in valid_tables if table]
        valid_columns = _validate_columns(item.get("columns"), table_by_name, columns_by_table, diagnostics, f"alias {name}")
        if not valid_tables and not valid_columns:
            continue
        all_aliases = _dedupe([str(name), *[str(alias) for alias in _as_list(item.get("aliases"))]])
        for alias in all_aliases:
            key = alias.lower()
            terms[key] = BusinessSemanticTerm(
                term=alias,
                kind="alias",
                tables=_dedupe([*valid_tables, *[column.split(".", 1)[0] for column in valid_columns]]),
                columns=valid_columns,
                sources=["override"],
            )

    for section, model, target in [
        ("metrics", BusinessMetric, metrics),
        ("dimensions", BusinessDimension, dimensions),
    ]:
        raw_items = payload.get(section) or {}
        if not isinstance(raw_items, dict):
            continue
        for name, item in raw_items.items():
            if not isinstance(item, dict):
                diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_INVALID_ITEM", "message": f"Ignored {section} {name}: value must be a mapping."})
                continue
            table = _validate_table(item.get("table"), table_by_name, diagnostics, f"{section} {name}")
            column_ref = _validate_columns(item.get("column") or item.get("columns"), table_by_name, columns_by_table, diagnostics, f"{section} {name}")
            if table is None or not column_ref:
                continue
            first_table, first_column = column_ref[0].split(".", 1)
            if first_table != table:
                diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_TABLE_COLUMN_MISMATCH", "message": f"Ignored {section} {name}: table does not match column reference."})
                continue
            expression, _expression_columns = _validate_sql_fragment(
                item.get("expression"),
                table_by_name,
                columns_by_table,
                diagnostics,
                f"{section} {name} expression",
                required_table=table,
            ) if item.get("expression") else (None, [])
            target.append(model(name=str(name), table=table, column=first_column, aliases=_dedupe([str(alias) for alias in _as_list(item.get("aliases"))]), description=item.get("description"), expression=expression, source="override"))
            _add_term(terms, str(name), table=table, column=first_column, kind=section[:-1], source="override")
            for alias in _as_list(item.get("aliases")):
                _add_term(terms, str(alias), table=table, column=first_column, kind=section[:-1], source="override")

    raw_enums = payload.get("enums") or {}
    if isinstance(raw_enums, dict):
        for name, item in raw_enums.items():
            if not isinstance(item, dict):
                continue
            table = _validate_table(item.get("table"), table_by_name, diagnostics, f"enum {name}")
            column_refs = _validate_columns(item.get("column") or item.get("columns"), table_by_name, columns_by_table, diagnostics, f"enum {name}")
            values = item.get("values") if isinstance(item.get("values"), dict) else {}
            if table is None or not column_refs or not values:
                continue
            first_table, first_column = column_refs[0].split(".", 1)
            if first_table != table:
                diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_TABLE_COLUMN_MISMATCH", "message": f"Ignored enum {name}: table does not match column reference."})
                continue
            enum_values = {str(key): str(value) for key, value in values.items()}
            aliases = _dedupe([str(alias) for alias in _as_list(item.get("aliases"))])
            enums.append(BusinessEnum(name=str(name), table=table, column=first_column, values=enum_values, aliases=aliases, source="override"))
            for alias in [str(name), *aliases, *enum_values.values()]:
                _add_term(terms, alias, table=table, column=first_column, kind="enum", source="override")

    raw_filters = payload.get("default_filters") or {}
    if isinstance(raw_filters, dict):
        for name, item in raw_filters.items():
            if not isinstance(item, dict):
                continue
            table = _validate_table(item.get("table"), table_by_name, diagnostics, f"default_filter {name}")
            columns = _validate_columns(item.get("columns"), table_by_name, columns_by_table, diagnostics, f"default_filter {name}")
            condition, condition_columns = _validate_sql_fragment(
                item.get("condition"),
                table_by_name,
                columns_by_table,
                diagnostics,
                f"default_filter {name}",
                required_table=table,
            )
            columns = _dedupe([*columns, *condition_columns])
            if table is None or condition is None:
                continue
            if not columns:
                diagnostics.append({"level": "warning", "code": "SEMANTIC_OVERRIDE_UNVALIDATED_FILTER", "message": f"Ignored default_filter {name}: no valid column references were provided."})
                continue
            default_filters.append(BusinessDefaultFilter(name=str(name), table=table, condition=condition, columns=columns, aliases=_dedupe([str(alias) for alias in _as_list(item.get("aliases"))]), source="override"))
            for alias in [str(name), *[str(alias) for alias in _as_list(item.get("aliases"))]]:
                _add_term(terms, alias, table=table, kind="default_filter", source="override")

    return BusinessSemanticLayer(
        terms=sorted(terms.values(), key=lambda item: item.term.lower()),
        metrics=metrics,
        dimensions=dimensions,
        enums=enums,
        default_filters=default_filters,
        diagnostics=[*base.diagnostics, *diagnostics],
    )


def build_business_semantics(
    catalog: SchemaCatalog,
    override_path: str | None = None,
    *,
    yaml_enabled: bool = False,
    database_url: str | None = None,
    yaml_dir: str | Path = "yaml",
) -> BusinessSemanticLayer:
    base = derive_business_semantics(catalog)
    if yaml_enabled:
        payload, diagnostics = _load_or_refresh_yaml_overrides(catalog, base, database_url or catalog.database, yaml_dir)
        semantics = merge_business_semantic_overrides(catalog, base, override_payload=payload)
        semantics.diagnostics = [*semantics.diagnostics, *diagnostics]
        return semantics
    return merge_business_semantic_overrides(catalog, base, override_path)


def attach_business_semantics(
    catalog: SchemaCatalog,
    override_path: str | None = None,
    *,
    yaml_enabled: bool = False,
    database_url: str | None = None,
    yaml_dir: str | Path = "yaml",
) -> SchemaCatalog:
    catalog.business_semantics = build_business_semantics(
        catalog,
        override_path,
        yaml_enabled=yaml_enabled,
        database_url=database_url,
        yaml_dir=yaml_dir,
    )
    return catalog
