from app.rag.confidence_judge import ConfidenceJudge


def test_confidence_judge_high_confidence_no_rerank() -> None:
    decision = ConfidenceJudge().judge(
        schema_linking={"matched_tables": [{"table_name": "dish"}]},
        value_links=[],
        join_path_plan={"plan_confidence": "high", "ambiguous_paths": []},
        candidate_plans=[{"plan_id": "candidate_1", "score": 0.9}],
        candidate_tables={"required_tables": ["dish"]},
        validation_failed=False,
    )
    assert decision.needs_rerank is False
    assert decision.final_confidence >= 0.7


def test_confidence_judge_low_confidence_requires_rerank() -> None:
    decision = ConfidenceJudge().judge(
        schema_linking={"matched_tables": []},
        value_links=[{"match_type": "unresolved"}],
        join_path_plan={"plan_confidence": "low", "ambiguous_paths": ["orders<->user"]},
        candidate_plans=[{"plan_id": "candidate_1", "score": 0.62}, {"plan_id": "candidate_2", "score": 0.55}],
        candidate_tables={"required_tables": ["orders", "user", "address"]},
        validation_failed=True,
    )
    assert decision.needs_rerank is True
    assert decision.final_confidence < 0.7
    assert "validation_failed" in decision.reasons
