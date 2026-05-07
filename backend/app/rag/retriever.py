import re

from app.rag.schema_models import SchemaCatalog, SchemaRelation, SchemaTable


class SchemaRetriever:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog

    def search(self, question: str) -> list[str]:
        normalized_question = question.strip().lower()
        if not normalized_question:
            return self._fallback_context()

        tokens = self._tokenize(normalized_question)
        ranked_tables = sorted(
            self.catalog.tables,
            key=lambda table: self._score_table(table, normalized_question, tokens),
            reverse=True,
        )

        selected_tables = [
            table for table in ranked_tables if self._score_table(table, normalized_question, tokens) > 0
        ][:4]

        if not selected_tables:
            return self._fallback_context()

        selected_table_names = {table.name for table in selected_tables}
        related_relations = [
            relation
            for relation in self.catalog.relations
            if relation.from_table in selected_table_names or relation.to_table in selected_table_names
        ]

        context = [self._render_table(table) for table in selected_tables]
        if related_relations:
            context.append(self._render_relations(related_relations))
        return context

    def _tokenize(self, question: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9_一-鿿]+", question) if token}

    def _score_table(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> int:
        score = 0
        searchable_blob = " ".join(term.lower() for term in table.searchable_terms)
        description = (table.description or "").lower()
        column_description_blob = " ".join(
            (column.description or "").lower()
            for column in table.columns
            if column.description
        )

        if table.name.lower() in normalized_question:
            score += 8

        for token in tokens:
            if token == table.name.lower():
                score += 6
            if token in searchable_blob:
                score += 3
            if token in description:
                score += 2
            if token in column_description_blob:
                score += 2

        return score

    def _render_table(self, table: SchemaTable) -> str:
        lines = [f"table {table.name}"]
        if table.description:
            lines.append(f"description: {table.description}")
        for column in table.columns:
            primary_key_mark = " [PK]" if column.is_primary_key else ""
            nullable_mark = " nullable" if column.nullable else " required"
            desc_mark = ""
            if column.description:
                desc_mark = f" | desc: {column.description}"
            lines.append(
                f"- {column.name}: {column.data_type},{nullable_mark}{primary_key_mark}{desc_mark}"
            )
        return "\n".join(lines)

    def _render_relations(self, relations: list[SchemaRelation]) -> str:
        lines = ["relations"]
        for relation in relations:
            lines.append(
                f"- {relation.from_table}.{relation.from_column} -> {relation.to_table}.{relation.to_column}"
            )
        return "\n".join(lines)

    def _fallback_context(self) -> list[str]:
        top_tables = self.catalog.tables[:3]
        context = [self._render_table(table) for table in top_tables]
        if self.catalog.relations:
            context.append(self._render_relations(self.catalog.relations[:3]))
        return context
