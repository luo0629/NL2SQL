class SchemaRetriever:
    def search(self, question: str) -> list[str]:
        return [f"schema-hit:{question}"]
