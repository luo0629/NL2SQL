class RagService:
    def retrieve_relevant_schema(self, question: str) -> list[str]:
        normalized_question = question.strip().lower()

        # TODO(learning): 这里先返回内置 schema 摘要，后续再演进成真实数据库 schema 同步与向量检索。
        if any(
            keyword in normalized_question
            for keyword in ["sales", "revenue", "收入", "销售"]
        ):
            return [
                "table sales(id, customer_id, amount, created_at)",
                "table customers(id, name, segment, created_at)",
            ]

        if any(
            keyword in normalized_question
            for keyword in ["customer", "user", "客户", "用户"]
        ):
            return [
                "table customers(id, name, segment, created_at)",
                "table orders(id, customer_id, total_amount, created_at)",
            ]

        return [
            "table orders(id, customer_id, total_amount, status, created_at)",
            "table customers(id, name, segment, created_at)",
        ]
