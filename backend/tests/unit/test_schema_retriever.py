from app.rag.retriever import SchemaRetriever
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable


def build_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="sky_take_out",
        tables=[
            SchemaTable(
                name="orders",
                description="订单主表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(name="user_id", data_type="bigint", nullable=False),
                    SchemaColumn(name="status", data_type="int", nullable=True, description="1=起售,0=停售"),
                ],
                primary_keys=["id"],
                searchable_terms=["orders", "订单", "user_id", "status"],
            ),
            SchemaTable(
                name="user",
                description="用户表",
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="varchar", nullable=True),
                ],
                primary_keys=["id"],
                searchable_terms=["user", "用户", "name"],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="orders",
                from_column="user_id",
                to_table="user",
                to_column="id",
                relation_type="many-to-one",
            )
        ],
        synced_at="2026-04-28T00:00:00Z",
    )


def test_schema_retriever_matches_relevant_table() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("查询订单和用户信息")

    assert any("table orders" in item for item in context)
    assert any("desc: 1=起售,0=停售" in item for item in context)
    assert any("table user" in item for item in context)
    assert any("relations" in item for item in context)


def test_schema_retriever_falls_back_when_question_is_unclear() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("   ")

    assert len(context) >= 1
    assert context[0].startswith("table ")
