from app.rag.candidate_table_builder import CandidateTableBuilder


def test_candidate_table_builder_selects_single_target_table() -> None:
    result = CandidateTableBuilder().build(
        {"target_mentions": ["订单"], "confidence": 0.9},
        {
            "matched_tables": [
                {
                    "table_name": "orders",
                    "score": 30,
                    "matched_columns": [{"column_name": "amount", "score": 20}],
                }
            ]
        },
        [],
    )

    assert result.target_table == "orders"
    assert result.required_tables == ["orders"]
    assert result.related_table_count == 1
    assert result.table_reasons[0].source in {"schema_linking", "schema_linking_column"}


def test_candidate_table_builder_combines_schema_and_value_tables() -> None:
    result = CandidateTableBuilder().build(
        {"target_mentions": ["商品"], "possible_multi_table": True, "confidence": 0.85},
        {
            "matched_tables": [
                {
                    "table_name": "product",
                    "score": 32,
                    "matched_columns": [{"column_name": "name", "score": 16}],
                }
            ]
        },
        [
            {
                "mention": "甜味",
                "table": "product_flavor",
                "column": "value",
                "confidence": 0.9,
            }
        ],
    )

    assert result.target_table == "product"
    assert result.required_tables == ["product", "product_flavor"]
    assert result.optional_tables == []
    assert result.related_table_count == 2
    assert any(reason.source == "value_linking" for reason in result.table_reasons)


def test_candidate_table_builder_handles_unresolved_value_without_fake_table() -> None:
    result = CandidateTableBuilder().build(
        {"value_mentions": ["未知值"], "missing_slots": ["target_mentions"]},
        {"matched_tables": []},
        [{"mention": "未知值", "table": None, "column": None, "confidence": 0.0}],
    )

    assert result.target_table is None
    assert result.required_tables == []
    assert result.confidence == 0.0


def test_candidate_table_builder_keeps_target_first_for_ordering_or_aggregation() -> None:
    result = CandidateTableBuilder().build(
        {
            "aggregation": {"type": "COUNT"},
            "order_by": [{"term": "数量", "direction": "DESC"}],
            "confidence": 0.88,
        },
        {
            "matched_tables": [
                {
                    "table_name": "department",
                    "score": 25,
                    "matched_columns": [{"column_name": "name", "score": 12}],
                },
                {
                    "table_name": "employee",
                    "score": 12,
                    "matched_columns": [{"column_name": "department_id", "score": 12}],
                },
            ]
        },
        [],
    )

    assert result.required_tables == ["department"]
    assert result.optional_tables == ["employee"]
    assert result.required_tables[0] == "department"


def test_candidate_table_builder_single_table_preferred_without_multi_table_intent() -> None:
    result = CandidateTableBuilder().build(
        {"possible_multi_table": False, "confidence": 0.9},
        {
            "matched_tables": [
                {"table_name": "dish", "score": 35, "matched_columns": [{"column_name": "name", "score": 20}]},
                {"table_name": "category", "score": 30, "matched_columns": [{"column_name": "name", "score": 18}]},
            ]
        },
        [],
    )
    assert result.required_tables == ["dish"]
    assert result.optional_tables == ["category"]
