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


def test_sql_generator_renders_aggregate_group_having_order_plan() -> None:
    result = SQLGenerator().generate(
        {
            "select": [
                {"table": "dish", "column": "category_id"},
                {"expression": "SUM(dish.price)", "alias": "total"},
            ],
            "from_table": "dish",
            "where": [
                {"table": "dish", "column": "status", "operator": "=", "param_index": 0}
            ],
            "group_by": [{"table": "dish", "column": "category_id"}],
            "having": [{"expression": "total", "operator": ">", "param_index": 1}],
            "order_by": [{"expression": "total", "direction": "DESC"}],
            "limit": 10,
            "params": ["1", 100],
        }
    )

    assert result is not None
    assert result.sql == (
        "SELECT dish.category_id, SUM(dish.price) AS total\n"
        "FROM dish\n"
        "WHERE dish.status = :p0\n"
        "GROUP BY dish.category_id\n"
        "HAVING total > :p1\n"
        "ORDER BY total DESC\n"
        "LIMIT 10;"
    )
    assert result.params == ["1", 100]


def test_sql_generator_renders_count_star_expression() -> None:
    result = SQLGenerator().generate(
        {
            "select": [{"expression": "COUNT(*)", "alias": "count"}],
            "from_table": "dish",
            "group_by": [],
            "having": [],
            "order_by": [],
            "params": [],
        }
    )

    assert result is not None
    assert result.sql == "SELECT COUNT(*) AS count\nFROM dish;"
