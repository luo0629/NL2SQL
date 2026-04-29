from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_value_mappings() -> dict[str, dict[str, str]]:
    """Load per-table per-column value mapping hints.

    File format:
      { "table": { "column": "1=foo,0=bar" } }
    """

    path = Path(__file__).resolve().parent / "value_mappings.json"
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {}

    normalized: dict[str, dict[str, str]] = {}
    for table_name, table_mappings in raw.items():
        if not isinstance(table_name, str) or not isinstance(table_mappings, dict):
            continue
        table_key = table_name.strip()
        if not table_key:
            continue

        column_map: dict[str, str] = {}
        for column_name, mapping in table_mappings.items():
            if not isinstance(column_name, str) or not isinstance(mapping, str):
                continue
            column_key = column_name.strip()
            mapping_value = mapping.strip()
            if not column_key or not mapping_value:
                continue
            column_map[column_key] = mapping_value

        if column_map:
            normalized[table_key] = column_map

    return normalized


def merge_column_description(
    *,
    db_description: str | None,
    fallback_mapping: str | None,
) -> str | None:
    """Merge DB column comment with fallback mapping.

    Rules:
    - Prefer DB description.
    - If DB description missing, use fallback mapping.
    - If both exist and fallback not already mentioned, append as ' | values: ...'.
    """

    db_text = (db_description or "").strip()
    fallback_text = (fallback_mapping or "").strip()

    if not db_text and not fallback_text:
        return None
    if db_text and not fallback_text:
        return db_text
    if not db_text and fallback_text:
        return fallback_text

    if fallback_text in db_text:
        return db_text
    return f"{db_text} | values: {fallback_text}"


def get_fallback_mapping_for_column(
    mappings: dict[str, dict[str, str]],
    *,
    table_name: str,
    column_name: str,
) -> str | None:
    table_map = mappings.get(table_name) or mappings.get(table_name.lower())
    if not table_map:
        return None

    return table_map.get(column_name) or table_map.get(column_name.lower())

