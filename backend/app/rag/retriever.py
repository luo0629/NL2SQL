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
        ranked_tables = self._rank_tables(normalized_question, tokens)
        primary_tables = self._select_primary_tables(ranked_tables)
        if not primary_tables:
            return self._fallback_context()

        selected_tables = self._expand_related_tables(primary_tables, ranked_tables)
        selected_table_names = {table.name for table in selected_tables}
        related_relations = self._select_relations(selected_table_names)

        context = [self._render_table(table, normalized_question, tokens) for table in selected_tables]
        if related_relations:
            context.append(self._render_relations(related_relations))
        return context

    def _tokenize(self, question: str) -> set[str]:
        return {token for token in re.split(r"[^a-z0-9_一-鿿]+", question) if token}

    def _rank_tables(
        self,
        normalized_question: str,
        tokens: set[str],
    ) -> list[tuple[SchemaTable, int]]:
        ranked_tables = [
            (table, self._score_table(table, normalized_question, tokens))
            for table in self.catalog.tables
        ]
        return sorted(
            ranked_tables,
            key=lambda item: (-item[1], item[0].name),
        )

    def _select_primary_tables(
        self,
        ranked_tables: list[tuple[SchemaTable, int]],
    ) -> list[SchemaTable]:
        selected_tables = [table for table, score in ranked_tables if score > 0][:3]
        return selected_tables

    def _expand_related_tables(
        self,
        primary_tables: list[SchemaTable],
        ranked_tables: list[tuple[SchemaTable, int]],
    ) -> list[SchemaTable]:
        selected_tables = list(primary_tables)
        selected_table_names = {table.name for table in selected_tables}
        ranked_by_name = {table.name: score for table, score in ranked_tables}
        ranked_table_lookup = {table.name: table for table, _score in ranked_tables}

        relation_candidates: list[tuple[int, str, SchemaTable]] = []
        for relation in self.catalog.relations:
            if relation.from_table in selected_table_names and relation.to_table not in selected_table_names:
                related_table = ranked_table_lookup.get(relation.to_table)
                if related_table is not None:
                    relation_candidates.append(
                        (
                            ranked_by_name.get(relation.to_table, 0),
                            relation.to_table,
                            related_table,
                        )
                    )
            elif relation.to_table in selected_table_names and relation.from_table not in selected_table_names:
                related_table = ranked_table_lookup.get(relation.from_table)
                if related_table is not None:
                    relation_candidates.append(
                        (
                            ranked_by_name.get(relation.from_table, 0),
                            relation.from_table,
                            related_table,
                        )
                    )

        for _score, table_name, table in sorted(
            relation_candidates,
            key=lambda item: (-item[0], item[1]),
        ):
            if table_name in selected_table_names:
                continue
            selected_tables.append(table)
            selected_table_names.add(table_name)
            if len(selected_tables) >= 4:
                break

        return sorted(selected_tables, key=lambda table: table.name)

    def _score_table(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> int:
        score = 0
        searchable_blob = " ".join(term.lower() for term in table.searchable_terms)
        description = (table.description or "").lower()

        if table.name.lower() in normalized_question:
            score += 10

        for token in tokens:
            if token == table.name.lower():
                score += 6
            if token in searchable_blob:
                score += 3
            if token in description:
                score += 2

        for column in table.columns:
            score += self._score_column(column.name, column.description, normalized_question, tokens)

        return score

    def _score_column(
        self,
        column_name: str,
        column_description: str | None,
        normalized_question: str,
        tokens: set[str],
    ) -> int:
        score = 0
        lowered_column_name = column_name.lower()
        lowered_column_description = (column_description or "").lower()

        if lowered_column_name in normalized_question:
            score += 4

        for token in tokens:
            if token == lowered_column_name:
                score += 3
            if token in lowered_column_name:
                score += 2
            if lowered_column_description and token in lowered_column_description:
                score += 2

        return score

    def _rank_table_columns(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> list[tuple[str, int]]:
        ranked_columns = [
            (
                column.name,
                self._score_column(column.name, column.description, normalized_question, tokens),
            )
            for column in table.columns
        ]
        return sorted(ranked_columns, key=lambda item: (-item[1], item[0]))

    def _render_table(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> str:
        lines = [f"table {table.name}"]
        if table.description:
            lines.append(f"description: {table.description}")

        ranked_columns = self._rank_table_columns(table, normalized_question, tokens)
        ordered_column_names = [name for name, score in ranked_columns if score > 0]
        remaining_column_names = [
            column.name for column in table.columns if column.name not in ordered_column_names
        ]
        visible_column_names = ordered_column_names + remaining_column_names

        column_lookup = {column.name: column for column in table.columns}
        for column_name in visible_column_names[:8]:
            column = column_lookup[column_name]
            primary_key_mark = " [PK]" if column.is_primary_key else ""
            nullable_mark = " nullable" if column.nullable else " required"
            desc_mark = ""
            if column.description:
                desc_mark = f" | desc: {column.description}"
            lines.append(
                f"- {column.name}: {column.data_type},{nullable_mark}{primary_key_mark}{desc_mark}"
            )
        return "\n".join(lines)

    def _select_relations(self, selected_table_names: set[str]) -> list[SchemaRelation]:
        related_relations = [
            relation
            for relation in self.catalog.relations
            if relation.from_table in selected_table_names and relation.to_table in selected_table_names
        ]
        return sorted(
            related_relations,
            key=lambda relation: (
                relation.from_table,
                relation.to_table,
                relation.from_column,
                relation.to_column,
            ),
        )[:6]

    def _render_relations(self, relations: list[SchemaRelation]) -> str:
        lines = ["relations"]
        for relation in relations:
            lines.append(
                f"- {relation.from_table}.{relation.from_column} -> {relation.to_table}.{relation.to_column}"
            )
        return "\n".join(lines)

    def _fallback_context(self) -> list[str]:
        top_tables = sorted(self.catalog.tables[:3], key=lambda table: table.name)
        context = [self._render_table(table, "", set()) for table in top_tables]
        if self.catalog.relations:
            context.append(self._render_relations(self._select_relations({table.name for table in top_tables})))
        return context
