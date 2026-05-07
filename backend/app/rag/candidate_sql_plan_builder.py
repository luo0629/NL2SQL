from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.rag.sql_planner import SQLPlanner


class CandidateSQLPlan(BaseModel):
    plan_id: str
    meaning: str
    score: float
    sql_plan: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    uncertainties: list[str] = Field(default_factory=list)


class CandidateSQLPlanBundle(BaseModel):
    candidates: list[CandidateSQLPlan] = Field(default_factory=list)
    selected_plan_id: str | None = None
    summary: str = ""


class CandidateSQLPlanBuilder:
    def build(
        self,
        *,
        query_understanding: dict[str, Any],
        schema_linking: dict[str, Any],
        value_links: list[dict[str, Any]],
        candidate_tables: dict[str, Any],
        join_path_plan: dict[str, Any],
    ) -> CandidateSQLPlanBundle:
        planner = SQLPlanner()
        primary_plan = planner.build(
            query_understanding=query_understanding,
            schema_linking=schema_linking,
            value_links=value_links,
            join_path_plan=join_path_plan,
        ).model_dump()
        candidates: list[CandidateSQLPlan] = []
        unresolved_count = len([item for item in value_links if item.get("match_type") == "unresolved"])
        join_confidence = str(join_path_plan.get("plan_confidence") or "none").lower()
        table_count = len(candidate_tables.get("required_tables", []))
        base_score = 0.8
        if join_confidence == "high":
            base_score += 0.08
        elif join_confidence == "medium":
            base_score += 0.02
        elif join_confidence in {"low", "none"}:
            base_score -= 0.1
        if unresolved_count:
            base_score -= min(0.24, unresolved_count * 0.08)
        if table_count >= 3:
            base_score -= 0.05
        primary_score = max(0.0, min(0.98, round(base_score, 3)))

        candidates.append(
            CandidateSQLPlan(
                plan_id="candidate_1",
                meaning=self._build_meaning(query_understanding, join_path_plan, unresolved_count),
                score=primary_score,
                sql_plan=primary_plan,
                provenance={
                    "from": "sql_planner",
                    "join_confidence": join_confidence,
                    "required_tables": candidate_tables.get("required_tables", []),
                    "value_link_types": [item.get("match_type") for item in value_links],
                },
                uncertainties=list(primary_plan.get("uncertainties", [])),
            )
        )

        # 低置信场景补充一个保守候选，避免直接失败。
        if unresolved_count or join_confidence in {"low", "none"}:
            conservative_plan = dict(primary_plan)
            filtered_where = [
                clause
                for clause in primary_plan.get("where", [])
                if isinstance(clause, dict) and clause.get("source") == "value_linking"
            ]
            old_to_new_param_index: dict[int, int] = {}
            new_params: list[Any] = []
            for clause in filtered_where:
                old_index = clause.get("param_index")
                if not isinstance(old_index, int):
                    continue
                if old_index not in old_to_new_param_index:
                    old_to_new_param_index[old_index] = len(new_params)
                    if old_index < len(primary_plan.get("params", [])):
                        new_params.append(primary_plan["params"][old_index])
                clause["param_index"] = old_to_new_param_index[old_index]
            conservative_plan["where"] = filtered_where
            conservative_plan["params"] = new_params
            conservative_plan["uncertainties"] = list(primary_plan.get("uncertainties", [])) + ["dropped_unresolved_filters"]
            candidates.append(
                CandidateSQLPlan(
                    plan_id="candidate_2",
                    meaning="保守候选：仅保留可验证值条件并减少不确定过滤。",
                    score=max(0.0, round(primary_score - 0.07, 3)),
                    sql_plan=conservative_plan,
                    provenance={
                        "from": "candidate_sql_plan_builder",
                        "strategy": "conservative_filtering",
                    },
                    uncertainties=list(conservative_plan.get("uncertainties", [])),
                )
            )

        selected_plan_id = max(candidates, key=lambda item: item.score).plan_id if candidates else None
        return CandidateSQLPlanBundle(
            candidates=candidates,
            selected_plan_id=selected_plan_id,
            summary=f"生成 {len(candidates)} 个候选 SQL Plan，默认选择 {selected_plan_id or 'none'}。",
        )

    def _build_meaning(
        self,
        query_understanding: dict[str, Any],
        join_path_plan: dict[str, Any],
        unresolved_count: int,
    ) -> str:
        dimensions = query_understanding.get("dimensions", [])
        metrics = query_understanding.get("metrics", [])
        requires_distinct = bool(join_path_plan.get("requires_distinct"))
        parts: list[str] = []
        if dimensions:
            parts.append(f"按{','.join(str(item) for item in dimensions[:2])}分析")
        if metrics:
            parts.append(f"指标{','.join(str(item.get('aggregation', 'COUNT')) for item in metrics if isinstance(item, dict))}")
        if requires_distinct:
            parts.append("含一对多关系去重")
        if unresolved_count:
            parts.append(f"{unresolved_count}个条件未完全解析")
        if not parts:
            parts.append("基础查询")
        return "；".join(parts)
