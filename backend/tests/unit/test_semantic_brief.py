from app.rag.join_path_planner import JoinPathPlan, JoinEdge
from app.rag.schema_linker import LinkedColumn, LinkedTable, SchemaLinkingResult
from app.rag.semantic_brief import BusinessSemanticBriefBuilder
from app.rag.schema_models import SchemaRelation


def build_linking_result() -> SchemaLinkingResult:
    return SchemaLinkingResult(
        question="查询客户下单状态和用户信息",
        matched_tables=[
            LinkedTable(
                table_name="orders",
                score=100,
                matched_terms=["下单"],
                matched_columns=[
                    LinkedColumn(column_name="status", score=10, matched_terms=["状态"], semantic_role="dimension"),
                    LinkedColumn(column_name="amount", score=9, matched_terms=["金额"], semantic_role="metric"),
                ],
                rationale="命中订单相关术语",
            ),
            LinkedTable(
                table_name="user",
                score=90,
                matched_terms=["客户", "用户信息"],
                matched_columns=[
                    LinkedColumn(column_name="name", score=8, matched_terms=["用户信息"], semantic_role="dimension"),
                ],
                rationale="命中用户相关术语",
            ),
        ],
        matched_relations=[
            SchemaRelation(
                from_table="orders",
                from_column="user_id",
                to_table="user",
                to_column="id",
                relation_type="many-to-one",
                confidence="high",
                join_hint="通过下单用户ID关联订单与用户",
            )
        ],
        unresolved_terms=["客户画像"],
        linking_summary="命中订单与用户。",
    )


def build_join_plan() -> JoinPathPlan:
    return JoinPathPlan(
        primary_table="orders",
        tables_in_plan=["orders", "user"],
        edges=[
            JoinEdge(
                left_table="orders",
                left_column="user_id",
                right_table="user",
                right_column="id",
                relation_type="many-to-one",
                join_hint="通过下单用户ID关联订单与用户",
                confidence="high",
            )
        ],
        plan_confidence="high",
        unresolved_tables=[],
        planning_summary="主表: orders。连表覆盖: orders, user。路径: orders.user_id->user.id。规划置信度: high。",
    )


def test_business_semantic_brief_builder_outputs_prompt_ready_summary() -> None:
    brief = BusinessSemanticBriefBuilder().build(
        "查询客户下单状态和用户信息",
        build_linking_result(),
        build_join_plan(),
    )

    assert brief.business_entities == ["orders", "user"]
    assert "orders.amount" in brief.metrics
    assert "orders.status" in brief.filters
    assert "客户画像" in brief.uncertainties
    assert "## Business semantic brief" in brief.prompt_block
    assert "Join plan:" in brief.prompt_block
    assert "orders.user_id->user.id" in brief.prompt_block
