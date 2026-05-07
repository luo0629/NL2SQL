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


def test_sql_validator_allows_with_prefix() -> None:
    validator = SQLValidator()

    validator.validate_read_only(
        "WITH recent_customers AS (SELECT id FROM customers) SELECT id FROM recent_customers;"
    )


def test_sql_validator_rejects_prefix_lookalike() -> None:
    validator = SQLValidator()

    with raises(DangerousSQLError):
        validator.validate_read_only("SELECTED id FROM customers;")


def test_sql_validator_blocks_comment_injection_patterns() -> None:
    validator = SQLValidator()

    for sql in (
        "SELECT * FROM customers -- injected",
        "SELECT * FROM customers # injected",
        "SELECT * FROM customers /* injected */",
    ):
        with raises(DangerousSQLError):
            validator.validate_read_only(sql)


def test_sql_validator_blocks_dangerous_read_side_effect_functions() -> None:
    validator = SQLValidator()

    for sql in (
        "SELECT SLEEP(1);",
        "SELECT BENCHMARK(1000, MD5('x'));",
        "SELECT LOAD_FILE('/etc/passwd');",
        "SELECT * FROM customers INTO OUTFILE '/tmp/customers.csv';",
    ):
        with raises(DangerousSQLError):
            validator.validate_read_only(sql)
