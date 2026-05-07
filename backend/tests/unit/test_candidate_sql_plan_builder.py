from app.rag.candidate_sql_plan_builder import CandidateSQLPlanBuilder


def test_candidate_sql_plan_builder_builds_primary_candidate() -> None:
    bundle = CandidateSQLPlanBuilder().build(
        query_understanding={"dimensions": ["分类"]},
        schema_linking={
            "matched_tables": [
                {"table_name": "dish", "matched_columns": [{"column_name": "category_id"}, {"column_name": "name"}]}
            ]
        },
        value_links=[{"table": "dish", "column": "status", "db_value": "1", "match_type": "exact", "mention": "起售"}],
        candidate_tables={"required_tables": ["dish"], "target_table": "dish"},
        join_path_plan={"primary_table": "dish", "edges": [], "plan_confidence": "high", "requires_distinct": False},
    )
    assert len(bundle.candidates) >= 1
    assert bundle.selected_plan_id == "candidate_1"
    assert bundle.candidates[0].sql_plan["from_table"] == "dish"
    assert bundle.candidates[0].score > 0.8


def test_candidate_sql_plan_builder_adds_conservative_candidate_for_unresolved() -> None:
    bundle = CandidateSQLPlanBuilder().build(
        query_understanding={},
        schema_linking={"matched_tables": [{"table_name": "orders", "matched_columns": [{"column_name": "id"}]}]},
        value_links=[
            {"table": "orders", "column": "status", "db_value": "PAID", "match_type": "exact", "mention": "已支付"},
            {"table": "orders", "column": "note", "db_value": None, "match_type": "unresolved", "mention": "紧急"},
        ],
        candidate_tables={"required_tables": ["orders", "user"], "target_table": "orders"},
        join_path_plan={"primary_table": "orders", "edges": [], "plan_confidence": "low", "requires_distinct": False},
    )
    assert len(bundle.candidates) == 2
    conservative = bundle.candidates[1]
    assert conservative.plan_id == "candidate_2"
    assert "dropped_unresolved_filters" in conservative.uncertainties
