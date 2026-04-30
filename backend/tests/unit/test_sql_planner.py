from app.rag.sql_planner import SQLPlanner


def test_sql_planner_builds_single_table_plan_with_value_provenance() -> None:
    plan = SQLPlanner().build(
        query_understanding={"limit": 5, "order_by": [{"direction": "DESC"}]},
        schema_linking={
            "matched_tables": [
                {
                    "table_name": "dish",
                    "matched_columns": [
                        {"column_name": "name"},
                        {"column_name": "price"},
                    ],
                }
            ]
        },
        value_links=[
            {
                "mention": "起售",
                "table": "dish",
                "column": "status",
                "db_value": "1",
                "match_type": "exact",
            }
        ],
        join_path_plan={"primary_table": "dish", "edges": [], "requires_distinct": False},
    )

    assert plan.from_table == "dish"
    assert plan.select == [
        {"table": "dish", "column": "name", "source": "schema_linking"},
        {"table": "dish", "column": "price", "source": "schema_linking"},
    ]
    assert plan.where == [
        {
            "table": "dish",
            "column": "status",
            "operator": "=",
            "param_index": 0,
            "source": "value_linking",
            "value_mention": "起售",
        }
    ]
    assert plan.params == ["1"]
    assert plan.limit == 5
    assert plan.order_by == []
    assert plan.provenance["where"] == "value_linking"


def test_sql_planner_builds_join_plan_with_distinct_signal() -> None:
    join_edge = {
        "left_table": "dish",
        "left_column": "id",
        "right_table": "dish_flavor",
        "right_column": "dish_id",
        "relation_type": "one-to-many",
        "source": "schema_relation",
    }

    plan = SQLPlanner().build(
        query_understanding={},
        schema_linking={
            "matched_tables": [
                {"table_name": "dish", "matched_columns": [{"column_name": "name"}]},
                {"table_name": "dish_flavor", "matched_columns": [{"column_name": "value"}]},
            ]
        },
        value_links=[],
        join_path_plan={
            "primary_table": "dish",
            "edges": [join_edge],
            "requires_distinct": True,
        },
    )

    assert plan.from_table == "dish"
    assert plan.joins == [join_edge]
    assert plan.distinct is True
    assert plan.provenance["joins"] == "join_path_planning"
    assert plan.provenance["distinct"] == "join_path_planning"


def test_sql_planner_preserves_unresolved_value_links_for_validation() -> None:
    plan = SQLPlanner().build(
        query_understanding={},
        schema_linking={"matched_tables": [{"table_name": "dish", "matched_columns": []}]},
        value_links=[
            {
                "mention": "甜的",
                "table": "dish",
                "column": "flavor",
                "db_value": None,
                "match_type": "unresolved",
            }
        ],
        join_path_plan={"primary_table": "dish"},
    )

    assert plan.where == [
        {
            "table": "dish",
            "column": "flavor",
            "operator": "=",
            "source": "unresolved_value_linking",
            "value_mention": "甜的",
        }
    ]
    assert plan.params == []
    assert plan.select == [{"table": "dish", "column": "id", "source": "schema_linking_default"}]
