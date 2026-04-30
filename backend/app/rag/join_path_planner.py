from __future__ import annotations

from collections import deque

from pydantic import BaseModel, Field

from app.rag.schema_linker import SchemaLinkingResult
from app.rag.schema_models import SchemaCatalog, SchemaRelation


class JoinEdge(BaseModel):
    left_table: str
    left_column: str
    right_table: str
    right_column: str
    relation_type: str | None = None
    join_hint: str | None = None
    confidence: str | None = None


class JoinPathPlan(BaseModel):
    primary_table: str | None = None
    tables_in_plan: list[str] = Field(default_factory=list)
    edges: list[JoinEdge] = Field(default_factory=list)
    plan_confidence: str = "none"
    unresolved_tables: list[str] = Field(default_factory=list)
    planning_summary: str = ""


class JoinPathPlanner:
    def plan(
        self,
        linking_result: SchemaLinkingResult,
        catalog: SchemaCatalog,
    ) -> JoinPathPlan:
        linked_table_names = [table.table_name for table in linking_result.matched_tables]
        if not linked_table_names:
            return JoinPathPlan(
                plan_confidence="none",
                planning_summary="没有可用于连表规划的候选表。",
            )

        primary_table = self._select_primary_table(linking_result)
        selected_relations = self._prioritize_relations(linking_result.matched_relations)
        graph = self._build_relation_graph(selected_relations)
        edges_by_key = self._build_edge_lookup(selected_relations)

        resolved_tables = {primary_table}
        plan_edges: list[JoinEdge] = []
        unresolved_tables: list[str] = []

        for table_name in linked_table_names:
            if table_name == primary_table:
                continue
            path = self._find_path(primary_table, table_name, graph)
            if not path:
                unresolved_tables.append(table_name)
                continue

            for left_table, right_table in path:
                edge_key = (left_table, right_table)
                reverse_key = (right_table, left_table)
                if edge_key in edges_by_key:
                    relation = edges_by_key[edge_key]
                    join_edge = self._relation_to_join_edge(relation)
                elif reverse_key in edges_by_key:
                    relation = edges_by_key[reverse_key]
                    join_edge = self._reverse_relation_to_join_edge(relation)
                else:
                    continue

                if join_edge not in plan_edges:
                    plan_edges.append(join_edge)
                resolved_tables.add(left_table)
                resolved_tables.add(right_table)

        tables_in_plan = [primary_table] + [
            table_name for table_name in linked_table_names if table_name != primary_table and table_name in resolved_tables
        ]
        plan_confidence = self._determine_plan_confidence(plan_edges, unresolved_tables)
        planning_summary = self._build_planning_summary(
            primary_table=primary_table,
            tables_in_plan=tables_in_plan,
            plan_edges=plan_edges,
            unresolved_tables=unresolved_tables,
            plan_confidence=plan_confidence,
        )

        return JoinPathPlan(
            primary_table=primary_table,
            tables_in_plan=tables_in_plan,
            edges=plan_edges,
            plan_confidence=plan_confidence,
            unresolved_tables=unresolved_tables,
            planning_summary=planning_summary,
        )

    def _select_primary_table(self, linking_result: SchemaLinkingResult) -> str:
        ranked_tables = sorted(
            linking_result.matched_tables,
            key=lambda table: (-table.score, table.table_name),
        )
        return ranked_tables[0].table_name

    def _prioritize_relations(self, relations: list[SchemaRelation]) -> list[SchemaRelation]:
        confidence_rank = {"high": 0, "medium": 1, "low": 2, None: 3}
        return sorted(
            relations,
            key=lambda relation: (
                confidence_rank.get(relation.confidence, 3),
                relation.from_table,
                relation.to_table,
                relation.from_column,
                relation.to_column,
            ),
        )

    def _build_relation_graph(
        self,
        relations: list[SchemaRelation],
    ) -> dict[str, list[str]]:
        graph: dict[str, list[str]] = {}
        for relation in relations:
            graph.setdefault(relation.from_table, []).append(relation.to_table)
            graph.setdefault(relation.to_table, []).append(relation.from_table)

        for table_name, neighbors in graph.items():
            graph[table_name] = sorted(set(neighbors))
        return graph

    def _build_edge_lookup(
        self,
        relations: list[SchemaRelation],
    ) -> dict[tuple[str, str], SchemaRelation]:
        edge_lookup: dict[tuple[str, str], SchemaRelation] = {}
        for relation in relations:
            edge_lookup[(relation.from_table, relation.to_table)] = relation
        return edge_lookup

    def _find_path(
        self,
        start_table: str,
        target_table: str,
        graph: dict[str, list[str]],
    ) -> list[tuple[str, str]]:
        if start_table == target_table:
            return []
        if start_table not in graph or target_table not in graph:
            return []

        queue: deque[tuple[str, list[tuple[str, str]]]] = deque([(start_table, [])])
        visited = {start_table}

        while queue:
            current_table, path = queue.popleft()
            for neighbor in graph.get(current_table, []):
                if neighbor in visited:
                    continue
                next_path = path + [(current_table, neighbor)]
                if neighbor == target_table:
                    return next_path
                visited.add(neighbor)
                queue.append((neighbor, next_path))

        return []

    def _relation_to_join_edge(self, relation: SchemaRelation) -> JoinEdge:
        return JoinEdge(
            left_table=relation.from_table,
            left_column=relation.from_column,
            right_table=relation.to_table,
            right_column=relation.to_column,
            relation_type=relation.relation_type,
            join_hint=relation.join_hint,
            confidence=relation.confidence,
        )

    def _reverse_relation_to_join_edge(self, relation: SchemaRelation) -> JoinEdge:
        return JoinEdge(
            left_table=relation.to_table,
            left_column=relation.to_column,
            right_table=relation.from_table,
            right_column=relation.from_column,
            relation_type=relation.relation_type,
            join_hint=relation.join_hint,
            confidence=relation.confidence,
        )

    def _determine_plan_confidence(
        self,
        plan_edges: list[JoinEdge],
        unresolved_tables: list[str],
    ) -> str:
        if not plan_edges:
            return "none"
        if unresolved_tables:
            return "low"
        if any(edge.confidence == "medium" for edge in plan_edges):
            return "medium"
        if all(edge.confidence == "high" for edge in plan_edges):
            return "high"
        return "low"

    def _build_planning_summary(
        self,
        *,
        primary_table: str,
        tables_in_plan: list[str],
        plan_edges: list[JoinEdge],
        unresolved_tables: list[str],
        plan_confidence: str,
    ) -> str:
        summary = f"主表: {primary_table}。"
        if tables_in_plan:
            summary = f"{summary} 连表覆盖: {', '.join(tables_in_plan)}。"
        if plan_edges:
            edge_summary = ", ".join(
                f"{edge.left_table}.{edge.left_column}->{edge.right_table}.{edge.right_column}"
                for edge in plan_edges
            )
            summary = f"{summary} 路径: {edge_summary}。"
        if unresolved_tables:
            summary = f"{summary} 未解决表: {', '.join(unresolved_tables)}。"
        return f"{summary} 规划置信度: {plan_confidence}。"
