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
