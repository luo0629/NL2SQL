from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.rag.schema_models import SchemaCatalog, SchemaRelation, SchemaTable


class LinkedColumn(BaseModel):
    column_name: str
    score: int
    matched_terms: list[str] = Field(default_factory=list)
    semantic_role: str | None = None


class LinkedTable(BaseModel):
    table_name: str
    score: int
    matched_terms: list[str] = Field(default_factory=list)
    matched_columns: list[LinkedColumn] = Field(default_factory=list)
    rationale: str


class SchemaLinkingResult(BaseModel):
    question: str
    matched_tables: list[LinkedTable] = Field(default_factory=list)
    matched_relations: list[SchemaRelation] = Field(default_factory=list)
    unresolved_terms: list[str] = Field(default_factory=list)
    linking_summary: str


class SchemaLinker:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self.catalog = catalog
        self._table_lookup = {table.name: table for table in catalog.tables}

    def link(self, question: str, query_understanding: dict[str, object] | None = None) -> SchemaLinkingResult:
        normalized_question = question.strip().lower()
        if query_understanding:
            target_mentions: list[str] = []
            raw_targets = query_understanding.get("target_mentions", [])
            if isinstance(raw_targets, list):
                target_mentions.extend(
                    str(value).strip()
                    for value in raw_targets
                    if isinstance(value, str) and len(value.strip()) >= 2
                )
            if target_mentions:
                normalized_question = f"{normalized_question} {' '.join(target_mentions).lower()}".strip()

        if not normalized_question:
            return self._fallback_linking_result(question)

        tokens = self._tokenize(normalized_question)
        ranked_tables = self._rank_tables(normalized_question, tokens)
        primary_table_names = self._select_primary_table_names(ranked_tables)
        if not primary_table_names:
            return self._fallback_linking_result(question)

        allow_related_expansion = query_understanding is None or bool(query_understanding.get("requires_join_hint"))
        selected_table_names = self._expand_related_table_names(primary_table_names, ranked_tables, allow_related_expansion=allow_related_expansion)
        linked_tables = [
            self._build_linked_table(
                self._table_lookup[table_name],
                normalized_question,
                tokens,
                score=self._score_lookup(ranked_tables).get(table_name, 0),
            )
            for table_name in selected_table_names
        ]
        matched_relations = self._select_relations(set(selected_table_names))
        unresolved_terms = self._find_unresolved_terms(tokens, linked_tables)
        linking_summary = self._build_linking_summary(linked_tables, matched_relations, unresolved_terms)

        return SchemaLinkingResult(
            question=question,
            matched_tables=linked_tables,
            matched_relations=matched_relations,
            unresolved_terms=unresolved_terms,
            linking_summary=linking_summary,
        )

    def _tokenize(self, question: str) -> set[str]:
        # 基础分词：按非字母数字中文字符切分
        tokens = {token for token in re.split(r"[^a-z0-9_一-鿿]+", question) if token}

        # 提取中文连续子串（2-4字），用于匹配复合词如"订单详情"、"菜品口味"
        chinese_runs = re.findall(r"[一-鿿]+", question)
        for run in chinese_runs:
            if len(run) >= 2:
                # 保留完整词
                tokens.add(run)
                # 也保留2-3字的子串，提升模糊匹配能力
                for length in (2, 3):
                    for i in range(len(run) - length + 1):
                        tokens.add(run[i:i + length])

        return tokens

    def _score_lookup(self, ranked_tables: list[tuple[SchemaTable, int]]) -> dict[str, int]:
        return {table.name: score for table, score in ranked_tables}

    def _rank_tables(
        self,
        normalized_question: str,
        tokens: set[str],
    ) -> list[tuple[SchemaTable, int]]:
        ranked_tables = [
            (table, self._score_table(table, normalized_question, tokens))
            for table in self.catalog.tables
        ]
        return sorted(ranked_tables, key=lambda item: (-item[1], item[0].name))

    def _select_primary_table_names(
        self,
        ranked_tables: list[tuple[SchemaTable, int]],
    ) -> list[str]:
        return [table.name for table, score in ranked_tables if score > 0][:3]

    def _expand_related_table_names(
        self,
        primary_table_names: list[str],
        ranked_tables: list[tuple[SchemaTable, int]],
        *,
        allow_related_expansion: bool = True,
    ) -> list[str]:
        selected_table_names = list(primary_table_names)
        if not allow_related_expansion:
            return sorted(selected_table_names)

        selected_name_set = set(selected_table_names)
        score_lookup = self._score_lookup(ranked_tables)

        relation_candidates: list[tuple[int, str]] = []
        for relation in self.catalog.relations:
            if relation.from_table in selected_name_set and relation.to_table not in selected_name_set:
                relation_candidates.append((score_lookup.get(relation.to_table, 0), relation.to_table))
            elif relation.to_table in selected_name_set and relation.from_table not in selected_name_set:
                relation_candidates.append((score_lookup.get(relation.from_table, 0), relation.from_table))

        for _score, table_name in sorted(relation_candidates, key=lambda item: (-item[0], item[1])):
            if table_name in selected_name_set:
                continue
            selected_table_names.append(table_name)
            selected_name_set.add(table_name)
            if len(selected_table_names) >= 4:
                break

        return sorted(selected_table_names)

    def _score_table(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> int:
        score = 0
        searchable_terms = {term.lower().strip() for term in table.searchable_terms if term.strip()}
        description = (table.description or "").lower()
        alias_terms = {alias.lower().strip() for alias in table.aliases if alias.strip()}
        business_terms = {term.lower().strip() for term in table.business_terms if term.strip()}

        if table.name.lower() in normalized_question:
            score += 10
        if description and description in normalized_question:
            score += 4

        for alias in alias_terms:
            if alias and alias in normalized_question:
                score += 5

        for business_term in business_terms:
            if business_term and business_term in normalized_question:
                score += 5

        for token in tokens:
            if len(token) < 2:
                continue
            if token == table.name.lower():
                score += 6
            if token in alias_terms:
                score += 4
            if token in business_terms:
                score += 4
            if token in searchable_terms:
                score += 3
            if description and token == description:
                score += 2

        for column in table.columns:
            score += self._score_column(column.name, column.description, column.business_terms, normalized_question, tokens)

        return score

    def _score_column(
        self,
        column_name: str,
        column_description: str | None,
        business_terms: list[str],
        normalized_question: str,
        tokens: set[str],
    ) -> int:
        score = 0
        lowered_column_name = column_name.lower()
        lowered_column_description = (column_description or "").lower()
        business_term_set = {term.lower().strip() for term in business_terms if term.strip()}

        if lowered_column_name in normalized_question:
            score += 4
        if lowered_column_description and lowered_column_description in normalized_question:
            score += 3

        for business_term in business_term_set:
            if business_term and business_term in normalized_question:
                score += 4

        for token in tokens:
            if len(token) < 2:
                continue
            if token == lowered_column_name:
                score += 3
            if lowered_column_description and token == lowered_column_description:
                score += 2
            if token in business_term_set:
                score += 3

        return score

    def _match_table_terms(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> list[str]:
        candidates = [table.name, table.description or "", *table.aliases, *table.business_terms]
        matched_terms: set[str] = set()
        for candidate in candidates:
            lowered_candidate = candidate.lower().strip()
            if not lowered_candidate:
                continue
            if lowered_candidate in normalized_question:
                matched_terms.add(candidate)
                continue
            for token in tokens:
                if token == lowered_candidate or token in lowered_candidate:
                    matched_terms.add(candidate)
                    break
        return sorted(matched_terms)

    def _build_linked_column(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
    ) -> list[LinkedColumn]:
        linked_columns: list[LinkedColumn] = []
        for column in table.columns:
            score = self._score_column(
                column.name,
                column.description,
                column.business_terms,
                normalized_question,
                tokens,
            )
            if score <= 0:
                continue

            matched_terms: set[str] = set()
            candidates = [column.name, column.description or "", *column.business_terms]
            for candidate in candidates:
                lowered_candidate = candidate.lower().strip()
                if not lowered_candidate:
                    continue
                if lowered_candidate in normalized_question:
                    matched_terms.add(candidate)
                    continue
                for token in tokens:
                    if token == lowered_candidate or token in lowered_candidate:
                        matched_terms.add(candidate)
                        break

            linked_columns.append(
                LinkedColumn(
                    column_name=column.name,
                    score=score,
                    matched_terms=sorted(matched_terms),
                    semantic_role=column.semantic_role,
                )
            )

        return sorted(linked_columns, key=lambda item: (-item.score, item.column_name))

    def _build_linked_table(
        self,
        table: SchemaTable,
        normalized_question: str,
        tokens: set[str],
        *,
        score: int,
    ) -> LinkedTable:
        matched_terms = self._match_table_terms(table, normalized_question, tokens)
        linked_columns = self._build_linked_column(table, normalized_question, tokens)
        rationale_parts: list[str] = []
        if matched_terms:
            rationale_parts.append(f"命中术语: {', '.join(matched_terms[:4])}")
        if linked_columns:
            top_columns = ", ".join(column.column_name for column in linked_columns[:3])
            rationale_parts.append(f"关键字段: {top_columns}")
        if not rationale_parts:
            rationale_parts.append("通过基础 schema 与关系补全纳入候选")

        return LinkedTable(
            table_name=table.name,
            score=score,
            matched_terms=matched_terms,
            matched_columns=linked_columns,
            rationale="；".join(rationale_parts),
        )

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

    def _find_unresolved_terms(
        self,
        tokens: set[str],
        linked_tables: list[LinkedTable],
    ) -> list[str]:
        matched_tokens: set[str] = set()
        for linked_table in linked_tables:
            for term in linked_table.matched_terms:
                matched_tokens.update(self._tokenize(term.lower()))
            for linked_column in linked_table.matched_columns:
                for term in linked_column.matched_terms:
                    matched_tokens.update(self._tokenize(term.lower()))
        return sorted(token for token in tokens if token not in matched_tokens)

    def _build_linking_summary(
        self,
        linked_tables: list[LinkedTable],
        matched_relations: list[SchemaRelation],
        unresolved_terms: list[str],
    ) -> str:
        if not linked_tables:
            return "未命中明确的 schema 候选，已退回默认上下文。"

        table_names = ", ".join(table.table_name for table in linked_tables)
        summary = f"命中表: {table_names}。"
        if matched_relations:
            relation_summary = ", ".join(
                f"{relation.from_table}.{relation.from_column}->{relation.to_table}.{relation.to_column}"
                for relation in matched_relations[:3]
            )
            summary = f"{summary} 候选关系: {relation_summary}。"
        if unresolved_terms:
            summary = f"{summary} 未完全解析术语: {', '.join(unresolved_terms[:4])}。"
        return summary

    def _fallback_linking_result(self, question: str) -> SchemaLinkingResult:
        selected_tables = sorted(self.catalog.tables[:3], key=lambda table: table.name)
        linked_tables = [
            LinkedTable(
                table_name=table.name,
                score=0,
                matched_terms=[],
                matched_columns=[],
                rationale="默认回退上下文",
            )
            for table in selected_tables
        ]
        selected_table_names = {table.table_name for table in linked_tables}
        relations = self._select_relations(selected_table_names)
        return SchemaLinkingResult(
            question=question,
            matched_tables=linked_tables,
            matched_relations=relations,
            unresolved_terms=[],
            linking_summary="未命中明确术语，已返回稳定默认 schema 上下文。",
        )
