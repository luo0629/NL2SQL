from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ConfidenceDecision(BaseModel):
    final_confidence: float
    needs_rerank: bool
    reasons: list[str] = Field(default_factory=list)


class ConfidenceJudge:
    DEFAULT_THRESHOLD = 0.7

    def judge(
        self,
        *,
        schema_linking: dict[str, Any],
        value_links: list[dict[str, Any]],
        join_path_plan: dict[str, Any],
        candidate_plans: list[dict[str, Any]],
        candidate_tables: dict[str, Any],
        validation_failed: bool = False,
    ) -> ConfidenceDecision:
        score = 0.78
        reasons: list[str] = []
        matched_tables = schema_linking.get("matched_tables", [])
        if not matched_tables:
            score -= 0.25
            reasons.append("schema_linking_empty")

        unresolved_values = [item for item in value_links if item.get("match_type") == "unresolved"]
        if unresolved_values:
            score -= min(0.22, len(unresolved_values) * 0.08)
            reasons.append("unresolved_values")

        join_confidence = str(join_path_plan.get("plan_confidence") or "none").lower()
        if join_confidence == "high":
            score += 0.08
        elif join_confidence == "medium":
            score += 0.02
            reasons.append("join_confidence_medium")
        elif join_confidence in {"low", "none"}:
            score -= 0.16
            reasons.append("join_confidence_low")

        if join_path_plan.get("ambiguous_paths"):
            score -= 0.12
            reasons.append("ambiguous_join_paths")

        if len(candidate_plans) > 1:
            score -= 0.08
            reasons.append("multiple_candidate_plans")

        if len(candidate_tables.get("required_tables", [])) >= 3:
            score -= 0.1
            reasons.append("related_tables_ge_3")

        if validation_failed:
            score -= 0.25
            reasons.append("validation_failed")

        final_confidence = max(0.0, min(0.98, round(score, 3)))
        needs_rerank = final_confidence < self.DEFAULT_THRESHOLD
        if not reasons:
            reasons.append("high_confidence_path")
        return ConfidenceDecision(
            final_confidence=final_confidence,
            needs_rerank=needs_rerank,
            reasons=reasons,
        )
