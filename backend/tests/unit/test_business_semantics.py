import json

from app.rag.business_semantics import attach_business_semantics, build_business_semantics, business_semantic_yaml_path
from app.rag.schema_models import SchemaCatalog, SchemaColumn, SchemaTable


def _catalog() -> SchemaCatalog:
    return SchemaCatalog(
        database="test",
        tables=[
            SchemaTable(
                name="orders",
                description="订单主表",
                aliases=["订单"],
                business_terms=["交易订单"],
                columns=[
                    SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True),
                    SchemaColumn(
                        name="amount",
                        data_type="decimal(10,2)",
                        nullable=False,
                        description="订单金额",
                        business_terms=["销售额", "实收金额"],
                        semantic_role="metric",
                    ),
                    SchemaColumn(
                        name="status",
                        data_type="int",
                        nullable=False,
                        description="0 未支付 1 已支付 2 已取消",
                        business_terms=["订单状态"],
                        semantic_role="dimension",
                    ),
                ],
            ),
            SchemaTable(
                name="user",
                description="用户表",
                aliases=["会员"],
                columns=[
                    SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="varchar", nullable=True, business_terms=["客户姓名"], semantic_role="dimension"),
                ],
            ),
        ],
    )


def test_business_semantics_derives_terms_metrics_dimensions_and_enums() -> None:
    semantics = build_business_semantics(_catalog())

    terms = {term.term for term in semantics.terms}
    assert "销售额" in terms
    assert "订单主表" in terms
    assert any(metric.name == "销售额" and metric.table == "orders" and metric.column == "amount" for metric in semantics.metrics)
    assert any(dimension.table == "orders" and dimension.column == "status" for dimension in semantics.dimensions)
    assert any(enum.table == "orders" and enum.column == "status" and enum.values.get("1") == "已支付" for enum in semantics.enums)


