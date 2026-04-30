import re
from typing import ClassVar

from app.utils.exceptions import DangerousSQLError


class SQLValidator:
    # 明确禁止的写操作/DDL 关键词。
    FORBIDDEN_KEYWORDS: ClassVar[set[str]] = {
        "alter",
        "create",
        "insert",
        "delete",
        "drop",
        "grant",
        "merge",
        "replace",
        "revoke",
        "truncate",
        "update",
    }
    # 禁止注释与多语句拼接常见模式，降低注入风险。
    FORBIDDEN_PATTERNS: ClassVar[tuple[str, ...]] = ("--", "/*", "*/")
    # 仅允许只读前缀。
    ALLOWED_PREFIXES: ClassVar[tuple[str, ...]] = ("select", "with")

    def validate_read_only(self, sql: str) -> None:
        # 校验流程：非空 -> 只读前缀 -> 禁止模式 -> 单语句 -> 禁止关键词。
        normalized_sql = sql.strip()

        if not normalized_sql:
            raise DangerousSQLError("SQL cannot be empty.")

        lowered_sql = normalized_sql.lower()

        if not lowered_sql.startswith(self.ALLOWED_PREFIXES):
            raise DangerousSQLError("Only SELECT or WITH queries are allowed.")

        if any(pattern in lowered_sql for pattern in self.FORBIDDEN_PATTERNS):
            raise DangerousSQLError(
                "SQL contains forbidden comment or chaining patterns."
            )

        # Stability guard: LIMIT queries without ORDER BY are structurally unstable.
        if "limit" in lowered_sql and "order by" not in lowered_sql:
            raise DangerousSQLError(
                "LIMIT queries must include an explicit ORDER BY for stable results."
            )

        if ";" in normalized_sql[:-1] or normalized_sql.count(";") > 1:
            raise DangerousSQLError("Only a single SQL statement is allowed.")

        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered_sql):
                raise DangerousSQLError(f"Detected forbidden SQL keyword: {keyword}")


    def validate_plan_provenance(
        self,
        *,
        sql_plan: dict[str, object],
        params: list[object],
    ) -> list[dict[str, object]]:
        issues: list[dict[str, object]] = []
        provenance = sql_plan.get("provenance", {})
        if not isinstance(provenance, dict):
            issues.append(
                {
                    "level": "error",
                    "code": "MISSING_PROVENANCE",
                    "message": "SQL Plan 缺少 provenance 信息。",
                    "repairable": False,
                }
            )
            return issues

        if sql_plan.get("from_table") and provenance.get("from_table") != "schema_linking":
            issues.append(
                {
                    "level": "error",
                    "code": "FROM_TABLE_WITHOUT_SCHEMA_LINKING",
                    "message": "FROM 表缺少 Schema Linking 来源。",
                    "repairable": False,
                }
            )

        for join in sql_plan.get("joins", []):
            if not isinstance(join, dict):
                continue
            if join.get("source") != "schema_relation":
                issues.append(
                    {
                        "level": "error",
                        "code": "JOIN_WITHOUT_PLANNER_PROVENANCE",
                        "message": "JOIN 缺少 Join Path Planner 来源。",
                        "repairable": False,
                    }
                )

        where_clauses = sql_plan.get("where", [])
        if isinstance(where_clauses, list):
            for clause in where_clauses:
                if not isinstance(clause, dict):
                    continue
                if clause.get("source") != "value_linking":
                    issues.append(
                        {
                            "level": "error",
                            "code": "WHERE_WITHOUT_VALUE_LINKING",
                            "message": "WHERE 条件缺少 Value Linking 来源。",
                            "repairable": True,
                        }
                    )
                param_index = clause.get("param_index")
                if not isinstance(param_index, int) or param_index < 0 or param_index >= len(params):
                    issues.append(
                        {
                            "level": "error",
                            "code": "PARAMETER_INDEX_INVALID",
                            "message": "WHERE 条件参数索引无效。",
                            "repairable": True,
                        }
                    )

        return issues


    def validate_sql_matches_plan(
        self,
        *,
        sql: str,
        sql_plan: dict[str, object],
        params: list[object],
    ) -> list[dict[str, object]]:
        normalized_sql = sql.strip().lower()
        issues: list[dict[str, object]] = []

        from_table = sql_plan.get("from_table")
        if isinstance(from_table, str) and from_table and not re.search(rf"\bfrom\s+{re.escape(from_table.lower())}\b", normalized_sql):
            issues.append(
                {
                    "level": "error",
                    "code": "SQL_FROM_TABLE_MISMATCH",
                    "message": "最终 SQL 与 SQL Plan 的 FROM 表不一致。",
                    "repairable": True,
                }
            )

        for join in sql_plan.get("joins", []):
            if not isinstance(join, dict):
                continue
            right_table = str(join.get("right_table") or "").lower()
            left_table = str(join.get("left_table") or "").lower()
            left_column = str(join.get("left_column") or "").lower()
            right_column = str(join.get("right_column") or "").lower()
            if right_table and not re.search(rf"\bjoin\s+{re.escape(right_table)}\b", normalized_sql):
                issues.append(
                    {
                        "level": "error",
                        "code": "SQL_JOIN_MISMATCH",
                        "message": "最终 SQL 缺少 SQL Plan 里的 JOIN。",
                        "repairable": True,
                    }
                )
            if all([left_table, left_column, right_table, right_column]):
                join_pattern = rf"{re.escape(left_table)}\.{re.escape(left_column)}\s*=\s*{re.escape(right_table)}\.{re.escape(right_column)}"
                reverse_pattern = rf"{re.escape(right_table)}\.{re.escape(right_column)}\s*=\s*{re.escape(left_table)}\.{re.escape(left_column)}"
                if not re.search(join_pattern, normalized_sql) and not re.search(reverse_pattern, normalized_sql):
                    issues.append(
                        {
                            "level": "error",
                            "code": "SQL_JOIN_CONDITION_MISMATCH",
                            "message": "最终 SQL 的 JOIN 条件与 SQL Plan 不一致。",
                            "repairable": True,
                        }
                    )

        for clause in sql_plan.get("where", []):
            if not isinstance(clause, dict):
                continue
            table = str(clause.get("table") or "").lower()
            column = str(clause.get("column") or "").lower()
            operator = str(clause.get("operator") or "=")
            param_index = clause.get("param_index")
            if table and column and isinstance(param_index, int):
                where_pattern = rf"{re.escape(table)}\.{re.escape(column)}\s*{re.escape(operator.lower())}\s*:p{param_index}"
                if not re.search(where_pattern, normalized_sql):
                    issues.append(
                        {
                            "level": "error",
                            "code": "SQL_WHERE_MISMATCH",
                            "message": "最终 SQL 的 WHERE 条件与 SQL Plan 不一致。",
                            "repairable": True,
                        }
                    )

        if sql_plan.get("limit") is not None and str(sql_plan.get("limit")) not in normalized_sql:
            issues.append(
                {
                    "level": "error",
                    "code": "SQL_LIMIT_MISMATCH",
                    "message": "最终 SQL 的 LIMIT 与 SQL Plan 不一致。",
                    "repairable": True,
                }
            )

        order_by = sql_plan.get("order_by", [])
        if isinstance(order_by, list) and order_by:
            if "order by" not in normalized_sql:
                issues.append(
                    {
                        "level": "error",
                        "code": "SQL_ORDER_BY_MISSING",
                        "message": "最终 SQL 缺少 SQL Plan 指定的 ORDER BY。",
                        "repairable": True,
                    }
                )
            for item in order_by:
                if not isinstance(item, dict):
                    continue
                table = str(item.get("table") or "").lower()
                column = str(item.get("column") or "").lower()
                if table and column and f"{table}.{column}" not in normalized_sql:
                    issues.append(
                        {
                            "level": "error",
                            "code": "SQL_ORDER_BY_MISMATCH",
                            "message": "最终 SQL 的排序字段与 SQL Plan 不一致。",
                            "repairable": True,
                        }
                    )

        placeholder_count = len(set(re.findall(r":p(\d+)", normalized_sql)))
        if placeholder_count != len(params):
            issues.append(
                {
                    "level": "error",
                    "code": "SQL_PARAM_COUNT_MISMATCH",
                    "message": "最终 SQL 的占位符数量与参数数量不一致。",
                    "repairable": True,
                }
            )

        return issues
