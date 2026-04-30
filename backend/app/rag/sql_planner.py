from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SQLPlan(BaseModel):
    select: list[dict[str, object]] = Field(default_factory=list)
    from_table: str | None = None
    joins: list[dict[str, object]] = Field(default_factory=list)
    where: list[dict[str, object]] = Field(default_factory=list)
    group_by: list[dict[str, object]] = Field(default_factory=list)
    having: list[dict[str, object]] = Field(default_factory=list)
    order_by: list[dict[str, object]] = Field(default_factory=list)
    limit: int | None = None
    distinct: bool = False
    params: list[object] = Field(default_factory=list)
    provenance: dict[str, object] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)


class SQLPlanner:
    def build(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        value_links: list[dict[str, Any]],
        join_path_plan: dict[str, Any],
    ) -> SQLPlan:
        from_table = self._select_from_table(schema_linking, join_path_plan)
        group_by = self._build_group_by(query_understanding, schema_linking, from_table)
        select_fields = self._build_select_fields(schema_linking, from_table, query_understanding, group_by)
        where_clauses, params = self._build_where_clauses(value_links)
        having, params = self._build_having(query_understanding, select_fields, params)
        order_by = self._build_order_by(query_understanding, schema_linking, from_table, select_fields)
        uncertainties = list(join_path_plan.get("unresolved_tables", [])) + list(join_path_plan.get("ambiguous_paths", []))

        return SQLPlan(
            select=select_fields,
            from_table=from_table,
            joins=list(join_path_plan.get("edges", [])),
            where=where_clauses,
            group_by=group_by,
            having=having,
            order_by=order_by,
            limit=query_understanding.get("limit"),
            distinct=bool(join_path_plan.get("requires_distinct", False)),
            params=params,
            provenance={
                "select": "query_understanding" if self._has_aggregation(query_understanding) else "schema_linking",
                "from_table": "schema_linking" if from_table else None,
                "joins": "join_path_planning" if join_path_plan.get("edges") else None,
                "where": "value_linking" if where_clauses else None,
                "group_by": "query_understanding" if group_by else None,
                "having": "query_understanding" if having else None,
                "order_by": "query_understanding" if order_by else None,
                "limit": "query_understanding" if query_understanding.get("limit") is not None else None,
                "distinct": "join_path_planning" if join_path_plan.get("requires_distinct") else None,
                "uncertainties": "join_path_planning" if uncertainties else None,
            },
            uncertainties=[str(item) for item in uncertainties],
        )

    def _select_from_table(self, schema_linking: dict[str, Any], join_path_plan: dict[str, Any]) -> str | None:
        primary_table = join_path_plan.get("primary_table")
        if primary_table:
            return str(primary_table)

        matched_tables = schema_linking.get("matched_tables", schema_linking.get("linked_tables", []))
        if not matched_tables:
            return None
        first_table = matched_tables[0]
        return first_table.get("table_name") or first_table.get("name")

    def _build_select_fields(
        self,
        schema_linking: dict[str, Any],
        from_table: str | None,
        query_understanding: dict[str, Any] | None = None,
        group_by: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        query_understanding = query_understanding or {}
        group_by = group_by or []
        if self._has_aggregation(query_understanding):
            select_fields = [dict(item) for item in group_by]
            metric = self._select_metric(query_understanding, schema_linking, from_table)
            aggregation = str(metric.get("aggregation") or "COUNT").upper()
            table = metric.get("table")
            column = metric.get("column")
            if aggregation == "COUNT" and not column:
                expression = "COUNT(*)"
            else:
                expression = f"{aggregation}({table}.{column})" if table and column else "COUNT(*)"
            select_fields.append(
                {
                    "expression": expression,
                    "alias": metric.get("alias") or self._metric_alias(aggregation),
                    "table": table,
                    "column": column,
                    "aggregation": aggregation,
                    "source": "query_understanding",
                }
            )
            return select_fields

        matched_tables = schema_linking.get("matched_tables", schema_linking.get("linked_tables", []))
        for table in matched_tables:
            table_name = table.get("table_name") or table.get("name")
            if from_table and table_name != from_table:
                continue
            matched_columns = table.get("matched_columns", [])
            if matched_columns:
                return [
                    {
                        "table": table_name,
                        "column": column.get("column_name") or column.get("name"),
                        "source": "schema_linking",
                    }
                    for column in matched_columns[:3]
                    if column.get("column_name") or column.get("name")
                ]

        if from_table:
            return [{"table": from_table, "column": "id", "source": "schema_linking_default"}]
        return []

    def _build_order_by(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        from_table: str | None,
        select_fields: list[dict[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        select_fields = select_fields or []
        metric_select = next((item for item in select_fields if item.get("aggregation")), None)
        raw_order = query_understanding.get("order_by", [])
        if metric_select and raw_order:
            first_order = raw_order[0]
            direction = str(first_order.get("direction") or "DESC").upper() if isinstance(first_order, dict) else "DESC"
            return [
                {
                    "expression": metric_select.get("alias") or metric_select.get("expression"),
                    "direction": "DESC" if direction == "DESC" else "ASC",
                    "source": "query_understanding",
                }
            ]

        matched_tables = schema_linking.get("matched_tables", schema_linking.get("linked_tables", []))
        allowed_columns: dict[str, set[str]] = {}
        for table in matched_tables:
            table_name = str(table.get("table_name") or table.get("name") or "").strip()
            if not table_name:
                continue
            allowed_columns.setdefault(table_name, set())
            for column in table.get("matched_columns", []):
                column_name = str(column.get("column_name") or column.get("name") or "").strip()
                if column_name:
                    allowed_columns[table_name].add(column_name)

        normalized_items: list[dict[str, object]] = []
        for item in raw_order:
            if not isinstance(item, dict):
                continue
            table = str(item.get("table") or from_table or "").strip()
            column = str(item.get("column") or "").strip()
            if not table or not column or column not in allowed_columns.get(table, set()):
                continue
            direction = str(item.get("direction") or "ASC").upper()
            normalized_items.append(
                {
                    "table": table,
                    "column": column,
                    "direction": "DESC" if direction == "DESC" else "ASC",
                }
            )
        return normalized_items

    def _build_where_clauses(self, value_links: list[dict[str, Any]]) -> tuple[list[dict[str, object]], list[object]]:
        where_clauses: list[dict[str, object]] = []
        params: list[object] = []
        for value_link in value_links:
            table = value_link.get("table")
            column = value_link.get("column")
            if not table or not column:
                continue
            if value_link.get("match_type") == "unresolved":
                where_clauses.append(
                    {
                        "table": table,
                        "column": column,
                        "operator": "=",
                        "source": "unresolved_value_linking",
                        "value_mention": value_link.get("mention"),
                    }
                )
                continue
            params.append(value_link.get("db_value"))
            where_clauses.append(
                {
                    "table": table,
                    "column": column,
                    "operator": "=",
                    "param_index": len(params) - 1,
                    "source": "value_linking",
                    "value_mention": value_link.get("mention"),
                }
            )
        return where_clauses, params

    def _has_aggregation(self, query_understanding: dict[str, Any]) -> bool:
        aggregation = query_understanding.get("aggregation")
        metrics = query_understanding.get("metrics", [])
        return bool(aggregation or metrics)

    def _build_group_by(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        from_table: str | None,
    ) -> list[dict[str, object]]:
        raw_group_by = query_understanding.get("group_by", [])
        raw_dimensions = query_understanding.get("dimensions", [])
        terms: list[str] = []
        for item in raw_group_by:
            if isinstance(item, dict) and item.get("table") and item.get("column"):
                return [{"table": item["table"], "column": item["column"], "source": "query_understanding"}]
            if isinstance(item, dict) and item.get("term"):
                terms.append(str(item["term"]))
            elif item:
                terms.append(str(item))
        terms.extend(str(item) for item in raw_dimensions if str(item).strip())

        for term in terms:
            match = self._find_column_by_term(schema_linking, from_table, term)
            if match:
                table, column = match
                return [{"table": table, "column": column, "source": "query_understanding", "term": term}]
        return []

    def _select_metric(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        from_table: str | None,
    ) -> dict[str, object]:
        metrics = query_understanding.get("metrics", [])
        metric = metrics[0] if metrics and isinstance(metrics[0], dict) else {}
        raw_aggregation = query_understanding.get("aggregation") or {}
        fallback_aggregation = raw_aggregation.get("type") if isinstance(raw_aggregation, dict) else raw_aggregation
        aggregation = str(metric.get("aggregation") or fallback_aggregation or "COUNT").upper()
        term = str(metric.get("term") or "")
        if aggregation == "COUNT":
            return {"aggregation": "COUNT", "table": None, "column": None, "alias": "count"}
        match = self._find_column_by_term(schema_linking, from_table, term) or self._first_metric_candidate(schema_linking, from_table)
        if not match:
            return {"aggregation": "COUNT", "table": None, "column": None, "alias": "count"}
        table, column = match
        return {"aggregation": aggregation, "table": table, "column": column, "alias": self._metric_alias(aggregation)}

    def _build_having(
        self,
        query_understanding: dict[str, Any],
        select_fields: list[dict[str, object]],
        params: list[object],
    ) -> tuple[list[dict[str, object]], list[object]]:
        raw_having = query_understanding.get("having", [])
        if not isinstance(raw_having, list):
            return [], params
        metric_select = next((item for item in select_fields if item.get("aggregation")), None)
        having: list[dict[str, object]] = []
        for item in raw_having:
            if not isinstance(item, dict) or "value" not in item:
                continue
            params.append(item["value"])
            having.append(
                {
                    "expression": item.get("expression") or (metric_select or {}).get("alias") or (metric_select or {}).get("expression"),
                    "operator": item.get("operator") or ">",
                    "param_index": len(params) - 1,
                    "source": "query_understanding",
                }
            )
        return having, params

    def _find_column_by_term(
        self,
        schema_linking: dict[str, Any],
        from_table: str | None,
        term: str,
    ) -> tuple[str, str] | None:
        normalized_term = term.lower().strip()
        if not normalized_term:
            return None
        for table in schema_linking.get("matched_tables", schema_linking.get("linked_tables", [])):
            table_name = str(table.get("table_name") or table.get("name") or "").strip()
            if from_table and table_name != from_table:
                continue
            for column in table.get("matched_columns", []):
                column_name = str(column.get("column_name") or column.get("name") or "").strip()
                haystack = " ".join(
                    str(value)
                    for value in [
                        column_name,
                        column.get("description", ""),
                        " ".join(str(item) for item in column.get("business_terms", [])),
                    ]
                ).lower()
                if column_name and (normalized_term in haystack or haystack in normalized_term):
                    return table_name, column_name
        return None

    def _first_metric_candidate(self, schema_linking: dict[str, Any], from_table: str | None) -> tuple[str, str] | None:
        metric_names = ["amount", "price", "total", "money", "sales", "count", "数量", "金额", "价格", "销售额"]
        for table in schema_linking.get("matched_tables", schema_linking.get("linked_tables", [])):
            table_name = str(table.get("table_name") or table.get("name") or "").strip()
            if from_table and table_name != from_table:
                continue
            for column in table.get("matched_columns", []):
                column_name = str(column.get("column_name") or column.get("name") or "").strip()
                haystack = " ".join(
                    str(value)
                    for value in [column_name, column.get("description", ""), " ".join(str(item) for item in column.get("business_terms", []))]
                ).lower()
                if column_name and any(name in haystack for name in metric_names):
                    return table_name, column_name
        return None

    def _metric_alias(self, aggregation: str) -> str:
        aliases = {"COUNT": "count", "SUM": "total", "AVG": "average", "MIN": "minimum", "MAX": "maximum"}
        return aliases.get(aggregation.upper(), aggregation.lower())