def test_business_semantics_merges_valid_overrides_and_filters_invalid_refs(tmp_path) -> None:
    override_path = tmp_path / "business_semantics.yaml"
    override_path.write_text(
        json.dumps(
            {
                "aliases": {
                    "客户": {"tables": ["user"], "columns": ["user.name", "missing.name"]}
                },
                "metrics": {
                    "收入": {"table": "orders", "column": "orders.amount", "aliases": ["营收"]},
                    "坏指标": {"table": "missing", "column": "orders.amount"},
                },
                "enums": {
                    "支付状态": {"table": "orders", "column": "orders.status", "values": {"1": "已支付"}}
                },
                "default_filters": {
                    "已支付订单": {
                        "table": "orders",
                        "condition": "`orders`.`status` = 1",
                        "columns": ["orders.status", "orders.missing"],
                    },
                    "坏过滤": {
                        "table": "orders",
                        "condition": "`orders`.`missing` = 1",
                        "columns": ["orders.status"],
                    },
                    "危险过滤": {
                        "table": "orders",
                        "condition": "`orders`.`status` = 1; DROP TABLE orders",
                        "columns": ["orders.status"],
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    semantics = build_business_semantics(_catalog(), str(override_path))

    assert any(metric.name == "收入" and metric.source == "override" for metric in semantics.metrics)
    assert not any(metric.name == "坏指标" for metric in semantics.metrics)
    assert any(item.name == "已支付订单" for item in semantics.default_filters)
    assert not any(item.name in {"坏过滤", "危险过滤"} for item in semantics.default_filters)
    assert any(term.term == "客户" and term.tables == ["user"] and term.columns == ["user.name"] for term in semantics.terms)
    assert any(diagnostic["code"] in {"SEMANTIC_OVERRIDE_INVALID_TABLE", "SEMANTIC_OVERRIDE_INVALID_COLUMN"} for diagnostic in semantics.diagnostics)
    assert any(diagnostic["code"] == "SEMANTIC_OVERRIDE_INVALID_FRAGMENT_REF" for diagnostic in semantics.diagnostics)
    assert any(diagnostic["code"] == "SEMANTIC_OVERRIDE_UNSAFE_FRAGMENT" for diagnostic in semantics.diagnostics)


def test_business_semantics_loads_yaml_override_file(tmp_path) -> None:
    override_path = tmp_path / "business_semantics.yaml"
    override_path.write_text(
        """
metrics:
  net_sales:
    table: orders
    column: orders.amount
    aliases:
      - 净销售额
default_filters:
  paid_orders:
    table: orders
    condition: "`orders`.`status` = 1"
    columns:
      - orders.status
""".strip(),
        encoding="utf-8",
    )

    semantics = build_business_semantics(_catalog(), str(override_path))

    assert any(metric.name == "net_sales" and "净销售额" in metric.aliases for metric in semantics.metrics)
    assert any(item.name == "paid_orders" for item in semantics.default_filters)


def test_missing_override_diagnostic_does_not_expose_raw_file_path(tmp_path) -> None:
    override_path = tmp_path / "missing" / "business_semantics.yaml"

    semantics = build_business_semantics(_catalog(), str(override_path))

    assert any(diagnostic["code"] == "SEMANTIC_OVERRIDE_NOT_FOUND" for diagnostic in semantics.diagnostics)
    assert all(str(tmp_path) not in diagnostic["message"] for diagnostic in semantics.diagnostics)


def test_attach_business_semantics_keeps_catalog_contract() -> None:
    catalog = attach_business_semantics(_catalog())

    assert catalog.business_semantics is not None
    assert any(term.term == "交易订单" for term in catalog.business_semantics.terms)


def test_yaml_disabled_does_not_create_database_specific_file(tmp_path) -> None:
    database_url = "postgresql+asyncpg://user:secret@example.com:5432/sales"

    semantics = build_business_semantics(_catalog(), yaml_enabled=False, database_url=database_url, yaml_dir=tmp_path)

    assert any(metric.name == "销售额" for metric in semantics.metrics)
    assert list(tmp_path.iterdir()) == []


def test_yaml_enabled_generates_safe_database_specific_file(tmp_path) -> None:
    database_url = "postgresql+asyncpg://user:secret@example.com:5432/sales"

    semantics = build_business_semantics(_catalog(), yaml_enabled=True, database_url=database_url, yaml_dir=tmp_path)
    yaml_path = business_semantic_yaml_path(database_url, tmp_path)
    yaml_text = yaml_path.read_text(encoding="utf-8")

    assert yaml_path.exists()
    assert "secret" not in yaml_path.name
    assert "example.com" not in yaml_path.name
    assert "sales" not in yaml_path.name
    assert "secret" not in yaml_text
    assert "example.com" not in yaml_text
    assert "sales" not in yaml_text
    assert str(tmp_path) not in yaml_text
    assert "generated:" in yaml_text
    assert "overrides:" in yaml_text
    assert any(metric.name == "销售额" for metric in semantics.metrics)


def test_yaml_enabled_loads_overrides_and_preserves_them_on_refresh(tmp_path) -> None:
    database_url = "sqlite+aiosqlite:///./semantic-refresh.db"
    yaml_path = business_semantic_yaml_path(database_url, tmp_path)

    build_business_semantics(_catalog(), yaml_enabled=True, database_url=database_url, yaml_dir=tmp_path)
    yaml_path.write_text(
        yaml_path.read_text(encoding="utf-8").replace(
            "overrides:\n  aliases: {}",
            "overrides:\n  aliases:\n    客户:\n      tables:\n        - user\n      columns:\n        - user.name\n      aliases:\n        - 客人",
        ),
        encoding="utf-8",
    )

    refreshed = build_business_semantics(_catalog(), yaml_enabled=True, database_url=database_url, yaml_dir=tmp_path)
    refreshed_text = yaml_path.read_text(encoding="utf-8")

    assert any(term.term == "客人" and term.tables == ["user"] for term in refreshed.terms)
    assert "客人" in refreshed_text
    assert "generated:" in refreshed_text


def test_yaml_filename_separates_databases_without_exposing_credentials(tmp_path) -> None:
    first_url = "mysql+asyncmy://user:first-secret@db.example.com:3306/tenant_a"
    second_url = "mysql+asyncmy://user:second-secret@db.example.com:3306/tenant_b"

    first_path = business_semantic_yaml_path(first_url, tmp_path)
    second_path = business_semantic_yaml_path(second_url, tmp_path)

    assert first_path != second_path
    assert "first-secret" not in first_path.name
    assert "second-secret" not in second_path.name
    assert "db.example.com" not in first_path.name
    assert "tenant_a" not in first_path.name
