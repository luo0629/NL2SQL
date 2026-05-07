from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RerankResult(BaseModel):
    selected_plan_id: str | None = None
    revised_plan: dict[str, Any] | None = None
    accepted: bool = False
    reason: str = ""


class LLMPlanReranker:
    def compact_payload(
        self,
        *,
        question: str,
        candidate_plans: list[dict[str, Any]],
        confidence: dict[str, Any],
    ) -> dict[str, Any]:
        compact_candidates = []
        for candidate in candidate_plans:
            compact_candidates.append(
                {
                    "plan_id": candidate.get("plan_id"),
                    "meaning": candidate.get("meaning"),
                    "score": candidate.get("score"),
                    "from_table": candidate.get("sql_plan", {}).get("from_table"),
                    "joins": candidate.get("sql_plan", {}).get("joins", []),
                    "where": candidate.get("sql_plan", {}).get("where", []),
                    "group_by": candidate.get("sql_plan", {}).get("group_by", []),
                    "order_by": candidate.get("sql_plan", {}).get("order_by", []),
                    "uncertainties": candidate.get("uncertainties", []),
                }
            )
        return {
            "question": question,
            "confidence": confidence,
            "candidates": compact_candidates,
            "rules": [
                "只能选择已有候选 plan_id，或返回候选范围内 revised_plan",
                "禁止输出最终 SQL 字符串",
                "禁止引入候选之外的表、字段、join 或值",
            ],
        }

    def validate_result(
        self,
        *,
        payload: dict[str, Any],
        candidate_plans: list[dict[str, Any]],
    ) -> RerankResult:
        candidate_by_id = {
            str(item.get("plan_id")): item
            for item in candidate_plans
            if isinstance(item, dict) and item.get("plan_id")
        }
        selected_plan_id = payload.get("selected_plan_id")
        if isinstance(selected_plan_id, str) and selected_plan_id in candidate_by_id:
            return RerankResult(
                selected_plan_id=selected_plan_id,
                accepted=True,
                reason="selected_plan_id_in_range",
            )

        revised_plan = payload.get("revised_plan")
        based_on_id = payload.get("based_on_plan_id")
        if isinstance(revised_plan, dict) and isinstance(based_on_id, str) and based_on_id in candidate_by_id:
            if self._contains_raw_sql_text(revised_plan):
                return RerankResult(reason="rejected_raw_sql_output")
            if not self._is_within_candidate_scope(revised_plan, candidate_by_id[based_on_id].get("sql_plan", {})):
                return RerankResult(reason="rejected_out_of_candidate_scope")
            return RerankResult(
                selected_plan_id=based_on_id,
                revised_plan=revised_plan,
                accepted=True,
                reason="accepted_revised_plan",
            )
        return RerankResult(reason="no_valid_selection")

    def _contains_raw_sql_text(self, revised_plan: dict[str, Any]) -> bool:
        sql_text = revised_plan.get("sql")
        if isinstance(sql_text, str) and sql_text.strip():
            return True
        return False

    def _is_within_candidate_scope(self, revised_plan: dict[str, Any], base_plan: dict[str, Any]) -> bool:
        allowed_tables = self._collect_tables(base_plan)
        revised_tables = self._collect_tables(revised_plan)
        return revised_tables.issubset(allowed_tables)

    def _collect_tables(self, plan: dict[str, Any]) -> set[str]:
        tables: set[str] = set()
        from_table = plan.get("from_table")
        if isinstance(from_table, str) and from_table:
            tables.add(from_table)
        for join in plan.get("joins", []):
            if not isinstance(join, dict):
                continue
            for key in ("left_table", "right_table"):
                value = join.get(key)
                if isinstance(value, str) and value:
                    tables.add(value)
        for section in ("select", "where", "group_by", "order_by"):
            for item in plan.get(section, []):
                if not isinstance(item, dict):
                    continue
                table = item.get("table")
                if isinstance(table, str) and table:
                    tables.add(table)
        return tables
