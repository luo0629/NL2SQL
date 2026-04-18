from app.schemas.query import NLQueryResponse


class ResultProcessor:
    def to_summary(self, response: NLQueryResponse) -> dict[str, str]:
        return {
            "sql": response.sql,
            "summary": response.explanation,
            "status": response.status,
        }
