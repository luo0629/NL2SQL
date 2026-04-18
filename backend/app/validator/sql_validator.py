from typing import ClassVar

from app.utils.exceptions import DangerousSQLError


class SQLValidator:
    FORBIDDEN_KEYWORDS: ClassVar[set[str]] = {
        "insert",
        "update",
        "delete",
        "drop",
        "alter",
        "truncate",
    }

    def validate_read_only(self, sql: str) -> None:
        lowered_sql = sql.lower()
        for keyword in self.FORBIDDEN_KEYWORDS:
            if keyword in lowered_sql:
                raise DangerousSQLError(f"Detected forbidden SQL keyword: {keyword}")
