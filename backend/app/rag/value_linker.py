from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.rag.schema_models import SchemaCatalog, SchemaColumn


class ValueLink(BaseModel):
    mention: str
    field_mention: str | None = None
    table: str | None = None
    column: str | None = None
    db_value: object | None = None
    confidence: float
    match_type: Literal["exact", "normalized", "fuzzy", "semantic", "sample_value", "typed_literal", "unresolved"]
    source: Literal["database", "sample", "mapping", "literal", "fallback"]
    operator: Literal["=", "LIKE"] = "="
    evidence: list[dict[str, object]] = Field(default_factory=list)


class ValueLinkingResult(BaseModel):
    value_links: list[ValueLink] = Field(default_factory=list)


class ValueLinker:
    def link(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        catalog: SchemaCatalog | None = None,
    ) -> ValueLinkingResult:
        value_mentions = [
            str(value).strip()
            for value in query_understanding.get("value_mentions", [])
            if str(value).strip()
        ]
        condition_mentions = self._extract_condition_mentions(query_understanding)
        candidate_columns = self._candidate_columns(schema_linking, catalog)

        value_links: list[ValueLink] = []
        for mention in value_mentions:
            mapped_link = self._link_from_column_mappings(
                mention,
                condition_mentions,
                candidate_columns,
            )
            if mapped_link is not None:
                value_links.append(mapped_link)
                continue

            sample_link = self._link_from_sample_values(
                mention,
                condition_mentions,
                candidate_columns,
            )
            if sample_link is not None:
                value_links.append(sample_link)
                continue

            if self._is_typed_literal(mention):
                table, column = self._first_candidate(candidate_columns)
                value_links.append(
                    ValueLink(
                        mention=mention,
                        field_mention=condition_mentions[0] if condition_mentions else None,
                        table=table,
                        column=column,
                        db_value=self._coerce_literal(mention),
                        confidence=0.8,
                        match_type="typed_literal",
                        source="literal",
                        operator="=",
                        evidence=[{"source": "typed_literal", "score": 0.8}],
                    )
                )
                continue

            table, column = self._first_candidate(candidate_columns)
            value_links.append(
                ValueLink(
                    mention=mention,
                    field_mention=condition_mentions[0] if condition_mentions else None,
                    table=table,
                    column=column,
                    db_value=None,
                    confidence=0.0,
                    match_type="unresolved",
                    source="fallback",
                    operator="=",
                    evidence=[{"source": "unresolved", "score": 0.0}],
                )
            )

        return ValueLinkingResult(value_links=value_links)

    def _extract_condition_mentions(self, query_understanding: dict[str, Any]) -> list[str]:
        mentions: list[str] = []
        for condition in query_understanding.get("condition_mentions", []):
            if isinstance(condition, dict) and condition.get("mention"):
                mentions.append(str(condition["mention"]))
            elif condition:
                mentions.append(str(condition))
        return mentions

    def _candidate_columns(
        self,
        schema_linking: dict[str, Any],
        catalog: SchemaCatalog | None,
    ) -> list[tuple[str, str, SchemaColumn | None]]:
        candidates: list[tuple[str, str, SchemaColumn | None]] = []
        catalog_lookup = {
            table.name: {column.name: column for column in table.columns}
            for table in (catalog.tables if catalog else [])
        }

        for table in schema_linking.get("matched_tables", schema_linking.get("linked_tables", [])):
            table_name = str(table.get("table_name") or table.get("name") or "").strip()
            if not table_name:
                continue
            matched_columns = table.get("matched_columns", [])
            for matched_column in matched_columns:
                column_name = str(matched_column.get("column_name") or matched_column.get("name") or "").strip()
                if column_name:
                    candidates.append((table_name, column_name, catalog_lookup.get(table_name, {}).get(column_name)))

            if catalog and not matched_columns:
                for column_name, column in catalog_lookup.get(table_name, {}).items():
                    candidates.append((table_name, column_name, column))

        return candidates

    def _link_from_column_mappings(
        self,
        mention: str,
        condition_mentions: list[str],
        candidate_columns: list[tuple[str, str, SchemaColumn | None]],
    ) -> ValueLink | None:
        normalized_mention = self._normalize(mention)
        for table_name, column_name, column in candidate_columns:
            if column is None or not column.description:
                continue
            for raw_value, label in self._parse_mapping_description(column.description).items():
                normalized_label = self._normalize(label)
                if normalized_mention == normalized_label:
                    return ValueLink(
                        mention=mention,
                        field_mention=condition_mentions[0] if condition_mentions else None,
                        table=table_name,
                        column=column_name,
                        db_value=raw_value,
                        confidence=0.95,
                        match_type="exact",
                        source="mapping",
                        operator="LIKE" if self._is_text_column(column) else "=",
                        evidence=[{"source": "mapping", "label": label, "raw_value": raw_value, "score": 0.95}],
                    )
                if normalized_mention in normalized_label or normalized_label in normalized_mention:
                    return ValueLink(
                        mention=mention,
                        field_mention=condition_mentions[0] if condition_mentions else None,
                        table=table_name,
                        column=column_name,
                        db_value=raw_value,
                        confidence=0.85,
                        match_type="normalized",
                        source="mapping",
                        operator="LIKE" if self._is_text_column(column) else "=",
                        evidence=[{"source": "mapping", "label": label, "raw_value": raw_value, "score": 0.85}],
                    )
        return None


    def _link_from_sample_values(
        self,
        mention: str,
        condition_mentions: list[str],
        candidate_columns: list[tuple[str, str, SchemaColumn | None]],
    ) -> ValueLink | None:
        normalized_mention = self._normalize_for_semantic_match(mention)
        if not normalized_mention:
            return None

        best_link: ValueLink | None = None
        for table_name, column_name, column in candidate_columns:
            if column is None or not column.sample_values:
                continue
            for sample_value in column.sample_values:
                normalized_sample = self._normalize_for_semantic_match(str(sample_value))
                if not normalized_sample:
                    continue
                if normalized_mention == normalized_sample:
                    confidence = 0.93
                    match_type: Literal["exact", "normalized", "fuzzy", "semantic", "sample_value", "typed_literal", "unresolved"] = "sample_value"
                elif normalized_mention in normalized_sample or normalized_sample in normalized_mention:
                    confidence = 0.86
                    match_type = "normalized"
                elif self._has_semantic_overlap(normalized_mention, normalized_sample):
                    confidence = 0.78
                    match_type = "semantic"
                else:
                    continue

                operator = "LIKE" if self._is_text_column(column) else "="
                candidate = ValueLink(
                    mention=mention,
                    field_mention=condition_mentions[0] if condition_mentions else None,
                    table=table_name,
                    column=column_name,
                    db_value=sample_value,
                    confidence=confidence,
                    match_type=match_type,
                    source="sample",
                    operator=operator,
                    evidence=[
                        {
                            "source": "sample_value",
                            "sample_value": sample_value,
                            "score": confidence,
                        }
                    ],
                )
                if best_link is None or candidate.confidence > best_link.confidence:
                    best_link = candidate
        return best_link

    def _parse_mapping_description(self, description: str) -> dict[str, str]:
        mappings: dict[str, str] = {}
        for raw_value, label in re.findall(r"([^=,，\s]+)\s*=\s*([^,，\s]+)", description):
            mappings[raw_value.strip()] = label.strip()
        return mappings

    def _is_typed_literal(self, mention: str) -> bool:
        return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", mention.strip()))

    def _coerce_literal(self, mention: str) -> int | float:
        stripped = mention.strip()
        if "." in stripped:
            return float(stripped)
        return int(stripped)

    def _normalize(self, value: str) -> str:
        return re.sub(r"\s+", "", value.strip().lower())


    def _normalize_for_semantic_match(self, value: str) -> str:
        normalized = self._normalize(value)
        for suffix in ["的", "类", "型", "值"]:
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[: -len(suffix)]
        return normalized

    def _has_semantic_overlap(self, left: str, right: str) -> bool:
        if len(left) < 1 or len(right) < 1:
            return False
        if len(left) == 1 or len(right) == 1:
            return left[0] == right[0]
        return bool(set(left) & set(right)) and (left[0] == right[0] or left[-1] == right[-1])

    def _is_text_column(self, column: SchemaColumn | None) -> bool:
        if column is None:
            return False
        data_type = column.data_type.lower()
        return any(token in data_type for token in ["char", "text", "string", "varchar"])

    def _first_candidate(self, candidate_columns: list[tuple[str, str, SchemaColumn | None]]) -> tuple[str | None, str | None]:
        if not candidate_columns:
            return None, None
        table_name, column_name, _ = candidate_columns[0]
        return table_name, column_name
