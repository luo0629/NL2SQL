from app.rag.schema_linker import LinkedTable, SchemaLinker, SchemaLinkingResult
from app.rag.schema_models import SchemaCatalog, SchemaRelation


class SchemaRetriever:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog
        self.linker = SchemaLinker(catalog)
        self._table_lookup = {table.name: table for table in catalog.tables}

    def link(self, question: str) -> SchemaLinkingResult:
        return self.linker.link(question)

    def search(self, question: str) -> list[str]:
        return self.render_linking_result(self.link(question))

    def render_linking_result(self, linking_result: SchemaLinkingResult) -> list[str]:
        context = [self._render_table(linked_table) for linked_table in linking_result.matched_tables]
        if linking_result.matched_relations:
            context.append(self._render_relations(linking_result.matched_relations))
        return context

    def _render_table(self, linked_table: LinkedTable) -> str:
        table = self._table_lookup[linked_table.table_name]
        lines = [f"table {table.name}"]
        if table.description:
            lines.append(f"description: {table.description}")
        if linked_table.matched_terms:
            lines.append(f"matched_terms: {', '.join(linked_table.matched_terms[:6])}")
        lines.append(f"rationale: {linked_table.rationale}")

        ordered_column_names = [column.column_name for column in linked_table.matched_columns]
        remaining_column_names = [
            column.name for column in table.columns if column.name not in ordered_column_names
        ]
        visible_column_names = ordered_column_names + remaining_column_names

        column_lookup = {column.name: column for column in table.columns}
        matched_column_lookup = {column.column_name: column for column in linked_table.matched_columns}
        for column_name in visible_column_names[:8]:
            column = column_lookup[column_name]
            matched_column = matched_column_lookup.get(column_name)
            primary_key_mark = " [PK]" if column.is_primary_key else ""
            nullable_mark = " nullable" if column.nullable else " required"
            desc_mark = f" | desc: {column.description}" if column.description else ""
            semantic_role_mark = f" | role: {column.semantic_role}" if column.semantic_role else ""
            matched_terms_mark = ""
            if matched_column and matched_column.matched_terms:
                matched_terms_mark = f" | matched: {', '.join(matched_column.matched_terms[:4])}"
            lines.append(
                f"- {column.name}: {column.data_type},{nullable_mark}{primary_key_mark}{desc_mark}{semantic_role_mark}{matched_terms_mark}"
            )
        return "\n".join(lines)

    def _render_relations(self, relations: list[SchemaRelation]) -> str:
        lines = ["relations"]
        for relation in relations:
            confidence_mark = f" | confidence: {relation.confidence}" if relation.confidence else ""
            join_hint_mark = f" | hint: {relation.join_hint}" if relation.join_hint else ""
            lines.append(
                f"- {relation.from_table}.{relation.from_column} -> {relation.to_table}.{relation.to_column}{confidence_mark}{join_hint_mark}"
            )
        return "\n".join(lines)
