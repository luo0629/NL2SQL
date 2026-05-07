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
