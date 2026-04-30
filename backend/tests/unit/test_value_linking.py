from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable
from app.rag.value_linker import ValueLinker


def build_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="test",
        tables=[
            SchemaTable(
                name="dish",
                columns=[
                    SchemaColumn(
                        name="status",
                        data_type="int",
                        nullable=True,
                        description="1=起售,0=停售",
                    ),
                    SchemaColumn(
                        name="price",
                        data_type="decimal",
                        nullable=True,
                    ),
                ],
            )
        ],
    )


def build_schema_linking(column_name: str = "status") -> dict[str, object]:
    return {
        "matched_tables": [
            {
                "table_name": "dish",
                "matched_columns": [{"column_name": column_name}],
            }
        ]
    }


def test_value_linker_matches_exact_mapping_value() -> None:
    result = ValueLinker().link(
        {
            "condition_mentions": [{"mention": "状态"}],
            "value_mentions": ["起售"],
        },
        build_schema_linking(),
        build_catalog(),
    )

    link = result.value_links[0]

    assert link.table == "dish"
    assert link.column == "status"
    assert link.db_value == "1"
    assert link.match_type == "exact"
    assert link.source == "mapping"


def test_value_linker_matches_normalized_mapping_value() -> None:
    result = ValueLinker().link(
        {
            "condition_mentions": [{"mention": "状态"}],
            "value_mentions": ["起售的"],
        },
        build_schema_linking(),
        build_catalog(),
    )

    link = result.value_links[0]

    assert link.db_value == "1"
    assert link.match_type == "normalized"


def test_value_linker_keeps_numeric_typed_literal() -> None:
    result = ValueLinker().link(
        {
            "condition_mentions": [{"mention": "价格"}],
            "value_mentions": ["30"],
        },
        build_schema_linking("price"),
        build_catalog(),
    )

    link = result.value_links[0]

    assert link.table == "dish"
    assert link.column == "price"
    assert link.db_value == 30
    assert link.match_type == "typed_literal"
    assert link.source == "literal"


def test_value_linker_marks_unresolved_value() -> None:
    result = ValueLinker().link(
        {
            "condition_mentions": [{"mention": "口味"}],
            "value_mentions": ["甜的"],
        },
        build_schema_linking("status"),
        build_catalog(),
    )

    link = result.value_links[0]

    assert link.mention == "甜的"
    assert link.db_value is None
    assert link.match_type == "unresolved"
    assert link.source == "fallback"
