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


def test_schema_retriever_matches_column_description_terms() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("查询起售状态的订单")

    joined = "\n".join(context)
    assert "table orders" in joined
    assert "desc: 1=起售,0=停售" in joined



def test_schema_retriever_expands_related_tables_for_join_context() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("查询订单状态")

    joined = "\n".join(context)
    assert "table orders" in joined
    assert "table user" in joined
    assert "orders.user_id -> user.id" in joined



def test_schema_retriever_keeps_table_output_order_stable() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("查询用户和订单信息")

    table_blocks = [item for item in context if item.startswith("table ")]
    assert table_blocks[0].startswith("table orders")
    assert table_blocks[1].startswith("table user")
