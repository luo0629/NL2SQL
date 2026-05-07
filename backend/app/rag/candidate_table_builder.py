from __future__ import annotations

from pydantic import BaseModel, Field


class CandidateTableReason(BaseModel):
    table: str
    reason: str
    source: str
    confidence: float = 0.0


class CandidateTableSet(BaseModel):
    target_table: str | None = None
    required_tables: list[str] = Field(default_factory=list)
    optional_tables: list[str] = Field(default_factory=list)
    table_evidence_tier: dict[str, str] = Field(default_factory=dict)
    table_reasons: list[CandidateTableReason] = Field(default_factory=list)
    related_table_count: int = 0
    confidence: float = 0.0


class CandidateTableBuilder:
    def build(
        self,
        query_understanding: dict[str, object],
        schema_linking: dict[str, object],
        value_links: list[dict[str, object]],
    ) -> CandidateTableSet:
        reasons: dict[str, CandidateTableReason] = {}
        matched_tables = self._matched_tables(schema_linking)
        target_table = self._target_table(matched_tables)

        if target_table:
            self._add_reason(
                reasons,
                target_table,
                "主表来自 Schema Linking 排名最高的目标表",
                "schema_linking",
                self._table_confidence(matched_tables[0]),
            )

        for table in matched_tables:
            table_name = str(table.get("table_name") or table.get("name") or "").strip()
            if not table_name:
                continue
            for column in table.get("matched_columns", []) or []:
                if not isinstance(column, dict):
                    continue
                column_name = str(column.get("column_name") or column.get("name") or "").strip()
                if not column_name:
                    continue
                self._add_reason(
                    reasons,
                    table_name,
                    f"Schema Linking 匹配字段 {table_name}.{column_name}",
                    "schema_linking_column",
                    self._column_confidence(column),
                )

        for value_link in value_links:
            table_name = str(value_link.get("table") or "").strip()
            column_name = str(value_link.get("column") or "").strip()
            if not table_name:
                continue
            mention = str(value_link.get("mention") or "").strip()
            reason = f"Value Linking 将值 {mention or '<unknown>'} 绑定到 {table_name}.{column_name or '*'}"
            self._add_reason(
                reasons,
                table_name,
                reason,
                "value_linking",
                self._safe_float(value_link.get("confidence")),
            )

        if query_understanding.get("possible_multi_table") and len(reasons) == 1:
            only_table = next(iter(reasons))
            self._add_reason(
                reasons,
                only_table,
                "Query Understanding 标记可能需要多表，当前仅发现一个候选表",
                "query_understanding",
                self._safe_float(query_understanding.get("confidence")),
            )

        required_tables, optional_tables, evidence_tier = self._split_required_optional_tables(
            reasons=reasons,
            target_table=target_table,
            possible_multi_table=bool(query_understanding.get("possible_multi_table")),
        )

        confidence_values = [reason.confidence for reason in reasons.values() if reason.confidence > 0]
        confidence = min(confidence_values) if confidence_values else 0.0
        if query_understanding.get("missing_slots"):
            confidence = max(0.0, confidence - 0.15)

        return CandidateTableSet(
            target_table=target_table,
            required_tables=required_tables,
            optional_tables=optional_tables,
            table_evidence_tier=evidence_tier,
            table_reasons=list(reasons.values()),
            related_table_count=len(required_tables),
            confidence=round(confidence, 3),
        )

    def _split_required_optional_tables(
        self,
        *,
        reasons: dict[str, CandidateTableReason],
        target_table: str | None,
        possible_multi_table: bool,
    ) -> tuple[list[str], list[str], dict[str, str]]:
        ordered_tables = sorted(reasons)
        if target_table and target_table in ordered_tables:
            ordered_tables.remove(target_table)
            ordered_tables.insert(0, target_table)
        if not ordered_tables:
            return [], [], {}

        required_tables: list[str] = []
        optional_tables: list[str] = []
        evidence_tier: dict[str, str] = {}
        target_required = target_table or ordered_tables[0]

        for table in ordered_tables:
            reason = reasons[table]
            is_target = table == target_required
            high_confidence = reason.confidence >= 0.8
            strong_value_link = reason.source == "value_linking" and reason.confidence >= 0.78
            if is_target:
                required_tables.append(table)
                evidence_tier[table] = "required"
                continue
            if possible_multi_table and (high_confidence or strong_value_link):
                required_tables.append(table)
                evidence_tier[table] = "required"
            else:
                optional_tables.append(table)
                evidence_tier[table] = "optional"
        return required_tables, optional_tables, evidence_tier

    def _matched_tables(self, schema_linking: dict[str, object]) -> list[dict[str, object]]:
        raw_tables = schema_linking.get("matched_tables", schema_linking.get("linked_tables", []))
        return [table for table in raw_tables if isinstance(table, dict)] if isinstance(raw_tables, list) else []

    def _target_table(self, matched_tables: list[dict[str, object]]) -> str | None:
        if not matched_tables:
            return None
        return str(matched_tables[0].get("table_name") or matched_tables[0].get("name") or "").strip() or None

    def _add_reason(
        self,
        reasons: dict[str, CandidateTableReason],
        table: str,
        reason: str,
        source: str,
        confidence: float,
    ) -> None:
        existing = reasons.get(table)
        if existing is None or confidence > existing.confidence:
            reasons[table] = CandidateTableReason(
                table=table,
                reason=reason,
                source=source,
                confidence=round(confidence, 3),
            )

    def _table_confidence(self, table: dict[str, object]) -> float:
        score = self._safe_float(table.get("score"))
        if score <= 0:
            return 0.5
        return min(0.95, 0.55 + score / 100)

    def _column_confidence(self, column: dict[str, object]) -> float:
        score = self._safe_float(column.get("score"))
        if score <= 0:
            return 0.45
        return min(0.95, 0.5 + score / 80)

    def _safe_float(self, value: object) -> float:
        if isinstance(value, int | float):
            return float(value)
        return 0.0
