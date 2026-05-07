from app.rag.llm_plan_reranker import LLMPlanReranker


def _candidates() -> list[dict[str, object]]:
    return [
        {
            "plan_id": "candidate_1",
            "score": 0.8,
            "meaning": "主计划",
            "sql_plan": {
                "from_table": "orders",
                "joins": [{"left_table": "orders", "right_table": "user", "left_column": "user_id", "right_column": "id"}],
                "where": [{"table": "orders", "column": "status", "operator": "=", "param_index": 0}],
            },
        },
        {
            "plan_id": "candidate_2",
            "score": 0.72,
            "meaning": "备选",
            "sql_plan": {"from_table": "orders", "joins": [], "where": []},
        },
    ]


def test_llm_plan_reranker_accepts_selected_plan_id() -> None:
    result = LLMPlanReranker().validate_result(
        payload={"selected_plan_id": "candidate_2"},
        candidate_plans=_candidates(),
    )
    assert result.accepted is True
    assert result.selected_plan_id == "candidate_2"


def test_llm_plan_reranker_rejects_raw_sql_output() -> None:
    result = LLMPlanReranker().validate_result(
        payload={
            "based_on_plan_id": "candidate_1",
            "revised_plan": {"sql": "SELECT * FROM user"},
        },
        candidate_plans=_candidates(),
    )
    assert result.accepted is False
    assert result.reason == "rejected_raw_sql_output"


def test_llm_plan_reranker_rejects_out_of_scope_table() -> None:
    result = LLMPlanReranker().validate_result(
        payload={
            "based_on_plan_id": "candidate_1",
            "revised_plan": {
                "from_table": "payments",
                "joins": [],
                "where": [],
            },
        },
        candidate_plans=_candidates(),
    )
    assert result.accepted is False
    assert result.reason == "rejected_out_of_candidate_scope"
