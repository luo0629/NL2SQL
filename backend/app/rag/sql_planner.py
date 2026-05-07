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


class SQLPlanner:
    def build(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        value_links: list[dict[str, Any]],
        join_path_plan: dict[str, Any],
        semantic_query: dict[str, Any] | None = None,
    ) -> SQLPlan:
        from_table = self._select_from_table(schema_linking, join_path_plan)

        if semantic_query:
            where_clauses, params = self._build_where_clauses_from_semantic_query(semantic_query, value_links)
            select_fields = self._build_select_fields_from_semantic_query(semantic_query, schema_linking, from_table)
            order_by = self._build_order_by_from_semantic_query(semantic_query, schema_linking, from_table)
            group_by = self._build_group_by_from_semantic_query(semantic_query)
            limit = semantic_query.get("limit")
            select_source = "semantic_query"
            where_source = "semantic_query" if where_clauses else None
            order_source = "semantic_query" if order_by else None
            limit_source = "semantic_query" if limit is not None else None
        else:
            where_clauses, params = self._build_where_clauses(value_links)
            select_fields = self._build_select_fields(schema_linking, from_table)
            order_by = self._build_order_by(query_understanding, schema_linking, from_table)
            group_by = list(query_understanding.get("group_by", []))
            limit = query_understanding.get("limit")
            select_source = "schema_linking"
            where_source = "value_linking" if where_clauses else None
            order_source = "query_understanding" if order_by else None
            limit_source = "query_understanding" if query_understanding.get("limit") is not None else None

        return SQLPlan(
            select=select_fields,
            from_table=from_table,
            joins=list(join_path_plan.get("edges", [])),
            where=where_clauses,
            group_by=group_by,
            having=[],
            order_by=order_by,
            limit=limit,
            distinct=bool(join_path_plan.get("requires_distinct", False)),
            params=params,
            provenance={
                "select": select_source,
                "from_table": "schema_linking" if from_table else None,
                "joins": "join_path_planning" if join_path_plan.get("edges") else None,
                "where": where_source,
                "group_by": "semantic_query" if group_by and semantic_query else None,
                "order_by": order_source,
                "limit": limit_source,
                "distinct": "join_path_planning" if join_path_plan.get("requires_distinct") else None,
                "semantic_query_confidence": semantic_query.get("confidence") if semantic_query else None,
            },
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

    def _build_select_fields(self, schema_linking: dict[str, Any], from_table: str | None) -> list[dict[str, object]]:
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

    def _build_select_fields_from_semantic_query(
        self,
        semantic_query: dict[str, Any],
        schema_linking: dict[str, Any],
        from_table: str | None,
    ) -> list[dict[str, object]]:
        fields: list[dict[str, object]] = []
        for item in list(semantic_query.get("dimensions", []))[:3]:
            if not isinstance(item, dict):
                continue
            table = item.get("table") or from_table
            column = item.get("column")
            if table and column:
                fields.append({"table": table, "column": column, "source": "semantic_query", "role": item.get("role")})
        for item in list(semantic_query.get("metrics", []))[:3]:
            if not isinstance(item, dict):
                continue
            if item.get("expression"):
                fields.append({"expression": item.get("expression"), "alias": item.get("alias"), "source": "semantic_query", "role": "metric"})
                continue
            table = item.get("table") or from_table
            column = item.get("column")
            if table and column:
                fields.append({"table": table, "column": column, "source": "semantic_query", "role": "metric"})
        if fields:
            return fields
        return self._build_select_fields(schema_linking, from_table)

    def _build_group_by_from_semantic_query(self, semantic_query: dict[str, Any]) -> list[dict[str, object]]:
        if not semantic_query.get("metrics"):
            return []
        group_by: list[dict[str, object]] = []
        for item in semantic_query.get("dimensions", []):
            if not isinstance(item, dict):
                continue
            table = item.get("table")
            column = item.get("column")
            if table and column:
                group_by.append({"table": table, "column": column, "source": "semantic_query"})
        return group_by[:3]

    def _build_where_clauses_from_semantic_query(
        self,
        semantic_query: dict[str, Any],
        value_links: list[dict[str, Any]],
    ) -> tuple[list[dict[str, object]], list[object]]:
        where_clauses: list[dict[str, object]] = []
        params: list[object] = []
        allowed_operators = {"=", ">", "<", ">=", "<=", "LIKE"}
        for item in semantic_query.get("filters", []):
            if not isinstance(item, dict):
                continue
            table = str(item.get("table") or "").strip()
            column = str(item.get("column") or "").strip()
            source_param_index = item.get("param_index")
            if not table or not column or not isinstance(source_param_index, int):
                continue
            if source_param_index < 0 or source_param_index >= len(value_links):
                continue
            value_link = value_links[source_param_index]
            if value_link.get("match_type") == "unresolved":
                continue
            operator = str(item.get("operator") or "=").upper()
            if operator not in allowed_operators:
                operator = "="
            params.append(value_link.get("db_value"))
            where_clauses.append(
                {
                    "table": table,
                    "column": column,
                    "operator": operator,
                    "param_index": len(params) - 1,
                    "source": "value_linking",
                    "semantic_source": "semantic_query",
                    "value_mention": item.get("value_mention") or value_link.get("mention"),
                }
            )
        return where_clauses, params

    def _build_order_by_from_semantic_query(
        self,
        semantic_query: dict[str, Any],
        schema_linking: dict[str, Any],
        from_table: str | None,
    ) -> list[dict[str, object]]:
        order_by: list[dict[str, object]] = []
        for item in semantic_query.get("order_by", []):
            if not isinstance(item, dict):
                continue
            direction = str(item.get("direction") or "ASC").upper()
            normalized: dict[str, object] = {"direction": "DESC" if direction == "DESC" else "ASC", "source": "semantic_query"}
            if item.get("alias"):
                normalized["alias"] = item.get("alias")
            elif item.get("expression"):
                normalized["expression"] = item.get("expression")
            elif item.get("column"):
                normalized["table"] = item.get("table") or from_table
                normalized["column"] = item.get("column")
            if any(key in normalized for key in ("alias", "expression", "column")):
                order_by.append(normalized)
        if order_by:
            return order_by
        return self._build_order_by({"order_by": []}, schema_linking, from_table)

    def _build_order_by(
        self,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        from_table: str | None,
    ) -> list[dict[str, object]]:
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
        for item in query_understanding.get("order_by", []):
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
