from app.rag.retriever import SchemaRetriever
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaRelation, SchemaTable


def build_catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="sky_take_out",
        tables=[
            SchemaTable(
                name="orders",
                description="订单主表",
                aliases=["订单", "订单表"],
                business_terms=["下单", "交易订单"],
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True, business_terms=["订单ID"], semantic_role="identifier"),
                    SchemaColumn(name="user_id", data_type="bigint", nullable=False, business_terms=["下单用户"], semantic_role="foreign_key"),
                    SchemaColumn(name="status", data_type="int", nullable=True, description="1=起售,0=停售", business_terms=["订单状态"], semantic_role="dimension"),
                ],
                primary_keys=["id"],
                searchable_terms=["orders", "订单", "订单表", "下单", "user_id", "status"],
            ),
            SchemaTable(
                name="user",
                description="用户表",
                aliases=["用户", "会员"],
                business_terms=["客户", "注册用户"],
                columns=[
                    SchemaColumn(name="id", data_type="bigint", nullable=False, is_primary_key=True, business_terms=["用户ID"], semantic_role="identifier"),
                    SchemaColumn(name="name", data_type="varchar", nullable=True, business_terms=["用户名"], semantic_role="dimension"),
                ],
                primary_keys=["id"],
                searchable_terms=["user", "用户", "会员", "客户", "name"],
            ),
        ],
        relations=[
            SchemaRelation(
                from_table="orders",
                from_column="user_id",
                to_table="user",
                to_column="id",
                relation_type="many-to-one",
                confidence="high",
                join_hint="通过下单用户ID关联订单与用户",
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


def test_schema_retriever_matches_enriched_business_terms() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("查询客户下单状态")

    joined = "\n".join(context)
    assert "table orders" in joined
    assert "table user" in joined


def test_schema_retriever_link_returns_structured_schema_linking_result() -> None:
    retriever = SchemaRetriever(build_catalog())

    linking_result = retriever.link("查询客户下单状态")

    assert linking_result.matched_tables
    assert linking_result.matched_tables[0].table_name == "orders"
    assert any(table.table_name == "user" for table in linking_result.matched_tables)
    assert any("客户" in table.matched_terms or "下单" in table.matched_terms for table in linking_result.matched_tables)
    assert linking_result.linking_summary


def test_schema_retriever_search_renders_structured_linking_metadata() -> None:
    retriever = SchemaRetriever(build_catalog())

    context = retriever.search("查询客户下单状态")

    joined = "\n".join(context)
    assert "matched_terms:" in joined
    assert "rationale:" in joined
    assert "role:" in joined
    assert "confidence: high" in joined
