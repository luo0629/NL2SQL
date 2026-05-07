from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SemanticField(BaseModel):
    table: str | None = None
    column: str | None = None
    expression: str | None = None
    alias: str | None = None
    role: str | None = None
    source: str = "schema_linking"


class SemanticFilter(BaseModel):
    table: str
    column: str
    operator: str = "="
    param_index: int | None = None
    value_mention: str | None = None
    source: str = "value_linking"


class SemanticOrderBy(BaseModel):
    table: str | None = None
    column: str | None = None
    expression: str | None = None
    alias: str | None = None
    direction: Literal["ASC", "DESC"] = "ASC"
    source: str = "query_understanding"


class SemanticQuery(BaseModel):
    intent: str = "select"
    entities: list[str] = Field(default_factory=list)
    metrics: list[SemanticField] = Field(default_factory=list)
    dimensions: list[SemanticField] = Field(default_factory=list)
    filters: list[SemanticFilter] = Field(default_factory=list)
    time_range: dict[str, Any] | None = None
    joins: list[dict[str, Any]] = Field(default_factory=list)
    order_by: list[SemanticOrderBy] = Field(default_factory=list)
    limit: int | None = None
    confidence: float = 0.0
    confidence_reasons: list[str] = Field(default_factory=list)
    clarification_prompts: list[str] = Field(default_factory=list)
    source: str = "semantic_query_builder"


