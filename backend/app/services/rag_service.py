class RagService:
    def retrieve_relevant_schema(self, question: str) -> list[str]:
        return [f"mock-schema-context:{question}"]
