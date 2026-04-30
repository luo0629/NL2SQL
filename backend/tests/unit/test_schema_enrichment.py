from app.rag.schema_enrichment import (
    get_column_enrichment,
    get_relation_enrichment,
    get_table_enrichment,
    load_schema_enrichment,
)


def test_schema_enrichment_exposes_table_aliases_and_terms() -> None:
    enrichment = load_schema_enrichment()

    orders = get_table_enrichment(enrichment, "orders")

    assert "订单" in orders.aliases
    assert "下单" in orders.business_terms


def test_schema_enrichment_exposes_column_semantic_role() -> None:
    enrichment = load_schema_enrichment()

    amount = get_column_enrichment(
        enrichment,
        table_name="orders",
        column_name="amount",
    )

    assert amount.semantic_role == "metric"
    assert "订单金额" in amount.business_terms


def test_schema_enrichment_exposes_relation_confidence_and_join_hint() -> None:
    enrichment = load_schema_enrichment()

    relation = get_relation_enrichment(
        enrichment,
        from_table="orders",
        from_column="user_id",
        to_table="user",
        to_column="id",
    )

    assert relation.confidence == "high"
    assert relation.join_hint is not None
    assert "订单" in relation.join_hint
