from app.agent.nodes import value_linking
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
                    SchemaColumn(
                        name="flavor_value",
                        data_type="varchar",
                        nullable=True,
                        aliases=["口味"],
                        sample_values=["甜味", "微辣"],
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


def test_value_linker_matches_sample_values_with_like_intent() -> None:
    result = ValueLinker().link(
        {
            "condition_mentions": [{"mention": "口味"}],
            "value_mentions": ["甜的"],
        },
        build_schema_linking("flavor_value"),
        build_catalog(),
    )

    link = result.value_links[0]

    assert link.table == "dish"
    assert link.column == "flavor_value"
    assert link.db_value == "甜味"
    assert link.match_type == "normalized"
    assert link.source == "sample"
    assert link.operator == "LIKE"
    assert link.evidence[0]["source"] == "sample_value"


def test_value_linker_uses_current_schema_sample_values_for_other_domain() -> None:
    catalog = SchemaCatalog(
        database="crm",
        tables=[
            SchemaTable(
                name="customer",
                columns=[
                    SchemaColumn(
                        name="level_name",
                        data_type="varchar",
                        nullable=True,
                        aliases=["等级"],
                        sample_values=["黄金会员", "普通会员"],
                    )
                ],
            )
        ],
    )
    schema_linking = {
        "matched_tables": [
            {
                "table_name": "customer",
                "matched_columns": [{"column_name": "level_name"}],
            }
        ]
    }

    result = ValueLinker().link(
        {
            "condition_mentions": [{"mention": "等级"}],
            "value_mentions": ["黄金"],
        },
        schema_linking,
        catalog,
    )

    link = result.value_links[0]

    assert link.table == "customer"
    assert link.column == "level_name"
    assert link.db_value == "黄金会员"
    assert link.operator == "LIKE"


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


def test_agent_value_linking_passes_catalog_to_value_linker() -> None:
    result = value_linking(
        {
            "query_understanding": {
                "condition_mentions": [{"mention": "状态"}],
                "value_mentions": ["停售"],
            },
            "schema_linking": build_schema_linking(),
        },
        build_catalog(),
    )

    link = result["value_links"][0]

    assert link["table"] == "dish"
    assert link["column"] == "status"
    assert link["db_value"] == "0"
    assert link["match_type"] == "exact"
    assert link["source"] == "mapping"