class SemanticQueryBuilder:
    def build(
        self,
        *,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        value_links: list[dict[str, Any]],
        join_path_plan: dict[str, Any],
        business_semantic_brief: dict[str, Any],
    ) -> SemanticQuery:
        matched_tables = list(schema_linking.get("matched_tables", schema_linking.get("linked_tables", [])))
        primary_table = str(join_path_plan.get("primary_table") or "").strip() or self._first_table_name(matched_tables)
        entities = [self._table_name(table) for table in matched_tables if self._table_name(table)]
        dimensions = self._extract_fields(matched_tables, role_filter={"dimension", "timestamp"})
        metrics = self._extract_fields(matched_tables, role_filter={"metric"})

        intent = str(query_understanding.get("intent") or "select")
        if intent == "aggregate" and not metrics:
            metrics = [
                SemanticField(
                    table=primary_table,
                    expression="COUNT(*)",
                    alias="row_count",
                    role="metric",
                    source="query_understanding",
                )
            ]

        filters = self._build_filters(value_links)
        order_by = self._build_order_by(query_understanding, metrics, dimensions, primary_table)
        limit = query_understanding.get("limit")
        confidence, reasons, prompts = self._score_confidence(
            matched_tables=matched_tables,
            join_path_plan=join_path_plan,
            business_semantic_brief=business_semantic_brief,
            filters=filters,
            value_links=value_links,
        )

        return SemanticQuery(
            intent=intent,
            entities=entities,
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            time_range=query_understanding.get("time_range") if isinstance(query_understanding.get("time_range"), dict) else None,
            joins=list(join_path_plan.get("edges", [])),
            order_by=order_by,
            limit=limit if isinstance(limit, int) and limit > 0 else None,
            confidence=confidence,
            confidence_reasons=reasons,
            clarification_prompts=prompts,
        )

    def _first_table_name(self, matched_tables: list[dict[str, Any]]) -> str | None:
        for table in matched_tables:
            table_name = self._table_name(table)
            if table_name:
                return table_name
        return None

    def _table_name(self, table: dict[str, Any]) -> str:
        return str(table.get("table_name") or table.get("name") or "").strip()

    def _extract_fields(
        self,
        matched_tables: list[dict[str, Any]],
        *,
        role_filter: set[str],
    ) -> list[SemanticField]:
        fields: list[SemanticField] = []
        seen: set[tuple[str | None, str | None]] = set()
        for table in matched_tables:
            table_name = self._table_name(table)
            for column in table.get("matched_columns", []):
                if not isinstance(column, dict):
                    continue
                semantic_role = column.get("semantic_role")
                if semantic_role not in role_filter:
                    continue
                column_name = str(column.get("column_name") or column.get("name") or "").strip()
                key = (table_name, column_name)
                if not column_name or key in seen:
                    continue
                seen.add(key)
                fields.append(
                    SemanticField(
                        table=table_name,
                        column=column_name,
                        role=str(semantic_role),
                        source="schema_linking",
                    )
                )
        return fields

    def _build_filters(self, value_links: list[dict[str, Any]]) -> list[SemanticFilter]:
        filters: list[SemanticFilter] = []
        for index, value_link in enumerate(value_links):
            table = str(value_link.get("table") or "").strip()
            column = str(value_link.get("column") or "").strip()
            if not table or not column or value_link.get("match_type") == "unresolved":
                continue
            filters.append(
                SemanticFilter(
                    table=table,
                    column=column,
                    operator="=",
                    param_index=index,
                    value_mention=str(value_link.get("mention") or "") or None,
                )
            )
        return filters

    def _build_order_by(
        self,
        query_understanding: dict[str, Any],
        metrics: list[SemanticField],
        dimensions: list[SemanticField],
        primary_table: str | None,
    ) -> list[SemanticOrderBy]:
        raw_items = query_understanding.get("order_by", [])
        order_items: list[SemanticOrderBy] = []
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                direction = "DESC" if str(item.get("direction") or "ASC").upper() == "DESC" else "ASC"
                column = str(item.get("column") or "").strip() or None
                table = str(item.get("table") or primary_table or "").strip() or None
                if column:
                    order_items.append(SemanticOrderBy(table=table, column=column, direction=direction))
                    continue
                target = metrics[0] if metrics else (dimensions[0] if dimensions else None)
                if target:
                    order_items.append(
                        SemanticOrderBy(
                            table=target.table,
                            column=target.column,
                            expression=target.expression,
                            alias=target.alias,
                            direction=direction,
                        )
                    )
        if not order_items and query_understanding.get("limit") and (metrics or dimensions):
            target = metrics[0] if metrics else dimensions[0]
            order_items.append(
                SemanticOrderBy(
                    table=target.table,
                    column=target.column,
                    expression=target.expression,
                    alias=target.alias,
                    direction="DESC" if metrics else "ASC",
                    source="execution_gate_stability",
                )
            )
        return order_items

    def _score_confidence(
        self,
        *,
        matched_tables: list[dict[str, Any]],
        join_path_plan: dict[str, Any],
        business_semantic_brief: dict[str, Any],
        filters: list[SemanticFilter],
        value_links: list[dict[str, Any]],
    ) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        prompts: list[str] = []

        if not matched_tables:
            schema_score = 0.0
            prompts.append("没有匹配到可靠的数据表，请补充要查询的业务对象。")
        else:
            positive_scores = [int(table.get("score", 0)) for table in matched_tables if int(table.get("score", 0)) > 0]
            schema_score = 0.9 if positive_scores else 0.35
            reasons.append("schema grounding 命中候选表" if positive_scores else "schema grounding 使用默认候选表")
            if not positive_scores:
                prompts.append("当前问题没有命中明确表或字段，请补充表名、业务对象或字段。")

        unresolved_tables = list(join_path_plan.get("unresolved_tables", []))
        join_confidence = str(join_path_plan.get("plan_confidence") or "none")
        if len(matched_tables) <= 1 and not unresolved_tables:
            join_score = 0.9
            reasons.append("单表查询不需要连表规划")
        else:
            join_score = {"high": 0.9, "medium": 0.75, "low": 0.45, "none": 0.25}.get(join_confidence, 0.25)
            reasons.append(f"join planning 置信度为 {join_confidence}")
            if unresolved_tables:
                prompts.append(f"无法可靠规划连表：{', '.join(str(item) for item in unresolved_tables[:3])}。")

        unresolved_terms = list(business_semantic_brief.get("uncertainties", []))
        unresolved_value_links = [link for link in value_links if link.get("match_type") == "unresolved"]
        semantic_score = 0.85
        if unresolved_terms:
            semantic_score -= 0.2
            prompts.append(f"有未解析业务术语：{', '.join(str(item) for item in unresolved_terms[:3])}。")
        if unresolved_value_links:
            semantic_score -= 0.2
            prompts.append("部分筛选值没有绑定到真实数据库取值，请换用更明确的取值。")
        if filters:
            reasons.append("筛选条件已通过 value linking 绑定")

        confidence = max(0.0, min(schema_score, join_score, semantic_score))
        return round(confidence, 2), reasons, list(dict.fromkeys(prompts))
