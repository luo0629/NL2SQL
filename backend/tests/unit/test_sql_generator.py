from app.rag.sql_generator import SQLGenerator


def test_sql_generator_compiles_plan_to_parameterized_sql() -> None:
    result = SQLGenerator().generate(
        {
            "select": [
                {"table": "dish", "column": "name"},
                {"table": "dish", "column": "price"},
            ],
            "from_table": "dish",
            "joins": [],
            "where": [
                {
                    "table": "dish",
                    "column": "status",
                    "operator": "=",
                    "param_index": 0,
                }
            ],
            "order_by": [],
            "limit": 10,
            "distinct": False,
            "params": ["1"],
        }
    )

    assert result is not None
    assert result.sql == "SELECT dish.name, dish.price\nFROM dish\nWHERE dish.status = :p0\nLIMIT 10;"
    assert result.params == ["1"]


def test_sql_generator_compiles_join_distinct_plan() -> None:
    result = SQLGenerator().generate(
        {
            "select": [{"table": "dish", "column": "name"}],
            "from_table": "dish",
            "joins": [
                {
                    "left_table": "dish",
                    "left_column": "id",
                    "right_table": "dish_flavor",
                    "right_column": "dish_id",
                }
            ],
            "where": [],
            "order_by": [],
            "distinct": True,
            "params": [],
        }
    )

    assert result is not None
    assert result.sql == (
        "SELECT DISTINCT dish.name\n"
        "FROM dish\n"
        "JOIN dish_flavor ON dish.id = dish_flavor.dish_id;"
    )


def test_sql_generator_returns_none_without_from_table() -> None:
    assert SQLGenerator().generate({"select": []}) is None
