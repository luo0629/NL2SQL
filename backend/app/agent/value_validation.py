from __future__ import annotations

from dataclasses import dataclass

from sqlglot import errors, exp, parse_one

from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable


_STRING_TYPE_MARKERS = ("CHAR", "TEXT", "STRING", "CLOB")
_IDENTIFIER_LIKE_SUFFIXES = ("_id", "_code", "_no")


@dataclass(frozen=True)
class ValuePredicate:
    table: str
    column: str
    value: str
    operator: str


@dataclass(frozen=True)
class MissingValueIssue:
    table: str
    column: str
    value: str
    suggestions: list[str]


def extract_value_predicates(sql: str, catalog: SchemaCatalog) -> list[ValuePredicate]:
    try:
        tree = parse_one(sql, dialect="mysql")
    except (errors.ParseError, errors.TokenError):
        return []

    table_lookup = {table.name.lower(): table for table in catalog.tables}
    alias_to_table = _table_aliases(tree, table_lookup)
    where = tree.find(exp.Where)
    if where is None or where.this is None:
        return []

    predicates: list[ValuePredicate] = []
    _collect_predicates(where.this, alias_to_table, table_lookup, predicates)
    return predicates


def build_missing_value_prompt(question: str, issues: list[MissingValueIssue]) -> str:
    lines = [
        "SQL 值存在性校验未通过，请根据真实数据库值修正 SQL。",
        f"用户原始问题：{question}",
        "不存在的筛选值：",
    ]
    for issue in issues:
        suggestions = "、".join(issue.suggestions) if issue.suggestions else "未找到相似值"
        lines.append(
            f"- `{issue.table}`.`{issue.column}` 中不存在值 '{issue.value}'；可参考真实值：{suggestions}。"
        )
    lines.append("请只替换不存在的筛选值，不要引入 schema_context 之外的表或字段。")
    return "\n".join(lines)


def _table_aliases(tree: exp.Expression, table_lookup: dict[str, SchemaTable]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table_expr in tree.find_all(exp.Table):
        table_name = table_expr.name
        real_table = table_lookup.get(table_name.lower())
        if real_table is None:
            continue
        aliases[table_name.lower()] = real_table.name
        alias = table_expr.alias
        if alias:
            aliases[alias.lower()] = real_table.name
    return aliases


def _collect_predicates(
    expression: exp.Expression,
    alias_to_table: dict[str, str],
    table_lookup: dict[str, SchemaTable],
    predicates: list[ValuePredicate],
) -> None:
    if isinstance(expression, exp.And):
        _collect_predicates(expression.left, alias_to_table, table_lookup, predicates)
        _collect_predicates(expression.right, alias_to_table, table_lookup, predicates)
        return

    if isinstance(expression, exp.EQ):
        predicate = _predicate_from_eq(expression, alias_to_table, table_lookup)
        if predicate is not None:
            predicates.append(predicate)
        return

    if isinstance(expression, exp.In):
        predicates.extend(_predicates_from_in(expression, alias_to_table, table_lookup))


def _predicate_from_eq(
    expression: exp.EQ,
    alias_to_table: dict[str, str],
    table_lookup: dict[str, SchemaTable],
) -> ValuePredicate | None:
    left = expression.left
    right = expression.right
    if isinstance(left, exp.Column) and _is_string_literal(right):
        return _predicate_from_parts(left, right.this, "=", alias_to_table, table_lookup)
    if isinstance(right, exp.Column) and _is_string_literal(left):
        return _predicate_from_parts(right, left.this, "=", alias_to_table, table_lookup)
    return None


def _predicates_from_in(
    expression: exp.In,
    alias_to_table: dict[str, str],
    table_lookup: dict[str, SchemaTable],
) -> list[ValuePredicate]:
    column = expression.this
    if not isinstance(column, exp.Column):
        return []

    predicates: list[ValuePredicate] = []
    for item in expression.expressions:
        if _is_string_literal(item):
            predicate = _predicate_from_parts(column, item.this, "IN", alias_to_table, table_lookup)
            if predicate is not None:
                predicates.append(predicate)
    return predicates


def _predicate_from_parts(
    column_expr: exp.Column,
    value: object,
    operator: str,
    alias_to_table: dict[str, str],
    table_lookup: dict[str, SchemaTable],
) -> ValuePredicate | None:
    if not isinstance(value, str):
        return None

    resolved = _resolve_column(column_expr, alias_to_table, table_lookup)
    if resolved is None:
        return None

    table, column = resolved
    if not _should_validate_column(column):
        return None

    return ValuePredicate(table=table.name, column=column.name, value=value, operator=operator)


def _resolve_column(
    column_expr: exp.Column,
    alias_to_table: dict[str, str],
    table_lookup: dict[str, SchemaTable],
) -> tuple[SchemaTable, SchemaColumn] | None:
    column_name = column_expr.name
    qualifier = column_expr.table

    if qualifier:
        table_name = alias_to_table.get(qualifier.lower()) or qualifier
        table = table_lookup.get(table_name.lower())
        if table is None:
            return None
        column = _find_column(table, column_name)
        return (table, column) if column is not None else None

    candidate_tables = {name.lower() for name in alias_to_table.values()} or set(table_lookup)
    matches: list[tuple[SchemaTable, SchemaColumn]] = []
    for table_key in candidate_tables:
        table = table_lookup.get(table_key)
        if table is None:
            continue
        column = _find_column(table, column_name)
        if column is not None:
            matches.append((table, column))
    return matches[0] if len(matches) == 1 else None


def _find_column(table: SchemaTable, column_name: str) -> SchemaColumn | None:
    for column in table.columns:
        if column.name.lower() == column_name.lower():
            return column
    return None


def _should_validate_column(column: SchemaColumn) -> bool:
    name = column.name.lower()
    data_type = column.data_type.upper()
    description = (column.description or "").lower()
    if column.is_primary_key or name == "id" or name.endswith(_IDENTIFIER_LIKE_SUFFIXES):
        return False
    if "enum_mapping" in description:
        return False
    return any(marker in data_type for marker in _STRING_TYPE_MARKERS)


def _is_string_literal(expression: exp.Expression) -> bool:
    return isinstance(expression, exp.Literal) and bool(expression.is_string)
