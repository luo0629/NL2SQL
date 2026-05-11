from pathlib import Path

import yaml

from app.config import get_settings
from app.config_loader import reload_app_config
from app.rag.schema_enrichment import (
    RelationEnrichment,
    SchemaEnrichment,
    get_column_enrichment,
    get_relation_enrichment,
    get_table_enrichment,
    load_schema_enrichment,
)


def test_schema_enrichment_exposes_table_aliases_and_terms() -> None:
    config_dir = Path(get_settings().config_dir)
    (config_dir / "business_terms.yaml").write_text(
        yaml.safe_dump(
            {
                "generated": {
                    "terms": [
                        {
                            "alias": "订单",
                            "standard": "orders",
                            "tables": ["orders"],
                            "business_terms": ["订单表"],
                        }
                    ]
                },
                "overrides": {"terms": []},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    reload_app_config()
    enrichment = load_schema_enrichment()
    orders = get_table_enrichment(enrichment, "orders")

    assert "订单" in orders.aliases
    assert "订单表" in orders.business_terms


def test_schema_enrichment_exposes_column_semantic_role() -> None:
    config_dir = Path(get_settings().config_dir)
    (config_dir / "field_semantics.yaml").write_text(
        yaml.safe_dump(
            {
                "generated": {
                    "fields": {
                        "orders": {
                            "total_amount": {
                                "business_terms": ["订单金额"],
                                "semantic_role": "metric",
                                "cross_table_diff": None,
                            }
                        }
                    }
                },
                "overrides": {"fields": {}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    reload_app_config()
    enrichment = load_schema_enrichment()

    total_amount = get_column_enrichment(
        enrichment,
        table_name="orders",
        column_name="total_amount",
    )

    assert total_amount.semantic_role == "metric"


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
