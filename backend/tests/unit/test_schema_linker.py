from app.rag.schema_linker import SchemaLinker
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable


def test_schema_linker_uses_aliases_and_sample_values_as_evidence() -> None:
    catalog = SchemaCatalog(
        database="generic",
        tables=[
            SchemaTable(
                name="product",
                aliases=["商品"],
                searchable_terms=["product", "商品", "口味", "甜味"],
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(
                        name="flavor_value",
                        data_type="varchar",
                        nullable=True,
                        aliases=["口味"],
                        business_terms=["味道"],
                        sample_values=["甜味", "微辣"],
                        semantic_role="dimension",
                    ),
                    SchemaColumn(
                        name="description",
                        data_type="varchar",
                        nullable=True,
                        description="商品描述",
                        semantic_role="description",
                    ),
                ],
            )
        ],
    )

    result = SchemaLinker(catalog).link("查询甜味商品")
    linked_columns = result.matched_tables[0].matched_columns

    assert linked_columns[0].column_name == "flavor_value"
    assert "甜味" in linked_columns[0].matched_terms
    assert any(item["source"] == "sample_value" for item in linked_columns[0].evidence)


def test_schema_linker_downranks_generic_description_columns_unless_requested() -> None:
    catalog = SchemaCatalog(
        database="generic",
        tables=[
            SchemaTable(
                name="product",
                aliases=["商品"],
                searchable_terms=["product", "商品", "甜味", "描述"],
                columns=[
                    SchemaColumn(
                        name="flavor_value",
                        data_type="varchar",
                        nullable=True,
                        aliases=["口味"],
                        sample_values=["甜味"],
                        semantic_role="dimension",
                    ),
                    SchemaColumn(
                        name="description",
                        data_type="varchar",
                        nullable=True,
                        description="甜味 商品 描述",
                        semantic_role="description",
                    ),
                ],
            )
        ],
    )

    result = SchemaLinker(catalog).link("查询甜味商品")
    columns = result.matched_tables[0].matched_columns
    score_by_column = {column.column_name: column.score for column in columns}

    assert score_by_column["flavor_value"] > score_by_column["description"]
    description_column = next(column for column in columns if column.column_name == "description")
    assert any(item["source"] == "weak_text_downrank" for item in description_column.evidence)


def test_schema_linker_allows_description_when_user_requests_description() -> None:
    catalog = SchemaCatalog(
        database="generic",
        tables=[
            SchemaTable(
                name="product",
                aliases=["商品"],
                searchable_terms=["product", "商品", "描述"],
                columns=[
                    SchemaColumn(
                        name="description",
                        data_type="varchar",
                        nullable=True,
                        description="商品描述",
                        semantic_role="description",
                    )
                ],
            )
        ],
    )

    result = SchemaLinker(catalog).link("查询商品描述")
    description_column = result.matched_tables[0].matched_columns[0]

    assert description_column.column_name == "description"
    assert not any(item["source"] == "weak_text_downrank" for item in description_column.evidence)
