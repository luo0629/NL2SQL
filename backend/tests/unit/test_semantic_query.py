from app.rag.semantic_query import SemanticQueryBuilder
from app.rag.sql_generator import SQLGenerator
from app.rag.sql_planner import SQLPlanner


def test_semantic_query_collects_grounded_intent_and_confidence() -> None:
    semantic_query = SemanticQueryBuilder().build(
        query_understanding={"intent": "aggregate", "limit": 5, "order_by": [{"direction": "DESC"}]},
        schema_linking={
            "matched_tables": [
                {
                    "table_name": "orders",
                    "score": 12,
                    "matched_columns": [
                        {"column_name": "customer_id", "semantic_role": "dimension"},
                        {"column_name": "amount", "semantic_role": "metric"},
                    ],
                }
            ]
        },
        value_links=[{"table": "orders", "column": "status", "db_value": "paid", "mention": "已支付"}],
        join_path_plan={"primary_table": "orders", "edges": [], "unresolved_tables": []},
        business_semantic_brief={"uncertainties": []},
    )

    assert semantic_query.intent == "aggregate"
    assert semantic_query.entities == ["orders"]
    assert semantic_query.metrics[0].column == "amount"
    assert semantic_query.dimensions[0].column == "customer_id"
    assert semantic_query.filters[0].column == "status"
    assert semantic_query.confidence >= 0.85

    plan = SQLPlanner().build(
        query_understanding={},
        schema_linking={"matched_tables": [{"table_name": "orders", "matched_columns": []}]},
        value_links=[{"table": "orders", "column": "status", "db_value": "paid", "mention": "已支付"}],
        join_path_plan={"primary_table": "orders", "edges": []},
        semantic_query=semantic_query.model_dump(),
    )
    sql = SQLGenerator().generate(plan.model_dump())

    assert plan.provenance["select"] == "semantic_query"
    assert plan.provenance["where"] == "semantic_query"
    assert plan.provenance["semantic_query_confidence"] == semantic_query.confidence
    assert plan.params == ["paid"]
    assert sql is not None
    assert "WHERE orders.status = :p0" in sql.sql
    assert "GROUP BY orders.customer_id" in sql.sql
    assert "ORDER BY orders.amount DESC" in sql.sql


def test_semantic_query_low_confidence_prompts_for_clarification() -> None:
    semantic_query = SemanticQueryBuilder().build(
        query_understanding={"intent": "select"},
        schema_linking={"matched_tables": [{"table_name": "users", "score": 0, "matched_columns": []}]},
        value_links=[],
        join_path_plan={"primary_table": "users", "edges": [], "unresolved_tables": []},
        business_semantic_brief={"uncertainties": ["模糊术语"]},
    )

    assert semantic_query.confidence < 0.55
    assert semantic_query.clarification_prompts
