from app.rag.schema_enrichment import (
    RelationEnrichment,
    SchemaEnrichment,
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


def test_table_and_column_enrichment_match_qualified_and_unqualified_table_names() -> None:
    enrichment = SchemaEnrichment(
        table_enrichments={
            "jc_experimental.weituo": {
                "aliases": ["委托单"],
                "business_terms": ["委托表"],
            }
        },
        column_enrichments={
            "jc_experimental.weituo": {
                "wtbh": {
                    "business_terms": ["委托编号"],
                    "semantic_role": "identifier",
                    "cross_table_diff": "业务主编号，跨表时优先使用",
                }
            }
        },
        relation_enrichments={
            "jc_experimental.weituo.wtbh->jc_experimental.weituo_settle_bill.wtbh": RelationEnrichment(
                confidence="high",
                join_hint="优先按委托编号联表",
            )
        },
    )

    table = get_table_enrichment(enrichment, "weituo")
    column = get_column_enrichment(
        enrichment,
        table_name="weituo",
        column_name="wtbh",
    )
    relation = get_relation_enrichment(
        enrichment,
        from_table="weituo",
        from_column="wtbh",
        to_table="weituo_settle_bill",
        to_column="wtbh",
    )

    assert "委托单" in table.aliases
    assert "委托编号" in column.business_terms
    assert column.semantic_role == "identifier"
    assert column.cross_table_diff == "业务主编号，跨表时优先使用"
    assert relation.confidence == "high"
    assert relation.join_hint == "优先按委托编号联表"
