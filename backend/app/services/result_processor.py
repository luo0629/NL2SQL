from app.schemas.query import NLQueryResponse


class ResultProcessor:
    def to_summary(self, response: NLQueryResponse) -> dict[str, str]:
        # 将响应模型转成更轻量的摘要结构，便于日志或前端二次展示。
        return {
            "sql": response.sql,
            "summary": response.explanation,
            "status": response.status,
        }
