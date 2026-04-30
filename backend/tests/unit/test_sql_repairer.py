from app.rag.sql_repairer import SQLRepairer


def test_sql_repairer_adds_missing_where_source() -> None:
    result = SQLRepairer().repair(
        {
            "where": [
                {
                    "table": "dish",
                    "column": "status",
                    "param_index": 0,
                }
            ],
            "params": ["1"],
        },
        [{"code": "WHERE_WITHOUT_VALUE_LINKING", "repairable": True}],
    )

    assert result.repaired is True
    assert result.fatal is False
    assert result.sql_plan["where"][0]["source"] == "value_linking"


def test_sql_repairer_removes_invalid_parameter_clause() -> None:
    result = SQLRepairer().repair(
        {
            "where": [
                {
                    "table": "dish",
                    "column": "status",
                    "source": "value_linking",
                    "param_index": 2,
                }
            ],
            "params": ["1"],
        },
        [{"code": "PARAMETER_INDEX_INVALID", "repairable": True}],
    )

    assert result.repaired is True
    assert result.fatal is False
    assert result.sql_plan["where"] == []


def test_sql_repairer_rejects_fatal_issue() -> None:
    result = SQLRepairer().repair(
        {"joins": [{"right_table": "unknown"}]},
        [{"code": "JOIN_WITHOUT_PLANNER_PROVENANCE", "repairable": False}],
    )

    assert result.repaired is False
    assert result.fatal is True
    assert "不可自动修复" in result.summary
