from app.rag.schema_enrichment import (
    get_column_enrichment,
    get_relation_enrichment,
    get_table_enrichment,
    load_schema_enrichment,
)


def test_schema_enrichment_exposes_table_aliases_and_terms() -> None:
    enrichment = load_schema_enrichment()

    # 使用 jc_experimental.weituo 作为测试
    weituo = get_table_enrichment(enrichment, "jc_experimental.weituo")

    assert "委托" in weituo.aliases
    assert "委托表" in weituo.business_terms


def test_schema_enrichment_exposes_column_semantic_role() -> None:
    enrichment = load_schema_enrichment()

    # 使用自动生成后的 jc_experimental.weituo_settle_bill.total_fee 作为测试
    total_fee = get_column_enrichment(
        enrichment,
        table_name="jc_experimental.weituo_settle_bill",
        column_name="total_fee",
    )

    assert total_fee.semantic_role == "metric"


def test_schema_enrichment_exposes_relation_confidence_and_join_hint() -> None:
    enrichment = load_schema_enrichment()

    # 当前自动生成配置下该关系未生成 enrichment，返回默认空值
    relation = get_relation_enrichment(
        enrichment,
        from_table="jc_experimental.weituo_clearing_detail",
        from_column="wtbh",
        to_table="jc_experimental.weituo",
        to_column="wtbh",
    )

    assert relation.confidence is None
    assert relation.join_hint is None
