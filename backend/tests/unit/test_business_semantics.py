import json

from app.rag.business_semantics import (
    attach_business_semantics,
    build_business_semantics,
    business_semantic_yaml_path,
    conversational_enum_mapping,
    conversational_enum_mapping_for_field,
)
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
                    SchemaColumn(name="user_id", data_type="int", nullable=False, semantic_role="foreign_key"),
                    SchemaColumn(name="create_user", data_type="varchar", nullable=True, business_terms=["创建人"]),
                    SchemaColumn(name="update_time", data_type="datetime", nullable=True, business_terms=["更新时间"], semantic_role="timestamp"),
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
    assert not any(metric.table == "orders" and metric.column == "status" for metric in semantics.metrics)
    assert any(dimension.table == "orders" and dimension.column == "status" for dimension in semantics.dimensions)
    assert not any(dimension.table == "orders" and dimension.column in {"id", "user_id", "create_user", "update_time"} for dimension in semantics.dimensions)
    assert any(term.term == "user_id" and term.kind == "foreign_key" for term in semantics.terms)
    assert any(term.term == "创建人" and term.kind == "internal" for term in semantics.terms)
    assert any(term.term == "更新时间" and term.kind == "internal" for term in semantics.terms)
    assert any(enum.table == "orders" and enum.column == "status" and enum.values.get("1") == "已支付" for enum in semantics.enums)


def test_comment_derived_enum_mapping_is_prompt_safe_and_field_level() -> None:
    semantics = build_business_semantics(_catalog())
    enum = next(item for item in semantics.enums if item.table == "orders" and item.column == "status")

    assert conversational_enum_mapping(enum) == "未支付=0, 已支付=1, 已取消=2"
    assert conversational_enum_mapping_for_field(semantics, "orders", "status") == "未支付=0, 已支付=1, 已取消=2"


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


def test_business_semantics_accepts_database_qualified_overrides(tmp_path) -> None:
    catalog = SchemaCatalog(
        database="jc_config,jc_experimental",
        tables=[
            SchemaTable(
                database="jc_config",
                name="employee",
                columns=[
                    SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="varchar", nullable=True, semantic_role="dimension"),
                    SchemaColumn(name="status", data_type="int", nullable=True, description="0 离职 1 在职"),
                ],
            ),
            SchemaTable(
                database="jc_experimental",
                name="employee",
                columns=[
                    SchemaColumn(name="id", data_type="int", nullable=False, is_primary_key=True),
                    SchemaColumn(name="name", data_type="varchar", nullable=True, semantic_role="dimension"),
                ],
            ),
        ],
    )
    override_path = tmp_path / "business_semantics.yaml"
    override_path.write_text(
        json.dumps(
            {
                "aliases": {
                    "配置员工": {
                        "tables": ["jc_config.employee"],
                        "columns": ["jc_config.employee.name"],
                    }
                },
                "metrics": {
                    "员工姓名指标": {
                        "table": "jc_config.employee",
                        "column": "jc_config.employee.name",
                    }
                },
                "enums": {
                    "员工状态": {
                        "table": "jc_config.employee",
                        "column": "jc_config.employee.status",
                        "values": {"1": "在职"},
                    }
                },
                "default_filters": {
                    "在职员工": {
                        "table": "jc_config.employee",
                        "condition": "`jc_config`.`employee`.`status` = 1",
                        "columns": ["jc_config.employee.status"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    semantics = build_business_semantics(catalog, str(override_path))

    assert any(term.term == "配置员工" and term.tables == ["jc_config.employee"] and term.columns == ["jc_config.employee.name"] for term in semantics.terms)
    assert any(metric.name == "员工姓名指标" and metric.table == "jc_config.employee" and metric.column == "name" for metric in semantics.metrics)
    assert any(enum.name == "员工状态" and enum.table == "jc_config.employee" and enum.column == "status" for enum in semantics.enums)
    assert any(item.name == "在职员工" and item.table == "jc_config.employee" and item.columns == ["jc_config.employee.status"] for item in semantics.default_filters)
    assert not semantics.diagnostics


def test_enum_yaml_overrides_add_value_level_conversational_aliases(tmp_path) -> None:
    override_path = tmp_path / "business_semantics.yaml"
    override_path.write_text(
        """
enums:
  payment_status:
    table: orders
    column: orders.status
    values:
      "0":
        label: 待支付
        aliases:
          - 未支付
          - 待付款
      "1": 已支付
    aliases:
      "1":
        - 已付款
""".strip(),
        encoding="utf-8",
    )

    semantics = build_business_semantics(_catalog(), str(override_path))

    assert conversational_enum_mapping_for_field(semantics, "orders", "status") == "未支付/待支付/待付款=0, 已支付/已付款=1, 已取消=2"
    assert any(term.term == "待付款" and term.columns == ["orders.status"] for term in semantics.terms)


def test_invalid_enum_override_values_are_filtered_from_prompt_mappings(tmp_path) -> None:
    override_path = tmp_path / "business_semantics.yaml"
    override_path.write_text(
        json.dumps(
            {
                "enums": {
                    "bad_enum": {
                        "table": "orders",
                        "column": "orders.status",
                        "values": {
                            "1; DROP TABLE orders": "危险",
                            "2": ["not", "scalar"],
                            "3": {"label": "已退款", "aliases": ["退款; DELETE FROM orders", "退款"]},
                            "4 OR 1=1": "绕过",
                        },
                    },
                    "missing_column_enum": {
                        "table": "orders",
                        "column": "orders.missing",
                        "values": {"1": "测试"},
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    semantics = build_business_semantics(_catalog(), str(override_path))
    mapping = conversational_enum_mapping_for_field(semantics, "orders", "status")

    assert "DROP" not in mapping
    assert "DELETE" not in mapping
    assert "OR 1=1" not in mapping
    assert "退款=3" in mapping
    assert "not" not in mapping
    assert any(diagnostic["code"] == "SEMANTIC_OVERRIDE_UNSAFE_ENUM_VALUE" for diagnostic in semantics.diagnostics)
    assert any(diagnostic["code"] == "SEMANTIC_OVERRIDE_INVALID_ENUM_LABEL" for diagnostic in semantics.diagnostics)
    assert any(diagnostic["code"] == "SEMANTIC_OVERRIDE_INVALID_COLUMN" for diagnostic in semantics.diagnostics)


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
