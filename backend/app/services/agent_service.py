from app.schemas.query import NLQueryRequest, NLQueryResponse


class AgentService:
    def generate_sql(self, payload: NLQueryRequest) -> NLQueryResponse:
        normalized_question = payload.question.strip().lower()
        table_name = "orders"

        if any(
            keyword in normalized_question for keyword in ["user", "customer", "客户"]
        ):
            table_name = "customers"
        elif any(
            keyword in normalized_question
            for keyword in ["sales", "revenue", "收入", "销售"]
        ):
            table_name = "sales"

        sql = (
            f"SELECT *\nFROM {table_name}\n"
            "WHERE created_at >= CURRENT_DATE - INTERVAL '30 days'\n"
            "LIMIT 100;"
        )
        explanation = (
            "当前是初始化阶段，接口返回的是示例 SQL 模板。"
            "后续可以在这里接入真实的 LangGraph、Schema RAG 和执行链路。"
        )
        return NLQueryResponse(sql=sql, explanation=explanation, status="mock")
