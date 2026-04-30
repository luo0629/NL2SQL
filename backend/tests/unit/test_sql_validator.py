from pytest import raises

from app.utils.exceptions import DangerousSQLError
from app.validator.sql_validator import SQLValidator


def test_sql_validator_allows_single_select() -> None:
    validator = SQLValidator()

    validator.validate_read_only(
        "SELECT id, name FROM customers ORDER BY id ASC LIMIT 10;"
    )


def test_sql_validator_blocks_limit_without_order_by() -> None:
    validator = SQLValidator()

    with raises(DangerousSQLError):
        validator.validate_read_only("SELECT id, name FROM customers LIMIT 10;")


def test_sql_validator_blocks_dangerous_keyword() -> None:
    validator = SQLValidator()

    with raises(DangerousSQLError):
        validator.validate_read_only("DROP TABLE customers;")


def test_sql_validator_blocks_multiple_statements() -> None:
    validator = SQLValidator()

    with raises(DangerousSQLError):
        validator.validate_read_only("SELECT * FROM customers; DELETE FROM customers;")


def test_sql_validator_accepts_valid_plan_provenance() -> None:
    issues = SQLValidator().validate_plan_provenance(
        sql_plan={
            "from_table": "dish",
            "joins": [
                {
                    "left_table": "dish",
                    "left_column": "id",
                    "right_table": "dish_flavor",
                    "right_column": "dish_id",
                    "source": "schema_relation",
                }
            ],
            "where": [
                {
                    "table": "dish_flavor",
                    "column": "value",
                    "source": "value_linking",
                    "param_index": 0,
                }
            ],
            "provenance": {"from_table": "schema_linking"},
        },
        params=["甜味"],
    )

    assert issues == []


def test_sql_validator_reports_unlinked_where_value() -> None:
    issues = SQLValidator().validate_plan_provenance(
        sql_plan={
            "from_table": "dish",
            "where": [{"table": "dish", "column": "status", "param_index": 0}],
            "provenance": {"from_table": "schema_linking"},
        },
        params=["1"],
    )

    assert issues[0]["code"] == "WHERE_WITHOUT_VALUE_LINKING"
    assert issues[0]["repairable"] is True


def test_sql_validator_reports_invented_join() -> None:
    issues = SQLValidator().validate_plan_provenance(
        sql_plan={
            "from_table": "dish",
            "joins": [
                {
                    "left_table": "dish",
                    "left_column": "id",
                    "right_table": "unknown",
                    "right_column": "dish_id",
                }
            ],
            "provenance": {"from_table": "schema_linking"},
        },
        params=[],
    )

    assert issues[0]["code"] == "JOIN_WITHOUT_PLANNER_PROVENANCE"
    assert issues[0]["repairable"] is False


def test_sql_validator_reports_invalid_parameter_index() -> None:
    issues = SQLValidator().validate_plan_provenance(
        sql_plan={
            "from_table": "dish",
            "where": [
                {
                    "table": "dish",
                    "column": "status",
                    "source": "value_linking",
                    "param_index": 1,
                }
            ],
            "provenance": {"from_table": "schema_linking"},
        },
        params=["1"],
    )

    assert issues[0]["code"] == "PARAMETER_INDEX_INVALID"
    assert issues[0]["repairable"] is True


def test_sql_validator_reports_sql_plan_from_table_mismatch() -> None:
    issues = SQLValidator().validate_sql_matches_plan(
        sql="SELECT dish.name FROM dish WHERE dish.status = :p0 LIMIT 5;",
        sql_plan={
            "from_table": "orders",
            "where": [{"table": "dish", "column": "status", "operator": "=", "param_index": 0}],
            "limit": 5,
        },
        params=["1"],
    )

    assert issues[0]["code"] == "SQL_FROM_TABLE_MISMATCH"


def test_sql_validator_reports_sql_plan_where_mismatch() -> None:
    issues = SQLValidator().validate_sql_matches_plan(
        sql="SELECT dish.name FROM dish LIMIT 5;",
        sql_plan={
            "from_table": "dish",
            "where": [{"table": "dish", "column": "status", "operator": "=", "param_index": 0}],
            "limit": 5,
        },
        params=["1"],
    )

    assert any(issue["code"] == "SQL_WHERE_MISMATCH" for issue in issues)


def test_sql_validator_reports_sql_plan_param_count_mismatch() -> None:
    issues = SQLValidator().validate_sql_matches_plan(
        sql="SELECT dish.name FROM dish WHERE dish.status = :p0;",
        sql_plan={
            "from_table": "dish",
            "where": [{"table": "dish", "column": "status", "operator": "=", "param_index": 0}],
        },
        params=["1", "extra"],
    )

    assert any(issue["code"] == "SQL_PARAM_COUNT_MISMATCH" for issue in issues)
