from __future__ import annotations

from typing import Any


class SQLGenerationResult:
    def __init__(self, sql: str, params: list[object]) -> None:
        self.sql = sql
        self.params = params


class SQLGenerator:
    def generate(self, sql_plan: dict[str, Any]) -> SQLGenerationResult | None:
        from_table = sql_plan.get("from_table")
        if not from_table:
            return None

        select_clause = self._render_select(sql_plan)
        from_clause = f"FROM {from_table}"
        join_clause = self._render_joins(sql_plan.get("joins", []))
        where_clause = self._render_where(sql_plan.get("where", []))
        group_by_clause = self._render_group_by(sql_plan.get("group_by", []))
        having_clause = self._render_having(sql_plan.get("having", []))
        order_clause = self._render_order_by(sql_plan.get("order_by", []))
        limit_clause = self._render_limit(sql_plan.get("limit"))

        parts = [select_clause, from_clause, join_clause, where_clause, group_by_clause, having_clause, order_clause, limit_clause]
        sql = "\n".join(part for part in parts if part).strip()
        return SQLGenerationResult(sql=f"{sql};", params=list(sql_plan.get("params", [])))

    def _render_select(self, sql_plan: dict[str, Any]) -> str:
        distinct = "DISTINCT " if sql_plan.get("distinct") else ""
        select_items = []
        for item in sql_plan.get("select", []):
            expression = item.get("expression")
            alias = item.get("alias")
            if expression:
                rendered = str(expression)
                if alias:
                    rendered = f"{rendered} AS {alias}"
                select_items.append(rendered)
                continue
            table = item.get("table")
            column = item.get("column")
            if not column:
                continue
            rendered = str(column) if column == "*" or not table else f"{table}.{column}"
            if alias:
                rendered = f"{rendered} AS {alias}"
            select_items.append(rendered)
        if not select_items:
            select_items.append("*")
        return f"SELECT {distinct}{', '.join(select_items)}"

    def _render_joins(self, joins: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for join in joins:
            left_table = join.get("left_table")
            left_column = join.get("left_column")
            right_table = join.get("right_table")
            right_column = join.get("right_column")
            if not all([left_table, left_column, right_table, right_column]):
                continue
            lines.append(
                f"JOIN {right_table} ON {left_table}.{left_column} = {right_table}.{right_column}"
            )
        return "\n".join(lines)

    def _render_where(self, where_clauses: list[dict[str, Any]]) -> str:
        predicates: list[str] = []
        for clause in where_clauses:
            table = clause.get("table")
            column = clause.get("column")
            operator = clause.get("operator", "=")
            if not table or not column:
                continue
            predicates.append(f"{table}.{column} {operator} :p{clause.get('param_index', len(predicates))}")
        if not predicates:
            return ""
        return f"WHERE {' AND '.join(predicates)}"

    def _render_group_by(self, group_by: list[dict[str, Any]]) -> str:
        items: list[str] = []
        for item in group_by:
            expression = item.get("expression")
            table = item.get("table")
            column = item.get("column")
            if expression:
                items.append(str(expression))
            elif table and column:
                items.append(f"{table}.{column}")
        if not items:
            return ""
        return f"GROUP BY {', '.join(items)}"

    def _render_having(self, having: list[dict[str, Any]]) -> str:
        predicates: list[str] = []
        for clause in having:
            expression = clause.get("expression")
            operator = clause.get("operator", "=")
            if not expression:
                continue
            predicates.append(f"{expression} {operator} :p{clause.get('param_index', len(predicates))}")
        if not predicates:
            return ""
        return f"HAVING {' AND '.join(predicates)}"

    def _render_order_by(self, order_by: list[dict[str, Any]]) -> str:
        items: list[str] = []
        for item in order_by:
            expression = item.get("expression")
            table = item.get("table")
            column = item.get("column")
            direction = str(item.get("direction", "ASC")).upper()
            if direction not in {"ASC", "DESC"}:
                direction = "ASC"
            if expression:
                items.append(f"{expression} {direction}")
            elif table and column:
                items.append(f"{table}.{column} {direction}")
        if not items:
            return ""
        return f"ORDER BY {', '.join(items)}"

    def _render_limit(self, limit: object) -> str:
        if not isinstance(limit, int) or limit <= 0:
            return ""
        return f"LIMIT {limit}"
