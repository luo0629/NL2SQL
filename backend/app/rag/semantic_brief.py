from __future__ import annotations

from pydantic import BaseModel, Field

from app.rag.join_path_planner import JoinPathPlan
from app.rag.schema_linker import SchemaLinkingResult


class BusinessSemanticBrief(BaseModel):
    intent_summary: str
    business_entities: list[str] = Field(default_factory=list)
    key_fields: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    join_path_summary: str = ""
    constraints: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    prompt_block: str


class QuerySchemaPlan(BaseModel):
    schema_context: list[str] = Field(default_factory=list)
    schema_linking: SchemaLinkingResult
    join_path_plan: JoinPathPlan
    business_semantic_brief: BusinessSemanticBrief


class BusinessSemanticBriefBuilder:
    def build(
        self,
        question: str,
        linking_result: SchemaLinkingResult,
        join_path_plan: JoinPathPlan,
    ) -> BusinessSemanticBrief:
        business_entities = [table.table_name for table in linking_result.matched_tables]
        key_fields: list[str] = []
        metrics: list[str] = []
        filters: list[str] = []

        for linked_table in linking_result.matched_tables:
            for linked_column in linked_table.matched_columns:
                qualified_name = f"{linked_table.table_name}.{linked_column.column_name}"
                if qualified_name not in key_fields:
                    key_fields.append(qualified_name)
                if linked_column.semantic_role == "metric" and qualified_name not in metrics:
                    metrics.append(qualified_name)
                if linked_column.semantic_role in {"dimension", "timestamp", "foreign_key"} and qualified_name not in filters:
                    filters.append(qualified_name)

        constraints = [
            "优先使用系统提供的候选表、候选字段和连表路径。",
            "不要擅自引入 schema metadata 中不存在的表关系。",
        ]
        uncertainties = list(linking_result.unresolved_terms)
        if join_path_plan.unresolved_tables:
            uncertainties.extend(
                f"无法可靠连表: {table_name}" for table_name in join_path_plan.unresolved_tables
            )
        if join_path_plan.plan_confidence in {"none", "low"}:
            constraints.append("连表规划置信度不足时，应优先保守生成并显式说明不确定性。")

        intent_summary = self._build_intent_summary(question, business_entities, metrics)
        prompt_block = self._build_prompt_block(
            question=question,
            business_entities=business_entities,
            key_fields=key_fields,
            metrics=metrics,
            filters=filters,
            join_path_summary=join_path_plan.planning_summary,
            uncertainties=uncertainties,
            constraints=constraints,
        )

        return BusinessSemanticBrief(
            intent_summary=intent_summary,
            business_entities=business_entities,
            key_fields=key_fields,
            metrics=metrics,
            filters=filters,
            join_path_summary=join_path_plan.planning_summary,
            constraints=constraints,
            uncertainties=uncertainties,
            prompt_block=prompt_block,
        )

    def _build_intent_summary(
        self,
        question: str,
        business_entities: list[str],
        metrics: list[str],
    ) -> str:
        entities = ", ".join(business_entities) if business_entities else "未识别实体"
        metric_summary = ", ".join(metrics) if metrics else "未识别明确指标"
        return f"问题: {question}；候选实体: {entities}；关键指标: {metric_summary}。"

    def _build_prompt_block(
        self,
        *,
        question: str,
        business_entities: list[str],
        key_fields: list[str],
        metrics: list[str],
        filters: list[str],
        join_path_summary: str,
        uncertainties: list[str],
        constraints: list[str],
    ) -> str:
        entities_line = ", ".join(business_entities) if business_entities else "无"
        key_fields_line = ", ".join(key_fields[:8]) if key_fields else "无"
        metrics_line = ", ".join(metrics[:6]) if metrics else "无"
        filters_line = ", ".join(filters[:8]) if filters else "无"
        uncertainty_line = ", ".join(uncertainties[:6]) if uncertainties else "无"
        constraints_line = "；".join(constraints)
        return (
            "## Business semantic brief\n"
            f"Question: {question}\n"
            f"Entities: {entities_line}\n"
            f"Key fields: {key_fields_line}\n"
            f"Metrics: {metrics_line}\n"
            f"Filters: {filters_line}\n"
            f"Join plan: {join_path_summary}\n"
            f"Uncertainties: {uncertainty_line}\n"
            f"Constraints: {constraints_line}"
        )
