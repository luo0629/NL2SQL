import re
from typing import ClassVar

from app.utils.exceptions import DangerousSQLError


class SQLValidator:
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
    FORBIDDEN_PATTERNS: ClassVar[tuple[str, ...]] = ("--", "/*", "*/")
    ALLOWED_PREFIXES: ClassVar[tuple[str, ...]] = ("select", "with")

    def validate_read_only(self, sql: str) -> None:
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

        if ";" in normalized_sql[:-1] or normalized_sql.count(";") > 1:
            raise DangerousSQLError("Only a single SQL statement is allowed.")

        for keyword in self.FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{re.escape(keyword)}\b", lowered_sql):
                raise DangerousSQLError(f"Detected forbidden SQL keyword: {keyword}")
